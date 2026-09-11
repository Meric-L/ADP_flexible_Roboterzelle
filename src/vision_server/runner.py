"""Zusammenbau des Vision-Systems, als Einbau oder als eigener Prozess."""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Mapping
from dataclasses import dataclass

from asyncua import Server, ua, uamethod

from .address_space import VisionAddressSpace, attach_vision_system, configure_server
from .camera_stream import CameraStreamPublisher
from .config import VisionServerConfig
from .detection import DetectionSource, build_detection_sources
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


def _start_camera_stream(
    space: VisionAddressSpace,
    sources: Mapping[str, DetectionSource],
    *,
    image_recognition_opened: bool,
) -> CameraStreamPublisher | None:
    """Startet den Livestream-Publisher, falls konfiguriert und Kamera bereit.

    Nutzt bewusst dieselbe `SharedCamera`, die auch die QR-Erkennung offen
    haelt (`ImageRecognitionDetectionSource.camera`) — eine zweite Kamera
    lohnt sich hier nicht, siehe `camera.py`.
    """
    if space.latest_camera_frame is None:
        return None
    source = sources.get("image_recognition")
    if source is None or not image_recognition_opened:
        _log.error(
            "Livestream-Knoten konfiguriert, aber Kamera-Quelle fehlt oder nicht bereit "
            "-- kein Stream"
        )
        return None
    stream = CameraStreamPublisher(
        source.camera, space.latest_camera_frame, space.config.camera_stream
    )
    stream.start()
    return stream


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

    async def aclose(self) -> None:
        """Faehrt Watchdog, Livestream, laufenden Job und Quelle herunter.

        Best-Effort und idempotent; braucht im Aufrufer einen Signal-Handler,
        sonst laeuft es unter systemd nicht.
        """
        if self.lag_watchdog is not None:
            self.lag_watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.lag_watchdog
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

    camera_stream = _start_camera_stream(
        space, sources, image_recognition_opened=opened.get("image_recognition", False)
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
