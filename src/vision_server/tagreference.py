"""MeasureTagReference / PlanTagReferenceView: Tag-Position ueber Bezugskanten einmessen.

Der Zollstock-Ersatz: Die Steuerung (webskillcomposition) schickt den
Katalog-Startwert `T_module_tag` und die Bezugskanten des Moduls (3D-Strecken
im Modul-KS). Der Hand-Pi nimmt frische Bilder, erkennt den Tag, misst die
Kanten im selben Bild und liefert die in der Tag-Ebene korrigierte
`T_module_tag` -- ohne Roboterkinematik, ohne Hand-Auge-Fehler. Die Rechnung
steht in `tagloc.reference_edges`.

Hier steckt kein asyncua und keine Kamera: Anfrage parsen, Ergebnis-JSON bauen,
dazu die kleinen reinen Helfer dazwischen (Tag-Pose ueber Bilder,
Zusammenfassung der Kanten, Warnungen). Siehe
`doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

import numpy as np

from .measuretag import view_angle_deg

REQUEST_SCHEMA = "wsc.vision.tagreference.request/1"
RESULT_SCHEMA = "wsc.vision.tagreference.result/1"
VIEW_SCHEMA = "wsc.vision.tagreference.view/1"

DEFAULT_FRAMES = 3
MAX_FRAMES = 10
DEFAULT_SEARCH_BAND_M = 0.015
MIN_SEARCH_BAND_M = 0.003
MAX_SEARCH_BAND_M = 0.05
DEFAULT_LINE_EXTENT_M = 0.10
#: Kippwinkel der Planungsansicht. Frontal (0 Grad) kippt die Tag-Pose gern in
#: die Spiegelloesung; schon 10 Grad machen sie eindeutig.
DEFAULT_TILT_DEG = 10.0
#: Obergrenze wie in `plan_reference_view` (dort: < 80 Grad).
MAX_TILT_DEG = 80.0
MAX_LINES = 4
MAX_LINE_EXTENT_M = 1.0

NO_FRAME = "NO_FRAME"
TAG_NOT_SEEN = "TAG_NOT_SEEN"


@dataclass(frozen=True)
class TagReferenceRequest:
    """Eine geprueft gelesene Anfrage (fuer beide Methoden)."""

    tag_id: int
    #: Groesse aus der Anfrage, Meter; `None` = Tag-Map bzw. Standard.
    tag_size_m: float | None
    #: Startwert `T_module_tag` als 4x4-Pose.
    pose_module_tag_guess: np.ndarray
    #: `tagloc.reference_edges.ReferenceLine`, Modul-KS, Meter.
    lines: tuple
    search_band_m: float = DEFAULT_SEARCH_BAND_M
    frames: int = DEFAULT_FRAMES
    line_extent_m: float = DEFAULT_LINE_EXTENT_M
    tilt_deg: float = DEFAULT_TILT_DEG
    #: Wirksame Overlay-Ebenen der Debug-Ansicht (alle Schluessel aus
    #: `tagloc.reference_overlay.LAYER_KEYS`, fehlende = `True`).
    overlay: dict = field(default_factory=dict)
    #: `MeasureTagReference`: Mess-Protokoll auf Platte schreiben.
    save_debug: bool = True


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value, length: int, name: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != length
        or not all(_is_number(item) for item in value)
    ):
        raise ValueError(f"{name} muss eine Liste aus {length} endlichen Zahlen sein.")
    return [float(item) for item in value]


def parse_request(text: str) -> TagReferenceRequest:
    """`wsc.vision.tagreference.request/1` lesen; ValueError mit Klartext.

    Pflicht: `tagId`, `moduleTagGuess`, `lines` (1 bis 4). Optional:
    `overlay` (Ebenen der Debug-Ansicht) und `saveDebug` (Mess-Protokoll).
    Unbekannte Felder werden ignoriert.
    """
    from tagloc.geometry import from_position_quaternion
    from tagloc.reference_edges import MIN_LINE_LENGTH_M, ReferenceLine

    try:
        data = json.loads(text) if text and text.strip() else None
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", REQUEST_SCHEMA) != REQUEST_SCHEMA:
        raise ValueError(f"Erwartet ein Objekt mit schema „{REQUEST_SCHEMA}“.")

    tag_id = data.get("tagId")
    if not _is_int(tag_id) or tag_id < 0:
        raise ValueError("tagId (nicht-negative Ganzzahl) fehlt oder ist ungueltig.")

    tag_size = data.get("tagSizeM")
    if tag_size is not None and (not _is_number(tag_size) or not tag_size > 0):
        raise ValueError("tagSizeM muss eine positive Zahl (Meter) sein.")

    guess = data.get("moduleTagGuess")
    if not isinstance(guess, dict):
        raise ValueError("moduleTagGuess {position, orientation} fehlt.")
    position = _vector(guess.get("position"), 3, "moduleTagGuess.position")
    orientation = _vector(guess.get("orientation"), 4, "moduleTagGuess.orientation")
    if math.sqrt(sum(value * value for value in orientation)) < 1e-9:
        raise ValueError("moduleTagGuess.orientation ist ein Nullquaternion.")
    pose = from_position_quaternion(position, orientation)

    raw_lines = data.get("lines")
    if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= MAX_LINES:
        raise ValueError(f"lines muss 1 bis {MAX_LINES} Kanten enthalten.")
    lines = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            raise ValueError(f"lines[{index}] muss ein Objekt {{id, start, end}} sein.")
        line_id = raw.get("id")
        if not isinstance(line_id, str) or not line_id:
            raise ValueError(f"lines[{index}].id muss ein nicht leerer String sein.")
        if line_id in seen_ids:
            raise ValueError(f"lines: id „{line_id}“ kommt doppelt vor.")
        seen_ids.add(line_id)
        start = np.array(_vector(raw.get("start"), 3, f"lines[{index}].start"))
        end = np.array(_vector(raw.get("end"), 3, f"lines[{index}].end"))
        if float(np.linalg.norm(end - start)) < MIN_LINE_LENGTH_M:
            raise ValueError(f"lines[{index}] ({line_id}) ist kuerzer als {MIN_LINE_LENGTH_M * 100:.0f} cm.")
        lines.append(ReferenceLine(line_id, start, end))

    band = data.get("searchBandM", DEFAULT_SEARCH_BAND_M)
    if not _is_number(band) or not MIN_SEARCH_BAND_M <= band <= MAX_SEARCH_BAND_M:
        raise ValueError(
            f"searchBandM muss zwischen {MIN_SEARCH_BAND_M} und {MAX_SEARCH_BAND_M} m liegen."
        )

    frames = data.get("frames", DEFAULT_FRAMES)
    if not _is_int(frames) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine Ganzzahl von 1 bis {MAX_FRAMES} sein.")

    view = data.get("view", {})
    if view is None:
        view = {}
    if not isinstance(view, dict):
        raise ValueError("view muss ein Objekt {lineExtentM, tiltDeg} sein.")
    extent = view.get("lineExtentM", DEFAULT_LINE_EXTENT_M)
    if not _is_number(extent) or not 0 < extent <= MAX_LINE_EXTENT_M:
        raise ValueError(f"view.lineExtentM muss zwischen 0 und {MAX_LINE_EXTENT_M} m liegen.")
    tilt = view.get("tiltDeg", DEFAULT_TILT_DEG)
    if not _is_number(tilt) or abs(tilt) >= MAX_TILT_DEG:
        raise ValueError(f"view.tiltDeg muss betragsmaessig kleiner als {MAX_TILT_DEG:.0f} Grad sein.")

    from tagloc.reference_overlay import LAYER_KEYS, effective_layers

    overlay = data.get("overlay")
    if overlay is None:
        overlay = {}
    if not isinstance(overlay, dict):
        raise ValueError("overlay muss ein Objekt {guess, band, candidates, ...} sein.")
    for key in LAYER_KEYS:
        if key in overlay and not isinstance(overlay[key], bool):
            raise ValueError(f"overlay.{key} muss true oder false sein.")
    save_debug = data.get("saveDebug", True)
    if not isinstance(save_debug, bool):
        raise ValueError("saveDebug muss true oder false sein.")

    return TagReferenceRequest(
        overlay=effective_layers(overlay),
        save_debug=save_debug,
        tag_id=int(tag_id),
        tag_size_m=float(tag_size) if tag_size is not None else None,
        pose_module_tag_guess=pose,
        lines=tuple(lines),
        search_band_m=float(band),
        frames=int(frames),
        line_extent_m=float(extent),
        tilt_deg=float(tilt),
    )


# -- Tag-Pose ueber mehrere Bilder ---------------------------------------------


def combine_tag_pose(tag_poses: Sequence) -> tuple[np.ndarray, bool]:
    """Eine Tag-Pose aus den Bildern, in denen der Tag zu sehen war.

    Der Roboter steht waehrend der Messung, die Pose ist in allen Bildern
    dieselbe. Wie bei `MeasureTags`: Position als Mittel ueber alle Bilder,
    Orientierung nur ueber die eindeutigen; gibt es keine, ueber alle -- und
    dann ist der Rueckgabewert `ambiguous=True` (Spiegelloesung moeglich, die
    Drehung ist nicht belastbar).
    """
    from tagloc.geometry import average_poses

    clear = [item for item in tag_poses if not item.is_ambiguous]
    pose = np.array(average_poses([item.pose_cam_tag for item in (clear or tag_poses)]), dtype=np.float64)
    pose[:3, 3] = np.mean([np.asarray(item.pose_cam_tag)[:3, 3] for item in tag_poses], axis=0)
    return pose, not clear


def keep_guess_rotation(correction, pose_module_tag_guess):
    """Nur die Position der Korrektur uebernehmen, Drehung aus dem Startwert.

    Fuer einen mehrdeutigen Tag: Seine Drehung (und damit die Neigung der
    Tag-Ebene) ist nicht belastbar, die Lage seines Ursprungs schon.
    `fit_in_plane_correction` liefert `D` (Drehung `theta` um Tag-z und
    Verschiebung `t` am Tag-Ursprung); der neue Tag-Ursprung liegt im alten
    Tag-KS bei `s = -R(theta)^T t`. Hier bleibt die Orientierung des
    Startwerts, nur der Ursprung wandert nach `s`:
    `T_module_tag = T_module_tag_guess * Verschiebung(s)`. `delta_xy_m` ist
    danach `-s` (reine Verschiebung, `delta_rot_deg = 0`), also dasselbe `D`
    ohne Drehung. Bei `dofs == "none"` bleibt alles unveraendert.
    """
    from tagloc.geometry import compose, from_rotation_translation

    theta = math.radians(correction.delta_rot_deg)
    cosine, sine = math.cos(theta), math.sin(theta)
    translation = np.asarray(correction.delta_xy_m, dtype=np.float64)
    # R(theta)^T t
    plane_shift = np.array(
        [cosine * translation[0] + sine * translation[1], -sine * translation[0] + cosine * translation[1]]
    )
    move = from_rotation_translation(np.eye(3), [-plane_shift[0], -plane_shift[1], 0.0])
    return replace(
        correction,
        delta_xy_m=plane_shift,
        delta_rot_deg=0.0,
        pose_module_tag=compose(pose_module_tag_guess, move),
    )


# -- Ergebnis --------------------------------------------------------------------


def _mean(values) -> float | None:
    values = [float(value) for value in values if value is not None and math.isfinite(value)]
    return float(np.mean(values)) if values else None


def summarize_lines(per_frame: Sequence[Sequence]) -> list[dict]:
    """Je Kante ein Eintrag ueber alle Bilder (`LineMeasurement` je Bild).

    * `found`: in mindestens einem Bild gefunden
    * Werte (`offsetMm`, `angleDeg`, `supportPx`, ...): Mittel ueber die Bilder,
      in denen die Kante eindeutig war; gab es keine, ueber die gefundenen
    * `ambiguous`: in mindestens einem Bild mehrdeutig (vorsichtig gemeldet;
      die Korrektur nimmt die Kante nur in Bildern, in denen sie eindeutig ist)
    * `candidates`: groesste Kandidatenzahl
    * `reason`: nur bei nicht gefundenen Kanten, aus dem letzten Bild
    """
    if not per_frame:
        return []
    summary = []
    for index, first in enumerate(per_frame[0]):
        items = [frame[index] for frame in per_frame]
        found = [item for item in items if item.found]
        usable = [item for item in found if not item.ambiguous]
        basis = usable or found
        offset = _mean(item.offset_m for item in basis)
        summary.append(
            {
                "id": first.id,
                "found": bool(found),
                "offsetMm": offset * 1000.0 if offset is not None else None,
                "angleDeg": _mean(item.angle_deg for item in basis),
                "supportPx": _mean(item.support_px for item in basis) or 0.0,
                "residualPx": _mean(item.residual_px for item in basis) or 0.0,
                "contrast": _mean(item.contrast for item in basis) or 0.0,
                "candidates": max(int(item.candidates) for item in items),
                "ambiguous": any(item.ambiguous for item in found),
                "reason": None if found else items[-1].reason,
            }
        )
    return summary


def reference_warnings(
    *,
    correction,
    lines: Sequence[dict],
    search_band_m: float,
    tag_ambiguous: bool,
    intrinsics_source: str,
) -> list[str]:
    """Hinweise zum Ergebnis, Klartext. Leer = nichts aufgefallen."""
    warnings = []
    if intrinsics_source == "placeholder":
        warnings.append("Intrinsik ist ein Platzhalter: Messung nicht belastbar.")
    if tag_ambiguous:
        warnings.append(
            "Tag-Pose mehrdeutig (Spiegelloesung moeglich): Drehung aus dem Startwert, nur Position korrigiert."
        )
    if correction.dofs == "none":
        warnings.append("Keine Kante verwertbar: Startwert unveraendert.")
    elif correction.dofs == "partial":
        warnings.append(
            "Nur Teilkorrektur (eine verwertbare Kante oder nur parallele Kanten): "
            "entlang der Kante bleibt der Startwert."
        )
    if any(line["ambiguous"] for line in lines):
        warnings.append("Mindestens eine Kante ist mehrdeutig (z. B. Item-Nut) und wurde nicht verwendet.")
    shift = float(np.linalg.norm(correction.delta_xy_m))
    offsets = [abs(line["offsetMm"]) / 1000.0 for line in lines if line["offsetMm"] is not None]
    if shift > search_band_m or (offsets and max(offsets) >= 0.9 * search_band_m):
        warnings.append(
            f"Korrektur ({shift * 1000.0:.1f} mm) liegt nahe am oder ueber dem Suchband "
            f"({search_band_m * 1000.0:.1f} mm): Startwert vermutlich falsch."
        )
    return warnings


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def build_result(
    *,
    captured_at: datetime | None = None,
    captured_until: datetime | None = None,
    image_size: tuple[int, int] | None = None,
    intrinsics_source: str | None = None,
    frames_used: int = 0,
    tag_poses: Sequence = (),
    tag_pose: np.ndarray | None = None,
    tag_ambiguous: bool = False,
    line_frames: Sequence[Sequence] = (),
    correction=None,
    spread: dict | None = None,
    warnings: Sequence[str] = (),
    error: str | None = None,
    debug_dir: str | None = None,
) -> dict:
    """Das Ergebnis-JSON (`wsc.vision.tagreference.result/1`) als dict.

    Bei `error` (oder ohne Korrektur): `correction: null`, `lines: []`. Auch
    ein reines `build_result(error="...")` ist gueltig -- so entstehen die
    Ablehnungen vor der Messung.

    `tag_poses`: `TagPose` der verwendeten Bilder (Reprojektionsfehler);
    `tag_pose`: die daraus gemittelte `T_cam_tag`; `line_frames`:
    `LineMeasurement`-Listen je Bild; `correction` / `spread` wie aus
    `average_corrections`.
    """
    from tagloc.geometry import pose_to_dict
    from tagloc.reference_edges import catalog_rpy_deg

    tag = None
    if tag_pose is not None:
        errors = [
            item.reprojection_error_px
            for item in tag_poses
            if math.isfinite(item.reprojection_error_px)
        ]
        tag = {
            "poseInCamera": pose_to_dict(tag_pose),
            "reprojectionErrorPx": float(np.mean(errors)) if errors else None,
            "viewAngleDeg": view_angle_deg(tag_pose),
            "ambiguous": bool(tag_ambiguous),
        }

    lines: list[dict] = []
    correction_dict = None
    if error is None and correction is not None:
        lines = summarize_lines(line_frames)
        module_tag = pose_to_dict(correction.pose_module_tag)
        spread = spread or {"spreadMm": 0.0, "spreadDeg": 0.0}
        correction_dict = {
            "dofs": correction.dofs,
            "deltaMm": [float(value) * 1000.0 for value in correction.delta_xy_m],
            "deltaRotDeg": float(correction.delta_rot_deg),
            "moduleTag": module_tag,
            "position": list(module_tag["position"]),
            "rotationDeg": [float(value) for value in catalog_rpy_deg(correction.pose_module_tag)],
            "spreadMm": float(spread["spreadMm"]),
            "spreadDeg": float(spread["spreadDeg"]),
            "residualMm": float(correction.residual_m) * 1000.0,
        }

    return {
        "schema": RESULT_SCHEMA,
        "capturedAt": _iso(captured_at) if captured_at else None,
        "capturedUntil": _iso(captured_until) if captured_until else None,
        "imageSize": list(image_size) if image_size else None,
        "intrinsicsSource": intrinsics_source,
        "framesUsed": int(frames_used),
        "tag": tag,
        "lines": lines,
        "correction": correction_dict,
        "warnings": list(warnings),
        "error": error,
        "debugDir": debug_dir,
    }


def build_view(plan=None, *, error: str | None = None) -> dict:
    """`wsc.vision.tagreference.view/1` aus einem `ViewPlan`; ohne Plan nur `error`.

    `camInTag`: Kamerapose im Tag-KS (OpenCV-Kamera-KS, +Z aus der Linse).
    """
    from tagloc.geometry import pose_to_dict

    return {
        "schema": VIEW_SCHEMA,
        "camInTag": pose_to_dict(plan.pose_tag_cam) if plan is not None else None,
        "standoffM": float(plan.standoff_m) if plan is not None else None,
        "fits": bool(plan.fits) if plan is not None else False,
        "uncovered": list(plan.uncovered) if plan is not None else [],
        "error": error,
    }


def dumps(result: dict) -> str:
    return json.dumps(result, allow_nan=False, default=float)
