"""ApplyHandEyeCorrection: die Kameralage am Flansch nach der Punkte-Pruefung nachkorrigieren.

Die Steuerung laesst die Greiferspitze ueber die 9 Punkte eines gemessenen
Blatts fahren, der Bediener liest ab, wie weit sie jeweils daneben steht, und
die Steuerung rechnet daraus eine korrigierte `T_flansch_cam`. Hier wird sie
gegen die aktive Datei geprueft, die alte Datei gesichert
(`cam_flange.json.<UTC>.bak`, wie nach einer Kalibrierung), die neue
geschrieben und ein Protokoll unter `corrections/` daneben abgelegt.

Nur eine Korrektur, keine neue Kalibrierung: Die Anfrage nennt die
Kalibrierung, auf der sie beruht (`basedOnCalibrationId`), und darf die Lage
nur wenig aendern (`MAX_SHIFT_M`, `MAX_TURN_DEG`). Die Guetezahlen der Datei
bleiben die der urspruenglichen Kalibrierung.

Kein asyncua. Siehe `doc/projektdoku/arbeitsplaene/handeye-punkte-pruefung.md`.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from tagloc.geometry import Pose, pose_to_dict, rotation_distance_rad, translation_distance_m
from tagloc.handeye import HandEye, load_hand_eye
from tagloc.jsonio import utc_stamp

from .errors import VisionErrorCode
from .handeye_session import save_with_backup
from .layer2.plan import _load, _pose

_log = logging.getLogger(__name__)

REQUEST_SCHEMA = "wsc.vision.handeye.correction/1"
RESULT_SCHEMA = "wsc.vision.handeye.correction.result/1"
LOG_SCHEMA = "wsc.vision.handeye.correction.log/1"

#: Groesste erlaubte Aenderung der Kameralage. Mehr ist keine Korrektur mehr,
#: sondern ein Zeichen fuer ein falsches Blatt oder eine falsche Eingabe.
MAX_SHIFT_M = 0.02
MAX_TURN_DEG = 2.0
#: Ordner der Protokolle, neben der Kalibrierdatei.
LOG_DIR_NAME = "corrections"


@dataclass(frozen=True)
class CorrectionRequest:
    based_on_calibration_id: str
    pose_flange_cam: Pose
    #: Protokoll der Pruefung aus der Steuerung, wird nur abgelegt.
    check: dict


class CorrectionRefused(Exception):
    """Nichts geaendert: `code` (INVALID_STATE / INVALID_ARGUMENT) und Klartext."""

    def __init__(self, code: VisionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CorrectionOutcome:
    #: Das Ergebnis-JSON (`wsc.vision.handeye.correction.result/1`) als dict.
    result: dict
    #: Die neue Kalibrierung, aus der Datei zurueckgelesen -- fuer die Quelle.
    hand_eye: HandEye


def parse_correction(text: str) -> CorrectionRequest:
    """`wsc.vision.handeye.correction/1` lesen; ValueError mit Klartext."""
    data = _load(text, REQUEST_SCHEMA)
    based_on = data.get("basedOnCalibrationId")
    if not isinstance(based_on, str) or not based_on.strip():
        raise ValueError("basedOnCalibrationId fehlt: die Kalibrierung, auf der die Korrektur beruht.")
    pose = _pose(data.get("poseFlangeCam"), "poseFlangeCam")
    check = data.get("check", {})
    if not isinstance(check, dict):
        raise ValueError("check muss ein JSON-Objekt sein.")
    return CorrectionRequest(based_on, pose, check)


def apply_correction(request: CorrectionRequest, path: Path, log_dir: Path | None = None) -> CorrectionOutcome:
    """Prueft die Korrektur gegen die Datei `path`, sichert sie und schreibt die neue Lage.

    `CorrectionRefused` (nichts geaendert): keine oder unlesbare Datei,
    andere Kalibrierung als `basedOnCalibrationId`, Aenderung zu gross.
    `OSError`, wenn die Datei nicht geschrieben werden konnte. Ein
    fehlgeschlagenes Protokoll laesst die Korrektur stehen (`logPath: null`).
    """
    path = Path(path)
    try:
        current = load_hand_eye(path)
    except FileNotFoundError:
        raise CorrectionRefused(
            VisionErrorCode.INVALID_STATE, "Keine Hand-Auge-Kalibrierung auf dem Pi -- erst kalibrieren."
        ) from None
    except (ValueError, KeyError, TypeError) as error:
        raise CorrectionRefused(VisionErrorCode.INVALID_STATE, f"Hand-Auge-Datei unlesbar: {error}") from None
    if current.calibration_id != request.based_on_calibration_id:
        raise CorrectionRefused(
            VisionErrorCode.INVALID_STATE,
            f"Die Kalibrierung hat sich inzwischen geaendert (aktiv: {current.calibration_id or '?'}, "
            f"Korrektur fuer: {request.based_on_calibration_id}) -- Pruefung wiederholen.",
        )
    shift_m = translation_distance_m(request.pose_flange_cam, current.pose_flange_cam)
    turn_deg = math.degrees(rotation_distance_rad(request.pose_flange_cam, current.pose_flange_cam))
    if shift_m > MAX_SHIFT_M or turn_deg > MAX_TURN_DEG:
        raise CorrectionRefused(
            VisionErrorCode.INVALID_ARGUMENT,
            f"Korrektur zu gross ({shift_m * 1000:.1f} mm / {turn_deg:.2f} Grad, erlaubt "
            f"{MAX_SHIFT_M * 1000:.0f} mm / {MAX_TURN_DEG:.0f} Grad) -- Blatt und Eingaben pruefen "
            "oder neu kalibrieren.",
        )

    # Neue Kennung (leer: `save_hand_eye` vergibt `<frame>@<Zeit>`), sonst alles wie gehabt.
    corrected = dataclasses.replace(current, pose_flange_cam=request.pose_flange_cam, calibration_id="")
    backup = save_with_backup(path, corrected)
    loaded = load_hand_eye(path)
    delta = np.asarray(loaded.pose_flange_cam)[:3, 3] - np.asarray(current.pose_flange_cam)[:3, 3]
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "applied": True,
        "calibrationId": loaded.calibration_id,
        "previousCalibrationId": current.calibration_id,
        "poseFlangeCam": pose_to_dict(loaded.pose_flange_cam),
        "previousPoseFlangeCam": pose_to_dict(current.pose_flange_cam),
        "deltaPositionM": [float(value) for value in delta],
        "deltaRotationDeg": turn_deg,
        "path": str(path),
        "backupPath": str(backup) if backup is not None else None,
        "logPath": None,
        "error": None,
        "message": (
            f"Kameralage um {np.linalg.norm(delta) * 1000:.1f} mm korrigiert "
            f"(x {delta[0] * 1000:+.1f}, y {delta[1] * 1000:+.1f}, z {delta[2] * 1000:+.1f} mm im Flansch-KS)"
        ),
    }
    result["logPath"] = _write_log(log_dir or path.parent / LOG_DIR_NAME, request, result)
    return CorrectionOutcome(result, loaded)


def _write_log(folder: Path, request: CorrectionRequest, result: dict) -> str | None:
    """Protokoll der Korrektur; `None`, wenn es nicht geschrieben werden konnte."""
    payload = {
        "schema": LOG_SCHEMA,
        "basedOnCalibrationId": request.based_on_calibration_id,
        "result": {key: value for key, value in result.items() if key != "logPath"},
        "check": request.check,
    }
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{utc_stamp()}.json"
        suffix = 2
        while target.exists():
            target = folder / f"{utc_stamp()}_{suffix}.json"
            suffix += 1
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=float) + "\n", encoding="utf-8")
    except OSError as error:
        _log.warning("Protokoll der Hand-Auge-Korrektur nicht geschrieben: %s", error)
        return None
    return str(target)


def refusal_result(code: VisionErrorCode, message: str) -> dict:
    """Das Ergebnis-JSON einer Ablehnung: nichts geaendert."""
    return {"schema": RESULT_SCHEMA, "applied": False, "error": code.name, "message": message}


def dumps(result: dict) -> str:
    return json.dumps(result, allow_nan=False, default=float)


__all__ = [
    "MAX_SHIFT_M",
    "MAX_TURN_DEG",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "CorrectionOutcome",
    "CorrectionRefused",
    "CorrectionRequest",
    "apply_correction",
    "dumps",
    "parse_correction",
    "refusal_result",
]
