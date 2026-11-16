"""Cell watch (ceiling camera): start, stop, pause and resume.

The cell watch checks the cell with the ceiling camera and exposes four OPC UA
methods to control it.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..result_management import ResultStore
from ..state_machine import VisionStateMachines
from .uamethods import add_vision_method, code_reply


def _make_observe(apriltag_source: Any, detector: Any, keeper: Any):
    """Build the observation function that the cell watch calls on each pass.

    Reads the newest frame of the shared camera, evaluates it with the cell
    watch's own detector in a thread, and lets `keeper` judge the result. It
    remembers the timestamp of the last frame: without a new frame there is no
    verdict.
    """
    seen = {"timestamp": None}

    async def observe(options):
        frame = apriltag_source.camera.latest_frame
        if frame is None:
            return None, "kein Kamerabild"
        if frame.timestamp == seen["timestamp"]:
            return None, ""  # no new frame, so no verdict
        seen["timestamp"] = frame.timestamp
        tag_map = apriltag_source._tag_map
        stored = keeper.stored_for(tag_map) if options.min_world_tags <= 1 else None
        locations, localization, visible = await asyncio.to_thread(
            apriltag_source.observe_cell, frame.image, detector, stored
        )
        sightings, reason = keeper.judge(locations, localization, options, tag_map)
        return sightings, reason, visible

    return observe


def _cell_watch_handlers(cell_watch: Any, states: VisionStateMachines):
    """Return the four handlers as `(name, handler, inputs)`, in creation order."""

    @uamethod
    async def start_cell_watch(parent, options_json: str):
        """1:StartCellWatch -- arm the watch, or set a new reference."""
        # Operational is enough: the watch may be armed during a job, since it
        # pauses during a job anyway.
        if states.state_names()[0] != "Operational":
            return code_reply(VisionErrorCode.INVALID_STATE)
        return code_reply(await cell_watch.start(options_json or ""))

    @uamethod
    async def stop_cell_watch(parent):
        """1:StopCellWatch -- switch the watch off."""
        return code_reply(await cell_watch.stop())

    @uamethod
    async def pause_cell_watch(parent, reason_text: str, max_pause_s: float):
        """1:PauseCellWatch -- pause while the robot is moving."""
        return code_reply(await cell_watch.pause(reason_text or "", max_pause_s))

    @uamethod
    async def resume_cell_watch(parent):
        """1:ResumeCellWatch -- end the pause."""
        return code_reply(await cell_watch.resume())

    return (
        ("StartCellWatch", start_cell_watch, [ua.VariantType.String]),
        ("StopCellWatch", stop_cell_watch, []),
        ("PauseCellWatch", pause_cell_watch, [ua.VariantType.String, ua.VariantType.Double]),
        ("ResumeCellWatch", resume_cell_watch, []),
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
    """Create the Start/Stop/Pause/ResumeCellWatch methods and wire up the watch.

    Only on the ceiling camera (nodes exist only without `hand_eye_path`). It reads
    the frames of the shared camera -- the same ones used by the live stream and
    jobs, with no separate capture -- and computes with its own detector in its
    own thread. It pauses during a job or a calibration, so that a layer-1 job
    always has the full CPU. See
    `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md`.
    """
    # Imported only here: the watch needs numpy/OpenCV, the rest of the server does not.
    from tagloc.detector import build_detector

    from ..cellwatch import CameraPoseKeeper, CellWatch, CellWatchPorts

    apriltag = config.apriltag
    detector = build_detector(
        apriltag.tag_family,
        apriltag.detector_backend,
        subpixel_corner_refinement=apriltag.subpixel_corner_refinement,
        corner_refinement_method=getattr(apriltag, "corner_refinement_method", "subpix"),
        apriltag_quad_decimate=apriltag.apriltag_quad_decimate,
        detector_parameters=apriltag.detector_parameters,
    )
    #: Pose of the fixed-mounted camera, estimated once from >= 2 world tags;
    #: persists across `StartCellWatch` calls (changing timing, new reference).
    keeper = CameraPoseKeeper(path=apriltag.calibration_path.parent / "cellwatch_camera_pose.json")
    keeper.load()

    async def publish(status: str) -> None:
        await space.cell_watch_status.write_value(ua.Variant(status, ua.VariantType.String))

    cell_watch = CellWatch(
        CellWatchPorts(
            observe=_make_observe(apriltag_source, detector, keeper),
            busy_reason=busy_reason,
            result_for_job=results.result_for_job,
            world_frame=lambda: apriltag_source._tag_map.frame_id,
            publish=publish,
            adopt_pose=lambda pose, errors: keeper.adopt(pose, errors, apriltag_source._tag_map),
            pose_info=keeper.info,
        )
    )
    await cell_watch.publish_idle()

    for name, handler, inputs in _cell_watch_handlers(cell_watch, states):
        await add_vision_method(
            space, config, methods, name, handler, inputs, [ua.VariantType.Int32]
        )
    return cell_watch
