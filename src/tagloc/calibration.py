"""Camera calibration as data: read, write, validate. No OpenCV required.

JSON is used instead of the OpenCV FileStorage YAML format, for these reasons:

* It can be read without ``cv2``. The vision server builds the ``configurationId``
  without loading OpenCV, and the tests run everywhere.
* It has room for ``rms``, the image size and the board geometry. The prototype
  (``src/apriltag/calibrate_camera.py``, removed on 2026-10-30) only printed ``rms``
  to stdout and wrote ``image_width``/``image_height`` without ever reading them back.
* It can be diffed and read by hand when something fails.

A calibration file belongs to **one physical camera** and is not part of the repo.
It lives under ``data/``, excluded by ``.gitignore``. Every Pi creates its own.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .identity import calibration_identity

SCHEMA = "wsc.vision.calibration/2"
#: Predates the lens-model field (2026-09-29). Still read, and treated as
#: ``PINHOLE``, the only model at that time. Only ``SCHEMA`` is written now, so an old
#: code version rejects a new file loudly instead of silently reading fisheye
#: coefficients as pinhole.
LEGACY_SCHEMA = "wsc.vision.calibration/1"

#: OpenCV standard model (Brown-Conrady), 5 coefficients k1, k2, p1, p2, k3.
PINHOLE = "pinhole"
#: OpenCV fisheye model (Kannala-Brandt), 4 coefficients k1..k4.
FISHEYE = "fisheye"
DISTORTION_MODELS = (PINHOLE, FISHEYE)

#: Rough assumption for ``default_calibration``. A typical value for Pi camera and
#: RealSense color sensors, but not the actual field of view of a specific camera.
#: A starting point only, not a measurement.
DEFAULT_HORIZONTAL_FOV_DEG = 70.0
PLACEHOLDER_CALIBRATION_ID = "placeholder-unkalibriert"


@dataclass(frozen=True)
class CameraCalibration:
    """Intrinsics and distortion of a camera, with provenance."""

    camera_matrix: np.ndarray
    distortion: np.ndarray
    image_size: tuple[int, int]
    frame_id: str = ""
    calibration_id: str = ""
    rms_reprojection_error: float = float("nan")
    sample_count: int = 0
    board: dict[str, Any] = field(default_factory=dict)
    #: Lens model that ``distortion`` belongs to (``PINHOLE`` or ``FISHEYE``).
    #: Decides which OpenCV functions undistort and project; see ``tagloc.lens``.
    #: The coefficients alone do not identify the model, so it is stored explicitly.
    model: str = PINHOLE

    @property
    def camera_params(self) -> tuple[float, float, float, float]:
        """Return ``(fx, fy, cx, cy)``, the format tag detectors expect."""
        matrix = np.asarray(self.camera_matrix, dtype=np.float64)
        return (
            float(matrix[0, 0]),
            float(matrix[1, 1]),
            float(matrix[0, 2]),
            float(matrix[1, 2]),
        )


def save_calibration(path: Path, calibration: CameraCalibration) -> None:
    """Write the calibration as JSON, creating missing parent folders."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "calibrationId": calibration.calibration_id
        or f"{calibration.frame_id or path.stem}@{datetime.now(timezone.utc).isoformat()}",
        "frameId": calibration.frame_id,
        "distortionModel": _checked_model(calibration.model, path),
        "imageSize": [int(calibration.image_size[0]), int(calibration.image_size[1])],
        "cameraMatrix": np.asarray(calibration.camera_matrix, dtype=np.float64).tolist(),
        "distortionCoefficients": np.asarray(
            calibration.distortion, dtype=np.float64
        ).reshape(-1).tolist(),
        "rmsReprojectionError": float(calibration.rms_reprojection_error),
        "sampleCount": int(calibration.sample_count),
        "board": dict(calibration.board),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_calibration(path: Path) -> CameraCalibration:
    """Read a calibration file. If a field is missing, the exception names the path."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Kalibrierung nicht gefunden: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema == LEGACY_SCHEMA:
        model = PINHOLE
    elif schema == SCHEMA:
        model = _checked_model(data.get("distortionModel"), path)
    else:
        raise ValueError(f"Unbekanntes Kalibrierschema '{schema}' in {path} (erwartet {SCHEMA})")
    size = data["imageSize"]
    distortion = np.asarray(data["distortionCoefficients"], dtype=np.float64).reshape(-1)
    if model == FISHEYE and distortion.size != 4:
        raise ValueError(
            f"Fisheye-Kalibrierung in {path} hat {distortion.size} statt 4 Koeffizienten"
        )
    return CameraCalibration(
        camera_matrix=np.asarray(data["cameraMatrix"], dtype=np.float64).reshape(3, 3),
        distortion=distortion,
        image_size=(int(size[0]), int(size[1])),
        frame_id=str(data.get("frameId", "")),
        calibration_id=str(data.get("calibrationId", "")),
        rms_reprojection_error=float(data.get("rmsReprojectionError", float("nan"))),
        sample_count=int(data.get("sampleCount", 0)),
        board=dict(data.get("board", {})),
        model=model,
    )


def _checked_model(model: Any, path: Path) -> str:
    """Return ``model`` if it is a known lens model.

    An unknown model is an error, not a fallback to pinhole. With the wrong model,
    undistortion is wrong and every pose is off in a plausible-looking way.
    """
    if model not in DISTORTION_MODELS:
        raise ValueError(
            f"Unbekanntes Linsenmodell '{model}' in {path} (erwartet eines von "
            f"{', '.join(DISTORTION_MODELS)})"
        )
    return str(model)


def default_calibration(resolution: tuple[int, int], frame_id: str = "") -> CameraCalibration:
    """Return a rough estimated calibration for when no real one exists yet.

    **Not a replacement for a real calibration run** (test plan section 3.3). The
    intrinsics come from an assumed horizontal field of view, not from a measurement.
    Poses are plausibly oriented but not metric: distances and sizes can be far off.
    This exists only to keep AprilTag detection runnable without a calibration file,
    so that the detector, overlay and job path can be checked while the real
    calibration is still pending. ``calibration_id`` marks this clearly in the result.
    """
    width, height = resolution
    focal_length = width / (2.0 * math.tan(math.radians(DEFAULT_HORIZONTAL_FOV_DEG) / 2.0))
    matrix = np.array(
        [
            [focal_length, 0.0, width / 2.0],
            [0.0, focal_length, height / 2.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.zeros(5, dtype=np.float64),
        image_size=(int(width), int(height)),
        frame_id=frame_id,
        calibration_id=PLACEHOLDER_CALIBRATION_ID,
        rms_reprojection_error=float("nan"),
        sample_count=0,
        board={},
    )


def check_resolution(calibration: CameraCalibration, image_size: tuple[int, int]) -> None:
    """Raise if the calibration belongs to a different resolution.

    Without this check every pose is off by the scale factor, plausibly but unnoticed.
    This happens in practice: ``AprilTagProfileConfig.resolution`` is 2028x1520, while
    a calibration made via ``cv2.VideoCapture(0)`` easily ends up at 640x480.
    """
    expected = (int(calibration.image_size[0]), int(calibration.image_size[1]))
    actual = (int(image_size[0]), int(image_size[1]))
    if expected != actual:
        raise ValueError(
            f"Kalibrierung gilt fuer {expected[0]}x{expected[1]}, Bild ist "
            f"{actual[0]}x{actual[1]}. Neu kalibrieren oder scale_to_resolution() benutzen."
        )


def scale_to_resolution(
    calibration: CameraCalibration, image_size: tuple[int, int]
) -> CameraCalibration:
    """Scale the intrinsics to a different resolution.

    Only valid for the same image section and aspect ratio (pure downscaling, no
    cropping). The distortion coefficients refer to normalized coordinates and stay
    unchanged, for both pinhole and fisheye. ``model`` is therefore copied as is.
    """
    source = (int(calibration.image_size[0]), int(calibration.image_size[1]))
    target = (int(image_size[0]), int(image_size[1]))
    scale_x = target[0] / source[0]
    scale_y = target[1] / source[1]
    if abs(scale_x - scale_y) > 1e-3:
        raise ValueError(
            f"Seitenverhaeltnis aendert sich ({source} -> {target}); "
            "Skalieren wuerde die Intrinsik verfaelschen."
        )
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64).copy()
    matrix[0, :] *= scale_x
    matrix[1, :] *= scale_y
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.asarray(calibration.distortion, dtype=np.float64).copy(),
        image_size=target,
        frame_id=calibration.frame_id,
        calibration_id=f"{calibration.calibration_id}@{target[0]}x{target[1]}",
        rms_reprojection_error=calibration.rms_reprojection_error,
        sample_count=calibration.sample_count,
        board=dict(calibration.board),
        model=calibration.model,
    )


#: ``calibration_identity`` is re-exported here so callers do not have to search a
#: second module. It lives in ``identity`` because that module must work without numpy.
__all__ = [
    "DISTORTION_MODELS",
    "FISHEYE",
    "LEGACY_SCHEMA",
    "PINHOLE",
    "SCHEMA",
    "PLACEHOLDER_CALIBRATION_ID",
    "CameraCalibration",
    "calibration_identity",
    "check_resolution",
    "default_calibration",
    "load_calibration",
    "save_calibration",
    "scale_to_resolution",
]
