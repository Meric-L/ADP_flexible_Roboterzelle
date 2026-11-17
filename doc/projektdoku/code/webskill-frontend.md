# webskillcomposition: frontend

Reference for the frontend of webskillcomposition outside `features/cell-modules/`
([webskill-cell-modules.md](webskill-cell-modules.md)), branch `ADP_Lokalisierung`, as of 2026-11-17.
Overview: [code-dokumentation.md](../code-dokumentation.md). Paths are relative to `frontend/src/`.

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
backend: run sequencing and Pi orchestration (a run stops when the tab closes), the Pi busy state
(per page only), the tag-map sync, and the hand-written Pi bindings in `piServers.ts` (the backend
discovery does not know the Pis). Moving it is not part of this project.

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
- Public methods cover server connect/disconnect, address-space browsing, robot create/bind/sync, robot goto (`callRobotGoto`, speed-capped by `capGotoSpeed`), end effector, actions, `callRawMethod`, `writeNodeValue`, and waypoint capture.
- Details: file header and `applicationController.test.ts` (about 1600 lines of tests).

### `app/layout/DesktopLayout.tsx` — extended
- `DesktopLayout`: the single screen. Nested `react-resizable-panels` groups hold the joint panel, `Viewport`, camera streams, the Layout1 (address space, node details, message log) or Layout2 (`TaskPanel`) switch, `CellWatchPanel` and `ServerManager`.
- Wraps content in the providers for robot control, robot interaction, OPC UA server, Pi activity and task editor. Uses `LIVESTREAM_CAMERAS` from `features/viewport/model/sceneState.ts`.

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
- `ServerManager` auto-connects both Pis (`PI_1_SERVER_URL`, `PI_2_SERVER_URL`) on mount and lists other servers. Camera and calibration UI: `CameraView`, `CameraLiveImage`, `CameraZoomModal`, `CameraCalibrationModal`, `CameraPreview`. Vision and cell watch: `AutolocateActions`, `CellWatchPanel`, `RobotRunConfirm`, `TagMapSyncLine`, `PiStatusList`, `PiActivity`.
- Method and event UI: `RawMethodCallModal`, `EventSubscriptionPanel`, `VariableSubscriptionPanel`, `SaveRawImageButton`, `LastCalibrationBadge`, `ConnectOpcUa`.
- Hooks used here are in `features/opcua-server/model/`. Behaviour details: `docs/vision-workflows.md`.

### `features/opcua-server/config/piServers.ts` — new
- Pi endpoints (`PI_1_SERVER_URL`, `PI_2_SERVER_URL`, hard-coded `opc.tcp://10.10.38.x` addresses), namespace URIs and `LAYER_LABELS`.
- `resolveVisionBinding(namespaceUris)` builds the NodeId set for a Pi (`VisionBinding`). An unknown namespace yields "unknown", and callers must refuse to run. Binding types: `CalibrationBinding`, `HandEyeBinding`, `CellWatchBinding`, `Layer2Binding`.
- Also `CAMERA_OVERLAY_MODES`, `START_SINGLE_JOB_ERRORS` (error code map). All NodeId construction is centralised here, as `docs/vision-workflows.md` states.

### `features/opcua-server/config/robotServers.ts` — new
- `KNOWN_ROBOT_SERVER_URLS`: static list of robot server URLs shown in the server manager.

### `features/opcua-server/context/OpcuaServerContext.tsx` — extended
- `OpcuaServerProvider` (props `controller`, `snapshot`) and `useOpcuaServer()`. Exposes server state plus `subscribeNode`, `unsubscribeNode`, `subscribeEvent`, `unsubscribeEvent`, `callRawMethod`, `writeNodeValue`, all delegating to `ApplicationController`.

### `features/opcua-server/context/PiActivityContext.tsx` — new
- `PiActivityProvider`, `usePiActivity()` (register, returns a remover), `usePiBlocker(serverUrl, ownerId)` (returns the activity holding the Pi, if another owner has one).

### `features/opcua-server/model/piMethodCall.ts` — new
- `callPiMethod(controller, serverUrl, nodeId, args)`: calls a Pi method and polls for its result (15 s timeout, 100 ms poll). Returns `PiCallOutcome` (`done`, `nothingToDo`, `failed`). An abort of an idle job counts as `nothingToDo`.

### `features/opcua-server/model/useVisionJob.ts` — new
- `useVisionJob`: runs one `StartSingleJob` on a Pi. Subscribes to the vision node's events, calls the method through `callRawMethod`, and returns `VisionJobState` (`idle`, `running`, `succeeded`, `failed`).

### `features/opcua-server/model/visionResult.ts` — new
- `parseVisionResult(raw)` turns the vision JSON into `VisionResult` (`detections`, `worldTags`, `VisionCameraLocalization`). Test: `visionResult.test.ts`.

### `features/opcua-server/model/useMjpegStreamUrl.ts` — new
- `useMjpegStreamUrl(serverUrl)`: MJPEG URL built from the Pi's `CameraStreamHttpPort` node. Returns null while the port is 0 or the node is missing (older Pi builds).

### `features/opcua-server/model/useMjpegWatchdog.ts` — new
- `useMjpegWatchdog(imageRef, streamUrl)`: detects a frozen MJPEG stream by hashing the middle square of the picture and checks `/snapshot.jpg`. Reconnects on stall, returns a reconnect counter. Thresholds are module constants.

### `features/opcua-server/model/useCameraFrame.ts` — new
- `useCameraFrame(serverUrl, enabled)`: base64 frame fallback from the Pi's frame nodes. Subscribes nothing while `enabled` is false (MJPEG in use).

### `features/opcua-server/model/useCameraHealth.ts` — new
- `useCameraHealth(serverUrl)` reads the OPC UA `DeviceHealth` node. Returns `CameraHealthState`; `describeCameraHealth` maps it to text. Consumed by `CameraStreamPanel` and `PiStatusList`.

### `features/opcua-server/model/useCameraCalibration.ts` and `cameraCalibration.ts` — new
- `useCameraCalibration(serverUrl)`: drives the Pi's camera calibration (`start`, `capture`, `finish`, `abort`) and returns `CameraCalibrationState` with phase and progress.
- `cameraCalibration.ts`: pure types and parsers (`CalibrationProgress`, `CalibrationResult`, `deriveCalibrationPhase`, `expectedProcessingMs`). Warning threshold `RMS_WARNING_THRESHOLD_PX` = 0.5 px; `MIN_FINISH_SAMPLES` = 3.

### `features/opcua-server/model/useHandEyeCalibration.ts` — new
- `useHandEyeCalibration(serverUrl)`: hand-eye calibration commands (`start`, `capture`, `finish`, `abort`) through `callRawMethod`, with a command-ended counter to avoid missing fast replies.

### `features/opcua-server/model/useTagMapSync.ts` — new
- `useTagMapSync(serverUrl)`: subscribes to the Pi's tag-map JSON node, compares it with the catalog, and sends the map via `SetTagMap`. Returns `TagMapSyncState` (`available`, `differences`, `sending`, `sent`, `waiting`, `error`). Error texts are German strings from `tagMapErrorText`.

### `features/opcua-server/model/useCellWatch.ts` — new
- `useCellWatch(serverUrl)`: cell-watch start/stop (re-arm with a reference) on the ceiling Pi. Returns `CellWatchState` (`status`, `error`, start/stop calls). Consumed by `AutolocateActions` and `Viewport`.

### `features/opcua-server/model/useCaseMeasurement.ts` and `useSaveRawImage.ts` — new
- `useCaseMeasurement.ts`: parses `SaveUseCaseImage` result JSON into `UseCaseMeasurement` (accuracy zone `gruen`/`gelb`/`rot`/`ausserhalb`) and formats it.
- `useSaveRawImage.ts`: `useSaveRawImage(serverUrl)` saves a raw or use-case image on the Pi. Error texts are German, mapped from `VisionErrorCode` values.

### `features/opcua-server/model/piActivity.ts`, `piConnection.ts`, `usePauseWhileRobotMoves.ts`, `useConnectServerWithFeedback.ts` — new
- `piActivity.ts`: pure list helpers (`addPiActivity`, `removePiActivity`, `findBlockingPiActivity`) for the Pi busy state.
- `piConnection.ts`: `isPiConnected(servers, url)`.
- `usePauseWhileRobotMoves.ts`: pauses the Pi watch while the robot is moving (reason text "Roboter fährt", i.e. "robot is moving"). Used by `AutolocateActions`.
- `useConnectServerWithFeedback.ts`: connect-with-toast hook used by `ConnectOpcUa` and `AutolocateActions`.

### `features/viewport/components/` (other files) — mixed
- `index.ts` exports `Viewport` and `ViewportHeaderControls` (used by `DesktopLayout`).
- `ViewportHeaderControls.tsx`: controls in the viewport header panel (props take the viewport scene state).
- `DragControls.tsx`, `GoalMarker.tsx`, `OriginGizmo.tsx`, `TaskWaypointMarkers.tsx`, `WorkspacePointCloud.tsx` (memoized): scene objects.
- `SolverStatusPanel.tsx`, `StatsOverlay.tsx`, `EnvironmentLoader.tsx`, `EnvironmentErrorBoundary.tsx`: status overlays and environment loading.

### `features/viewport/components/URDFLoader.js` and `URDFLoader.d.ts` — mixed
- Local JavaScript copy of the `urdf-loader` parser (`URDFLoader` class; imports `urdf-loader/src/URDFClasses`). The `.d.ts` gives it types. Used by `Viewport.tsx` and by `tag-tool/loadModule.ts`.

### `features/viewport/model/sceneState.ts` — extended
- `useViewportSceneState()` returns the viewport scene state (settings, camera streams, pose previews, Layer 2 proposals). Defines `LIVESTREAM_CAMERAS` ("Kamera 1" and "Kamera 2", i.e. camera 1 and camera 2, on the two Pi URLs) and `CameraStreamSettings`.

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
