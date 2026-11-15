"""Geometry of the target sheet v2 and the cube marker -- the single source.

The target generator (`tools/make_use_case_target.py`), the measurement
(`use_case_eval.measure`) and the synthetic images (`use_case_eval.synthetic`)
read everything from here. numpy only, no OpenCV.

Sheet coordinates in mm: origin at the centre of the target, x points to 0 degrees,
y to 90 degrees (i.e. upwards on the printout), z upwards out of the sheet.
The corners of a marker are in OpenCV ArUco order: top-left, top-right,
bottom-right, bottom-left **in the marker image**. Because sheet y points up
(image/SVG y points down), "top" corresponds to +y.
"""

import numpy as np

#: Name in `tagloc.detector.ARUCO_DICTIONARIES` (cv2.aruco.DICT_4X4_50).
ARUCO_FAMILY = "aruco_4x4_50"

#: Scale ring (radii in mm).
RING_INNER_RADIUS_MM = 80.0
RING_OUTER_RADIUS_MM = 92.0

#: Tolerance circles: (zone, diameter in mm). Cube diagonal 28.3 + 2 x 1/3/5 mm.
CIRCLES = (
    ("gruen", 30.3),
    ("gelb", 34.3),
    ("rot", 38.3),
)
#: Zones from inside to outside with radius in mm. The outermost cube corner
#: must lie within this radius -- this is also visible on the sheet.
ZONE_RADII_MM = tuple((name, diameter / 2.0) for name, diameter in CIRCLES)
ZONE_OUTSIDE = "ausserhalb"

CUBE_SIZE_MM = 20.0
#: Height of the top face of the cube above the sheet.
CUBE_TOP_Z_MM = 20.0

#: Sheet markers: ID -> centre (x, y) in mm. Edges parallel to the sheet axes.
SHEET_MARKER_SIZE_MM = 20.0
SHEET_MARKERS = {
    0: (-45.0, 45.0),
    1: (45.0, 45.0),
    2: (45.0, -45.0),
    3: (-45.0, -45.0),
}

#: Cube marker (sticker on the top face).
CUBE_MARKER_ID = 10
CUBE_MARKER_SIZE_MM = 18.0
#: Short form for the callers.
CUBE_MARKER = {"id": CUBE_MARKER_ID, "size_mm": CUBE_MARKER_SIZE_MM}

#: At least one marker cell of white border around the sheet markers (6 cells incl. border).
SHEET_MARKER_QUIET_MM = SHEET_MARKER_SIZE_MM / 6.0


def marker_corners(size_mm: float, center_xy=(0.0, 0.0), z_mm: float = 0.0) -> np.ndarray:
    """Corners of an axis-parallel marker, `(4, 3)`, in OpenCV ArUco order."""
    half = size_mm / 2.0
    cx, cy = center_xy
    return np.array(
        [
            [cx - half, cy + half, z_mm],  # top-left
            [cx + half, cy + half, z_mm],  # top-right
            [cx + half, cy - half, z_mm],  # bottom-right
            [cx - half, cy - half, z_mm],  # bottom-left
        ],
        dtype=np.float64,
    )


def sheet_marker_corners(marker_id: int) -> np.ndarray:
    """Corners of sheet marker `marker_id` in sheet coordinates (mm, z = 0), `(4, 3)`."""
    return marker_corners(SHEET_MARKER_SIZE_MM, SHEET_MARKERS[int(marker_id)])


def cube_marker_corners() -> np.ndarray:
    """Corners of the cube marker in marker coordinates (centre = origin, z = 0), `(4, 3)`."""
    return marker_corners(CUBE_MARKER_SIZE_MM)


def rotation_2d(angle_deg: float) -> np.ndarray:
    """Return the 2x2 rotation matrix, positive in the mathematical sense (counter-clockwise, y up)."""
    a = np.radians(angle_deg)
    return np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])


def cube_corners_xy(center_xy, yaw_deg: float) -> np.ndarray:
    """Return the four cube corners in sheet coordinates (mm), `(4, 2)`."""
    half = CUBE_SIZE_MM / 2.0
    local = np.array([[-half, half], [half, half], [half, -half], [-half, -half]])
    return np.asarray(center_xy, dtype=np.float64) + local @ rotation_2d(yaw_deg).T


def zone_for_radius(radius_mm: float) -> str:
    """Return the zone for the radius of the outermost cube corner."""
    for name, limit in ZONE_RADII_MM:
        if radius_mm <= limit:
            return name
    return ZONE_OUTSIDE
