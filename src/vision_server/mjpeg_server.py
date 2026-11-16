"""Live stream as MJPEG over HTTP, straight from the Pi to the browser.

Why not over OPC UA: there every image travels as a Base64 string (+33 %)
through asyncua, the backend, a JSON WebSocket message and the React store.
That is fine for 5 fps, but not for a smooth image. MJPEG is what an
`<img src=...>` plays without any library: `multipart/x-mixed-replace`, one JPEG
after another.

OPC UA stays the control path: the server tells the frontend the port via the
node `CameraStreamHttpPort`, and the overlay mode is still selected via
`CameraStreamMode`. The images arrive already marked up and encoded from the
`CameraStreamPublisher` -- this server encodes nothing itself.

Deliberately without a framework (aiohttp or similar): two GET paths,
`asyncio.start_server` is enough, and no dependency is added on the Pi.

    GET /stream.mjpg    endless MJPEG stream
    GET /snapshot.jpg   the newest single image
    GET /tagreference.jpg  debug image of the last MeasureTagReference (404 without)
"""

import asyncio
import base64
import contextlib
import logging
from pathlib import Path

from .camera_stream import CameraStreamPublisher

_log = logging.getLogger(__name__)

BOUNDARY = b"frame"
#: How long a client waits for a new image before the loop checks
#: whether the connection is still alive.
FRAME_WAIT_S = 2.0
#: A client that does not accept an image within this time is disconnected.
WRITE_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 5.0

_COMMON_HEADERS = (
    b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
    b"Pragma: no-cache\r\n"
    b"Access-Control-Allow-Origin: *\r\n"
    b"Connection: close\r\n"
)


class MjpegServer:
    """Serve the images of a `CameraStreamPublisher` over HTTP."""

    def __init__(
        self,
        publisher: CameraStreamPublisher,
        port: int,
        *,
        host: str = "0.0.0.0",
        max_clients: int = 4,
        tag_reference_dir: Path | None = None,
    ) -> None:
        #: Log store of the tag referencing (`tagreference_log`); `None` = 404.
        self._tag_reference_dir = tag_reference_dir
        self._publisher = publisher
        self._host = host
        self._requested_port = port
        #: Upper limit, so that forgotten browser tabs do not load down the Pi.
        self._max_clients = max_clients
        self._clients = 0
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        """The port actually bound (assigned by the system when `port=0`)."""
        if self._server is None or not self._server.sockets:
            return 0
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, self._host, self._requested_port
        )
        _log.info("MJPEG-Livestream auf http://%s:%d/stream.mjpg", self._host, self.port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        # Open streams never end on their own; without cancelling, wait_closed() hangs.
        for task in list(self._handlers):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._handlers.add(task)
        try:
            path = await self._read_request_path(reader)
            if path is None:
                await self._respond(writer, b"400 Bad Request", b"text/plain", b"bad request")
            elif path == "/stream.mjpg":
                await self._stream(writer)
            elif path == "/snapshot.jpg":
                await self._snapshot(writer)
            elif path == "/tagreference.jpg":
                await self._tag_reference(writer)
            else:
                await self._respond(writer, b"404 Not Found", b"text/plain", b"not found")
        except (ConnectionError, TimeoutError, asyncio.IncompleteReadError):
            # Browser tab closed, network gone: everyday events, not errors.
            pass
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception("MJPEG-Anfrage fehlgeschlagen")
        finally:
            if task is not None:
                self._handlers.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _read_request_path(self, reader: asyncio.StreamReader) -> str | None:
        """Read the request line and headers; return the path without the query."""
        request_line = await asyncio.wait_for(reader.readline(), REQUEST_TIMEOUT_S)
        parts = request_line.decode("latin-1").split()
        # Discard the headers up to the blank line -- none of them is needed.
        while True:
            line = await asyncio.wait_for(reader.readline(), REQUEST_TIMEOUT_S)
            if line in (b"\r\n", b"\n", b""):
                break
        if len(parts) < 2 or parts[0] != "GET":
            return None
        return parts[1].split("?", 1)[0]

    async def _respond(
        self, writer: asyncio.StreamWriter, status: bytes, content_type: bytes, body: bytes
    ) -> None:
        writer.write(
            b"HTTP/1.1 " + status + b"\r\n"
            b"Content-Type: " + content_type + b"\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            + _COMMON_HEADERS
            + b"\r\n"
            + body
        )
        await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT_S)

    async def _snapshot(self, writer: asyncio.StreamWriter) -> None:
        latest = self._publisher.latest
        if latest is None:
            await self._respond(
                writer, b"503 Service Unavailable", b"text/plain", b"kein aktuelles Bild"
            )
            return
        await self._respond(writer, b"200 OK", b"image/jpeg", base64.b64decode(latest[1]))

    async def _tag_reference(self, writer: asyncio.StreamWriter) -> None:
        from .tagreference_log import latest_overlay

        def read() -> bytes | None:
            path = latest_overlay(self._tag_reference_dir)
            try:
                return path.read_bytes() if path is not None else None
            except OSError:
                return None

        body = await asyncio.get_running_loop().run_in_executor(None, read)
        if body is None:
            await self._respond(
                writer, b"404 Not Found", b"text/plain", b"keine Einmess-Messung"
            )
            return
        await self._respond(writer, b"200 OK", b"image/jpeg", body)

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        if self._clients >= self._max_clients:
            await self._respond(
                writer, b"503 Service Unavailable", b"text/plain", b"zu viele Zuschauer"
            )
            return
        self._clients += 1
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: multipart/x-mixed-replace; boundary=" + BOUNDARY + b"\r\n"
                + _COMMON_HEADERS
                + b"\r\n"
            )
            await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT_S)
            async with self._publisher.viewer():
                seq = -1
                while True:
                    latest = await self._publisher.next_frame(seq, FRAME_WAIT_S)
                    if latest is None:
                        # Camera stalled or image stale: send nothing, the browser
                        # keeps the last image. We notice a dead client on the
                        # next write.
                        if writer.is_closing():
                            return
                        continue
                    seq, encoded = latest
                    jpeg = base64.b64decode(encoded)
                    writer.write(
                        b"--" + BOUNDARY + b"\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                    await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT_S)
        finally:
            self._clients -= 1
