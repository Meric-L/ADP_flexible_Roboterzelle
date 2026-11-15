"""On-disk measurement log of `MeasureTagReference`.

Each measurement gets one folder `<store>/<YYYYmmdd-HHMMSS>_tag<id>/` containing
`request.json`, `result.json`, `frame<k>.png` (raw images, lossless) and
`overlay.jpg` (debug view of the last image). At most `MAX_FOLDERS` folders are
kept; the oldest are deleted. Write errors are passed on by the caller only as
a warning: the measurement itself must not fail because of them.
"""

import json
import re
import shutil
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .raw_image import FILE_STAMP_FORMAT

#: Maximum number of log folders kept on disk.
MAX_FOLDERS = 50
OVERLAY_NAME = "overlay.jpg"
_FOLDER_PATTERN = re.compile(r"^\d{8}-\d{6}_tag\d+(_\d+)?$")


def write_protocol(
    directory: Path,
    tag_id: int,
    request_json: str,
    result: dict,
    frames: Sequence[Any],
    overlay: Any | None,
    now: datetime | None = None,
) -> Path:
    """Create the folder, write all files into it, and return the folder.

    Raises `OSError` on write errors. If the folder name already exists (two
    measurements in the same second), `_2`, `_3`, ... is appended.
    """
    import cv2

    stamp = (now or datetime.now()).strftime(FILE_STAMP_FORMAT)
    base = f"{stamp}_tag{int(tag_id)}"
    folder = directory / base
    counter = 2
    while folder.exists():
        folder = directory / f"{base}_{counter}"
        counter += 1
    folder.mkdir(parents=True)
    (folder / "request.json").write_text(request_json, encoding="utf-8")
    (folder / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for index, frame in enumerate(frames, start=1):
        if not cv2.imwrite(str(folder / f"frame{index}.png"), frame):
            raise OSError(f"frame{index}.png konnte nicht geschrieben werden")
    if overlay is not None and not cv2.imwrite(str(folder / OVERLAY_NAME), overlay):
        raise OSError(f"{OVERLAY_NAME} konnte nicht geschrieben werden")
    prune(directory, protect=folder)
    return folder


def _own_folders(directory: Path) -> list[Path]:
    """Return this module's log folders, newest first (by mtime, name as tie-breaker).

    Sorted by mtime, not by name: the Pi has no RTC, so the clock may be wrong
    after a start. Foreign folders are ignored.
    """
    found = []
    for path in directory.iterdir():
        if _FOLDER_PATTERN.match(path.name) and path.is_dir():
            try:
                found.append((path.stat().st_mtime, path.name, path))
            except OSError:
                continue
    return [item[2] for item in sorted(found, reverse=True)]


def prune(directory: Path, keep: int = MAX_FOLDERS, protect: Path | None = None) -> None:
    """Delete the oldest log folders until at most `keep` remain.

    `protect` (the folder just created) is never deleted; foreign folders are not either.
    """
    folders = [path for path in _own_folders(directory) if path != protect]
    # The protected folder counts toward the limit but is always kept.
    for old in folders[max(0, keep - (1 if protect is not None else 0)) :]:
        shutil.rmtree(old, ignore_errors=True)


def latest_overlay(directory: Path | None) -> Path | None:
    """Return the `overlay.jpg` of the NEWEST measurement, otherwise `None`.

    If the newest measurement has no image (e.g. the tag was not seen), there is
    no image: an older one from another measurement would be misleading.
    """
    if directory is None or not directory.is_dir():
        return None
    folders = _own_folders(directory)
    if not folders:
        return None
    candidate = folders[0] / OVERLAY_NAME
    return candidate if candidate.is_file() else None
