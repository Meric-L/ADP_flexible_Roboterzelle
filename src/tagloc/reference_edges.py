"""Tag-Position auf dem Modul aus Bezugskanten im Kamerabild einmessen.

Der Zollstock-Ersatz: Der Modulkatalog liefert `T_module_tag` als Startwert,
dazu zwei physische Bezugskanten des Moduls (Tischkante, Profil-Stirnkante ...,
3D-Strecken im Modul-KS). Die Hand-Kamera sieht Tag und Kanten im **selben**
Bild, also laesst sich alles im Tag-KS messen -- ohne Roboterkinematik und
ohne Hand-Auge-Fehler. Aus der Lage der Kanten relativ zum Tag wird
`T_module_tag` **in der Tag-Ebene** korrigiert (x, y, Drehung um Tag-z).
Neigung und Abstand zur Flaeche bleiben aus dem Startwert (Tag liegt buendig).

Ablauf je Bild (Details am jeweiligen Baustein):

1. `measure_reference_lines`: Kante ueber den Startwert ins Tag-KS, ins Bild
   projizieren (mit Verzeichnung), Querprofile, Gradientenmaxima, RANSAC-Gerade,
   Sehstrahlen mit der Kantenhoehe schneiden -> gemessene 3D-Gerade im Tag-KS.
2. `fit_in_plane_correction`: starre 2D-Transformation `D` in der Tag-Ebene,
   die die Startwert-Kanten auf die gemessenen abbildet.
3. `average_corrections`: mehrere Bilder mitteln, Streuung melden.

`plan_reference_view` rechnet vorab die Kamerapose im Tag-KS, aus der Tag und
Kantenstuecke ins Bild passen. `catalog_rpy_deg` wandelt eine Rotation ins
Katalogformat (`rotationDeg`).

Konventionen wie im ganzen Projekt (`tagloc.geometry`): `T_a_b` ist die Pose
von b im KS a, Meter, Radiant intern. Tag-KS: +X rechts, +Y Oberkante, +Z aus
dem Tag. Kamera-KS: OpenCV (+Z aus der Linse, +X rechts, +Y unten).

`cv2` wird nur innerhalb der Funktionen importiert.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from . import lens
from .calibration import CameraCalibration, check_resolution
from .geometry import (
    Pose,
    average_poses,
    compose,
    from_rotation_translation,
    invert,
    to_rvec_tvec,
)
from .pose import tag_object_points

#: Kuerzere Bezugskanten sind als Richtungsgeber unbrauchbar.
MIN_LINE_LENGTH_M = 0.02

#: Kanten, die weniger als so viel Grad von der Tag-Normalen abweichen,
#: liegen "senkrecht zur Tag-Ebene": ihre Lage in der Ebene ist im
#: Bild nicht bestimmt.
PERPENDICULAR_LIMIT_DEG = 10.0

#: Mindestwinkel zwischen zwei Kanten fuer die volle 3-DoF-Korrektur.
MIN_ANGLE_FULL_DEG = 20.0

#: Gruende fuer `LineMeasurement.reason`.
REASON_NOT_VISIBLE = "NOT_VISIBLE"
REASON_NO_EDGE = "NO_EDGE"
REASON_PERPENDICULAR = "PERPENDICULAR_TO_TAG"

# --- Parameter der Kantensuche (Bildraum) -----------------------------------

#: Abstand der Querprofile entlang der projizierten Kante, in Pixeln.
_PROFILE_SPACING_PX = 4.0
#: Schrittweite innerhalb eines Querprofils, in Pixeln.
_PROFILE_STEP_PX = 0.5
#: Glaettung des Graubilds vor dem Ableiten (Rauschen vs. Kantenlage).
_SMOOTH_SIGMA_PX = 1.2
#: Weniger sichtbare Kante (projizierte Laenge) gilt als nicht sichtbar.
_MIN_VISIBLE_PX = 40.0
#: Mindest-Helligkeitssprung (Graustufen, ueber +-2,5 px) fuer einen Kandidaten.
_MIN_CONTRAST = 8.0
#: Hoechstens so viele Gradientenmaxima je Querprofil.
_PEAKS_PER_PROFILE = 3
#: Mindest-Bandbreite der Suche in Pixeln, falls die Umrechnung zu klein ausfaellt.
_MIN_BAND_PX = 6.0
#: Stuetzstellen der Sichtbarkeitsabtastung entlang der Kante.
_DENSE_SAMPLES = 300
#: Rand, den ein sichtbarer Kantenpunkt zum Bildrand halten muss.
_IMAGE_MARGIN_PX = 3.0

_RANSAC_ITERATIONS = 200
_RANSAC_MIN_PAIR_PX = 15.0
_RANSAC_SEED = 20261020
#: Abstand eines Punkts zur Geraden, bis zu dem er als Stuetzpunkt zaehlt.
_INLIER_PX = 1.0
#: Beim Suchen weiterer Kandidaten werden Punkte so nah an einer
#: gefundenen Geraden (gleiche Polaritaet) entfernt.
_REMOVE_PX = 2.5
_MAX_CANDIDATES = 4
#: Ein Kandidat, dessen Richtung mehr als so viel vom Startwert abweicht,
#: ist eine andere Kante.
_MAX_ANGLE_TO_GUESS_DEG = 15.0
#: Zweiter Kandidat mit mindestens diesem Anteil der Bewertung, ...
_AMBIGUITY_SCORE_RATIO = 0.7
#: ... und mehr als so viel Pixel Abstand macht die Messung mehrdeutig.
_AMBIGUITY_DISTANCE_PX = 2.0
#: Standard-Ausdehnung einer Kante, wenn die Messung keine meldet (Meter).
_DEFAULT_EXTENT_M = 0.05


@dataclass(frozen=True)
class ReferenceLine:
    """Bezugskante: 3D-Strecke im Modul-KS, in Metern."""

    id: str
    start: np.ndarray
    end: np.ndarray


@dataclass(frozen=True)
class LineMeasurement:
    """Messergebnis einer Bezugskante, im Tag-KS.

    `guess_*` ist der Startwert (aus `T_module_tag_guess`), `point_tag` /
    `direction_tag` die gemessene Gerade. `guess_point_tag` ist der Punkt der
    Startwert-Kante, bei dem die Messung stuetzt (Mitte der Stuetzpunkte);
    bei nicht gefundenen Kanten die Mitte des sichtbaren Teils.
    `offset_m` ist die senkrechte Verschiebung in der Tag-Ebene bei diesem
    Punkt (gemessen minus Startwert, positiv zur linken Normalen
    `(-dy, dx)` der Startwert-Richtung), `angle_deg` die Drehung der
    Richtung um Tag-z (gemessen minus Startwert).
    """

    id: str
    found: bool
    guess_point_tag: np.ndarray
    guess_direction_tag: np.ndarray
    point_tag: np.ndarray | None
    direction_tag: np.ndarray | None
    offset_m: float | None
    angle_deg: float | None
    support_px: float
    residual_px: float
    contrast: float
    candidates: int
    ambiguous: bool
    #: "NOT_VISIBLE" | "NO_EDGE" | "PERPENDICULAR_TO_TAG" | None
    reason: str | None
    #: Laenge des gemessenen Kantenstuecks in der Tag-Ebene (Meter). Gewichtet
    #: die Drehung in der Korrektur: kurze Kante, kurzer Hebelarm.
    extent_m: float = 0.0


@dataclass(frozen=True)
class InPlaneCorrection:
    """Starre Korrektur `D` in der Tag-Ebene: `x' = R(theta) x + delta_xy`.

    `D` wirkt im Tag-KS auf die Modulgeometrie:
    `T_tag_module_neu = D * T_tag_module_guess`.
    """

    #: "full" (x, y, Drehung), "partial" (nur senkrecht zur Kante + Drehung)
    #: oder "none" (Startwert unveraendert).
    dofs: str
    delta_xy_m: np.ndarray
    delta_rot_deg: float
    #: Korrigiertes `T_module_tag`.
    pose_module_tag: Pose
    #: RMS der verbleibenden Punkt-zu-Gerade-Abstaende in der Tag-Ebene.
    residual_m: float


@dataclass(frozen=True)
class ViewPlan:
    """Kamerapose im Tag-KS, aus der Tag und Kantenstuecke ins Bild passen."""

    pose_tag_cam: Pose
    standoff_m: float
    fits: bool
    #: IDs der Kanten (ggf. "tag"), die bei `max_standoff_m` nicht ins Bild passen.
    uncovered: list[str]


# --------------------------------------------------------------------------
# Eingaben pruefen
# --------------------------------------------------------------------------


def _vector3(value, name: str) -> np.ndarray:
    try:
        vector = np.asarray(value, dtype=np.float64).reshape(3)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}: erwartet einen 3-Vektor") from error
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name}: nicht endlich")
    return vector


def _pose(value, name: str) -> Pose:
    try:
        pose = np.asarray(value, dtype=np.float64).reshape(4, 4)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}: erwartet eine 4x4-Pose") from error
    if not np.all(np.isfinite(pose)):
        raise ValueError(f"{name}: nicht endlich")
    return pose


def _positive(value, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} muss endlich und > 0 sein (ist {value})")
    return number


def _checked_lines(lines: Sequence[ReferenceLine]) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Prueft die Kanten und gibt `(id, start, end)` als Arrays zurueck."""
    checked = []
    for line in lines:
        start = _vector3(line.start, f"Kante {line.id}: start")
        end = _vector3(line.end, f"Kante {line.id}: end")
        if float(np.linalg.norm(end - start)) < MIN_LINE_LENGTH_M:
            raise ValueError(
                f"Kante {line.id} ist kuerzer als {MIN_LINE_LENGTH_M * 100:.0f} cm"
            )
        checked.append((str(line.id), start, end))
    return checked


def _as_gray(gray) -> np.ndarray:
    image = np.asarray(gray)
    if image.ndim == 3 and image.shape[2] == 3:
        import cv2

        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim != 2 or image.size == 0:
        raise ValueError("Graubild erwartet (H, W) oder BGR (H, W, 3)")
    return image


def _to_tag(pose_tag_module: Pose, point_module: np.ndarray) -> np.ndarray:
    return pose_tag_module[:3, :3] @ point_module + pose_tag_module[:3, 3]


# --------------------------------------------------------------------------
# Kante im Bild finden
# --------------------------------------------------------------------------


def _fit_line_2d(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Totale kleinste Quadrate: `(Schwerpunkt, Einheitsrichtung)`."""
    centre = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centre, full_matrices=False)
    return centre, vt[0]


def _line_distance(points: np.ndarray, centre: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Vorzeichenbehafteter Abstand der Punkte zur Geraden (links positiv)."""
    delta = points - centre
    return direction[0] * delta[:, 1] - direction[1] * delta[:, 0]


def _ransac_one(points, signs, indices, rng):
    """Beste Gerade (meiste Stuetzpunkte gleicher Polaritaet) unter `indices`.

    Gibt `(Stuetzindizes, Schwerpunkt, Richtung)` zurueck oder `None`.
    """
    subset = points[indices]
    sub_signs = signs[indices]
    count = len(indices)
    if count < 2:
        return None
    best_inliers = None
    best_count = 0
    for _ in range(_RANSAC_ITERATIONS):
        first = int(rng.integers(count))
        same = np.nonzero(sub_signs == sub_signs[first])[0]
        if same.size < 2:
            continue
        second = int(same[int(rng.integers(same.size))])
        delta = subset[second] - subset[first]
        length = float(np.linalg.norm(delta))
        if length < _RANSAC_MIN_PAIR_PX:
            continue
        direction = delta / length
        distance = np.abs(_line_distance(subset, subset[first], direction))
        inliers = (distance < _INLIER_PX) & (sub_signs == sub_signs[first])
        total = int(inliers.sum())
        if total > best_count:
            best_count = total
            best_inliers = inliers
    if best_inliers is None or best_count < 2:
        return None
    sign = sub_signs[np.nonzero(best_inliers)[0][0]]
    inliers = best_inliers
    centre, direction = _fit_line_2d(subset[inliers])
    for _ in range(3):
        distance = np.abs(_line_distance(subset, centre, direction))
        refined = (distance < _INLIER_PX) & (sub_signs == sign)
        if refined.sum() < 2:
            break
        inliers = refined
        centre, direction = _fit_line_2d(subset[inliers])
    return indices[np.nonzero(inliers)[0]], centre, direction


def _ransac_lines(points, signs, rng, min_inliers):
    """Mehrere Geradenkandidaten nacheinander: `[(Stuetzindizes, Schwerpunkt, Richtung)]`."""
    remaining = np.arange(len(points))
    found = []
    for _ in range(_MAX_CANDIDATES):
        if remaining.size < min_inliers:
            break
        best = _ransac_one(points, signs, remaining, rng)
        if best is None or best[0].size < min_inliers:
            break
        found.append(best)
        indices, centre, direction = best
        sign = signs[indices[0]]
        distance = np.abs(_line_distance(points[remaining], centre, direction))
        drop = (distance < _REMOVE_PX) & (signs[remaining] == sign)
        remaining = remaining[~drop]
    return found


def _tag_mask(calibration, pose_cam_tag, tag_size_m, margin_m, shape) -> np.ndarray:
    """Maske der Tag-Flaeche plus Rand im Bild (True = ausgeblendet)."""
    import cv2

    half = tag_size_m / 2.0 + margin_m
    steps = 40
    along = np.linspace(-half, half, steps, endpoint=False)
    outline = np.concatenate(
        [
            np.column_stack([along, np.full(steps, half)]),
            np.column_stack([np.full(steps, half), -along]),
            np.column_stack([-along, np.full(steps, -half)]),
            np.column_stack([np.full(steps, -half), along]),
        ]
    )
    points = np.column_stack([outline, np.zeros(len(outline))])
    camera = points @ pose_cam_tag[:3, :3].T + pose_cam_tag[:3, 3]
    mask = np.zeros(shape, dtype=np.uint8)
    if np.any(camera[:, 2] <= 1e-3):
        return mask.astype(bool)
    rvec, tvec = to_rvec_tvec(pose_cam_tag)
    projected = lens.project_points(points, rvec, tvec, calibration)
    if not np.all(np.isfinite(projected)):
        return mask.astype(bool)
    cv2.fillPoly(mask, [np.round(projected).astype(np.int32)], 1)
    return mask.astype(bool)


def _longest_run(flags: np.ndarray) -> tuple[int, int] | None:
    """Erster und letzter Index der laengsten Folge von True, sonst `None`."""
    best = None
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        if (not flag or index == len(flags) - 1) and start is not None:
            end = index if flag else index - 1
            if best is None or end - start > best[1] - best[0]:
                best = (start, end)
            start = None
    return best


def _band_px(calibration, pose_cam_tag, centre_tag, lateral_tag, band_m) -> float:
    """Suchband in Pixeln: die Strecke `band_m` quer zur Kante, projiziert."""
    rvec, tvec = to_rvec_tvec(pose_cam_tag)
    points = np.array(
        [centre_tag, centre_tag + band_m * lateral_tag, centre_tag - band_m * lateral_tag]
    )
    camera = points @ pose_cam_tag[:3, :3].T + pose_cam_tag[:3, 3]
    if np.all(camera[:, 2] > 1e-3):
        projected = lens.project_points(points, rvec, tvec, calibration)
        spread = max(
            float(np.linalg.norm(projected[1] - projected[0])),
            float(np.linalg.norm(projected[2] - projected[0])),
        )
        if math.isfinite(spread):
            return max(spread, _MIN_BAND_PX)
    focal = float(np.asarray(calibration.camera_matrix)[0, 0])
    return max(band_m * focal / max(float(camera[0, 2]), 1e-3), _MIN_BAND_PX)


def _failed(line_id, guess_point, guess_direction, reason, candidates=0, ambiguous=False):
    return LineMeasurement(
        id=line_id,
        found=False,
        guess_point_tag=guess_point,
        guess_direction_tag=guess_direction,
        point_tag=None,
        direction_tag=None,
        offset_m=None,
        angle_deg=None,
        support_px=0.0,
        residual_px=0.0,
        contrast=0.0,
        candidates=candidates,
        ambiguous=ambiguous,
        reason=reason,
    )


def _collect_edge_points(blurred, mask, positions, normals, band_px):
    """Gradientenmaxima aller Querprofile, subpixelgenau.

    Gibt `(Bildpunkte (N, 2), Polaritaet (N,), Kontrast (N,), Profilindex (N,))`
    zurueck, Bildpunkte noch verzeichnet.
    """
    import cv2

    height, width = blurred.shape
    offsets = np.arange(-band_px, band_px + 1e-9, _PROFILE_STEP_PX)
    map_x = (positions[:, 0:1] + offsets[None, :] * normals[:, 0:1]).astype(np.float32)
    map_y = (positions[:, 1:2] + offsets[None, :] * normals[:, 1:2]).astype(np.float32)
    samples = cv2.remap(
        blurred, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    ).astype(np.float64)
    inside = (map_x >= 0) & (map_x <= width - 1) & (map_y >= 0) & (map_y <= height - 1)
    gradient = np.gradient(samples, _PROFILE_STEP_PX, axis=1)
    magnitude = np.abs(gradient)
    half = int(round(2.5 / _PROFILE_STEP_PX))
    columns = len(offsets)

    points: list[np.ndarray] = []
    signs: list[float] = []
    contrasts: list[float] = []
    profile_index: list[int] = []
    for row in range(len(positions)):
        core = magnitude[row, 1:-1]
        peaks = np.nonzero((core > magnitude[row, :-2]) & (core >= magnitude[row, 2:]))[0] + 1
        peaks = peaks[(peaks >= half) & (peaks < columns - half)]
        chosen = []
        for column in peaks:
            if not inside[row, column - half : column + half + 1].all():
                continue
            contrast = abs(samples[row, column + half] - samples[row, column - half])
            if contrast < _MIN_CONTRAST:
                continue
            chosen.append((magnitude[row, column], int(column), contrast))
        chosen.sort(reverse=True)
        for _, column, contrast in chosen[:_PEAKS_PER_PROFILE]:
            before, centre, after = magnitude[row, column - 1 : column + 2]
            denominator = before - 2.0 * centre + after
            shift = 0.5 * (before - after) / denominator if denominator < -1e-12 else 0.0
            shift = float(np.clip(shift, -0.5, 0.5))
            offset = offsets[column] + shift * _PROFILE_STEP_PX
            point = positions[row] + offset * normals[row]
            column_px, row_px = int(round(point[0])), int(round(point[1]))
            if mask[min(max(row_px, 0), height - 1), min(max(column_px, 0), width - 1)]:
                continue
            points.append(point)
            signs.append(1.0 if gradient[row, column] > 0 else -1.0)
            contrasts.append(contrast)
            profile_index.append(row)
    if not points:
        return np.zeros((0, 2)), np.zeros(0), np.zeros(0), np.zeros(0, dtype=int)
    return (
        np.array(points),
        np.array(signs),
        np.array(contrasts),
        np.array(profile_index, dtype=int),
    )


def _measure_one(
    line_id,
    start_module,
    end_module,
    gray_blurred,
    mask,
    calibration,
    pose_cam_tag,
    pose_tag_cam,
    pose_tag_module,
    search_band_m,
):
    """Eine Bezugskante messen."""
    start_tag = _to_tag(pose_tag_module, start_module)
    end_tag = _to_tag(pose_tag_module, end_module)
    direction = end_tag - start_tag
    length = float(np.linalg.norm(direction))
    unit = direction / length
    middle = start_tag + 0.5 * direction

    if abs(float(unit[2])) > math.cos(math.radians(PERPENDICULAR_LIMIT_DEG)):
        return _failed(line_id, middle, unit, REASON_PERPENDICULAR)

    height, width = gray_blurred.shape
    rvec, tvec = to_rvec_tvec(pose_cam_tag)
    rotation = pose_cam_tag[:3, :3]

    # Sichtbaren Teil der Kante bestimmen.
    parameters = np.linspace(0.0, 1.0, _DENSE_SAMPLES)
    dense = start_tag + parameters[:, None] * direction
    camera = dense @ rotation.T + pose_cam_tag[:3, 3]
    in_front = camera[:, 2] > 1e-3
    pixels = np.full((_DENSE_SAMPLES, 2), np.nan)
    if in_front.any():
        pixels[in_front] = lens.project_points(dense[in_front], rvec, tvec, calibration)
    visible = (
        in_front
        & np.all(np.isfinite(pixels), axis=1)
        & (pixels[:, 0] >= _IMAGE_MARGIN_PX)
        & (pixels[:, 0] <= width - 1 - _IMAGE_MARGIN_PX)
        & (pixels[:, 1] >= _IMAGE_MARGIN_PX)
        & (pixels[:, 1] <= height - 1 - _IMAGE_MARGIN_PX)
    )
    run = _longest_run(visible)
    if run is None:
        return _failed(line_id, middle, unit, REASON_NOT_VISIBLE)
    first, last = run
    segment = pixels[first : last + 1]
    steps = np.linalg.norm(np.diff(segment, axis=0), axis=1)
    arclength = np.concatenate([[0.0], np.cumsum(steps)])
    total = float(arclength[-1])
    if total < _MIN_VISIBLE_PX:
        return _failed(line_id, middle, unit, REASON_NOT_VISIBLE)
    visible_middle = start_tag + 0.5 * (parameters[first] + parameters[last]) * direction

    # Querprofile alle ~4 px entlang der projizierten Kante.
    count = int(total // _PROFILE_SPACING_PX) + 1
    wanted = np.linspace(0.0, total, count)
    profile_parameters = np.interp(wanted, arclength, parameters[first : last + 1])
    profile_points = start_tag + profile_parameters[:, None] * direction
    positions = lens.project_points(profile_points, rvec, tvec, calibration)
    tangents = np.gradient(positions, axis=0)
    norms = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangents = tangents / np.maximum(norms, 1e-12)
    normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])

    lateral = np.array([-unit[1], unit[0], 0.0])
    lateral_norm = float(np.linalg.norm(lateral))
    lateral = lateral / lateral_norm if lateral_norm > 1e-9 else np.array([1.0, 0.0, 0.0])
    band_px = _band_px(calibration, pose_cam_tag, visible_middle, lateral, search_band_m)

    edge_points, signs, contrasts, profile_index = _collect_edge_points(
        gray_blurred, mask, positions, normals, band_px
    )
    min_inliers = max(5, int(0.2 * count))
    if len(edge_points) < min_inliers:
        return _failed(line_id, visible_middle, unit, REASON_NO_EDGE)

    undistorted = lens.undistort_points(edge_points, calibration)

    # Startwert-Gerade im idealen (entzerrten) Bild, fuer Naehe und Richtung.
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    ends = np.array([profile_points[0], profile_points[-1]]) @ rotation.T + pose_cam_tag[:3, 3]
    ideal = np.column_stack(
        [
            matrix[0, 0] * ends[:, 0] / ends[:, 2] + matrix[0, 2],
            matrix[1, 1] * ends[:, 1] / ends[:, 2] + matrix[1, 2],
        ]
    )
    guess_direction_px = ideal[1] - ideal[0]
    guess_direction_px = guess_direction_px / max(float(np.linalg.norm(guess_direction_px)), 1e-12)

    rng = np.random.default_rng(_RANSAC_SEED)
    spacing = total / max(count - 1, 1)
    candidates = []
    for indices, centre, direction_px in _ransac_lines(undistorted, signs, rng, min_inliers):
        cosine = abs(float(direction_px @ guess_direction_px))
        angle = math.degrees(math.acos(min(1.0, cosine)))
        if angle > _MAX_ANGLE_TO_GUESS_DEG:
            continue
        distance_to_guess = abs(float(_line_distance(centre[None, :], ideal[0], guess_direction_px)[0]))
        proximity = 1.0 - 0.5 * min(distance_to_guess / band_px, 1.0)
        support = len(indices) * spacing
        contrast = float(contrasts[indices].mean())
        residual = float(
            np.sqrt(np.mean(_line_distance(undistorted[indices], centre, direction_px) ** 2))
        )
        candidates.append(
            {
                "indices": indices,
                "centre": centre,
                "direction": direction_px,
                "support": support,
                "contrast": contrast,
                "residual": residual,
                "score": proximity * support * contrast,
            }
        )
    if not candidates:
        return _failed(line_id, visible_middle, unit, REASON_NO_EDGE)
    candidates.sort(key=lambda item: -item["score"])
    best = candidates[0]
    ambiguous = any(
        other["score"] >= _AMBIGUITY_SCORE_RATIO * best["score"]
        and abs(float(_line_distance(best["centre"][None, :], other["centre"], other["direction"])[0]))
        > _AMBIGUITY_DISTANCE_PX
        for other in candidates[1:]
    )

    # Sehstrahlen der Stuetzpunkte mit der Ebene der Kantenhoehe schneiden.
    indices = best["indices"]
    inverse_matrix = np.linalg.inv(matrix)
    homogeneous = np.column_stack([undistorted[indices], np.ones(len(indices))])
    rays = homogeneous @ inverse_matrix.T @ pose_tag_cam[:3, :3].T
    origin = pose_tag_cam[:3, 3]
    inlier_parameters = profile_parameters[profile_index[indices]]
    plane_z = start_tag[2] + inlier_parameters * direction[2]
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = (plane_z - origin[2]) / rays[:, 2]
    valid = np.isfinite(scale) & (np.abs(rays[:, 2]) > 1e-6) & (scale > 0.0)
    if valid.sum() < 3:
        return _failed(line_id, visible_middle, unit, REASON_NO_EDGE, len(candidates), ambiguous)
    hits = origin + scale[valid, None] * rays[valid]
    guess_point = start_tag + float(inlier_parameters[valid].mean()) * direction

    point = hits.mean(axis=0)
    _, _, vt = np.linalg.svd(hits - point, full_matrices=False)
    measured_direction = vt[0]
    if float(measured_direction @ unit) < 0.0:
        measured_direction = -measured_direction
    extent = float(np.ptp((hits - point) @ measured_direction))

    plane_direction = measured_direction[:2]
    plane_norm = float(np.linalg.norm(plane_direction))
    guess_plane = unit[:2] / float(np.linalg.norm(unit[:2]))
    if plane_norm < 1e-6:
        return _failed(line_id, visible_middle, unit, REASON_NO_EDGE, len(candidates), ambiguous)
    measured_plane = plane_direction / plane_norm
    left = np.array([-guess_plane[1], guess_plane[0]])
    measured_left = np.array([-measured_plane[1], measured_plane[0]])
    offset = float(measured_left @ (point[:2] - guess_point[:2])) / float(measured_left @ left)
    angle = math.degrees(
        math.atan2(
            guess_plane[0] * measured_plane[1] - guess_plane[1] * measured_plane[0],
            float(guess_plane @ measured_plane),
        )
    )
    return LineMeasurement(
        id=line_id,
        found=True,
        guess_point_tag=guess_point,
        guess_direction_tag=unit,
        point_tag=point,
        direction_tag=measured_direction,
        offset_m=offset,
        angle_deg=angle,
        support_px=float(best["support"]),
        residual_px=float(best["residual"]),
        contrast=float(best["contrast"]),
        candidates=len(candidates),
        ambiguous=ambiguous,
        reason=None,
        extent_m=extent,
    )


def measure_reference_lines(
    gray,
    calib: CameraCalibration,
    pose_cam_tag: Pose,
    pose_module_tag_guess: Pose,
    lines: Sequence[ReferenceLine],
    *,
    tag_size_m: float,
    search_band_m: float = 0.015,
    tag_margin_m: float = 0.01,
) -> list[LineMeasurement]:
    """Misst die Bezugskanten im Bild, im Tag-KS.

    `gray`: Graubild (oder BGR), Aufloesung wie `calib`. `pose_cam_tag`:
    `T_cam_tag` aus der Tag-Erkennung. `pose_module_tag_guess`: Startwert
    `T_module_tag` aus dem Katalog; er bestimmt, wo im Bild gesucht wird
    (Band +-`search_band_m` quer zur projizierten Kante). Die Tag-Flaeche plus
    `tag_margin_m` wird ausgeblendet, weil die Tag-Raender selbst starke
    Kanten sind.

    Je Kante genau ein `LineMeasurement`, in der Reihenfolge von `lines`.
    Nicht messbare Kanten kommen mit `found=False` und `reason` zurueck.
    Mehrdeutige Kanten (zweite, gleich gute Kante im Band, z. B. eine
    Item-Nut) sind `found=True, ambiguous=True` und gehen nicht in die
    Korrektur ein.

    Hoehe der Kante: kommt aus dem Startwert. Ein Fehler darin verschiebt die
    gemessene Kante seitlich um etwa `delta_h * tan(Sehwinkel)`.

    Wirft `ValueError` bei ungueltigen Eingaben (Kante kuerzer 2 cm, Groessen
    <= 0, nicht endliche Werte, Auflosung passt nicht zur Kalibrierung).
    """
    import cv2

    tag_size = _positive(tag_size_m, "tag_size_m")
    band = _positive(search_band_m, "search_band_m")
    margin = float(tag_margin_m)
    if not math.isfinite(margin) or margin < 0.0:
        raise ValueError(f"tag_margin_m muss endlich und >= 0 sein (ist {tag_margin_m})")
    pose_cam_tag = _pose(pose_cam_tag, "pose_cam_tag")
    guess = _pose(pose_module_tag_guess, "pose_module_tag_guess")
    checked = _checked_lines(lines)
    image = _as_gray(gray)
    height, width = image.shape
    check_resolution(calib, (width, height))

    pose_tag_module = invert(guess)
    pose_tag_cam = invert(pose_cam_tag)
    blurred = cv2.GaussianBlur(image.astype(np.float32), (0, 0), _SMOOTH_SIGMA_PX)
    mask = _tag_mask(calib, pose_cam_tag, tag_size, margin, image.shape)
    return [
        _measure_one(
            line_id,
            start,
            end,
            blurred,
            mask,
            calib,
            pose_cam_tag,
            pose_tag_cam,
            pose_tag_module,
            band,
        )
        for line_id, start, end in checked
    ]


# --------------------------------------------------------------------------
# Korrektur in der Tag-Ebene
# --------------------------------------------------------------------------


def _rotation_2d(theta: float) -> np.ndarray:
    cosine, sine = math.cos(theta), math.sin(theta)
    return np.array([[cosine, -sine], [sine, cosine]])


def _in_plane_transform(theta: float, translation_xy: np.ndarray) -> Pose:
    """`D` als 4x4: Drehung um Tag-z, Verschiebung in der Tag-Ebene."""
    rotation = np.eye(3)
    rotation[:2, :2] = _rotation_2d(theta)
    return from_rotation_translation(rotation, [translation_xy[0], translation_xy[1], 0.0])


def _is_usable(measurement: LineMeasurement) -> bool:
    return (
        measurement.found
        and not measurement.ambiguous
        and measurement.reason is None
        and measurement.point_tag is not None
        and measurement.direction_tag is not None
    )


def _unit_xy(vector: np.ndarray) -> np.ndarray:
    plane = np.asarray(vector, dtype=np.float64)[:2]
    return plane / float(np.linalg.norm(plane))


def _solve_in_plane(samples, normals, offsets, pivot, normal_ref):
    """Gauss-Newton fuer `D(q) = R(theta)(q - pivot) + pivot + shift`.

    `normal_ref=None`: freie Verschiebung (x, y, theta). Sonst nur entlang
    `normal_ref` (Unbekannte: Verschiebung `u`, theta). Residuum je Stuetzpunkt:
    Abstand von `D(q)` zur gemessenen Geraden `normal . x = offset`.
    Rueckgabe `(theta, Verschiebung (2,), RMS-Residuum)`.
    """
    theta = 0.0
    shift = np.zeros(2)
    relative = samples - pivot
    for _ in range(30):
        rotation = _rotation_2d(theta)
        derivative = np.array(
            [[-math.sin(theta), -math.cos(theta)], [math.cos(theta), -math.sin(theta)]]
        )
        moved = relative @ rotation.T + pivot + shift
        residual = np.einsum("ij,ij->i", normals, moved) - offsets
        d_theta = np.einsum("ij,ij->i", normals, relative @ derivative.T)
        if normal_ref is None:
            jacobian = np.column_stack([d_theta, normals[:, 0], normals[:, 1]])
        else:
            jacobian = np.column_stack([d_theta, normals @ normal_ref])
        step, *_ = np.linalg.lstsq(jacobian, -residual, rcond=None)
        theta += float(step[0])
        shift = shift + (step[1:] if normal_ref is None else step[1] * normal_ref)
        if float(np.linalg.norm(step)) < 1e-12:
            break
    rotation = _rotation_2d(theta)
    moved = relative @ rotation.T + pivot + shift
    residual = np.einsum("ij,ij->i", normals, moved) - offsets
    return theta, shift, float(np.sqrt(np.mean(residual**2)))


def fit_in_plane_correction(
    measurements: Sequence[LineMeasurement], pose_module_tag_guess: Pose
) -> InPlaneCorrection:
    """Korrigiert `T_module_tag` in der Tag-Ebene aus den Kantenmessungen.

    Gesucht ist die starre 2D-Transformation `D` (x, y, Drehung um Tag-z), die
    die Startwert-Kanten auf die gemessenen abbildet (kleinste Quadrate,
    Punkt-zu-Gerade). Die gemessene Modulgeometrie im Tag-KS ist `D` mal die
    Startwert-Geometrie, daher

        T_tag_module_neu = D * inv(T_module_tag_guess)
        T_module_tag_neu = inv(T_tag_module_neu)

    Verwendet werden nur Kanten mit `found` und ohne `ambiguous`.

    * mindestens zwei Kanten, davon zwei mit >= 20 Grad Winkel: `"full"`
    * sonst mindestens eine Kante: `"partial"` -- nur Drehung und Verschiebung
      senkrecht zur (besten) Kante, entlang der Kante unveraendert
    * keine: `"none"`, Startwert unveraendert

    `delta_xy_m` und `delta_rot_deg` beschreiben `D` um den Tag-Ursprung:
    `x' = R(delta_rot) x + delta_xy`.
    """
    guess = _pose(pose_module_tag_guess, "pose_module_tag_guess")
    usable = [item for item in measurements if _is_usable(item)]
    if not usable:
        return InPlaneCorrection(
            dofs="none",
            delta_xy_m=np.zeros(2),
            delta_rot_deg=0.0,
            pose_module_tag=guess.copy(),
            residual_m=0.0,
        )

    samples = []
    normals = []
    offsets = []
    for item in usable:
        guess_point = np.asarray(item.guess_point_tag, dtype=np.float64)[:2]
        guess_direction = _unit_xy(item.guess_direction_tag)
        measured_direction = _unit_xy(item.direction_tag)
        measured_point = np.asarray(item.point_tag, dtype=np.float64)[:2]
        measured_normal = np.array([-measured_direction[1], measured_direction[0]])
        half = 0.5 * (item.extent_m if item.extent_m > 0.0 else _DEFAULT_EXTENT_M)
        for factor in (-1.0, 0.0, 1.0):
            samples.append(guess_point + factor * half * guess_direction)
            normals.append(measured_normal)
            offsets.append(float(measured_normal @ measured_point))
    samples = np.array(samples)
    normals = np.array(normals)
    offsets = np.array(offsets)
    pivot = np.mean([np.asarray(item.guess_point_tag)[:2] for item in usable], axis=0)

    directions = [_unit_xy(item.guess_direction_tag) for item in usable]
    sine_limit = math.sin(math.radians(MIN_ANGLE_FULL_DEG))
    full = any(
        abs(first[0] * second[1] - first[1] * second[0]) >= sine_limit
        for index, first in enumerate(directions)
        for second in directions[index + 1 :]
    )
    if full:
        dofs = "full"
        theta, shift, residual = _solve_in_plane(samples, normals, offsets, pivot, None)
    else:
        dofs = "partial"
        best = max(usable, key=lambda item: item.support_px)
        reference = _unit_xy(best.guess_direction_tag)
        normal_ref = np.array([-reference[1], reference[0]])
        theta, shift, residual = _solve_in_plane(samples, normals, offsets, pivot, normal_ref)

    # D(q) = R (q - pivot) + pivot + shift = R q + (pivot - R pivot + shift)
    translation = pivot - _rotation_2d(theta) @ pivot + shift
    delta = _in_plane_transform(theta, translation)
    pose_tag_module_new = compose(delta, invert(guess))
    return InPlaneCorrection(
        dofs=dofs,
        delta_xy_m=translation,
        delta_rot_deg=math.degrees(theta),
        pose_module_tag=invert(pose_tag_module_new),
        residual_m=residual,
    )


_DOF_RANK = {"none": 0, "partial": 1, "full": 2}


def average_corrections(
    corrections: Sequence[InPlaneCorrection],
) -> tuple[InPlaneCorrection, dict[str, float]]:
    """Mittelt die Korrekturen mehrerer Bilder.

    Gemittelt werden nur Korrekturen mit der hoechsten Freiheitsgrad-Stufe
    (`full` vor `partial` vor `none`): eine Teilkorrektur laesst die Verschiebung
    entlang der Kante bei 0 und wuerde eine volle Korrektur sonst verfaelschen.
    Streuung `{"spreadMm", "spreadDeg"}`: RMS-Abweichung der Einzelkorrekturen
    (`delta_xy_m` bzw. `delta_rot_deg`) vom Mittel; bei einem Bild 0.

    Wirft `ValueError` ohne Korrektur.
    """
    if not corrections:
        raise ValueError("average_corrections braucht mindestens eine Korrektur")
    top = max(_DOF_RANK[item.dofs] for item in corrections)
    chosen = [item for item in corrections if _DOF_RANK[item.dofs] == top]
    delta_xy = np.mean([item.delta_xy_m for item in chosen], axis=0)
    delta_rot = float(np.mean([item.delta_rot_deg for item in chosen]))
    spread_mm = float(
        1000.0
        * np.sqrt(np.mean([np.sum((item.delta_xy_m - delta_xy) ** 2) for item in chosen]))
    )
    spread_deg = float(
        np.sqrt(np.mean([(item.delta_rot_deg - delta_rot) ** 2 for item in chosen]))
    )
    averaged = InPlaneCorrection(
        dofs=chosen[0].dofs,
        delta_xy_m=np.asarray(delta_xy, dtype=np.float64),
        delta_rot_deg=delta_rot,
        pose_module_tag=average_poses([item.pose_module_tag for item in chosen]),
        residual_m=float(np.mean([item.residual_m for item in chosen])),
    )
    return averaged, {"spreadMm": spread_mm, "spreadDeg": spread_deg}


# --------------------------------------------------------------------------
# Ansichtsplanung
# --------------------------------------------------------------------------


def plan_reference_view(
    pose_module_tag_guess: Pose,
    lines: Sequence[ReferenceLine],
    calib: CameraCalibration,
    *,
    tag_size_m: float,
    line_extent_m: float = 0.10,
    margin: float = 0.10,
    min_standoff_m: float = 0.25,
    max_standoff_m: float = 0.60,
    tilt_deg: float = 0.0,
) -> ViewPlan:
    """Kamerapose im Tag-KS, aus der Tag und Kantenstuecke ins Bild passen.

    Frontal: die Kamera schaut entlang -Tag-z, Bild-x entspricht Tag-x, Bild-y
    zeigt nach -Tag-y (Tag-Oberkante oben im Bild). Ziel sind die vier
    Tag-Ecken plus das tagnahe Stueck jeder Kante der Laenge `line_extent_m`
    (zentriert auf den Punkt der Kante, der dem Tag am naechsten liegt, und in
    die Kante hineingeschoben). Die Kamera sitzt ueber der Mitte dieser
    Punkte; der Abstand ist der kleinste in `[min_standoff_m, max_standoff_m]`,
    bei dem alles mit `margin` (Anteil der Bildbreite/-hoehe) Rand im Bild liegt
    (Projektion mit Verzeichnung).

    `tilt_deg`: Kippung der Kamera um die Tag-x-Achse durch den Zielpunkt
    (positiv: Kamera wandert nach -y). Eine leichte Kippung vermeidet die
    Spiegelmehrdeutigkeit der Tag-Pose bei frontaler Sicht.

    Passt nicht alles bei `max_standoff_m` ins Bild: `fits=False`, `uncovered`
    nennt die Kanten-IDs (ggf. "tag"), die fehlen. Die Pose gilt dann fuer den
    Tag plus die Kanten, die zusammen noch passen (nach Abstand zum Tag
    nacheinander dazugenommen) -- eine zu weit entfernte Kante zieht also nicht
    den Tag mit aus dem Bild. `standoff_m` ist die Tiefe des Zielpunkts im
    Kamera-KS.

    Wirft `ValueError` bei ungueltigen Eingaben.
    """
    tag_size = _positive(tag_size_m, "tag_size_m")
    extent = _positive(line_extent_m, "line_extent_m")
    minimum = _positive(min_standoff_m, "min_standoff_m")
    maximum = _positive(max_standoff_m, "max_standoff_m")
    if maximum < minimum:
        raise ValueError("max_standoff_m muss >= min_standoff_m sein")
    margin = float(margin)
    if not math.isfinite(margin) or not 0.0 <= margin < 0.5:
        raise ValueError(f"margin muss in [0, 0.5) liegen (ist {margin})")
    tilt = float(tilt_deg)
    if not math.isfinite(tilt) or abs(tilt) >= 80.0:
        raise ValueError(f"tilt_deg muss endlich und < 80 sein (ist {tilt_deg})")
    guess = _pose(pose_module_tag_guess, "pose_module_tag_guess")
    checked = _checked_lines(lines)

    pose_tag_module = invert(guess)
    groups: list[tuple[str, np.ndarray]] = [("tag", tag_object_points(tag_size))]
    for line_id, start_module, end_module in checked:
        start = _to_tag(pose_tag_module, start_module)
        end = _to_tag(pose_tag_module, end_module)
        direction = end - start
        length = float(np.linalg.norm(direction))
        nearest = float(np.clip(-(start @ direction) / (length * length), 0.0, 1.0))
        half = min(extent, length) / (2.0 * length)
        low = float(np.clip(nearest - half, 0.0, 1.0 - 2.0 * half))
        groups.append((line_id, np.array([start + low * direction, start + (low + 2 * half) * direction])))

    matrix = np.asarray(calib.camera_matrix, dtype=np.float64)
    width, height = int(calib.image_size[0]), int(calib.image_size[1])
    base_rotation = np.diag([1.0, -1.0, -1.0])
    tilt_rad = math.radians(tilt)
    tilt_rotation = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, math.cos(tilt_rad), -math.sin(tilt_rad)],
            [0.0, math.sin(tilt_rad), math.cos(tilt_rad)],
        ]
    )

    def camera_pose(chosen, standoff: float) -> Pose:
        everything = np.concatenate([points for _, points in chosen])
        target = 0.5 * (everything.min(axis=0) + everything.max(axis=0))
        # Zielpunkt auf die Bildmitte legen (nicht auf den Hauptpunkt).
        position = np.array(
            [
                target[0] - (width / 2.0 - matrix[0, 2]) * standoff / matrix[0, 0],
                target[1] + (height / 2.0 - matrix[1, 2]) * standoff / matrix[1, 1],
                target[2] + standoff,
            ]
        )
        position = target + tilt_rotation @ (position - target)
        return from_rotation_translation(tilt_rotation @ base_rotation, position)

    def uncovered_at(chosen, standoff: float) -> list[str]:
        pose_tag_cam = camera_pose(chosen, standoff)
        pose_cam_tag = invert(pose_tag_cam)
        rvec, tvec = to_rvec_tvec(pose_cam_tag)
        missing = []
        for group_id, points in chosen:
            depth = points @ pose_cam_tag[:3, :3].T + pose_cam_tag[:3, 3]
            ok = bool(np.all(depth[:, 2] > 1e-3))
            if ok:
                pixels = lens.project_points(points, rvec, tvec, calib)
                ok = bool(
                    np.all(np.isfinite(pixels))
                    and np.all(pixels[:, 0] >= margin * width)
                    and np.all(pixels[:, 0] <= (1.0 - margin) * width)
                    and np.all(pixels[:, 1] >= margin * height)
                    and np.all(pixels[:, 1] <= (1.0 - margin) * height)
                )
            if not ok:
                missing.append(group_id)
        return missing

    # Der Tag kommt immer ins Bild; Kanten werden nach Abstand zum Tag
    # nacheinander dazugenommen, solange alles bei `max_standoff_m` passt.
    # Eine zu weit entfernte Kante soll nicht den Tag und die anderen
    # Kanten mit aus dem Bild ziehen -- sie steht in `uncovered`.
    chosen = groups[:1]
    uncovered: list[str] = []
    if uncovered_at(chosen, maximum):
        uncovered.append("tag")
    else:
        def distance_to_tag(group) -> float:
            return float(np.max(np.linalg.norm(group[1][:, :2], axis=1)))

        for group in sorted(groups[1:], key=distance_to_tag):
            if uncovered_at([*chosen, group], maximum):
                uncovered.append(group[0])
            else:
                chosen.append(group)

    if not uncovered_at(chosen, minimum):
        standoff = minimum
    elif uncovered_at(chosen, maximum):
        standoff = maximum
    else:
        low_standoff, high_standoff = minimum, maximum
        for _ in range(50):
            middle = 0.5 * (low_standoff + high_standoff)
            if uncovered_at(chosen, middle):
                low_standoff = middle
            else:
                high_standoff = middle
        standoff = high_standoff
    return ViewPlan(
        pose_tag_cam=camera_pose(chosen, standoff),
        standoff_m=float(standoff),
        fits=not uncovered,
        uncovered=uncovered,
    )


# --------------------------------------------------------------------------
# Katalogformat
# --------------------------------------------------------------------------


def catalog_rpy_deg(rotation) -> np.ndarray:
    """Rotation im Katalogformat `rotationDeg`: `[roll, pitch, yaw]` in Grad.

    RPY um **feste** Achsen X, Y, Z: `R = Rz(yaw) * Ry(pitch) * Rx(roll)` -- die
    URDF-`rpy`-Konvention, wie `quaternionFromRpyDeg` /
    `rpyDegFromQuaternion` im Frontend (`tagGeometry.ts`). Bei `pitch = +-90`
    Grad (Kardan-Sperre) geht die gesamte Drehung in `roll`, `yaw = 0`.

    `rotation`: 3x3-Rotation oder 4x4-Pose (nur der Rotationsteil zaehlt).
    """
    matrix = np.asarray(rotation, dtype=np.float64)
    if matrix.shape == (4, 4):
        matrix = matrix[:3, :3]
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValueError("catalog_rpy_deg: erwartet eine endliche 3x3-Rotation oder 4x4-Pose")
    sine_pitch = float(np.clip(-matrix[2, 0], -1.0, 1.0))
    pitch = math.asin(sine_pitch)
    if abs(sine_pitch) > 1.0 - 1e-9:
        roll = math.atan2(-matrix[1, 2], matrix[1, 1])
        yaw = 0.0
    else:
        roll = math.atan2(matrix[2, 1], matrix[2, 2])
        yaw = math.atan2(matrix[1, 0], matrix[0, 0])
    return np.degrees(np.array([roll, pitch, yaw], dtype=np.float64))


__all__ = [
    "InPlaneCorrection",
    "LineMeasurement",
    "ReferenceLine",
    "ViewPlan",
    "average_corrections",
    "catalog_rpy_deg",
    "fit_in_plane_correction",
    "measure_reference_lines",
    "plan_reference_view",
]
