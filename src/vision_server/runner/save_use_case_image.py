"""SaveUseCaseImage: Use-Case-Foto samt Messung und Metadaten ablegen."""

import asyncio
import dataclasses
import json
import logging
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..profiles import _REPO_ROOT

_log = logging.getLogger(__package__)


#: Ein Use-Case-Foto muss mindestens so viel juenger als der Methodenaufruf
#: sein (Bewegungsunschaerfe/Nachschwingen der Hand abwarten).
USE_CASE_MIN_FRAME_AGE_S = 0.3


#: Nach so vielen Sekunden ohne frisches Bild gibt `SaveUseCaseImage` auf.
USE_CASE_FRESH_FRAME_TIMEOUT_S = 5.0


USE_CASE_POLL_INTERVAL_S = 0.05


def _load_file_content(path: Path) -> Any:
    """Inhalt einer Konfigurationsdatei: JSON/YAML geparst, sonst Text, sonst None."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml

        return yaml.safe_load(text)
    except Exception:
        return text


def _file_meta(path: Any) -> dict[str, Any]:
    """`{"path", "exists", "content"}` zu einer Datei aus der Config (defensiv)."""
    meta: dict[str, Any] = {"path": str(path)}
    try:
        p = Path(path)
        meta["exists"] = p.is_file()
        if meta["exists"]:
            meta["content"] = _load_file_content(p)
    except Exception:
        pass
    return meta


def _build_use_case_pi_meta(
    config: VisionServerConfig,
    frame: Any,
    frame_id: str,
    now: float,
    calibration: Any = None,
    intrinsics_source: str = "calibration",
) -> dict[str, Any]:
    """Metadaten des Pi fuer die JSON-Datei; jedes Feld einzeln abgesichert."""
    meta: dict[str, Any] = {}

    def put(key: str, getter: Callable[[], Any]) -> None:
        try:
            value = getter()
        except Exception:
            return
        if value is not None:
            meta[key] = value

    put("frame_timestamp", lambda: frame.timestamp)
    put("frame_age_s", lambda: round(now - frame.timestamp, 3))
    put("image_shape", lambda: list(frame.image.shape))
    meta["frame_id"] = frame_id
    meta["vision_system_name"] = config.vision_system_name
    meta["vision_system_id"] = config.vision_system_id
    put("hostname", socket.gethostname)
    meta["endpoint"] = config.endpoint
    put(
        "camera_stream",
        lambda: dataclasses.asdict(config.camera_stream)
        if dataclasses.is_dataclass(config.camera_stream)
        else None,
    )
    if calibration is not None:
        from use_case_eval.live import intrinsics_meta

        put("intrinsics", lambda: intrinsics_meta(calibration, intrinsics_source))
    tag = config.apriltag
    if tag is not None:
        put("apriltag", lambda: dataclasses.asdict(tag))
        put("calibration", lambda: _file_meta(tag.calibration_path))
        if tag.hand_eye_path is not None:
            put("hand_eye", lambda: _file_meta(tag.hand_eye_path))
    return meta


async def _install_save_use_case_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> None:
    """Legt `SaveUseCaseImage` an: frisches Kamera-Vollbild + Metadaten ablegen.

    Eingaben `Series` (Int32, >= 1) und `MetadataJson` (String, vom Client);
    Ausgaben `FileName` (relativ zum Repo), `ResultJson` (Bildmessung als JSON,
    bei Fehler-Codes leer) und `Error`. Wartet auf ein Bild, das mindestens
    `USE_CASE_MIN_FRAME_AGE_S` nach dem Aufruf entstand. Waehrend eines Jobs
    abgelehnt (wie `SaveRawImage`). Ein Messfehler laesst das Speichern nie
    scheitern: er steht als `{"ok": false, "grund": ...}` in der Messung.
    """
    from use_case_eval.live import load_sticker_offset, measure_for_save

    from ..use_case_image import save_use_case_image

    frame_id = (
        config.apriltag.frame_id if config.apriltag is not None else config.vision_system_name
    )
    base_dir = config.use_case_dir

    def reply(name: str, code: VisionErrorCode, result_json: str = ""):
        return (
            ua.Variant(name, ua.VariantType.String),
            ua.Variant(result_json, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def save_use_case_image_method(parent, series: int, metadata_json: str):
        if busy():
            _log.warning("SaveUseCaseImage waehrend laufendem Job abgelehnt")
            return reply("", VisionErrorCode.BUSY)
        if series < 1:
            _log.warning("SaveUseCaseImage: ungueltige Reihe %s", series)
            return reply("", VisionErrorCode.INVALID_ARGUMENT)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        deadline = t0 + USE_CASE_FRESH_FRAME_TIMEOUT_S
        frame = None
        while True:
            frame = camera_source.camera.latest_frame
            if frame is not None and frame.timestamp >= t0 + USE_CASE_MIN_FRAME_AGE_S:
                break
            if loop.time() >= deadline:
                _log.warning("SaveUseCaseImage: kein frisches Kamerabild")
                return reply("", VisionErrorCode.INVALID_STATE)
            await asyncio.sleep(USE_CASE_POLL_INTERVAL_S)
        calibration = getattr(camera_source, "calibration", None)
        intrinsics_source = getattr(camera_source, "intrinsics_source", "calibration")
        try:
            pi_meta = _build_use_case_pi_meta(
                config, frame, frame_id, loop.time(), calibration, intrinsics_source
            )
        except Exception:
            _log.exception("Use-Case-Metadaten unvollstaendig")
            pi_meta = {}
        # Erst messen, dann speichern: die Messung steht so schon in der einmalig
        # exklusiv angelegten JSON, nichts wird nachtraeglich umgeschrieben.
        sticker_offset = await asyncio.to_thread(load_sticker_offset, base_dir)
        measurement, annotated = await asyncio.to_thread(
            measure_for_save, frame.image, calibration, sticker_offset
        )
        try:
            path, _trial = await asyncio.to_thread(
                save_use_case_image,
                frame.image, base_dir, series, metadata_json or "", pi_meta,
                None, measurement, annotated,
            )
        except Exception:
            _log.exception("Use-Case-Bild konnte nicht gespeichert werden")
            return reply("", VisionErrorCode.INTERNAL)
        try:
            name = path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            name = path.as_posix()  # ausserhalb des Repos: absoluter Pfad
        _log.info("Use-Case-Bild gespeichert: %s", name)
        return reply(name, VisionErrorCode.OK, json.dumps(measurement, ensure_ascii=False))

    methods["SaveUseCaseImage"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.SaveUseCaseImage", space.own_idx),
        ua.QualifiedName("SaveUseCaseImage", space.own_idx),
        save_use_case_image_method,
        [ua.VariantType.Int32, ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.String, ua.VariantType.Int32],
    )
