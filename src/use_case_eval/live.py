"""Hook for ``SaveUseCaseImage``: non-raising measurement, sticker offset and intrinsics metadata.

A measurement error must never prevent the photo from being saved; it is reported as
``{"ok": false, "grund": ...}`` in the trial JSON.
"""

import json
from pathlib import Path
from typing import Any

#: Name of the optional file with the sticker offset in the use-case folder.
STICKER_OFFSET_FILE = "aufkleber_versatz.json"


def load_sticker_offset(base_dir: Path) -> tuple[float, float]:
    """Read ``<base_dir>/aufkleber_versatz.json`` (``{"x_mm": .., "y_mm": ..}``); else (0, 0)."""
    path = Path(base_dir) / STICKER_OFFSET_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data["x_mm"]), float(data["y_mm"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0, 0.0


def write_sticker_offset(base_dir: Path, offset_mm: tuple[float, float]) -> Path:
    """Write the sticker offset (marker system, mm) to ``<base_dir>/aufkleber_versatz.json``."""
    path = Path(base_dir) / STICKER_OFFSET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"x_mm": offset_mm[0], "y_mm": offset_mm[1]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def intrinsics_meta(calibration: Any, source: str) -> dict:
    """Return ``pi_meta.intrinsics``: the camera matrix and distortion actually used, plus source.

    ``source`` is ``calibration`` (calibration file), ``realsense_factory`` or
    ``placeholder``.
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
    """Inverse of ``intrinsics_meta``, for offline evaluation from the trial JSON."""
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


def measure_for_save(
    image: Any, calibration: Any, sticker_offset_mm=(0.0, 0.0), reference: dict | None = None
) -> tuple[dict, Any]:
    """Measure ``image``; return ``(measurement_dict, control_image_or_None)``. Never raises.

    Without a calibration: ``{"ok": False, "grund": ...}``. The control image is also
    produced when the measurement fails (with the reason in the text), unless the
    annotation itself fails. ``reference``: see ``measure_trial`` (``None`` = target).
    """
    if calibration is None:
        return {"ok": False, "grund": "Keine Kamerakalibrierung verfuegbar"}, None
    try:
        from .measure import annotate, measure_trial

        measurement = measure_trial(image, calibration, sticker_offset_mm, reference=reference)
        result = measurement.to_dict()
    except Exception as error:
        return {"ok": False, "grund": f"Messung fehlgeschlagen: {type(error).__name__}: {error}"}, None
    try:
        return result, annotate(image, measurement)
    except Exception:
        return result, None
