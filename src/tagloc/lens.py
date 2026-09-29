"""Linsenmodell-abhaengige OpenCV-Aufrufe, an einer Stelle.

OpenCV hat fuer das Fisheye-Modell (Kannala-Brandt) einen eigenen Namensraum
`cv2.fisheye` mit anderen Funktionen und anderen Anforderungen an die
Array-Formen als fuer das Standard-Pinhole-Modell (Brown-Conrady). Wer mit
Koeffizienten des einen Modells die Funktion des anderen aufruft, bekommt
keinen Fehler, sondern stumm falsche Punkte. Deshalb entscheidet nur dieses
Modul anhand von `CameraCalibration.model`, was aufgerufen wird -- Pose,
Overlay und Kalibrierung fragen hier an, statt selbst zu verzweigen.

Welche Kamera welches Modell nutzt, legt die Config fest
(`AprilTagProfileConfig.calibration_model`, siehe Arbeitsplan
`kalibrierung-linsenmodell-je-kamera.md`): Deckenkamera Fisheye, Handkamera
Pinhole.
"""

import logging

import numpy as np

from .calibration import DISTORTION_MODELS, FISHEYE, PINHOLE, CameraCalibration

_log = logging.getLogger(__name__)

#: Anzahl Koeffizienten, die `cv2.fisheye` erwartet (k1..k4).
FISHEYE_COEFFICIENTS = 4

#: Abbruchkriterium fuer `cv2.fisheye.calibrate`. Der OpenCV-Standard
#: (20 Iterationen) konvergiert bei 12-MP-Aufnahmen mit vielen Ecken nicht
#: immer; 100 Iterationen kosten gegen die Eckensuche kaum etwas.
_FISHEYE_CRITERIA_MAX_ITER = 100
_FISHEYE_CRITERIA_EPS = 1e-6


def _fisheye_flag(name: str) -> int:
    """Liefert eine Fisheye-Kalibrierflagge, unabhaengig von der OpenCV-Version.

    OpenCV 4.x fuehrt sie als `cv2.fisheye.CALIB_*` (z. B.
    RECOMPUTE_EXTRINSIC = 2), OpenCV 5 nur noch als `cv2.CALIB_*` mit
    **anderen Zahlenwerten** (dort 8388608). Hart kodierte Zahlen waeren
    also auf einer der beiden Versionen still falsch -- auf dem Pi laeuft
    eine andere Version als auf den Entwicklungsrechnern.
    """
    import cv2

    fisheye_value = getattr(cv2.fisheye, name, None)
    return int(fisheye_value if fisheye_value is not None else getattr(cv2, name))


def _camera_matrix(calibration: CameraCalibration) -> np.ndarray:
    return np.asarray(calibration.camera_matrix, dtype=np.float64)


def _fisheye_coefficients(calibration: CameraCalibration) -> np.ndarray:
    coefficients = np.asarray(calibration.distortion, dtype=np.float64).reshape(-1)
    if coefficients.size != FISHEYE_COEFFICIENTS:
        raise ValueError(
            f"Fisheye-Kalibrierung braucht {FISHEYE_COEFFICIENTS} Koeffizienten, "
            f"hat {coefficients.size}"
        )
    return coefficients.reshape(FISHEYE_COEFFICIENTS, 1)


def _check_model(model: str) -> None:
    if model not in DISTORTION_MODELS:
        raise ValueError(
            f"Unbekanntes Linsenmodell '{model}' (erwartet eines von "
            f"{', '.join(DISTORTION_MODELS)})"
        )


def undistort_points(points, calibration: CameraCalibration) -> np.ndarray:
    """Entzerrt Bildpunkte auf das ideale Pinhole-Modell, in Pixeln (`P=K`).

    Danach laesst sich mit Verzeichnung null weiterrechnen -- fuer beide
    Linsenmodelle gleich (siehe `tagloc.pose`). Rueckgabe `(N, 2)`.
    """
    import cv2

    _check_model(calibration.model)
    distorted = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
    camera_matrix = _camera_matrix(calibration)
    if calibration.model == FISHEYE:
        undistorted = cv2.fisheye.undistortPoints(
            distorted, camera_matrix, _fisheye_coefficients(calibration), P=camera_matrix
        )
    else:
        undistorted = cv2.undistortPoints(
            distorted,
            camera_matrix,
            np.asarray(calibration.distortion, dtype=np.float64),
            P=camera_matrix,
        )
    return np.asarray(undistorted).reshape(-1, 2)


def project_points(object_points, rvec, tvec, calibration: CameraCalibration) -> np.ndarray:
    """Projiziert 3D-Punkte (Objekt-KS) mit Verzeichnung ins Bild, `(N, 2)`."""
    import cv2

    _check_model(calibration.model)
    rvec = np.asarray(rvec, dtype=np.float64).reshape(3, 1)
    tvec = np.asarray(tvec, dtype=np.float64).reshape(3, 1)
    camera_matrix = _camera_matrix(calibration)
    if calibration.model == FISHEYE:
        projected, _ = cv2.fisheye.projectPoints(
            np.asarray(object_points, dtype=np.float64).reshape(-1, 1, 3),
            rvec,
            tvec,
            camera_matrix,
            _fisheye_coefficients(calibration),
        )
    else:
        projected, _ = cv2.projectPoints(
            np.asarray(object_points, dtype=np.float64).reshape(-1, 3),
            rvec,
            tvec,
            camera_matrix,
            np.asarray(calibration.distortion, dtype=np.float64),
        )
    return np.asarray(projected).reshape(-1, 2)


def calibrate(
    object_points, image_points, image_size: tuple[int, int], model: str = PINHOLE
) -> tuple[float, np.ndarray, np.ndarray]:
    """Rechnet `(rms, K, D)` aus zugeordneten Punkten je Aufnahme.

    `PINHOLE`: `cv2.calibrateCamera` ohne Zusatzflags -- Brown-Conrady mit
    genau 5 Koeffizienten (k1, k2, p1, p2, k3).

    `FISHEYE`: `cv2.fisheye.calibrate` (Kannala-Brandt, k1..k4) mit
    `RECOMPUTE_EXTRINSIC | CHECK_COND | FIX_SKEW`. Schlaegt `CHECK_COND` an
    (eine Aufnahme numerisch schlecht konditioniert, typisch: Board fast
    am Bildrand oder sehr flach), wird einmal ohne diese Pruefung
    nachgerechnet -- sonst kippt eine einzelne schlechte Aufnahme die ganze
    Kalibrierung. Scheitert auch das, fliegt der `cv2.error` durch.

    Wirft `ValueError` bei unbekanntem Modell.
    """
    import cv2

    _check_model(model)
    size = (int(image_size[0]), int(image_size[1]))
    if model == PINHOLE:
        rms, camera_matrix, distortion, _, _ = cv2.calibrateCamera(
            list(object_points), list(image_points), size, None, None
        )
        return (
            float(rms),
            np.asarray(camera_matrix, dtype=np.float64),
            np.asarray(distortion, dtype=np.float64).reshape(-1),
        )

    # cv2.fisheye ist bei den Formen strenger als calibrateCamera: float64,
    # (1, N, 3) bzw. (1, N, 2) je Aufnahme. (N, 1, 3) scheitert unter
    # OpenCV 5 mit "Sizes of input arguments do not match" (gemessen
    # 2026-09-29); (1, N, *) ist auch die Form aus den OpenCV-4-Beispielen.
    objects = [np.asarray(points, dtype=np.float64).reshape(1, -1, 3) for points in object_points]
    images = [np.asarray(points, dtype=np.float64).reshape(1, -1, 2) for points in image_points]
    criteria = (
        cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS,
        _FISHEYE_CRITERIA_MAX_ITER,
        _FISHEYE_CRITERIA_EPS,
    )
    base_flags = _fisheye_flag("CALIB_RECOMPUTE_EXTRINSIC") | _fisheye_flag("CALIB_FIX_SKEW")

    def run(flags: int):
        return cv2.fisheye.calibrate(
            objects,
            images,
            size,
            np.zeros((3, 3), dtype=np.float64),
            np.zeros((FISHEYE_COEFFICIENTS, 1), dtype=np.float64),
            flags=flags,
            criteria=criteria,
        )

    try:
        rms, camera_matrix, distortion, _, _ = run(base_flags | _fisheye_flag("CALIB_CHECK_COND"))
    except cv2.error as error:
        # Nur die Konditionspruefung selbst rechtfertigt den Neuversuch
        # ("CALIB_CHECK_COND - Ill-conditioned matrix for input array N").
        # Jeder andere Fehler (Formen, zu wenige Punkte) scheiterte ohne die
        # Flagge genauso und soll mit seiner eigenen Meldung durchfliegen.
        if "ill-conditioned" not in str(error).lower():
            raise
        _log.warning(
            "Fisheye-Kalibrierung: Konditionspruefung schlug an (%s) -- "
            "rechne ohne CALIB_CHECK_COND nach",
            str(error).strip().splitlines()[-1],
        )
        rms, camera_matrix, distortion, _, _ = run(base_flags)
    return (
        float(rms),
        np.asarray(camera_matrix, dtype=np.float64),
        np.asarray(distortion, dtype=np.float64).reshape(-1),
    )


__all__ = [
    "FISHEYE_COEFFICIENTS",
    "calibrate",
    "project_points",
    "undistort_points",
]
