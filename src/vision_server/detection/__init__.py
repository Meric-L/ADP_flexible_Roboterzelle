"""Registry der Erkennungsprofile."""

from collections.abc import Callable

from ..config import VisionServerConfig
from .base import Detection, DetectionSource
from .hello_world import HelloWorldDetectionSource

DETECTION_SOURCES: dict[str, Callable[[VisionServerConfig], DetectionSource]] = {
    "hello_world": lambda config: HelloWorldDetectionSource(config.detection_latency),
}


def build_detection_source(config: VisionServerConfig) -> DetectionSource:
    """Erzeugt die zum konfigurierten Profil gehoerende Erkennungsquelle."""
    try:
        factory = DETECTION_SOURCES[config.detection_profile]
    except KeyError:
        known = ", ".join(sorted(DETECTION_SOURCES))
        raise ValueError(
            f"Unbekanntes Erkennungsprofil '{config.detection_profile}' (bekannt: {known})"
        ) from None
    return factory(config)


__all__ = ["Detection", "DetectionSource", "DETECTION_SOURCES", "build_detection_source"]
