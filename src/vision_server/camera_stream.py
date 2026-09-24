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

    **Keine Ueberwachung hier.** Ob die Kamera lebt, beantwortet allein
    `Health/DeviceHealth` nach OPC 40100-2 (`camera_health.py`), gespeist aus
    dem Watchdog in `camera.py`. Dieser Publisher hat frueher zusaetzlich das
    Bildalter geprueft und den Knoten geleert -- zwei Wahrheiten ueber
    dieselbe Frage, von denen eine am Standard vorbeilief. Bei haengender
    Kamera steht das Livebild deshalb jetzt still; dass es steht, sagt die
    Zustandsampel, nicht das fehlende Bild.
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
        #: Der letzte Overlay-Lauf; haengt er noch, wird kein neuer gestartet.
        self._overlay_run: asyncio.Future | None = None
        #: Das zuletzt kodierte Bild (Base64) samt laufender Nummer. Knoten und
        #: MJPEG-Server lesen beide hier -- jeder Frame wird nur einmal
        #: markiert und kodiert, egal wie viele Abnehmer es gibt.
        self._latest: str | None = None
        self._seq = 0
        #: (Frame-Zeitstempel, Modus) des zuletzt kodierten Bildes; gleicher
        #: Schluessel = kein neuer Frame, nichts neu zu kodieren.
        self._encoded_key: tuple | None = None
        self._frame_ready = asyncio.Condition()
        #: Laufende MJPEG-Verbindungen; ohne sie tickt der Loop nur mit
        #: `stream_fps`, um die CPU des Pi zu schonen.
        self._viewers = 0
        self._next_node_write = 0.0

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

    @property
    def latest(self) -> tuple[int, str] | None:
        """Laufende Nummer und Base64-JPEG des neuesten Bildes.

        Bewusst ohne Altersgrenze: ob die Kamera lebt, sagt `DeviceHealth`
        nach OPC 40100-2 (camera_health.py), nicht dieser Publisher. Ein
        Standbild ist ein Bild, kein Gesundheitssignal -- zwei Wahrheiten
        darueber waeren eine zu viel.
        """
        if self._latest is None:
            return None
        return self._seq, self._latest

    @contextlib.asynccontextmanager
    async def viewer(self):
        """Meldet einen MJPEG-Zuschauer an; solange er da ist, tickt der Loop schneller."""
        self._viewers += 1
        try:
            yield
        finally:
            self._viewers -= 1

    async def next_frame(self, after_seq: int, timeout: float) -> tuple[int, str] | None:
        """Wartet auf ein Bild mit hoeherer Nummer als `after_seq`.

        `None` nach `timeout` -- etwa, weil die Kamera haengt und der Frame
        veraltet ist. Wer langsam liest, verpasst Bilder statt einen Rueckstau
        aufzubauen: es gibt immer nur das neueste.
        """
        async with self._frame_ready:
            try:
                await asyncio.wait_for(
                    self._frame_ready.wait_for(
                        lambda: self.latest is not None and self._seq > after_seq
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                return None
            return self.latest

    async def _annotate(self, loop: asyncio.AbstractEventLoop, image, mode: str):
        """Overlay mit Timeout; bei Timeout oder noch laufendem Lauf das Rohbild.

        Gibt `(bild, vollstaendig)` zurueck; `False` heisst, das Rohbild ging
        ersatzweise raus und darf nicht als fertig markiertes Bild gelten.

        Ein haengender Lauf blockiert einen Thread des Default-Executors, der
        sich nicht abbrechen laesst. Deshalb startet kein neuer, bevor er
        fertig ist -- sonst liefe der Executor Tick fuer Tick voll.
        """
        if self._overlay_run is not None and not self._overlay_run.done():
            return image, False
        # In the worker thread: detection and drawing are blocking and have no
        # business on the event loop. The annotator works on a copy.
        run = loop.run_in_executor(None, self._annotator.annotate, image, mode)
        # Holt die Exception eines abgehaengten Laufs ab, sonst meldet asyncio
        # "Future exception was never retrieved".
        run.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._overlay_run = run
        try:
            annotated = await asyncio.wait_for(
                asyncio.shield(run), timeout=self._config.overlay_timeout_s
            )
        except TimeoutError:
            _log.warning(
                "Overlay laenger als %.1f s, sende das Rohbild",
                self._config.overlay_timeout_s,
            )
            return image, False
        return annotated, True

    async def _encoded(self, loop: asyncio.AbstractEventLoop, frame) -> str | None:
        """Markiert und kodiert einen Frame, aber nur, wenn er neu ist."""
        mode = await self._read_mode()
        key = (frame.timestamp, mode)
        if key == self._encoded_key and self._latest is not None:
            return self._latest
        # "apriltag" und "off" gehen vom vollen Frame aus, nicht von der
        # kleinen ISP-Vorschau:
        # - "apriltag" bekommt den vollen Frame als Eingabe, verkleinert ihn
        #   aber selbst als ERSTEN Schritt in `AprilTagStreamAnnotator.
        #   annotate()` auf `detection_max_width` -- Erkennung UND Zeichnen
        #   laufen auf dieser kleineren Leinwand (siehe Kommentar dort:
        #   Job-Erkennung und Kalibrierung bleiben unveraendert auf voller
        #   Aufloesung, nur der Demo-Livestream ist guenstiger). Frueher lief
        #   die Erkennung hier zusaetzlich auf dem vollen Frame -- auf pi1
        #   laut py-spy ~102% einer Core und Ursache fuer Job-Timeouts.
        # - "off" ist das Debug-Rohbild: soll genau das zeigen, was der Pi
        #   tatsaechlich sieht (Fokus, Belichtung, Bildausschnitt pruefen),
        #   nicht die verkleinerte Vorschau. Kostet kaum mehr als vorher --
        #   ohne Erkennung/Overlay ist hier nur der groessere Encode neu.
        # "calibration" bleibt beim kleinen Vorschaubild: dort geht es nicht
        # um Erkennungstreue, und die Session hat ohnehin ein eigenes, separat
        # getuntes Downscale vor `detect_board` (`detection_max_width`).
        if mode in ("apriltag", "off"):
            source = frame.image
        else:
            source = getattr(frame, "preview", None)
            if source is None:
                source = frame.image
        image, complete = source, True
        if mode != "off" and self._annotator is not None:
            image, complete = await self._annotate(loop, source, mode)
        if self._config.max_stream_width is not None:
            # Nach dem Overlay und nur fuers Publizieren -- beide Wege
            # (MJPEG und Knoten) bekommen dasselbe verkleinerte Bild. Im
            # "apriltag"-Modus hat `annotate()` selbst schon auf
            # `detection_max_width` (== `max_stream_width`) verkleinert --
            # `_resize_for_stream` ist hier also ein No-Op (Breite passt
            # bereits), fuer "off" (voller Frame) macht es die eigentliche
            # Arbeit.
            image = await loop.run_in_executor(
                None, _resize_for_stream, image, self._config.max_stream_width
            )
        encoded = await loop.run_in_executor(
            None, self._encode_frame, image, self._config.jpeg_quality
        )
        if encoded is None:
            return None
        self._latest = encoded
        self._seq += 1
        # Ein Ersatz-Rohbild wird beim naechsten Tick neu versucht.
        self._encoded_key = key if complete else None
        async with self._frame_ready:
            self._frame_ready.notify_all()
        return encoded

    async def _publish_progress(self) -> None:
        """Schreibt den Kalibrier-Fortschritt, im Takt des Knotens.

        Unabhaengig vom gewaehlten `mode` -- der Fortschritt soll auch sichtbar
        sein, wenn der Viewer gerade "off" zeigt.
        """
        if self._progress_node is None or self._annotator is None:
            return
        try:
            progress = self._annotator.calibration_progress
            await self._progress_node.write_value(
                json.dumps(progress if progress is not None else {"running": False})
            )
        except Exception:
            _log.exception("Kalibrier-Fortschritt konnte nicht veroeffentlicht werden")

    async def _publish_loop(self) -> None:
        """Tickt mit `http_fps`, solange MJPEG-Zuschauer da sind, sonst mit `stream_fps`.

        Der Knoten wird unabhaengig davon hoechstens mit `stream_fps`
        beschrieben -- er ist der Rueckfallweg, kein Videokanal.
        """
        loop = asyncio.get_running_loop()
        node_interval = 1.0 / self._config.stream_fps
        live_interval = 1.0 / max(self._config.http_fps, self._config.stream_fps)
        while True:
            started = loop.time()
            node_due = started >= self._next_node_write
            frame = self._camera.latest_frame
            if frame is not None:
                try:
                    encoded = await self._encoded(loop, frame)
                    if encoded is not None and node_due:
                        await self._node.write_value(encoded)
                except Exception:
                    _log.exception("Kamera-Frame konnte nicht veroeffentlicht werden")
            if node_due:
                # Fester Takt statt "seit dem letzten Schreiben", sonst sinkt
                # die Rate bei schnellem Tick unter stream_fps.
                self._next_node_write = max(self._next_node_write + node_interval, started)
                await self._publish_progress()
            interval = live_interval if self._viewers else node_interval
            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
