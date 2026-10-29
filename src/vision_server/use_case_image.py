"""Use-Case-Foto: PNG plus Metadaten-JSON pro Versuch (OPC-UA-Methode `SaveUseCaseImage`).

Ablage: `<base_dir>/reihe_<NNN>/versuch_<NNN>.png` und `versuch_<NNN>.json`.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

#: Schema-Kennung in der JSON-Datei neben jedem Bild.
SCHEMA = "adp.usecase.trial/1"

_TRIAL_RE = re.compile(r"^versuch_(\d+)\.(?:png|json)$")


def next_trial_number(directory: Path) -> int:
    """Hoechste vorhandene Versuchsnummer in `directory` plus 1 (leer/fehlend: 1).

    Zaehlt `versuch_NNN.png` und `versuch_NNN.json`; Luecken werden nicht
    aufgefuellt.
    """
    highest = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            match = _TRIAL_RE.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _client_section(client_meta: str) -> tuple[str, Any]:
    """`("client", Objekt)` bei gueltigem JSON-Objekt, sonst `("client_raw", Text)`."""
    try:
        parsed = json.loads(client_meta)
    except (TypeError, ValueError):
        return "client_raw", client_meta
    if isinstance(parsed, dict):
        return "client", parsed
    return "client_raw", client_meta


def save_use_case_image(
    image: Any,
    base_dir: Path,
    series: int,
    client_meta: str,
    pi_meta: dict,
    now: datetime | None = None,
) -> tuple[Path, int]:
    """Schreibt `image` als PNG samt JSON; liefert (PNG-Pfad, Versuchsnummer).

    Nie ueberschreibend: die Dateien werden exklusiv angelegt, bei einer
    Kollision (paralleler Aufruf) wird die naechste Nummer genommen. Wirft
    `ValueError` bei `series < 1`, `OSError` bei Schreibfehlern.
    """
    if series < 1:
        raise ValueError(f"Reihe muss >= 1 sein, war {series}")
    # Erst hier: der Rest des Servers soll ohne OpenCV importierbar bleiben.
    import cv2

    directory = base_dir / f"reihe_{series:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    key, value = _client_section(client_meta)

    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError("Bild konnte nicht als PNG kodiert werden")
    png_bytes = encoded.tobytes()

    n = next_trial_number(directory)
    while True:
        png_path = directory / f"versuch_{n:03d}.png"
        json_path = directory / f"versuch_{n:03d}.json"
        try:
            # "xb": schlaegt fehl, wenn die Datei existiert -> nie ueberschreiben.
            with open(png_path, "xb") as fh:
                fh.write(png_bytes)
        except FileExistsError:
            n += 1
            continue
        if json_path.exists():
            png_path.unlink(missing_ok=True)
            n += 1
            continue
        break

    document = {
        "schema": SCHEMA,
        "reihe": series,
        "versuch": n,
        "bild": png_path.name,
        "gespeichert": stamp,
        "pi": pi_meta,
        key: value,
    }
    try:
        with open(json_path, "x", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, ensure_ascii=False, default=str)
            fh.write("\n")
    except OSError:
        png_path.unlink(missing_ok=True)
        raise
    return png_path, n
