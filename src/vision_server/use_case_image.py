"""Use-case photo: PNG plus metadata JSON per trial (OPC UA method `SaveUseCaseImage`).

Layout: `<base_dir>/reihe_<NNN>/versuch_<NNN>.png` and `versuch_<NNN>.json`, and if a
measurement is available additionally `versuch_<NNN>_messung.png` (control image).
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

#: Schema identifier in the JSON file next to each image.
SCHEMA = "adp.usecase.trial/1"

_TRIAL_RE = re.compile(r"^versuch_(\d+)\.(?:png|json)$")


def next_trial_number(directory: Path) -> int:
    """Return the highest existing trial number in `directory` plus 1 (empty/missing: 1).

    Counts `versuch_NNN.png` and `versuch_NNN.json`; gaps are not filled.
    """
    highest = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            match = _TRIAL_RE.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def _client_section(client_meta: str) -> tuple[str, Any]:
    """Return `("client", object)` for a valid JSON object, else `("client_raw", text)`."""
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
) -> tuple[Path, int]:
    """Write `image` as PNG together with its JSON; return (PNG path, trial number).

    `measurement` (image measurement, computed before saving) is stored under
    "messung" in the JSON -- so the file is still created exclusively in one go and never
    rewritten afterwards. `annotated` is the control image, stored as
    `versuch_NNN_messung.png` next to it (best effort: an error there does not make the
    save fail).

    Never overwrites: files are created exclusively; on a collision (parallel call) the
    next number is taken. Raises `ValueError` if `series < 1`, `OSError` on write errors.
    """
    if series < 1:
        raise ValueError(f"Reihe muss >= 1 sein, war {series}")
    # Only here: the rest of the server should remain importable without OpenCV.
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
            # "xb": fails if the file exists -> never overwrite.
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
                with open(directory / f"versuch_{n:03d}_messung.png", "xb") as fh:
                    fh.write(control.tobytes())
            except OSError:
                pass
    return png_path, n
