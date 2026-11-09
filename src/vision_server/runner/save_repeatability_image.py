"""SaveRepeatabilityImage: Bild der Wiederholgenauigkeitsmessung samt Live-Messung ablegen."""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..repeatability_image import PHASES, SESSION_RE
from .save_use_case_image import _build_use_case_pi_meta, _wait_for_fresh_frame
from .uamethods import add_vision_method, repo_relative

_log = logging.getLogger(__package__)


async def _install_save_repeatability_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> None:
    """Legt `SaveRepeatabilityImage` an.

    Eingaben `Session` (String, `^[0-9A-Za-z_-]{1,64}$`), `Phase` (`warmlauf`,
    `rauschen`, `zyklen_auto`, `zyklen_hand`) und `MetadataJson` (String, vom
    Client); Ausgaben `FileName` (relativ zum Repo), `ResultJson` (kalibrierfreie
    Live-Messung, bei Fehlercodes leer) und `Error`. Ein Messfehler laesst das
    Speichern nie scheitern.
    """
    from repeatability_eval.live import measure_for_save

    from ..repeatability_image import save_repeatability_image

    frame_id = (
        config.apriltag.frame_id if config.apriltag is not None else config.vision_system_name
    )
    base_dir = config.repeatability_dir

    def reply(name: str, code: VisionErrorCode, result_json: str = ""):
        return (
            ua.Variant(name, ua.VariantType.String),
            ua.Variant(result_json, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def save_repeatability_image_method(
        parent, session: str, phase: str, metadata_json: str
    ):
        if busy():
            _log.warning("SaveRepeatabilityImage waehrend laufendem Job abgelehnt")
            return reply("", VisionErrorCode.BUSY)
        if not isinstance(session, str) or not SESSION_RE.match(session) or phase not in PHASES:
            _log.warning("SaveRepeatabilityImage: ungueltige Session/Phase %r/%r", session, phase)
            return reply("", VisionErrorCode.INVALID_ARGUMENT)
        frame = await _wait_for_fresh_frame(camera_source)
        if frame is None:
            _log.warning("SaveRepeatabilityImage: kein frisches Kamerabild")
            return reply("", VisionErrorCode.INVALID_STATE)
        try:
            pi_meta = _build_use_case_pi_meta(
                config, frame, frame_id, asyncio.get_running_loop().time()
            )
        except Exception:
            _log.exception("Metadaten unvollstaendig")
            pi_meta = {}
        # Kamera-Controls (Belichtung/Gain) sind ueber den Frame nicht erreichbar,
        # ohne eine weitere Aufnahme auszuloesen -> bewusst nicht aufgenommen.
        measurement = await asyncio.to_thread(measure_for_save, frame.image, metadata_json or "")
        try:
            path, _n = await asyncio.to_thread(
                save_repeatability_image,
                frame.image, base_dir, session, phase, metadata_json or "", pi_meta,
                measurement,
            )
        except ValueError:
            return reply("", VisionErrorCode.INVALID_ARGUMENT)
        except Exception:
            _log.exception("Wiederholgenauigkeits-Bild konnte nicht gespeichert werden")
            return reply("", VisionErrorCode.INTERNAL)
        name = repo_relative(path)
        _log.info("Wiederholgenauigkeits-Bild gespeichert: %s", name)
        return reply(name, VisionErrorCode.OK, json.dumps(measurement, ensure_ascii=False))

    await add_vision_method(
        space, config, methods, "SaveRepeatabilityImage", save_repeatability_image_method,
        [ua.VariantType.String, ua.VariantType.String, ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.String, ua.VariantType.Int32],
    )
