"""Image measurement of the cube placement: deviation (dx, dy, dtheta) from the target center.

Flow of `measure_trial`:

1. Detect the ArUco markers (4x4_50), corners refined to subpixel accuracy.
2. Sheet pose via `cv2.solvePnP` from the corners of the sheet markers (IDs 0-3,
   at least 2). Image points are undistorted beforehand (`tagloc.lens`), so that
   pinhole and fisheye calibrations run the same way.
3. Cast the corners of the cube marker (ID 10) as rays into the sheet frame and
   intersect them with the plane z = 20 mm (cube top). Center and yaw angle
   follow from the four intersection points; a sticker offset is subtracted.

Errors (no marker, too few sheet markers, pose not solvable) return `ok=False`
with a German `grund` message, never an exception. The calculation functions
are pure; OpenCV is imported only inside the functions.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from . import target

#: Minimum number of detected sheet markers for a sheet pose.
MIN_SHEET_MARKERS = 2
#: Corner refinement method of the ArUco detector (`tagloc.detector.CORNER_METHODS`).
CORNER_METHOD = "subpix"

# Edge refinement of the markers (see `_refine_quad`).
#: Cross profiles: spacing along the edge (px) and step size within the profile (px).
_EDGE_STEP_PX = 0.5
_PROFILE_SAMPLE_PX = 0.25
#: Profile range perpendicular to the edge (px): inside is the black marker border,
#: outside the white margin (for the cube sticker only 1 mm, about 2 px wide).
_PROFILE_INNER_PX = 3.0
_PROFILE_OUTER_PX = 1.6
#: Levels: black = mean up to -2 px inward, white = maximum from +0.75 px outward.
_PLATEAU_INNER_PX = 2.0
_PLATEAU_OUTER_PX = 0.75
#: Minimum contrast (gray values) black -> white.
_MIN_CONTRAST = 60.0
#: Minimum number of usable edge points per edge, and the allowed deviation of the
#: refined corner from the start corner in the second pass (px).
_MIN_EDGE_POINTS = 8
_MAX_CORNER_SHIFT_PX = 1.0
#: The same for the first pass, against the ArUco corners (less accurate under strong distortion).
_MAX_FIRST_SHIFT_PX = 4.0


@dataclass
class TrialMeasurement:
    """Result of an image measurement. Lengths in mm, angles in degrees."""

    ok: bool
    grund: str = ""
    #: Cube center relative to the target center (sheet x/y), sticker offset subtracted.
    dx_mm: float | None = None
    dy_mm: float | None = None
    #: Yaw angle of the cube, wrapped modulo 90 degrees to [-45, 45).
    dtheta_deg: float | None = None
    r_mm: float | None = None
    #: Zone ("gruen" / "gelb" / "rot" / "ausserhalb") based on the outermost cube corner.
    zone: str | None = None
    #: IDs of the detected sheet markers.
    sheet_markers: list[int] = field(default_factory=list)
    reprojection_rms_px: float | None = None
    camera_height_mm: float | None = None
    camera_tilt_deg: float | None = None
    #: Distance from the point where the optical axis pierces the plane z = 20 mm
    #: to the cube center.
    nadir_offset_mm: float | None = None
    #: Cube corners in sheet coordinates, `[[x, y], ...]` (starting at top-left).
    cube_corners_mm: list[list[float]] | None = None
    #: Center of the marker itself (without sticker offset) and its full yaw angle
    #: (0..360). The sticker calibration needs both.
    marker_center_mm: list[float] | None = None
    marker_yaw_deg: float | None = None
    #: Edges of the cube marker refined to subpixel accuracy (otherwise ArUco corners).
    cube_edge_refined: bool = False
    #: Number of sheet markers whose edges were refined.
    sheet_edges_refined: int = 0
    #: Sticker offset used, in the marker frame (mm).
    sticker_offset_mm: list[float] = field(default_factory=lambda: [0.0, 0.0])
    #: Reference of the measurement: "sheet" (target) or "tag" (outer edge of a
    #: black tag square, center = origin).
    reference: str = "sheet"
    #: Tag mode only: edge length (mm), expected ID, edges refined?,
    #: raw corners in the (distorted) image in the chosen order (top-left,
    #: top-right, bottom-right, bottom-left in the tag frame).
    tag_size_mm: float | None = None
    tag_id: int | None = None
    tag_edges_refined: bool | None = None
    tag_corners_px: list | None = None
    #: Image points for `annotate`; not part of `to_dict`.
    overlay: dict | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict (without `overlay`)."""
        result: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key == "overlay":
                continue
            result[key] = _plain(value)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "TrialMeasurement":
        """Inverse of `to_dict`. Unknown keys are ignored."""
        names = set(cls.__dataclass_fields__) - {"overlay"}
        values = {key: value for key, value in data.items() if key in names}
        values.setdefault("ok", False)
        return cls(**values)


def _plain(value: Any) -> Any:
    """Convert numpy values into plain Python types."""
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
    """Wrap an angle to [-45, 45). The cube is symmetric under 90-degree rotation."""
    return (angle_deg + 45.0) % 90.0 - 45.0


def _circular_mean_deg(angles_deg: Iterable[float], fold: int = 1) -> float:
    """Circular mean. ``fold=4`` averages modulo 90 degrees."""
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
    """Map each ID to the corners ``(4, 2)`` of all detected markers. The first one wins on duplicates."""
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
    """Fit a line by total least squares. Return ``(centroid, unit direction)``."""
    centre = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centre, full_matrices=False)
    return centre, vt[0]


def _fit_line_robust(points: np.ndarray):
    """Fit a line with iterative outlier rejection (median distance).

    Return ``None`` if too few points remain.
    """
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
    """Subpixel edge points (distorted image points) along the segment `start`-`end`.

    Cross profiles perpendicular to the edge (bilinear via `cv2.remap`). The edge
    position follows from the area under the normalized profile (inside black ->
    outside white, levels from the plateaus). With an area-sampling camera this
    is independent of the phase of the edge relative to the pixel grid, unlike a
    gradient maximum (which shows a staircase pattern for nearly axis-aligned
    edges). `outward` is the outward normal.
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
    # The ramp must have reached white (otherwise area is missing at the profile end).
    usable = (contrast > _MIN_CONTRAST) & (samples[:, -1] >= black + 0.95 * contrast)
    with np.errstate(divide="ignore", invalid="ignore"):
        fraction = (samples - black[:, None]) / contrast[:, None]
    fraction = np.clip(fraction, -0.1, 1.1)
    # Inside (black) 1 - f = 1, outside 0: integral(1 - f) = e - start.
    outside = 1.0 - fraction
    area = (outside.sum(axis=1) - 0.5 * (outside[:, 0] + outside[:, -1])) * _PROFILE_SAMPLE_PX
    edge = offsets[0] + area
    usable &= np.isfinite(edge) & (np.abs(edge) < 1.5)
    return positions[usable] + edge[usable, None] * outward


def _refine_quad_once(gray: np.ndarray, raw: np.ndarray, calibration, max_shift_px: float) -> np.ndarray | None:
    """Refine the marker corners from the four outer edges.

    Return the undistorted ``(4, 2)`` corners, or ``None`` on failure.

    For each edge, collect edge points in the original image and undistort them
    (`tagloc.lens`, pinhole and fisheye alike). Fit a line by robust total least
    squares. Corners are the intersections of adjacent lines. If a corner
    deviates from the start corner by more than `max_shift_px`, the refinement
    counts as failed.
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
        (c1, d1), (c2, d2) = lines[i - 1], lines[i]  # Corner i = intersection of edges i-1 and i
        matrix = np.column_stack([d1, -d2])
        if abs(np.linalg.det(matrix)) < 0.3:
            return None
        t = np.linalg.solve(matrix, c2 - c1)[0]
        corners[i] = c1 + t * d1
    if np.max(np.linalg.norm(corners - undistort_points(raw, calibration), axis=1)) > max_shift_px:
        return None
    return corners


def _refine_quad(gray: np.ndarray, raw: np.ndarray, calibration) -> np.ndarray | None:
    """Two-stage refinement: first around the ArUco corners, then around the refined position."""
    first = _refine_quad_once(gray, raw, calibration, _MAX_FIRST_SHIFT_PX)
    if first is None:
        return None
    second_raw = _distort(first, calibration)
    second = _refine_quad_once(gray, second_raw, calibration, _MAX_CORNER_SHIFT_PX)
    return first if second is None else second


def _distort(undistorted_px: np.ndarray, calibration) -> np.ndarray:
    """Map undistorted image points back into the distorted original image (pinhole and fisheye)."""
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


#: Tag mode: default edge length (m), minimum area relative to the cube marker,
#: maximum share of the image area, allowed side ratio and minimum
#: rectangularity (contour area / area of the convex hull).
TAG_DEFAULT_SIZE_M = 0.08
_TAG_MIN_AREA_FACTOR = 6.0
_TAG_MAX_IMAGE_FRACTION = 0.6
_TAG_MAX_SIDE_RATIO = 1.5
_TAG_MIN_FILL = 0.9


def _find_tag_quad(gray: np.ndarray, marker_raw: np.ndarray) -> np.ndarray | None:
    """Find the outer quad of the dark tag square around the cube marker.

    Return the raw ``(4, 2)`` corners, or ``None``. Otsu threshold (dark =
    foreground), outer contours only (the inner pattern and the cube lie inside
    and do not interfere), convex quads that contain the marker center. Among
    the plausible candidates (area, side ratio), the smallest one wins.
    """
    import cv2

    blurred = cv2.GaussianBlur(np.asarray(gray, dtype=np.uint8), (0, 0), 1.5)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    marker_area = float(cv2.contourArea(marker_raw.astype(np.float32)))
    image_area = float(gray.shape[0] * gray.shape[1])
    centre = (float(marker_raw[:, 0].mean()), float(marker_raw[:, 1].mean()))
    best, best_area = None, None
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < _TAG_MIN_AREA_FACTOR * marker_area or area > _TAG_MAX_IMAGE_FRACTION * image_area:
            continue
        hull = cv2.convexHull(contour)
        hull_area = float(cv2.contourArea(hull))
        if hull_area <= 0 or area / hull_area < _TAG_MIN_FILL:
            continue
        perimeter = cv2.arcLength(hull, True)
        quad = None
        for factor in (0.01, 0.02, 0.03, 0.05):
            approx = cv2.approxPolyDP(hull, factor * perimeter, True)
            if len(approx) == 4:
                quad = approx.reshape(4, 2).astype(np.float64)
                break
        if quad is None:
            continue
        quad32 = quad.astype(np.float32).reshape(-1, 1, 2)
        if not cv2.isContourConvex(quad32) or cv2.pointPolygonTest(quad32, centre, False) < 0:
            continue
        sides = np.linalg.norm(quad - np.roll(quad, -1, axis=0), axis=1)
        if sides.min() <= 0 or sides.max() / sides.min() > _TAG_MAX_SIDE_RATIO:
            continue
        quad_area = float(cv2.contourArea(quad32))
        if best_area is None or quad_area < best_area:
            best, best_area = quad, quad_area
    return best


def _tag_pose(gray, marker_raw, calibration, camera_matrix, reference, refine_edges):
    """Tag-to-camera pose from the outer edge of the tag square.

    Tag frame: origin at the tag center, z up (toward the camera), x along the
    edge that best matches `reference["tagXInImage"]` (unit vector in the image,
    x right, y down; if absent, image +x). y is chosen so that the frame is
    right-handed. Looking from above, image "up" therefore corresponds to
    x = image right. Corner order: (-s/2, +s/2), (+s/2, +s/2), (+s/2, -s/2),
    (-s/2, -s/2), as in `target.marker_corners`.

    Return ``(rvec, tvec, rms, extra)``, or a failed `TrialMeasurement`.
    """
    import cv2

    from tagloc.lens import undistort_points

    try:
        size_mm = float(reference.get("tagSizeM", TAG_DEFAULT_SIZE_M)) * 1000.0
    except (TypeError, ValueError):
        size_mm = TAG_DEFAULT_SIZE_M * 1000.0
    tag_id = reference.get("tagId")
    tag_id = int(tag_id) if isinstance(tag_id, (int, float)) and not isinstance(tag_id, bool) else None
    quad = _find_tag_quad(gray, marker_raw)
    if quad is None:
        return _fail("Tag-Rand nicht gefunden (kein Viereck um den Wuerfelmarker)", reference="tag")
    # Winding order in the image (y down): top-left -> top-right -> bottom-right gives positive area.
    x, y = quad[:, 0], quad[:, 1]
    if 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) < 0:
        quad = quad[::-1]
    try:
        axis = np.asarray(reference.get("tagXInImage"), dtype=np.float64).reshape(2)
        axis = axis / np.linalg.norm(axis)
        if not np.all(np.isfinite(axis)):
            raise ValueError
    except (TypeError, ValueError):
        axis = np.array([1.0, 0.0])
    edges = np.roll(quad, -1, axis=0) - quad
    edges = edges / np.linalg.norm(edges, axis=1)[:, None]
    start = int(np.argmax(edges @ axis))
    raw = np.roll(quad, -start, axis=0)  # Edge 0 -> 1 is the tag +x edge

    refined = _refine_quad(gray.astype(np.float32), raw, calibration) if refine_edges else None
    pixels = refined if refined is not None else undistort_points(raw, calibration)
    half = size_mm / 2.0
    object_points = np.array(
        [[-half, half, 0.0], [half, half, 0.0], [half, -half, 0.0], [-half, -half, 0.0]]
    )
    ok, rvec, tvec = cv2.solvePnP(object_points, pixels, camera_matrix, None, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        return _fail("Tag-Pose nicht berechenbar (solvePnP)", reference="tag")
    rvec, tvec = cv2.solvePnPRefineLM(object_points, pixels, camera_matrix, None, rvec, tvec)
    projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, None)
    rms = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - pixels) ** 2, axis=1))))
    extra = {
        "tag_size_mm": size_mm,
        "tag_id": tag_id,
        "tag_edges_refined": refined is not None,
        "tag_corners_px": raw.tolist(),
    }
    return rvec, tvec, rms, extra


def measure_trial(
    image,
    calibration,
    cube_marker_offset_mm: Sequence[float] = (0.0, 0.0),
    *,
    refine_edges: bool = True,
    reference: dict | None = None,
) -> TrialMeasurement:
    """Measure the cube placement in `image`.

    Args:
        image: Input image (gray, BGR or BGRA).
        calibration: `tagloc.calibration.CameraCalibration` of the capture.
        cube_marker_offset_mm: Position of the marker center relative to the cube
            center, in the marker frame (x = direction from the top-left to the
            top-right corner, y = toward "up" in the marker image). It is rotated
            into the sheet axes and subtracted.
        refine_edges: Also determine the marker corners from the outer edges
            (more accurate than the ArUco corners). On failure the ArUco corners
            are used; this is recorded in `cube_edge_refined` and
            `sheet_edges_refined`.
        reference: ``None`` means the target (sheet markers). A dict like
            ``{"kind": "tag", "tagSizeM": 0.08, "tagId": 3, "tagXInImage": [ux, uy] | None}``
            means the reference is the black outer edge of a tag square around
            the cube (see `_tag_pose`). The tag is not decoded. The origin is the
            tag center.

    Never raises. Errors are reported in `ok` and `grund`.
    """
    try:
        return _measure(image, calibration, cube_marker_offset_mm, refine_edges, reference)
    except Exception as error:  # Measurement errors must never reach the caller
        return _fail(f"Messung fehlgeschlagen: {type(error).__name__}: {error}")


def _measure(image, calibration, offset, refine_edges=True, reference=None) -> TrialMeasurement:
    """Implement `measure_trial`. May raise; the wrapper catches the errors."""
    import cv2

    from tagloc.lens import project_points, undistort_points

    offset_vec = np.asarray(offset, dtype=np.float64).reshape(2)
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
        """Return ``(undistorted corners (4, 2), refined?)`` of a detected marker."""
        if blurred is not None:
            refined = _refine_quad(blurred, raw, calibration)
            if refined is not None:
                return refined, True
        return undistort_points(raw, calibration), False

    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    tag_mode = isinstance(reference, dict) and reference.get("kind") == "tag"
    extra: dict[str, Any] = {}
    if tag_mode:
        sheet_ids = []
        sheet_refined = 0
        if target.CUBE_MARKER_ID not in found:
            return _fail("Kein Wuerfelmarker (ID 10) erkannt", reference="tag")
        pose = _tag_pose(
            gray, found[target.CUBE_MARKER_ID], calibration, camera_matrix, reference, refine_edges
        )
        if isinstance(pose, TrialMeasurement):
            return pose
        rvec, tvec, rms, extra = pose
    else:
        sheet_ids = sorted(i for i in found if i in target.SHEET_MARKERS)
        if target.CUBE_MARKER_ID not in found:
            return _fail("Kein Wuerfelmarker (ID 10) erkannt", sheet_markers=sheet_ids)
        if len(sheet_ids) < MIN_SHEET_MARKERS:
            return _fail(
                f"Nur {len(sheet_ids)} Blattmarker erkannt, mindestens {MIN_SHEET_MARKERS} noetig",
                sheet_markers=sheet_ids,
            )

        object_points = np.vstack([target.sheet_marker_corners(i) for i in sheet_ids])
        sheet_results = [undistorted_corners(found[i]) for i in sheet_ids]
        sheet_px = np.vstack([corners for corners, _ in sheet_results])
        sheet_refined = sum(1 for _, refined in sheet_results if refined)

        ok, rvec, tvec = cv2.solvePnP(
            object_points, sheet_px, camera_matrix, None, flags=cv2.SOLVEPNP_IPPE
        )
        if not ok:
            return _fail("Blattpose nicht berechenbar (solvePnP)", sheet_markers=sheet_ids)
        rvec, tvec = cv2.solvePnPRefineLM(object_points, sheet_px, camera_matrix, None, rvec, tvec)
        projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, None)
        rms = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - sheet_px) ** 2, axis=1))))

    rotation, _ = cv2.Rodrigues(rvec)  # sheet -> camera
    camera_center = -rotation.T @ tvec.reshape(3)  # camera in the sheet frame
    if camera_center[2] <= target.CUBE_TOP_Z_MM:
        return _fail(
            "Tag-Pose unplausibel (Kamera nicht ueber dem Tag)"
            if tag_mode
            else "Blattpose unplausibel (Kamera nicht ueber dem Blatt)",
            sheet_markers=sheet_ids,
            reprojection_rms_px=rms,
            reference="tag" if tag_mode else "sheet",
        )
    axis_sheet = rotation.T @ np.array([0.0, 0.0, 1.0])  # optical axis in the sheet frame
    tilt = math.degrees(math.acos(float(np.clip(-axis_sheet[2], -1.0, 1.0))))

    def to_sheet_plane(pixels: np.ndarray, z_mm: float) -> np.ndarray:
        """Map undistorted image points onto the plane z = `z_mm` in the sheet frame."""
        normalized = np.column_stack(
            [
                (pixels[:, 0] - camera_matrix[0, 2]) / camera_matrix[0, 0],
                (pixels[:, 1] - camera_matrix[1, 2]) / camera_matrix[1, 1],
                np.ones(len(pixels)),
            ]
        )
        directions = normalized @ rotation  # == (R^T d) per row
        scale = (z_mm - camera_center[2]) / directions[:, 2]
        return camera_center + directions * scale[:, None]

    cube_raw = found[target.CUBE_MARKER_ID]
    cube_px, cube_refined = undistorted_corners(cube_raw)
    cube_pts = to_sheet_plane(cube_px, target.CUBE_TOP_Z_MM)
    xy = cube_pts[:, :2]

    # Edge directions: top-left -> top-right and bottom-left -> bottom-right lie at
    # theta, the cross edges at theta - 90 (hence + 90).
    edges = [
        xy[1] - xy[0],
        xy[2] - xy[3],
        _rot90(xy[3] - xy[0]),
        _rot90(xy[2] - xy[1]),
    ]
    yaw_full = _circular_mean_deg(math.degrees(math.atan2(e[1], e[0])) for e in edges)
    dtheta = _wrap90(yaw_full)

    marker_center = xy.mean(axis=0)
    center = marker_center - target.rotation_2d(yaw_full) @ offset_vec
    corners = target.cube_corners_xy(center, yaw_full)
    max_radius = float(np.max(np.hypot(corners[:, 0], corners[:, 1])))

    # Point where the optical axis pierces the cube top, used as a quality measure.
    if axis_sheet[2] < -1e-6:
        hit = camera_center + axis_sheet * ((target.CUBE_TOP_Z_MM - camera_center[2]) / axis_sheet[2])
        nadir_offset = float(np.hypot(hit[0] - center[0], hit[1] - center[1]))
    else:
        nadir_offset = None

    overlay = _build_overlay(
        calibration, rvec, tvec, sheet_ids, found, cube_raw, corners, project_points,
        tag_size_mm=extra.get("tag_size_mm"),
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
        reference="tag" if tag_mode else "sheet",
        tag_size_mm=extra.get("tag_size_mm"),
        tag_id=extra.get("tag_id"),
        tag_edges_refined=extra.get("tag_edges_refined"),
        tag_corners_px=extra.get("tag_corners_px"),
        overlay=overlay,
    )


def _rot90(vector: np.ndarray) -> np.ndarray:
    """Rotate a vector by +90 degrees (sheet frame, y up)."""
    return np.array([-vector[1], vector[0]])


def _build_overlay(
    calibration, rvec, tvec, sheet_ids, found, cube_raw, cube_corners, project_points, tag_size_mm=None
):
    """Image points for the control image: sheet markers or tag square, cube marker, cube outline, circles."""
    outline = np.column_stack([cube_corners, np.full(4, target.CUBE_TOP_Z_MM)])
    angles = np.radians(np.arange(0, 360, 5.0))
    circles = {}
    for zone, radius in target.ZONE_RADII_MM:
        ring = np.column_stack(
            [radius * np.cos(angles), radius * np.sin(angles), np.zeros(len(angles))]
        )
        circles[zone] = project_points(ring, rvec, tvec, calibration)
    tag_quad = None
    if tag_size_mm is not None:
        half = tag_size_mm / 2.0
        quad = np.array([[-half, half, 0.0], [half, half, 0.0], [half, -half, 0.0], [-half, -half, 0.0]])
        tag_quad = project_points(quad, rvec, tvec, calibration)
    return {
        "sheet": {i: found[i] for i in sheet_ids},
        "tag": tag_quad,
        "cube": cube_raw,
        "outline": project_points(outline, rvec, tvec, calibration),
        "circles": circles,
    }


def annotate(image, measurement: TrialMeasurement):
    """Control image: a copy of `image` with detected markers, circles and measured values."""
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
        if overlay.get("tag") is not None:
            cv2.polylines(canvas, [np.round(overlay["tag"]).astype(np.int32)], True, (255, 160, 0), 1)
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
    """Sample standard deviation. Return 0 for a single value."""
    return float(np.std(values, ddof=1)) if len(values) > 1 else 0.0


def summarize(measurements: Iterable[TrialMeasurement]) -> dict:
    """Key figures based on ISO 9283, over all successful measurements.

    - Accuracy `AP_mm` = distance of the mean of all placements from the target
      (target center); `AP_theta_deg` = |mean dtheta|.
    - Repeatability `RP_mm` = l_mean + 3 * S_l, where l = distance of each single
      placement from the mean; `RP_theta_deg` = 3 * S_theta.
    The mean of dtheta is circular modulo 90 degrees.
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
    """Sticker offset (mm, marker frame) from measurements before and after a 180-degree turn.

    The cube is rotated about its center, so the marker moves to the opposite
    side. Center = (M0 + M1) / 2, offset = (M0 - M1) / 2, rotated back into the
    marker frame of the first position. The measurements may have been taken
    with or without a previous offset (the marker center is used). Raises
    `ValueError` if a measurement is missing or the turn is not about 180
    degrees (more than 30 degrees off).
    """
    for m in (m0, m180):
        if not m.ok or m.marker_center_mm is None or m.marker_yaw_deg is None:
            raise ValueError("Beide Messungen muessen erfolgreich sein (Markerlage fehlt)")
    turn = (m180.marker_yaw_deg - m0.marker_yaw_deg + 180.0) % 360.0 - 180.0  # expected: +-180
    if abs(abs(turn) - 180.0) > 30.0:
        raise ValueError(f"Wuerfel wurde um {turn:.0f} statt um 180 Grad gedreht")
    yaw0 = _circular_mean_deg([m0.marker_yaw_deg, m180.marker_yaw_deg - 180.0])
    half_diff = (np.asarray(m0.marker_center_mm) - np.asarray(m180.marker_center_mm)) / 2.0
    offset = target.rotation_2d(-yaw0) @ half_diff
    return float(offset[0]), float(offset[1])
