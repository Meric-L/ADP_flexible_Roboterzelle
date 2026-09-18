"""Calibration boards: detect, cover, compute.

Extracted from `src/apriltag/calibrate_camera.py`, whose calculation core was
trapped in the keyboard loop of `main()` -- there was no way to calibrate
from a set of existing images. That's now possible, and is the precondition
for testing the calibration path without a camera.

Board type and geometry are parameters (`BoardSpec`), not module globals like
`USE_CHARUCO` and `CHESSBOARD_SIZE`.
"""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from .calibration import CameraCalibration

_log = logging.getLogger(__name__)

CHESSBOARD = "chessboard"
CHARUCO = "charuco"


@dataclass(frozen=True)
class BoardSpec:
    """Geometry of the calibration board.

    For `chessboard`, `cols`/`rows` are the **inner** corners (9x6 for a
    10x7-square board); for `charuco`, the number of squares.
    """

    type: str = CHESSBOARD
    cols: int = 9
    rows: int = 6
    square_size_m: float = 0.030
    marker_size_m: float = 0.022
    dictionary: str = "DICT_4X4_50"

    def as_dict(self) -> dict[str, Any]:
        """Return the provenance record stored in the calibration file."""
        data = {
            "type": self.type,
            "cols": self.cols,
            "rows": self.rows,
            "squareSizeM": self.square_size_m,
        }
        if self.type == CHARUCO:
            data["markerSizeM"] = self.marker_size_m
            data["dictionary"] = self.dictionary
        return data


@dataclass(frozen=True)
class BoardSample:
    """An image in which the board was found."""

    corners: np.ndarray
    ids: np.ndarray | None = None

    def count(self) -> int:
        return int(np.asarray(self.corners).reshape(-1, 2).shape[0])


def build_board(spec: BoardSpec) -> Any:
    """Build the ChArUco board object. `None` for chessboard."""
    if spec.type != CHARUCO:
        return None
    import cv2

    from .detector import predefined_dictionary

    aruco = cv2.aruco
    dictionary = predefined_dictionary(spec.dictionary)
    if hasattr(aruco, "CharucoBoard"):
        try:
            return aruco.CharucoBoard(
                (spec.cols, spec.rows), spec.square_size_m, spec.marker_size_m, dictionary
            )
        except TypeError:  # pragma: no cover - older signature
            return aruco.CharucoBoard_create(
                spec.cols, spec.rows, spec.square_size_m, spec.marker_size_m, dictionary
            )
    return aruco.CharucoBoard_create(  # pragma: no cover
        spec.cols, spec.rows, spec.square_size_m, spec.marker_size_m, dictionary
    )


def detect_board(gray, spec: BoardSpec, board: Any = None) -> BoardSample | None:
    """Look for the board in a grayscale image. `None` if not found."""
    import cv2

    if spec.type == CHARUCO:
        board = board if board is not None else build_board(spec)
        if hasattr(cv2.aruco, "CharucoDetector"):
            detector = cv2.aruco.CharucoDetector(board)
            corners, ids, _, _ = detector.detectBoard(gray)
        else:  # pragma: no cover - older OpenCV version
            marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(gray, board.dictionary)
            if marker_ids is None or len(marker_ids) == 0:
                return None
            _, corners, ids = cv2.aruco.interpolateCornersCharuco(
                marker_corners, marker_ids, gray, board
            )
        if corners is None or ids is None or len(corners) < 4:
            return None
        return BoardSample(corners=np.asarray(corners), ids=np.asarray(ids))

    found, corners = cv2.findChessboardCorners(
        gray,
        (spec.cols, spec.rows),
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE,
    )
    if not found:
        return None
    refined = cv2.cornerSubPix(
        gray,
        corners,
        (11, 11),
        (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3),
    )
    return BoardSample(corners=np.asarray(refined), ids=None)


def compute_coverage(samples, image_size: tuple[int, int]) -> tuple[float, float]:
    """Fraction of image width/height covered by all samples combined.

    Carried over from `calibrate_camera.py`. More important than it looks: a
    calibration taken only from the image centre gets a good RMS but useless
    distortion coefficients -- and the error only shows up at the edges,
    where the modules sit.
    """
    points = []
    for sample in samples:
        corners = sample.corners if isinstance(sample, BoardSample) else sample
        points.append(np.asarray(corners, dtype=np.float64).reshape(-1, 2))
    if not points:
        return (0.0, 0.0)
    stacked = np.vstack(points)
    width = max(int(image_size[0]), 1)
    height = max(int(image_size[1]), 1)
    span_x = float(stacked[:, 0].max() - stacked[:, 0].min()) / width
    span_y = float(stacked[:, 1].max() - stacked[:, 1].min()) / height
    return (min(1.0, max(0.0, span_x)), min(1.0, max(0.0, span_y)))


def _chessboard_object_points(spec: BoardSpec) -> np.ndarray:
    grid = np.zeros((spec.cols * spec.rows, 3), dtype=np.float32)
    grid[:, :2] = np.mgrid[0 : spec.cols, 0 : spec.rows].T.reshape(-1, 2)
    return grid * spec.square_size_m


def calibrate_from_samples(
    samples,
    image_size: tuple[int, int],
    spec: BoardSpec,
    board: Any = None,
    *,
    frame_id: str = "",
) -> CameraCalibration:
    """Compute the calibration from collected board samples.

    No image, no GUI, no camera -- just points. The same function runs
    behind both the live UI and the image-folder path.
    """
    import cv2

    samples = list(samples)
    if len(samples) < 3:
        raise ValueError(f"Zu wenige Aufnahmen fuer eine Kalibrierung: {len(samples)}")

    object_points: list[np.ndarray] = []
    image_points: list[np.ndarray] = []
    if spec.type == CHARUCO:
        board = board if board is not None else build_board(spec)
        for sample in samples:
            matched_object, matched_image = board.matchImagePoints(sample.corners, sample.ids)
            if matched_object is None or len(matched_object) < 4:
                continue
            object_points.append(matched_object)
            image_points.append(matched_image)
    else:
        grid = _chessboard_object_points(spec)
        for sample in samples:
            object_points.append(grid)
            image_points.append(np.asarray(sample.corners, dtype=np.float32))

    if len(object_points) < 3:
        raise ValueError("Zu wenige verwertbare Aufnahmen nach der Zuordnung")

    rms, camera_matrix, distortion, _, _ = cv2.calibrateCamera(
        object_points, image_points, (int(image_size[0]), int(image_size[1])), None, None
    )
    return CameraCalibration(
        camera_matrix=np.asarray(camera_matrix, dtype=np.float64),
        distortion=np.asarray(distortion, dtype=np.float64).reshape(-1),
        image_size=(int(image_size[0]), int(image_size[1])),
        frame_id=frame_id,
        rms_reprojection_error=float(rms),
        sample_count=len(object_points),
        board=spec.as_dict(),
    )


__all__ = [
    "CHARUCO",
    "CHESSBOARD",
    "BoardSample",
    "BoardSpec",
    "build_board",
    "calibrate_from_samples",
    "compute_coverage",
    "detect_board",
]
