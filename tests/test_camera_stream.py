"""Tests fuer den Livestream-Publisher.

`encode_frame` wird durch einen Fake ersetzt, damit die Tests ohne `cv2`
laufen und ohne echte Bilddaten auskommen.
"""

import asyncio
import unittest
from dataclasses import replace

from vision_server.camera import CameraFrame
from vision_server.camera_stream import CameraStreamPublisher
from vision_server.profiles import CameraStreamConfig

FAST_CONFIG = CameraStreamConfig(stream_fps=50.0)


class FakeCamera:
    def __init__(self) -> None:
        self.latest_frame: CameraFrame | None = None


class FakeNode:
    def __init__(self) -> None:
        self.written: list[str] = []

    async def write_value(self, value) -> None:
        self.written.append(value)


def fake_encode(image, quality: int) -> str:
    return f"encoded:{image}:{quality}"


async def _run_briefly(publisher: CameraStreamPublisher, seconds: float) -> None:
    publisher.start()
    await asyncio.sleep(seconds)
    await publisher.stop()


class PublishLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_writes_the_encoded_frame_to_the_node(self):
        camera = FakeCamera()
        camera.latest_frame = CameraFrame(image="frame-1", timestamp=1.0)
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await _run_briefly(publisher, 0.1)

        self.assertGreaterEqual(len(node.written), 1)
        self.assertEqual(node.written[0], f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}")

    async def test_writes_nothing_before_the_first_frame_exists(self):
        camera = FakeCamera()
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await _run_briefly(publisher, 0.06)

        self.assertEqual(node.written, [])

    async def test_stop_is_idempotent_and_cancels_the_loop(self):
        camera = FakeCamera()
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)
        publisher.start()
        await publisher.stop()
        await publisher.stop()

    async def test_an_encode_failure_does_not_kill_the_loop(self):
        """Ein einzelner kaputter Frame darf den Stream nicht dauerhaft stoppen."""
        camera = FakeCamera()
        camera.latest_frame = CameraFrame(image="bad", timestamp=1.0)
        node = FakeNode()

        calls = 0

        def flaky_encode(image, quality):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Encode kaputt")
            return "ok"

        config = replace(FAST_CONFIG, stream_fps=50.0)
        publisher = CameraStreamPublisher(camera, node, config, encode_frame=flaky_encode)

        await _run_briefly(publisher, 0.1)

        self.assertGreaterEqual(calls, 2)
        self.assertIn("ok", node.written)


if __name__ == "__main__":
    unittest.main()
