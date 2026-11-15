"""Calibration boards: detect, cover, compute.

Extracted from `src/apriltag/calibrate_camera.py` (prototype, removed 2026-10-30).
Its calculation core was trapped in the keyboard loop of `main()`, so it was not
possible to calibrate from a set of existing images. That is possible now, and it
is the precondition for testing the calibration path without a camera.

Board type and geometry are parameters (`BoardSpec`), not module globals like
`USE_CHARUCO` and `CHESSBOARD_SIZE`.
"""

import logging
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from .calibration import PINHOLE, CameraCalibration

_log = logging.getLogger(__name__)

CHESSBOARD = "chessboard"
CHARUCO = "charuco"


@dataclass(frozen=True)
class BoardSpec:
    """Geometry of the calibration board.

    For `chessboard`, `cols`/`rows` are the **inner** corners (9x6 for a
    10x7-square board). For `charuco`, they are the number of squares.
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
    return aruco.CharucoBoard(
        (spec.cols, spec.rows), spec.square_size_m, spec.marker_size_m, dictionary
    )


def detect_board(
    gray, spec: BoardSpec, board: Any = None, *, max_side: int | None = None
) -> BoardSample | None:
    """Look for the board in a grayscale image. Return `None` if not found.

    `max_side`: the chessboard is searched in an image scaled down to this edge
    length, and the corners are then refined in the full image. This is much
    faster, especially when nothing is found: a failed attempt takes seconds on
    the full image, and without a board in the image every attempt fails. Meant
    for requests that must answer quickly.
    """
    import cv2

    if spec.type == CHARUCO:
        board = board if board is not None else build_board(spec)
        detector = cv2.aruco.CharucoDetector(board)
        corners, ids, _, _ = detector.detectBoard(gray)
        if corners is None or ids is None or len(corners) < 4:
            return None
        return BoardSample(corners=np.asarray(corners), ids=np.asarray(ids))

    if max_side is not None and max(gray.shape[:2]) > max_side:
        scale = max_side / max(gray.shape[:2])
        small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        sample = detect_board(small, spec)
        if sample is None:
            return None
        seeds = np.ascontiguousarray(np.asarray(sample.corners, dtype=np.float32).reshape(-1, 1, 2) / scale)
        refined = cv2.cornerSubPix(
            gray, seeds, (11, 11), (-1, -1), (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)
        )
        return BoardSample(corners=np.asarray(refined), ids=None)

    sample = _detect_chessboard(gray, spec)
    if sample is None:
        # Light squares on a black carrier without a light quiet zone: the dark
        # border squares merge with the frame, and the detectors do not find the
        # grid. Inverted, the border is light and the board looks like a standard
        # printed board. The corners lie at the same positions; the square colour
        # for the origin is still read from the original image by
        # `canonical_chessboard_corners`.
        sample = _detect_chessboard(cv2.bitwise_not(gray), spec)
    return sample


def _detect_chessboard(gray, spec: BoardSpec) -> BoardSample | None:
    """Run one detection pass for a chessboard: SB first, then the classic method."""
    import cv2

    # The newer "Sector-Based" detector: more robust than the classic method under
    # poor light, blur and strong tilt, and it already returns sub-pixel accurate
    # corners -- an extra cornerSubPix() is not needed here.
    #
    # Note, not obvious: SB returns the corners in the **exact reverse order** of
    # `findChessboardCorners` (checked empirically with a synthetic test image on
    # 2026-09-23: after reversing, the positions agree to < 0.1 px). Taken
    # unfiltered, this would silently swap the image-to-world point assignment in
    # `_chessboard_object_points` -- the calibration would run through, but with a
    # wrong result instead of an error.
    # DELIBERATELY WITHOUT CALIB_CB_ACCURACY: according to the OpenCV docs the flag
    # is an "own, slower algorithm" -- measured at ~4000 px width (ceiling camera
    # format): 4.45 s with it versus 0.87 s without it (factor 5), and 0.06 s for the
    # classic method (factor 73). The calibration capture always runs on the FULL
    # camera frame (12 MP on the ceiling camera) -- with the flag, the OPC UA request
    # on the Pi ran into the timeout ("Failed to send request"), found live on
    # 2026-09-23.
    found, corners = cv2.findChessboardCornersSB(
        gray, (spec.cols, spec.rows), flags=cv2.CALIB_CB_NORMALIZE_IMAGE
    )
    if found:
        return BoardSample(corners=np.asarray(corners)[::-1], ids=None)

    # Fallback: SB did not find the board in this frame. The classic method sometimes
    # finds boards that SB misses (and vice versa); a second attempt only costs time
    # when SB fails.
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


def partial_patterns(spec: BoardSpec) -> list[tuple[int, int]]:
    """Return smaller grids with which a partly visible board can still be found.

    From just below `spec` down to 3x3, largest first.
    """
    patterns = []
    cols, rows = spec.cols - 1, spec.rows - 1
    while min(cols, rows) >= 3:
        patterns.append((cols, rows))
        cols, rows = cols - 1, rows - 1
    if (3, 3) not in patterns and min(spec.cols, spec.rows) > 3:
        patterns.append((3, 3))
    return patterns


def detect_partial_board(gray, spec: BoardSpec, *, max_side: int | None = None):
    """Find a partial grid of the board in the image.

    Returns `(corners (N, 2), cols, rows)` or `None`. Used for centering when the
    whole board is not in the image: detection needs the full grid, but a smaller
    grid fits any visible part. The origin of a partial grid is arbitrary -- it is
    good for centroid and direction, not for a board pose.
    """
    if spec.type != CHESSBOARD:
        return None
    for cols, rows in partial_patterns(spec):
        sample = detect_board(gray, replace(spec, cols=cols, rows=rows), max_side=max_side)
        if sample is not None:
            return np.asarray(sample.corners, dtype=np.float64).reshape(-1, 2), cols, rows
    return None


def diagnose_board(gray, spec: BoardSpec) -> dict:
    """Explain why detection does not find the board. Only for troubleshooting.

    Tries the given grid with both detectors, the swapped grid (rows/columns), and
    grids around the given size with the fast detector, largest first.
    `largestGrid` is the first one found. Also reports brightness values of the
    image (exposure, overexposure).
    """
    import cv2

    result = {
        "imageSize": [int(gray.shape[1]), int(gray.shape[0])],
        "grayMin": int(gray.min()),
        "grayMax": int(gray.max()),
        "grayMean": round(float(gray.mean()), 1),
        "saturatedFraction": round(float((gray >= 250).mean()), 4),
        "sbExact": None,
        "classicExact": None,
        "sbSwapped": None,
        "largestGrid": None,
        "tried": [],
    }
    if spec.type != CHESSBOARD:
        return result

    def sb(cols, rows) -> bool:
        found, _ = cv2.findChessboardCornersSB(gray, (cols, rows), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
        return bool(found)

    result["sbExact"] = sb(spec.cols, spec.rows)
    found, _ = cv2.findChessboardCorners(
        gray, (spec.cols, spec.rows), flags=cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
    )
    result["classicExact"] = bool(found)
    if spec.cols != spec.rows:
        result["sbSwapped"] = sb(spec.rows, spec.cols)
    candidates = sorted(
        {
            (cols, rows)
            for cols in range(max(3, spec.cols - 2), spec.cols + 2)
            for rows in range(max(3, spec.rows - 2), spec.rows + 2)
        },
        key=lambda size: (-size[0] * size[1], size),
    )
    for cols, rows in candidates:
        result["tried"].append([cols, rows])
        if sb(cols, rows):
            result["largestGrid"] = {"cols": cols, "rows": rows}
            break
    return result


def compute_coverage(samples, image_size: tuple[int, int]) -> tuple[float, float]:
    """Return the fraction of image width and height covered by all samples combined.

    Carried over from `calibrate_camera.py`. It matters more than it seems: a
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


def chessboard_object_points(spec: BoardSpec) -> np.ndarray:
    """Return the inner corners in board coordinates, `(N, 3)` float64.

    Origin at the first corner, +x along the row, +y to the next row, z = 0. The
    order matches the canonical corners (`canonical_chessboard_corners`).
    """
    return _chessboard_object_points(spec).astype(np.float64)


def matched_board_points(sample: BoardSample, spec: BoardSpec) -> tuple[np.ndarray, np.ndarray]:
    """Return image and object points of a board in the same, fixed order."""
    if spec.type == CHARUCO:
        if sample.ids is None:
            raise ValueError("ChArUco-IDs fehlen")
        object_points, image_points = build_board(spec).matchImagePoints(
            np.asarray(sample.corners, dtype=np.float32), np.asarray(sample.ids, dtype=np.int32))
        if object_points is None or image_points is None:
            raise ValueError("ChArUco-Korrespondenzen fehlen")
        return (np.asarray(object_points, dtype=np.float64).reshape(-1, 3),
                np.asarray(image_points, dtype=np.float64).reshape(-1, 2))
    return chessboard_object_points(spec), np.asarray(sample.corners, dtype=np.float64).reshape(-1, 2)


def chessboard_is_unambiguous(spec: BoardSpec) -> bool:
    """Return whether a chessboard rotated by 180 degrees differs from the original.

    Only then can the corner order be fixed by the colour of the first square:
    rotated by 180 degrees, the first square lands on the last one, and that has
    the other colour exactly when `cols + rows` (inner corners) is odd -- e.g.
    5x6 inner corners on a board with 6x7 squares.
    """
    return spec.type == CHESSBOARD and spec.cols >= 3 and spec.rows >= 2 and (spec.cols + spec.rows) % 2 == 1


def canonical_chessboard_corners(gray, corners, spec: BoardSpec) -> np.ndarray:
    """Bring the chessboard corners into a fixed order, `(N, 2)`.

    OpenCV returns the corners row by row, but depending on the viewing angle it
    starts at a different corner. This does not matter for the camera calibration,
    but it does for the hand-eye calibration: there the board must have the **same**
    coordinate system in **every** image, otherwise it jumps by 180 degrees or
    mirrored, and `T_flange_cam` becomes wrong instead of merely imprecise.

    Two steps:
    1. Handedness: +x (along the row) and +y (to the next row) must run clockwise
       in the image (image y points down), so the board +z points away from the
       camera. Otherwise flip each row.
    2. Colour: the square between the first four corners is black. Otherwise flip
       the whole list (180 degrees, handedness stays). Requires
       `chessboard_is_unambiguous(spec)`.
    """
    points = np.asarray(corners, dtype=np.float64).reshape(spec.rows, spec.cols, 2)
    along_row = points[0, 1] - points[0, 0]
    to_next_row = points[1, 0] - points[0, 0]
    if along_row[0] * to_next_row[1] - along_row[1] * to_next_row[0] < 0:
        points = points[:, ::-1]

    def brightness(cell_row: int, cell_col: int) -> float:
        centre = points[cell_row : cell_row + 2, cell_col : cell_col + 2].reshape(-1, 2).mean(axis=0)
        x, y = int(round(centre[0])), int(round(centre[1]))
        height, width = np.asarray(gray).shape[:2]
        x0, x1 = max(0, x - 2), min(width, x + 3)
        y0, y1 = max(0, y - 2), min(height, y + 3)
        return float(np.mean(np.asarray(gray)[y0:y1, x0:x1]))

    # The neighbouring square certainly has the other colour: compare instead of
    # using a threshold, so that exposure and material (lasered wood) do not matter.
    if brightness(0, 0) > brightness(0, 1):
        points = points[::-1, ::-1]
    return points.reshape(-1, 2)


def estimate_board_pose(
    corners, spec: BoardSpec, calibration: CameraCalibration, *, ids=None
) -> tuple[np.ndarray, float]:
    """Return `(T_cam_board, reprojection error in px)` from the board corners.

    Board frame: origin at the first inner corner, +x along the row, +y to the next
    row, +z away from the camera (into the board). The undistortion is done first
    and identically for both lens models (`tagloc.lens`); the pose is then solved
    without distortion, as for the tags (`tagloc.pose`).
    """
    import cv2

    from . import lens
    from .geometry import from_rvec_tvec

    if spec.type == CHARUCO:
        if ids is None:
            raise ValueError("ChArUco-IDs fehlen")
        object_points, corners = matched_board_points(BoardSample(np.asarray(corners), np.asarray(ids)), spec)
    else:
        object_points = _chessboard_object_points(spec).astype(np.float64)
    image_points = lens.undistort_points(corners, calibration).reshape(-1, 1, 2)
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    no_distortion = np.zeros(5, dtype=np.float64)

    def reprojection_px(rvec, tvec) -> float:
        projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, no_distortion)
        error = projected.reshape(-1, 2) - image_points.reshape(-1, 2)
        return float(np.sqrt(np.mean(np.sum(error**2, axis=1))))

    # IPPE returns both candidates for a planar target. Viewed head-on they lie close
    # together, and the first is often tilted by a few degrees (on the rendered board:
    # 2.5 degrees, 2 mm). So take the better one and refine it with Levenberg-Marquardt
    # over all corners.
    candidates = []
    found, rvecs, tvecs, _ = cv2.solvePnPGeneric(
        object_points, image_points, camera_matrix, no_distortion, flags=cv2.SOLVEPNP_IPPE
    )
    if found:
        candidates = list(zip(rvecs, tvecs))
    if not candidates:
        ok, rvec, tvec = cv2.solvePnP(object_points, image_points, camera_matrix, no_distortion)
        if not ok:
            raise ValueError("Keine Board-Pose loesbar")
        candidates = [(rvec, tvec)]
    refined = []
    for rvec, tvec in candidates:
        rvec, tvec = np.array(rvec, dtype=np.float64), np.array(tvec, dtype=np.float64)
        rvec, tvec = cv2.solvePnPRefineLM(
            object_points, image_points, camera_matrix, no_distortion, rvec, tvec
        )
        refined.append((reprojection_px(rvec, tvec), rvec, tvec))
    error_px, rvec, tvec = min(refined, key=lambda candidate: candidate[0])
    return from_rvec_tvec(rvec, tvec), error_px


def calibrate_from_samples(
    samples,
    image_size: tuple[int, int],
    spec: BoardSpec,
    board: Any = None,
    *,
    frame_id: str = "",
    model: str = PINHOLE,
) -> CameraCalibration:
    """Compute the calibration from collected board samples.

    No image, no GUI, no camera -- just points. The same function runs
    behind both the live UI and the image-folder path.

    `model` picks the lens model (`calibration.PINHOLE` or
    `calibration.FISHEYE`), see `tagloc.lens.calibrate`.
    """
    from . import lens

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

    rms, camera_matrix, distortion = lens.calibrate(
        object_points, image_points, image_size, model
    )
    return CameraCalibration(
        camera_matrix=camera_matrix,
        distortion=distortion,
        image_size=(int(image_size[0]), int(image_size[1])),
        frame_id=frame_id,
        rms_reprojection_error=rms,
        sample_count=len(object_points),
        board=spec.as_dict(),
        model=model,
    )


__all__ = [
    "CHARUCO",
    "CHESSBOARD",
    "BoardSample",
    "BoardSpec",
    "build_board",
    "calibrate_from_samples",
    "canonical_chessboard_corners",
    "chessboard_is_unambiguous",
    "chessboard_object_points",
    "estimate_board_pose",
    "compute_coverage",
    "detect_board",
    "detect_partial_board",
    "diagnose_board",
    "partial_patterns",
]
