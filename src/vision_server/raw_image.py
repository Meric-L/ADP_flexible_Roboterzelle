"""Save a raw camera image as PNG (OPC UA method ``SaveRawImage``)."""

from datetime import datetime
from pathlib import Path
from typing import Any

#: Timestamp in file names (raw images, measurement log folders): ``YYYYmmdd-HHMMSS``.
FILE_STAMP_FORMAT = "%Y%m%d-%H%M%S"


def save_raw_image(
    image: Any, directory: Path, frame_id: str, now: datetime | None = None
) -> Path:
    """Write ``image`` losslessly to ``<directory>/<frame_id>_<time>.png``.

    The timestamp is ``YYYYmmdd-HHMMSS``. If the name already exists, ``_2``, ``_3``, ...
    is appended, so an existing file is never overwritten. The directory is created if
    needed. Raises ``OSError`` if writing fails.
    """
    # Imported only here: the rest of the server should remain importable without OpenCV.
    import cv2

    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime(FILE_STAMP_FORMAT)
    path = directory / f"{frame_id}_{stamp}.png"
    counter = 2
    while path.exists():
        path = directory / f"{frame_id}_{stamp}_{counter}.png"
        counter += 1
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Rohbild konnte nicht nach {path} geschrieben werden")
    return path
