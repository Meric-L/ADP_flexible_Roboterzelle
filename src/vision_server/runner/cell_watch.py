"""Zellen-Ueberwachung (Deckenkamera): Start, Stop, Pause, Resume."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..result_management import ResultStore
from ..state_machine import VisionStateMachines

_log = logging.getLogger(__package__)


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
