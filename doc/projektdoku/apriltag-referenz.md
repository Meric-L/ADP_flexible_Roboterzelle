# AprilTag functionality — overview

Rough map of the AprilTag code: what each area is for and which functions to look at.
This file holds no code. What each function does is in the per-module handbook
[`code/zelle-tagloc.md`](code/zelle-tagloc.md) (package `src/tagloc/`) and
[`code/zelle-vision-server.md`](code/zelle-vision-server.md) (OPC UA side). Concept and
rationale: [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md). OPC UA interface:
[`vision-server-interface.md`](vision-server-interface.md).

---

## Terms and conventions

| Term | Meaning |
|---|---|
| `T_a_b` | Pose of `b` expressed in `a`, a 4×4 homogeneous `float64` matrix. Chaining: `T_a_c = T_a_b · T_b_c` |
| Units | meters and radians everywhere (degrees only in catalog/UI fields named `...Deg`) |
| Quaternion | **xyzw**, sign normalised to `w >= 0` (payload key `"rotation": "quaternion_xyzw"`) |
| Camera frame | OpenCV optical frame: +X right, +Y down, +Z forward. `frameConvention` = `z_forward_x_right_y_down` |
| World frame | defined by the anchor tag of the tag map, `frameId` `world` |
| Frame names | `cam_ceiling` (Layer 1, ceiling Pi), `cam_flange` (Layer 2, hand Pi) |

A frame name alone does not say where +Z points, so every camera-relative pose carries
`frameConvention`. Readers of the payload read `frameId`, they never assume it.

**Layering rule** (tested in `tests/test_layering.py`): `identity`, `modes` and `jsonio` need
only the standard library; `geometry`, `calibration`, `tagmap`, `localize`, `observations`
and `handeye` load without OpenCV; `detector`, `pose`, `boards`, `overlay` and `frames`
import OpenCV only inside functions; `tagloc` imports nothing from `vision_server`.

---

## Areas of `src/tagloc/`

### Geometry
- Poses as plain numpy arrays; build, chain, invert and average them:
  `from_rvec_tvec`, `compose`, `invert`, `average_poses`
  ([`geometry.py`](code/zelle-tagloc.md#srctaglocgeometrypy)).
- Payload form position + quaternion: `to_position_quaternion` / `from_position_quaternion`.
- One JSON form for every stored pose (calibration, tag map, hand-eye, CLI files):
  `pose_to_dict` / `pose_from_dict`.

### Camera calibration and boards
- `CameraCalibration` with `load_calibration` / `save_calibration`; one file per physical
  camera ([`calibration.py`](code/zelle-tagloc.md#srctagloccalibrationpy)).
- `check_resolution` refuses a calibration made at a different image size;
  `scale_to_resolution` is the deliberate way to use it anyway.
- `default_calibration` is a placeholder for tests only (poses not to scale), enabled by
  `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1`.
- Lens model per camera (`pinhole` or `fisheye`); all OpenCV lens calls go through
  [`lens.py`](code/zelle-tagloc.md#srctagloclenspy) (`undistort_points`, `project_points`, `calibrate`).
- Board detection and the calibration math: `BoardSpec`, `detect_board`, `compute_coverage`,
  `calibrate_from_samples` ([`boards.py`](code/zelle-tagloc.md#srctaglocboardspy)).
- `calibration_identity` / `tag_map_identity` give `"<name>#<mtime>"` for `configurationId`
  without opening the file ([`identity.py`](code/zelle-tagloc.md#srctaglocidentitypy)).

### Tag map
- `TagMap` / `TagEntry`: which tag is which module, its size, role (`world`, `robot`,
  `module`) and CAD offset; `load_tag_map`, `save_tag_map`, `tag_map_from_json`
  ([`tagmap.py`](code/zelle-tagloc.md#srctagloctagmappy)).
- `validate_tag_map` reports objections (exactly four surveyed world tags, anchor is a world
  tag, …) as a list instead of raising; `nearest_world_tag` finds the closest world tag.
- Building a map from co-observations: `place_tags`, `residuals` (closure errors, the quality
  figure of the map), `with_world_poses`, `missing_tags`, `format_residual_report`.
- Old schema `/1` is migrated on load, not rejected; an unknown schema or role raises.

### Detection and pose
- `build_detector` returns a `TagDetector`: `ArucoTagDetector` (default) or
  `PupilAprilTagDetector`, optionally wrapped in `RoiTagDetector`; extra OpenCV settings via
  `apply_detector_parameters` ([`detector.py`](code/zelle-tagloc.md#srctaglocdetectorpy)).
- Data types between image and math: `TagObservation`, `TagPose` with `is_ambiguous` and
  `is_usable` ([`observations.py`](code/zelle-tagloc.md#srctaglocobservationspy)).
- `estimate_tag_poses` undistorts the corners and solves each tag with the size from the tag
  map; `solve_camera_from_world_tags` solves the camera jointly from several world tags
  ([`pose.py`](code/zelle-tagloc.md#srctaglocposepy)).

### Localization (tag → module)
- `localize_camera` gives `T_world_cam` (`CameraLocalization`) from the visible world tags
  ([`localize.py`](code/zelle-tagloc.md#srctagloclocalizepy)).
- `locate_modules` is the only place where `T_world_cam · T_cam_tag · T_tag_module` is
  chained; without a localization the result stays in the camera frame.
- Merging: `merge_by_module` (several tags of one module in one image), `merge_samples`
  (several images of one job), `confidence_from` (confidence from error and ambiguity).
- `expected_but_missing` lists modules of the map that were not found.

### Hand-eye and anchoring
- `HandEye` holds `T_flange_cam`; `solve_hand_eye`, `solve_hand_eye_robust`,
  `refine_board_session`, `load_hand_eye` / `save_hand_eye`
  ([`handeye.py`](code/zelle-tagloc.md#srctaglochandeyepy)).
- Anchoring: `RobotAnchor`, `anchor_world_base`, `camera_pose_from_robot`; quality measure
  `target_spread`.
- In `localize.py`: `anchor_from_localization`, `localize_camera_from_anchor`, `anchor_drift`.
  The payload says which origin was used (`world_tags` or `robot_pose`).
- Offline re-evaluation of a recorded session:
  [`handeye_offline.py`](code/zelle-tagloc.md#srctaglochandeye_offlinepy).

### Overlay
- `draw_tag_overlay` (outline, axis cross, labels; ambiguous poses orange) for the CLI,
  `draw_tag_boxes` (green box and ID) for the live stream
  ([`overlay.py`](code/zelle-tagloc.md#srctaglocoverlaypy)).
- `Window` / `has_display` keep the CLI running without a screen.
- Stream modes `OVERLAY_MODES` and `normalise_mode`
  ([`modes.py`](code/zelle-tagloc.md#srctaglocmodespy)).

### Surveying tag positions with reference edges
- Corrects a catalog start value `T_module_tag` in the tag plane (x, y, rotation about z)
  from table or profile edges seen next to the tag by the hand camera.
- `measure_reference_lines`, `fit_in_plane_correction`, `average_corrections`,
  `plan_reference_view`, `catalog_rpy_deg`
  ([`reference_edges.py`](code/zelle-tagloc.md#srctaglocreference_edgespy)).
- Result `dofs`: `full` (two edges), `partial` (one edge), `none` (start value kept).
- Debug drawing of the edge search: `draw_reference_debug`
  ([`reference_overlay.py`](code/zelle-tagloc.md#srctaglocreference_overlaypy)).
- Work plans: [`tag-einmessen-bezugskanten.md`](arbeitsplaene/tag-einmessen-bezugskanten.md),
  [`tag-einmessen-liveview.md`](arbeitsplaene/tag-einmessen-liveview.md).

---

## File formats

**Camera calibration** `data/calibration/<frame_id>.json` (per camera, gitignored except
`cam_flange.json`), written by `save_calibration`:

| Field | Content |
|---|---|
| `schema` | `wsc.vision.calibration/2` (`/1` is still read, as pinhole) |
| `calibrationId`, `frameId`, `createdAt` | provenance |
| `distortionModel` | `pinhole` or `fisheye` |
| `imageSize` | `[width, height]` the calibration is valid for |
| `cameraMatrix`, `distortionCoefficients` | intrinsics (fisheye: 4 coefficients) |
| `rmsReprojectionError`, `sampleCount`, `board` | quality and the board used |

**Tag map** `config/tagmap.json` (versioned, describes the cell), written by `save_tag_map`:

| Field | Content |
|---|---|
| `schema` | `wsc.vision.tagmap/2` |
| `frameId`, `anchorTagId`, `tagFamily` | target frame (`world`), anchor tag, family (`tag36h11`) |
| `tags[]` | `tagId`, `role`, `sizeM`, optional `moduleId`, `instanceId`, `tagToModule`, and `poseInWorld` (`null` for movable tags) |

**Hand-eye** `data/handeye/<frame_id>.json`, written by `save_hand_eye`:

| Field | Content |
|---|---|
| `schema` | `wsc.vision.handeye/1` |
| `frameId`, `calibrationId`, `createdAt` | provenance |
| `poseFlangeCam` | `T_flange_cam` |
| `rmsPositionM`, `rmsRotationDeg`, `maxPositionM`, `maxRotationDeg`, `sampleCount` | quality (`null` if not available) |
| optional | `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`, `intrinsicsSource` |

**Module catalog, tag survey data** (frontend side, catalog `ModuleTag`; format defined in
[`modell-flaechen-tag-platzierung.md`](arbeitsplaene/modell-flaechen-tag-platzierung.md)
and [`tag-einmessen-bezugskanten.md`](arbeitsplaene/tag-einmessen-bezugskanten.md)):

- `placement.lines`: reference edges `A`/`B` with `start`/`end` in the module frame, meters.
- `measurement`: `method` (`camera-edges` for module tags, `robot-fk` for tags on the robot),
  `measuredAt`, `dofs`, `guess` (start value), `deltaMm`, `deltaRotDeg`, `spreadMm`, `lines`.
  A measured value overwrites `position`/`rotationDeg`; each tag is surveyed once.

---

## Command-line tools

All accept `--source` as a camera or an image folder (`open_source` in
[`frames.py`](code/zelle-tagloc.md#srctaglocframespy)); shared options and loaders in
[`cli/_common.py`](code/zelle-tagloc.md#srctagloccli_commonpy).

| Module | Purpose |
|---|---|
| [`tagloc.cli.calibrate`](code/zelle-tagloc.md#srctaglocclicalibratepy) | camera calibration, interactive or headless (`--board`, `--model`, `--out`) |
| [`tagloc.cli.detect`](code/zelle-tagloc.md#srctaglocclidetectpy) | detect tags, print poses, optional overlay or `--json` file |
| [`tagloc.cli.transform`](code/zelle-tagloc.md#srctaglocclitransformpy) | pure coordinate transform (`--camera-pose` or `--from-reference`, `--invert`) |
| [`tagloc.cli.calibrate_handeye`](code/zelle-tagloc.md#srctaglocclicalibrate_handeyepy) | hand-eye from a sample list (`--samples`) or a recorded session (`--session`) |
| [`tagloc.cli.calibrate_handeye_offline`](code/zelle-tagloc.md#srctaglocclicalibrate_handeye_offlinepy) | re-run the offline evaluation of a session |
| [`tagloc.cli.build_tagmap`](code/zelle-tagloc.md#srctaglocclibuild_tagmappy) | place tags from an image series (`--anchor`, `--out`); every image needs two or more tags |

Helpers under `tools/`: [`make_tag_sheet.py`](code/zelle-tools-tests.md#toolsmake_tag_sheetpy)
(true-to-scale SVG print sheet with a 100 mm scale bar) and
[`make_synthetic_scene.py`](code/zelle-tools-tests.md#toolsmake_synthetic_scenepy)
(synthetic images with known ground truth).

---

## In the vision server

- Source: `AprilTagDetectionSource`, one class for both Pis, composed from mixins
  ([`detection/apriltag/__init__.py`](code/zelle-vision-server.md#srcvision_serverdetectionapriltag__init__py)).
  `open()` loads calibration, tag map, hand-eye and detector
  ([`resources.py`](code/zelle-vision-server.md#srcvision_serverdetectionapriltagresourcespy));
  without a calibration the server stays in `Preoperational`.
- Configuration: `AprilTagProfileConfig`
  ([`profiles.py`](code/zelle-vision-server.md#srcvision_serverprofilespy)), filled per Pi by
  `apriltag_config` from `PI_APRILTAG_PRESETS`
  ([`server.py`](code/zelle-vision-server.md#srcvision_serverserverpy)). Environment
  overrides include `VISION_APRILTAG_DETECTOR_PARAMS` and `VISION_ALLOW_PLACEHOLDER_CALIBRATION`.
- Job path: `JobMixin.acquire_and_detect` localizes and builds the attributes
  ([`job.py`](code/zelle-vision-server.md#srcvision_serverdetectionapriltagjobpy));
  frames come from the shared camera with its own `capture_timeout_s`
  ([`capture.py`](code/zelle-vision-server.md#srcvision_serverdetectionapriltagcapturepy));
  every OpenCV call runs via `run_blocking`
  ([`detection/base.py`](code/zelle-vision-server.md#srcvision_serverdetectionbasepy)).
- Result: payload schema `wsc.vision.detections/1`
  ([`payload.py`](code/zelle-vision-server.md#srcvision_serverpayloadpy)); attributes such as
  `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`, `role`, `referenceTagId`,
  `cameraPoseOrigin`, `anchorDriftM`, `missingModules`. Field list:
  [`vision-server-interface.md`](vision-server-interface.md), section 6.
- Further methods: `measure_tags` ([`tags.py`](code/zelle-vision-server.md#srcvision_serverdetectionapriltagtagspy)),
  `measure_tag_reference` / `plan_tag_reference_view`
  ([`reference.py`](code/zelle-vision-server.md#srcvision_serverdetectionapriltagreferencepy),
  [`tagreference.py`](code/zelle-vision-server.md#srcvision_servertagreferencepy)),
  live stream overlay ([`stream_overlay.py`](code/zelle-vision-server.md#srcvision_serverstream_overlaypy)).

---

## When something is wrong

| Symptom | Where to look |
|---|---|
| Distances off by a constant factor | `sizeM` in the tag map, or calibration resolution (`check_resolution`) |
| Pose flips between two states | `ambiguous` in the payload (`TagPose.is_ambiguous`); view the tag more steeply |
| Poses at the image edge worse than in the centre | calibration coverage (`compute_coverage`), not only the RMS |
| `place_tags` returns fewer tags than captured | `missing_tags`: no co-observation chain to the anchor |
| Large closure errors | `residuals`: calibration or a tag size is wrong |
| Server stays `Preoperational` | `open()` in `resources.py`: missing calibration or tag-map file (log names the path) |
| Layer 2 reports camera frame instead of `world` | `AprilTagProfileConfig.hand_eye_path`, no anchor (`anchor_from_localization`) |
| `anchorDriftM` / `anchorDriftDeg` grow | stale anchor (`anchor_drift`); bring a world tag into view again |
| Hand-eye result with large spread | `target_spread`: too little rotation between robot poses, or calibration/tag size |
| `DETECTION_FAILED` although a tag is visible | `TagPose.is_usable`: NaN reprojection error or above `max_reproj_error_px` |
