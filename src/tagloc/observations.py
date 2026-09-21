"""Data types between image and math. No OpenCV.

Split out because `detector.py` and `pose.py` need OpenCV but `localize.py`
must not: if these dataclasses lived in the cv2 modules, `localize` would
drag OpenCV in with them. Both types are re-exported from `detector` and
`pose` so callers find them where they expect them.
"""

import math
from dataclasses import dataclass, field
from typing import Any

from .geometry import Pose

#: Above this ratio a pose counts as ambiguous.
AMBIGUITY_THRESHOLD = 0.6


@dataclass(frozen=True)
class TagObservation:
    """A tag found in the image -- no pose yet.

    `corners` are four image points in **clockwise order, starting top-left**
    (the order `cv2.aruco` uses). Every detector adapter brings its output
    into this order; `pose.py` relies on it because `SOLVEPNP_IPPE_SQUARE`
    expects exactly this correspondence to the object points.
    """

    tag_id: int
    corners: tuple[tuple[float, float], ...]
    decision_margin: float | None = None

    def center(self) -> tuple[float, float]:
        xs = [corner[0] for corner in self.corners]
        ys = [corner[1] for corner in self.corners]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def side_length_px(self) -> float:
        """Mean edge length in the image -- rough measure of detection quality."""
        total = 0.0
        count = len(self.corners)
        for index in range(count):
            first = self.corners[index]
            second = self.corners[(index + 1) % count]
            total += math.dist(first, second)
        return total / count


@dataclass(frozen=True)
class TagPose:
    """A tag with an estimated pose in the camera coordinate frame."""

    tag_id: int
    pose_cam_tag: Pose
    reprojection_error_px: float = float("nan")
    #: Best-solution error divided by second-best, in (0, 1]. Near 1 means
    #: both solutions explain the image equally well.
    ambiguity_ratio: float = 0.0
    observation: TagObservation | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ambiguous(self) -> bool:
        """A square marker seen at a shallow angle has two good solutions.

        Ambiguous doesn't mean wrong, but such a pose must not be acted on
        unchecked, hence the flag is carried in the payload.
        """
        return self.ambiguity_ratio > AMBIGUITY_THRESHOLD

    def is_usable(self, max_reprojection_error_px: float | None = None) -> bool:
        """Return whether this pose is fit to pass on.

        The NaN case is checked first and explicitly: `nan > threshold` is
        `False` in Python, so a plain comparison would let a degenerate
        solution through -- exactly the case this check guards against.
        Downstream, `payload` would then raise on serialisation
        (`allow_nan=False`) and the job would end as INTERNAL instead of
        DETECTION_FAILED.
        """
        error = self.reprojection_error_px
        if math.isnan(error) or math.isinf(error):
            return False
        return max_reprojection_error_px is None or error <= max_reprojection_error_px
