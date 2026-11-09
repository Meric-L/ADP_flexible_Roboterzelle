"""ChArUco-Hilfen fuer die kalibrierfreie Auswertung (eigenstaendig, unabhaengig von `tagloc`)."""

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class CharucoSpec:
    """Geometrie des ChArUco-Boards; `cols`/`rows` = Anzahl FELDER."""

    cols: int
    rows: int
    square_size_m: float
    marker_size_m: float
    dictionary: str = "DICT_4X4_50"
    legacy_pattern: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Client-Format `{"type": "charuco", "cols", "rows", "squareSizeM", ...}`."""
        return {
            "type": "charuco",
            "cols": self.cols,
            "rows": self.rows,
            "squareSizeM": self.square_size_m,
            "markerSizeM": self.marker_size_m,
            "dictionary": self.dictionary,
            "legacyPattern": self.legacy_pattern,
        }


def spec_from_dict(d: Any) -> CharucoSpec:
    """`CharucoSpec` aus dem Client-Format; `ValueError` bei Unsinn."""
    if not isinstance(d, dict):
        raise ValueError("Board-Angabe ist kein Objekt")
    if d.get("type", "charuco") != "charuco":
        raise ValueError(f"Board-Typ {d.get('type')!r} nicht unterstuetzt (nur charuco)")
    try:
        cols = int(d["cols"])
        rows = int(d["rows"])
        square = float(d["squareSizeM"])
        marker = float(d["markerSizeM"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Board-Angabe unvollstaendig oder ungueltig: {error}") from error
    if cols < 2 or rows < 2:
        raise ValueError("Board braucht mindestens 2x2 Felder")
    if not (square > 0 and marker > 0 and marker < square):
        raise ValueError("Es muss 0 < markerSizeM < squareSizeM gelten")
    dictionary = str(d.get("dictionary", "DICT_4X4_50"))
    if not dictionary.startswith("DICT_"):
        raise ValueError(f"Unbekanntes Dictionary {dictionary!r}")
    return CharucoSpec(cols, rows, square, marker, dictionary, bool(d.get("legacyPattern", False)))


def build_board(spec: CharucoSpec) -> Any:
    """`cv2.aruco.CharucoBoard` zur Spezifikation."""
    import cv2

    from tagloc.detector import predefined_dictionary

    board = cv2.aruco.CharucoBoard(
        (spec.cols, spec.rows), spec.square_size_m, spec.marker_size_m,
        predefined_dictionary(spec.dictionary),
    )
    board.setLegacyPattern(spec.legacy_pattern)
    return board


def _to_gray(image: np.ndarray) -> np.ndarray:
    import cv2

    if image.ndim == 3:
        code = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
        return cv2.cvtColor(image, code)
    return image


def _detect(gray, spec: CharucoSpec, board: Any = None):
    """`(ids, corners, n_markers)`; leere Arrays, wenn nichts gefunden."""
    import cv2

    board = board if board is not None else build_board(spec)
    detector = cv2.aruco.CharucoDetector(board)
    params = detector.getDetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector.setDetectorParameters(params)
    corners, ids, _mc, marker_ids = detector.detectBoard(_to_gray(gray))
    n_markers = 0 if marker_ids is None else int(len(marker_ids))
    if corners is None or ids is None or len(ids) == 0:
        return np.empty(0, dtype=int), np.empty((0, 2)), n_markers
    ids = np.asarray(ids).reshape(-1).astype(int)
    corners = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    return ids, corners, n_markers


def detect_corners(gray, spec: CharucoSpec, board: Any = None) -> tuple[np.ndarray, np.ndarray]:
    """ChArUco-Ecken: `(ids (N,) int, corners (N, 2) float64)`; leer, wenn nichts gefunden."""
    ids, corners, _ = _detect(gray, spec, board)
    return ids, corners


def object_points_mm(ids: np.ndarray, spec: CharucoSpec) -> np.ndarray:
    """Boardkoordinaten (x, y) in mm zu den Ecken-IDs, `(N, 2)`."""
    all_points = np.asarray(build_board(spec).getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
    ids = np.asarray(ids, dtype=int).reshape(-1)
    return all_points[ids, :2] * 1000.0
