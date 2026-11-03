"""Mess-Protokoll von `MeasureTagReference` auf Platte.

Je Messung ein Ordner `<Ablage>/<YYYYmmdd-HHMMSS>_tag<id>/` mit `request.json`,
`result.json`, `frame<k>.png` (Rohbilder, verlustfrei) und `overlay.jpg` (Debug-
Ansicht des letzten Bilds). Hoechstens `MAX_FOLDERS` Ordner, die aeltesten
werden geloescht. Fehler beim Schreiben wirft der Aufrufer nur als Warnung weiter:
die Messung selbst darf daran nicht scheitern.
"""

import json
import re
import shutil
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .raw_image import FILE_STAMP_FORMAT

#: So viele Protokollordner bleiben hoechstens liegen.
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
    """Legt den Ordner an und schreibt alles hinein; gibt den Ordner zurueck.

    Wirft `OSError` bei Schreibfehlern. Existiert der Ordnername schon (zwei
    Messungen in derselben Sekunde), folgt `_2`, `_3`, ...
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
    """Eigene Protokollordner, neueste zuerst (nach mtime, Namen als Gleichstand).

    Nach mtime, nicht nach Namen: der Pi hat keine RTC, die Uhr kann beim Start
    falsch stehen. Fremde Ordner zaehlen nicht.
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
    """Loescht die aeltesten eigenen Protokollordner, bis hoechstens `keep` bleiben.

    `protect` (der gerade angelegte Ordner) wird nie geloescht; fremde Ordner auch nicht.
    """
    folders = [path for path in _own_folders(directory) if path != protect]
    # Der geschuetzte Ordner zaehlt mit, bleibt aber immer.
    for old in folders[max(0, keep - (1 if protect is not None else 0)) :]:
        shutil.rmtree(old, ignore_errors=True)


def latest_overlay(directory: Path | None) -> Path | None:
    """`overlay.jpg` der NEUESTEN Messung, sonst `None`.

    Hat die neueste Messung kein Bild (z. B. Tag nicht gesehen), gibt es kein
    Bild: ein aelteres einer anderen Messung waere irrefuehrend.
    """
    if directory is None or not directory.is_dir():
        return None
    folders = _own_folders(directory)
    if not folders:
        return None
    candidate = folders[0] / OVERLAY_NAME
    return candidate if candidate.is_file() else None
