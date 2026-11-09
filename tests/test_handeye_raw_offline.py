"""Rohaufnahme und Offline-Rechnung mit eindeutiger Pose-Zuordnung."""

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from tagloc import geometry, lens
from tagloc.boards import BoardSpec, chessboard_object_points
from tagloc.calibration import CameraCalibration
from tagloc.handeye_offline import Observation, load_observations, solve_subset
from vision_server.errors import VisionErrorCode
from vision_server.handeye_session import HandEyePorts, HandEyeSession


SPEC = BoardSpec(type="chessboard", cols=7, rows=10, square_size_m=0.023)
CAL = CameraCalibration(np.array([[700., 0, 320], [0, 700., 240], [0, 0, 1]]),
                        np.zeros(5), (640, 480), frame_id="cam_flange")


class RawCaptureTest(unittest.IsolatedAsyncioTestCase):
    async def test_image_and_robot_state_share_pose_id_without_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            applied, statuses = [], []

            async def measure(_):
                return geometry.identity(), "", {
                    "image": np.full((480, 640), 127, dtype=np.uint8),
                    "calibration": CAL, "capturedAt": "2026-11-09T10:00:00+00:00",
                    "intrinsicsSource": "calibration", "reprojectionErrorPx": 0.2,
                }

            async def publish(text):
                statuses.append(json.loads(text))

            session = HandEyeSession(HandEyePorts(
                measure_tag=measure, capture_board_raw=measure, busy=lambda: False,
                path=Path(tmp) / "active.json", apply=applied.append, publish=publish,
                raw_sessions_dir=Path(tmp) / "data" / "rohbilder" / "calibration"))
            start = {"schema": "wsc.vision.handeye.start/1", "captureOnly": True,
                     "board": SPEC.as_dict()}
            self.assertEqual(await session.start(json.dumps(start)), VisionErrorCode.OK)
            sample = {"schema": "wsc.vision.handeye.sample/1", "poseId": "pose_007",
                      "poseGroup": "B", "flangeInBase": geometry.pose_to_dict(geometry.identity()),
                      "jointAnglesRad": [0.] * 7}
            self.assertEqual(await session.capture(json.dumps(sample)), VisionErrorCode.OK)
            self.assertEqual(await session.capture(json.dumps({
                "schema": "wsc.vision.handeye.sample/1", "invalidatePoseId": "pose_007",
                "reason": "Arm bewegte sich"})), VisionErrorCode.OK)
            self.assertEqual(statuses[-1]["acceptedCount"], 0)
            self.assertEqual(await session.finish(), VisionErrorCode.OK)
            folder = Path(statuses[-1]["sessionPath"])
            image = folder / "poses" / "pose_007" / "camera.png"
            state = json.loads((image.parent / "robot_state.json").read_text())
            self.assertEqual(cv2.imread(str(image), cv2.IMREAD_UNCHANGED).shape, (480, 640))
            self.assertEqual(state["poseId"], image.parent.name)
            self.assertEqual(state["poseGroup"], "B")
            self.assertFalse(json.loads((image.parent / "quality.json").read_text())["valid"])
            loaded, excluded, _, _ = load_observations(folder)
            self.assertEqual(loaded, [])
            self.assertIn("verworfen", excluded[0]["reason"])
            self.assertEqual(json.loads((folder / "config.json").read_text())["board"]["cols"], 7)
            self.assertEqual(applied, [])
            self.assertFalse((Path(tmp) / "active.json").exists())


class OfflineSolverTest(unittest.TestCase):
    def test_offline_reader_detects_full_board_from_saved_png(self):
        square_px, margin = 50, 20
        texture = np.full((11 * square_px + 2 * margin,
                           8 * square_px + 2 * margin), 255, dtype=np.uint8)
        for row in range(11):
            for col in range(8):
                if (row + col) % 2 == 0:
                    texture[margin + row * square_px:margin + (row + 1) * square_px,
                            margin + col * square_px:margin + (col + 1) * square_px] = 0
        board = geometry.from_rotation_translation(np.eye(3), [-0.069, -0.1035, 0.45])
        scale = SPEC.square_size_m / square_px
        image_to_board = np.array([[scale, 0, -(margin + square_px) * scale],
                                   [0, scale, -(margin + square_px) * scale], [0, 0, 1]])
        plane = np.column_stack([board[:3, 0], board[:3, 1], board[:3, 3]])
        image = cv2.warpPerspective(texture, CAL.camera_matrix @ plane @ image_to_board,
                                    CAL.image_size, borderValue=255)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "poses" / "pose_000").mkdir(parents=True)
            from tagloc.handeye import intrinsics_to_dict
            (folder / "config.json").write_text(json.dumps({
                "schema": "wsc.vision.handeye.raw/1", "imageStream": "configured",
                "board": SPEC.as_dict(), "intrinsics": intrinsics_to_dict(CAL),
                "intrinsicsSource": "calibration"}))
            (folder / "poses" / "pose_000" / "robot_state.json").write_text(json.dumps({
                "poseId": "pose_000", "poseGroup": "A", "T_base_flange": geometry.identity().tolist(),
                "jointAnglesRad": [0] * 7}))
            cv2.imwrite(str(folder / "poses" / "pose_000" / "camera.png"), image)
            observed, excluded, _, points = load_observations(folder)
            self.assertEqual(excluded, [])
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0].corners.shape, (70, 2))
            self.assertEqual(points.shape, (70, 3))

    def test_three_methods_and_refinement_recover_known_transform(self):
        points = chessboard_object_points(SPEC)
        handeye = geometry.from_rvec_tvec([0.08, -0.04, 0.1], [0.025, -0.018, 0.08])
        base_board = geometry.from_rvec_tvec([0, 0, 0.1], [0.45, 0.05, 0])
        observations = []
        for i in range(12):
            flange = geometry.from_rvec_tvec(
                [0.12 * np.sin(i), 0.13 * np.cos(i * 1.4), 0.2 * np.sin(i * 0.7)],
                [0.4 + 0.02 * np.sin(i), 0.04 + 0.02 * np.cos(i), 0.4 + 0.01 * i])
            board = geometry.invert(flange @ handeye) @ base_board
            corners = lens.project_points(points, geometry.rvec_from_rotation(board[:3, :3]),
                                          board[:3, 3], CAL)
            observations.append(Observation(f"pose_{i:03d}", "ABCDE"[i % 5],
                                            flange, board, corners, 0.0, None))
        solved = solve_subset(observations, CAL, points)
        self.assertIn("T_flange_camera", solved["methods"]["park"])
        self.assertIn("T_flange_camera", solved["methods"]["tsai"])
        self.assertIn("T_flange_camera", solved["methods"]["daniilidis"])
        measured = np.asarray(solved["optimized"]["T_flange_camera"])
        self.assertLess(geometry.translation_distance_m(measured, handeye), 1e-5)
        self.assertLess(solved["optimized"]["reprojectionRmsPx"], 1e-4)
