"""Tag reference measurement via reference edges (hand Pi): measure, plan, live preview.

This measures the tag position of a module via reference edges. Both measuring
methods reply directly with the result; the node `LastTagReference` is for
display only.

Only on the hand Pi (nodes exist only with `hand_eye_path`).

* `MeasureTagReference` needs fresh camera frames: `Operational`, otherwise
  `INVALID_STATE`; `BUSY` during a job, camera calibration, hand-eye session,
  `MeasureTags` or another tag reference measurement.
* `PlanTagReferenceView` computes only from the calibration, without an image:
  it needs `Operational` (only then is the calibration loaded), but it is never
  `BUSY` -- it disturbs neither a job, a calibration nor a measurement.

* `SetTagReferencePreview(RequestJson) -> Error` switches the live preview of the
  edge search in the live stream (overlay mode `tagreference`) on for one tag;
  `""` switches it off. `INVALID_ARGUMENT`, `INVALID_STATE` (not `Operational`,
  or no live-stream overlay), never `BUSY`. `MeasureTagReference` sets the preview
  itself to its request and, if `saveDebug` (default `true`), writes a measurement
  log (`tagreference_log.py`, `debugDir` in the result).

See `doc/project_docs/work-plans/tag-survey-reference-edges.md`.

The numpy-dependent modules `tagreference` and `tagreference_log` are imported
only inside the functions, so the rest of the server does not need numpy.
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
    """Reply with a result or plan payload as JSON."""
    from ..tagreference import dumps

    return text_reply(dumps(payload), code)


def _refusal(code: VisionErrorCode, message: str):
    """Reply to a refused `MeasureTagReference`: an empty result with the error text."""
    from ..tagreference import build_result

    return _reply(build_result(error=f"{code.name}: {message}"), code)


def _view_refusal(code: VisionErrorCode, message: str):
    """Reply to a refused `PlanTagReferenceView`: an empty plan with the error text."""
    from ..tagreference import build_view

    return _reply(build_view(error=f"{code.name}: {message}"), code)


async def _write_protocol(config: VisionServerConfig, request, request_json: str,
                          result: dict, artifacts: dict) -> None:
    """Write the measurement log and add `debugDir` to the result.

    Only if `saveDebug` is set in the request and
    `config.tag_reference_debug_dir` is set. A write error is logged and does not
    make the measurement fail.
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
        # result.json in the folder does not know debugDir yet -- write it again.
        await asyncio.to_thread(
            (folder / "result.json").write_text,
            dumps(result),
            "utf-8",
        )
    except Exception:
        _log.warning("Mess-Protokoll nicht geschrieben", exc_info=True)


async def _measure(space, config, apriltag_source, annotator, activity, request, request_json):
    """1:MeasureTagReference -- measure the tag position via reference edges."""
    from ..tagreference import dumps

    activity.active = True
    if annotator is not None:
        annotator.set_reference_preview(request)
        annotator.set_reference_measuring(True)
    artifacts: dict = {}
    try:
        result = await apriltag_source.measure_tag_reference(request, artifacts)
    except ValueError as error:
        # e.g. image size does not match the calibration
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
    """1:PlanTagReferenceView -- plan the camera pose in the tag frame, without an image."""
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
    """Create MeasureTagReference, PlanTagReferenceView and SetTagReferencePreview.

    Only on the hand Pi (nodes exist only with `hand_eye_path`). The behavior and
    error codes of the three methods are described in the module docstring.
    """
    from ..tagreference import parse_request

    if annotator is not None and hasattr(annotator, "show_reference_debug"):
        # The measurement shows its edge search on the live stream, frame by frame.
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
        """1:SetTagReferencePreview -- switch the live preview of the edge search on or off."""
        if annotator is None:
            return code_reply(VisionErrorCode.INVALID_STATE)
        annotator.set_reference_preview(request)
        return code_reply(VisionErrorCode.OK)

    preview = json_method(
        "SetTagReferencePreview",
        states,
        # Empty is allowed and switches the preview off.
        parse=lambda text: parse_request(text) if text.strip() else None,
        refuse=lambda code, message: code_reply(code),
        run=set_preview,
    )
    await add_vision_method(
        space, config, methods, "SetTagReferencePreview", preview,
        [("OptionsJson", ua.VariantType.String, "Vorschauoptionen als JSON.")], [("Error", ua.VariantType.Int32, "Vision-Fehlercode.")],
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
            [("RequestJson", ua.VariantType.String, "Messanfrage als JSON.")], [("ResultJson", ua.VariantType.String, "Messergebnis als JSON."), ("Error", ua.VariantType.Int32, "Vision-Fehlercode.")],
        )
