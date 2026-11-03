"""Synthetische Testbilder der Zielscheibe mit Wuerfelmarker bei bekannter Wahrheit.

Rueckwaertsabbildung per `cv2.remap`: jeder Bildpunkt wird entzerrt, als Strahl
mit der Blattebene (und der Wuerfeloberseite z = 20 mm) geschnitten und dort aus
einer Textur gelesen. Damit stimmen auch Bilder mit Linsenverzeichnung exakt zur
Kalibrierung. Gerendert werden nur die vier Blattmarker, die Toleranzkreise und
der Wuerfel, nicht die Winkelskala.
"""

import numpy as np

from . import target

#: Texturaufloesung in Pixel je mm.
TEXTURE_PX_PER_MM = 12.0
#: Weichzeichner (Texel) als Ersatz fuer die Pixelflaeche der Kamera; unterdrueckt Aliasing.
TEXTURE_BLUR_SIGMA = 2.0
#: Ausdehnung der Blattstextur um die Scheibenmitte (mm).
SHEET_HALF_WIDTH_MM = 110.0
SHEET_HALF_HEIGHT_MM = 160.0


def make_calibration(fx: float = 466.0, size=(640, 480), distortion=None):
    """Pinhole-Kalibrierung mit Bildmitte als Hauptpunkt (Standard: RealSense-aehnlich)."""
    from tagloc.calibration import PINHOLE, CameraCalibration

    width, height = size
    matrix = np.array([[fx, 0.0, width / 2.0], [0.0, fx, height / 2.0], [0.0, 0.0, 1.0]])
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.zeros(5) if distortion is None else np.asarray(distortion, dtype=np.float64),
        image_size=(int(width), int(height)),
        model=PINHOLE,
        calibration_id="synthetisch",
    )


def look_down_pose(x_mm=0.0, y_mm=0.0, height_mm=200.0, yaw_deg=0.0, tilt_deg=0.0) -> np.ndarray:
    """Kamerapose im Blattsystem (4x4, `T_sheet_cam`) mit Blick senkrecht nach unten.

    Kamera-x zeigt nach Blatt-x, Kamera-y nach Blatt-(-y) (Bild oben = Blatt oben),
    Kamera-z nach Blatt-(-z). `yaw_deg` dreht um die Blatt-z-Achse, `tilt_deg` kippt
    um die Kamera-x-Achse.
    """
    base = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])
    yaw, tilt = np.radians(yaw_deg), np.radians(tilt_deg)
    rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)], [0, np.sin(tilt), np.cos(tilt)]])
    pose = np.eye(4)
    pose[:3, :3] = rz @ base @ rx
    pose[:3, 3] = [x_mm, y_mm, height_mm]
    return pose


def _marker_texture(marker_id: int, size_mm: float) -> np.ndarray:
    """Marker als Graubild (schwarz/weiss), exakt `size_mm * TEXTURE_PX_PER_MM` gross."""
    import cv2

    pixels = int(round(size_mm * TEXTURE_PX_PER_MM))
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    # Ganzzahlige Zellgroesse (Vielfaches von 6): sonst wandern die Modulkanten.
    image = cv2.aruco.generateImageMarker(dictionary, marker_id, pixels)
    return cv2.GaussianBlur(image, (0, 0), TEXTURE_BLUR_SIGMA)


def _sheet_texture() -> np.ndarray:
    """Weisses Blatt mit Markern (samt Ruhezone), Toleranzkreisen und Fadenkreuz."""
    import cv2

    width = int(2 * SHEET_HALF_WIDTH_MM * TEXTURE_PX_PER_MM)
    height = int(2 * SHEET_HALF_HEIGHT_MM * TEXTURE_PX_PER_MM)
    texture = np.full((height, width), 255, dtype=np.uint8)

    def to_px(x_mm: float, y_mm: float) -> tuple[int, int]:
        return (
            int(round((x_mm + SHEET_HALF_WIDTH_MM) * TEXTURE_PX_PER_MM)),
            int(round((SHEET_HALF_HEIGHT_MM - y_mm) * TEXTURE_PX_PER_MM)),
        )

    for _zone, radius in target.ZONE_RADII_MM:
        cv2.circle(texture, to_px(0, 0), int(radius * TEXTURE_PX_PER_MM), 128, 3)
    cv2.line(texture, to_px(-80, 0), to_px(80, 0), 0, 2)
    cv2.line(texture, to_px(0, -80), to_px(0, 80), 0, 2)
    for marker_id, (cx, cy) in target.SHEET_MARKERS.items():
        image = _marker_texture(marker_id, target.SHEET_MARKER_SIZE_MM)
        x0, y0 = to_px(cx - target.SHEET_MARKER_SIZE_MM / 2, cy + target.SHEET_MARKER_SIZE_MM / 2)
        texture[y0 : y0 + image.shape[0], x0 : x0 + image.shape[1]] = image
    return cv2.GaussianBlur(texture, (0, 0), TEXTURE_BLUR_SIGMA)


def _plane_points(calibration, pose_sheet_cam: np.ndarray, z_mm: float):
    """Blatt-x/-y (mm) der Ebene z = `z_mm` fuer jeden Bildpunkt; `NaN` ohne Schnitt."""
    from tagloc.lens import undistort_points

    width, height = calibration.image_size
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float64), np.arange(height, dtype=np.float64))
    pixels = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    undistorted = undistort_points(pixels, calibration)
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    rays = np.column_stack(
        [
            (undistorted[:, 0] - matrix[0, 2]) / matrix[0, 0],
            (undistorted[:, 1] - matrix[1, 2]) / matrix[1, 1],
            np.ones(len(undistorted)),
        ]
    )
    rotation, center = pose_sheet_cam[:3, :3], pose_sheet_cam[:3, 3]
    directions = rays @ rotation.T
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = (z_mm - center[2]) / directions[:, 2]
    scale[scale <= 0] = np.nan
    points = center + directions * scale[:, None]
    return points[:, 0].reshape(height, width), points[:, 1].reshape(height, width)


def render_trial(
    calibration,
    camera_pose_in_sheet,
    cube_dx_mm: float,
    cube_dy_mm: float,
    cube_dtheta_deg: float,
    sticker_offset_mm=(0.0, 0.0),
    with_cube: bool = True,
    with_sheet_markers=None,
    supersample: int = 1,
):
    """Rendert ein Graubild (uint8) von Blatt und Wuerfel.

    `camera_pose_in_sheet`: 4x4 `T_sheet_cam` (siehe `look_down_pose`).
    Wuerfelmitte (`cube_dx_mm`, `cube_dy_mm`) und Gierwinkel (`cube_dtheta_deg`,
    gegen den Uhrzeigersinn) gelten im Blattsystem. `sticker_offset_mm`: Lage
    der Markermitte relativ zur Wuerfelmitte im Wuerfel-/Markersystem.
    `supersample` > 1: mit dem n-fachen Raster rendern und per `INTER_AREA`
    verkleinern (Intrinsik skaliert, Hauptpunkt `(c + 0.5) * n - 0.5`) -- die
    Pixelflaeche statt Punktabtastung, wie bei einer echten Kamera.
    `with_sheet_markers`: Liste der Blatt-IDs, die gezeichnet werden (Standard: alle);
    `with_cube=False` laesst den Wuerfel weg.
    """
    import cv2

    if supersample > 1:
        from tagloc.calibration import CameraCalibration

        n = int(supersample)
        matrix = np.asarray(calibration.camera_matrix, dtype=np.float64).copy()
        matrix[:2, :2] *= n
        matrix[0, 2] = (calibration.camera_matrix[0][2] + 0.5) * n - 0.5
        matrix[1, 2] = (calibration.camera_matrix[1][2] + 0.5) * n - 0.5
        big = CameraCalibration(
            camera_matrix=matrix,
            distortion=np.asarray(calibration.distortion, dtype=np.float64),
            image_size=(calibration.image_size[0] * n, calibration.image_size[1] * n),
            model=calibration.model,
            calibration_id=calibration.calibration_id,
        )
        image = render_trial(
            big, camera_pose_in_sheet, cube_dx_mm, cube_dy_mm, cube_dtheta_deg,
            sticker_offset_mm, with_cube, with_sheet_markers,
        )
        return cv2.resize(image, tuple(calibration.image_size), interpolation=cv2.INTER_AREA)

    pose = np.asarray(camera_pose_in_sheet, dtype=np.float64)
    texture = _sheet_texture()
    if with_sheet_markers is not None:
        hidden = set(target.SHEET_MARKERS) - set(with_sheet_markers)
        for marker_id in hidden:
            cx, cy = target.SHEET_MARKERS[marker_id]
            half = target.SHEET_MARKER_SIZE_MM / 2 + 1
            x0 = int(round((cx - half + SHEET_HALF_WIDTH_MM) * TEXTURE_PX_PER_MM))
            y0 = int(round((SHEET_HALF_HEIGHT_MM - cy - half) * TEXTURE_PX_PER_MM))
            size = int(round(2 * half * TEXTURE_PX_PER_MM))
            texture[y0 : y0 + size, x0 : x0 + size] = 255

    plane_x, plane_y = _plane_points(calibration, pose, 0.0)
    map_x = ((plane_x + SHEET_HALF_WIDTH_MM) * TEXTURE_PX_PER_MM - 0.5).astype(np.float32)
    map_y = ((SHEET_HALF_HEIGHT_MM - plane_y) * TEXTURE_PX_PER_MM - 0.5).astype(np.float32)
    image = cv2.remap(
        texture, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255
    )
    if not with_cube:
        return image

    # Wuerfeloberseite: 20-mm-Flaeche mit 18-mm-Marker (Mitte um den Versatz verschoben).
    cube_x, cube_y = _plane_points(calibration, pose, target.CUBE_TOP_Z_MM)
    rel = np.stack([cube_x - cube_dx_mm, cube_y - cube_dy_mm], axis=-1)
    local = rel @ target.rotation_2d(cube_dtheta_deg)  # == R(-theta) * rel
    half = target.CUBE_SIZE_MM / 2.0
    on_cube = (np.abs(local[..., 0]) <= half) & (np.abs(local[..., 1]) <= half)
    marker = _marker_texture(target.CUBE_MARKER_ID, target.CUBE_MARKER_SIZE_MM)
    mx = (local[..., 0] - sticker_offset_mm[0] + target.CUBE_MARKER_SIZE_MM / 2) * TEXTURE_PX_PER_MM
    my = (target.CUBE_MARKER_SIZE_MM / 2 - (local[..., 1] - sticker_offset_mm[1])) * TEXTURE_PX_PER_MM
    top = cv2.remap(
        marker,
        (mx - 0.5).astype(np.float32),
        (my - 0.5).astype(np.float32),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    top = np.where(np.isnan(cube_x), 255, top)
    return np.where(on_cube & ~np.isnan(cube_x), top, image).astype(np.uint8)
