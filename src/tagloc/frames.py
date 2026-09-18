"""Where images come from: camera, image folder, or single image.

The image folder is what makes the whole pipeline reproducible without
hardware: every CLI tool accepts `--source` as either a camera or a
directory, so the same code runs in the lab and in tests.

`cv2`/`picamera2` are imported only on open.
"""

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

_log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")


class FrameSource(Protocol):
    """Provide frames in BGR, as OpenCV expects them."""

    def read(self) -> Any | None:
        """Return the next frame, or `None` when there are no more."""
        ...

    def close(self) -> None: ...


class ImageFolderSource:
    """Read images from a directory, sorted by filename.

    After the last image, `read()` returns `None` -- so CLI loops terminate
    on their own instead of waiting for a camera that doesn't exist.
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

    @property
    def current_path(self) -> Path | None:
        if self._index == 0:
            return None
        return self._paths[(self._index - 1) % len(self._paths)]

    def read(self) -> Any | None:
        import cv2

        if self._index >= len(self._paths) and not self._loop:
            return None
        path = self._paths[self._index % len(self._paths)]
        self._index += 1
        image = cv2.imread(str(path))
        if image is None:
            _log.warning("Bild nicht lesbar, uebersprungen: %s", path)
            return self.read()
        return image

    def close(self) -> None:
        return None


class SingleImageSource(ImageFolderSource):
    """A single image, readable any number of times."""

    def __init__(self, path: Path, *, loop: bool = True) -> None:
        self._paths = [Path(path)]
        self._index = 0
        self._loop = loop


class VideoCaptureSource:
    """Camera via `cv2.VideoCapture` -- webcam or USB camera on a PC."""

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

    The server does **not** use this path; it uses the shared `SharedCamera`
    instead -- Picamera2 allows only one open access per camera, and the
    livestream holds it.
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


class SharedCameraSource:
    """Adapter over `vision_server.camera.SharedCamera`.

    Doesn't know the type, only its `latest_frame` property -- keeps
    `tagloc` free of a dependency on `vision_server`.
    """

    def __init__(self, camera: Any) -> None:
        self._camera = camera

    def read(self) -> Any | None:
        frame = self._camera.latest_frame
        return None if frame is None else frame.image

    def close(self) -> None:
        return None


def open_source(spec: str, *, resolution: tuple[int, int] | None = None, loop: bool = False):
    """Resolve the CLI tools' `--source` argument.

    Recognised forms:

    * `camera` or `camera:2`  -- `cv2.VideoCapture`
    * `picamera`              -- Picamera2 on the Pi
    * a directory             -- all images in it, sorted
    * an image file           -- that one image
    """
    text = str(spec)
    if text == "picamera":
        return PiCameraSource(resolution or (2028, 1520))
    if text == "camera" or text.startswith("camera:"):
        _, _, index = text.partition(":")
        return VideoCaptureSource(int(index or 0), resolution)
    path = Path(text)
    if path.is_dir():
        return ImageFolderSource(path, loop=loop)
    if path.is_file():
        return SingleImageSource(path)
    raise FileNotFoundError(
        f"--source '{spec}' ist weder 'camera[:n]', 'picamera', Verzeichnis noch Datei"
    )


def iter_frames(source, limit: int | None = None) -> Iterator[Any]:
    """Yield frames from a source until it's empty or `limit` is reached."""
    count = 0
    while limit is None or count < limit:
        frame = source.read()
        if frame is None:
            return
        count += 1
        yield frame


def to_gray(image) -> Any:
    """Convert BGR to grayscale. An already single-channel image is unchanged."""
    import cv2

    import numpy as np

    array = np.asarray(image)
    if array.ndim == 2:
        return array
    return cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)


def image_size(image) -> tuple[int, int]:
    """Return `(width, height)` of an image."""
    import numpy as np

    shape = np.asarray(image).shape
    return (int(shape[1]), int(shape[0]))


__all__ = [
    "FrameSource",
    "ImageFolderSource",
    "PiCameraSource",
    "SharedCameraSource",
    "SingleImageSource",
    "VideoCaptureSource",
    "image_size",
    "iter_frames",
    "open_source",
    "to_gray",
]
