"""Detection sources: find the source that holds the camera and open the sources."""

import asyncio
import logging
from collections.abc import Mapping

from ..detection import DetectionSource

_log = logging.getLogger(__package__)


def _camera_owner(
    sources: Mapping[str, DetectionSource], opened: Mapping[str, bool]
) -> DetectionSource | None:
    """Return the first opened source that holds a shared camera.

    Uses the `camera` attribute instead of a profile name. A lookup by name, such
    as `sources.get("image_recognition")`, would silently switch off the live
    stream when that profile is renamed or removed.
    """
    for profile in sorted(sources):
        if not opened.get(profile):
            continue
        if getattr(sources[profile], "camera", None) is not None:
            return sources[profile]
    return None


async def _open_source(source: DetectionSource) -> bool:
    """Open a source; return `False` if it is not ready for operation."""
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
