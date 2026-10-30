"""Layer-2-Lauf (Hand-Pi): `StartLayer2Run` und `ReportRobotPose`."""

import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..job import JobRunner
from ..result_management import ResultStore

_log = logging.getLogger(__package__)


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
    from ..layer2.run import Layer2Ports, Layer2Run

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
