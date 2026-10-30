"""Erkennungsquellen: Kamera-Halter finden und Quellen oeffnen."""

import asyncio
import logging
from collections.abc import Mapping

from ..detection import DetectionSource

_log = logging.getLogger(__package__)


def _camera_owner(
    sources: Mapping[str, DetectionSource], opened: Mapping[str, bool]
) -> DetectionSource | None:
    """Return the first opened source holding a shared camera.

    Deliberately via the `camera` attribute rather than a profile name:
    this used to be `sources.get("image_recognition")`, and renaming or
    removing that profile would have silently killed the livestream.
    """
    for profile in sorted(sources):
        if not opened.get(profile):
            continue
        if getattr(sources[profile], "camera", None) is not None:
            return sources[profile]
    return None


async def _open_source(source: DetectionSource) -> bool:
    """Oeffnet eine Quelle; `False`, wenn sie nicht betriebsbereit ist."""
    started = asyncio.get_running_loop().time()
    try:
        await source.open()
    except Exception:
        _log.exception(
            "Erkennungsquelle '%s' konnte nicht geoeffnet werden", source.profile_id
        )
        return False
    duration = asyncio.get_running_loop().time() - started
    if duration > 0.5:
        _log.info("Quelle '%s' in %.1f s bereit", source.profile_id, duration)
    return True
