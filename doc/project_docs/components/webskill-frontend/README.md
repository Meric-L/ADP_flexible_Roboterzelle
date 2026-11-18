# WSC frontend

The webskillcomposition (WSC) browser frontend (React, TypeScript, Vite). It controls the robot, talks to the vision Pis through the backend and runs the vision workflows.

Code: `frontend/src/` in the WSC repo (`/home/meric/ADP/webskillcomposition`, branch `geraete-discovery-stufe1`).

## Structure

| Folder (under `frontend/src/`) | Content |
| --- | --- |
| `app/` | `ApplicationController` (`app/model/applicationController.ts`, owns the snapshot, dispatches WebSocket messages), `DesktopLayout.tsx` |
| `entities/` | `server/` and `robot/` stores (`announcedServers`, `motionDevicesById`, `deviceActionStates`) |
| `features/opcua-server/` | Pi and OPC UA side: device path, camera, calibration, cell watch, tag-map sync |
| `features/cell-modules/` | Module catalog, Layer 1 and Layer 2, board calibration, tag reference, pick and place |
| `features/robot-control/`, `features/viewport/`, `features/task/` | Robot control, 3D viewport with IK, task editor (WSC team) |
| `shared/`, `tag-tool/` | Helpers; the tag tool (own entry `tag-tool.html`) |

Only devices with `kind: "robot"` become URDF robots. Vite dev server: port 1234 (`npm run dev` in `frontend/`).

## How it talks to devices

All Pi access uses the generic device path; the frontend names no NodeIds and no Pi URLs.

| File (`features/opcua-server/model/`) | Role |
| --- | --- |
| `visionDevice.ts` | `DEVICE_ACTION` and `DEVICE_VARIABLE` (snake_case keys); `deviceByRole(state, "ceiling" \| "hand")` picks by serial prefix `vision-ceiling-` or `vision-flange-` |
| `deviceCall.ts` | `callDevice(controller, deviceId, action, inputs, { timeoutMs })` runs `executeRobotAction`, polls the action state every 100 ms |
| `useDevice.ts` | `useDeviceCall`, `useDeviceVariable`, `useVisionServerUrls` |

`callDevice` returns a `DeviceCallOutcome`:

| Outcome | Cause |
| --- | --- |
| `done` | terminal state; `outputs` by name, `result` for a program |
| `rejected` | non-zero device error; `errorCode`, German `message` |
| `nothingToDo`, `missing` | backend codes `nothingToDo`, `methodMissing` |
| `failed`, `timeout` | other backend error; no terminal state in time |

`describeFailure(outcome)` gives the German operator text. Measurement calls `(RequestJson) -> (ResultJson, Error)` go through `features/cell-modules/model/deviceMeasure.ts`.

## Vision workflows in the frontend

The Pi measures; the frontend sequences, plans poses, solves IK and drives the robot (a run stops when the tab closes).

| Workflow | Frontend entry | Pi action |
| --- | --- | --- |
| Layer 1 cell scan | `AutolocateLayer1Window.tsx`, `useVisionJob.ts`, `layer1Import.ts` | `vision_program` (ceiling) |
| Layer 2 approach | `ApproachPlanPanel.tsx`; `planApproach` in `approachPlan.ts` with real IK | `measure_tags` (hand) |
| Tag survey and reference | `TagReferencePanel.tsx`, `tagReferenceRun.ts` | `plan_tag_reference_view`, `measure_tag_reference` |
| Hand-eye auto | `HandEyeAutoPanel.tsx`, `handEyeAutoPoses.ts` | `*_hand_eye_calibration`, `measure_board` |
| Board calibration (ceiling camera) | `useBoardCalibrationRun.ts`, `boardCalibrationPoses.ts` | `start_calibration`, `capture_calibration_sample`, `finish_calibration` |
| Tag-map sync | `useTagMapSync.ts`, `tagMapSync.ts` | `set_tag_map` |
| Cell watch | `useCellWatch.ts`, `AutolocateActions.tsx` | `start_cell_watch`, `stop_cell_watch` |

- Cell watch pause: `usePauseWhileRobotMoves.ts` pauses the ceiling Pi's watch while the robot moves (`pause_cell_watch`, reason "Roboter fährt", max 120 s, renewed every 60 s) and resumes it when the robot stands.
- Pick and place (`PickPlacePanel.tsx`, `pickPlace.ts`, `usePickPlaceRun.ts`) is a use case of our group only; it does not go to `dev`.
- UI polish and layout are done by the WSC team, not by us.

## Ours and pre-existing

| Part | Origin |
| --- | --- |
| Robot control, viewport and IK, OPC UA browser, task editor, WebSocket client | WSC team |
| Pi and vision side (`features/opcua-server/`), `features/cell-modules/`, `tag-tool/`, `shared/`, flange kinematics | ours |
| Task editor, viewport scene state | extended by us |

Per-module origin: [code/webskill-frontend.md](../../code/webskill-frontend.md), [code/webskill-cell-modules.md](../../code/webskill-cell-modules.md).

## Where to read more

| Topic | Document |
| --- | --- |
| Per-module reference | [webskill-frontend.md](../../code/webskill-frontend.md), [webskill-cell-modules.md](../../code/webskill-cell-modules.md) |
| Backend and WebSocket model | [WSC backend](../webskill-backend/README.md) |
| Calibration and integration topics | [frontend-integration-calibration.md](../../topics/frontend-integration-calibration.md), [layer2-rules.md](../../topics/layer2-rules.md) |
| Setup, integration, layers | [installation](../../usage/installation.md), [integration](../../usage/integration.md), [layers](../../layers.md) |
| WSC docs | `docs/vision-workflows.md`, `docs/data-and-interface-model.md`, `docs/architecture.md`, `docs/features.md` |
