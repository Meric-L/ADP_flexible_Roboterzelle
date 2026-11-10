"""MeasureTarget: Zielscheibe in Kamerakoordinaten (Visual Servoing beim Ablegen)."""

import json
import unittest
from datetime import datetime, timedelta, timezone

try:
    import numpy as np

    from use_case_eval import synthetic
    from use_case_eval.measure import measure_sheet
    from vision_server.measuretarget import (
        REQUEST_SCHEMA,
        RESULT_SCHEMA,
        TargetFrame,
        build_result,
        dumps,
        parse_request,
        pose_cam_sheet,
    )
except ImportError:  # pragma: no cover - ohne numpy/OpenCV
    np = None

WALL = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class RequestTest(unittest.TestCase):
    def test_defaults_and_limits(self):
        self.assertEqual(parse_request("").frames, 3)
        self.assertEqual(parse_request(json.dumps({"schema": REQUEST_SCHEMA, "frames": 5})).frames, 5)
        for broken in ("[]", "{", json.dumps({"frames": 0}), json.dumps({"frames": 11}), json.dumps({"schema": "x"})):
            with self.subTest(broken=broken):
                with self.assertRaises(ValueError):
                    parse_request(broken)


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class SheetPoseTest(unittest.TestCase):
    CALIBRATION = synthetic.make_calibration() if np is not None else None

    def render(self, x=0.0, y=0.0, height=250.0, yaw=0.0, tilt=0.0, **kwargs):
        pose = synthetic.look_down_pose(x, y, height, yaw, tilt)
        image = synthetic.render_trial(self.CALIBRATION, pose, 0.0, 0.0, 0.0, supersample=4, **kwargs)
        return image, pose

    def test_pose_in_camera_is_the_inverse_of_where_the_camera_stands(self):
        image, sheet_cam = self.render(x=4.0, y=-3.0, height=250.0, yaw=20.0, tilt=3.0)
        pose = pose_cam_sheet(measure_sheet(image, self.CALIBRATION))
        self.assertIsNotNone(pose)
        expected = np.linalg.inv(sheet_cam)
        expected[:3, 3] /= 1000.0
        np.testing.assert_allclose(pose[:3, 3], expected[:3, 3], atol=0.0005)
        np.testing.assert_allclose(pose[:3, :3], expected[:3, :3], atol=0.005)

    def test_a_cube_in_the_way_does_not_matter_while_two_markers_are_seen(self):
        image, _ = self.render(with_sheet_markers=[1, 3])
        self.assertIsNotNone(pose_cam_sheet(measure_sheet(image, self.CALIBRATION)))

    def test_result_averages_the_frames_that_found_the_sheet_and_keeps_every_frame(self):
        seen, _ = self.render(x=2.0)
        blind, _ = self.render(with_sheet_markers=[1])
        frames = [
            TargetFrame(WALL, (640, 480), measure_sheet(seen, self.CALIBRATION)),
            TargetFrame(WALL + timedelta(milliseconds=70), (640, 480), measure_sheet(blind, self.CALIBRATION)),
        ]
        result = build_result(
            frames, frames_requested=2, frame_id="cam_flange", intrinsics_source="calibration", calibration_id="c"
        )
        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertTrue(result["found"])
        self.assertEqual(result["sheetMarkers"], [0, 1, 2, 3])
        self.assertEqual([frame["found"] for frame in result["frames"]], [True, False])
        self.assertIn("Blattmarker", result["frames"][1]["reason"])
        # Camera 2 mm along sheet x: the sheet's origin lies 2 mm along camera -x (camera x = sheet x).
        self.assertAlmostEqual(result["poseInCamera"]["position"][0], -0.002, delta=0.0005)
        json.loads(dumps(result))

    def test_nothing_found_is_no_error(self):
        blind, _ = self.render(with_sheet_markers=[])
        result = build_result(
            [TargetFrame(WALL, (640, 480), measure_sheet(blind, self.CALIBRATION))],
            frames_requested=1, frame_id="cam_flange", intrinsics_source="calibration", calibration_id="",
        )
        self.assertFalse(result["found"])
        self.assertIsNone(result["poseInCamera"])
        self.assertIsNone(result["error"])


if __name__ == "__main__":
    unittest.main()
