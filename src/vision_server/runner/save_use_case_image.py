"""SaveImage (und veraltet SaveUseCaseImage): Foto samt Messung und Metadaten ablegen."""

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
from ..config import ImageKind, VisionServerConfig
from ..errors import VisionErrorCode
from .uamethods import add_vision_method, repo_relative

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


async def _wait_for_fresh_frame(camera_source: Any) -> Any:
    """Wartet auf ein Bild, das mindestens `USE_CASE_MIN_FRAME_AGE_S` nach dem Aufruf entstand.

    Gibt `None` zurueck, wenn nach `USE_CASE_FRESH_FRAME_TIMEOUT_S` keines kam.
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


def _usecase_kind(config: VisionServerConfig) -> ImageKind | None:
    """Zweck "usecase" der Konfiguration; ohne `image_kinds` (alte Config) aus `use_case_dir`."""
    kinds = getattr(config, "image_kinds", None)
    if kinds is not None:
        return kinds.get("usecase")
    base_dir = getattr(config, "use_case_dir", None)
    if base_dir is None:
        return None
    return ImageKind(
        base_dir=Path(base_dir), folder="reihe", stem="versuch", series_key="reihe",
        number_key="versuch", schema="adp.usecase.trial/1", measure="trial",
    )


def _make_save(
    config: VisionServerConfig,
    camera_source: Any,
    busy: Callable[[], bool],
) -> Callable[..., Any]:
    """Baut die gemeinsame Speicherfunktion von `SaveImage` und `SaveUseCaseImage`.

    `save(kind_name, series, metadata_json, label, min_series=0)` liefert das
    OPC-UA-Antworttupel `(FileName, ResultJson, Error)`.

    Verhalten von `SaveImage(Kind, Series, MetadataJson)`:

    frisches Kamera-Vollbild samt Messung und Metadaten ablegen. `Kind` ist ein
    Schluessel aus `config.image_kinds` (unbekannt: INVALID_ARGUMENT), `Series`
    `0` legt die naechste freie Reihe an, `>= 1` nimmt diese, negativ:
    INVALID_ARGUMENT. `FileName` ist relativ zum Repo, `ResultJson` die Messung
    des Zwecks (bei Fehler-Codes leer). Wartet auf ein Bild, das mindestens
    `USE_CASE_MIN_FRAME_AGE_S` nach dem Aufruf entstand. Waehrend eines Jobs
    abgelehnt (wie `SaveRawImage`). Ein Messfehler laesst das Speichern nie
    scheitern: er steht als `{"ok": false, "grund": ...}` in der Messung.
    """
    from use_case_eval.live import load_sticker_offset, measure_for_save, measure_sheet_for_save

    from ..use_case_image import next_series_number, save_use_case_image

    frame_id = (
        config.apriltag.frame_id if config.apriltag is not None else config.vision_system_name
    )

    def reply(name: str, code: VisionErrorCode, result_json: str = ""):
        return (
            ua.Variant(name, ua.VariantType.String),
            ua.Variant(result_json, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    async def save(kind_name: str, series: int, metadata_json: str, label: str, min_series: int = 0):
        if busy():
            _log.warning("%s waehrend laufendem Job abgelehnt", label)
            return reply("", VisionErrorCode.BUSY)
        kind = (
            _usecase_kind(config)
            if kind_name == "usecase"
            else getattr(config, "image_kinds", {}).get(kind_name)
        )
        if kind is None:
            _log.warning("%s: unbekannter Zweck %r", label, kind_name)
            return reply("", VisionErrorCode.INVALID_ARGUMENT)
        if series < min_series:
            _log.warning("%s: ungueltige Reihe %s", label, series)
            return reply("", VisionErrorCode.INVALID_ARGUMENT)
        frame = await _wait_for_fresh_frame(camera_source)
        if frame is None:
            _log.warning("%s: kein frisches Kamerabild", label)
            return reply("", VisionErrorCode.INVALID_STATE)
        calibration = getattr(camera_source, "calibration", None)
        intrinsics_source = getattr(camera_source, "intrinsics_source", "calibration")
        try:
            pi_meta = _build_use_case_pi_meta(
                config, frame, frame_id, asyncio.get_running_loop().time(),
                calibration, intrinsics_source
            )
        except Exception:
            _log.exception("Bild-Metadaten unvollstaendig")
            pi_meta = {}
        # Erst messen, dann speichern: die Messung steht so schon in der einmalig
        # exklusiv angelegten JSON, nichts wird nachtraeglich umgeschrieben.
        measurement: dict | None = None
        annotated = None
        if kind.measure == "trial":
            sticker_offset = await asyncio.to_thread(load_sticker_offset, kind.base_dir)
            measurement, annotated = await asyncio.to_thread(
                measure_for_save, frame.image, calibration, sticker_offset
            )
        elif kind.measure == "sheet":
            measurement = await asyncio.to_thread(measure_sheet_for_save, frame.image, calibration)
        try:
            if series == 0:
                series = await asyncio.to_thread(next_series_number, kind.base_dir, kind.folder)
            path, _number = await asyncio.to_thread(
                save_use_case_image,
                frame.image, kind.base_dir, series, metadata_json or "", pi_meta,
                None, measurement, annotated,
                folder=kind.folder, stem=kind.stem, series_key=kind.series_key,
                number_key=kind.number_key, schema=kind.schema,
            )
        except Exception:
            _log.exception("Bild konnte nicht gespeichert werden")
            return reply("", VisionErrorCode.INTERNAL)
        name = repo_relative(path)
        _log.info("Bild gespeichert (%s): %s", kind_name, name)
        return reply(name, VisionErrorCode.OK, json.dumps(measurement or {}, ensure_ascii=False))

    return save


async def _install_save_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
) -> Callable[..., Any]:
    """Legt `SaveImage(Kind, Series, MetadataJson) -> (FileName, ResultJson, Error)` an.

    Liefert die gemeinsame Speicherfunktion, die `SaveUseCaseImage` mitbenutzt.
    """
    save = _make_save(config, camera_source, busy)

    @uamethod
    async def save_image_method(parent, kind: str, series: int, metadata_json: str):
        return await save(kind, series, metadata_json, "SaveImage")

    await add_vision_method(
        space, config, methods, "SaveImage", save_image_method,
        [ua.VariantType.String, ua.VariantType.Int32, ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.String, ua.VariantType.Int32],
    )
    return save


async def _install_save_use_case_image(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    camera_source: Any,
    methods: dict[str, Node],
    busy: Callable[[], bool],
    save: Callable[..., Any] | None = None,
) -> None:
    """Legt die veraltete Methode `SaveUseCaseImage(Series, MetadataJson)` an.

    Leitet an dieselbe Speicherfunktion wie `SaveImage` mit Zweck "usecase"
    weiter; `Series` muss weiterhin `>= 1` sein. Ohne `save` wird eine eigene
    Speicherfunktion aufgebaut (ohne `SaveImage` zu registrieren).
    """
    if save is None:
        save = _make_save(config, camera_source, busy)

    @uamethod
    async def save_use_case_image_method(parent, series: int, metadata_json: str):
        return await save("usecase", series, metadata_json, "SaveUseCaseImage", 1)

    await add_vision_method(
        space, config, methods, "SaveUseCaseImage", save_use_case_image_method,
        [ua.VariantType.Int32, ua.VariantType.String],
        [ua.VariantType.String, ua.VariantType.String, ua.VariantType.Int32],
    )
