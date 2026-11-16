"""Tests that the livestream camera is found by its attribute, not by profile name.

Renaming the profile used to disable the livestream silently, with only a log line.
"""

import unittest
from types import SimpleNamespace

from vision_server.asset_model import VisionAssetNodes
from vision_server.camera import SharedCamera
from vision_server.detection.base import Detection, DetectionRequest, DetectionSource
from vision_server.nodeset_ids import DeviceHealth
from vision_server.profiles import CameraStreamConfig
from vision_server.runner import _build_annotator, _camera_owner, _start_camera_health


class FakeCamera:
    """Stands in for a source that holds a camera."""


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
        """A profile with any name is still found."""
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

    def test_the_stream_detector_uses_the_detector_parameters_of_the_job(self):
        from unittest.mock import patch

        from vision_server.profiles import AprilTagProfileConfig

        source = CameraSource()
        source._detector = object()
        source._calibration = object()
        source._config = AprilTagProfileConfig(
            detector_parameters={"errorCorrectionRate": 0.8, "adaptiveThreshWinSizeMax": 33}
        )

        with patch("tagloc.detector.build_detector") as build_detector, patch(
            "vision_server.stream_overlay.AprilTagStreamAnnotator"
        ):
            _build_annotator(source)

        keywords = build_detector.call_args.kwargs
        self.assertEqual(
            keywords["detector_parameters"],
            {"errorCorrectionRate": 0.8, "adaptiveThreshWinSizeMax": 33},
        )
        self.assertFalse(keywords["subpixel_corner_refinement"])

    def test_the_stream_detector_drops_corner_refinement_from_the_parameters(self):
        """Subpixel refinement stays off in the stream (CPU contention with the job),
        even if `cornerRefinementMethod` is set explicitly in the parameters."""
        from unittest.mock import patch

        from vision_server.profiles import AprilTagProfileConfig

        source = CameraSource()
        source._detector = object()
        source._calibration = object()
        source._config = AprilTagProfileConfig(
            detector_parameters={"errorCorrectionRate": 0.8, "cornerRefinementMethod": 3}
        )

        with patch("tagloc.detector.build_detector") as build_detector, patch(
            "vision_server.stream_overlay.AprilTagStreamAnnotator"
        ):
            _build_annotator(source)

        self.assertEqual(
            build_detector.call_args.kwargs["detector_parameters"],
            {"errorCorrectionRate": 0.8},
        )
        # The job's config mapping stays unchanged.
        self.assertIn("cornerRefinementMethod", source._config.detector_parameters)

    def test_the_stream_gets_its_own_detector_without_subpixel_refinement(self):
        """Regression: job and stream once shared one detector instance, and both
        threads running `detect()` with subpixel refinement caused job timeouts.
        The stream must always get its own instance, regardless of the config value."""
        from unittest.mock import patch

        from vision_server.profiles import AprilTagProfileConfig

        source = CameraSource()
        source._detector = object()  # job detector, must stay untouched
        source._calibration = object()
        source._config = AprilTagProfileConfig(subpixel_corner_refinement=True)

        stream_detector = object()
        with patch(
            "tagloc.detector.build_detector", return_value=stream_detector
        ) as build_detector, patch("vision_server.stream_overlay.AprilTagStreamAnnotator") as annotator_cls:
            _build_annotator(source)

        build_detector.assert_called_once_with(
            source._config.tag_family,
            source._config.detector_backend,
            subpixel_corner_refinement=False,
            detector_parameters={},
        )
        self.assertIs(annotator_cls.call_args.kwargs["detector"], stream_detector)
        self.assertIsNot(annotator_cls.call_args.kwargs["detector"], source._detector)


class FakeHealthNode:
    def __init__(self) -> None:
        self.written: list[int] = []
        self.nodeid = type("NodeId", (), {"to_string": lambda self_: "ns=6;s=Health"})()

    async def write_value(self, value) -> None:
        self.written.append(value.Value)


class FakeSpace:
    """Only what `_start_camera_health` reads from `VisionAddressSpace`."""

    def __init__(self, camera_stream=None) -> None:
        self.latest_camera_frame = None
        self.config = SimpleNamespace(camera_stream=camera_stream)


class StartCameraHealthTest(unittest.IsolatedAsyncioTestCase):
    """The state node depends on the camera, not on the livestream."""

    async def test_is_not_tied_to_the_livestream(self):
        """Without a configured livestream (`latest_camera_frame is None`) the
        state must still be published."""
        source = CameraSource(camera=SharedCamera(CameraStreamConfig()))
        assets = VisionAssetNodes(root=None, device_health=(FakeHealthNode(),))

        publisher = await _start_camera_health(
            FakeSpace(), assets, {"x": source}, {"x": True}
        )

        self.assertIsNotNone(publisher)
        await publisher.stop()

    async def test_does_nothing_without_part_two(self):
        self.assertIsNone(
            await _start_camera_health(FakeSpace(), None, {}, {})
        )

    async def test_does_nothing_without_health_nodes(self):
        assets = VisionAssetNodes(root=None)
        self.assertIsNone(
            await _start_camera_health(FakeSpace(), assets, {}, {})
        )

    async def test_reports_failure_when_no_camera_is_open(self):
        """Node present, camera absent: FAILURE is the honest answer."""
        node = FakeHealthNode()
        assets = VisionAssetNodes(root=None, device_health=(node,))

        publisher = await _start_camera_health(
            FakeSpace(), assets, {"a": CameralessSource()}, {"a": True}
        )

        self.assertIsNone(publisher)
        self.assertEqual([int(DeviceHealth.FAILURE)], node.written)


if __name__ == "__main__":
    unittest.main()
