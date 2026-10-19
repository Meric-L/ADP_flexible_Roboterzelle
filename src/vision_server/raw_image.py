"""Speichern eines Kamera-Rohbilds als PNG (OPC-UA-Methode `SaveRawImage`)."""

from datetime import datetime
from pathlib import Path
from typing import Any


def save_raw_image(
    image: Any, directory: Path, frame_id: str, now: datetime | None = None
) -> Path:
    """Schreibt `image` verlustfrei nach `<directory>/<frame_id>_<Zeit>.png`.

    Zeitstempel `YYYYmmdd-HHMMSS`. Existiert der Name schon, folgt `_2`, `_3`,
    ... -- eine vorhandene Datei wird nie ueberschrieben. Legt das Verzeichnis
    bei Bedarf an. Wirft `OSError`, wenn das Schreiben fehlschlaegt.
    """
    # Erst hier: der Rest des Servers soll ohne OpenCV importierbar bleiben.
    import cv2

    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = directory / f"{frame_id}_{stamp}.png"
    counter = 2
    while path.exists():
        path = directory / f"{frame_id}_{stamp}_{counter}.png"
        counter += 1
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Rohbild konnte nicht nach {path} geschrieben werden")
    return path
