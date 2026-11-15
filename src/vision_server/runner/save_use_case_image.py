"""SaveUseCaseImage: store a use-case photo together with its measurement and metadata."""

import asyncio
import dataclasses
import json
import logging
import math
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from .uamethods import add_vision_method, repo_relative

_log = logging.getLogger(__package__)


#: A use-case photo must be at least this much newer than the method call
#: (waits for motion blur and hand vibration to settle).
USE_CASE_MIN_FRAME_AGE_S = 0.3
#: After this many seconds without a fresh frame, `SaveUseCaseImage` gives up.
USE_CASE_FRESH_FRAME_TIMEOUT_S = 5.0
USE_CASE_POLL_INTERVAL_S = 0.05


def _load_file_content(path: Path) -> Any:
    """Return the content of a config file: parsed JSON/YAML, else text, else None."""
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
    """Return `{"path", "exists", "content"}` for a file from the config (defensive)."""
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
    """Return the Pi metadata for the JSON file; each field is guarded separately."""
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


def _reference_from_metadata(metadata_json: str) -> dict | None:
    """Return the measurement reference from the client's `MetadataJson`.

    Looks for the field `reference` with `kind` "tag". Returns
    `{"kind": "tag", "tagSizeM", "tagId", "tagXInImage"}` with validated values
    (`tagSizeM` in 0.01-0.5, otherwise 0.08; `tagXInImage` two finite numbers,
    not both zero, or None). Otherwise returns `None` (target board). Never raises.
    """
    try:
        parsed = json.loads(metadata_json or "")
    except (TypeError, ValueError):
        return None
    ref = parsed.get("reference") if isinstance(parsed, dict) else None
    if not isinstance(ref, dict) or ref.get("kind") != "tag":
        return None

    def number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) else None

    size = number(ref.get("tagSizeM"))
    if size is None or not 0.01 <= size <= 0.5:
        size = 0.08
    tag_id = ref.get("tagId")
    tag_id = tag_id if isinstance(tag_id, int) and not isinstance(tag_id, bool) else None
    axis = ref.get("tagXInImage")
    if isinstance(axis, (list, tuple)) and len(axis) == 2:
        ux, uy = number(axis[0]), number(axis[1])
        axis = [ux, uy] if ux is not None and uy is not None and (ux or uy) else None
    else:
        axis = None
    return {"kind": "tag", "tagSizeM": size, "tagId": tag_id, "tagXInImage": axis}


async def _wait_for_fresh_frame(camera_source: Any) -> Any:
    """Wait for a frame captured at least `USE_CASE_MIN_FRAME_AGE_S` after the call.

    Returns `None` if no such frame arrives within `USE_CASE_FRESH_FRAME_TIMEOUT_S`.
    """
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    deadline = t0 + USE_CASE_FRESH_FRAME_TIMEOUT_S
    while True:
        frame = camera_source.camera.latest_frame
        if frame is not None and frame.timestamp >= t0 + USE_CASE_MIN_FRAME_AGE_S:
            return frame
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(USE_CASE_POLL_INTERVAL_S)


async def _install_save_use_case_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> None:
    """Create `SaveUseCaseImage`: store a fresh full camera frame with its metadata.

    Inputs: `Series` (Int32, >= 1) and `MetadataJson` (String, from the client).
    Outputs: `FileName` (relative to the repo), `ResultJson` (image measurement as
    JSON, empty on error codes) and `Error`. It waits for a frame captured at least
    `USE_CASE_MIN_FRAME_AGE_S` after the call. Rejected during a job (like
    `SaveRawImage`). A measurement error never makes the save fail: it is stored in
    the measurement as `{"ok": false, "grund": ...}`.
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
        frame = await _wait_for_fresh_frame(camera_source)
        if frame is None:
            _log.warning("SaveUseCaseImage: kein frisches Kamerabild")
            return reply("", VisionErrorCode.INVALID_STATE)
        calibration = getattr(camera_source, "calibration", None)
        intrinsics_source = getattr(camera_source, "intrinsics_source", "calibration")
        try:
            pi_meta = _build_use_case_pi_meta(
                config, frame, frame_id, asyncio.get_running_loop().time(),
                calibration, intrinsics_source
            )
        except Exception:
            _log.exception("Use-Case-Metadaten unvollstaendig")
            pi_meta = {}
        # Measure first, then save: the measurement is then already part of the JSON,
        # which is created exclusively once, so nothing is rewritten afterwards.
        sticker_offset = await asyncio.to_thread(load_sticker_offset, base_dir)
        reference = _reference_from_metadata(metadata_json)
        measurement, annotated = await asyncio.to_thread(
            measure_for_save, frame.image, calibration, sticker_offset, reference
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
        name = repo_relative(path)
        _log.info("Use-Case-Bild gespeichert: %s", name)
        return reply(name, VisionErrorCode.OK, json.dumps(measurement, ensure_ascii=False))

    await add_vision_method(
        space, config, methods, "SaveUseCaseImage", save_use_case_image_method,
        [ua.VariantType.Int32, ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.String, ua.VariantType.Int32],
    )
