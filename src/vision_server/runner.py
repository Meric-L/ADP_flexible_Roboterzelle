"""Zusammenbau des Vision-Systems, als Einbau oder als eigener Prozess."""

import asyncio
import contextlib
import dataclasses
import json
import logging
import signal
import socket
from collections.abc import Callable, Mapping
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
from .profiles import _REPO_ROOT
from .result_management import ResultStore
from .state_machine import VisionStateMachines
from .vision_program import VisionProgram, install_vision_program

_log = logging.getLogger(__name__)

LOOP_LAG_INTERVAL_S = 0.25
LOOP_LAG_WARN_S = 0.75
#: Takt, in dem `CalibrationProgress` waehrend der Hintergrund-Auswertung
#: geschrieben wird, solange der Livestream pausiert ist (siehe
#: `_pause_stream_during_processing`). Ein neu verbundener Client sieht
#: `processing: true` damit spaetestens nach dieser Zeit.
CALIBRATION_PROGRESS_INTERVAL_S = 1.0


async def _watch_loop_lag(
    interval: float = LOOP_LAG_INTERVAL_S, warn: float = LOOP_LAG_WARN_S
) -> None:
    """Loggt, wenn der Event-Loop blockiert war.

    Vergisst eine Quelle `run_blocking`, sieht man es hier statt als
    unerklaerlichen Verbindungsabbruch woanders. Ersetzt `RaspiDevice/Counter`
    als Referenzsignal (doc/projektdoku/altlasten.md A3/A5).
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

    Stream and job use the same loaded calibration and tag map -- if the
    image shows something different from the job result, it's not because
    of two configurations. Der Detektor ist bewusst NICHT derselbe: Job und
    Stream liefen bis 2026-09-24 auf einer geteilten Instanz, beide mit
    `subpixel_corner_refinement` aus der Config (aktuell `True` -- Layer 1
    braucht das, siehe `AprilTagProfileConfig`). Live auf pi1 gemessen
    (py-spy-Dump): der Job-Thread (`vision-apriltag_0`) und der
    Overlay-Thread liefen dabei GLEICHZEITIG in `detector.detect()` mit
    aktivem `CORNER_REFINE_APRILTAG` -- zusammen genug CPU-Konkurrenz, um
    den 20s-Job-Timeout wieder reissen zu lassen. Der Stream braucht die
    Verfeinerung nicht (Demo-Bild, kleinere Aufloesung als der Job) --
    deshalb bekommt er eine EIGENE Detektor-Instanz mit der Verfeinerung
    fest aus, unabhaengig vom Config-Wert fuer den Job-Pfad.

    Seit 21.10.2026 zeichnet das Overlay die Tags aus der Erkennung in voller
    Aufloesung (`source.latest_tags`) und erkennt nur noch selten selbst, im
    Hintergrund und nicht waehrend eines Jobs (`set_busy`). Der eigene
    Detektor bleibt dafuer -- ein Detektor ist nicht fuer zwei Threads gebaut.
    """
    job_detector = getattr(source, "_detector", None)
    calibration = getattr(source, "_calibration", None)
    config = getattr(source, "_config", None)
    camera_config = getattr(source, "_camera_config", None)
    if job_detector is None or calibration is None or config is None:
        return None

    from tagloc.detector import build_detector

    from .stream_overlay import AprilTagStreamAnnotator

    # Detektor-Parameter wie beim Job, sonst zeigt das Overlay andere Tags --
    # bis auf die Eckenverfeinerung: sie bleibt im Stream bewusst aus (siehe
    # oben, CPU-Konkurrenz zum Job), auch wenn `cornerRefinementMethod`
    # ausdruecklich in den Parametern steht.
    stream_parameters = {
        name: value
        for name, value in config.detector_parameters.items()
        if name != "cornerRefinementMethod"
    }
    stream_detector = build_detector(
        config.tag_family,
        config.detector_backend,
        subpixel_corner_refinement=False,
        detector_parameters=stream_parameters,
    )

    return AprilTagStreamAnnotator(
        config,
        detector=stream_detector,
        calibration=calibration,
        tag_map=getattr(source, "_tag_map", None),
        interval_s=getattr(camera_config, "overlay_interval_s", 0.5),
        detection_max_width=getattr(camera_config, "max_stream_width", None),
        full_resolution_tags=lambda: getattr(source, "latest_tags", None),
        detect_interval_s=getattr(camera_config, "overlay_detect_interval_s", 5.0),
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
        preview_status_node=space.tag_reference_preview_status,
    )
    stream.start()
    _log.info(
        "Livestream aus Profil '%s'%s",
        source.profile_id,
        " mit Overlay" if annotator is not None else " ohne Overlay",
    )
    return stream, annotator


def _write_tag_map_file(path, text: str) -> None:
    """Schreibt die Karte, Verzeichnis wird bei Bedarf angelegt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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
    server = MjpegServer(
        stream, config.http_port, tag_reference_dir=space.config.tag_reference_debug_dir
    )
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
        "model": calibration.model,
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
    #: Layer-2-Lauf (`layer2/run.py`); `None` ausser auf dem Hand-Pi.
    layer2_run: Any = None
    #: Hand-Auge-Kalibrierung (`handeye_session.py`); `None` ausser auf dem Hand-Pi.
    hand_eye_session: Any = None
    #: Zellen-Ueberwachung (`cellwatch.py`); `None` ausser auf der Deckenkamera.
    cell_watch: Any = None

    async def aclose(self) -> None:
        """Faehrt Watchdog, Livestream, laufenden Job und Quelle herunter.

        Best-Effort und idempotent; braucht im Aufrufer einen Signal-Handler,
        sonst laeuft es unter systemd nicht.
        """
        if self.calibration_session is not None and self.calibration_session.running:
            await self.calibration_session.abort()
        if self.layer2_run is not None:
            await self.layer2_run.abort("Server faehrt herunter")
        if self.hand_eye_session is not None and self.hand_eye_session.active:
            await self.hand_eye_session.abort()
        if self.cell_watch is not None:
            await self.cell_watch.aclose()
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
    layer2_run: Any = None
    hand_eye_session: Any = None

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
        if calibration_session is not None and calibration_session.busy:
            _log.warning("StartSingleJob waehrend laufender Kalibrierung/Auswertung abgelehnt")
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
        # Erst den Lauf, dann den Job: sonst wertete der Lauf den
        # abgebrochenen Job noch aus und veroeffentlichte ein neues Ziel.
        if layer2_run is not None:
            await layer2_run.abort("Stop")
        error = await jobs.stop()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    server.link_method(space.stop, stop_job)

    @uamethod
    async def start_continuous(parent, meas_id, part_id, recipe_id, product_id, parameters):
        """1:StartContinuous -- Dauerbetrieb bis Stop oder Abort."""
        if calibration_session is not None and calibration_session.busy:
            _log.warning("StartContinuous waehrend laufender Kalibrierung/Auswertung abgelehnt")
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
    if annotator is not None and hasattr(annotator, "set_busy"):
        # Wie die Zellen-Ueberwachung: waehrend Job oder Kalibrierung keine
        # eigene Overlay-Erkennung, der Job soll die CPU fuer sich haben.
        annotator.set_busy(
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.busy)
        )
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
            if layer2_run is not None and layer2_run.active:
                _log.warning("StartCalibration waehrend laufendem Layer-2-Lauf abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if hand_eye_session is not None and hand_eye_session.active:
                _log.warning("StartCalibration waehrend Hand-Auge-Kalibrierung abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if calibration_session.busy:
                # `.busy` statt `.running`: eine Hintergrund-Auswertung aus
                # einem vorherigen `FinishCalibration` liest noch aus dem
                # `_pending_dir` der Session -- ein neuer `start()` waehrend-
                # dessen wuerde den gerade genau darunter wegleeren.
                _log.warning("StartCalibration waehrend laufender Session/Auswertung abgelehnt")
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
            """1:CaptureCalibrationSample -- merkt sich das aktuelle
            Kamerabild, manuell ausgeloest (z. B. per Leertaste im
            Stream-Viewer). Keine Board-Erkennung in diesem Aufruf, die
            passiert erst nach `FinishCalibration` (siehe
            `CalibrationSession.capture`). `Error=OK` heisst nur "Bild
            gespeichert"; `INVALID_STATE` heisst keine Session aktiv oder
            gerade kein Kamera-Frame -- die Session laeuft weiter, ein
            erneuter Versuch ist ok.
            """
            if not calibration_session.running:
                return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
            stored = await calibration_session.capture()
            error = VisionErrorCode.OK if stored else VisionErrorCode.INVALID_STATE
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

        async def _write_calibration_progress() -> None:
            """Schreibt `CalibrationProgress` direkt aus der Session.

            Sonst schreibt nur die Publish-Schleife des Livestreams diesen
            Knoten (`CameraStreamPublisher._publish_progress`) -- und genau
            die ist waehrend der Auswertung pausiert. Ohne diesen eigenen
            Schreibpfad bliebe der Knoten dann auf dem letzten Stand der
            Aufnahme-Phase stehen, `processing: true` waere nie sichtbar
            (gefunden 2026-09-28).
            """
            try:
                await space.calibration_progress.write_value(
                    json.dumps(calibration_session.progress)
                )
            except Exception:
                _log.exception("Kalibrier-Fortschritt konnte nicht veroeffentlicht werden")

        async def _pause_stream_during_processing() -> None:
            """Waehrend `CalibrationSession._process()` im Hintergrund
            rechnet, den Livestream pausieren, damit der Pi die volle CPU
            fuer die Ecken-Erkennung hat (2026-09-28, auf Vorschlag/mit
            Bestaetigung, nachdem die vorherige Deckenkamera-Auswertung
            wiederholt haengen blieb). Laeuft selbst als Hintergrund-Task --
            `finish_calibration` darf darauf nicht warten, siehe dort.

            `CalibrationProgress` wird in dieser Zeit hier geschrieben statt
            vom (pausierten) Livestream: einmal sofort (`processing: true`),
            danach im Takt `CALIBRATION_PROGRESS_INTERVAL_S` und ein letztes
            Mal mit `result`, bevor der Livestream wieder uebernimmt.
            """
            if camera_stream is not None:
                await camera_stream.stop()
            await _write_calibration_progress()
            while True:
                try:
                    await asyncio.wait_for(
                        calibration_session.wait_for_processing(),
                        timeout=CALIBRATION_PROGRESS_INTERVAL_S,
                    )
                    break
                except TimeoutError:
                    await _write_calibration_progress()
            await _write_calibration_progress()
            if camera_stream is not None:
                camera_stream.start()

        @uamethod
        async def finish_calibration(parent):
            """1:FinishCalibration -- beendet die Aufnahme-Phase und stoesst
            die Auswertung im Hintergrund an; kehrt sofort zurueck, OHNE auf
            das Ergebnis zu warten (`CalibrationSession.finish()`, siehe
            dort -- eine synchrone Auswertung mehrerer
            Vollaufloesungs-Aufnahmen haette auf der Deckenkamera dasselbe
            Timeout-Risiko wie eine einzelne haengende `detect_board`-
            Aufnahme, live 2026-09-28 gefunden). Das Frontend beobachtet das
            Ergebnis ausschliesslich ueber `CalibrationProgress`
            (`processing` -> `result`).

            `Summary` ist immer gueltiges JSON, auch im Fehlerfall (dann mit
            `message` statt `samples`/... ), damit das Frontend nicht
            zwischen Erfolgs- und Fehlerform unterscheiden muss.
            """
            if not calibration_session.running:
                return (
                    ua.Variant('{"message": "keine Session aktiv"}', ua.VariantType.String),
                    ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
                )
            error, summary = await calibration_session.finish()
            # `annotator` bleibt in beiden Faellen bewusst an der Session
            # haengen (kein `set_calibration_session(None)` hier): der
            # Livestream veroeffentlicht so weiter `progress` samt `result`,
            # statt in den session-losen Zustand `{"running": false}`
            # zurueckzufallen -- das gilt auch fuer die sofortige Ablehnung
            # mit zu wenigen Aufnahmen, deren `result` sonst nie in
            # `CalibrationProgress` ankaeme. Geloest wird das erst durch das
            # naechste `StartCalibration`/`AbortCalibration`.
            if error == VisionErrorCode.OK:
                asyncio.ensure_future(_pause_stream_during_processing())
            else:
                await _write_calibration_progress()
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

    apriltag_source = sources.get("apriltag") if opened.get("apriltag") else None
    if apriltag_source is not None and space.tag_map_json is not None:
        from tagloc.tagmap import tag_map_from_json, tag_map_to_json

        method_prefix = config.vision_system_name

        async def _publish_tag_map() -> None:
            """Spiegelt die geladene Karte in den Leseknoten."""
            current = getattr(apriltag_source, "_tag_map", None)
            if current is not None and space.tag_map_json is not None:
                await space.tag_map_json.write_value(
                    ua.Variant(tag_map_to_json(current), ua.VariantType.String)
                )

        await _publish_tag_map()

        @uamethod
        async def set_tag_map(parent, tag_map_json: str):
            """1:SetTagMap -- setzt die Tag-Map der Zelle.

            Damit ist das Backend die Pflegestelle: eine neue Tag-Groesse oder
            ein verschobener Welttag brauchen keinen Dateizugriff auf dem Pi
            mehr. Die Datei bleibt trotzdem der lokale Zwischenspeicher, damit
            der Pi ohne Backend messen kann.

            Erst pruefen, dann uebernehmen: eine abgelehnte Karte laesst die
            laufende unangetastet.
            """
            if jobs.busy:
                _log.warning("SetTagMap waehrend laufendem Job abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if calibration_session is not None and calibration_session.running:
                _log.warning("SetTagMap waehrend laufender Kalibrierung abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if layer2_run is not None and layer2_run.active:
                # Eine neue Karte verwirft den Anker, auf dem der Lauf steht.
                _log.warning("SetTagMap waehrend laufendem Layer-2-Lauf abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if hand_eye_session is not None and hand_eye_session.active:
                # Die Tag-Groesse aus der Karte skaliert jede Aufnahme.
                _log.warning("SetTagMap waehrend Hand-Auge-Kalibrierung abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            try:
                tag_map = tag_map_from_json(tag_map_json or "", source="SetTagMap")
            except ValueError as error:
                _log.warning("SetTagMap abgelehnt: %s", error)
                return (
                    ua.Variant(int(VisionErrorCode.INVALID_ARGUMENT), ua.VariantType.Int32),
                )

            for problem in apriltag_source.apply_tag_map(tag_map):
                # Bericht, kein Fehler -- eine Zelle im Aufbau darf messen.
                _log.warning("Tag-Map aus SetTagMap: %s", problem)
            await _publish_tag_map()

            path = config.apriltag.tag_map_path if config.apriltag is not None else None
            if path is None:
                return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)
            try:
                await asyncio.to_thread(
                    _write_tag_map_file, path, tag_map_to_json(tag_map)
                )
            except OSError as error:
                # Messen geht vor Persistenz: die Karte ist bereits aktiv, nur
                # ein Neustart faellt auf die alte Datei zurueck. Das muss der
                # Aufrufer wissen, deshalb trotzdem ein Fehlercode.
                _log.error("Tag-Map konnte nicht nach %s geschrieben werden: %s", path, error)
                return (ua.Variant(int(VisionErrorCode.INTERNAL), ua.VariantType.Int32),)
            _log.info("Tag-Map gesetzt und nach %s geschrieben", path)
            return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

        calibration_methods["SetTagMap"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.SetTagMap", space.own_idx),
            ua.QualifiedName("SetTagMap", space.own_idx),
            set_tag_map,
            [ua.VariantType.String],
            [ua.VariantType.Int32],
        )

    if (
        apriltag_source is not None
        and space.layer2_target is not None
        and space.layer2_status is not None
    ):
        layer2_run = await _install_layer2_run(
            space, config, jobs, apriltag_source, results, calibration_methods,
            lambda: (calibration_session is not None and calibration_session.busy)
            or (hand_eye_session is not None and hand_eye_session.active),
        )
    if apriltag_source is not None and space.hand_eye_status is not None:
        hand_eye_session = await _install_hand_eye_session(
            space, config, apriltag_source, calibration_methods,
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.busy)
            or (layer2_run is not None and layer2_run.active),
        )
    if annotator is not None and hasattr(annotator, "set_overlay_paused") and apriltag_source is not None:
        # Auf dem Hand-Pi wuerde das Overlay waehrend einer Messung oder
        # Hand-Auge-Sitzung in voller Aufloesung mitrechnen, die CPU gehoert
        # der Messung. Der Stream zeigt dann das Rohbild.
        annotator.set_overlay_paused(
            lambda: apriltag_source.measure_calls > 0
            or (hand_eye_session is not None and hand_eye_session.active)
        )
    if apriltag_source is not None:
        # MeasureBoard an beiden Pis. Es liest nur Kamerabilder und aendert keinen
        # Zustand, laeuft deshalb auch waehrend einer Hand-Auge-Sitzung (die
        # Steuerung zentriert das Brett damit vor `CaptureHandEyeSample`) und in
        # der Aufnahmephase einer Kamera-Kalibrierung (die automatische
        # Kalibrierfahrt nimmt nur auf, wenn das ganze Brett im Bild ist).
        # Gesperrt fuer Jobs und waehrend der Auswertung der Kalibrierung.
        await _install_measure_board(
            space, config, apriltag_source, calibration_methods, states,
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.processing),
        )
    if apriltag_source is not None and space.last_tag_measurement is not None:
        # MeasureTags und MeasureTagReference nehmen beide frische Bilder; sie
        # sperren sich gegenseitig (und MeasureTagReference auch sich selbst).
        tags_activity = _Activity()
        reference_activity = _Activity()
        measuring_busy = (
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.busy)
        )
        # MeasureTags laeuft auch WAEHREND einer Hand-Auge-Sitzung: Es liest nur
        # Kamerabilder und aendert keinen Zustand. Eine haengengebliebene Sitzung
        # soll Layer 2 nicht sperren. Gesperrt nur fuer Job und Kamera-Kalibrierung.
        # MeasureTagReference faehrt den Roboter nicht, misst aber laenger; eine
        # Hand-Auge-Sitzung sperrt es weiterhin.
        reference_busy = (
            lambda: measuring_busy()
            or (hand_eye_session is not None and hand_eye_session.active)
        )
        await _install_measure_tags(
            space, config, apriltag_source, calibration_methods, states,
            measuring_busy,
            activity=tags_activity,
            blocked_by=lambda: reference_activity.active,
        )
        if space.last_tag_reference is not None:
            await _install_tag_reference(
                space, config, apriltag_source, calibration_methods, states,
                reference_busy,
                activity=reference_activity,
                blocked_by=lambda: tags_activity.active or reference_activity.active,
                annotator=annotator,
            )

    camera_owner = _camera_owner(sources, opened)
    if camera_owner is not None and config.raw_image_dir is not None:
        await _install_save_raw_image(
            space, config, camera_owner, calibration_methods, lambda: jobs.busy
        )
    if camera_owner is not None and config.use_case_dir is not None:
        await _install_save_use_case_image(
            space, config, camera_owner, calibration_methods, lambda: jobs.busy
        )

    cell_watch = None
    if apriltag_source is not None and space.cell_watch_status is not None:
        cell_watch = await _install_cell_watch(
            space, config, apriltag_source, results, calibration_methods, states,
            lambda: "Job laeuft" if jobs.busy else (
                "Kamera-Kalibrierung laeuft"
                if calibration_session is not None and calibration_session.busy
                else ""
            ),
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
    if space.tag_map_json is not None:
        mirror_nodes["TagMapJson"] = space.tag_map_json
    if space.active_calibration_info is not None:
        mirror_nodes["ActiveCalibrationInfo"] = space.active_calibration_info
    if space.layer2_target is not None and space.layer2_status is not None:
        mirror_nodes["Layer2Target"] = space.layer2_target
        mirror_nodes["Layer2Status"] = space.layer2_status
    if space.hand_eye_status is not None:
        mirror_nodes["HandEyeStatus"] = space.hand_eye_status
    if space.last_tag_measurement is not None:
        mirror_nodes["LastTagMeasurement"] = space.last_tag_measurement
    if space.last_tag_reference is not None:
        mirror_nodes["LastTagReference"] = space.last_tag_reference
    if space.tag_reference_preview_status is not None:
        mirror_nodes["TagReferencePreviewStatus"] = space.tag_reference_preview_status
    if space.cell_watch_status is not None:
        mirror_nodes["CellWatchStatus"] = space.cell_watch_status
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
        layer2_run=layer2_run,
        hand_eye_session=hand_eye_session,
        cell_watch=cell_watch,
    )


async def _install_layer2_run(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    jobs: JobRunner,
    apriltag_source: Any,
    results: ResultStore,
    methods: dict[str, Node],
    calibration_busy: Callable[[], bool],
) -> Any:
    """Legt StartLayer2Run/ReportRobotPose an und verdrahtet den Lauf.

    Nur auf dem Hand-Pi (Knoten nur mit `hand_eye_path`). Jeder Schritt
    startet einen gewoehnlichen AprilTag-Job ueber den JobRunner -- gleiche
    Events, gleiches `LatestResultJson`, `Stop` wirkt wie gewohnt. Siehe
    `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md`.
    """
    # Erst hier: `layer2` braucht numpy, der Rest des Servers nicht.
    from .layer2.run import Layer2Ports, Layer2Run

    async def publish(target: str, status: str) -> None:
        await space.layer2_target.write_value(ua.Variant(target, ua.VariantType.String))
        await space.layer2_status.write_value(ua.Variant(status, ua.VariantType.String))

    async def read_result() -> str:
        return await results.json_node.read_value()

    def start_job(parameters: list[str]) -> tuple[str, VisionErrorCode]:
        return jobs.start_single_job("", "", "apriltag", "", parameters)

    layer2_run = Layer2Run(
        Layer2Ports(
            start_job=start_job,
            busy=lambda: jobs.busy or calibration_busy(),
            read_anchor=lambda: getattr(apriltag_source, "_anchor", None),
            read_hand_eye=lambda: getattr(apriltag_source, "_hand_eye", None),
            read_tag_map=lambda: apriltag_source._tag_map,
            read_result=read_result,
            publish=publish,
        )
    )
    jobs.add_finish_listener(layer2_run.on_job_finished)
    await layer2_run.publish_idle()

    method_prefix = config.vision_system_name

    @uamethod
    async def start_layer2_run(parent, run_json: str):
        """1:StartLayer2Run -- Lauf mit den groben Posen aus Layer 1 starten."""
        error = await layer2_run.start(run_json or "")
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def report_robot_pose(parent, report_json: str):
        """1:ReportRobotPose -- erreichte Flanschpose melden, Messung starten."""
        error = await layer2_run.report(report_json or "")
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    methods["StartLayer2Run"] = await space.vision_system.add_method(
        ua.NodeId(f"{method_prefix}.StartLayer2Run", space.own_idx),
        ua.QualifiedName("StartLayer2Run", space.own_idx),
        start_layer2_run,
        [ua.VariantType.String],
        [ua.VariantType.Int32],
    )
    methods["ReportRobotPose"] = await space.vision_system.add_method(
        ua.NodeId(f"{method_prefix}.ReportRobotPose", space.own_idx),
        ua.QualifiedName("ReportRobotPose", space.own_idx),
        report_robot_pose,
        [ua.VariantType.String],
        [ua.VariantType.Int32],
    )
    return layer2_run


async def _install_hand_eye_session(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> Any:
    """Legt die Hand-Auge-Methoden an und verdrahtet die Session.

    Nur auf dem Hand-Pi (Knoten nur mit `hand_eye_path`). Siehe
    `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`.
    """
    # Erst hier: die Session braucht numpy, der Rest des Servers nicht.
    from .handeye_session import HandEyePorts, HandEyeSession

    async def publish(status: str) -> None:
        await space.hand_eye_status.write_value(ua.Variant(status, ua.VariantType.String))

    session = HandEyeSession(
        HandEyePorts(
            measure_tag=apriltag_source.measure_tag,
            measure_board=getattr(apriltag_source, "measure_board", None),
            intrinsics_source=lambda: getattr(apriltag_source, "intrinsics_source", None),
            busy=busy,
            path=config.apriltag.hand_eye_path,
            apply=apriltag_source.apply_hand_eye,
            publish=publish,
            frame_id=config.apriltag.frame_id,
        )
    )
    await session.publish_idle()

    method_prefix = config.vision_system_name

    @uamethod
    async def start_hand_eye(parent, options_json: str):
        """1:StartHandEyeCalibration -- Tag waehlen, Sitzung oeffnen."""
        error = await session.start(options_json or "")
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def capture_hand_eye(parent, sample_json: str):
        """1:CaptureHandEyeSample -- gemeldete Flanschpose, Tag messen."""
        error = await session.capture(sample_json or "")
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def finish_hand_eye(parent):
        """1:FinishHandEyeCalibration -- rechnen, speichern, aktivieren."""
        error = await session.finish()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def abort_hand_eye(parent):
        """1:AbortHandEyeCalibration -- verwerfen, nichts speichern."""
        error = await session.abort()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    for name, handler, inputs in (
        ("StartHandEyeCalibration", start_hand_eye, [ua.VariantType.String]),
        ("CaptureHandEyeSample", capture_hand_eye, [ua.VariantType.String]),
        ("FinishHandEyeCalibration", finish_hand_eye, []),
        ("AbortHandEyeCalibration", abort_hand_eye, []),
    ):
        methods[name] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.{name}", space.own_idx),
            ua.QualifiedName(name, space.own_idx),
            handler,
            inputs,
            [ua.VariantType.Int32],
        )
    return session


async def _install_measure_board(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
) -> None:
    """Legt MeasureBoard an: das Kalibrier-Schachbrett messen, damit die
    Steuerung es vor der Hand-Auge-Aufnahme zentrieren und die Kalibrierfahrt
    der Deckenkamera vor jeder Aufnahme pruefen kann. An beiden Pis. Antwortet
    erst nach der Messung, das Ergebnis kommt direkt zurueck.
    Siehe `doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`.
    """
    from .handeye_session import _parse_board
    from .measureboard import RESULT_SCHEMA, dumps, parse_request

    def refusal(code: VisionErrorCode, message: str):
        result = {"schema": RESULT_SCHEMA, "found": False, "error": code.name, "message": message}
        return (
            ua.Variant(dumps(result), ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def measure_board(parent, request_json: str):
        """1:MeasureBoard -- Brett messen, nur in Kamerakoordinaten."""
        try:
            request = parse_request(request_json or "", _parse_board)
        except ValueError as error:
            _log.warning("MeasureBoard abgelehnt: %s", error)
            return refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        if busy():
            return refusal(VisionErrorCode.BUSY, "Job oder Auswertung der Kalibrierung laeuft")
        result = await apriltag_source.measure_board_view(request)
        payload = dumps(result)
        if result["error"]:
            code = VisionErrorCode.DETECTION_FAILED
        else:
            code = VisionErrorCode.OK
        return (
            ua.Variant(payload, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    methods["MeasureBoard"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.MeasureBoard", space.own_idx),
        ua.QualifiedName("MeasureBoard", space.own_idx),
        measure_board,
        [ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.Int32],
    )


class _Activity:
    """Merkt, ob gerade eine Messung mit frischen Bildern laeuft.

    Einfaches Flag: der Server ist single-threaded (asyncio), zwischen Pruefen
    und Setzen liegt kein `await`.
    """

    def __init__(self) -> None:
        self.active = False


async def _install_measure_tags(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
    *,
    activity: _Activity | None = None,
    blocked_by: Callable[[], bool] = lambda: False,
) -> None:
    """Legt MeasureTags an: Tags in Kamerakoordinaten fuer die Regelung der
    Steuerung. Nur auf dem Hand-Pi (Knoten nur mit `hand_eye_path`). Antwortet
    erst nach der Messung, das Ergebnis kommt direkt zurueck; der Knoten
    `LastTagMeasurement` ist nur zur Anzeige. Siehe
    `doc/projektdoku/arbeitsplaene/layer2-measuretags.md`.
    """
    from .measuretag import RESULT_SCHEMA, dumps, parse_request

    activity = activity if activity is not None else _Activity()

    def refusal(code: VisionErrorCode, message: str):
        result = {"schema": RESULT_SCHEMA, "tags": [], "error": f"{code.name}: {message}"}
        return (
            ua.Variant(dumps(result), ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def measure_tags(parent, request_json: str):
        """1:MeasureTags -- Tags messen, nur in Kamerakoordinaten."""
        try:
            request = parse_request(request_json or "")
        except ValueError as error:
            _log.warning("MeasureTags abgelehnt: %s", error)
            return refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        if busy() or blocked_by():
            return refusal(VisionErrorCode.BUSY, "Job, Kalibrierung oder Tag-Einmessung laeuft")
        # Solange gemessen wird, haelt das Stream-Overlay an (siehe unten).
        apriltag_source.measure_calls += 1
        activity.active = True
        try:
            result = await apriltag_source.measure_tags(request)
        finally:
            activity.active = False
            apriltag_source.measure_calls -= 1
        payload = dumps(result)
        try:
            await space.last_tag_measurement.write_value(
                ua.Variant(payload, ua.VariantType.String)
            )
        except Exception:
            _log.exception("LastTagMeasurement konnte nicht geschrieben werden")
        code = VisionErrorCode.DETECTION_FAILED if result["error"] else VisionErrorCode.OK
        return (
            ua.Variant(payload, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    methods["MeasureTags"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.MeasureTags", space.own_idx),
        ua.QualifiedName("MeasureTags", space.own_idx),
        measure_tags,
        [ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.Int32],
    )


async def _install_tag_reference(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
    *,
    activity: _Activity | None = None,
    blocked_by: Callable[[], bool] = lambda: False,
    annotator: Any = None,
) -> None:
    """Legt MeasureTagReference, PlanTagReferenceView und SetTagReferencePreview an: Tag-Position eines
    Moduls ueber Bezugskanten einmessen. Nur auf dem Hand-Pi (Knoten nur mit
    `hand_eye_path`). Beide antworten direkt mit dem Ergebnis; der Knoten
    `LastTagReference` ist nur zur Anzeige.

    * `MeasureTagReference` braucht frische Kamerabilder: `Operational`,
      sonst `INVALID_STATE`; `BUSY` bei Job, Kamera-Kalibrierung,
      Hand-Auge-Sitzung, `MeasureTags` oder einer weiteren Einmessung.
    * `PlanTagReferenceView` rechnet nur mit der Kalibrierung, ohne Bild:
      braucht `Operational` (erst dann ist die Kalibrierung geladen), ist aber
      nie `BUSY` -- sie stoert weder Job noch Kalibrierung noch Messung.

    * `SetTagReferencePreview(RequestJson) -> Error` schaltet die Live-Vorschau
      der Kantensuche im Livestream (Overlay-Modus `tagreference`) fuer einen
      Tag ein; `""` schaltet sie aus. `INVALID_ARGUMENT`, `INVALID_STATE`
      (nicht `Operational` oder kein Livestream-Overlay), nie `BUSY`.
      `MeasureTagReference` setzt die Vorschau selbst auf ihre Anfrage und
      schreibt, wenn `saveDebug` (Standard `true`), ein Mess-Protokoll
      (`tagreference_log.py`, `debugDir` im Ergebnis).

    Siehe `doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md`.
    """
    from .tagreference import build_result, build_view, dumps, parse_request
    from .tagreference_log import write_protocol

    if annotator is not None and hasattr(annotator, "show_reference_debug"):
        # Die Messung zeigt dem Livestream ihre Kantensuche Bild fuer Bild.
        apriltag_source.reference_debug_sink = annotator.show_reference_debug
    else:
        annotator = None

    activity = activity if activity is not None else _Activity()

    def reply(payload: dict, code: VisionErrorCode):
        return (
            ua.Variant(dumps(payload), ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    def refusal(code: VisionErrorCode, message: str):
        return reply(build_result(error=f"{code.name}: {message}"), code)

    def view_refusal(code: VisionErrorCode, message: str):
        return reply(build_view(error=f"{code.name}: {message}"), code)

    @uamethod
    async def measure_tag_reference(parent, request_json: str):
        """1:MeasureTagReference -- Tag-Position ueber Bezugskanten einmessen."""
        try:
            request = parse_request(request_json or "")
        except ValueError as error:
            _log.warning("MeasureTagReference abgelehnt: %s", error)
            return refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        if busy() or blocked_by():
            return refusal(
                VisionErrorCode.BUSY,
                "Job, Kalibrierung, Hand-Auge-Sitzung, MeasureTags oder Tag-Einmessung laeuft",
            )
        activity.active = True
        if annotator is not None:
            annotator.set_reference_preview(request)
            annotator.set_reference_measuring(True)
        artifacts: dict = {}
        try:
            result = await apriltag_source.measure_tag_reference(request, artifacts)
        except ValueError as error:
            # z. B. Bildgroesse passt nicht zur Kalibrierung
            _log.warning("MeasureTagReference nicht moeglich: %s", error)
            return refusal(VisionErrorCode.DETECTION_FAILED, str(error))
        finally:
            activity.active = False
            if annotator is not None:
                annotator.set_reference_measuring(False)
        directory = config.tag_reference_debug_dir
        if request.save_debug and directory is not None:
            try:
                folder = await asyncio.to_thread(
                    write_protocol,
                    directory,
                    request.tag_id,
                    request_json,
                    result,
                    artifacts.get("frames", []),
                    artifacts.get("overlay"),
                )
                try:
                    result["debugDir"] = folder.relative_to(_REPO_ROOT).as_posix()
                except ValueError:
                    result["debugDir"] = folder.as_posix()
                # result.json im Ordner kennt debugDir nicht -- nachtragen.
                await asyncio.to_thread(
                    (folder / "result.json").write_text,
                    dumps(result),
                    "utf-8",
                )
            except Exception:
                _log.warning("Mess-Protokoll nicht geschrieben", exc_info=True)
        payload = dumps(result)
        try:
            await space.last_tag_reference.write_value(ua.Variant(payload, ua.VariantType.String))
        except Exception:
            _log.exception("LastTagReference konnte nicht geschrieben werden")
        code = VisionErrorCode.DETECTION_FAILED if result["error"] else VisionErrorCode.OK
        return (
            ua.Variant(payload, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def plan_tag_reference_view(parent, request_json: str):
        """1:PlanTagReferenceView -- Kamerapose im Tag-KS planen, ohne Bild."""
        try:
            request = parse_request(request_json or "")
        except ValueError as error:
            _log.warning("PlanTagReferenceView abgelehnt: %s", error)
            return view_refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return view_refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        try:
            view = apriltag_source.plan_tag_reference_view(request)
        except ValueError as error:
            _log.warning("PlanTagReferenceView abgelehnt: %s", error)
            return view_refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        return reply(view, VisionErrorCode.OK)

    @uamethod
    async def set_tag_reference_preview(parent, request_json: str):
        """1:SetTagReferencePreview -- Live-Vorschau der Kantensuche ein-/ausschalten."""

        def code(value: VisionErrorCode):
            return (ua.Variant(int(value), ua.VariantType.Int32),)

        text = request_json or ""
        request = None
        if text.strip():
            try:
                request = parse_request(text)
            except ValueError as error:
                _log.warning("SetTagReferencePreview abgelehnt: %s", error)
                return code(VisionErrorCode.INVALID_ARGUMENT)
        if states.state_names()[0] != "Operational" or annotator is None:
            return code(VisionErrorCode.INVALID_STATE)
        annotator.set_reference_preview(request)
        return code(VisionErrorCode.OK)

    methods["SetTagReferencePreview"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.SetTagReferencePreview", space.own_idx),
        ua.QualifiedName("SetTagReferencePreview", space.own_idx),
        set_tag_reference_preview,
        [ua.VariantType.String],
        [ua.VariantType.Int32],
    )

    for name, handler in (
        ("MeasureTagReference", measure_tag_reference),
        ("PlanTagReferenceView", plan_tag_reference_view),
    ):
        methods[name] = await space.vision_system.add_method(
            ua.NodeId(f"{config.vision_system_name}.{name}", space.own_idx),
            ua.QualifiedName(name, space.own_idx),
            handler,
            [ua.VariantType.String],
            [ua.VariantType.String, ua.VariantType.Int32],
        )


async def _install_save_raw_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> None:
    """Legt `SaveRawImage` an: aktuelles Kamera-Vollbild als PNG speichern.

    Waehrend eines Jobs abgelehnt: das PNG-Encoding eines 12-MP-Bilds soll
    den Job-Timeout nicht gefaehrden. Siehe
    `doc/projektdoku/arbeitsplaene/rohbild-speichern.md`.
    """
    from .raw_image import save_raw_image

    frame_id = (
        config.apriltag.frame_id if config.apriltag is not None else config.vision_system_name
    )
    directory = config.raw_image_dir

    def reply(name: str, code: VisionErrorCode):
        return (
            ua.Variant(name, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def save_raw_image_method(parent):
        if busy():
            _log.warning("SaveRawImage waehrend laufendem Job abgelehnt")
            return reply("", VisionErrorCode.BUSY)
        frame = camera_source.camera.latest_frame
        if frame is None:
            return reply("", VisionErrorCode.INVALID_STATE)
        try:
            path = await asyncio.to_thread(
                save_raw_image, frame.image, directory, frame_id
            )
        except Exception:
            _log.exception("Rohbild konnte nicht gespeichert werden")
            return reply("", VisionErrorCode.INTERNAL)
        try:
            name = path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            name = path.as_posix()  # ausserhalb des Repos: absoluter Pfad
        _log.info("Rohbild gespeichert: %s", name)
        return reply(name, VisionErrorCode.OK)

    methods["SaveRawImage"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.SaveRawImage", space.own_idx),
        ua.QualifiedName("SaveRawImage", space.own_idx),
        save_raw_image_method,
        [],
        [ua.VariantType.String, ua.VariantType.Int32],
    )


#: Ein Use-Case-Foto muss mindestens so viel juenger als der Methodenaufruf
#: sein (Bewegungsunschaerfe/Nachschwingen der Hand abwarten).
USE_CASE_MIN_FRAME_AGE_S = 0.3
#: Nach so vielen Sekunden ohne frisches Bild gibt `SaveUseCaseImage` auf.
USE_CASE_FRESH_FRAME_TIMEOUT_S = 5.0
USE_CASE_POLL_INTERVAL_S = 0.05


def _load_file_content(path: Path) -> Any:
    """Inhalt einer Konfigurationsdatei: JSON/YAML geparst, sonst Text, sonst None."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml

        return yaml.safe_load(text)
    except Exception:
        return text


def _file_meta(path: Any) -> dict[str, Any]:
    """`{"path", "exists", "content"}` zu einer Datei aus der Config (defensiv)."""
    meta: dict[str, Any] = {"path": str(path)}
    try:
        p = Path(path)
        meta["exists"] = p.is_file()
        if meta["exists"]:
            meta["content"] = _load_file_content(p)
    except Exception:
        pass
    return meta


def _build_use_case_pi_meta(
    config: VisionServerConfig,
    frame: Any,
    frame_id: str,
    now: float,
    calibration: Any = None,
    intrinsics_source: str = "calibration",
) -> dict[str, Any]:
    """Metadaten des Pi fuer die JSON-Datei; jedes Feld einzeln abgesichert."""
    meta: dict[str, Any] = {}

    def put(key: str, getter: Callable[[], Any]) -> None:
        try:
            value = getter()
        except Exception:
            return
        if value is not None:
            meta[key] = value

    put("frame_timestamp", lambda: frame.timestamp)
    put("frame_age_s", lambda: round(now - frame.timestamp, 3))
    put("image_shape", lambda: list(frame.image.shape))
    meta["frame_id"] = frame_id
    meta["vision_system_name"] = config.vision_system_name
    meta["vision_system_id"] = config.vision_system_id
    put("hostname", socket.gethostname)
    meta["endpoint"] = config.endpoint
    put(
        "camera_stream",
        lambda: dataclasses.asdict(config.camera_stream)
        if dataclasses.is_dataclass(config.camera_stream)
        else None,
    )
    if calibration is not None:
        from use_case_eval.live import intrinsics_meta

        put("intrinsics", lambda: intrinsics_meta(calibration, intrinsics_source))
    tag = config.apriltag
    if tag is not None:
        put("apriltag", lambda: dataclasses.asdict(tag))
        put("calibration", lambda: _file_meta(tag.calibration_path))
        if tag.hand_eye_path is not None:
            put("hand_eye", lambda: _file_meta(tag.hand_eye_path))
    return meta


async def _install_save_use_case_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> None:
    """Legt `SaveUseCaseImage` an: frisches Kamera-Vollbild + Metadaten ablegen.

    Eingaben `Series` (Int32, >= 1) und `MetadataJson` (String, vom Client);
    Ausgaben `FileName` (relativ zum Repo), `ResultJson` (Bildmessung als JSON,
    bei Fehler-Codes leer) und `Error`. Wartet auf ein Bild, das mindestens
    `USE_CASE_MIN_FRAME_AGE_S` nach dem Aufruf entstand. Waehrend eines Jobs
    abgelehnt (wie `SaveRawImage`). Ein Messfehler laesst das Speichern nie
    scheitern: er steht als `{"ok": false, "grund": ...}` in der Messung.
    """
    from use_case_eval.live import load_sticker_offset, measure_for_save

    from .use_case_image import save_use_case_image

    frame_id = (
        config.apriltag.frame_id if config.apriltag is not None else config.vision_system_name
    )
    base_dir = config.use_case_dir

    def reply(name: str, code: VisionErrorCode, result_json: str = ""):
        return (
            ua.Variant(name, ua.VariantType.String),
            ua.Variant(result_json, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def save_use_case_image_method(parent, series: int, metadata_json: str):
        if busy():
            _log.warning("SaveUseCaseImage waehrend laufendem Job abgelehnt")
            return reply("", VisionErrorCode.BUSY)
        if series < 1:
            _log.warning("SaveUseCaseImage: ungueltige Reihe %s", series)
            return reply("", VisionErrorCode.INVALID_ARGUMENT)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        deadline = t0 + USE_CASE_FRESH_FRAME_TIMEOUT_S
        frame = None
        while True:
            frame = camera_source.camera.latest_frame
            if frame is not None and frame.timestamp >= t0 + USE_CASE_MIN_FRAME_AGE_S:
                break
            if loop.time() >= deadline:
                _log.warning("SaveUseCaseImage: kein frisches Kamerabild")
                return reply("", VisionErrorCode.INVALID_STATE)
            await asyncio.sleep(USE_CASE_POLL_INTERVAL_S)
        calibration = getattr(camera_source, "calibration", None)
        intrinsics_source = getattr(camera_source, "intrinsics_source", "calibration")
        try:
            pi_meta = _build_use_case_pi_meta(
                config, frame, frame_id, loop.time(), calibration, intrinsics_source
            )
        except Exception:
            _log.exception("Use-Case-Metadaten unvollstaendig")
            pi_meta = {}
        # Erst messen, dann speichern: die Messung steht so schon in der einmalig
        # exklusiv angelegten JSON, nichts wird nachtraeglich umgeschrieben.
        sticker_offset = await asyncio.to_thread(load_sticker_offset, base_dir)
        measurement, annotated = await asyncio.to_thread(
            measure_for_save, frame.image, calibration, sticker_offset
        )
        try:
            path, _trial = await asyncio.to_thread(
                save_use_case_image,
                frame.image, base_dir, series, metadata_json or "", pi_meta,
                None, measurement, annotated,
            )
        except Exception:
            _log.exception("Use-Case-Bild konnte nicht gespeichert werden")
            return reply("", VisionErrorCode.INTERNAL)
        try:
            name = path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            name = path.as_posix()  # ausserhalb des Repos: absoluter Pfad
        _log.info("Use-Case-Bild gespeichert: %s", name)
        return reply(name, VisionErrorCode.OK, json.dumps(measurement, ensure_ascii=False))

    methods["SaveUseCaseImage"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.SaveUseCaseImage", space.own_idx),
        ua.QualifiedName("SaveUseCaseImage", space.own_idx),
        save_use_case_image_method,
        [ua.VariantType.Int32, ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.String, ua.VariantType.Int32],
    )


async def _install_cell_watch(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    results: ResultStore,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy_reason: Callable[[], str],
) -> Any:
    """Legt Start-/Stop-/Pause-/ResumeCellWatch an und verdrahtet die Ueberwachung.

    Nur auf der Deckenkamera (Knoten nur ohne `hand_eye_path`). Liest die
    Frames der geteilten Kamera -- dieselben wie Livestream und Jobs, keine
    eigene Aufnahme -- und rechnet mit eigenem Detektor in einem eigenen
    Thread. Waehrend eines Jobs oder einer Kalibrierung pausiert sie, damit
    ein Layer-1-Job jederzeit volle CPU hat. Siehe
    `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md`.
    """
    # Erst hier: die Ueberwachung braucht numpy/OpenCV, der Rest des Servers nicht.
    from tagloc.detector import build_detector

    from .cellwatch import CameraPoseKeeper, CellWatch, CellWatchPorts

    apriltag = config.apriltag
    detector = build_detector(
        apriltag.tag_family,
        apriltag.detector_backend,
        subpixel_corner_refinement=apriltag.subpixel_corner_refinement,
        corner_refinement_method=getattr(apriltag, "corner_refinement_method", "subpix"),
        apriltag_quad_decimate=apriltag.apriltag_quad_decimate,
        detector_parameters=apriltag.detector_parameters,
    )
    seen = {"timestamp": None}
    #: Die Pose der fest montierten Kamera, einmal aus >= 2 Welttags geschaetzt;
    #: lebt ueber `StartCellWatch`-Aufrufe hinweg (Zeiten aendern, neue Referenz).
    keeper = CameraPoseKeeper(path=apriltag.calibration_path.parent / "cellwatch_camera_pose.json")
    keeper.load()

    async def observe(options):
        frame = apriltag_source.camera.latest_frame
        if frame is None:
            return None, "kein Kamerabild"
        if frame.timestamp == seen["timestamp"]:
            return None, ""  # kein neues Bild, keine Aussage
        seen["timestamp"] = frame.timestamp
        tag_map = apriltag_source._tag_map
        stored = keeper.stored_for(tag_map) if options.min_world_tags <= 1 else None
        locations, localization, visible = await asyncio.to_thread(
            apriltag_source.observe_cell, frame.image, detector, stored
        )
        sightings, reason = keeper.judge(locations, localization, options, tag_map)
        return sightings, reason, visible

    async def publish(status: str) -> None:
        await space.cell_watch_status.write_value(ua.Variant(status, ua.VariantType.String))

    cell_watch = CellWatch(
        CellWatchPorts(
            observe=observe,
            busy_reason=busy_reason,
            result_for_job=results.result_for_job,
            world_frame=lambda: apriltag_source._tag_map.frame_id,
            publish=publish,
            adopt_pose=lambda pose, errors: keeper.adopt(pose, errors, apriltag_source._tag_map),
            pose_info=keeper.info,
        )
    )
    await cell_watch.publish_idle()

    method_prefix = config.vision_system_name

    @uamethod
    async def start_cell_watch(parent, options_json: str):
        """1:StartCellWatch -- scharf schalten bzw. neue Referenz setzen."""
        # Operational reicht: waehrend eines Jobs darf neu scharf geschaltet
        # werden, die Ueberwachung pausiert dann ohnehin.
        if states.state_names()[0] != "Operational":
            return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
        error = await cell_watch.start(options_json or "")
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def stop_cell_watch(parent):
        """1:StopCellWatch -- Ueberwachung aus."""
        error = await cell_watch.stop()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def pause_cell_watch(parent, reason_text: str, max_pause_s: float):
        """1:PauseCellWatch -- pausieren, solange der Roboter faehrt."""
        error = await cell_watch.pause(reason_text or "", max_pause_s)
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def resume_cell_watch(parent):
        """1:ResumeCellWatch -- Pause beenden."""
        error = await cell_watch.resume()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    for name, handler, inputs in (
        ("StartCellWatch", start_cell_watch, [ua.VariantType.String]),
        ("StopCellWatch", stop_cell_watch, []),
        ("PauseCellWatch", pause_cell_watch, [ua.VariantType.String, ua.VariantType.Double]),
        ("ResumeCellWatch", resume_cell_watch, []),
    ):
        methods[name] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.{name}", space.own_idx),
            ua.QualifiedName(name, space.own_idx),
            handler,
            inputs,
            [ua.VariantType.Int32],
        )
    return cell_watch


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
