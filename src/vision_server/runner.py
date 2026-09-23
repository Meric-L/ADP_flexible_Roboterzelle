"""Zusammenbau des Vision-Systems, als Einbau oder als eigener Prozess."""

import asyncio
import contextlib
import json
import logging
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from asyncua import Node, Server, ua, uamethod

from .address_space import VisionAddressSpace, attach_vision_system, configure_server
from .asset_model import VisionAssetNodes, attach_asset_model
from .calibration_session import CalibrationSession
from .camera import exit_process
from .camera_health import CameraHealthPublisher, HealthAlarms, write_device_health
from .camera_stream import CameraStreamPublisher
from .mjpeg_server import MjpegServer
from .config import VisionServerConfig
from .detection import DetectionSource, build_detection_sources
from .errors import VisionErrorCode
from .events import VisionEvents, create_event_generators
from .job import JobRunner
from .nodeset_ids import DeviceHealth
from .result_management import ResultStore
from .state_machine import VisionStateMachines
from .vision_program import VisionProgram, install_vision_program

_log = logging.getLogger(__name__)

LOOP_LAG_INTERVAL_S = 0.25
LOOP_LAG_WARN_S = 0.75


async def _watch_loop_lag(
    interval: float = LOOP_LAG_INTERVAL_S, warn: float = LOOP_LAG_WARN_S
) -> None:
    """Loggt, wenn der Event-Loop blockiert war.

    Vergisst eine Quelle `run_blocking`, sieht man es hier statt als
    unerklaerlichen Verbindungsabbruch woanders. Ersetzt `RaspiDevice/Counter`
    als Referenzsignal (doc/altlasten.md A3/A5).
    """
    loop = asyncio.get_running_loop()
    while True:
        before = loop.time()
        await asyncio.sleep(interval)
        elapsed = loop.time() - before
        if elapsed > warn:
            _log.warning(
                "Event-Loop %d ms blockiert (erwartet %d ms)",
                int(elapsed * 1000),
                int(interval * 1000),
            )


def _camera_owner(
    sources: Mapping[str, DetectionSource], opened: Mapping[str, bool]
) -> DetectionSource | None:
    """Return the first opened source holding a shared camera.

    Deliberately via the `camera` attribute rather than a profile name:
    this used to be `sources.get("image_recognition")`, and renaming or
    removing that profile would have silently killed the livestream.
    """
    for profile in sorted(sources):
        if not opened.get(profile):
            continue
        if getattr(sources[profile], "camera", None) is not None:
            return sources[profile]
    return None


def _build_annotator(source: DetectionSource):
    """Build the stream overlay for a source, if it can provide one.

    Stream and job then use the same loaded calibration and tag map -- if
    the image shows something different from the job result, it's not
    because of two configurations.
    """
    detector = getattr(source, "_detector", None)
    calibration = getattr(source, "_calibration", None)
    config = getattr(source, "_config", None)
    camera_config = getattr(source, "_camera_config", None)
    if detector is None or calibration is None or config is None:
        return None
    from .stream_overlay import AprilTagStreamAnnotator

    return AprilTagStreamAnnotator(
        config,
        detector=detector,
        calibration=calibration,
        tag_map=getattr(source, "_tag_map", None),
        interval_s=getattr(camera_config, "overlay_interval_s", 0.5),
        detection_max_width=getattr(camera_config, "max_stream_width", None),
    )


def _start_camera_stream(
    space: VisionAddressSpace,
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
) -> tuple[CameraStreamPublisher | None, Any]:
    """Startet den Livestream-Publisher, falls konfiguriert und Kamera bereit.

    Nutzt dieselbe `SharedCamera`, die auch die Erkennung offen haelt — eine
    zweite Kamera lohnt sich hier nicht, siehe `camera.py`. Gibt den
    Annotator zusaetzlich zurueck, damit `install_vision_machine` ihm spaeter
    eine laufende `CalibrationSession` anhaengen kann (`set_calibration_session`).
    """
    if space.latest_camera_frame is None:
        return None, None
    source = _camera_owner(sources, opened)
    if source is None:
        _log.error(
            "Livestream-Knoten konfiguriert, aber keine geoeffnete Quelle haelt eine "
            "Kamera -- kein Stream"
        )
        return None, None
    annotator = None
    try:
        annotator = _build_annotator(source)
    except Exception:
        _log.exception("Stream-Overlay nicht verfuegbar, Stream laeuft ohne Markierung")
    stream = CameraStreamPublisher(
        source.camera,
        space.latest_camera_frame,
        space.config.camera_stream,
        annotator=annotator,
        mode_node=space.camera_stream_mode,
        progress_node=space.calibration_progress,
    )
    stream.start()
    _log.info(
        "Livestream aus Profil '%s'%s",
        source.profile_id,
        " mit Overlay" if annotator is not None else " ohne Overlay",
    )
    return stream, annotator


async def _start_mjpeg_server(
    space: VisionAddressSpace, stream: CameraStreamPublisher
) -> MjpegServer | None:
    """Startet den MJPEG-Stream und nennt seinen Port im Adressraum.

    Scheitert das Binden (Port belegt), laeuft der Server ohne weiter: der
    Knoten bleibt auf 0 und das Frontend faellt auf `LatestCameraFrame`
    zurueck. Der Livestream ist ein Debugwerkzeug, kein Grund fuer einen
    Startabbruch.
    """
    config = space.config.camera_stream
    if config is None or config.http_port <= 0 or space.camera_stream_http_port is None:
        return None
    server = MjpegServer(stream, config.http_port)
    try:
        await server.start()
    except OSError:
        _log.exception(
            "MJPEG-Livestream auf Port %d nicht startbar -- nur der OPC-UA-Rueckfallweg",
            config.http_port,
        )
        return None
    await space.camera_stream_http_port.write_value(
        ua.Variant(server.port, ua.VariantType.Int32)
    )
    return server


def _camera_config_of(space: VisionAddressSpace, source: DetectionSource):
    """Die Konfiguration der Kamera, die wirklich laeuft.

    `space.config.camera_stream` kann `None` sein, waehrend die Quelle sehr
    wohl eine Kamera mit eigenen Schwellen haelt (`detection/apriltag.py`
    haelt sie in `_camera_config`). Gleiches Muster wie `_build_annotator`.
    """
    from .profiles import CameraStreamConfig

    return (
        getattr(source, "_camera_config", None)
        or space.config.camera_stream
        or CameraStreamConfig()
    )


async def _start_camera_health(
    space: VisionAddressSpace,
    assets: VisionAssetNodes | None,
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
) -> CameraHealthPublisher | None:
    """Verbindet den Kamera-Watchdog mit `DeviceHealth` der Anlagensicht.

    Der Watchdog in `camera.py` erkennt haengende Kameras, meldet das aber nur
    ins Log. Hier wird daraus ein Wert, den ein generischer OPC-UA-Client
    sieht -- ohne Kenntnis dieses Repos.

    Bewusst nicht an `_start_camera_stream` gehaengt: der Livestream ist eine
    Debughilfe und kann fehlen, waehrend die Job-Quelle eine Kamera haelt.
    Ohne Part 2 oder ohne Zustandsknoten passiert nichts.
    """
    if assets is None or not assets.device_health:
        return None
    source = _camera_owner(sources, opened)
    if source is None:
        # Knoten da, Kamera nicht: FAILURE ist die ehrliche Antwort, und sie
        # bleibt stehen -- es gibt nichts, was sie spaeter widerlegen koennte.
        _log.warning(
            "Zustandsknoten der Anlagensicht vorhanden, aber keine geoeffnete "
            "Kamera -- DeviceHealth bleibt auf FAILURE"
        )
        await write_device_health(assets.device_health, DeviceHealth.FAILURE)
        return None
    # Der normkonforme Ereignisweg von Part 2. Emittiert wird von
    # `VisionMachine`, weil Clients ohnehin genau diesen Knoten abonnieren;
    # `SourceNode` nennt die Komponente, um die es geht -- den Bildsensor,
    # falls sein Modell bekannt ist, sonst die Anlagenwurzel.
    alarms = (
        HealthAlarms(
            space.server,
            space.vision_system,
            assets.health_alarms,
            source=assets.image_sensor or assets.root,
        )
        if assets.health_alarms
        else None
    )
    publisher = CameraHealthPublisher(
        source.camera,
        assets.device_health,
        _camera_config_of(space, source),
        alarms=alarms,
    )
    # Der Watchdog reisst den Prozess, wenn er aufgibt; vorher soll noch ein
    # letztes FAILURE rausgehen.
    source.camera.set_give_up_handler(publisher.give_up_handler(exit_process))
    publisher.start()
    _log.info(
        "Kamerazustand (OPC 40100-2) aus Profil '%s' auf %d Knoten, %d Alarme",
        source.profile_id,
        len(assets.device_health),
        len(assets.health_alarms),
    )
    return publisher


def _build_calibration_session(
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
    config: VisionServerConfig,
) -> CalibrationSession | None:
    """Baut die (wiederverwendbare) Kalibrier-Session, falls moeglich.

    Nur wenn die `apriltag`-Quelle offen ist -- dieselbe `SharedCamera` wie
    Job und Livestream, kein zweiter Kamera-Zugriff -- und `config.apriltag`
    gesetzt ist (auf den echten Pis der Fall, siehe `vision_server/server.py`; lokale
    Entwicklung/Tests ohne explizite Konfiguration lassen das Feature aus).
    """
    if config.apriltag is None:
        return None
    source = sources.get("apriltag")
    if source is None or not opened.get("apriltag") or getattr(source, "camera", None) is None:
        return None
    return CalibrationSession(source.camera, config.apriltag)


def _calibration_info_payload(calibration: Any, path: Any) -> dict:
    """JSON-faehige Kurzfassung der gerade *aktiven* Kalibrierung.

    Anders als `CalibrationSession.progress`/`last_result` (Fortschritt
    *einer Session*) beschreibt das hier, was `AprilTagDetectionSource`
    tatsaechlich fuer Posen benutzt -- direkt nach dem Laden beim Start und
    nach jeder interaktiven Neu-Kalibrierung.

    `calibrationId`/`createdAt` liest diese Funktion aus der Datei nach,
    statt sie hier ein zweites Mal zu erzeugen: `CameraCalibration` selbst
    kennt `createdAt` gar nicht, und `calibration_id` ist bei einem frisch
    berechneten Objekt noch leer -- das Format entsteht erst beim Schreiben
    in `tagloc.calibration.save_calibration`. Einzige Ausnahme: die
    Platzhalter-Kalibrierung, zu der keine Datei existiert.
    """
    from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

    is_placeholder = calibration.calibration_id == PLACEHOLDER_CALIBRATION_ID
    info = {
        "placeholder": is_placeholder,
        "frameId": calibration.frame_id,
        "rms": None if is_placeholder else round(calibration.rms_reprojection_error, 4),
        "samples": calibration.sample_count,
        "board": dict(calibration.board),
        "calibrationId": calibration.calibration_id or None,
        "createdAt": None,
        "path": str(path),
    }
    if not is_placeholder:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            info["calibrationId"] = raw.get("calibrationId")
            info["createdAt"] = raw.get("createdAt")
        except OSError:
            pass
    return info


async def _write_calibration_info(node: Node | None, calibration: Any, path: Any) -> None:
    """Schreibt `_calibration_info_payload` in `ActiveCalibrationInfo`.

    Fehler landen nur im Log -- ein nicht schreibbarer Info-Knoten darf
    weder den Start noch eine gerade erfolgreich gespeicherte Kalibrierung
    zu Fall bringen.
    """
    if node is None or calibration is None:
        return
    try:
        await node.write_value(json.dumps(_calibration_info_payload(calibration, path)))
    except Exception:
        _log.exception("ActiveCalibrationInfo konnte nicht geschrieben werden")


async def _open_source(source: DetectionSource) -> bool:
    """Oeffnet eine Quelle; `False`, wenn sie nicht betriebsbereit ist."""
    started = asyncio.get_running_loop().time()
    try:
        await source.open()
    except Exception:
        _log.exception(
            "Erkennungsquelle '%s' konnte nicht geoeffnet werden", source.profile_id
        )
        return False
    duration = asyncio.get_running_loop().time() - started
    if duration > 0.5:
        _log.info("Quelle '%s' in %.1f s bereit", source.profile_id, duration)
    return True


@dataclass(frozen=True)
class VisionMachine:
    """Das fertig verdrahtete Vision-System eines Servers."""

    config: VisionServerConfig
    states: VisionStateMachines
    events: VisionEvents
    results: ResultStore
    sources: Mapping[str, DetectionSource]
    jobs: JobRunner
    lag_watchdog: asyncio.Task | None = None
    camera_stream: CameraStreamPublisher | None = None
    #: MJPEG livestream over HTTP; `None` when disabled or the port was taken.
    camera_http: MjpegServer | None = None
    #: OPC 40100-2 asset view; `None` when Part 2 is not configured.
    assets: VisionAssetNodes | None = None
    #: Schreibt `DeviceHealth` der Anlagensicht; `None` ohne Part 2, ohne
    #: Zustandsknoten oder ohne geoeffnete Kamera.
    camera_health: CameraHealthPublisher | None = None
    #: Part-10-Programm als generische Bedienoberflaeche auf denselben Jobs.
    program: VisionProgram | None = None
    #: `None`, wenn `config.apriltag` nicht gesetzt ist -- kein
    #: `StartCalibration`/`CaptureCalibrationSample`/`FinishCalibration`/
    #: `AbortCalibration`.
    calibration_session: CalibrationSession | None = None

    async def aclose(self) -> None:
        """Faehrt Watchdog, Livestream, laufenden Job und Quelle herunter.

        Best-Effort und idempotent; braucht im Aufrufer einen Signal-Handler,
        sonst laeuft es unter systemd nicht.
        """
        if self.calibration_session is not None and self.calibration_session.running:
            await self.calibration_session.abort()
        # Vor Stream und Quellen: das geordnete Schliessen der Kamera schlaege
        # sonst als Haenger durch, und der letzte Wert im Adressraum waere
        # OFF_SPEC statt des letzten echten Zustands.
        if self.camera_health is not None:
            await self.camera_health.stop()
        if self.lag_watchdog is not None:
            self.lag_watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.lag_watchdog
        if self.camera_http is not None:
            await self.camera_http.stop()
        if self.camera_stream is not None:
            await self.camera_stream.stop()
        await self.jobs.cancel_running()
        for source in self.sources.values():
            try:
                await source.close()
            except Exception:
                _log.exception("Schliessen der Quelle '%s' fehlgeschlagen", source.profile_id)


async def install_vision_machine(server: Server, config: VisionServerConfig) -> VisionMachine:
    """Baut das Vision-System in einen initialisierten Server ein.

    Muss nach `server.init()` und vor `server.start()` laufen. Der Server darf
    daneben beliebige eigene Knoten haben — Endpoint, ApplicationURI und
    ServerName bleiben unberuehrt.
    """
    space = await attach_vision_system(server, config)
    # Part 2 vor den Automaten: es haengt an nichts und soll auch dann stehen,
    # wenn der Job-Pfad spaeter nicht in Operational kommt -- gerade dann ist
    # die Frage "welche Kamera, welcher Zustand" interessant.
    assets = (
        await attach_asset_model(space, config.assets) if config.assets is not None else None
    )
    states = await VisionStateMachines.bind(space)
    events = await create_event_generators(space)
    results = await ResultStore.create(space)
    sources = build_detection_sources(config)
    jobs = JobRunner(config, states, events, results, sources)
    #: Erst spaeter auf ihren echten Wert gesetzt (siehe unten, nach dem
    #: Oeffnen der Quellen) -- die Closures hier greifen erst beim
    #: tatsaechlichen Methodenaufruf darauf zu, also lange danach. Python loest
    #: Namen in Closures spaet auf, das ist hier bewusst genutzt.
    calibration_session: CalibrationSession | None = None
    annotator: Any = None

    @uamethod
    async def start_single_job(parent, meas_id, part_id, recipe_id, product_id, parameters):
        """OPC-UA-Einstiegspunkt fuer 1:StartSingleJob.

        Muss `async` sein: synchrone Handler laufen bei asyncua in einem
        ThreadPoolExecutor ohne laufenden Event-Loop, dort scheitert das
        Starten des Job-Tasks. `jobs.start_single_job` bleibt synchron und
        wird ohne `await`-Punkt aufgerufen — die Zulassung bleibt atomar.

        Gibt JobId und Error als String bzw. Int32 zurueck, obwohl das Nodeset
        `JobIdDataType` deklariert — asyncua validiert Methodenargumente nicht,
        und ein Client koennte das ExtensionObject nicht dekodieren. Die
        Rueckgabe muss ein Tupel sein; eine Liste wuerde asyncua als einen
        einzigen Variant verpacken.
        """
        if calibration_session is not None and calibration_session.running:
            _log.warning("StartSingleJob waehrend laufender Kalibrierung abgelehnt")
            return (
                ua.Variant("", ua.VariantType.String),
                ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),
            )
        job_id, error = jobs.start_single_job(
            meas_id, part_id, recipe_id, product_id, parameters
        )
        return (
            ua.Variant(job_id, ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    server.link_method(space.start_single_job, start_single_job)

    @uamethod
    async def stop_job(parent, cause, cause_description):
        """OPC-UA-Einstiegspunkt fuer 1:Stop.

        `Cause`/`CauseDescription` schickt das Frontend fire-and-forget immer
        als 0/"" und wertet sie nicht aus; wir werten sie ebenfalls nicht aus.
        Muss wie `start_single_job` `async` sein.
        """
        error = await jobs.stop()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    server.link_method(space.stop, stop_job)

    @uamethod
    async def start_continuous(parent, meas_id, part_id, recipe_id, product_id, parameters):
        """1:StartContinuous -- Dauerbetrieb bis Stop oder Abort."""
        if calibration_session is not None and calibration_session.running:
            _log.warning("StartContinuous waehrend laufender Kalibrierung abgelehnt")
            return (
                ua.Variant("", ua.VariantType.String),
                ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),
            )
        job_id, error = jobs.start_continuous(
            meas_id, part_id, recipe_id, product_id, parameters
        )
        return (
            ua.Variant(job_id, ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    server.link_method(space.start_continuous, start_continuous)

    @uamethod
    async def abort_job(parent, cause, cause_description):
        """1:Abort -- wie Stop, aber ueber den Abort-Uebergang.

        Fuer uns ist der Unterschied nur der Zustandsuebergang und die Meldung
        im Ergebnis: es gibt keinen Zwischenstand, den ein Abbruch verwerfen
        koennte. Beide melden `CANCELLED`.
        """
        error = await jobs.stop(abort=True)
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    server.link_method(space.abort, abort_job)

    @uamethod
    async def halt_system(parent, cause, cause_description):
        """1:Halt -- laufenden Job beenden, dann keine Jobs mehr annehmen.

        Aus Halted fuehrt nur `Reset` zurueck. Das ist der Sinn: Halt ist die
        Bremse fuer den Bediener, nicht ein weiterer Betriebszustand.
        """
        error = await jobs.stop()
        if error != VisionErrorCode.OK:
            return (ua.Variant(int(error), ua.VariantType.Int32),)
        try:
            await states.halt()
        except Exception:
            _log.exception("Halt fehlgeschlagen")
            return (ua.Variant(int(VisionErrorCode.INTERNAL), ua.VariantType.Int32),)
        return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

    server.link_method(space.halt, halt_system)

    @uamethod
    async def reset_system(parent, cause, cause_description):
        """1:Reset -- zurueck in den betriebsbereiten Zustand.

        Ueber Preoperational, weil das Nodeset keinen Uebergang
        Halted -> Operational kennt; `enter_operational` faehrt genau diesen
        konformen Weg.
        """
        try:
            await states.enter_operational()
        except Exception:
            _log.exception("Reset fehlgeschlagen")
            return (ua.Variant(int(VisionErrorCode.INTERNAL), ua.VariantType.Int32),)
        return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

    server.link_method(space.reset, reset_system)

    # Erst oeffnen, dann Operational: `Ready` soll "Hardware bereit" heissen.
    # Die Methode bleibt verlinkt, sonst antwortet der Server BadNothingToDo.
    opened = {profile: await _open_source(source) for profile, source in sources.items()}
    if all(opened.values()):
        await states.enter_operational()
    else:
        _log.error(
            "Vision-System '%s' bleibt in Preoperational, Quelle nicht bereit",
            config.vision_system_name,
        )

    calibration_session = _build_calibration_session(sources, opened, config)
    camera_stream, annotator = _start_camera_stream(space, sources, opened)
    camera_health = await _start_camera_health(space, assets, sources, opened)
    camera_http = (
        await _start_mjpeg_server(space, camera_stream)
        if camera_stream is not None
        else None
    )

    apriltag_source = sources.get("apriltag")
    if config.apriltag is not None and opened.get("apriltag"):
        # Was `open()` gerade geladen hat (echte Datei oder Platzhalter) --
        # ohne das waere ActiveCalibrationInfo leer, bis zum ersten
        # StartCalibration.
        await _write_calibration_info(
            space.active_calibration_info,
            getattr(apriltag_source, "_calibration", None),
            config.apriltag.calibration_path,
        )
    if calibration_session is not None:

        async def _apply_live_calibration(calibration: Any) -> None:
            """Bringt Erkennung, Overlay und den Info-Knoten sofort auf den
            neuen Stand -- kein Server-Neustart noetig, siehe
            `AprilTagDetectionSource.apply_calibration`."""
            if apriltag_source is not None and hasattr(apriltag_source, "apply_calibration"):
                apriltag_source.apply_calibration(calibration)
            if annotator is not None and hasattr(annotator, "apply_calibration"):
                annotator.apply_calibration(calibration)
            await _write_calibration_info(
                space.active_calibration_info, calibration, config.apriltag.calibration_path
            )

        calibration_session.set_on_calibrated(_apply_live_calibration)

    #: Kalibriermethoden, die zusaetzlich unter `VisionProgram` aufrufbar
    #: werden -- gefuellt nur, wenn es eine Kalibrier-Session gibt.
    calibration_methods: dict[str, Node] = {}

    if calibration_session is not None and space.calibration_progress is not None:
        #: Praefix der Methoden-NodeIds. Explizit statt der laufenden Nummer,
        #: die `add_method(own_idx, ...)` vergeben wuerde: die verschiebt sich,
        #: sobald jemand davor einen Knoten einfuegt, und das Frontend spricht
        #: die Methoden ueber feste Adressen an.
        method_prefix = config.vision_system_name

        @uamethod
        async def start_calibration(parent):
            """1:StartCalibration -- setzt eine neue Session auf; Aufnahmen
            kommen danach ausschliesslich ueber `CaptureCalibrationSample`.

            Kein Kalibrierdurchlauf gegen einen laufenden Job oder eine
            zweite Session gleichzeitig -- beide teilen sich Kamera und
            Detektor.
            """
            if jobs.busy:
                _log.warning("StartCalibration waehrend laufendem Job abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if calibration_session.running:
                _log.warning("StartCalibration waehrend laufender Session abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if not states.is_ready():
                return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
            calibration_session.start()
            if annotator is not None:
                annotator.set_calibration_session(calibration_session)
            _log.info("Kalibrier-Session gestartet")
            return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

        calibration_methods["StartCalibration"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.StartCalibration", space.own_idx),
            ua.QualifiedName("StartCalibration", space.own_idx),
            start_calibration,
            [],
            [ua.VariantType.Int32],
        )

        @uamethod
        async def capture_calibration_sample(parent):
            """1:CaptureCalibrationSample -- versucht eine Aufnahme vom
            aktuellen Kamerabild, manuell ausgeloest (z. B. per Leertaste im
            Stream-Viewer). `Error=OK` heisst: Board gefunden und
            uebernommen; `DETECTION_FAILED` heisst nur "dieser Versuch nicht"
            -- die Session laeuft weiter, ein erneuter Versuch ist ok.
            """
            if not calibration_session.running:
                return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
            found = await calibration_session.capture()
            error = VisionErrorCode.OK if found else VisionErrorCode.DETECTION_FAILED
            return (ua.Variant(int(error), ua.VariantType.Int32),)

        calibration_methods["CaptureCalibrationSample"] = (
            await space.vision_system.add_method(
                ua.NodeId(f"{method_prefix}.CaptureCalibrationSample", space.own_idx),
                ua.QualifiedName("CaptureCalibrationSample", space.own_idx),
                capture_calibration_sample,
                [],
                [ua.VariantType.Int32],
            )
        )

        @uamethod
        async def finish_calibration(parent):
            """1:FinishCalibration -- rechnet aus den gesammelten Aufnahmen
            und speichert bei Erfolg `data/calibration/<frame_id>.json`.

            `Summary` ist immer gueltiges JSON, auch im Fehlerfall (dann mit
            `message` statt `rms`/`samples`/... ), damit das Frontend nicht
            zwischen Erfolgs- und Fehlerform unterscheiden muss.
            """
            if not calibration_session.running:
                return (
                    ua.Variant('{"message": "keine Session aktiv"}', ua.VariantType.String),
                    ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
                )
            error, summary = await calibration_session.finish()
            if annotator is not None:
                annotator.set_calibration_session(None)
            return (
                ua.Variant(json.dumps(summary), ua.VariantType.String),
                ua.Variant(int(error), ua.VariantType.Int32),
            )

        calibration_methods["FinishCalibration"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.FinishCalibration", space.own_idx),
            ua.QualifiedName("FinishCalibration", space.own_idx),
            finish_calibration,
            [],
            [ua.VariantType.String, ua.VariantType.Int32],
        )

        @uamethod
        async def abort_calibration(parent):
            """1:AbortCalibration -- stoppt ohne zu speichern."""
            if not calibration_session.running:
                return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
            await calibration_session.abort()
            if annotator is not None:
                annotator.set_calibration_session(None)
            _log.info("Kalibrier-Session abgebrochen")
            return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

        calibration_methods["AbortCalibration"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.AbortCalibration", space.own_idx),
            ua.QualifiedName("AbortCalibration", space.own_idx),
            abort_calibration,
            [],
            [ua.VariantType.Int32],
        )

    # Part-10-Aufsatz auf denselben JobRunner. Muss nach den Zustaenden
    # stehen: das Programm spiegelt den Zustand des Vision-Systems und waere
    # sonst `Ready`, bevor feststeht, ob die Quelle ueberhaupt aufgeht.
    mirror_nodes = {"LatestResultJson": results.json_node}
    if space.latest_camera_frame is not None:
        mirror_nodes["LatestCameraFrame"] = space.latest_camera_frame
    if space.camera_stream_mode is not None:
        mirror_nodes["CameraStreamMode"] = space.camera_stream_mode
    if space.calibration_progress is not None:
        mirror_nodes["CalibrationProgress"] = space.calibration_progress
    if space.active_calibration_info is not None:
        mirror_nodes["ActiveCalibrationInfo"] = space.active_calibration_info
    program = await install_vision_program(
        server,
        server.nodes.objects,
        space.own_idx,
        jobs,
        known_recipes=config.known_recipe_ids,
        mirror_nodes=mirror_nodes,
        mirror_methods=calibration_methods,
    )
    if not states.is_ready():
        # Ein generischer Client soll nicht `Ready` sehen, wenn kein Job
        # angenommen wuerde. Teil 10 hat keinen Fehlerzustand -- `Halted` ist
        # die einzige ehrliche Entsprechung.
        sm = program.state_machine
        await sm.change_state(
            sm.halted, sm.ready_to_halted, "Vision-System nicht betriebsbereit"
        )

    lag_watchdog = asyncio.create_task(_watch_loop_lag())

    _log.info(
        "Vision-System '%s' bereit (Profile %s, Namespace %s)",
        config.vision_system_name,
        ", ".join(sorted(sources)),
        config.namespace_uri,
    )
    return VisionMachine(
        config=config,
        states=states,
        events=events,
        results=results,
        sources=sources,
        jobs=jobs,
        lag_watchdog=lag_watchdog,
        camera_stream=camera_stream,
        camera_health=camera_health,
        camera_http=camera_http,
        assets=assets,
        program=program,
        calibration_session=calibration_session,
    )


async def run(config: VisionServerConfig) -> None:
    """Startet den Vision-Server als eigenen Prozess und haelt ihn am Leben.

    Produktiv haengt das Vision-System im Server der Roboterzelle; dieser Weg
    ist fuer Entwicklung und isolierte Tests.
    """
    server = Server()
    await configure_server(server, config)
    machine = await install_vision_machine(server, config)
    _log.info("Vision-Server laeuft auf %s", config.endpoint)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        await machine.aclose()
