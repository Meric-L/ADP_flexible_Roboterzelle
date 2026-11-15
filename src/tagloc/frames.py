"""Image sources: camera, image folder or single image.

A folder source makes the whole pipeline reproducible without hardware. Every
CLI tool takes ``--source`` as either a camera or a directory, so the same code
runs in the lab and in tests.

``cv2`` and ``picamera2`` are imported only when a source is opened.
"""

import logging
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


class FrameSource(Protocol):
    """Provides images in BGR, the format OpenCV expects."""

    def read(self) -> Any | None:
        """Return the next image, or ``None`` when no more are available."""
        ...

    def close(self) -> None: ...


class ImageFolderSource:
    """Read images from a directory, sorted by file name.

    After the last image, ``read()`` returns ``None``. CLI loops therefore end
    on their own instead of waiting for a camera that does not exist.
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
        """Return the next readable image; unreadable ones are skipped with a warning.

        At most one full pass per call. If all images are unreadable, return
        ``None``, also with ``loop=True``. The earlier recursive version ended
        in a ``RecursionError`` in that case.
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
    """A single image that can be read any number of times."""

    def __init__(self, path: Path, *, loop: bool = True) -> None:
        self._paths = [Path(path)]
        self._index = 0
        self._loop = loop


class VideoCaptureSource:
    """Camera via ``cv2.VideoCapture``: a webcam or USB camera on a PC."""

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
    """The Raspberry Pi camera via Picamera2, for CLI calls on the Pi.

    The server does **not** use this path. It uses the shared ``SharedCamera``
    instead: Picamera2 allows only one open handle per camera, and the live
    stream holds it.
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
    """Intel RealSense via ``pyrealsense2``, for CLI calls on the hand Pi.

    Like ``PiCameraSource``, the server does not use this path. It uses the
    shared ``SharedCamera`` instead: a RealSense pipeline allows only one open
    handle per camera, and the live stream holds it. For a calibration run the
    server process must therefore be stopped briefly.
    The default resolution and fps are conservative, matching
    ``vision_server.profiles.CameraStreamConfig``. The RSUSB/libuvc backend
    needed on the Pi does not support every combination.
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
    """Resolve the ``--source`` argument of the CLI tools to a frame source.

    Recognized forms:

    * ``camera`` or ``camera:2`` -- ``cv2.VideoCapture``
    * ``picamera`` -- Picamera2 on the Pi
    * ``realsense`` -- Intel RealSense via ``pyrealsense2``
    * a directory -- all images in it, sorted
    * an image file -- exactly that image
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
    """Convert BGR to grayscale. A single-channel image is returned unchanged."""
    import cv2

    import numpy as np

    array = np.asarray(image)
    if array.ndim == 2:
        return array
    return cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)


def image_size(image) -> tuple[int, int]:
    """Return the ``(width, height)`` of an image."""
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
