# webskillcomposition: frontend

Reference for the frontend of webskillcomposition outside `features/cell-modules/`
([webskill-cell-modules.md](webskill-cell-modules.md)), branch `ADP_Lokalisierung`.
Overview: [README.md](README.md). Paths are relative to `frontend/src/`.

## What is new and what already existed

The frontend existed before our project. It was written by the webskillcomposition team (robot
control, 3D viewport and IK, OPC UA browser, task editor, WebSocket client). Our group added the
whole Pi and vision side (`features/opcua-server/` model, config and most components), the tag tool
(`tag-tool/`), shared helpers (`shared/`), flange kinematics, and extended the task editor and the
viewport scene state.

Parts we did not change are not described here: `App.tsx`, `main.tsx`, `index.css`, `app/context/`,
`app/components/`, `entities/server/`, `shared/api/`, `features/viewport/components/Viewport.tsx`,
the viewport IK, collision and surface models, and the robot-control contexts, joint sync and axis
mapping. See the webskillcomposition docs below.

**Note:** much of our workflow logic runs in the frontend although it would fit better in the
backend: run sequencing and Pi orchestration (a run stops when the tab closes), the IK and robot
driving of the workflows, and the tag-map sync. The Pis themselves are discovered by the backend
and called through the generic device path (`visionDevice.ts`, `deviceCall.ts`, `useDevice.ts`).

### Documentation sources

| Document | Written by | Covers |
| --- | --- | --- |
| `webskillcomposition/docs/architecture.md` | webskillcomposition team | project structure, system overview |
| `webskillcomposition/docs/data-and-interface-model.md` | webskillcomposition team | WebSocket commands and events |
| `webskillcomposition/docs/features.md` | webskillcomposition team, small additions by us | features of the application |
| `webskillcomposition/docs/vision-workflows.md` | us | vision, catalog and tag workflows, Pi connection path |
| this page | us | module reference with origin |

## Module reference

Origin per heading: **new**, **extended** (existing module with our additions), **mixed** (folder
with new and existing files).

### `app/model/applicationController.ts` — extended
- Central controller class `ApplicationController` (`createApplicationController` factory, `ApplicationSnapshot` type). Owns the snapshot and dispatches WebSocket messages.
- Public methods cover server connect/disconnect, `listAnnouncedServers`, address-space browsing, robot create/bind/sync, robot goto (`callRobotGoto`, speed-capped by `capGotoSpeed`), end effector, actions, `callRawMethod`, `writeNodeValue`, and waypoint capture.
- Only discovered devices with `kind: "robot"` become URDF robots. `executeRobotAction` also runs the actions of other devices (a vision Pi) on their own id.
- Details: file header and `applicationController.test.ts` (about 1600 lines of tests).

### `app/layout/DesktopLayout.tsx` — extended
- `DesktopLayout`: the single screen. Nested `react-resizable-panels` groups hold the joint panel, `Viewport`, camera streams, the Layout1 (address space, node details, message log) or Layout2 (`TaskPanel`) switch, `CellWatchPanel` and `ServerManager`.
- Wraps content in the providers for robot control, robot interaction, OPC UA server and task editor.

### `entities/server/model/store.ts`, `entities/server/model/types.ts` — extended
- `ServerStoreState` additionally holds `announcedServers` (from `serversAnnounced`), `motionDevicesById` (every discovered device, any kind) and `deviceActionStates[deviceId][action]` (`DeviceActionRecord`: `requestId` plus the last `RobotActionState`, with `result` and `errorCode`).
- A device's watcher states carry no request id; they are filed under the request that started the action. `isRoboticsServer` is true when the server has a device of kind `robot`.
- Types: `RobotSessionInfo.kind` (`DeviceKind`), `RobotActionState.result` / `errorCode`, `MethodArgument.enumValues`.

### `entities/robot/model/store.ts` — extended
- `RobotStoreState` (`byId`, `activeRobotId`), `initialRobotStoreState`, and `applyRobotMessage(state, ServerMessage)`, a pure reducer over server messages.

### `entities/robot/model/maxSpeed.ts` — new
- Speed cap for robot goto moves: `MAX_SPEED_RANGE` (0.01 to 0.5, step 0.01), `DEFAULT_MAX_SPEED` 0.1.
- `parseMaxSpeed`, `getMaxSpeed`, `setMaxSpeed`, `useMaxSpeed`. Persisted with `readStored`/`writeStored` from `shared/storage.ts`.

### `entities/robot/model/endEffector.ts` — new
- `findToggleEndEffectorKey` and `findEndEffectorActions` locate the toggle and open/close actions in a robot's action map.

### `entities/` (dependency note) — mixed
- `entities/robot/model/maxSpeed.ts` is the only file in `entities/` that uses `shared/storage.ts`. The two stores import message types from `shared/api/messages.ts`.

### `shared/createStore.ts` — new
- `createStore<T>(initial)` returns `{ get, set, subscribe }`. `useStore(store)` wraps `useSyncExternalStore`. Used by `entities/robot/model/maxSpeed.ts`.

### `shared/storage.ts` — new
- `readStored(key, parse, fallback)` and `writeStored(key, value)` over `localStorage`. Any failure (no storage, throw, bad JSON) returns the fallback or is ignored.

### `shared/format.ts` and `shared/parse.ts` — new
- `format.ts`: operator-facing text helpers. `comma` (German decimal comma), `roundedMm`, `formatMm`, `formatWorldMm`, `errorText`.
- `parse.ts`: `finiteOrNull` and `isFiniteVector` for untyped server values.

### `shared/math/angles.ts` — new
- `DEG`, `degToRad`, `radToDeg`.

### `shared/ui/` — mixed
- `FloatingWindow.tsx` (`FloatingWindowProps`, default export) with geometry helpers in `floatingWindowGeometry.ts` (`clampMove`, `clampResize`, `initialWindowRect`, placement presets, pixel constants).
- Small controls: `DisclosureSection.tsx`, `ResizeHandle.tsx`, `SecondsField.tsx`, `StepButton.tsx`, `Toggle.tsx`. Used by the opcua-server, cell-modules and task components.

### `features/opcua-server/components/` — mixed
- React UI for the OPC UA side. `index.ts` re-exports the six components used by `DesktopLayout`: `AddressSpaceTree`, `CameraStreamPanel`, `MessageLog`, `NodeDetailsPanel`, `ServerManager`, `CellWatchPanel`.
- `ServerManager` asks the backend for the announced servers (`listAnnouncedServers`), connects and discovers each one once, and lists the servers that offer robots; manual entry is `ConnectOpcUa`. Camera and calibration UI: `CameraView`, `CameraLiveImage`, `CameraZoomModal`, `CameraCalibrationModal`, `CameraPreview`. Vision and cell watch: `AutolocateActions`, `CellWatchPanel`, `TagMapSyncLine`, `PiStatusList`, `PiActivity`.
- `PiActivity`: shows what a Pi does (variable `busy_reason`, cell-watch state) and resets it (abort calibration and hand-eye, halt `vision_program`).
- The components take a device (`RobotSessionInfo`) from `deviceByRole`, not a server URL.
- Method and event UI: `RawMethodCallModal`, `EventSubscriptionPanel`, `VariableSubscriptionPanel`, `SaveRawImageButton`, `LastCalibrationBadge`, `ConnectOpcUa`.
- Hooks used here are in `features/opcua-server/model/`. Behaviour details: `docs/vision-workflows.md`.

### `features/opcua-server/config/piServers.ts` — new
- `CAMERA_OVERLAY_MODES` (`off`, `apriltag`, `undistorted`, `tagreference`) and `CameraOverlayMode`.
- `buildMjpegStreamUrl(serverUrl, port)` and `buildTagReferenceImageUrl(serverUrl, port)`: the host is taken from the OPC UA endpoint.
- `DEVICE_HEALTH`: texts and tone for the `DeviceHealthEnumeration` values 0 to 4.
- No NodeIds and no Pi URLs: the devices come from backend discovery.

### `features/opcua-server/model/visionDevice.ts` — new
- `DEVICE_ACTION` and `DEVICE_VARIABLE`: the snake_case keys of the Pi's actions and variables (`vision_program`, `save_raw_image`, `measure_tags`, `busy_reason`, `latest_camera_frame`, ...). The one place the app names them.
- `deviceByRole(serverState, "ceiling" | "hand")`: the vision device whose serial number starts with `vision-ceiling-` or `vision-flange-`. `visionDevices`, `variableNodeId(device, key)` (`""` when not offered), `hasAction(device, key)`.

### `features/opcua-server/model/deviceCall.ts` — new
- `callDevice(controller, deviceId, action, inputs, { timeoutMs })`: runs the action through `executeRobotAction` (quiet), polls the device action state every 100 ms and returns a `DeviceCallOutcome`: `done` (`outputs`, `result`), `rejected` (`errorCode`, German `message`, `outputs`, `result`), `nothingToDo`, `missing`, `failed`, `timeout`.
- Backend error codes `methodMissing` give `missing`, `nothingToDo` gives `nothingToDo`.
- `describeFailure(outcome)`: German operator text, or null for `done` and `nothingToDo`.

### `features/opcua-server/model/useDevice.ts` — new
- `useDeviceCall(device)`: `{ busy, error, call }`; `call(action, inputs, timeoutMs)` wraps `callDevice` and keeps the last failure text.
- `useDeviceVariable(device, key, parse)`: subscribes to a device variable and returns the parsed live value (node details first, then live values).
- `useVisionServerUrls()`: server URLs of the discovered vision devices.

### `features/opcua-server/config/robotServers.ts` — new
- `KNOWN_ROBOT_SERVER_URLS`: static list of robot server URLs shown in the server manager.

### `features/opcua-server/context/OpcuaServerContext.tsx` — extended
- `OpcuaServerProvider` (props `controller`, `snapshot`) and `useOpcuaServer()`. Exposes server state plus `listAnnouncedServers`, `subscribeNode`, `unsubscribeNode`, `subscribeEvent`, `unsubscribeEvent`, `callRawMethod`, `writeNodeValue`, all delegating to `ApplicationController`.

### `features/opcua-server/model/useVisionJob.ts` — new
- `useVisionJob(device)`: runs one job as the device action `vision_program` (`recipe_id`, `continuous: false`) and takes the payload from the final `ResultSet` (`latest_result_json`; a failed job still carries it). Returns `VisionJobState` (`idle`, `running`, `succeeded`, `failed`, `blockedBy` from `busy_reason`). `close()` halts the program.

### `features/opcua-server/model/visionResult.ts` — new
- `parseVisionResult(raw)` turns the vision JSON into `VisionResult` (`detections`, `worldTags`, `VisionCameraLocalization`). Test: `visionResult.test.ts`.

### `features/opcua-server/model/useMjpegStreamUrl.ts` — new
- `useMjpegStreamUrl(device)`: MJPEG URL built from the variable `camera_stream_http_port`. Returns null while the port is 0 or the variable is missing.

### `features/opcua-server/model/useMjpegWatchdog.ts` — new
- `useMjpegWatchdog(imageRef, streamUrl)`: detects a frozen MJPEG stream by hashing the middle square of the picture and checks `/snapshot.jpg`. Reconnects on stall, returns a reconnect counter. Thresholds are module constants.

### `features/opcua-server/model/useCameraFrame.ts` — new
- `useCameraFrame(device, enabled)`: base64 frame fallback from the variable `latest_camera_frame`. Subscribes nothing while `enabled` is false (MJPEG in use).

### `features/opcua-server/model/useCameraHealth.ts` — new
- `useCameraHealth(device)` reads the variable `device_health`. Returns `CameraHealthState`; `describeCameraHealth` maps it to text. Consumed by `CameraStreamPanel` and `PiStatusList`.

### `features/opcua-server/model/useCameraCalibration.ts` and `cameraCalibration.ts` — new
- `useCameraCalibration(device)`: drives the Pi's camera calibration (`start_calibration`, `capture_calibration_sample`, `finish_calibration`, `abort_calibration`; `start`, `capture`, `finish`, `abort`) and returns `CameraCalibrationState` with phase and progress.
- `cameraCalibration.ts`: pure types and parsers (`CalibrationProgress`, `CalibrationResult`, `deriveCalibrationPhase`, `expectedProcessingMs`). Warning threshold `RMS_WARNING_THRESHOLD_PX` = 0.5 px; `MIN_FINISH_SAMPLES` = 3.

### `features/opcua-server/model/useHandEyeCalibration.ts` — new
- `useHandEyeCalibration(device)`: hand-eye calibration commands (`start`, `capture`, `finish`, `abort`) through `callDevice`; status from the variable `hand_eye_status`.

### `features/opcua-server/model/useTagMapSync.ts` — new
- `useTagMapSync(device)`: subscribes to the variable `tag_map_json`, compares it with the catalog, and sends the map with `set_tag_map`. Returns `TagMapSyncState` (`available`, `differences`, `sending`, `sent`, `waiting`, `error`). Error texts are German strings from `tagMapErrorText`.

### `features/opcua-server/model/useCellWatch.ts` — new
- `useCellWatch(device)`: cell-watch start/stop (`start_cell_watch`, `stop_cell_watch`) (re-arm with a reference) on the ceiling Pi. Returns `CellWatchState` (`status`, `error`, start/stop calls). Consumed by `AutolocateActions` and `Viewport`.

### `features/opcua-server/model/useCaseMeasurement.ts` and `useSaveRawImage.ts` — new
- `useCaseMeasurement.ts`: parses the `ResultJson` output of `save_use_case_image` into `UseCaseMeasurement` (accuracy zone `gruen`/`gelb`/`rot`/`ausserhalb`) and formats it.
- `useSaveRawImage.ts`: `useSaveRawImage(device)` saves a raw image on the Pi (`save_raw_image`). The error text is the Pi's German `VisionErrorCode` description (`describeFailure`).

### `features/opcua-server/model/usePauseWhileRobotMoves.ts`, `useConnectServerWithFeedback.ts` — new
- `usePauseWhileRobotMoves.ts`: pauses the ceiling Pi's cell watch while the robot is moving (`pause_cell_watch` with `reason` "Roboter fährt", i.e. "robot is moving", and `max_pause_seconds`, renewed every 60 s; `resume_cell_watch` afterwards). Used by `AutolocateActions`.
- `useConnectServerWithFeedback.ts`: connect-with-toast hook used by `ConnectOpcUa` and `AutolocateActions`.

### `features/viewport/components/` (other files) — mixed
- `index.ts` exports `Viewport` and `ViewportHeaderControls` (used by `DesktopLayout`).
- `ViewportHeaderControls.tsx`: controls in the viewport header panel (props take the viewport scene state).
- `DragControls.tsx`, `GoalMarker.tsx`, `OriginGizmo.tsx`, `TaskWaypointMarkers.tsx`, `WorkspacePointCloud.tsx` (memoized): scene objects.
- `SolverStatusPanel.tsx`, `StatsOverlay.tsx`, `EnvironmentLoader.tsx`, `EnvironmentErrorBoundary.tsx`: status overlays and environment loading.

### `features/viewport/components/URDFLoader.js` and `URDFLoader.d.ts` — mixed
- Local JavaScript copy of the `urdf-loader` parser (`URDFLoader` class; imports `urdf-loader/src/URDFClasses`). The `.d.ts` gives it types. Used by `Viewport.tsx` and by `tag-tool/loadModule.ts`.

### `features/viewport/model/sceneState.ts` — extended
- `useViewportSceneState()` returns the viewport scene state (settings, camera streams, pose previews, Layer 2 proposals). Defines `CameraStreamSettings`; the stream settings are kept per vision Pi server URL.

### `features/viewport/model/robotIk.ts`, `robotIkModel.ts`, `robotIkConstraints.ts` — mixed
- `robotIk.ts`: `solvePositionIk` and tool-point helpers (`findToolPoint`, `getToolPointWorldPosition`, `getToolPointWorldQuaternion`). Looks for end-effector link names such as `tool_point`, `tool0`, `tcp`.
- `robotIkModel.ts`: `createRobotIkModel` returns `RobotIkModel` with `IkSolveResult` and `IkTargetPose`.
- `robotIkConstraints.ts`: `prepareIkTarget` and `evaluateIkConvergence` (`pose` or `position` mode).

### `features/robot-control/components/` — mixed
- `JointAnglesPanel.tsx` (joint values panel), `RobotActionsPanel.tsx`, `RobotDetails.tsx`, `RobotManager.tsx` (robot list with select, sync and take-control toggles; props `RobotManagerProps`).

### `features/robot-control/model/robotJointRuntime.ts` — extended
- `RobotJointRuntime` class (`createRobotJointRuntime()`): per-robot joint managers, sync sessions, manipulation state, sync goto in flight, animation (`advanceAnimation`), and heal of stale manipulation (15 s). Entry points: `startSync`, `update`, `stopSync`, `beginManipulation`.

### `features/robot-control/model/robotModels.ts` — extended
- `ROBOT_MODEL_OPTIONS` (models `eva`, `fr3`, `fr3_wagon`, `ur5e`, `demo1`) with URDF paths, home angles and mount rotation.
- Helpers: `resolveRobotModelFromIdentity`, `resolveHomeAnglesForModel`, `resolveRobotMountRotation`, `defaultRobotOrigin`.

### `features/robot-control/model/flangeKinematics.ts` — new
- `createFlangeKinematics` / `loadFlangeKinematics`: forward and inverse kinematics of the flange in the robot base frame, without a viewport. Tolerances: `IK_TRANSLATION_TOLERANCE_M` = 0.002 m, `IK_ROTATION_TOLERANCE_RAD` = 1 degree.

### `features/task/components/` — mixed
- Task editor UI for the Layout2 panel: `TaskPanel` (program list left, editor right), `TaskProgramPanel` (task list and save; `TaskSaveControls` is exported from the same file), `TaskEditor` (React Flow canvas), `GenericEditorNode` (node renderer), `TaskInsertionMenu` (skill picker, `buildInitialSkillInputs`).
- `index.ts` exports `TaskPanel`, `TaskEditor`, `TaskInsertionMenu`, `GenericEditorNode`, `TaskProgramPanel`, `TaskEditorProvider`, `useTaskEditor`. `DesktopLayout` imports `TaskPanel` and `TaskEditorProvider` from it.
- Saving calls the backend's add-task method (`callRawMethod` with `runtimeNodeIds.addTask`) and then re-browses the task list.

### `features/task/context/TaskEditorContext.tsx` — extended
- `TaskEditorProvider({ snapshot })` and `useTaskEditor()`: editor nodes and edges and node updates. Task-server node ids used for saving (`addTask`, `taskList`) come from `runtimeNodeIds` in `TaskProgramPanel`.

### `features/task/model/taskDocument.ts` — new
- Document model for a task: `TaskDocument`, `TaskMetadata`, `TaskWaypointValue`, `TaskConstant`, `SkillTaskStep`, `TaskTimelineStep`.
- Parsers and serialisers: `parseTaskDocument`, `serializeTaskDocumentForServer`, `createEmptyTaskDocument`, `parseTaskTimelineStep`, `isTaskConstantReference`.
- Also contains `Legacy*` graph types and parsers for older stored tasks.

### `features/task/model/editorGraph.ts` — extended
- Conversion between the React Flow graph and the task document: `importTaskDocumentToGraph`, `exportTaskDocumentFromGraph`, `validateTaskEditorGraphForSave`, node data builders (`createSkillNodeData`, `createVariableNodeData`, constant nodes), handle ids and value-type compatibility checks.

### `features/task/model/taskScene.ts`, `resolveNodeCollisions.ts`, `types.ts` — mixed
- `taskScene.ts`: `getTaskSceneWaypointMarkers` produces waypoint markers for `Viewport`.
- `resolveNodeCollisions.ts`: `resolveNodeCollisions` resolves overlaps between editor nodes (options type `CollisionAlgorithmOptions`).
- `types.ts`: type re-exports only.

### `features/task/` (status) — mixed
- Still referenced from `app/layout/DesktopLayout.tsx` (Layout2 is the default). Last commit touching this folder is `839857c`. There is no current `docs/task-editor.md`.

### `tag-tool/TagToolApp.tsx` and `tag-tool/main.tsx` — new
- `main.tsx` is the entry for `tag-tool.html` (second Rollup input in `vite.config.ts`). Mounts `TagToolApp` in `StrictMode`.
- `TagToolApp`: standalone tag-placement page (title "Tag-Platzierung", i.e. "tag placement"). Loads a bundled or picked module, places AprilTags on surfaces, edits bracket offsets and spin, and exports tag poses and a fixed-link URDF snippet.

### `tag-tool/loadModule.ts` — new
- `BUNDLED_MODULES` (from `ROBOT_MODEL_OPTIONS`), `loadBundledModule`, and `loadPickedModule(folder, urdfPath)`. Picked folders are mapped through `uploadedFiles.ts` to `upload://` URLs.

### `tag-tool/components/` — new
- `ModuleScene.tsx`: 3D view (`SceneTag`, `GizmoSettings`). `sceneParts.tsx`: `TagModel` (texture from `tagTexture`), `Bracket`, `AxesArrows`, `OriginFrame`.
- `ExportPanel.tsx`: export UI (`buildTagExport`, `buildUrdfSnippet`). `NumberField.tsx`, `Section.tsx`: form helpers (`Section`, `Hint`, `Segmented`).
- `components/sceneParts.tsx` is imported by `features/cell-modules/components/WorldTagMarkers.tsx`.

### `tag-tool/model/exportTagPose.ts` — new
- Builds the export: `buildTagMapEntry` (role `module` or `robot`), `buildTagExport`, `buildUrdfSnippet`, `formatExportJson`. `APRILTAG_FRAME_DESCRIPTION` is a text constant for the output.

### `tag-tool/model/tagFrame.ts` — new
- `APRILTAG_TO_PI_TAG_FRAME` and `toPiTagFrame`: converts the AprilTag frame used in the tool to the OpenCV frame the Pi uses (half turn about x). `DEFAULT_TAG_SIZE_M` = 0.08 m. `printedTagSizeM` adds the white quiet zone.

### `tag-tool/model/placement.ts` — new
- `tagPoseOnSurface(anchor, spin)`, `spinAboutFaceNormal`, `bracketOffsets`, `withBracketOffsets`, `defaultImageUp`. Surface anchor type: `SurfaceAnchor`.

### `tag-tool/model/pose.ts`, `units.ts` — new
- `pose.ts`: `Pose`, `Vec3`, `Quat` types; conversions to and from three.js, `poseToMatrix`, `matrixToPose`, `composePoses`, `invertPose`, `rpyFromQuaternion`.
- `units.ts`: `mToMm`, `mmToM`, `radToDeg`, `degToRad`.

### `tag-tool/model/tag36h11.ts` — new
- AprilTag tag36h11 code table (`TAG36H11_CODES`, max id `TAG36H11_MAX_ID`) and `tag36h11Cells(id)` for drawing the tag. Border 8 cells, printed size 10 cells.

### `tag-tool/model/uploadedFiles.ts` — new
- `UPLOAD_SCHEME` (`upload://`), `UPLOAD_PACKAGE_SCHEME`, `normalizePath`, `directoryOf`, `resolveUploadedPath(url, paths)`. Maps URDF mesh references onto picked files.

### `tag-tool/model/urdfTree.ts` — new
- URDF helpers for the tool: `owningLink`, `defaultOriginLink`, `linkPoseInRoot`, `movableJointsBetween`.

### `tag-tool/` (dependencies) — new
- Imports `features/robot-control/model/robotModels.ts` (bundled module list) and `features/viewport/components/URDFLoader.js`. Served by `tag-tool.html`. Behaviour overview: `docs/vision-workflows.md`, section on the tag tool.
