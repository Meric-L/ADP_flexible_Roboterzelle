"""Tests fuer SharedCamera: nur der SDK-unabhaengige Teil (Backend-Auswahl).

Die drei Backends selbst (Picamera2, RealSense, OpenCV) brauchen echte
Hardware bzw. SDKs, die hier nicht installiert sind -- deferred-import per
Konvention (siehe camera.py). Verifiziert wird deshalb nur das Dispatching,
nicht das Ansprechen der eigentlichen Kamera.
"""

import types
import unittest

try:
    import cv2
    import numpy as np
except Exception:  # OpenCV fehlt in dieser Umgebung
    cv2 = None

from vision_server.camera import (
    PICAMERA2_MAIN_FORMAT,
    PICAMERA2_PREVIEW_FORMAT,
    SharedCamera,
    format_camera_metadata,
    picamera2_video_configuration,
    resolve_camera_controls,
    validate_camera_controls,
    yuv420_to_bgr,
)
from vision_server.profiles import CameraStreamConfig


class UnknownBackendTest(unittest.TestCase):
    def test_open_camera_rejects_an_unknown_backend(self):
        camera = SharedCamera(CameraStreamConfig(backend="does-not-exist"))
        with self.assertRaises(ValueError) as caught:
            camera._open_camera()
        self.assertIn("does-not-exist", str(caught.exception))


class FakePipeline:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeCapture:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class CloseDispatchTest(unittest.TestCase):
    def test_closes_a_realsense_pipeline_via_stop(self):
        pipeline = FakePipeline()
        camera = SharedCamera(CameraStreamConfig(backend="realsense"))

        camera._close_camera(pipeline)

        self.assertTrue(pipeline.stopped)

    def test_closes_an_opencv_capture_via_release(self):
        capture = FakeCapture()
        camera = SharedCamera(CameraStreamConfig(backend="opencv"))

        camera._close_camera(capture)

        self.assertTrue(capture.released)


class Picamera2ConfigurationTest(unittest.TestCase):
    """Was Picamera2 zum Konfigurieren bekommt -- ohne Kamera pruefbar."""

    def test_main_stream_only_by_default(self):
        arguments = picamera2_video_configuration(CameraStreamConfig(resolution=(1280, 720)))
        self.assertEqual(
            arguments, {"main": {"size": (1280, 720), "format": PICAMERA2_MAIN_FORMAT}}
        )

    def test_full_resolution_with_a_small_preview_stream(self):
        """Das Preset der Deckenkamera: 12 MP fuer Jobs, 960x720 fuer den Stream."""
        arguments = picamera2_video_configuration(
            CameraStreamConfig(
                resolution=(4056, 3040), preview_resolution=(960, 720), buffer_count=2
            )
        )
        self.assertEqual(arguments["main"]["size"], (4056, 3040))
        self.assertEqual(
            arguments["lores"], {"size": (960, 720), "format": PICAMERA2_PREVIEW_FORMAT}
        )
        self.assertEqual(arguments["buffer_count"], 2)

    def test_rejects_a_preview_larger_than_the_main_stream(self):
        with self.assertRaises(ValueError):
            picamera2_video_configuration(
                CameraStreamConfig(resolution=(640, 480), preview_resolution=(960, 720))
            )


    def test_no_controls_key_without_camera_controls(self):
        arguments = picamera2_video_configuration(CameraStreamConfig())
        self.assertNotIn("controls", arguments)

    def test_camera_controls_end_up_in_the_configuration(self):
        arguments = picamera2_video_configuration(
            CameraStreamConfig(
                camera_controls={"ExposureValue": -0.5, "NoiseReductionMode": "Fast"}
            )
        )
        self.assertEqual(
            arguments["controls"], {"ExposureValue": -0.5, "NoiseReductionMode": "Fast"}
        )

    def test_the_controls_are_a_copy_of_the_config(self):
        config = CameraStreamConfig(camera_controls={"Sharpness": 1.5})
        picamera2_video_configuration(config)["controls"]["Sharpness"] = 9.0
        self.assertEqual(config.camera_controls, {"Sharpness": 1.5})


class FakeNoiseReductionModeEnum:
    Off = 0
    Fast = 1
    HighQuality = 2


class FakeAwbModeEnum:
    Auto = 0
    Daylight = 5


FAKE_LIBCAMERA_CONTROLS = types.SimpleNamespace(
    draft=types.SimpleNamespace(NoiseReductionModeEnum=FakeNoiseReductionModeEnum),
    AwbModeEnum=FakeAwbModeEnum,
    AeMeteringModeEnum=types.SimpleNamespace(CentreWeighted=0),
    AeExposureModeEnum=types.SimpleNamespace(Normal=0),
    AeConstraintModeEnum=types.SimpleNamespace(Normal=0),
)


class ResolveCameraControlsTest(unittest.TestCase):
    """String-Enums -> libcamera-Werte, mit untergeschobenem Namensraum."""

    def test_enum_names_are_resolved(self):
        resolved = resolve_camera_controls(
            {"NoiseReductionMode": "HighQuality", "AwbMode": "Daylight"}, FAKE_LIBCAMERA_CONTROLS
        )
        self.assertEqual(resolved, {"NoiseReductionMode": 2, "AwbMode": 5})

    def test_numbers_and_other_controls_stay_unchanged(self):
        controls = {"NoiseReductionMode": 1, "ExposureTime": 20000, "AeEnable": False, "Sharpness": 1.5}
        self.assertEqual(resolve_camera_controls(controls, FAKE_LIBCAMERA_CONTROLS), controls)

    def test_does_not_modify_its_input(self):
        controls = {"NoiseReductionMode": "Off"}
        resolve_camera_controls(controls, FAKE_LIBCAMERA_CONTROLS)
        self.assertEqual(controls, {"NoiseReductionMode": "Off"})

    def test_unknown_enum_name_raises_with_the_valid_names(self):
        with self.assertRaises(ValueError) as caught:
            resolve_camera_controls({"NoiseReductionMode": "Turbo"}, FAKE_LIBCAMERA_CONTROLS)
        message = str(caught.exception)
        self.assertIn("Turbo", message)
        self.assertIn("HighQuality", message)


class ValidateCameraControlsTest(unittest.TestCase):
    AVAILABLE = {
        "ExposureTime": (60, 66666, 20000),
        "AnalogueGain": (1.0, 16.0, 1.0),
        "AeEnable": (False, True, True),
        "Sharpness": (0.0, 16.0, 1.0),
        "ScalerCrop": ((0, 0, 64, 64), (0, 0, 4056, 3040), (0, 0, 4056, 3040)),
    }

    def test_unknown_names_raise_and_list_the_cameras_names(self):
        with self.assertRaises(ValueError) as caught:
            validate_camera_controls({"Sharpnes": 1.5}, self.AVAILABLE)
        message = str(caught.exception)
        self.assertIn("Sharpnes", message)
        self.assertIn("Sharpness", message)

    def test_number_types_are_aligned_to_the_cameras_defaults(self):
        checked = validate_camera_controls(
            {"AnalogueGain": 2, "ExposureTime": 20000.0, "AeEnable": 0}, self.AVAILABLE
        )
        self.assertIsInstance(checked["AnalogueGain"], float)
        self.assertIsInstance(checked["ExposureTime"], int)
        self.assertIs(checked["AeEnable"], False)

    def test_json_lists_become_tuples(self):
        checked = validate_camera_controls({"ScalerCrop": [0, 0, 100, 100]}, self.AVAILABLE)
        self.assertEqual(checked["ScalerCrop"], (0, 0, 100, 100))


class CameraMetadataFormatTest(unittest.TestCase):
    def test_lists_known_keys_in_fixed_order_and_skips_missing_ones(self):
        text = format_camera_metadata(
            {"Lux": 120.5, "ExposureTime": 8000, "AnalogueGain": 1.0, "SensorTimestamp": 1}
        )
        self.assertEqual(text, "ExposureTime=8000, AnalogueGain=1.0, Lux=120.5")

    def test_empty_metadata_gives_an_empty_text(self):
        self.assertEqual(format_camera_metadata({}), "")


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class Yuv420ConversionTest(unittest.TestCase):
    def _picture(self, width=64, height=48):
        # Flaechige Farbfelder: YUV420 halbiert die Farbaufloesung, feines
        # Rauschen kaeme nicht verlustfrei zurueck.
        image = np.zeros((height, width, 3), dtype=np.uint8)
        image[:, : width // 2] = (200, 40, 40)
        image[:, width // 2 :] = (30, 180, 220)
        return image

    def test_round_trips_an_unpadded_buffer(self):
        image = self._picture()
        yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV_I420)

        result = yuv420_to_bgr(yuv, (64, 48))

        self.assertEqual(result.shape, (48, 64, 3))
        self.assertLess(int(np.abs(result.astype(int) - image.astype(int)).max()), 6)

    def test_crops_the_stride_padding(self):
        """Picamera2 richtet Zeilen aus; der Puffer ist dann breiter als das Bild."""
        width, height, stride = 64, 48, 96
        image = self._picture(width, height)
        yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV_I420)
        y = yuv[:height]
        u = yuv[height : height + height // 4].reshape(height // 2, width // 2)
        v = yuv[height + height // 4 :].reshape(height // 2, width // 2)
        pad = ((0, 0), (0, (stride - width)))
        half_pad = ((0, 0), (0, (stride - width) // 2))
        padded = np.vstack(
            [
                np.pad(y, pad),
                np.pad(u, half_pad).reshape(height // 4, stride),
                np.pad(v, half_pad).reshape(height // 4, stride),
            ]
        )

        result = yuv420_to_bgr(padded, (width, height))

        self.assertEqual(result.shape, (height, width, 3))
        self.assertLess(int(np.abs(result.astype(int) - image.astype(int)).max()), 6)


class FakeRequest:
    def __init__(self, arrays, fail=None) -> None:
        self._arrays = arrays
        self._fail = fail
        self.released = False

    def make_array(self, name):
        if name == self._fail:
            raise RuntimeError("Puffer kaputt")
        return self._arrays[name]

    def release(self) -> None:
        self.released = True


class FakePicamera2:
    def __init__(self, request) -> None:
        self.request = request

    def capture_request(self):
        return self.request

    def capture_array(self, name="main"):
        return f"array:{name}"


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ReadFramesTest(unittest.TestCase):
    def _camera(self, request):
        camera = SharedCamera(
            CameraStreamConfig(resolution=(64, 48), preview_resolution=(32, 24))
        )
        camera._camera = FakePicamera2(request)
        camera._preview_size = (32, 24)
        return camera

    def test_takes_both_images_from_one_request(self):
        small = cv2.cvtColor(np.full((24, 32, 3), 128, np.uint8), cv2.COLOR_BGR2YUV_I420)
        request = FakeRequest({"main": "voll", "lores": small})
        camera = self._camera(request)

        image, preview = camera._read_frames()

        self.assertEqual(image, "voll")
        self.assertEqual(preview.shape, (24, 32, 3))
        self.assertTrue(request.released)

    def test_returns_the_request_even_when_reading_fails(self):
        """Ein nicht zurueckgegebener Request blockiert die Kamera -- bei 2 Puffern sofort."""
        request = FakeRequest({"main": "voll"}, fail="lores")
        camera = self._camera(request)

        with self.assertRaises(RuntimeError):
            camera._read_frames()
        self.assertTrue(request.released)

    def test_without_preview_reads_only_the_main_stream(self):
        camera = SharedCamera(CameraStreamConfig())
        camera._camera = FakePicamera2(None)

        image, preview = camera._read_frames()

        self.assertEqual(image, "array:main")
        self.assertIsNone(preview)


if __name__ == "__main__":
    unittest.main()
