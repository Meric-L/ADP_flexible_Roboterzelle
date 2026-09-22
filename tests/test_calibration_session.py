"""Remote calibration: settings, session flow, and the OPC-UA interface.

The camera is scripted with rendered chessboard views of known geometry
(`tools/make_synthetic_scene.py`); each view is delivered several times, like
a camera watching a board that is held still. Calibration files go to a
tempdir, never into `data/`.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np

    from tools.make_synthetic_scene import (
        chessboard_views,
        render_chessboard,
        synthetic_calibration,
    )
except Exception:  # OpenCV or numpy missing in this environment
    cv2 = None

from tagloc.boards import CHARUCO, CHESSBOARD, BoardSpec
from vision_server.calibration_session import (
    COLLECTING,
    DONE,
    IDLE,
    REVIEW,
    CalibrationInputError,
    CalibrationSession,
    StreamAnnotator,
    parse_settings,
)
from vision_server.camera import CameraFrame
from vision_server.errors import VisionErrorCode

IMAGE = (640, 480)
CHESSBOARD_SETTINGS = {
    "board": {"type": "chessboard", "cols": 9, "rows": 6, "squareSizeM": 0.03},
    "targetSamples": 5,
    "autoCapture": True,
}


class ScriptedCamera:
    """Delivers each image `repeat` times in a row, then holds the last one."""

    def __init__(self, images, repeat: int = 3) -> None:
        self._images = list(images)
        self._repeat = repeat
        self._reads = 0

    @property
    def latest_frame(self):
        if not self._images:
            return None
        index = min(self._reads // self._repeat, len(self._images) - 1)
        frame = CameraFrame(image=self._images[index], timestamp=float(self._reads))
        self._reads += 1
        return frame


class ModeNode:
    """Stands in for the writable `CameraStreamMode` node."""

    def __init__(self, value: str = "apriltag") -> None:
        self.value = value
        self.writes: list[str] = []

    async def read_value(self):
        return self.value

    async def write_value(self, value):
        self.value = value
        self.writes.append(value)


_VIEWS: list = []


def board_images(count: int = 30):
    """Rendered once per test run -- rendering is the slow part."""
    if not _VIEWS:
        calibration = synthetic_calibration(IMAGE)
        for pose in chessboard_views(count, calibration, BoardSpec(), image_size=IMAGE):
            image, _ = render_chessboard(pose, calibration, BoardSpec(), image_size=IMAGE)
            _VIEWS.append(image)
    return list(_VIEWS)


async def wait_for(predicate, timeout: float = 30.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("Bedingung nicht rechtzeitig erfuellt")
        await asyncio.sleep(0.01)


class ParseSettingsTest(unittest.TestCase):
    def test_chessboard(self):
        settings = parse_settings(json.dumps(CHESSBOARD_SETTINGS), min_samples=5)
        self.assertEqual(settings.board, BoardSpec(CHESSBOARD, 9, 6, 0.03))
        self.assertEqual(settings.target_samples, 5)
        self.assertTrue(settings.auto_capture)

    def test_charuco_with_marker_and_dictionary(self):
        settings = parse_settings(
            json.dumps(
                {
                    "board": {
                        "type": "charuco",
                        "cols": 7,
                        "rows": 5,
                        "squareSizeM": 0.03,
                        "markerSizeM": 0.022,
                        "dictionary": "DICT_4X4_50",
                    }
                }
            )
        )
        self.assertEqual(settings.board, BoardSpec(CHARUCO, 7, 5, 0.03, 0.022, "DICT_4X4_50"))
        self.assertEqual(settings.target_samples, 20)

    def test_board_record_round_trips_through_the_settings(self):
        """What the file records as `board` can be sent back as settings."""
        spec = BoardSpec(CHARUCO, 7, 5, 0.03, 0.022, "DICT_4X4_50")
        settings = parse_settings(json.dumps({"board": spec.as_dict()}))
        self.assertEqual(settings.board, spec)

    def test_rejects_unusable_settings_with_a_reason(self):
        cases = {
            "kein json": "{",
            "kein Objekt": "[]",
            "ohne board": "{}",
            "Typ": {"board": {"type": "kreis", "cols": 9, "rows": 6, "squareSizeM": 0.03}},
            "zu klein": {"board": {"type": "chessboard", "cols": 2, "rows": 6, "squareSizeM": 0.03}},
            "Kommazahl": {"board": {"type": "chessboard", "cols": 9.5, "rows": 6, "squareSizeM": 0.03}},
            "mm statt m": {"board": {"type": "chessboard", "cols": 9, "rows": 6, "squareSizeM": 30}},
            "Marker zu gross": {
                "board": {"type": "charuco", "cols": 7, "rows": 5, "squareSizeM": 0.03, "markerSizeM": 0.03}
            },
            "zu wenige Aufnahmen": {**CHESSBOARD_SETTINGS, "targetSamples": 3},
            "autoCapture": {**CHESSBOARD_SETTINGS, "autoCapture": "ja"},
        }
        for name, settings in cases.items():
            with self.subTest(name):
                text = settings if isinstance(settings, str) else json.dumps(settings)
                with self.assertRaises(CalibrationInputError) as caught:
                    parse_settings(text, min_samples=5)
                self.assertTrue(str(caught.exception))

    def test_square_size_message_names_the_unit(self):
        with self.assertRaises(CalibrationInputError) as caught:
            parse_settings(
                json.dumps({"board": {"type": "chessboard", "cols": 9, "rows": 6, "squareSizeM": 30}})
            )
        self.assertIn("Metern", str(caught.exception))


class StreamAnnotatorTest(unittest.TestCase):
    class Base:
        def annotate(self, image, mode):
            return ("base", mode)

    class Session:
        collecting = True

        def annotate(self, image):
            return "session"

    def test_collecting_session_wins_in_calibration_mode(self):
        annotator = StreamAnnotator(self.Base(), self.Session())
        self.assertEqual(annotator.annotate("bild", "calibration"), "session")
        self.assertEqual(annotator.annotate("bild", "apriltag"), ("base", "apriltag"))

    def test_idle_session_leaves_the_stream_to_the_base(self):
        session = self.Session()
        session.collecting = False
        annotator = StreamAnnotator(self.Base(), session)
        self.assertEqual(annotator.annotate("bild", "calibration"), ("base", "calibration"))

    def test_without_base_the_image_stays_unmarked(self):
        self.assertEqual(StreamAnnotator().annotate("bild", "apriltag"), "bild")


@unittest.skipUnless(cv2 is not None, "OpenCV/numpy nicht verfuegbar")
class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "calibration" / "cam_test.json"
        self.published: list[dict] = []
        self.saved_calls = 0
        self.mode = ModeNode()

    async def asyncTearDown(self):
        self.folder.cleanup()

    def session(self, camera, **kwargs) -> CalibrationSession:
        async def publish(text):
            self.published.append(json.loads(text))

        async def on_saved():
            self.saved_calls += 1
            return True, "uebernommen"

        options = dict(
            calibration_path=self.path,
            frame_id="cam_test",
            publish=publish,
            on_saved=on_saved,
            mode_node=self.mode,
            min_samples=4,
            capture_interval_s=0.0,
            analysis_pause_s=0.0,
        )
        options.update(kwargs)
        session = CalibrationSession(camera, **options)
        self.addAsyncCleanup(session.close)
        return session

    async def test_full_run_collects_computes_reviews_and_saves(self):
        session = self.session(ScriptedCamera(board_images()))
        code, message = await session.start(json.dumps(CHESSBOARD_SETTINGS))
        self.assertEqual(code, VisionErrorCode.OK, message)
        self.assertEqual(self.mode.value, "calibration")

        await wait_for(lambda: session.sample_count >= 5)
        await wait_for(lambda: (self.published[-1].get("hint") or {}).get("code") == "enough")
        status = self.published[-1]
        self.assertEqual(status["state"], COLLECTING)
        self.assertEqual(status["imageSize"], list(IMAGE))
        self.assertEqual(status["lastCapture"]["reason"], "auto")
        self.assertEqual(len(status["grid"]), 3)
        self.assertGreater(status["progress"]["x"], 0.0)

        code, message = await session.compute()
        self.assertEqual(code, VisionErrorCode.OK, message)
        self.assertEqual(self.mode.value, "apriltag")
        await wait_for(lambda: session.state == REVIEW)
        result = self.published[-1]["result"]
        # Rendered with an ideal pinhole camera: the fit must be near-perfect.
        self.assertLess(result["rmsReprojectionError"], 0.5)
        self.assertIn(result["quality"], ("good", "usable", "poor"))
        self.assertFalse(self.path.exists(), "vor Save darf nichts geschrieben sein")

        code, message = await session.save()
        self.assertEqual(code, VisionErrorCode.OK, message)
        await wait_for(lambda: session.state == DONE)
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(written["schema"], "wsc.vision.calibration/1")
        self.assertEqual(written["imageSize"], list(IMAGE))
        self.assertEqual(written["frameId"], "cam_test")
        self.assertEqual(written["board"]["type"], "chessboard")
        self.assertEqual(self.saved_calls, 1)
        final = self.published[-1]
        self.assertEqual(final["state"], DONE)
        self.assertTrue(final["saved"]["applied"])
        self.assertIsNone(final["saved"]["backupPath"])
        self.assertTrue(final["current"]["exists"])

    async def test_save_keeps_a_backup_of_the_previous_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"alt": true}', encoding="utf-8")
        session = self.session(ScriptedCamera(board_images()))
        await session.start(json.dumps({**CHESSBOARD_SETTINGS, "targetSamples": 4}))
        await wait_for(lambda: session.sample_count >= 4)
        await session.compute()
        await wait_for(lambda: session.state == REVIEW)
        await session.save()
        await wait_for(lambda: session.state == DONE)
        backup = Path(self.published[-1]["saved"]["backupPath"])
        self.assertEqual(backup.read_text(encoding="utf-8"), '{"alt": true}')
        self.assertFalse(backup.name.endswith(".json"))
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    async def test_commands_outside_their_state_are_refused(self):
        session = self.session(ScriptedCamera(board_images()))
        for command in (session.capture, session.compute, session.save):
            code, _ = await command()
            self.assertEqual(code, VisionErrorCode.INVALID_STATE)

        await session.start(json.dumps(CHESSBOARD_SETTINGS))
        code, _ = await session.start(json.dumps(CHESSBOARD_SETTINGS))
        self.assertEqual(code, VisionErrorCode.INVALID_STATE)
        code, message = await session.compute()
        self.assertEqual(code, VisionErrorCode.INVALID_STATE)
        self.assertIn("von mindestens 4", message)

    async def test_failed_computation_goes_back_to_collecting(self):
        """More views are the remedy, so the session must not end in a dead state.

        Two views are below OpenCV's minimum -- `calibrate_from_samples`
        refuses, which is the cheapest way to reach the failure path.
        """
        session = self.session(ScriptedCamera(board_images()), min_samples=2)
        await session.start(json.dumps({**CHESSBOARD_SETTINGS, "targetSamples": 2}))
        await wait_for(lambda: session.sample_count >= 2)
        code, _ = await session.compute()
        self.assertEqual(code, VisionErrorCode.OK)

        await wait_for(lambda: session.state == COLLECTING and self.published[-1]["error"])
        self.assertIn("Berechnung fehlgeschlagen", self.published[-1]["error"])
        # Collecting resumed, including the stream overlay it switches on.
        self.assertEqual(self.mode.value, "calibration")
        self.assertEqual(session.sample_count, 2)

        code, _ = await session.cancel()
        self.assertEqual(code, VisionErrorCode.OK)
        self.assertEqual(session.state, IDLE)
        self.assertEqual(self.mode.value, "apriltag")

    async def test_invalid_settings_leave_the_session_idle(self):
        session = self.session(ScriptedCamera(board_images()))
        code, message = await session.start('{"board": {"type": "kreis"}}')
        self.assertEqual(code, VisionErrorCode.INVALID_ARGUMENT)
        self.assertIn("kreis", message)
        self.assertEqual(session.state, IDLE)

    async def test_cancel_discards_everything_and_restores_the_stream(self):
        session = self.session(ScriptedCamera(board_images()))
        await session.start(json.dumps(CHESSBOARD_SETTINGS))
        await wait_for(lambda: session.sample_count >= 2)
        code, _ = await session.cancel()
        self.assertEqual(code, VisionErrorCode.OK)
        self.assertEqual(session.state, IDLE)
        self.assertEqual(session.sample_count, 0)
        self.assertEqual(self.mode.value, "apriltag")
        self.assertEqual(self.published[-1]["state"], IDLE)
        self.assertEqual((await session.cancel())[0], VisionErrorCode.OK)

    async def test_manual_mode_captures_only_on_request(self):
        session = self.session(ScriptedCamera(board_images()[:1], repeat=1))
        await session.start(json.dumps({**CHESSBOARD_SETTINGS, "autoCapture": False}))
        await wait_for(lambda: self.published[-1]["boardStill"])
        self.assertEqual(session.sample_count, 0)
        code, message = await session.capture()
        self.assertEqual(code, VisionErrorCode.OK, message)
        self.assertEqual(session.sample_count, 1)
        code, message = await session.capture()
        self.assertEqual(code, VisionErrorCode.OK)
        self.assertIn("ähnelt", message)

    async def test_manual_capture_without_a_board_fails(self):
        blank = np.full((IMAGE[1], IMAGE[0], 3), 200, dtype=np.uint8)
        session = self.session(ScriptedCamera([blank], repeat=1))
        await session.start(json.dumps(CHESSBOARD_SETTINGS))
        await wait_for(lambda: self.published[-1]["hint"]["code"] == "show_board")
        code, _ = await session.capture()
        self.assertEqual(code, VisionErrorCode.DETECTION_FAILED)

    async def test_without_frames_it_waits_for_the_camera(self):
        session = self.session(ScriptedCamera([]))
        await session.start(json.dumps(CHESSBOARD_SETTINGS))
        await wait_for(lambda: self.published[-1]["hint"] is not None)
        self.assertEqual(self.published[-1]["hint"]["code"], "no_camera")
        self.assertFalse(self.published[-1]["cameraOk"])

    async def test_annotate_draws_on_a_copy(self):
        images = board_images()
        session = self.session(ScriptedCamera(images))
        await session.start(json.dumps(CHESSBOARD_SETTINGS))
        await wait_for(lambda: session.sample_count >= 1)
        original = images[0].copy()
        marked = session.annotate(images[0])
        self.assertEqual(marked.shape, images[0].shape)
        self.assertTrue(np.array_equal(images[0], original))
        self.assertFalse(np.array_equal(marked, original))


@unittest.skipUnless(cv2 is not None, "OpenCV/numpy nicht verfuegbar")
class CalibrationOverOpcUaTest(unittest.IsolatedAsyncioTestCase):
    """The whole path a frontend takes: methods and status node over a real server.

    Starts like a Pi without a calibration file: the detection source does
    not open, the system sits in Preoperational. After `Save` the source
    must open and the system must be Operational.
    """

    async def test_calibrate_a_camera_that_had_no_calibration(self):
        from asyncua import Client, Server, ua

        from vision_server.address_space import attach_vision_system, configure_server
        from vision_server.calibration_session import StreamAnnotator as Annotator
        from vision_server.config import VisionServerConfig
        from vision_server.detection.base import DetectionSource
        from vision_server.profiles import CameraStreamConfig
        from vision_server.runner import _install_calibration
        from vision_server.state_machine import VisionStateMachines

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "cam_test.json"

        class WaitingSource(DetectionSource):
            """Like the AprilTag source: will not open without a calibration file."""

            profile_id = "apriltag"

            def __init__(self, camera):
                self.camera = camera
                self.calibration_path = path
                self.calibration_frame_id = "cam_test"
                self.calibration = None

            async def open(self):
                from tagloc.calibration import load_calibration

                self.calibration = load_calibration(path)

            async def reload_calibration(self):
                await self.open()

            async def acquire_and_detect(self, request):
                return []

        port = 48431
        config = VisionServerConfig(
            endpoint=f"opc.tcp://127.0.0.1:{port}/test/", camera_stream=CameraStreamConfig()
        )
        server = Server()
        await configure_server(server, config)
        space = await attach_vision_system(server, config)
        states = await VisionStateMachines.bind(space)
        source = WaitingSource(ScriptedCamera(board_images()))
        opened = {"apriltag": False}
        session = await _install_calibration(
            server, space, states, source, {"apriltag": source}, opened, Annotator()
        )
        self.addAsyncCleanup(session.close)
        session._min_samples = 4
        session._capture_interval_s = 0.0
        session._analysis_pause_s = 0.0

        async with server:
            client = Client(f"opc.tcp://127.0.0.1:{port}/test/")
            async with client:
                ns = await client.get_namespace_index(config.namespace_uri)
                folder_node = client.get_node(f"ns={ns};s=VisionMachine.Calibration")
                status_node = client.get_node(f"ns={ns};s=VisionMachine.Calibration.Status")

                def method(name):
                    return client.get_node(f"ns={ns};s=VisionMachine.Calibration.{name}")

                async def status():
                    return json.loads(await status_node.read_value())

                # Argument metadata as the WSC backend reads it: a scalar string.
                inputs = await method("Start").get_child("0:InputArguments")
                (argument,) = await inputs.read_value()
                self.assertEqual(argument.Name, "Settings")
                self.assertEqual(argument.DataType, ua.NodeId(ua.ObjectIds.String))
                self.assertEqual(argument.ValueRank, -1)
                self.assertIsInstance(inputs.nodeid.Identifier, str)
                self.assertEqual((await status())["current"], {"exists": False})

                error, message = await folder_node.call_method(
                    method("Start"), json.dumps({**CHESSBOARD_SETTINGS, "targetSamples": 4})
                )
                self.assertEqual(error, 0, message)
                self.assertEqual((await status())["state"], COLLECTING)

                await wait_for(lambda: session.sample_count >= 4)
                error, message = await folder_node.call_method(method("Compute"))
                self.assertEqual(error, 0, message)
                await wait_for(lambda: session.state == REVIEW)
                error, message = await folder_node.call_method(method("Save"))
                self.assertEqual(error, 0, message)
                await wait_for(lambda: session.state == DONE)

                final = await status()
                self.assertTrue(final["saved"]["applied"], final["saved"])
                self.assertTrue(path.is_file())
                self.assertTrue(opened["apriltag"])
                self.assertIsNotNone(source.calibration)
                self.assertEqual(states.state_names(), ("Operational", "Ready"))

                error, message = await folder_node.call_method(method("Capture"))
                self.assertEqual(error, int(VisionErrorCode.INVALID_STATE), message)


if __name__ == "__main__":
    unittest.main()
