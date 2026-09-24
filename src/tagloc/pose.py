"""From image corners to pose: `T_cam_tag` plus quality measures.

The prototype (`src/apriltag/detect_apriltags.py`) does two things worse:

1. No undistortion. `D` there only went to `drawFrameAxes`; the pose was
   computed on the distorted image (doc/projektdoku/altlasten.md, "Was nicht hier
   steht"). Here, corners are undistorted first, then solved with an ideal
   pinhole model.
2. No ambiguity reporting. A square marker seen at a shallow angle has two
   nearly equally good solutions; `solvePnPGeneric` returns both, and the
   ratio of their residual errors is the most honest warning available.
"""

import logging
from collections.abc import Sequence
from typing import Any

import numpy as np

from .calibration import CameraCalibration
from .geometry import Pose, from_rvec_tvec
from .observations import TagObservation, TagPose

_log = logging.getLogger(__name__)


def tag_object_points(size_m: float) -> np.ndarray:
    """Return the four tag corners in the tag frame, in meters.

    Order matches `TagObservation.corners` (clockwise from top-left) and
    what `SOLVEPNP_IPPE_SQUARE` expects. The tag origin is at the centre,
    +Z points out of the tag.
    """
    half = float(size_m) / 2.0
    return np.array(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float64,
    )


def undistort_corners(corners, calibration: CameraCalibration) -> np.ndarray:
    """Undistort image corners onto the ideal pinhole model.

    `P=K` puts the result back into pixels so it can be solved with zero
    distortion afterwards.
    """
    import cv2

    points = np.asarray(corners, dtype=np.float64).reshape(-1, 1, 2)
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    undistorted = cv2.undistortPoints(
        points, camera_matrix, np.asarray(calibration.distortion, dtype=np.float64),
        P=camera_matrix,
    )
    return undistorted.reshape(-1, 2)


def _reprojection_error_px(object_points, image_points, rvec, tvec, camera_matrix) -> float:
    """Return the RMS distance between measured and reprojected corners."""
    import cv2

    projected, _ = cv2.projectPoints(
        object_points, rvec, tvec, camera_matrix, np.zeros(5, dtype=np.float64)
    )
    difference = projected.reshape(-1, 2) - np.asarray(image_points).reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(difference**2, axis=1))))


def _solve_tag_pose(
    observation: TagObservation,
    object_points: np.ndarray,
    image_points: np.ndarray,
    camera_matrix: np.ndarray,
    no_distortion: np.ndarray,
) -> TagPose:
    """Löst eine Tag-Pose aus bereits vorbereiteten Punkten und Kameradaten."""
    import cv2

    flags = getattr(cv2, "SOLVEPNP_IPPE_SQUARE", None)
    if flags is not None and hasattr(cv2, "solvePnPGeneric"):
        found, rvecs, tvecs, errors = cv2.solvePnPGeneric(
            object_points, image_points, camera_matrix, no_distortion, flags=flags
        )
        if not found:
            raise ValueError(f"Keine Pose fuer Tag {observation.tag_id} loesbar")
        rvec, tvec = rvecs[0], tvecs[0]
        ambiguity = 0.0
        if errors is not None and len(errors) >= 2:
            best = float(np.asarray(errors).reshape(-1)[0])
            second = float(np.asarray(errors).reshape(-1)[1])
            # In (0, 1]: near 1 means both solutions explain the image
            # equally well. A second error of 0 can't occur unless the first
            # is too -- that would mean a degenerate configuration.
            ambiguity = best / second if second > 1e-12 else 1.0
    else:  # pragma: no cover - very old OpenCV version
        _log.debug("SOLVEPNP_IPPE_SQUARE nicht verfuegbar, weiche auf ITERATIVE aus")
        ok, rvec, tvec = cv2.solvePnP(
            object_points, image_points, camera_matrix, no_distortion
        )
        if not ok:
            raise ValueError(f"Keine Pose fuer Tag {observation.tag_id} loesbar")
        # This path yields only one solution, so ambiguity is unknown. Report
        # ambiguous rather than unique as the safer default: a falsely
        # flagged tag costs a check, a falsely unique one costs a bad move.
        ambiguity = 1.0

    error_px = _reprojection_error_px(
        object_points, image_points, rvec, tvec, camera_matrix
    )
    return TagPose(
        tag_id=observation.tag_id,
        pose_cam_tag=from_rvec_tvec(rvec, tvec),
        reprojection_error_px=error_px,
        ambiguity_ratio=ambiguity,
        observation=observation,
        attributes={"sideLengthPx": observation.side_length_px()},
    )


def estimate_tag_pose(
    observation: TagObservation, size_m: float, calibration: CameraCalibration
) -> TagPose:
    """Estimate `T_cam_tag` for a single tag."""
    return _solve_tag_pose(
        observation,
        tag_object_points(size_m),
        undistort_corners(observation.corners, calibration),
        np.asarray(calibration.camera_matrix, dtype=np.float64),
        np.zeros(5, dtype=np.float64),
    )


def estimate_tag_poses(
    observations: Sequence[TagObservation],
    calibration: CameraCalibration,
    *,
    tag_map: Any = None,
    default_size_m: float = 0.05,
    max_reprojection_error_px: float | None = None,
) -> list[TagPose]:
    """Estimate poses for all found tags.

    Tag size comes **per tag from the map**, not a global constant: world
    board and module tags differ in size, and a wrong size scales distance
    linearly without otherwise standing out.

    Tags above `max_reprojection_error_px` are dropped and logged, not
    passed through silently.
    """
    import cv2

    expected_errors = _solver_exceptions()
    if not observations:
        return []
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    distortion = np.asarray(calibration.distortion, dtype=np.float64)
    no_distortion = np.zeros(5, dtype=np.float64)
    object_points_by_size: dict[float, np.ndarray] = {}
    prepared: list[tuple[TagObservation, np.ndarray, np.ndarray]] = []
    for observation in observations:
        size_m = (
            tag_map.size_for(observation.tag_id, default_size_m)
            if tag_map is not None
            else default_size_m
        )
        try:
            corners = np.asarray(observation.corners, dtype=np.float64).reshape(4, 1, 2)
        except expected_errors:
            _log.info("Pose fuer Tag %d nicht loesbar", observation.tag_id, exc_info=True)
            continue
        object_points = object_points_by_size.get(size_m)
        if object_points is None:
            object_points = tag_object_points(size_m)
            object_points_by_size[size_m] = object_points
        prepared.append((observation, object_points, corners))
    if not prepared:
        return []

    # OpenCV kann alle Ecken eines Bildes in einem Aufruf entzerren. Bei einem
    # defekten Punkt bleibt der bisherige Einzel-Tag-Fehlerpfad erhalten.
    try:
        points = np.concatenate([item[2] for item in prepared])
        undistorted = cv2.undistortPoints(
            points, camera_matrix, distortion, P=camera_matrix
        ).reshape(-1, 4, 2)
    except cv2.error:
        undistorted = None

    poses: list[TagPose] = []
    for index, (observation, object_points, corners) in enumerate(prepared):
        try:
            image_points = (
                undistorted[index]
                if undistorted is not None
                else cv2.undistortPoints(
                    corners, camera_matrix, distortion, P=camera_matrix
                ).reshape(4, 2)
            )
            tag_pose = _solve_tag_pose(
                observation, object_points, image_points, camera_matrix, no_distortion
            )
        except expected_errors:
            _log.info("Pose fuer Tag %d nicht loesbar", observation.tag_id, exc_info=True)
            continue
        if not tag_pose.is_usable(max_reprojection_error_px):
            _log.info(
                "Tag %d verworfen: Reprojektionsfehler %s px (Schranke %s px)",
                tag_pose.tag_id,
                f"{tag_pose.reprojection_error_px:.2f}",
                max_reprojection_error_px,
            )
            continue
        poses.append(tag_pose)
    return poses


def _solver_exceptions() -> tuple[type[BaseException], ...]:
    """Return the exceptions a single observation can plausibly raise.

    Deliberately narrow: an `AttributeError` or `TypeError` would be a bug
    and would recur for every tag in every frame -- a repeated log line
    instead of a clear crash. Such errors should propagate.
    """
    try:
        import cv2

        return (ValueError, cv2.error)
    except Exception:
        return (ValueError,)


def poses_by_tag(tag_poses: Sequence[TagPose]) -> dict[int, Pose]:
    """Return `{tag_id: T_cam_tag}`, the input format of `tagmap.place_tags`."""
    return {tag_pose.tag_id: tag_pose.pose_cam_tag for tag_pose in tag_poses}


__all__ = [
    "TagPose",
    "estimate_tag_pose",
    "estimate_tag_poses",
    "poses_by_tag",
    "tag_object_points",
    "undistort_corners",
]
