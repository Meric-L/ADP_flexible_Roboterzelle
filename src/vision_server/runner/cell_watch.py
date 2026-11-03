"""Zellen-Ueberwachung (Deckenkamera): Start, Stop, Pause, Resume."""

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
    """Die Beobachtung, die die Ueberwachung je Durchlauf aufruft.

    Liest das neueste Bild der geteilten Kamera, wertet es mit dem eigenen
    Detektor in einem Thread aus und laesst `keeper` urteilen. Merkt sich den
    Zeitstempel des letzten Bilds: ohne neues Bild gibt es keine Aussage.
    """
    seen = {"timestamp": None}

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

    return observe


def _cell_watch_handlers(cell_watch: Any, states: VisionStateMachines):
    """Die vier Handler: `(Name, Handler, Eingaben)` in der Reihenfolge der Anlage."""

    @uamethod
    async def start_cell_watch(parent, options_json: str):
        """1:StartCellWatch -- scharf schalten bzw. neue Referenz setzen."""
        # Operational reicht: waehrend eines Jobs darf neu scharf geschaltet
        # werden, die Ueberwachung pausiert dann ohnehin.
        if states.state_names()[0] != "Operational":
            return code_reply(VisionErrorCode.INVALID_STATE)
        return code_reply(await cell_watch.start(options_json or ""))

    @uamethod
    async def stop_cell_watch(parent):
        """1:StopCellWatch -- Ueberwachung aus."""
        return code_reply(await cell_watch.stop())

    @uamethod
    async def pause_cell_watch(parent, reason_text: str, max_pause_s: float):
        """1:PauseCellWatch -- pausieren, solange der Roboter faehrt."""
        return code_reply(await cell_watch.pause(reason_text or "", max_pause_s))

    @uamethod
    async def resume_cell_watch(parent):
        """1:ResumeCellWatch -- Pause beenden."""
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
    #: Die Pose der fest montierten Kamera, einmal aus >= 2 Welttags geschaetzt;
    #: lebt ueber `StartCellWatch`-Aufrufe hinweg (Zeiten aendern, neue Referenz).
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
