"""Livestream als MJPEG ueber HTTP, direkt vom Pi in den Browser.

Warum nicht ueber OPC UA: dort reist jedes Bild als Base64-String (+33 %)
durch asyncua, das Backend, eine JSON-WebSocket-Nachricht und den
React-Store. Fuer 5 fps reicht das, fuer ein fluessiges Bild nicht. MJPEG ist
das, was ein `<img src=...>` ohne jede Bibliothek abspielt:
`multipart/x-mixed-replace`, ein JPEG nach dem anderen.

OPC UA bleibt der Steuerweg: der Server nennt dem Frontend den Port ueber den
Knoten `CameraStreamHttpPort`, der Overlay-Modus wird weiter ueber
`CameraStreamMode` gewaehlt. Die Bilder kommen fertig markiert und kodiert vom
`CameraStreamPublisher` -- dieser Server kodiert nichts selbst.

Bewusst ohne Framework (aiohttp o. ae.): zwei GET-Pfade, `asyncio.start_server`
genuegt, und auf dem Pi kommt keine Abhaengigkeit dazu.

    GET /stream.mjpg    endloser MJPEG-Stream
    GET /snapshot.jpg   das neueste Einzelbild
"""

import asyncio
import base64
import contextlib
import logging

from .camera_stream import CameraStreamPublisher

_log = logging.getLogger(__name__)

BOUNDARY = b"frame"
#: So lange wartet ein Client auf ein neues Bild, bevor die Schleife prueft,
#: ob die Verbindung noch lebt.
FRAME_WAIT_S = 2.0
#: Wer ein Bild nicht in dieser Zeit abnimmt, wird getrennt.
WRITE_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 5.0

_COMMON_HEADERS = (
    b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
    b"Pragma: no-cache\r\n"
    b"Access-Control-Allow-Origin: *\r\n"
    b"Connection: close\r\n"
)


class MjpegServer:
    """Liefert die Bilder eines `CameraStreamPublisher` per HTTP aus."""

    def __init__(
        self,
        publisher: CameraStreamPublisher,
        port: int,
        *,
        host: str = "0.0.0.0",
        max_clients: int = 4,
    ) -> None:
        self._publisher = publisher
        self._host = host
        self._requested_port = port
        #: Obergrenze, damit vergessene Browser-Tabs den Pi nicht auslasten.
        self._max_clients = max_clients
        self._clients = 0
        self._server: asyncio.Server | None = None
        self._handlers: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        """Der tatsaechlich gebundene Port (bei `port=0` vom System vergeben)."""
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
        # Offene Streams enden nie von selbst; ohne Abbruch haengt wait_closed().
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
            else:
                await self._respond(writer, b"404 Not Found", b"text/plain", b"not found")
        except (ConnectionError, TimeoutError, asyncio.IncompleteReadError):
            # Browser-Tab zu, Netz weg: Alltag, kein Fehler.
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
        """Liest Anfragezeile und Header; gibt den Pfad ohne Query zurueck."""
        request_line = await asyncio.wait_for(reader.readline(), REQUEST_TIMEOUT_S)
        parts = request_line.decode("latin-1").split()
        # Header bis zur Leerzeile verwerfen -- gebraucht wird keiner.
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
                        # Kamera haengt oder Bild veraltet: nichts senden, der
                        # Browser behaelt das letzte Bild. Toten Client merken
                        # wir beim naechsten Schreiben.
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
