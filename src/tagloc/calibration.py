"""Kamerakalibrierung als Daten: lesen, schreiben, pruefen. Ohne OpenCV.

Bewusst JSON statt OpenCV-FileStorage-YAML:

* ohne `cv2` lesbar -- der Vision-Server baut die `configurationId`, ohne
  OpenCV zu laden, und die Tests laufen ueberall
* Platz fuer `rms`, Bildgroesse und Brettgeometrie. Der Prototyp
  (`src/apriltag/calibrate_camera.py`, am 2026-10-30 entfernt) gab `rms` nur
  auf stdout aus und schrieb `image_width`/`image_height`, ohne sie je wieder
  zu lesen
* vergleichbar (diff) und im Fehlerfall von Hand lesbar

Die Datei gehoert zu **einer physischen Kamera**, nicht ins Repo. Sie liegt
unter `data/`, per `.gitignore` ausgeschlossen -- jeder Pi erzeugt seine eigene.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .identity import calibration_identity

SCHEMA = "wsc.vision.calibration/2"
#: Vor dem Linsenmodell-Feld (2026-09-29). Wird weiter gelesen und gilt als
#: `PINHOLE` -- das war damals das einzige Modell. Geschrieben wird nur noch
#: `SCHEMA`: ein alter Code-Stand lehnt eine neue Datei damit laut ab, statt
#: Fisheye-Koeffizienten stumm als Pinhole zu deuten.
LEGACY_SCHEMA = "wsc.vision.calibration/1"

#: OpenCV-Standardmodell (Brown-Conrady), 5 Koeffizienten k1, k2, p1, p2, k3.
PINHOLE = "pinhole"
#: OpenCV-Fisheye-Modell (Kannala-Brandt), 4 Koeffizienten k1..k4.
FISHEYE = "fisheye"
DISTORTION_MODELS = (PINHOLE, FISHEYE)

#: Grobe Annahme fuer `default_calibration` -- typischer Wert fuer Pi-Kamera-
#: und RealSense-Farbsensoren, aber nicht das tatsaechliche Sichtfeld einer
#: bestimmten Kamera. Nur ein Ausgangspunkt, keine Messung.
DEFAULT_HORIZONTAL_FOV_DEG = 70.0
PLACEHOLDER_CALIBRATION_ID = "placeholder-unkalibriert"


@dataclass(frozen=True)
class CameraCalibration:
    """Intrinsik und Verzeichnung einer Kamera, mit Herkunft."""

    camera_matrix: np.ndarray
    distortion: np.ndarray
    image_size: tuple[int, int]
    frame_id: str = ""
    calibration_id: str = ""
    rms_reprojection_error: float = float("nan")
    sample_count: int = 0
    board: dict[str, Any] = field(default_factory=dict)
    #: Linsenmodell, zu dem `distortion` gehoert (`PINHOLE` oder `FISHEYE`).
    #: Entscheidet, welche OpenCV-Funktionen entzerren und projizieren --
    #: siehe `tagloc.lens`. Die Koeffizienten allein verraten es nicht
    #: eindeutig, deshalb steht es ausdruecklich in der Datei.
    model: str = PINHOLE

    @property
    def camera_params(self) -> tuple[float, float, float, float]:
        """Liefert `(fx, fy, cx, cy)`, das Format, das Tag-Detektoren erwarten."""
        matrix = np.asarray(self.camera_matrix, dtype=np.float64)
        return (
            float(matrix[0, 0]),
            float(matrix[1, 1]),
            float(matrix[0, 2]),
            float(matrix[1, 2]),
        )


def save_calibration(path: Path, calibration: CameraCalibration) -> None:
    """Schreibt die Kalibrierung als JSON und legt fehlende Ordner an."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "calibrationId": calibration.calibration_id
        or f"{calibration.frame_id or path.stem}@{datetime.now(timezone.utc).isoformat()}",
        "frameId": calibration.frame_id,
        "distortionModel": _checked_model(calibration.model, path),
        "imageSize": [int(calibration.image_size[0]), int(calibration.image_size[1])],
        "cameraMatrix": np.asarray(calibration.camera_matrix, dtype=np.float64).tolist(),
        "distortionCoefficients": np.asarray(
            calibration.distortion, dtype=np.float64
        ).reshape(-1).tolist(),
        "rmsReprojectionError": float(calibration.rms_reprojection_error),
        "sampleCount": int(calibration.sample_count),
        "board": dict(calibration.board),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_calibration(path: Path) -> CameraCalibration:
    """Liest eine Kalibrierdatei. Fehlt etwas, meldet die Ausnahme den Pfad."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Kalibrierung nicht gefunden: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema == LEGACY_SCHEMA:
        model = PINHOLE
    elif schema == SCHEMA:
        model = _checked_model(data.get("distortionModel"), path)
    else:
        raise ValueError(f"Unbekanntes Kalibrierschema '{schema}' in {path} (erwartet {SCHEMA})")
    size = data["imageSize"]
    distortion = np.asarray(data["distortionCoefficients"], dtype=np.float64).reshape(-1)
    if model == FISHEYE and distortion.size != 4:
        raise ValueError(
            f"Fisheye-Kalibrierung in {path} hat {distortion.size} statt 4 Koeffizienten"
        )
    return CameraCalibration(
        camera_matrix=np.asarray(data["cameraMatrix"], dtype=np.float64).reshape(3, 3),
        distortion=distortion,
        image_size=(int(size[0]), int(size[1])),
        frame_id=str(data.get("frameId", "")),
        calibration_id=str(data.get("calibrationId", "")),
        rms_reprojection_error=float(data.get("rmsReprojectionError", float("nan"))),
        sample_count=int(data.get("sampleCount", 0)),
        board=dict(data.get("board", {})),
        model=model,
    )


def _checked_model(model: Any, path: Path) -> str:
    """Gibt `model` zurueck, wenn es ein bekanntes Linsenmodell ist.

    Ein unbekanntes Modell ist ein Fehler, kein Rueckfall auf Pinhole: mit
    falschem Modell entzerrt, liegen alle Posen plausibel daneben.
    """
    if model not in DISTORTION_MODELS:
        raise ValueError(
            f"Unbekanntes Linsenmodell '{model}' in {path} (erwartet eines von "
            f"{', '.join(DISTORTION_MODELS)})"
        )
    return str(model)


def default_calibration(resolution: tuple[int, int], frame_id: str = "") -> CameraCalibration:
    """Grob geschaetzte Kalibrierung, wenn (noch) keine echte existiert.

    **Kein Ersatz fuer eine echte Kalibrierfahrt** (Testplan Abschnitt 3.3) --
    die Intrinsik ist aus einer angenommenen Sichtfeldbreite gerechnet, nicht
    gemessen. Posen damit sind plausibel orientiert, aber nicht masshaltig:
    Distanzen und Groessen koennen deutlich daneben liegen. Nur dafuer da,
    die AprilTag-Erkennung ohne Kalibrierdatei lauffaehig zu halten, damit
    Detektor, Overlay und Job-Pfad sich pruefen lassen, waehrend die echte
    Kalibrierung noch aussteht. `calibration_id` markiert das im Ergebnis
    unuebersehbar.
    """
    width, height = resolution
    focal_length = width / (2.0 * math.tan(math.radians(DEFAULT_HORIZONTAL_FOV_DEG) / 2.0))
    matrix = np.array(
        [
            [focal_length, 0.0, width / 2.0],
            [0.0, focal_length, height / 2.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.zeros(5, dtype=np.float64),
        image_size=(int(width), int(height)),
        frame_id=frame_id,
        calibration_id=PLACEHOLDER_CALIBRATION_ID,
        rms_reprojection_error=float("nan"),
        sample_count=0,
        board={},
    )


def check_resolution(calibration: CameraCalibration, image_size: tuple[int, int]) -> None:
    """Wirft, wenn die Kalibrierung zu einer anderen Aufloesung gehoert.

    Ohne diese Pruefung ist jede Pose um den Skalierungsfaktor daneben --
    plausibel daneben, also unbemerkt. Das kommt in der Praxis vor:
    `AprilTagProfileConfig.resolution` ist 2028x1520, eine Kalibrierung ueber
    `cv2.VideoCapture(0)` endet leicht bei 640x480.
    """
    expected = (int(calibration.image_size[0]), int(calibration.image_size[1]))
    actual = (int(image_size[0]), int(image_size[1]))
    if expected != actual:
        raise ValueError(
            f"Kalibrierung gilt fuer {expected[0]}x{expected[1]}, Bild ist "
            f"{actual[0]}x{actual[1]}. Neu kalibrieren oder scale_to_resolution() benutzen."
        )


def scale_to_resolution(
    calibration: CameraCalibration, image_size: tuple[int, int]
) -> CameraCalibration:
    """Skaliert die Intrinsik auf eine andere Aufloesung.

    Nur fuer denselben Bildausschnitt und dasselbe Seitenverhaeltnis gueltig
    (reines Verkleinern, kein Zuschneiden). Die Verzeichnungskoeffizienten
    beziehen sich auf normierte Koordinaten und bleiben unveraendert -- fuer
    das Pinhole- wie das Fisheye-Modell, daher wird `model` unveraendert
    uebernommen.
    """
    source = (int(calibration.image_size[0]), int(calibration.image_size[1]))
    target = (int(image_size[0]), int(image_size[1]))
    scale_x = target[0] / source[0]
    scale_y = target[1] / source[1]
    if abs(scale_x - scale_y) > 1e-3:
        raise ValueError(
            f"Seitenverhaeltnis aendert sich ({source} -> {target}); "
            "Skalieren wuerde die Intrinsik verfaelschen."
        )
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64).copy()
    matrix[0, :] *= scale_x
    matrix[1, :] *= scale_y
    return CameraCalibration(
        camera_matrix=matrix,
        distortion=np.asarray(calibration.distortion, dtype=np.float64).copy(),
        image_size=target,
        frame_id=calibration.frame_id,
        calibration_id=f"{calibration.calibration_id}@{target[0]}x{target[1]}",
        rms_reprojection_error=calibration.rms_reprojection_error,
        sample_count=calibration.sample_count,
        board=dict(calibration.board),
        model=calibration.model,
    )


#: `calibration_identity` wird hier erneut ausgegeben, damit Aufrufer nicht in
#: einem zweiten Modul suchen muessen. Es steht in `identity`, weil jenes
#: Modul ohne numpy funktionieren muss.
__all__ = [
    "DISTORTION_MODELS",
    "FISHEYE",
    "LEGACY_SCHEMA",
    "PINHOLE",
    "SCHEMA",
    "PLACEHOLDER_CALIBRATION_ID",
    "CameraCalibration",
    "calibration_identity",
    "check_resolution",
    "default_calibration",
    "load_calibration",
    "save_calibration",
    "scale_to_resolution",
]
