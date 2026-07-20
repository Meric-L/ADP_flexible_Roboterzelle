import cv2
import numpy as np
import pupil_apriltags
from pathlib import Path

CALIBRATION_PATH = Path(__file__).parent / "calibration.yaml"
CAMERA_SOURCE = 0
TAG_FAMILY = "tag36h11"
TAG_SIZE_M = 0.05


def load_calibration(path):
    """Lädt Kameramatrix und Verzerrungskoeffizienten aus einer YAML-Datei.

    Parameters
    ----------
    path : pathlib.Path or str
        Pfad zur Kalibrierdatei (OpenCV-YAML-Format).

    Returns
    -------
    K : numpy.ndarray
        Kameramatrix der Form (3, 3).
    D : numpy.ndarray
        Verzerrungskoeffizienten.
    """
    fs = cv2.FileStorage(str(path), cv2.FILE_STORAGE_READ)
    K = fs.getNode("camera_matrix").mat()
    D = fs.getNode("distortion_coefficients").mat()
    fs.release()
    return K, D


def draw_tag(frame, tag, K, D):
    """Zeichnet einen erkannten AprilTag mit Pose-Informationen ins Bild.

    Umfasst die Umrandung, die Koordinatenachsen, die Tag-ID sowie die
    geschätzte Distanz zur Kamera.

    Parameters
    ----------
    frame : numpy.ndarray
        Kameraframe, in den gezeichnet wird (wird in-place verändert).
    tag : pupil_apriltags.Detection
        Ein erkannter Tag inkl. Ecken und geschätzter Pose.
    K : numpy.ndarray
        Kameramatrix der Form (3, 3).
    D : numpy.ndarray
        Verzerrungskoeffizienten.

    Returns
    -------
    None
    """
    corners = tag.corners.astype(int)
    cv2.polylines(frame, [corners.reshape(-1, 1, 2)], True, (255, 255, 255), 2)

    if tag.pose_R is not None and tag.pose_t is not None:
        rvec, _ = cv2.Rodrigues(tag.pose_R)
        tvec = tag.pose_t
        cv2.drawFrameAxes(frame, K, D, rvec, tvec, TAG_SIZE_M * 0.5)
        distance = float(np.linalg.norm(tvec))
        cx, cy = corners.mean(axis=0).astype(int)
        cv2.putText(frame, f"ID:{tag.tag_id}  {distance:.2f}m",
                    (cx - 40, cy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)


def main():
    """Öffnet die Kamera und führt die Live-AprilTag-Erkennung aus.

    Lädt die Kalibrierung, detektiert in jedem Frame die Tags mit
    Pose-Schätzung und zeichnet das Overlay bis zum Beenden mit 'Q'.

    Returns
    -------
    None
    """
    K, D = load_calibration(CALIBRATION_PATH)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]

    detector = pupil_apriltags.Detector(families=TAG_FAMILY)
    cap = cv2.VideoCapture(CAMERA_SOURCE)
    if not cap.isOpened():
        raise RuntimeError(f"Kamera konnte nicht geöffnet werden (CAMERA_SOURCE={CAMERA_SOURCE})")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        tags = detector.detect(gray, estimate_tag_pose=True,
                               camera_params=(fx, fy, cx, cy), tag_size=TAG_SIZE_M)

        for tag in tags:
            draw_tag(frame, tag, K, D)

        cv2.imshow("AprilTag Detection", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
