"""Profile-specific configuration.

Kept at package level so that `config.py` can import it without a detection
source (which needs OpenCV) ending up on the import chain of the cell server.
Stdlib types only: `tag_family` is a string and is only resolved to a
`cv2.aruco.DICT_*` constant in the source.

Never put a matrix in here. `K`/`D` are numpy, unhashable, and belong to
whatever `calibration_path` loads.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Machine-specific, hence under `data/` (excluded by .gitignore).
DEFAULT_CALIBRATION_PATH = _REPO_ROOT / "data" / "calibration" / "camera.json"
#: Cell layout, hence versioned under `config/`.
DEFAULT_TAG_MAP_PATH = _REPO_ROOT / "config" / "tagmap.json"

#: Hand-eye calibration per camera. It belongs to the hardware (camera rigidly
#: mounted on the robot), so like the calibration it lives under `data/` and is
#: not versioned. Only the hand Pi has one; the ceiling camera is not on the robot.
DEFAULT_HAND_EYE_PATH: Path | None = None


@dataclass(frozen=True)
class AprilTagProfileConfig:
    """Operating parameters of the AprilTag detection."""

    resolution: tuple[int, int] = (2028, 1520)
    calibration_path: Path = DEFAULT_CALIBRATION_PATH
    tag_map_path: Path | None = DEFAULT_TAG_MAP_PATH
    #: `None` = no hand-eye calibration. Then only the optical path applies:
    #: without a world tag in the image, the poses stay in the camera frame.
    hand_eye_path: Path | None = DEFAULT_HAND_EYE_PATH
    #: Same as `tagloc.tagmap.DEFAULT_TAG_FAMILY` (here as a literal: no numpy import).
    tag_family: str = "tag36h11"
    tag_size_m: float = 0.05
    capture_timeout_s: float = 5.0
    samples_per_job: int = 3
    max_reproj_error_px: float = 3.0
    frame_id: str = "cam_ceiling"
    frame_convention: str = "z_forward_x_right_y_down"
    #: "aruco" (default), "pupil" or "auto" -- see tagloc.detector.
    detector_backend: str = "aruco"
    #: `True` enables `cv2.aruco.CORNER_REFINE_APRILTAG` (subpixel corners) in the
    #: job detector (`AprilTagDetectionSource`). Since the detector split (commit
    #: 3d91ee1) it ONLY affects the job path. The live stream overlay gets its own
    #: instance with this refinement fixed in `runner.livestream._build_annotator`,
    #: independent of this field.
    #:
    #: Default `False`: live on pi1 it was confirmed that Layer 1 ("find modules")
    #: reliably finds tags even without the refinement. This saves ~46% CPU in the
    #: job thread (py-spy). It is kept as a switch (not deleted): an open idea is to
    #: use the refinement specifically for more accurate localization on a second
    #: detection layer or for calibration (see work plan
    #: `deckenkamera-volle-aufloesung.md`, open questions). Not decided yet, not
    #: implemented yet.
    subpixel_corner_refinement: bool = False
    #: Method used when `subpixel_corner_refinement` is active: `"apriltag"`
    #: (CORNER_REFINE_APRILTAG), `"subpix"` (CORNER_REFINE_SUBPIX, default) or
    #: `"none"`. The hand camera (`cam_flange`) has had the refinement on since
    #: 28.10.2026: a fixed offset when approaching also comes from rounded tag corners.
    corner_refinement_method: str = "subpix"
    #: Decimation factor for `cv2.aruco.DetectorParameters.
    #: aprilTagQuadDecimate`. It only has an effect if `subpixel_corner_refinement`
    #: is active (so it is currently inert, see there). Standard AprilTag
    #: parameter (taken over from the apriltag reference library): runs the
    #: AprilTag refinement search on a downscaled copy instead of the full frame.
    #: Much faster, but costs some range and robustness for very small or distant
    #: tags. `0.0` (default) = no decimation. Kept for the case that
    #: `subpixel_corner_refinement` is needed again later (Layer 2, calibration).
    apriltag_quad_decimate: float = 0.0
    #: Free choice of OpenCV `DetectorParameters`: attribute name -> value, e.g.
    #: `{"errorCorrectionRate": 0.8, "adaptiveThreshWinSizeMax": 33}`. Empty =
    #: OpenCV default. Only effective with the aruco backend. An unknown name or a
    #: value of the wrong type raises `ValueError` at startup
    #: (`tagloc.detector.apply_detector_parameters`). Job, stream overlay and cell
    #: watch use the same mapping, otherwise the overlay would show different tags.
    #: An explicit `cornerRefinementMethod` wins over `subpixel_corner_refinement`,
    #: but is removed for the overlay (see `runner.livestream._build_annotator`).
    #: Preset and override via `VISION_APRILTAG_DETECTOR_PARAMS`, see
    #: `server.apriltag_config`.
    #: `hash=False`: a dict is not hashable, but the config should stay hashable.
    detector_parameters: dict[str, Any] = field(default_factory=dict, hash=False)
    #: Scale intrinsics to the actual image size instead of aborting. Only
    #: enable when deliberately running at a resolution other than the one
    #: calibrated for.
    allow_resolution_mismatch: bool = False
    #: Deliberate emergency exit, not a permanent state: lets the source start
    #: without a real calibration file (roughly estimated intrinsics instead of
    #: Preoperational). This allows checking detector, overlay and job path before
    #: the real calibration drive (test plan section 3.3) is done. Poses are then
    #: plausibly oriented, but not metrically accurate. Set back to `False` before
    #: rollout, otherwise a missing real calibration never fails again.
    allow_placeholder_calibration: bool = False
    #: Geometry of the calibration board for `CalibrationSession`
    #: (`vision_server/calibration_session.py`). Scalars instead of a
    #: `tagloc.boards.BoardSpec` object, for the same reason as `tag_family`: this
    #: file stays free of a `tagloc` dependency. The object is only built where it
    #: is needed.
    calibration_board_type: str = "chessboard"
    calibration_board_cols: int = 9
    calibration_board_rows: int = 6
    calibration_board_square_size_m: float = 0.030
    calibration_board_marker_size_m: float = 0.022
    calibration_board_dictionary: str = "DICT_4X4_50"
    #: From how many captures on `FinishCalibration` even tries to compute (the
    #: CLI tool `tagloc.cli.calibrate` calls this `MIN_SAMPLES`).
    calibration_min_samples: int = 15
    #: Lens model with which `CalibrationSession` calibrates NEW:
    #: `"pinhole"` (Brown-Conrady, 5 coefficients) or `"fisheye"` (Kannala-Brandt,
    #: 4 coefficients), see `tagloc.lens`. String instead of a `tagloc` constant,
    #: for the same reason as `tag_family`. For pose estimation only the model in
    #: the loaded calibration file counts, not this field. An older pinhole file
    #: therefore still applies until it is recalibrated.
    calibration_model: str = "pinhole"
    #: If set, `CalibrationSession` additionally archives every capture
    #: permanently here, for checking outside the server. The naming convention is
    #: the same as for `tagloc.cli.calibrate --capture-to` (`kalib_001.jpg`,
    #: `kalib_002.jpg`, ...). `None` (default) archives nothing extra. The captures
    #: themselves always end up first in a session-own work folder
    #: (`CalibrationSession._pending_dir`), which is emptied at every
    #: `StartCalibration`. This field is only meant for the case that they should
    #: be kept permanently beyond that. It lands under `data/` like the calibration
    #: file (`.gitignore`).
    calibration_capture_dir: Path | None = None


#: Supported values for `CameraStreamConfig.backend`.
CAMERA_BACKENDS: tuple[str, ...] = ("picamera2", "opencv", "realsense")


@dataclass(frozen=True)
class CameraStreamConfig:
    """Operating parameters of the one camera that detection and live stream share.

    A single `SharedCamera` instance (see `camera.py`) is opened with these
    values. The detection source (`detection/apriltag.py`) and the node publisher
    (`camera_stream.py`) both read from it, instead of each opening its own camera
    handle. Both Picamera2/libcamera and RealSense allow only one open handle per
    camera at a time.

    `backend` selects the hardware interface, see `CAMERA_BACKENDS`:
    "picamera2" (Pi camera, e.g. ceiling Pi), "realsense" (Intel RealSense via
    `pyrealsense2`, e.g. hand Pi) or "opencv" (`cv2.VideoCapture`, generic
    fallback for development).
    """

    backend: str = "picamera2"
    #: `False` only suppresses the stream NODES, the publisher and the MJPEG server
    #: (see `address_space.py`, `runner.livestream._start_camera_stream`). The
    #: remaining fields here (especially `resolution`) stay in effect, because
    #: `detection/apriltag.py` uses the same `CameraStreamConfig` for the job camera
    #: (`SharedCamera`) too. Do NOT set the whole `CameraStreamConfig` object to
    #: `None` to switch off only the stream. That falls back to the bare default in
    #: `detection/__init__.py` (`config.camera_stream or CameraStreamConfig()`) and
    #: silently changes the job camera resolution as well (found live on pi1:
    #: 2028x1520 -> 1280x720, "aspect ratio changes").
    stream_enabled: bool = True
    camera_index: int = 0
    #: Picamera2/OpenCV only. RealSense has its own field (`realsense_resolution`),
    #: because the sensors -- especially over the RSUSB/libuvc backend connection
    #: needed on the Pi -- only support certain resolution/FPS combinations.
    #: `1280x720` is too bandwidth-hungry for that and makes `pipeline.start()` fail
    #: with "Couldn't resolve requests".
    resolution: tuple[int, int] = (1280, 720)
    #: Picamera2 only: a second, small image stream ("lores") from the same frame,
    #: scaled in the ISP -- practically no CPU load. The live stream and overlay
    #: compute on it; jobs and calibration keep working on `resolution`. `None` = no
    #: second stream. The stream then scales down itself (`max_stream_width`), as
    #: with RealSense and OpenCV.
    preview_resolution: tuple[int, int] | None = None
    #: Picamera2 only: number of camera buffers. `None` = Picamera2 default (6 for
    #: video). At 12 MP that would be ~220 MB of CMA memory, more than the Pi
    #: reserves by default, so `configure()` then fails.
    buffer_count: int | None = None
    #: Picamera2 only: libcamera controls that are set at startup, e.g.
    #: `{"ExposureValue": -0.5, "Sharpness": 1.5, "NoiseReductionMode": "Fast"}`.
    #: Enum controls may be given by name (`camera.resolve_camera_controls`).
    #: Unknown names raise `ValueError` on open. Empty = Picamera2 default.
    #: RealSense/OpenCV ignore this field (warning if it is set). Preset and
    #: override via `VISION_CAMERA_CONTROLS`, see `server.py`.
    #: `hash=False`: a dict is not hashable, but the config should stay hashable.
    camera_controls: dict[str, Any] = field(default_factory=dict, hash=False)
    warmup_s: float = 2.0
    #: How often the `SharedCamera` captures. Upper limit for the HTTP stream.
    capture_fps: float = 15.0
    #: Rate of the OPC UA node `LatestCameraFrame`. Deliberately low: it is only
    #: the fallback path now, and base64 over OPC UA and backend is not suitable
    #: for video. The live image comes via `http_port`.
    stream_fps: float = 5.0
    #: Chosen conservatively so that the pipeline also starts reliably over the
    #: RSUSB backend connection (needed because the Pi kernel has no usable UVC
    #: driver for RealSense). Raise it if needed, once the error message of
    #: `_open_realsense` shows the profiles the connected camera actually supports.
    #: Since 10.11.2026 the full resolution of the color sensor (D415: 1920x1080,
    #: 16:9, full field of view) is the default for all branches. 640x480 was a 4:3
    #: crop (1440x1080, scaled down by 2.25, horizontal 55 instead of 70 degrees).
    #: Over USB 2 the D415 offers 1920x1080 only at 8 Hz; with a USB 3 cable
    #: 6/15/30 Hz -- then adjust `realsense_fps`. The intrinsics of the hand camera
    #: must match this resolution.
    realsense_resolution: tuple[int, int] = (1920, 1080)
    #: Native capture frame rate of the RealSense pipeline. Independent of
    #: `stream_fps`, because the sensors only support certain fps values per
    #: resolution (typically 6/15/30/60). `stream_fps` remains the cadence with
    #: which the `SharedCamera` fetches the newest frame.
    realsense_fps: int = 8
    jpeg_quality: int = 70
    #: Maximum image width in the stream. Wider frames are scaled down before the
    #: JPEG encode (for the stream only -- detection and calibration keep working on
    #: the full camera frame). Without this, e.g. cam_ceiling (2028x1520) costs one
    #: encode of a ~3 MP image per tick on the Pi, and the frame rate dropped
    #: noticeably. `None` disables the scaling.
    max_stream_width: int | None = 960
    #: Port of the MJPEG stream (`http://<pi>:<port>/stream.mjpg`); 0 = off. The
    #: frontend reads it from `http_port_node_name` and builds the URL from the
    #: address under which it reaches the OPC UA server.
    http_port: int = 8080
    #: Frame rate of the MJPEG stream while at least one viewer is connected.
    http_fps: float = 15.0
    http_port_node_name: str = "CameraStreamHttpPort"
    node_name: str = "LatestCameraFrame"
    #: Writable node through which the frontend selects the overlay mode.
    mode_node_name: str = "CameraStreamMode"
    #: Initial value; valid values are "off", "apriltag", "calibration"
    #: (tagloc.overlay).
    overlay_mode: str = "apriltag"
    #: Minimum gap between two detection runs for the overlay. The stream is
    #: meant to help debugging, not load the Pi's CPU -- between runs, the
    #: last result is redrawn. Since 21.10.2026 this applies only to the mode
    #: "calibration"; for "apriltag" see `overlay_detect_interval_s`.
    overlay_interval_s: float = 0.5
    #: Mode "apriltag": the overlay shows the newest result in full resolution from
    #: the job, cell watch or measurement. If that result is older than this value,
    #: the overlay itself detects in full resolution -- in the background, and not
    #: during a job or calibration. On the Pi, a run on 12 MP costs roughly 1 s. At
    #: 5 s that is about 20% of one core, instead of the ~100% that a detection every
    #: 0.5 s cost. `0` = never detect itself, only show results from others.
    overlay_detect_interval_s: float = 5.0
    #: Watchdog: a single `capture_array()` may take at most this long. After that
    #: the camera counts as hung. Otherwise Picamera2 waits forever for a frame that
    #: libcamera never delivers (Pi 1, 2026-09-22).
    frame_timeout_s: float = 3.0
    #: So many capture errors in a row before the camera is reopened. A timeout
    #: counts fully at once: the worker hangs, and every further call would only
    #: queue up behind it.
    max_capture_failures: int = 3
    #: So many reopens without a single frame in between before the process exits
    #: and systemd (`Restart=always`) restarts it.
    max_reopen_attempts: int = 2
    #: Capture side: from this age on, the last image counts as stale and
    #: `DeviceHealth` reports OFF_SPEC (camera_health.py). This affects **only** the
    #: state assessment. The live stream keeps publishing regardless; whether the
    #: camera is alive is said by the asset view, not by the image.
    stale_frame_s: float = 2.0
    #: An overlay run may not take longer than this, otherwise the raw image goes out.
    overlay_timeout_s: float = 2.0
    #: Cadence of the state query for `DeviceHealth` (camera_health.py). It is not
    #: picked arbitrarily but derived from the narrowest state window: OFF_SPEC
    #: applies as soon as the frame is older than `stale_frame_s`, and ends when the
    #: watchdog escalates after `frame_timeout_s`. With the default values that is
    #: 3.0 - 2.0 = 1.0 s. Sampled at 1 Hz it would often be missed; half as long
    #: hits at least once. It costs nothing: the query only reads counter values,
    #: and writes happen only on change.
    health_interval_s: float = 0.5


@dataclass(frozen=True)
class AssetConfig:
    """What this vision system consists of -- Part 2 (AMCM).

    Pure master data. Like the other profiles, it uses only stdlib types and is
    hashable. What changes at runtime (CPU temperature, camera state) is not
    stored here. `asset_model` reads it from the running objects instead.
    """

    manufacturer: str = "TU Darmstadt PLCM"
    model: str = "Flexible Roboterzelle -- Vision"
    serial_number: str = ""
    software_revision: str = "0.1.0"
    #: The computing unit on which this server runs.
    computing_device_model: str = "Raspberry Pi"
    #: Camera module and lens. Leave empty what is not known: an invented value
    #: would be worse than an empty field.
    image_sensor_model: str = ""
    lens_model: str = ""
