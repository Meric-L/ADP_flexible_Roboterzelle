"""ApplyHandEyeCorrection (Hand-Pi): Kameralage aus der Punkte-Pruefung uebernehmen."""

import asyncio
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..state_machine import VisionStateMachines
from .uamethods import add_vision_method, json_method, text_reply


async def _install_hand_eye_correction(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    session: Any,
    busy: Callable[[], bool],
) -> None:
    """Legt ApplyHandEyeCorrection an: eine korrigierte `T_flansch_cam` pruefen,
    die alte Datei sichern, die neue schreiben und sofort aktivieren (wie nach
    `FinishHandEyeCalibration`). Gesperrt waehrend einer Hand-Auge-Sitzung und
    wenn `busy()` (Job, Kamera-Kalibrierung, Layer-2-Lauf). Siehe
    `doc/projektdoku/arbeitsplaene/handeye-punkte-pruefung.md`.
    """
    from ..handeye_correction import (
        CorrectionRefused,
        apply_correction,
        dumps,
        parse_correction,
        refusal_result,
    )

    def refusal(code: VisionErrorCode, message: str):
        return text_reply(dumps(refusal_result(code, message)), code)

    async def apply(request, request_json: str):
        """1:ApplyHandEyeCorrection -- Kameralage korrigieren, alte Datei sichern."""
        try:
            outcome = await asyncio.to_thread(apply_correction, request, config.apriltag.hand_eye_path)
        except CorrectionRefused as refused:
            return refusal(refused.code, refused.message)
        except OSError as error:
            return refusal(VisionErrorCode.INTERNAL, f"Nicht gespeichert, die alte Kalibrierung bleibt: {error}")
        apriltag_source.apply_hand_eye(outcome.hand_eye)
        await session.forget_result(outcome.result["message"])
        return text_reply(dumps(outcome.result), VisionErrorCode.OK)

    handler = json_method(
        "ApplyHandEyeCorrection",
        states,
        parse=parse_correction,
        refuse=refusal,
        run=apply,
        busy=lambda: session.active or busy(),
        busy_message="Hand-Auge-Sitzung, Job, Kamera-Kalibrierung oder Layer-2-Lauf aktiv",
    )
    await add_vision_method(
        space, config, methods, "ApplyHandEyeCorrection", handler,
        [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
    )
