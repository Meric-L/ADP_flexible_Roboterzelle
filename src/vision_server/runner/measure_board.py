"""MeasureBoard: das Kalibrier-Schachbrett in Kamerakoordinaten messen."""

from collections.abc import Callable
from typing import Any

from asyncua import Node, ua

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..state_machine import VisionStateMachines
from .uamethods import add_vision_method, json_method, text_reply


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
        return text_reply(dumps(result), code)

    async def measure(request, request_json: str):
        """1:MeasureBoard -- Brett messen, nur in Kamerakoordinaten."""
        result = await apriltag_source.measure_board_view(request)
        code = VisionErrorCode.DETECTION_FAILED if result["error"] else VisionErrorCode.OK
        return text_reply(dumps(result), code)

    handler = json_method(
        "MeasureBoard",
        states,
        parse=lambda text: parse_request(text, _parse_board),
        refuse=refusal,
        run=measure,
        busy=busy,
        busy_message="Job oder Auswertung der Kalibrierung laeuft",
    )
    await add_vision_method(
        space, config, methods, "MeasureBoard", handler,
        [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
    )
