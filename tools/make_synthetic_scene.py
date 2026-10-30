"""Rendert synthetische Szenen: Bilder mit **bekannter** Grundwahrheit, ohne Kamera.

Das ist die Grundlage von `tests/test_tag_pipeline.py` und die einzige
Moeglichkeit, die ganze Kette (Detektor -> Pose -> Platzierung -> Modulpose)
gegen Referenzwerte statt gegen sich selbst zu pruefen: Tag-Posen werden hier
vorgegeben, mit den echten Intrinsiken projiziert und dann von der Bibliothek
wiedergefunden.

    # Bildserie mit Tags plus passender Kalibrierung
    python tools/make_synthetic_scene.py --out /tmp/szene --count 20 \
        --calibration /tmp/szene/kalibrierung.json

    # Schachbrettansichten fuer den Kalibrierpfad
    python tools/make_synthetic_scene.py --out /tmp/board --count 20 --mode chessboard

Die Werkzeuge laufen dann ohne Hardware:

    PYTHONPATH=src python3 -m tagloc.cli.calibrate --source /tmp/board \
        --out /tmp/kalib.json
    PYTHONPATH=src python3 -m tagloc.cli.build_tagmap --source /tmp/szene \
        --calibration /tmp/szene/kalibrierung.json --anchor 0 --out /tmp/tagmap.json

Gerendert mit `cv2.aruco.generateImageMarker` (Rueckfall `drawMarker`) und
`cv2.warpPerspective`; die Eckpunkte stammen aus `cv2.projectPoints` mit genau
den Intrinsiken, die auch in die Kalibrierdatei geschrieben werden. Das macht
das Bild passend zur Kalibrierung, und nur das macht den Referenzwert zu einem
Referenzwert.

`cv2` wird nur innerhalb von Funktionen importiert -- `--help` und der Import
dieses Moduls funktionieren ohne OpenCV.
"""

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tagloc.boards import BoardSpec  # noqa: E402
from tagloc.calibration import CameraCalibration, save_calibration  # noqa: E402
from tagloc.geometry import (  # noqa: E402
    Pose,
    compose,
    from_rotation_translation,
    invert,
    pose_to_dict,
    rotation_from_rvec,
    to_rvec_tvec,
)
from tagloc.pose import tag_object_points  # noqa: E402
from tagloc.tagmap import DEFAULT_TAG_FAMILY  # noqa: E402

SCHEMA = "wsc.vision.synthetic/1"

#: Dateiname der geschriebenen Ansichten -- sortierbar, damit `ImageFolderSource`
#: sie in Aufnahmereihenfolge liefert. PNG, weil `.gitignore` `*.jpg` verschluckt
#: und weil JPEG-Artefakte die Eckengenauigkeit verfaelschen wuerden.
IMAGE_PATTERN = "ansicht_{index:03d}.png"
TRUTH_FILE = "wahrheit.json"


@dataclass(frozen=True)
class TagPlacement:
    """Ein Tag in der Welt: ID, Pose und Kantenlaenge in Metern."""

    tag_id: int
    pose_world_tag: Pose
    size_m: float = 0.08


def _as_placement(item) -> TagPlacement:
    """Nimmt ein `TagPlacement` oder das Tripel `(tag_id, T_world_tag, size_m)` an."""
    if isinstance(item, TagPlacement):
        return item
    tag_id, pose, size_m = item
    return TagPlacement(int(tag_id), np.asarray(pose, dtype=np.float64), float(size_m))


# --------------------------------------------------------------------------
# Kamera und Posen
# --------------------------------------------------------------------------


def synthetic_calibration(
    image_size: tuple[int, int] = (1600, 1200),
    *,
    focal_px: float | None = None,
    frame_id: str = "cam_synth",
) -> CameraCalibration:
    """Ideale Lochkamera: Hauptpunkt in der Bildmitte, keine Verzeichnung.

    `focal_px` nicht gesetzt = Bildbreite, also etwa 53 Grad horizontales
    Sichtfeld. Das Ergebnis speist sowohl das Rendern als auch die
    Kalibrierdatei, sodass Bild und Kalibrierung nie auseinanderlaufen koennen.
    """
    width, height = int(image_size[0]), int(image_size[1])
    focal = float(focal_px) if focal_px else float(width)
    matrix = np.array(
        [[focal, 0.0, (width - 1) / 2.0], [0.0, focal, (height - 1) / 2.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.zeros(5, dtype=np.float64),
        image_size=(width, height),
        frame_id=frame_id,
        calibration_id=f"{frame_id}@synthetisch",
        rms_reprojection_error=0.0,
        sample_count=0,
        board={"type": "synthetisch"},
    )


def _scaled_calibration(calibration: CameraCalibration, factor: int) -> CameraCalibration:
    """Intrinsiken fuer das Rendern auf dem uebersampelten Raster.

    Bei Faktor `s` wird der Zielbildpunkt `x` im grossen Bild auf
    `s*x + (s-1)/2` abgebildet -- genau die Abbildung, die `cv2.resize` mit
    `INTER_AREA` spaeter umkehrt. Ohne den Versatz `(s-1)/2` saesse das fertige
    Bild einen halben Pixel neben der Kalibrierung.
    """
    if factor <= 1:
        return calibration
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64).copy()
    matrix[0, 0] *= factor
    matrix[1, 1] *= factor
    matrix[0, 2] = matrix[0, 2] * factor + (factor - 1) / 2.0
    matrix[1, 2] = matrix[1, 2] * factor + (factor - 1) / 2.0
    width, height = calibration.image_size
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.zeros(5, dtype=np.float64),
        image_size=(width * factor, height * factor),
        frame_id=calibration.frame_id,
    )


def look_at(eye, target, up=(0.0, 0.0, 1.0)) -> Pose:
    """Liefert die Kamerapose `T_world_cam` mit Blick von `eye` auf `target`.

    Ergebnis im optischen OpenCV-KS: +Z entlang der Blickrichtung, +X nach
    rechts, +Y nach unten. `up` ist nur ein Hinweis fuer die Bildausrichtung;
    liegt es parallel zur Blickrichtung, wird stattdessen die Weltachse +Y benutzt.
    """
    eye = np.asarray(eye, dtype=np.float64).reshape(3)
    target = np.asarray(target, dtype=np.float64).reshape(3)
    up = np.asarray(up, dtype=np.float64).reshape(3)
    forward = target - eye
    norm = float(np.linalg.norm(forward))
    if norm < 1e-9:
        raise ValueError("Kamera und Zielpunkt fallen zusammen")
    forward = forward / norm
    if abs(float(np.dot(up, forward))) > 0.999:
        up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    down = -(up - float(np.dot(up, forward)) * forward)
    down = down / float(np.linalg.norm(down))
    right = np.cross(down, forward)
    return from_rotation_translation(np.column_stack([right, down, forward]), eye)


def tag_pose(position, yaw_deg: float = 0.0, pitch_deg: float = 0.0) -> Pose:
    """Tag-Pose in der Welt: liegt flach (+Z oben), um Z, dann um X gedreht."""
    yaw = rotation_from_rvec([0.0, 0.0, math.radians(yaw_deg)])
    pitch = rotation_from_rvec([math.radians(pitch_deg), 0.0, 0.0])
    return from_rotation_translation(yaw @ pitch, position)


def default_tag_layout() -> list[TagPlacement]:
    """Liefert die Beispielzelle: vier Referenztags und einen Modul-Tag.

    Die Groessen sind bewusst verschieden -- eine angenommene globale
    Tag-Groesse wuerde dann sofort auffallen, statt den Abstand still zu skalieren.
    """
    return [
        TagPlacement(0, tag_pose((0.000, 0.000, 0.000), 0.0), 0.070),
        TagPlacement(1, tag_pose((0.130, 0.000, 0.000), 15.0), 0.070),
        TagPlacement(2, tag_pose((0.130, 0.110, 0.000), -20.0), 0.060),
        TagPlacement(3, tag_pose((0.000, 0.110, 0.000), 0.0), 0.060),
        TagPlacement(7, tag_pose((0.065, 0.220, 0.020), 40.0), 0.055),
    ]


def orbit_views(
    count: int,
    *,
    center=(0.065, 0.105, 0.0),
    radius: float = 0.30,
    height: float = 0.46,
    start_deg: float = -90.0,
    sweep_deg: float = 260.0,
) -> list[Pose]:
    """Liefert Kameraposen auf einem Bogen ueber der Szene.

    Der schraege Blickwinkel ist Absicht: Frontal auf einen quadratischen
    Marker ist die Neigung schlecht bestimmt (genau die Mehrdeutigkeit, die
    `TagPose.ambiguity_ratio` meldet). Bei etwa 30 Grad ausserhalb der Achse
    ist sie es nicht.
    """
    center = np.asarray(center, dtype=np.float64).reshape(3)
    views = []
    for index in range(max(1, int(count))):
        angle = math.radians(start_deg + sweep_deg * index / max(1, count - 1))
        eye = center + np.array(
            [radius * math.cos(angle), radius * math.sin(angle), height], dtype=np.float64
        )
        views.append(look_at(eye, center))
    return views


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _project(object_points, pose_cam_object: Pose, calibration: CameraCalibration) -> np.ndarray:
    """Projiziert Objektpunkte mit den echten Intrinsiken ins Bild."""
    import cv2

    rvec, tvec = to_rvec_tvec(pose_cam_object)
    projected, _ = cv2.projectPoints(
        np.asarray(object_points, dtype=np.float64),
        rvec,
        tvec,
        np.asarray(calibration.camera_matrix, dtype=np.float64),
        np.asarray(calibration.distortion, dtype=np.float64),
    )
    return projected.reshape(-1, 2)


def _paste(canvas, patch, source_quad, target_quad) -> None:
    """Fuegt `patch` perspektivisch ins Bild ein: `source_quad` wird auf `target_quad` abgebildet.

    Gerechnet wird nur im umschliessenden Rechteck des Ziels statt auf der
    ganzen Leinwand -- mit Supersampling ist das der Unterschied zwischen
    Sekunden und Minuten. Die Kanten werden ueber eine mitverzerrte Maske
    weich eingeblendet, damit der Rand des Ausschnitts keine sichtbare Stufe
    hinterlaesst.
    """
    import cv2

    matrix = cv2.getPerspectiveTransform(
        np.asarray(source_quad, dtype=np.float32), np.asarray(target_quad, dtype=np.float32)
    )
    height, width = patch.shape[:2]
    outline = np.array(
        [[-0.5, -0.5], [width - 0.5, -0.5], [width - 0.5, height - 0.5], [-0.5, height - 0.5]],
        dtype=np.float64,
    )
    mapped = cv2.perspectiveTransform(outline.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    left = max(0, int(math.floor(mapped[:, 0].min())))
    top = max(0, int(math.floor(mapped[:, 1].min())))
    right = min(canvas.shape[1], int(math.ceil(mapped[:, 0].max())) + 1)
    bottom = min(canvas.shape[0], int(math.ceil(mapped[:, 1].max())) + 1)
    if right <= left or bottom <= top:
        return
    shift = np.array([[1.0, 0.0, -left], [0.0, 1.0, -top], [0.0, 0.0, 1.0]]) @ matrix
    size = (right - left, bottom - top)
    warped = cv2.warpPerspective(patch, shift, size, flags=cv2.INTER_LINEAR, borderValue=0)
    alpha = cv2.warpPerspective(
        np.full((height, width), 255, dtype=np.uint8), shift, size,
        flags=cv2.INTER_LINEAR, borderValue=0,
    )
    weight = alpha.astype(np.float64) / 255.0
    region = canvas[top:bottom, left:right].astype(np.float64)
    blended = region * (1.0 - weight) + warped.astype(np.float64) * weight
    canvas[top:bottom, left:right] = np.clip(blended, 0.0, 255.0).astype(np.uint8)


def marker_patch(family: str, tag_id: int, marker_px: int, quiet_px: int):
    """Liefert ein Markerbild mit weisser Ruhezone: `(Bild, Markerecken)`.

    Die Ruhezone ist erforderlich, nicht kosmetisch: Ohne weissen Rand findet
    kein Detektor den schwarzen Markerrahmen. Die zurueckgegebenen Ecken
    liegen auf der **Aussenkante** des schwarzen Rahmens (daher die halben
    Pixel) -- das ist die Kante, auf die sich `size_m` bezieht und die der
    Detektor misst.
    """
    import cv2

    from tagloc.detector import aruco_dictionary

    dictionary = aruco_dictionary(family)
    marker_px = int(marker_px)
    if hasattr(cv2.aruco, "generateImageMarker"):
        marker = cv2.aruco.generateImageMarker(dictionary, int(tag_id), marker_px)
    else:  # OpenCV < 4.7
        marker = cv2.aruco.drawMarker(dictionary, int(tag_id), marker_px)
    quiet_px = int(quiet_px)
    patch = np.full((marker_px + 2 * quiet_px, marker_px + 2 * quiet_px), 255, dtype=np.uint8)
    patch[quiet_px : quiet_px + marker_px, quiet_px : quiet_px + marker_px] = marker
    low = quiet_px - 0.5
    high = quiet_px + marker_px - 0.5
    return patch, [(low, low), (high, low), (high, high), (low, high)]


def _inside(points, image_size: tuple[int, int], margin: float = 2.0) -> bool:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    width, height = image_size
    return bool(
        points[:, 0].min() >= margin
        and points[:, 1].min() >= margin
        and points[:, 0].max() <= width - 1 - margin
        and points[:, 1].max() <= height - 1 - margin
    )


def render_tags(
    placements,
    pose_world_cam: Pose,
    calibration: CameraCalibration,
    *,
    image_size: tuple[int, int] | None = None,
    family: str = DEFAULT_TAG_FAMILY,
    marker_px: int = 320,
    quiet_px: int = 48,
    background: int = 235,
    supersample: int = 2,
):
    """Rendert eine Ansicht der Szene und liefert ihre Grundwahrheit.

    Liefert `(Bild in BGR, {tag_id: T_cam_tag})`. Das Wahrheits-Dict enthaelt
    nur Tags, die vollstaendig im Bild sichtbar sind -- ein Test soll nicht an
    einem Tag scheitern, den er unmoeglich sehen konnte.

    `supersample` rendert auf einem ganzzahlig feineren Raster und verkleinert
    danach mit `INTER_AREA`. Das ersetzt die Kantenglaettung, die die Optik
    einer echten Kamera liefert; ohne sie haben die Markerkanten Treppen, und
    die Ecken werden ungenauer, als die Aufgabe verlangt.
    """
    import cv2

    width, height = tuple(image_size or calibration.image_size)
    factor = max(1, int(supersample))
    render_calibration = _scaled_calibration(
        synthetic_from(calibration, (width, height)), factor
    )
    canvas = np.full((height * factor, width * factor), int(background), dtype=np.uint8)
    pose_cam_world = invert(pose_world_cam)

    prepared = [_as_placement(item) for item in placements]
    # Von hinten nach vorn zeichnen, damit sich ueberlappende Tags richtig
    # verdecken statt nach Listenreihenfolge.
    prepared.sort(key=lambda item: -float(compose(pose_cam_world, item.pose_world_tag)[2, 3]))

    truth: dict[int, Pose] = {}
    for placement in prepared:
        pose_cam_tag = compose(pose_cam_world, placement.pose_world_tag)
        if float(pose_cam_tag[2, 3]) <= 1e-3:
            continue  # hinter oder innerhalb der Kamera
        corners = _project(tag_object_points(placement.size_m), pose_cam_tag, render_calibration)
        if not _inside(corners, render_calibration.image_size, margin=factor * 2.0):
            continue
        patch, quad = marker_patch(family, placement.tag_id, marker_px, quiet_px)
        _paste(canvas, patch, quad, corners)
        truth[placement.tag_id] = pose_cam_tag

    image = canvas
    if factor > 1:
        image = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), truth


def synthetic_from(calibration: CameraCalibration, image_size) -> CameraCalibration:
    """Passt die Intrinsiken an eine andere Bildgroesse an, ohne zu skalieren.

    Wird die Bildgroesse getrennt von der Kalibrierung angegeben, ist die
    Kalibrierung massgebend und die Groesse nur ein Ausschnitt. Alles andere
    waere eine stille Neuskalierung -- genau der Fehler, vor dem
    `check_resolution` warnt.
    """
    size = (int(image_size[0]), int(image_size[1]))
    if tuple(calibration.image_size) == size:
        return calibration
    return CameraCalibration(
        camera_matrix=np.asarray(calibration.camera_matrix, dtype=np.float64).copy(),
        distortion=np.asarray(calibration.distortion, dtype=np.float64).copy(),
        image_size=size,
        frame_id=calibration.frame_id,
        calibration_id=calibration.calibration_id,
    )


# --------------------------------------------------------------------------
# Reference edges (Tag auf heller Flaeche vor dunklem Hintergrund)
# --------------------------------------------------------------------------


def _polygon_basis(points: np.ndarray):
    """Liefert `(Ursprung, e1, e2)`: eine Orthonormalbasis der Ebene des Polygons."""
    origin = points[0]
    first = points[1] - origin
    first = first / float(np.linalg.norm(first))
    normal = np.cross(first, points[2] - origin)
    norm = float(np.linalg.norm(normal))
    if norm < 1e-12:
        raise ValueError("Flaechenpolygon ist entartet (Punkte auf einer Geraden)")
    second = np.cross(normal / norm, first)
    return origin, first, second


def _inset_polygon(points: np.ndarray, edge_widths: dict[int, float]) -> np.ndarray:
    """Verschiebt die angegebenen Kanten eines konvexen Polygons um ihre Breite nach innen.

    Beschneidet das Polygon gegen je eine Halbebene pro Kante
    (Sutherland-Hodgman) in der eigenen Ebene des Polygons; die
    zurueckgegebenen 3D-Punkte liegen in dieser Ebene.
    """
    origin, first, second = _polygon_basis(points)
    flat = np.column_stack([(points - origin) @ first, (points - origin) @ second])
    # Orientierung: Die nach innen zeigende Normale einer Kante zeigt zur Polygonmitte.
    centre = flat.mean(axis=0)
    clipped = [tuple(point) for point in flat]
    for index, width in edge_widths.items():
        a = flat[index % len(flat)]
        b = flat[(index + 1) % len(flat)]
        along = (b - a) / float(np.linalg.norm(b - a))
        inward = np.array([-along[1], along[0]])
        if float(inward @ (centre - a)) < 0.0:
            inward = -inward
        anchor = a + float(width) * inward
        result = []
        for k, current in enumerate(clipped):
            previous = clipped[k - 1]
            d_current = float((np.array(current) - anchor) @ inward)
            d_previous = float((np.array(previous) - anchor) @ inward)
            if d_current >= 0.0:
                if d_previous < 0.0:
                    t = d_previous / (d_previous - d_current)
                    result.append(tuple(np.array(previous) + t * (np.array(current) - np.array(previous))))
                result.append(current)
            elif d_previous >= 0.0:
                t = d_previous / (d_previous - d_current)
                result.append(tuple(np.array(previous) + t * (np.array(current) - np.array(previous))))
        clipped = result
    if len(clipped) < 3:
        raise ValueError("Nut breiter als die Flaeche")
    inner = np.array(clipped)
    return origin + np.outer(inner[:, 0], first) + np.outer(inner[:, 1], second)


def _fill_polygon(canvas, pixels: np.ndarray, value: int) -> None:
    """Fuellt ein projiziertes Polygon mit subpixelgenauen Eckpunkten."""
    import cv2

    fixed = np.round(np.asarray(pixels, dtype=np.float64) * 16.0).astype(np.int32)
    cv2.fillPoly(canvas, [fixed], int(value), lineType=cv2.LINE_8, shift=4)


def render_reference_scene(
    pose_cam_tag: Pose,
    pose_module_tag: Pose,
    calibration: CameraCalibration,
    surface_polygon,
    *,
    tag_id: int = 7,
    tag_size_m: float = 0.08,
    family: str = DEFAULT_TAG_FAMILY,
    image_size: tuple[int, int] | None = None,
    groove_edges: dict[int, float] | None = None,
    background: int = 60,
    surface: int = 205,
    groove_gray: int = 130,
    noise_sigma: float = 0.0,
    blur_sigma_px: float = 0.6,
    seed: int = 20261020,
    supersample: int = 4,
    marker_px: int = 320,
    quiet_px: int = 40,
):
    """Rendert einen Tag auf einer hellen Flaeche vor dunklerem Hintergrund.

    Die Flaechenraender sind die Referenzkanten. `surface_polygon` ist ein
    ebenes Polygon im **Modul-KS** (N x 3, in Reihenfolge); seine Kante `i`
    laeuft von Punkt `i` nach Punkt `i + 1` und wird als Kante `"E{i}"`
    gemeldet. Der Tag sitzt bei `pose_module_tag` (`T_module_tag`), gesehen
    mit `pose_cam_tag` (`T_cam_tag`); die Modulpose folgt aus beiden. Das
    Polygon muss nicht in der Tag-Ebene liegen -- eine Kante unterhalb der
    Tag-Ebene ist die Art, wie die Hoehenbehandlung getestet wird.

    `groove_edges` bildet einen Kantenindex auf eine Breite in Metern ab: eine
    zweite, parallele Kante in dieser Entfernung innerhalb der Flaeche (eine
    Item-Nut), gerendert als Streifen in `groove_gray` -- gleiche Polaritaet
    wie die Hauptkante, sodass ein Detektor zwei aehnliche Kanten sieht.

    Der Tag ist auf die Flaeche gedruckt (seine weissen Zellen und die
    Ruhezone haben das Grau der Flaeche), sodass der Tag-Umriss keine
    konkurrierende Kante hinzufuegt.

    Die Verzeichnung von `calibration` wird angewendet: Die Szene wird ideal
    gerendert (mit Rand) und ueber `tagloc.lens.undistort_points` neu
    abgebildet, sodass jedes Linsenmodell funktioniert. Danach:
    `blur_sigma_px` (Optik), gaussches Rauschen `noise_sigma` in Graustufen.

    Liefert `(Bild in BGR, Wahrheit)`; die Wahrheit ist ein dict mit
    `pose_cam_tag`, `pose_module_tag`, `pose_cam_module` und `edges`, einer
    Liste von `{"id", "start", "end"}` (Modul-KS, Meter).
    """
    import cv2

    from tagloc import lens

    polygon = np.asarray(surface_polygon, dtype=np.float64).reshape(-1, 3)
    if len(polygon) < 3:
        raise ValueError("Flaechenpolygon braucht mindestens 3 Punkte")
    pose_cam_tag = np.asarray(pose_cam_tag, dtype=np.float64)
    pose_module_tag = np.asarray(pose_module_tag, dtype=np.float64)
    pose_cam_module = compose(pose_cam_tag, invert(pose_module_tag))

    width, height = tuple(image_size or calibration.image_size)
    factor = max(1, int(supersample))
    view = synthetic_from(calibration, (width, height))
    distorted = bool(np.any(np.asarray(view.distortion, dtype=np.float64) != 0.0))
    pad = int(0.2 * max(width, height)) if distorted else 0

    matrix = np.asarray(view.camera_matrix, dtype=np.float64).copy()
    matrix[0, 2] += pad
    matrix[1, 2] += pad
    ideal = _scaled_calibration(
        CameraCalibration(
            camera_matrix=matrix,
            distortion=np.zeros(5, dtype=np.float64),
            image_size=(width + 2 * pad, height + 2 * pad),
            frame_id=view.frame_id,
        ),
        factor,
    )
    canvas = np.full(
        ((height + 2 * pad) * factor, (width + 2 * pad) * factor), int(background), dtype=np.uint8
    )

    camera = polygon @ pose_cam_module[:3, :3].T + pose_cam_module[:3, 3]
    if np.any(camera[:, 2] <= 1e-3):
        raise ValueError("Flaeche liegt (teilweise) hinter der Kamera")
    outline = _project(polygon, pose_cam_module, ideal)
    grooves = dict(groove_edges or {})
    if grooves:
        _fill_polygon(canvas, outline, groove_gray)
        inner = _inset_polygon(polygon, grooves)
        _fill_polygon(canvas, _project(inner, pose_cam_module, ideal), surface)
    else:
        _fill_polygon(canvas, outline, surface)

    tag_corners = _project(tag_object_points(tag_size_m), pose_cam_tag, ideal)
    patch, quad = marker_patch(family, tag_id, marker_px, quiet_px)
    patch = np.round(patch.astype(np.float64) * (float(surface) / 255.0)).astype(np.uint8)
    _paste(canvas, patch, quad, tag_corners)

    if distorted:
        # Verzeichneter Ausgabepixel -> ideale Koordinaten, auf dem endgueltigen Raster,
        # dann auf das uebersampelte Raster hochinterpoliert (die Abbildung ist glatt).
        columns, rows = np.meshgrid(np.arange(width), np.arange(height))
        grid = np.column_stack([columns.reshape(-1), rows.reshape(-1)]).astype(np.float64)
        source = lens.undistort_points(grid, view).reshape(height, width, 2).astype(np.float32)
        if factor > 1:
            source = cv2.resize(source, (width * factor, height * factor), interpolation=cv2.INTER_LINEAR)
        map_x = (source[..., 0] + pad) * factor + (factor - 1) / 2.0
        map_y = (source[..., 1] + pad) * factor + (factor - 1) / 2.0
        canvas = cv2.remap(
            canvas, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=int(background),
        )

    image = canvas
    if factor > 1:
        image = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
    image = image.astype(np.float32)
    if blur_sigma_px > 0.0:
        image = cv2.GaussianBlur(image, (0, 0), float(blur_sigma_px))
    if noise_sigma > 0.0:
        rng = np.random.default_rng(int(seed))
        image = image + rng.normal(0.0, float(noise_sigma), image.shape).astype(np.float32)
    image = np.clip(np.round(image), 0, 255).astype(np.uint8)

    truth = {
        "pose_cam_tag": pose_cam_tag,
        "pose_module_tag": pose_module_tag,
        "pose_cam_module": pose_cam_module,
        "edges": [
            {
                "id": f"E{index}",
                "start": polygon[index].copy(),
                "end": polygon[(index + 1) % len(polygon)].copy(),
            }
            for index in range(len(polygon))
        ],
    }
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), truth


# --------------------------------------------------------------------------
# Chessboard
# --------------------------------------------------------------------------


def chessboard_object_points(spec: BoardSpec) -> np.ndarray:
    """Liefert die inneren Ecken im Brett-KS, in derselben Reihenfolge wie `boards.py`."""
    grid = np.zeros((spec.cols * spec.rows, 3), dtype=np.float64)
    grid[:, :2] = np.mgrid[0 : spec.cols, 0 : spec.rows].T.reshape(-1, 2)
    return grid * float(spec.square_size_m)


def _chessboard_patch(spec: BoardSpec, square_px: int, margin_squares: int):
    """Liefert das Brett als Bild: `(Bild, Eckpunkte der Brettflaeche)`.

    Ein Brett mit `cols` x `rows` **inneren** Ecken hat `cols+1` x `rows+1`
    Felder. Der weisse Rand darum ist erforderlich: `findChessboardCorners`
    findet kein Brett, das buendig an der Kante abgeschnitten ist.
    """
    across = spec.cols + 1
    down = spec.rows + 1
    offset = int(margin_squares)
    square_px = int(square_px)
    patch = np.full(
        ((down + 2 * offset) * square_px, (across + 2 * offset) * square_px), 255, dtype=np.uint8
    )
    for column in range(across):
        for row in range(down):
            if (column + row) % 2:
                continue
            top = (row + offset) * square_px
            left = (column + offset) * square_px
            patch[top : top + square_px, left : left + square_px] = 0
    low_x = offset * square_px - 0.5
    low_y = offset * square_px - 0.5
    high_x = (offset + across) * square_px - 0.5
    high_y = (offset + down) * square_px - 0.5
    return patch, [(low_x, low_y), (high_x, low_y), (high_x, high_y), (low_x, high_y)]


def _chessboard_outline(spec: BoardSpec, expand_squares: float = 0.0) -> np.ndarray:
    """Liefert die vier aeusseren Ecken der Brettflaeche im Brett-KS.

    Der Ursprung sitzt auf der ersten inneren Ecke, eine Feldbreite von der
    Kante entfernt -- wie `boards._chessboard_object_points` es annimmt.
    `expand_squares` vergroessert das Rechteck um die weisse Ruhezone, die im
    Bild sein muss, sonst findet `findChessboardCorners` das Brett nicht.
    """
    square = float(spec.square_size_m)
    grow = float(expand_squares) * square
    left = -square - grow
    top = -square - grow
    right = spec.cols * square + grow
    bottom = spec.rows * square + grow
    return np.array(
        [[left, top, 0.0], [right, top, 0.0], [right, bottom, 0.0], [left, bottom, 0.0]],
        dtype=np.float64,
    )


def render_chessboard(
    pose_cam_board: Pose,
    calibration: CameraCalibration,
    spec: BoardSpec = BoardSpec(),
    *,
    image_size: tuple[int, int] | None = None,
    square_px: int = 48,
    margin_squares: int = 1,
    background: int = 235,
    supersample: int = 2,
):
    """Rendert eine Schachbrettansicht. Liefert `(Bild in BGR, T_cam_board)`.

    Die Pose ist die Grundwahrheit; sie zurueckzugeben erspart einem Test,
    die Eingabe noch einmal durchzureichen.
    """
    import cv2

    width, height = tuple(image_size or calibration.image_size)
    factor = max(1, int(supersample))
    render_calibration = _scaled_calibration(
        synthetic_from(calibration, (width, height)), factor
    )
    canvas = np.full((height * factor, width * factor), int(background), dtype=np.uint8)
    corners = _project(_chessboard_outline(spec), pose_cam_board, render_calibration)
    patch, quad = _chessboard_patch(spec, square_px, margin_squares)
    _paste(canvas, patch, quad, corners)
    image = canvas
    if factor > 1:
        image = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), np.asarray(pose_cam_board, dtype=np.float64)


def chessboard_views(
    count: int,
    calibration: CameraCalibration,
    spec: BoardSpec = BoardSpec(),
    *,
    distance_m: float = 0.55,
    seed: int = 20260917,
    image_size: tuple[int, int] | None = None,
) -> list[Pose]:
    """Liefert Brettposen fuer eine Kalibrierserie: gekippt, verschoben, gedreht.

    Ohne Kippung ist die Kalibrierung unterbestimmt -- alle Ansichten
    truegen dieselbe Information, und Brennweite und Verzeichnung liessen sich
    nicht trennen. Jede Pose wird geprueft: Ragt das Brett samt weissem Rand
    aus dem Bild, wird sie verworfen und neu gezogen.
    """
    size = tuple(image_size or calibration.image_size)
    view_calibration = synthetic_from(calibration, size)
    centre = chessboard_object_points(spec).mean(axis=0)
    generator = random.Random(int(seed))

    poses: list[Pose] = []
    attempts = 0
    while len(poses) < int(count) and attempts < int(count) * 60:
        attempts += 1
        rotation = rotation_from_rvec(
            [
                math.radians(generator.uniform(-32.0, 32.0)),
                math.radians(generator.uniform(-32.0, 32.0)),
                math.radians(generator.uniform(-180.0, 180.0)),
            ]
        )
        depth = distance_m * generator.uniform(0.85, 1.25)
        # Seitlicher Versatz bis zum Bildrand: Eine nur aus der Mitte
        # aufgenommene Kalibrierung hat ein gutes RMS, aber unbrauchbare Verzeichnungskoeffizienten.
        target = np.array(
            [
                depth * generator.uniform(-0.22, 0.22),
                depth * generator.uniform(-0.18, 0.18),
                depth,
            ],
            dtype=np.float64,
        )
        pose = from_rotation_translation(rotation, target - rotation @ centre)
        # Mehr als nur die inneren Ecken pruefen: Ein abgeschnittenes Brett ist
        # nur brauchbar, wenn sein weisser Rand ebenfalls im Bild ist.
        outline = _project(_chessboard_outline(spec, 1.0), pose, view_calibration)
        if _inside(outline, size, margin=2.0):
            poses.append(pose)
    if len(poses) < int(count):
        raise RuntimeError(
            f"Nur {len(poses)} von {count} Board-Ansichten passen ins Bild "
            "-- groesseren Abstand (--distance-m) oder kleineres Board waehlen."
        )
    return poses


# --------------------------------------------------------------------------
# Writing folders
# --------------------------------------------------------------------------


def write_tag_scene(
    out_dir: Path,
    count: int,
    calibration: CameraCalibration,
    *,
    placements=None,
    family: str = DEFAULT_TAG_FAMILY,
    supersample: int = 2,
    min_tags: int = 2,
) -> dict:
    """Write `count` tag views plus `wahrheit.json`."""
    import cv2

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared = [_as_placement(item) for item in (placements or default_tag_layout())]

    views = []
    for index, pose_world_cam in enumerate(orbit_views(count), start=1):
        image, truth = render_tags(
            prepared, pose_world_cam, calibration, family=family, supersample=supersample
        )
        if len(truth) < int(min_tags):
            continue
        name = IMAGE_PATTERN.format(index=index)
        cv2.imwrite(str(out_dir / name), image)
        views.append(
            {
                "file": name,
                "poseWorldCam": pose_to_dict(pose_world_cam),
                "tagsInCamera": {
                    str(tag_id): pose_to_dict(pose) for tag_id, pose in sorted(truth.items())
                },
            }
        )

    truth_document = {
        "schema": SCHEMA,
        "mode": "tags",
        "tagFamily": family,
        "imageSize": list(calibration.image_size),
        "anchorTagId": prepared[0].tag_id if prepared else 0,
        "tags": [
            {
                "tagId": placement.tag_id,
                "sizeM": placement.size_m,
                "poseInWorld": pose_to_dict(placement.pose_world_tag),
            }
            for placement in sorted(prepared, key=lambda item: item.tag_id)
        ],
        "views": views,
    }
    (out_dir / TRUTH_FILE).write_text(
        json.dumps(truth_document, indent=2) + "\n", encoding="utf-8"
    )
    return truth_document


def write_chessboard_scene(
    out_dir: Path,
    count: int,
    calibration: CameraCalibration,
    spec: BoardSpec = BoardSpec(),
    *,
    distance_m: float = 0.55,
    supersample: int = 2,
    seed: int = 20260917,
) -> dict:
    """Write `count` chessboard views plus `wahrheit.json`."""
    import cv2

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    views = []
    poses = chessboard_views(count, calibration, spec, distance_m=distance_m, seed=seed)
    for index, pose_cam_board in enumerate(poses, start=1):
        image, pose = render_chessboard(
            pose_cam_board, calibration, spec, supersample=supersample
        )
        name = IMAGE_PATTERN.format(index=index)
        cv2.imwrite(str(out_dir / name), image)
        views.append({"file": name, "poseCamBoard": pose_to_dict(pose)})

    truth_document = {
        "schema": SCHEMA,
        "mode": "chessboard",
        "imageSize": list(calibration.image_size),
        "board": spec.as_dict(),
        "views": views,
    }
    (out_dir / TRUTH_FILE).write_text(
        json.dumps(truth_document, indent=2) + "\n", encoding="utf-8"
    )
    return truth_document


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synthetische Bilder mit bekannter Wahrheit rendern (ohne Kamera)"
    )
    parser.add_argument("--out", type=Path, required=True, help="Zielordner der Bilder")
    parser.add_argument("--count", type=int, default=20, help="Anzahl der Ansichten")
    parser.add_argument(
        "--mode", choices=("tags", "chessboard"), default="tags", help="Was gerendert wird"
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=None,
        help="Die passende synthetische Kalibrierung hierhin schreiben",
    )
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument(
        "--focal-px", type=float, default=None, help="Brennweite in Pixeln (Standard: Bildbreite)"
    )
    parser.add_argument("--frame-id", default="cam_synth", help="Bezugsrahmen der Kalibrierung")
    parser.add_argument("--family", default=DEFAULT_TAG_FAMILY, help="Tag-Familie")
    parser.add_argument(
        "--supersample", type=int, default=2, help="Ueberabtastung beim Rendern (1 = aus)"
    )
    parser.add_argument("--cols", type=int, default=9, help="Schachbrett: innere Ecken je Zeile")
    parser.add_argument("--rows", type=int, default=6, help="Schachbrett: innere Ecken je Spalte")
    parser.add_argument("--square-size-m", type=float, default=0.030)
    parser.add_argument(
        "--distance-m", type=float, default=0.55, help="Schachbrett: mittlerer Abstand"
    )
    parser.add_argument("--seed", type=int, default=20260917, help="Zufallssaat der Board-Posen")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.count < 1:
        raise SystemExit("--count muss mindestens 1 sein")

    calibration = synthetic_calibration(
        (args.width, args.height), focal_px=args.focal_px, frame_id=args.frame_id
    )
    if args.calibration is not None:
        save_calibration(args.calibration, calibration)
        print(f"Kalibrierung geschrieben: {args.calibration}")

    if args.mode == "tags":
        document = write_tag_scene(
            args.out, args.count, calibration, family=args.family, supersample=args.supersample
        )
        print(f"{len(document['views'])} Ansichten mit {len(document['tags'])} Tags")
        print("Anker-Tag:", document["anchorTagId"])
    else:
        spec = BoardSpec(
            type="chessboard",
            cols=args.cols,
            rows=args.rows,
            square_size_m=args.square_size_m,
        )
        document = write_chessboard_scene(
            args.out,
            args.count,
            calibration,
            spec,
            distance_m=args.distance_m,
            supersample=args.supersample,
            seed=args.seed,
        )
        print(f"{len(document['views'])} Schachbrettansichten, Board {spec.cols}x{spec.rows}")

    print(f"Bilder und {TRUTH_FILE} in: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
