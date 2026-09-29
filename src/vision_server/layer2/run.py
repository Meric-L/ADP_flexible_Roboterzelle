"""Ablauf des Layer-2-Laufs auf dem Hand-Pi.

Ein Lauf ist eine Kette aus Schritten -- zuerst der Welttag, dann die Module.
Je Schritt:

1. Der Pi veroeffentlicht das Fahrziel (`Layer2Target`).
2. Draussen faehrt jemand den Roboter hin und meldet die **erreichte** Pose
   (`ReportRobotPose`).
3. Der Pi startet mit dieser Pose einen gewoehnlichen AprilTag-Job -- dieselben
   Events und dasselbe `LatestResultJson` wie `StartSingleJob`.
4. Am Job-Ende wertet er aus und geht zum naechsten Schritt.

Der Anker-Schritt gilt als geglueckt, wenn der Job einen **neuen**
`RobotAnchor` hinterlassen hat. Der Code des Jobs zaehlt dabei nicht:
`locate_modules` ueberspringt Welttags, ein Bild nur mit dem Welttag endet
deshalb in `DETECTION_FAILED`, obwohl geankert wurde. Danach werden die
restlichen Schritte mit der gemessenen Basis neu geplant.

Hier steckt kein asyncua: alles, was den Server betrifft, kommt als Funktion
herein (`Layer2Ports`). So laesst sich der Ablauf gegen Fakes testen.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

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
    """Was der Lauf vom Server braucht."""

    #: Startet einen AprilTag-Job mit diesen Parametern: `(job_id, code)`.
    start_job: Callable[[list[str]], tuple[str, VisionErrorCode]]
    #: Laeuft gerade ein Job oder eine Kalibrierung?
    busy: Callable[[], bool]
    #: Der aktuelle `RobotAnchor` der AprilTag-Quelle, oder `None`.
    read_anchor: Callable[[], Any]
    #: `T_flansch_cam` aus der Hand-Auge-Datei, oder `None` ohne Datei.
    read_hand_eye: Callable[[], Any]
    #: Die geladene Tag-Map.
    read_tag_map: Callable[[], Any]
    #: Payload des letzten Jobs (`LatestResultJson`).
    read_result: Callable[[], Awaitable[str]]
    #: Schreibt `Layer2Target` und `Layer2Status`.
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
    #: Der Anker dieses Laufs; `None`, bis Schritt 0 geglueckt ist.
    anchor: Any = None
    #: Job, auf dessen Ende der Lauf gerade wartet.
    pending_job_id: str | None = None
    #: Anker vor dem Anker-Job -- nur ein anderer danach zaehlt als neu.
    anchor_before: Any = None


class Layer2Run:
    def __init__(self, ports: Layer2Ports) -> None:
        self._ports = ports
        self._s = _State()
        self._counter = 0
        self._background: set[asyncio.Task] = set()

    @property
    def active(self) -> bool:
        """Laeuft ein Lauf? Sperrt Kalibrierung und SetTagMap."""
        return self._s.state in (WAITING_FOR_ROBOT, MEASURING)

    # --- Eingaenge -------------------------------------------------------

    async def start(self, request_json: str) -> VisionErrorCode:
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
            await self._finish_step(FAILED, reason)
            return VisionErrorCode.OK

        s.anchor_before = self._ports.read_anchor()
        job_id, code = self._ports.start_job(layer2_plan.pose_parameters(report.flange_in_base))
        if code != VisionErrorCode.OK:
            # Nichts veraendert: der Aufrufer darf es noch einmal melden.
            _log.warning("Layer-2-Job fuer Schritt %d nicht gestartet: %s", s.index, code.name)
            return code
        s.pending_job_id = job_id
        s.state = MEASURING
        step.status = STEP_MEASURING
        step.detail = ""
        await self._publish()
        return VisionErrorCode.OK

    def on_job_finished(self, job_id: str, code: VisionErrorCode) -> None:
        """Beobachter von `JobRunner.add_finish_listener` -- synchron, daher
        wird die Auswertung als Task gestartet."""
        if job_id != self._s.pending_job_id:
            return
        task = asyncio.ensure_future(self._evaluate(job_id, code))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def abort(self, reason: str) -> None:
        """Bricht einen laufenden Lauf ab; ohne Lauf passiert nichts."""
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
        """Erstbelegung der Knoten nach dem Serverstart."""
        await self._publish()

    async def wait_idle(self) -> None:
        """Fuer Tests: wartet, bis alle Auswertungen durch sind."""
        while self._background:
            await asyncio.gather(*list(self._background))

    # --- Ablauf ----------------------------------------------------------

    async def _evaluate(self, job_id: str, code: VisionErrorCode) -> None:
        s = self._s
        if job_id != s.pending_job_id or s.state != MEASURING:
            return
        s.pending_job_id = None
        step = s.steps[s.index]
        if step.planned.spec.purpose == layer2_plan.ANCHOR:
            await self._evaluate_anchor(code)
        else:
            await self._evaluate_measure(step, code)

    async def _evaluate_anchor(self, code: VisionErrorCode) -> None:
        s = self._s
        anchor = self._ports.read_anchor()
        if anchor is None or anchor is s.anchor_before:
            if code in (VisionErrorCode.OK, VisionErrorCode.DETECTION_FAILED):
                reason = "Welttag nicht gesehen -- nicht geankert"
            else:
                reason = f"Job-Fehler {code.name} -- nicht geankert"
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
        seen = any(
            (detection.get("attributes") or {}).get("tagId") == step.planned.tag_id
            for detection in detections
        )
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
        if status == FAILED and step.planned.spec.purpose == layer2_plan.ANCHOR:
            # Ohne Anker entsteht keine Weltpose -- jede weitere Fahrt waere umsonst.
            await self.abort(f"Anker fehlgeschlagen: {detail}")
            return
        await self._enter(s.index + 1)

    async def _enter(self, index: int) -> None:
        """Geht zu Schritt `index`; Ziele ausser Reichweite ohne Fahrt uebersprungen."""
        s = self._s
        while index < len(s.steps):
            step = s.steps[index]
            if step.planned.reachable or step.planned.spec.purpose == layer2_plan.ANCHOR:
                break
            step.status = SKIPPED
            step.detail = f"ausser Reichweite ({step.planned.target.reach_m:.2f} m)"
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
        """Plant alle Schritte nach `index` mit der gemessenen Basis neu."""
        s = self._s
        assert s.request is not None
        for step in s.steps[index + 1:]:
            step.planned = layer2_plan.plan_step(
                step.planned.spec,
                pose_world_base,
                s.pose_flange_cam,
                s.request.standoff_m,
                s.request.reach_m,
            )

    def _summary(self) -> str:
        measured = [step for step in self._s.steps if step.planned.spec.purpose == layer2_plan.MEASURE]
        done = sum(1 for step in measured if step.status == DONE)
        return f"{done} von {len(measured)} Modulen gemessen."

    # --- Knoten ----------------------------------------------------------

    async def _publish(self) -> None:
        try:
            await self._ports.publish(self.target_json(), self.status_json())
        except Exception:
            _log.exception("Layer2Target/Layer2Status konnten nicht geschrieben werden")

    def target_json(self) -> str:
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
            },
            allow_nan=False,
        )

    def status_json(self) -> str:
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
