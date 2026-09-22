"""Zusammenbau des Vision-Systems, als Einbau oder als eigener Prozess."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from asyncua import Server, ua, uamethod

from .address_space import VisionAddressSpace, attach_vision_system, configure_server
from .asset_model import VisionAssetNodes, attach_asset_model
from .camera_stream import CameraStreamPublisher
from .mjpeg_server import MjpegServer
from .config import VisionServerConfig
from .detection import DetectionSource, build_detection_sources
from .errors import VisionErrorCode
from .events import VisionEvents, create_event_generators
from .job import JobRunner
from .result_management import ResultStore
from .state_machine import VisionStateMachines

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
    )


def _safe_build_annotator(source: DetectionSource):
    """`_build_annotator`, but a failure only costs the markup, never the stream."""
    try:
        return _build_annotator(source)
    except Exception:
        _log.exception("Stream-Overlay nicht verfuegbar, Stream laeuft ohne Markierung")
        return None


async def _camera_source(
    sources: Mapping[str, DetectionSource], opened: Mapping[str, bool]
) -> DetectionSource | None:
    """Return the source whose camera feeds the livestream and the remote calibration.

    An opened source first. Failing that, a source that holds a camera but
    could not open -- in practice the AprilTag source without a calibration
    file. Its camera is then opened on its own: the operator needs the image
    exactly in that situation, to calibrate. The source itself stays closed
    and the system in Preoperational until a calibration exists.
    """
    owner = _camera_owner(sources, opened)
    if owner is not None:
        return owner
    for profile in sorted(sources):
        camera = getattr(sources[profile], "camera", None)
        if camera is None:
            continue
        try:
            await camera.open()
        except Exception:
            _log.exception("Kamera von Profil '%s' laesst sich nicht oeffnen", profile)
            return None
        _log.warning(
            "Profil '%s' ist nicht betriebsbereit; Kamera trotzdem geoeffnet, damit "
            "Livestream und Kalibrierung laufen",
            profile,
        )
        return sources[profile]
    return None


def _start_camera_stream(
    space: VisionAddressSpace, source: DetectionSource, annotator: Any
) -> CameraStreamPublisher:
    """Startet den Livestream-Publisher auf der Kamera der Quelle.

    Nutzt dieselbe `SharedCamera`, die auch die Erkennung offen haelt — eine
    zweite Kamera lohnt sich hier nicht, siehe `camera.py`.
    """
    stream = CameraStreamPublisher(
        source.camera,
        space.latest_camera_frame,
        space.config.camera_stream,
        annotator=annotator,
        mode_node=space.camera_stream_mode,
    )
    stream.start()
    _log.info(
        "Livestream aus Profil '%s'%s",
        source.profile_id,
        " mit Overlay" if getattr(annotator, "base", None) is not None else " ohne Overlay",
    )
    return stream


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


def _calibration_handler(command, arity: int):
    """Wrap a session command as OPC-UA method: `(Error: Int32, Message: String)`.

    Checks the argument count itself -- a TypeError from inside the command
    must surface as INTERNAL, not be mistaken for a wrong call.
    """

    @uamethod
    async def handler(parent, *args):
        if len(args) != arity:
            code, message = (
                VisionErrorCode.INVALID_ARGUMENT,
                f"Erwartet {arity} Argument(e), erhalten {len(args)}.",
            )
        else:
            try:
                code, message = await command(*args)
            except Exception:
                _log.exception("Kalibrierbefehl fehlgeschlagen")
                code, message = VisionErrorCode.INTERNAL, "Interner Fehler, siehe Server-Log."
        return (
            ua.Variant(int(code), ua.VariantType.Int32),
            ua.Variant(message, ua.VariantType.String),
        )

    return handler


async def _install_calibration(
    server: Server,
    space: VisionAddressSpace,
    states: VisionStateMachines,
    source: DetectionSource,
    sources: Mapping[str, DetectionSource],
    opened: dict[str, bool],
    annotator: Any,
):
    """Wire the remote calibration to the camera source and link its methods.

    `None` if the source does not use a calibration file. After `Save`, the
    source takes the new file: reloaded if it was running, opened for the
    first time if it was waiting for exactly this -- and then the system
    leaves Preoperational.
    """
    nodes = space.calibration
    calibration_path = getattr(source, "calibration_path", None)
    if nodes is None or calibration_path is None:
        return None
    from .calibration_session import CalibrationSession

    profile = next(name for name, candidate in sources.items() if candidate is source)

    async def apply_calibration() -> tuple[bool, str]:
        if opened.get(profile):
            await source.reload_calibration()
        else:
            opened[profile] = await _open_source(source)
            if not opened[profile]:
                return (
                    False,
                    "Gespeichert, aber die Erkennung startet noch nicht. Ursache im Server-Log.",
                )
        if annotator.base is None:
            annotator.base = _safe_build_annotator(source)
        else:
            annotator.base.set_calibration(source.calibration)
        outer_state, _ = states.state_names()
        if all(opened.values()) and outer_state in ("", "Preoperational"):
            await states.enter_operational()
            return True, "Kalibrierung übernommen. Das Vision-System ist jetzt betriebsbereit."
        return True, "Kalibrierung übernommen."

    async def publish(text: str) -> None:
        await nodes.status.write_value(ua.Variant(text, ua.VariantType.String))

    session = CalibrationSession(
        source.camera,
        calibration_path=calibration_path,
        frame_id=getattr(source, "calibration_frame_id", ""),
        publish=publish,
        on_saved=apply_calibration,
        mode_node=space.camera_stream_mode,
    )
    for node, command, arity in (
        (nodes.start, session.start, 1),
        (nodes.capture, session.capture, 0),
        (nodes.compute, session.compute, 0),
        (nodes.save, session.save, 0),
        (nodes.cancel, session.cancel, 0),
    ):
        server.link_method(node, _calibration_handler(command, arity))
    await session.refresh_current()
    _log.info("Fernkalibrierung bereit, Ziel %s", calibration_path)
    return session


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
    #: Remote calibration (`calibration_session.CalibrationSession`); `None`
    #: without a camera.
    calibration: Any = None

    async def aclose(self) -> None:
        """Faehrt Watchdog, Livestream, laufenden Job und Quelle herunter.

        Best-Effort und idempotent; braucht im Aufrufer einen Signal-Handler,
        sonst laeuft es unter systemd nicht.
        """
        if self.lag_watchdog is not None:
            self.lag_watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.lag_watchdog
        if self.camera_http is not None:
            await self.camera_http.stop()
        if self.camera_stream is not None:
            await self.camera_stream.stop()
        if self.calibration is not None:
            await self.calibration.close()
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

    camera_stream = None
    camera_http = None
    calibration = None
    if space.latest_camera_frame is not None:
        camera_source = await _camera_source(sources, opened)
        if camera_source is None:
            _log.error(
                "Livestream-Knoten konfiguriert, aber keine Quelle haelt eine nutzbare "
                "Kamera -- kein Stream, keine Kalibrierung"
            )
        else:
            # Imported here: the session pulls numpy in via tagloc, and a server
            # without a camera must not need it.
            from .calibration_session import StreamAnnotator

            annotator = StreamAnnotator(base=_safe_build_annotator(camera_source))
            calibration = await _install_calibration(
                server, space, states, camera_source, sources, opened, annotator
            )
            annotator.session = calibration
            camera_stream = _start_camera_stream(space, camera_source, annotator)
            camera_http = await _start_mjpeg_server(space, camera_stream)

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
        camera_http=camera_http,
        assets=assets,
        calibration=calibration,
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
