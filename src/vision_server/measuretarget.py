"""MeasureTarget: die Zielscheibe in Kamerakoordinaten messen (Hand-Pi).

Fuer das Visual Servoing beim Ablegen: Die Steuerung haelt den Wuerfel ueber
dem Ziel, ruft `MeasureTarget` und korrigiert den Flansch, bis die
Wuerfelmitte ueber der Scheibenmitte steht. Der Pi misst nur `T_cam_sheet`
aus frischen Bildern -- ohne Roboter, ohne Welt-KS.
Siehe `doc/projektdoku/arbeitsplaene/zielscheibe-servoing.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import numpy as np

REQUEST_SCHEMA = "wsc.vision.measuretarget.request/1"
RESULT_SCHEMA = "wsc.vision.measuretarget.result/1"

DEFAULT_FRAMES = 3
MAX_FRAMES = 10
NO_FRAME = "NO_FRAME"


@dataclass(frozen=True)
class TargetRequest:
    frames: int = DEFAULT_FRAMES


@dataclass(frozen=True)
class TargetFrame:
    """Ein ausgewertetes Bild."""

    captured_at: datetime
    image_size: tuple[int, int]
    #: Ergebnis von `use_case_eval.measure.measure_sheet` (dict, mm, Kamera im Blatt-KS).
    sheet: dict


def parse_request(text: str) -> TargetRequest:
    """`wsc.vision.measuretarget.request/1` lesen; ValueError mit Klartext.

    Leer oder `{}` ist gueltig: Standardanzahl Bilder.
    """
    try:
        data = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict):
        raise ValueError("Anfrage muss ein JSON-Objekt sein")
    schema = data.get("schema", REQUEST_SCHEMA)
    if schema != REQUEST_SCHEMA:
        raise ValueError(f"Unbekanntes Schema {schema!r}, erwartet {REQUEST_SCHEMA}")
    frames = data.get("frames", DEFAULT_FRAMES)
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine ganze Zahl von 1 bis {MAX_FRAMES} sein")
    return TargetRequest(frames=frames)


def pose_cam_sheet(sheet: dict):
    """`T_cam_sheet` (4x4, Meter) aus `measure_sheet`; `None` ohne gueltige Pose."""
    if not sheet.get("ok") or sheet.get("camera_in_sheet_mm") is None or sheet.get("camera_rotation_in_sheet") is None:
        return None
    sheet_cam = np.eye(4)
    sheet_cam[:3, :3] = np.asarray(sheet["camera_rotation_in_sheet"], dtype=np.float64)
    sheet_cam[:3, 3] = np.asarray(sheet["camera_in_sheet_mm"], dtype=np.float64) / 1000.0
    return np.linalg.inv(sheet_cam)


def _pose_json(pose) -> dict:
    from tagloc.geometry import to_position_quaternion

    position, orientation = to_position_quaternion(np.asarray(pose, dtype=np.float64))
    return {"position": [float(v) for v in position], "orientation": [float(v) for v in orientation]}


def build_result(
    frames: list[TargetFrame],
    *,
    frames_requested: int,
    frame_id: str,
    intrinsics_source: str,
    calibration_id: str,
    error: str | None = None,
) -> dict:
    """Das Ergebnis-JSON als dict: Mittel ueber alle Bilder mit gefundener Blattpose."""
    from tagloc.geometry import average_poses

    poses = [(frame, pose_cam_sheet(frame.sheet)) for frame in frames]
    seen = [(frame, pose) for frame, pose in poses if pose is not None]
    pose = None
    if seen:
        averaged = np.array(average_poses([one for _, one in seen]), dtype=np.float64)
        averaged[:3, 3] = np.mean([one[:3, 3] for _, one in seen], axis=0)
        pose = averaged
    markers = sorted({int(marker) for frame, _ in seen for marker in frame.sheet.get("sheet_markers", [])})
    errors = [frame.sheet["reprojection_rms_px"] for frame, _ in seen if frame.sheet.get("reprojection_rms_px") is not None]
    return {
        "schema": RESULT_SCHEMA,
        "capturedAt": _iso(frames[0].captured_at) if frames else None,
        "capturedUntil": _iso(frames[-1].captured_at) if frames else None,
        "frameId": frame_id,
        "imageSize": list(frames[-1].image_size) if frames else None,
        "intrinsicsSource": intrinsics_source,
        "calibrationId": calibration_id,
        "framesRequested": frames_requested,
        "framesUsed": len(frames),
        "found": pose is not None,
        "poseInCamera": _pose_json(pose) if pose is not None else None,
        "reprojectionRmsPx": float(np.mean(errors)) if errors else None,
        "sheetMarkers": markers,
        "frames": [
            {
                "capturedAt": _iso(frame.captured_at),
                "found": one is not None,
                "sheetMarkers": [int(marker) for marker in frame.sheet.get("sheet_markers", [])],
                "poseInCamera": _pose_json(one) if one is not None else None,
                "reprojectionRmsPx": frame.sheet.get("reprojection_rms_px"),
                "reason": frame.sheet.get("grund", ""),
            }
            for frame, one in poses
        ],
        "error": error,
    }


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def dumps(result: dict) -> str:
    return json.dumps(result, allow_nan=False, default=float)
