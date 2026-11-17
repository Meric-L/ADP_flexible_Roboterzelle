"""Tag map over OPC UA: read node `TagMapJson` and method `SetTagMap`."""

import asyncio
import logging

from asyncua import ua, uamethod

from ..errors import VisionErrorCode
from .uamethods import add_vision_method, code_reply
from .wiring import Wiring

_log = logging.getLogger(__package__)


def _write_tag_map_file(path, text: str) -> None:
    """Write the map file; create the directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def _publish_tag_map(wiring: Wiring) -> None:
    """Mirror the loaded map into the read node."""
    from tagloc.tagmap import tag_map_to_json

    space = wiring.space
    current = getattr(wiring.apriltag_source, "_tag_map", None)
    if current is not None and space.tag_map_json is not None:
        await space.tag_map_json.write_value(
            ua.Variant(tag_map_to_json(current), ua.VariantType.String)
        )


def _busy_reason(wiring: Wiring) -> str | None:
    """Return why a new map may not be set now (`None` means it may)."""
    if wiring.jobs.busy:
        return "SetTagMap waehrend laufendem Job abgelehnt"
    session = wiring.calibration_session
    if session is not None and session.running:
        return "SetTagMap waehrend laufender Kalibrierung abgelehnt"
    if wiring.layer2_active():
        # A new map discards the anchor the run is based on.
        return "SetTagMap waehrend laufendem Layer-2-Lauf abgelehnt"
    if wiring.hand_eye_active():
        # The tag size from the map scales every capture.
        return "SetTagMap waehrend Hand-Auge-Kalibrierung abgelehnt"
    return None


async def _persist_tag_map(wiring: Wiring, tag_map) -> VisionErrorCode:
    """Write the active map to the file as a local cache."""
    from tagloc.tagmap import tag_map_to_json

    config = wiring.config
    path = config.apriltag.tag_map_path if config.apriltag is not None else None
    if path is None:
        return VisionErrorCode.OK
    try:
        await asyncio.to_thread(_write_tag_map_file, path, tag_map_to_json(tag_map))
    except OSError as error:
        # Measuring comes before persistence: the map is already active, and
        # only a restart falls back to the old file. The caller must know that,
        # hence an error code anyway.
        _log.error("Tag-Map konnte nicht nach %s geschrieben werden: %s", path, error)
        return VisionErrorCode.INTERNAL
    _log.info("Tag-Map gesetzt und nach %s geschrieben", path)
    return VisionErrorCode.OK


def _set_tag_map_method(wiring: Wiring):
    """Handler for `1:SetTagMap` -- set the tag map of the cell.

    This makes the backend the maintenance point: a new tag size or a moved
    world tag no longer needs file access on the Pi. The file remains the local
    cache, so the Pi can still measure without the backend.

    Check first, then apply: a rejected map leaves the running map untouched.
    """
    from tagloc.tagmap import tag_map_from_json

    @uamethod
    async def method(parent, tag_map_json: str):
        reason = _busy_reason(wiring)
        if reason is not None:
            _log.warning(reason)
            return code_reply(VisionErrorCode.BUSY)
        try:
            tag_map = tag_map_from_json(tag_map_json or "", source="SetTagMap")
        except ValueError as error:
            _log.warning("SetTagMap abgelehnt: %s", error)
            return code_reply(VisionErrorCode.INVALID_ARGUMENT)

        for problem in wiring.apriltag_source.apply_tag_map(tag_map):
            # A report, not an error -- a cell that is still being set up may measure.
            _log.warning("Tag-Map aus SetTagMap: %s", problem)
        await _publish_tag_map(wiring)
        return code_reply(await _persist_tag_map(wiring, tag_map))

    return method


async def install_tag_map(wiring: Wiring) -> None:
    """Publish the loaded map and create `SetTagMap`.

    Only with an opened AprilTag source and a `TagMapJson` node.
    """
    space = wiring.space
    if wiring.apriltag_source is None or space.tag_map_json is None:
        return
    await _publish_tag_map(wiring)
    await add_vision_method(
        space, wiring.config, wiring.methods, "SetTagMap", _set_tag_map_method(wiring),
        [("TagMapJson", ua.VariantType.String, "Neue Tag-Map als JSON.")], [("Error", ua.VariantType.Int32, "Vision-Fehlercode.")],
    )
