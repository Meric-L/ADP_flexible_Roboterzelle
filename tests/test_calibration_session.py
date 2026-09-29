"""Tests fuer `CalibrationSession`: keine Kamera -- die Board-/Kalibrier-
Funktionen aus `tagloc.boards`/`tagloc.calibration` sind injiziert (Muster
aus `test_apriltag_source.py`). `tagloc.frames.image_size`/`to_gray` laufen
dagegen echt (brauchen numpy, `to_gray` zusaetzlich cv2 fuer ein 3-Kanal-Bild)
-- ein Fake-"Bild" muss deshalb ein echtes Array sein, kein Platzhalter.

Aufnahme und Auswertung sind entkoppelt (siehe Moduldoc von
`calibration_session.py`): `capture()` merkt sich nur noch den Frame, kein
`detect_board` mehr im RPC-Pfad. Die eigentliche Erkennung passiert erst in
`_process()`, angestossen von `finish()` und im Hintergrund laufend -- Tests,
die das Ergebnis pruefen wollen, rufen darum immer
`await session.wait_for_processing()` nach `finish()`, bevor sie
`session.progress["result"]` lesen.

`start()` legt/leert echt einen Arbeitsordner auf der Platte (`_pending_dir`)
-- `FAST_CONFIG` zeigt deshalb auf ein Temp-Verzeichnis, nie auf den
Standardpfad im Repo (`data/calibration/...`).
"""

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

try:
    import numpy as np
except Exception:  # numpy broken in this environment
    np = None

from vision_server.calibration_session import (
    MIN_SAMPLES_FOR_CALIBRATION,
    RMS_WARNING_PX,
    CalibrationSession,
)
from vision_server.errors import VisionErrorCode
from vision_server.profiles import AprilTagProfileConfig

#: `start()` legt/leert `_pending_dir` (abgeleitet aus `calibration_path`)
#: wirklich auf der Platte -- nie auf den Repo-Standardpfad zeigen lassen.
_TMP_DIR = tempfile.mkdtemp(prefix="calibration_session_test_")

FAST_CONFIG = AprilTagProfileConfig(
    calibration_min_samples=2,
    calibration_path=Path(_TMP_DIR) / "camera.json",
)

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
    """Findet das Board immer -- Tests steuern ueber die Aufnahmen, nicht
    ueber einen Erkennungsfehlschlag."""
    return FakeSample()


def fake_detect_board_never(gray, spec, board):
    """Findet das Board nie -- fuer den Fehlschlag-Test."""
    return None


def fake_build_board(spec):
    return None  # Chessboard braucht kein Board-Objekt


def fake_compute_coverage(samples, image_size):
    return (0.42, 0.37)


def make_session(config=FAST_CONFIG, **overrides):
    """Baut eine `CalibrationSession` mit gefakten `tagloc`-Funktionen UND
    einem gefakten In-Memory-"Dateisystem" fuer `save_capture_image`/
    `load_capture_image` -- `_process()` liest damit exakt das zurueck, was
    `capture()` zuvor "gespeichert" hat, ohne echte Dateien anzufassen."""
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
    """Nimmt `n` Aufnahmen auf und wartet, bis sie alle "gespeichert" sind --
    `_process()` braucht das (siehe `wait_for_pending_saves` dort)."""
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
        """Seit der Entkopplung von Aufnahme und Auswertung prueft `capture()`
        nicht mehr, ob das Board sichtbar ist -- das entscheidet sich erst in
        `_process()`. `detect_board` wird hier absichtlich nie aufgerufen."""
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
    """Jede Aufnahme landet immer im session-eigenen `_pending_dir` (Aufnahme
    und Auswertung sind darauf angewiesen) und zusaetzlich im optionalen
    `calibration_capture_dir`-Archiv, wenn konfiguriert."""

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

        config = replace(FAST_CONFIG, calibration_capture_dir=Path("archive"))
        session, camera, _, _ = make_session(
            config, save_capture_image=fake_save_capture_image
        )
        session.start()
        camera.push()

        await session.capture()
        await session.wait_for_pending_saves()

        # Einmal in den Pending-Ordner, einmal ins Archiv.
        self.assertEqual(len(saved_calls), 2)
        archive_paths = [p for p in saved_calls if p.parent == Path("archive")]
        self.assertEqual(len(archive_paths), 1)
        self.assertEqual(archive_paths[0].name, "kalib_001.jpg")

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
        """Regressionstest fuer den Warteschlangen-Bug, live 2026-09-28 an
        der Deckenkamera gefunden: `_schedule_capture_save` teilte sich
        anfangs `_pool()` mit `detect_board` -- eine noch laufende
        Speicherung liess jede weitere `capture()` darauf warten. Mit
        `_save_pool()` (eigener Pool) darf eine haengende erste Speicherung
        eine zweite Aufnahme nicht mehr aufhalten."""
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
        # Blockierend auf `save_started` warten wuerde hier den Event-Loop
        # selbst einfrieren (derselbe Thread) und den Hintergrund-Task nie
        # zum Laufen kommen lassen -- stattdessen kurz pollen und dem Loop
        # dabei jedes Mal die Kontrolle zurueckgeben.
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
        """`finish()` beendet die Aufnahme-Phase sofort, auch wenn die
        Auswertung selbst noch im Hintergrund laeuft."""
        session, camera, _, _ = make_session()
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()

        self.assertFalse(session.progress["running"])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class BusyTest(unittest.IsolatedAsyncioTestCase):
    """`busy` (Aufnahme ODER Auswertung) schuetzt `StartCalibration` davor,
    den `_pending_dir` einer noch laufenden Auswertung wegzuraeumen."""

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
        self.assertTrue(session.busy)  # Auswertung laeuft noch
        release.set()
        await session.wait_for_processing()
        self.assertFalse(session.busy)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class FinishTest(unittest.IsolatedAsyncioTestCase):
    async def test_finish_returns_immediately_without_waiting_for_processing(self):
        """Der eigentliche Grund fuer die Entkopplung: `finish()` darf nicht
        auf die Auswertung warten, sonst haette eine langsame/haengende
        Auswertung ueber mehrere Vollaufloesungs-Aufnahmen dasselbe
        Timeout-Risiko wie zuvor eine einzelne haengende `detect_board`-
        Aufnahme (live 2026-09-28 gefunden)."""
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
        """Das Linsenmodell kommt aus der Config (Deckenkamera: Fisheye) und
        steht im Ergebnis, damit das Frontend es anzeigen kann."""
        config = replace(FAST_CONFIG, calibration_model="fisheye")
        session, camera, calibrate_calls, _save_calls = make_session(config)
        session.start()
        await _capture_n(session, camera, 3)

        await session.finish()
        await session.wait_for_processing()

        self.assertEqual(calibrate_calls[0][2], "fisheye")
        self.assertEqual(session.progress["result"]["model"], "fisheye")

    async def test_detection_failed_with_too_few_captures(self):
        """Unter `MIN_SAMPLES_FOR_CALIBRATION` gibt `finish()` sofort auf,
        ohne ueberhaupt eine Hintergrund-Auswertung anzustossen."""
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
        await _capture_n(session, camera, 4)  # eine davon "verpasst" das Board

        await session.finish()
        await session.wait_for_processing()

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.OK))
        self.assertEqual(result["samples"], 3)  # 4 aufgenommen, 3 erkannt
        self.assertEqual(len(save_calls), 1)

    async def test_detection_failed_after_processing_when_too_few_boards_found(self):
        """Genug Aufnahmen gemacht (>= MIN_SAMPLES_FOR_CALIBRATION), aber zu
        wenige zeigten tatsaechlich das Board -- anders als der Fall mit zu
        wenigen Aufnahmen scheitert das erst NACH der Hintergrund-Auswertung,
        nicht sofort in `finish()`."""
        session, camera, _calibrate_calls, save_calls = make_session(
            detect_board=fake_detect_board_never
        )
        session.start()
        await _capture_n(session, camera, 3)

        error, summary = await session.finish()
        self.assertEqual(error, VisionErrorCode.OK)  # Auswertung wurde angestossen

        await session.wait_for_processing()

        result = session.progress["result"]
        self.assertEqual(result["error"], int(VisionErrorCode.DETECTION_FAILED))
        self.assertEqual(save_calls, [])

    async def test_detection_failed_when_calibration_raises_a_non_value_error(self):
        """cv2.calibrateCamera scheitert bei numerisch ungeeigneten Aufnahmen
        mit `cv2.error`, keinem `ValueError` -- muss trotzdem als sauberes
        DETECTION_FAILED zurueckkommen statt die Session unsichtbar tot
        haengen zu lassen (Bug, live auf Pi 2 gefunden 2026-09-23)."""

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
    """Live an der Deckenkamera (12 MP) gefunden, 2026-09-28: `detect_board`
    auf dem vollen Kamera-Frame kann auf der Pi-Hardware unbegrenzt haengen
    (kein Fehler, kein Rueckgabewert). Das passiert jetzt in `_process()`
    (waehrend der Auswertung nach `finish()`), nicht mehr in `capture()`.
    `CAPTURE_DETECT_TIMEOUT_S` wird hier auf einen Testwert gepatcht, der
    echte Wert (20 s) waere fuer einen Unittest zu lang."""

    async def test_a_hanging_capture_is_skipped_instead_of_blocking_the_evaluation(self):
        import threading
        from unittest import mock

        release = threading.Event()
        calls = 0

        def flaky_detect_board(gray, spec, board):
            nonlocal calls
            calls += 1
            if calls == 1:
                release.wait(timeout=5)  # haengt "fuer immer" (Testlimit 5s)
                return FakeSample()
            return FakeSample()  # die anderen sind normal schnell

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
        self.assertEqual(result["samples"], 3)  # 4 aufgenommen, 1 haengt/verworfen
        self.assertEqual(len(save_calls), 1)
        release.set()


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class OnCalibratedTest(unittest.IsolatedAsyncioTestCase):
    """`on_calibrated` -- `runner.py` haengt hier den Hot-Reload von
    Erkennung/Overlay ein (`AprilTagDetectionSource.apply_calibration` &
    Co.), ausgeloest direkt nach dem Speichern in der Hintergrund-Auswertung."""

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
        await _capture_n(session, camera, 3)  # fake_calibrate_from_samples liefert rms=0.1234

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
