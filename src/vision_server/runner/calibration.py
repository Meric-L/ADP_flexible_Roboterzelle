"""Camera calibration: build the session, info node and the `StartCalibration` etc. methods."""

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from asyncua import Node, ua, uamethod

from ..calibration_session import CalibrationSession
from ..config import VisionServerConfig
from ..detection import DetectionSource
from ..errors import VisionErrorCode
from .uamethods import add_vision_method, code_reply
from .wiring import Wiring

_log = logging.getLogger(__package__)


#: Interval at which `CalibrationProgress` is written during background processing,
#: while the live stream is paused (see `_pause_stream_during_processing`). A newly
#: connected client then sees `processing: true` at the latest after this time.
CALIBRATION_PROGRESS_INTERVAL_S = 1.0


def _build_calibration_session(
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
    config: VisionServerConfig,
) -> CalibrationSession | None:
    """Build the reusable calibration session, if possible.

    Only if the `apriltag` source is open. It uses the same `SharedCamera` as the
    job and the live stream, so there is no second camera access. Also requires
    `config.apriltag` to be set. This is the case on the real Pis (see
    `vision_server/server.py`). Local development and tests without explicit
    configuration leave the feature out.
    """
    if config.apriltag is None:
        return None
    source = sources.get("apriltag")
    if source is None or not opened.get("apriltag") or getattr(source, "camera", None) is None:
        return None
    return CalibrationSession(source.camera, config.apriltag)


def _calibration_info_payload(calibration: Any, path: Any) -> dict:
    """Return a short, JSON-ready summary of the *active* calibration.

    Unlike `CalibrationSession.progress`/`last_result`, which describe the progress
    of one session, this describes what `AprilTagDetectionSource` actually uses for
    poses. It is written right after loading at start-up and after every
    interactive recalibration.

    This function reads `calibrationId`/`createdAt` back from the file rather than
    creating them again. `CameraCalibration` itself does not know `createdAt`, and
    `calibration_id` is still empty on a freshly computed object. The format only
    appears when `tagloc.calibration.save_calibration` writes it. The only
    exception is the placeholder calibration, for which no file exists.
    """
    from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

    is_placeholder = calibration.calibration_id == PLACEHOLDER_CALIBRATION_ID
    info = {
        "placeholder": is_placeholder,
        "frameId": calibration.frame_id,
        "rms": None if is_placeholder else round(calibration.rms_reprojection_error, 4),
        "samples": calibration.sample_count,
        "board": dict(calibration.board),
        "model": calibration.model,
        "calibrationId": calibration.calibration_id or None,
        "createdAt": None,
        "path": str(path),
    }
    if not is_placeholder:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            info["calibrationId"] = raw.get("calibrationId")
            info["createdAt"] = raw.get("createdAt")
        except OSError:
            pass
    return info


async def _write_calibration_info(node: Node | None, calibration: Any, path: Any) -> None:
    """Write `_calibration_info_payload` to `ActiveCalibrationInfo`.

    Errors are only logged. A node that cannot be written must not break
    start-up, or a calibration that was just saved successfully.
    """
    if node is None or calibration is None:
        return
    try:
        await node.write_value(json.dumps(_calibration_info_payload(calibration, path)))
    except Exception:
        _log.exception("ActiveCalibrationInfo konnte nicht geschrieben werden")


async def write_initial_calibration_info(wiring: Wiring) -> None:
    """Write which calibration is active, after the sources have been opened.

    This is what `open()` just loaded, either the real file or the placeholder.
    Without it, `ActiveCalibrationInfo` would stay empty until the first
    `StartCalibration`.
    """
    config = wiring.config
    if config.apriltag is not None and wiring.opened.get("apriltag"):
        await _write_calibration_info(
            wiring.space.active_calibration_info,
            getattr(wiring.apriltag_source, "_calibration", None),
            config.apriltag.calibration_path,
        )


def connect_live_calibration(wiring: Wiring) -> None:
    """Hook live adoption of a new calibration into the session, if there is one."""
    session = wiring.calibration_session
    if session is None:
        return

    async def _apply_live_calibration(calibration: Any) -> None:
        """Update detection, overlay and the info node to the new calibration at once.

        No server restart is needed. See `AprilTagDetectionSource.apply_calibration`.
        """
        source, annotator = wiring.apriltag_source, wiring.annotator
        if source is not None and hasattr(source, "apply_calibration"):
            source.apply_calibration(calibration)
        if annotator is not None and hasattr(annotator, "apply_calibration"):
            annotator.apply_calibration(calibration)
        await _write_calibration_info(
            wiring.space.active_calibration_info,
            calibration,
            wiring.config.apriltag.calibration_path,
        )

    session.set_on_calibrated(_apply_live_calibration)


async def _write_calibration_progress(wiring: Wiring) -> None:
    """Write `CalibrationProgress` directly from the session.

    Otherwise only the publish loop of the live stream writes this node
    (`CameraStreamPublisher._publish_progress`), and that loop is paused during
    processing. Without this separate write path the node would stay at the last
    state of the capture phase, and `processing: true` would never be visible.
    """
    try:
        await wiring.space.calibration_progress.write_value(
            json.dumps(wiring.calibration_session.progress)
        )
    except Exception:
        _log.exception("Kalibrier-Fortschritt konnte nicht veroeffentlicht werden")


async def _pause_stream_during_processing(wiring: Wiring) -> None:
    """Pause the live stream while `CalibrationSession._process()` runs in the background.

    This leaves the Pi the full CPU for corner detection; without the pause the
    ceiling-camera processing hung. It runs as a background task itself.
    `FinishCalibration` must not wait for it, see there.

    During this time `CalibrationProgress` is written from here instead of from the
    (paused) live stream. It is written once immediately (`processing: true`), then
    every `CALIBRATION_PROGRESS_INTERVAL_S`, and one last time with `result` before
    the live stream takes over again.
    """
    stream = wiring.camera_stream
    if stream is not None:
        await stream.stop()
    await _write_calibration_progress(wiring)
    while True:
        try:
            await asyncio.wait_for(
                wiring.calibration_session.wait_for_processing(),
                timeout=CALIBRATION_PROGRESS_INTERVAL_S,
            )
            break
        except TimeoutError:
            await _write_calibration_progress(wiring)
    await _write_calibration_progress(wiring)
    if stream is not None:
        stream.start()


def _start_calibration_method(wiring: Wiring):
    """Return the handler for `1:StartCalibration`, which starts a new session.

    Samples come only via `CaptureCalibrationSample` afterwards. No calibration
    run may run at the same time as a job or a second session.
    Both share the camera and the detector.
    """

    def busy_reason() -> str | None:
        if wiring.jobs.busy:
            return "StartCalibration waehrend laufendem Job abgelehnt"
        if wiring.layer2_active():
            return "StartCalibration waehrend laufendem Layer-2-Lauf abgelehnt"
        if wiring.hand_eye_active():
            return "StartCalibration waehrend Hand-Auge-Kalibrierung abgelehnt"
        if wiring.calibration_busy():
            # `.busy` instead of `.running`: a background evaluation from an
            # earlier `FinishCalibration` still reads from the session's
            # `_pending_dir`. A new `start()` in that window would empty the
            # directory it is reading from.
            return "StartCalibration waehrend laufender Session/Auswertung abgelehnt"
        return None

    @uamethod
    async def method(parent):
        reason = busy_reason()
        if reason is not None:
            _log.warning(reason)
            return code_reply(VisionErrorCode.BUSY)
        if not wiring.states.is_ready():
            return code_reply(VisionErrorCode.INVALID_STATE)
        wiring.calibration_session.start()
        if wiring.annotator is not None:
            wiring.annotator.set_calibration_session(wiring.calibration_session)
        _log.info("Kalibrier-Session gestartet")
        return code_reply(VisionErrorCode.OK)

    return method


def _capture_calibration_method(wiring: Wiring):
    """Return the handler for `1:CaptureCalibrationSample`, which stores the current camera image.

    No board detection happens in this call. It runs only after `FinishCalibration`
    (see `CalibrationSession.capture`). `Error=OK` only means "image saved".
    `INVALID_STATE` means no session is active, or there is no camera frame right
    now. The session keeps running, and a retry is fine.
    """

    @uamethod
    async def method(parent):
        session = wiring.calibration_session
        if not session.running:
            return code_reply(VisionErrorCode.INVALID_STATE)
        stored = await session.capture()
        error = VisionErrorCode.OK if stored else VisionErrorCode.INVALID_STATE
        return code_reply(error)

    return method


def _finish_calibration_method(wiring: Wiring):
    """Return the handler for `1:FinishCalibration`, which ends capture and starts the evaluation in the background.

    It returns at once, WITHOUT waiting for the result (see `CalibrationSession.finish()`).
    A synchronous evaluation of several full-resolution captures would carry the
    same timeout risk on the ceiling camera as a single hanging `detect_board`
    capture. The frontend follows the result only through
    `CalibrationProgress` (`processing` -> `result`).

    `Summary` is always valid JSON, also on error. On error it holds `message`
    instead of `samples`/..., so the frontend does not need to tell the success
    shape from the error shape.
    """

    @uamethod
    async def method(parent):
        session = wiring.calibration_session
        if not session.running:
            return (
                ua.Variant('{"message": "keine Session aktiv"}', ua.VariantType.String),
                ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
            )
        error, summary = await session.finish()
        # The annotator deliberately stays attached to the session in both cases
        # (no `set_calibration_session(None)` here). The live stream then keeps
        # publishing `progress` with `result`, instead of falling back to the
        # session-less state `{"running": false}`. This also holds for the
        # immediate rejection with too few captures, whose `result` would
        # otherwise never reach `CalibrationProgress`. Only the next
        # `StartCalibration`/`AbortCalibration` clears it.
        if error == VisionErrorCode.OK:
            asyncio.ensure_future(_pause_stream_during_processing(wiring))
        else:
            await _write_calibration_progress(wiring)
        return (
            ua.Variant(json.dumps(summary), ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    return method


def _abort_calibration_method(wiring: Wiring):
    """Return the handler for `1:AbortCalibration`, which stops the session without saving."""

    @uamethod
    async def method(parent):
        session = wiring.calibration_session
        if not session.running:
            return code_reply(VisionErrorCode.INVALID_STATE)
        await session.abort()
        if wiring.annotator is not None:
            wiring.annotator.set_calibration_session(None)
        _log.info("Kalibrier-Session abgebrochen")
        return code_reply(VisionErrorCode.OK)

    return method


async def install_calibration_methods(wiring: Wiring) -> None:
    """Create the four calibration methods. Only when a session and `CalibrationProgress` exist.

    The nodes go into `wiring.methods`, so they also appear under `VisionProgram`.
    """
    space = wiring.space
    if wiring.calibration_session is None or space.calibration_progress is None:
        return
    for name, handler, outputs in (
        ("StartCalibration", _start_calibration_method(wiring), [ua.VariantType.Int32]),
        ("CaptureCalibrationSample", _capture_calibration_method(wiring), [ua.VariantType.Int32]),
        (
            "FinishCalibration",
            _finish_calibration_method(wiring),
            [ua.VariantType.String, ua.VariantType.Int32],
        ),
        ("AbortCalibration", _abort_calibration_method(wiring), [ua.VariantType.Int32]),
    ):
        await add_vision_method(
            space, wiring.config, wiring.methods, name, handler, [], outputs
        )
