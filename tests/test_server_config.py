"""What each Pi receives as camera and detection settings.

The identity comes here via `VISION_FRAME_ID`/`VISION_CAMERA_BACKEND` instead
of the hostname: the same variables that can be used to set up a Pi by hand.
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
    """Pi 1: HQ camera (IMX477) at full sensor resolution."""

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
        """Otherwise `scale_to_resolution` rejects rescaling the calibration."""
        full = self.config.camera_stream.resolution
        preview = self.config.camera_stream.preview_resolution
        self.assertLess(abs(preview[0] / full[0] - preview[1] / full[1]), 1e-3)

    def test_asks_only_for_what_the_sensor_delivers_at_full_resolution(self):
        self.assertEqual(self.config.camera_stream.capture_fps, 10.0)
        self.assertEqual(self.config.camera_stream.buffer_count, 2)

    def test_accepts_the_old_binned_calibration_until_recalibrated(self):
        self.assertTrue(self.config.apriltag.allow_resolution_mismatch)

    def test_overlay_gets_more_time_for_the_full_12_megapixels(self):
        """The global default (2.0 s, profiles.py) was not enough for detection and
        drawing at 4056x3040; the overlay then fell back to the unmarked raw image
        on every tick (see camera_stream.py._annotate)."""
        self.assertGreater(self.config.camera_stream.overlay_timeout_s, 2.0)


class FlangeCameraTest(unittest.TestCase):
    """Pi 2 (RealSense): unaffected by the change."""

    def test_keeps_its_settings(self):
        config = config_for("cam_flange", "realsense")
        # Full resolution of the D415 colour sensor.
        self.assertEqual(config.apriltag.resolution, (1920, 1080))
        self.assertIsNone(config.camera_stream.preview_resolution)
        self.assertIsNone(config.camera_stream.buffer_count)
        self.assertFalse(config.apriltag.allow_resolution_mismatch)

    def test_detection_resolution_matches_the_realsense_frames(self):
        """The frames arrive in `realsense_resolution`; detection must expect the
        same resolution, otherwise the first job fails only at runtime
        ("calibration applies to ...")."""
        config = config_for("cam_flange", "realsense")
        self.assertEqual(config.apriltag.resolution, config.camera_stream.realsense_resolution)
        self.assertEqual(config.camera_stream.realsense_resolution, (1920, 1080))
        # Over USB 2 the D415 offers 1920x1080 only at 8 Hz.
        self.assertEqual(config.camera_stream.realsense_fps, 8)

    def test_keeps_the_default_overlay_timeout(self):
        """cam_flange takes the full frame (1920x1080) with no second preview
        stream; the watchdog keeps its original, tight value."""
        config = config_for("cam_flange", "realsense")
        self.assertEqual(config.camera_stream.overlay_timeout_s, 2.0)


class StreamDisableSwitchTest(unittest.TestCase):
    """`VISION_DISABLE_STREAM=1`: diagnostic switch for the A/B test, i.e. job
    timeout even without livestream and overlay.

    Regression (found live on pi1): an earlier version set `config.camera_stream`
    to `None` for this. That falls back in `detection/__init__.py`
    (`config.camera_stream or CameraStreamConfig()`) to the bare default and
    silently also changes the job camera resolution (2028x1520 -> 1280x720,
    aspect ratio changes). So `camera_stream` always stays fully configured;
    only `stream_enabled` toggles."""

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
    """`VISION_APRILTAG_QUAD_DECIMATE`: tuning lever, not yet validated at real
    distances, hence default 0.0 (unchanged)."""

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
    """`VISION_SUBPIXEL_CORNER_REFINEMENT`: the ceiling camera runs with subpix
    on (integer corners jittered, see server.py). The expensive `apriltag`
    method stays off; the env var switches per Pi."""

    def test_ceiling_defaults_to_subpix(self):
        apriltag = config_for("cam_ceiling", "picamera2").apriltag
        self.assertTrue(apriltag.subpixel_corner_refinement)
        self.assertEqual(apriltag.corner_refinement_method, "subpix")

    def test_can_be_disabled_via_env_var(self):
        with mock.patch.dict(
            os.environ,
            {
                "VISION_FRAME_ID": "cam_ceiling",
                "VISION_CAMERA_BACKEND": "picamera2",
                "VISION_SUBPIXEL_CORNER_REFINEMENT": "0",
            },
        ):
            config = vision_config()
        self.assertFalse(config.apriltag.subpixel_corner_refinement)


class MergedJsonObjectEnvTest(unittest.TestCase):
    """Preset plus JSON object from the environment variable."""

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
    """`VISION_APRILTAG_DETECTOR_PARAMS` and the (empty) preset."""

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
        # Mixed per key over the preset.
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
        # The preset itself stays untouched.
        self.assertEqual(
            presets["cam_ceiling"]["detector_parameters"],
            {"errorCorrectionRate": 1.0, "perspectiveRemovePixelPerCell": 8},
        )

    def test_invalid_json_fails_at_start(self):
        with self.assertRaises(ValueError):
            config_for("cam_ceiling", "picamera2", VISION_APRILTAG_DETECTOR_PARAMS="{nope")


class CameraControlsEnvTest(unittest.TestCase):
    """`VISION_CAMERA_CONTROLS` and the (empty) preset."""

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
        # The remaining stream preset values still come through.
        self.assertEqual(config.camera_stream.buffer_count, 2)

    def test_env_sets_controls(self):
        config = config_for(
            "cam_ceiling",
            "picamera2",
            VISION_CAMERA_CONTROLS='{"ExposureValue": -0.5, "NoiseReductionMode": "Fast"}',
        )
        # Mixed per key over the preset.
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
