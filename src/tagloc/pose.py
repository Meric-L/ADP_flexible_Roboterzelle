"""From the image corners to the pose: `T_cam_tag` together with quality measures.

The prototype (`src/apriltag/detect_apriltags.py`, removed on 2026-10-30) did two
things worse:

1. No undistortion. There `D` was only passed to `drawFrameAxes`; the pose was
   computed on the distorted image (doc/projektdoku/arbeitsplaene/altlasten.md, "Was nicht hier
   steht"). Here the corners are first undistorted and then solved with an ideal
   pinhole model.
2. No statement of ambiguity. A square marker seen at a shallow angle has two
   almost equally good solutions; `solvePnPGeneric` returns both, and the ratio of
   their residual errors is the most honest warning there is.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .calibration import CameraCalibration
from .geometry import Pose, compose, from_rvec_tvec, rvec_from_rotation
from .observations import TagObservation, TagPose

_log = logging.getLogger(__name__)


def tag_object_points(size_m: float) -> np.ndarray:
    """Return the four tag corners in the tag frame, in metres.

    The order matches `TagObservation.corners` (clockwise, top-left first) and what
    `SOLVEPNP_IPPE_SQUARE` expects. The tag origin lies at the centre, +Z points
    out of the tag.
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
    """Undistort image corners to the ideal pinhole model.

    `P=K` brings the result back to pixels, so that the pose can afterwards be
    solved with zero distortion -- independent of the lens model of the
    calibration (pinhole or fisheye, see `tagloc.lens`).
    """
    from . import lens

    return lens.undistort_points(corners, calibration)


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
    """Solve a tag pose from already prepared points and camera data."""
    import cv2

    flags = cv2.SOLVEPNP_IPPE_SQUARE
    found, rvecs, tvecs, errors = cv2.solvePnPGeneric(
        object_points, image_points, camera_matrix, no_distortion, flags=flags
    )
    if not found:
        raise ValueError(f"Keine Pose fuer Tag {observation.tag_id} loesbar")
    # IPPE returns both candidates without refinement. Refine both with
    # Levenberg-Marquardt and take the one with the smaller reprojection error --
    # like `boards.estimate_board_pose`.
    candidates = []
    for candidate_rvec, candidate_tvec in zip(rvecs, tvecs):
        candidate_rvec = np.array(candidate_rvec, dtype=np.float64)
        candidate_tvec = np.array(candidate_tvec, dtype=np.float64)
        candidate_rvec, candidate_tvec = cv2.solvePnPRefineLM(
            object_points,
            image_points,
            camera_matrix,
            no_distortion,
            candidate_rvec,
            candidate_tvec,
        )
        candidates.append(
            (
                _reprojection_error_px(
                    object_points, image_points, candidate_rvec, candidate_tvec, camera_matrix
                ),
                candidate_rvec,
                candidate_tvec,
            )
        )
    _, rvec, tvec = min(candidates, key=lambda candidate: candidate[0])
    ambiguity = 0.0
    if errors is not None and len(errors) >= 2:
        # The ratio is deliberately taken from the **raw** IPPE errors, not from the
        # refined ones: after refinement, both solutions explain a tag with only four
        # corners almost equally well, the ratio would rise above `AMBIGUITY_THRESHOLD`
        # (0.6), and `measure_tag` would reject unambiguous tags. The threshold is
        # measured on the raw ratio.
        best = float(np.asarray(errors).reshape(-1)[0])
        second = float(np.asarray(errors).reshape(-1)[1])
        # In (0, 1]: close to 1 means both solutions explain the image equally well.
        # A second error of 0 only occurs if the first one is 0 too -- that would be
        # a degenerate arrangement.
        ambiguity = best / second if second > 1e-12 else 1.0

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
    """Estimate the poses of all detected tags.

    The tag size comes **per tag from the map**, not as a global constant:
    world-sheet and module tags differ in size, and a wrong size scales the
    distance linearly without otherwise standing out.

    Tags above `max_reprojection_error_px` are discarded and logged, not passed
    through silently.
    """
    import cv2

    from . import lens

    expected_errors = _solver_exceptions()
    if not observations:
        return []
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
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

    # OpenCV can undistort all corners of an image in one call. If one point is
    # defective, the previous single-tag error path is kept.
    try:
        points = np.concatenate([item[2] for item in prepared])
        undistorted = lens.undistort_points(points, calibration).reshape(-1, 4, 2)
    except cv2.error:
        undistorted = None

    poses: list[TagPose] = []
    for index, (observation, object_points, corners) in enumerate(prepared):
        try:
            image_points = (
                undistorted[index]
                if undistorted is not None
                else lens.undistort_points(corners, calibration).reshape(4, 2)
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
    """Return the exceptions that a single observation can plausibly raise.

    Deliberately narrow: an `AttributeError` or `TypeError` would be a bug in the
    code and would recur for every tag in every image -- a repeated log line instead
    of a clear crash. Such errors should propagate.
    """
    try:
        import cv2

        return (ValueError, cv2.error)
    except Exception:
        return (ValueError,)


@dataclass(frozen=True)
class JointCameraPose:
    """`T_cam_world` from one PnP over all corners of several world tags."""

    pose_cam_world: Pose
    #: RMS over all corners of all involved tags, in pixels.
    reprojection_error_px: float
    #: RMS of the four corners of each tag under the joint pose, per tag.
    tag_errors_px: dict[int, float]
    tag_ids: tuple[int, ...]


def solve_camera_from_world_tags(
    tag_poses: Sequence[TagPose],
    world_poses: dict[int, Pose],
    sizes_m: dict[int, float],
    calibration: CameraCalibration,
    initial_pose_cam_world: Pose | None = None,
) -> JointCameraPose | None:
    """Camera pose from **all** corners of all world tags in one PnP.

    A separate PnP per tag, from four corners of a 20 cm base, is poorly determined
    at 2-3 m distance in tilt -- a 1 degree tilt error amounts to 5 cm on the floor.
    The mean of such individual estimates inherits this. A PnP over the corners of
    all world tags, in contrast, has the whole cell as its base.

    The corners are undistorted as for a single tag (lens model from `tagloc.lens`)
    and then solved with ideal pinhole. Starting values: the passed-in estimate
    (typically the mean of the single tags) and `SOLVEPNP_SQPNP` or `IPPE`; each
    is refined with Levenberg-Marquardt, and the one with the smaller error wins.

    Ambiguous tags **are included**: the ambiguity belongs to the single-tag
    solution from four corners (two tilts explain it equally well), not to the
    corners themselves. A tag seen straight on from above is often "ambiguous" --
    in the joint PnP over all corners this disappears. Tags without a world pose
    are left out. Fewer than two usable tags returns `None` -- then there is
    nothing to connect.
    """
    import cv2

    used = [
        tag_pose
        for tag_pose in sorted(tag_poses, key=lambda item: item.tag_id)
        if tag_pose.tag_id in world_poses and tag_pose.observation is not None
    ]
    if len(used) < 2:
        return None

    object_points, image_points = _world_correspondences(used, world_poses, sizes_m, calibration)
    objects = np.concatenate(object_points).reshape(-1, 1, 3)
    images = np.concatenate(image_points).reshape(-1, 1, 2)
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    no_distortion = np.zeros(5, dtype=np.float64)

    starts = _pnp_starts(objects, images, camera_matrix, no_distortion, initial_pose_cam_world)
    if not starts:
        return None

    best = None
    for rvec, tvec in starts:
        rvec, tvec = cv2.solvePnPRefineLM(
            objects, images, camera_matrix, no_distortion, rvec.copy(), tvec.copy()
        )
        error = _reprojection_error_px(objects, images, rvec, tvec, camera_matrix)
        if best is None or error < best[0]:
            best = (error, rvec, tvec)
    error, rvec, tvec = best

    tag_errors = {}
    for index, tag_pose in enumerate(used):
        tag_errors[int(tag_pose.tag_id)] = _reprojection_error_px(
            object_points[index], image_points[index], rvec, tvec, camera_matrix
        )
    _log.debug("Kamerapose gemeinsam aus %d Welttags, RMS %.2f px", len(used), error)
    return JointCameraPose(
        pose_cam_world=from_rvec_tvec(rvec, tvec),
        reprojection_error_px=error,
        tag_errors_px=tag_errors,
        tag_ids=tuple(int(tag_pose.tag_id) for tag_pose in used),
    )


def _world_correspondences(
    used: Sequence[TagPose],
    world_poses: dict[int, Pose],
    sizes_m: dict[int, float],
    calibration: CameraCalibration,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """For each tag, return the four corners in the world frame and the undistorted
    image corners, in the same order.
    """
    object_points = []
    image_points = []
    for tag_pose in used:
        corners_tag = tag_object_points(sizes_m[tag_pose.tag_id])
        corners_world = [
            compose(world_poses[tag_pose.tag_id], _translation(corner))[:3, 3]
            for corner in corners_tag
        ]
        object_points.append(np.asarray(corners_world, dtype=np.float64))
        image_points.append(undistort_corners(tag_pose.observation.corners, calibration))
    return object_points, image_points


def _pnp_starts(
    objects: np.ndarray,
    images: np.ndarray,
    camera_matrix: np.ndarray,
    no_distortion: np.ndarray,
    initial_pose_cam_world: Pose | None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return starting values `(rvec, tvec)` for the joint PnP.

    First the passed-in estimate, then the first solution of `SQPNP` or `IPPE`
    that OpenCV returns. Empty if none is obtained.
    """
    import cv2

    starts = []
    if initial_pose_cam_world is not None:
        start = np.asarray(initial_pose_cam_world, dtype=np.float64)
        starts.append((rvec_from_rotation(start[:3, :3]).reshape(3, 1), start[:3, 3].reshape(3, 1)))
    for name in ("SOLVEPNP_SQPNP", "SOLVEPNP_IPPE"):
        flag = getattr(cv2, name, None)
        if flag is None:
            continue
        try:
            found, rvec, tvec = cv2.solvePnP(objects, images, camera_matrix, no_distortion, flags=flag)
        except cv2.error:
            continue
        if found:
            starts.append((rvec, tvec))
            break
    return starts


def _translation(point) -> Pose:
    """Return a pose that is a pure translation by `point`."""
    pose = np.eye(4, dtype=np.float64)
    pose[:3, 3] = point
    return pose


def poses_by_tag(tag_poses: Sequence[TagPose]) -> dict[int, Pose]:
    """Return `{tag_id: T_cam_tag}`, the input format of `tagmap.place_tags`."""
    return {tag_pose.tag_id: tag_pose.pose_cam_tag for tag_pose in tag_poses}


__all__ = [
    "JointCameraPose",
    "TagPose",
    "estimate_tag_pose",
    "estimate_tag_poses",
    "poses_by_tag",
    "solve_camera_from_world_tags",
    "tag_object_points",
    "undistort_corners",
]
