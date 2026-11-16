"""Install the vision system into a server: `install_vision_machine`.

The function only builds the sequence of steps; each step lives in its own
module of this package (see `__init__`). The order is part of the interface:
the order in which methods are created, and thus the entries under
`VisionProgram`, depend on it.
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
    """Open all sources; if all of them open, move to `Operational`.

    Open first, then Operational: `Ready` should mean "hardware ready". The
    methods stay linked, otherwise the server answers BadNothingToDo.
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
    """Start the calibration session, live stream, camera health and MJPEG server."""
    space, sources, opened = wiring.space, wiring.sources, wiring.opened
    wiring.calibration_session = _build_calibration_session(sources, opened, wiring.config)
    wiring.camera_stream, wiring.annotator = _start_camera_stream(space, sources, opened)
    annotator = wiring.annotator
    if annotator is not None and hasattr(annotator, "set_busy"):
        # As with the cell watch: during a job or calibration there is no
        # separate overlay detection; the job should have the CPU to itself.
        annotator.set_busy(lambda: wiring.jobs.busy or wiring.calibration_busy())
    camera_health = await _start_camera_health(space, assets, sources, opened)
    camera_http = (
        await _start_mjpeg_server(space, wiring.camera_stream)
        if wiring.camera_stream is not None
        else None
    )
    return camera_health, camera_http


async def _install_hand_pi_sessions(wiring: Wiring) -> None:
    """Layer 2 run and hand-eye session (hand-camera Pi only), plus the overlay pause."""
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
            annotator=wiring.annotator,
        )
    annotator = wiring.annotator
    if annotator is not None and hasattr(annotator, "set_overlay_paused") and source is not None:
        # On the hand-camera Pi the overlay would otherwise be computed at full
        # resolution during a measurement or hand-eye session; the CPU belongs to
        # the measurement. The stream then shows the raw image.
        annotator.set_overlay_paused(
            lambda: source.measure_calls > 0 or wiring.hand_eye_active()
        )


async def _install_measurements(wiring: Wiring) -> None:
    """Install MeasureBoard, MeasureTags and the tag measurement."""
    space, config, source = wiring.space, wiring.config, wiring.apriltag_source
    if source is None:
        return
    # MeasureBoard runs on both Pis. It only reads camera images and changes no
    # state, so it also runs during a hand-eye session (the controller centers
    # the board with it before `CaptureHandEyeSample`) and during the capture
    # phase of a camera calibration (the automatic calibration drive only
    # records when the whole board is in view). Blocked for jobs and while the
    # calibration is being evaluated.
    await _install_measure_board(
        space, config, source, wiring.methods, wiring.states,
        lambda: wiring.jobs.busy or wiring.calibration_processing(),
    )
    if space.last_tag_measurement is None:
        return
    # MeasureTags and MeasureTagReference both take fresh images; they block
    # each other (and MeasureTagReference also blocks itself).
    tags_activity = _Activity()
    reference_activity = _Activity()

    def measuring_busy() -> bool:
        return wiring.jobs.busy or wiring.calibration_busy()

    # MeasureTags also runs DURING a hand-eye session: it only reads camera
    # images and changes no state. A stuck session should not block Layer 2.
    # Blocked only for a job and a camera calibration. MeasureTagReference does
    # not move the robot, but it takes longer; a hand-eye session still blocks it.
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
    """Install `SaveRawImage` and `SaveUseCaseImage` (they need a camera)."""
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
    """Install the cell watch (ceiling camera only); `None` if there is none."""
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
    """Build the vision system into an initialized server.

    Must run after `server.init()` and before `server.start()`. The server may
    have any other nodes of its own -- endpoint, ApplicationURI and ServerName
    stay untouched.
    """
    space = await attach_vision_system(server, config)
    # Part 2 before the state machines: it depends on nothing and should be up
    # even if the job path never reaches Operational -- that is exactly when the
    # question "which camera, which state" is interesting.
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
