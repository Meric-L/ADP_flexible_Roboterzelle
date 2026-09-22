"""Was jeder Pi an Kamera- und Erkennungseinstellungen bekommt.

Die Identitaet kommt hier ueber `VISION_FRAME_ID`/`VISION_CAMERA_BACKEND`
statt ueber den Hostnamen -- dieselben Variablen, mit denen man einen Pi auch
von Hand umstellen kann.
"""

import os
import unittest
from unittest import mock

from vision_server.server import vision_config


def config_for(frame_id: str, backend: str):
    with mock.patch.dict(
        os.environ, {"VISION_FRAME_ID": frame_id, "VISION_CAMERA_BACKEND": backend}
    ):
        return vision_config()


class CeilingCameraTest(unittest.TestCase):
    """Pi 1: HQ-Kamera (IMX477) mit voller Sensoraufloesung."""

    def setUp(self):
        self.config = config_for("cam_ceiling", "picamera2")

    def test_jobs_and_camera_use_the_full_12_megapixels(self):
        self.assertEqual(self.config.apriltag.resolution, (4056, 3040))
        self.assertEqual(self.config.camera_stream.resolution, (4056, 3040))

    def test_livestream_gets_the_small_isp_stream(self):
        stream = self.config.camera_stream
        self.assertEqual(stream.preview_resolution, (960, 720))
        self.assertLessEqual(stream.preview_resolution[0], stream.max_stream_width)

    def test_preview_keeps_the_sensor_aspect_ratio(self):
        """Sonst lehnt `scale_to_resolution` das Umrechnen der Kalibrierung ab."""
        full = self.config.camera_stream.resolution
        preview = self.config.camera_stream.preview_resolution
        self.assertLess(abs(preview[0] / full[0] - preview[1] / full[1]), 1e-3)

    def test_asks_only_for_what_the_sensor_delivers_at_full_resolution(self):
        self.assertEqual(self.config.camera_stream.capture_fps, 10.0)
        self.assertEqual(self.config.camera_stream.buffer_count, 2)

    def test_accepts_the_old_binned_calibration_until_recalibrated(self):
        self.assertTrue(self.config.apriltag.allow_resolution_mismatch)


class FlangeCameraTest(unittest.TestCase):
    """Pi 2 (RealSense): von der Umstellung unberuehrt."""

    def test_keeps_its_settings(self):
        config = config_for("cam_flange", "realsense")
        self.assertEqual(config.apriltag.resolution, (640, 480))
        self.assertIsNone(config.camera_stream.preview_resolution)
        self.assertIsNone(config.camera_stream.buffer_count)
        self.assertFalse(config.apriltag.allow_resolution_mismatch)


if __name__ == "__main__":
    unittest.main()
