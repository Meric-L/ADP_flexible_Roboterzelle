"""Camera calibration as data: read, write, check. No OpenCV.

JSON instead of OpenCV FileStorage YAML, deliberately:

* readable without `cv2` -- the vision server builds `configurationId`
  without loading OpenCV, and tests run everywhere
* room for `rms`, image size and board geometry. The prototype
  (`src/apriltag/calibrate_camera.py`) only printed `rms` to stdout and wrote
  `image_width`/`image_height` without ever reading them back
* diffable and human-readable on error

The file belongs to **one physical camera**, not the repo. Stored under
`data/`, excluded by `.gitignore` -- each Pi generates its own.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .identity import calibration_identity

SCHEMA = "wsc.vision.calibration/1"


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

    @property
    def camera_params(self) -> tuple[float, float, float, float]:
        """Return `(fx, fy, cx, cy)`, the format tag detectors expect."""
        matrix = np.asarray(self.camera_matrix, dtype=np.float64)
        return (
            float(matrix[0, 0]),
            float(matrix[1, 1]),
            float(matrix[0, 2]),
            float(matrix[1, 2]),
        )


def save_calibration(path: Path, calibration: CameraCalibration) -> None:
    """Write the calibration as JSON, creating missing directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "calibrationId": calibration.calibration_id
        or f"{calibration.frame_id or path.stem}@{datetime.now(timezone.utc).isoformat()}",
        "frameId": calibration.frame_id,
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
    """Read a calibration file. Raises with the path if something is missing."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Kalibrierung nicht gefunden: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema != SCHEMA:
        raise ValueError(f"Unbekanntes Kalibrierschema '{schema}' in {path} (erwartet {SCHEMA})")
    size = data["imageSize"]
    return CameraCalibration(
        camera_matrix=np.asarray(data["cameraMatrix"], dtype=np.float64).reshape(3, 3),
        distortion=np.asarray(data["distortionCoefficients"], dtype=np.float64).reshape(-1),
        image_size=(int(size[0]), int(size[1])),
        frame_id=str(data.get("frameId", "")),
        calibration_id=str(data.get("calibrationId", "")),
        rms_reprojection_error=float(data.get("rmsReprojectionError", float("nan"))),
        sample_count=int(data.get("sampleCount", 0)),
        board=dict(data.get("board", {})),
    )


def check_resolution(calibration: CameraCalibration, image_size: tuple[int, int]) -> None:
    """Raise if the calibration belongs to a different resolution.

    Without this check, every pose is off by the scale factor -- plausibly
    off, so it goes unnoticed. This happens in practice:
    `AprilTagProfileConfig.resolution` is 2028x1520, while a calibration via
    `cv2.VideoCapture(0)` easily ends up at 640x480.
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

    Only valid for the same framing and aspect ratio (pure downscaling, not
    cropping). Distortion coefficients are relative to normalised
    coordinates and stay unchanged.
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
    )


#: `calibration_identity` is re-exported here so callers don't need to look
#: in a second module. It lives in `identity` because that module must work
#: without numpy.
__all__ = [
    "SCHEMA",
    "CameraCalibration",
    "calibration_identity",
    "check_resolution",
    "load_calibration",
    "save_calibration",
    "scale_to_resolution",
]
