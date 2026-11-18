# Vision workflows on the Pi, robot driving in the backend (stage 2) — handover to Codex

**Status:** dropped — 18.11.2026
**Reason:** phase 2 was tried on branch `vision-workflows-stufe2` and dropped after live tests; workflows stay in the frontend.
**Note:** none of the interfaces or files below exist in the code.
**Owner:** `Agent: Codex (handover from Claude orchestration)`
**Topic:** vision
**Branch:** Pi: `vision-workflows-stufe2`; webskillcomposition: `vision-workflows-stufe2` (not merged)

This plan is self-contained: it hands the rest of the refactor to Codex. Stage 1 is
described in [`pi-backend-discovery.md`](pi-backend-discovery.md) (the device contract this
stage builds on). Read both before starting.

## Goal

The vision workflows that combine robot moves and Pi measurements (Layer 2 approach, tag
survey, hand-eye auto calibration, ceiling board calibration, cell-watch pause while the robot
moves) leave the React frontend. The Pi plans and measures, the webskillcomposition (WSC)
backend drives the robot (kinematics, go_to, arrival), the frontend only starts/stops runs and
shows progress. Target: Pi-related frontend code from ~19.800 lines (after stage 1) to
~12.000 lines, the same behaviour covered by tests, no dead code.

Out of scope (user decision 17.11.2026, also label it so in the docs):
- **Pick & Place** (`usePickPlaceRun.ts`, `pickPlace*.ts`) is only the group's use case and will
  not go to WSC `dev`: leave it unchanged.
- **UI polish**: the WSC team reworks the UI themselves. Touch panels only as far as the move
  requires (form + start/stop + progress).

## Part A — finish stage 1 first (small)

State on 17.11.2026, 23:30:

| Repo / branch | State |
| --- | --- |
| Pi `vision-part10-discovery` | complete, committed (see `git log`). Full suite 1073 tests OK, 3 skipped. |
| WSC `geraete-discovery-stufe1` | complete, committed (`f40e2ab`). Backend 106 passed / 1 skipped; frontend 960 passed + 4 pre-existing failures, typecheck clean. Smoke test against the real cell server green. |

Steps:
1. (done) Pi changes committed, suite green.
2. Docs to the current state, **no history** (this was planned for Haiku agents):
   - WSC `docs/`: `vision-workflows.md` (Pi devices, `vision_program`, device actions,
     announced servers), `architecture.md`, `data-and-interface-model.md` (`kind`,
     `RobotActionState.result`/`errorCode`, `MethodArgument.enumValues`,
     `listAnnouncedServers`/`serversAnnounced`, error codes `methodMissing`/`nothingToDo`),
     `development.md` (Vite, `npm run dev`, test commands, `WSC_MDNS`).
   - Pi repo `doc/projektdoku/code/webskill-backend.md`, `code/webskill-frontend.md`,
     `code/webskill-cell-modules.md` (Pi hooks now take a device; `piServers.ts` only URL
     builders/overlay modes/DEVICE_HEALTH; removed files), `vision-system-integration.md`
     (blocker B1 resolved by Part 10 discovery; open points), `code/zelle-vision-server.md`.
   - Mark Pick & Place as "use case of the group, not for `dev`" and UI as "reworked by the
     WSC team" where those parts are described.
3. Set [`pi-backend-discovery.md`](pi-backend-discovery.md) to `done` with the actual
   interfaces; update its row in `README.md`.
4. Known pre-existing frontend failures (not caused by this refactor, report, do not hide):
   3 cases in `app/model/applicationController.test.ts` expect max speed 0.1 but commit
   `bd94f97` set 0.15; 1 case in `cell-modules/model/cellClearance.test.ts` fails on the user's
   checkout too.

## Part B — stage 2

### Placement

| Piece (today, WSC frontend `src/features/…`) | Goes to |
| --- | --- |
| Generic robot-target port, generalised from Pi `src/vision_server/layer2/run.py` (`Layer2Ports`, publish target → wait for `ReportRobotPose` → measure → plan next) | Pi `src/vision_server/robot_target.py`; Layer 2 moved onto it |
| Layer 2 approach (`cell-modules/model/approachPlan.ts`, `ApproachPlanPanel.drive()`) | Pi, existing `layer2/plan.py` + `run.py` (already implements it; the frontend duplicate goes) |
| Centring loops (`centreOnTag.ts`, `tagServo.ts`, `boardCentring.ts`) | Pi `src/vision_server/centring.py` (MeasureBoard already computes `offsetPx`) |
| Tag survey (`tagReferenceRun.ts`, `robotTagRun.ts`, runner part of `tagViews.ts`, `tagReferenceFusion.ts`) | Pi `src/vision_server/tagref_run.py`, reusing `tagloc/reference_edges.py` and `tagreference.py` |
| Hand-eye auto (`handEyeAutoPoses.ts`, `handEyeOrder.ts`, `HandEyeAutoPanel.run()`) | Pi `src/vision_server/handeye_auto.py` on `HandEyeSession` |
| Ceiling board calibration (`boardCalibrationRunner.ts`, `boardCalibrationPoses.ts`, `useBoardCalibrationRun.ts`) | ceiling Pi `src/vision_server/calibration_run.py` on `calibration_session.py` |
| Kinematics, arrival, settle, go_to reset, clearance (`robot-control/model/flangeKinematics.ts`, `cell-modules/model/{robotDrive,jointSettling,moveTracker,controllerRobotDrive,cellClearance}.ts`) | WSC backend `kinematics/{urdf,chain,ik}.py` (own numpy DLS, same tolerances as `closed-chain-ik`: 2 mm / 1°, 20 rounds × 50 iterations, seed = current joints, joint limits), `runtime/{robot_drive,clearance}.py` |
| "Robot operating" → pause cell watch (`robotOperation.ts`, re-arm part of `AutolocateActions.tsx`) | WSC backend `runtime/cell_watch_guard.py` |
| Forms, start/stop, progress, confirm dialog, 3D preview, interactive reference-edge editing | stays frontend: new `features/runs/useRun.ts` + `RunProgress.tsx` |

URDFs: `frontend/public/urdf` (git submodule). Robot model table moves to
`frontend/public/urdf/models.json` (one source for `robotModels.ts` and the backend) — the
submodule is a separate repo: if it cannot be changed, put `models.json` next to
`frontend/src/features/robot-control/model/` and read it from both sides.

### Interfaces

**Pi (each workflow device method, on `VisionMachine`, referenced from `VisionProgram`):**

| Method | Inputs → outputs | Meaning |
| --- | --- | --- |
| `StartLayer2Run`, `StartTagReferenceRun`, `StartHandEyeAuto`, `StartBoardCalibrationRun` | `RunJson: String` → `RunId: String`, `Error: VisionErrorCode` | start a workflow; refused with BUSY while another runs |
| `ReportRobotPose` | `ReportJson: String` → `Error` | `{ "runId", "targetId", "reached": bool, "flangeInBase": pose, "joints": [..] }` |
| `AbortRun` | `RunId` → `Error` | stop the run, Pi returns to idle |
| status `Status/RobotTarget` | String (JSON) | `wsc.vision.robottarget/1`: `{ runId, targetId, flangeInBase: {position, orientation xyzw}, confirm: bool, label }`, `""` when no target is pending |
| status `Status/RunStatus` | String (JSON) | `{ runId, kind, phase, step, total, detail, finished, outcome, result }` |

Not reached (`reached:false`, e.g. no IK solution) → the Pi tries the next candidate
(tilt, standoff) or skips the item; the run never hangs: no report within a timeout → abort.

**WSC backend (wire messages, `models/messages.py`):**
- Client: `startRun { runKind, deviceId, robotId, params }`, `haltRun { runId }`,
  `confirmRun { runId, answer }`, `listRuns {}`.
- Server: `runProgress { runId, kind, phase, step, total, target?, plannedJoints?, detail }`,
  `runConfirmRequired { runId, prompt }`, `runFinished { runId, outcome, result?, error? }`
  — broadcast through `websocket/client_hub.py`.
- `runtime/runs.py` `RunManager`: one run per robot and per Pi (second start → BUSY); runs
  survive a page reload (`listRuns` to reattach); shutdown halts robots.
- `runtime/motion_bridge.py`: one generic run kind `piWorkflow`: call the Pi start method →
  watch `Status/RobotTarget` → IK + clearance (+ confirm) → `robot_drive` → FK of the reached
  pose → `ReportRobotPose`; mirror `Status/RunStatus` into `runProgress`.
- `runtime/robot_drive.py`: port of `robotDrive.driveJoints` (go_to via
  `action_execution.execute_robot_action`, poll joints every 250 ms, 3 still reads = arrived,
  120 s timeout, go_to reset), also available as run kind `driveJoints`.

### Tests (behaviour-equivalent, same case names)

| TS test (WSC frontend) | Goes to |
| --- | --- |
| `centreOnTag.test` (11), `boardCentring.test`, `tagServo.test` | Pi `tests/test_centring.py` |
| `tagReferenceRun.test` (29), `robotTagRun.test` (36), `tagViews.test` runner part (20) | Pi `tests/test_tagref_run.py` |
| `tagReferenceFusion.test` | merged into existing Pi `tests/test_tagreference.py` (only missing cases) |
| `handEyeAutoPoses.test`, `handEyeOrder.test` | Pi `tests/test_handeye_auto.py` (golden poses as JSON fixtures, generated once from the TS code) |
| `boardCalibrationRunner.test` (17), `boardCalibrationPoses.test` | Pi `tests/test_calibration_run.py` |
| `approachPlan.test` | compared with Pi `tests/test_layer2_plan.py`; port only missing cases |
| `robotDrive*.test`, `jointSettling.test`, `moveTracker.test`, `cellClearance.test` | WSC `backend/tests/test_robot_drive.py`, `test_clearance.py` |
| new | WSC `backend/tests/test_kinematics.py` (FK vs. TS goldens for all 4 URDFs to 1e-9; IK round trip within 2 mm / 1°; seed configuration kept), `test_runs.py`, `test_motion_bridge.py` |

Record the full TS case → Python case table in this plan ("Test mapping").

## Lessons from stage 1 (read before working)

Problems found in review of Codex' stage 1 work — avoid them:
1. **Hollow ported tests.** Several "ported" cases had the right name but asserted something
   unrelated (e.g. a node id string). A ported case must exercise the same behaviour as the
   TS case. A case that only makes sense in the frontend stays in the frontend.
2. **Tests that never ran.** A test that skips in the sandbox (sockets) was never executed and
   contained wrong code (async API without `await`). Every new test must have been run green
   **outside** the sandbox, and each bug-fix test must fail without the fix.
3. **Duplicate test names** silently replace earlier tests in Python. Check with
   `grep -o 'def test_[a-z_]*' FILE | sort | uniq -d`.
4. **Fake-only coverage of external libraries.** The mDNS module only had tests with a fake
   browser and did not work with real zeroconf. Add one test against the real library where
   it is cheap (loopback).
5. **"Dead" code that was not dead.** A write-only `self._control_task` kept asyncio tasks
   alive; removing it risked garbage-collected tasks. Keep strong task references.
6. **Contract errors propagate.** A wrong type in the contract (`SaveUseCaseImage.Series` as
   String) was implemented on three sides. If the contract contradicts existing code, ask.
7. **Real-environment smoke test finds what unit tests miss** (referenced methods crashed the
   discovery). Always end with the smoke test below.
8. Readability: lines ≤ 100 characters (ruff config), no 300-character one-liners.

## How to run Codex

The sandbox caused most stalls in stage 1. Use:

```
codex exec -C <worktree> -s workspace-write \
  -c sandbox_workspace_write.network_access=true \
  --json -o <dir>/codex-last.json --output-schema <dir>/codex-result.schema.json - < prompt.md
```

- `network_access=true` lets the Pi and backend tests open loopback OPC UA servers; without it
  they hang (the Pi suite stalled for 40 min).
- The sandbox protects `.git` even with `writable_roots`: Codex cannot commit from a worktree.
  Either commit yourself after review, or run Codex in this trusted local repo with
  `-s danger-full-access`.
- Long commands: start the full Pi suite in the background with a log file and poll it.
- Resume a session with `codex exec resume <thread_id> -c sandbox_mode='"workspace-write"' …`
  (`resume` has no `-s`/`-C`; start it from the worktree directory).

## Codex prompt (stage 2, verbatim)

```
Du arbeitest an zwei lokalen Repos. Lies zuerst vollständig:
/home/meric/ADP/ADP_flexible_Roboterzelle/<Worktree>/doc/projektdoku/arbeitsplaene/pi-workflows-robotertarget.md
(dieser Plan, verbindlich), pi-backend-discovery.md (Stufe-1-Vertrag), CLAUDE.md und .claude/skills/projektdoku/.

Erledige zuerst Part A (Stufe 1 abschließen), dann Part B (Stufe 2) in dieser Reihenfolge:
1. Branches anlegen: Pi `vision-workflows-stufe2` von `vision-part10-discovery`,
   WSC `vision-workflows-stufe2` von `geraete-discovery-stufe1`; je ein eigener git worktree.
   Im WSC-Worktree: node_modules aus /home/meric/ADP/webskillcomposition/frontend verlinken und das
   Submodul frontend/public/urdf lokal aus /home/meric/ADP/webskillcomposition/frontend/public/urdf
   initialisieren (git -c protocol.file.allow=always submodule update).
2. Verträge: Abschnitt "Interfaces" dieses Plans ist fix; jede Abweichung erst hier eintragen.
3. Pi: robot_target.py (Layer 2 darauf), centring.py, tagref_run.py, handeye_auto.py,
   calibration_run.py mit Methoden und Status-Knoten laut Vertrag; Tests portieren.
4. WSC-Backend: kinematics/, runs.py, motion_bridge.py, robot_drive.py, clearance.py,
   cell_watch_guard.py, Nachrichten; Tests.
5. WSC-Frontend: features/runs (useRun, RunProgress); Panels auf Formular + Start/Stopp + Fortschritt;
   die im Plan genannten Dateien samt Tests löschen. Pick & Place und UI-Feinschliff NICHT anfassen.
6. Doku auf aktuellen Stand (keine Historie), wie in Part A Schritt 2 beschrieben, plus Stufe 2.

Regeln: kein toter Code (was ersetzt wird, wird im selben Schritt gelöscht); portierte Tests prüfen
dasselbe Verhalten wie der TS-Fall (keine Hüllen); jeder neue Test wurde außerhalb der Sandbox grün
ausgeführt; Bugfix-Tests schlagen ohne den Fix fehl; Zeilen ≤ 100 Zeichen; Englisch für Code und Doku,
deutsche Bediener-/Log-Texte bleiben deutsch. Lies die "Lessons from stage 1" im Plan.
Prüfung am Ende (alle grün, Zahlen berichten):
- Pi: PYTHONPATH=src /home/meric/ADP/ADP_flexible_Roboterzelle/.venv/bin/python -m unittest discover -s tests -t .
- WSC: cd backend && /home/meric/ADP/webskillcomposition/backend/.venv/bin/python -m pytest -q
- WSC: cd frontend && npx vitest run && npm run typecheck (4 bekannte Altfehler, siehe Plan)
- Smoke-Test laut Plan gegen den echten Zellserver.
- Zeilenbilanz: git diff --shortstat gegen die Basis beider Branches.
Abschlussmeldung im vorgegebenen JSON-Schema.
```

## Verification (end of stage 2)

1. All three suites green; pre-existing frontend failures unchanged (4).
2. Test parity: mapping table complete; no hollow cases; no duplicate test names.
3. Dead code: `grep` for removed names empty; `tsc --noEmit --noUnusedLocals` no new errors;
   no exported symbol without importer in changed TS files.
4. Smoke test (stage 1 already proven): start the real cell server locally **without a camera**
   — set `VISION_CAMERA_BACKEND` to a backend that does not open the laptop webcam, or disable
   capture — with `OPCUA_MDNS=0 OPCUA_LDS_URL=""`; then drive the WSC backend
   `handle_client_message`: `connectServer` → `discoverRobots` → `executeRobotAction`
   (`vision_program`, a method) and, for stage 2, `startRun` with a fake robot connection.
   Note: in stage 1 the OpenCV backend opened the laptop webcam and saved a frame; avoid that.
5. Line balance reported per repo and area.

## Open questions

- Can the `frontend/public/urdf` submodule take `models.json`, or does it live in WSC?
- Real-robot validation of the Python IK (fr3 redundancy) needs a supervised session at low
  speed before use; dry-run mode (preview only) first.

## Deviations from the plan

- none yet

## After completion

Status `done`, actual interfaces, README row, lasting knowledge into the topic docs.
