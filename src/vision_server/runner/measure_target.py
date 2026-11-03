"""MeasureTarget: die Zielscheibe in Kamerakoordinaten messen (Hand-Pi)."""

from collections.abc import Callable
from typing import Any

from asyncua import Node, ua

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..state_machine import VisionStateMachines
from .uamethods import add_vision_method, json_method, text_reply


async def _install_measure_target(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
) -> None:
    """Legt MeasureTarget an: die Zielscheibe (Blattmarker 0-3) messen, damit
    die Steuerung den Wuerfel vor dem Absetzen ueber ihre Mitte regeln kann.
    Antwortet erst nach der Messung, das Ergebnis kommt direkt zurueck.
    Siehe `doc/projektdoku/arbeitsplaene/zielscheibe-servoing.md`.
    """
    from ..measuretarget import RESULT_SCHEMA, dumps, parse_request

    def refusal(code: VisionErrorCode, message: str):
        result = {"schema": RESULT_SCHEMA, "found": False, "error": f"{code.name}: {message}"}
        return text_reply(dumps(result), code)

    async def measure(request, request_json: str):
        """1:MeasureTarget -- Zielscheibe messen, nur in Kamerakoordinaten."""
        result = await apriltag_source.measure_target(request)
        code = VisionErrorCode.DETECTION_FAILED if result["error"] else VisionErrorCode.OK
        return text_reply(dumps(result), code)

    handler = json_method(
        "MeasureTarget",
        states,
        parse=parse_request,
        refuse=refusal,
        run=measure,
        busy=busy,
        busy_message="Job oder Kalibrierung laeuft",
    )
    await add_vision_method(
        space, config, methods, "MeasureTarget", handler,
        [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
    )
