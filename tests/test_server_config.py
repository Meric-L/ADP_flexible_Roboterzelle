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

    def test_overlay_gets_more_time_for_the_full_12_megapixels(self):
        """Der globale Default (2,0 s, profiles.py) reichte nicht fuer
        Erkennung + Zeichnen auf 4056x3040 -- das Overlay fiel jeden Tick auf
        das unmarkierte Rohbild zurueck (Bug: Overlay-Text fehlte komplett
        auf Pi 1, siehe camera_stream.py._annotate)."""
        self.assertGreater(self.config.camera_stream.overlay_timeout_s, 2.0)


class FlangeCameraTest(unittest.TestCase):
    """Pi 2 (RealSense): von der Umstellung unberuehrt."""

    def test_keeps_its_settings(self):
        config = config_for("cam_flange", "realsense")
        self.assertEqual(config.apriltag.resolution, (640, 480))
        self.assertIsNone(config.camera_stream.preview_resolution)
        self.assertIsNone(config.camera_stream.buffer_count)
        self.assertFalse(config.apriltag.allow_resolution_mismatch)

    def test_keeps_the_default_overlay_timeout(self):
        """Erkennungsaufloesung hat sich fuer cam_flange nicht geaendert
        (640x480 war schon immer der volle Frame) -- der Watchdog soll seinen
        urspruenglichen, knappen Wert behalten."""
        config = config_for("cam_flange", "realsense")
        self.assertEqual(config.camera_stream.overlay_timeout_s, 2.0)


class StreamDisableSwitchTest(unittest.TestCase):
    """`VISION_DISABLE_STREAM=1` -- Diagnose-Schalter fuer den A/B-Test:
    Job-Timeout auch ganz ohne Livestream/Overlay?

    Regression (live auf pi1 gefunden): eine fruehere Fassung setzte dafuer
    `config.camera_stream` komplett auf `None` -- das faellt in
    `detection/__init__.py` (`config.camera_stream or CameraStreamConfig()`)
    auf den blanken Default zurueck und aendert damit lautlos auch die
    Job-Kamera-Aufloesung (2028x1520 -> 1280x720, "Seitenverhaeltnis
    aendert sich"). `camera_stream` bleibt deshalb IMMER voll konfiguriert;
    nur `stream_enabled` schaltet um."""

    def test_disables_only_the_stream_nodes_not_the_resolution(self):
        with mock.patch.dict(
            os.environ,
            {
                "VISION_FRAME_ID": "cam_ceiling",
                "VISION_CAMERA_BACKEND": "picamera2",
                "VISION_DISABLE_STREAM": "1",
            },
        ):
            config = vision_config()
        self.assertIsNotNone(config.camera_stream)
        self.assertFalse(config.camera_stream.stream_enabled)
        self.assertEqual(config.camera_stream.resolution, (4056, 3040))

    def test_stays_enabled_without_the_switch(self):
        stream = config_for("cam_ceiling", "picamera2").camera_stream
        self.assertIsNotNone(stream)
        self.assertTrue(stream.stream_enabled)


if __name__ == "__main__":
    unittest.main()
