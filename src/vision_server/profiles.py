"""Profilspezifische Konfiguration.

Auf Paketebene, damit `config.py` sie importieren kann, ohne dass eine
Erkennungsquelle mit OpenCV auf die Importkette des Zellenservers geraet.
Nur Stdlib-Typen: `tag_family` ist ein String und wird erst in der Quelle zu
einer `cv2.aruco.DICT_*`-Konstante aufgeloest.

Nie eine Matrix hier hinein — `K`/`D` sind numpy, unhashbar und gehoeren dem,
was sie aus `calibration_path` laedt.
"""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_CALIBRATION_PATH = Path(__file__).resolve().parent.parent / "apriltag" / "calibration.yaml"


@dataclass(frozen=True)
class AprilTagProfileConfig:
    """Betriebsparameter der AprilTag-Erkennung."""

    camera_index: int = 0
    use_picamera: bool = True
    resolution: tuple[int, int] = (2028, 1520)
    calibration_path: Path = DEFAULT_CALIBRATION_PATH
    tag_map_path: Path | None = None
    tag_family: str = "tag36h11"
    tag_size_m: float = 0.05
    warmup_s: float = 2.0
    capture_timeout_s: float = 5.0
    samples_per_job: int = 3
    max_reproj_error_px: float = 3.0
    frame_id: str = "cam_ceiling"
    frame_convention: str = "z_forward_x_right_y_down"


@dataclass(frozen=True)
class CameraStreamConfig:
    """Betriebsparameter der einen Kamera, die sich QR-Erkennung und Livestream teilen.

    Eine einzige `SharedCamera`-Instanz (siehe `camera.py`) wird mit diesen
    Werten geoeffnet; sowohl die QR-Erkennung (`detection/image_recognition.py`)
    als auch der Node-Publisher (`camera_stream.py`) lesen von dort, statt
    selbst je einen eigenen Kamera-Handle zu oeffnen — Picamera2 laesst pro
    Kamera nur einen offenen Zugriff gleichzeitig zu.
    """

    camera_index: int = 0
    use_picamera: bool = True
    resolution: tuple[int, int] = (1280, 720)
    warmup_s: float = 2.0
    stream_fps: float = 5.0
    jpeg_quality: int = 70
    qr_scan_duration_s: float = 30.0
    node_name: str = "LatestCameraFrame"
