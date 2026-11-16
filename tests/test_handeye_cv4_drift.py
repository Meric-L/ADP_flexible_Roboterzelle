"""Driftmodell der Offline-Auswertung: findet eine bekannte Brettdrift wieder."""

import importlib.util
import unittest
from pathlib import Path

import numpy as np

from tagloc import geometry, lens
from tagloc.boards import BoardSpec, chessboard_object_points
from tagloc.calibration import CameraCalibration
from tagloc.handeye_offline import Observation

SPEC = BoardSpec(type="chessboard", cols=7, rows=10, square_size_m=0.023)
CAL = CameraCalibration(np.array([[700., 0, 320], [0, 700., 240], [0, 0, 1]]),
                        np.zeros(5), (640, 480), frame_id="cam_flange")
DRIFT_PATH = Path(__file__).resolve().parents[1] / "tools" / "handeye_offline_cv4" / "drift.py"


def _load_drift():
    spec = importlib.util.spec_from_file_location("handeye_cv4_drift", DRIFT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _session(rate):
    """30 Posen ueber 400 s; die Brettlage wandert mit `rate` (rvec/s, m/s)."""
    points = chessboard_object_points(SPEC)
    handeye = geometry.from_rvec_tvec([0.08, -0.04, 0.1], [-0.047, -0.034, 0.057])
    base_board = geometry.from_rvec_tvec([0, 0, 0.1], [0.45, 0.05, 0])
    times = np.linspace(-200.0, 200.0, 30)
    observations = []
    for i, t in enumerate(times):
        flange = geometry.from_rvec_tvec(
            [0.25 * np.sin(i), 0.3 * np.cos(i * 1.4), 0.6 * np.sin(i * 0.7)],
            [0.4 + 0.02 * np.sin(i), 0.04 + 0.02 * np.cos(i), 0.4 + 0.005 * i])
        drifted = base_board @ geometry.from_rvec_tvec(rate[:3] * t, rate[3:] * t)
        board = geometry.invert(flange @ handeye) @ drifted
        corners = lens.project_points(points, geometry.rvec_from_rotation(board[:3, :3]),
                                      board[:3, 3], CAL)
        observations.append(Observation(f"pose_{i:03d}", "ABCDE"[i % 5],
                                        flange, board, corners, 0.0, None, points))
    return observations, times, points, handeye


class DriftModelTest(unittest.TestCase):
    def setUp(self):
        self.drift = _load_drift()

    def test_board_drift_is_recovered_and_rigid_model_is_not_enough(self):
        rate = np.r_[0.0, 0.0, 0.0, -3e-3 / 400, 0.0, -1.5e-3 / 400]  # 3 mm x, 1,5 mm z
        obs, times, points, handeye = _session(rate)
        rigid, _ = self.drift.fit(obs, times, CAL, points, "rigid")
        drift, _ = self.drift.fit(obs, times, CAL, points, "boardDrift")
        self.assertGreater(rigid["reprojectionRmsPx"], 0.5)
        self.assertLess(drift["reprojectionRmsPx"], 1e-3)
        np.testing.assert_allclose(drift["driftOverSessionMm"], [-3.0, 0.0, -1.5], atol=0.02)
        np.testing.assert_allclose(drift["translationMm"], handeye[:3, 3] * 1000, atol=0.02)

    def test_without_drift_both_models_agree(self):
        obs, times, points, handeye = _session(np.zeros(6))
        rigid, _ = self.drift.fit(obs, times, CAL, points, "rigid")
        drift, _ = self.drift.fit(obs, times, CAL, points, "boardDrift")
        self.assertLess(drift["driftOverSessionNormMm"], 0.01)
        np.testing.assert_allclose(rigid["translationMm"], handeye[:3, 3] * 1000, atol=0.01)
        np.testing.assert_allclose(drift["translationMm"], rigid["translationMm"], atol=0.01)


if __name__ == "__main__":
    unittest.main()
