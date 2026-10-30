"""Einbau des Vision-Systems in einen Server: `install_vision_machine`.

Die Funktion baut nur die Reihenfolge der Schritte auf; jeder Schritt steht in
einem eigenen Modul dieses Pakets (siehe `__init__`). Die Reihenfolge ist
Teil der Schnittstelle: davon haengen die Reihenfolge der Methoden-Anlage und
damit die Eintraege unter `VisionProgram` ab.
"""

import asyncio
import logging

from asyncua import Server

from ..address_space import attach_vision_system
from ..asset_model import VisionAssetNodes, attach_asset_model
from ..camera_health import CameraHealthPublisher
from ..config import VisionServerConfig
from ..detection import build_detection_sources
from ..events import create_event_generators
from ..job import JobRunner
from ..mjpeg_server import MjpegServer
from ..result_management import ResultStore
from ..state_machine import VisionStateMachines
from .calibration import (
    _build_calibration_session,
    connect_live_calibration,
    install_calibration_methods,
    write_initial_calibration_info,
)
from .cell_watch import _install_cell_watch
from .hand_eye import _install_hand_eye_session
from .health import _start_camera_health
from .jobs import install_job_methods
from .layer2 import _install_layer2_run
from .livestream import _start_camera_stream, _start_mjpeg_server
from .machine import VisionMachine, _watch_loop_lag
from .measure_board import _install_measure_board
from .measure_tags import _Activity, _install_measure_tags
from .part10 import install_part10_program
from .save_raw_image import _install_save_raw_image
from .save_use_case_image import _install_save_use_case_image
from .sources import _camera_owner, _open_source
from .tag_map import install_tag_map
from .tag_reference import _install_tag_reference
from .wiring import Wiring

_log = logging.getLogger(__package__)


async def _open_sources(wiring: Wiring) -> None:
    """Oeffnet alle Quellen und geht, wenn alle aufgingen, nach `Operational`.

    Erst oeffnen, dann Operational: `Ready` soll "Hardware bereit" heissen.
    Die Methoden bleiben verlinkt, sonst antwortet der Server BadNothingToDo.
    """
    sources = wiring.sources
    wiring.opened = {profile: await _open_source(source) for profile, source in sources.items()}
    if all(wiring.opened.values()):
        await wiring.states.enter_operational()
    else:
        _log.error(
            "Vision-System '%s' bleibt in Preoperational, Quelle nicht bereit",
            wiring.config.vision_system_name,
        )
    wiring.apriltag_source = sources.get("apriltag") if wiring.opened.get("apriltag") else None


async def _start_services(
    wiring: Wiring, assets: VisionAssetNodes | None
) -> tuple[CameraHealthPublisher | None, MjpegServer | None]:
    """Kalibrier-Session, Livestream, Kamerazustand und MJPEG-Server starten."""
    space, sources, opened = wiring.space, wiring.sources, wiring.opened
    wiring.calibration_session = _build_calibration_session(sources, opened, wiring.config)
    wiring.camera_stream, wiring.annotator = _start_camera_stream(space, sources, opened)
    annotator = wiring.annotator
    if annotator is not None and hasattr(annotator, "set_busy"):
        # Wie die Zellen-Ueberwachung: waehrend Job oder Kalibrierung keine
        # eigene Overlay-Erkennung, der Job soll die CPU fuer sich haben.
        annotator.set_busy(lambda: wiring.jobs.busy or wiring.calibration_busy())
    camera_health = await _start_camera_health(space, assets, sources, opened)
    camera_http = (
        await _start_mjpeg_server(space, wiring.camera_stream)
        if wiring.camera_stream is not None
        else None
    )
    return camera_health, camera_http


async def _install_hand_pi_sessions(wiring: Wiring) -> None:
    """Layer-2-Lauf und Hand-Auge-Sitzung (nur Hand-Pi), dazu die Overlay-Pause."""
    space, config, source = wiring.space, wiring.config, wiring.apriltag_source
    if source is not None and space.layer2_target is not None and space.layer2_status is not None:
        wiring.layer2_run = await _install_layer2_run(
            space, config, wiring.jobs, source, wiring.results, wiring.methods,
            lambda: wiring.calibration_busy() or wiring.hand_eye_active(),
        )
    if source is not None and space.hand_eye_status is not None:
        wiring.hand_eye_session = await _install_hand_eye_session(
            space, config, source, wiring.methods,
            lambda: wiring.jobs.busy or wiring.calibration_busy() or wiring.layer2_active(),
        )
    annotator = wiring.annotator
    if annotator is not None and hasattr(annotator, "set_overlay_paused") and source is not None:
        # Auf dem Hand-Pi wuerde das Overlay waehrend einer Messung oder
        # Hand-Auge-Sitzung in voller Aufloesung mitrechnen, die CPU gehoert
        # der Messung. Der Stream zeigt dann das Rohbild.
        annotator.set_overlay_paused(
            lambda: source.measure_calls > 0 or wiring.hand_eye_active()
        )


async def _install_measurements(wiring: Wiring) -> None:
    """MeasureBoard, MeasureTags und die Tag-Einmessung."""
    space, config, source = wiring.space, wiring.config, wiring.apriltag_source
    if source is None:
        return
    # MeasureBoard an beiden Pis. Es liest nur Kamerabilder und aendert keinen
    # Zustand, laeuft deshalb auch waehrend einer Hand-Auge-Sitzung (die
    # Steuerung zentriert das Brett damit vor `CaptureHandEyeSample`) und in
    # der Aufnahmephase einer Kamera-Kalibrierung (die automatische
    # Kalibrierfahrt nimmt nur auf, wenn das ganze Brett im Bild ist).
    # Gesperrt fuer Jobs und waehrend der Auswertung der Kalibrierung.
    await _install_measure_board(
        space, config, source, wiring.methods, wiring.states,
        lambda: wiring.jobs.busy or wiring.calibration_processing(),
    )
    if space.last_tag_measurement is None:
        return
    # MeasureTags und MeasureTagReference nehmen beide frische Bilder; sie
    # sperren sich gegenseitig (und MeasureTagReference auch sich selbst).
    tags_activity = _Activity()
    reference_activity = _Activity()

    def measuring_busy() -> bool:
        return wiring.jobs.busy or wiring.calibration_busy()

    # MeasureTags laeuft auch WAEHREND einer Hand-Auge-Sitzung: Es liest nur
    # Kamerabilder und aendert keinen Zustand. Eine haengengebliebene Sitzung
    # soll Layer 2 nicht sperren. Gesperrt nur fuer Job und Kamera-Kalibrierung.
    # MeasureTagReference faehrt den Roboter nicht, misst aber laenger; eine
    # Hand-Auge-Sitzung sperrt es weiterhin.
    def reference_busy() -> bool:
        return measuring_busy() or wiring.hand_eye_active()

    await _install_measure_tags(
        space, config, source, wiring.methods, wiring.states,
        measuring_busy,
        activity=tags_activity,
        blocked_by=lambda: reference_activity.active,
    )
    if space.last_tag_reference is not None:
        await _install_tag_reference(
            space, config, source, wiring.methods, wiring.states,
            reference_busy,
            activity=reference_activity,
            blocked_by=lambda: tags_activity.active or reference_activity.active,
            annotator=wiring.annotator,
        )


async def _install_image_saves(wiring: Wiring) -> None:
    """`SaveRawImage` und `SaveUseCaseImage` (brauchen eine Kamera)."""
    space, config = wiring.space, wiring.config
    camera_owner = _camera_owner(wiring.sources, wiring.opened)
    if camera_owner is not None and config.raw_image_dir is not None:
        await _install_save_raw_image(
            space, config, camera_owner, wiring.methods, lambda: wiring.jobs.busy
        )
    if camera_owner is not None and config.use_case_dir is not None:
        await _install_save_use_case_image(
            space, config, camera_owner, wiring.methods, lambda: wiring.jobs.busy
        )


async def _install_cell_watch_step(wiring: Wiring):
    """Zellen-Ueberwachung (nur Deckenkamera); `None`, wenn es sie nicht gibt."""
    space, source = wiring.space, wiring.apriltag_source
    if source is None or space.cell_watch_status is None:
        return None

    def busy_reason() -> str:
        if wiring.jobs.busy:
            return "Job laeuft"
        return "Kamera-Kalibrierung laeuft" if wiring.calibration_busy() else ""

    return await _install_cell_watch(
        space, wiring.config, source, wiring.results, wiring.methods, wiring.states, busy_reason
    )


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
    wiring = Wiring(server, space, config, states, results, sources, jobs)

    install_job_methods(wiring)
    await _open_sources(wiring)
    camera_health, camera_http = await _start_services(wiring, assets)
    await write_initial_calibration_info(wiring)
    connect_live_calibration(wiring)
    await install_calibration_methods(wiring)
    await install_tag_map(wiring)
    await _install_hand_pi_sessions(wiring)
    await _install_measurements(wiring)
    await _install_image_saves(wiring)
    cell_watch = await _install_cell_watch_step(wiring)
    program = await install_part10_program(wiring)

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
        camera_stream=wiring.camera_stream,
        camera_health=camera_health,
        camera_http=camera_http,
        assets=assets,
        program=program,
        calibration_session=wiring.calibration_session,
        layer2_run=wiring.layer2_run,
        hand_eye_session=wiring.hand_eye_session,
        cell_watch=cell_watch,
    )
