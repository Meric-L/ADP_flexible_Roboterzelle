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

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Machine-specific, hence under `data/` (excluded by .gitignore).
DEFAULT_CALIBRATION_PATH = _REPO_ROOT / "data" / "calibration" / "camera.json"
#: Cell layout, hence versioned under `config/`.
DEFAULT_TAG_MAP_PATH = _REPO_ROOT / "config" / "tagmap.json"


@dataclass(frozen=True)
class AprilTagProfileConfig:
    """Betriebsparameter der AprilTag-Erkennung."""

    camera_index: int = 0
    use_picamera: bool = True
    resolution: tuple[int, int] = (2028, 1520)
    calibration_path: Path = DEFAULT_CALIBRATION_PATH
    tag_map_path: Path | None = DEFAULT_TAG_MAP_PATH
    tag_family: str = "tag36h11"
    tag_size_m: float = 0.05
    warmup_s: float = 2.0
    capture_timeout_s: float = 5.0
    samples_per_job: int = 3
    max_reproj_error_px: float = 3.0
    frame_id: str = "cam_ceiling"
    frame_convention: str = "z_forward_x_right_y_down"
    #: "aruco" (default), "pupil" or "auto" -- see tagloc.detector.
    detector_backend: str = "aruco"
    #: Scale intrinsics to the actual image size instead of aborting. Only
    #: enable when deliberately running at a resolution other than the one
    #: calibrated for.
    allow_resolution_mismatch: bool = False


@dataclass(frozen=True)
class CameraStreamConfig:
    """Betriebsparameter der einen Kamera, die sich Erkennung und Livestream teilen.

    Eine einzige `SharedCamera`-Instanz (siehe `camera.py`) wird mit diesen
    Werten geoeffnet; sowohl die Erkennungsquelle (`detection/apriltag.py`) als
    auch der Node-Publisher (`camera_stream.py`) lesen von dort, statt selbst je
    einen eigenen Kamera-Handle zu oeffnen — Picamera2 laesst pro Kamera nur
    einen offenen Zugriff gleichzeitig zu.
    """

    camera_index: int = 0
    use_picamera: bool = True
    resolution: tuple[int, int] = (1280, 720)
    warmup_s: float = 2.0
    stream_fps: float = 5.0
    jpeg_quality: int = 70
    node_name: str = "LatestCameraFrame"
    #: Writable node through which the frontend selects the overlay mode.
    mode_node_name: str = "CameraStreamMode"
    #: Initial value; valid values are "off", "apriltag", "calibration"
    #: (tagloc.overlay).
    overlay_mode: str = "apriltag"
    #: Minimum gap between two detection runs for the overlay. The stream is
    #: meant to help debugging, not load the Pi's CPU -- between runs, the
    #: last result is redrawn.
    overlay_interval_s: float = 0.5


@dataclass(frozen=True)
class AssetConfig:
    """Woraus dieses Vision-System besteht -- Part 2 (AMCM).

    Reine Stammdaten, wie die uebrigen Profile nur Stdlib-Typen und hashbar.
    Was sich zur Laufzeit aendert (CPU-Temperatur, Kamerazustand) steht nicht
    hier, sondern wird von `asset_model` aus den laufenden Objekten gelesen.
    """

    manufacturer: str = "TU Darmstadt PLCM"
    model: str = "Flexible Roboterzelle -- Vision"
    serial_number: str = ""
    software_revision: str = "0.1.0"
    #: Die Recheneinheit, auf der dieser Server laeuft.
    computing_device_model: str = "Raspberry Pi"
    #: Kameramodul und Objektiv. Leer lassen, was nicht bekannt ist -- ein
    #: erfundener Wert waere schlimmer als ein leeres Feld.
    image_sensor_model: str = ""
    lens_model: str = ""
