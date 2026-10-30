"""Woher die Bilder kommen: Kamera, Bildordner oder Einzelbild.

Der Bildordner macht die ganze Pipeline ohne Hardware reproduzierbar: Jedes
CLI-Werkzeug nimmt `--source` entweder als Kamera oder als Verzeichnis, sodass
derselbe Code im Labor und in Tests laeuft.

`cv2`/`picamera2` werden erst beim Oeffnen importiert.
"""

import logging
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


class FrameSource(Protocol):
    """Liefert Bilder in BGR, wie OpenCV sie erwartet."""

    def read(self) -> Any | None:
        """Liefert das naechste Bild oder `None`, wenn keine mehr kommen."""
        ...

    def close(self) -> None: ...


class ImageFolderSource:
    """Liest Bilder aus einem Verzeichnis, nach Dateiname sortiert.

    Nach dem letzten Bild liefert `read()` `None` -- so enden CLI-Schleifen von
    selbst, statt auf eine Kamera zu warten, die es nicht gibt.
    """

    def __init__(self, folder: Path, *, loop: bool = False) -> None:
        self._paths = sorted(
            path
            for path in Path(folder).iterdir()
            if path.suffix.lower() in IMAGE_SUFFIXES
        )
        if not self._paths:
            raise FileNotFoundError(f"Keine Bilder in {folder} (erwartet {IMAGE_SUFFIXES})")
        self._index = 0
        self._loop = loop

    def __len__(self) -> int:
        return len(self._paths)

    def read(self) -> Any | None:
        """Naechstes lesbares Bild; unlesbare werden mit Warnung uebersprungen.

        Hoechstens ein voller Durchlauf je Aufruf: sind alle Bilder
        unlesbar, kommt `None` -- auch mit `loop=True`, wo die fruehere
        rekursive Fassung in einem `RecursionError` endete.
        """
        import cv2

        for _ in range(len(self._paths)):
            if self._index >= len(self._paths) and not self._loop:
                return None
            path = self._paths[self._index % len(self._paths)]
            self._index += 1
            image = cv2.imread(str(path))
            if image is not None:
                return image
            _log.warning("Bild nicht lesbar, uebersprungen: %s", path)
        return None

    def close(self) -> None:
        return None


class SingleImageSource(ImageFolderSource):
    """Ein einzelnes Bild, beliebig oft lesbar."""

    def __init__(self, path: Path, *, loop: bool = True) -> None:
        self._paths = [Path(path)]
        self._index = 0
        self._loop = loop


class VideoCaptureSource:
    """Kamera ueber `cv2.VideoCapture` -- Webcam oder USB-Kamera am PC."""

    def __init__(self, index: int = 0, resolution: tuple[int, int] | None = None) -> None:
        import cv2

        self._capture = cv2.VideoCapture(index)
        if not self._capture.isOpened():
            raise RuntimeError(f"Kamera konnte nicht geoeffnet werden (index={index})")
        if resolution is not None:
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(resolution[0]))
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(resolution[1]))

    def read(self) -> Any | None:
        ok, frame = self._capture.read()
        return frame if ok else None

    def close(self) -> None:
        self._capture.release()


class PiCameraSource:
    """Die Raspberry-Pi-Kamera ueber Picamera2, fuer CLI-Aufrufe auf dem Pi.

    Der Server nutzt diesen Weg **nicht**; er nutzt stattdessen die geteilte
    `SharedCamera` -- Picamera2 erlaubt je Kamera nur einen offenen Zugriff,
    und den haelt der Livestream.
    """

    def __init__(self, resolution: tuple[int, int] = (2028, 1520), warmup_s: float = 2.0) -> None:
        import time

        import cv2
        from picamera2 import Picamera2

        self._cv2 = cv2
        self._camera = Picamera2()
        self._camera.configure(
            self._camera.create_video_configuration(main={"size": tuple(resolution)})
        )
        self._camera.start()
        time.sleep(warmup_s)

    def read(self) -> Any | None:
        return self._cv2.cvtColor(self._camera.capture_array(), self._cv2.COLOR_RGB2BGR)

    def close(self) -> None:
        self._camera.stop()
        self._camera.close()


class RealSenseSource:
    """Intel RealSense ueber `pyrealsense2`, fuer CLI-Aufrufe auf dem Hand-Pi.

    Wie `PiCameraSource`: der Server nutzt diesen Pfad nicht, sondern die
    geteilte `SharedCamera` -- eine RealSense-Pipeline laesst pro Kamera nur
    einen offenen Zugriff zu, der Livestream haelt sie. Fuer eine
    Kalibrierfahrt muss der Server-Prozess deshalb kurz gestoppt sein.
    Konservative Default-Aufloesung/-fps wie in
    `vision_server.profiles.CameraStreamConfig` -- ueber die auf dem Pi
    noetige RSUSB/libuvc-Backend-Anbindung unterstuetzen nicht alle
    Kombinationen.
    """

    def __init__(
        self, resolution: tuple[int, int] = (640, 480), fps: int = 15, warmup_s: float = 2.0
    ) -> None:
        import time

        import numpy as np
        import pyrealsense2 as rs

        self._np = np
        width, height = resolution
        rs_config = rs.config()
        rs_config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        self._pipeline = rs.pipeline()
        self._pipeline.start(rs_config)
        time.sleep(warmup_s)

    def read(self) -> Any | None:
        frames = self._pipeline.wait_for_frames()
        color_frame = frames.get_color_frame()
        if not color_frame:
            return None
        return self._np.asanyarray(color_frame.get_data())

    def close(self) -> None:
        self._pipeline.stop()


def open_source(spec: str, *, resolution: tuple[int, int] | None = None, loop: bool = False):
    """Loest das `--source`-Argument der CLI-Werkzeuge auf.

    Erkannte Formen:

    * `camera` oder `camera:2`  -- `cv2.VideoCapture`
    * `picamera`              -- Picamera2 auf dem Pi
    * `realsense`             -- Intel RealSense ueber `pyrealsense2`
    * ein Verzeichnis         -- alle Bilder darin, sortiert
    * eine Bilddatei          -- genau dieses Bild
    """
    text = str(spec)
    if text == "picamera":
        return PiCameraSource(resolution or (2028, 1520))
    if text == "realsense":
        return RealSenseSource(resolution or (640, 480))
    if text == "camera" or text.startswith("camera:"):
        _, _, index = text.partition(":")
        return VideoCaptureSource(int(index or 0), resolution)
    path = Path(text)
    if path.is_dir():
        return ImageFolderSource(path, loop=loop)
    if path.is_file():
        return SingleImageSource(path)
    raise FileNotFoundError(
        f"--source '{spec}' ist weder 'camera[:n]', 'picamera', 'realsense', "
        "Verzeichnis noch Datei"
    )


def to_gray(image) -> Any:
    """Wandelt BGR in Graustufen um. Ein bereits einkanaliges Bild bleibt unveraendert."""
    import cv2

    import numpy as np

    array = np.asarray(image)
    if array.ndim == 2:
        return array
    return cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)


def image_size(image) -> tuple[int, int]:
    """Liefert `(Breite, Hoehe)` eines Bildes."""
    import numpy as np

    shape = np.asarray(image).shape
    return (int(shape[1]), int(shape[0]))


__all__ = [
    "FrameSource",
    "ImageFolderSource",
    "PiCameraSource",
    "RealSenseSource",
    "SingleImageSource",
    "VideoCaptureSource",
    "image_size",
    "open_source",
    "to_gray",
]
