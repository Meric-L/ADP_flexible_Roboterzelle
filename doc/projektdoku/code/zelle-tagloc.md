# Zelle: tagloc library, CLI, jobs, use-case eval, UA program

Detailed reference for the code in the ADP repository (branch `apriltag-aufraeumen`). Overview and system picture: [code-dokumentation.md](../code-dokumentation.md).


Scope: `src/tagloc/` (incl. `cli/`), `src/jobs/`, `src/use_case_eval/`, `src/ua_program/`, and the repo root files. Paths are relative to the repository root. Project-wide overviews: `doc/projektdoku/code-dokumentation.md` (per-file handbook), `doc/projektdoku/apriltag-referenz.md` (functions, CLI, file formats), `doc/projektdoku/apriltag-lokalisierung.md` (concept).

Layering (from `src/tagloc/__init__.py`): `geometry`, `calibration`, `tagmap`, `localize` use numpy only; `boards`, `detector`, `pose`, `overlay`, `frames` use cv2; `cli/*` are thin adapters. `identity`, `jsonio`, `modes` and `observations` are standard library or numpy-light and are imported by the vision server without OpenCV.

### `src/tagloc/__init__.py`
- Package docstring only: describes the layering above. No exports.
- Details: `doc/projektdoku/apriltag-lokalisierung.md`.

### `src/tagloc/boards.py`
- Calibration boards (chessboard, ChArUco) and their detection, coverage and pose.
- Entry points: `BoardSpec`, `BoardSample`, `build_board`, `detect_board`, `detect_partial_board`, `diagnose_board`, `compute_coverage`, `estimate_board_pose`, `calibrate_from_samples`.
- Input: grayscale image plus `BoardSpec` (geometry is a parameter, not a module global). Output: samples and `CameraCalibration`-ready point sets.
- Details: `doc/projektdoku/arbeitsplaene/handauge-charuco-auswahl.md`, `doc/projektdoku/code-dokumentation.md`.

### `src/tagloc/calibration.py`
- Camera calibration as JSON (no OpenCV needed to read it); one file per physical camera.
- Entry points: `CameraCalibration`, `save_calibration`, `load_calibration`, `default_calibration`, `check_resolution`, `scale_to_resolution`.
- Input/output: `data/calibration/<frame>.json` in, `CameraCalibration` out. `_checked_model` validates the lens model.
- Details: `doc/projektdoku/arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md`.

### `src/tagloc/cli/__init__.py`
- Docstring only: lists the four CLI modules (`calibrate`, `detect`, `transform`, `build_tagmap`). No code.

### `src/tagloc/cli/_common.py`
- Shared CLI helpers: logging setup, `--source` and detector argument groups, `open_source`, calibration and tag-map loading with exit messages, JSON output helpers.
- Entry points: `add_source_arguments`, `add_detection_arguments`, `open_source`, `load_inputs`, `fit_calibration`, `tag_poses_to_json`, `poses_from_json`, `write_json`.
- Error messages name the next command to run (`SystemExit`), not only the missing file.
- Details: `doc/projektdoku/apriltag-referenz.md` (CLI section).

### `src/tagloc/cli/calibrate.py`
- Camera calibration, interactive (`SPACE` capture, `C` compute and save, `Q` quit) or headless from an image folder.
- Entry points: `build_parser`, `main(argv=None) -> int`. Options include `--board`, `--out`, `--frame-id`, `--model` (pinhole or fisheye), `--capture-to`.
- Output: calibration JSON at `--out`; `--capture-to` only saves images.
- Details: `doc/projektdoku/apriltag-referenz.md`, `doc/projektdoku/apriltag-e2e-test.md`.

### `src/tagloc/cli/calibrate_handeye.py`
- Hand-eye calibration (flange to camera, `T_flange_cam`), solved from a recorded session folder.
- Entry points: `build_parser`, `load_samples`, `solve_session(path, *, estimate_scale, out)`, `main`.
- Input: session folder with raw captures and robot poses (`load_samples`). Prints a refinement summary; `solve_session` takes an optional `out` path.
- Details: `doc/projektdoku/arbeitsplaene/handeye-genauigkeit.md`, `doc/projektdoku/apriltag-referenz.md`.

### `src/tagloc/cli/calibrate_handeye_offline.py`
- Re-runs the offline evaluation of a hand-eye session on its stored raw images (`main(argv)`).
- Calls `tagloc.handeye_offline.run_experiments`. Writes results and an HTML comparison report into the session folder.
- Details: `doc/projektdoku/arbeitsplaene/handauge-schachbrett-rohdaten-offline.md`.

### `src/tagloc/cli/detect.py`
- Detects AprilTags live (camera) or in saved images. Prints per-tag ID, pose (quaternion xyzw), reprojection error and ambiguity.
- Entry points: `build_parser`, `main(argv=None) -> int`. Options include `--calibration`, `--tag-map`, `--overlay`, `--json`.
- Details: `doc/projektdoku/apriltag-referenz.md`.

### `src/tagloc/cli/transform.py`
- Pure coordinate transform: a single pose or a whole `detect` result file, with a camera pose from a fixed-pose JSON or from reference tags (`--from-reference`).
- Entry points: `build_parser`, `_load_transform`, `main(argv=None) -> int`.
- Details: `doc/projektdoku/apriltag-referenz.md`.

### `src/tagloc/cli/build_tagmap.py`
- Places several AprilTags in one shared frame from a capture series. Requires every image to show at least two tags and the pairs to form a chain to the anchor tag.
- Entry points: `build_parser`, `main(argv=None) -> int`. Options include `--source`, `--calibration`, `--anchor`, `--out`.
- Output: tag-map JSON (default target `config/tagmap.json`).
- Details: `doc/projektdoku/apriltag-referenz.md`, `doc/projektdoku/arbeitsplaene/apriltag-tagmap-robustheit.md`.

### `src/tagloc/detector.py`
- Tag detection behind one interface (`TagDetector` Protocol). Backends: `ArucoTagDetector` (`cv2.aruco`, default) and `PupilAprilTagDetector` (optional `pupil_apriltags`).
- Entry points: `build_detector(family, backend="aruco", *, ...)`, `predefined_dictionary`, `aruco_dictionary`, `apply_detector_parameters`.
- Input: grayscale image. Output: `TagObservation` list. Pose estimation is deliberately not here (see `pose.py`).
- Details: `doc/projektdoku/arbeitsplaene/kamera-controls-und-detektor-parameter.md`.

### `src/tagloc/frames.py`
- Image sources: `ImageFolderSource`, `SingleImageSource`, `VideoCaptureSource`, `PiCameraSource` (picamera2), `RealSenseSource`. One `FrameSource` Protocol.
- Entry point: `open_source(spec, *, resolution, loop)`. `--source` is a camera or a folder, so the same code runs with and without hardware.
- `cv2` and `picamera2` are imported only when a source is opened.
- Details: `doc/projektdoku/code-dokumentation.md`.

### `src/tagloc/geometry.py`
- Poses, composition and quaternions in numpy only. A pose is a `(4, 4)` float64 array; `T_a_b` is the pose of b in a.
- Conventions: metres, radians, quaternions in xyzw.
- Entry points: `from_rvec_tvec`, `to_rvec_tvec` (Rodrigues in numpy), `compose`, `invert`, `average_poses`, `quaternion_from_rotation`, `rotation_from_quaternion`, `pose_to_dict`, `pose_from_dict`.
- Details: `doc/projektdoku/apriltag-referenz.md` (coordinate conventions).

### `src/tagloc/handeye.py`
- Hand-eye math: `T_flange_cam` is a hardware constant, calibrated once. Carries the pose from a world tag seen at the start of a run through the robot kinematics.
- Entry points: `HandEye`, `solve_hand_eye`, `solve_hand_eye_robust`, `refine_hand_eye`, `refine_hand_eye_robust`, `refine_board_session`, `leave_one_out_errors`, `load_session`, `load_hand_eye`, `save_hand_eye`, `session_payload`.
- Input: session JSON with robot flange poses and camera-to-board poses. Output: `HandEye` and `HandEyeSolution`, saved as JSON.
- Details: `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md`, `doc/projektdoku/arbeitsplaene/handeye-genauigkeit.md`.

### `src/tagloc/handeye_offline.py`
- Offline evaluation of a raw-image session without camera or robot: re-detects boards, solves intrinsics and hand-eye poses, writes results and an HTML report.
- Entry points: `calibrate_session`, `load_observations`, `solve_subset`, `diverse_subsets`, `run_experiments`.
- Details: `doc/projektdoku/arbeitsplaene/handauge-schachbrett-rohdaten-offline.md`.

### `src/tagloc/identity.py`
- Identity string of the active calibration and tag map: file name plus mtime (`calibration_identity`, `tag_map_identity`). Standard library only.
- Used by the vision server to build `configurationId` before OpenCV or numpy are loaded. A missing file yields `fehlt` (German, from the code).
- Details: 

### `src/tagloc/jsonio.py`
- JSON helpers: `finite_or_none` (NaN and inf become `None`), `utc_now_z`, `utc_stamp`. Standard library only.

### `src/tagloc/lens.py`
- The only module that calls OpenCV lens functions. It dispatches on `CameraCalibration.model` (pinhole or fisheye).
- Entry points: `undistort_points`, `project_points`, `undistortion_maps`, `calibrate`.
- Why it exists: calling a fisheye function with pinhole coefficients (or the reverse) does not raise an error and returns wrong points.
- Details: `doc/projektdoku/arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md`.

### `src/tagloc/localize.py`
- The only place where `T_world_module = T_world_cam @ T_cam_tag @ T_tag_module` is evaluated. Pure numpy.
- Entry points: `localize_camera`, `camera_pose_from_reference_tags`, `locate_modules`, `merge_locations`, `anchor_from_localization`, `localize_camera_from_anchor`, `anchor_drift`, `world_tag_for_robot`, `expected_but_missing`.
- Input: `TagPose` lists and a `TagMap`. Output: `CameraLocalization`, `ModuleLocation` lists.
- Details: `doc/projektdoku/apriltag-lokalisierung.md`, `doc/projektdoku/arbeitsplaene/apriltag-welttag-konzept.md`.

### `src/tagloc/modes.py`
- Overlay modes of the live stream: `OVERLAY_MODES` (`off`, `apriltag`, `calibration`, `undistorted`, `tagreference`), `DEFAULT_OVERLAY_MODE = "apriltag"`, `normalise_mode`. Standard library only.
- `tagloc.overlay` re-exports them.

### `src/tagloc/observations.py`
- Dataclasses between image and math layers: `TagObservation` (raw detection) and `TagPose` (with pose and quality). No OpenCV.
- Re-exported by `detector` and `pose`.

### `src/tagloc/overlay.py`
- Draws detection results on images. `draw_tag_overlay` (outline, axis cross, ID) for the CLI; `draw_tag_boxes` (green box and ID) for the live stream.
- Entry points: `draw_tag_overlay`, `draw_tag_boxes`, `draw_board_overlay`, `draw_calibration_caption`, `draw_status_bar`, `Window`, `has_display`, `summarise`.
- Details: `doc/projektdoku/vision-server-interface.md` (stream overlay modes).

### `src/tagloc/pose.py`
- From image corners to `T_cam_tag` with quality measures. Corners are undistorted first, then solved with a pinhole model. Ambiguity comes from `solvePnPGeneric`.
- Entry points: `estimate_tag_pose`, `estimate_tag_poses`, `undistort_corners`, `solve_camera_from_world_tags` (returns `JointCameraPose`), `poses_by_tag`.
- Details: `doc/projektdoku/apriltag-referenz.md`.

### `src/tagloc/reference_edges.py`
- Measures a module's tag position from reference edges (table edge, profile end face) in the hand-camera image, correcting `T_module_tag` in the tag plane (x, y, rotation about z).
- Entry points: `measure_reference_lines`, `fit_in_plane_correction`, `average_corrections`, `plan_reference_view`, `catalog_rpy_deg`. Data types: `ReferenceLine`, `LineMeasurement`, `InPlaneCorrection`, `ViewPlan`.
- Uses RANSAC line fits on gradient maxima. `debug=` returns intermediate results for `reference_overlay`.
- Details: `doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md`.

### `src/tagloc/reference_overlay.py`
- Drawing only, for the debug output of `reference_edges.measure_reference_lines`. Colors and layers are documented in the module.
- Entry points: `draw_reference_debug`, `effective_layers`, `scale_debug`.

### `src/tagloc/tagmap.py`
- Tag map: which tag ID is which module, and where each tag sits in the shared frame. World tags are fixed; modules and the robot are measured.
- Entry points: `TagMap`, `TagEntry`, `load_tag_map`, `save_tag_map`, `tag_map_from_json`, `validate_tag_map`, `place_tags`, `residuals`, `with_world_poses`, `nearest_world_tag`.
- Details: `doc/projektdoku/apriltag-referenz.md` (file format), `doc/projektdoku/apriltag-lokalisierung.md`.

### `src/jobs/calibrate.py`
- OPC UA job `calibration`: a readiness check, not a calibration. Run as a subprocess by `ScriptDetectionSource`; stdout becomes `attributes.message`.
- Entry points: `main() -> str`, `_frame_id()`. Frame ID comes from `VISION_FRAME_ID`, else from the hostname (`FRAME_BY_HOST`), else `world`.
- Reads `data/calibration/<frame>.json` and `config/tagmap.json` under the repo root. Reports identity, resolution, model, RMS, sample count, age, and whether a tag map exists.
- Wired in `src/vision_server/detection/__init__.py` (`JOBS_DIR`). Details: `doc/projektdoku/vision-server-interface.md` (recipe `calibration`).

### `src/use_case_eval/__init__.py`
- Package docstring only: image measurement for the use-case dice tray. No exports.

### `src/use_case_eval/target.py`
- Single source of geometry for target sheet v2 and the cube marker, in mm. numpy only.
- Entry points: `marker_corners`, `sheet_marker_corners`, `cube_marker_corners`, `cube_corners_xy`, `rotation_2d`, `zone_for_radius`.
- Constants: `ARUCO_FAMILY`, `SHEET_MARKERS` (IDs 0-3), `CUBE_MARKER_ID` (10), `CIRCLES` and `ZONE_RADII_MM` (zones `gruen`, `gelb`, `rot`, else `ausserhalb`).
- Used by `tools/make_use_case_target.py`, `measure.py` and `synthetic.py`.
- Details: `doc/projektdoku/arbeitsplaene/use-case-messung.md`.

### `src/use_case_eval/measure.py`
- Measures the cube placement in a photo: deviation `dx`, `dy`, `dtheta` from the target centre. Sheet pose via `cv2.solvePnP` from the sheet markers; cube corners cast as rays onto the cube-top plane.
- Entry points: `measure_trial(image, calibration, sticker_offset_mm, reference=None)` returning `TrialMeasurement`, `annotate`, `summarize`, `sticker_offset_from_pair`.
- Image points are undistorted through `tagloc.lens`.
- Details: `doc/projektdoku/arbeitsplaene/use-case-messung.md`.

### `src/use_case_eval/live.py`
- Hook for `SaveUseCaseImage` (`src/vision_server/runner/save_use_case_image.py`). Never raises: errors become `{"ok": false, "grund": ...}` in the trial JSON.
- Entry points: `measure_for_save`, `load_sticker_offset` and `write_sticker_offset` (file `aufkleber_versatz.json`), `intrinsics_meta`, `calibration_from_meta`.
- Details: `doc/projektdoku/arbeitsplaene/use-case-foto.md`.

### `src/use_case_eval/synthetic.py`
- Synthetic test images with known ground truth (target, cube marker, sheet markers), rendered through the calibration model.
- Entry points: `make_calibration`, `look_down_pose`, `render_trial`, `render_tag_scene`.
- Used by tests and development only.

### `src/ua_program/__init__.py`
- Re-exports `Program`, `ProgramException`, `ProgramStateMachine` from `program.py`.
- Used by `src/vision_server/vision_program.py`.

### `src/ua_program/program.py`
- Minimal OPC UA Part 10 program framework on `asyncua`: `Program` (abstract base with start, halt, reset, suspend, resume methods), `ProgramStateMachine`, `ProgramException`.
- Based on the supervisor's template; the module docstring lists the changes made to it.
- Details: `doc/projektdoku/part10-programm-schnittstelle.md`.

### `README.md`
- Project overview (German) and repository layout. Links to the technical docs under `doc/projektdoku/`.
- Lists `src/tagloc`, `src/vision_server`, `src/ua_program`, `src/jobs`, `deploy`, `tools`, `config`, `hardware`, `data`, `tests`. Does not list `src/use_case_eval`.
- Test command: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

### `requirements.txt`
- Python dependencies: `opencv-contrib-python>=4.8.0,<5`, `scipy`, `numpy`, `asyncua>=1.1.5`, `zeroconf>=0.131` (optional at runtime), `tzdata`, and `pupil-apriltags` (optional second detector backend).
- Not listed on purpose: `picamera2` (installed with apt on the ceiling Pi) and `pyrealsense2` (hand Pi, manual install). Comments in the file explain why.

### `setup.sh`
- One-time setup after clone (Bash): adds the git remote `sharelatex` for the `doc/` subtree and two git aliases, `pushall` and `pullsl`.
- Idempotent for the remote (skips if it exists). Echo texts are German.
- Details: `GIT_ANLEITUNG.md`.

### `setup.bat`
- Windows equivalent of `setup.sh`: same remote and aliases, using `git remote get-url` to check for the remote. Ends with `pause`.
- Echo texts are German.

### `.gitignore`
- Ignores `.venv/`, `__pycache__/`, `*.pyc`, `*.log`, `*.jpg`, `*.txt`, `*.bat`, `*.sh`, with exceptions for `requirements.txt`, `setup.sh`, `setup.bat`.
- Excludes `.claude/worktrees/` and `.claude/settings.local.json`. Ignores `data/calibration/**` and `data/handeye/**` except `cam_flange.json` in each; ignores `data/tagreference/` entirely.

