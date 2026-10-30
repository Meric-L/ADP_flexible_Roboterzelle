"""Gepaarter Vergleich des Layer-2-Versuchs ohne Kamera-Hardware."""

import importlib.util
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np

from tagloc.calibration import CameraCalibration, save_calibration
from tagloc.geometry import identity, pose_to_dict
from tagloc.handeye import HandEye, save_hand_eye


SPEC = importlib.util.spec_from_file_location(
    "layer2_information_gain", Path(__file__).resolve().parents[1] / "tools/layer2_information_gain.py"
)
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)
CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "layer2_capture_view", Path(__file__).resolve().parents[1] / "tools/layer2_capture_view.py"
)
capture_tool = importlib.util.module_from_spec(CAPTURE_SPEC)
CAPTURE_SPEC.loader.exec_module(capture_tool)


def observed(x_mm, ambiguous=False):
    pose = identity()
    pose[0, 3] = x_mm / 1000
    return SimpleNamespace(pose_cam_tag=pose, is_ambiguous=ambiguous)


class InformationGainTest(unittest.TestCase):
    def test_capture_view_saves_images_and_actual_flange_pose(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            session_path = folder / "session.json"
            session_path.write_text(json.dumps({"schema": experiment.SCHEMA, "trials": []}), encoding="utf-8")
            pose_path = folder / "pose.json"
            flange = pose_to_dict(identity())
            pose_path.write_text(json.dumps(flange), encoding="utf-8")
            originals = []
            for index in range(3):
                path = folder / f"original-{index}.png"
                self.assertTrue(cv2.imwrite(str(path), np.full((4, 4), index, dtype=np.uint8)))
                originals.append(path)

            class Machine:
                index = 0

                async def call_method(self, method):
                    path = originals[self.index]
                    self.index += 1
                    return str(path), 0

            machine = Machine()

            class Client:
                def __init__(self, endpoint):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                def get_node(self, node_id):
                    return machine if node_id == "machine" else "method"

            args = SimpleNamespace(
                session=session_path, pose_file=pose_path, trial=1, view=1, interval=0,
                endpoint="opc.tcp://test", machine_node="machine", method_node="method", repo_root=folder,
            )
            with mock.patch.object(capture_tool, "Client", Client):
                asyncio.run(capture_tool.capture(args))
            saved = json.loads(session_path.read_text(encoding="utf-8"))["trials"][0]["views"][0]
            self.assertEqual(saved["flangeInBase"], flange)
            self.assertEqual(len(saved["images"]), 3)
            self.assertTrue(all((folder / name).is_file() for name in saved["images"]))

    def test_offline_replay_of_saved_pngs(self):
        with tempfile.TemporaryDirectory() as folder_name:
            folder = Path(folder_name)
            calibration = CameraCalibration(
                np.array([[600, 0, 320], [0, 600, 240], [0, 0, 1]], dtype=float),
                np.zeros(5), (640, 480), frame_id="cam_flange", calibration_id="test",
            )
            save_calibration(folder / "camera.json", calibration)
            save_hand_eye(folder / "handeye.json", HandEye(pose_flange_cam=identity(), frame_id="cam_flange"))
            image = np.full((480, 640), 255, dtype=np.uint8)
            marker = cv2.aruco.generateImageMarker(
                cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), 7, 140
            )
            image[170:310, 250:390] = marker
            views = []
            for view in range(3):
                names = []
                for frame in range(3):
                    name = f"v{view}-f{frame}.png"
                    self.assertTrue(cv2.imwrite(str(folder / name), image))
                    names.append(name)
                views.append({"flangeInBase": pose_to_dict(identity()), "images": names})
            session = {
                "schema": experiment.SCHEMA, "tagId": 7, "tagSizeM": 0.05,
                "calibration": "camera.json", "handEye": "handeye.json",
                "trials": [{"id": 1, "views": views}],
            }
            path = folder / "session.json"
            path.write_text(json.dumps(session), encoding="utf-8")
            result = experiment.evaluate(path)
        self.assertEqual(result["summary"]["drei_ansichten"]["successfulTrials"], 1)
        self.assertTrue(all(row["detectedFrames"] == 3 for row in result["trials"][0]["views"]))

    def test_frame_position_uses_all_but_orientation_requires_clear_image(self):
        estimate = experiment._combine([observed(0), observed(3, True)], identity(), identity())
        self.assertAlmostEqual(estimate["pose"][0, 3], 0.0015)
        self.assertTrue(estimate["orientationValid"])
        only_ambiguous = experiment._combine([observed(2, True)], identity(), identity())
        self.assertFalse(only_ambiguous["orientationValid"])

    def test_paired_gain_is_per_trial_and_equal_budget_is_separate(self):
        def entry(error_mm):
            return {"error": {"positionMm": error_mm, "orientationDeg": 1.0}, "orientationValid": True}

        trials = [
            {"strategies": {"ein_bild": entry(5), "drei_bilder": entry(4),
                            "drei_ansichten_je_ein_bild": entry(2), "drei_ansichten": entry(1)}},
            {"strategies": {"ein_bild": entry(4), "drei_bilder": entry(3),
                            "drei_ansichten_je_ein_bild": entry(2), "drei_ansichten": entry(1)}},
            {"strategies": {"ein_bild": None, "drei_bilder": None,
                            "drei_ansichten_je_ein_bild": None, "drei_ansichten": None}},
        ]
        summary = experiment.summarise(trials, True)
        self.assertEqual(summary["ein_bild"]["successfulTrials"], 2)
        self.assertEqual(summary["ein_bild"]["totalTrials"], 3)
        self.assertEqual(summary["pairedGain"]["drei_ansichten"]["positionMm"]["median"], 3.5)
        self.assertEqual(
            summary["pairedGain"]["ansichten_gegen_drei_bilder_gleiches_budget"]["positionMm"]["mean"], 1.5
        )
        self.assertEqual(summary["pairedGain"]["drei_ansichten"]["positionMm"]["mean95Ci"], [3, 4])

    def test_without_reference_only_repeatability_is_reported(self):
        def entry(x_mm):
            return {"tagInBase": {"position": [x_mm / 1000, 0, 0], "orientation": [0, 0, 0, 1]},
                    "orientationValid": False}

        trials = [{"strategies": {name: entry(x) for name in experiment.STRATEGIES}} for x in (0, 2)]
        summary = experiment.summarise(trials, False)
        self.assertAlmostEqual(summary["ein_bild"]["repeatabilityPositionMm"]["median"], 1)
        self.assertNotIn("pairedGain", summary)
        self.assertNotIn("orientationDeg", summary["ein_bild"])


if __name__ == "__main__":
    unittest.main()
