"""Registry der Erkennungsprofile."""

from collections.abc import Callable
from pathlib import Path

from ..config import VisionServerConfig
from .base import Detection, DetectionSource
from .hello_world import HelloWorldDetectionSource
from .script_runner import ScriptDetectionSource

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

DETECTION_SOURCES: dict[str, Callable[[VisionServerConfig], DetectionSource]] = {
    "hello_world": lambda config: HelloWorldDetectionSource(config.detection_latency),
    "calibration": lambda config: ScriptDetectionSource(
        "calibration", SCRIPTS_DIR / "calibrate.py"
    ),
    "image_recognition": lambda config: ScriptDetectionSource(
        "image_recognition", SCRIPTS_DIR / "take_image.py"
    ),
}

# Ordnet jede bekannte RecipeId (config.known_recipe_ids) einem Erkennungsprofil
# aus DETECTION_SOURCES zu. Recipe-Ids ohne Eintrag fallen auf config.detection_profile
# zurueck (Kompatibilitaet zur bisherigen "eine Quelle pro Server"-Konfiguration).
RECIPE_PROFILES: dict[str, str] = {
    "hello-world": "hello_world",
    "calibration": "calibration",
    "image-recognition": "image_recognition",
}


def build_detection_source(config: VisionServerConfig) -> DetectionSource:
    """Erzeugt die zum konfigurierten Profil gehoerende Erkennungsquelle."""
    return _build(config, config.detection_profile)


def build_detection_sources(config: VisionServerConfig) -> dict[str, DetectionSource]:
    """Erzeugt fuer jede bekannte RecipeId die zugehoerige Erkennungsquelle.

    Ermoeglicht mehrere Jobs pro Server (z.B. Kalibrierung und Bilderkennung),
    die ueber die RecipeId eines StartSingleJob-Aufrufs ausgewaehlt werden.
    """
    sources: dict[str, DetectionSource] = {}
    for recipe_id in config.known_recipe_ids:
        profile = RECIPE_PROFILES.get(recipe_id, config.detection_profile)
        sources[recipe_id] = _build(config, profile)
    return sources


def _build(config: VisionServerConfig, profile: str) -> DetectionSource:
    try:
        factory = DETECTION_SOURCES[profile]
    except KeyError:
        known = ", ".join(sorted(DETECTION_SOURCES))
        raise ValueError(
            f"Unbekanntes Erkennungsprofil '{profile}' (bekannt: {known})"
        ) from None
    return factory(config)


__all__ = [
    "Detection",
    "DetectionSource",
    "DETECTION_SOURCES",
    "RECIPE_PROFILES",
    "build_detection_source",
    "build_detection_sources",
]
