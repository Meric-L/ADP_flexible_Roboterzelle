"""Hand-Auge: gemeinsame Verfeinerung gegen die Brettecken, Massstab, Vorhersagefehler.

Synthetisch, aber wie am Aufbau: die Ecken werden mit einer Kamera-Matrix
projiziert und verrauscht, die Brettpose je Aufnahme kommt aus PnP mit dem
**Nennmass** -- ein falsches Nennmass wirkt also wie am echten Brett.
"""

import math
import unittest

try:
    import cv2
    import numpy as np

    from tagloc import geometry as g
    from tagloc.boards import BoardSpec, CHESSBOARD, chessboard_object_points
    from tagloc.handeye import (
        leave_one_out_errors,
        refine_hand_eye,
        solve_hand_eye,
        estimate_base_board,
    )
except Exception:  # pragma: no cover - numpy/cv2 fehlt in dieser Umgebung
    np = None

try:
    from tagloc.handeye import refine_hand_eye_robust
except Exception:  # pragma: no cover - noch nicht gebaut
    refine_hand_eye_robust = None

#: Das Brett am Aufbau: 7x8 Felder, 6x7 Innenecken, 23 mm.
SPEC = None if np is None else BoardSpec(type=CHESSBOARD, cols=6, rows=7, square_size_m=0.023)
#: RealSense-Farbstrom 640x480, etwa 69 Grad horizontal.
K = None if np is None else np.array([[466.0, 0, 320.0], [0, 466.0, 240.0], [0, 0, 1.0]])
X_TRUE = None if np is None else g.from_rvec_tvec(
    (math.radians(3), math.radians(-5), math.radians(92)), (0.035, -0.045, 0.085)
)
#: Brett flach auf dem Tisch: +z zeigt ins Brett, also nach unten.
T_BASE_BOARD = None if np is None else g.from_rvec_tvec((math.pi, 0, 0.4), (0.55, 0.05, 0.02))


def board_points():
    return chessboard_object_points(SPEC)


def camera_in_board(rng, distance=0.45):
    """Kamera schaut aus `distance` auf die Brettmitte, gekippt und gerollt."""
    points = board_points()
    centre = points.mean(axis=0)
    tilt = g.from_rvec_tvec(np.radians(rng.uniform(-35, 35, 2).tolist() + [0.0]), (0, 0, 0))[:3, :3]
    roll = g.from_rvec_tvec((0, 0, math.radians(rng.uniform(-90, 90))), (0, 0, 0))[:3, :3]
    rotation = tilt @ roll  # Kamera-z = Brett-z gekippt: schaut ins Brett
    position = centre - distance * rotation[:, 2] + rng.uniform(-0.02, 0.02, 3)
    return g.from_rotation_translation(rotation, position)


def scene(count=15, seed=1, scale=1.0, sigma_px=0.3, distances=(0.45, 0.585)):
    """Flanschposen, entzerrte Pixelecken und PnP-Brettposen (Nennmass).

    `scale`: das echte Brett ist um diesen Faktor groesser als das Nennmass.
    """
    rng = np.random.default_rng(seed)
    nominal = board_points()
    real = scale * nominal
    flanges, corners, boards = [], [], []
    for index in range(count):
        cam = camera_in_board(rng, distances[index % len(distances)])
        flange = g.compose(T_BASE_BOARD, cam, g.invert(X_TRUE))
        board_in_cam = g.invert(cam)
        in_cam = (board_in_cam[:3, :3] @ real.T).T + board_in_cam[:3, 3]
        assert np.all(in_cam[:, 2] > 0.1), "Brett hinter der Kamera"
        pixels = (K @ in_cam.T).T
        pixels = pixels[:, :2] / pixels[:, 2:3] + rng.normal(0, sigma_px, (len(real), 2))
        ok, rvec, tvec = cv2.solvePnP(nominal, pixels, K, None, flags=cv2.SOLVEPNP_IPPE)
        assert ok
        rvec, tvec = cv2.solvePnPRefineLM(nominal, pixels, K, None, rvec, tvec)
        flanges.append(flange)
        corners.append(pixels)
        boards.append(g.from_rvec_tvec(rvec.ravel(), tvec.ravel()))
    return flanges, corners, boards


def error_mm_deg(pose):
    return (
        1000 * g.translation_distance_m(pose, X_TRUE),
        math.degrees(g.rotation_distance_rad(pose, X_TRUE)),
    )


def refine(flanges, corners, boards, **options):
    start, _, _ = solve_hand_eye(flanges, boards)
    return start, refine_hand_eye(
        flanges, corners, board_points(), K,
        initial_flange_cam=start,
        initial_base_board=estimate_base_board(flanges, boards, start),
        **options,
    )


@unittest.skipUnless(np is not None, "numpy/cv2 fehlt")
class RefineTest(unittest.TestCase):
    def test_noise_free_data_gives_the_truth(self):
        flanges, corners, boards = scene(sigma_px=0.0)
        _, refined = refine(flanges, corners, boards)
        position_mm, rotation_deg = error_mm_deg(refined.pose_flange_cam)
        self.assertLess(position_mm, 1e-3)
        self.assertLess(rotation_deg, 1e-4)
        self.assertAlmostEqual(refined.board_scale, 1.0, places=6)
        np.testing.assert_allclose(refined.pose_base_board, T_BASE_BOARD, atol=1e-6)
        self.assertTrue(refined.converged)
        self.assertLess(refined.reprojection_rms_px, 1e-4)

    def test_pixel_noise_refined_beats_park_martin(self):
        flanges, corners, boards = scene(sigma_px=0.3, seed=4)
        start, refined = refine(flanges, corners, boards)
        start_mm, _ = error_mm_deg(start)
        refined_mm, refined_deg = error_mm_deg(refined.pose_flange_cam)
        self.assertLess(refined_mm, start_mm)
        self.assertLess(refined_mm, 0.5)
        self.assertLess(refined_deg, 0.05)
        # Rest-Reprojektion etwa das eingespeiste Rauschen (2D-Betrag: sigma*sqrt(2)).
        self.assertAlmostEqual(refined.reprojection_rms_px, 0.3 * math.sqrt(2), delta=0.1)
        self.assertEqual(len(refined.sample_rms_px), 15)

    def test_wrong_square_size_is_found_and_no_longer_shifts_x(self):
        """Der feste Versatz: Brett 1 % groesser als angegeben."""
        flanges, corners, boards = scene(scale=1.01, sigma_px=0.2, seed=7)
        start, refined = refine(flanges, corners, boards)
        _, fixed = refine(flanges, corners, boards, estimate_scale=False)
        start_mm, _ = error_mm_deg(start)
        self.assertGreater(start_mm, 1.0, "ohne Massstab verschiebt sich t_X um Millimeter")
        # Streuung der Massstabsschaetzung bei 0,3 px: etwa 0,06 % (30 Seeds).
        self.assertAlmostEqual(refined.board_scale, 1.01, delta=0.002)
        self.assertLess(error_mm_deg(refined.pose_flange_cam)[0], 1.0)
        self.assertGreater(error_mm_deg(fixed.pose_flange_cam)[0], 1.0)
        self.assertEqual(fixed.board_scale, 1.0)

    def test_huber_damps_a_bad_sample_and_points_at_it(self):
        """Huber allein faengt eine ganz verschobene Aufnahme nicht ab (dafuer
        gibt es `refine_hand_eye_robust`), daempft sie aber stark."""
        flanges, corners, boards = scene(sigma_px=0.3, seed=9)
        corners = list(corners)
        corners[6] = corners[6] + np.array([6.0, -4.0])
        _, huber = refine(flanges, corners, boards)
        _, plain = refine(flanges, corners, boards, huber_delta_px=1e9)
        self.assertLess(error_mm_deg(huber.pose_flange_cam)[0], error_mm_deg(plain.pose_flange_cam)[0] / 3)
        worst = max(range(15), key=lambda i: huber.sample_rms_px[i])
        self.assertEqual(worst, 6)

    def test_mismatched_inputs_are_refused(self):
        flanges, corners, boards = scene(count=5)
        with self.assertRaises(ValueError):
            refine_hand_eye(flanges, corners[:4], board_points(), K,
                            initial_flange_cam=X_TRUE, initial_base_board=T_BASE_BOARD)
        with self.assertRaises(ValueError):
            refine_hand_eye(flanges, [c[:-1] for c in corners], board_points(), K,
                            initial_flange_cam=X_TRUE, initial_base_board=T_BASE_BOARD)


@unittest.skipUnless(np is not None, "numpy/cv2 fehlt")
class LeaveOneOutTest(unittest.TestCase):
    def test_prediction_error_is_about_the_noise_and_finds_the_bad_sample(self):
        flanges, corners, boards = scene(count=10, sigma_px=0.3, seed=11)
        _, full = refine(flanges, corners, boards)
        loo = leave_one_out_errors(flanges, corners, boards, board_points(), K, full)
        self.assertEqual(len(loo.error_px), 10)
        # Vorhersage einer fremden Ansicht: etwas schlechter als das Rauschen, nie viel.
        self.assertGreater(loo.rms_px, 0.3)
        self.assertLess(loo.rms_px, 1.0)
        self.assertLess(loo.rms_m, 0.001)

        corners = list(corners)
        corners[3] = corners[3] + np.array([5.0, 0.0])
        _, full = refine(flanges, corners, boards)
        loo = leave_one_out_errors(flanges, corners, boards, board_points(), K, full)
        self.assertEqual(max(range(10), key=lambda i: loo.error_px[i]), 3)

    def test_scale_error_is_not_counted_as_prediction_error(self):
        flanges, corners, boards = scene(count=8, scale=1.01, sigma_px=0.0, seed=12)
        _, full = refine(flanges, corners, boards)
        loo = leave_one_out_errors(flanges, corners, boards, board_points(), K, full)
        self.assertLess(loo.rms_m, 1e-5)

    def test_too_few_samples_are_refused(self):
        flanges, corners, boards = scene(count=3, sigma_px=0.0)
        _, full = refine(flanges, corners, boards)
        with self.assertRaises(ValueError):
            leave_one_out_errors(flanges, corners, boards, board_points(), K, full)


@unittest.skipUnless(np is not None and refine_hand_eye_robust is not None, "noch nicht gebaut")
class RobustRefineTest(unittest.TestCase):
    def robust(self, flanges, corners, boards, **options):
        return refine_hand_eye_robust(flanges, corners, boards, board_points(), K, **options)

    def test_a_scale_error_alone_rejects_nothing(self):
        """Der Fall, den die Ausreisserpruefung auf der Park-Martin-Streuung falsch machte."""
        flanges, corners, boards = scene(count=14, scale=1.01, sigma_px=0.3, seed=21)
        refined, rejected, _ = self.robust(flanges, corners, boards)
        self.assertEqual(rejected, ())
        self.assertAlmostEqual(refined.board_scale, 1.01, delta=0.002)
        self.assertLess(error_mm_deg(refined.pose_flange_cam)[0], 1.0)

    def test_a_bad_sample_is_rejected_and_x_stays_good(self):
        flanges, corners, boards = scene(count=14, scale=1.01, sigma_px=0.3, seed=22)
        corners = list(corners)
        corners[5] = corners[5] + np.array([8.0, 3.0])
        refined, rejected, _ = self.robust(flanges, corners, boards)
        self.assertEqual(rejected, (5,))
        # So gut, als waere die Aufnahme nie gemacht worden.
        keep = [i for i in range(14) if i != 5]
        _, without = refine([flanges[i] for i in keep], [corners[i] for i in keep], [boards[i] for i in keep])
        self.assertAlmostEqual(
            error_mm_deg(refined.pose_flange_cam)[0], error_mm_deg(without.pose_flange_cam)[0], delta=0.02
        )
        self.assertLess(error_mm_deg(refined.pose_flange_cam)[0], 1.5)

    def test_rejection_is_capped(self):
        flanges, corners, boards = scene(count=8, sigma_px=0.3, seed=23)
        corners = [c + np.array([4.0 * (i + 1), 0.0]) if i < 5 else c for i, c in enumerate(corners)]
        _, rejected, _ = self.robust(flanges, corners, boards)
        self.assertLessEqual(len(rejected), 2)  # 25 % von 8

    def test_without_rejection_nothing_is_dropped(self):
        flanges, corners, boards = scene(count=8, sigma_px=0.3, seed=24)
        corners = list(corners)
        corners[2] = corners[2] + np.array([8.0, 0.0])
        _, rejected, _ = self.robust(flanges, corners, boards, reject=False)
        self.assertEqual(rejected, ())


if __name__ == "__main__":
    unittest.main()
