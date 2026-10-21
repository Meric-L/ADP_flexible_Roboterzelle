"""Mess-Protokoll von `MeasureTagReference` auf Platte.

Je Messung ein Ordner `<Ablage>/<YYYYmmdd-HHMMSS>_tag<id>/` mit `request.json`,
`result.json`, `frame<k>.png` (Rohbilder, verlustfrei) und `overlay.jpg` (Debug-
Ansicht des letzten Bilds). Hoechstens `MAX_FOLDERS` Ordner, die aeltesten
werden geloescht. Fehler beim Schreiben wirft der Aufrufer nur als Warnung weiter:
die Messung selbst darf daran nicht scheitern.
"""

import json
import shutil
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

#: So viele Protokollordner bleiben hoechstens liegen.
MAX_FOLDERS = 50
OVERLAY_NAME = "overlay.jpg"


def write_protocol(
    directory: Path,
    tag_id: int,
    request_json: str,
    result: dict,
    frames: Sequence[Any],
    overlay: Any | None,
    now: datetime | None = None,
) -> Path:
    """Legt den Ordner an und schreibt alles hinein; gibt den Ordner zurueck.

    Wirft `OSError` bei Schreibfehlern. Existiert der Ordnername schon (zwei
    Messungen in derselben Sekunde), folgt `_2`, `_3`, ...
    """
    import cv2

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
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
    prune(directory)
    return folder


def prune(directory: Path, keep: int = MAX_FOLDERS) -> None:
    """Loescht die aeltesten Protokollordner, bis hoechstens `keep` bleiben."""
    folders = sorted(path for path in directory.iterdir() if path.is_dir())
    for old in folders[: max(0, len(folders) - keep)]:
        shutil.rmtree(old, ignore_errors=True)


def latest_overlay(directory: Path | None) -> Path | None:
    """`overlay.jpg` des neuesten Protokollordners, der eines hat, sonst `None`."""
    if directory is None or not directory.is_dir():
        return None
    for folder in sorted((p for p in directory.iterdir() if p.is_dir()), reverse=True):
        candidate = folder / OVERLAY_NAME
        if candidate.is_file():
            return candidate
    return None
