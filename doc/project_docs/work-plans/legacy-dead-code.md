# Legacy — dead code, untested code, AprilTag duplicates

> **Superseded (30.10.2026)** by [`legacy-cell.md`](legacy-cell.md) (this repo) and [`legacy-wsc.md`](legacy-wsc.md) (webskillcomposition). Open items from here have been re-checked there and carried over under their old ID. This document is kept as a historical record.

As of: 2026-10-21. Supplement to [`legacy.md`](legacy.md) (demo setups,
placeholders, deployment workarounds). This document is about **code**: what is
no longer called anywhere, what no test touches, and where the AprilTag topic
has two paths for the same thing.

Both repos were examined:

- **Cell** = `ADP_flexible_Roboterzelle` (this repo)
- **WSC** = `webskillcomposition` (university repo, branch `tag-einmessen`)

Method: static search (AST script and `git grep -w`) over sources, tests, tools
and docs; `vulture` was not available. Nothing was deleted or changed. "Certain"
means: the search found no hit apart from the definition (and `__all__`), and no
dynamic access is recognizable. Before each deletion, rerun the named search —
other sessions write in parallel.

**Test run:**

- Cell, `.venv`: `Ran 763 tests … OK` (121 s). Without `.venv` (system Python
  without cv2/asyncua), 30 tests fail due to the environment — not a code error.
- WSC backend: 65 collected, 60 passed, 1 skipped
  (`tests/test_websocket_router.py:85`, needs `WSC2_DEMO_OPCUA_URL`).
- WSC frontend: `npx vitest run` 57 files, 560 tests, all green.

**Correction to `legacy.md`:** D7 (`test_asyncua_discovery.py` aborts during
collect) and D8 (red test in `store.test.ts`) no longer apply — both run green.
Remove them there at the next upkeep; here the existing document was deliberately
not touched.

---

## 1. Dead code

### 1.1 Cell

| # | What | Where | Evidence | Confidence | Risk when deleting |
|---|---|---|---|---|---|
| T1 | **Prototype `src/apriltag/`** (`calibrate_camera.py`, `detect_apriltags.py`, `MANUAL_TEST.md`) | `src/apriltag/` | No import, no test. Superseded by `tagloc.cli.calibrate` / `tagloc.cli.detect` (stated in `MANUAL_TEST.md` itself). Accesses `src/apriltag/calibration.yaml`, which is not tracked. Applies no distortion correction | certain | low. Also update the references in `apriltag-localization.md:460`, `apriltag-reference.md:435`, `legacy.md:116-118` and the origin docstrings in `tagloc/pose.py:3`, `boards.py:3,160`, `calibration.py:8` |
| T2 | `SharedCameraSource` and `iter_frames` | `src/tagloc/frames.py:174`, `:222` | Definition and `__all__` only (`:257`, `:261`) | certain | low; first check in `apriltag-reference.md` whether it is described as a CLI building block |
| T3 | `ImageFolderSource.current_path` | `src/tagloc/frames.py:52` | Definition only | certain | minimal |
| T4 | `poses_to_json` | `src/tagloc/cli/_common.py:229` | Definition only; docstring names `cli/transform.py`, which does not use it | certain | minimal |
| T5 | `build_detection_source` (singular) | `src/vision_server/detection/__init__.py:51` | Definition and `__all__` only (`:70`); `build_detection_sources` is what is used | certain | low |
| T6 | `Program.get_current_state` | `src/ua_program/program.py:162` | Definition only | certain | low; possibly taken over from the supervisor's template, then compare there |
| T7 | `world_tag_for_robot` | `src/tagloc/localize.py:513` | In `src/` only the definition and `__all__` (`:641`); only caller `tests/test_welttags.py:370,381` | certain (never called in production) | medium — the test would be dropped too; possibly intended as a replacement for Layer 2 |
| T8 | `list_realsense_profiles.py`, `pi_load_profiler.py` (465 lines) | `src/vision_server/tools/` | No reference in code, tests or docs | likely | low to medium — could be local tools; ask first |
| T9 | Outdated reference in docstring: "OPCUA/server.py" | `src/jobs/calibrate.py:22` | File is still in use (started by `ScriptDetectionSource`), only the reference is outdated | certain | none (cosmetic) |

No commented-out code, no `TODO`/`FIXME`/`HACK` markers, no compatibility
aliases found. **Not examined:** flags and parameters that are never set.

Already in `legacy.md`: D11 (`src/OPCUA/server.py`), C2 (`IsPartial`), C7 (LDS URL),
D4 (`setup.sh`/`.bat`).

### 1.2 WSC — the group's code

| # | What | Where | Evidence | Confidence | Risk |
|---|---|---|---|---|---|
| W1 | **`AutolocateLayer2Window`** | `frontend/src/features/cell-modules/components/AutolocateLayer2Window.tsx:80` | No import, not even lazy. Superseded by `AutolocateActions.tsx` (commit `2b56b4f`, "Layer 2" with `ApproachPlanPanel compact`) | certain | low |
| W2 | **Chain that hangs only on W1:** `Layer2RunPanel.tsx`, `RobotRunConfirm.tsx`, `EndEffectorSpinTest.tsx`, `model/endEffectorSpin.ts` (+ `.test.ts`), `opcua-server/model/useLayer2Run.ts` | `cell-modules/…`, `opcua-server/model/` | Each name is imported only by its predecessor in the chain. The test `endEffectorSpin.test.ts` keeps dead code green | certain | low; `layer2Gate`, `layer2Run`, `robotDrive`, `controllerRobotDrive` stay (needed by `ApproachPlanPanel`, `TagReferencePanel`, `Viewport`) |
| W3 | `sceneState.layer2Target` / `setLayer2Target`, `Layer2TargetMarker` | `viewport/model/sceneState.ts:112-113,158,203-212`, `Viewport.tsx:1775`, `Layer2TargetMarker.tsx` | It is only set in `Layer2RunPanel.tsx:203,207` (dead). The marker therefore never renders anything | certain | medium, depends on W2 |
| W4 | Layer 2 binding in `piServers.ts` (`Layer2Binding`, `StartLayer2Run`, `Layer2Target`) | `opcua-server/config/piServers.ts:115-122,219,284,402-405` | `startNodeId`/`targetNodeId` have no reader without `useLayer2Run`; `statusNodeId` is still read in `PiActivity.tsx:64` | likely | medium — the Pi contract `wsc.vision.layer2.*` is documented |
| W5 | `ModuleCatalogButton.tsx` | `cell-modules/components/` | Definition only; the catalog is opened directly in `ViewportHeaderControls.tsx:11,511` | certain | low |
| W6 | Unused exports: `shiftBounds` (`config/moduleCatalog.ts`), `DISCONNECTED_DRIVER` (`model/robotDrive.ts`) | see column | Definition only | certain | low |
| W7 | `trackModuleMoves` (`model/moveTracker.ts`), `VISION_RECIPES` (`piServers.ts:26`) | see column | Used only by the test (`visionResult.test.ts:240-265` for `VISION_RECIPES`) | likely | low |
| W8 | **Outdated comments** | `AutolocateActions.tsx:64-65` (claims the Layer 1/2 windows would be rendered here and opened via `autolocateUi`; `autolocateUi` only knows `timing` and `tagReferenceOpen` now), `cell-modules/model/layer2.ts:24` (refers to `Layer2RunPanel`) | — | certain | none |
| W9 | **Hard-coded robot addresses** `10.10.38.25`–`.28` plus `127.0.0.1` | `opcua-server/config/robotServers.ts` | Supplement to B1 in `legacy.md`, not mentioned there | certain | — |

No `TODO`/`FIXME` markers, no commented-out code, no dead feature flags (only
environment variable: `VITE_WSC2_WS_URL`). Nothing in the backend that belongs to
the group is unused.

### 1.3 WSC — university code (note only, not ours)

History not checked; look into it before reporting to the supervisors.

- Backend: `runtime/robot_session.py` `require_method_node_id`;
  `runtime/server_session.py` `replace_robots`; `services/runtime_registry.py`
  `surface_jobs` (probably a property); `opcua/asyncua_discovery.py`
  `discover_methods`, `discover_skills`.
- Frontend: `viewport/model/robotIk.ts` `solvePositionIk`; `task/model/editorGraph.ts`
  `describeTaskEditorNode`, `createVariableNodeData`;
  `robot-control/model/robotJointSync.ts` `applyRobotJointMapping`,
  `createActiveRobotJointSyncSession` (only in the test); `tag-tool/model/units.ts`
  `snapToStep`.

---

## 2. Untested

### 2.1 Cell

Thinly covered are mainly entry points and hardware-near sources; the core
calculation (`pose`, `localize`, `calibration`, `tagmap`, `handeye`, `cellwatch`,
`layer2_*`) is covered.

**Modules with no hits in `tests/`:**

- `src/jobs/calibrate.py`
- `src/tagloc/cli/{build_tagmap,calibrate,calibrate_handeye,transform}.py` (`detect`
  only indirectly) — these are the start commands named in the docs
- `src/vision_server/state_machine.py`, `vision_program.py` (only indirectly possible
  via `test_part10_fassade` / `test_layering`)
- `tools/{calibration_client,diagnose_board,handshake_client,stream_viewer}.py`,
  `src/vision_server/tools/{list_realsense_profiles,measure_framerate,pi_load_profiler}.py`

**Only one test reference (thin):** `tagloc/cli/_common.py`, `ua_program/program.py`,
`detection/script_runner.py`, `discovery/lds.py`, `events.py`, `mjpeg_server.py`,
`raw_image.py`, `result_management.py`, `tagreference.py`.

**Large public symbols without a direct test:**

| Symbol | Where | Note |
|---|---|---|
| `Program`, `ProgramStateMachine` | `ua_program/program.py:39`, `:307` (265 + 159 lines) | possibly indirectly via the facade, not verified |
| `VisionStateMachines` | `vision_server/state_machine.py:47` | only via job/runner tests |
| `solve_camera_from_world_tags` | `tagloc/pose.py:242` | runs only via `localize` |
| `calibrate` | `tagloc/lens.py:162` | |
| `merge_samples` | `tagloc/localize.py:364` | indirectly via `apriltag.py:1046` |
| **`PupilAprilTagDetector`**, `apply_detector_parameters` | `tagloc/detector.py:231`, `:85` | the pupil backend and the `auto` fallback are **completely untested** |
| `ImageFolderSource`, `PiCameraSource`, `RealSenseSource` | `tagloc/frames.py` | `test_frame_sources.py` has 35 lines |
| `parse_detector_params`, `load_*_or_exit`, `build_parser`, `add_detection_arguments` | `tagloc/cli/` | CLIs without test |
| `build_config`, `coerce_id`, `reference_warnings`, `asset_config`, `vision_camera_backend`, `mdns_instance_name`, `sanitize_instance_name`, `save_with_backup`, `parse_sample` | `vision_server/` (`__main__.py:15`, `job.py:48`, `tagreference.py:268`, `server.py:326/349/355`, `discovery/mdns.py:52`, `handeye_session.py:140/436`) | mostly only via surrounding tests |

**Cross-reference — "not tested on real hardware"** (per the work plans, so not a
test gap but missing measurement): `handeye-schachbrett-automatik`,
`handeye-ueber-opcua`, `layer2-annaeherung`, `layer2-measuretags`,
`layer2-schraege-ansicht`, `layer2-lauf-hand-pi` (drive command open),
`zellen-ueberwachung` (`cellwatch.py`), `tag-einmessen-bezugskanten`
(`reference_edges.py`, `tagreference.py`, `measure_tag_reference` in `apriltag.py:526`),
`kalibrierung-linsenmodell-je-kamera` (recalibration of LV1 pending),
`livestream-tags-volle-aufloesung` and `stream-overlay-schlank` (not measured on the
Pi). OpenCV 4.x on the Pi has also never been run (`legacy.md` D2).

### 2.2 WSC

**Backend** (`tests/` covers `module_catalog_api`, `method_calls`, `messages`,
`runtime_registry`, `application_service`, `surface_websocket`, `websocket_router`,
`robot_models`, `asyncua_discovery`). Without its own test: `opcua/server_connection.py`
(624 lines), `opcua/discovery.py`, `runtime/action_execution.py` (332 lines),
`geometry/*` — probably only indirectly via `application_service`, not verified.

**Frontend:** there is **not a single component test** (only `*.test.ts`, no
`*.test.tsx`). Without its own test:

- **All hooks in `opcua-server/model/`:** `useVisionJob` (344 lines),
  `useCameraCalibration` (386), `useCellWatch`, `useHandEyeCalibration`,
  `useCameraHealth`, `useCameraFrame`, `useMjpegStreamUrl`,
  `useConnectServerWithFeedback`.
- `opcua-server/config/piServers.ts` (501 lines, only indirectly via
  `visionResult.test.ts`), `OpcuaServerContext.tsx`, `PiActivityContext.tsx`.
- `cell-modules/model`: `controllerRobotDrive.ts`, `measureTagsCall.ts`,
  `useCellRobot.ts`, `useRobotHold.ts`, `flangeTrajectory.ts`, `format.ts`,
  `autolocateUi.ts`, `planeWorker.ts`; `config/moduleCatalog.ts` (454 lines).
- `viewport/model`: `sceneState.ts`, `workspaceSurfaceGeneration.ts` (776 lines),
  `robotIk*.ts`.

**By function:**

| Function | Calculation/model tested | Untested |
|---|---|---|
| Module catalog | `catalogStore`, `catalogSync`, `catalogValidation`, `cellModules`, `modelPlanes`, `modelEdges`, `modelFit` | `ModuleCatalogModal` |
| Tag placement / tag survey | `tagPlacement`, `tagGeometry`, `tagReferenceCall`, `tagReferenceRun`, `tagMeasurementState`, `tagServo`, `tagSightings`, `centreOnTag`, `robotTagRun`, `worldTagCheck`, `routeProgress`, `approachPlan` | `TagReferencePanel`, `TagReferenceWindow`, `ApproachPlanPanel`, `measureTagsCall.ts` |
| Hand camera calibration | `handEye`, `handEyeAutoPoses` | `useHandEyeCalibration`, `HandEyeCalibrationWindow`, `HandEyeAutoPanel` |
| Cell monitoring | `cellWatch`, `cellClearance` | `useCellWatch`, `CellChangeAlert`, `AutolocateActions` |
| Autolocate | `layer1Import` | `AutolocateLayer1Window`, `AutolocateActions`, `useVisionJob` |

Limitation: not checked whether individual files are covered within foreign tests
(e.g. `layer2Gate.test.ts`).

---

## 3. AprilTag: duplicates and gaps

### 3.1 Dead code related to AprilTag (overview)

Summary from section 1 — everything that belongs to detection, pose, tag map or
the tag approach and runs nowhere in production. Details and evidence there.

| Repo | What | Ref. | Confidence |
|---|---|---|---|
| Cell | Prototype detection and calibration `src/apriltag/` (incl. `MANUAL_TEST.md`) | T1 | certain |
| Cell | `SharedCameraSource`, `iter_frames`, `ImageFolderSource.current_path` (image sources of the tag CLIs) | T2, T3 | certain |
| Cell | `poses_to_json` (output of the tag poses in the CLI) | T4 | certain |
| Cell | `build_detection_source` (singular, detection source) | T5 | certain |
| Cell | `world_tag_for_robot` (world-tag selection for a robot; only in the test) | T7 | certain |
| Cell | `PupilAprilTagDetector` with `--backend pupil` and `auto` fallback: never set in production, never tested | P1 | likely |
| WSC | `AutolocateLayer2Window` and the chain `Layer2RunPanel`, `RobotRunConfirm`, `EndEffectorSpinTest`, `endEffectorSpin`, `useLayer2Run` (tag approach, superseded by `AutolocateActions`) | W1, W2 | certain |
| WSC | `layer2Target`, `setLayer2Target`, `Layer2TargetMarker` (target marker never renders) | W3 | certain |
| WSC | `startNodeId`/`targetNodeId` in `Layer2Binding` (`piServers.ts`) | W4 | likely |
| WSC | `tag-tool/model/units.ts` `snapToStep` (definition only); the rest of the tag tool duplicates the catalog placement, but is partly still used | P5, 1.3 | certain / future open |

Everything else in Cell and WSC with a tag reference (`tagloc/*`, `tagreference`,
`cellwatch`, `tagPlacement`, `tagGeometry`, tag survey) is used; there are tests
for the calculations, only the UI and the hooks are not (section 2).

### 3.2 Duplicates and gaps

| # | Finding | Where | Assessment |
|---|---|---|---|
| P1 | **Two detector backends.** `ArucoTagDetector` (default) and `PupilAprilTagDetector` (fallback with `backend="auto"`, selectable with `--backend pupil`) | Cell: `tagloc/detector.py:231,287,293`, `cli/_common.py:86`, `profiles.py:49` | Only `"aruco"` is set in production; the pupil backend has no test. If `src/apriltag/` (T1) goes, `pupil-apriltags` remains as a dependency solely for this untested fallback. Risk medium: the fallback covers OpenCV without the tag family (`legacy.md` D3) |
| P2 | **Two pose paths.** Prototype `src/apriltag/detect_apriltags.py` (without undistortion) next to `tagloc/pose.py` (undistorted) | Cell | In production only `pose.py`; prototype see T1 |
| P3 | **Three ways to the camera pose.** World tags (`solve_camera_from_world_tags`), reference tags (`camera_pose_from_reference_tags`, `localize.py:263`), anchor (`anchor_from_localization`, `localize_camera_from_anchor`, `localize.py:534,559`) with shared `_solve_joint` (`localize.py:241`) | Cell | No dead code, everything is used. Complexity note only, not examined in depth |
| P4 | `world_tag_for_robot` only in tests | `localize.py:513` | see T7 |
| P5 | **Two ways to place a tag.** Standalone `frontend/src/tag-tool/` (`tag-tool.html`, Vite entry `vite.config.ts:92`, `placement.ts`, `pose.ts`, `exportTagPose.ts`; produces a JSON file) and in the module catalog `cell-modules/model/tagPlacement.ts` + `tagGeometry.ts` (corner plus distances, edges; stores in the catalog). Duplicate helpers: `quaternionFromAxes` (`tag-tool/model/pose.ts`) vs. `quaternionFromBasis` (`tagGeometry.ts`) | WSC | The tag tool is not entirely dead: `WorldTagMarkers.tsx:5-6` uses its `tag36h11` pattern and `printedTagSizeM`. It does not appear in `docs/development.md` or `README.md`. According to commit `e5c183a` the author is someone else — **clarify the future before anyone deletes it**. Risk high |
| P6 | Dead parts with tag reference: `Layer2TargetMarker`, `setLayer2Target` | WSC | see W3 |
| P7 | **`data/LV1/`** (train 29, val 11 files `kalib_*.jpg`), tracked | Cell | No code reads them; only docs (`apriltag-reference.md:1041`, `vision-server-interface.md:1418`: tag-free chessboard images as false-alarm reference). Not re-obtainable → **keep**, but anchor them as a source in the test or the docs |
| P8 | `data/rohbilder/cam_ceiling_20261019-*.png` | Cell | In use (`tests/test_detector.py:29,34`, target folder in `config.py:79`) — no finding |
| P9 | `hardware/Hardware_Architektur_v2(2).drawio.png` | Cell | Not referenced by any file; probably a docs asset. Uncertain |
| P10 | File `backend/catalog/module_catalog.json` | WSC | All fields have a reader. Only `placement.normal` open (2 entries, not individually verified). The file has foreign uncommitted changes, not assessed |
| P11 | Backend knows **no** AprilTag code | WSC `backend/` | Connection runs via generic method calls (`opcua/method_calls.py`); all tag knowledge lies in the frontend |

---

## 4. Recommended order

1. **Immediately, no risk:** remove D7/D8 from `legacy.md`; correct the outdated
   comments W8 and T9.
2. **Delete the WSC Layer 2 chain** (W1–W3, then W4): the only larger legacy item in
   the group. First check the `Layer2Binding` section against `layer2-approach.md`,
   because the Pi contract stays documented.
3. **Cell: remove `src/apriltag/` (T1)** including doc references, then T2–T6.
   Then decide deliberately about `pupil-apriltags` (P1): either test the fallback or
   remove backend and dependency together.
4. **Clarify instead of deleting:** future of the tag tool (P5), `pi_load_profiler.py`
   and `list_realsense_profiles.py` (T8), `world_tag_for_robot` (T7).
5. **Test gaps with the best ratio of benefit to effort:** pupil backend (or its
   removal), `tagloc/cli/*` as smoke tests (do they even run?), `useVisionJob` and
   `useCellWatch` in the frontend, `PiCameraSource`/`RealSenseSource` with stubs.
