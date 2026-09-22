"""Tests fuer `CalibrationSession`: keine Kamera -- die Board-/Kalibrier-
Funktionen aus `tagloc.boards`/`tagloc.calibration` sind injiziert (Muster
aus `test_apriltag_source.py`). `tagloc.frames.image_size`/`to_gray` laufen
dagegen echt (brauchen numpy, `to_gray` zusaetzlich cv2 fuer ein 3-Kanal-Bild)
-- ein Fake-"Bild" muss deshalb ein echtes Array sein, kein Platzhalter.

Aufnahmen sind manuell (`capture()`), kein automatisches Zeitintervall mehr
-- Tests rufen `capture()` darum direkt auf, statt auf einen Hintergrund-Loop
zu warten.
"""

import unittest
from types import SimpleNamespace

try:
    import numpy as np
except Exception:  # numpy broken in this environment
    np = None

from vision_server.calibration_session import CalibrationSession
from vision_server.errors import VisionErrorCode
from vision_server.profiles import AprilTagProfileConfig

FAST_CONFIG = AprilTagProfileConfig(calibration_min_samples=2)

#: Kleines echtes Bild -- `tagloc.frames.image_size`/`to_gray` sind nicht
#: injiziert und brauchen ein echtes Array, kein String-Platzhalter.
_FRAME = np.zeros((4, 4, 3), dtype=np.uint8) if np is not None else None


class FakeCamera:
    """Liefert `latest_frame`, ohne eine echte Kamera zu oeffnen."""

    def __init__(self) -> None:
        self.latest_frame = None

    def push(self) -> None:
        self.latest_frame = SimpleNamespace(image=_FRAME, timestamp=0.0)


class FakeSample:
    """Steht fuer `tagloc.boards.BoardSample`."""

    def __init__(self, corners: int = 20) -> None:
        self._corners = corners

    def count(self) -> int:
        return self._corners


def fake_detect_board(gray, spec, board):
    """Findet das Board immer -- Tests steuern ueber die Kamera, nicht ueber
    einen Erkennungsfehlschlag."""
    return FakeSample()


def fake_detect_board_never(gray, spec, board):
    """Findet das Board nie -- fuer den Fehlschlag-Test."""
    return None


def fake_build_board(spec):
    return None  # Chessboard braucht kein Board-Objekt


def fake_compute_coverage(samples, image_size):
    return (0.42, 0.37)


def make_session(config=FAST_CONFIG, **overrides):
    camera = FakeCamera()
    calibrate_calls: list = []
    save_calls: list = []

    def fake_calibrate_from_samples(samples, image_size, spec, board, *, frame_id):
        calibrate_calls.append((len(samples), frame_id))
        return SimpleNamespace(
            rms_reprojection_error=0.1234,
            sample_count=len(samples),
        )

    def fake_save_calibration(path, calibration):
        save_calls.append((path, calibration))

    kwargs = dict(
        detect_board=fake_detect_board,
        build_board=fake_build_board,
        calibrate_from_samples=fake_calibrate_from_samples,
        compute_coverage=fake_compute_coverage,
        save_calibration=fake_save_calibration,
    )
    kwargs.update(overrides)
    session = CalibrationSession(camera, config, **kwargs)
    return session, camera, calibrate_calls, save_calls


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class CaptureTest(unittest.IsolatedAsyncioTestCase):
    async def test_capture_adds_a_sample_when_board_is_seen(self):
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

    async def test_capture_without_board_found_returns_false(self):
        session, camera, _, _ = make_session(detect_board=fake_detect_board_never)
        session.start()
        camera.push()

        found = await session.capture()

        self.assertFalse(found)
        self.assertEqual(session.progress["samples"], 0)

    async def test_repeated_captures_accumulate_samples(self):
        session, camera, _, _ = make_session()
        session.start()
        camera.push()

        for _ in range(3):
            await session.capture()

        self.assertEqual(session.progress["samples"], 3)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_reports_running_min_samples_and_coverage(self):
        session, camera, _, _ = make_session()
        session.start()
        camera.push()
        await session.capture()

        progress = session.progress

        self.assertTrue(progress["running"])
        self.assertEqual(progress["minSamples"], 2)
        self.assertEqual(progress["coverageX"], 0.42)
        self.assertEqual(progress["coverageY"], 0.37)

    async def test_running_is_false_before_start_and_after_abort(self):
        session, _camera, _, _ = make_session()
        self.assertFalse(session.progress["running"])
        session.start()
        self.assertTrue(session.progress["running"])
        await session.abort()
        self.assertFalse(session.progress["running"])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class FinishTest(unittest.IsolatedAsyncioTestCase):
    async def test_ok_and_saves_with_enough_samples(self):
        session, camera, calibrate_calls, save_calls = make_session()
        session.start()
        camera.push()
        for _ in range(3):
            await session.capture()

        error, summary = await session.finish()

        self.assertEqual(error, VisionErrorCode.OK)
        self.assertEqual(summary["rms"], 0.1234)
        self.assertEqual(summary["coverageX"], 0.42)
        self.assertEqual(len(save_calls), 1)
        self.assertEqual(calibrate_calls[0][1], FAST_CONFIG.frame_id)
        self.assertFalse(session.running)

    async def test_detection_failed_with_too_few_samples(self):
        session, camera, _calibrate_calls, save_calls = make_session()
        session.start()
        camera.push()
        await session.capture()

        error, summary = await session.finish()

        self.assertEqual(error, VisionErrorCode.DETECTION_FAILED)
        self.assertIn("message", summary)
        self.assertEqual(save_calls, [])

    async def test_finish_without_ever_starting_reports_zero_samples(self):
        session, _camera, _calibrate_calls, save_calls = make_session()

        error, summary = await session.finish()

        self.assertEqual(error, VisionErrorCode.DETECTION_FAILED)
        self.assertEqual(summary["samples"], 0)
        self.assertEqual(save_calls, [])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class AbortTest(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_save(self):
        session, camera, _calibrate_calls, save_calls = make_session()
        session.start()
        camera.push()
        for _ in range(3):
            await session.capture()

        await session.abort()

        self.assertEqual(save_calls, [])
        self.assertFalse(session.running)

    async def test_is_idempotent(self):
        session, _camera, _, _ = make_session()
        session.start()
        await session.abort()
        await session.abort()  # darf nicht werfen
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

        session.start()  # neuer Lauf, gleiche Instanz

        self.assertEqual(session.progress["samples"], 0)
        await session.abort()


if __name__ == "__main__":
    unittest.main()
