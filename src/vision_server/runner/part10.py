"""Part 10 wrapper (`VisionProgram`) on the same `JobRunner`."""

from asyncua import Node

from ..vision_program import VisionProgram, install_vision_program
from .wiring import Wiring


def _status_nodes(wiring: Wiring) -> dict[str, Node]:
    """Return the existing operational status nodes."""
    space = wiring.space
    nodes = {
        "LatestCameraFrame": space.latest_camera_frame,
        "CameraStreamMode": space.camera_stream_mode,
        "CameraStreamHttpPort": space.camera_stream_http_port,
        "CalibrationProgress": space.calibration_progress,
        "TagMapJson": space.tag_map_json,
        "ActiveCalibrationInfo": space.active_calibration_info,
        "HandEyeStatus": space.hand_eye_status,
        "Layer2Target": space.layer2_target,
        "Layer2Status": space.layer2_status,
        "CellWatchStatus": space.cell_watch_status,
        "LastTagMeasurement": space.last_tag_measurement,
        "LastTagReference": space.last_tag_reference,
        "TagReferencePreviewStatus": space.tag_reference_preview_status,
    }
    if wiring.device_health_nodes:
        nodes["DeviceHealth"] = wiring.device_health_nodes[0]
    return {name: node for name, node in nodes.items() if node is not None}


def _busy_reason(wiring: Wiring) -> str:
    """Return the current German reason why the Pi cannot accept work."""
    from .tag_map import _busy_reason as tag_map_busy_reason
    from .install import busy_reason as install_busy_reason
    from .calibration import busy_reason as calibration_busy_reason

    reason = install_busy_reason(wiring)
    if reason:
        return reason
    reason = tag_map_busy_reason(wiring)
    if reason:
        return reason
    reason = calibration_busy_reason(wiring)
    if reason:
        return reason
    return ""


async def install_part10_program(wiring: Wiring) -> VisionProgram:
    """Install `VisionProgram` and mirror its nodes and methods into it.

    Must run after the states are set up: the program mirrors the state of the
    vision system and would otherwise be `Ready` before it is known whether the
    source starts at all.
    """
    program = await install_vision_program(
        wiring.server,
        wiring.server.nodes.objects,
        wiring.space.own_idx,
        wiring.jobs,
        known_recipes=wiring.config.known_recipe_ids,
        mirror_nodes={"LatestResultJson": wiring.results.json_node},
        status_nodes=_status_nodes(wiring),
        mirror_methods=wiring.methods,
        busy_reason=lambda: _busy_reason(wiring),
        vision_system_id=wiring.config.vision_system_id,
        manufacturer=wiring.config.assets.manufacturer if wiring.config.assets else "",
        model=wiring.config.assets.model if wiring.config.assets else "",
    )
    if not wiring.states.is_ready():
        # A generic client must not see `Ready` if no job would be accepted.
        # Part 10 has no error state -- `Halted` is the only honest equivalent.
        sm = program.state_machine
        await sm.change_state(
            sm.halted, sm.ready_to_halted, "Vision-System nicht betriebsbereit"
        )
    return program
