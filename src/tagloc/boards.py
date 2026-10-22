"""Calibration boards: detect, cover, compute.

Extracted from `src/apriltag/calibrate_camera.py`, whose calculation core was
trapped in the keyboard loop of `main()` -- there was no way to calibrate
from a set of existing images. That's now possible, and is the precondition
for testing the calibration path without a camera.

Board type and geometry are parameters (`BoardSpec`), not module globals like
`USE_CHARUCO` and `CHESSBOARD_SIZE`.
"""

import logging
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from .calibration import PINHOLE, CameraCalibration

_log = logging.getLogger(__name__)

CHESSBOARD = "chessboard"
CHARUCO = "charuco"


@dataclass(frozen=True)
class BoardSpec:
    """Geometry of the calibration board.

    For `chessboard`, `cols`/`rows` are the **inner** corners (9x6 for a
    10x7-square board); for `charuco`, the number of squares.
    """

    type: str = CHESSBOARD
    cols: int = 9
    rows: int = 6
    square_size_m: float = 0.030
    marker_size_m: float = 0.022
    dictionary: str = "DICT_4X4_50"

    def as_dict(self) -> dict[str, Any]:
        """Return the provenance record stored in the calibration file."""
        data = {
            "type": self.type,
            "cols": self.cols,
            "rows": self.rows,
            "squareSizeM": self.square_size_m,
        }
        if self.type == CHARUCO:
            data["markerSizeM"] = self.marker_size_m
            data["dictionary"] = self.dictionary
        return data


@dataclass(frozen=True)
class BoardSample:
    """An image in which the board was found."""

    corners: np.ndarray
    ids: np.ndarray | None = None

    def count(self) -> int:
        return int(np.asarray(self.corners).reshape(-1, 2).shape[0])


def build_board(spec: BoardSpec) -> Any:
    """Build the ChArUco board object. `None` for chessboard."""
    if spec.type != CHARUCO:
        return None
    import cv2

    from .detector import predefined_dictionary

    aruco = cv2.aruco
    dictionary = predefined_dictionary(spec.dictionary)
    if hasattr(aruco, "CharucoBoard"):
        try:
            return aruco.CharucoBoard(
                (spec.cols, spec.rows), spec.square_size_m, spec.marker_size_m, dictionary
            )
        except TypeError:  # pragma: no cover - older signature
            return aruco.CharucoBoard_create(
                spec.cols, spec.rows, spec.square_size_m, spec.marker_size_m, dictionary
            )
    return aruco.CharucoBoard_create(  # pragma: no cover
        spec.cols, spec.rows, spec.square_size_m, spec.marker_size_m, dictionary
    )


def detect_board(gray, spec: BoardSpec, board: Any = None) -> BoardSample | None:
    """Look for the board in a grayscale image. `None` if not found."""
    import cv2

    if spec.type == CHARUCO:
        board = board if board is not None else build_board(spec)
        if hasattr(cv2.aruco, "CharucoDetector"):
            detector = cv2.aruco.CharucoDetector(board)
            corners, ids, _, _ = detector.detectBoard(gray)
        else:  # pragma: no cover - older OpenCV version
            marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(gray, board.dictionary)
            if marker_ids is None or len(marker_ids) == 0:
                return None
            _, corners, ids = cv2.aruco.interpolateCornersCharuco(
                marker_corners, marker_ids, gray, board
            )
        if corners is None or ids is None or len(corners) < 4:
            return None
        return BoardSample(corners=np.asarray(corners), ids=np.asarray(ids))

    if hasattr(cv2, "findChessboardCornersSB"):
        # Der neuere "Sector-Based"-Detektor: robuster bei schlechtem Licht,
        # Unschaerfe und starker Neigung als die klassische Methode, und
        # liefert die Ecken bereits subpixelgenau -- ein zusaetzliches
        # cornerSubPix() ist hier nicht mehr noetig.
        #
        # Achtung, nicht offensichtlich: SB liefert die Ecken in **exakt
        # umgekehrter Reihenfolge** gegenueber `findChessboardCorners`
        # (empirisch mit einem synthetischen Testbild geprueft, 2026-09-23:
        # nach Umkehren stimmen die Positionen auf < 0.1 px). Ungefiltert
        # uebernommen wuerde das die Bild-zu-Weltpunkt-Zuordnung in
        # `_chessboard_object_points` lautlos vertauschen -- die Kalibrierung
        # liefe zwar durch, aber mit falschem Ergebnis statt eines Fehlers.
        # BEWUSST OHNE CALIB_CB_ACCURACY: der Flag ist laut OpenCV-Doku ein
        # "eigener, langsamerer Algorithmus" -- gemessen bei ~4000 px Breite
        # (Deckenkamera-Format) 4,45 s gegen 0,87 s ohne (Faktor 5) bzw.
        # 0,06 s bei der klassischen Methode (Faktor 73). Die Kalibrier-
        # Aufnahme laeuft immer auf dem VOLLEN Kamera-Frame (12 MP an der
        # Deckenkamera) -- mit dem Flag lief die OPC-UA-Anfrage auf dem Pi in
        # den Timeout ("Failed to send request"), live gefunden 2026-09-23.
        found, corners = cv2.findChessboardCornersSB(
            gray, (spec.cols, spec.rows), flags=cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        if found:
            return BoardSample(corners=np.asarray(corners)[::-1], ids=None)

    # Rueckfall: aeltere OpenCV-Version ohne SB, oder SB hat das Board in
    # diesem Frame nicht gefunden -- die klassische Methode findet manchmal
    # Boards, die SB verpasst (und umgekehrt), ein zweiter Versuch kostet nur
    # bei einem Fehlschlag von SB etwas.
    found, corners = cv2.findChessboardCorners(
        gray,
        (spec.cols, spec.rows),
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE,
    )
    if not found:
        return None
    refined = cv2.cornerSubPix(
        gray,
        corners,
        (11, 11),
        (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3),
    )
    return BoardSample(corners=np.asarray(refined), ids=None)


def partial_patterns(spec: BoardSpec) -> list[tuple[int, int]]:
    """Kleinere Raster, mit denen ein nur teilweise sichtbares Brett noch zu
    finden ist: von knapp unter `spec` bis 3x3, groesstes zuerst."""
    patterns = []
    cols, rows = spec.cols - 1, spec.rows - 1
    while min(cols, rows) >= 3:
        patterns.append((cols, rows))
        cols, rows = cols - 1, rows - 1
    if (3, 3) not in patterns and min(spec.cols, spec.rows) > 3:
        patterns.append((3, 3))
    return patterns


def detect_partial_board(gray, spec: BoardSpec):
    """Ein Teilraster des Bretts im Bild: `(Ecken (N, 2), cols, rows)` oder `None`.

    Fuer das Zentrieren, wenn das ganze Brett nicht im Bild ist: Die Erkennung
    braucht das ganze Raster, aber ein kleineres passt in jeden sichtbaren
    Teil. Der Ursprung eines Teilrasters ist beliebig -- es taugt fuer
    Schwerpunkt und Richtung, nicht fuer eine Brettpose.
    """
    if spec.type != CHESSBOARD:
        return None
    for cols, rows in partial_patterns(spec):
        sample = detect_board(gray, replace(spec, cols=cols, rows=rows))
        if sample is not None:
            return np.asarray(sample.corners, dtype=np.float64).reshape(-1, 2), cols, rows
    return None


def diagnose_board(gray, spec: BoardSpec) -> dict:
    """Warum findet die Erkennung das Brett nicht? Nur fuer die Fehlersuche.

    Probiert das angegebene Raster mit beiden Detektoren, das vertauschte
    (Zeilen/Spalten) und Raster um die Angabe herum mit dem schnellen
    Detektor, groesstes zuerst. `largestGrid` ist das erste gefundene.
    Dazu Helligkeitswerte des Bilds (Belichtung, Ueberstrahlung).
    """
    import cv2

    result = {
        "imageSize": [int(gray.shape[1]), int(gray.shape[0])],
        "grayMin": int(gray.min()),
        "grayMax": int(gray.max()),
        "grayMean": round(float(gray.mean()), 1),
        "saturatedFraction": round(float((gray >= 250).mean()), 4),
        "sbExact": None,
        "classicExact": None,
        "sbSwapped": None,
        "largestGrid": None,
        "tried": [],
    }
    if spec.type != CHESSBOARD or not hasattr(cv2, "findChessboardCornersSB"):
        return result

    def sb(cols, rows) -> bool:
        found, _ = cv2.findChessboardCornersSB(gray, (cols, rows), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
        return bool(found)

    result["sbExact"] = sb(spec.cols, spec.rows)
    found, _ = cv2.findChessboardCorners(
        gray, (spec.cols, spec.rows), flags=cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
    )
    result["classicExact"] = bool(found)
    if spec.cols != spec.rows:
        result["sbSwapped"] = sb(spec.rows, spec.cols)
    candidates = sorted(
        {
            (cols, rows)
            for cols in range(max(3, spec.cols - 2), spec.cols + 2)
            for rows in range(max(3, spec.rows - 2), spec.rows + 2)
        },
        key=lambda size: (-size[0] * size[1], size),
    )
    for cols, rows in candidates:
        result["tried"].append([cols, rows])
        if sb(cols, rows):
            result["largestGrid"] = {"cols": cols, "rows": rows}
            break
    return result


def compute_coverage(samples, image_size: tuple[int, int]) -> tuple[float, float]:
    """Fraction of image width/height covered by all samples combined.

    Carried over from `calibrate_camera.py`. More important than it looks: a
    calibration taken only from the image centre gets a good RMS but useless
    distortion coefficients -- and the error only shows up at the edges,
    where the modules sit.
    """
    points = []
    for sample in samples:
        corners = sample.corners if isinstance(sample, BoardSample) else sample
        points.append(np.asarray(corners, dtype=np.float64).reshape(-1, 2))
    if not points:
        return (0.0, 0.0)
    stacked = np.vstack(points)
    width = max(int(image_size[0]), 1)
    height = max(int(image_size[1]), 1)
    span_x = float(stacked[:, 0].max() - stacked[:, 0].min()) / width
    span_y = float(stacked[:, 1].max() - stacked[:, 1].min()) / height
    return (min(1.0, max(0.0, span_x)), min(1.0, max(0.0, span_y)))


def _chessboard_object_points(spec: BoardSpec) -> np.ndarray:
    grid = np.zeros((spec.cols * spec.rows, 3), dtype=np.float32)
    grid[:, :2] = np.mgrid[0 : spec.cols, 0 : spec.rows].T.reshape(-1, 2)
    return grid * spec.square_size_m


def chessboard_is_unambiguous(spec: BoardSpec) -> bool:
    """Ob sich ein Schachbrett um 180 Grad gedreht vom Original unterscheidet.

    Nur dann laesst sich die Eckenreihenfolge an der Farbe des ersten Felds
    festmachen: um 180 Grad gedreht landet das erste Feld auf dem letzten,
    und das hat genau dann die andere Farbe, wenn `cols + rows` (Innenecken)
    ungerade ist -- z. B. 5x6 Innenecken bei einem Brett mit 6x7 Feldern.
    """
    return spec.type == CHESSBOARD and spec.cols >= 3 and spec.rows >= 2 and (spec.cols + spec.rows) % 2 == 1


def canonical_chessboard_corners(gray, corners, spec: BoardSpec) -> np.ndarray:
    """Bringt die Schachbrett-Ecken in eine feste Reihenfolge, `(N, 2)`.

    OpenCV liefert die Ecken zeilenweise, faengt aber je nach Blickwinkel an
    einer anderen Ecke an. Fuer die Kamera-Kalibrierung ist das egal, fuer
    die Hand-Auge-Kalibrierung nicht: dort muss das Board in **jedem** Bild
    dasselbe Koordinatensystem haben, sonst springt es um 180 Grad oder
    gespiegelt, und `T_flansch_cam` wird falsch statt ungenau.

    Zwei Schritte:
    1. Haendigkeit: +x (entlang der Zeile) und +y (zur naechsten Zeile)
       muessen im Bild im Uhrzeigersinn liegen (Bild-y zeigt nach unten),
       das Board-+z zeigt also von der Kamera weg. Sonst jede Zeile umdrehen.
    2. Farbe: das Feld zwischen den ersten vier Ecken ist schwarz. Sonst die
       ganze Liste umdrehen (180 Grad, Haendigkeit bleibt). Setzt
       `chessboard_is_unambiguous(spec)` voraus.
    """
    points = np.asarray(corners, dtype=np.float64).reshape(spec.rows, spec.cols, 2)
    along_row = points[0, 1] - points[0, 0]
    to_next_row = points[1, 0] - points[0, 0]
    if along_row[0] * to_next_row[1] - along_row[1] * to_next_row[0] < 0:
        points = points[:, ::-1]

    def brightness(cell_row: int, cell_col: int) -> float:
        centre = points[cell_row : cell_row + 2, cell_col : cell_col + 2].reshape(-1, 2).mean(axis=0)
        x, y = int(round(centre[0])), int(round(centre[1]))
        height, width = np.asarray(gray).shape[:2]
        x0, x1 = max(0, x - 2), min(width, x + 3)
        y0, y1 = max(0, y - 2), min(height, y + 3)
        return float(np.mean(np.asarray(gray)[y0:y1, x0:x1]))

    # Das Nachbarfeld hat sicher die andere Farbe: Vergleich statt Schwelle,
    # damit Belichtung und Material (gelasertes Holz) keine Rolle spielen.
    if brightness(0, 0) > brightness(0, 1):
        points = points[::-1, ::-1]
    return points.reshape(-1, 2)


def estimate_board_pose(
    corners, spec: BoardSpec, calibration: CameraCalibration
) -> tuple[np.ndarray, float]:
    """`(T_cam_board, Reprojektionsfehler in px)` aus kanonischen Ecken.

    Board-Frame: Ursprung in der ersten Innenecke, +x entlang der Zeile,
    +y zur naechsten Zeile, +z von der Kamera weg (ins Board). Entzerrt wird
    vorher fuer beide Linsenmodelle gleich (`tagloc.lens`), geloest dann
    ohne Verzeichnung -- wie bei den Tags (`tagloc.pose`).
    """
    import cv2

    from . import lens
    from .geometry import from_rvec_tvec

    object_points = _chessboard_object_points(spec).astype(np.float64)
    image_points = lens.undistort_points(corners, calibration).reshape(-1, 1, 2)
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    no_distortion = np.zeros(5, dtype=np.float64)

    def reprojection_px(rvec, tvec) -> float:
        projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, no_distortion)
        error = projected.reshape(-1, 2) - image_points.reshape(-1, 2)
        return float(np.sqrt(np.mean(np.sum(error**2, axis=1))))

    # IPPE liefert fuer ein ebenes Ziel beide Kandidaten; frontal betrachtet
    # liegen sie nah beieinander und der erste ist oft um ein paar Grad
    # gekippt (am gerenderten Brett: 2,5 Grad, 2 mm). Deshalb den besseren
    # nehmen und mit Levenberg-Marquardt ueber alle Ecken nachschaerfen.
    candidates = []
    if hasattr(cv2, "solvePnPGeneric") and hasattr(cv2, "SOLVEPNP_IPPE"):
        found, rvecs, tvecs, _ = cv2.solvePnPGeneric(
            object_points, image_points, camera_matrix, no_distortion, flags=cv2.SOLVEPNP_IPPE
        )
        if found:
            candidates = list(zip(rvecs, tvecs))
    if not candidates:
        ok, rvec, tvec = cv2.solvePnP(object_points, image_points, camera_matrix, no_distortion)
        if not ok:
            raise ValueError("Keine Board-Pose loesbar")
        candidates = [(rvec, tvec)]
    refined = []
    for rvec, tvec in candidates:
        rvec, tvec = np.array(rvec, dtype=np.float64), np.array(tvec, dtype=np.float64)
        if hasattr(cv2, "solvePnPRefineLM"):
            rvec, tvec = cv2.solvePnPRefineLM(
                object_points, image_points, camera_matrix, no_distortion, rvec, tvec
            )
        refined.append((reprojection_px(rvec, tvec), rvec, tvec))
    error_px, rvec, tvec = min(refined, key=lambda candidate: candidate[0])
    return from_rvec_tvec(rvec, tvec), error_px


def calibrate_from_samples(
    samples,
    image_size: tuple[int, int],
    spec: BoardSpec,
    board: Any = None,
    *,
    frame_id: str = "",
    model: str = PINHOLE,
) -> CameraCalibration:
    """Compute the calibration from collected board samples.

    No image, no GUI, no camera -- just points. The same function runs
    behind both the live UI and the image-folder path.

    `model` picks the lens model (`calibration.PINHOLE` or
    `calibration.FISHEYE`), see `tagloc.lens.calibrate`.
    """
    from . import lens

    samples = list(samples)
    if len(samples) < 3:
        raise ValueError(f"Zu wenige Aufnahmen fuer eine Kalibrierung: {len(samples)}")

    object_points: list[np.ndarray] = []
    image_points: list[np.ndarray] = []
    if spec.type == CHARUCO:
        board = board if board is not None else build_board(spec)
        for sample in samples:
            matched_object, matched_image = board.matchImagePoints(sample.corners, sample.ids)
            if matched_object is None or len(matched_object) < 4:
                continue
            object_points.append(matched_object)
            image_points.append(matched_image)
    else:
        grid = _chessboard_object_points(spec)
        for sample in samples:
            object_points.append(grid)
            image_points.append(np.asarray(sample.corners, dtype=np.float32))

    if len(object_points) < 3:
        raise ValueError("Zu wenige verwertbare Aufnahmen nach der Zuordnung")

    rms, camera_matrix, distortion = lens.calibrate(
        object_points, image_points, image_size, model
    )
    return CameraCalibration(
        camera_matrix=camera_matrix,
        distortion=distortion,
        image_size=(int(image_size[0]), int(image_size[1])),
        frame_id=frame_id,
        rms_reprojection_error=rms,
        sample_count=len(object_points),
        board=spec.as_dict(),
        model=model,
    )


__all__ = [
    "CHARUCO",
    "CHESSBOARD",
    "BoardSample",
    "BoardSpec",
    "build_board",
    "calibrate_from_samples",
    "canonical_chessboard_corners",
    "chessboard_is_unambiguous",
    "estimate_board_pose",
    "compute_coverage",
    "detect_board",
    "detect_partial_board",
    "diagnose_board",
    "partial_patterns",
]
