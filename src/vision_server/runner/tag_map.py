"""Tag-Map ueber OPC UA: Leseknoten `TagMapJson` und Methode `SetTagMap`."""

import asyncio
import logging

from asyncua import ua, uamethod

from ..errors import VisionErrorCode
from .uamethods import add_vision_method, code_reply
from .wiring import Wiring

_log = logging.getLogger(__package__)


def _write_tag_map_file(path, text: str) -> None:
    """Schreibt die Karte, Verzeichnis wird bei Bedarf angelegt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def _publish_tag_map(wiring: Wiring) -> None:
    """Spiegelt die geladene Karte in den Leseknoten."""
    from tagloc.tagmap import tag_map_to_json

    space = wiring.space
    current = getattr(wiring.apriltag_source, "_tag_map", None)
    if current is not None and space.tag_map_json is not None:
        await space.tag_map_json.write_value(
            ua.Variant(tag_map_to_json(current), ua.VariantType.String)
        )


def _busy_reason(wiring: Wiring) -> str | None:
    """Warum jetzt keine neue Karte gesetzt werden darf (`None` = darf)."""
    if wiring.jobs.busy:
        return "SetTagMap waehrend laufendem Job abgelehnt"
    session = wiring.calibration_session
    if session is not None and session.running:
        return "SetTagMap waehrend laufender Kalibrierung abgelehnt"
    if wiring.layer2_active():
        # Eine neue Karte verwirft den Anker, auf dem der Lauf steht.
        return "SetTagMap waehrend laufendem Layer-2-Lauf abgelehnt"
    if wiring.hand_eye_active():
        # Die Tag-Groesse aus der Karte skaliert jede Aufnahme.
        return "SetTagMap waehrend Hand-Auge-Kalibrierung abgelehnt"
    return None


async def _persist_tag_map(wiring: Wiring, tag_map) -> VisionErrorCode:
    """Schreibt die aktive Karte als lokalen Zwischenspeicher in die Datei."""
    from tagloc.tagmap import tag_map_to_json

    config = wiring.config
    path = config.apriltag.tag_map_path if config.apriltag is not None else None
    if path is None:
        return VisionErrorCode.OK
    try:
        await asyncio.to_thread(_write_tag_map_file, path, tag_map_to_json(tag_map))
    except OSError as error:
        # Messen geht vor Persistenz: die Karte ist bereits aktiv, nur
        # ein Neustart faellt auf die alte Datei zurueck. Das muss der
        # Aufrufer wissen, deshalb trotzdem ein Fehlercode.
        _log.error("Tag-Map konnte nicht nach %s geschrieben werden: %s", path, error)
        return VisionErrorCode.INTERNAL
    _log.info("Tag-Map gesetzt und nach %s geschrieben", path)
    return VisionErrorCode.OK


def _set_tag_map_method(wiring: Wiring):
    """Handler fuer `1:SetTagMap` -- setzt die Tag-Map der Zelle.

    Damit ist das Backend die Pflegestelle: eine neue Tag-Groesse oder
    ein verschobener Welttag brauchen keinen Dateizugriff auf dem Pi
    mehr. Die Datei bleibt trotzdem der lokale Zwischenspeicher, damit
    der Pi ohne Backend messen kann.

    Erst pruefen, dann uebernehmen: eine abgelehnte Karte laesst die
    laufende unangetastet.
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
            # Bericht, kein Fehler -- eine Zelle im Aufbau darf messen.
            _log.warning("Tag-Map aus SetTagMap: %s", problem)
        await _publish_tag_map(wiring)
        return code_reply(await _persist_tag_map(wiring, tag_map))

    return method


async def install_tag_map(wiring: Wiring) -> None:
    """Veroeffentlicht die geladene Karte und legt `SetTagMap` an.

    Nur mit geoeffneter AprilTag-Quelle und `TagMapJson`-Knoten.
    """
    space = wiring.space
    if wiring.apriltag_source is None or space.tag_map_json is None:
        return
    await _publish_tag_map(wiring)
    await add_vision_method(
        space, wiring.config, wiring.methods, "SetTagMap", _set_tag_map_method(wiring),
        [ua.VariantType.String], [ua.VariantType.Int32],
    )
