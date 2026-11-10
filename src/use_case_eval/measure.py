"""Bildmessung der Wuerfelablage: Abweichung (dx, dy, dtheta) zur Zielscheibenmitte.

Ablauf von `measure_trial`:

1. ArUco-Marker (4x4_50) erkennen, Ecken subpixelgenau.
2. Blattpose per `cv2.solvePnP` aus den Ecken der Blattmarker (IDs 0-3,
   mindestens 2), Bildpunkte vorher entzerrt (`tagloc.lens`), damit Pinhole- und
   Fisheye-Kalibrierungen gleich laufen.
3. Ecken des Wuerfelmarkers (ID 10) als Strahlen ins Blattsystem rechnen und mit
   der Ebene z = 20 mm (Wuerfeloberseite) schneiden. Mitte und Gierwinkel
   folgen aus den vier Schnittpunkten; ein Aufkleber-Versatz wird abgezogen.

Fehler (kein Marker, zu wenige Blattmarker, Pose nicht loesbar) liefern
`ok=False` mit deutschem `grund`, nie eine Ausnahme. Die Rechenfunktionen sind
rein; OpenCV wird erst in den Funktionen importiert.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from . import target

#: Mindestzahl erkannter Blattmarker fuer eine Blattpose.
MIN_SHEET_MARKERS = 2
#: Eckenverfeinerung des ArUco-Detektors (`tagloc.detector.CORNER_METHODS`).
CORNER_METHOD = "subpix"

# Kantenverfeinerung der Marker (siehe `_refine_quad`).
#: Querprofile: Abstand entlang der Kante (px) und Schrittweite im Profil (px).
_EDGE_STEP_PX = 0.5
_PROFILE_SAMPLE_PX = 0.25
#: Profilbereich senkrecht zur Kante (px): innen schwarzer Markerrahmen, aussen
#: die weisse Randzone (beim Wuerfelaufkleber nur 1 mm, etwa 2 px breit).
_PROFILE_INNER_PX = 3.0
_PROFILE_OUTER_PX = 1.6
#: Pegel: Schwarz = Mittel bis -2 px nach innen, Weiss = Maximum ab +0,75 px nach aussen.
_PLATEAU_INNER_PX = 2.0
_PLATEAU_OUTER_PX = 0.75
#: Mindestkontrast (Grauwerte) schwarz -> weiss.
_MIN_CONTRAST = 60.0
#: Mindestzahl brauchbarer Kantenpunkte je Kante und erlaubte Abweichung der
#: verfeinerten Ecke von der Startecke im zweiten Durchgang (px).
_MIN_EDGE_POINTS = 8
_MAX_CORNER_SHIFT_PX = 1.0
#: Dasselbe im ersten Durchgang gegen die ArUco-Ecken (bei starker Verzeichnung ungenauer).
_MAX_FIRST_SHIFT_PX = 4.0


@dataclass
class TrialMeasurement:
    """Ergebnis einer Bildmessung. Laengen in mm, Winkel in Grad."""

    ok: bool
    grund: str = ""
    #: Wuerfelmitte relativ zur Scheibenmitte (Blatt-x/-y), Aufkleber-Versatz abgezogen.
    dx_mm: float | None = None
    dy_mm: float | None = None
    #: Gierwinkel des Wuerfels, modulo 90 Grad auf [-45, 45) gewickelt.
    dtheta_deg: float | None = None
    r_mm: float | None = None
    #: "gruen" / "gelb" / "rot" / "ausserhalb" nach der aeussersten Wuerfelecke.
    zone: str | None = None
    #: IDs der erkannten Blattmarker.
    sheet_markers: list[int] = field(default_factory=list)
    reprojection_rms_px: float | None = None
    camera_height_mm: float | None = None
    camera_tilt_deg: float | None = None
    #: Abstand des Durchstosspunkts der optischen Achse (z = 20 mm) zur Wuerfelmitte.
    nadir_offset_mm: float | None = None
    #: Wuerfelecken in Blattkoordinaten, `[[x, y], ...]` (oben-links beginnend).
    cube_corners_mm: list[list[float]] | None = None
    #: Mitte des Markers selbst (ohne Aufkleber-Versatz) und sein voller Gierwinkel
    #: (0..360); die Aufkleber-Kalibrierung braucht beides.
    marker_center_mm: list[float] | None = None
    marker_yaw_deg: float | None = None
    #: Kanten des Wuerfelmarkers subpixelgenau verfeinert (sonst ArUco-Ecken).
    cube_edge_refined: bool = False
    #: Zahl der Blattmarker, deren Kanten verfeinert wurden.
    sheet_edges_refined: int = 0
    #: Verwendeter Aufkleber-Versatz im Markersystem (mm).
    sticker_offset_mm: list[float] = field(default_factory=lambda: [0.0, 0.0])
    #: Jede Zwischengroesse der Messung (Marker-Ecken roh und entzerrt,
    #: Blattpose, Reprojektion je Marker, Wuerfelecken, Kantenwinkel); bei
    #: einem Fehlschlag soweit berechnet. Felder: `messkette-protokoll.md`.
    zwischenwerte: dict | None = None
    #: Bildpunkte fuer `annotate`; nicht Teil von `to_dict`.
    overlay: dict | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        """JSON-faehiges Dict (ohne `overlay`)."""
        result: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key == "overlay":
                continue
            result[key] = _plain(value)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "TrialMeasurement":
        """Umkehrung von `to_dict`; unbekannte Schluessel werden ignoriert."""
        names = set(cls.__dataclass_fields__) - {"overlay"}
        values = {key: value for key, value in data.items() if key in names}
        values.setdefault("ok", False)
        return cls(**values)


def _plain(value: Any) -> Any:
    """numpy-Werte in reine Python-Typen wandeln."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _fail(reason: str, **extra: Any) -> TrialMeasurement:
    return TrialMeasurement(ok=False, grund=reason, **extra)


def _wrap90(angle_deg: float) -> float:
    """Winkel auf [-45, 45) wickeln (Wuerfel ist 90-Grad-symmetrisch)."""
    return (angle_deg + 45.0) % 90.0 - 45.0


def _circular_mean_deg(angles_deg: Iterable[float], fold: int = 1) -> float:
    """Zirkulaerer Mittelwert; `fold=4` mittelt modulo 90 Grad."""
    a = np.radians(np.asarray(list(angles_deg), dtype=np.float64)) * fold
    return float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())) / fold)


def _gray(image) -> np.ndarray:
    import cv2

    array = np.asarray(image)
    if array.ndim == 2:
        return array
    if array.shape[2] == 4:
        return cv2.cvtColor(array, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)


def _detect(gray: np.ndarray) -> dict[int, np.ndarray]:
    """ID -> Ecken `(4, 2)` aller erkannten Marker; bei Doppelten gewinnt der erste."""
    from tagloc.detector import build_detector

    detector = build_detector(
        target.ARUCO_FAMILY,
        "aruco",
        subpixel_corner_refinement=True,
        corner_refinement_method=CORNER_METHOD,
    )
    found: dict[int, np.ndarray] = {}
    for observation in detector.detect(gray):
        found.setdefault(
            int(observation.tag_id), np.asarray(observation.corners, dtype=np.float64).reshape(4, 2)
        )
    return found


def _fit_line(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Totale kleinste Quadrate: `(Schwerpunkt, Einheitsrichtung)`."""
    centre = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centre, full_matrices=False)
    return centre, vt[0]


def _fit_line_robust(points: np.ndarray):
    """Gerade mit iterativem Ausreisserausschluss (Median-Abstand); `None` bei zu wenigen Punkten."""
    keep = np.ones(len(points), dtype=bool)
    for _ in range(4):
        if keep.sum() < _MIN_EDGE_POINTS:
            return None
        centre, direction = _fit_line(points[keep])
        delta = points - centre
        distance = np.abs(direction[0] * delta[:, 1] - direction[1] * delta[:, 0])
        scale = max(1.4826 * float(np.median(distance[keep])), 0.1)
        keep = distance < 3.0 * scale
    if keep.sum() < _MIN_EDGE_POINTS:
        return None
    return _fit_line(points[keep])


def _edge_points(gray: np.ndarray, start: np.ndarray, end: np.ndarray, outward: np.ndarray):
    """Subpixel-Kantenpunkte (verzerrte Bildpunkte) entlang der Strecke `start`-`end`.

    Querprofile senkrecht zur Kante (bilinear per `cv2.remap`). Die Kantenlage
    folgt aus der Flaeche unter dem normierten Profil (innen schwarz -> aussen
    weiss, Pegel aus den Plateaus): bei flaechig abtastender Kamera ist das
    unabhaengig von der Phase der Kante zum Pixelraster, anders als ein
    Gradientenmaximum (das bei fast achsparallelen Kanten ein Treppenmuster
    zeigt). `outward` ist die Aussennormale.
    """
    import cv2

    length = float(np.linalg.norm(end - start))
    count = max(_MIN_EDGE_POINTS, int(0.7 * length / _EDGE_STEP_PX))
    positions = start + np.linspace(0.15, 0.85, count)[:, None] * (end - start)
    offsets = np.arange(-_PROFILE_INNER_PX, _PROFILE_OUTER_PX + 1e-9, _PROFILE_SAMPLE_PX)
    map_x = (positions[:, 0:1] + offsets[None, :] * outward[0]).astype(np.float32)
    map_y = (positions[:, 1:2] + offsets[None, :] * outward[1]).astype(np.float32)
    height, width = gray.shape
    if map_x.min() < 1 or map_y.min() < 1 or map_x.max() > width - 2 or map_y.max() > height - 2:
        return np.zeros((0, 2))
    samples = cv2.remap(gray, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    samples = samples.astype(np.float64)
    black = samples[:, offsets <= -_PLATEAU_INNER_PX].mean(axis=1)
    white = samples[:, offsets >= _PLATEAU_OUTER_PX].max(axis=1)
    contrast = white - black
    # Rampe muss das Weiss erreicht haben (sonst fehlt Flaeche am Profilende).
    usable = (contrast > _MIN_CONTRAST) & (samples[:, -1] >= black + 0.95 * contrast)
    with np.errstate(divide="ignore", invalid="ignore"):
        fraction = (samples - black[:, None]) / contrast[:, None]
    fraction = np.clip(fraction, -0.1, 1.1)
    # Innen (schwarz) ist 1 - f = 1, aussen 0: Integral(1 - f) = e - Anfang.
    outside = 1.0 - fraction
    area = (outside.sum(axis=1) - 0.5 * (outside[:, 0] + outside[:, -1])) * _PROFILE_SAMPLE_PX
    edge = offsets[0] + area
    usable &= np.isfinite(edge) & (np.abs(edge) < 1.5)
    return positions[usable] + edge[usable, None] * outward


def _refine_quad_once(gray: np.ndarray, raw: np.ndarray, calibration, max_shift_px: float) -> np.ndarray | None:
    """Marker-Ecken aus den vier Aussenkanten, entzerrt `(4, 2)`; `None` bei Misserfolg.

    Je Kante Kantenpunkte im Originalbild, danach entzerrt (`tagloc.lens`,
    Pinhole wie Fisheye), Gerade per robustem TLS, Ecken = Schnitte benachbarter
    Geraden. Weicht eine Ecke um mehr als `max_shift_px` von der Startecke ab,
    gilt die Verfeinerung als gescheitert.
    """
    from tagloc.lens import undistort_points

    centre = raw.mean(axis=0)
    lines = []
    for i in range(4):
        start, end = raw[i], raw[(i + 1) % 4]
        length = float(np.linalg.norm(end - start))
        if length < 12.0:
            return None
        tangent = (end - start) / length
        normal = np.array([tangent[1], -tangent[0]])
        if normal @ ((start + end) / 2.0 - centre) < 0:
            normal = -normal
        points = _edge_points(gray, start, end, normal)
        if len(points) < _MIN_EDGE_POINTS:
            return None
        line = _fit_line_robust(undistort_points(points, calibration))
        if line is None:
            return None
        lines.append(line)
    corners = np.zeros((4, 2))
    for i in range(4):
        (c1, d1), (c2, d2) = lines[i - 1], lines[i]  # Ecke i = Schnitt der Kanten i-1 und i
        matrix = np.column_stack([d1, -d2])
        if abs(np.linalg.det(matrix)) < 0.3:
            return None
        t = np.linalg.solve(matrix, c2 - c1)[0]
        corners[i] = c1 + t * d1
    if np.max(np.linalg.norm(corners - undistort_points(raw, calibration), axis=1)) > max_shift_px:
        return None
    return corners


def _refine_quad(gray: np.ndarray, raw: np.ndarray, calibration) -> np.ndarray | None:
    """Zweistufig: erst um die ArUco-Ecken, dann um die schon verfeinerte Lage."""
    first = _refine_quad_once(gray, raw, calibration, _MAX_FIRST_SHIFT_PX)
    if first is None:
        return None
    second_raw = _distort(first, calibration)
    second = _refine_quad_once(gray, second_raw, calibration, _MAX_CORNER_SHIFT_PX)
    return first if second is None else second


def _distort(undistorted_px: np.ndarray, calibration) -> np.ndarray:
    """Entzerrte Bildpunkte zurueck ins verzerrte Originalbild (Pinhole und Fisheye)."""
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    rays = np.column_stack(
        [
            (undistorted_px[:, 0] - matrix[0, 2]) / matrix[0, 0],
            (undistorted_px[:, 1] - matrix[1, 2]) / matrix[1, 1],
            np.ones(len(undistorted_px)),
        ]
    )
    from tagloc.lens import project_points

    return project_points(rays, np.zeros(3), np.zeros(3), calibration)


def measure_trial(
    image,
    calibration,
    cube_marker_offset_mm: Sequence[float] = (0.0, 0.0),
    *,
    refine_edges: bool = True,
) -> TrialMeasurement:
    """Misst die Wuerfelablage in `image`.

    `calibration`: `tagloc.calibration.CameraCalibration` der Aufnahme.
    `cube_marker_offset_mm`: Lage der Markermitte relativ zur Wuerfelmitte,
    im Markersystem (x = Richtung Kante oben-links -> oben-rechts, y = nach
    "oben" im Markerbild). Wird in Blattachsen gedreht und abgezogen.
    `refine_edges`: Marker-Ecken zusaetzlich aus den Aussenkanten bestimmen
    (genauer als die ArUco-Ecken); bei Misserfolg gelten die ArUco-Ecken, das
    steht in `cube_edge_refined`/`sheet_edges_refined`.
    Wirft nie; Fehler stehen in `ok`/`grund`.
    """
    try:
        return _measure(image, calibration, cube_marker_offset_mm, refine_edges)
    except Exception as error:  # Messfehler duerfen nie den Aufrufer treffen
        return _fail(f"Messung fehlgeschlagen: {type(error).__name__}: {error}")


@dataclass
class _SheetPose:
    """Ergebnis von `_sheet_pose`: Blattpose samt allem, was die Aufrufer weiter brauchen."""

    found: dict
    sheet_ids: list
    undistorted_corners: Any
    marker_log: dict
    zwischenwerte: dict
    camera_matrix: np.ndarray
    object_points: np.ndarray
    rvec: np.ndarray
    tvec: np.ndarray
    rotation: np.ndarray
    camera_center: np.ndarray
    axis_sheet: np.ndarray
    rms: float
    sheet_refined: int


def _sheet_pose(image, calibration, refine_edges: bool, require_cube: bool):
    """Blattpose aus den Blattmarkern; `_SheetPose` oder eine fehlgeschlagene `TrialMeasurement`.

    Gemeinsamer Teil von `_measure` und `measure_sheet`, damit beide dieselben
    Zahlen rechnen: Erkennung, entzerrte/verfeinerte Blattecken, solvePnP (IPPE)
    plus RefineLM, Reprojektionsfehler, Drehung, Kamerazentrum, optische Achse.
    `require_cube`: zusaetzlich den Wuerfelmarker (ID 10) verlangen.
    """
    import cv2

    from tagloc.lens import undistort_points

    height_px, width_px = np.asarray(image).shape[:2]
    if (int(width_px), int(height_px)) != tuple(int(v) for v in calibration.image_size):
        return _fail(
            f"Bildgroesse {width_px}x{height_px} passt nicht zur Kalibrierung "
            f"{calibration.image_size[0]}x{calibration.image_size[1]}"
        )
    gray = _gray(image)
    found = _detect(gray)
    blurred = gray.astype(np.float32) if refine_edges else None

    def undistorted_corners(raw: np.ndarray):
        """`(entzerrte Ecken (4, 2), verfeinert?)` eines erkannten Markers."""
        if blurred is not None:
            refined = _refine_quad(blurred, raw, calibration)
            if refined is not None:
                return refined, True
        return undistort_points(raw, calibration), False

    sheet_ids = sorted(i for i in found if i in target.SHEET_MARKERS)
    # Alles, was die Messung unterwegs berechnet -- auch bei einem Fehlschlag.
    marker_log: dict[int, dict] = {
        int(i): {"eckenRohPx": np.asarray(corners, dtype=np.float64).reshape(-1, 2)} for i, corners in found.items()
    }
    zwischenwerte: dict[str, Any] = {"marker": marker_log}
    if require_cube and target.CUBE_MARKER_ID not in found:
        return _fail("Kein Wuerfelmarker (ID 10) erkannt", sheet_markers=sheet_ids, zwischenwerte=zwischenwerte)
    if len(sheet_ids) < MIN_SHEET_MARKERS:
        return _fail(
            f"Nur {len(sheet_ids)} Blattmarker erkannt, mindestens {MIN_SHEET_MARKERS} noetig",
            sheet_markers=sheet_ids,
            zwischenwerte=zwischenwerte,
        )

    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    object_points = np.vstack([target.sheet_marker_corners(i) for i in sheet_ids])
    sheet_results = [undistorted_corners(found[i]) for i in sheet_ids]
    sheet_px = np.vstack([corners for corners, _ in sheet_results])
    sheet_refined = sum(1 for _, refined in sheet_results if refined)
    for marker_id, (corners, refined) in zip(sheet_ids, sheet_results):
        marker_log[int(marker_id)].update(eckenEntzerrtPx=corners, kanteVerfeinert=refined)

    ok, rvec, tvec = cv2.solvePnP(
        object_points, sheet_px, camera_matrix, None, flags=cv2.SOLVEPNP_IPPE
    )
    if not ok:
        return _fail("Blattpose nicht berechenbar (solvePnP)", sheet_markers=sheet_ids, zwischenwerte=zwischenwerte)
    rvec, tvec = cv2.solvePnPRefineLM(object_points, sheet_px, camera_matrix, None, rvec, tvec)
    projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, None)
    squared = np.sum((projected.reshape(-1, 2) - sheet_px) ** 2, axis=1)
    rms = float(np.sqrt(np.mean(squared)))
    # Je Blattmarker vier Punkte, in der Reihenfolge von `sheet_ids`.
    for at, marker_id in enumerate(sheet_ids):
        marker_log[int(marker_id)]["reprojektionRmsPx"] = float(np.sqrt(np.mean(squared[4 * at : 4 * at + 4])))

    rotation, _ = cv2.Rodrigues(rvec)  # Blatt -> Kamera
    camera_center = -rotation.T @ tvec.reshape(3)  # Kamera im Blattsystem
    axis_sheet = rotation.T @ np.array([0.0, 0.0, 1.0])  # optische Achse im Blattsystem
    zwischenwerte["blattPose"] = {
        "rvec": rvec.reshape(3),
        "tvecMm": tvec.reshape(3),
        "rotationBlattZuKamera": rotation,
        "kameraImBlattMm": camera_center,
        "optischeAchseImBlatt": axis_sheet,
        "punkte": int(len(object_points)),
    }
    return _SheetPose(
        found=found,
        sheet_ids=sheet_ids,
        undistorted_corners=undistorted_corners,
        marker_log=marker_log,
        zwischenwerte=zwischenwerte,
        camera_matrix=camera_matrix,
        object_points=object_points,
        rvec=rvec,
        tvec=tvec,
        rotation=rotation,
        camera_center=camera_center,
        axis_sheet=axis_sheet,
        rms=rms,
        sheet_refined=sheet_refined,
    )


def measure_sheet(image, calibration, *, refine_edges: bool = True) -> dict:
    """Blattpose aus den Blattmarkern (IDs 0-3), ohne Wuerfel. Wirft nie.

    Rechnet dieselbe Blattpose wie `measure_trial` (gemeinsamer Helfer).
    Rueckgabe: `{"ok", "grund", "camera_height_mm", "camera_tilt_deg",
    "camera_in_sheet_mm" [x, y, z], "camera_rotation_in_sheet" (3x3 zeilenweise,
    Spalten = Kamera-x/-y/-z im Blatt-KS), "camera_yaw_deg" (Kamera-x in der Blattebene), "axis_hit_mm" [x, y] (optische Achse trifft
    die Blattebene z = 0), "reprojection_rms_px", "sheet_markers",
    "sheet_edges_refined"}`; bei Fehlschlag `ok` False, `grund` erklaert, die
    Messwerte sind None.
    """
    result: dict[str, Any] = {
        "ok": False,
        "grund": "",
        "camera_height_mm": None,
        "camera_tilt_deg": None,
        "camera_in_sheet_mm": None,
        "axis_hit_mm": None,
        "camera_rotation_in_sheet": None,
        "camera_yaw_deg": None,
        "reprojection_rms_px": None,
        "sheet_markers": [],
        "sheet_edges_refined": 0,
    }
    try:
        pose = _sheet_pose(image, calibration, refine_edges, require_cube=False)
        if isinstance(pose, TrialMeasurement):
            result["grund"] = pose.grund
            result["sheet_markers"] = [int(i) for i in pose.sheet_markers]
            return result
        result["sheet_markers"] = [int(i) for i in pose.sheet_ids]
        result["reprojection_rms_px"] = float(pose.rms)
        result["sheet_edges_refined"] = int(pose.sheet_refined)
        center, axis = pose.camera_center, pose.axis_sheet
        if center[2] <= 0.0:
            result["grund"] = "Blattpose unplausibel (Kamera nicht ueber dem Blatt)"
            return result
        result["camera_height_mm"] = float(center[2])
        result["camera_tilt_deg"] = math.degrees(math.acos(float(np.clip(-axis[2], -1.0, 1.0))))
        result["camera_in_sheet_mm"] = [float(v) for v in center]
        # Spalten = Kamera-x, -y, -z im Blatt-KS (pose.rotation: Blatt -> Kamera).
        camera_rotation = np.asarray(pose.rotation, dtype=np.float64).T
        result["camera_rotation_in_sheet"] = [[float(v) for v in row] for row in camera_rotation]
        result["camera_yaw_deg"] = float(
            math.degrees(math.atan2(camera_rotation[1][0], camera_rotation[0][0]))
        )
        if axis[2] >= -1e-6:
            result["grund"] = "Optische Achse trifft die Blattebene nicht"
            return result
        hit = center + axis * (-center[2] / axis[2])
        result["axis_hit_mm"] = [float(hit[0]), float(hit[1])]
        result["ok"] = True
        return result
    except Exception as error:  # Messfehler duerfen nie den Aufrufer treffen
        result["ok"] = False
        result["grund"] = f"Messung fehlgeschlagen: {type(error).__name__}: {error}"
        return result


def _measure(image, calibration, offset, refine_edges=True) -> TrialMeasurement:
    from tagloc.lens import project_points

    offset_vec = np.asarray(offset, dtype=np.float64).reshape(2)
    pose = _sheet_pose(image, calibration, refine_edges, require_cube=True)
    if isinstance(pose, TrialMeasurement):
        return pose
    found, sheet_ids = pose.found, pose.sheet_ids
    undistorted_corners, marker_log = pose.undistorted_corners, pose.marker_log
    zwischenwerte, camera_matrix = pose.zwischenwerte, pose.camera_matrix
    rvec, tvec, rotation = pose.rvec, pose.tvec, pose.rotation
    camera_center, axis_sheet = pose.camera_center, pose.axis_sheet
    rms, sheet_refined = pose.rms, pose.sheet_refined
    if camera_center[2] <= target.CUBE_TOP_Z_MM:
        return _fail(
            "Blattpose unplausibel (Kamera nicht ueber dem Blatt)",
            sheet_markers=sheet_ids,
            reprojection_rms_px=rms,
            zwischenwerte=zwischenwerte,
        )
    tilt = math.degrees(math.acos(float(np.clip(-axis_sheet[2], -1.0, 1.0))))

    def to_sheet_plane(pixels: np.ndarray, z_mm: float) -> np.ndarray:
        """Entzerrte Bildpunkte -> Punkte auf der Ebene z = `z_mm` im Blattsystem."""
        normalized = np.column_stack(
            [
                (pixels[:, 0] - camera_matrix[0, 2]) / camera_matrix[0, 0],
                (pixels[:, 1] - camera_matrix[1, 2]) / camera_matrix[1, 1],
                np.ones(len(pixels)),
            ]
        )
        directions = normalized @ rotation  # == (R^T d) je Zeile
        scale = (z_mm - camera_center[2]) / directions[:, 2]
        return camera_center + directions * scale[:, None]

    cube_raw = found[target.CUBE_MARKER_ID]
    cube_px, cube_refined = undistorted_corners(cube_raw)
    marker_log[int(target.CUBE_MARKER_ID)].update(eckenEntzerrtPx=cube_px, kanteVerfeinert=cube_refined)
    cube_pts = to_sheet_plane(cube_px, target.CUBE_TOP_Z_MM)
    xy = cube_pts[:, :2]

    # Kantenrichtungen: oben-links->oben-rechts und unten-links->unten-rechts liegen
    # bei theta, die Quer-Kanten bei theta - 90 (daher + 90).
    edges = [
        xy[1] - xy[0],
        xy[2] - xy[3],
        _rot90(xy[3] - xy[0]),
        _rot90(xy[2] - xy[1]),
    ]
    yaw_full = _circular_mean_deg(math.degrees(math.atan2(e[1], e[0])) for e in edges)
    dtheta = _wrap90(yaw_full)

    marker_center = xy.mean(axis=0)
    offset_in_sheet = target.rotation_2d(yaw_full) @ offset_vec
    center = marker_center - offset_in_sheet
    corners = target.cube_corners_xy(center, yaw_full)
    max_radius = float(np.max(np.hypot(corners[:, 0], corners[:, 1])))

    # Durchstosspunkt der optischen Achse mit der Wuerfeloberseite fuer die Qualitaet.
    if axis_sheet[2] < -1e-6:
        hit = camera_center + axis_sheet * ((target.CUBE_TOP_Z_MM - camera_center[2]) / axis_sheet[2])
        nadir_offset = float(np.hypot(hit[0] - center[0], hit[1] - center[1]))
    else:
        hit = None
        nadir_offset = None
    zwischenwerte["wuerfel"] = {
        "eckenOberseiteMm": cube_pts,
        "kantenYawDeg": [math.degrees(math.atan2(edge[1], edge[0])) for edge in edges],
        "markerMitteMm": marker_center,
        "versatzImBlattMm": offset_in_sheet,
    }
    zwischenwerte["durchstosspunktMm"] = hit

    overlay = _build_overlay(
        calibration, rvec, tvec, sheet_ids, found, cube_raw, corners, project_points
    )
    return TrialMeasurement(
        ok=True,
        dx_mm=float(center[0]),
        dy_mm=float(center[1]),
        dtheta_deg=float(dtheta),
        r_mm=float(np.hypot(center[0], center[1])),
        zone=target.zone_for_radius(max_radius),
        sheet_markers=sheet_ids,
        reprojection_rms_px=rms,
        camera_height_mm=float(camera_center[2]),
        camera_tilt_deg=tilt,
        nadir_offset_mm=nadir_offset,
        cube_corners_mm=corners.tolist(),
        cube_edge_refined=cube_refined,
        sheet_edges_refined=sheet_refined,
        marker_center_mm=marker_center.tolist(),
        marker_yaw_deg=float(yaw_full % 360.0),
        sticker_offset_mm=offset_vec.tolist(),
        zwischenwerte=zwischenwerte,
        overlay=overlay,
    )


def _rot90(vector: np.ndarray) -> np.ndarray:
    """Vektor um +90 Grad drehen (Blatt, y oben)."""
    return np.array([-vector[1], vector[0]])


def _build_overlay(calibration, rvec, tvec, sheet_ids, found, cube_raw, cube_corners, project_points):
    """Bildpunkte fuers Kontrollbild (Blattmarker, Wuerfelmarker, Wuerfelumriss, Kreise)."""
    outline = np.column_stack([cube_corners, np.full(4, target.CUBE_TOP_Z_MM)])
    angles = np.radians(np.arange(0, 360, 5.0))
    circles = {}
    for zone, radius in target.ZONE_RADII_MM:
        ring = np.column_stack(
            [radius * np.cos(angles), radius * np.sin(angles), np.zeros(len(angles))]
        )
        circles[zone] = project_points(ring, rvec, tvec, calibration)
    return {
        "sheet": {i: found[i] for i in sheet_ids},
        "cube": cube_raw,
        "outline": project_points(outline, rvec, tvec, calibration),
        "circles": circles,
    }


def annotate(image, measurement: TrialMeasurement):
    """Kontrollbild: Kopie von `image` mit erkannten Markern, Kreisen und Messwerten."""
    import cv2

    array = np.asarray(image)
    canvas = (
        cv2.cvtColor(array, cv2.COLOR_GRAY2BGR) if array.ndim == 2 else array[:, :, :3].copy()
    )
    overlay = measurement.overlay
    if overlay:
        zone_colors = {"gruen": (50, 180, 50), "gelb": (0, 200, 240), "rot": (40, 40, 220)}
        for zone, ring in overlay["circles"].items():
            cv2.polylines(canvas, [np.round(ring).astype(np.int32)], True, zone_colors[zone], 1)
        for corners in overlay["sheet"].values():
            cv2.polylines(canvas, [np.round(corners).astype(np.int32)], True, (255, 160, 0), 1)
        cv2.polylines(canvas, [np.round(overlay["cube"]).astype(np.int32)], True, (255, 0, 255), 1)
        cv2.polylines(canvas, [np.round(overlay["outline"]).astype(np.int32)], True, (0, 255, 255), 1)
    if measurement.ok:
        lines = [
            f"dx {measurement.dx_mm:+.2f} mm  dy {measurement.dy_mm:+.2f} mm  "
            f"dtheta {measurement.dtheta_deg:+.2f} deg",
            f"r {measurement.r_mm:.2f} mm  Zone {measurement.zone}",
        ]
    else:
        lines = [f"Messung fehlgeschlagen: {measurement.grund}"]
    for row, text in enumerate(lines):
        position = (6, 16 + 16 * row)
        cv2.putText(canvas, text, position, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(canvas, text, position, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


def _std(values: np.ndarray) -> float:
    """Stichproben-Standardabweichung; 0 bei einem Wert."""
    return float(np.std(values, ddof=1)) if len(values) > 1 else 0.0


def summarize(measurements: Iterable[TrialMeasurement]) -> dict:
    """Kennzahlen in Anlehnung an ISO 9283 ueber alle erfolgreichen Messungen.

    - Genauigkeit `AP_mm` = Abstand des Mittelpunkts aller Ablagen vom Soll
      (Scheibenmitte), `AP_theta_deg` = |mittleres dtheta|.
    - Wiederholgenauigkeit `RP_mm` = l_mittel + 3 * S_l mit l = Abstand der
      Einzelablage zum Mittelpunkt; `RP_theta_deg` = 3 * S_theta.
    Das Mittel von dtheta ist zirkulaer modulo 90 Grad.
    """
    all_items = list(measurements)
    good = [m for m in all_items if m.ok]
    zones = {name: 0 for name, _ in target.ZONE_RADII_MM}
    zones[target.ZONE_OUTSIDE] = 0
    for m in good:
        zones[m.zone] += 1
    summary: dict[str, Any] = {
        "n": len(good),
        "n_gesamt": len(all_items),
        "n_fehlgeschlagen": len(all_items) - len(good),
        "zonen": zones,
    }
    if not good:
        return summary
    dx = np.array([m.dx_mm for m in good])
    dy = np.array([m.dy_mm for m in good])
    theta = np.array([m.dtheta_deg for m in good])
    mean_x, mean_y = float(dx.mean()), float(dy.mean())
    mean_theta = _circular_mean_deg(theta, fold=4)
    spread = np.array([_wrap90(t - mean_theta) for t in theta])
    distance = np.hypot(dx - mean_x, dy - mean_y)
    summary.update(
        {
            "dx_mean_mm": mean_x,
            "dx_std_mm": _std(dx),
            "dy_mean_mm": mean_y,
            "dy_std_mm": _std(dy),
            "dtheta_mean_deg": mean_theta,
            "dtheta_std_deg": _std(spread),
            "AP_mm": float(math.hypot(mean_x, mean_y)),
            "l_mean_mm": float(distance.mean()),
            "l_std_mm": _std(distance),
            "RP_mm": float(distance.mean() + 3.0 * _std(distance)),
            "AP_theta_deg": abs(mean_theta),
            "RP_theta_deg": 3.0 * _std(spread),
        }
    )
    return summary


def sticker_offset_from_pair(m0: TrialMeasurement, m180: TrialMeasurement) -> tuple[float, float]:
    """Aufkleber-Versatz (mm, Markersystem) aus Messungen vor/nach 180-Grad-Drehung.

    Der Wuerfel wird um seine Mitte gedreht, der Marker wandert dabei auf die
    gegenueberliegende Seite: Mitte = (M0 + M1) / 2, Versatz = (M0 - M1) / 2,
    ins Markersystem der ersten Lage zurueckgedreht. Die Messungen duerfen mit
    oder ohne bisherigen Versatz entstanden sein (verwendet wird die
    Markermitte). Wirft `ValueError`, wenn eine Messung fehlt oder die
    Drehung nicht etwa 180 Grad betraegt (mehr als 30 Grad Abweichung).
    """
    for m in (m0, m180):
        if not m.ok or m.marker_center_mm is None or m.marker_yaw_deg is None:
            raise ValueError("Beide Messungen muessen erfolgreich sein (Markerlage fehlt)")
    turn = (m180.marker_yaw_deg - m0.marker_yaw_deg + 180.0) % 360.0 - 180.0  # Soll: +-180
    if abs(abs(turn) - 180.0) > 30.0:
        raise ValueError(f"Wuerfel wurde um {turn:.0f} statt um 180 Grad gedreht")
    yaw0 = _circular_mean_deg([m0.marker_yaw_deg, m180.marker_yaw_deg - 180.0])
    half_diff = (np.asarray(m0.marker_center_mm) - np.asarray(m180.marker_center_mm)) / 2.0
    offset = target.rotation_2d(-yaw0) @ half_diff
    return float(offset[0]), float(offset[1])
