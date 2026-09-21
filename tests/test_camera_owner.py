"""Who feeds the livestream depends on an attribute, not a name.

`runner._start_camera_stream` used to get the camera via
`sources.get("image_recognition")`. Renaming or removing that profile would
silently kill the livestream -- just a log line. These tests pin down that
selection goes by the presence of an opened camera.
"""

import unittest

from vision_server.detection.base import Detection, DetectionRequest, DetectionSource
from vision_server.runner import _build_annotator, _camera_owner


class FakeCamera:
    """Stands in for a source holding a camera."""


class CameralessSource(DetectionSource):
    profile_id = "ohne_kamera"

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        return []


class CameraSource(DetectionSource):
    profile_id = "mit_kamera"

    def __init__(self, camera=None) -> None:
        self.camera = camera if camera is not None else FakeCamera()

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        return []


class CameraOwnerTest(unittest.TestCase):
    def test_finds_the_opened_source_that_holds_a_camera(self):
        sources = {"a": CameralessSource(), "b": CameraSource()}
        opened = {"a": True, "b": True}
        self.assertIs(_camera_owner(sources, opened), sources["b"])

    def test_ignores_a_source_that_failed_to_open(self):
        sources = {"b": CameraSource()}
        self.assertIsNone(_camera_owner(sources, {"b": False}))

    def test_returns_none_when_no_source_holds_a_camera(self):
        sources = {"a": CameralessSource()}
        self.assertIsNone(_camera_owner(sources, {"a": True}))

    def test_does_not_depend_on_the_profile_name(self):
        """The actual point: an arbitrarily named profile is still found."""
        source = CameraSource()
        source.profile_id = "voellig_anderer_name"
        self.assertIs(_camera_owner({"x": source}, {"x": True}), source)

    def test_ignores_a_camera_attribute_that_is_none(self):
        source = CameraSource(camera=None)
        source.camera = None
        self.assertIsNone(_camera_owner({"x": source}, {"x": True}))


class BuildAnnotatorTest(unittest.TestCase):
    def test_returns_none_for_a_source_without_detector_or_calibration(self):
        self.assertIsNone(_build_annotator(CameraSource()))

    def test_returns_none_instead_of_raising_when_pieces_are_missing(self):
        source = CameraSource()
        source._detector = object()
        self.assertIsNone(_build_annotator(source))


if __name__ == "__main__":
    unittest.main()
