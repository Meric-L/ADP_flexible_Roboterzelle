"""Layer 2 run (hand-camera Pi): `StartLayer2Run` and `ReportRobotPose`."""

from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..job import JobRunner
from ..result_management import ResultStore
from .uamethods import add_vision_method, code_reply


async def _install_layer2_run(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    jobs: JobRunner,
    apriltag_source: Any,
    results: ResultStore,
    methods: dict[str, Node],
    calibration_busy: Callable[[], bool],
) -> Any:
    """Create StartLayer2Run and ReportRobotPose and wire up the run.

    Only on the hand-camera Pi (nodes exist only with `hand_eye_path`). Each step
    starts an ordinary AprilTag job via the JobRunner -- same events, same
    `LatestResultJson`, and `Stop` works as usual. See
    `doc/project_docs/work-plans/layer2-run-hand-pi.md`.
    """
    # Imported here only: `layer2` needs numpy, the rest of the server does not.
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

    @uamethod
    async def start_layer2_run(parent, run_json: str):
        """1:StartLayer2Run -- start a run with the coarse poses from Layer 1."""
        return code_reply(await layer2_run.start(run_json or ""))

    @uamethod
    async def report_robot_pose(parent, report_json: str):
        """1:ReportRobotPose -- report the reached flange pose and start the measurement."""
        return code_reply(await layer2_run.report(report_json or ""))

    for name, handler, description in (
        ("StartLayer2Run", start_layer2_run, "Laufparameter als JSON."),
        ("ReportRobotPose", report_robot_pose, "Gemeldete Roboterpose als JSON."),
    ):
        await add_vision_method(space, config, methods, name, handler,
            [("RequestJson", ua.VariantType.String, description)],
            [("Error", ua.VariantType.Int32, "Vision-Fehlercode.")])
    return layer2_run
