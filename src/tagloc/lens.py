"""Lens-model-dependent OpenCV calls, in one place.

OpenCV has a separate namespace `cv2.fisheye` for the fisheye model (Kannala-Brandt)
with different functions and different array-shape requirements than the standard
pinhole model (Brown-Conrady). Calling the function of one model with the coefficients
of the other does not raise an error; it silently returns wrong points. This module
is therefore the only place that decides, based on `CameraCalibration.model`, what
gets called. Pose, overlay and calibration request functions here instead of
branching themselves.

The config sets which camera uses which model
(`AprilTagProfileConfig.calibration_model`, see work plan
`kalibrierung-linsenmodell-je-kamera.md`): ceiling camera fisheye, hand camera
pinhole.
"""

import logging
import re

import numpy as np

from .calibration import DISTORTION_MODELS, FISHEYE, PINHOLE, CameraCalibration

_log = logging.getLogger(__name__)

#: Number of coefficients that `cv2.fisheye` expects (k1..k4).
FISHEYE_COEFFICIENTS = 4

#: Termination criteria for `cv2.fisheye.calibrate`. The OpenCV default (20 iterations)
#: does not always converge for 12 MP captures with many corners; 100 iterations cost
#: hardly anything compared to the corner search.
_FISHEYE_CRITERIA_MAX_ITER = 100
_FISHEYE_CRITERIA_EPS = 1e-6


def _fisheye_flag(name: str) -> int:
    """Return a fisheye calibration flag, independent of the OpenCV version.

    OpenCV 4.x provides them as `cv2.fisheye.CALIB_*` (e.g. RECOMPUTE_EXTRINSIC = 2),
    OpenCV 5 only as `cv2.CALIB_*` with **different numeric values** (there 8388608).
    Hard-coded numbers would silently be wrong on one of the two versions. The Pi runs
    a different OpenCV version than the development machines.
    """
    import cv2

    fisheye_value = getattr(cv2.fisheye, name, None)
    return int(fisheye_value if fisheye_value is not None else getattr(cv2, name))


def _camera_matrix(calibration: CameraCalibration) -> np.ndarray:
    return np.asarray(calibration.camera_matrix, dtype=np.float64)


def _fisheye_coefficients(calibration: CameraCalibration) -> np.ndarray:
    coefficients = np.asarray(calibration.distortion, dtype=np.float64).reshape(-1)
    if coefficients.size != FISHEYE_COEFFICIENTS:
        raise ValueError(
            f"Fisheye-Kalibrierung braucht {FISHEYE_COEFFICIENTS} Koeffizienten, "
            f"hat {coefficients.size}"
        )
    return coefficients.reshape(FISHEYE_COEFFICIENTS, 1)


def _check_model(model: str) -> None:
    if model not in DISTORTION_MODELS:
        raise ValueError(
            f"Unbekanntes Linsenmodell '{model}' (erwartet eines von "
            f"{', '.join(DISTORTION_MODELS)})"
        )


def undistort_points(points, calibration: CameraCalibration) -> np.ndarray:
    """Undistort image points to the ideal pinhole model, in pixels (`P=K`).

    Afterwards the calculation can continue with zero distortion. This is the same for
    both lens models (see `tagloc.pose`). Returns `(N, 2)`.
    """
    import cv2

    _check_model(calibration.model)
    distorted = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    camera_matrix = _camera_matrix(calibration)
    if calibration.model == FISHEYE:
        undistorted = cv2.fisheye.undistortPoints(
            distorted, camera_matrix, _fisheye_coefficients(calibration), P=camera_matrix
        )
    else:
        undistorted = cv2.undistortPoints(
            distorted,
            camera_matrix,
            np.asarray(calibration.distortion, dtype=np.float64),
            P=camera_matrix,
        )
    return np.asarray(undistorted).reshape(-1, 2)


def project_points(object_points, rvec, tvec, calibration: CameraCalibration) -> np.ndarray:
    """Project 3D points (object frame) into the image with distortion, `(N, 2)`."""
    import cv2

    _check_model(calibration.model)
    rvec = np.asarray(rvec, dtype=np.float64).reshape(3, 1)
    tvec = np.asarray(tvec, dtype=np.float64).reshape(3, 1)
    camera_matrix = _camera_matrix(calibration)
    if calibration.model == FISHEYE:
        projected, _ = cv2.fisheye.projectPoints(
            np.asarray(object_points, dtype=np.float64).reshape(-1, 1, 3),
            rvec,
            tvec,
            camera_matrix,
            _fisheye_coefficients(calibration),
        )
    else:
        projected, _ = cv2.projectPoints(
            np.asarray(object_points, dtype=np.float64).reshape(-1, 3),
            rvec,
            tvec,
            camera_matrix,
            np.asarray(calibration.distortion, dtype=np.float64),
        )
    return np.asarray(projected).reshape(-1, 2)


def undistortion_maps(calibration: CameraCalibration, alpha: float = 1.0):
    """Precompute maps for `cv2.remap` that undistort a whole image.

    Used for display (live stream mode "undistorted"), not for measurement; measurement
    only undistorts corner points (`undistort_points`). Compute once per resolution and
    calibration: `initUndistortRectifyMap` is expensive, while `remap` with ready-made
    maps is not.

    `alpha` selects the crop: 1 keeps all image points visible, with the curved black
    borders that show the effect of the undistortion; 0 crops to valid pixels. For
    fisheye this corresponds to `balance`.

    The image size is `calibration.image_size`; for a different resolution, call
    `scale_to_resolution` first. Returns `(map1, map2)`.
    """
    import cv2

    _check_model(calibration.model)
    size = (int(calibration.image_size[0]), int(calibration.image_size[1]))
    camera_matrix = _camera_matrix(calibration)
    if calibration.model == FISHEYE:
        coefficients = _fisheye_coefficients(calibration)
        new_matrix = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
            camera_matrix, coefficients, size, np.eye(3), balance=float(alpha)
        )
        return cv2.fisheye.initUndistortRectifyMap(
            camera_matrix, coefficients, np.eye(3), new_matrix, size, cv2.CV_16SC2
        )
    distortion = np.asarray(calibration.distortion, dtype=np.float64)
    new_matrix, _ = cv2.getOptimalNewCameraMatrix(
        camera_matrix, distortion, size, float(alpha), size
    )
    return cv2.initUndistortRectifyMap(
        camera_matrix, distortion, None, new_matrix, size, cv2.CV_16SC2
    )


def calibrate(
    object_points, image_points, image_size: tuple[int, int], model: str = PINHOLE
) -> tuple[float, np.ndarray, np.ndarray]:
    """Compute `(rms, K, D)` from point correspondences per capture.

    `PINHOLE`: `cv2.calibrateCamera` without extra flags, i.e. Brown-Conrady with
    exactly 5 coefficients (k1, k2, p1, p2, k3).

    `FISHEYE`: `cv2.fisheye.calibrate` (Kannala-Brandt, k1..k4) with
    `RECOMPUTE_EXTRINSIC | CHECK_COND | FIX_SKEW`. If `CHECK_COND` fails, OpenCV names
    the badly conditioned capture ("input array N", typically a board far away,
    almost at the image edge or very flat); that capture is left out and the
    calibration computed again, until it succeeds or fewer than three captures
    are left. Since 17.11.2026: before, the calibration was recomputed without the
    check, which kept the bad capture and could crash OpenCV later
    ("Assertion failed ... _rowRange" in `Mat`).

    Returns:
        `(rms, camera_matrix, distortion, used)`; `used` is the number of captures
        the result was computed from.

    Raises:
        ValueError: If the model is unknown.
    """
    import cv2

    _check_model(model)
    size = (int(image_size[0]), int(image_size[1]))
    if model == PINHOLE:
        rms, camera_matrix, distortion, _, _ = cv2.calibrateCamera(
            list(object_points), list(image_points), size, None, None
        )
        return (
            float(rms),
            np.asarray(camera_matrix, dtype=np.float64),
            np.asarray(distortion, dtype=np.float64).reshape(-1),
            len(object_points),
        )

    # cv2.fisheye is stricter about shapes than calibrateCamera: float64,
    # (1, N, 3) or (1, N, 2) per capture. (N, 1, 3) fails under
    # OpenCV 5 with "Sizes of input arguments do not match" (measured
    # 2026-09-29); (1, N, *) is also the shape from the OpenCV 4 examples.
    objects = [np.asarray(points, dtype=np.float64).reshape(1, -1, 3) for points in object_points]
    images = [np.asarray(points, dtype=np.float64).reshape(1, -1, 2) for points in image_points]
    criteria = (
        cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS,
        _FISHEYE_CRITERIA_MAX_ITER,
        _FISHEYE_CRITERIA_EPS,
    )
    base_flags = _fisheye_flag("CALIB_RECOMPUTE_EXTRINSIC") | _fisheye_flag("CALIB_FIX_SKEW")

    def run(kept: list[int], flags: int):
        return cv2.fisheye.calibrate(
            [objects[index] for index in kept],
            [images[index] for index in kept],
            size,
            np.zeros((3, 3), dtype=np.float64),
            np.zeros((FISHEYE_COEFFICIENTS, 1), dtype=np.float64),
            flags=flags,
            criteria=criteria,
        )

    kept = list(range(len(objects)))
    while True:
        try:
            rms, camera_matrix, distortion, _, _ = run(kept, base_flags | _fisheye_flag("CALIB_CHECK_COND"))
            break
        except cv2.error as error:
            # Only the conditioning check names a capture to leave out
            # ("CALIB_CHECK_COND - Ill-conditioned matrix for input array N").
            # Any other error (shapes, too few points) propagates with its own message.
            match = re.search(r"input array (\d+)", str(error))
            if "ill-conditioned" not in str(error).lower() or match is None or len(kept) <= 3:
                raise
            dropped = kept.pop(int(match.group(1)))
            _log.warning(
                "Fisheye-Kalibrierung: Aufnahme %d schlecht konditioniert -- ohne sie neu gerechnet (%d uebrig)",
                dropped + 1,
                len(kept),
            )
    return (
        float(rms),
        np.asarray(camera_matrix, dtype=np.float64),
        np.asarray(distortion, dtype=np.float64).reshape(-1),
        len(kept),
    )


__all__ = [
    "FISHEYE_COEFFICIENTS",
    "calibrate",
    "project_points",
    "undistort_points",
    "undistortion_maps",
]
