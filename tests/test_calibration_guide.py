"""Rules for collecting calibration views (`tagloc.calibration_guide`).

Board views are built from a known homography, so each test states what the
view *is* (centred, tilted, at the edge) and checks that the rules see it
that way. No OpenCV: the module is pure numpy by design (test_layering).
"""

import math
import unittest

try:
    import numpy as np

    from tagloc import calibration_guide as guide
    from tagloc.boards import CHARUCO, BoardSample, BoardSpec
except Exception:  # numpy broken in this environment
    np = None

IMAGE = (1280, 720)


def project(homography, plane):
    points = np.column_stack([plane, np.ones(len(plane))]) @ np.asarray(homography).T
    return points[:, :2] / points[:, 2:3]


def chessboard_view(homography, spec=None):
    spec = spec or BoardSpec()
    plane = np.mgrid[0 : spec.cols, 0 : spec.rows].T.reshape(-1, 2).astype(float)
    return BoardSample(corners=project(homography, plane).reshape(-1, 1, 2))


def charuco_view(homography, spec, ids):
    per_row = spec.cols - 1
    ids = np.asarray(ids)
    plane = np.column_stack([ids % per_row + 1, ids // per_row + 1]).astype(float)
    return BoardSample(
        corners=project(homography, plane).reshape(-1, 1, 2), ids=ids.reshape(-1, 1)
    )


def affine(scale, tx, ty, angle_deg=0.0):
    angle = math.radians(angle_deg)
    return np.array(
        [
            [scale * math.cos(angle), -scale * math.sin(angle), tx],
            [scale * math.sin(angle), scale * math.cos(angle), ty],
            [0.0, 0.0, 1.0],
        ]
    )


def tilted(scale, tx, ty, perspective):
    """A board whose far side appears smaller -- what tilting it does."""
    homography = affine(scale, tx, ty)
    homography[2, 1] = perspective
    return homography


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class GeometryTest(unittest.TestCase):
    def test_recovers_a_known_homography(self):
        truth = np.array([[40.0, 5.0, 300.0], [-3.0, 38.0, 200.0], [0.0004, 0.002, 1.0]])
        plane = np.array([[0, 0], [8, 0], [8, 5], [0, 5], [3, 2], [6, 4]], dtype=float)
        fitted = guide.fit_homography(plane, project(truth, plane))
        np.testing.assert_allclose(fitted, truth, rtol=1e-6, atol=1e-6)

    def test_collinear_points_have_no_homography(self):
        plane = np.array([[0, 0], [1, 0], [2, 0], [3, 0]], dtype=float)
        self.assertIsNone(guide.fit_homography(plane, plane * 10))

    def test_chessboard_plane_points_follow_the_grid(self):
        spec = BoardSpec(cols=4, rows=3)
        sample = chessboard_view(affine(10, 0, 0), spec)
        plane = guide.board_plane_points(sample, spec)
        self.assertEqual(plane.shape, (12, 2))
        np.testing.assert_array_equal(plane[:5], [[0, 0], [1, 0], [2, 0], [3, 0], [0, 1]])

    def test_incomplete_chessboard_does_not_fit_the_spec(self):
        spec = BoardSpec(cols=4, rows=3)
        sample = BoardSample(corners=np.zeros((5, 1, 2)))
        self.assertIsNone(guide.board_plane_points(sample, spec))

    def test_charuco_ids_map_row_major_over_inner_corners(self):
        """Checked against OpenCV 5: id = y * (cols - 1) + x, at ((x+1), (y+1)) squares."""
        spec = BoardSpec(type=CHARUCO, cols=7, rows=5)
        sample = charuco_view(affine(10, 0, 0), spec, [0, 5, 6, 23])
        np.testing.assert_array_equal(
            guide.board_plane_points(sample, spec), [[1, 1], [6, 1], [1, 2], [6, 4]]
        )

    def test_partial_charuco_board_still_yields_the_full_outline(self):
        spec = BoardSpec(type=CHARUCO, cols=7, rows=5)
        homography = affine(60, 100, 100)
        full = guide.board_outline(charuco_view(homography, spec, range(24)), spec)
        part = guide.board_outline(charuco_view(homography, spec, [0, 1, 6, 7, 8]), spec)
        np.testing.assert_allclose(part, full, atol=1e-6)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ViewParamsTest(unittest.TestCase):
    def test_centred_frontal_board_sits_in_the_middle_without_skew(self):
        # 9x6 inner corners, 50 px squares: 400 x 250 px, centred.
        params = guide.view_params(chessboard_view(affine(50, 440, 235)), BoardSpec(), IMAGE)
        self.assertAlmostEqual(params.x, 0.5, places=3)
        self.assertAlmostEqual(params.y, 0.5, places=3)
        self.assertAlmostEqual(params.skew, 0.0, places=6)
        self.assertAlmostEqual(params.size, math.sqrt(400 * 250 / (1280 * 720)), places=3)

    def test_rotation_in_the_image_plane_is_not_skew(self):
        params = guide.view_params(
            chessboard_view(affine(50, 640, 200, angle_deg=30)), BoardSpec(), IMAGE
        )
        self.assertAlmostEqual(params.skew, 0.0, places=6)

    def test_tilted_board_has_skew(self):
        params = guide.view_params(
            chessboard_view(tilted(50, 440, 235, 0.03)), BoardSpec(), IMAGE
        )
        self.assertGreater(params.skew, 0.1)

    def test_board_at_the_left_edge_reaches_x_zero(self):
        params = guide.view_params(chessboard_view(affine(50, 0, 235)), BoardSpec(), IMAGE)
        self.assertLess(params.x, 0.05)

    def test_nearer_board_is_larger(self):
        far = guide.view_params(chessboard_view(affine(30, 500, 300)), BoardSpec(), IMAGE)
        near = guide.view_params(chessboard_view(affine(70, 300, 150)), BoardSpec(), IMAGE)
        self.assertGreater(near.size, far.size)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class SelectionTest(unittest.TestCase):
    def test_the_same_view_twice_is_not_novel(self):
        view = guide.ViewParams(0.5, 0.5, 0.3, 0.1)
        self.assertTrue(guide.is_novel(view, []))
        self.assertFalse(guide.is_novel(view, [guide.ViewParams(0.55, 0.5, 0.3, 0.1)]))
        self.assertTrue(guide.is_novel(view, [guide.ViewParams(0.1, 0.5, 0.3, 0.1)]))

    def test_small_movement_counts_as_still(self):
        spec = BoardSpec()
        before = chessboard_view(affine(50, 440, 235))
        self.assertTrue(guide.is_still(chessboard_view(affine(50, 441, 235)), before, spec, IMAGE))
        self.assertFalse(guide.is_still(chessboard_view(affine(50, 480, 235)), before, spec, IMAGE))

    def test_reversed_chessboard_corner_order_is_not_movement(self):
        spec = BoardSpec()
        before = chessboard_view(affine(50, 440, 235))
        flipped = BoardSample(corners=np.asarray(before.corners)[::-1].copy())
        self.assertTrue(guide.is_still(flipped, before, spec, IMAGE))

    def test_nothing_to_compare_is_not_still(self):
        sample = chessboard_view(affine(50, 440, 235))
        self.assertFalse(guide.is_still(sample, None, BoardSpec(), IMAGE))

    def test_charuco_stillness_compares_matching_ids_only(self):
        spec = BoardSpec(type=CHARUCO, cols=7, rows=5)
        homography = affine(60, 100, 100)
        before = charuco_view(homography, spec, range(0, 12))
        after = charuco_view(homography, spec, range(4, 16))
        self.assertTrue(guide.is_still(after, before, spec, IMAGE))

    def test_progress_measures_the_spanned_range(self):
        views = [guide.ViewParams(0.1, 0.5, 0.2, 0.0), guide.ViewParams(0.8, 0.5, 0.4, 0.15)]
        covered = guide.progress(views)
        self.assertEqual(covered["x"], 1.0)
        self.assertEqual(covered["y"], 0.0)
        self.assertAlmostEqual(covered["size"], 0.5)
        self.assertAlmostEqual(covered["skew"], 0.5)

    def test_coverage_grid_counts_views_per_cell(self):
        top_left = BoardSample(corners=np.array([[[10.0, 10.0]], [[20.0, 30.0]]]))
        everywhere = BoardSample(corners=np.array([[[10.0, 10.0]], [[1270.0, 710.0]]]))
        grid = guide.coverage_grid([top_left, everywhere], IMAGE)
        self.assertEqual(grid[0][0], 2)
        self.assertEqual(grid[2][2], 1)
        self.assertEqual(sum(map(sum, grid)), 3)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class HintTest(unittest.TestCase):
    def hint(self, **overrides):
        arguments = dict(
            camera_ok=True,
            board_visible=True,
            board_still=True,
            novel=False,
            sample_count=5,
            target_samples=20,
            grid=[[1, 1, 1], [1, 1, 1], [1, 1, 1]],
            covered={"x": 1.0, "y": 1.0, "size": 1.0, "skew": 1.0},
            board_type="chessboard",
        )
        arguments.update(overrides)
        return guide.next_hint(**arguments)

    def test_order_of_priorities(self):
        self.assertEqual(self.hint(camera_ok=False).code, "no_camera")
        self.assertEqual(self.hint(sample_count=20).code, "enough")
        self.assertEqual(self.hint(board_visible=False).code, "show_board")
        self.assertEqual(self.hint(board_still=False).code, "hold_still")
        self.assertEqual(self.hint(novel=True).code, "capturing")

    def test_points_to_an_empty_image_corner_first(self):
        hint = self.hint(grid=[[1, 0, 1], [1, 1, 1], [1, 1, 0]])
        self.assertEqual(hint.code, "move_to_region")
        self.assertIn("unten rechts", hint.text)

    def test_asks_for_tilt_before_distance(self):
        self.assertEqual(self.hint(covered={"x": 1, "y": 1, "size": 0.2, "skew": 0.2}).code, "tilt")
        self.assertEqual(
            self.hint(covered={"x": 1, "y": 1, "size": 0.2, "skew": 1.0}).code, "vary_distance"
        )

    def test_manual_mode_asks_for_the_button(self):
        self.assertIn("Aufnehmen", self.hint(novel=True, auto_capture=False).text)

    def test_region_names(self):
        self.assertEqual(guide.region_name(0, 0), "oben links")
        self.assertEqual(guide.region_name(1, 2), "rechts")
        self.assertEqual(guide.region_name(1, 1), "Bildmitte")


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RatingTest(unittest.TestCase):
    def test_good_needs_low_rms_and_wide_coverage(self):
        self.assertEqual(guide.rate_calibration(0.3, (0.8, 0.75))[0], "good")
        self.assertEqual(guide.rate_calibration(0.3, (0.8, 0.65))[0], "usable")
        self.assertEqual(guide.rate_calibration(0.7, (0.8, 0.8))[0], "usable")
        self.assertEqual(guide.rate_calibration(1.4, (0.9, 0.9))[0], "poor")
        self.assertEqual(guide.rate_calibration(0.3, (0.4, 0.9))[0], "poor")

    def test_explains_what_is_wrong(self):
        _, notes = guide.rate_calibration(0.8, (0.5, 0.9), {"skew": 0.2})
        self.assertEqual(len(notes), 3)

    def test_nan_rms_is_poor(self):
        self.assertEqual(guide.rate_calibration(float("nan"), (1.0, 1.0))[0], "poor")


if __name__ == "__main__":
    unittest.main()
