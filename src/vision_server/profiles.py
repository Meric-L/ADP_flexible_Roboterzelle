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
    #: `None` = no hand-eye calibration; poses then stay in the camera frame unless a world tag is visible.
    hand_eye_path: Path | None = DEFAULT_HAND_EYE_PATH
    #: Same as `tagloc.tagmap.DEFAULT_TAG_FAMILY` (a literal here: no numpy import).
    tag_family: str = "tag36h11"
    tag_size_m: float = 0.05
    capture_timeout_s: float = 5.0
    samples_per_job: int = 3
    max_reproj_error_px: float = 3.0
    frame_id: str = "cam_ceiling"
    frame_convention: str = "z_forward_x_right_y_down"
    #: "aruco" (default), "pupil" or "auto" -- see tagloc.detector.
    detector_backend: str = "aruco"
    #: `True` enables corner refinement in the job detector only; the live overlay
    #: has its own fixed setting (`runner.livestream._build_annotator`). Default
    #: `False`: tags are found reliably without it, and it saves ~46% job CPU.
    subpixel_corner_refinement: bool = False
    #: Method used when `subpixel_corner_refinement` is on: `"apriltag"`,
    #: `"subpix"` (default) or `"none"`.
    corner_refinement_method: str = "subpix"
    #: Decimation of the AprilTag refinement search (`aprilTagQuadDecimate`): faster,
    #: but less range for small or distant tags. `0.0` = off. Only effective with
    #: `subpixel_corner_refinement`.
    apriltag_quad_decimate: float = 0.0
    #: OpenCV `DetectorParameters` overrides (attribute name -> value), e.g.
    #: `{"errorCorrectionRate": 0.8}`; empty = OpenCV default; aruco backend only.
    #: An unknown name or wrong type raises `ValueError` at startup. Job, overlay and
    #: cell watch share the mapping. An explicit `cornerRefinementMethod` wins over
    #: `subpixel_corner_refinement` (but not in the overlay). Env override:
    #: `VISION_APRILTAG_DETECTOR_PARAMS`. `hash=False`: a dict is unhashable.
    detector_parameters: dict[str, Any] = field(default_factory=dict, hash=False)
    #: Detect tags only in this rectangle `(x, y, width, height)` in pixels of
    #: `resolution`; `None` = whole image. Corners stay in full-frame coordinates, so
    #: calibration and poses are unaffected; tags outside (world tags too) are not
    #: found. Env override: `VISION_DETECTION_ROI`.
    detection_roi: tuple[int, int, int, int] | None = None
    #: Scale intrinsics to the actual image size instead of aborting. Only
    #: enable when deliberately running at a resolution other than the one
    #: calibrated for.
    allow_resolution_mismatch: bool = False
    #: Emergency exit: start without a calibration file, using roughly estimated
    #: intrinsics. Poses are plausibly oriented but not metric. Set back to `False`
    #: before rollout.
    allow_placeholder_calibration: bool = False
    #: Geometry of the calibration board for `CalibrationSession`, as scalars (no
    #: `tagloc` import here); see `calibration_board_spec()`.
    calibration_board_type: str = "chessboard"
    calibration_board_cols: int = 9
    calibration_board_rows: int = 6
    calibration_board_square_size_m: float = 0.030
    calibration_board_marker_size_m: float = 0.022
    calibration_board_dictionary: str = "DICT_4X4_50"
    #: Minimum number of captures for `FinishCalibration` to compute.
    calibration_min_samples: int = 15
    #: Lens model for NEW calibrations: `"pinhole"` (Brown-Conrady) or `"fisheye"`
    #: (Kannala-Brandt). Pose estimation uses the model in the loaded calibration file.
    calibration_model: str = "pinhole"
    #: If set, every calibration capture is also archived here (`kalib_001.jpg`, ...);
    #: `None` = no extra archive. Lands under `data/` (gitignored).
    calibration_capture_dir: Path | None = None

    def calibration_board_spec(self) -> Any:
        """Return the configured calibration board as a `tagloc.boards.BoardSpec`.

        The one place that turns the scalar `calibration_board_*` fields into a
        board, for the calibration session, the `calibration` stream overlay and
        `MeasureBoard`. `tagloc` is imported only here, at call time.
        """
        from tagloc.boards import BoardSpec

        return BoardSpec(
            type=self.calibration_board_type,
            cols=self.calibration_board_cols,
            rows=self.calibration_board_rows,
            square_size_m=self.calibration_board_square_size_m,
            marker_size_m=self.calibration_board_marker_size_m,
            dictionary=self.calibration_board_dictionary,
        )


#: Supported values for `CameraStreamConfig.backend`.
CAMERA_BACKENDS: tuple[str, ...] = ("picamera2", "opencv", "realsense")


@dataclass(frozen=True)
class CameraStreamConfig:
    """Operating parameters of the one camera that detection and live stream share.

    One `SharedCamera` (see `camera.py`) is opened with these values and read by both
    the detection source and the node publisher, because Picamera2 and RealSense allow
    only one open handle per camera.

    `backend` selects the hardware interface, see `CAMERA_BACKENDS`:
    "picamera2" (Pi camera, e.g. ceiling Pi), "realsense" (Intel RealSense via
    `pyrealsense2`, e.g. hand Pi) or "opencv" (`cv2.VideoCapture`, generic
    fallback for development).
    """

    backend: str = "picamera2"
    #: `False` only suppresses the stream nodes, publisher and MJPEG server; the other
    #: fields (especially `resolution`) still configure the job camera. Never set the
    #: whole `CameraStreamConfig` to `None` for this: the fallback default would silently
    #: change the job camera resolution.
    stream_enabled: bool = True
    camera_index: int = 0
    #: Picamera2/OpenCV only; RealSense uses `realsense_resolution`.
    resolution: tuple[int, int] = (1280, 720)
    #: Picamera2 only: second small ISP-scaled stream ("lores") on which live stream and
    #: overlay compute; jobs and calibration use `resolution`. `None` = no second stream
    #: (the stream then scales down itself, see `max_stream_width`).
    preview_resolution: tuple[int, int] | None = None
    #: Picamera2 only: number of camera buffers; `None` = Picamera2 default (6). At 12 MP
    #: that exceeds the Pi's default CMA memory, so `configure()` fails.
    buffer_count: int | None = None
    #: Picamera2 only: libcamera controls set at startup, e.g.
    #: `{"ExposureValue": -0.5, "NoiseReductionMode": "Fast"}`; enum controls by name.
    #: Unknown names raise `ValueError`; empty = default; other backends ignore it
    #: (with a warning). Env override: `VISION_CAMERA_CONTROLS`. `hash=False`: a dict
    #: is unhashable.
    camera_controls: dict[str, Any] = field(default_factory=dict, hash=False)
    warmup_s: float = 2.0
    #: How often the `SharedCamera` captures. Upper limit for the HTTP stream.
    capture_fps: float = 15.0
    #: Rate of the OPC UA node `LatestCameraFrame`. Low on purpose: base64 over OPC UA
    #: is only a fallback; the live image comes via `http_port`.
    stream_fps: float = 5.0
    #: RealSense color resolution; must match the hand camera's intrinsics. The Pi's
    #: RSUSB backend supports only certain resolution/FPS combinations (1280x720 fails
    #: with "Couldn't resolve requests"). Over USB 2 the D415 gives 1920x1080 only at
    #: 8 Hz; with USB 3 adjust `realsense_fps`.
    realsense_resolution: tuple[int, int] = (1920, 1080)
    #: Native RealSense pipeline frame rate (typically 6/15/30/60 per resolution),
    #: independent of `stream_fps`.
    realsense_fps: int = 8
    jpeg_quality: int = 70
    #: Maximum stream image width; wider frames are scaled down before the JPEG encode
    #: (stream only, detection keeps the full frame). `None` = no scaling.
    max_stream_width: int | None = 960
    #: Port of the MJPEG stream (`http://<pi>:<port>/stream.mjpg`); 0 = off. Published
    #: in `http_port_node_name` so the frontend can build the URL.
    http_port: int = 8080
    #: Frame rate of the MJPEG stream while a viewer is connected.
    http_fps: float = 15.0
    http_port_node_name: str = "CameraStreamHttpPort"
    node_name: str = "LatestCameraFrame"
    #: Writable node through which the frontend selects the overlay mode.
    mode_node_name: str = "CameraStreamMode"
    #: Initial overlay mode: "off", "apriltag", "undistorted" or "tagreference"
    #: (`tagloc.modes`).
    overlay_mode: str = "apriltag"
    #: Mode "apriltag": if the newest job/watch/measurement result is older than this,
    #: the overlay detects itself at full resolution in the background (never during a
    #: job or calibration). `0` = never, only show results from others.
    overlay_detect_interval_s: float = 5.0
    #: Watchdog: a single `capture_array()` may take at most this long, then the camera
    #: counts as hung.
    frame_timeout_s: float = 3.0
    #: Consecutive capture errors before the camera is reopened; a timeout counts fully.
    max_capture_failures: int = 3
    #: Reopens without a frame in between before the camera counts as failed; it then
    #: keeps retrying every `failed_retry_interval_s`.
    max_reopen_attempts: int = 2
    #: Pause between reopen attempts once the camera counts as failed (budget exhausted).
    failed_retry_interval_s: float = 5.0
    #: How long the camera may stay failed without a single frame before the process exits
    #: and systemd restarts it (last resort against a wedged libcamera/driver that only a
    #: fresh process clears). `0` = exit at once when the budget is exhausted, `None` =
    #: never exit.
    give_up_after_s: float | None = 300.0
    #: Image age from which `DeviceHealth` reports OFF_SPEC (`camera_health.py`). Affects
    #: only the state assessment, not the stream.
    stale_frame_s: float = 2.0
    #: An overlay run may not take longer than this, otherwise the raw image goes out.
    overlay_timeout_s: float = 2.0
    #: Cadence of the `DeviceHealth` query. Must stay below the narrowest state window
    #: (`frame_timeout_s - stale_frame_s`, 1.0 s by default), so it is sampled at least
    #: once; half of that is used.
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
