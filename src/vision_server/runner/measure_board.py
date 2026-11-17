"""MeasureBoard: measure the calibration chessboard in camera coordinates."""

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
    """Register the MeasureBoard method: measure the calibration chessboard.

    This lets the controller centre the board before the hand-eye capture and
    check the ceiling camera's calibration run before each capture. It runs on
    both Pis. The method replies only after the measurement, and the result is
    returned directly.
    See `doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`.
    """
    from ..handeye_session import _parse_board
    from ..measureboard import RESULT_SCHEMA, dumps, parse_request

    def refusal(code: VisionErrorCode, message: str):
        result = {"schema": RESULT_SCHEMA, "found": False, "error": code.name, "message": message}
        return text_reply(dumps(result), code)

    async def measure(request, request_json: str):
        """1:MeasureBoard -- measure the board, in camera coordinates only."""
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
        [("RequestJson", ua.VariantType.String, "Messanfrage als JSON.")], [("ResultJson", ua.VariantType.String, "Messergebnis als JSON."), ("Error", ua.VariantType.Int32, "Vision-Fehlercode.")],
    )
