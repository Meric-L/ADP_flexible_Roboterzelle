# webskillcomposition: frontend outside cell modules

Detailed reference for the other frontend code of webskillcomposition, branch `ADP_Lokalisierung`, as of 2026-11-16. Overview: [code-dokumentation.md](../code-dokumentation.md).


Source: branch `ADP_Lokalisierung`, working tree. Paths are relative to `frontend/src/` unless noted. Project docs referenced below: `docs/vision-workflows.md`, `docs/features.md`, `docs/architecture.md`, `docs/data-and-interface-model.md`.

### `App.tsx`
- Root component. Creates one `ApplicationController` (memoized) over a `WscWebSocketClient` built from `getWebSocketUrl()`.
- Subscribes to controller state and WebSocket status, then renders `AppFeedbackProvider` > `SolverConfigProvider` > `MethodCallFeedbackBridge` > `DesktopLayout`.
- Logo asset: `assets/plcm-logo.svg`. Architecture overview: `docs/architecture.md`.

### `main.tsx`
- Entry for `index.html`. Imports `index.css`, mounts `<App />` in `StrictMode` on `#root`.

### `index.css`
- Tailwind v4 (`@import "tailwindcss"`) plus `@heroui/styles`.
- Design tokens are RGB triplets on `:root` (e.g. `--bg`, `--fg`, `--fg-muted`), with utility classes for panels, headers and buttons (`panel`, `panel-header`, `button-ghost`).

### `vite-env.d.ts`
- One line: `/// <reference types="vite/client" />`. No project code.

### `app/model/applicationController.ts`
- Central controller class `ApplicationController` (`createApplicationController` factory, `ApplicationSnapshot` type). Owns the snapshot and dispatches WebSocket messages.
- Public methods cover server connect/disconnect, address-space browsing, robot create/bind/sync, robot goto (`callRobotGoto`, speed-capped by `capGotoSpeed`), end effector, actions, `callRawMethod`, `writeNodeValue`, and waypoint capture.
- Details: file header and `applicationController.test.ts` (about 1600 lines of tests).

### `app/context/AppFeedbackContext.tsx`
- `AppFeedbackProvider` and `useAppFeedback()`: app-wide toast/feedback API built on antd `App` (`AntdApp`). Error helpers take `description`, `key`, `duration` options.

### `app/components/`
- `MethodCallFeedbackBridge.tsx`: watches `snapshot` method-call records and pushes success/failure feedback through `useAppFeedback`.
- `FeedbackPopupContent.tsx`: popup body with the `FeedbackPopupVariant` styles used by the feedback API.

### `app/layout/DesktopLayout.tsx`
- `DesktopLayout`: the single screen. Nested `react-resizable-panels` groups hold the joint panel, `Viewport`, camera streams, the Layout1 (address space, node details, message log) or Layout2 (`TaskPanel`) switch, `CellWatchPanel` and `ServerManager`.
- Wraps content in the providers for robot control, robot interaction, OPC UA server, Pi activity and task editor. Uses `LIVESTREAM_CAMERAS` from `features/viewport/model/sceneState.ts`.

### `entities/robot/model/types.ts`
- Robot domain types: `Robot`, `RobotInfo`, `RobotJointState`, `RobotTcpPose`, `RobotActionBinding`, `RobotVisualBinding`, `RobotPanelState`, `RobotConnectionStatus`.
- Factories and bindings: `createRobotFromSession`, `createLocalRobot`, `bindRobotToMotionDevice`, `unbindRobotFromMotionDevice`.

### `entities/robot/model/store.ts`
- `RobotStoreState` (`byId`, `activeRobotId`), `initialRobotStoreState`, and `applyRobotMessage(state, ServerMessage)`, a pure reducer over server messages.

### `entities/robot/model/maxSpeed.ts`
- Speed cap for robot goto moves: `MAX_SPEED_RANGE` (0.01 to 0.5, step 0.01), `DEFAULT_MAX_SPEED` 0.1.
- `parseMaxSpeed`, `getMaxSpeed`, `setMaxSpeed`, `useMaxSpeed`. Persisted with `readStored`/`writeStored` from `shared/storage.ts`.

### `entities/robot/model/endEffector.ts`
- `findToggleEndEffectorKey` and `findEndEffectorActions` locate the toggle and open/close actions in a robot's action map.

### `entities/server/model/types.ts`
- Server domain types: `ServerStatus`, `ServerSessionInfo`, `NodeBinding`, `AddressSpaceNode`, `AddressSpaceNodeDetails`, `MethodArgument`, `MotionDeviceBinding`, `AxisBinding`, `MethodBinding`, `SkillBinding`.

### `entities/server/model/store.ts`
- `ServerStoreState` (`initialServerStoreState`): servers, address space, node values, method-call status records, OPC UA events, error records.
- `applyServerMessage` is the reducer. Helpers track node and event subscriptions (`markNodeSubscription`, `markEventSubscription`) and address-space requests. Node keys use `serverNodeKey`/`serverNodeIdFromKey`.
- Tests: `store.test.ts` (392 lines).

### `entities/` (dependency note)
- `entities/robot/model/maxSpeed.ts` is the only file in `entities/` that uses `shared/storage.ts`. The two stores import message types from `shared/api/messages.ts`.

### `shared/api/messages.ts`
- Wire protocol types: `ClientMessage` (union of all outgoing commands: connect/discover, subscribe/unsubscribe, joints, modes, method calls, address-space browse, write) and `ServerMessage` (union of incoming updates and results).
- Protocol reference: `docs/data-and-interface-model.md`.

### `shared/api/websocketClient.ts`
- `WscWebSocketClient`: the transport. Methods `connect`, `disconnect`, `send`, `onMessage`, `onStatus`, `onMessageLog`, plus one request-builder per command (returns a request id). Outgoing messages are queued until the socket is open.
- Types: `WebSocketClientStatus`, `WebSocketLike`, `WebSocketFactory` (injectable for tests), `WebSocketMessageLogEntry`.
- Test: `websocketClient.test.ts`.

### `shared/api/websocketUrls.ts`
- `getWebSocketUrl()`: `VITE_WSC2_WS_URL`, else `ws://127.0.0.1:8000/ws` in dev, else derived from `window.location` (`/ws`).
- `getSurfaceWebSocketUrl()`: same base with `/ws/surface`. Used by workspace surface generation.

### `shared/api/surfaceMessages.ts` and `surfaceMessageLog.ts`
- `surfaceMessages.ts`: client/server message types and `SurfaceProcessingConfig` (voxel size, sigma, iso level, mapping mode) for the surface WebSocket.
- `surfaceMessageLog.ts`: `emitSurfaceMessageLog` / `onSurfaceMessageLog`, a small pub/sub used to log surface traffic.

### `shared/api/formatUnknownPayload.ts`
- `formatUnknownPayload(value)`: turns an arbitrary payload into display text. Used by `features/opcua-server/components/NodeDetailsPanel.tsx` and `VariableSubscriptionPanel.tsx`.

### `shared/createStore.ts`
- `createStore<T>(initial)` returns `{ get, set, subscribe }`. `useStore(store)` wraps `useSyncExternalStore`. Used by `entities/robot/model/maxSpeed.ts`.

### `shared/storage.ts`
- `readStored(key, parse, fallback)` and `writeStored(key, value)` over `localStorage`. Any failure (no storage, throw, bad JSON) returns the fallback or is ignored.

### `shared/format.ts` and `shared/parse.ts`
- `format.ts`: operator-facing text helpers. `comma` (German decimal comma), `roundedMm`, `formatMm`, `formatWorldMm`, `errorText`.
- `parse.ts`: `finiteOrNull` and `isFiniteVector` for untyped server values.

### `shared/math/angles.ts`
- `DEG`, `degToRad`, `radToDeg`.

### `shared/ui/`
- `FloatingWindow.tsx` (`FloatingWindowProps`, default export) with geometry helpers in `floatingWindowGeometry.ts` (`clampMove`, `clampResize`, `initialWindowRect`, placement presets, pixel constants).
- Small controls: `DisclosureSection.tsx`, `ResizeHandle.tsx`, `SecondsField.tsx`, `StepButton.tsx`, `Toggle.tsx`. Used by the opcua-server, cell-modules and task components.

### `features/opcua-server/components/`
- React UI for the OPC UA side. `index.ts` re-exports the six components used by `DesktopLayout`: `AddressSpaceTree`, `CameraStreamPanel`, `MessageLog`, `NodeDetailsPanel`, `ServerManager`, `CellWatchPanel`.
- `ServerManager` auto-connects both Pis (`PI_1_SERVER_URL`, `PI_2_SERVER_URL`) on mount and lists other servers. Camera and calibration UI: `CameraView`, `CameraLiveImage`, `CameraZoomModal`, `CameraCalibrationModal`, `CameraPreview`. Vision and cell watch: `AutolocateActions`, `CellWatchPanel`, `RobotRunConfirm`, `TagMapSyncLine`, `PiStatusList`, `PiActivity`.
- Method and event UI: `RawMethodCallModal`, `EventSubscriptionPanel`, `VariableSubscriptionPanel`, `SaveRawImageButton`, `LastCalibrationBadge`, `ConnectOpcUa`.
- Hooks used here are in `features/opcua-server/model/`. Behaviour details: `docs/vision-workflows.md`.

### `features/opcua-server/config/piServers.ts`
- Pi endpoints (`PI_1_SERVER_URL`, `PI_2_SERVER_URL`, hard-coded `opc.tcp://10.10.38.x` addresses), namespace URIs and `LAYER_LABELS`.
- `resolveVisionBinding(namespaceUris)` builds the NodeId set for a Pi (`VisionBinding`). An unknown namespace yields "unknown", and callers must refuse to run. Binding types: `CalibrationBinding`, `HandEyeBinding`, `CellWatchBinding`, `Layer2Binding`.
- Also `CAMERA_OVERLAY_MODES`, `START_SINGLE_JOB_ERRORS` (error code map). All NodeId construction is centralised here, as `docs/vision-workflows.md` states.

### `features/opcua-server/config/robotServers.ts`
- `KNOWN_ROBOT_SERVER_URLS`: static list of robot server URLs shown in the server manager.

### `features/opcua-server/context/OpcuaServerContext.tsx`
- `OpcuaServerProvider` (props `controller`, `snapshot`) and `useOpcuaServer()`. Exposes server state plus `subscribeNode`, `unsubscribeNode`, `subscribeEvent`, `unsubscribeEvent`, `callRawMethod`, `writeNodeValue`, all delegating to `ApplicationController`.

### `features/opcua-server/context/PiActivityContext.tsx`
- `PiActivityProvider`, `usePiActivity()` (register, returns a remover), `usePiBlocker(serverUrl, ownerId)` (returns the activity holding the Pi, if another owner has one).

### `features/opcua-server/model/piMethodCall.ts`
- `callPiMethod(controller, serverUrl, nodeId, args)`: calls a Pi method and polls for its result (15 s timeout, 100 ms poll). Returns `PiCallOutcome` (`done`, `nothingToDo`, `failed`). An abort of an idle job counts as `nothingToDo`.

### `features/opcua-server/model/useVisionJob.ts`
- `useVisionJob`: runs one `StartSingleJob` on a Pi. Subscribes to the vision node's events, calls the method through `callRawMethod`, and returns `VisionJobState` (`idle`, `running`, `succeeded`, `failed`).

### `features/opcua-server/model/visionResult.ts`
- `parseVisionResult(raw)` turns the vision JSON into `VisionResult` (`detections`, `worldTags`, `VisionCameraLocalization`). Test: `visionResult.test.ts`.

### `features/opcua-server/model/useMjpegStreamUrl.ts`
- `useMjpegStreamUrl(serverUrl)`: MJPEG URL built from the Pi's `CameraStreamHttpPort` node. Returns null while the port is 0 or the node is missing (older Pi builds).

### `features/opcua-server/model/useMjpegWatchdog.ts`
- `useMjpegWatchdog(imageRef, streamUrl)`: detects a frozen MJPEG stream by hashing the middle square of the picture and checks `/snapshot.jpg`. Reconnects on stall, returns a reconnect counter. Thresholds are module constants.

### `features/opcua-server/model/useCameraFrame.ts`
- `useCameraFrame(serverUrl, enabled)`: base64 frame fallback from the Pi's frame nodes. Subscribes nothing while `enabled` is false (MJPEG in use).

### `features/opcua-server/model/useCameraHealth.ts`
- `useCameraHealth(serverUrl)` reads the OPC UA `DeviceHealth` node. Returns `CameraHealthState`; `describeCameraHealth` maps it to text. Consumed by `CameraStreamPanel` and `PiStatusList`.

### `features/opcua-server/model/useCameraCalibration.ts` and `cameraCalibration.ts`
- `useCameraCalibration(serverUrl)`: drives the Pi's camera calibration (`start`, `capture`, `finish`, `abort`) and returns `CameraCalibrationState` with phase and progress.
- `cameraCalibration.ts`: pure types and parsers (`CalibrationProgress`, `CalibrationResult`, `deriveCalibrationPhase`, `expectedProcessingMs`). Warning threshold `RMS_WARNING_THRESHOLD_PX` = 0.5 px; `MIN_FINISH_SAMPLES` = 3.

### `features/opcua-server/model/useHandEyeCalibration.ts`
- `useHandEyeCalibration(serverUrl)`: hand-eye calibration commands (`start`, `capture`, `finish`, `abort`) through `callRawMethod`, with a command-ended counter to avoid missing fast replies.

### `features/opcua-server/model/useTagMapSync.ts`
- `useTagMapSync(serverUrl)`: subscribes to the Pi's tag-map JSON node, compares it with the catalog, and sends the map via `SetTagMap`. Returns `TagMapSyncState` (`available`, `differences`, `sending`, `sent`, `waiting`, `error`). Error texts are German strings from `tagMapErrorText`.

### `features/opcua-server/model/useCellWatch.ts`
- `useCellWatch(serverUrl)`: cell-watch start/stop (re-arm with a reference) on the ceiling Pi. Returns `CellWatchState` (`status`, `error`, start/stop calls). Consumed by `AutolocateActions` and `Viewport`.

### `features/opcua-server/model/useCaseMeasurement.ts` and `useSaveRawImage.ts`
- `useCaseMeasurement.ts`: parses `SaveUseCaseImage` result JSON into `UseCaseMeasurement` (accuracy zone `gruen`/`gelb`/`rot`/`ausserhalb`) and formats it.
- `useSaveRawImage.ts`: `useSaveRawImage(serverUrl)` saves a raw or use-case image on the Pi. Error texts are German, mapped from `VisionErrorCode` values.

### `features/opcua-server/model/piActivity.ts`, `piConnection.ts`, `usePauseWhileRobotMoves.ts`, `useConnectServerWithFeedback.ts`
- `piActivity.ts`: pure list helpers (`addPiActivity`, `removePiActivity`, `findBlockingPiActivity`) for the Pi busy state.
- `piConnection.ts`: `isPiConnected(servers, url)`.
- `usePauseWhileRobotMoves.ts`: pauses the Pi watch while the robot is moving (reason text "Roboter fährt"). Used by `AutolocateActions`.
- `useConnectServerWithFeedback.ts`: connect-with-toast hook used by `ConnectOpcUa` and `AutolocateActions`.

### `features/viewport/components/Viewport.tsx`
- `Viewport({ sceneState })`: the 3D scene (`@react-three/fiber` Canvas, drei, postprocessing). Loads the robot URDF with `URDFLoader`, renders robots, goal marker, origin gizmo, drag controls, workspace point cloud, task waypoints, cell-module and world-tag markers.
- Runs the IK and collision checks (`createRobotIkModel`, `detectCollidingRobotIds`) and the workspace surface generation. About 1800 lines; the file has no header comment.

### `features/viewport/components/` (other files)
- `index.ts` exports `Viewport` and `ViewportHeaderControls` (used by `DesktopLayout`).
- `ViewportHeaderControls.tsx`: controls in the viewport header panel (props take the viewport scene state).
- `DragControls.tsx`, `GoalMarker.tsx`, `OriginGizmo.tsx`, `TaskWaypointMarkers.tsx`, `WorkspacePointCloud.tsx` (memoized): scene objects.
- `SolverStatusPanel.tsx`, `StatsOverlay.tsx`, `EnvironmentLoader.tsx`, `EnvironmentErrorBoundary.tsx`: status overlays and environment loading.

### `features/viewport/components/URDFLoader.js` and `URDFLoader.d.ts`
- Local JavaScript copy of the `urdf-loader` parser (`URDFLoader` class; imports `urdf-loader/src/URDFClasses`). The `.d.ts` gives it types. Used by `Viewport.tsx` and by `tag-tool/loadModule.ts`. Origin of the copy is not documented (see Flags).

### `features/viewport/context/SolverConfigContext.tsx`
- `SolverConfigProvider`, `useSolverConfig()`. Holds the `SolverConfig` (defaults in `model/solverConfig.ts`: `DEFAULT_SOLVER_CONFIG`, `SOLVE_STATUS_LABELS`).

### `features/viewport/model/sceneState.ts`
- `useViewportSceneState()` returns the viewport scene state (settings, camera streams, pose previews, Layer 2 proposals). Defines `LIVESTREAM_CAMERAS` (Kamera 1 and Kamera 2 on the two Pi URLs) and `CameraStreamSettings`.

### `features/viewport/model/robotIk.ts`, `robotIkModel.ts`, `robotIkConstraints.ts`
- `robotIk.ts`: `solvePositionIk` and tool-point helpers (`findToolPoint`, `getToolPointWorldPosition`, `getToolPointWorldQuaternion`). Looks for end-effector link names such as `tool_point`, `tool0`, `tcp`.
- `robotIkModel.ts`: `createRobotIkModel` returns `RobotIkModel` with `IkSolveResult` and `IkTargetPose`.
- `robotIkConstraints.ts`: `prepareIkTarget` and `evaluateIkConvergence` (`pose` or `position` mode).

### `features/viewport/model/workspaceSurfaceGeneration.ts`
- `generateWorkspaceSurface({ robot, localRoot, resolution, signal, onProgress })`: samples joint space and sends points to the surface WebSocket (`getSurfaceWebSocketUrl`), then returns the surface. Resolutions: `low`, `medium`, `high`. Supports `AbortSignal`.

### `features/viewport/model/robotCollision.ts`
- `detectCollidingRobotIds(entries: RobotCollisionEntry[])` returns a `Set` of colliding robot ids.

### `features/robot-control/components/`
- `JointAnglesPanel.tsx` (joint values panel), `RobotActionsPanel.tsx`, `RobotDetails.tsx`, `RobotManager.tsx` (robot list with select, sync and take-control toggles; props `RobotManagerProps`).

### `features/robot-control/context/RobotControlContext.tsx`
- `RobotControlProvider` (props `controller`, `snapshot`) and `useRobotControl()`. Exposes robots, active robot, motion devices, joint managers, and actions (`createRobot`, `selectRobot`, `startRobotSync`, `setRobotTakeControl`, `executeRobotAction`, and others).

### `features/robot-control/context/RobotInteractionContext.tsx`
- `RobotInteractionProvider` and `useRobotInteraction()`. Tracks which joint source is manipulating a robot (`JOINT_SOURCE_ID`), with a sync-mode flag and a completion event. Used by `Viewport` and `JointAnglesPanel`.

### `features/robot-control/model/jointStateManager.ts`
- `createJointStateManager()` returns a `JointStateManager` that merges joint angles from sources. Sources are ranked by `JOINT_SOURCE_PRIORITY` (RESET 6, ANIMATION 5, SYNC 4, IK 3, DRAG 2, MANUAL 1, FK 0).

### `features/robot-control/model/robotJointRuntime.ts`
- `RobotJointRuntime` class (`createRobotJointRuntime()`): per-robot joint managers, sync sessions, manipulation state, sync goto in flight, animation (`advanceAnimation`), and heal of stale manipulation (15 s). Entry points: `startSync`, `update`, `stopSync`, `beginManipulation`.

### `features/robot-control/model/robotJointSync.ts`
- Maps the robot's URDF joints to OPC UA joint values: `createRobotJointMapping`, `applyRobotJointMapping`, `createRobotJointSyncSession`, `createActiveRobotJointSyncSession`, `getActiveRobot`.

### `features/robot-control/model/robotModels.ts`
- `ROBOT_MODEL_OPTIONS` (models `eva`, `fr3`, `fr3_wagon`, `ur5e`, `demo1`) with URDF paths, home angles and mount rotation.
- Helpers: `resolveRobotModelFromIdentity`, `resolveHomeAnglesForModel`, `resolveRobotMountRotation`, `defaultRobotOrigin`.

### `features/robot-control/model/flangeKinematics.ts`
- `createFlangeKinematics` / `loadFlangeKinematics`: forward and inverse kinematics of the flange in the robot base frame, without a viewport. Tolerances: `IK_TRANSLATION_TOLERANCE_M` = 0.002 m, `IK_ROTATION_TOLERANCE_RAD` = 1 degree.

### `features/robot-control/model/axisMapping.ts`
- Maps between axis values from the OPC UA side and visual joint angles: `mapVisualAnglesToAxisValues`, `mapAxisValuesToJointAngles`, `mapRobotJointStateToVisualAngles`, plus degree/radian conversion and `normalizeJointValue`.

### `features/task/components/`
- Task editor UI for the Layout2 panel: `TaskPanel` (program list left, editor right), `TaskProgramPanel` (task list and save; `TaskSaveControls` is exported from the same file), `TaskEditor` (React Flow canvas), `GenericEditorNode` (node renderer), `TaskInsertionMenu` (skill picker, `buildInitialSkillInputs`).
- `index.ts` exports `TaskPanel`, `TaskEditor`, `TaskInsertionMenu`, `GenericEditorNode`, `TaskProgramPanel`, `TaskEditorProvider`, `useTaskEditor`. `DesktopLayout` imports `TaskPanel` and `TaskEditorProvider` from it.
- Saving calls the backend's add-task method (`callRawMethod` with `runtimeNodeIds.addTask`) and then re-browses the task list.

### `features/task/context/TaskEditorContext.tsx`
- `TaskEditorProvider({ snapshot })` and `useTaskEditor()`: editor nodes and edges and node updates. Task-server node ids used for saving (`addTask`, `taskList`) come from `runtimeNodeIds` in `TaskProgramPanel`.

### `features/task/model/taskDocument.ts`
- Document model for a task: `TaskDocument`, `TaskMetadata`, `TaskWaypointValue`, `TaskConstant`, `SkillTaskStep`, `TaskTimelineStep`.
- Parsers and serialisers: `parseTaskDocument`, `serializeTaskDocumentForServer`, `createEmptyTaskDocument`, `parseTaskTimelineStep`, `isTaskConstantReference`.
- Also contains `Legacy*` graph types and parsers for older stored tasks (see Flags).

### `features/task/model/editorGraph.ts`
- Conversion between the React Flow graph and the task document: `importTaskDocumentToGraph`, `exportTaskDocumentFromGraph`, `validateTaskEditorGraphForSave`, node data builders (`createSkillNodeData`, `createVariableNodeData`, constant nodes), handle ids and value-type compatibility checks.

### `features/task/model/taskScene.ts`, `resolveNodeCollisions.ts`, `types.ts`
- `taskScene.ts`: `getTaskSceneWaypointMarkers` produces waypoint markers for `Viewport`.
- `resolveNodeCollisions.ts`: `resolveNodeCollisions` resolves overlaps between editor nodes (options type `CollisionAlgorithmOptions`).
- `types.ts`: type re-exports only.

### `features/task/` (status)
- Still referenced from `app/layout/DesktopLayout.tsx` (Layout2 is the default). Last commit touching this folder is `839857c` (see Flags). There is no current `docs/task-editor.md`.

### `tag-tool/TagToolApp.tsx` and `tag-tool/main.tsx`
- `main.tsx` is the entry for `tag-tool.html` (second Rollup input in `vite.config.ts`). Mounts `TagToolApp` in `StrictMode`.
- `TagToolApp`: standalone tag-placement page (title "Tag-Platzierung"). Loads a bundled or picked module, places AprilTags on surfaces, edits bracket offsets and spin, and exports tag poses and a fixed-link URDF snippet.

### `tag-tool/loadModule.ts`
- `BUNDLED_MODULES` (from `ROBOT_MODEL_OPTIONS`), `loadBundledModule`, and `loadPickedModule(folder, urdfPath)`. Picked folders are mapped through `uploadedFiles.ts` to `upload://` URLs.

### `tag-tool/components/`
- `ModuleScene.tsx`: 3D view (`SceneTag`, `GizmoSettings`). `sceneParts.tsx`: `TagModel` (texture from `tagTexture`), `Bracket`, `AxesArrows`, `OriginFrame`.
- `ExportPanel.tsx`: export UI (`buildTagExport`, `buildUrdfSnippet`). `NumberField.tsx`, `Section.tsx`: form helpers (`Section`, `Hint`, `Segmented`).
- `components/sceneParts.tsx` is imported by `features/cell-modules/components/WorldTagMarkers.tsx`.

### `tag-tool/model/exportTagPose.ts`
- Builds the export: `buildTagMapEntry` (role `module` or `robot`), `buildTagExport`, `buildUrdfSnippet`, `formatExportJson`. `APRILTAG_FRAME_DESCRIPTION` is a text constant for the output.

### `tag-tool/model/tagFrame.ts`
- `APRILTAG_TO_PI_TAG_FRAME` and `toPiTagFrame`: converts the AprilTag frame used in the tool to the OpenCV frame the Pi uses (half turn about x). `DEFAULT_TAG_SIZE_M` = 0.08 m. `printedTagSizeM` adds the white quiet zone.

### `tag-tool/model/placement.ts`
- `tagPoseOnSurface(anchor, spin)`, `spinAboutFaceNormal`, `bracketOffsets`, `withBracketOffsets`, `defaultImageUp`. Surface anchor type: `SurfaceAnchor`.

### `tag-tool/model/pose.ts`, `units.ts`
- `pose.ts`: `Pose`, `Vec3`, `Quat` types; conversions to and from three.js, `poseToMatrix`, `matrixToPose`, `composePoses`, `invertPose`, `rpyFromQuaternion`.
- `units.ts`: `mToMm`, `mmToM`, `radToDeg`, `degToRad`.

### `tag-tool/model/tag36h11.ts`
- AprilTag tag36h11 code table (`TAG36H11_CODES`, max id `TAG36H11_MAX_ID`) and `tag36h11Cells(id)` for drawing the tag. Border 8 cells, printed size 10 cells.

### `tag-tool/model/uploadedFiles.ts`
- `UPLOAD_SCHEME` (`upload://`), `UPLOAD_PACKAGE_SCHEME`, `normalizePath`, `directoryOf`, `resolveUploadedPath(url, paths)`. Maps URDF mesh references onto picked files.

### `tag-tool/model/urdfTree.ts`
- URDF helpers for the tool: `owningLink`, `defaultOriginLink`, `linkPoseInRoot`, `movableJointsBetween`.

### `tag-tool/` (dependencies)
- Imports `features/robot-control/model/robotModels.ts` (bundled module list) and `features/viewport/components/URDFLoader.js`. Served by `tag-tool.html`. Behaviour overview: `docs/vision-workflows.md`, section on the tag tool.

