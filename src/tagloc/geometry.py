"""Poses, composition, quaternions. Pure math, no OpenCV.

Conventions used across the whole project:

* A pose is an `np.ndarray` of shape `(4, 4)`, `float64`, homogeneous.
  No dedicated type -- numpy is enough, and every function name states
  which coordinate frame is meant.
* `T_a_b` means "pose of b, expressed in a". Composition:
  `T_a_c = compose(T_a_b, T_b_c)`.
* Lengths in **meters**, angles in **radians**, no exceptions.
* Quaternions in **xyzw** order -- matching the payload's declaration
  (`"rotation": "quaternion_xyzw"`) and what three.js expects. Not wxyz.

Rodrigues is spelled out here instead of calling `cv2.Rodrigues` so this
module stays importable without OpenCV, keeping the math testable everywhere.
"""

import math
from collections.abc import Sequence

import numpy as np

#: A homogeneous 4x4 transform.
Pose = np.ndarray

#: Below this rotation angle, the small-angle approximation is used.
_ANGLE_EPS = 1e-12


def identity() -> Pose:
    """Return the identity pose."""
    return np.eye(4, dtype=np.float64)


def from_rotation_translation(rotation, translation) -> Pose:
    """Build a pose from a 3x3 rotation matrix and a translation vector."""
    rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    translation = np.asarray(translation, dtype=np.float64).reshape(3)
    pose = np.eye(4, dtype=np.float64)
    pose[:3, :3] = rotation
    pose[:3, 3] = translation
    return pose


def skew(vector) -> np.ndarray:
    """Return the skew-symmetric matrix of a 3-vector."""
    x, y, z = np.asarray(vector, dtype=np.float64).reshape(3)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=np.float64)


def rotation_from_rvec(rvec) -> np.ndarray:
    """Rodrigues formula: rotation vector -> rotation matrix."""
    rvec = np.asarray(rvec, dtype=np.float64).reshape(3)
    theta = float(np.linalg.norm(rvec))
    if theta < _ANGLE_EPS:
        # Small-angle approximation; converges to identity as theta -> 0.
        return np.eye(3, dtype=np.float64) + skew(rvec)
    axis = rvec / theta
    k = skew(axis)
    return np.eye(3, dtype=np.float64) + math.sin(theta) * k + (1.0 - math.cos(theta)) * (k @ k)


def rvec_from_rotation(rotation) -> np.ndarray:
    """Invert `rotation_from_rvec`.

    Theta near pi is handled separately: the skew-symmetric part vanishes
    there and the usual formula loses the axis.
    """
    rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    cos_theta = (np.trace(rotation) - 1.0) / 2.0
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    theta = math.acos(cos_theta)
    if theta < 1e-8:
        return np.zeros(3, dtype=np.float64)
    if math.pi - theta < 1e-6:
        # Axis from the diagonal of R + I; the sign is free at pi anyway.
        diagonal = np.clip((np.diag(rotation) + 1.0) / 2.0, 0.0, None)
        axis = np.sqrt(diagonal)
        largest = int(np.argmax(axis))
        if axis[largest] < 1e-9:
            return np.zeros(3, dtype=np.float64)
        signs = np.ones(3, dtype=np.float64)
        for index in range(3):
            if index != largest and rotation[largest, index] < 0.0:
                signs[index] = -1.0
        axis = axis * signs
        axis = axis / float(np.linalg.norm(axis))
        return axis * theta
    factor = theta / (2.0 * math.sin(theta))
    return factor * np.array(
        [
            rotation[2, 1] - rotation[1, 2],
            rotation[0, 2] - rotation[2, 0],
            rotation[1, 0] - rotation[0, 1],
        ],
        dtype=np.float64,
    )


def from_rvec_tvec(rvec, tvec) -> Pose:
    """Build a pose from the rvec/tvec pair returned by solvePnP."""
    return from_rotation_translation(rotation_from_rvec(rvec), np.asarray(tvec).reshape(3))


def to_rvec_tvec(pose: Pose) -> tuple[np.ndarray, np.ndarray]:
    """Split a pose into `(rvec, tvec)`, as expected by `cv2.drawFrameAxes`."""
    pose = np.asarray(pose, dtype=np.float64)
    return rvec_from_rotation(pose[:3, :3]), pose[:3, 3].copy()


def quaternion_from_rotation(rotation) -> np.ndarray:
    """Rotation matrix -> quaternion xyzw, sign normalised to `w >= 0`.

    Without sign normalisation, two mathematically identical rotations would
    yield different numbers, breaking comparisons against a reference value.
    Uses Shepperd's method: always pick the largest of the four denominators
    to avoid dividing by near-zero.
    """
    rotation = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    trace = float(np.trace(rotation))
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (rotation[2, 1] - rotation[1, 2]) / scale
        y = (rotation[0, 2] - rotation[2, 0]) / scale
        z = (rotation[1, 0] - rotation[0, 1]) / scale
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2.0
        w = (rotation[2, 1] - rotation[1, 2]) / scale
        x = 0.25 * scale
        y = (rotation[0, 1] + rotation[1, 0]) / scale
        z = (rotation[0, 2] + rotation[2, 0]) / scale
    elif rotation[1, 1] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2.0
        w = (rotation[0, 2] - rotation[2, 0]) / scale
        x = (rotation[0, 1] + rotation[1, 0]) / scale
        y = 0.25 * scale
        z = (rotation[1, 2] + rotation[2, 1]) / scale
    else:
        scale = math.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2.0
        w = (rotation[1, 0] - rotation[0, 1]) / scale
        x = (rotation[0, 2] + rotation[2, 0]) / scale
        y = (rotation[1, 2] + rotation[2, 1]) / scale
        z = 0.25 * scale
    quaternion = np.array([x, y, z, w], dtype=np.float64)
    quaternion = quaternion / float(np.linalg.norm(quaternion))
    if quaternion[3] < 0.0:
        quaternion = -quaternion
    return quaternion


def rotation_from_quaternion(quaternion) -> np.ndarray:
    """Convert quaternion xyzw to a rotation matrix."""
    x, y, z, w = np.asarray(quaternion, dtype=np.float64).reshape(4)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm < _ANGLE_EPS:
        raise ValueError("Quaternion mit Laenge null")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def from_position_quaternion(position, orientation_xyzw) -> Pose:
    """Build a pose from a position (m) and a quaternion xyzw."""
    return from_rotation_translation(rotation_from_quaternion(orientation_xyzw), position)


def to_position_quaternion(
    pose: Pose,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Split a pose into the payload's `position` and `orientation` fields."""
    pose = np.asarray(pose, dtype=np.float64)
    position = tuple(float(value) for value in pose[:3, 3])
    orientation = tuple(float(value) for value in quaternion_from_rotation(pose[:3, :3]))
    return position, orientation  # type: ignore[return-value]


def compose(*poses: Pose) -> Pose:
    """Compose poses left to right: `compose(T_a_b, T_b_c) == T_a_c`."""
    result = identity()
    for pose in poses:
        result = result @ np.asarray(pose, dtype=np.float64)
    return result


def invert(pose: Pose) -> Pose:
    """Invert a pose analytically instead of via `np.linalg.inv`.

    Exact and cheaper for a rigid transform: a rotation matrix's inverse is
    its transpose.
    """
    pose = np.asarray(pose, dtype=np.float64)
    rotation = pose[:3, :3]
    translation = pose[:3, 3]
    inverse = np.eye(4, dtype=np.float64)
    inverse[:3, :3] = rotation.T
    inverse[:3, 3] = -rotation.T @ translation
    return inverse


def translation_distance_m(first: Pose, second: Pose) -> float:
    """Return the distance between the two origins, in meters."""
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    return float(np.linalg.norm(first[:3, 3] - second[:3, 3]))


def rotation_distance_rad(first: Pose, second: Pose) -> float:
    """Return the residual rotation angle between two poses, in radians."""
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    relative = first[:3, :3].T @ second[:3, :3]
    cos_theta = (np.trace(relative) - 1.0) / 2.0
    return float(math.acos(float(np.clip(cos_theta, -1.0, 1.0))))


def average_poses(poses: Sequence[Pose], weights: Sequence[float] | None = None) -> Pose:
    """Average several poses of the same object.

    Translation arithmetically, rotation via Markley: eigenvector of the
    largest eigenvalue of `sum(w q qT)`. Averaging quaternions componentwise
    would be wrong -- the result wouldn't be normalised, and `q` and `-q`
    (the same rotation) would cancel each other out.

    `weights` is optional and defaults to equal weighting, so every existing
    caller keeps its exact result. It exists for the four world tags: they are
    seen at very different distances and obliquities, and an unweighted mean
    lets the worst of them drag the camera pose.

    Negative or non-finite weights are rejected, and weights summing to zero
    fall back to equal weighting -- a degenerate weight vector must not
    silently yield a pose that is merely the numerically largest leftover.
    """
    if not poses:
        raise ValueError("average_poses braucht mindestens eine Pose")
    if weights is not None and len(weights) != len(poses):
        raise ValueError(f"average_poses: {len(weights)} Gewichte zu {len(poses)} Posen")
    if len(poses) == 1:
        return np.asarray(poses[0], dtype=np.float64).copy()

    if weights is None:
        weight_vector = np.ones(len(poses), dtype=np.float64)
    else:
        weight_vector = np.asarray(weights, dtype=np.float64).reshape(len(poses))
        if not np.all(np.isfinite(weight_vector)) or np.any(weight_vector < 0.0):
            raise ValueError("average_poses: Gewichte muessen endlich und >= 0 sein")
        if weight_vector.sum() <= 0.0:
            weight_vector = np.ones(len(poses), dtype=np.float64)
    weight_vector = weight_vector / weight_vector.sum()

    translations = np.array([np.asarray(p, dtype=np.float64)[:3, 3] for p in poses])
    quaternions = np.array(
        [quaternion_from_rotation(np.asarray(p, dtype=np.float64)[:3, :3]) for p in poses]
    )
    # No sign alignment needed: `q` and `-q` are the same rotation, and the
    # accumulator is invariant to that since `q qT == (-q)(-q)T` -- unlike a
    # componentwise mean, which is why this must stay eigenvector-based.
    accumulator = (quaternions * weight_vector[:, None]).T @ quaternions
    eigenvalues, eigenvectors = np.linalg.eigh(accumulator)
    mean_quaternion = eigenvectors[:, int(np.argmax(eigenvalues))]
    if mean_quaternion[3] < 0.0:
        mean_quaternion = -mean_quaternion
    return from_rotation_translation(
        rotation_from_quaternion(mean_quaternion), weight_vector @ translations
    )


def pose_to_dict(pose: Pose) -> dict[str, list[float]]:
    """Convert a pose to `{"position": [...], "orientation": [x, y, z, w]}` for JSON."""
    position, orientation = to_position_quaternion(pose)
    return {"position": list(position), "orientation": list(orientation)}


def pose_from_dict(data) -> Pose:
    """Invert `pose_to_dict`."""
    return from_position_quaternion(data["position"], data["orientation"])
