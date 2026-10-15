"""MeasureTags: Tags in Kamerakoordinaten messen, fuer die Regelung im Frontend.

Layer 2 ist neu aufgeteilt: Die Steuerung (webskillcomposition) plant und
faehrt, der Hand-Pi misst nur. Steht der Roboter, ruft die Steuerung
`MeasureTags` auf. Der Pi nimmt nur Bilder, die **nach** dem Aufruf eintreffen,
mittelt ueber einige davon und liefert je Tag `T_cam_tag` -- ohne Welt-KS,
ohne CAD-Versatz, ohne Kenntnis des Roboters. Die Steuerung rechnet daraus mit
FK und `T_flansch_cam` das naechste Flanschziel.

Hier steckt kein asyncua und keine Kamera: Messungen kommen als Daten herein.
Siehe `doc/projektdoku/arbeitsplaene/layer2-measuretags.md`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

REQUEST_SCHEMA = "wsc.vision.measuretag.request/1"
RESULT_SCHEMA = "wsc.vision.measuretag.result/1"

#: OpenCV-Optikrahmen: +Z aus der Linse, +X rechts, +Y unten -- wie im
#: Detektions-Payload (`frameConvention`).
FRAME_CONVENTION = "z_forward_x_right_y_down"

DEFAULT_FRAMES = 3
MAX_FRAMES = 10

NO_FRAME = "NO_FRAME"


@dataclass(frozen=True)
class MeasureRequest:
    #: `None` = alle erkannten Tags.
    tag_ids: frozenset[int] | None
    #: Groesse je Tag aus der Anfrage, Meter.
    sizes_m: dict[int, float]
    frames: int = DEFAULT_FRAMES


@dataclass(frozen=True)
class FrameMeasurement:
    """Ein ausgewertetes Bild."""

    captured_at: datetime
    image_size: tuple[int, int]
    #: `TagPose` aus `tagloc.pose.estimate_tag_poses`, bereits auf
    #: `tag_ids` gefiltert.
    tag_poses: tuple


def parse_request(text: str) -> MeasureRequest:
    """`wsc.vision.measuretag.request/1` lesen; ValueError mit Klartext.

    Leer oder `{}` ist gueltig: alle Tags, Standardanzahl Bilder.
    """
    try:
        data = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", REQUEST_SCHEMA) != REQUEST_SCHEMA:
        raise ValueError(f"Erwartet schema „{REQUEST_SCHEMA}“.")

    raw_ids = data.get("tagIds")
    tag_ids = None
    if raw_ids is not None:
        if not isinstance(raw_ids, list) or not all(_is_int(v) and v >= 0 for v in raw_ids):
            raise ValueError("tagIds muss eine Liste nicht-negativer Ganzzahlen oder null sein.")
        tag_ids = frozenset(raw_ids)

    raw_sizes = data.get("tagSizeM") or {}
    if not isinstance(raw_sizes, dict):
        raise ValueError("tagSizeM muss ein Objekt {\"<tagId>\": Meter} sein.")
    sizes = {}
    for key, value in raw_sizes.items():
        try:
            tag_id = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"tagSizeM: Schluessel „{key}“ ist keine Tag-ID.") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise ValueError(f"tagSizeM[{key}] muss eine positive Zahl sein.")
        sizes[tag_id] = float(value)

    frames = data.get("frames", DEFAULT_FRAMES)
    if not _is_int(frames) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine Ganzzahl von 1 bis {MAX_FRAMES} sein.")
    return MeasureRequest(tag_ids, sizes, int(frames))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class SizeLookup:
    """Tag-Groesse: Anfrage vor Tag-Map vor Rueckfall -- mit Herkunft.

    Hat dieselbe Schnittstelle wie `TagMap.size_for`, damit
    `estimate_tag_poses` sie direkt nimmt.
    """

    def __init__(self, request_sizes: dict[int, float], tag_map, default_m: float) -> None:
        self._request = request_sizes
        self._tag_map = tag_map
        self._default = default_m

    def size_for(self, tag_id: int, default_m: float | None = None) -> float:
        return self.size_and_source(tag_id)[0]

    def size_and_source(self, tag_id: int) -> tuple[float, str]:
        if tag_id in self._request:
            return self._request[tag_id], "request"
        entry = self._tag_map.get(tag_id) if self._tag_map is not None else None
        if entry is not None:
            return float(entry.size_m), "tagmap"
        return self._default, "default"


def build_result(
    frames: list[FrameMeasurement],
    *,
    frames_requested: int,
    frame_id: str,
    sizes: SizeLookup,
    intrinsics_source: str,
    calibration_id: str,
    error: str | None = None,
) -> dict:
    """Das Ergebnis-JSON (`wsc.vision.measuretag.result/1`) als dict.

    Je Tag ueber alle Bilder zusammengefuehrt:
    * Position: Mittel ueber alle Bilder, in denen der Tag zu sehen war. Sie
      ist auch bei mehrdeutiger Orientierung verlaesslich.
    * Orientierung: Mittel ueber die eindeutigen Bilder; nur wenn es keine
      gibt, ueber alle -- und dann `ambiguous: true`.
    * Pixel (`cornersPx`, `centerPx`, `decisionMargin`): aus dem letzten Bild,
      roh wie vom Detektor (verzeichnet).
    """
    from tagloc.geometry import average_poses, to_position_quaternion

    by_tag: dict[int, list] = {}
    for frame in frames:
        for tag_pose in frame.tag_poses:
            by_tag.setdefault(int(tag_pose.tag_id), []).append(tag_pose)

    tags = []
    for tag_id in sorted(by_tag):
        seen = by_tag[tag_id]
        clear = [tag_pose for tag_pose in seen if not tag_pose.is_ambiguous]
        oriented = average_poses([tag_pose.pose_cam_tag for tag_pose in (clear or seen)])
        pose = np.array(oriented, dtype=np.float64)
        pose[:3, 3] = np.mean([np.asarray(t.pose_cam_tag)[:3, 3] for t in seen], axis=0)
        position, orientation = to_position_quaternion(pose)
        last = seen[-1]
        corners = (
            [[float(u), float(v)] for u, v in last.observation.corners]
            if last.observation is not None
            else None
        )
        size_m, size_source = sizes.size_and_source(tag_id)
        errors = [t.reprojection_error_px for t in seen if math.isfinite(t.reprojection_error_px)]
        tags.append(
            {
                "tagId": tag_id,
                "poseInCamera": {"position": list(position), "orientation": list(orientation)},
                "centerPx": list(np.mean(corners, axis=0)) if corners else None,
                "cornersPx": corners,
                "reprojectionErrorPx": float(np.mean(errors)) if errors else None,
                "decisionMargin": _finite(
                    getattr(last.observation, "decision_margin", None)
                ),
                "sizeM": size_m,
                "sizeSource": size_source,
                # Orientierung nur aus mehrdeutigen Bildern -- nicht darauf regeln.
                "ambiguous": not clear,
                "ambiguityRatio": float(max(t.ambiguity_ratio for t in seen)),
                "frameCount": len(seen),
                "distanceM": float(np.linalg.norm(pose[:3, 3])),
                "viewAngleDeg": view_angle_deg(pose),
            }
        )

    return {
        "schema": RESULT_SCHEMA,
        "capturedAt": _iso(frames[0].captured_at) if frames else None,
        "capturedUntil": _iso(frames[-1].captured_at) if frames else None,
        "frameId": frame_id,
        "frameConvention": FRAME_CONVENTION,
        "imageSize": list(frames[-1].image_size) if frames else None,
        "intrinsicsSource": intrinsics_source,
        "calibrationId": calibration_id,
        "framesRequested": frames_requested,
        "framesUsed": len(frames),
        "tags": tags,
        "error": error,
    }


def view_angle_deg(pose_cam_tag) -> float:
    """Winkel zwischen Tag-Normale und Sichtlinie zur Kamera, Grad. 0 = frontal."""
    pose = np.asarray(pose_cam_tag, dtype=np.float64)
    distance = float(np.linalg.norm(pose[:3, 3]))
    if distance < 1e-9:
        return 0.0
    towards_camera = -pose[:3, 3] / distance
    cosine = float(np.clip(pose[:3, 2] @ towards_camera, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _finite(value) -> float | None:
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def dumps(result: dict) -> str:
    return json.dumps(result, allow_nan=False, default=float)
