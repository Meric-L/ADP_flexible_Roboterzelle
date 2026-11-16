"""Tests for the livestream publisher.

`encode_frame` is replaced by a fake, so the tests run without `cv2` and without
real image data. `max_stream_width=None` turns off downscaling before encoding;
that would need a real array with `.shape` instead of the placeholder strings
used here, and is tested separately in `ResizeForStreamTest` below.
"""

import asyncio
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests._support import RecordingNode, run_briefly
from tagloc.modes import DEFAULT_OVERLAY_MODE, OVERLAY_MODES, normalise_mode
from vision_server.camera import CameraFrame
from vision_server.camera_stream import CameraStreamPublisher, _resize_for_stream
from vision_server.profiles import CameraStreamConfig

try:
    import cv2
    import numpy as np
except Exception:  # OpenCV not installed
    cv2 = None
    np = None

FAST_CONFIG = CameraStreamConfig(stream_fps=50.0, max_stream_width=None)


class FakeCamera:
    def __init__(self) -> None:
        self.latest_frame: CameraFrame | None = None


class FakeModeNode:
    """The writable node through which the frontend selects the overlay mode."""

    def __init__(self, value, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.reads = 0

    async def read_value(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("Knoten nicht lesbar")
        return self.value


class FakeAnnotator:
    """Record what it was called with, and mark the image recognisably."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def annotate(self, image, mode: str):
        self.calls.append((image, mode))
        return f"annotated:{image}:{mode}"


def fake_encode(image, quality: int) -> str:
    return f"encoded:{image}:{quality}"


def _fresh_frame(image: str) -> CameraFrame:
    """A frame from just now; older ones count as stale and are not sent out."""
    return CameraFrame(image=image, timestamp=asyncio.get_running_loop().time())


class PublishLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_writes_the_encoded_frame_to_the_node(self):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = RecordingNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await run_briefly(publisher, 0.1)

        self.assertGreaterEqual(len(node.written), 1)
        self.assertEqual(node.written[0], f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}")

    async def test_writes_nothing_before_the_first_frame_exists(self):
        camera = FakeCamera()
        node = RecordingNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await run_briefly(publisher, 0.06)

        self.assertEqual(node.written, [])

    async def test_stop_is_idempotent_and_cancels_the_loop(self):
        camera = FakeCamera()
        node = RecordingNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)
        publisher.start()
        await publisher.stop()
        await publisher.stop()

    async def test_an_encode_failure_does_not_kill_the_loop(self):
        """A single broken frame must not stop the stream permanently."""
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("bad")
        node = RecordingNode()

        calls = 0

        def flaky_encode(image, quality):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Encode kaputt")
            return "ok"

        config = replace(FAST_CONFIG, stream_fps=50.0)
        publisher = CameraStreamPublisher(camera, node, config, encode_frame=flaky_encode)

        # Generous window: the first `run_in_executor(None, ...)` call pays the
        # thread-pool start-up, which on a loaded machine can take the whole first
        # iteration alone.
        await run_briefly(publisher, 0.5)

        self.assertGreaterEqual(calls, 2)
        self.assertIn("ok", node.written)


class OverlayModeTest(unittest.IsolatedAsyncioTestCase):
    def _publisher(self, mode: str, *, annotator, mode_node=None):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = RecordingNode()
        publisher = CameraStreamPublisher(
            camera,
            node,
            replace(FAST_CONFIG, overlay_mode=mode),
            encode_frame=fake_encode,
            annotator=annotator,
            mode_node=mode_node,
        )
        return publisher, node

    async def test_encodes_the_raw_frame_in_mode_off(self):
        annotator = FakeAnnotator()
        publisher, node = self._publisher("off", annotator=annotator)

        await run_briefly(publisher, 0.1)

        self.assertEqual(annotator.calls, [])
        self.assertEqual(node.written[0], f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}")

    async def test_encodes_the_annotated_frame_in_mode_apriltag(self):
        annotator = FakeAnnotator()
        publisher, node = self._publisher("apriltag", annotator=annotator)

        await run_briefly(publisher, 0.1)

        self.assertGreaterEqual(len(annotator.calls), 1)
        self.assertEqual(annotator.calls[0], ("frame-1", "apriltag"))
        self.assertEqual(
            node.written[0], f"encoded:annotated:frame-1:apriltag:{FAST_CONFIG.jpeg_quality}"
        )

    async def test_follows_the_mode_node(self):
        annotator = FakeAnnotator()
        publisher, node = self._publisher(
            "apriltag", annotator=annotator, mode_node=FakeModeNode("  OFF ")
        )

        await run_briefly(publisher, 0.1)

        self.assertEqual(publisher.mode, "off")
        self.assertEqual(annotator.calls, [])
        self.assertEqual(node.written[0], f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}")

    async def test_keeps_the_last_known_mode_when_the_node_cannot_be_read(self):
        """An unreadable node must not stop the stream."""
        annotator = FakeAnnotator()
        mode_node = FakeModeNode(None, fail=True)
        publisher, node = self._publisher(
            "calibration", annotator=annotator, mode_node=mode_node
        )

        await run_briefly(publisher, 0.1)

        self.assertGreaterEqual(mode_node.reads, 1)
        self.assertEqual(publisher.mode, "calibration")
        self.assertEqual(annotator.calls[0], ("frame-1", "calibration"))
        self.assertGreaterEqual(len(node.written), 1)

    async def test_an_annotator_failure_does_not_kill_the_loop(self):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = RecordingNode()

        calls = 0

        class FlakyAnnotator:
            def annotate(self, image, mode):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("Overlay kaputt")
                return "markiert"

        publisher = CameraStreamPublisher(
            camera, node, FAST_CONFIG, encode_frame=fake_encode, annotator=FlakyAnnotator()
        )

        await run_briefly(publisher, 0.1)

        self.assertGreaterEqual(calls, 2)
        self.assertIn(f"encoded:markiert:{FAST_CONFIG.jpeg_quality}", node.written)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ResizeForStreamTest(unittest.TestCase):
    def test_leaves_a_narrower_image_untouched(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)

        result = _resize_for_stream(image, 960)

        self.assertIs(result, image)

    def test_downscales_a_wider_image_keeping_aspect_ratio(self):
        image = np.zeros((1520, 2028, 3), dtype=np.uint8)

        result = _resize_for_stream(image, 960)

        self.assertEqual(result.shape[1], 960)
        self.assertEqual(result.shape[0], round(1520 * 960 / 2028))


class StaleFrameTest(unittest.IsolatedAsyncioTestCase):
    """The publisher does not monitor; it publishes.

    It used to clear the node as soon as the frame was older than `stale_frame_s`.
    That was a second source of truth about whether the camera is alive, next to
    `DeviceHealth` per OPC 40100-2, and at odds with the companion spec. These tests
    pin the reverse, so that it does not slip back in.
    """

    async def test_keeps_publishing_a_stale_frame(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="alt", timestamp=loop.time() - 10.0)
        node = RecordingNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await run_briefly(publisher, 0.1)

        self.assertGreaterEqual(len(node.written), 1)
        self.assertEqual(node.written[0], f"encoded:alt:{FAST_CONFIG.jpeg_quality}")

    async def test_never_writes_an_empty_value(self):
        """Clearing the node was the signal; it must not come back."""
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="alt", timestamp=loop.time() - 10.0)
        node = RecordingNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await run_briefly(publisher, 0.1)

        self.assertNotIn("", node.written)

    async def test_a_fresh_frame_simply_replaces_the_old_one(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="alt", timestamp=loop.time() - 10.0)
        node = RecordingNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        publisher.start()
        await asyncio.sleep(0.06)
        camera.latest_frame = _fresh_frame("neu")
        await asyncio.sleep(0.06)
        await publisher.stop()

        self.assertIn(f"encoded:neu:{FAST_CONFIG.jpeg_quality}", node.written)


class OverlayTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_raw_frame_while_the_overlay_hangs(self):
        release = threading.Event()
        calls = 0

        class HangingAnnotator:
            def annotate(self, image, mode):
                nonlocal calls
                calls += 1
                release.wait(timeout=5.0)
                return "markiert"

        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = RecordingNode()
        publisher = CameraStreamPublisher(
            camera,
            node,
            replace(FAST_CONFIG, overlay_timeout_s=0.02),
            encode_frame=fake_encode,
            annotator=HangingAnnotator(),
        )

        try:
            await run_briefly(publisher, 0.15)
        finally:
            release.set()

        self.assertIn(f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}", node.written)
        # While the first run hangs, no second one starts.
        self.assertEqual(calls, 1)


class NormaliseModeTest(unittest.TestCase):
    def test_keeps_every_known_mode(self):
        for mode in OVERLAY_MODES:
            self.assertEqual(normalise_mode(mode), mode)

    def test_falls_back_for_an_empty_value(self):
        for value in ("", None, "   "):
            self.assertEqual(normalise_mode(value), DEFAULT_OVERLAY_MODE)

    def test_falls_back_for_an_unknown_value(self):
        self.assertEqual(normalise_mode("tippfehler"), DEFAULT_OVERLAY_MODE)

    def test_ignores_case_and_surrounding_whitespace(self):
        self.assertEqual(normalise_mode("  OFF  "), "off")
        self.assertEqual(normalise_mode("Calibration"), "calibration")




class PreviewSourceTest(unittest.IsolatedAsyncioTestCase):
    """With a small ISP image the stream computes in `calibration` mode on the
    scaled-down preview image. This is exactly what made the ceiling camera stutter
    when every mode still computed on the full frame.

    `apriltag` and `off`, by contrast, deliberately use the full frame. `apriltag`
    so that the stream shows the same image as the real job; otherwise it cannot be
    trusted to show whether the Pi really detects a tag. `off` is the debug raw
    image and shows what the Pi actually sees (focus/exposure/crop), not the
    preview; without detection it costs hardly more.
    """

    async def test_apriltag_mode_overlays_and_encodes_the_full_frame(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="voll", timestamp=loop.time(), preview="klein")
        node = RecordingNode()
        annotator = FakeAnnotator()
        publisher = CameraStreamPublisher(
            camera, node, FAST_CONFIG, encode_frame=fake_encode, annotator=annotator
        )

        await run_briefly(publisher, 0.1)

        self.assertEqual({image for image, _ in annotator.calls}, {"voll"})
        self.assertIn(
            f"encoded:annotated:voll:{DEFAULT_OVERLAY_MODE}:{FAST_CONFIG.jpeg_quality}",
            node.written,
        )

    async def test_calibration_mode_still_uses_the_preview(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="voll", timestamp=loop.time(), preview="klein")
        node = RecordingNode()
        annotator = FakeAnnotator()
        publisher = CameraStreamPublisher(
            camera,
            node,
            FAST_CONFIG,
            encode_frame=fake_encode,
            annotator=annotator,
            mode_node=FakeModeNode("calibration"),
        )

        await run_briefly(publisher, 0.1)

        self.assertEqual({image for image, _ in annotator.calls}, {"klein"})

    async def test_raw_mode_sends_the_full_frame_unmarked(self):
        """'off' is the debug raw image: it shows what the Pi actually sees (check
        focus/exposure/crop), not the preview."""
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="voll", timestamp=loop.time(), preview="klein")
        node = RecordingNode()
        annotator = FakeAnnotator()
        publisher = CameraStreamPublisher(
            camera,
            node,
            FAST_CONFIG,
            encode_frame=fake_encode,
            annotator=annotator,
            mode_node=FakeModeNode("off"),
        )

        await run_briefly(publisher, 0.1)

        self.assertEqual(annotator.calls, [])
        self.assertIn(f"encoded:voll:{FAST_CONFIG.jpeg_quality}", node.written)

    async def test_without_a_preview_the_full_frame_is_used_as_before(self):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("voll")
        node = RecordingNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await run_briefly(publisher, 0.1)

        self.assertIn(f"encoded:voll:{FAST_CONFIG.jpeg_quality}", node.written)


class UndistortedStreamTest(unittest.IsolatedAsyncioTestCase):
    """Mode "undistorted": full frame to the annotator, which undistorts."""

    async def test_the_full_frame_goes_to_the_annotator(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="voll", timestamp=loop.time(), preview="klein")
        node = RecordingNode()
        annotator = FakeAnnotator()
        publisher = CameraStreamPublisher(
            camera,
            node,
            FAST_CONFIG,
            encode_frame=fake_encode,
            annotator=annotator,
            mode_node=FakeModeNode("undistorted"),
        )

        await run_briefly(publisher, 0.1)

        self.assertEqual({call for call in annotator.calls}, {("voll", "undistorted")})
        self.assertIn(
            f"encoded:annotated:voll:undistorted:{FAST_CONFIG.jpeg_quality}", node.written
        )

    async def test_without_an_annotator_the_raw_frame_gets_a_hint(self):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("voll")
        node = RecordingNode()
        publisher = CameraStreamPublisher(
            camera,
            node,
            FAST_CONFIG,
            encode_frame=fake_encode,
            mode_node=FakeModeNode("undistorted"),
        )

        with patch(
            "vision_server.camera_stream._no_calibration_hint", lambda image: f"hinweis:{image}"
        ):
            await run_briefly(publisher, 0.1)

        self.assertIn(f"encoded:hinweis:voll:{FAST_CONFIG.jpeg_quality}", node.written)


if __name__ == "__main__":
    unittest.main()
