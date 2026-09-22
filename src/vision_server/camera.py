"""Eine Kamera, ein Capture-Loop, mehrere Leser.

Erkennung (`detection/apriltag.py`) und Livestream-Publisher
(`camera_stream.py`) brauchen beide Bilder derselben physischen Kamera.
Picamera2/libcamera lassen pro Kamera aber nur einen offenen Zugriff
gleichzeitig zu — ein zweiter `Picamera2()`-Open waehrend der Stream laeuft
wuerde fehlschlagen. Deshalb haelt genau eine `SharedCamera` die Hardware
offen und nimmt kontinuierlich Frames auf; alle Leser bekommen ueber
`latest_frame` denselben zwischengespeicherten Frame statt selbst die
Hardware anzusprechen.

`picamera2`/`cv2` werden erst in `open()` importiert, damit ein Server ohne
`camera_stream`-Konfiguration (z. B. lokale Entwicklung, Tests) ohne diese
Abhaengigkeiten startet.

Watchdog: `capture_array()` kann ohne jede Fehlermeldung ewig haengen, wenn
libcamera keinen Frame mehr liefert (so auf Pi 1 am 2026-09-22 per `py-spy`
beobachtet). Der Capture-Loop begrenzt deshalb jede Aufnahme, oeffnet die
Kamera bei Haengern neu und beendet als letztes Mittel den Prozess, damit
systemd ihn neu startet. Ein haengender Worker-Thread laesst sich in Python
nicht abbrechen -- er wird zurueckgelassen, nicht beendet.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .profiles import CameraStreamConfig

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraFrame:
    """Ein aufgenommener Frame (BGR, wie von OpenCV erwartet) mit Zeitstempel."""

    image: Any
    timestamp: float


def _exit_process() -> None:
    """Beendet den Prozess hart, damit systemd (`Restart=always`) neu startet.

    `os._exit` statt `sys.exit`: ein normales Beenden wartet beim Shutdown auf
    alle Executor-Threads -- und damit auf den, der in `capture_array()`
    haengt. Der Prozess wuerde nie enden.
    """
    logging.shutdown()
    os._exit(1)


class SharedCamera:
    """Haelt eine Kamera offen und stellt den jeweils neuesten Frame bereit."""

    def __init__(
        self,
        config: CameraStreamConfig,
        *,
        on_give_up: Callable[[], None] = _exit_process,
    ) -> None:
        self._config = config
        self._camera: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._loop_task: asyncio.Task | None = None
        self._latest: CameraFrame | None = None
        #: Austauschbar fuer Tests, die den Prozess nicht beenden duerfen.
        self._on_give_up = on_give_up

    @property
    def latest_frame(self) -> CameraFrame | None:
        """Der zuletzt aufgenommene Frame, oder `None` vor dem ersten Capture."""
        return self._latest

    @property
    def is_open(self) -> bool:
        """Whether the capture loop is running."""
        return self._loop_task is not None

    async def open(self) -> None:
        """Oeffnet die Kamera und startet den Capture-Loop. Idempotent.

        Idempotent because two parties may open it: the runner opens the
        camera on its own when the detection source cannot open for lack of a
        calibration (livestream and remote calibration need the image exactly
        then), and the source opens it again once a calibration exists.
        """
        if self.is_open:
            return
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-camera")
        loop = asyncio.get_running_loop()
        try:
            self._camera = await loop.run_in_executor(self._executor, self._open_camera)
        except BaseException:
            # A failed open must not leave a worker behind for the next attempt.
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
            raise
        self._loop_task = asyncio.create_task(self._capture_loop())

    def _open_camera(self) -> Any:
        """Laeuft im Kamera-Worker-Thread: Hardware oeffnen plus Warmup."""
        import time

        if self._config.use_picamera:
            from picamera2 import Picamera2

            camera = Picamera2()
            camera.configure(
                camera.create_video_configuration(main={"size": self._config.resolution})
            )
            camera.start()
        else:
            import cv2

            camera = cv2.VideoCapture(self._config.camera_index)
            if not camera.isOpened():
                raise RuntimeError(
                    "Kamera konnte nicht geoeffnet werden "
                    f"(camera_index={self._config.camera_index})"
                )
        time.sleep(self._config.warmup_s)
        return camera

    def _read_frame(self) -> Any:
        """Laeuft im Kamera-Worker-Thread: ein Frame in BGR."""
        import cv2

        if self._config.use_picamera:
            return cv2.cvtColor(self._camera.capture_array(), cv2.COLOR_RGB2BGR)
        ok, frame = self._camera.read()
        if not ok:
            raise RuntimeError("Kamera lieferte kein Bild")
        return frame

    async def _capture_once(self, loop: asyncio.AbstractEventLoop) -> str:
        """Eine Aufnahme mit Timeout: `"ok"`, `"error"` oder `"hung"`."""
        if self._camera is None:
            # Die letzte Neu-Oeffnung ist gescheitert; nichts zu lesen.
            return "hung"
        try:
            image = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._read_frame),
                timeout=self._config.frame_timeout_s,
            )
        except TimeoutError:
            _log.error(
                "Kamera liefert seit %.1f s kein Bild -- capture haengt",
                self._config.frame_timeout_s,
            )
            return "hung"
        except Exception:
            _log.exception("Kamera-Frame konnte nicht aufgenommen werden")
            return "error"
        self._latest = CameraFrame(image=image, timestamp=loop.time())
        return "ok"

    async def _capture_loop(self) -> None:
        """Nimmt Frames mit `capture_fps` auf, bis die Task abgebrochen wird.

        Eskalation: Fehler zaehlen, ab `max_capture_failures` (ein Haenger
        zaehlt sofort voll) die Kamera neu oeffnen, nach `max_reopen_attempts`
        Neu-Oeffnungen ohne einen Frame dazwischen aufgeben.
        """
        loop = asyncio.get_running_loop()
        interval = 1.0 / self._config.capture_fps
        failures = 0
        reopens = 0
        while True:
            started = loop.time()
            outcome = await self._capture_once(loop)
            if outcome == "ok":
                failures = 0
                reopens = 0
            elif outcome == "hung":
                failures = self._config.max_capture_failures
            else:
                failures += 1
            if failures >= self._config.max_capture_failures:
                failures = 0
                reopens += 1
                if reopens > self._config.max_reopen_attempts:
                    _log.critical(
                        "Kamera nach %d Neu-Oeffnungen ohne Bild -- beende den Prozess, "
                        "systemd startet ihn neu",
                        self._config.max_reopen_attempts,
                    )
                    self._on_give_up()
                    return
                await self._reopen(reopens)
                continue
            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def _reopen(self, attempt: int) -> None:
        """Gibt die Kamera frei und oeffnet sie mit frischem Worker neu.

        Der alte Worker haengt womoeglich noch in `capture_array()`; er wird
        samt Executor zurueckgelassen. Ein Fehlschlag laesst `_camera` auf
        `None`, der naechste Durchlauf eskaliert dann weiter.
        """
        _log.warning(
            "Kamera haengt, oeffne sie neu (Versuch %d/%d)",
            attempt,
            self._config.max_reopen_attempts,
        )
        camera, self._camera = self._camera, None
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if camera is not None:
            await self._close_in_own_thread(camera)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-camera")
        loop = asyncio.get_running_loop()
        try:
            self._camera = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._open_camera),
                timeout=self._config.frame_timeout_s + self._config.warmup_s,
            )
        except Exception:
            _log.exception("Kamera laesst sich nicht neu oeffnen")
            return
        _log.info("Kamera neu geoeffnet")

    async def _close_in_own_thread(self, camera: Any) -> None:
        """Schliesst die Kamera in einem Wegwerf-Thread, mit Timeout.

        Nicht im Kamera-Worker: der kann in `capture_array()` haengen, ein
        `close` stuende dann ewig hinter ihm an. Haengt auch das Schliessen,
        bleibt dieser Thread eben zurueck.
        """
        closer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-camera-close")
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(closer, self._close_camera, camera),
                timeout=self._config.frame_timeout_s,
            )
        except Exception:
            _log.exception("Kamera liess sich nicht sauber schliessen")
        finally:
            closer.shutdown(wait=False, cancel_futures=True)

    async def close(self) -> None:
        """Stoppt den Capture-Loop und gibt die Kamera frei. Idempotent."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        if self._camera is not None:
            camera, self._camera = self._camera, None
            await self._close_in_own_thread(camera)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _close_camera(self, camera: Any) -> None:
        if self._config.use_picamera:
            camera.stop()
            camera.close()
        else:
            camera.release()
