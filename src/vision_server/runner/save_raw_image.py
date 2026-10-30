"""SaveRawImage: das aktuelle Kamera-Vollbild als PNG ablegen."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..profiles import _REPO_ROOT

_log = logging.getLogger(__package__)


async def _install_save_raw_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> None:
    """Legt `SaveRawImage` an: aktuelles Kamera-Vollbild als PNG speichern.

    Waehrend eines Jobs abgelehnt: das PNG-Encoding eines 12-MP-Bilds soll
    den Job-Timeout nicht gefaehrden. Siehe
    `doc/projektdoku/arbeitsplaene/rohbild-speichern.md`.
    """
    from ..raw_image import save_raw_image

    frame_id = (
        config.apriltag.frame_id if config.apriltag is not None else config.vision_system_name
    )
    directory = config.raw_image_dir

    def reply(name: str, code: VisionErrorCode):
        return (
            ua.Variant(name, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def save_raw_image_method(parent):
        if busy():
            _log.warning("SaveRawImage waehrend laufendem Job abgelehnt")
            return reply("", VisionErrorCode.BUSY)
        frame = camera_source.camera.latest_frame
        if frame is None:
            return reply("", VisionErrorCode.INVALID_STATE)
        try:
            path = await asyncio.to_thread(
                save_raw_image, frame.image, directory, frame_id
            )
        except Exception:
            _log.exception("Rohbild konnte nicht gespeichert werden")
            return reply("", VisionErrorCode.INTERNAL)
        try:
            name = path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            name = path.as_posix()  # ausserhalb des Repos: absoluter Pfad
        _log.info("Rohbild gespeichert: %s", name)
        return reply(name, VisionErrorCode.OK)

    methods["SaveRawImage"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.SaveRawImage", space.own_idx),
        ua.QualifiedName("SaveRawImage", space.own_idx),
        save_raw_image_method,
        [],
        [ua.VariantType.String, ua.VariantType.Int32],
    )
