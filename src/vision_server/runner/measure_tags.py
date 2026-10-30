"""MeasureTags: Tags in Kamerakoordinaten messen (Hand-Pi)."""

import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..state_machine import VisionStateMachines

_log = logging.getLogger(__package__)


class _Activity:
    """Merkt, ob gerade eine Messung mit frischen Bildern laeuft.

    Einfaches Flag: der Server ist single-threaded (asyncio), zwischen Pruefen
    und Setzen liegt kein `await`.
    """

    def __init__(self) -> None:
        self.active = False


async def _install_measure_tags(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
    *,
    activity: _Activity | None = None,
    blocked_by: Callable[[], bool] = lambda: False,
) -> None:
    """Legt MeasureTags an: Tags in Kamerakoordinaten fuer die Regelung der
    Steuerung. Nur auf dem Hand-Pi (Knoten nur mit `hand_eye_path`). Antwortet
    erst nach der Messung, das Ergebnis kommt direkt zurueck; der Knoten
    `LastTagMeasurement` ist nur zur Anzeige. Siehe
    `doc/projektdoku/arbeitsplaene/layer2-measuretags.md`.
    """
    from ..measuretag import RESULT_SCHEMA, dumps, parse_request

    activity = activity if activity is not None else _Activity()

    def refusal(code: VisionErrorCode, message: str):
        result = {"schema": RESULT_SCHEMA, "tags": [], "error": f"{code.name}: {message}"}
        return (
            ua.Variant(dumps(result), ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def measure_tags(parent, request_json: str):
        """1:MeasureTags -- Tags messen, nur in Kamerakoordinaten."""
        try:
            request = parse_request(request_json or "")
        except ValueError as error:
            _log.warning("MeasureTags abgelehnt: %s", error)
            return refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        if busy() or blocked_by():
            return refusal(VisionErrorCode.BUSY, "Job, Kalibrierung oder Tag-Einmessung laeuft")
        # Solange gemessen wird, haelt das Stream-Overlay an (siehe unten).
        apriltag_source.measure_calls += 1
        activity.active = True
        try:
            result = await apriltag_source.measure_tags(request)
        finally:
            activity.active = False
            apriltag_source.measure_calls -= 1
        payload = dumps(result)
        try:
            await space.last_tag_measurement.write_value(
                ua.Variant(payload, ua.VariantType.String)
            )
        except Exception:
            _log.exception("LastTagMeasurement konnte nicht geschrieben werden")
        code = VisionErrorCode.DETECTION_FAILED if result["error"] else VisionErrorCode.OK
        return (
            ua.Variant(payload, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    methods["MeasureTags"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.MeasureTags", space.own_idx),
        ua.QualifiedName("MeasureTags", space.own_idx),
        measure_tags,
        [ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.Int32],
    )
