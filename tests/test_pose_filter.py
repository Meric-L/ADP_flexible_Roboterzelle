"""The quality filter of an estimated pose -- testable without OpenCV.

The interesting case is NaN. `nan > threshold` is `False` in Python, so a
plain comparison would let a degenerate solution through -- and
`payload._dumps` with `allow_nan=False` would then end the job as INTERNAL
instead of DETECTION_FAILED. Hence it gets its own case here.
"""

import unittest

from tests._support import SQUARE, make_tag_pose

try:
    from tagloc.observations import AMBIGUITY_THRESHOLD, TagObservation, TagPose
except Exception:  # numpy broken in this environment
    TagPose = None


@unittest.skipUnless(TagPose is not None, "numpy nicht verfuegbar")
class IsUsableTest(unittest.TestCase):
    def test_keeps_a_pose_below_the_threshold(self):
        self.assertTrue(make_tag_pose(7, error_px=0.4).is_usable(3.0))

    def test_keeps_a_pose_exactly_at_the_threshold(self):
        self.assertTrue(make_tag_pose(7, error_px=3.0).is_usable(3.0))

    def test_rejects_a_pose_above_the_threshold(self):
        self.assertFalse(make_tag_pose(7, error_px=3.01).is_usable(3.0))

    def test_rejects_nan_even_though_the_comparison_would_pass_it(self):
        degenerate = make_tag_pose(7, error_px=float("nan"))
        self.assertFalse(degenerate.reprojection_error_px > 3.0)
        self.assertFalse(degenerate.is_usable(3.0))

    def test_rejects_infinity(self):
        self.assertFalse(make_tag_pose(7, error_px=float("inf")).is_usable(3.0))

    def test_rejects_nan_even_without_a_threshold(self):
        self.assertFalse(make_tag_pose(7, error_px=float("nan")).is_usable(None))

    def test_keeps_any_finite_error_without_a_threshold(self):
        self.assertTrue(make_tag_pose(7, error_px=99.0).is_usable(None))


@unittest.skipUnless(TagPose is not None, "numpy nicht verfuegbar")
class IsAmbiguousTest(unittest.TestCase):
    def test_a_clearly_better_best_solution_is_not_ambiguous(self):
        self.assertFalse(make_tag_pose(7, error_px=0.3, ambiguity=0.1).is_ambiguous)

    def test_two_equally_good_solutions_are_ambiguous(self):
        self.assertTrue(make_tag_pose(7, error_px=0.3, ambiguity=0.98).is_ambiguous)

    def test_the_threshold_itself_is_not_ambiguous(self):
        self.assertFalse(make_tag_pose(7, error_px=0.3, ambiguity=AMBIGUITY_THRESHOLD).is_ambiguous)


@unittest.skipUnless(TagPose is not None, "numpy nicht verfuegbar")
class ObservationTest(unittest.TestCase):
    def test_center_is_the_mean_of_the_corners(self):
        self.assertEqual((5.0, 5.0), TagObservation(tag_id=1, corners=SQUARE).center())

    def test_side_length_is_the_mean_edge_length(self):
        self.assertAlmostEqual(10.0, TagObservation(tag_id=1, corners=SQUARE).side_length_px())


if __name__ == "__main__":
    unittest.main()
