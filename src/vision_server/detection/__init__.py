"""Registry of detection profiles."""

from collections.abc import Callable
from pathlib import Path

from ..config import VisionServerConfig
from .base import Detection, DetectionRequest, DetectionSource

#: Common location of all job scripts called by the backend. It is outside
#: ``vision_server``, so that jobs from other parts of the backend fit here too.
JOBS_DIR = Path(__file__).resolve().parent.parent.parent / "jobs"

SourceFactory = Callable[[VisionServerConfig], DetectionSource]


def _calibration(config: VisionServerConfig) -> DetectionSource:
    from .script_runner import ScriptDetectionSource

    return ScriptDetectionSource("calibration", JOBS_DIR / "calibrate.py")


def _apriltag(config: VisionServerConfig) -> DetectionSource:
    from ..profiles import AprilTagProfileConfig, CameraStreamConfig
    from .apriltag import AprilTagDetectionSource

    return AprilTagDetectionSource(
        config.apriltag or AprilTagProfileConfig(),
        config.camera_stream or CameraStreamConfig(),
    )


#: Factories import their module **inside** the function. Otherwise every dependency
#: of a source ends up on the import chain of the cell server, and an ``import cv2``
#: in one profile would make the server unstartable without OpenCV.
DETECTION_SOURCES: dict[str, SourceFactory] = {
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
    """Create one instance per referenced profile, so that recipes share a camera.

    Runs at installation time: a typo in a profile aborts server startup, never a job.
    """
    return {profile: _factory(profile)(config) for profile in sorted(config.detection_profiles)}


__all__ = [
    "Detection",
    "DetectionRequest",
    "DetectionSource",
    "DETECTION_SOURCES",
    "build_detection_sources",
]
