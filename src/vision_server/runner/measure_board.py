"""MeasureBoard: das Kalibrier-Schachbrett in Kamerakoordinaten messen."""

import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..state_machine import VisionStateMachines

_log = logging.getLogger(__package__)


async def _install_measure_board(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
) -> None:
    """Legt MeasureBoard an: das Kalibrier-Schachbrett messen, damit die
    Steuerung es vor der Hand-Auge-Aufnahme zentrieren und die Kalibrierfahrt
    der Deckenkamera vor jeder Aufnahme pruefen kann. An beiden Pis. Antwortet
    erst nach der Messung, das Ergebnis kommt direkt zurueck.
    Siehe `doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`.
    """
    from ..handeye_session import _parse_board
    from ..measureboard import RESULT_SCHEMA, dumps, parse_request

    def refusal(code: VisionErrorCode, message: str):
        result = {"schema": RESULT_SCHEMA, "found": False, "error": code.name, "message": message}
        return (
            ua.Variant(dumps(result), ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def measure_board(parent, request_json: str):
        """1:MeasureBoard -- Brett messen, nur in Kamerakoordinaten."""
        try:
            request = parse_request(request_json or "", _parse_board)
        except ValueError as error:
            _log.warning("MeasureBoard abgelehnt: %s", error)
            return refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        if busy():
            return refusal(VisionErrorCode.BUSY, "Job oder Auswertung der Kalibrierung laeuft")
        result = await apriltag_source.measure_board_view(request)
        payload = dumps(result)
        if result["error"]:
            code = VisionErrorCode.DETECTION_FAILED
        else:
            code = VisionErrorCode.OK
        return (
            ua.Variant(payload, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    methods["MeasureBoard"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.MeasureBoard", space.own_idx),
        ua.QualifiedName("MeasureBoard", space.own_idx),
        measure_board,
        [ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.Int32],
    )
