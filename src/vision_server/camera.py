"""Eine Kamera, ein Capture-Loop, mehrere Leser.

QR-Erkennung (`detection/image_recognition.py`) und Livestream-Publisher
(`camera_stream.py`) brauchen beide Bilder derselben physischen Kamera.
Picamera2/libcamera **und** RealSense lassen pro Kamera aber nur einen
offenen Zugriff gleichzeitig zu — ein zweiter Open waehrend der Stream laeuft
wuerde fehlschlagen. Deshalb haelt genau eine `SharedCamera` die Hardware
offen und nimmt kontinuierlich Frames auf; alle Leser bekommen ueber
`latest_frame` denselben zwischengespeicherten Frame statt selbst die
Hardware anzusprechen.

Welche Hardware das ist, waehlt `CameraStreamConfig.backend`
(`profiles.CAMERA_BACKENDS`) -- z. B. Picamera2 auf dem Decken-Pi, RealSense
auf dem Hand-Pi. Alle Backends liefern denselben `CameraFrame` (BGR-Array),
QR-Erkennung und Stream-Publisher kennen den konkreten Backend-Typ nicht.

`picamera2`/`pyrealsense2`/`cv2` werden erst in `open()` importiert, damit ein
Server ohne `camera_stream`-Konfiguration (z. B. lokale Entwicklung, Tests)
ohne diese Abhaengigkeiten startet.
"""

import asyncio
import contextlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .profiles import CAMERA_BACKENDS, CameraStreamConfig

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraFrame:
    """Ein aufgenommener Frame (BGR, wie von OpenCV erwartet) mit Zeitstempel."""

    image: Any
    timestamp: float


class SharedCamera:
    """Haelt eine Kamera offen und stellt den jeweils neuesten Frame bereit."""

    def __init__(self, config: CameraStreamConfig) -> None:
        self._config = config
        self._camera: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._loop_task: asyncio.Task | None = None
        self._latest: CameraFrame | None = None

    @property
    def latest_frame(self) -> CameraFrame | None:
        """Der zuletzt aufgenommene Frame, oder `None` vor dem ersten Capture."""
        return self._latest

    async def open(self) -> None:
        """Oeffnet die Kamera und startet den Capture-Loop. Nicht idempotent."""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-camera")
        loop = asyncio.get_running_loop()
        self._camera = await loop.run_in_executor(self._executor, self._open_camera)
        self._loop_task = asyncio.create_task(self._capture_loop())

    def _open_camera(self) -> Any:
        """Laeuft im Kamera-Worker-Thread: Hardware oeffnen plus Warmup."""
        import time

        backend = self._config.backend
        if backend == "picamera2":
            camera = self._open_picamera2()
        elif backend == "realsense":
            camera = self._open_realsense()
        elif backend == "opencv":
            camera = self._open_opencv()
        else:
            raise ValueError(
                f"Unbekanntes Kamera-Backend '{backend}' (bekannt: "
                f"{', '.join(CAMERA_BACKENDS)})"
            )
        time.sleep(self._config.warmup_s)
        return camera

    def _open_picamera2(self) -> Any:
        from picamera2 import Picamera2

        camera = Picamera2()
        camera.configure(
            camera.create_video_configuration(main={"size": self._config.resolution})
        )
        camera.start()
        return camera

    def _open_realsense(self) -> Any:
        """Startet eine RealSense-Pipeline auf dem Farb-Stream (BGR8)."""
        import pyrealsense2 as rs

        width, height = self._config.resolution
        rs_config = rs.config()
        rs_config.enable_stream(
            rs.stream.color, width, height, rs.format.bgr8, self._config.realsense_fps
        )
        pipeline = rs.pipeline()
        pipeline.start(rs_config)
        return pipeline

    def _open_opencv(self) -> Any:
        import cv2

        camera = cv2.VideoCapture(self._config.camera_index)
        if not camera.isOpened():
            raise RuntimeError(
                "Kamera konnte nicht geoeffnet werden "
                f"(camera_index={self._config.camera_index})"
            )
        return camera

    def _read_frame(self) -> Any:
        """Laeuft im Kamera-Worker-Thread: ein Frame in BGR."""
        backend = self._config.backend
        if backend == "picamera2":
            import cv2

            return cv2.cvtColor(self._camera.capture_array(), cv2.COLOR_RGB2BGR)
        if backend == "realsense":
            import numpy as np

            frames = self._camera.wait_for_frames()
            color_frame = frames.get_color_frame()
            if not color_frame:
                raise RuntimeError("RealSense-Pipeline lieferte keinen Farbframe")
            return np.asanyarray(color_frame.get_data())
        ok, frame = self._camera.read()
        if not ok:
            raise RuntimeError("Kamera lieferte kein Bild")
        return frame

    async def _capture_loop(self) -> None:
        """Nimmt Frames mit `stream_fps` auf, bis die Task abgebrochen wird."""
        loop = asyncio.get_running_loop()
        interval = 1.0 / self._config.stream_fps
        while True:
            started = loop.time()
            try:
                image = await loop.run_in_executor(self._executor, self._read_frame)
                self._latest = CameraFrame(image=image, timestamp=loop.time())
            except Exception:
                _log.exception("Kamera-Frame konnte nicht aufgenommen werden")
            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def close(self) -> None:
        """Stoppt den Capture-Loop und gibt die Kamera frei. Idempotent."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        if self._camera is not None and self._executor is not None:
            camera, self._camera = self._camera, None
            await asyncio.get_running_loop().run_in_executor(
                self._executor, self._close_camera, camera
            )
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _close_camera(self, camera: Any) -> None:
        backend = self._config.backend
        if backend == "picamera2":
            camera.stop()
            camera.close()
        elif backend == "realsense":
            camera.stop()  # `camera` ist die `rs.pipeline()`
        else:
            camera.release()
