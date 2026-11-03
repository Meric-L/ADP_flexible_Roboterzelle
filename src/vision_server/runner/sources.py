"""Erkennungsquellen: Kamera-Halter finden und Quellen oeffnen."""

import asyncio
import logging
from collections.abc import Mapping

from ..detection import DetectionSource

_log = logging.getLogger(__package__)


def _camera_owner(
    sources: Mapping[str, DetectionSource], opened: Mapping[str, bool]
) -> DetectionSource | None:
    """Gibt die erste geoeffnete Quelle zurueck, die eine geteilte Kamera haelt.

    Bewusst ueber das Attribut `camera` statt ueber einen Profilnamen: frueher
    war das `sources.get("image_recognition")`, und das Umbenennen oder
    Entfernen dieses Profils haette den Livestream still abgeschaltet.
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
