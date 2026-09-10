"""Registry der Erkennungsprofile."""

from collections.abc import Callable
from pathlib import Path

from ..config import VisionServerConfig
from .base import Detection, DetectionRequest, DetectionSource

#: Gesammelter Ablageort aller vom Backend aufgerufenen Job-Scripts, nicht
#: unter vision_server, damit auch Jobs anderer Backend-Teile hierher passen.
JOBS_DIR = Path(__file__).resolve().parent.parent.parent / "jobs"

SourceFactory = Callable[[VisionServerConfig], DetectionSource]


def _hello_world(config: VisionServerConfig) -> DetectionSource:
    from .hello_world import HelloWorldDetectionSource

    return HelloWorldDetectionSource(config.detection_latency)


def _calibration(config: VisionServerConfig) -> DetectionSource:
    from .script_runner import ScriptDetectionSource

    return ScriptDetectionSource("calibration", JOBS_DIR / "calibrate.py")


def _image_recognition(config: VisionServerConfig) -> DetectionSource:
    from .script_runner import ScriptDetectionSource

    return ScriptDetectionSource("image_recognition", JOBS_DIR / "take_image.py")


#: Factories importieren ihr Modul **innerhalb** der Funktion. Sonst liegt jede
#: Abhaengigkeit einer Quelle auf der Importkette des Zellenservers, und ein
#: `import cv2` in einem Profil macht den Server ohne OpenCV unstartbar.
DETECTION_SOURCES: dict[str, SourceFactory] = {
    "hello_world": _hello_world,
    "calibration": _calibration,
    "image_recognition": _image_recognition,
}


def _factory(profile_id: str) -> SourceFactory:
    try:
        return DETECTION_SOURCES[profile_id]
    except KeyError:
        known = ", ".join(sorted(DETECTION_SOURCES))
        raise ValueError(
            f"Unbekanntes Erkennungsprofil '{profile_id}' (bekannt: {known})"
        ) from None


def build_detection_source(config: VisionServerConfig, profile_id: str) -> DetectionSource:
    """Erzeugt die Quelle eines Profils."""
    return _factory(profile_id)(config)


def build_detection_sources(config: VisionServerConfig) -> dict[str, DetectionSource]:
    """Eine Instanz je referenziertem Profil, damit Rezepte sich eine Kamera teilen.

    Laeuft zur Installationszeit: ein Tippfehler im Profil bricht den
    Serverstart, nie einen Job.
    """
    return {profile: _factory(profile)(config) for profile in sorted(config.detection_profiles)}


__all__ = [
    "Detection",
    "DetectionRequest",
    "DetectionSource",
    "DETECTION_SOURCES",
    "build_detection_source",
    "build_detection_sources",
]
