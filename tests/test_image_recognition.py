"""Tests fuer die QR-Erkennung ueber die geteilte Kamera.

Nutzt Fake-Kamera und Fake-Detector statt Picamera2/cv2 zu oeffnen -- die
Quelle wird nie `open()`et, `_detector` wird direkt gesetzt.
"""

import unittest
from dataclasses import replace

from vision_server.camera import CameraFrame
from vision_server.detection.base import DetectionRequest
from vision_server.detection.image_recognition import (
    NO_QR_CODE_MESSAGE,
    ImageRecognitionDetectionSource,
)
from vision_server.profiles import CameraStreamConfig

FAST_CONFIG = CameraStreamConfig(qr_scan_duration_s=0.15)


class FakeCamera:
    """Liefert `latest_frame`, ohne eine echte Kamera zu oeffnen."""

    def __init__(self) -> None:
        self.latest_frame: CameraFrame | None = None
        self.opened = 0
        self.closed = 0

    async def open(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1

    def push_frame(self, image, timestamp: float) -> None:
        self.latest_frame = CameraFrame(image=image, timestamp=timestamp)


class FakeDetector:
    """`detectAndDecode`-Stub: liest den QR-Inhalt direkt aus dem "Bild"."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def detectAndDecode(self, image):  # noqa: N802 (cv2-Namenskonvention)
        self.calls.append(image)
        return (image or "", None, None)


def make_source(config: CameraStreamConfig = FAST_CONFIG):
    camera = FakeCamera()
    detector = FakeDetector()
    source = ImageRecognitionDetectionSource(config, camera=camera)
    source._detector = detector
    return source, camera, detector


class ScanQrCodeTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_decoded_text_once_found(self):
        source, camera, _ = make_source()
        camera.push_frame("MODUL-42", timestamp=1.0)
        message = await source._scan_qr_code()
        self.assertEqual(message, "MODUL-42")
        await source.close()

    async def test_returns_no_qr_code_message_when_nothing_found(self):
        source, camera, _ = make_source()
        camera.push_frame("", timestamp=1.0)  # Frame vorhanden, aber kein QR-Inhalt
        message = await source._scan_qr_code()
        self.assertEqual(message, NO_QR_CODE_MESSAGE)
        await source.close()

    async def test_returns_no_qr_code_message_without_any_frame(self):
        source, _camera, detector = make_source()
        message = await source._scan_qr_code()
        self.assertEqual(message, NO_QR_CODE_MESSAGE)
        self.assertEqual(detector.calls, [])
        await source.close()

    async def test_does_not_redecode_the_same_frame_twice(self):
        """Ein unveraenderter Zeitstempel darf nicht erneut teuer decodiert werden."""
        source, camera, detector = make_source(replace(FAST_CONFIG, qr_scan_duration_s=0.08))
        camera.push_frame("", timestamp=5.0)
        await source._scan_qr_code()
        self.assertEqual(len(detector.calls), 1)
        await source.close()

    async def test_close_delegates_to_the_shared_camera(self):
        source, camera, _ = make_source()
        await source.close()
        self.assertEqual(camera.closed, 1)


class AcquireAndDetectTest(unittest.IsolatedAsyncioTestCase):
    async def test_wraps_the_message_into_a_detection(self):
        source, camera, _ = make_source()
        camera.push_frame("MODUL-7", timestamp=2.0)
        [detection] = await source.acquire_and_detect(
            DetectionRequest(job_id="job-1", recipe_id="image-recognition")
        )
        self.assertEqual(detection.attributes["message"], "MODUL-7")
        self.assertEqual(detection.attributes["recipeId"], "image-recognition")
        self.assertEqual(detection.module_id, "IMAGE_RECOGNITION")
        await source.close()

    async def test_stays_non_simulated_and_in_world_frame(self):
        source, _camera, _detector = make_source()
        self.assertFalse(source.is_simulated)
        self.assertEqual(source.frame_id, "world")
        await source.close()


if __name__ == "__main__":
    unittest.main()
