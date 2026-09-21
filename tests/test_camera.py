"""Tests fuer SharedCamera: nur der SDK-unabhaengige Teil (Backend-Auswahl).

Die drei Backends selbst (Picamera2, RealSense, OpenCV) brauchen echte
Hardware bzw. SDKs, die hier nicht installiert sind -- deferred-import per
Konvention (siehe camera.py). Verifiziert wird deshalb nur das Dispatching,
nicht das Ansprechen der eigentlichen Kamera.
"""

import unittest

from vision_server.camera import SharedCamera
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


if __name__ == "__main__":
    unittest.main()
