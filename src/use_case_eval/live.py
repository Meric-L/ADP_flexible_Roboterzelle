"""Anbindung an `SaveUseCaseImage`: Messung ohne Ausnahmen, Aufkleber-Versatz, Intrinsik-Meta.

Alles hier ist defensiv: ein Messfehler darf das Speichern des Fotos nie
verhindern, er landet als `{"ok": false, "grund": ...}` in der Versuchs-JSON.
"""

import json
from pathlib import Path
from typing import Any

#: Name der optionalen Datei mit dem Aufkleber-Versatz im Use-Case-Ordner.
STICKER_OFFSET_FILE = "aufkleber_versatz.json"


def load_sticker_offset(base_dir: Path) -> tuple[float, float]:
    """Liest `<base_dir>/aufkleber_versatz.json` (`{"x_mm": .., "y_mm": ..}`); sonst (0, 0)."""
    path = Path(base_dir) / STICKER_OFFSET_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data["x_mm"]), float(data["y_mm"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0, 0.0


def write_sticker_offset(base_dir: Path, offset_mm: tuple[float, float]) -> Path:
    """Schreibt den Aufkleber-Versatz (Markersystem, mm) in `<base_dir>/aufkleber_versatz.json`."""
    path = Path(base_dir) / STICKER_OFFSET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"x_mm": offset_mm[0], "y_mm": offset_mm[1]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def intrinsics_meta(calibration: Any, source: str) -> dict:
    """`pi_meta.intrinsics`: tatsaechlich benutzte Kameramatrix und Verzeichnung samt Quelle.

    `source`: `calibration` (Kalibrierdatei), `realsense_factory` oder `placeholder`.
    """
    import numpy as np

    return {
        "source": source,
        "calibrationId": getattr(calibration, "calibration_id", ""),
        "imageSize": [int(v) for v in calibration.image_size],
        "cameraMatrix": np.asarray(calibration.camera_matrix, dtype=float).tolist(),
        "distortionCoefficients": np.asarray(calibration.distortion, dtype=float).reshape(-1).tolist(),
        "distortionModel": calibration.model,
    }


def calibration_from_meta(meta: dict):
    """Umkehrung von `intrinsics_meta` (Offline-Auswertung aus der Versuchs-JSON)."""
    import numpy as np

    from tagloc.calibration import CameraCalibration

    size = meta["imageSize"]
    return CameraCalibration(
        camera_matrix=np.asarray(meta["cameraMatrix"], dtype=np.float64).reshape(3, 3),
        distortion=np.asarray(meta["distortionCoefficients"], dtype=np.float64).reshape(-1),
        image_size=(int(size[0]), int(size[1])),
        calibration_id=str(meta.get("calibrationId", "")),
        model=str(meta.get("distortionModel", "pinhole")),
    )


def measure_for_save(image: Any, calibration: Any, sticker_offset_mm=(0.0, 0.0)) -> tuple[dict, Any]:
    """Misst `image`; liefert `(messung_dict, kontrollbild_oder_None)`, wirft nie.

    Ohne Kalibrierung: `{"ok": False, "grund": ...}`. Das Kontrollbild gibt es auch
    bei fehlgeschlagener Messung (mit Grund im Text), ausser die Annotation selbst
    scheitert.
    """
    if calibration is None:
        return {"ok": False, "grund": "Keine Kamerakalibrierung verfuegbar"}, None
    try:
        from .measure import annotate, measure_trial

        measurement = measure_trial(image, calibration, sticker_offset_mm)
        result = measurement.to_dict()
    except Exception as error:
        return {"ok": False, "grund": f"Messung fehlgeschlagen: {type(error).__name__}: {error}"}, None
    try:
        return result, annotate(image, measurement)
    except Exception:
        return result, None
