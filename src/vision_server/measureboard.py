"""MeasureBoard: das Kalibrier-Schachbrett vor der Hand-Auge-Aufnahme messen.

Fuer das Zentrieren: Die Steuerung fuehrt den Roboter ueber das Brett, ruft
`MeasureBoard` auf und regelt nach, bis das ganze Brett mittig im Bild liegt.
Erst dann nimmt sie `CaptureHandEyeSample` auf. Wie `MeasureTags`: Der Pi
misst nur und schickt keine Fahrbefehle.

* **Ganzes Brett im Bild:** `complete`, mit `T_cam_board` (Kamera-KS wie
  OpenCV), den Ecken in Pixeln und dem Reprojektionsfehler.
* **Brett nur teilweise im Bild:** Die Erkennung braucht das ganze Raster. Ein
  kleineres Teilraster passt aber in jeden sichtbaren Teil. Daraus kommen der
  Schwerpunkt der gefundenen Ecken in Pixeln und die Bildraender, denen sie
  nahe liegen -- genug zum Zentrieren, aber keine Brettpose: Der Ursprung eines
  Teilrasters ist beliebig.
* **Nichts gefunden:** `found: false`, kein Fehler.

Hier steckt kein asyncua und keine Kamera. Siehe
`doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .measuretag import DEFAULT_FRAMES, FRAME_CONVENTION, MAX_FRAMES, NO_FRAME, near_edge, view_angle_deg

REQUEST_SCHEMA = "wsc.vision.measureboard.request/1"
RESULT_SCHEMA = "wsc.vision.measureboard.result/1"



@dataclass(frozen=True)
class BoardRequest:
    #: `None` = die Brettgroesse aus der Konfiguration der Kamera.
    board: object | None
    frames: int = DEFAULT_FRAMES


@dataclass(frozen=True)
class FrameBoard:
    """Ein ausgewertetes Bild."""

    captured_at: datetime
    image_size: tuple[int, int]
    #: Ganzes Brett gefunden.
    complete: bool = False
    pose: object | None = None
    reprojection_error_px: float | None = None
    #: Alle Ecken in fester Reihenfolge, roh (verzeichnet), `(N, 2)`; nur bei `complete`.
    corners: object | None = None
    #: Teilraster: `(Ecken (N, 2), cols, rows)`; nur wenn nicht `complete`.
    partial: tuple | None = None


def parse_request(text: str, parse_board) -> BoardRequest:
    """`wsc.vision.measureboard.request/1` lesen; ValueError mit Klartext.

    `parse_board` ist der Brett-Parser der Hand-Auge-Sitzung -- dieselben
    Regeln, damit beide Aufrufe dasselbe Brett meinen. Leer oder `{}` ist
    gueltig: Brett aus der Konfiguration, Standardanzahl Bilder.
    """
    try:
        data = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", REQUEST_SCHEMA) != REQUEST_SCHEMA:
        raise ValueError(f"Erwartet schema „{REQUEST_SCHEMA}“.")
    board = parse_board(data["board"]) if data.get("board") is not None else None
    frames = data.get("frames", DEFAULT_FRAMES)
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine Ganzzahl von 1 bis {MAX_FRAMES} sein.")
    return BoardRequest(board, int(frames))


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def _finite(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def build_result(
    frames: list[FrameBoard],
    *,
    spec,
    frames_requested: int,
    frame_id: str,
    intrinsics_source: str,
    calibration_id: str,
    max_error_px: float,
    error: str | None = None,
    message: str = "",
) -> dict:
    """Das Ergebnis-JSON (`wsc.vision.measureboard.result/1`) als dict.

    Pose und Reprojektionsfehler: Mittel ueber die Bilder mit ganzem Brett.
    Pixel (`cornersPx`, `centerPx`, `offsetPx`, `partial`): aus dem letzten Bild.
    """
    from tagloc.geometry import to_position_quaternion
    from tagloc.tagmap import merge_tag_poses

    complete_frames = [frame for frame in frames if frame.complete]
    last = frames[-1] if frames else None
    width, height = last.image_size if last is not None else (0, 0)
    centre = (width / 2.0, height / 2.0)

    result = {
        "schema": RESULT_SCHEMA,
        "capturedAt": _iso(frames[0].captured_at) if frames else None,
        "capturedUntil": _iso(frames[-1].captured_at) if frames else None,
        "frameId": frame_id,
        "frameConvention": FRAME_CONVENTION,
        "imageSize": [width, height] if frames else None,
        "intrinsicsSource": intrinsics_source,
        "calibrationId": calibration_id,
        "board": {
            "type": spec.type,
            "cols": spec.cols,
            "rows": spec.rows,
            "squareSizeM": spec.square_size_m,
        },
        "framesRequested": frames_requested,
        "framesUsed": len(frames),
        "found": False,
        "complete": False,
        "frameCount": len(complete_frames),
        "poseInCamera": None,
        "poseOk": None,
        "reprojectionErrorPx": None,
        "distanceM": None,
        "viewAngleDeg": None,
        "centerPx": None,
        "offsetPx": None,
        "cornersPx": None,
        "nearEdge": [],
        "partial": None,
        "error": error,
        "message": message,
    }
    if not frames:
        return result

    corners = None
    if complete_frames:
        pose = merge_tag_poses([frame.pose for frame in complete_frames])
        position, orientation = to_position_quaternion(pose)
        errors = [frame.reprojection_error_px for frame in complete_frames if frame.reprojection_error_px is not None]
        mean_error = float(np.mean(errors)) if errors else None
        corners = np.asarray(complete_frames[-1].corners, dtype=np.float64).reshape(-1, 2)
        result.update(
            found=True,
            complete=True,
            poseInCamera={"position": list(position), "orientation": list(orientation)},
            # Gut genug fuer die Hand-Auge-Aufnahme? Dieselbe Schranke wie dort.
            poseOk=bool(mean_error is not None and mean_error <= max_error_px),
            reprojectionErrorPx=_finite(mean_error),
            distanceM=float(np.linalg.norm(np.asarray(pose)[:3, 3])),
            viewAngleDeg=view_angle_deg(pose),
        )
    elif last.partial is not None:
        partial_corners, grid_cols, grid_rows = last.partial
        corners = np.asarray(partial_corners, dtype=np.float64).reshape(-1, 2)
        result["found"] = True
        result["partial"] = {
            "gridCols": int(grid_cols),
            "gridRows": int(grid_rows),
            "cornerCount": int(len(corners)),
            "bboxPx": [
                float(corners[:, 0].min()),
                float(corners[:, 1].min()),
                float(corners[:, 0].max()),
                float(corners[:, 1].max()),
            ],
        }

    if corners is not None:
        mean = corners.mean(axis=0)
        result["centerPx"] = [float(mean[0]), float(mean[1])]
        # Vom Bildmittelpunkt zum Brett: dorthin muss die Kamera, damit das Brett
        # in die Mitte rueckt (x nach rechts, y nach unten).
        result["offsetPx"] = [float(mean[0] - centre[0]), float(mean[1] - centre[1])]
        result["nearEdge"] = near_edge(corners.tolist(), (width, height))
        if result["complete"]:
            result["cornersPx"] = [[float(u), float(v)] for u, v in corners]
    return result


def dumps(result: dict) -> str:
    return json.dumps(result, allow_nan=False, default=float)


__all__ = [
    "NO_FRAME",
    "RESULT_SCHEMA",
    "BoardRequest",
    "FrameBoard",
    "build_result",
    "dumps",
    "parse_request",
]
