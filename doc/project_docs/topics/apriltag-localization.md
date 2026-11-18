# AprilTag localisation — maintainer guide

The cameras on the two Pis find AprilTags, estimate each tag's pose, place the camera in the world frame using the fixed
world tags, and report module poses as OPC UA detections. This guide says where that code lives, which conventions
it relies on, and how to change it safely.

**Concept and rationale** (what the world tags are for, why both cameras share one code path, why anchoring is
needed): see the written thesis (LaTeX). They are not repeated here.

Function-level reference: [`code/cell-tagloc.md`](../code/cell-tagloc.md) and
[`code/cell-vision-server.md`](../code/cell-vision-server.md). This guide does not repeat them.

---

## 1. Code map

### 1.1 Library `src/tagloc/`

| Module | Responsibility | Key names |
|---|---|---|
| `geometry` | 4×4 poses, composition, quaternions (xyzw), averaging. numpy only | `compose`, `invert`, `from_rvec_tvec`, `from_position_quaternion`, `to_position_quaternion`, `average_poses`, `pose_from_dict`/`pose_to_dict` |
| `observations` | Data types between the image layer and the maths layer | `TagObservation`, `TagPose`, `AMBIGUITY_THRESHOLD` |
| `calibration` | Camera calibration JSON: read, write, resolution check | `CameraCalibration`, `load_calibration`, `save_calibration`, `check_resolution`, `scale_to_resolution`, `default_calibration` |
| `lens` | The **only** place that branches between pinhole and fisheye OpenCV calls | `undistort_points`, `project_points`, `calibrate` |
| `tagmap` | Tag map: roles, sizes, world poses, CAD offsets; placing tags into one frame | `TagMap`, `TagEntry`, `load_tag_map`, `tag_map_from_json`, `validate_tag_map`, `place_tags`, `residuals`, `with_world_poses`, `nearest_world_tag` |
| `detector` | Tag detection behind one interface (`cv2.aruco` or `pupil_apriltags`), optional ROI | `TagDetector`, `ArucoTagDetector`, `PupilAprilTagDetector`, `RoiTagDetector`, `build_detector` |
| `pose` | Corners → `T_cam_tag` with reprojection error and ambiguity; joint PnP over all world tags | `estimate_tag_poses`, `tag_object_points`, `solve_camera_from_world_tags` |
| `localize` | Camera pose from world tags, module poses, merging, anchor | `localize_camera`, `locate_modules`, `merge_samples`, `merge_by_module`, `expected_but_missing`, `anchor_from_localization`, `localize_camera_from_anchor`, `anchor_drift` |
| `handeye` | Hand-eye solver (numpy) and its file; anchor data type | `HandEye`, `RobotAnchor`, `solve_hand_eye_robust`, `refine_hand_eye_robust`, `load_hand_eye`, `save_hand_eye` |
| `handeye_offline` | Re-evaluate a stored hand-eye raw-image session without camera or robot | `calibrate_session`, `run_experiments` |
| `boards` | Calibration boards (chessboard, ChArUco): detect, coverage, board pose, calibrate | `BoardSpec`, `detect_board`, `estimate_board_pose`, `calibrate_from_samples` |
| `frames` | Frame sources for the CLI: camera, Pi camera, RealSense, image folder, single image | `open_source`, `to_gray`, `image_size` |
| `overlay` | Drawing only (axis cross, boxes, captions) | `draw_tag_overlay`, `draw_tag_boxes`, `summarise` |
| `reference_edges`, `reference_overlay` | Measure the tag position on a module against reference edges (hand Pi) | `measure_reference_lines`, `fit_in_plane_correction`, `plan_reference_view` |
| `identity` | `configurationId` parts, standard library only | `calibration_identity`, `tag_map_identity` |
| `modes` | Live-stream overlay modes, standard library only | `OVERLAY_MODES`, `normalise_mode` |
| `jsonio` | NaN-safe numbers and UTC timestamps for JSON | `finite_or_none`, `utc_now_z` |

### 1.2 Command-line tools and helpers

| Command | Purpose |
|---|---|
| `python -m tagloc.cli.calibrate` | Camera calibration, interactive or headless from an image folder (`--model pinhole\|fisheye`) |
| `python -m tagloc.cli.detect` | Detect tags and print poses; `--tag-map` adds world poses, `--overlay` opens a window |
| `python -m tagloc.cli.transform` | Convert poses between frames (no OpenCV) |
| `python -m tagloc.cli.build_tagmap` | Survey tags from an image series into a tag map, prints closure errors |
| `python -m tagloc.cli.calibrate_handeye` | Hand-eye calibration from recorded samples |
| `python -m tagloc.cli.calibrate_handeye_offline` | Re-run the offline evaluation of a hand-eye session |
| `tools/make_tag_sheet.py` | True-to-size SVG print sheet for tags or a ChArUco board |
| `tools/make_synthetic_scene.py` | Rendered scene with known tag poses (used by `tests/test_tag_pipeline.py`) |
| `src/vision_server/tools/diagnose_board.py` | Check board `cols`/`rows` against a real frame |

All CLI tools share the arguments in `tagloc/cli/_common.py` (`--source`, `--calibration`, `--tag-map`, `--family`,
`--backend`, `--tag-size-m`, `--max-reproj-error-px`, `--detector-param`, `--allow-resolution-mismatch`).
`--source` accepts `camera[:n]`, `picamera`, `realsense`, a directory or an image file (`frames.open_source`).

### 1.3 Where the vision server uses it

| File | Role |
|---|---|
| `src/vision_server/detection/__init__.py` | Registry `DETECTION_SOURCES`; `_apriltag` builds the source (import inside the factory) |
| `src/vision_server/detection/apriltag/__init__.py` | `AprilTagDetectionSource` = mixins below; `profile_id = "apriltag"` |
| `…/apriltag/resources.py` | `open`: load calibration, tag map, hand-eye, build detector; `apply_tag_map`, `apply_calibration`, `apply_hand_eye`; `configuration_id` |
| `…/apriltag/capture.py` | Fresh frames from `SharedCamera`, `_calibration_for` (resolution check), `_remember_tags` |
| `…/apriltag/job.py` | `acquire_and_detect` (the job), `_locate`, `_carry_with_anchor`, payload attributes, `robot_pose_from_parameters`, `SOURCE_BY_FRAME` |
| `…/apriltag/tags.py`, `board.py`, `reference.py`, `cell.py` | `MeasureTags`/`measure_tag`, board measurement, reference edges, cell watch (`observe_cell`) |
| `src/vision_server/profiles.py` | `AprilTagProfileConfig` (all tunables with defaults) |
| `src/vision_server/server.py` | Per-Pi values: `PI_IDENTITIES`, `PI_APRILTAG_PRESETS`, `apriltag_config(frame_id)` |
| `src/vision_server/runner/tag_map.py` | OPC UA `SetTagMap` and node `TagMapJson` |
| `src/vision_server/runner/livestream.py`, `stream_overlay.py` | Live-stream overlay; own detector without corner refinement (`_build_annotator`) |
| `src/vision_server/payload.py` | Payload envelope `wsc.vision.detections/1` |

---

## 2. Data flow of one `apriltag` job

All in `JobMixin` (`detection/apriltag/job.py`) unless stated otherwise.

1. `acquire_and_detect(request)` reads the robot pose `T_base_flange` from the job parameters with
   `robot_pose_from_parameters` (seven floats `x y z qx qy qz qw`, metres, xyzw; anything else → `None`).
2. `_capture_samples` takes `samples_per_job` fresh frames (`_next_frame`) and runs `_locate` on each via
   `run_blocking` (worker thread, never on the event loop).
3. `_locate(image, pose_base_flange)` per frame:
   1. `_calibration_for(image)` → calibration for this image size (§ 4.2).
   2. `self._detector.detect(frames.to_gray(image))` → `TagObservation` list.
   3. `pose.estimate_tag_poses(…, tag_map=…, default_size_m=tag_size_m, max_reprojection_error_px=…)` →
      `TagPose` list (`pose_cam_tag`, `reprojection_error_px`, `ambiguity_ratio`). Tags above the error limit are dropped.
   4. `_remember_tags` stores a `TagSnapshot` for the live-stream overlay.
   5. `localize.localize_camera(tag_poses, tag_map, calibration=…)` → `CameraLocalization` (`T_world_cam`) or `None`
      if no surveyed world tag is visible. With two or more world tags: joint PnP (`solver="joint"`), else `single`/`average`.
   6. `_carry_with_anchor`: only with a hand-eye file **and** a robot pose. World tag seen → new anchor
      (`anchor_from_localization`) and drift against the old one (`anchor_drift`); no world tag → camera pose from the
      anchor (`localize_camera_from_anchor`, `origin="robot_pose"`).
   7. `localize.locate_modules(…, localization, frame_id, source=SOURCE_BY_FRAME[...])` → `ModuleLocation` list:
      `T_world_module = T_world_cam · T_cam_tag · T_tag_module`, plus nearest world tag and `T_worldtag_module`.
      World tags themselves are skipped.
4. Back in `acquire_and_detect`: `merge_samples` (average per tag over the frames, `sampleCount`); empty →
   `VisionJobError(DETECTION_FAILED, "Kein AprilTag der Familie '…' gefunden")`; `expected_but_missing`;
   `merge_by_module` (several tags on one module, e.g. the robot); `_warn_no_world_tag` if there is no world frame.
5. `_build_detection` / `_detection_attributes` → one `Detection` per module. Attribute names and order are part of
   the payload interface (`tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`, `source`, `role`, `referenceTagId`,
   `cameraPoseOrigin`, `anchorDriftM`, `missingModules`, …).

The other paths (`MeasureTags`, cell watch, `tagloc.cli.detect`) reuse steps 3.2–3.7 with fewer stages.

---

## 3. Conventions you must not break

### 3.1 Poses, units, frames

| Rule | Where it is stated |
|---|---|
| A pose is a 4×4 `float64` `np.ndarray`; `T_a_b` = pose of `b` expressed in `a`; `T_a_c = compose(T_a_b, T_b_c)` | `tagloc/geometry.py` docstring |
| Metres and radians everywhere; degrees only in payload fields named `…Deg` | `geometry.py`, `job.py` |
| Quaternions in **xyzw** order (payload and three.js), never wxyz | `geometry.py` |
| Frame IDs: `world` (tag map `frameId`), `cam_ceiling` (ceiling Pi), `cam_flange` (hand Pi). The frame ID also names the calibration file and the preset key | `server.py` `PI_IDENTITIES`, `apriltag_config` |
| Camera frame convention `z_forward_x_right_y_down` (OpenCV), reported as `frameConvention` while poses stay in the camera frame | `AprilTagProfileConfig.frame_convention` |
| `SOURCE_BY_FRAME`: `cam_ceiling` → `ceiling`, `cam_flange` → `flange` | `detection/apriltag/job.py` |

### 3.2 Tag frame and corners

- Tag origin at the centre, +Z out of the tag (`pose.tag_object_points`).
- `TagObservation.corners`: four image points, **clockwise from the top left** (the `cv2.aruco` order).
  `SOLVEPNP_IPPE_SQUARE` in `pose.py` relies on it, so every detector adapter must convert to this order.
- A pose is `ambiguous` when `ambiguity_ratio > AMBIGUITY_THRESHOLD` (0.6). The flag goes into the payload.

### 3.3 Meaning lives in the tag map, chaining in one place

- A tag ID carries no meaning in code. Role, module, size and CAD offset come only from the tag map.
- Only `role: "world"` **with** `poseInWorld` gives `T_world_cam` (`TagEntry.is_reference`). `robot` and `module` are
  both movable (`MOVABLE_ROLES`) and measured; the robot is treated like a module.
- `T_world_module = T_world_cam · T_cam_tag · T_tag_module` is evaluated only in `tagloc/localize.py`. Do not add a
  second place (vision server, frontend, CLI).
- `tagToModule` may be applied in one place only. The WebSkillComposition frontend currently applies the module
  offset itself (see [`code/webskill-cell-modules.md`](../code/webskill-cell-modules.md)), so `config/tagmap.json` on the
  Pis holds no `tagToModule`. Otherwise the offset is applied twice.

### 3.4 Import layering (enforced by `tests/test_layering.py`)

| Group | Modules | Rule |
|---|---|---|
| Standard library only | `identity`, `modes`, `jsonio` | not even numpy |
| No cv2 | `identity`, `modes`, `observations`, `geometry`, `calibration`, `tagmap`, `localize`, `handeye` | must import without OpenCV |
| cv2 inside functions only | `detector`, `pose`, `boards`, `overlay`, `frames` | no `import cv2` at module level |
| Direction | `tagloc` never imports `vision_server` | vision server → tagloc only |

In `vision_server/detection/__init__.py` the factories import their source inside the function, so the server starts
without OpenCV. `geometry.from_rvec_tvec` implements Rodrigues in numpy for the same reason.

---

## 4. Files and configuration

### 4.1 Per-Pi profile

`apriltag_config(frame_id)` in `src/vision_server/server.py` builds one `AprilTagProfileConfig` per Pi from the
defaults in `profiles.py` plus `PI_APRILTAG_PRESETS[frame_id]`. Both Pis run the same server and the same source; the
preset is the only difference.

| Field | `cam_ceiling` | `cam_flange` | Default (`profiles.py`) |
|---|---|---|---|
| `resolution` | 4056×3040 | 1920×1080 (must equal `CameraStreamConfig.realsense_resolution`) | 2028×1520 |
| `allow_resolution_mismatch` | `True` (old 2028×1520 calibration scaled) | — | `False` |
| `tag_size_m` | 0.080 | 0.080 | 0.05 |
| `samples_per_job` | 3 | 5 | 3 |
| `max_reproj_error_px` | 3.0 | 1.5 | 3.0 |
| `calibration_model` | `fisheye` | `pinhole` | `pinhole` |
| Calibration board | chessboard 7×10, 23 mm | chessboard 7×10, 23 mm | chessboard 9×6, 30 mm |
| `subpixel_corner_refinement` / method | `True` / `subpix` | `True` / `subpix` | `False` / `subpix` |
| `detector_parameters` | `{"perspectiveRemovePixelPerCell": 8}` | — | `{}` |
| `detection_roi` | `(917, 568, 2114, 2079)` | — | `None` |
| `hand_eye_path` | `None` | `data/handeye/cam_flange.json` | `None` |

`samples_per_job` is the number of frames averaged per job (`merge_samples`).

Environment overrides read in `server.py`: `VISION_APRILTAG_DETECTOR_PARAMS` (JSON object, merged per key, `null`
removes), `VISION_DETECTION_ROI` (`x,y,w,h` or `off`), `VISION_SUBPIXEL_CORNER_REFINEMENT`, `VISION_CALIBRATION_MODEL`,
`VISION_APRILTAG_QUAD_DECIMATE`, `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1`, `VISION_SAVE_CALIBRATION_CAPTURES=1`,
`VISION_FRAME_ID`.

### 4.2 Camera calibration — `data/calibration/<frame_id>.json`

- Not versioned (`data/` is gitignored); each Pi has its own. Written by `tagloc.cli.calibrate` or the OPC UA
  calibration session (`apply_calibration` swaps it in without a restart).
- Schema `wsc.vision.calibration/2`: `calibrationId`, `frameId`, `distortionModel` (`pinhole`|`fisheye`), `imageSize`,
  `cameraMatrix`, `distortionCoefficients` (fisheye: exactly 4), `rmsReprojectionError`, `sampleCount`, `board`,
  `createdAt`. Schema `/1` is still read and treated as pinhole.
- Image size ≠ `imageSize`: `check_resolution` raises, unless `allow_resolution_mismatch` is set. Then
  `scale_to_resolution` scales the intrinsics, which only works for the same aspect ratio.
- File missing: `open` raises and the server stays in Preoperational. Exceptions: RealSense factory intrinsics
  (`_fallback_calibration`), or `allow_placeholder_calibration` (poses not metric).

### 4.3 Tag map — `config/tagmap.json`

Versioned, the same file for both Pis. Examples: `config/tagmap.example.json` (full schema),
`config/tagmap.test.example.json` (one world tag + one module).

| Key | Meaning |
|---|---|
| `schema` | `wsc.vision.tagmap/2`. `/1` is migrated on load (`reference` → `world`, `robot_table` → `robot`, logged) |
| `frameId`, `anchorTagId`, `tagFamily` | world frame name, origin tag (must have role `world`), family (default `tag36h11`) |
| `tags[].tagId`, `sizeM` | required; `sizeM` = printed edge of the black square in metres |
| `tags[].role` | `world`, `robot` or `module` (missing → `module`; unknown → `ValueError`) |
| `tags[].poseInWorld` | `{position, orientation(xyzw)}` for world tags; `null` for movable tags (a pose on them is ignored) |
| `tags[].moduleId`, `instanceId`, `tagToModule` | optional; tags without an entry are reported as `TAG-<id>` |

- Loading: `ResourcesMixin._load_tag_map`. Missing or unusable file → empty map, a warning, poses in the camera frame.
  `validate_tag_map` problems (not 4 world tags, unsurveyed world tag, anchor not `world`, no `robot` tag) are only
  logged.
- At runtime: OPC UA `SetTagMap` (`runner/tag_map.py`) validates, calls `apply_tag_map` (discards anchor and drift,
  rebuilds `configurationId`) and writes the file. It is rejected with `BUSY` during a job, a camera or hand-eye calibration, or a Layer 2 run.

### 4.4 Tag sizes

`TagMap.size_for(tag_id, default_m)` decides per tag: the map's `sizeM` if the tag is listed, otherwise
`tag_size_m` of the profile. In operation, world tags are in the map (currently 0.2 m) and all module and robot tags
use the preset value (0.080 m). A wrong size scales the distance linearly and is otherwise not visible.

### 4.5 Hand-eye — `data/handeye/cam_flange.json`

Schema `wsc.vision.handeye/1`, key `poseFlangeCam` (`T_flange_cam`), plus quality figures. Loaded only on `cam_flange`.
`apply_hand_eye` swaps it at runtime and discards the anchor. Without the file or without a robot pose in the job,
`_carry_with_anchor` does nothing.

---

## 5. Maintenance recipes

**Add or move a world tag**
1. Print it with `tools/make_tag_sheet.py --ids … --size-mm …` and measure the printed edge.
2. Take an image series in which neighbouring tags are seen together, then
   `python -m tagloc.cli.build_tagmap --source <folder> --calibration data/calibration/<frame>.json --tag-map config/tagmap.json --anchor <id> --out config/tagmap.json`.
   Roles, sizes and offsets in the existing map are kept; new tags become `world`. Check the closure errors it prints.
3. Distribute it with `SetTagMap` or by copying the file and restarting. Check the log for `validate_tag_map` warnings.
   `EXPECTED_WORLD_TAG_COUNT` (4) is only a warning threshold.

**Change the tag size**
- World tag: `sizeM` of its entry.
- All module/robot tags: `tag_size_m` in `PI_APRILTAG_PRESETS` (both cameras).
- A single deviating tag: give it its own entry with `sizeM` in the map.

**Change the calibration board**: `calibration_board_*` keys in the preset (`calibration_board_spec()` turns them into
a `BoardSpec`). Check them against a frame with `src/vision_server/tools/diagnose_board.py`. For the hand-eye
chessboard, keep `cols + rows` odd so a 180° rotation can be told apart.

**Add a camera / Pi**
1. Hostname → `(system id, frame_id)` in `PI_IDENTITIES`, backend in `PI_CAMERA_BACKENDS`.
2. Preset under the new `frame_id` in `PI_APRILTAG_PRESETS` (and `PI_CAMERA_STREAM_PRESETS`, `PI_ASSET_PRESETS`).
3. Add the frame to `SOURCE_BY_FRAME` if the payload should carry a `source`. A hand-eye path is only set for
   `cam_flange` (hard-coded in `apriltag_config`).
4. Calibrate to `data/calibration/<frame_id>.json`. Extend `tests/test_server_config.py`.

**Change detector parameters**: try them first with `VISION_APRILTAG_DETECTOR_PARAMS` or `--detector-param` in
`tagloc.cli.detect`, then put them in the preset. An unknown name or a wrong type raises `ValueError` at startup
(`apply_detector_parameters`). Corner refinement in the job (`subpixel_corner_refinement`) does not affect the
live-stream detector.

**Change the lens model**: set `calibration_model` (applies to new calibrations), then recalibrate. Pose estimation
uses the model stored in the file. Fisheye/pinhole branching belongs in `tagloc/lens.py` only.

**No world tag found** (log `Kein Welttag im Bild -- Posen bleiben im Kamera-KS`, payload without `cameraPoseOrigin`):
1. The log line lists the seen tag IDs and the surveyed world tags of the map. Does the map have `role: "world"` **with**
   `poseInWorld`?
2. Is the tag inside `detection_roi` (ceiling)? Tags outside the region are not detected.
3. Was it dropped by `max_reproj_error_px`? Run `python -m tagloc.cli.detect --source … --calibration … --tag-map config/tagmap.json -v`.
4. Is `tagFamily` / `tag_family` right? Do calibration and frame resolution match (§ 4.2)?
5. Hand camera: without a hand-eye file and a robot pose in the job, world tag and module must be in the same image.

**Add a payload attribute**: extend `_detection_attributes` in `job.py`. Add keys only; do not rename or reorder
existing ones (schema `wsc.vision.detections/1`).

---

## 6. Tests

Run from the repository root (full suite about 6 minutes):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .
# one file:
PYTHONPATH=src python3 -m unittest tests.test_tag_map
```

| Test file | Covers |
|---|---|
| `test_tag_geometry.py` | `geometry` golden values (rvec/tvec, quaternions, compose/invert) |
| `test_tag_map.py` | `place_tags`, `residuals`, tag map file round trip |
| `test_calibration_io.py` | Calibration file, resolution check, configuration identity |
| `test_lens.py` | Pinhole and fisheye in `lens` |
| `test_detector.py`, `test_detector_roi.py` | Corner refinement flag, `RoiTagDetector` |
| `test_pose_filter.py` | `TagPose.is_usable` / reprojection filter |
| `test_joint_camera_pose.py` | Joint PnP over all world tags |
| `test_welttags.py` | Roles, world-tag localisation, merging |
| `test_handeye*.py` | Hand-eye solver, anchoring, sessions, offline evaluation |
| `test_tag_pipeline.py` | Synthetic scene, whole chain detector → pose → `place_tags` → `locate_modules`; `POSITION_TOLERANCE_M = 0.002`, `ANGLE_TOLERANCE_DEG = 1.0`. Skipped without OpenCV |
| `test_apriltag_source.py` | `AprilTagDetectionSource` with fake camera and fake detector (no OpenCV, no files) |
| `test_server_config.py`, `test_subpixel_settings.py` | Per-Pi presets and environment overrides |
| `test_layering.py` | Import layering of § 3.4 |
| `test_stream_overlay.py`, `test_overlay_scaling.py` | Live-stream overlay and drawing |
| `test_frame_sources.py`, `test_calibrate_cli.py` | `--source` handling, calibrate CLI |

Test images are generated in the test (`tools/make_synthetic_scene.py`), not committed.

---

## 7. Related documents

| Document | Content |
|---|---|
| [`code/cell-tagloc.md`](../code/cell-tagloc.md) | Per-file reference of `src/tagloc/` and its CLI |
| [`code/cell-vision-server.md`](../code/cell-vision-server.md) | Per-file reference of `src/vision_server/` |
| [`vision-server-interface-short.md`](../interfaces/vision-server-interface-short.md) | OPC UA interface in brief: jobs, result, error codes |
| [`apriltag-reference.md`](apriltag-reference.md) | Functions, CLI and file formats in detail |
