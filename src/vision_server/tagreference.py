"""MeasureTagReference / PlanTagReferenceView: measure the tag position via reference edges.

The folding-rule replacement: the controller (webskillcomposition) sends the
catalog start value `T_module_tag` and the module's reference edges (3D segments
in the module frame). The hand Pi takes fresh images, detects the tag, measures
the edges in the same image, and returns the `T_module_tag` corrected in the tag
plane -- without robot kinematics and without hand-eye error. The math lives in
`tagloc.reference_edges`.

This module holds no asyncua and no camera: it parses the request, builds the
result JSON, and provides the small pure helpers in between (tag pose across
images, edge summary, warnings). See
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
#: Tilt angle of the planning view. Head-on (0 degrees) the tag pose tends to flip
#: into the mirror solution; already 10 degrees makes it unambiguous.
DEFAULT_TILT_DEG = 10.0
#: Upper limit as in `plan_reference_view` (there: < 80 degrees).
MAX_TILT_DEG = 80.0
MAX_LINES = 4
MAX_LINE_EXTENT_M = 1.0

NO_FRAME = "NO_FRAME"
TAG_NOT_SEEN = "TAG_NOT_SEEN"


@dataclass(frozen=True)
class TagReferenceRequest:
    """A validated request (for both methods)."""

    tag_id: int
    #: Size from the request, metres; `None` = from the tag map or the default.
    tag_size_m: float | None
    #: Start value `T_module_tag` as a 4x4 pose.
    pose_module_tag_guess: np.ndarray
    #: `tagloc.reference_edges.ReferenceLine` in the module frame, metres.
    lines: tuple
    search_band_m: float = DEFAULT_SEARCH_BAND_M
    frames: int = DEFAULT_FRAMES
    line_extent_m: float = DEFAULT_LINE_EXTENT_M
    tilt_deg: float = DEFAULT_TILT_DEG
    #: Effective overlay layers of the debug view (all keys from
    #: `tagloc.reference_overlay.LAYER_KEYS`; missing ones are `True`).
    overlay: dict = field(default_factory=dict)
    #: `MeasureTagReference`: write the measurement log to disk.
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
    """Read a `wsc.vision.tagreference.request/1`; raise ValueError with a plain message.

    Required: `tagId`, `moduleTagGuess`, `lines` (1 to 4). Optional: `overlay`
    (debug view layers) and `saveDebug` (measurement log). Unknown fields are
    ignored.
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


# -- Tag pose across several images ---------------------------------------------


def combine_tag_pose(tag_poses: Sequence) -> tuple[np.ndarray, bool]:
    """Combine one tag pose from the images in which the tag was visible.

    The robot stands still during the measurement, so the pose is the same in all
    images. As in `MeasureTags`: the position is the mean over all images, the
    orientation only over the unambiguous ones; if there are none, over all of
    them -- and then the returned flag is `ambiguous=True` (a mirror solution is
    possible and the rotation is not reliable).
    """
    from tagloc.geometry import average_poses

    clear = [item for item in tag_poses if not item.is_ambiguous]
    pose = np.array(average_poses([item.pose_cam_tag for item in (clear or tag_poses)]), dtype=np.float64)
    pose[:3, 3] = np.mean([np.asarray(item.pose_cam_tag)[:3, 3] for item in tag_poses], axis=0)
    return pose, not clear


def keep_guess_rotation(correction, pose_module_tag_guess):
    """Keep only the position of the correction; take the rotation from the start value.

    For an ambiguous tag: its rotation (and hence the tilt of the tag plane) is not
    reliable, but the location of its origin is. `fit_in_plane_correction` returns
    `D` (rotation `theta` about tag z and translation `t` at the tag origin); the
    new tag origin lies in the old tag frame at `s = -R(theta)^T t`. Here the
    orientation of the start value stays, only the origin moves to `s`:
    `T_module_tag = T_module_tag_guess * translation(s)`. `delta_xy_m` is then
    `-s` (pure translation, `delta_rot_deg = 0`), i.e. the same `D` without
    rotation. With `dofs == "none"` everything stays unchanged.
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


# -- Result --------------------------------------------------------------------


def _mean(values) -> float | None:
    values = [float(value) for value in values if value is not None and math.isfinite(value)]
    return float(np.mean(values)) if values else None


def summarize_lines(per_frame: Sequence[Sequence]) -> list[dict]:
    """One entry per edge across all images (`LineMeasurement` per image).

    * `found`: found in at least one image
    * Values (`offsetMm`, `angleDeg`, `supportPx`, ...): mean over the images in
      which the edge was unambiguous; if there were none, over the found ones
    * `ambiguous`: ambiguous in at least one image (reported conservatively; the
      correction only uses the edge in images where it is unambiguous)
    * `candidates`: the largest candidate count
    * `reason`: only for edges not found, from the last image
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
    """Return plain-text notes about the result. Empty = nothing noticed."""
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
    """Build the result JSON (`wsc.vision.tagreference.result/1`) as a dict.

    With `error` (or without a correction): `correction: null`, `lines: []`. A bare
    `build_result(error="...")` is valid too -- this is how rejections before the
    measurement are produced.

    `tag_poses`: `TagPose` of the images used (reprojection error); `tag_pose`:
    the `T_cam_tag` averaged from them; `line_frames`: `LineMeasurement` lists per
    image; `correction` / `spread` as returned by `average_corrections`.
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
    """Build a `wsc.vision.tagreference.view/1` from a `ViewPlan`; without a plan only `error`.

    `camInTag`: camera pose in the tag frame (OpenCV camera frame, +Z out of the lens).
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
