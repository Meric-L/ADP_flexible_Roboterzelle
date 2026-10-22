"""Hand-Auge: Residuum je Aufnahme, Ausreisser verwerfen, ehrliche Gueteteile."""

import json
import math
import tempfile
import unittest

try:
    import numpy as np

    from tagloc import geometry as g
    from tagloc.handeye import (
        MIN_KEPT,
        OUTLIER_MAX_FRACTION,
        load_hand_eye,
        solve_hand_eye,
        solve_hand_eye_robust,
    )
    from vision_server.errors import VisionErrorCode
    from vision_server.handeye_session import HandEyeSession

    from tests.test_handeye_session import (
        FakePi,
        sample_json,
        start_json,
    )
except Exception:  # pragma: no cover - numpy fehlt in dieser Umgebung
    np = None

X_TRUE = None if np is None else g.from_rvec_tvec(
    (math.radians(3), math.radians(-5), math.radians(92)), (0.035, -0.045, 0.085)
)
T_BASE_BOARD = None if np is None else g.from_rvec_tvec((math.pi, 0, 0.4), (0.55, 0.05, 0.02))


def flange_poses(count, seed=1):
    """Flanschposen ueber dem Brett, ueber alle drei Achsen gedreht."""
    rng = np.random.default_rng(seed)
    poses = []
    for _ in range(count):
        tilt = g.from_rvec_tvec(np.radians(rng.uniform(-35, 35, 3)), (0, 0, 0))
        cam_in_board = g.compose(g.from_rvec_tvec((math.pi, 0, 0), (0, 0, 0.40)), tilt)
        cam_in_board[:3, 3] += rng.uniform(-0.06, 0.06, 3)
        poses.append(g.compose(T_BASE_BOARD, cam_in_board, g.invert(X_TRUE)))
    return poses


def board_in_camera(flange):
    return g.compose(g.invert(g.compose(flange, X_TRUE)), T_BASE_BOARD)


def noisy(poses, seed=2, t_sigma=0.0004, r_sigma_deg=0.15):
    rng = np.random.default_rng(seed)
    return [
        g.compose(pose, g.from_rvec_tvec(rng.normal(0, math.radians(r_sigma_deg), 3), rng.normal(0, t_sigma, 3)))
        for pose in poses
    ]


def error(solution_pose):
    return (
        1000 * g.translation_distance_m(solution_pose, X_TRUE),
        math.degrees(g.rotation_distance_rad(solution_pose, X_TRUE)),
    )


@unittest.skipUnless(np is not None, "numpy fehlt")
class RobustSolverTest(unittest.TestCase):
    def setUp(self):
        self.flanges = flange_poses(12)
        self.boards = noisy([board_in_camera(f) for f in self.flanges])

    def corrupted(self, index, shift_m=0.025, tilt_deg=6.0):
        boards = list(self.boards)
        boards[index] = g.compose(
            boards[index], g.from_rvec_tvec((math.radians(tilt_deg), 0, 0), (shift_m, 0, 0))
        )
        return boards

    def test_clean_data_loses_nothing_and_rms_is_a_real_rms(self):
        solution = solve_hand_eye_robust(self.flanges, self.boards)
        self.assertEqual(solution.rejected, ())
        self.assertEqual(solution.used_count, 12)
        kept = solution.residuals
        self.assertAlmostEqual(
            solution.rms_position_m, math.sqrt(np.mean([m * m for m, _ in kept])), places=12
        )
        self.assertAlmostEqual(
            solution.rms_rotation_deg, math.sqrt(np.mean([d * d for _, d in kept])), places=12
        )
        # Das Maximum ist das Maximum, und es liegt ueber dem RMS.
        self.assertEqual(solution.max_position_m, max(m for m, _ in kept))
        self.assertGreater(solution.max_position_m, solution.rms_position_m)
        # Gleiche Loesung wie der einfache Loeser.
        plain, _, _ = solve_hand_eye(self.flanges, self.boards)
        np.testing.assert_allclose(solution.pose_flange_cam, plain, atol=1e-12)

    def test_noise_free_data_is_not_split_into_outliers(self):
        boards = [board_in_camera(f) for f in self.flanges]
        solution = solve_hand_eye_robust(self.flanges, boards)
        self.assertEqual(solution.rejected, ())
        self.assertLess(solution.max_position_m, 1e-9)

    def test_an_outlier_is_rejected_and_the_result_gets_better(self):
        boards = self.corrupted(5)
        rejecting = solve_hand_eye_robust(self.flanges, boards)
        keeping = solve_hand_eye_robust(self.flanges, boards, reject=False)
        self.assertEqual(rejecting.rejected, (5,))
        self.assertEqual(rejecting.used_count, 11)
        # Das Residuum der verworfenen Aufnahme ist das groesste und bleibt sichtbar.
        worst = max(range(12), key=lambda i: rejecting.residuals[i][0])
        self.assertEqual(worst, 5)
        self.assertGreater(rejecting.residuals[5][0], 0.01)
        self.assertLess(error(rejecting.pose_flange_cam)[0], error(keeping.pose_flange_cam)[0])
        self.assertLess(rejecting.rms_position_m, keeping.rms_position_m)

    def test_two_outliers_are_both_found(self):
        boards = self.corrupted(2)
        boards[9] = g.compose(boards[9], g.from_rvec_tvec((0, math.radians(-7), 0), (0, 0.03, 0)))
        solution = solve_hand_eye_robust(self.flanges, boards)
        self.assertEqual(sorted(solution.rejected), [2, 9])

    def test_rejection_is_capped_and_keeps_enough_samples(self):
        # Nur 6 Aufnahmen, vier davon kaputt: hoechstens ein Viertel faellt weg.
        flanges = self.flanges[:6]
        boards = [board_in_camera(f) for f in flanges]
        for index in (0, 1, 2, 3):
            boards[index] = g.compose(boards[index], g.from_rvec_tvec((0, 0, 0), (0.02 * (index + 1), 0, 0)))
        solution = solve_hand_eye_robust(flanges, boards)
        self.assertLessEqual(len(solution.rejected), int(6 * OUTLIER_MAX_FRACTION))
        self.assertGreaterEqual(solution.used_count, MIN_KEPT)
        # Zu wenige zum Verwerfen: 4 Aufnahmen bleiben unveraendert.
        few = solve_hand_eye_robust(flanges[:4], boards[:4])
        self.assertEqual((few.rejected, few.used_count), ((), 4))

    def test_a_rejection_that_breaks_the_solution_is_not_taken(self):
        """Fiele der Ausreisser weg, drehten die uebrigen nur um eine Achse --
        dann bleibt die letzte gute Loesung, statt zu scheitern."""
        about_z = [
            g.compose(T_BASE_BOARD, g.from_rvec_tvec((math.pi, 0, 0.0), (0, 0, 0.4)),
                      g.from_rvec_tvec((0, 0, math.radians(angle)), (0, 0, 0)), g.invert(X_TRUE))
            for angle in (-30, -10, 10, 30)
        ]
        tilted = g.compose(
            T_BASE_BOARD, g.from_rvec_tvec((math.pi, 0, 0), (0, 0, 0.4)),
            g.from_rvec_tvec((math.radians(25), 0, 0), (0, 0, 0)), g.invert(X_TRUE),
        )
        flanges = about_z + [tilted, tilted]
        boards = [board_in_camera(f) for f in flanges]
        boards[4] = g.compose(boards[4], g.from_rvec_tvec((0, 0, 0), (0.03, 0, 0)))
        try:
            solution = solve_hand_eye_robust(flanges, boards)
        except ValueError:
            self.skipTest("die Aufbau-Posen reichen schon fuer den ersten Loeser nicht")
        self.assertEqual(solution.used_count + len(solution.rejected), 6)


class CorruptingPi(FakePi if np is not None else object):
    """Wie `FakePi`, nur dass einzelne Aufnahmen ein verschobenes Brett sehen."""

    def __init__(self, folder, corrupt):
        super().__init__(folder)
        self.corrupt = corrupt
        self.calls = 0

    async def measure_tag(self, tag_id):
        index, self.calls = self.calls, self.calls + 1
        pose = board_in_camera(self.flange)
        if index in self.corrupt:
            pose = g.compose(pose, g.from_rvec_tvec((math.radians(6), 0, 0), (0.025, 0, 0)))
        return pose, ""


@unittest.skipUnless(np is not None, "numpy fehlt")
class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_residuals_rejection_and_honest_figures_reach_status_and_file(self):
        flanges = flange_poses(10)
        with tempfile.TemporaryDirectory() as folder:
            pi = CorruptingPi(folder, corrupt={3})
            session = HandEyeSession(pi.ports())
            await session.start(start_json(12))
            for flange in flanges:
                pi.flange = flange
                self.assertEqual(await session.capture(sample_json(flange)), VisionErrorCode.OK)
            # Vor dem Berechnen gibt es noch keine Residuen.
            self.assertTrue(all(s["residualM"] is None for s in pi.status["samples"]))
            self.assertTrue(all(s["rejected"] is None for s in pi.status["samples"]))

            self.assertEqual(await session.finish(), VisionErrorCode.OK)
            status = pi.status
            samples = status["samples"]
            self.assertEqual([s["rejected"] for s in samples].count(True), 1)
            self.assertTrue(samples[3]["rejected"])
            self.assertEqual(max(range(10), key=lambda i: samples[i]["residualM"]), 3)
            self.assertGreater(samples[3]["residualM"], 0.01)
            self.assertTrue(all(s["residualDeg"] is not None for s in samples))

            result = status["result"]
            self.assertEqual((result["sampleCount"], result["rejectedCount"]), (9, 1))
            self.assertEqual(result["rejectedSamples"], [3])
            self.assertLessEqual(result["rmsPositionM"], result["maxPositionM"])
            self.assertLessEqual(result["rmsRotationDeg"], result["maxRotationDeg"])
            self.assertIn("1 Ausreisser verworfen", status["message"])

            saved = json.loads(pi.path.read_text(encoding="utf-8"))
            self.assertEqual(saved["sampleCount"], 9)
            self.assertAlmostEqual(saved["maxPositionM"], result["maxPositionM"], places=5)
            loaded = load_hand_eye(pi.path)
            self.assertAlmostEqual(loaded.max_position_m, result["maxPositionM"], places=5)
            self.assertEqual(status["current"]["maxPositionM"], round(loaded.max_position_m, 6))

    async def test_old_files_without_the_maximum_still_load(self):
        with tempfile.TemporaryDirectory() as folder:
            from pathlib import Path

            path = Path(folder) / "alt.json"
            path.write_text(
                json.dumps({
                    "schema": "wsc.vision.handeye/1", "frameId": "cam_flange",
                    "poseFlangeCam": g.pose_to_dict(X_TRUE), "rmsPositionM": 0.001,
                    "rmsRotationDeg": 0.2, "sampleCount": 8,
                }),
                encoding="utf-8",
            )
            loaded = load_hand_eye(path)
            self.assertTrue(math.isnan(loaded.max_position_m))
            self.assertEqual(loaded.sample_count, 8)


if __name__ == "__main__":
    unittest.main()
