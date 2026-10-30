"""Hand-Auge-Kalibrierung (Hand-Pi): vier Methoden und die Session."""

from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from .uamethods import add_vision_method, code_reply


async def _install_hand_eye_session(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> Any:
    """Legt die Hand-Auge-Methoden an und verdrahtet die Session.

    Nur auf dem Hand-Pi (Knoten nur mit `hand_eye_path`). Siehe
    `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`.
    """
    # Erst hier: die Session braucht numpy, der Rest des Servers nicht.
    from ..handeye_session import HandEyePorts, HandEyeSession

    async def publish(status: str) -> None:
        await space.hand_eye_status.write_value(ua.Variant(status, ua.VariantType.String))

    session = HandEyeSession(
        HandEyePorts(
            measure_tag=apriltag_source.measure_tag,
            measure_board=getattr(apriltag_source, "measure_board", None),
            intrinsics_source=lambda: getattr(apriltag_source, "intrinsics_source", None),
            busy=busy,
            path=config.apriltag.hand_eye_path,
            apply=apriltag_source.apply_hand_eye,
            publish=publish,
            frame_id=config.apriltag.frame_id,
        )
    )
    await session.publish_idle()

    @uamethod
    async def start_hand_eye(parent, options_json: str):
        """1:StartHandEyeCalibration -- Tag waehlen, Sitzung oeffnen."""
        return code_reply(await session.start(options_json or ""))

    @uamethod
    async def capture_hand_eye(parent, sample_json: str):
        """1:CaptureHandEyeSample -- gemeldete Flanschpose, Tag messen."""
        return code_reply(await session.capture(sample_json or ""))

    @uamethod
    async def finish_hand_eye(parent):
        """1:FinishHandEyeCalibration -- rechnen, speichern, aktivieren."""
        return code_reply(await session.finish())

    @uamethod
    async def abort_hand_eye(parent):
        """1:AbortHandEyeCalibration -- verwerfen, nichts speichern."""
        return code_reply(await session.abort())

    for name, handler, inputs in (
        ("StartHandEyeCalibration", start_hand_eye, [ua.VariantType.String]),
        ("CaptureHandEyeSample", capture_hand_eye, [ua.VariantType.String]),
        ("FinishHandEyeCalibration", finish_hand_eye, []),
        ("AbortHandEyeCalibration", abort_hand_eye, []),
    ):
        await add_vision_method(
            space, config, methods, name, handler, inputs, [ua.VariantType.Int32]
        )
    return session
