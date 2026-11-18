# tagloc — AprilTag localisation library

`src/tagloc/` turns camera images into poses of tags, modules and the robot in one shared world coordinate system.

## What it does

- Detects AprilTags (`cv2.aruco` or `pupil_apriltags`) and estimates `T_cam_tag` with quality measures.
- Finds the camera pose in the world from the fixed **world tags** (`T_world_cam`).
- Places movable tags (modules, robot) in the world frame via a **tag map** file: a tag carries a number, the map says what it is.
- Calibrates cameras (lens model, hand-eye) and surveys tag maps.
- Library first: the CLI tools are thin adapters, the vision server is the main caller.

## Data flow

```
image (frames.py)
  -> TagObservation: id + corners          detector.py
  -> TagPose: T_cam_tag + reprojection     pose.py   (lens.py undistorts, calibration.py supplies intrinsics)
  -> CameraLocalization: T_world_cam       localize.localize_camera (world tags from tagmap.py)
  -> ModuleLocation: T_world_module        localize.locate_modules  (tag map: tag -> module, offset)
  -> merged over samples / cameras         merge_samples, merge_by_module, merge_locations
```

- Camera pose: `SOLVER_JOINT` (all world tags at once, `pose.solve_camera_from_world_tags`), `SOLVER_SINGLE`, `SOLVER_AVERAGE`, or `SOLVER_STORED` (pose taken over).
- `CameraLocalization.origin` is `ORIGIN_WORLD_TAGS` (optical, precise) or `ORIGIN_ROBOT_POSE` (camera on the flange, derived from the robot pose via hand-eye).
- Sources are ranked `SOURCE_CEILING` before `SOURCE_FLANGE` when merging.
- Only world tags are fixed; the robot is a module like any other.

## Import layers

| Layer | Modules | Needs |
|---|---|---|
| Pure math | `geometry`, `calibration`, `tagmap`, `localize`, `observations`, `identity`, `jsonio`, `modes` | numpy only, no `cv2` |
| Image | `boards`, `detector`, `pose`, `lens`, `frames`, `overlay`, `reference_edges`, `reference_overlay` | OpenCV |
| Hand-eye | `handeye`, `handeye_offline` | numpy and OpenCV |
| Tools | `cli/*` | thin adapters |

The package never imports `vision_server` at module level; the dependency runs one way (`vision_server` imports `tagloc`).

## Main modules

| Module | Role |
|---|---|
| `detector.py` | `TagDetector` interface, `ArucoTagDetector`, `PupilAprilTagDetector`, `RoiTagDetector`, `build_detector` |
| `pose.py` | `estimate_tag_poses`, `solve_camera_from_world_tags` (`JointCameraPose`) |
| `tagmap.py` | `TagMap`, `TagEntry`, roles `world` / `robot` / `module`, `load_tag_map`, `place_tags`, `residuals` |
| `localize.py` | `localize_camera`, `locate_modules`, merge functions, `anchor_from_localization`, `anchor_drift` |
| `handeye.py` | `HandEye`, `solve_hand_eye_robust`, `refine_hand_eye`, `camera_pose_from_robot`, `RobotAnchor` |
| `handeye_offline.py` | Re-evaluate a recorded hand-eye session without camera or robot |
| `calibration.py` | `CameraCalibration`, `load_calibration`, `save_calibration`, `scale_to_resolution` |
| `lens.py` | Pinhole/fisheye-dependent OpenCV calls: `undistort_points`, `project_points`, `calibrate` |
| `boards.py` | Calibration boards: `build_board`, `detect_board`, `compute_coverage`, `estimate_board_pose` |
| `reference_edges.py` | Measure the tag position on a module from reference edges (`measure_reference_lines`, `plan_reference_view`) |
| `frames.py` | `FrameSource` implementations, `open_source` (folder, image, `VideoCaptureSource`, `PiCameraSource`, `RealSenseSource`) |
| `identity.py` | `calibration_identity`, `tag_map_identity`: `"<name>#<mtime>"` strings of the active files |
| `overlay.py`, `modes.py` | Drawing for the live stream; overlay modes `off`, `apriltag`, `undistorted`, `tagreference` |

## Files it reads and writes

| File | Schema | Code |
|---|---|---|
| Camera calibration JSON (default `data/calibration/camera.json`, per camera e.g. `cam_ceiling.json`) | `wsc.vision.calibration/2` | `calibration.py` |
| Hand-eye JSON (e.g. `data/handeye/cam_flange.json`; optional, only the hand camera) | `wsc.vision.handeye/1` | `handeye.py` |
| Hand-eye session record | `wsc.vision.handeye.session/1` | `handeye.py` |
| Tag map JSON (default `config/tagmap.json`) | `wsc.vision.tagmap/2` | `tagmap.py` |

Paths are chosen by the caller (`vision_server.profiles`, CLI arguments), not by the library.

## How the vision server uses it

- `vision_server.detection.apriltag` (`cell.py`, `job.py`, `capture.py`, `reference.py`) runs detection, pose, localisation and merging; `SOURCE_BY_FRAME` maps `cam_ceiling` / `cam_flange` to the sources.
- `vision_server.runner.tag_map` persists the tag map with `tag_map_to_json` / `tag_map_from_json`.
- `vision_server.runner.calibration`, `calibration_session`, `handeye_session` call the calibration and hand-eye code.
- `stream_overlay`, `camera_stream` draw with `overlay` and `modes`.
- Imports of `tagloc` happen inside methods, so the server modules load without numpy.

## CLI tools

| Command | Purpose |
|---|---|
| `python -m tagloc.cli.calibrate` | Camera calibration, interactive or from images |
| `python -m tagloc.cli.detect` | Detect tags, print poses, optional overlay |
| `python -m tagloc.cli.build_tagmap` | Survey a tag map from an image series |
| `python -m tagloc.cli.transform` | Convert poses between frames |
| `python -m tagloc.cli.calibrate_handeye` | Hand-eye calibration from samples |
| `python -m tagloc.cli.calibrate_handeye_offline` | Offline hand-eye evaluation |

Shared arguments live in `cli/_common.py`.

## Where to read more

| Topic | Document |
|---|---|
| Per-function reference | [code/cell-tagloc.md](../../code/cell-tagloc.md) |
| Concept, frames, tag map, pipeline | [topics/apriltag-localization.md](../../topics/apriltag-localization.md) |
| AprilTag functionality overview | [topics/apriltag-reference.md](../../topics/apriltag-reference.md) |
| Vision server context | [topics/vision-system.md](../../topics/vision-system.md) |
| Layer 2 rules | [topics/layer2-rules.md](../../topics/layer2-rules.md) |
| Vision server component | [../vision-server/README.md](../vision-server/README.md) |
| Layers overview | [../../layers.md](../../layers.md) |
| Installation and usage | [../../usage/installation.md](../../usage/installation.md), [../../usage/quickstart.md](../../usage/quickstart.md) |
