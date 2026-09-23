"""Registry der Erkennungsprofile."""

from collections.abc import Callable
from pathlib import Path

from ..config import VisionServerConfig
from .base import Detection, DetectionRequest, DetectionSource

#: `src/` -- Wurzel aller Importpfade. Einzige Stelle dieses Pakets, die sie
#: aus dem eigenen Dateipfad ableitet; die Job-Scripts bekommen sie als
#: `PYTHONPATH` (`ScriptDetectionSource.subprocess_env`).
SRC_DIR = Path(__file__).resolve().parents[2]

#: Gesammelter Ablageort aller vom Backend aufgerufenen Job-Scripts, nicht
#: unter vision_server, damit auch Jobs anderer Backend-Teile hierher passen.
JOBS_DIR = SRC_DIR / "jobs"

SourceFactory = Callable[[VisionServerConfig], DetectionSource]


def _hello_world(config: VisionServerConfig) -> DetectionSource:
    from .hello_world import HelloWorldDetectionSource

    return HelloWorldDetectionSource(config.detection_latency)


def calibration_script_env(config: VisionServerConfig) -> dict[str, str]:
    """Was `jobs/calibrate.py` ueber diesen Pi wissen muss.

    Frame-ID immer; Kalibrier- und Tag-Map-Pfad nur mit AprilTag-Profil --
    ohne faellt das Script auf seine eigenen Standardpfade zurueck. Eine
    bewusst abgeschaltete Tag-Map (`tag_map_path=None`) kommt als leerer
    String an.
    """
    env = {"VISION_FRAME_ID": config.frame_id}
    if config.apriltag is not None:
        env["VISION_FRAME_ID"] = config.apriltag.frame_id or config.frame_id
        env["VISION_CALIBRATION_PATH"] = str(config.apriltag.calibration_path)
        tag_map = config.apriltag.tag_map_path
        env["VISION_TAG_MAP_PATH"] = "" if tag_map is None else str(tag_map)
    return env


def _calibration(config: VisionServerConfig) -> DetectionSource:
    from .script_runner import ScriptDetectionSource

    return ScriptDetectionSource(
        "calibration", JOBS_DIR / "calibrate.py", env=calibration_script_env(config)
    )


def _apriltag(config: VisionServerConfig) -> DetectionSource:
    from ..profiles import AprilTagProfileConfig, CameraStreamConfig
    from .apriltag import AprilTagDetectionSource

    return AprilTagDetectionSource(
        config.apriltag or AprilTagProfileConfig(),
        config.camera_stream or CameraStreamConfig(),
    )


#: Factories importieren ihr Modul **innerhalb** der Funktion. Sonst liegt jede
#: Abhaengigkeit einer Quelle auf der Importkette des Zellenservers, und ein
#: `import cv2` in einem Profil macht den Server ohne OpenCV unstartbar.
DETECTION_SOURCES: dict[str, SourceFactory] = {
    "hello_world": _hello_world,
    "calibration": _calibration,
    "apriltag": _apriltag,
}


def _factory(profile_id: str) -> SourceFactory:
    try:
        return DETECTION_SOURCES[profile_id]
    except KeyError:
        known = ", ".join(sorted(DETECTION_SOURCES))
        raise ValueError(
            f"Unbekanntes Erkennungsprofil '{profile_id}' (bekannt: {known})"
        ) from None


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
    "build_detection_sources",
]
