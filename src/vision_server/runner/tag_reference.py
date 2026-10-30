"""Tag-Einmessung ueber Bezugskanten (Hand-Pi): Messen, Planen, Live-Vorschau.

Damit wird die Tag-Position eines Moduls ueber Bezugskanten eingemessen. Beide
Messmethoden antworten direkt mit dem Ergebnis; der Knoten `LastTagReference` ist
nur zur Anzeige.

Nur auf dem Hand-Pi (Knoten nur mit `hand_eye_path`).

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

Die numpy-abhaengigen Module `tagreference` und `tagreference_log` werden erst
in den Funktionen importiert, der Rest des Servers braucht numpy nicht.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node, ua

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..profiles import _REPO_ROOT
from ..state_machine import VisionStateMachines
from .measure_tags import _Activity
from .uamethods import add_vision_method, code_reply, json_method, text_reply

_log = logging.getLogger(__package__)


def _reply(payload: dict, code: VisionErrorCode):
    """Antwort mit einem Ergebnis- oder Planungs-Payload als JSON."""
    from ..tagreference import dumps

    return text_reply(dumps(payload), code)


def _refusal(code: VisionErrorCode, message: str):
    """Abgelehnte `MeasureTagReference`: leeres Ergebnis mit Fehlertext."""
    from ..tagreference import build_result

    return _reply(build_result(error=f"{code.name}: {message}"), code)


def _view_refusal(code: VisionErrorCode, message: str):
    """Abgelehnte `PlanTagReferenceView`: leere Planung mit Fehlertext."""
    from ..tagreference import build_view

    return _reply(build_view(error=f"{code.name}: {message}"), code)


async def _write_protocol(config: VisionServerConfig, request, request_json: str,
                          result: dict, artifacts: dict) -> None:
    """Schreibt das Mess-Protokoll und traegt `debugDir` ins Ergebnis nach.

    Nur mit `saveDebug` in der Anfrage und gesetztem
    `config.tag_reference_debug_dir`. Ein Fehler beim Schreiben landet im Log
    und laesst die Messung nicht scheitern.
    """
    from ..tagreference import dumps
    from ..tagreference_log import write_protocol

    directory = config.tag_reference_debug_dir
    if not (request.save_debug and directory is not None):
        return
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


async def _measure(space, config, apriltag_source, annotator, activity, request, request_json):
    """1:MeasureTagReference -- Tag-Position ueber Bezugskanten einmessen."""
    from ..tagreference import dumps

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
        return _refusal(VisionErrorCode.DETECTION_FAILED, str(error))
    finally:
        activity.active = False
        if annotator is not None:
            annotator.set_reference_measuring(False)
    await _write_protocol(config, request, request_json, result, artifacts)
    payload = dumps(result)
    try:
        await space.last_tag_reference.write_value(ua.Variant(payload, ua.VariantType.String))
    except Exception:
        _log.exception("LastTagReference konnte nicht geschrieben werden")
    code = VisionErrorCode.DETECTION_FAILED if result["error"] else VisionErrorCode.OK
    return text_reply(payload, code)


async def _plan(apriltag_source, request):
    """1:PlanTagReferenceView -- Kamerapose im Tag-KS planen, ohne Bild."""
    try:
        view = apriltag_source.plan_tag_reference_view(request)
    except ValueError as error:
        _log.warning("PlanTagReferenceView abgelehnt: %s", error)
        return _view_refusal(VisionErrorCode.INVALID_ARGUMENT, str(error))
    return _reply(view, VisionErrorCode.OK)


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
    """Legt MeasureTagReference, PlanTagReferenceView und SetTagReferencePreview an.

    Nur auf dem Hand-Pi (Knoten nur mit `hand_eye_path`). Verhalten und Fehlercodes
    der drei Methoden stehen im Modul-Docstring.
    """
    from ..tagreference import parse_request

    if annotator is not None and hasattr(annotator, "show_reference_debug"):
        # Die Messung zeigt dem Livestream ihre Kantensuche Bild fuer Bild.
        apriltag_source.reference_debug_sink = annotator.show_reference_debug
    else:
        annotator = None

    activity = activity if activity is not None else _Activity()

    async def measure(request, request_json: str):
        return await _measure(
            space, config, apriltag_source, annotator, activity, request, request_json
        )

    async def plan(request, request_json: str):
        return await _plan(apriltag_source, request)

    async def set_preview(request, request_json: str):
        """1:SetTagReferencePreview -- Live-Vorschau der Kantensuche ein-/ausschalten."""
        if annotator is None:
            return code_reply(VisionErrorCode.INVALID_STATE)
        annotator.set_reference_preview(request)
        return code_reply(VisionErrorCode.OK)

    preview = json_method(
        "SetTagReferencePreview",
        states,
        # Leer ist erlaubt und schaltet die Vorschau aus.
        parse=lambda text: parse_request(text) if text.strip() else None,
        refuse=lambda code, message: code_reply(code),
        run=set_preview,
    )
    await add_vision_method(
        space, config, methods, "SetTagReferencePreview", preview,
        [ua.VariantType.String], [ua.VariantType.Int32],
    )

    handlers = (
        ("MeasureTagReference", json_method(
            "MeasureTagReference",
            states,
            parse=parse_request,
            refuse=_refusal,
            run=measure,
            busy=lambda: busy() or blocked_by(),
            busy_message=(
                "Job, Kalibrierung, Hand-Auge-Sitzung, MeasureTags oder Tag-Einmessung laeuft"
            ),
        )),
        ("PlanTagReferenceView", json_method(
            "PlanTagReferenceView",
            states,
            parse=parse_request,
            refuse=_view_refusal,
            run=plan,
        )),
    )
    for name, handler in handlers:
        await add_vision_method(
            space, config, methods, name, handler,
            [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
        )
