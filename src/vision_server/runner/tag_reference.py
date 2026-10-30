"""Tag-Einmessung ueber Bezugskanten (Hand-Pi): Messen, Planen, Live-Vorschau."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..profiles import _REPO_ROOT
from ..state_machine import VisionStateMachines
from .measure_tags import _Activity

_log = logging.getLogger(__package__)


async def _install_tag_reference(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    apriltag_source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
    *,
    activity: _Activity | None = None,
    blocked_by: Callable[[], bool] = lambda: False,
    annotator: Any = None,
) -> None:
    """Legt MeasureTagReference, PlanTagReferenceView und SetTagReferencePreview an: Tag-Position eines
    Moduls ueber Bezugskanten einmessen. Nur auf dem Hand-Pi (Knoten nur mit
    `hand_eye_path`). Beide antworten direkt mit dem Ergebnis; der Knoten
    `LastTagReference` ist nur zur Anzeige.

    * `MeasureTagReference` braucht frische Kamerabilder: `Operational`,
      sonst `INVALID_STATE`; `BUSY` bei Job, Kamera-Kalibrierung,
      Hand-Auge-Sitzung, `MeasureTags` oder einer weiteren Einmessung.
    * `PlanTagReferenceView` rechnet nur mit der Kalibrierung, ohne Bild:
      braucht `Operational` (erst dann ist die Kalibrierung geladen), ist aber
      nie `BUSY` -- sie stoert weder Job noch Kalibrierung noch Messung.

    * `SetTagReferencePreview(RequestJson) -> Error` schaltet die Live-Vorschau
      der Kantensuche im Livestream (Overlay-Modus `tagreference`) fuer einen
      Tag ein; `""` schaltet sie aus. `INVALID_ARGUMENT`, `INVALID_STATE`
      (nicht `Operational` oder kein Livestream-Overlay), nie `BUSY`.
      `MeasureTagReference` setzt die Vorschau selbst auf ihre Anfrage und
      schreibt, wenn `saveDebug` (Standard `true`), ein Mess-Protokoll
      (`tagreference_log.py`, `debugDir` im Ergebnis).

    Siehe `doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md`.
    """
    from ..tagreference import build_result, build_view, dumps, parse_request
    from ..tagreference_log import write_protocol

    if annotator is not None and hasattr(annotator, "show_reference_debug"):
        # Die Messung zeigt dem Livestream ihre Kantensuche Bild fuer Bild.
        apriltag_source.reference_debug_sink = annotator.show_reference_debug
    else:
        annotator = None

    activity = activity if activity is not None else _Activity()

    def reply(payload: dict, code: VisionErrorCode):
        return (
            ua.Variant(dumps(payload), ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    def refusal(code: VisionErrorCode, message: str):
        return reply(build_result(error=f"{code.name}: {message}"), code)

    def view_refusal(code: VisionErrorCode, message: str):
        return reply(build_view(error=f"{code.name}: {message}"), code)

    @uamethod
    async def measure_tag_reference(parent, request_json: str):
        """1:MeasureTagReference -- Tag-Position ueber Bezugskanten einmessen."""
        try:
            request = parse_request(request_json or "")
        except ValueError as error:
            _log.warning("MeasureTagReference abgelehnt: %s", error)
            return refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        if busy() or blocked_by():
            return refusal(
                VisionErrorCode.BUSY,
                "Job, Kalibrierung, Hand-Auge-Sitzung, MeasureTags oder Tag-Einmessung laeuft",
            )
        activity.active = True
        if annotator is not None:
            annotator.set_reference_preview(request)
            annotator.set_reference_measuring(True)
        artifacts: dict = {}
        try:
            result = await apriltag_source.measure_tag_reference(request, artifacts)
        except ValueError as error:
            # z. B. Bildgroesse passt nicht zur Kalibrierung
            _log.warning("MeasureTagReference nicht moeglich: %s", error)
            return refusal(VisionErrorCode.DETECTION_FAILED, str(error))
        finally:
            activity.active = False
            if annotator is not None:
                annotator.set_reference_measuring(False)
        directory = config.tag_reference_debug_dir
        if request.save_debug and directory is not None:
            try:
                folder = await asyncio.to_thread(
                    write_protocol,
                    directory,
                    request.tag_id,
                    request_json,
                    result,
                    artifacts.get("frames", []),
                    artifacts.get("overlay"),
                )
                try:
                    result["debugDir"] = folder.relative_to(_REPO_ROOT).as_posix()
                except ValueError:
                    result["debugDir"] = folder.as_posix()
                # result.json im Ordner kennt debugDir nicht -- nachtragen.
                await asyncio.to_thread(
                    (folder / "result.json").write_text,
                    dumps(result),
                    "utf-8",
                )
            except Exception:
                _log.warning("Mess-Protokoll nicht geschrieben", exc_info=True)
        payload = dumps(result)
        try:
            await space.last_tag_reference.write_value(ua.Variant(payload, ua.VariantType.String))
        except Exception:
            _log.exception("LastTagReference konnte nicht geschrieben werden")
        code = VisionErrorCode.DETECTION_FAILED if result["error"] else VisionErrorCode.OK
        return (
            ua.Variant(payload, ua.VariantType.String),
            ua.Variant(int(code), ua.VariantType.Int32),
        )

    @uamethod
    async def plan_tag_reference_view(parent, request_json: str):
        """1:PlanTagReferenceView -- Kamerapose im Tag-KS planen, ohne Bild."""
        try:
            request = parse_request(request_json or "")
        except ValueError as error:
            _log.warning("PlanTagReferenceView abgelehnt: %s", error)
            return view_refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return view_refusal(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        try:
            view = apriltag_source.plan_tag_reference_view(request)
        except ValueError as error:
            _log.warning("PlanTagReferenceView abgelehnt: %s", error)
            return view_refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
        return reply(view, VisionErrorCode.OK)

    @uamethod
    async def set_tag_reference_preview(parent, request_json: str):
        """1:SetTagReferencePreview -- Live-Vorschau der Kantensuche ein-/ausschalten."""

        def code(value: VisionErrorCode):
            return (ua.Variant(int(value), ua.VariantType.Int32),)

        text = request_json or ""
        request = None
        if text.strip():
            try:
                request = parse_request(text)
            except ValueError as error:
                _log.warning("SetTagReferencePreview abgelehnt: %s", error)
                return code(VisionErrorCode.INVALID_ARGUMENT)
        if states.state_names()[0] != "Operational" or annotator is None:
            return code(VisionErrorCode.INVALID_STATE)
        annotator.set_reference_preview(request)
        return code(VisionErrorCode.OK)

    methods["SetTagReferencePreview"] = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.SetTagReferencePreview", space.own_idx),
        ua.QualifiedName("SetTagReferencePreview", space.own_idx),
        set_tag_reference_preview,
        [ua.VariantType.String],
        [ua.VariantType.Int32],
    )

    for name, handler in (
        ("MeasureTagReference", measure_tag_reference),
        ("PlanTagReferenceView", plan_tag_reference_view),
    ):
        methods[name] = await space.vision_system.add_method(
            ua.NodeId(f"{config.vision_system_name}.{name}", space.own_idx),
            ua.QualifiedName(name, space.own_idx),
            handler,
            [ua.VariantType.String],
            [ua.VariantType.String, ua.VariantType.Int32],
        )
