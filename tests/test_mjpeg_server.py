"""Tests fuer den MJPEG-Livestream ueber HTTP.

Laeuft gegen einen echten Socket auf einem vom System vergebenen Port; die
Kamera ist ein Fake, das "JPEG" ein erkennbarer Byte-String.
"""

import asyncio
import base64
import unittest

from vision_server.camera import CameraFrame
from vision_server.camera_stream import CameraStreamPublisher
from vision_server.mjpeg_server import MjpegServer
from vision_server.profiles import CameraStreamConfig

#: `max_stream_width=None`: die Fake-Bilder sind Strings, keine Arrays --
#: wie in test_camera_stream.py, sonst scheitert schon das Verkleinern.
CONFIG = CameraStreamConfig(
    stream_fps=20.0, http_fps=100.0, overlay_mode="off", max_stream_width=None
)


class FakeCamera:
    def __init__(self) -> None:
        self.latest_frame: CameraFrame | None = None
        self.counter = 0

    def next(self) -> None:
        self.counter += 1
        self.latest_frame = CameraFrame(
            image=f"jpeg-{self.counter}", timestamp=asyncio.get_running_loop().time()
        )


class FakeNode:
    def __init__(self) -> None:
        self.written: list[str] = []

    async def write_value(self, value) -> None:
        self.written.append(value)


def fake_encode(image, quality: int) -> str:
    return base64.b64encode(image.encode()).decode()


async def _get(port: int, path: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: pi\r\n\r\n".encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2.0)
    return reader, writer, head


async def _read_part(reader: asyncio.StreamReader) -> bytes:
    part_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2.0)
    length = next(
        int(line.split(b":")[1])
        for line in part_head.split(b"\r\n")
        if line.lower().startswith(b"content-length")
    )
    body = await reader.readexactly(length)
    await reader.readexactly(2)
    return body


class MjpegServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.camera = FakeCamera()
        self.node = FakeNode()
        self.publisher = CameraStreamPublisher(
            self.camera, self.node, CONFIG, encode_frame=fake_encode
        )
        self.publisher.start()
        self.server = MjpegServer(self.publisher, 0, host="127.0.0.1", max_clients=1)
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()
        await self.publisher.stop()

    async def test_streams_new_frames_as_multipart_jpeg(self):
        self.camera.next()
        reader, writer, head = await _get(self.server.port, "/stream.mjpg")

        self.assertIn(b"200 OK", head)
        self.assertIn(b"multipart/x-mixed-replace; boundary=frame", head)
        first = await _read_part(reader)
        self.camera.next()
        second = await _read_part(reader)
        writer.close()

        self.assertEqual(first, b"jpeg-1")
        self.assertEqual(second, b"jpeg-2")

    async def test_does_not_resend_an_unchanged_frame(self):
        self.camera.next()
        reader, writer, _ = await _get(self.server.port, "/stream.mjpg")
        await _read_part(reader)

        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 0.2)
        writer.close()

    async def test_snapshot_returns_the_latest_jpeg(self):
        self.camera.next()
        await asyncio.sleep(0.1)
        reader, writer, head = await _get(self.server.port, "/snapshot.jpg")
        body = await reader.read()
        writer.close()

        self.assertIn(b"200 OK", head)
        self.assertIn(b"image/jpeg", head)
        self.assertEqual(body, b"jpeg-1")

    async def test_snapshot_is_unavailable_before_the_first_frame(self):
        reader, writer, head = await _get(self.server.port, "/snapshot.jpg")
        writer.close()

        self.assertIn(b"503", head)

    async def test_unknown_path_is_404(self):
        reader, writer, head = await _get(self.server.port, "/")
        writer.close()

        self.assertIn(b"404", head)

    async def test_refuses_viewers_beyond_the_limit(self):
        self.camera.next()
        reader, writer, _ = await _get(self.server.port, "/stream.mjpg")
        await _read_part(reader)

        _, second, head = await _get(self.server.port, "/stream.mjpg")
        writer.close()
        second.close()

        self.assertIn(b"503", head)

    async def test_stop_ends_open_streams(self):
        self.camera.next()
        reader, writer, _ = await _get(self.server.port, "/stream.mjpg")
        await _read_part(reader)

        await asyncio.wait_for(self.server.stop(), 3.0)

        self.assertEqual(await asyncio.wait_for(reader.read(), 2.0), b"")
        writer.close()


class PublisherFanOutTest(unittest.IsolatedAsyncioTestCase):
    async def test_node_stays_at_stream_fps_while_viewers_run_faster(self):
        camera = FakeCamera()
        node = FakeNode()
        config = CameraStreamConfig(
            stream_fps=10.0, http_fps=100.0, overlay_mode="off", max_stream_width=None
        )
        publisher = CameraStreamPublisher(camera, node, config, encode_frame=fake_encode)
        loop = asyncio.get_running_loop()
        started = loop.time()
        publisher.start()
        frames = 0
        async with publisher.viewer():
            seq = -1
            for _ in range(25):
                camera.next()
                latest = await publisher.next_frame(seq, 1.0)
                self.assertIsNotNone(latest)
                seq = latest[0]
                frames += 1
                await asyncio.sleep(0.012)
        await publisher.stop()
        elapsed = loop.time() - started

        self.assertEqual(frames, 25)
        # Gemessen statt angenommen: unter Windows rundet asyncio.sleep auf den
        # Timer-Takt (~15,6 ms) auf, die Schleife dauert dann eher 1,5 s als
        # 0,5 s. Zugesichert ist nur: der Knoten folgt stream_fps, nicht den Frames.
        self.assertLessEqual(len(node.written), elapsed * config.stream_fps + 2)
        self.assertLess(len(node.written), frames)
        self.assertGreaterEqual(len(node.written), 2)

    async def test_still_serves_a_stale_frame(self):
        """MJPEG zeigt weiter das letzte Bild, auch wenn die Kamera haengt.

        Ob sie haengt, sagt `DeviceHealth` nach OPC 40100-2 -- nicht ein
        fehlendes Bild. Frueher lieferte `latest` hier `None` und
        `/snapshot.jpg` antwortete mit 503.
        """
        camera = FakeCamera()
        camera.latest_frame = CameraFrame(
            image="alt", timestamp=asyncio.get_running_loop().time() - 10.0
        )
        publisher = CameraStreamPublisher(camera, FakeNode(), CONFIG, encode_frame=fake_encode)
        publisher.start()

        latest = await publisher.next_frame(-1, 0.1)
        await publisher.stop()

        self.assertIsNotNone(latest)


if __name__ == "__main__":
    unittest.main()
