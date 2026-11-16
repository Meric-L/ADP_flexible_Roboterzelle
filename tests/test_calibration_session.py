"""Tests for `CalibrationSession`: no camera. The board/calibration functions from
`tagloc.boards`/`tagloc.calibration` are injected (pattern from
`test_apriltag_source.py`). `tagloc.frames.image_size`/`to_gray` run for real (they
need numpy, and `to_gray` also cv2 for a 3-channel image), so a fake "image" must
be a real array, not a placeholder.

Capture and evaluation are decoupled (see the module docstring of
`calibration_session.py`): `capture()` only stores the frame, with no
`detect_board` in the RPC path. The actual detection runs in `_process()`,
started by `finish()` and running in the background. Tests that check the result
therefore always call `await session.wait_for_processing()` after `finish()`
before reading `session.progress["result"]`.

`start()` really creates and empties a working folder on disk (`_pending_dir`),
so `FAST_CONFIG` points at a temp directory, never at the default path in the
repo (`data/calibration/...`).
"""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace

try:
    import numpy as np
except Exception:  # numpy broken in this environment
    np = None

from vision_server.calibration_session import (
    RMS_WARNING_PX,
    CalibrationSession,
    _save_capture_image,
)
from vision_server.errors import VisionErrorCode
from vision_server.profiles import AprilTagProfileConfig

#: `start()` really creates and empties `_pending_dir` (derived from
#: `calibration_path`) on disk; never point it at the repo default path.
_TMP_DIR = tempfile.mkdtemp(prefix="calibration_session_test_")

FAST_CONFIG = AprilTagProfileConfig(
    calibration_min_samples=2,
    calibration_path=Path(_TMP_DIR) / "camera.json",
)

#: Small real image: `tagloc.frames.image_size`/`to_gray` are not injected and
#: need a real array, not a string placeholder.
_FRAME = np.zeros((4, 4, 3), dtype=np.uint8) if np is not None else None

#: Fixed clock for the archive tests: session folder `20261110T091500Z`.
_T0 = datetime(2026, 11, 10, 9, 15, 0, tzinfo=timezone.utc)


class FakeCamera:
    """Provides `latest_frame` without opening a real camera."""

    def __init__(self) -> None:
        self.latest_frame = None

    def push(self) -> None:
        self.latest_frame = SimpleNamespace(image=_FRAME, timestamp=0.0)


class FakeSample:
    """Stands for `tagloc.boards.BoardSample`."""

    def __init__(self, corners: int = 20) -> None:
        self._corners = corners

    def count(self) -> int:
        return self._corners


def fake_detect_board(gray, spec, board):
    """Always finds the board; tests steer via the captures, not via a detection miss."""
    return FakeSample()


def fake_detect_board_never(gray, spec, board):
    """Never finds the board; for the failure test."""
    return None


def fake_build_board(spec):
    return None  # Chessboard braucht kein Board-Objekt


def fake_compute_coverage(samples, image_size):
    return (0.42, 0.37)


def make_session(config=FAST_CONFIG, **overrides):
    """Builds a `CalibrationSession` with faked `tagloc` functions and an in-memory
    fake "file system" for `save_capture_image`/`load_capture_image`. `_process()`
    then reads back exactly what `capture()` previously "saved", without touching
    real files."""
    camera = FakeCamera()
    calibrate_calls: list = []
    save_calls: list = []
    stored_images: dict = {}

    def fake_calibrate_from_samples(samples, image_size, spec, board, *, frame_id, model):
        calibrate_calls.append((len(samples), frame_id, model))
        return SimpleNamespace(
            rms_reprojection_error=0.1234,
            sample_count=len(samples),
            model=model,
        )

    def fake_save_calibration(path, calibration):
        save_calls.append((path, calibration))

    def fake_save_capture_image(path, image):
        stored_images[path] = image

    def fake_load_capture_image(path):
        return stored_images.get(path)

    kwargs = dict(
        detect_board=fake_detect_board,
        build_board=fake_build_board,
        calibrate_from_samples=fake_calibrate_from_samples,
        compute_coverage=fake_compute_coverage,
        save_calibration=fake_save_calibration,
        save_capture_image=fake_save_capture_image,
        load_capture_image=fake_load_capture_image,
    )
    kwargs.update(overrides)
    session = CalibrationSession(camera, config, **kwargs)
    return session, camera, calibrate_calls, save_calls


async def _capture_n(session, camera, n: int) -> None:
    """Takes `n` captures and waits until they are all "saved"; `_process()` needs
    that (see `wait_for_pending_saves` there)."""
    for _ in range(n):
        camera.push()
        await session.capture()
    await session.wait_for_pending_saves()


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class CaptureTest(unittest.IsolatedAsyncioTestCase):
    async def test_capture_records_a_sample_when_a_frame_is_available(self):
        session, camera, _, _ = make_session()
        session.start()
        camera.push()

        found = await session.capture()

        self.assertTrue(found)
        self.assertEqual(session.progress["samples"], 1)

    async def test_capture_without_a_frame_returns_false(self):
        session, _camera, _, _ = make_session()
        session.start()

        found = await session.capture()

        self.assertFalse(found)
        self.assertEqual(session.progress["samples"], 0)

    async def test_capture_before_start_returns_false(self):
        session, camera, _, _ = make_session()
        camera.push()

        found = await session.capture()

        self.assertFalse(found)

    async def test_capture_succeeds_even_if_the_board_would_not_be_found_later(self):
        """Since capture and evaluation were decoupled, `capture()` no longer checks
        whether the board is visible; that is decided only in `_process()`.
        `detect_board` is deliberately never called here."""
        session, camera, _, _ = make_session(detect_board=fake_detect_board_never)
        session.start()
        camera.push()

        found = await session.capture()

        self.assertTrue(found)
        self.assertEqual(session.progress["samples"], 1)

    async def test_repeated_captures_accumulate_samples(self):
        session, camera, _, _ = make_session()
        session.start()
        camera.push()

        for _ in range(3):
            await session.capture()

        self.assertEqual(session.progress["samples"], 3)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class SaveCaptureImageTest(unittest.IsolatedAsyncioTestCase):
    """Every capture always lands in the session's own `_pending_dir` (capture and
    evaluation depend on it), and additionally in the optional
    `calibration_capture_dir` archive, if configured."""

    async def test_saves_each_capture_into_the_pending_dir(self):
        saved_calls: list = []

        def fake_save_capture_image(path, image):
            saved_calls.append((path, image))

        session, camera, _, _ = make_session(save_capture_image=fake_save_capture_image)
        session.start()
        camera.push()

        await session.capture()
        await session.capture()
        await session.wait_for_pending_saves()

        self.assertEqual(len(saved_calls), 2)
        self.assertEqual(saved_calls[0][0].name, "kalib_001.jpg")
        self.assertEqual(saved_calls[1][0].name, "kalib_002.jpg")
        self.assertIs(saved_calls[0][1], _FRAME)

    async def test_also_archives_when_a_capture_dir_is_configured(self):
        saved_calls: list = []

        def fake_save_capture_image(path, image):
            saved_calls.append(path)

        archive = Path(tempfile.mkdtemp(prefix="calib_archive_test_"))
        config = replace(FAST_CONFIG, calibration_capture_dir=archive)
        session, camera, _, _ = make_session(
            config, save_capture_image=fake_save_capture_image, now=lambda: _T0
        )
        session.start()
        camera.push()

        await session.capture()
        await session.wait_for_pending_saves()

        # Once into the pending folder (JPEG), once into the session archive (PNG).
        self.assertEqual(len(saved_calls), 2)
        archive_paths = [p for p in saved_calls if p.parent == archive / "20261110T091500Z"]
        self.assertEqual(len(archive_paths), 1)
        self.assertEqual(archive_paths[0].name, "kalib_001.png")
        pending = [p for p in saved_calls if p not in archive_paths]
        self.assertEqual(pending[0].name, "kalib_001.jpg")

    async def test_counter_resets_on_a_new_start(self):
        saved_calls: list = []

        def fake_save_capture_image(path, image):
            saved_calls.append(path)

        session, camera, _, _ = make_session(save_capture_image=fake_save_capture_image)
        session.start()
        camera.push()
        await session.capture()
        await session.abort()

        session.start()
        await session.capture()
        await session.wait_for_pending_saves()

        self.assertEqual(saved_calls[-1].name, "kalib_001.jpg")

    async def test_a_slow_save_does_not_block_the_next_capture(self):
        """Regression test for the queue bug found live on the ceiling camera:
        `_schedule_capture_save` initially shared `_pool()` with `detect_board`, so
        a save still running made every further `capture()` wait for it. With
        `_save_pool()` (own pool) a hanging first save must not hold up a second
        capture."""
        import threading

        save_started = threading.Event()
        release_save = threading.Event()

        def slow_save(path, image):
            save_started.set()
            release_save.wait(timeout=2)

        session, camera, _, _ = make_session(save_capture_image=slow_save)
        session.start()
        camera.push()

        await session.capture()
        # Blocking on `save_started` here would freeze the event loop itself (same
        # thread) and never let the background task run; instead poll briefly and
        # hand control back to the loop each time.
        for _ in range(200):
            if save_started.is_set():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(save_started.is_set(), "erste Speicherung nie gestartet")

        found = await asyncio.wait_for(session.capture(), timeout=1.0)

        self.assertTrue(found)
        self.assertEqual(session.progress["samples"], 2)
        release_save.set()
        await session.wait_for_pending_saves()


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_reports_running_min_samples_and_processing(self):
        session, camera, _, _ = make_session()
        session.start()
        camera.push()
        await session.capture()

        progress = session.progress

        self.assertTrue(progress["running"])
        self.assertFalse(progress["processing"])
        self.assertEqual(progress["samples"], 1)
        self.assertEqual(progress["minSamples"], 2)

    async def test_running_is_false_before_start_and_after_abort(self):
        session, _camera, _, _ = make_session()
        self.assertFalse(session.progress["running"])
        session.start()
        self.assertTrue(session.progress["running"])
        await session.abort()
        self.assertFalse(session.progress["running"])

    async def test_running_becomes_false_once_finish_is_called(self):
        """`finish()` ends the capture phase at once, even if the evaluation itself is
        still running in the background."""
        session, camera, _, _ = make_session()
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()

        self.assertFalse(session.progress["running"])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class BusyTest(unittest.IsolatedAsyncioTestCase):
    """`busy` (capture OR evaluation) protects `StartCalibration` from clearing the
    `_pending_dir` of a still-running evaluation."""

    async def test_busy_while_running(self):
        session, _camera, _, _ = make_session()
        self.assertFalse(session.busy)
        session.start()
        self.assertTrue(session.busy)

    async def test_busy_while_processing_even_after_running_is_false(self):
        import threading

        release = threading.Event()

        def blocking_detect_board(gray, spec, board):
            release.wait(timeout=5)
            return FakeSample()

        session, camera, _, _ = make_session(detect_board=blocking_detect_board)
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()

        self.assertFalse(session.running)
        self.assertTrue(session.busy)  # evaluation still running
        self.assertTrue(session.processing)
        release.set()
        await session.wait_for_processing()
        self.assertFalse(session.busy)
        self.assertFalse(session.processing)

    async def test_not_processing_while_capturing(self):
        """MeasureBoard may measure in the capture phase, just not during the evaluation."""
        session, camera, _, _ = make_session()
        session.start()
        await _capture_n(session, camera, 2)
        self.assertTrue(session.busy)
        self.assertFalse(session.processing)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class FinishTest(unittest.IsolatedAsyncioTestCase):
    async def test_finish_returns_immediately_without_waiting_for_processing(self):
        """The actual reason for the decoupling: `finish()` must not wait for the
        evaluation. Otherwise a slow or hanging evaluation over several full-resolution
        captures would carry the same timeout risk as a single hanging `detect_board` call."""
        import threading

        release = threading.Event()

        def blocking_detect_board(gray, spec, board):
            release.wait(timeout=5)
            return FakeSample()

        session, camera, _, _ = make_session(detect_board=blocking_detect_board)
        session.start()
        await _capture_n(session, camera, 3)

        error, summary = await asyncio.wait_for(session.finish(), timeout=1.0)

        self.assertEqual(error, VisionErrorCode.OK)
        self.assertEqual(summary["samples"], 3)
        self.assertIsNone(session.progress.get("result"))
        self.assertTrue(session.progress["processing"])
        release.set()
        await session.wait_for_processing()

    async def test_new_calibration_keeps_the_old_file_as_backup(self):
        folder = Path(tempfile.mkdtemp(prefix="calibration_backup_test_"))
        out = folder / "cam.json"
        out.write_text('{"calibrationId": "alt"}', encoding="utf-8")
        clock = lambda: datetime(2026, 11, 15, 9, 30, 0, tzinfo=timezone.utc)
        for _ in range(2):  # twice in the same second: two separate backups
            session, camera, _, save_calls = make_session(
                config=replace(FAST_CONFIG, calibration_path=out), now=clock)
            session.start()
            await _capture_n(session, camera, 3)
            await session.finish()
            await session.wait_for_processing()
            self.assertEqual(len(save_calls), 1)
            self.assertEqual(save_calls[0][0], out)

        result = session.progress["result"]
        backups = sorted(p.name for p in folder.glob("cam.json.*.bak"))
        self.assertEqual(backups, ["cam.json.20261115T093000Z-1.bak", "cam.json.20261115T093000Z.bak"])
        self.assertEqual(result["backup"], str(folder / "cam.json.20261115T093000Z-1.bak"))
        self.assertEqual((folder / backups[1]).read_text(encoding="utf-8"), '{"calibrationId": "alt"}')

    async def test_first_calibration_has_no_backup(self):
        folder = Path(tempfile.mkdtemp(prefix="calibration_backup_test_"))
        session, camera, _, _ = make_session(config=replace(FAST_CONFIG, calibration_path=folder / "cam.json"))
        session.start()
        await _capture_n(session, camera, 3)
        await session.finish()
        await session.wait_for_processing()

        self.assertNotIn("backup", session.progress["result"])
        self.assertEqual(list(folder.glob("*.bak")), [])

    async def test_ok_and_saves_with_enough_samples(self):
        session, camera, calibrate_calls, save_calls = make_session()
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.OK))
        self.assertEqual(result["rms"], 0.1234)
        self.assertEqual(result["coverageX"], 0.42)
        self.assertEqual(len(save_calls), 1)
        self.assertEqual(calibrate_calls[0][1], FAST_CONFIG.frame_id)
        self.assertEqual(calibrate_calls[0][2], "pinhole")
        self.assertEqual(result["model"], "pinhole")
        self.assertFalse(session.running)
        self.assertFalse(session.progress["processing"])

    async def test_calibrates_with_the_configured_lens_model(self):
        """The lens model comes from the config (ceiling camera: fisheye) and is part
        of the result, so that the frontend can display it."""
        config = replace(FAST_CONFIG, calibration_model="fisheye")
        session, camera, calibrate_calls, _save_calls = make_session(config)
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        self.assertEqual(calibrate_calls[0][2], "fisheye")
        self.assertEqual(session.progress["result"]["model"], "fisheye")

    async def test_progress_names_the_model_already_while_capturing(self):
        config = replace(FAST_CONFIG, calibration_model="fisheye")
        session, camera, _calibrate_calls, _save_calls = make_session(config)
        session.start()
        await _capture_n(session, camera, 1)

        self.assertEqual(session.progress["model"], "fisheye")
        await session.abort()

    async def test_early_refusal_names_the_model(self):
        """Fewer than 3 captures: the immediate `result` also names the model, and so
        does the summary of `FinishCalibration`."""
        config = replace(FAST_CONFIG, calibration_model="fisheye")
        session, camera, _calibrate_calls, _save_calls = make_session(config)
        session.start()
        await _capture_n(session, camera, 1)

        _error, summary = await session.finish()

        self.assertEqual(summary["model"], "fisheye")
        self.assertEqual(session.progress["result"]["model"], "fisheye")

    async def test_failure_after_processing_names_the_model(self):
        config = replace(FAST_CONFIG, calibration_model="fisheye")
        session, camera, _calibrate_calls, _save_calls = make_session(
            config, detect_board=fake_detect_board_never
        )
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.DETECTION_FAILED))
        self.assertEqual(result["model"], "fisheye")

    async def test_detection_failed_with_too_few_captures(self):
        """Below `MIN_SAMPLES_FOR_CALIBRATION`, `finish()` gives up at once, without
        even starting a background evaluation."""
        session, camera, _calibrate_calls, save_calls = make_session()
        session.start()
        await _capture_n(session, camera, 1)

        error, summary = await session.finish()

        self.assertEqual(error, VisionErrorCode.DETECTION_FAILED)
        self.assertIn("message", summary)
        self.assertEqual(save_calls, [])
        self.assertFalse(session.progress["processing"])

    async def test_finish_without_ever_starting_reports_zero_samples(self):
        session, _camera, _calibrate_calls, save_calls = make_session()

        error, summary = await session.finish()

        self.assertEqual(error, VisionErrorCode.DETECTION_FAILED)
        self.assertEqual(summary["samples"], 0)
        self.assertEqual(save_calls, [])

    async def test_skips_captures_where_the_board_was_not_found(self):
        calls = 0

        def flaky_detect_board(gray, spec, board):
            nonlocal calls
            calls += 1
            return None if calls == 1 else FakeSample()

        session, camera, _calibrate_calls, save_calls = make_session(
            detect_board=flaky_detect_board
        )
        session.start()
        await _capture_n(session, camera, 4)  # one of them "misses" the board

        await session.finish()
        await session.wait_for_processing()

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.OK))
        self.assertEqual(result["samples"], 3)  # 4 aufgenommen, 3 erkannt
        self.assertEqual(len(save_calls), 1)

    async def test_detection_failed_after_processing_when_too_few_boards_found(self):
        """Enough captures made (>= MIN_SAMPLES_FOR_CALIBRATION), but too few actually
        showed the board. Unlike the case with too few captures, this fails only
        AFTER the background evaluation, not immediately in `finish()`."""
        session, camera, _calibrate_calls, save_calls = make_session(
            detect_board=fake_detect_board_never
        )
        session.start()
        await _capture_n(session, camera, 3)

        error, summary = await session.finish()
        self.assertEqual(error, VisionErrorCode.OK)  # evaluation was started

        await session.wait_for_processing()

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.DETECTION_FAILED))
        self.assertEqual(save_calls, [])

    async def test_detection_failed_when_calibration_raises_a_non_value_error(self):
        """cv2.calibrateCamera fails with `cv2.error`, not a `ValueError`, on
        numerically unsuitable captures. It must still come back as a clean
        DETECTION_FAILED instead of leaving the session silently dead (bug found
        live on Pi 2)."""

        def fake_calibrate_raises_runtime_error(
            samples, image_size, spec, board, *, frame_id, model
        ):
            raise RuntimeError("cv2.calibrateCamera: Rueckprojektion divergiert")

        session, camera, _calibrate_calls, save_calls = make_session(
            calibrate_from_samples=fake_calibrate_raises_runtime_error
        )
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.DETECTION_FAILED))
        self.assertIn("divergiert", result["message"])
        self.assertEqual(save_calls, [])
        self.assertFalse(session.running)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class DetectBoardHangTest(unittest.IsolatedAsyncioTestCase):
    """Found live on the ceiling camera (12 MP): `detect_board` on the full camera
    frame can hang indefinitely on the Pi hardware (no error, no return value).
    That now happens in `_process()` (during the evaluation after `finish()`), no
    longer in `capture()`. `CAPTURE_DETECT_TIMEOUT_S` is patched here to a test
    value; the real value (20 s) would be too long for a unit test."""

    async def test_a_hanging_capture_is_skipped_instead_of_blocking_the_evaluation(self):
        import threading
        from unittest import mock

        release = threading.Event()
        calls = 0

        def flaky_detect_board(gray, spec, board):
            nonlocal calls
            calls += 1
            if calls == 1:
                release.wait(timeout=5)  # hangs "forever" (test limit 5 s)
                return FakeSample()
            return FakeSample()  # the others are normally fast

        session, camera, _calibrate_calls, save_calls = make_session(
            detect_board=flaky_detect_board
        )
        session.start()
        await _capture_n(session, camera, 4)

        with mock.patch("vision_server.calibration_session.CAPTURE_DETECT_TIMEOUT_S", 0.05):
            await session.finish()
            await asyncio.wait_for(session.wait_for_processing(), timeout=2.0)

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.OK))
        self.assertEqual(result["samples"], 3)  # 4 captured, 1 hangs/discarded
        self.assertEqual(len(save_calls), 1)
        release.set()


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class OnCalibratedTest(unittest.IsolatedAsyncioTestCase):
    """`on_calibrated`: `runner/calibration.py` hooks in the hot reload of detection
    and overlay here (`AprilTagDetectionSource.apply_calibration` & co.), triggered
    directly after saving in the background evaluation."""

    async def test_calls_a_sync_callback_after_a_successful_finish(self):
        received: list = []
        session, camera, _, _ = make_session(on_calibrated=received.append)
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].rms_reprojection_error, 0.1234)

    async def test_calls_an_async_callback_after_a_successful_finish(self):
        received: list = []

        async def on_calibrated(calibration):
            received.append(calibration)

        session, camera, _, _ = make_session(on_calibrated=on_calibrated)
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        self.assertEqual(len(received), 1)

    async def test_not_called_when_there_are_too_few_captures(self):
        received: list = []
        session, camera, _, _ = make_session(on_calibrated=received.append)
        session.start()
        await _capture_n(session, camera, 1)  # zu wenig, kurzgeschlossen

        await session.finish()

        self.assertEqual(received, [])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RmsWarningTest(unittest.IsolatedAsyncioTestCase):
    async def test_result_carries_a_warning_above_the_rms_target(self):
        def fake_calibrate_high_rms(samples, image_size, spec, board, *, frame_id, model):
            return SimpleNamespace(
                rms_reprojection_error=RMS_WARNING_PX + 1.5,
                sample_count=len(samples),
                model=model,
            )

        session, camera, _calibrate_calls, _save_calls = make_session(
            calibrate_from_samples=fake_calibrate_high_rms,
        )
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        self.assertIn("warning", session.progress["result"])

    async def test_result_carries_no_warning_within_the_rms_target(self):
        session, camera, _calibrate_calls, _save_calls = make_session()
        session.start()
        await _capture_n(session, camera, 3)  # fake_calibrate_from_samples returns rms=0.1234

        await session.finish()
        await session.wait_for_processing()

        self.assertNotIn("warning", session.progress["result"])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class AbortTest(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_save(self):
        session, camera, _calibrate_calls, save_calls = make_session()
        session.start()
        await _capture_n(session, camera, 3)

        await session.abort()

        self.assertEqual(save_calls, [])
        self.assertFalse(session.running)
        self.assertFalse(session.busy)

    async def test_is_idempotent(self):
        session, _camera, _, _ = make_session()
        session.start()
        await session.abort()
        await session.abort()  # must not raise
        self.assertFalse(session.running)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RestartTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_resets_samples_from_a_previous_run(self):
        session, camera, _calibrate_calls, _save_calls = make_session()
        session.start()
        camera.push()
        await session.capture()
        self.assertEqual(session.progress["samples"], 1)
        await session.abort()

        session.start()  # new run, same instance

        self.assertEqual(session.progress["samples"], 0)
        await session.abort()

    async def test_start_clears_a_previous_result(self):
        session, camera, _calibrate_calls, _save_calls = make_session()
        session.start()
        await _capture_n(session, camera, 3)
        await session.finish()
        await session.wait_for_processing()
        self.assertIsNotNone(session.progress.get("result"))

        session.start()

        self.assertIsNone(session.progress.get("result"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ArchiveTest(unittest.IsolatedAsyncioTestCase):
    """Archive `calibration_capture_dir`: each session gets its own folder
    `<UTC stamp>` with lossless PNGs and `session.json`; a new session no longer
    overwrites the previous one."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="calib_archive_test_"))
        self.calibration_path = self.root / "cam_flange.json"
        self.config = replace(
            FAST_CONFIG,
            calibration_capture_dir=self.root / "cam_flange_captures",
            calibration_path=self.calibration_path,
            frame_id="cam_flange",
        )

    def _session(self, **overrides):
        saved: list = []
        kwargs = dict(save_capture_image=lambda path, image: saved.append(path), now=lambda: _T0)
        kwargs.update(overrides)
        session, camera, _, _ = make_session(self.config, **kwargs)
        return session, camera, saved

    async def test_session_json_describes_board_and_active_calibration(self):
        self.calibration_path.write_text(json.dumps({"cameraMatrix": [[1, 0, 2], [0, 3, 4], [0, 0, 1]]}))
        session, _, _ = self._session()
        session.start()

        self.assertEqual(session.archive_dir, self.config.calibration_capture_dir / "20261110T091500Z")
        meta = json.loads((session.archive_dir / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["schema"], "adp.calibration.captures/1")
        self.assertEqual(meta["frameId"], "cam_flange")
        self.assertEqual(meta["startedAt"], "2026-11-10T09:15:00Z")
        self.assertEqual((meta["imageFormat"], meta["pendingFormat"]), ("png", "jpg"))
        self.assertEqual(meta["board"], {
            "type": self.config.calibration_board_type,
            "cols": self.config.calibration_board_cols,
            "rows": self.config.calibration_board_rows,
            "squareSizeM": self.config.calibration_board_square_size_m,
            "markerSizeM": self.config.calibration_board_marker_size_m,
            "dictionary": self.config.calibration_board_dictionary,
        })
        self.assertEqual(meta["model"], self.config.calibration_model)
        self.assertEqual(meta["activeCalibration"]["cameraMatrix"][0], [1, 0, 2])
        self.assertEqual(session.progress["archiveDir"], str(session.archive_dir))

    async def test_two_sessions_in_the_same_second_do_not_overwrite_each_other(self):
        session, camera, saved = self._session()
        camera.push()
        session.start()
        first = session.archive_dir
        await session.capture()
        await session.abort()
        session.start()
        second = session.archive_dir
        await session.capture()
        await session.wait_for_pending_saves()

        self.assertEqual(first.name, "20261110T091500Z")
        self.assertEqual(second.name, "20261110T091500Z_2")
        archived = [p for p in saved if p.suffix == ".png"]
        self.assertEqual(archived, [first / "kalib_001.png", second / "kalib_001.png"])
        self.assertTrue((first / "session.json").exists())
        self.assertTrue((second / "session.json").exists())

    async def test_unreadable_active_calibration_keeps_the_archive(self):
        self.calibration_path.write_text("kein json")
        session, _, _ = self._session()
        session.start()

        self.assertIsNotNone(session.archive_dir)
        meta = json.loads((session.archive_dir / "session.json").read_text(encoding="utf-8"))
        self.assertIsNone(meta["activeCalibration"])

    async def test_archive_failure_does_not_break_the_calibration(self):
        # Archive path is a file, so the folder cannot be created.
        self.config.calibration_capture_dir.write_text("blockiert")
        session, camera, saved = self._session()
        with self.assertLogs("vision_server.calibration_session", level="WARNING"):
            session.start()
        camera.push()
        self.assertTrue(await session.capture())
        await session.wait_for_pending_saves()

        self.assertIsNone(session.archive_dir)
        self.assertIsNone(session.progress["archiveDir"])
        self.assertEqual([p.suffix for p in saved], [".jpg"])

    async def test_without_capture_dir_no_archive(self):
        session = make_session(FAST_CONFIG)[0]
        session.start()
        self.assertIsNone(session.archive_dir)
        self.assertIsNone(session.progress["archiveDir"])


class SaveCaptureImageFormatTest(unittest.TestCase):
    """`_save_capture_image` picks the format by extension: PNG lossless, otherwise JPEG."""

    def setUp(self) -> None:
        try:
            import cv2  # noqa: F401
        except Exception:
            self.skipTest("cv2 nicht verfuegbar")
        if np is None:
            self.skipTest("numpy nicht verfuegbar")
        self.dir = Path(tempfile.mkdtemp(prefix="calib_save_test_"))
        rng = np.random.default_rng(0)
        self.image = rng.integers(0, 256, size=(48, 64, 3), dtype=np.uint8)

    def test_png_is_lossless(self):
        import cv2

        path = self.dir / "sub" / "kalib_001.png"
        _save_capture_image(path, self.image)
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertTrue(np.array_equal(cv2.imread(str(path)), self.image))

    def test_jpg_stays_jpeg(self):
        path = self.dir / "kalib_001.jpg"
        _save_capture_image(path, self.image)
        self.assertEqual(path.read_bytes()[:3], b"\xff\xd8\xff")
