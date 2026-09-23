"""Eine Kamera, ein Capture-Loop, mehrere Leser.

Erkennung (`detection/apriltag.py`) und Livestream-Publisher
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
Erkennungsquelle und Stream-Publisher kennen den konkreten Backend-Typ nicht.

`picamera2`/`pyrealsense2`/`cv2` werden erst in `open()` importiert, damit ein
Server ohne `camera_stream`-Konfiguration (z. B. lokale Entwicklung, Tests)
ohne diese Abhaengigkeiten startet.

Watchdog: `capture_array()` kann ohne jede Fehlermeldung ewig haengen, wenn
libcamera keinen Frame mehr liefert (so auf Pi 1 am 2026-09-22 per `py-spy`
beobachtet). Der Capture-Loop begrenzt deshalb jede Aufnahme, oeffnet die
Kamera bei Haengern neu und beendet als letztes Mittel den Prozess, damit
systemd ihn neu startet. Das gilt fuer alle Backends gleich -- auch
`wait_for_frames()` der RealSense laeuft durch denselben Timeout. Ein
haengender Worker-Thread laesst sich in Python nicht abbrechen -- er wird
zurueckgelassen, nicht beendet.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .aio import cancel_and_wait
from .profiles import CAMERA_BACKENDS, CameraStreamConfig

_log = logging.getLogger(__name__)


#: Hauptstrom der Pi-Kamera. Picamera2 legt `RGB888` im Speicher als B,G,R ab
#: -- das ist bereits OpenCV-BGR, anders als der Picamera2-Standard `XBGR8888`
#: (R,G,B,X), der je Frame eine Farbumrechnung brauchte. Bei 12 MP kostete die
#: allein einen guten Teil eines Kerns.
PICAMERA2_MAIN_FORMAT = "RGB888"
#: Der `lores`-Strom kann auf dem Pi 4 nur YUV420 (Pi 5: auch RGB).
PICAMERA2_PREVIEW_FORMAT = "YUV420"


@dataclass(frozen=True)
class CameraFrame:
    """Ein aufgenommener Frame (BGR, wie von OpenCV erwartet) mit Zeitstempel.

    `image` ist immer das volle Bild -- Erkennung und Kalibrierung rechnen
    darauf. `preview` ist dasselbe Bild klein, vom Kamera-ISP skaliert, fuer
    Livestream und Overlay; `None`, wenn das Backend keinen zweiten Strom hat.
    """

    image: Any
    timestamp: float
    preview: Any = None


@dataclass(frozen=True)
class CameraStatus:
    """Momentaufnahme dessen, was der Watchdog ohnehin schon weiss.

    Eine Datenklasse statt sieben Properties: wer den Zustand auswertet, soll
    ein in sich stimmiges Bild sehen und nicht zwischen zwei Abfragen einen
    Reopen erwischen. `camera_health.py` macht daraus `DeviceHealth` nach
    OPC 40100-2; diese Datei bleibt frei von OPC UA.
    """

    #: Der Capture-Loop laeuft (entspricht `is_open`).
    running: bool
    #: Ein Kamera-Handle ist offen. Waehrend `_reopen` kurzzeitig False.
    has_handle: bool
    #: Ergebnis der letzten Aufnahme: "none" | "ok" | "error" | "hung".
    last_outcome: str
    #: Aufnahmefehler in Folge, zurueckgesetzt von jedem "ok".
    consecutive_failures: int
    #: Neu-Oeffnungen ohne einen Frame dazwischen; 0 heisst "gesund".
    reopen_attempts: int
    #: Das Reopen-Budget ist erschoepft, der Prozess beendet sich gleich.
    gave_up: bool
    #: Alter des neuesten Frames in Sekunden, `None` vor dem ersten Capture.
    frame_age_s: float | None


def yuv420_to_bgr(array: Any, size: tuple[int, int]) -> Any:
    """Wandelt einen Picamera2-`YUV420`-Puffer (I420) in ein BGR-Bild `size` um.

    Der Puffer hat die Form `(hoehe * 3 / 2, stride)`; `stride` kann wegen der
    Zeilenausrichtung breiter sein als das Bild. Die Farbebenen liegen dann mit
    `stride / 2` im selben Raster, die Umrechnung auf voller `stride`-Breite
    ist also stimmig -- danach wird auf die echte Breite zugeschnitten.
    """
    import cv2

    width, height = int(size[0]), int(size[1])
    bgr = cv2.cvtColor(array, cv2.COLOR_YUV2BGR_I420)
    if bgr.shape[0] != height:
        raise ValueError(
            f"YUV420-Puffer passt nicht zu {width}x{height}: Form {tuple(array.shape)}"
        )
    return bgr[:, :width] if bgr.shape[1] > width else bgr


def realsense_color_profiles(device: Any) -> set[tuple[int, int, int, str]]:
    """Farb-Stream-Profile einer RealSense als `(breite, hoehe, fps, format)`.

    `device` ist ein Eintrag aus `rs.context().query_devices()`. Fragt nur die
    Faehigkeiten ab und oeffnet keine Pipeline. Genutzt von der Fehlermeldung
    in `_open_realsense` und von `tools/list_realsense_profiles.py`.
    """
    import pyrealsense2 as rs

    profiles = set()
    for sensor in device.query_sensors():
        for profile in sensor.get_stream_profiles():
            if profile.stream_type() != rs.stream.color:
                continue
            video = profile.as_video_stream_profile()
            profiles.add((video.width(), video.height(), profile.fps(), profile.format().name))
    return profiles


def _list_realsense_color_profiles() -> str:
    """Fragt die angeschlossene RealSense nach ihren Farb-Stream-Profilen.

    Rein diagnostisch fuer die Fehlermeldung in `_open_realsense` -- darf
    selbst nie werfen, sonst verschluckt sie den eigentlichen Fehler.
    """
    try:
        import pyrealsense2 as rs

        devices = rs.context().query_devices()
        if len(devices) == 0:
            return "keine RealSense gefunden (Kabel/USB-Port pruefen)"
        profiles = realsense_color_profiles(devices[0])
        if not profiles:
            return "Kamera gefunden, aber keine Farb-Profile gemeldet"
        return ", ".join(
            f"{w}x{h}@{fps}fps({fmt})" for w, h, fps, fmt in sorted(profiles)
        )
    except Exception as error:  # noqa: BLE001 -- rein diagnostisch, siehe Docstring
        return f"Profile nicht abrufbar ({error})"


def exit_process() -> None:
    """Beendet den Prozess hart, damit systemd (`Restart=always`) neu startet.

    `os._exit` statt `sys.exit`: ein normales Beenden wartet beim Shutdown auf
    alle Executor-Threads -- und damit auf den, der in `capture_array()`
    haengt. Der Prozess wuerde nie enden.
    """
    logging.shutdown()
    os._exit(1)


def picamera2_video_configuration(config: CameraStreamConfig) -> dict[str, Any]:
    """Argumente fuer `Picamera2.create_video_configuration()` aus der Config.

    Hauptstrom in voller `resolution` fuer Jobs und Kalibrierung; mit
    `preview_resolution` zusaetzlich ein `lores`-Strom fuer den Livestream,
    den der ISP aus demselben Frame skaliert.
    """
    arguments: dict[str, Any] = {
        "main": {"size": tuple(config.resolution), "format": PICAMERA2_MAIN_FORMAT},
    }
    if config.preview_resolution is not None:
        preview = tuple(config.preview_resolution)
        if preview[0] > config.resolution[0] or preview[1] > config.resolution[1]:
            raise ValueError(
                f"preview_resolution {preview} ist groesser als resolution "
                f"{tuple(config.resolution)} -- der lores-Strom darf nur kleiner sein"
            )
        arguments["lores"] = {"size": preview, "format": PICAMERA2_PREVIEW_FORMAT}
    if config.buffer_count is not None:
        arguments["buffer_count"] = int(config.buffer_count)
    return arguments


class SharedCamera:
    """Haelt eine Kamera offen und stellt den jeweils neuesten Frame bereit."""

    def __init__(
        self,
        config: CameraStreamConfig,
        *,
        on_give_up: Callable[[], Awaitable[None] | None] = exit_process,
    ) -> None:
        self._config = config
        self._camera: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._loop_task: asyncio.Task | None = None
        self._latest: CameraFrame | None = None
        #: Austauschbar fuer Tests, die den Prozess nicht beenden duerfen, und
        #: fuer den Runner, der vorher noch FAILURE veroeffentlichen will.
        self._on_give_up = on_give_up
        #: Zaehlerstaende des Watchdogs. Bewusst auf Instanzebene und nicht als
        #: lokale Variablen im Capture-Loop: sonst kennt nur die Schleife den
        #: Zustand, und `status()` muesste ihn ein zweites Mal herleiten.
        self._failures = 0
        self._reopens = 0
        self._last_outcome = "none"
        self._gave_up = False
        #: Groesse des `lores`-Stroms, sobald Picamera2 damit konfiguriert ist;
        #: sonst `None` und jede Aufnahme liefert nur `image`.
        self._preview_size: tuple[int, int] | None = None

    @property
    def latest_frame(self) -> CameraFrame | None:
        """Der zuletzt aufgenommene Frame, oder `None` vor dem ersten Capture."""
        return self._latest

    @property
    def is_open(self) -> bool:
        """Ob der Capture-Loop laeuft."""
        return self._loop_task is not None

    def status(self, now: float) -> CameraStatus:
        """Zustand zum Zeitpunkt `now` (derselbe `loop.time()`-Zeitstrahl).

        `now` wird uebergeben statt selbst geholt: `CameraFrame.timestamp`
        kommt aus `loop.time()`, und nur der Aufrufer weiss, ob er auf
        demselben Loop laeuft. So bleibt die Methode synchron und ohne
        laufenden Loop testbar.
        """
        latest = self._latest
        return CameraStatus(
            running=self.is_open,
            has_handle=self._camera is not None,
            last_outcome=self._last_outcome,
            consecutive_failures=self._failures,
            reopen_attempts=self._reopens,
            gave_up=self._gave_up,
            frame_age_s=None if latest is None else now - latest.timestamp,
        )

    def set_give_up_handler(
        self, handler: Callable[[], Awaitable[None] | None]
    ) -> None:
        """Ersetzt, was beim endgueltigen Aufgeben passiert.

        Noetig, weil die Kamera in `build_detection_sources` entsteht -- lange
        bevor es einen Zustandsknoten gibt, in den sich ein letztes FAILURE
        schreiben liesse.
        """
        self._on_give_up = handler

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
            camera.create_video_configuration(**picamera2_video_configuration(self._config))
        )
        camera.start()
        self._preview_size = (
            tuple(self._config.preview_resolution)
            if self._config.preview_resolution is not None
            else None
        )
        return camera

    def _open_realsense(self) -> Any:
        """Startet eine RealSense-Pipeline auf dem Farb-Stream (BGR8).

        `pipeline.start()` wirft `RuntimeError("Couldn't resolve requests")`,
        wenn die angeschlossene Kamera das angeforderte Profil (Aufloesung +
        fps + Format) nicht unterstuetzt -- z. B. weil die auf dem Pi noetige
        RSUSB-Backend-Anbindung die Bandbreite begrenzt. Im Fehlerfall listen
        wir die tatsaechlich unterstuetzten Farb-Profile ins Log, statt blind
        weiter zu raten.
        """
        import pyrealsense2 as rs

        width, height = self._config.realsense_resolution
        fps = self._config.realsense_fps
        rs_config = rs.config()
        rs_config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        pipeline = rs.pipeline()
        try:
            pipeline.start(rs_config)
        except RuntimeError:
            _log.error(
                "RealSense lehnt %dx%d@%dfps (bgr8) ab. Tatsaechlich "
                "unterstuetzte Farb-Profile dieser Kamera: %s",
                width, height, fps, _list_realsense_color_profiles(),
            )
            raise
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

    def _read_frames(self) -> tuple[Any, Any]:
        """Laeuft im Kamera-Worker-Thread: `(volles Bild, kleines Bild | None)`.

        Mit `lores`-Strom holt ein einziger Request beide Bilder -- sie
        stammen damit garantiert aus derselben Aufnahme, und das Overlay zeigt
        nie ein anderes Bild als das, das der Job sieht. `make_array` kopiert,
        der Request geht also sofort an die Kamera zurueck.
        """
        if self._preview_size is None:
            return self._read_frame(), None
        request = self._camera.capture_request()
        try:
            image = request.make_array("main")
            preview = request.make_array("lores")
        finally:
            request.release()
        return image, yuv420_to_bgr(preview, self._preview_size)

    def _read_frame(self) -> Any:
        """Laeuft im Kamera-Worker-Thread: ein Frame in BGR."""
        backend = self._config.backend
        if backend == "picamera2":
            # Bereits BGR, siehe PICAMERA2_MAIN_FORMAT.
            return self._camera.capture_array("main")
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

    async def _capture_once(self, loop: asyncio.AbstractEventLoop) -> str:
        """Eine Aufnahme mit Timeout: `"ok"`, `"error"` oder `"hung"`."""
        if self._camera is None:
            # Die letzte Neu-Oeffnung ist gescheitert; nichts zu lesen.
            self._last_outcome = "hung"
            return "hung"
        try:
            image, preview = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._read_frames),
                timeout=self._config.frame_timeout_s,
            )
        except TimeoutError:
            _log.error(
                "Kamera liefert seit %.1f s kein Bild -- capture haengt",
                self._config.frame_timeout_s,
            )
            self._last_outcome = "hung"
            return "hung"
        except Exception:
            _log.exception("Kamera-Frame konnte nicht aufgenommen werden")
            self._last_outcome = "error"
            return "error"
        self._latest = CameraFrame(image=image, timestamp=loop.time(), preview=preview)
        self._last_outcome = "ok"
        return "ok"

    async def _capture_loop(self) -> None:
        """Nimmt Frames mit `capture_fps` auf, bis die Task abgebrochen wird.

        Eskalation: Fehler zaehlen, ab `max_capture_failures` (ein Haenger
        zaehlt sofort voll) die Kamera neu oeffnen, nach `max_reopen_attempts`
        Neu-Oeffnungen ohne einen Frame dazwischen aufgeben.
        """
        loop = asyncio.get_running_loop()
        interval = 1.0 / self._config.capture_fps
        while True:
            started = loop.time()
            outcome = await self._capture_once(loop)
            if outcome == "ok":
                self._failures = 0
                self._reopens = 0
            elif outcome == "hung":
                self._failures = self._config.max_capture_failures
            else:
                self._failures += 1
            if self._failures >= self._config.max_capture_failures:
                self._failures = 0
                self._reopens += 1
                if self._reopens > self._config.max_reopen_attempts:
                    _log.critical(
                        "Kamera nach %d Neu-Oeffnungen ohne Bild -- beende den Prozess, "
                        "systemd startet ihn neu",
                        self._config.max_reopen_attempts,
                    )
                    self._gave_up = True
                    # Der Handler darf asynchron sein: der Runner schreibt hier
                    # ein letztes FAILURE in die Anlagensicht, bevor der
                    # Prozess gerissen wird. Der Standardhandler ist synchron
                    # und kehrt ohnehin nie zurueck.
                    result = self._on_give_up()
                    if result is not None:
                        await result
                    return
                await self._reopen(self._reopens)
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
        await cancel_and_wait(self._loop_task)
        self._loop_task = None
        if self._camera is not None:
            camera, self._camera = self._camera, None
            await self._close_in_own_thread(camera)
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
