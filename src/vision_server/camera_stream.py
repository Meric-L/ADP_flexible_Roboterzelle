"""Veroeffentlicht Frames der geteilten Kamera als Base64-JPEG in einem Knoten.

Transportweg laut Backend-Absprache: das Backend abonniert diesen Knoten
ganz normal ueber die bestehende `subscribeNode`-Infrastruktur, keine neue
Backend-Logik noetig. Dieses Modul kennt daher nur den Knoten, keine
WebSocket- oder Backend-Details.

The stream can be annotated: an `annotator` draws detected AprilTags or
board corners onto the image, and a second, **writable** node selects the
mode ("off", "apriltag", "calibration"). The publisher itself knows neither
OpenCV nor detection -- it only passes mode and image along.
"""

import asyncio
import base64
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from asyncua.common.node import Node

from tagloc.modes import normalise_mode

from .camera import SharedCamera
from .profiles import CameraStreamConfig

_log = logging.getLogger(__name__)


def _encode_jpeg_base64(image, quality: int) -> str | None:
    """Laeuft im Worker-Thread: JPEG-Encode plus Base64. `None` bei Encode-Fehler.

    Import bewusst hier drin statt auf Modulebene, damit ein Import dieses
    Moduls (z. B. ueber `runner.py`) kein `cv2` braucht, solange kein Stream
    laeuft.
    """
    import cv2

    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    return base64.b64encode(buffer).decode("ascii")


class CameraStreamPublisher:
    """Schreibt periodisch den neuesten Kamera-Frame in einen String-Knoten.

    Oeffnet und schliesst die Kamera **nicht** selbst — die gehoert der
    Erkennungsquelle, die dieselbe `SharedCamera`-Instanz reicht (siehe
    `runner.py`). Schreibt bewusst jeden Tick, auch bei einem unveraenderten
    Frame: das Frontend soll ein einfaches "Bild kommt an / kommt nicht an"
    sehen, keine Diff-Logik.

    Ausnahme: ein Frame aelter als `stale_frame_s` geht nicht raus, der Knoten
    wird einmal geleert. Sonst sieht ein haengendes `capture_array()` im
    Frontend aus wie ein lebendes, nur stehendes Bild.
    """

    def __init__(
        self,
        camera: SharedCamera,
        node: Node,
        config: CameraStreamConfig,
        *,
        encode_frame: Callable[[Any, int], str | None] = _encode_jpeg_base64,
        annotator: Any = None,
        mode_node: Node | None = None,
    ) -> None:
        self._camera = camera
        self._node = node
        self._config = config
        #: Austauschbar fuer Tests, die ohne `cv2` laufen sollen.
        self._encode_frame = encode_frame
        #: Draws detection results onto the image; `None` = always raw frame.
        self._annotator = annotator
        #: Writable node through which the frontend selects the mode.
        self._mode_node = mode_node
        self._mode = normalise_mode(config.overlay_mode)
        self._task: asyncio.Task | None = None
        #: True, solange der Knoten wegen eines veralteten Frames geleert ist.
        self._stale = False
        #: Der letzte Overlay-Lauf; haengt er noch, wird kein neuer gestartet.
        self._overlay_run: asyncio.Future | None = None

    @property
    def mode(self) -> str:
        """Return the last-read overlay mode."""
        return self._mode

    async def _read_mode(self) -> str:
        """Read the selected mode. Keeps the last value on failure.

        An unreadable node is no reason to stop the stream -- the image
        matters more than the markup.
        """
        if self._mode_node is None:
            return self._mode
        try:
            self._mode = normalise_mode(await self._mode_node.read_value())
        except Exception:
            _log.debug("Overlay-Modus nicht lesbar, bleibe bei '%s'", self._mode)
        return self._mode

    def start(self) -> None:
        self._task = asyncio.create_task(self._publish_loop())

    async def _annotate(self, loop: asyncio.AbstractEventLoop, image, mode: str):
        """Overlay mit Timeout; bei Timeout oder noch laufendem Lauf das Rohbild.

        Ein haengender Lauf blockiert einen Thread des Default-Executors, der
        sich nicht abbrechen laesst. Deshalb startet kein neuer, bevor er
        fertig ist -- sonst liefe der Executor Tick fuer Tick voll.
        """
        if self._overlay_run is not None and not self._overlay_run.done():
            return image
        # In the worker thread: detection and drawing are blocking and have no
        # business on the event loop. The annotator works on a copy.
        run = loop.run_in_executor(None, self._annotator.annotate, image, mode)
        # Holt die Exception eines abgehaengten Laufs ab, sonst meldet asyncio
        # "Future exception was never retrieved".
        run.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._overlay_run = run
        try:
            return await asyncio.wait_for(
                asyncio.shield(run), timeout=self._config.overlay_timeout_s
            )
        except TimeoutError:
            _log.warning(
                "Overlay laenger als %.1f s, sende das Rohbild",
                self._config.overlay_timeout_s,
            )
            return image

    async def _mark_stale(self, age: float) -> None:
        """Leert den Knoten einmal, wenn der neueste Frame zu alt ist."""
        if self._stale:
            return
        _log.warning(
            "Neuester Kamera-Frame ist %.1f s alt -- Livestream pausiert, bis wieder "
            "Bilder kommen",
            age,
        )
        self._stale = True
        with contextlib.suppress(Exception):
            await self._node.write_value("")

    async def _publish_loop(self) -> None:
        loop = asyncio.get_running_loop()
        interval = 1.0 / self._config.stream_fps
        while True:
            started = loop.time()
            frame = self._camera.latest_frame
            age = None if frame is None else started - frame.timestamp
            if age is not None and age > self._config.stale_frame_s:
                await self._mark_stale(age)
            elif frame is not None:
                if self._stale:
                    _log.info("Kamera liefert wieder Bilder, Livestream laeuft weiter")
                    self._stale = False
                try:
                    mode = await self._read_mode()
                    image = frame.image
                    if mode != "off" and self._annotator is not None:
                        image = await self._annotate(loop, frame.image, mode)
                    encoded = await loop.run_in_executor(
                        None, self._encode_frame, image, self._config.jpeg_quality
                    )
                    if encoded is not None:
                        await self._node.write_value(encoded)
                except Exception:
                    _log.exception("Kamera-Frame konnte nicht veroeffentlicht werden")
            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
