"""Who feeds the livestream depends on an attribute, not a name.

`runner._start_camera_stream` used to get the camera via
`sources.get("image_recognition")`. Renaming or removing that profile would
silently kill the livestream -- just a log line. These tests pin down that
selection goes by the presence of an opened camera.
"""

import unittest

from vision_server.detection.base import Detection, DetectionRequest, DetectionSource
from vision_server.runner import _build_annotator, _camera_owner, _camera_source


class FakeCamera:
    """Stands in for a source holding a camera."""


class OpenableCamera(FakeCamera):
    """A camera the runner may open on its own."""

    def __init__(self, fails: bool = False) -> None:
        self.opened = False
        self._fails = fails

    async def open(self) -> None:
        if self._fails:
            raise RuntimeError("Kamera belegt")
        self.opened = True


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


class CameraSourceTest(unittest.IsolatedAsyncioTestCase):
    """Without a calibration the source stays closed -- the camera must not.

    That is exactly the situation in which the operator wants the livestream
    and the remote calibration: a Pi whose calibration file is missing.
    """

    async def test_prefers_an_opened_source(self):
        sources = {"a": CameraSource(), "b": CameraSource(OpenableCamera())}
        source = await _camera_source(sources, {"a": True, "b": False})
        self.assertIs(source, sources["a"])
        self.assertFalse(sources["b"].camera.opened)

    async def test_opens_the_camera_of_a_source_that_failed_to_open(self):
        sources = {"b": CameraSource(OpenableCamera())}
        source = await _camera_source(sources, {"b": False})
        self.assertIs(source, sources["b"])
        self.assertTrue(source.camera.opened)

    async def test_no_stream_when_the_camera_itself_refuses(self):
        sources = {"b": CameraSource(OpenableCamera(fails=True))}
        self.assertIsNone(await _camera_source(sources, {"b": False}))

    async def test_no_stream_without_any_camera(self):
        self.assertIsNone(await _camera_source({"a": CameralessSource()}, {"a": False}))


class BuildAnnotatorTest(unittest.TestCase):
    def test_returns_none_for_a_source_without_detector_or_calibration(self):
        self.assertIsNone(_build_annotator(CameraSource()))

    def test_returns_none_instead_of_raising_when_pieces_are_missing(self):
        source = CameraSource()
        source._detector = object()
        self.assertIsNone(_build_annotator(source))


if __name__ == "__main__":
    unittest.main()
