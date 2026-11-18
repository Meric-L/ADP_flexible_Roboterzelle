# Layer-2 run on the hand Pi: anchor, plan, measure

**Status:** done — 29.09.2026 (drive command on the robot still open, see Open questions)
**Owner:** Meric, `Agent: layer-2 run on the hand Pi`
**Topic:** apriltag
**Branch:** apriltag

## Goal

The hand Pi performs the fine measurement (layer 2) itself:
1. It plans where the flange has to go in order to see the next world tag.
2. It anchors itself there (`apriltag-hand-eye-anchoring.md`).
3. It then plans every module from the **measured** instead of the coarse robot base.
4. It measures every module.

The frontend and backend only pass on poses:
- from the frontend to the Pi: coarse poses from layer 1 and the tag positions from the module catalog;
- from the Pi to the robot: the next motion target;
- from the robot to the Pi: the reached pose.

Everything runs via OPC UA.

Previously the planning lay in the frontend (`webskillcomposition`, `cell-modules/model/layer2.ts`). It lacked the anchor step, and it always planned with the coarse base from layer 1.

## Already read

- `CLAUDE.md`, `doc/project_docs/work-plans/README.md` (no plan for a layer-2 run; `ceiling-camera-full-resolution.md` in progress, different topic)
- `doc/project_docs/work-plans/apriltag-hand-eye-anchoring.md` (anchor calculation, robot pose as job parameter)
- `doc/project_docs/work-plans/apriltag-tagmap-over-opcua.md` (pattern SetTagMap/TagMapJson)
- `doc/project_docs/interfaces/vision-server-interface.md` (methods, error codes, locks 12.7)
- `doc/project_docs/topics/apriltag-localization.md`, section on repositioning: "not part of this library … belongs in the sequence control". Therefore the run lives in `vision_server/`, not in `tagloc/`.

## Affected files

- `src/vision_server/layer2/__init__.py`, `plan.py`, `run.py` (new)
- `src/vision_server/address_space.py`: nodes `Layer2Target` and `Layer2Status`
- `src/vision_server/runner.py`: methods, wiring, locks, stop
- `tests/test_layer2_plan.py`, `tests/test_layer2_run.py` (new)
- `doc/project_docs/interfaces/vision-server-interface.md`: new section
- `doc/project_docs/work-plans/README.md`: index line

## Interfaces

### OPC UA

All elements are located under `VisionMachine` in the namespace `http://launch-rm.de/vision`. They exist **only on the hand Pi**, i.e. when `AprilTagProfileConfig.hand_eye_path` is set. They are additionally linked under `VisionProgram`, like the calibration methods.

| Element | NodeId | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| StartLayer2Run | `ns=<vision>;s=VisionMachine.StartLayer2Run` | Method | Call | starts a run |
| ReportRobotPose | `ns=<vision>;s=VisionMachine.ReportRobotPose` | Method | Call | reports the reached flange pose, starts the measurement |
| Layer2Target | `ns=<vision>;s=VisionMachine.Layer2Target` | String (JSON) | Read/subscribe | next motion target; `""` if the run currently has none |
| Layer2Status | `ns=<vision>;s=VisionMachine.Layer2Status` | String (JSON) | Read/subscribe | state of the run including steps and results |
| Stop | present | Method | Call | additionally aborts a running layer-2 run |

**Method signatures:**
- `StartLayer2Run(RunJson: String) -> (Error: Int32)`
- `ReportRobotPose(ReportJson: String) -> (Error: Int32)`

**Error codes** (`errors.py`):

| Code | When |
| --- | --- |
| `OK` | accepted |
| `INVALID_ARGUMENT` | JSON invalid or schema wrong |
| `INVALID_STATE` | vision system not `Ready`; no hand-eye file loaded; `ReportRobotPose` without a waiting target or with a wrong `stepIndex` |
| `BUSY` | a job, a calibration or already a run active (StartLayer2Run); a job is running (ReportRobotPose) |

**Locks during a run:** `StartCalibration` and `SetTagMap` answer `BUSY`, because `SetTagMap` discards the anchor. `StartSingleJob` remains allowed, for debugging.

### Data

**`RunJson`**, schema `wsc.vision.layer2.run/1`. Poses are everywhere `{"position": [x,y,z], "orientation": [qx,qy,qz,qw]}`, in metres, quaternion xyzw.

```json
{
  "schema": "wsc.vision.layer2.run/1",
  "robotBase": { "position": [..], "orientation": [..] },
  "modules": [
    {
      "moduleId": "MOD-A",
      "name": "Modul A",
      "pose": { "position": [..], "orientation": [..] },
      "tags": [ { "tagId": 7, "poseInModule": { "position": [..], "orientation": [..] } } ]
    }
  ],
  "options": { "standoffM": 0.3, "reachM": 0.85 }
}
```

- `robotBase`: `T_world_base` from layer 1, coarse.
- `modules[].pose`: `T_world_module` from layer 1, coarse. The robot itself does not belong in the list.
- `modules[].tags[].poseInModule`: `T_module_tag` from the module catalog. The tag frame is the library's: +X right, +Y toward the upper edge of the print, +Z out of the tag.
- `options` is optional. The defaults are a 0.3 m standoff and a 0.85 m reach. The reach is only a hint.

**`ReportJson`**, schema `wsc.vision.layer2.report/1`:

```json
{ "schema": "wsc.vision.layer2.report/1", "stepIndex": 0, "reached": true,
  "flangeInBase": { "position": [..], "orientation": [..] } }
{ "schema": "wsc.vision.layer2.report/1", "stepIndex": 0, "reached": false,
  "reason": "außer Reichweite" }
```

`flangeInBase` is the pose that the robot **reports**, not the target. Only with it may anchoring be performed (`apriltag-hand-eye-anchoring.md`).

**`Layer2Target`**, schema `wsc.vision.layer2.target/1`:

```json
{ "schema": "wsc.vision.layer2.target/1", "runId": "l2-000001", "stepIndex": 0,
  "purpose": "anchor", "moduleId": "", "name": "Welttag 0", "tagId": 0,
  "flangeInBase": {..}, "cameraInWorld": {..}, "tagInWorld": {..},
  "reachM": 0.62, "reachable": true }
```

- `purpose` is `"anchor"` or `"measure"`.
- `flangeInBase` is the motion target for the robot.
- `cameraInWorld` and `tagInWorld` are for display.

**`Layer2Status`**, schema `wsc.vision.layer2.status/1`:

```json
{ "schema": "wsc.vision.layer2.status/1", "runId": "l2-000001",
  "state": "waitingForRobot", "message": "", "stepIndex": 0,
  "anchor": { "worldTagId": 0, "baseInWorld": {..} },
  "steps": [ { "stepIndex": 0, "purpose": "anchor", "moduleId": "", "name": "Welttag 0",
               "tagId": 0, "reachM": 0.62, "reachable": true,
               "status": "done", "detail": "", "result": null } ] }
```

- `state` is one of: `idle`, `waitingForRobot`, `measuring`, `finished`, `aborted`.
- `steps[].status` is one of: `pending`, `target`, `measuring`, `done`, `failed`, `skipped`.
- `anchor` is `null` until this run has anchored.
- `steps[].result` exists only for measurement steps: `{"frameId", "detections"}` from the job's payload (`wsc.vision.detections/1`).

### Python

```python
# Module: src/vision_server/layer2/plan.py   (numpy, tagloc; no asyncua)
@dataclass(frozen=True)
class RunRequest: robot_base: Pose; modules: tuple[ModuleRequest, ...]; standoff_m: float; reach_m: float
def parse_run_request(text: str) -> RunRequest          # ValueError with plain text
def parse_report(text: str) -> RobotReport              # ValueError
def view_target(T_world_tag, T_world_base, T_flange_cam, standoff_m) -> ViewTarget
def plan_run(request, tag_map, pose_world_base, T_flange_cam) -> list[PlannedStep]

# Module: src/vision_server/layer2/run.py   (asyncio, no asyncua)
class Layer2Run:
    active: bool
    def start(self, request_json: str) -> VisionErrorCode
    def report(self, report_json: str) -> VisionErrorCode
    def on_job_finished(self, job_id: str, code: VisionErrorCode) -> None
    async def abort(self, reason: str) -> None
```

### Calculation

World tag and module tag go through **the same** function `view_target`:

```
T_tag_cam      = rotation 180° about x, 0.3 m along +z of the tag  (camera looks at the tag)
T_world_cam    = T_world_tag · T_tag_cam
T_world_flange = T_world_cam · inv(T_flansch_cam)
T_base_flange  = inv(T_world_base) · T_world_flange             <- motion target
```

The two tag types differ only in **where** `T_world_tag` comes from:
- World tag: from the tag map (`nearest_world_tag`, the one nearest to the base).
- Module tag: `T_world_module · T_module_tag` of the tag that points most towards the base.

In addition, `T_world_base` is switched:
- before anchoring: the coarse base from layer 1;
- afterwards: the measured one from `RobotAnchor.pose_world_base`.

### Failure cases

| Case | Behaviour |
| --- | --- |
| World tag not seen, no hand-eye file, robot pose missing | anchor step `failed`, run `aborted`, rest `skipped` |
| Robot reports `reached: false` | step `failed`; at the anchor abort, otherwise continue |
| Module tag not seen or pose not in the world frame | step `skipped` with reason, continue |
| Job error (timeout, camera) | step `failed`, continue; at the anchor abort |
| Module out of reach | step `skipped` without motion |
| `Stop` | run `aborted`, `Layer2Target = ""`, running job is cancelled |

Note on the anchor step: `locate_modules` skips world tags. An image with only the world tag therefore yields `DETECTION_FAILED`, although anchoring was performed. The step counts as successful if the job left behind a **new** `RobotAnchor`, regardless of which code it returned.

## Approach

1. `plan.py` with tests: parser, `view_target`, tag selection, order, replanning.
2. `run.py` with tests against fakes: sequence, anchor, abort, report check.
3. Nodes and methods in `address_space.py`/`runner.py`, locks, stop.
4. Topic doc `vision-server-interface.md`: new section.

## Open questions

- **Drive command on the robot:** the robot servers today offer only `go_to` with joint angles. A Cartesian command (flange pose in the base frame) and reading out the actual flange pose are missing. Until then the frontend fills the report itself with the target in the dry run. This is intended only for trying things out.
- `T_flansch_cam` has not yet been measured on the real hardware (see `apriltag-hand-eye-anchoring.md`). Without the file `StartLayer2Run` rejects with `INVALID_STATE`.

## Deviations from the plan

- `Layer2Status` additionally has `stepIndex` (current step, `-1` for none) and `anchor.spreadM`.
- The methods do not depend on `jobs.busy` alone: `StartLayer2Run` also checks the calibration session (`calibration_session.busy`).
- **Stop via the Part-10 facade** (`VisionProgram`) aborts only the job, not the run. The run then evaluates the aborted job as `failed` (`CANCELLED`); at the anchor step it aborts. The whole run is ended by `AutomaticModeStateMachine/Stop`.
- The OPC UA test (`tests/test_layer2_opcua.py`) runs `runner._install_layer2_run` against a real asyncua server with a fake job runner and fake source. The run has not been tested across the whole machine with a camera.
- **Frontend** (`webskillcomposition`, branch `Ungetestet` (untested)): the window "Autolocate Layer 2" has "Tags erkennen (Debug)" (detect tags (debug)) and the section "Feinmessung: Ankern und Module" (fine measurement: anchor and modules). The old planning in the browser has been removed.

## After completion

- [x] Status set to `done`, actual interfaces entered.
- [x] Index line updated.
- [x] Topic knowledge carried over into `vision-server-interface.md` section 14.
- [x] Test run: 522 tests, OK. New: `test_layer2_plan.py` (11), `test_layer2_run.py` (8), `test_layer2_opcua.py` (1).
