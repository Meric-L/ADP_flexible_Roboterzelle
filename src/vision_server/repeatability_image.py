"""Wiederholgenauigkeit: PNG plus Metadaten-JSON je Aufnahme (OPC-UA-Methode `SaveRepeatabilityImage`).

Ablage: `<base_dir>/<session>/<NN_phase>/<praefix>_<NNN>.png` und `.json`, z. B.
`s1/02_rauschen/r_003.png`. Dateien werden nie ueberschrieben.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

#: Schema-Kennung in der JSON-Datei neben jedem Bild.
SCHEMA = "adp.repeatability.image/1"

#: Erlaubte Session-Namen.
SESSION_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")

#: Phase -> (Ordnername, Dateipraefix).
PHASES: dict[str, tuple[str, str]] = {
    "warmlauf": ("01_warmlauf", "w"),
    "rauschen": ("02_rauschen", "r"),
    "zyklen_auto": ("03_zyklen_auto", "a"),
    "zyklen_hand": ("04_zyklen_hand", "h"),
}


def next_image_number(directory: Path, prefix: str) -> int:
    """Hoechste vorhandene Nummer von `<prefix>_NNN.png/.json` plus 1 (leer/fehlend: 1)."""
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)\.(?:png|json)$")
    highest = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            match = pattern.match(entry.name)
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


def save_repeatability_image(
    image: Any,
    base_dir: Path,
    session: str,
    phase: str,
    client_meta: str,
    pi_meta: dict,
    measurement: dict | None,
    now: datetime | None = None,
) -> tuple[Path, int]:
    """Schreibt `image` verlustfrei als PNG samt JSON; liefert (PNG-Pfad, Nummer).

    Das Bild bleibt unveraendert (Graustufe bleibt Graustufe). Nie
    ueberschreibend: exklusives Anlegen, bei Kollision die naechste Nummer.
    Wirft `ValueError` bei ungueltiger Session/Phase, `OSError` bei
    Schreibfehlern.
    """
    if not isinstance(session, str) or not SESSION_RE.match(session):
        raise ValueError(f"Ungueltige Session: {session!r}")
    if phase not in PHASES:
        raise ValueError(f"Ungueltige Phase: {phase!r}")
    import cv2

    folder, prefix = PHASES[phase]
    directory = Path(base_dir) / session / folder
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    key, value = _client_section(client_meta)

    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError("Bild konnte nicht als PNG kodiert werden")
    png_bytes = encoded.tobytes()

    n = next_image_number(directory, prefix)
    while True:
        png_path = directory / f"{prefix}_{n:03d}.png"
        json_path = directory / f"{prefix}_{n:03d}.json"
        try:
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
        "session": session,
        "phase": phase,
        "index": n,
        "bild": png_path.name,
        "gespeichert": stamp,
        "pi": pi_meta,
        key: value,
        "messung": measurement,
    }
    try:
        with open(json_path, "x", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, ensure_ascii=False, default=str)
            fh.write("\n")
    except OSError:
        png_path.unlink(missing_ok=True)
        raise
    return png_path, n
