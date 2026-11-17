"""Hand-eye: where the camera sits on the robot, and how an anchor is derived from it.

The camera is **rigidly** attached to the robot. Only the robot as a whole moves.
`T_flange_cam` is therefore a constant of the hardware and is calibrated exactly
**once**, not per operation.

This closes the gap the purely optical path leaves open. A world tag only has to
be seen **once, at the start** of a localization run. If the hand camera then
gets close to a module, the next world tag is long out of view. The kinematics
carry the pose from there.

```
Once per run (anchoring):
    T_world_base = T_world_cam · inv(T_flange_cam) · inv(T_base_flange)

Afterwards, per module:
    T_world_cam  = T_world_base · T_base_flange(now) · T_flange_cam
```

No OpenCV, the solver included. `cv2.calibrateHandEye` would be the obvious
choice, but OpenCV 5.0 no longer exports it to Python. Only the
`CALIB_HAND_EYE_*` constants remain. The repo deliberately supports both 4.x and
5.x (doc/projektdoku/arbeitsplaene/altlasten.md D3). A version branch in a spot that runs once
in the life of the cell would be worse than thirty lines of math. This follows
the same approach as `geometry.from_rvec_tvec`, which computes Rodrigues in numpy
instead of calling `cv2.Rodrigues`.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import lens
from .boards import BoardSpec, chessboard_object_points
from .calibration import CameraCalibration
from .geometry import (
    Pose,
    average_poses,
    compose,
    from_rotation_translation,
    invert,
    pose_from_dict,
    pose_to_dict,
    rotation_distance_rad,
    rotation_from_rvec,
    rvec_from_rotation,
    translation_distance_m,
)
from .jsonio import finite_or_none, utc_now_z

SCHEMA = "wsc.vision.handeye/1"
#: Record of a session (`data/handeye/sessions/<time>/session.json`). It is written by
#: `vision_server.handeye_session`, and `tagloc.cli.calibrate_handeye --session`
#: recomputes it offline.
SESSION_SCHEMA = "wsc.vision.handeye.session/1"
SESSION_FILE = "session.json"
#: Digits to which `save_hand_eye` rounds the metrics in the file.
FILE_DIGITS = 6

#: Hand-eye needs motion around more than one axis, otherwise the rotation is not
#: determined. Three poses are the mathematical minimum. The calibration run should
#: record clearly more.
MIN_SAMPLES = 3

#: Below this eigenvalue the distribution of rotation axes counts as degenerate.
#: The robot then has essentially rotated around only one axis.
_DEGENERATE_EIGENVALUE = 1e-12

#: From this much rotation about an axis, the axis counts as "clearly rotated".
#: Below that, the noise of the tag measurement also affects the rotation.
AXIS_THRESHOLD_DEG = 5.0
#: Minimum number of clearly rotated axes so that `T_flange_cam` is determined.
MIN_AXES = 3


@dataclass(frozen=True)
class HandEye:
    """The constant offset from flange to camera optics, with its origin."""

    pose_flange_cam: Pose
    frame_id: str = ""
    calibration_id: str = ""
    #: Spread of the reconstructed target location over all captures of the
    #: calibration run. The tag is fixed, so it must come out at the same place
    #: from every robot pose. If it does not, the calibration is wrong.
    rms_position_m: float = float("nan")
    rms_rotation_deg: float = float("nan")
    sample_count: int = 0
    #: Largest deviation (not RMS) among the captures used. Separate from the RMS
    #: since 22.10.2026. Older files do not have it.
    max_position_m: float = float("nan")
    max_rotation_deg: float = float("nan")
    #: Since 28.10.2026, only for board calibration with joint refinement
    #: (`refine_hand_eye`). Otherwise NaN or empty. Older files do not have these.
    #: Reprojection RMS of all board corners after refinement (px).
    refined_reprojection_rms_px: float = float("nan")
    #: Estimated board scale against the nominal size (1.0 = matches).
    board_scale: float = float("nan")
    #: Leave-one-out prediction error (RMS over the captures), in px and m.
    loo_error_rms_px: float = float("nan")
    loo_error_rms_m: float = float("nan")
    #: Where the intrinsics came from (`calibration`, `realsense_factory`, ...).
    intrinsics_source: str = ""


@dataclass(frozen=True)
class RobotAnchor:
    """Where the robot base sits in the world frame, with the origin of the anchor."""

    pose_world_base: Pose
    #: World tag the anchor was taken from.
    world_tag_id: int = -1
    #: Quality of the world tag localization at the moment of anchoring. An anchor
    #: is never better than the measurement it comes from.
    spread_m: float = 0.0
    spread_rad: float = 0.0


def anchor_world_base(
    pose_world_cam: Pose, pose_base_flange: Pose, pose_flange_cam: Pose
) -> Pose:
    """Return `T_world_base` from a world tag sighting and the robot pose.

    `T_world_cam = T_world_base · T_base_flange · T_flange_cam`, solved for
    `T_world_base`. This is the anchoring step. From here on the robot knows where
    its own base is in the world frame, and it no longer needs the world tag in view.
    """
    return compose(pose_world_cam, invert(pose_flange_cam), invert(pose_base_flange))


def camera_pose_from_robot(
    pose_world_base: Pose, pose_base_flange: Pose, pose_flange_cam: Pose
) -> Pose:
    """Return `T_world_cam` from the anchor and the current robot pose.

    The path without a world tag in view. The accuracy now depends on the anchor
    and on the kinematics, no longer on the field of view.
    """
    return compose(pose_world_base, pose_base_flange, pose_flange_cam)


def target_spread(
    poses_base_flange: Sequence[Pose],
    poses_cam_tag: Sequence[Pose],
    pose_flange_cam: Pose,
) -> tuple[float, float]:
    """Return how far the reconstructed target location spreads, as `(m, rad)`.

    The tag of the calibration run is fixed. So
    `T_base_tag = T_base_flange(i) · T_flange_cam · T_cam_tag(i)` must give the same
    pose for every `i`. The spread around it is the quality measure. It is also the
    only one that needs no second measurement chain.
    """
    reconstructed = [
        compose(pose_base_flange, pose_flange_cam, pose_cam_tag)
        for pose_base_flange, pose_cam_tag in zip(poses_base_flange, poses_cam_tag)
    ]
    if len(reconstructed) < 2:
        return (0.0, 0.0)
    mean = average_poses(reconstructed)
    return (
        max(translation_distance_m(mean, pose) for pose in reconstructed),
        max(rotation_distance_rad(mean, pose) for pose in reconstructed),
    )


def _motion_pairs(
    poses_base_flange: Sequence[Pose], poses_cam_tag: Sequence[Pose]
) -> list[tuple[Pose, Pose]]:
    """Return the motion pairs `(A, B)` of the hand-eye equation `A·X = X·B`.

    The target location is fixed, so for any two captures `i`, `j`:

        T_base_flange(i) · X · T_cam_tag(i) = T_base_flange(j) · X · T_cam_tag(j)

    rearranged to `A · X = X · B` with

        A = inv(T_base_flange(j)) · T_base_flange(i)
        B = T_cam_tag(j) · inv(T_cam_tag(i))

    The unknown target location cancels out. So there is no need to know where the
    tag is, only that it does not move.
    """
    pairs = []
    count = len(poses_base_flange)
    for first in range(count):
        for second in range(first + 1, count):
            motion_flange = compose(
                invert(poses_base_flange[second]), poses_base_flange[first]
            )
            motion_cam = compose(poses_cam_tag[second], invert(poses_cam_tag[first]))
            pairs.append((motion_flange, motion_cam))
    return pairs


def solve_hand_eye(
    poses_base_flange: Sequence[Pose], poses_cam_tag: Sequence[Pose]
) -> tuple[Pose, float, float]:
    """Solve `T_flange_cam` from N robot poses that look at one fixed tag.

    Capture setup: the tag stays put, and the robot moves to N clearly different
    poses and measures it each time. Pure translation is not enough. Without
    rotation about several axes the rotation is not determined.

    Method after Park and Martin. The rotation axes of the motion pairs span
    `M = sum(beta · alpha^T)`. From that, `R_X = (M^T M)^(-1/2) · M^T`. The
    translation is then obtained as a linear least-squares problem,
    `(R_A - I) · t_X = R_X · t_B - t_A`.

    Returns `(T_flange_cam, spread in m, spread in degrees)`.
    """
    if len(poses_base_flange) != len(poses_cam_tag):
        raise ValueError(
            f"Hand-Auge: {len(poses_base_flange)} Roboterposen zu "
            f"{len(poses_cam_tag)} Tag-Messungen"
        )
    if len(poses_base_flange) < MIN_SAMPLES:
        raise ValueError(
            f"Hand-Auge braucht mindestens {MIN_SAMPLES} Posen, "
            f"bekommen: {len(poses_base_flange)}. Der Roboter muss dabei um "
            "mehrere Achsen drehen, reines Verschieben genuegt nicht."
        )

    pairs = _motion_pairs(poses_base_flange, poses_cam_tag)

    accumulator = np.zeros((3, 3), dtype=np.float64)
    for motion_flange, motion_cam in pairs:
        alpha = rvec_from_rotation(motion_flange[:3, :3]).reshape(3, 1)
        beta = rvec_from_rotation(motion_cam[:3, :3]).reshape(3, 1)
        accumulator += beta @ alpha.T

    eigenvalues, eigenvectors = np.linalg.eigh(accumulator.T @ accumulator)
    if float(np.min(eigenvalues)) < _DEGENERATE_EIGENVALUE:
        raise ValueError(
            "Hand-Auge: die Roboterposen drehen im Wesentlichen um nur eine "
            "Achse. Die Kalibrierfahrt braucht Drehungen um mehrere Achsen, "
            "sonst ist die Rotation nicht bestimmt."
        )
    inverse_sqrt = eigenvectors @ np.diag(1.0 / np.sqrt(eigenvalues)) @ eigenvectors.T
    rotation = inverse_sqrt @ accumulator.T

    left = np.zeros((3 * len(pairs), 3), dtype=np.float64)
    right = np.zeros((3 * len(pairs),), dtype=np.float64)
    for index, (motion_flange, motion_cam) in enumerate(pairs):
        row = slice(3 * index, 3 * index + 3)
        left[row, :] = motion_flange[:3, :3] - np.eye(3)
        right[row] = rotation @ motion_cam[:3, 3] - motion_flange[:3, 3]
    translation, *_ = np.linalg.lstsq(left, right, rcond=None)

    pose_flange_cam = np.eye(4, dtype=np.float64)
    pose_flange_cam[:3, :3] = rotation
    pose_flange_cam[:3, 3] = translation

    spread_m, spread_rad = target_spread(poses_base_flange, poses_cam_tag, pose_flange_cam)
    return pose_flange_cam, spread_m, math.degrees(spread_rad)


#: Outliers: a capture whose board pose deviates by more than `OUTLIER_MAD_FACTOR`
#: times the robust spread (MAD) above the median of the deviations. It is never
#: below the floors, though. With clean data the MAD is almost zero, and without a
#: floor every hundredth of a millimeter would count as an outlier.
OUTLIER_MAD_FACTOR = 3.0
OUTLIER_FLOOR_M = 0.002
OUTLIER_FLOOR_DEG = 0.3
#: At most this fraction of the captures is rejected.
OUTLIER_MAX_FRACTION = 0.25
#: At least this many captures are always kept.
MIN_KEPT = 4


@dataclass(frozen=True)
class HandEyeSolution:
    """Result of `solve_hand_eye_robust`."""

    pose_flange_cam: Pose
    #: True RMS of the board pose deviations from the mean, over the captures used.
    rms_position_m: float
    rms_rotation_deg: float
    #: Largest deviation among the captures used.
    max_position_m: float
    max_rotation_deg: float
    #: For each input capture, `(deviation in m, in degrees)` of the board pose from
    #: the mean of the used captures. This includes the rejected ones.
    residuals: tuple[tuple[float, float], ...]
    #: Indices of the rejected input captures, in the order they were rejected.
    rejected: tuple[int, ...]
    used_count: int


def _reconstruct(poses_base_flange, poses_cam_tag, pose_flange_cam) -> list[Pose]:
    """Return `T_base_tag = T_base_flange · T_flange_cam · T_cam_tag` for each capture."""
    return [
        compose(flange, pose_flange_cam, tag)
        for flange, tag in zip(poses_base_flange, poses_cam_tag)
    ]


def _distance(centre: Pose, pose: Pose) -> tuple[float, float]:
    return (
        translation_distance_m(centre, pose),
        math.degrees(rotation_distance_rad(centre, pose)),
    )


def _threshold(values: list[float], floor: float) -> float:
    """Return the median plus `OUTLIER_MAD_FACTOR` robust standard deviations, at least `floor`."""
    median = float(np.median(values))
    mad = float(np.median(np.abs(np.asarray(values) - median)))
    return max(floor, median + OUTLIER_MAD_FACTOR * 1.4826 * mad)


def solve_hand_eye_robust(
    poses_base_flange: Sequence[Pose],
    poses_cam_tag: Sequence[Pose],
    *,
    reject: bool = True,
) -> HandEyeSolution:
    """`solve_hand_eye` with outlier checks and honest quality numbers.

    The tag (or board) is fixed, so `T_base_tag` must come out as the same pose
    from every capture. The residual of a capture is its distance from the mean
    of the **other** captures (leave-one-out). A bad capture therefore does not
    pull the mean towards itself. If the largest residual is above the threshold,
    exactly that capture is rejected and the problem is solved again, until
    nothing stands out. At most `OUTLIER_MAX_FRACTION` of the captures are
    rejected, and never fewer than `MIN_KEPT` remain. If the re-solve fails (the
    remaining captures do not rotate enough about several axes), the last good
    solution is kept.

    Raises:
        ValueError: As `solve_hand_eye`, already for the first solution.
    """
    flanges = list(poses_base_flange)
    tags = list(poses_cam_tag)
    pose, _, _ = solve_hand_eye(flanges, tags)
    used = list(range(len(flanges)))
    rejected: list[int] = []
    max_rejected = int(len(flanges) * OUTLIER_MAX_FRACTION)

    while reject and len(rejected) < max_rejected and len(used) > max(MIN_SAMPLES, MIN_KEPT):
        reconstructed = _reconstruct(flanges, tags, pose)
        loo = {
            i: _distance(
                average_poses([reconstructed[j] for j in used if j != i]), reconstructed[i]
            )
            for i in used
        }
        threshold_m = _threshold([loo[i][0] for i in used], OUTLIER_FLOOR_M)
        threshold_deg = _threshold([loo[i][1] for i in used], OUTLIER_FLOOR_DEG)
        worst = max(used, key=lambda i: max(loo[i][0] / threshold_m, loo[i][1] / threshold_deg))
        if max(loo[worst][0] / threshold_m, loo[worst][1] / threshold_deg) <= 1.0:
            break
        trial = [i for i in used if i != worst]
        try:
            trial_pose, _, _ = solve_hand_eye([flanges[i] for i in trial], [tags[i] for i in trial])
        except ValueError:
            break
        used, pose = trial, trial_pose
        rejected.append(worst)

    reconstructed = _reconstruct(flanges, tags, pose)
    centre = average_poses([reconstructed[i] for i in used])
    residuals = tuple(_distance(centre, pose_i) for pose_i in reconstructed)
    kept = [residuals[i] for i in used]
    return HandEyeSolution(
        pose_flange_cam=pose,
        rms_position_m=math.sqrt(sum(m * m for m, _ in kept) / len(kept)),
        rms_rotation_deg=math.sqrt(sum(d * d for _, d in kept) / len(kept)),
        max_position_m=max(m for m, _ in kept),
        max_rotation_deg=max(d for _, d in kept),
        residuals=residuals,
        rejected=tuple(rejected),
        used_count=len(used),
    )


# --- Joint refinement against the pixels (board only) --------------------------

#: Huber threshold in pixels: quadratic below it, linear weighting above it.
HUBER_DELTA_PX = 1.0
REFINE_MAX_ITERATIONS = 60
#: Step size of the numerical Jacobian: radians / meters / scale.
_JACOBIAN_STEP = 1e-6


@dataclass(frozen=True)
class HandEyeRefinement:
    """Result of `refine_hand_eye`."""

    #: `X = T_flange_cam`.
    pose_flange_cam: Pose
    #: `T_base_board` (board origin = first inner corner), i.e. how the board lies
    #: in the base frame. This is the `targetInBase` status.
    pose_base_board: Pose
    #: Scale of the square size against the nominal value (1.0 = board as specified).
    #: With `estimate_scale=False` it stays at the start value.
    board_scale: float
    #: RMS of the pixel distance (2D magnitude) over all corners of all captures,
    #: unweighted.
    reprojection_rms_px: float
    iterations: int
    converged: bool
    #: RMS pixel error per capture (in input order).
    sample_rms_px: tuple[float, ...] = ()


@dataclass(frozen=True)
class LeaveOneOutResult:
    """Result of `leave_one_out_errors`."""

    #: Per capture: RMS pixel error of the predicted corners.
    error_px: tuple[float, ...]
    #: Per capture: mean 3D distance of the predicted corners to the corners of the
    #: measured `T_cam_board`, in the camera frame, in meters.
    error_m: tuple[float, ...]
    rms_px: float
    rms_m: float


def _project_all(
    inverse_flanges: np.ndarray,
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    flange_cam: Pose,
    base_board: Pose,
    scale: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Corners of all captures: `(pixels (n, m, 2), camera frame (n, m, 3))`.

    `inv(F_i · X) · T_base_board · (s · P)`, without distortion.
    """
    to_camera = invert(flange_cam)[None, :, :] @ inverse_flanges @ base_board[None, :, :]
    points = scale * object_points
    camera = np.einsum("nij,mj->nmi", to_camera[:, :3, :3], points) + to_camera[:, None, :3, 3]
    depth = np.where(np.abs(camera[..., 2]) < 1e-9, 1e-9, camera[..., 2])
    pixels = np.stack(
        [
            camera_matrix[0, 0] * camera[..., 0] / depth + camera_matrix[0, 2],
            camera_matrix[1, 1] * camera[..., 1] / depth + camera_matrix[1, 2],
        ],
        axis=-1,
    )
    return pixels, camera


def _apply_increment(pose: Pose, delta: np.ndarray) -> Pose:
    """Apply a local increment `(rvec, t)`: `R' = exp(rvec) · R`, `t' = t + t_delta`."""
    rotation = rotation_from_rvec(delta[:3]) @ pose[:3, :3]
    return from_rotation_translation(rotation, pose[:3, 3] + delta[3:6])


def _huber_cost(errors: np.ndarray, delta: float) -> float:
    """Huber cost over the 2D magnitude of each corner."""
    quadratic = errors <= delta
    return float(
        np.sum(np.where(quadratic, 0.5 * errors**2, delta * (errors - 0.5 * delta)))
    )


@dataclass(frozen=True)
class _RefineProblem:
    """Observations and board points of `refine_hand_eye`, with the residual function.

    The state is a triple `(X, T_base_board, scale)`. A step is a vector of 13
    increments (`X` 6, board 6, scale 1). Without `estimate_scale` only the first
    12 are free.
    """

    inverse_flanges: np.ndarray
    points: np.ndarray
    matrix: np.ndarray
    observed: np.ndarray
    estimate_scale: bool

    @property
    def free(self) -> int:
        return 13 if self.estimate_scale else 12

    def residuals(self, x_pose, board_pose, scale) -> np.ndarray:
        """Projected minus observed corners, `(n, m, 2)`."""
        pixels, _ = _project_all(self.inverse_flanges, self.points, self.matrix, x_pose, board_pose, scale)
        return pixels - self.observed

    def step(self, x_pose, board_pose, scale, delta):
        """Return the state after applying the increment `delta`."""
        return (
            _apply_increment(x_pose, delta[:6]),
            _apply_increment(board_pose, delta[6:12]),
            scale + (delta[12] if self.estimate_scale else 0.0),
        )

    def jacobian(self, state, size: int) -> np.ndarray:
        """Numerical Jacobian (central differences) of the flattened residuals."""
        jacobian = np.empty((size, self.free), dtype=np.float64)
        for column in range(self.free):
            offset = np.zeros(13, dtype=np.float64)
            offset[column] = _JACOBIAN_STEP
            plus = self.residuals(*self.step(*state, offset)).reshape(-1)
            minus = self.residuals(*self.step(*state, -offset)).reshape(-1)
            jacobian[:, column] = (plus - minus) / (2.0 * _JACOBIAN_STEP)
        return jacobian


def _refine_problem(
    flanges: list[np.ndarray],
    corners_px: Sequence[np.ndarray],
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    estimate_scale: bool,
) -> _RefineProblem:
    """Check the inputs of `refine_hand_eye` and convert them to arrays.

    Raises:
        ValueError: Poses and corner sets do not match, too few captures,
            or the corner count does not match the board points.
    """
    if len(flanges) != len(corners_px):
        raise ValueError(f"Verfeinerung: {len(flanges)} Roboterposen zu {len(corners_px)} Eckensaetzen")
    if len(flanges) < MIN_SAMPLES:
        raise ValueError(f"Verfeinerung braucht mindestens {MIN_SAMPLES} Aufnahmen")
    observed = np.stack([np.asarray(c, dtype=np.float64).reshape(-1, 2) for c in corners_px])
    points = np.asarray(object_points, dtype=np.float64).reshape(-1, 3)
    if observed.shape[1] != points.shape[0]:
        raise ValueError("Verfeinerung: Eckenzahl passt nicht zu den Brettpunkten")
    matrix = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)
    inverse_flanges = np.stack([invert(f) for f in flanges])
    return _RefineProblem(inverse_flanges, points, matrix, observed, estimate_scale)


def refine_hand_eye(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    *,
    initial_flange_cam: Pose,
    initial_base_board: Pose,
    initial_scale: float = 1.0,
    estimate_scale: bool = True,
    huber_delta_px: float = HUBER_DELTA_PX,
    max_iterations: int = REFINE_MAX_ITERATIONS,
) -> HandEyeRefinement:
    """Refine `X`, `T_base_board` and the board scale against the pixels.

    The Park-Martin solution fixes the rotation and derives the translation from it.
    A small rotation error shifts `t_X` by millimeters. Here all board corners of
    all captures enter **jointly**:

        u_ik = K · (inv(F_i · X) · T_base_board · (s · P_k))   (perspective)

    compared with the undistorted pixels of the capture. Unknowns: `X` (6),
    `T_base_board` (6) and optionally `s` (1). Levenberg-Marquardt with a numerical
    Jacobian. The updates are local increments (rotation vector and translation)
    applied to the current pose. Huber weighting (IRLS) keeps single bad corners or
    captures out of the solution.

    `corners_px`: per capture `(N, 2)` **undistorted** corners in pixels
    (`lens.undistort_points`, `P=K`), in the order of `object_points` `(N, 3)`
    (board frame, nominal size). Start values: `initial_flange_cam` from
    `solve_hand_eye_robust`, `initial_base_board` from `estimate_base_board`.
    """
    flanges = [np.asarray(f, dtype=np.float64) for f in poses_base_flange]
    problem = _refine_problem(flanges, corners_px, object_points, camera_matrix, estimate_scale)
    free = problem.free

    flange_cam = np.asarray(initial_flange_cam, dtype=np.float64).copy()
    base_board = np.asarray(initial_base_board, dtype=np.float64).copy()
    scale = float(initial_scale)

    current = problem.residuals(flange_cam, base_board, scale)
    cost = _huber_cost(np.linalg.norm(current, axis=-1), huber_delta_px)
    damping = 1e-3
    converged = False
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        flat = current.reshape(-1)
        jacobian = problem.jacobian((flange_cam, base_board, scale), flat.size)
        norms = np.linalg.norm(current, axis=-1)
        # IRLS: Huber weight per corner, the same for both pixel components.
        corner_weight = np.where(norms <= huber_delta_px, 1.0, huber_delta_px / np.maximum(norms, 1e-12))
        weights = np.repeat(corner_weight.reshape(-1), 2)
        weighted = jacobian * weights[:, None]
        normal = jacobian.T @ weighted
        gradient = weighted.T @ flat
        improved = False
        while damping < 1e12:
            try:
                solved = np.linalg.solve(normal + damping * np.diag(np.diag(normal) + 1e-12), -gradient)
            except np.linalg.LinAlgError:
                damping *= 10.0
                continue
            delta = np.zeros(13, dtype=np.float64)
            delta[:free] = solved
            trial = problem.step(flange_cam, base_board, scale, delta)
            trial_residuals = problem.residuals(*trial)
            trial_cost = _huber_cost(np.linalg.norm(trial_residuals, axis=-1), huber_delta_px)
            if trial_cost < cost:
                relative = (cost - trial_cost) / max(cost, 1e-18)
                flange_cam, base_board, scale = trial
                current, cost = trial_residuals, trial_cost
                damping = max(damping / 3.0, 1e-12)
                improved = True
                converged = relative < 1e-10 or float(np.max(np.abs(delta[:free]))) < 1e-10
                break
            damping *= 4.0
        if not improved:
            # No step lowers the cost any more: minimum reached.
            converged = True
            break
        if converged:
            break

    errors = np.linalg.norm(current, axis=-1)
    return HandEyeRefinement(
        pose_flange_cam=flange_cam,
        pose_base_board=base_board,
        board_scale=float(scale),
        reprojection_rms_px=float(np.sqrt(np.mean(errors**2))),
        iterations=iterations,
        converged=converged,
        sample_rms_px=tuple(float(np.sqrt(np.mean(row**2))) for row in errors),
    )


def rotation_diversity(poses_base_flange: list[Pose]) -> tuple[float, int]:
    """Return `(largest rotation between two poses in degrees, number of rotation axes)`.

    The axes come from the rotation vectors of all motion pairs. Their singular
    values are normalized by the number of pairs. If the robot rotates about only
    one axis, all vectors lie on one line. Then `T_flange_cam` is not determined,
    no matter how many captures there are.
    """
    count = len(poses_base_flange)
    if count < 2:
        return 0.0, 0
    vectors = []
    spread = 0.0
    for first in range(count):
        for second in range(first + 1, count):
            motion = compose(invert(poses_base_flange[first]), poses_base_flange[second])
            vectors.append(rvec_from_rotation(motion[:3, :3]).reshape(3))
            spread = max(
                spread, rotation_distance_rad(poses_base_flange[first], poses_base_flange[second])
            )
    singular = np.linalg.svd(np.asarray(vectors), compute_uv=False) / math.sqrt(len(vectors))
    axes = int(np.sum(singular >= math.radians(AXIS_THRESHOLD_DEG)))
    return math.degrees(spread), axes


#: Floor of the refinement outlier threshold, in pixels. Below it no image is an
#: outlier, even if the MAD is almost zero.
REFINE_OUTLIER_FLOOR_PX = 1.0


def refine_hand_eye_robust(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    poses_cam_board: Sequence[Pose],
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    *,
    estimate_scale: bool = True,
    reject: bool = True,
    huber_delta_px: float = HUBER_DELTA_PX,
) -> tuple[HandEyeRefinement, tuple[int, ...], HandEyeSolution]:
    """Refinement with outlier checks **in pixels** (board only).

    The Park-Martin spread of the board pose is not suitable here. A scale error
    of the board inflates it, and that would reject good captures as outliers. So
    the per-capture RMS pixel error after the joint refinement is used instead,
    which also estimates the scale.

    Start: `solve_hand_eye_robust(..., reject=False)` and the averaged board pose.
    Then all captures are refined. If the worst RMS is above
    `max(REFINE_OUTLIER_FLOOR_PX, median + OUTLIER_MAD_FACTOR · 1.4826 · MAD)`,
    that capture is removed and the refinement is run again (warm start). At most
    `OUTLIER_MAX_FRACTION` of the captures are removed. Never fewer than `MIN_KEPT`
    remain, and the remaining poses never rotate about fewer than `MIN_AXES` axes.

    Returns `(refinement of the remaining captures, indices of the rejected input
    captures in rejection order, Park-Martin start)`. `HandEyeRefinement.sample_rms_px`
    refers to the remaining captures.

    Raises:
        ValueError: As `solve_hand_eye`.
    """
    flanges = list(poses_base_flange)
    corners = list(corners_px)
    boards = list(poses_cam_board)
    initial = solve_hand_eye_robust(flanges, boards, reject=False)

    def refine(indices: list[int], start: HandEyeRefinement | None) -> HandEyeRefinement:
        pose_x = start.pose_flange_cam if start else initial.pose_flange_cam
        board = (
            start.pose_base_board
            if start
            else estimate_base_board(
                [flanges[i] for i in indices], [boards[i] for i in indices], pose_x
            )
        )
        return refine_hand_eye(
            [flanges[i] for i in indices],
            [corners[i] for i in indices],
            object_points,
            camera_matrix,
            initial_flange_cam=pose_x,
            initial_base_board=board,
            initial_scale=start.board_scale if start else 1.0,
            estimate_scale=estimate_scale,
            huber_delta_px=huber_delta_px,
        )

    kept = list(range(len(flanges)))
    rejected: list[int] = []
    refinement = refine(kept, None)
    max_rejected = int(len(flanges) * OUTLIER_MAX_FRACTION)
    while reject and len(rejected) < max_rejected and len(kept) > max(MIN_SAMPLES, MIN_KEPT):
        errors = list(refinement.sample_rms_px)
        threshold = _threshold(errors, REFINE_OUTLIER_FLOOR_PX)
        worst = int(np.argmax(errors))
        if errors[worst] <= threshold:
            break
        trial = [i for i in kept if i != kept[worst]]
        if rotation_diversity([flanges[i] for i in trial])[1] < MIN_AXES:
            break
        try:
            trial_refinement = refine(trial, refinement)
        except (ValueError, np.linalg.LinAlgError):
            break
        rejected.append(kept[worst])
        kept, refinement = trial, trial_refinement
    return refinement, tuple(rejected), initial


def estimate_base_board(
    poses_base_flange: Sequence[Pose], poses_cam_board: Sequence[Pose], pose_flange_cam: Pose
) -> Pose:
    """Starting board pose: mean of `F_i · X · C_i` (as in `target_spread`)."""
    return average_poses(_reconstruct(poses_base_flange, poses_cam_board, pose_flange_cam))


def leave_one_out_errors(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    poses_cam_board: Sequence[Pose],
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    full: HandEyeRefinement,
    *,
    estimate_scale: bool = True,
    huber_delta_px: float = HUBER_DELTA_PX,
) -> LeaveOneOutResult:
    """Prediction error per capture: refine without it, then predict its corners.

    For each capture `i`, `refine_hand_eye` is run without `i`. It warm-starts from
    the full solution `full`. It then predicts where the corners in `i` should lie.
    This is a more honest measure than the spread. A fixed offset that acts the same
    in all captures stays invisible there. A poorly determined `X`, however, predicts
    an unseen view badly.

    `error_px`: RMS distance to the undistorted pixel corners of `i`.
    `error_m`: mean 3D distance in the camera frame between the predicted corners
    (`inv(F_i · X) · T_base_board · s · P`) and the corners of the measured board
    pose, `s · C_i · P`. The factor `s` matters. A board that is `s` times larger
    than nominal looks smaller to the PnP estimate, which uses the nominal size, by
    `1/s` in the camera frame. Without `s` the scale error would count as a distance,
    not the prediction error.

    Raises:
        ValueError: Fewer than `MIN_SAMPLES + 1` captures.
    """
    count = len(poses_base_flange)
    if count < MIN_SAMPLES + 1:
        raise ValueError(f"Leave-one-out braucht mindestens {MIN_SAMPLES + 1} Aufnahmen")
    points = np.asarray(object_points, dtype=np.float64).reshape(-1, 3)
    matrix = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)
    homogeneous = np.hstack([points, np.ones((points.shape[0], 1))])
    errors_px: list[float] = []
    errors_m: list[float] = []
    for index in range(count):
        keep = [j for j in range(count) if j != index]
        partial = refine_hand_eye(
            [poses_base_flange[j] for j in keep],
            [corners_px[j] for j in keep],
            points,
            matrix,
            initial_flange_cam=full.pose_flange_cam,
            initial_base_board=full.pose_base_board,
            initial_scale=full.board_scale,
            estimate_scale=estimate_scale,
            huber_delta_px=huber_delta_px,
        )
        flange = np.asarray(poses_base_flange[index], dtype=np.float64)
        pixels, predicted = _project_all(
            invert(flange)[None, :, :],
            points,
            matrix,
            partial.pose_flange_cam,
            partial.pose_base_board,
            partial.board_scale,
        )
        observed = np.asarray(corners_px[index], dtype=np.float64).reshape(-1, 2)
        errors_px.append(float(np.sqrt(np.mean(np.sum((pixels[0] - observed) ** 2, axis=1)))))
        measured = partial.board_scale * (np.asarray(poses_cam_board[index], dtype=np.float64) @ homogeneous.T).T[:, :3]
        errors_m.append(float(np.mean(np.linalg.norm(predicted[0] - measured, axis=1))))
    return LeaveOneOutResult(
        error_px=tuple(errors_px),
        error_m=tuple(errors_m),
        rms_px=float(np.sqrt(np.mean(np.square(errors_px)))),
        rms_m=float(np.sqrt(np.mean(np.square(errors_m)))),
    )


def reconstruction_spread(
    poses_base_flange: Sequence[Pose],
    poses_cam_target: Sequence[Pose],
    pose_flange_cam: Pose,
    used: Sequence[int],
) -> tuple[tuple[tuple[float, float], ...], float, float, float, float]:
    """Spread of `T_base_target` for a given `X`, as in `solve_hand_eye_robust`.

    Returns `(residuals per capture (m, degrees), RMS m, RMS degrees, max m, max
    degrees)`. The mean and the statistics use only the indices in `used`. The
    residuals are given for all captures. Intended for a refined `X` that does not
    come from the Park-Martin solver.
    """
    reconstructed = _reconstruct(poses_base_flange, poses_cam_target, pose_flange_cam)
    centre = average_poses([reconstructed[i] for i in used])
    residuals = tuple(_distance(centre, pose) for pose in reconstructed)
    kept = [residuals[i] for i in used]
    return (
        residuals,
        math.sqrt(sum(m * m for m, _ in kept) / len(kept)),
        math.sqrt(sum(d * d for _, d in kept) / len(kept)),
        max(m for m, _ in kept),
        max(d for _, d in kept),
    )


# --- Refinement of a board session: one pipeline for server and CLI -----------

#: From this deviation of the estimated board scale from the nominal size (0.3 %)
#: on, a hint is shown (hand Pi status, output of `calibrate_handeye --session`):
#: the square size or the intrinsics do not match.
BOARD_SCALE_WARNING = 0.003

#: Steps of `refine_board_session`, for `RefinementError.stage`.
STAGE_REFINEMENT = "refinement"
STAGE_LEAVE_ONE_OUT = "leave-one-out"


class RefinementError(Exception):
    """A computation step of the board refinement failed (not a `ValueError`).

    `stage` names the step (`STAGE_REFINEMENT`, `STAGE_LEAVE_ONE_OUT`). The cause is
    attached as `__cause__`. A `ValueError` (poses too similar, too few captures)
    passes through unchanged. It is a finding about the data, not a computation error.
    """

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True)
class BoardRefinement:
    """Result of `refine_board_session`."""

    refinement: HandEyeRefinement
    #: Park-Martin start of the refinement (without the outlier check).
    closed_form: HandEyeSolution
    #: Indices of the remaining input captures (after the pixel outlier check).
    kept: tuple[int, ...]
    #: Prediction error per remaining capture; `None` for at most `MIN_SAMPLES`.
    loo: LeaveOneOutResult | None
    #: The refined `X` with the spread of the board pose. Indices as in the input.
    solution: HandEyeSolution

    def hand_eye_fields(self) -> dict[str, float]:
        """Return the refinement metrics as keyword arguments for `HandEye`."""
        fields = {
            "refined_reprojection_rms_px": self.refinement.reprojection_rms_px,
            "board_scale": self.refinement.board_scale,
        }
        if self.loo is not None:
            fields["loo_error_rms_px"] = self.loo.rms_px
            fields["loo_error_rms_m"] = self.loo.rms_m
        return fields


def refine_board_session(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    poses_cam_board: Sequence[Pose],
    spec: BoardSpec,
    calibration: CameraCalibration,
    *,
    estimate_scale: bool = True,
) -> BoardRefinement:
    """Refine a board session, from the distorted corners to the spread.

    Undistort (`lens.undistort_points`), then `refine_hand_eye_robust` (outliers in
    pixels), then leave-one-out over the remaining captures, then
    `reconstruction_spread` for the refined `X`. This is the computation path of the
    hand Pi (`FinishHandEyeCalibration`) and of the offline recomputation on a
    computer (`calibrate_handeye --session`). Both call this function.

    `corners_px`: per capture, the canonical corners `(N, 2)` in the **distorted**
    image. Safe to call from a thread, with no side effects.

    Raises:
        ValueError: As `refine_hand_eye_robust` (poses rotate about too few axes,
            too few captures, corner count does not match the board).
        RefinementError: Another error during refinement or leave-one-out.
    """
    flanges = list(poses_base_flange)
    boards = list(poses_cam_board)
    try:
        points = chessboard_object_points(spec)
        matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
        undistorted = [lens.undistort_points(corners, calibration) for corners in corners_px]
        refinement, rejected, closed_form = refine_hand_eye_robust(
            flanges, undistorted, boards, points, matrix, estimate_scale=estimate_scale
        )
    except ValueError:
        raise
    except Exception as error:
        raise RefinementError(STAGE_REFINEMENT) from error
    try:
        kept = [index for index in range(len(flanges)) if index not in rejected]
        loo = None
        if len(kept) > MIN_SAMPLES:
            loo = leave_one_out_errors(
                [flanges[i] for i in kept],
                [undistorted[i] for i in kept],
                [boards[i] for i in kept],
                points,
                matrix,
                refinement,
                estimate_scale=estimate_scale,
            )
        residuals, rms_m, rms_deg, max_m, max_deg = reconstruction_spread(
            flanges, boards, refinement.pose_flange_cam, kept
        )
    except Exception as error:
        raise RefinementError(STAGE_LEAVE_ONE_OUT) from error
    solution = HandEyeSolution(
        pose_flange_cam=refinement.pose_flange_cam,
        rms_position_m=rms_m,
        rms_rotation_deg=rms_deg,
        max_position_m=max_m,
        max_rotation_deg=max_deg,
        residuals=residuals,
        rejected=rejected,
        used_count=len(kept),
    )
    return BoardRefinement(
        refinement=refinement,
        closed_form=closed_form,
        kept=tuple(kept),
        loo=loo,
        solution=solution,
    )


# --- Session record (`session.json`): writer and reader ----------------------


class UnknownSessionSchema(ValueError):
    """The file is not a session record of this version (`SESSION_SCHEMA`)."""


@dataclass(frozen=True)
class SessionSample:
    """One capture, as it is stored in the record."""

    accepted: bool
    detail: str
    flange_in_base: Pose
    cam_to_target: Pose | None
    reprojection_error_px: float | None
    #: Canonical corners `(N, 2)` in the distorted image (board), otherwise `None`.
    corners_px: np.ndarray | None
    #: File name of the image next to `session.json`, otherwise `None`.
    image: str | None


def intrinsics_to_dict(calibration: CameraCalibration) -> dict:
    """Return the intrinsics for the record. Counterpart of `intrinsics_from_dict`."""
    return {
        "cameraMatrix": np.asarray(calibration.camera_matrix, dtype=np.float64).tolist(),
        "distortionCoefficients": np.asarray(calibration.distortion, dtype=np.float64)
        .reshape(-1)
        .tolist(),
        "distortionModel": calibration.model,
        "imageSize": [int(calibration.image_size[0]), int(calibration.image_size[1])],
        "calibrationId": calibration.calibration_id,
        "frameId": calibration.frame_id,
    }


def intrinsics_from_dict(data: dict) -> CameraCalibration:
    """Read the intrinsics of a record. Counterpart of `intrinsics_to_dict`."""
    size = data["imageSize"]
    return CameraCalibration(
        camera_matrix=np.asarray(data["cameraMatrix"], dtype=np.float64).reshape(3, 3),
        distortion=np.asarray(data["distortionCoefficients"], dtype=np.float64),
        image_size=(int(size[0]), int(size[1])),
        frame_id=str(data.get("frameId", "")),
        calibration_id=str(data.get("calibrationId", "")),
        model=data.get("distortionModel", "pinhole"),
    )


def board_from_dict(data: dict) -> BoardSpec:
    """Read the board of a record (`BoardSpec.as_dict` is the counterpart)."""
    return BoardSpec(
        type=data["type"],
        cols=int(data["cols"]),
        rows=int(data["rows"]),
        square_size_m=float(data["squareSizeM"]),
    )


def session_payload(
    *,
    outcome: str,
    frame_id: str,
    tag_id: int,
    tag_in_base: Pose | None,
    board: BoardSpec | None,
    intrinsics_source: str | None,
    calibration: CameraCalibration | None,
    samples: Sequence[SessionSample],
    result: dict | None,
) -> dict:
    """Return the record of a session as a dict (`SESSION_SCHEMA`), with a fixed key order.

    `outcome`: `finished`, `refused` or `failed`. The reported flange poses, the
    measured board or tag poses, the corners, the intrinsics and the board geometry
    are enough to recompute with `load_session` and `refine_board_session`.
    """
    return {
        "schema": SESSION_SCHEMA,
        "createdAt": utc_now_z(),
        "outcome": outcome,
        "frameId": frame_id,
        "tagId": tag_id,
        "tagInBase": pose_to_dict(tag_in_base) if tag_in_base is not None else None,
        "board": board.as_dict() if board is not None else None,
        "intrinsicsSource": intrinsics_source,
        "intrinsics": intrinsics_to_dict(calibration) if calibration is not None else None,
        "samples": [
            {
                "index": index,
                "accepted": sample.accepted,
                "detail": sample.detail,
                "flangeInBase": pose_to_dict(sample.flange_in_base),
                "camToTarget": (
                    pose_to_dict(sample.cam_to_target) if sample.cam_to_target is not None else None
                ),
                "reprojectionErrorPx": sample.reprojection_error_px,
                "cornersPx": sample.corners_px.tolist() if sample.corners_px is not None else None,
                "image": sample.image,
            }
            for index, sample in enumerate(samples)
        ],
        "result": result,
    }


def session_json(payload: dict) -> str:
    """Return the text of `session.json` for `session_payload`."""
    return json.dumps(payload, indent=2, default=float) + "\n"


@dataclass(frozen=True)
class SessionDataset:
    """A record read from disk: only the accepted captures, plus the raw intrinsics and board."""

    path: Path
    frame_id: str
    #: As stored in the record (`None` if unknown).
    intrinsics_source: str | None
    #: Number of all captures in the record, including the rejected ones.
    total_count: int
    flanges: list[Pose]
    targets: list[Pose]
    #: Per accepted capture, its corners (nested lists) or `None`.
    corners_px: list
    intrinsics: dict | None
    board: dict | None

    @property
    def can_refine(self) -> bool:
        """Whether this is a board session with intrinsics and corners in every accepted capture."""
        return bool(self.intrinsics and self.board and all(c is not None for c in self.corners_px))

    def refine(self, *, estimate_scale: bool = True) -> BoardRefinement:
        """Run `refine_board_session` on this record (only if `can_refine`)."""
        return refine_board_session(
            self.flanges,
            self.corners_px,
            self.targets,
            board_from_dict(self.board),
            intrinsics_from_dict(self.intrinsics),
            estimate_scale=estimate_scale,
        )


def load_session(path) -> SessionDataset:
    """Read a record (folder or `session.json`).

    Raises:
        UnknownSessionSchema: Another version, or not a session file.
    """
    path = Path(path)
    if path.is_dir():
        path = path / SESSION_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SESSION_SCHEMA:
        raise UnknownSessionSchema(
            f"Unbekanntes Sitzungs-Schema '{data.get('schema')}' in {path} (erwartet {SESSION_SCHEMA})"
        )
    samples = data.get("samples", [])
    accepted = [raw for raw in samples if raw.get("accepted")]
    return SessionDataset(
        path=path,
        frame_id=str(data.get("frameId", "")),
        intrinsics_source=data.get("intrinsicsSource"),
        total_count=len(samples),
        flanges=[pose_from_dict(raw["flangeInBase"]) for raw in accepted],
        targets=[pose_from_dict(raw["camToTarget"]) for raw in accepted],
        corners_px=[raw.get("cornersPx") for raw in accepted],
        intrinsics=data.get("intrinsics"),
        board=data.get("board"),
    )


def load_hand_eye(path) -> HandEye:
    """Read a hand-eye calibration from JSON."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Hand-Auge-Kalibrierung nicht gefunden: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema != SCHEMA:
        raise ValueError(
            f"Unbekanntes Hand-Auge-Schema '{schema}' in {path} (erwartet {SCHEMA})"
        )
    pose = data.get("poseFlangeCam")
    if not pose:
        raise ValueError(f"Hand-Auge-Datei {path} enthaelt kein 'poseFlangeCam'")
    return HandEye(
        pose_flange_cam=pose_from_dict(pose),
        frame_id=str(data.get("frameId", "")),
        calibration_id=str(data.get("calibrationId", "")),
        rms_position_m=_or_nan(data.get("rmsPositionM")),
        rms_rotation_deg=_or_nan(data.get("rmsRotationDeg")),
        sample_count=int(data.get("sampleCount", 0)),
        max_position_m=_or_nan(data.get("maxPositionM")),
        max_rotation_deg=_or_nan(data.get("maxRotationDeg")),
        refined_reprojection_rms_px=_or_nan(data.get("refinedReprojectionRmsPx")),
        board_scale=_or_nan(data.get("boardScale")),
        loo_error_rms_px=_or_nan(data.get("looErrorRmsPx")),
        loo_error_rms_m=_or_nan(data.get("looErrorRmsM")),
        intrinsics_source=str(data.get("intrinsicsSource") or ""),
    )


def save_hand_eye(path, hand_eye: HandEye) -> None:
    """Write a hand-eye calibration as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now_z()
    payload = {
        "schema": SCHEMA,
        "frameId": hand_eye.frame_id,
        "calibrationId": hand_eye.calibration_id or f"{hand_eye.frame_id or path.stem}@{now}",
        "poseFlangeCam": pose_to_dict(hand_eye.pose_flange_cam),
        "rmsPositionM": finite_or_none(hand_eye.rms_position_m, FILE_DIGITS),
        "rmsRotationDeg": finite_or_none(hand_eye.rms_rotation_deg, FILE_DIGITS),
        "maxPositionM": finite_or_none(hand_eye.max_position_m, FILE_DIGITS),
        "maxRotationDeg": finite_or_none(hand_eye.max_rotation_deg, FILE_DIGITS),
        "sampleCount": int(hand_eye.sample_count),
        "createdAt": now,
    }
    # Only when present: files without refinement stay as they were.
    for key, value in (
        ("refinedReprojectionRmsPx", hand_eye.refined_reprojection_rms_px),
        ("boardScale", hand_eye.board_scale),
        ("looErrorRmsPx", hand_eye.loo_error_rms_px),
        ("looErrorRmsM", hand_eye.loo_error_rms_m),
    ):
        rounded = finite_or_none(value, FILE_DIGITS)
        if rounded is not None:
            payload[key] = rounded
    if hand_eye.intrinsics_source:
        payload["intrinsicsSource"] = hand_eye.intrinsics_source
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _or_nan(value) -> float:
    return float("nan") if value is None else float(value)


__all__ = [
    "AXIS_THRESHOLD_DEG",
    "BOARD_SCALE_WARNING",
    "MIN_AXES",
    "MIN_SAMPLES",
    "SCHEMA",
    "SESSION_FILE",
    "SESSION_SCHEMA",
    "HUBER_DELTA_PX",
    "STAGE_LEAVE_ONE_OUT",
    "STAGE_REFINEMENT",
    "BoardRefinement",
    "HandEye",
    "HandEyeRefinement",
    "HandEyeSolution",
    "LeaveOneOutResult",
    "RefinementError",
    "RobotAnchor",
    "SessionDataset",
    "SessionSample",
    "UnknownSessionSchema",
    "anchor_world_base",
    "board_from_dict",
    "camera_pose_from_robot",
    "estimate_base_board",
    "intrinsics_from_dict",
    "intrinsics_to_dict",
    "leave_one_out_errors",
    "load_hand_eye",
    "load_session",
    "reconstruction_spread",
    "refine_board_session",
    "refine_hand_eye",
    "refine_hand_eye_robust",
    "rotation_diversity",
    "save_hand_eye",
    "session_json",
    "session_payload",
    "solve_hand_eye",
    "solve_hand_eye_robust",
    "target_spread",
]
