# webskillcomposition: cell modules (model and UI)

Detailed reference for the cell-module code of webskillcomposition, branch `ADP_Lokalisierung`, as of 2026-11-16. Overview: [code-dokumentation.md](../code-dokumentation.md).


Scope: `frontend/src/features/cell-modules/model/` (68 source files, 56 `*.test.ts` files), `config/`. `components/` is documented separately.

Overview: the model folder holds pure TypeScript logic (geometry, planning, validation, state machines) plus React hooks (`use*.ts`). Tests use Vitest and sit next to their source. Layer 1 = cell-wide localization from the ceiling Pi; Layer 2 = the robot-driven run (approach, hand-eye, tag reference, pick and place). Concepts: `webskillcomposition/docs/vision-workflows.md` (docs/vision-workflows.md) (sections "Calibration and localization", "Module catalog and tag map", "Tag measurement and reference-edge setup"); system overview in `webskillcomposition/docs/architecture.md` (docs/architecture.md); payload shapes in `webskillcomposition/docs/data-and-interface-model.md` (docs/data-and-interface-model.md).

### `frontend/src/features/cell-modules/config/`

- Module catalog data and types. `moduleCatalog.ts` defines `CellModuleDefinition`, `ModuleTag`, `ModuleModel`, `TagPlacement` and related types, `MODULE_CATALOG` (default seed), `TAG_FAMILY = "tag36h11"`, `findModuleByTag`, `worldTagIds`, `TAG_FACE_PRESETS`.
- `modelFiles.d.ts` is an ambient declaration for the virtual module `virtual:cell-module-files` (list of model files under `public/`, produced by a plugin in `frontend/vite.config.ts`). Used by `components/ModuleCatalogModal.tsx`.
- Details: the catalog backend and API are described in `webskillcomposition/docs/vision-workflows.md#module-catalog-and-tag-map` (docs/vision-workflows.md).

### `model/approachPlan.ts`

- Layer 2 part one: plans one reachable camera pose in front of each module tag from Layer 1 data only.
- Entry points: `planApproach` (ranks candidates, `onePerModule` option), `approachTags`, `approachTrajectory`, `standoffCandidates`, `cameraPoseFacing`, `lookDownHomeAngles`, `rangeCentre`, `describeSkipped`.
- Consumes `FlangeKinematics` limits; output `ApproachStop` list. Details: vision-workflows "Layer 2 approach" text in the Tag measurement section.

### `model/approachPlan.test.ts`

- Vitest tests for `approachPlan.ts` (standoff, tags, trajectory, skipped-stop text).

### `model/autolocateUi.ts`

- Cell-watch settings kept outside the component so they survive remounts.
- Entry points: `useAutolocateUi`, `AutolocateUiState`, `DEFAULT_WATCH_INTERVAL_S`, `DEFAULT_WATCH_CHANGE_S`.

### `model/boardCalibrationPoses.ts`

- Taught FR3 poses for ceiling-camera board calibration (joint limit margin, tool point). No fixed count since 17.11.2026: any number from `MIN_CALIBRATION_POSITIONS` (3) on.
- Entry points: `taughtPositionsFromStorage`, `taughtPositionsForStorage`, `stopsFromPositions`, `variedWrist`, `turnedAboutToolPoint`.

### `model/boardCalibrationRunner.ts`

- State machine that drives the taught board poses, takes pictures, and checks the board is seen (`idle` … states in the header comment).
- Entry points: `BoardRunner`, `stepsForPasses`, `targetPictures(count)`, `BoardRunState`, `BoardCheck`; constants `TARGET_PICTURES` (20, the minimum target), `MAX_PASSES`, `MIN_PICTURES`, `SETTLE_MS`. `targetPictures` is `max(TARGET_PICTURES, count)`, so every taught position gets at least one picture (since 17.11.2026).

### `model/boardCentring.ts`

- Centres the chessboard in the hand camera before a hand-eye sample.
- Entry points: `boardCentringStep`, `boardViewScore`, `boardCentred`, `describeBoardCheck`, `measureBoardRequestJson` / `parseMeasureBoardResult` (Pi request/response schemas).

### `model/catalogStore.ts`

- Module catalog as the operator edits it: parse/serialize the file, revision-based sync with the backend.
- Entry points: `parseCatalog`, `serializeCatalog`, `upsertModule`, `refreshModuleCatalog`, `saveModuleCatalog`, `resetModuleCatalog`, `useModuleCatalog`, `useModuleCatalogSync`, `CatalogConflictError`.
- Backend API: `GET`/`PUT /api/module-catalog` (see vision-workflows "Module catalog and tag map").

### `model/catalogStore.test.ts`, `model/catalogSync.test.ts`, `model/catalogValidation.test.ts`

- Vitest tests for the catalog store, its sync with the backend, and validation.

### `model/catalogValidation.ts`

- Validates a module definition before save: tag IDs (`MAX_TAG_ID`), tag size, tag centre on the module surface (`TAG_SURFACE_TOLERANCE_M`).
- Entry points: `validateModule`, `ModuleValidation`, `distanceToBoxSurface`, `tagSizeMatches`, `describeTagSize`.

### `model/cellClearance.ts`

- Keep-out volumes the arm must avoid: table (wagon), floor, and the arm/hand as spheres.
- Entry points: `keepOutFromRobotBounds`, `armPoints`, `collision`, `legCollision`; constants `TABLE_CLEARANCE_M`, `FLOOR_CLEARANCE_M`, `ARM_RADIUS_M`, `HAND_RADIUS_M`.

### `model/cellModules.ts`

- Detected cell modules as placed in the 3D scene; world-frame to scene-frame conversion (scene = world rotated −90° about X).
- Entry points: `PlacedCellModule`, `PlacedWorldTag`, `PlacementResult`, `placeLayer1Detections`, `placeWorldTags`, `worldToScenePosition`, `levelModulePose`, `pointOnRayAtHeight`, `tagIdOf`.
- Frames and conventions are described in the file header.

### `model/cellWatch.ts`

- Compares what the ceiling Pi sees with the placed modules; reports changed modules over time.
- Entry points: `CellWatchState`, `CellWatchStatus`, `CellChange`, `parseCellWatchStatus`, `cellWatchStartJson`, `cellChangeKey`, `CELL_WATCH_MAX_CAMERA_ERROR_PX`.

### `model/cellWatch.test.ts`

- Vitest tests for `cellWatch.ts`.

### `model/cellWatchSwitch.ts`

- Cancelling a job intentionally turns the cell watch off on the Pi.
- Entry points: `requestCellWatchOff`, `onCellWatchOffRequest`.

### `model/centreOnTag.ts`

- Centres the hand camera on a tag by repeated corrections; searches in a ring of views when the tag is not in the picture.
- Entry points: `centreOnTag`, `searchViews`, `CentreOutcome`; limits `MAX_CENTRING_ROUNDS`, `MAX_SEARCH_STEPS`, `SEARCH_STEP_DEG`.

### `model/centreOnTag.test.ts`

- Vitest tests for `centreOnTag.ts`.

### `model/controllerRobotDrive.ts`

- Decides whether the real robot may be driven for Layer 2 (`driveReadiness`) and builds the controller-backed drive dependencies.
- Entry points: `driveReadiness`, `DriveReadiness`, `robotModelLoaded`, `controllerRobotDeps`.

### `model/flangeTrajectory.ts`

- Type for a planned robot move drawn before anything drives (base frame).
- Entry point: `FlangeTrajectory`.

### `model/handEye.ts`

- Hand-eye calibration on the hand Pi (`T_flansch_cam`): state, sample and result types, the calibration result as stored, Pi status schema.
- Entry points: `HandEyeState`, `HandEyeSample`, `HandEyeResult`, `HandEyeStatus`, `FullCalibrationResult`, `parseFullCalibrationResult`, `HandEyeCurrent`, `CHARUCO_DICTIONARIES`. Format: cell repo `vision-server-interface.md` (not in this repo).

### `model/handEye.test.ts`

- Vitest tests for `handEye.ts`.

### `model/handEyeAutoPoses.ts`

- Generates robot poses for automatic hand-eye calibration: the robot looks at a fixed board from a set of views.
- Entry points: `generateHandEyePoses`, `selectHandEyePoses`, `boardIsUnambiguous`, `projectBoardCentrePx`, `HandEyeAutoPose`, `HandEyeAutoOptions`.

### `model/handEyeAutoPoses.test.ts`

- Vitest tests for `handEyeAutoPoses.ts`.

### `model/handEyeBoard.ts`

- Chessboard and ChArUco board settings: validation, load/save (local storage), text parse/format.
- Entry points: `HandEyeBoardSettings`, `CharucoBoardSettings`, `validateHandEyeBoard`, `validateCharucoBoard`, `loadHandEyeBoard`, `saveHandEyeBoard`, `loadCharucoBoard`, `charucoBoardToText`, `charucoBoardFromText`.
- Validation messages are German user-facing strings (quoted as data, not docstrings).

### `model/handEyeBoard.test.ts`

- Vitest tests for `handEyeBoard.ts`.

### `model/handEyeBoardOrigin.ts`

- Offset check ("Brettursprung anfahren" (drive to board origin)): flange target for driving to the board origin after a calibration.
- Entry points: `boardOriginFlangeTarget`, `flangeToToolPose`; constants `FR3_TOOL_OFFSET_M`, `BOARD_ORIGIN_STANDOFF_M`, `BOARD_ORIGIN_APPROACH_M`.

### `model/handEyeBoardOrigin.test.ts`

- Vitest tests for `handEyeBoardOrigin.ts`.

### `model/handEyeOrder.ts`

- Drive order of hand-eye views: groups take turns; warm-up minutes setting and clock formatting.
- Entry points: `interleaveByGroup`, `nextPendulumIndex`, `parseWarmupMinutes`, `loadWarmupMinutes`, `saveWarmupMinutes`, `formatClock`.

### `model/handEyeOrder.test.ts`

- Vitest tests for `handEyeOrder.ts`.

### `model/jointSettling.ts`

- Decides whether the arm has settled before a sample: joint spread and mean of recent joint readings.
- Entry points: `readsSettled`, `jointSpread`, `meanJoints`, `jointsMoved`; constant `JOINT_SETTLE_TOLERANCE_RAD`.

### `model/jointSettling.test.ts`

- Vitest tests for `jointSettling.ts`.

### `model/layer1Import.ts`

- Applies Layer 1 detections to the viewport: matches detected robots to catalog entries and plans the twin placement.
- Entry points: `planLayer1Import`, `describeLayer1Import`, `DetectedRobot`, `Layer1Import`.

### `model/layer1Import.test.ts`

- Vitest tests for `layer1Import.ts`.

### `model/layer2.ts`

- Robot base and its digital twin in the viewport: pose conversions between robot base and origin frames, heading, equality.
- Entry points: `RobotBase`, `robotBaseFrom`, `robotBaseFromOrigin`, `robotOriginFromBase`, `robotBaseHeading`, `robotOriginsEqual`.

### `model/layer2.test.ts`

- Vitest tests for `layer2.ts`.

### `model/layer2Gate.ts`

- Layer 2 (full run as test drive) only starts when Layer 1 has placed the cell.
- Entry points: `layer2Gate`, `Layer2Gate`.

### `model/layer2Gate.test.ts`

- Vitest tests for `layer2Gate.ts`.

### `model/layer2Photo.ts`

- The centred photo a Layer 2 tag is measured from: camera-to-tag distance and tilt.
- Entry points: `CentredPhoto`, `photoDistanceM`, `photoTiltDeg`, `describeLayer2Photo`.

### `model/layer2Photo.test.ts`

- Vitest tests for `layer2Photo.ts`.

### `model/measureBoardCall.ts`

- Request/response wrapper for measuring the chessboard on the Pi; a board not fully in view costs extra time per picture.
- Entry points: `measureBoard`, `MeasureBoardOutcome`.

### `model/measureTagsCall.ts`

- Request/response wrapper for measuring tags with the hand camera for a short window (longer than a few frames, shorter than a robot move).
- Entry points: `measureTags`, `MeasureOutcome`.

### `model/modelEdges.ts`

- Edges of a module model and what clicking one means (corner or edge pick).
- Entry points: `pickOnEdge`, `closestPointOnSegment`, `EdgePick`, `EdgePickKind`; constants `CORNER_FRACTION`, `CORNER_MAX_M`.

### `model/modelEdges.test.ts`

- Vitest tests for `modelEdges.ts`.

### `model/modelFit.ts`

- Which file type a module model is (`modelFormat`) and where its meshes live (`urdfPackageRoot`).
- Entry points: `modelFormat`, `urdfPackageRoot`, `ModelFormat`.

### `model/modelFit.test.ts`

- Vitest tests for `modelFit.ts`.

### `model/modelLoadQueue.ts`

- Serialises model loads (large STL/Collada files) and caches promises per model.
- Entry points: `createSerialQueue`, `createPromiseCache`, `enqueueModelLoad`.

### `model/modelLoadQueue.test.ts`

- Vitest tests for `modelLoadQueue.ts`.

### `model/modelPlanes.ts`

- Dominant flat faces of a module model with their long edges and corners; used for tag placement.
- Entry points: `findPlanes`, `ModelPlane`, `PlaneOptions`, `DEFAULT_PLANE_OPTIONS`, `MeshInput`. Largest file in the folder (about 1190 lines).

### `model/modelPlanes.test.ts`

- Vitest tests for `modelPlanes.ts`.

### `model/moduleTracking.ts`

- Compares ceiling-camera measurements cycle after cycle with the viewport and reports a module (or tag robot) as moved after a sustained disagreement. Compares tag position in the floor plane and heading only.
- Entry points: `trackObservation`, `trackingReferences`, `TrackingState`, `DEFAULT_TRACKING_THRESHOLDS`, `movedEntries`.
- No importer outside its test file. See Flags.

### `model/moduleTracking.test.ts`

- Vitest tests for `moduleTracking.ts`.

### `model/moveTracker.ts`

- Applies confirmed module moves to the scene; untouched modules stay as they are.
- Entry point: `applyModuleMoves` (used by `features/viewport/model/sceneState.ts`).

### `model/pickPlace.ts`

- Pick and place geometry and planning: taught pick and transfer points, table-top and tag-based place targets, carry clearance, return plan, placed-object serialisation.
- Entry points: `planPickPlace`, `planReturn`, `TaughtPick`, `PickPlacePlan`, `PickPlaceStep`, `measurePlaceFromCorner`, `placeBlocker`, `footprintExit`, `carryClearance`, `placedObjectFrom`.
- Constants: `DEFAULT_LIFT_M`, `CUBE_SIZE_M`, `CARRY_CLEARANCE_M`, `MEASURED_PLACE_TARGET`.

### `model/pickPlace.test.ts`

- Vitest tests for `pickPlace.ts`.

### `model/pickPlaceRoute.ts`

- Turns a pick-and-place plan into a routed step list with carry checks; step labels and progress keys; storing picks as strings.
- Entry points: `routePickPlace`, `PickPlaceRoute`, `RoutedStep`, `CarryCheck`, `PICK_PLACE_STEP_LABELS`, `pickPlaceStepKey`, `storedPickToString`, `storedPickFromString`.

### `model/pickPlaceRoute.test.ts`, `model/pickPlaceFrame.test.ts`, `model/pickPlaceReturn.test.ts`, `model/pickPlaceTag.test.ts`

- Vitest tests for route, frame, return and tag-based place logic.

### `model/planeWorker.ts`

- Web Worker entry: runs `findPlanes` off the main thread (request/response types `PlaneRequest`, `PlaneResponse`).
- Worker instantiation and consumers were not traced (see Flags).

### `model/robotDrive.ts`

- Seam between a run that moves the flange (cube move, hand-eye, tag measurement) and the robot. A driver reports "reached" only after the robot confirms.
- Entry points: `RobotDriver`, `createRobotDriver`, `driveJoints`, `planMove`, `hasArrived`, `DriveTarget`, `DriveOutcome`, `RobotDriveDeps`, `JointsTimeoutError`; constant `DRIVE_TIMEOUT_MS`.

### `model/robotDriveReset.test.ts`, `model/robotDrive.test.ts`

- Vitest tests for `robotDrive.ts`.

### `model/robotHold.ts`

- Take Control and Sync for a driving component; shared holds across components.
- Entry points: `holdRobot`, `RobotHold`, `RobotHoldApi`, `createSharedRobotHolds`, `sharedRobotHolds`.

### `model/robotHold.test.ts`

- Vitest tests for `robotHold.ts`.

### `model/robotOperation.ts`

- Whether the robot is currently driving on its own (Layer 2, hand-eye, etc.); begin/end markers and a hook.
- Entry points: `beginRobotOperation`, `endRobotOperation`, `useRobotOperation`, `RobotOperation`.

### `model/robotTagRun.ts`

- Measures the robot's own tags through its joint angles (robot FK), combining several views.
- Entry points: `measureRobotTags`, `robotTagJob`, `combineRobotTagViews`, `RobotTagJob`, `RobotTagMeasured`, `RobotTagRunOptions`; constants `ROBOT_TAG_STANDOFF_M`, `ROBOT_TAG_MIN_VIEWS`, `ROBOT_MODULE_TO_BASE`.

### `model/routeProgress.ts`

- Status of each viewpoint of the route; where a measured module stands.
- Entry points: `RouteProgress`, `RouteStatus`, `measuredModule`, `describeModulePosition`.

### `model/tagEdgeDistance.ts`

- Distance from a tag centre to each reference edge, for checking a placed tag.
- Entry points: `tagEdgeDistances`, `tagEdgeDistanceMm`, `describeEdgeDistances`, `TagEdgeDistance`.

### `model/tagGeometry.ts`

- Quaternion and pose helpers (xyzw): multiply, rotate, normalise, compose, invert, RPY conversions, tag-to-module and tag-to-world poses.
- Entry points: `multiplyQuaternions`, `rotateVector`, `composePoses`, `invertPose`, `tagWorldPose`, `moduleWorldPose`, `tagToModuleOf`, `rpyDegFromQuaternion`, `Pose`, `IDENTITY_POSE`.

### `model/tagMapSync.ts`

- World tags of the Pi tag map (`config/tagmap.json`): parse, build JSON, compare with the catalog.
- Entry points: `parseTagMap`, `buildTagMapJson`, `compareWorldTags`, `catalogWorldEntries`, `WorldTagDifference`.

### `model/tagMapSync.test.ts`

- Vitest tests for `tagMapSync.ts`.

### `model/tagMeasurementState.ts`

- Which tags still need a hand-camera measurement; applies a measurement and invalidates changed ones.
- Entry points: `pendingTags`, `measurableTags`, `selectTagReferenceTags`, `applyTagMeasurement`, `invalidateChangedMeasurements`, `describeTagMeasurement`, `PendingTag`.

### `model/tagMeasurementState.test.ts`

- Vitest tests for `tagMeasurementState.ts`.

### `model/tagMethodComparison.ts`

- Compares a robot tag measured through joint angles with the same tag measured through reference edges.
- Entry points: `compareRobotTagWithEdges`, `TagMethodComparison`; constants `EDGE_CHECK_MAX_MM`, `EDGE_CHECK_MAX_DEG`.

### `model/tagMethodComparison.test.ts`

- Vitest tests for `tagMethodComparison.ts`.

### `model/tagPlacement.ts`

- Where a tag sits on a module, from measured corners or edges: foot point, position, axes, pose from placement.
- Entry points: `placementFromCorner`, `placementFromEdges`, `tagPoseFromPlacement`, `placementMatches`, `turnPlacement`, `EdgeLine`.

### `model/tagPlacement.test.ts`

- Vitest tests for `tagPlacement.ts`.

### `model/tagPositions.ts`

- One line per tag in the cell's world frame, for the list used to measure the real cell.
- Entry points: `listTagPositions`, `tagPositionsText`, `TagPositionRow`.

### `model/tagPositions.test.ts`

- Vitest tests for `tagPositions.ts`.

### `model/tagReferenceCall.ts`

- Hand Pi tag measurement against reference edges: request/result/preview/view JSON schemas and parsers, and the call wrapper. Nothing here moves the robot.
- Entry points: `tagReferenceRequestJson`, `parseTagReferenceResult`, `TagReferenceRequest`, `TagReferenceResult`, `TagReferenceCorrection`, `TagReferenceView`, `TagReferencePreviewStatus`.
- Pi-side counterpart named in the header: `src/vision_server/tagreference.py` (cell repo).

### `model/tagReferenceCall.test.ts`

- Vitest tests for `tagReferenceCall.ts`.

### `model/tagReferenceFusion.ts`

- Fuses several views of one tag against its reference edges into one correction; outliers rejected, spread warnings.
- Entry points: `fuseReferenceViews`, `FusionView`, `FusedReference`; `FUSION_*` thresholds.

### `model/tagReferenceFusion.test.ts`

- Vitest tests for `tagReferenceFusion.ts`.

### `model/tagReferenceLiveView.ts`

- Turns the embedded live stream of the edge search on or off.
- Entry point: `enterLiveView`.

### `model/tagReferencePreview.ts`

- Controller for the Pi's edge preview, including the renewal timer (`PREVIEW_RENEW_MS`).
- Entry points: `createPreviewController`, `startPreviewRenewal`, `PreviewController`.

### `model/tagReferencePreview.test.ts`

- Vitest tests for `tagReferencePreview.ts`.

### `model/tagReferenceProgress.ts`

- Phase bar for one tag: labels and marks per step; module tags use all phases, robot tags use a subset.
- Entry points: `phaseMarks`, `phaseLabel`, `PHASE_LABELS`, `MODULE_PHASES`, `ROBOT_PHASES`.

### `model/tagReferenceProgress.test.ts`

- Vitest tests for `tagReferenceProgress.ts`.

### `model/tagReferenceRun.ts`

- Measures tag positions with the hand camera, one tag after another: centre, plan a view, drive to each reachable view, measure, fuse. Largest run module (about 700 lines).
- Entry points: `measureTagReferences`, `planViewMove`, `flangeForView`, `driveClear`, `TagReferenceRunOptions`, `TagReferenceJobResult`, `TAG_REFERENCE_VIEWS`, `DEFAULT_CENTRING_STANDOFF_M`.
- Process steps are described in the file header. Mentions `planApproach` for first-view joints.

### `model/tagReferenceRun.test.ts`

- Vitest tests for `tagReferenceRun.ts`.

### `model/tagReferenceSettings.ts`

- Settings of the tag reference window (overlay layers etc.), kept outside the component.
- Entry points: `useTagReferenceSettings`, `tagReferenceSettings`, `parseTagReferenceSettings`, `overlayRequest`, `DEFAULT_TAG_REFERENCE_SETTINGS`, `OverlayLayers`.

### `model/tagReferenceSettings.test.ts`

- Vitest tests for `tagReferenceSettings.ts`.

### `model/tagServo.ts`

- Layer 2 part two: the hand camera measures the tag it was sent to; servo step and base correction computed from the measurement.
- Entry points: `servoStep`, `baseCorrection`, `measureTagsRequestJson`, `parseMeasureTagsResult`, `TagMeasurement`, `ServoStep`, `BaseCorrection`; constants `CENTRED_LATERAL_M`, `CENTRED_DISTANCE_M`, `MAX_STEP_M`.

### `model/tagServo.test.ts`

- Vitest tests for `tagServo.ts`.

### `model/tagSightings.ts`

- Per-tag history of what the cell watch saw over its last checks; a tag is "recently seen" within a window.
- Entry points: `addSighting`, `recentlySeenTagIds`, `tagSighting`, `SightingHistory`; constants `SIGHTING_WINDOW`, `MISSING_CHECKS`.

### `model/tagSightings.test.ts`

- Vitest tests for `tagSightings.ts`.

### `model/tagViews.ts`

- Measures one tag from several sides with the hand camera and fuses the views; shared by robot tags (`robotTagRun.ts`) and the Layer 2 run.
- Entry points: `tagViews`, `fuseTagViews`, `viewsAround`, `meanOrientation`, `tagInBaseFrom` (see `tagServo.ts`), `SeenTag`, `FusedTag`; `TAG_VIEW_*` constants.

### `model/tagViews.test.ts`

- Vitest tests for `tagViews.ts`.

### `model/useBoardCalibrationRun.ts`

- React hook running the board calibration for the component; exposes checks, robot problem text, and the run state.
- Entry points: `useBoardCalibrationRun`, `BoardCalibrationRun`; storage key `wsc.cameraCalibration.camera1Positions` for taught poses.

### `model/useCameraOverlay.ts`

- Hook that sets a camera's overlay ("apriltag" or "off") while a window or run needs it.
- Entry point: `useCameraOverlay`.

### `model/useCaseRecord.ts`

- Builds the use-case record sent with a photo: optional fields, only known values are included.
- Entry points: `useCaseRecord`, `UseCaseRecordInput`, `USE_CASE_RECORD_SCHEMA`.

### `model/useCaseRecord.test.ts`

- Vitest tests for `useCaseRecord.ts`.

### `model/useCellRobot.ts`

- The cell's robot as the vision features need it: catalog entry and robot model.
- Entry points: `useCellRobot`, `useCatalogRobot`, `CellRobot`.

### `model/usePickPlaceRun.ts`

- React hook for the pick-and-place run: teach pick, select table, plan route, run place or return, status (`PickPlaceStatus`).
- Entry points: `usePickPlaceRun`, `PickPlaceRun`, `RunKind`, `PlaceSource`, `DROP_HEIGHT_RANGE_MM`. Largest hook (about 1180 lines).

### `model/usePlaceRobots.ts`

- Places robots found by a scan at the positions their tags say, using the connected real robot if present.
- Entry point: `usePlaceRobots`.

### `model/useRobotHold.ts`

- Hook holding Take Control and Sync for as long as the calling component drives the robot.
- Entry points: `useRobotHold`, `RobotHoldState`, `RobotHoldStatus`.

### `model/useRobotMotionWatch.ts`

- Hook that watches the synced robots' joints and flags when an arm moves (joint change threshold in radians).
- Entry point: `useRobotMotionWatch`.

### `model/useTagReferenceLive.ts`

- Hooks subscribing to one node of a Pi while the edge preview is active (live values, image URL).
- Entry points: `useTagReferencePreviewStatus`, `useTagReferenceImageUrl`.

### `model/vec3.ts`

- Small helpers for `Vec3` tuples (add, subtract, scale, dot, cross, length, distance, normalise); header notes three.js is not used here.
- Entry points: `add`, `subtract`, `scale`, `dot`, `cross`, `length`, `distance`, `normalise`.

### `model/vec3.test.ts`

- Vitest tests for `vec3.ts`.

### `model/worldTagCheck.ts`

- World tags as an error measure for the camera: compares observed world tags with catalog poses, rates deviation, pairs tags.
- Entry points: `checkWorldTags`, `WorldTagCheck`, `WorldTagDeviation`, `rateDeviation`, `pairWorldTags`, `describeWorldTagCheck`; thresholds `WORLD_TAG_WARN_M`, `WORLD_TAG_BAD_M`.

### `model/worldTagCheck.test.ts`

- Vitest tests for `worldTagCheck.ts`.



Scope: `frontend/src/features/cell-modules/components/` (28 files, grouped by feature). Model logic lives in `frontend/src/features/cell-modules/model/` and is covered in another section. Entity and shared imports are named where a component uses them. Related overview: `docs/vision-workflows.md` (Layer 1, cell watch, hand-eye, and tag reference workflows).

#### Layer 1 and cell watch

### `components/AutolocateLayer1Window.tsx`
- Floating window for the ceiling camera (Pi 1) scan: live image with AprilTags marked, and the list of detected modules and robots. "Module laden" places them in the viewport.
- Entry point: default export `AutolocateLayer1Window`. Props include a scan-request counter, `followTagIds` (follow the cell watch and move only the named modules), `headless`, and `onProposal`.
- Uses `model/catalogStore` (`useModuleCatalog`), `model/layer1Import` (`planLayer1Import`), `model/usePlaceRobots`, `model/useCameraOverlay`, and `opcua-server/model/useVisionJob` and `useCameraHealth`. Types: `model/cellModules`, `opcua-server/model/visionResult`, `viewport/model/sceneState`.
- Opened by `opcua-server/components/AutolocateActions.tsx`. Details: `docs/vision-workflows.md`.

### `components/CellChangeAlert.tsx`
- Alert shown above the viewport when the cell watch reports a change that has held longer than the set time. Removed modules are red, new ones green, moved ones red at the old and green at the new place. One answer takes all changes over or ignores them.
- Entry point: default export `CellChangeAlert`, rendered through `react-dom` `createPortal`. Label table `KINDS` maps each change kind to its text.
- Uses `model/cellWatch` (`cellChangeModuleName`, `cellChangeMoveDetail`, type `CellChange`) and type `CellModuleDefinition` from `config/moduleCatalog`.
- Opened by `opcua-server/components/AutolocateActions.tsx`.

### `components/TagPositionsWindow.tsx`
- Floating window listing where every tag of the cell lies, measured from the world origin. The "Kopieren" button copies the list as a table for a spreadsheet.
- Entry point: default export `TagPositionsWindow`. Helper `signedMm` formats differences.
- Uses `model/catalogStore` (`useModuleCatalog`), `model/tagPositions` (`listTagPositions`, `tagPositionsText`), and `shared/format` (`roundedMm`).
- Opened by `opcua-server/components/AutolocateActions.tsx`.

### `components/WatchTimingFields.tsx`
- Two fields in Settings for the cell watch check interval and the time a change must hold. Empty fields show the defaults greyed out.
- Entry point: default export `WatchTimingFields`, built from `shared/ui/SecondsField`.
- Uses `model/autolocateUi` (`autolocateUi`, `useAutolocateUi`, `DEFAULT_WATCH_INTERVAL_S`, `DEFAULT_WATCH_CHANGE_S`).
- Rendered by `viewport/components/ViewportHeaderControls.tsx`.

#### Layer 2 approach (robot drive)

### `components/ApproachPlanPanel.tsx`
- Layer 2 panel: plans a pose in front of each module tag and each world tag, draws the route in the viewport, and drives it stop by stop, then back to the start. At each stop the hand camera measures the tag and the robot centres on it. At the anchor world tag the robot base is corrected. A `compact` prop gives the reduced variant in the main panel.
- Entry point: default export `ApproachPlanPanel`. Constant `AT_HOME_RAD` sets the tolerance for the home pose.
- Uses `entities/robot/model/maxSpeed` (`getMaxSpeed`) and `model/` files `controllerRobotDrive`, `robotDrive`, `robotOperation`, `cellClearance`, `centreOnTag`, `layer2`, `layer2Gate`, `layer2Photo`, `measureTagsCall`, `routeProgress`, `tagServo`, `tagGeometry`, `cellModules`, `useCellRobot`, `useCameraOverlay`, `useRobotHold`. Hooks from `opcua-server` (`useOpcuaServer`, `useHandEyeCalibration`) and `robot-control` (`useRobotControl`).
- Children: `RouteList`, `Layer2CameraWindow`. Imported by `opcua-server/components/AutolocateActions.tsx`.

### `components/RouteList.tsx`
- List of the viewpoints in the order the robot drives them, with status and the reason a stop is skipped or unreachable.
- Entry point: default export `RouteList`. Inputs: `ApproachStop[]` (`model/approachPlan`) and `RouteProgress` (`model/routeProgress`).
- Rendered by `ApproachPlanPanel.tsx`.

### `components/Layer2CameraWindow.tsx`
- Small floating window with only the hand camera, open while the Level 2 positioning drives. It closes by itself when the run ends.
- Entry point: default export `Layer2CameraWindow`, built on `CameraView` and `CameraHealthBadge` from `opcua-server/components`.
- Uses `opcua-server/model/useCameraHealth`, `opcua-server/config/piServers`.
- Rendered by `ApproachPlanPanel.tsx`.

### `components/Layer2GhostRobot.tsx`
- See-through copy of the robot twin in the pose its next target will take, shown before the robot moves.
- Entry points: default export `Layer2GhostRobot`, and `preloadGhostRobot(urdfUrl)` which parses the URDF once per page ahead of time. Parsed templates are cached and cloned.
- Uses `entities/robot/model/types` (`Robot`), `robot-control/model/robotModels` (`resolveRobotMountRotation`, `RobotOrigin`), and `viewport/components/URDFLoader`.
- Used by `HandEyeCalibrationWindow.tsx` and `viewport/components/Viewport.tsx`.

#### Hand-eye and board calibration

### `components/HandEyeCalibrationWindow.tsx`
- Floating window for hand-eye calibration of the hand camera (Pi 2). Shows the live image, the calibration results (residuals, leave-one-out check, rejected samples, intrinsics warnings), and mounts `HandEyeAutoPanel`.
- Entry points: default export `HandEyeCalibrationWindow`; exported helpers `intrinsicsTrusted(source)` and `intrinsicsWarning(source)`. Private views `ResultView` and `FullResultView` show the results.
- Uses `model/handEye` (types `FullCalibrationResult`, `HandEyeResult`), `model/tagGeometry` (`rpyDegFromQuaternion`), `model/approachPlan` (`lookDownHomeAngles`), `model/useCellRobot`, `opcua-server/model/useHandEyeCalibration`, `opcua-server/model/useCameraHealth`, and `preloadGhostRobot` from `Layer2GhostRobot.tsx`.
- Opened from `viewport/components/ViewportHeaderControls.tsx`.

### `components/HandEyeAutoPanel.tsx`
- Panel that drives the robot through the calibration views round the chessboard by itself, with a warm-up option, board-origin check, and per-pose status. It captures the raw data on the Pi and then closes the calibration session.
- Entry point: default export `HandEyeAutoPanel`. Module constants set timing (`SETTLE_MS`, `CAPTURE_TIMEOUT_MS`) and aim thresholds (`AIM_MAX_RMS_M`, `AIM_MAX_RMS_DEG`).
- Uses `model/handEye`, `model/handEyeAutoPoses` (`generateHandEyePoses`, `selectHandEyePoses`), `model/handEyeOrder`, `model/handEyeBoardOrigin`, `model/boardCentring`, `model/measureBoardCall`, `model/jointSettling`, `model/robotDrive` (`createRobotDriver`, `driveJoints`, `planMove`), `model/cellClearance`, `model/controllerRobotDrive`, `model/robotOperation`, `model/tagGeometry`. Entity type `entities/robot/model/types` (`Robot`). Hooks `opcua-server/model/useHandEyeCalibration` and `opcua-server/context/OpcuaServerContext`.
- Rendered by `HandEyeCalibrationWindow.tsx`.

### `components/HandEyeCalibrationWindow.test.ts`
- Vitest unit tests for `intrinsicsTrusted` and `intrinsicsWarning` from `HandEyeCalibrationWindow.tsx`.
- Test file only; not part of the runtime.

### `components/BoardCalibrationRobot.tsx`
- Three panels for the robot part of the camera 1 board calibration: `BoardRobotSetup` (choose the robot holding the board, page through or teach saved positions, nothing is driven), `BoardRobotStatus` (live run status, checks, gripper and drive controls), and `BoardRobotAfterRun` (result after the run).
- Entry points: named exports `BoardRobotSetup`, `BoardRobotStatus`, `BoardRobotAfterRun`, each taking a `BoardCalibrationRun`. Private helpers `GripperButton`, `Checks`, `formatSavedAt`.
- Uses `model/boardCalibrationPoses` (`MIN_CALIBRATION_POSITIONS`), `model/boardCalibrationRunner` (`targetPictures`), and type `BoardCalibrationRun` from `model/useBoardCalibrationRun`.
- Used by `opcua-server/components/CameraCalibrationModal.tsx`.

#### Tag reference

### `components/TagReferenceWindow.tsx`
- Floating window for the tag reference workflow, with the hand camera live view beside the list. It holds the on/off switches for the run options (step by step, measure all, robot-edge cross-check, saving the measurement log, debug image).
- Entry point: default export `TagReferenceWindow`. Constant `SWITCHES` lists the switches; private `SwitchRow` renders one.
- Uses `model/tagReferenceSettings` (`useTagReferenceSettings`, `tagReferenceSettings`), `model/tagReferenceLiveView` (`enterLiveView`), `opcua-server/components/CameraView`, `opcua-server/context/OpcuaServerContext`, `opcua-server/config/piServers`, and `shared/ui` (`FloatingWindow`, `DisclosureSection`, `Toggle`).
- Renders `TagReferencePanel`. Opened from `viewport/components/ViewportHeaderControls.tsx`.

### `components/TagReferencePanel.tsx`
- Main tag reference panel (about 1100 lines). It measures the position of each pending tag of the modules in the cell and of the robot's own tags with the hand camera, runs and previews the jobs, and shows one row per tag with its result or the reason it stays pending.
- Entry point: default export `TagReferencePanel({ sceneState })`. File-level helpers `describeMeasured`, `describeRobotMeasured`, `describeEdgeDistancesOf`, `describeResult`, `describeRobotResult`, `describeCheck` build the row text.
- Uses `model/tagReferenceCall`, `model/tagReferenceRun`, `model/robotTagRun`, `model/tagReferenceProgress` (`MODULE_PHASES`, `ROBOT_PHASES`), `model/tagReferencePreview`, `model/tagReferenceSettings`, `model/useTagReferenceLive`, `model/tagMethodComparison`, `model/tagEdgeDistance`, `model/tagGeometry`, `model/tagMeasurementState`, `model/catalogStore`, `model/measureTagsCall`, `model/layer2Gate`, `model/useCellRobot`, `model/useRobotHold`, and `entities/robot/model/maxSpeed` (`getMaxSpeed`).
- Renders `TagReferenceLiveParts`. Rendered by `TagReferenceWindow.tsx`.

### `components/TagReferenceLiveParts.tsx`
- Presentation parts of the tag reference run: `PhaseBar` (steps of one tag's job), `LiveValues` (what the Pi's edge search sees now), `LastMeasurement` (edges, correction, warnings, debug image of the last measurement), and a private `DebugImage` that reloads per measurement.
- Uses `model/tagReferenceRun` (type `TagReferencePhase`) and `model/tagReferenceProgress` (`phaseLabel`, `phaseMarks`, `PHASE_LABELS`).
- Rendered by `TagReferencePanel.tsx`.

#### Pick and place

### `components/PickPlacePanel.tsx`
- Pick and place panel in the main panel under Layer 2. Shows the table corners, the drop-height slider, the step list of the planned route, and the Pi's measurement of the placed cube (`MeasurementLine`).
- Entry point: default export `PickPlacePanel({ sceneState })`. Helpers `StepList`, `DropHeightSlider`, `cornerLabel`.
- Uses `model/pickPlace` (type `TableCorner`, `DEFAULT_DROP_HEIGHT_M`), `model/pickPlaceRoute` (`PICK_PLACE_STEP_LABELS`, `pickPlaceStepKey`), `model/usePickPlaceRun` (`DROP_HEIGHT_RANGE_MM`, types `PlaceSource`, `RunKind`), and `opcua-server/model/useCaseMeasurement` (`formatUseCaseMeasurement`).
- Imported by `opcua-server/components/AutolocateActions.tsx`.

#### Settings

### `components/RobotSpeedField.tsx`
- Settings field for the robot speed (`go_to` `max_speed`): a slider with a reset to the default. The value is kept per browser.
- Entry point: default export `RobotSpeedField`. Module constant `TITLE` holds the tooltip text.
- Uses `entities/robot/model/maxSpeed` (`DEFAULT_MAX_SPEED`, `MAX_SPEED_RANGE`, `setMaxSpeed`, `useMaxSpeed`) and `shared/format` (`comma`).
- Rendered by `viewport/components/ViewportHeaderControls.tsx`.

#### 3D model viewport markers

### `components/CellModuleMarkers.tsx`
- Draws each detected cell module in the viewport as a placeholder box with its model (`CellModuleModel`) and a hover label. Coarse (ceiling camera only) and precise (Layer 2 measured) states are marked differently.
- Entry point: default export `CellModuleMarkers({ modules })`. Private `CellModuleMarker` per module, wrapped in `ModelErrorBoundary` and `Suspense`.
- Uses `config/moduleCatalog` (`boundsCentre`, `boundsSize`) and `model/cellModules` (`PlacedCellModule`, `TAG_SPREAD_WARNING_M`).
- Rendered by `viewport/components/Viewport.tsx`.

### `components/FlangeTrajectoryMarker.tsx`
- Draws a planned robot move before it runs: dots for points, paths where known, arrows for order, dashed helper lines, and unreachable targets in amber. Drawn in the robot base frame, placed like the twin.
- Entry point: default export `FlangeTrajectoryMarker`. Input type `FlangeTrajectory` from `model/flangeTrajectory`.
- Uses `entities/robot/model/types` (`Robot`) and `robot-control/model/robotModels` (`resolveRobotMountRotation`).
- Rendered by `viewport/components/Viewport.tsx`.

### `components/HandCameraMarker.tsx`
- Draws the hand camera at the pose given by the hand-eye calibration: a small box for the body and a view cone along the camera's +Z. It follows the twin's flange every frame.
- Entry point: default export `HandCameraMarker({ sceneState })`. Pose source is `opcua-server/model/useHandEyeCalibration` (`HandEyeStatus.current` on Pi 2).
- Uses `model/useCellRobot`, `robot-control/context/RobotControlContext`, `robot-control/model/robotModels`, `opcua-server/config/piServers`.
- Rendered by `viewport/components/Viewport.tsx`.

### `components/Layer2ProposalMarkers.tsx`
- Shows the result of Layer 2 before it is taken over: the measured modules in their scene position (red) and the positions Layer 2 found (green) as see-through boxes, plus the robot base as a disc with a facing arrow. Only drawings; nothing in the scene changes.
- Entry point: default export `Layer2ProposalMarkers`. Private `GhostBox`, `GhostModule`, `GhostBase`, and helpers `shiftMm` and `headingDiffDeg`.
- Uses `model/cellModules` (`PlacedCellModule`), `model/layer2` (`robotBaseHeading`), `config/moduleCatalog` (`boundsCentre`, `boundsSize`), `viewport/model/sceneState` (type `Layer2Proposal`), `robot-control/model/robotModels`.
- Rendered by `viewport/components/Viewport.tsx`.

### `components/WorldTagMarkers.tsx`
- Draws the world tags as fixed points on the floor, each with a lock icon above it. An eye icon (open or struck through) shows whether the ceiling camera sees the tag in the last scan. Exports the printed tag plane and the distances between tag pairs with the last scan's error.
- Entry points: default export `WorldTagMarkers`; named exports `AprilTagPlane({ tagId, sizeM })`, `WorldTagDistances`, and type `WorldTagSighting`.
- Uses `model/cellModules` (`PlacedWorldTag`), `model/worldTagCheck`, `config/moduleCatalog` (`Vec3`), `tag-tool/components/sceneParts` (`tagTexture`), and `tag-tool/model/tagFrame` (`printedTagSizeM`).
- Also used by `ModulePreview.tsx` (`AprilTagPlane`) and `viewport/components/Viewport.tsx`.

#### 3D model loading and editing

### `components/CellModuleModel.tsx`
- Loads a module's 3D model (GLTF, Collada, STL, or URDF) through the three.js loaders, places it in the module frame, and reports its bounds. A colour option draws the model see-through (Layer 2 comparison).
- Entry points: default export `CellModuleModel({ model, onPlaced, tint })`; exported `measureModelBounds(model)`, and types `ModuleObjectFactory`. Loads are cached per URL and queued through `model/modelLoadQueue`.
- Uses `config/moduleCatalog` (types `ModuleBounds`, `ModuleModel`), `model/modelFit` (`modelFormat`, `urdfPackageRoot`), `model/modelLoadQueue` (`createPromiseCache`, `enqueueModelLoad`), `shared/math/angles` (`DEG`), and `viewport/components/URDFLoader`.
- Rendered by `CellModuleMarkers.tsx` and `ModulePreview.tsx`.

### `components/ModelErrorBoundary.tsx`
- React error boundary around a module's model. If the file cannot be loaded, it shows a fallback (the plain module box) and logs the error. A `key` of the model URL gives a new file a new attempt.
- Entry point: default class export `ModelErrorBoundary`.
- Used by `CellModuleMarkers.tsx` and `ModulePreview.tsx`.

### `components/ModelEdges.tsx`
- Computes and draws the edges of a model mesh so the operator can pick one. Edges are computed per mesh on first hover and cached. Meshes above `MAX_TRIANGLES` are skipped.
- Entry points: `geometryEdges(geometry)` returning `GeometryEdges`; components `SurfaceLines`, `MeshEdgeLines`, and `HighlightedEdge`.
- Uses `config/moduleCatalog` (type `Vec3`) and the three.js line helpers (`LineSegments2`, `LineMaterial`).
- Used by `ModelPlanes.tsx` (`SurfaceLines`), and `ModulePreview.tsx` (`geometryEdges`, `HighlightedEdge`, `MeshEdgeLines`).

### `components/ModelPlanes.tsx`
- Shows the dominant planar faces of a module's model in the preview, computed in a worker, and turns clicks into a face, corner, or edge for measuring a tag from.
- Entry points: hook `useModelPlanes(object, frame, enabled, mergeGapM)`; components `PlaneOverlay` (face, corner, and edge picking) and `PlacementMeasure` (ruler-style measurement of a placed tag); helpers `planeColor`, `snapToSegments`, `sameLine`, `collectMeshes`; types `PlanesState`, `PlaneToolSelection`.
- Uses `config/moduleCatalog` (types `TagPlacement`, `Vec3`), `model/planeWorker` (type `PlaneResponse`), `model/modelEdges`, `model/tagPlacement` (`placementFootPoint`, `placementPosition`, type `EdgeLine`), `model/vec3`, and `model/modelPlanes` (type `ModelPlane`).
- Used by `ModuleCatalogModal.tsx` and `ModulePreview.tsx`.

### `components/ModulePreview.tsx`
- Interactive 3D preview of one catalog module: box, model, tag markers, and the tag-placement tool. Clicking an edge or face of the model can pick a measurement point for a tag.
- Entry points: `ModuleScene` (scene with the module, tags, and optional `PlaneTool`), `ModuleCamera` (default camera framing the module box), and interface `PlaneTool`. Helpers `tagTexture`, `TagMarker`, `edgeUnderCursor`, and `toModuleFrame`.
- Uses `config/moduleCatalog`, `model/modelEdges` (`closestPointOnSegment`, `pickOnEdge`, type `EdgePick`), `model/modelPlanes` (`DEFAULT_PLANE_OPTIONS`), `model/tagPlacement` (`placementMatches`, type `EdgeLine`), `shared/math/angles`. Child components: `CellModuleModel`, `ModelEdges`, `ModelErrorBoundary`, `WorldTagMarkers` (`AprilTagPlane`), and `ModelPlanes` types.
- Used by `ModuleCatalogModal.tsx`.

### `components/ModuleCatalogModal.tsx`
- Full catalog editor opened from the viewport header: list of modules and world tags, a form for each module (bounds, tags, model file), tag placement on the model with turn buttons, and a sync status line for the catalog.
- Entry point: default export `ModuleCatalogModal({ onClose })`. Large internal parts: `ModuleEditor`, `TagEditor`, `TagPlacementPanel`, `WorldTagFields`, `ModuleRow`, `CatalogSyncLine`, plus field helpers (`NumberField`, `Vec3Fields`, `TurnButtons`).
- Uses `config/moduleCatalog` (types `CellModuleDefinition`, `ModuleBounds`, `ModuleTag`, `TagPlacement`, `Vec3`), `model/catalogStore` (`CatalogSyncState`), `model/catalogValidation` (`describeTagSize`, `validateModule`), `model/modelPlanes`, `model/tagPlacement`, `model/tagMeasurementState`, `model/tagGeometry` (`turnRotationDegAboutTagZ`), `robot-control/model/robotModels` (`ROBOT_MODEL_OPTIONS`), `opcua-server/config/robotServers` (`KNOWN_ROBOT_SERVER_URLS`), and the virtual module `cell-module-files`.
- Renders `ModulePreview` (`ModuleScene`, `ModuleCamera`) and `ModelPlanes` (`sameLine`, types). Opened from `viewport/components/ViewportHeaderControls.tsx`.

