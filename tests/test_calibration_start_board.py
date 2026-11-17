"""Tests fuer das im Frontend gewaehlte Kalibrierbrett der Deckenkamera.

`StartCalibration(OptionsJson)` nimmt seit 17.11.2026 optional ein Brett
(Schachbrett oder ChArUco) nach denselben Regeln wie die Hand-Auge-
Kalibrierung. Siehe `doc/projektdoku/arbeitsplaene/deckenkamera-kalibrierung-board-wahl.md`.
"""

import asyncio
import json
import unittest
from types import SimpleNamespace

from tagloc.boards import CHARUCO, CHESSBOARD, BoardSpec

from vision_server.calibration_session import START_SCHEMA, parse_start_options
from vision_server.errors import VisionErrorCode
from vision_server.runner.calibration import _start_calibration_method

from tests.test_calibration_session import FAST_CONFIG, _capture_n, make_session

CHARUCO_BOARD = {
    "type": "charuco",
    "cols": 11,
    "rows": 7,
    "squareSizeM": 0.025,
    "markerSizeM": 0.0185,
    "dictionary": "DICT_5X5_100",
}


def options(board) -> str:
    return json.dumps({"schema": START_SCHEMA, "board": board})


class ParseStartOptionsTest(unittest.TestCase):
    def test_empty_or_without_board_keeps_the_configured_board(self):
        for text in ("", "   ", "{}", json.dumps({"schema": START_SCHEMA})):
            self.assertIsNone(parse_start_options(text), text)

    def test_charuco_board(self):
        board = parse_start_options(options(CHARUCO_BOARD))
        self.assertEqual(board.type, CHARUCO)
        self.assertEqual((board.cols, board.rows), (11, 7))
        self.assertAlmostEqual(board.marker_size_m, 0.0185)
        self.assertEqual(board.dictionary, "DICT_5X5_100")

    def test_chessboard(self):
        board = parse_start_options(options({"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": 0.023}))
        self.assertEqual((board.type, board.cols, board.rows), (CHESSBOARD, 6, 7))

    def test_unusable_input_is_refused_with_text(self):
        for text in (
            "kein json",
            json.dumps({"schema": "anderes/1"}),
            options({**CHARUCO_BOARD, "markerSizeM": 0.03}),  # Marker groesser als Feld
            options({**CHARUCO_BOARD, "dictionary": "DICT_4X4_50", "cols": 12, "rows": 10}),  # zu wenig Marker
            options({"type": "chessboard", "cols": 6, "rows": 6, "squareSizeM": 0.023}),  # mehrdeutig
            options({"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": 0.2}),  # Feld zu gross
        ):
            with self.assertRaises(ValueError, msg=text):
                parse_start_options(text)


class SessionBoardTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_given_board_reaches_detection_progress_and_calibration(self):
        seen_specs: list = []
        board = parse_start_options(options(CHARUCO_BOARD))

        from tests.test_calibration_session import FakeSample

        def detect(gray, spec, built):
            seen_specs.append(spec)
            return FakeSample()

        session, camera, calibrate_calls, _ = make_session(detect_board=detect)
        session.start(board)
        self.assertEqual(session.board_spec, board)
        self.assertEqual(session.progress["board"], board.as_dict())

        await _capture_n(session, camera, 3)
        await session.finish()
        await session.wait_for_processing()

        self.assertTrue(seen_specs)
        self.assertTrue(all(spec == board for spec in seen_specs))
        self.assertEqual(len(calibrate_calls), 1)

    def test_start_without_board_falls_back_to_the_configuration(self):
        session, _, _, _ = make_session()
        session.start(parse_start_options(options(CHARUCO_BOARD)))
        session.start()
        self.assertEqual(session.board_spec, FAST_CONFIG.calibration_board_spec())


class StartCalibrationMethodTest(unittest.IsolatedAsyncioTestCase):
    def _wiring(self):
        session, _, _, _ = make_session()
        written: list = []

        async def write_value(value):
            written.append(json.loads(value))

        wiring = SimpleNamespace(
            jobs=SimpleNamespace(busy=False),
            layer2_active=lambda: False,
            hand_eye_active=lambda: False,
            calibration_busy=lambda: session.busy,
            states=SimpleNamespace(is_ready=lambda: True),
            calibration_session=session,
            annotator=None,
            space=SimpleNamespace(calibration_progress=SimpleNamespace(write_value=write_value)),
        )
        return wiring, session, written

    async def _call(self, wiring, *args):
        from asyncua import ua

        method = _start_calibration_method(wiring)
        reply = await method(ua.NodeId(1, 1), *[ua.Variant(arg, ua.VariantType.String) for arg in args])
        return reply[0].Value

    async def test_without_argument_uses_the_configured_board(self):
        wiring, session, _ = self._wiring()
        self.assertEqual(await self._call(wiring), VisionErrorCode.OK)
        self.assertTrue(session.running)
        self.assertEqual(session.board_spec, FAST_CONFIG.calibration_board_spec())

    async def test_with_board(self):
        wiring, session, _ = self._wiring()
        self.assertEqual(await self._call(wiring, options(CHARUCO_BOARD)), VisionErrorCode.OK)
        self.assertEqual(session.board_spec.type, CHARUCO)

    async def test_bad_board_is_refused_and_reported(self):
        wiring, session, written = self._wiring()
        code = await self._call(wiring, options({**CHARUCO_BOARD, "markerSizeM": 0.03}))
        self.assertEqual(code, VisionErrorCode.INVALID_ARGUMENT)
        self.assertFalse(session.running)
        self.assertIn("markerSizeM", written[-1]["startError"])
        # Der naechste gueltige Start loescht den Grund wieder.
        await self._call(wiring, "")
        self.assertNotIn("startError", session.progress)


if __name__ == "__main__":
    unittest.main()
