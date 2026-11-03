"""Part-10-Aufsatz (`VisionProgram`) auf denselben `JobRunner`."""

from asyncua import Node

from ..vision_program import VisionProgram, install_vision_program
from .wiring import Wiring


def _mirror_nodes(wiring: Wiring) -> dict[str, Node]:
    """Die Knoten, die `VisionProgram` zusaetzlich spiegelt (nur die vorhandenen)."""
    space = wiring.space
    mirror_nodes = {"LatestResultJson": wiring.results.json_node}
    optional = (
        ("LatestCameraFrame", space.latest_camera_frame),
        ("CameraStreamMode", space.camera_stream_mode),
        ("CalibrationProgress", space.calibration_progress),
        ("TagMapJson", space.tag_map_json),
        ("ActiveCalibrationInfo", space.active_calibration_info),
    )
    for name, node in optional:
        if node is not None:
            mirror_nodes[name] = node
    if space.layer2_target is not None and space.layer2_status is not None:
        mirror_nodes["Layer2Target"] = space.layer2_target
        mirror_nodes["Layer2Status"] = space.layer2_status
    optional = (
        ("HandEyeStatus", space.hand_eye_status),
        ("LastTagMeasurement", space.last_tag_measurement),
        ("LastTagReference", space.last_tag_reference),
        ("TagReferencePreviewStatus", space.tag_reference_preview_status),
        ("CellWatchStatus", space.cell_watch_status),
    )
    for name, node in optional:
        if node is not None:
            mirror_nodes[name] = node
    return mirror_nodes


async def install_part10_program(wiring: Wiring) -> VisionProgram:
    """Setzt `VisionProgram` auf und spiegelt Knoten und Methoden hinein.

    Muss nach den Zustaenden stehen: das Programm spiegelt den Zustand des
    Vision-Systems und waere sonst `Ready`, bevor feststeht, ob die Quelle
    ueberhaupt aufgeht.
    """
    program = await install_vision_program(
        wiring.server,
        wiring.server.nodes.objects,
        wiring.space.own_idx,
        wiring.jobs,
        known_recipes=wiring.config.known_recipe_ids,
        mirror_nodes=_mirror_nodes(wiring),
        mirror_methods=wiring.methods,
    )
    if not wiring.states.is_ready():
        # Ein generischer Client soll nicht `Ready` sehen, wenn kein Job
        # angenommen wuerde. Teil 10 hat keinen Fehlerzustand -- `Halted` ist
        # die einzige ehrliche Entsprechung.
        sm = program.state_machine
        await sm.change_state(
            sm.halted, sm.ready_to_halted, "Vision-System nicht betriebsbereit"
        )
    return program
