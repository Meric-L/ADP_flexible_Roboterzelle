import cv2
import numpy as np
from pathlib import Path

USE_CHARUCO = False

CAMERA_SOURCE = 0

CHESSBOARD_SIZE = (9, 6)
CHARUCO_COLS = 7
CHARUCO_ROWS = 5
SQUARE_SIZE_M = 0.030
MARKER_SIZE_M = 0.022
ARUCO_DICT_ID = cv2.aruco.DICT_4X4_50
MIN_SAMPLES = 15
TARGET_SAMPLES = 20
OUTPUT_PATH = Path(__file__).parent / "calibration.yaml"


def create_charuco_board():
    """Erzeugt ein ChArUco-Board und den zugehörigen Detektor.

    Nutzt die Board-Parameter auf Modulebene (Spalten, Zeilen, Feld- und
    Markergröße, Marker-Dictionary).

    Returns
    -------
    board : cv2.aruco.CharucoBoard
        Das konfigurierte ChArUco-Board.
    detector : cv2.aruco.CharucoDetector
        Detektor, der auf dieses Board abgestimmt ist.
    """
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    board = cv2.aruco.CharucoBoard((CHARUCO_COLS, CHARUCO_ROWS), SQUARE_SIZE_M, MARKER_SIZE_M, aruco_dict)
    return board, cv2.aruco.CharucoDetector(board)


def detect_chessboard(gray):
    """Erkennt Schachbrett-Ecken und verfeinert sie subpixelgenau.

    Parameters
    ----------
    gray : numpy.ndarray
        Graustufenbild des aktuellen Kameraframes.

    Returns
    -------
    numpy.ndarray or None
        Ecken als Array der Form (N, 1, 2), oder None wenn kein
        vollständiges Schachbrett gefunden wurde.
    """
    found, corners = cv2.findChessboardCorners(gray, CHESSBOARD_SIZE)
    if not found:
        return None
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    return cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)


def detect_charuco(gray, detector):
    """Erkennt ChArUco-Ecken im Kamerabild.

    Parameters
    ----------
    gray : numpy.ndarray
        Graustufenbild des aktuellen Kameraframes.
    detector : cv2.aruco.CharucoDetector
        Auf das Board abgestimmter Detektor.

    Returns
    -------
    corners : numpy.ndarray or None
        Erkannte ChArUco-Ecken, oder None bei weniger als 4 Ecken.
    ids : numpy.ndarray or None
        IDs der erkannten Ecken, oder None bei weniger als 4 Ecken.
    """
    corners, ids, _, _ = detector.detectBoard(gray)
    if ids is None or len(ids) < 4:
        return None, None
    return corners, ids


def compute_coverage(all_corners, image_size):
    """Berechnet die räumliche Abdeckung der erfassten Samples im Bild.

    Misst, welchen Anteil der Bildbreite und -höhe die bisher erfassten
    Ecken insgesamt überspannen — ein Maß dafür, wie gut das Bildfeld
    durch die Kalibrierungssamples abgedeckt ist.

    Parameters
    ----------
    all_corners : list of numpy.ndarray
        Liste aller bisher erfassten Ecken-Arrays.
    image_size : tuple of int
        Bildgröße als (Breite, Höhe) in Pixeln.

    Returns
    -------
    x_cov : float
        Horizontale Abdeckung im Bereich [0, 1].
    y_cov : float
        Vertikale Abdeckung im Bereich [0, 1].
    """
    if not all_corners:
        return 0.0, 0.0
    pts = np.vstack([c.reshape(-1, 2) for c in all_corners])
    x_cov = (pts[:, 0].max() - pts[:, 0].min()) / image_size[0]
    y_cov = (pts[:, 1].max() - pts[:, 1].min()) / image_size[1]
    return float(np.clip(x_cov, 0, 1)), float(np.clip(y_cov, 0, 1))


def draw_progress_bar(frame, label, value, y):
    """Zeichnet einen beschrifteten, gefüllten Fortschrittsbalken ins Bild.

    Parameters
    ----------
    frame : numpy.ndarray
        Bild, in das gezeichnet wird (wird in-place verändert).
    label : str
        Beschriftung links neben dem Balken.
    value : float
        Füllgrad im Bereich [0, 1].
    y : int
        Vertikale Pixelposition der oberen Balkenkante.

    Returns
    -------
    None
    """
    cv2.putText(frame, f"{label}:", (10, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    cv2.rectangle(frame, (130, y), (330, y + 14), (60, 60, 60), -1)
    cv2.rectangle(frame, (130, y), (130 + int(200 * value), y + 14), (0, 200, 0), -1)
    cv2.putText(frame, f"{int(value * 100)}%", (335, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)


def draw_overlay(frame, detected, sample_count, x_cov, y_cov):
    """Zeichnet das vollständige Status-Overlay auf den Kameraframe.

    Umfasst Abdeckungsbalken, Sample-Zähler, Tastenhinweise sowie einen
    farbigen Rahmen (grün bei erkanntem Muster, rot sonst).

    Parameters
    ----------
    frame : numpy.ndarray
        Kameraframe, in den gezeichnet wird (wird in-place verändert).
    detected : bool
        True, wenn im aktuellen Frame ein Muster erkannt wurde.
    sample_count : int
        Anzahl der bisher erfassten Samples.
    x_cov : float
        Horizontale Abdeckung im Bereich [0, 1].
    y_cov : float
        Vertikale Abdeckung im Bereich [0, 1].

    Returns
    -------
    None
    """
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 74), (20, 20, 20), -1)
    draw_progress_bar(frame, "X-Coverage", x_cov, 8)
    draw_progress_bar(frame, "Y-Coverage", y_cov, 28)
    cv2.putText(frame, f"Samples: {sample_count}/{TARGET_SAMPLES}   SPACE=capture  C=calibrate  S=save  Q=quit",
                (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), (0, 200, 0) if detected else (0, 0, 200), 4)


def calibrate(all_corners, all_ids, image_size, board):
    """Berechnet Kameramatrix und Verzerrungskoeffizienten aus den Samples.

    Wählt je nach USE_CHARUCO den ChArUco- oder Schachbrett-Pfad; beide
    liefern dasselbe Ergebnisformat.

    Parameters
    ----------
    all_corners : list of numpy.ndarray
        Erfasste Ecken je Sample.
    all_ids : list of numpy.ndarray
        Zugehörige Ecken-IDs je Sample (nur im ChArUco-Pfad genutzt).
    image_size : tuple of int
        Bildgröße als (Breite, Höhe) in Pixeln.
    board : cv2.aruco.CharucoBoard or None
        Board-Objekt für den ChArUco-Pfad, sonst None.

    Returns
    -------
    K : numpy.ndarray
        Kameramatrix der Form (3, 3).
    D : numpy.ndarray
        Verzerrungskoeffizienten.
    rms : float
        Reprojektionsfehler (RMS) der Kalibrierung.
    """
    if USE_CHARUCO:
        all_obj_pts, all_img_pts = [], []
        for corners, ids in zip(all_corners, all_ids):
            obj_pts, img_pts = board.matchImagePoints(corners, ids)
            if obj_pts is not None and len(obj_pts) > 3:
                all_obj_pts.append(obj_pts)
                all_img_pts.append(img_pts)
        rms, K, D, _, _ = cv2.calibrateCamera(all_obj_pts, all_img_pts, image_size, None, None)
    else:
        obj_p = np.zeros((CHESSBOARD_SIZE[0] * CHESSBOARD_SIZE[1], 3), np.float32)
        obj_p[:, :2] = np.mgrid[0:CHESSBOARD_SIZE[0], 0:CHESSBOARD_SIZE[1]].T.reshape(-1, 2) * SQUARE_SIZE_M
        rms, K, D, _, _ = cv2.calibrateCamera([obj_p] * len(all_corners), all_corners, image_size, None, None)
    return K, D, rms


def save_calibration(K, D, image_size):
    """Speichert das Kalibrierergebnis als OpenCV-YAML unter OUTPUT_PATH.

    Parameters
    ----------
    K : numpy.ndarray
        Kameramatrix der Form (3, 3).
    D : numpy.ndarray
        Verzerrungskoeffizienten.
    image_size : tuple of int
        Bildgröße als (Breite, Höhe) in Pixeln.

    Returns
    -------
    None
    """
    fs = cv2.FileStorage(str(OUTPUT_PATH), cv2.FILE_STORAGE_WRITE)
    fs.write("image_width", image_size[0])
    fs.write("image_height", image_size[1])
    fs.write("camera_matrix", K)
    fs.write("distortion_coefficients", D)
    fs.release()
    print(f"Saved: {OUTPUT_PATH}")


def main():
    """Öffnet die Kamera und führt die interaktive Kalibrierschleife aus.

    Zeigt den Live-Feed mit Overlay und verarbeitet die Tasteneingaben
    zum Erfassen von Samples, Berechnen, Speichern und Beenden.

    Returns
    -------
    None
    """
    board, detector = create_charuco_board() if USE_CHARUCO else (None, None)
    cap = cv2.VideoCapture(CAMERA_SOURCE)
    if not cap.isOpened():
        raise RuntimeError(f"Kamera konnte nicht geöffnet werden (CAMERA_SOURCE={CAMERA_SOURCE})")
    all_corners, all_ids = [], []
    K, D, image_size = None, None, None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        image_size = (gray.shape[1], gray.shape[0])

        if USE_CHARUCO:
            corners, ids = detect_charuco(gray, detector)
            if corners is not None:
                cv2.aruco.drawDetectedCornersCharuco(frame, corners, ids)
        else:
            corners = detect_chessboard(gray)
            ids = None
            if corners is not None:
                cv2.drawChessboardCorners(frame, CHESSBOARD_SIZE, corners, True)

        x_cov, y_cov = compute_coverage(all_corners, image_size)
        draw_overlay(frame, corners is not None, len(all_corners), x_cov, y_cov)
        cv2.imshow("Camera Calibration", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord(' ') and corners is not None:
            all_corners.append(corners)
            if USE_CHARUCO:
                all_ids.append(ids)
            print(f"Sample {len(all_corners)} captured")
        elif key == ord('c') and len(all_corners) >= MIN_SAMPLES:
            K, D, rms = calibrate(all_corners, all_ids, image_size, board)
            print(f"Calibration RMS: {rms:.4f}")
        elif key == ord('s') and K is not None:
            save_calibration(K, D, image_size)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
