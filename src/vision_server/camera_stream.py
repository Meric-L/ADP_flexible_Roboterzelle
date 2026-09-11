"""Veroeffentlicht Frames der geteilten Kamera als Base64-JPEG in einem Knoten.

Transportweg laut Backend-Absprache: das Backend abonniert diesen Knoten
ganz normal ueber die bestehende `subscribeNode`-Infrastruktur, keine neue
Backend-Logik noetig. Dieses Modul kennt daher nur den Knoten, keine
WebSocket- oder Backend-Details.
"""

import asyncio
import base64
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from asyncua.common.node import Node

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
    `ImageRecognitionDetectionSource`, die dieselbe `SharedCamera`-Instanz
    reicht (siehe `runner.py`). Schreibt bewusst jeden Tick, auch bei einem
    unveraenderten Frame: das Frontend soll ein einfaches "Bild kommt an /
    kommt nicht an" sehen, keine Diff-Logik.
    """

    def __init__(
        self,
        camera: SharedCamera,
        node: Node,
        config: CameraStreamConfig,
        *,
        encode_frame: Callable[[Any, int], str | None] = _encode_jpeg_base64,
    ) -> None:
        self._camera = camera
        self._node = node
        self._config = config
        #: Austauschbar fuer Tests, die ohne `cv2` laufen sollen.
        self._encode_frame = encode_frame
        self._task: asyncio.Task | None = None

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
                    encoded = await loop.run_in_executor(
                        None, self._encode_frame, frame.image, self._config.jpeg_quality
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
