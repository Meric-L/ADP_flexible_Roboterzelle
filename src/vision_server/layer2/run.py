"""Flow of a Layer 2 run on the hand Pi.

A run is a chain of steps: first the world tag, then the modules. For each step:

1. The Pi publishes the target (`Layer2Target`).
2. Someone outside drives the robot there and reports the **reached** pose
   (`ReportRobotPose`).
3. With this pose, the Pi starts an ordinary AprilTag job -- the same events and
   the same `LatestResultJson` as `StartSingleJob`.
4. At the end of the job it evaluates the result and moves on to the next step.

The anchor step counts as successful if the job left a **new** `RobotAnchor`
behind. The code of the job does not count here: `locate_modules` skips world
tags, so an image with only the world tag ends in `DETECTION_FAILED`, even
though an anchor was set. Afterwards the remaining steps are re-planned with
the measured base.

Approach: before each anchor and measure step, the robot first drives from a
larger distance. Its image measures the tag pose, and the following close step
is re-planned from it. Anchoring and measuring happen only in the close image.
For the world tag, the measurement is a provisional anchor from the wide image.
It only serves for aiming; the run does not adopt it.

There is no asyncua here: everything that concerns the server comes in as a
function (`Layer2Ports`). This makes it possible to test the flow against fakes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from ..errors import VisionErrorCode
from . import plan as layer2_plan

_log = logging.getLogger(__name__)

IDLE = "idle"
WAITING_FOR_ROBOT = "waitingForRobot"
MEASURING = "measuring"
FINISHED = "finished"
ABORTED = "aborted"

PENDING = "pending"
TARGET = "target"
STEP_MEASURING = "measuring"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass(frozen=True)
class Layer2Ports:
    """What the run needs from the server."""

    #: Starts an AprilTag job with these parameters. Returns `(job_id, code)`.
    start_job: Callable[[list[str]], tuple[str, VisionErrorCode]]
    #: Is a job or a calibration running?
    busy: Callable[[], bool]
    #: The current `RobotAnchor` of the AprilTag source, or `None`.
    read_anchor: Callable[[], Any]
    #: `T_flansch_cam` (camera pose in the flange frame) from the hand-eye file,
    #: or `None` without a file.
    read_hand_eye: Callable[[], Any]
    #: The loaded tag map.
    read_tag_map: Callable[[], Any]
    #: Payload of the last job (`LatestResultJson`).
    read_result: Callable[[], Awaitable[str]]
    #: Writes `Layer2Target` and `Layer2Status`.
    publish: Callable[[str, str], Awaitable[None]]


@dataclass
class _Step:
    planned: layer2_plan.PlannedStep
    status: str = PENDING
    detail: str = ""
    result: dict | None = None


@dataclass
class _State:
    run_id: str = ""
    state: str = IDLE
    message: str = ""
    request: layer2_plan.RunRequest | None = None
    pose_flange_cam: Any = None
    steps: list[_Step] = field(default_factory=list)
    index: int = -1
    #: The anchor of this run; `None` until step 0 has succeeded.
    anchor: Any = None
    #: Job whose end the run is currently waiting for.
    pending_job_id: str | None = None
    #: Anchor before the anchor job -- only a different one afterwards counts as new.
    anchor_before: Any = None


class Layer2Run:
    """Run the step chain of a Layer 2 run and publish its state."""

    def __init__(self, ports: Layer2Ports) -> None:
        self._ports = ports
        self._s = _State()
        self._counter = 0
        self._background: set[asyncio.Task] = set()

    @property
    def active(self) -> bool:
        """Whether a run is in progress. Blocks calibration and SetTagMap."""
        return self._s.state in (WAITING_FOR_ROBOT, MEASURING)

    # --- Inputs ----------------------------------------------------------

    async def start(self, request_json: str) -> VisionErrorCode:
        """Start a run from ``request_json``. Return an error code instead of raising."""
        if self.active:
            _log.warning("StartLayer2Run abgelehnt: es laeuft schon ein Lauf")
            return VisionErrorCode.BUSY
        if self._ports.busy():
            _log.warning("StartLayer2Run abgelehnt: Job oder Kalibrierung laeuft")
            return VisionErrorCode.BUSY
        hand_eye = self._ports.read_hand_eye()
        if hand_eye is None:
            _log.warning("StartLayer2Run abgelehnt: keine Hand-Auge-Kalibrierung geladen")
            return VisionErrorCode.INVALID_STATE
        try:
            request = layer2_plan.parse_run_request(request_json)
        except ValueError as error:
            _log.warning("StartLayer2Run abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        pose_flange_cam = hand_eye.pose_flange_cam
        try:
            planned = layer2_plan.plan_run(request, self._ports.read_tag_map(), pose_flange_cam)
        except ValueError as error:
            _log.warning("StartLayer2Run abgelehnt: %s", error)
            return VisionErrorCode.INVALID_STATE

        self._counter += 1
        self._s = _State(
            run_id=f"l2-{self._counter:06d}",
            state=WAITING_FOR_ROBOT,
            request=request,
            pose_flange_cam=pose_flange_cam,
            steps=[_Step(step) for step in planned],
        )
        _log.info(
            "Layer-2-Lauf %s: Anker an %s, %d Module",
            self._s.run_id,
            planned[0].spec.name,
            len(planned) - 1,
        )
        await self._enter(0)
        return VisionErrorCode.OK

    async def report(self, report_json: str) -> VisionErrorCode:
        """Handle the pose the robot reports for the step that is waiting."""
        s = self._s
        if s.state != WAITING_FOR_ROBOT:
            _log.warning("ReportRobotPose ohne wartendes Ziel (Zustand %s)", s.state)
            return VisionErrorCode.INVALID_STATE
        try:
            report = layer2_plan.parse_report(report_json)
        except ValueError as error:
            _log.warning("ReportRobotPose abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        if report.step_index != s.index:
            _log.warning(
                "ReportRobotPose fuer Schritt %d, erwartet %d", report.step_index, s.index
            )
            return VisionErrorCode.INVALID_STATE
        step = s.steps[s.index]

        if not report.reached:
            reason = report.reason or "Roboter hat das Ziel nicht erreicht"
            if step.planned.spec.purpose == layer2_plan.APPROACH:
                # The approach is an aid, not a requirement: without it, the near
                # target simply stays the one from the coarse pose.
                await self._finish_step(
                    SKIPPED, f"nicht angefahren ({reason}) -- nahes Ziel aus grober Pose"
                )
            else:
                await self._finish_step(FAILED, reason)
            return VisionErrorCode.OK

        s.anchor_before = self._ports.read_anchor()
        job_id, code = self._ports.start_job(layer2_plan.pose_parameters(report.flange_in_base))
        if code != VisionErrorCode.OK:
            # Nothing changed: the caller may report again.
            _log.warning("Layer-2-Job fuer Schritt %d nicht gestartet: %s", s.index, code.name)
            return code
        s.pending_job_id = job_id
        s.state = MEASURING
        step.status = STEP_MEASURING
        step.detail = ""
        await self._publish()
        return VisionErrorCode.OK

    def on_job_finished(self, job_id: str, code: VisionErrorCode) -> None:
        """Observer for `JobRunner.add_finish_listener`.

        It runs synchronously, so the evaluation is started as a task.
        """
        if job_id != self._s.pending_job_id:
            return
        task = asyncio.ensure_future(self._evaluate(job_id, code))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def abort(self, reason: str) -> None:
        """Abort a running run. Does nothing if no run is active."""
        if not self.active:
            return
        s = self._s
        s.pending_job_id = None
        for step in s.steps[max(s.index, 0):]:
            if step.status in (PENDING, TARGET, STEP_MEASURING):
                step.status = SKIPPED
                step.detail = reason
        s.state = ABORTED
        s.message = reason
        s.index = -1
        _log.info("Layer-2-Lauf %s abgebrochen: %s", s.run_id, reason)
        await self._publish()

    async def publish_idle(self) -> None:
        """Initial publish of the nodes after the server starts."""
        await self._publish()

    async def wait_idle(self) -> None:
        """For tests: wait until all evaluations have finished."""
        while self._background:
            await asyncio.gather(*list(self._background))

    # --- Flow ------------------------------------------------------------

    async def _evaluate(self, job_id: str, code: VisionErrorCode) -> None:
        s = self._s
        if job_id != s.pending_job_id or s.state != MEASURING:
            return
        s.pending_job_id = None
        step = s.steps[s.index]
        purpose = step.planned.spec.purpose
        if purpose == layer2_plan.APPROACH:
            await self._evaluate_approach(step, code)
        elif purpose == layer2_plan.ANCHOR:
            await self._evaluate_anchor(code)
        else:
            await self._evaluate_measure(step, code)

    def _new_anchor(self, code: VisionErrorCode) -> tuple[Any, str]:
        """Return the anchor left by the last job, or ``(None, reason)``."""
        anchor = self._ports.read_anchor()
        if anchor is not None and anchor is not self._s.anchor_before:
            return anchor, ""
        if code in (VisionErrorCode.OK, VisionErrorCode.DETECTION_FAILED):
            return None, "Welttag nicht gesehen -- nicht geankert"
        return None, f"Job-Fehler {code.name} -- nicht geankert"

    async def _evaluate_approach(self, step: _Step, code: VisionErrorCode) -> None:
        """Tag measured in the wide image: re-plan the near step that follows."""
        s = self._s
        assert s.request is not None
        near = s.steps[s.index + 1]
        if near.planned.spec.purpose == layer2_plan.ANCHOR:
            # A world tag does not show up as a detection but as an anchor. The
            # near step aims with its base; the world tag pose stays the exact
            # one from the tag map.
            anchor, reason = self._new_anchor(code)
            if anchor is None:
                await self._finish_step(FAILED, f"Annaeherungsbild: {reason}")
                return
            pose_world_base = anchor.pose_world_base
            spec = near.planned.spec
            seen = f"Welttag {near.planned.tag_id} gesehen"
        else:
            tag_id = near.planned.tag_id
            tag_in_world, reason = await self._seen_tag(tag_id, code)
            if tag_in_world is None:
                near.status = SKIPPED
                near.detail = f"Annaeherungsbild: {reason}"
                await self._finish_step(
                    FAILED if reason.startswith("Job-Fehler") else SKIPPED, reason
                )
                return
            pose_world_base = s.anchor.pose_world_base
            spec = replace(near.planned.spec, seen_tag=(tag_id, tag_in_world))
            seen = f"Tag {tag_id} gesehen"
        planned = layer2_plan.plan_step(
            spec,
            pose_world_base,
            s.pose_flange_cam,
            s.request.standoff_m,
            s.request.reach_m,
            s.request.max_tilt_deg,
        )
        correction_m = float(
            np.linalg.norm(
                planned.target.flange_in_base[:3, 3] - near.planned.target.flange_in_base[:3, 3]
            )
        )
        near.planned = planned
        await self._finish_step(
            DONE, f"{seen}, nahes Ziel um {correction_m * 1000:.0f} mm korrigiert"
        )

    async def _seen_tag(self, tag_id: int, code: VisionErrorCode):
        """Return the world pose of the tag from the last job, or ``(None, reason)``."""
        if code == VisionErrorCode.DETECTION_FAILED:
            return None, f"Tag {tag_id} nicht gesehen"
        if code != VisionErrorCode.OK:
            return None, f"Job-Fehler {code.name}"
        try:
            payload = json.loads(await self._ports.read_result())
        except (json.JSONDecodeError, TypeError):
            return None, "Ergebnis des Jobs ist kein JSON"
        frame_id = payload.get("frameId")
        tag_map = self._ports.read_tag_map()
        if frame_id != tag_map.frame_id:
            return None, f"Pose nicht im Welt-KS („{frame_id}“) -- kein Anker?"
        detection = layer2_plan.find_detection(payload.get("detections") or [], tag_id)
        if detection is None:
            return None, f"Tag {tag_id} nicht gesehen"
        try:
            return layer2_plan.tag_in_world_from_detection(detection, tag_id, tag_map), ""
        except ValueError as error:
            return None, str(error)

    async def _evaluate_anchor(self, code: VisionErrorCode) -> None:
        s = self._s
        anchor, reason = self._new_anchor(code)
        if anchor is None:
            await self._finish_step(FAILED, reason)
            return
        s.anchor = anchor
        self._replan_after(s.index, anchor.pose_world_base)
        spread_mm = float(anchor.spread_m) * 1000
        await self._finish_step(
            DONE, f"geankert an Welttag {anchor.world_tag_id} (Streuung {spread_mm:.1f} mm)"
        )

    async def _evaluate_measure(self, step: _Step, code: VisionErrorCode) -> None:
        if code != VisionErrorCode.OK:
            await self._finish_step(FAILED, f"Job-Fehler {code.name}")
            return
        try:
            payload = json.loads(await self._ports.read_result())
        except (json.JSONDecodeError, TypeError):
            await self._finish_step(FAILED, "Ergebnis des Jobs ist kein JSON")
            return
        frame_id = payload.get("frameId")
        detections = payload.get("detections") or []
        world = self._ports.read_tag_map().frame_id
        seen = layer2_plan.find_detection(detections, step.planned.tag_id) is not None
        if frame_id != world:
            await self._finish_step(
                SKIPPED, f"Pose nicht im Welt-KS („{frame_id}“) -- kein Anker?"
            )
        elif not seen:
            await self._finish_step(SKIPPED, f"Tag {step.planned.tag_id} nicht gesehen")
        else:
            step.result = {"frameId": frame_id, "detections": detections}
            await self._finish_step(DONE, f"Tag {step.planned.tag_id} gemessen")

    async def _finish_step(self, status: str, detail: str) -> None:
        s = self._s
        step = s.steps[s.index]
        step.status = status
        step.detail = detail
        if status == FAILED and self._leads_to_anchor(s.index):
            # Without an anchor there is no world pose -- every further move would be wasted.
            await self.abort(f"Anker fehlgeschlagen: {detail}")
            return
        await self._enter(s.index + 1)

    def _leads_to_anchor(self, index: int) -> bool:
        """Return True for the anchor step, or for the approach step before it."""
        steps = self._s.steps
        purpose = steps[index].planned.spec.purpose
        if purpose == layer2_plan.APPROACH and index + 1 < len(steps):
            purpose = steps[index + 1].planned.spec.purpose
        return purpose == layer2_plan.ANCHOR

    async def _enter(self, index: int) -> None:
        """Move to step `index`. Targets out of reach are skipped without driving."""
        s = self._s
        while index < len(s.steps):
            step = s.steps[index]
            if step.status == SKIPPED:
                # Already cancelled by the approach before it.
                index += 1
                continue
            purpose = step.planned.spec.purpose
            near = s.steps[index + 1].planned if purpose == layer2_plan.APPROACH else None
            if near is not None and not near.reachable and near.spec.purpose != layer2_plan.ANCHOR:
                # Why drive closer if the near target is unreachable anyway?
                step.status = SKIPPED
                step.detail = "nahes Ziel ausser Reichweite"
            elif step.planned.reachable or purpose == layer2_plan.ANCHOR:
                break
            else:
                step.status = SKIPPED
                step.detail = f"ausser Reichweite ({step.planned.target.reach_m:.2f} m)"
                if purpose == layer2_plan.APPROACH:
                    step.detail += " -- nahes Ziel aus grober Pose"
            index += 1
        if index >= len(s.steps):
            s.state = FINISHED
            s.index = -1
            s.message = self._summary()
            _log.info("Layer-2-Lauf %s fertig: %s", s.run_id, s.message)
        else:
            s.state = WAITING_FOR_ROBOT
            s.index = index
            s.steps[index].status = TARGET
        await self._publish()

    def _replan_after(self, index: int, pose_world_base) -> None:
        """Re-plan all steps after `index` using the measured base."""
        s = self._s
        assert s.request is not None
        for step in s.steps[index + 1:]:
            step.planned = layer2_plan.plan_step(
                step.planned.spec,
                pose_world_base,
                s.pose_flange_cam,
                s.request.standoff_for(step.planned.spec.purpose),
                s.request.reach_m,
                s.request.max_tilt_deg,
            )

    def _summary(self) -> str:
        """Return the one-line result: how many measure steps were done."""
        measured = [step for step in self._s.steps if step.planned.spec.purpose == layer2_plan.MEASURE]
        done = sum(1 for step in measured if step.status == DONE)
        return f"{done} von {len(measured)} Modulen gemessen."

    # --- Nodes -----------------------------------------------------------

    async def _publish(self) -> None:
        try:
            await self._ports.publish(self.target_json(), self.status_json())
        except Exception:
            _log.exception("Layer2Target/Layer2Status konnten nicht geschrieben werden")

    def target_json(self) -> str:
        """Return the `Layer2Target` JSON of the waiting step, or an empty string."""
        s = self._s
        if s.state != WAITING_FOR_ROBOT:
            return ""
        step = s.steps[s.index].planned
        return json.dumps(
            {
                "schema": layer2_plan.TARGET_SCHEMA,
                "runId": s.run_id,
                "stepIndex": s.index,
                "purpose": step.spec.purpose,
                "moduleId": step.spec.module_id,
                "name": step.spec.name,
                "tagId": step.tag_id,
                "flangeInBase": layer2_plan.pose_to_dict(step.target.flange_in_base),
                "cameraInWorld": layer2_plan.pose_to_dict(step.target.camera_in_world),
                "tagInWorld": layer2_plan.pose_to_dict(step.tag_in_world),
                "reachM": step.target.reach_m,
                "reachable": step.reachable,
                "tiltDeg": step.target.tilt_deg,
            },
            allow_nan=False,
        )

    def status_json(self) -> str:
        """Return the `Layer2Status` JSON for the current run state."""
        s = self._s
        anchor = None
        if s.anchor is not None:
            anchor = {
                "worldTagId": int(s.anchor.world_tag_id),
                "baseInWorld": layer2_plan.pose_to_dict(s.anchor.pose_world_base),
                "spreadM": float(s.anchor.spread_m),
            }
        return json.dumps(
            {
                "schema": layer2_plan.STATUS_SCHEMA,
                "runId": s.run_id,
                "state": s.state,
                "message": s.message,
                "stepIndex": s.index,
                "anchor": anchor,
                "steps": [
                    {
                        "stepIndex": index,
                        "purpose": step.planned.spec.purpose,
                        "moduleId": step.planned.spec.module_id,
                        "name": step.planned.spec.name,
                        "tagId": step.planned.tag_id,
                        "reachM": step.planned.target.reach_m,
                        "reachable": step.planned.reachable,
                        "status": step.status,
                        "detail": step.detail,
                        "result": step.result,
                    }
                    for index, step in enumerate(s.steps)
                ],
            },
            allow_nan=False,
            default=float,
        )
