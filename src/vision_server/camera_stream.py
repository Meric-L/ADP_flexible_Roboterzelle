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
import json
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


def _resize_for_stream(image, max_width: int):
    """Skaliert nur fuers Publizieren herunter, falls breiter als `max_width`.

    Erkennung und Kalibrierung sehen weiter den vollen Kamera-Frame -- diese
    Funktion laeuft erst nach dem Overlay, auf einer Kopie fuers Encoding.
    Ohne das kostet z. B. cam_ceiling (2028x1520) pro Tick ein JPEG-Encode
    eines ~3-MP-Bildes, was die Stream-Framerate spuerbar drueckt.
    """
    import cv2

    height, width = image.shape[:2]
    if width <= max_width:
        return image
    scale = max_width / width
    return cv2.resize(image, (max_width, round(height * scale)), interpolation=cv2.INTER_AREA)


class CameraStreamPublisher:
    """Schreibt periodisch den neuesten Kamera-Frame in einen String-Knoten.

    Oeffnet und schliesst die Kamera **nicht** selbst — die gehoert der
    Erkennungsquelle, die dieselbe `SharedCamera`-Instanz reicht (siehe
    `runner.py`). Schreibt bewusst jeden Tick, auch bei einem unveraenderten
    Frame: das Frontend soll ein einfaches "Bild kommt an / kommt nicht an"
    sehen, keine Diff-Logik.
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
        progress_node: Node | None = None,
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
        #: `CalibrationProgress`-Knoten; nur beschrieben, wenn der Annotator
        #: gerade eine `CalibrationSession` haengen hat (siehe
        #: `AprilTagStreamAnnotator.calibration_progress`).
        self._progress_node = progress_node
        self._mode = normalise_mode(config.overlay_mode)
        self._task: asyncio.Task | None = None

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

    async def _publish_loop(self) -> None:
        loop = asyncio.get_running_loop()
        interval = 1.0 / self._config.stream_fps
        while True:
            started = loop.time()
            frame = self._camera.latest_frame
            if frame is not None:
                try:
                    mode = await self._read_mode()
                    image = frame.image
                    if mode != "off" and self._annotator is not None:
                        # In the worker thread: detection and drawing are
                        # blocking and have no business on the event loop.
                        # The annotator works on a copy.
                        image = await loop.run_in_executor(
                            None, self._annotator.annotate, frame.image, mode
                        )
                    if self._config.max_stream_width is not None:
                        image = await loop.run_in_executor(
                            None, _resize_for_stream, image, self._config.max_stream_width
                        )
                    encoded = await loop.run_in_executor(
                        None, self._encode_frame, image, self._config.jpeg_quality
                    )
                    if encoded is not None:
                        await self._node.write_value(encoded)
                except Exception:
                    _log.exception("Kamera-Frame konnte nicht veroeffentlicht werden")
                if self._progress_node is not None and self._annotator is not None:
                    # Unabhaengig vom gewaehlten `mode` -- der Fortschritt soll
                    # auch sichtbar sein, wenn der Viewer gerade "off" zeigt.
                    try:
                        progress = self._annotator.calibration_progress
                        await self._progress_node.write_value(
                            json.dumps(progress if progress is not None else {"running": False})
                        )
                    except Exception:
                        _log.exception("Kalibrier-Fortschritt konnte nicht veroeffentlicht werden")
            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
