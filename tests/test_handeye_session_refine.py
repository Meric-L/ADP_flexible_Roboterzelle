"""Hand-Auge-Sitzung mit Brett und Ecken: verfeinern, Massstab, Vorhersagefehler,
Warnungen, Datensatz, Mindestzahl Drehachsen."""

import json
import math
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc import geometry as g
    from tagloc.calibration import CameraCalibration
    from tagloc.handeye import load_hand_eye
    from vision_server.errors import VisionErrorCode
    from vision_server.handeye_session import HandEyePorts, HandEyeSession

    from tests.test_handeye_refine import K, T_BASE_BOARD, X_TRUE, scene
    from tests.test_handeye_session import board_json, sample_json
except Exception:  # pragma: no cover - numpy/cv2 fehlt in dieser Umgebung
    np = None


class BoardPi:
    """Kamera und Knoten fuer eine Brett-Sitzung: liefert Pose, Ecken und Intrinsik
    der jeweils naechsten vorbereiteten Aufnahme."""

    def __init__(self, folder, flanges, corners, boards, source="calibration"):
        self.path = Path(folder) / "handeye" / "cam_flange.json"
        self.views = list(zip(flanges, corners, boards))
        self.next = 0
        self.statuses = []
        self.applied = []
        self.source = source
        self.calibration = CameraCalibration(
            camera_matrix=K.copy(), distortion=np.zeros(5), image_size=(640, 480), frame_id="cam_flange"
        )

    async def measure_board(self, spec):
        _, corners, board = self.views[self.next]
        self.next += 1
        return board, "", {
            "corners": corners,
            "calibration": self.calibration,
            "intrinsicsSource": self.source,
            "reprojectionErrorPx": 0.3,
        }

    async def no_tag(self, tag_id):  # pragma: no cover - darf nicht gerufen werden
        raise AssertionError("mit Brett wird kein Tag gemessen")

    async def publish(self, status):
        self.statuses.append(json.loads(status))

    def ports(self):
        return HandEyePorts(
            measure_tag=self.no_tag,
            busy=lambda: False,
            path=self.path,
            apply=self.applied.append,
            publish=self.publish,
            measure_board=self.measure_board,
            intrinsics_source=lambda: self.source,
        )

    @property
    def status(self):
        return self.statuses[-1]


def board_6x7():
    return board_json(cols=6, rows=7, size=0.023)


@unittest.skipUnless(np is not None, "numpy/cv2 fehlt")
class RefinedSessionTest(unittest.IsolatedAsyncioTestCase):
    async def run_session(self, pi, count):
        session = HandEyeSession(pi.ports())
        self.assertEqual(await session.start(board_6x7()), VisionErrorCode.OK)
        for flange, _, _ in pi.views[:count]:
            self.assertEqual(await session.capture(sample_json(flange)), VisionErrorCode.OK)
        return session

    async def test_scale_error_is_refined_away_and_reported(self):
        flanges, corners, boards = scene(count=12, scale=1.01, sigma_px=0.2, seed=31)
        with tempfile.TemporaryDirectory() as folder:
            pi = BoardPi(folder, flanges, corners, boards)
            session = await self.run_session(pi, 12)
            self.assertEqual(await session.finish(), VisionErrorCode.OK)
            status = pi.status
            result = status["result"]
            self.assertEqual(status["state"], "finished")
            self.assertEqual(status["intrinsicsSource"], "calibration")
            self.assertAlmostEqual(result["boardScale"], 1.01, delta=0.002)
            self.assertLess(result["refinedReprojectionRmsPx"], 0.6)
            self.assertIsNotNone(result["looErrorRmsPx"])
            self.assertLess(result["looErrorRmsM"], 0.002)
            self.assertTrue(any("Brettmassstab" in w for w in result["warnings"]))
            self.assertFalse(any("Werkswerte" in w or "Platzhalter" in w for w in result["warnings"]))
            np.testing.assert_allclose(
                g.pose_from_dict(result["targetInBase"])[:3, 3], T_BASE_BOARD[:3, 3], atol=0.002
            )
            self.assertTrue(all(s["looErrorPx"] is not None for s in status["samples"]))

            # Gespeichert und aktiv ist das verfeinerte X -- nahe der Wahrheit.
            saved = load_hand_eye(pi.path)
            self.assertLess(1000 * g.translation_distance_m(saved.pose_flange_cam, X_TRUE), 1.0)
            self.assertEqual(len(pi.applied), 1)
            raw = json.loads(pi.path.read_text(encoding="utf-8"))
            self.assertAlmostEqual(raw["boardScale"], result["boardScale"], places=5)

            # Der Datensatz reicht zum Nachrechnen.
            dataset = json.loads((Path(result["sessionPath"]) / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(dataset["outcome"], "finished")
            self.assertEqual(len(dataset["samples"]), 12)
            self.assertEqual(len(dataset["samples"][0]["cornersPx"]), 42)

    async def test_factory_intrinsics_are_named_and_warned(self):
        flanges, corners, boards = scene(count=8, sigma_px=0.2, seed=32)
        with tempfile.TemporaryDirectory() as folder:
            pi = BoardPi(folder, flanges, corners, boards, source="realsense_factory")
            session = await self.run_session(pi, 8)
            self.assertEqual(await session.finish(), VisionErrorCode.OK)
            self.assertEqual(pi.status["intrinsicsSource"], "realsense_factory")
            self.assertTrue(any("Werkswerte" in w for w in pi.status["result"]["warnings"]))

    async def test_a_shifted_sample_is_flagged_rejected(self):
        flanges, corners, boards = scene(count=12, sigma_px=0.2, seed=33)
        corners = list(corners)
        corners[4] = corners[4] + np.array([7.0, -3.0])
        with tempfile.TemporaryDirectory() as folder:
            pi = BoardPi(folder, flanges, corners, boards)
            session = await self.run_session(pi, 12)
            self.assertEqual(await session.finish(), VisionErrorCode.OK)
            flags = [s["rejected"] for s in pi.status["samples"]]
            self.assertEqual(flags.count(True), 1)
            self.assertTrue(flags[4])
            self.assertLess(
                1000 * g.translation_distance_m(load_hand_eye(pi.path).pose_flange_cam, X_TRUE), 1.0
            )


def turned_about_one_axis(count=5):
    """Flanschposen ueber dem Brett, nur um die Blickachse gedreht."""
    look = g.from_rvec_tvec((0, 0, 0), (0.05, 0.07, -0.45))  # Kamera vor dem Brett, schaut hinein
    poses = []
    for index in range(count):
        roll = g.from_rvec_tvec((0, 0, math.radians(-60 + 30 * index)), (0, 0, 0))
        poses.append(g.compose(T_BASE_BOARD, look, roll, g.invert(X_TRUE)))
    return poses


@unittest.skipUnless(np is not None, "numpy/cv2 fehlt")
class AxesTest(unittest.IsolatedAsyncioTestCase):
    async def test_fewer_than_three_axes_are_refused_and_the_session_stays_open(self):
        flanges = turned_about_one_axis()
        boards = [g.compose(g.invert(g.compose(f, X_TRUE)), T_BASE_BOARD) for f in flanges]
        with tempfile.TemporaryDirectory() as folder:
            pi = BoardPi(folder, flanges, [None] * len(flanges), boards)
            session = HandEyeSession(pi.ports())
            await session.start(board_6x7())
            for flange in flanges:
                self.assertEqual(await session.capture(sample_json(flange)), VisionErrorCode.OK)
            self.assertEqual(await session.finish(), VisionErrorCode.INVALID_STATE)
            self.assertEqual(pi.status["state"], "capturing")
            self.assertIn("Drehachse", pi.status["message"])
            self.assertTrue(session.active)
            self.assertFalse(pi.path.exists())


if __name__ == "__main__":
    unittest.main()
