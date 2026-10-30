"""Was jeder Pi an Kamera- und Erkennungseinstellungen bekommt.

Die Identitaet kommt hier ueber `VISION_FRAME_ID`/`VISION_CAMERA_BACKEND`
statt ueber den Hostnamen -- dieselben Variablen, mit denen man einen Pi auch
von Hand umstellen kann.
"""

import os
import unittest
from unittest import mock

from vision_server import server
from vision_server.server import merged_json_object_env, vision_config


def config_for(frame_id: str, backend: str, **extra: str):
    with mock.patch.dict(
        os.environ, {"VISION_FRAME_ID": frame_id, "VISION_CAMERA_BACKEND": backend, **extra}
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


class QuadDecimateSwitchTest(unittest.TestCase):
    """`VISION_APRILTAG_QUAD_DECIMATE` -- Tuning-Hebel, noch nicht auf
    echten Distanzen validiert, deshalb Default 0.0 (unveraendert)."""

    def test_defaults_to_unchanged_behaviour(self):
        self.assertEqual(config_for("cam_ceiling", "picamera2").apriltag.apriltag_quad_decimate, 0.0)

    def test_can_be_set_via_env_var(self):
        with mock.patch.dict(
            os.environ,
            {
                "VISION_FRAME_ID": "cam_ceiling",
                "VISION_CAMERA_BACKEND": "picamera2",
                "VISION_APRILTAG_QUAD_DECIMATE": "2.0",
            },
        ):
            config = vision_config()
        self.assertEqual(config.apriltag.apriltag_quad_decimate, 2.0)


class SubpixelRefinementSwitchTest(unittest.TestCase):
    """`VISION_SUBPIXEL_CORNER_REFINEMENT` -- Default aus (spart ~46% CPU
    im Job-Thread, siehe profiles.py). Env-Var bleibt als Schalter fuer
    Layer 2/Kalibrierung."""

    def test_defaults_to_disabled(self):
        self.assertFalse(config_for("cam_ceiling", "picamera2").apriltag.subpixel_corner_refinement)

    def test_can_be_enabled_via_env_var(self):
        with mock.patch.dict(
            os.environ,
            {
                "VISION_FRAME_ID": "cam_ceiling",
                "VISION_CAMERA_BACKEND": "picamera2",
                "VISION_SUBPIXEL_CORNER_REFINEMENT": "1",
            },
        ):
            config = vision_config()
        self.assertTrue(config.apriltag.subpixel_corner_refinement)


class MergedJsonObjectEnvTest(unittest.TestCase):
    """Preset plus JSON-Objekt aus der Umgebungsvariable."""

    def merged(self, value, preset=None):
        env = {} if value is None else {"VISION_TEST_JSON": value}
        with mock.patch.dict(os.environ, env):
            if value is None:
                os.environ.pop("VISION_TEST_JSON", None)
            return merged_json_object_env("VISION_TEST_JSON", preset)

    def test_without_the_variable_the_preset_is_returned_as_a_copy(self):
        preset = {"a": 1}

        merged = self.merged(None, preset)

        self.assertEqual(merged, {"a": 1})
        self.assertIsNot(merged, preset)

    def test_blank_variable_counts_as_unset(self):
        self.assertEqual(self.merged("  ", {"a": 1}), {"a": 1})

    def test_env_wins_per_key_and_adds_new_keys(self):
        self.assertEqual(self.merged('{"a": 2, "c": 3}', {"a": 1, "b": 1}), {"a": 2, "b": 1, "c": 3})

    def test_null_removes_a_preset_entry(self):
        self.assertEqual(self.merged('{"a": null}', {"a": 1, "b": 1}), {"b": 1})

    def test_invalid_json_raises_value_error_naming_the_variable(self):
        with self.assertRaises(ValueError) as caught:
            self.merged("{kaputt", {})
        self.assertIn("VISION_TEST_JSON", str(caught.exception))

    def test_non_object_raises_value_error(self):
        for raw in ("[1, 2]", "0.8", '"text"', "null"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.merged(raw, {})


class DetectorParametersEnvTest(unittest.TestCase):
    """`VISION_APRILTAG_DETECTOR_PARAMS` und das (leere) Preset."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VISION_APRILTAG_DETECTOR_PARAMS", None)

    def test_ceiling_preset_uses_finer_sampling_grid(self):
        self.assertEqual(
            config_for("cam_ceiling", "picamera2").apriltag.detector_parameters,
            {"perspectiveRemovePixelPerCell": 8},
        )

    def test_flange_preset_is_empty(self):
        self.assertEqual(config_for("cam_flange", "realsense").apriltag.detector_parameters, {})

    def test_env_sets_parameters(self):
        config = config_for(
            "cam_ceiling", "picamera2", VISION_APRILTAG_DETECTOR_PARAMS='{"errorCorrectionRate": 0.8}'
        )
        # Je Schluessel ueber das Preset gemischt.
        self.assertEqual(
            config.apriltag.detector_parameters,
            {"perspectiveRemovePixelPerCell": 8, "errorCorrectionRate": 0.8},
        )

    def test_env_overrides_and_removes_preset_entries(self):
        presets = {
            "cam_ceiling": {
                **server.PI_APRILTAG_PRESETS["cam_ceiling"],
                "detector_parameters": {"errorCorrectionRate": 1.0, "perspectiveRemovePixelPerCell": 8},
            }
        }
        with mock.patch.object(server, "PI_APRILTAG_PRESETS", presets):
            config = config_for(
                "cam_ceiling",
                "picamera2",
                VISION_APRILTAG_DETECTOR_PARAMS='{"errorCorrectionRate": 0.8, "perspectiveRemovePixelPerCell": null}',
            )
        self.assertEqual(config.apriltag.detector_parameters, {"errorCorrectionRate": 0.8})
        # Das Preset selbst bleibt unberuehrt.
        self.assertEqual(
            presets["cam_ceiling"]["detector_parameters"],
            {"errorCorrectionRate": 1.0, "perspectiveRemovePixelPerCell": 8},
        )

    def test_invalid_json_fails_at_start(self):
        with self.assertRaises(ValueError):
            config_for("cam_ceiling", "picamera2", VISION_APRILTAG_DETECTOR_PARAMS="{nope")


class CameraControlsEnvTest(unittest.TestCase):
    """`VISION_CAMERA_CONTROLS` und das (leere) Preset."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("VISION_CAMERA_CONTROLS", None)

    def test_ceiling_preset_has_measured_controls(self):
        config = config_for("cam_ceiling", "picamera2")
        self.assertEqual(
            config.camera_stream.camera_controls,
            {"ExposureValue": -0.5, "Sharpness": 1.5, "Contrast": 1.15, "NoiseReductionMode": "Fast"},
        )
        # Die uebrigen Stream-Preset-Werte kommen weiter an.
        self.assertEqual(config.camera_stream.buffer_count, 2)

    def test_env_sets_controls(self):
        config = config_for(
            "cam_ceiling",
            "picamera2",
            VISION_CAMERA_CONTROLS='{"ExposureValue": -0.5, "NoiseReductionMode": "Fast"}',
        )
        # Je Schluessel ueber das Preset gemischt.
        self.assertEqual(
            config.camera_stream.camera_controls,
            {"ExposureValue": -0.5, "Sharpness": 1.5, "Contrast": 1.15, "NoiseReductionMode": "Fast"},
        )

    def test_env_overrides_and_removes_preset_entries(self):
        presets = {
            "cam_ceiling": {
                **server.PI_CAMERA_STREAM_PRESETS["cam_ceiling"],
                "camera_controls": {"Sharpness": 2.0, "Contrast": 1.2},
            }
        }
        with mock.patch.object(server, "PI_CAMERA_STREAM_PRESETS", presets):
            config = config_for(
                "cam_ceiling",
                "picamera2",
                VISION_CAMERA_CONTROLS='{"Sharpness": 1.5, "Contrast": null}',
            )
        self.assertEqual(config.camera_stream.camera_controls, {"Sharpness": 1.5})

    def test_realsense_keeps_the_controls_for_the_warning(self):
        config = config_for("cam_flange", "realsense", VISION_CAMERA_CONTROLS='{"Sharpness": 1.5}')
        self.assertEqual(config.camera_stream.camera_controls, {"Sharpness": 1.5})

    def test_invalid_json_fails_at_start(self):
        with self.assertRaises(ValueError):
            config_for("cam_ceiling", "picamera2", VISION_CAMERA_CONTROLS="[1]")


class TagFamilyDefaultTest(unittest.TestCase):
    def test_profil_und_tagloc_haben_dieselbe_standardfamilie(self):
        from tagloc.tagmap import DEFAULT_TAG_FAMILY
        from vision_server.profiles import AprilTagProfileConfig

        self.assertEqual(AprilTagProfileConfig().tag_family, DEFAULT_TAG_FAMILY)


if __name__ == "__main__":
    unittest.main()
