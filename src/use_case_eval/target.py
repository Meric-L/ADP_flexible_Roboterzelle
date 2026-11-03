"""Geometrie der Zielscheibe v2 und des Wuerfelmarkers -- die einzige Quelle.

Zielscheibengenerator (`tools/make_use_case_target.py`), Messung
(`use_case_eval.measure`) und synthetische Bilder (`use_case_eval.synthetic`)
lesen alles von hier. Nur numpy, kein OpenCV.

Blattkoordinaten in mm: Ursprung in der Scheibenmitte, x zeigt nach 0 Grad,
y nach 90 Grad (also im Ausdruck nach oben), z nach oben aus dem Blatt.
Die Ecken eines Markers stehen in der Reihenfolge von OpenCV-ArUco: oben-links,
oben-rechts, unten-rechts, unten-links **im Markerbild**. Weil Blatt-y nach oben
zeigt (Bild/SVG-y nach unten), liegt "oben" bei +y.
"""

import numpy as np

#: Name im `tagloc.detector.ARUCO_DICTIONARIES` (cv2.aruco.DICT_4X4_50).
ARUCO_FAMILY = "aruco_4x4_50"

#: Skalenring (Radien in mm).
RING_INNER_RADIUS_MM = 80.0
RING_OUTER_RADIUS_MM = 92.0

#: Toleranzkreise: (Zone, Durchmesser mm). Wuerfeldiagonale 28,3 + 2 x 1/3/5 mm.
CIRCLES = (
    ("gruen", 30.3),
    ("gelb", 34.3),
    ("rot", 38.3),
)
#: Zonen von innen nach aussen mit Radius in mm. Die aeusserste Wuerfelecke
#: muss innerhalb dieses Radius liegen -- das sieht man auch auf dem Blatt.
ZONE_RADII_MM = tuple((name, diameter / 2.0) for name, diameter in CIRCLES)
ZONE_OUTSIDE = "ausserhalb"

CUBE_SIZE_MM = 20.0
#: Hoehe der Wuerfeloberseite ueber dem Blatt.
CUBE_TOP_Z_MM = 20.0

#: Blattmarker: ID -> Mittelpunkt (x, y) in mm. Kanten parallel zu den Blattachsen.
SHEET_MARKER_SIZE_MM = 20.0
SHEET_MARKERS = {
    0: (-45.0, 45.0),
    1: (45.0, 45.0),
    2: (45.0, -45.0),
    3: (-45.0, -45.0),
}

#: Wuerfelmarker (Aufkleber auf der Oberseite).
CUBE_MARKER_ID = 10
CUBE_MARKER_SIZE_MM = 18.0
#: Kurzform fuer die Aufrufer.
CUBE_MARKER = {"id": CUBE_MARKER_ID, "size_mm": CUBE_MARKER_SIZE_MM}

#: Mindestens eine Markerzelle weisser Rand um die Blattmarker (6 Zellen inkl. Rand).
SHEET_MARKER_QUIET_MM = SHEET_MARKER_SIZE_MM / 6.0


def marker_corners(size_mm: float, center_xy=(0.0, 0.0), z_mm: float = 0.0) -> np.ndarray:
    """Ecken eines achsparallelen Markers, `(4, 3)`, Reihenfolge wie OpenCV-ArUco."""
    half = size_mm / 2.0
    cx, cy = center_xy
    return np.array(
        [
            [cx - half, cy + half, z_mm],  # oben-links
            [cx + half, cy + half, z_mm],  # oben-rechts
            [cx + half, cy - half, z_mm],  # unten-rechts
            [cx - half, cy - half, z_mm],  # unten-links
        ],
        dtype=np.float64,
    )


def sheet_marker_corners(marker_id: int) -> np.ndarray:
    """Ecken des Blattmarkers `marker_id` in Blattkoordinaten (mm, z = 0), `(4, 3)`."""
    return marker_corners(SHEET_MARKER_SIZE_MM, SHEET_MARKERS[int(marker_id)])


def cube_marker_corners() -> np.ndarray:
    """Ecken des Wuerfelmarkers im Markersystem (Mitte = Ursprung, z = 0), `(4, 3)`."""
    return marker_corners(CUBE_MARKER_SIZE_MM)


def rotation_2d(angle_deg: float) -> np.ndarray:
    """2x2-Drehmatrix, mathematisch positiv (gegen den Uhrzeigersinn, y oben)."""
    a = np.radians(angle_deg)
    return np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])


def cube_corners_xy(center_xy, yaw_deg: float) -> np.ndarray:
    """Die vier Wuerfelecken in Blattkoordinaten (mm), `(4, 2)`."""
    half = CUBE_SIZE_MM / 2.0
    local = np.array([[-half, half], [half, half], [half, -half], [-half, -half]])
    return np.asarray(center_xy, dtype=np.float64) + local @ rotation_2d(yaw_deg).T


def zone_for_radius(radius_mm: float) -> str:
    """Zone zu einem Radius der aeussersten Wuerfelecke."""
    for name, limit in ZONE_RADII_MM:
        if radius_mm <= limit:
            return name
    return ZONE_OUTSIDE
