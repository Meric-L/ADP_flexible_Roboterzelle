"""Use-Case-Foto: PNG plus Metadaten-JSON pro Versuch (OPC-UA-Methode `SaveUseCaseImage`).

Ablage: `<base_dir>/reihe_<NNN>/versuch_<NNN>.png` und `versuch_<NNN>.json`,
bei vorliegender Messung zusaetzlich `versuch_<NNN>_messung.png` (Kontrollbild).
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

#: Schema-Kennung in der JSON-Datei neben jedem Bild.
SCHEMA = "adp.usecase.trial/1"

def next_trial_number(directory: Path, stem: str = "versuch") -> int:
    """Hoechste vorhandene Bildnummer in `directory` plus 1 (leer/fehlend: 1).

    Zaehlt `<stem>_NNN.png` und `<stem>_NNN.json`; Luecken werden nicht
    aufgefuellt.
    """
    pattern = re.compile(rf"^{re.escape(stem)}_(\d+)\.(?:png|json)$")
    highest = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            match = pattern.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def next_series_number(base_dir: Path, folder: str) -> int:
    """Hoechste vorhandene Ordnernummer `<folder>_NNN` in `base_dir` plus 1 (leer/fehlend: 1)."""
    pattern = re.compile(rf"^{re.escape(folder)}_(\d+)$")
    highest = 0
    if Path(base_dir).is_dir():
        for entry in Path(base_dir).iterdir():
            match = pattern.match(entry.name)
            if match and entry.is_dir():
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
    measurement: dict | None = None,
    annotated: Any = None,
    *,
    folder: str = "reihe",
    stem: str = "versuch",
    series_key: str = "reihe",
    number_key: str = "versuch",
    schema: str = SCHEMA,
) -> tuple[Path, int]:
    """Schreibt `image` als PNG samt JSON; liefert (PNG-Pfad, Bildnummer).

    Ablage `<base_dir>/<folder>_<series>/<stem>_<n>.*`; `series_key`/`number_key`
    sind die Schluessel der Nummern in der JSON, `schema` ihre Kennung. Die
    Vorgaben ergeben das Use-Case-Foto (`reihe_NNN/versuch_NNN.*`).

    `measurement` (Bildmessung, vor dem Speichern berechnet) steht unter
    "messung" in der JSON -- so wird die Datei weiter in einem Zug exklusiv
    angelegt und nie nachtraeglich umgeschrieben. `annotated` ist das
    Kontrollbild, es kommt als `versuch_NNN_messung.png` daneben (best effort:
    ein Fehler dabei laesst das Speichern nicht scheitern).

    Nie ueberschreibend: die Dateien werden exklusiv angelegt, bei einer
    Kollision (paralleler Aufruf) wird die naechste Nummer genommen. Wirft
    `ValueError` bei `series < 1`, `OSError` bei Schreibfehlern.
    """
    if series < 1:
        raise ValueError(f"Reihe muss >= 1 sein, war {series}")
    # Erst hier: der Rest des Servers soll ohne OpenCV importierbar bleiben.
    import cv2

    directory = base_dir / f"{folder}_{series:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    key, value = _client_section(client_meta)

    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError("Bild konnte nicht als PNG kodiert werden")
    png_bytes = encoded.tobytes()

    n = next_trial_number(directory, stem)
    while True:
        png_path = directory / f"{stem}_{n:03d}.png"
        json_path = directory / f"{stem}_{n:03d}.json"
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
        "schema": schema,
        series_key: series,
        number_key: n,
        "bild": png_path.name,
        "gespeichert": stamp,
        "pi": pi_meta,
        key: value,
    }
    if measurement is not None:
        document["messung"] = measurement
    try:
        with open(json_path, "x", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, ensure_ascii=False, default=str)
            fh.write("\n")
    except OSError:
        png_path.unlink(missing_ok=True)
        raise
    if annotated is not None:
        ok, control = cv2.imencode(".png", annotated)
        if ok:
            try:
                with open(directory / f"{stem}_{n:03d}_messung.png", "xb") as fh:
                    fh.write(control.tobytes())
            except OSError:
                pass
    return png_path, n
