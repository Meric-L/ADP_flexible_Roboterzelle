"""Hand-Auge-Kalibrierung (Hand-Pi): vier Methoden und die Session."""

import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig

_log = logging.getLogger(__package__)


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

    method_prefix = config.vision_system_name

    @uamethod
    async def start_hand_eye(parent, options_json: str):
        """1:StartHandEyeCalibration -- Tag waehlen, Sitzung oeffnen."""
        error = await session.start(options_json or "")
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def capture_hand_eye(parent, sample_json: str):
        """1:CaptureHandEyeSample -- gemeldete Flanschpose, Tag messen."""
        error = await session.capture(sample_json or "")
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def finish_hand_eye(parent):
        """1:FinishHandEyeCalibration -- rechnen, speichern, aktivieren."""
        error = await session.finish()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    @uamethod
    async def abort_hand_eye(parent):
        """1:AbortHandEyeCalibration -- verwerfen, nichts speichern."""
        error = await session.abort()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    for name, handler, inputs in (
        ("StartHandEyeCalibration", start_hand_eye, [ua.VariantType.String]),
        ("CaptureHandEyeSample", capture_hand_eye, [ua.VariantType.String]),
        ("FinishHandEyeCalibration", finish_hand_eye, []),
        ("AbortHandEyeCalibration", abort_hand_eye, []),
    ):
        methods[name] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.{name}", space.own_idx),
            ua.QualifiedName(name, space.own_idx),
            handler,
            inputs,
            [ua.VariantType.Int32],
        )
    return session
