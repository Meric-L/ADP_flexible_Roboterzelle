"""Live-Ansicht der Tag-Einmessung: Debug-Daten, Zeichnen, Vorschau, Protokoll, HTTP.

Wie `test_tagreference.py` mit gerenderten Szenen (`render_reference_scene`);
echter Detektor und echte Kantensuche, nur ohne Kamera. Ohne OpenCV/numpy/asyncua
werden die Tests uebersprungen.
"""

import asyncio
import base64
import dataclasses
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

try:
    import cv2
    import numpy as np
    from asyncua import ua

    _ = (cv2.imread, np.ndarray)

    from tagloc.detector import build_detector
    from tagloc.reference_edges import ReferenceLine, measure_reference_lines
    from tagloc.reference_overlay import LAYER_KEYS, draw_reference_debug, effective_layers
    from vision_server import tagreference_log
    from vision_server.camera_stream import CameraStreamPublisher
    from vision_server.detection.apriltag import AprilTagDetectionSource
    from vision_server.errors import VisionErrorCode
    from vision_server.mjpeg_server import MjpegServer
    from vision_server.profiles import AprilTagProfileConfig, CameraStreamConfig
    from vision_server.stream_overlay import AprilTagStreamAnnotator
    from vision_server.tagreference import parse_request

    from tests.test_mjpeg_server import _get
    from tests.test_tagreference import (
        TAG_ID,
        TAG_SIZE_M,
        SceneSource,
        request_text,
    )
    from tests.test_tagreference import OpcUaTest as _OpcUaTest

    _start_server = _OpcUaTest.start
    del _OpcUaTest  # sonst sammelt der Test-Lader dessen Tests ein zweites Mal ein
except Exception:  # pragma: no cover - numpy/cv2/asyncua fehlen
    np = None
    SceneSource = object


def _same(a, b) -> bool:
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return a is not None and b is not None and np.array_equal(a, b)
    return a == b


def _same_measurements(first, second) -> bool:
    return len(first) == len(second) and all(
        all(
            _same(getattr(x, field.name), getattr(y, field.name))
            for field in dataclasses.fields(x)
        )
        for x, y in zip(first, second)
    )


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class DebugDataTest(SceneSource, unittest.TestCase):
    def setUp(self):
        self.make_scene()
        self.gray = cv2.cvtColor(self.images[0], cv2.COLOR_BGR2GRAY)

    def measure(self, debug=None, lines=None, image=None):
        return measure_reference_lines(
            self.gray if image is None else image,
            self.calibration,
            self.pose_cam_tag,
            self.guess,
            lines or self.LINES,
            tag_size_m=TAG_SIZE_M,
            debug=debug,
        )

    def test_result_is_bit_identical_with_and_without_debug(self):
        plain = self.measure()
        debug = []
        with_debug = self.measure(debug)
        self.assertTrue(_same_measurements(plain, with_debug))
        self.assertTrue(all(line.found for line in plain))

    def test_debug_content_is_plausible(self):
        debug = []
        self.measure(debug)
        json.dumps(debug)  # JSON-tauglich
        self.assertEqual([entry["id"] for entry in debug], ["_tag", "A", "B"])
        self.assertGreater(len(debug[0]["maskOutlinePx"]), 4)
        width, height = self.IMAGE_SIZE
        for entry in debug[1:]:
            self.assertTrue(entry["found"])
            self.assertIsNone(entry["reason"])
            for key in ("guessPx", "profilesPx", "edgePointsPx"):
                self.assertTrue(entry[key], key)
            for u, v in entry["guessPx"]:
                self.assertTrue(0 <= u < width and 0 <= v < height)
            self.assertEqual(len(entry["profilesPx"]), len(entry["normalsPx"]))
            self.assertGreater(entry["bandPx"], 0)
            chosen = [candidate for candidate in entry["candidates"] if candidate["chosen"]]
            self.assertEqual(len(chosen), 1)
            self.assertEqual(len(entry["measuredPx"]), 2)
            self.assertEqual(len(entry["measuredPolyPx"]), 12)
            self.assertIsNotNone(entry["offsetMm"])
            self.assertIsNotNone(entry["angleDeg"])
            self.assertGreater(entry["supportPx"], 0)

    def test_failed_lines_carry_their_reason(self):
        blank = np.full((480, 640), 120, dtype=np.uint8)
        debug = []
        self.measure(debug, image=blank)
        for entry in debug[1:]:
            self.assertFalse(entry["found"])
            self.assertEqual(entry["reason"], "NO_EDGE")
            self.assertIsNone(entry["measuredPx"])
            self.assertTrue(entry["guessPx"])
        far = [ReferenceLine("F", np.array([9.0, 9.0, 0.0]), np.array([9.5, 9.0, 0.0]))]
        debug = []
        self.measure(debug, lines=far)
        self.assertEqual(debug[1]["reason"], "NOT_VISIBLE")
        self.assertNotIn("guessPx", debug[1])

    def draw_cases(self):
        found = []
        self.measure(found)
        blank = []
        self.measure(blank, image=np.full((480, 640), 120, dtype=np.uint8))
        far = []
        self.measure(far, lines=[ReferenceLine("F", np.array([9.0, 9.0, 0.0]), np.array([9.5, 9.0, 0.0]))])
        ambiguous = [dict(entry) for entry in found]
        for entry in ambiguous[1:]:
            entry["ambiguous"] = True
        return {"found": found, "NO_EDGE": blank, "NOT_VISIBLE": far, "ambiguous": ambiguous}

    def test_drawing_runs_for_all_cases_and_changes_a_copy(self):
        for name, debug in self.draw_cases().items():
            with self.subTest(name):
                image = self.images[0].copy()
                original = image.copy()
                out = draw_reference_debug(image, debug, title="Tag 7 - Vorschau")
                self.assertEqual(out.shape, image.shape)
                self.assertTrue(np.array_equal(image, original), "Eingabe veraendert")
                self.assertFalse(np.array_equal(out, original))

    def test_grayscale_input_is_accepted(self):
        out = draw_reference_debug(self.gray, self.draw_cases()["found"], title="x")
        self.assertEqual(out.shape, (480, 640, 3))

    def test_layers_switch_things_off(self):
        found = self.draw_cases()["found"]
        image = self.images[0]
        everything = draw_reference_debug(image, found)
        none = {key: False for key in LAYER_KEYS}
        self.assertTrue(np.array_equal(draw_reference_debug(image, found, layers=none), image))
        self.assertTrue(np.array_equal(draw_reference_debug(image, found, layers={}), everything))
        self.assertTrue(np.array_equal(draw_reference_debug(image, found, layers=None), everything))
        # Titel bleibt auch ohne Ebenen.
        titled = draw_reference_debug(image, found, title="Tag 7", layers=none)
        self.assertFalse(np.array_equal(titled, image))
        # Eine verworfene RANSAC-Linie gibt es in der sauberen Szene nicht: eine ergaenzen.
        found = [dict(entry) for entry in found]
        found[1]["candidates"] = found[1]["candidates"] + [
            {"inliersPx": [[300, 200], [310, 205], [320, 210]], "score": 1.0, "chosen": False}
        ]
        for key in LAYER_KEYS:
            with self.subTest(key):
                layers = {key: False}
                out = draw_reference_debug(
                    image, found, tag_corners_px=[[100, 100], [200, 100], [200, 200], [100, 200]], layers=layers
                )
                reference = draw_reference_debug(
                    image, found, tag_corners_px=[[100, 100], [200, 100], [200, 200], [100, 200]]
                )
                self.assertFalse(np.array_equal(out, reference))

    def test_effective_layers_ignores_unknown_keys(self):
        layers = effective_layers({"guess": False, "unbekannt": False})
        self.assertEqual(set(layers), set(LAYER_KEYS))
        self.assertFalse(layers["guess"])
        self.assertTrue(all(value for key, value in layers.items() if key != "guess"))


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class RequestOptionsTest(unittest.TestCase):
    def test_defaults_and_options(self):
        request = parse_request(request_text())
        self.assertEqual(request.overlay, {key: True for key in LAYER_KEYS})
        self.assertTrue(request.save_debug)
        request = parse_request(
            request_text(overlay={"band": False, "irgendwas": 1}, saveDebug=False)
        )
        self.assertFalse(request.overlay["band"])
        self.assertTrue(request.overlay["guess"])
        self.assertFalse(request.save_debug)

    def test_broken_options(self):
        for overrides in ({"overlay": [1]}, {"overlay": {"guess": "ja"}}, {"saveDebug": 1}):
            with self.subTest(overrides):
                with self.assertRaises(ValueError):
                    parse_request(request_text(**overrides))


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class PreviewAnnotatorTest(SceneSource, unittest.TestCase):
    def setUp(self):
        self.make_scene()

    def annotator(self, **kwargs):
        annotator = AprilTagStreamAnnotator(
            AprilTagProfileConfig(tag_size_m=TAG_SIZE_M),
            detector=build_detector("tag36h11", "aruco", subpixel_corner_refinement=False),
            calibration=self.calibration,
            tag_map=None,
            **kwargs,
        )
        self.addCleanup(annotator.close)
        return annotator

    def settle(self, annotator):
        pending = annotator._preview_pending
        if pending is not None:
            pending.result(timeout=30)

    def test_preview_draws_and_writes_the_status(self):
        annotator = self.annotator()
        request = self.request()
        annotator.set_reference_preview(request)
        status = annotator.reference_preview_status
        self.assertTrue(status["active"])
        self.assertEqual(status["tagId"], TAG_ID)
        image = self.images[0]
        original = image.copy()
        annotator.annotate(image, "tagreference")  # startet die Auswertung im Hintergrund
        self.settle(annotator)
        out = annotator.annotate(image, "tagreference")
        self.assertTrue(np.array_equal(image, original))
        self.assertEqual(out.shape, image.shape)
        self.assertFalse(np.array_equal(out, image))
        status = annotator.reference_preview_status
        self.assertEqual(status["schema"], "wsc.vision.tagreference.preview/1")
        self.assertTrue(status["active"] and status["tagSeen"])
        self.assertEqual([line["id"] for line in status["lines"]], ["A", "B"])
        for line in status["lines"]:
            self.assertTrue(line["found"], line)
            self.assertIsNotNone(line["offsetMm"])
            self.assertEqual(
                set(line),
                {"id", "found", "offsetMm", "angleDeg", "ambiguous", "reason", "supportPx", "contrast"},
            )
        self.assertEqual(status["overlay"], {key: True for key in LAYER_KEYS})
        datetime.fromisoformat(status["updatedAt"])
        json.dumps(status)

    def test_layers_in_the_request_reach_the_status_and_the_picture(self):
        annotator = self.annotator()
        all_on = self.request()
        some_off = self.request(overlay={"band": False, "labels": False})
        pictures = []
        for request in (all_on, some_off):
            annotator.set_reference_preview(request)
            annotator.annotate(self.images[0], "tagreference")
            self.settle(annotator)
            pictures.append(annotator.annotate(self.images[0], "tagreference"))
        self.assertFalse(annotator.reference_preview_status["overlay"]["band"])
        self.assertFalse(np.array_equal(pictures[0], pictures[1]))

    def test_tag_not_in_the_picture(self):
        annotator = self.annotator()
        annotator.set_reference_preview(self.request())
        blank = np.full((480, 640, 3), 120, dtype=np.uint8)
        annotator.annotate(blank, "tagreference")
        self.settle(annotator)
        out = annotator.annotate(blank, "tagreference")
        self.assertFalse(np.array_equal(out, blank))  # Titel "nicht im Bild"
        status = annotator.reference_preview_status
        self.assertTrue(status["active"])
        self.assertFalse(status["tagSeen"])
        self.assertEqual(status["lines"], [])

    def test_without_preview_it_is_a_titled_apriltag_view(self):
        annotator = self.annotator()
        out = annotator.annotate(self.images[0], "tagreference")
        self.assertFalse(np.array_equal(out, self.images[0]))
        self.assertIsNone(annotator._preview_pending)
        annotator.set_reference_preview(self.request())
        annotator.set_reference_preview(None)
        status = annotator.reference_preview_status
        self.assertFalse(status["active"])
        self.assertIsNone(status["tagId"])

    def test_preview_expires_after_120_seconds(self):
        from vision_server.tagreference_preview import PREVIEW_TTL_S

        self.assertEqual(PREVIEW_TTL_S, 120.0)
        annotator = self.annotator()
        annotator.set_reference_preview(self.request())
        self.assertAlmostEqual(
            annotator._preview.expires_at - time.monotonic(), PREVIEW_TTL_S, delta=2.0
        )
        annotator._preview = dataclasses.replace(annotator._preview, expires_at=time.monotonic() - 1)
        annotator.annotate(self.images[0], "tagreference")
        self.assertIsNone(annotator._preview)
        self.assertFalse(annotator.reference_preview_status["active"])
        self.assertIsNone(annotator._preview_pending)

    def test_evaluation_is_throttled_and_never_runs_during_a_measurement(self):
        annotator = self.annotator()
        annotator.set_reference_preview(self.request())
        annotator.set_reference_measuring(True)
        annotator.annotate(self.images[0], "tagreference")
        self.assertIsNone(annotator._preview_pending)
        annotator.set_reference_measuring(False)
        annotator.annotate(self.images[0], "tagreference")
        self.settle(annotator)
        first = annotator._preview_pending
        annotator.annotate(self.images[0], "tagreference")  # < 1/3 s danach
        self.assertIs(annotator._preview_pending, first)

    def test_measurement_debug_is_shown_and_scaled_to_the_stream(self):
        annotator = self.annotator(detection_max_width=320)
        debug = []
        measure_reference_lines(
            cv2.cvtColor(self.images[0], cv2.COLOR_BGR2GRAY), self.calibration,
            self.pose_cam_tag, self.guess, self.LINES, tag_size_m=TAG_SIZE_M, debug=debug,
        )
        annotator.show_reference_debug(debug, None, (640, 480), "Tag 7 - Messung Bild 1/3", {})
        out = annotator.annotate(self.images[0], "tagreference")
        self.assertEqual(out.shape[:2], (240, 320))
        small = cv2.resize(self.images[0], (320, 240), interpolation=cv2.INTER_AREA)
        diff = np.any(out != small, axis=2)
        # Die gruene Linie muss im skalierten Bild liegen: Pixel nahe der Kante
        # A (links) sind veraendert, die Ecke unten rechts nicht.
        self.assertGreater(int(diff.sum()), 200)
        self.assertFalse(diff[-20:, -20:].any())

    def test_other_modes_are_unchanged(self):
        annotator = self.annotator()
        annotator.set_reference_preview(self.request())
        out = annotator.annotate(self.images[0], "off")
        self.assertIs(out is self.images[0] or np.array_equal(out, self.images[0]), True)
        self.assertIsNone(annotator._preview_pending)


class _StatusAnnotator:
    def __init__(self):
        self.reference_preview_status = None
        self.calibration_progress = None


class _Node:
    def __init__(self):
        self.written = []

    async def write_value(self, value):
        self.written.append(value)


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class StatusPublishTest(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_written_once_per_change(self):
        annotator = _StatusAnnotator()
        node = _Node()
        publisher = CameraStreamPublisher(
            camera=None, node=None, config=CameraStreamConfig(), annotator=annotator,
            preview_status_node=node,
        )
        await publisher._publish_preview_status()
        self.assertEqual(node.written, [])
        annotator.reference_preview_status = {"active": True}
        await publisher._publish_preview_status()
        await publisher._publish_preview_status()
        self.assertEqual([json.loads(item) for item in node.written], [{"active": True}])
        annotator.reference_preview_status = {"active": False}
        await publisher._publish_preview_status()
        self.assertEqual(len(node.written), 2)


class _FakeAnnotator:
    def __init__(self):
        self.calls = []

    def set_reference_preview(self, request):
        self.calls.append(("preview", None if request is None else request.tag_id))

    def set_reference_measuring(self, measuring):
        self.calls.append(("measuring", measuring))

    def show_reference_debug(self, debug, corners, size, title, layers):
        self.calls.append(("debug", len(debug), tuple(size), title))


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class OpcUaLiveViewTest(SceneSource, unittest.IsolatedAsyncioTestCase):
    #: Server mit installierten Methoden aus `OpcUaTest` (ohne dessen Tests zu erben).
    start = _start_server

    async def test_set_preview_method(self):
        annotator = _FakeAnnotator()
        source = AprilTagDetectionSource.__new__(AprilTagDetectionSource)
        vision_system, _, _, _ = await self.start(
            source, lambda: True, state="Operational", annotator=annotator  # busy: nie BUSY
        )
        call = vision_system.call_method
        self.assertEqual(await call(self.set_preview_method, request_text()), int(VisionErrorCode.OK))
        self.assertEqual(annotator.calls[-1], ("preview", TAG_ID))
        self.assertEqual(await call(self.set_preview_method, ""), int(VisionErrorCode.OK))
        self.assertEqual(annotator.calls[-1], ("preview", None))
        before = len(annotator.calls)
        for bad in ("kein json", request_text(overlay=5), request_text(tagId=-1)):
            self.assertEqual(
                await call(self.set_preview_method, bad), int(VisionErrorCode.INVALID_ARGUMENT)
            )
        self.assertEqual(len(annotator.calls), before)

    async def test_set_preview_needs_operational(self):
        annotator = _FakeAnnotator()
        source = AprilTagDetectionSource.__new__(AprilTagDetectionSource)
        vision_system, _, _, _ = await self.start(
            source, lambda: False, state="Preoperational", annotator=annotator
        )
        self.assertEqual(
            await vision_system.call_method(self.set_preview_method, request_text()),
            int(VisionErrorCode.INVALID_STATE),
        )
        self.assertEqual(annotator.calls, [])

    async def test_measurement_writes_the_protocol_and_drives_the_preview(self):
        self.make_scene()
        annotator = _FakeAnnotator()
        with tempfile.TemporaryDirectory() as folder:
            debug_dir = Path(folder) / "tagreference"
            source, camera = self.source(folder)
            source._config = dataclasses.replace(source._config, capture_timeout_s=2.0)
            vision_system, _, measure, _ = await self.start(
                source, lambda: False, annotator=annotator, debug_dir=debug_dir
            )
            self.assertEqual(source.reference_debug_sink, annotator.show_reference_debug)
            request = request_text(
                tagSizeM=TAG_SIZE_M,
                moduleTagGuess=self.guess_dict(),
                lines=self.lines_json(),
            )

            async def feed():
                await asyncio.sleep(0.02)
                for image in self.images:
                    camera.push_frame(image)

            feeder = asyncio.create_task(feed())
            result_json, error = await vision_system.call_method(measure, request)
            await feeder
            self.assertEqual(error, int(VisionErrorCode.OK), result_json)
            result = json.loads(result_json)
            folders = [path for path in debug_dir.iterdir()]
            self.assertEqual(len(folders), 1)
            protocol = folders[0]
            self.assertRegex(protocol.name, r"^\d{8}-\d{6}_tag7$")
            self.assertEqual(result["debugDir"], protocol.as_posix())  # ausserhalb des Repos: absolut
            names = {path.name for path in protocol.iterdir()}
            self.assertEqual(
                names,
                {"request.json", "result.json", "frame1.png", "frame2.png", "frame3.png", "overlay.jpg"},
            )
            self.assertEqual(json.loads((protocol / "result.json").read_text("utf-8")), result)
            self.assertEqual((protocol / "request.json").read_text("utf-8"), request)
            self.assertEqual(cv2.imread(str(protocol / "frame1.png")).shape, (480, 640, 3))
            self.assertTrue(np.array_equal(cv2.imread(str(protocol / "frame1.png")), self.images[0]))
            self.assertEqual(cv2.imread(str(protocol / "overlay.jpg")).shape, (480, 640, 3))
            # Vorschau gesetzt, Messung markiert, je Bild eine Debug-Anzeige.
            self.assertEqual(annotator.calls[0], ("preview", TAG_ID))
            self.assertEqual(annotator.calls[1], ("measuring", True))
            self.assertEqual(annotator.calls[-1], ("measuring", False))
            shown = [call for call in annotator.calls if call[0] == "debug"]
            self.assertEqual(len(shown), 3)
            self.assertEqual(shown[-1][3], "Tag 7 - Messung Bild 3/3")

            # saveDebug=false: kein Ordner, debugDir null.
            feeder = asyncio.create_task(feed())
            result_json, error = await vision_system.call_method(
                measure, json.dumps({**json.loads(request), "saveDebug": False})
            )
            await feeder
            self.assertEqual(error, int(VisionErrorCode.OK), result_json)
            self.assertIsNone(json.loads(result_json)["debugDir"])
            self.assertEqual(len(list(debug_dir.iterdir())), 1)

    async def test_a_protocol_error_does_not_stop_the_measurement(self):
        self.make_scene()
        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / "datei"
            blocked.write_text("im Weg")  # Ordner kann darunter nicht angelegt werden
            source, camera = self.source(folder)
            source._config = dataclasses.replace(source._config, capture_timeout_s=2.0)
            vision_system, _, measure, _ = await self.start(
                source, lambda: False, debug_dir=blocked
            )
            request = request_text(
                tagSizeM=TAG_SIZE_M, moduleTagGuess=self.guess_dict(), lines=self.lines_json()
            )

            async def feed():
                await asyncio.sleep(0.02)
                for image in self.images:
                    camera.push_frame(image)

            feeder = asyncio.create_task(feed())
            with self.assertLogs("vision_server.runner", "WARNING"):
                result_json, error = await vision_system.call_method(measure, request)
            await feeder
            self.assertEqual(error, int(VisionErrorCode.OK), result_json)
            self.assertIsNone(json.loads(result_json)["debugDir"])
            self.assertEqual(json.loads(result_json)["correction"]["dofs"], "full")

    def guess_dict(self):
        from tagloc.geometry import pose_to_dict

        return pose_to_dict(self.guess)

    def lines_json(self):
        return [{"id": line.id, "start": list(line.start), "end": list(line.end)} for line in self.LINES]


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class ProtocolFolderTest(unittest.TestCase):
    def test_only_the_newest_50_folders_stay(self):
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        start = datetime(2026, 10, 21, 12, 0, 0)
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for index in range(55):
                tagreference_log.write_protocol(
                    directory, 7, "{}", {"a": index}, [image], image,
                    now=start + timedelta(seconds=index),
                )
            names = sorted(path.name for path in directory.iterdir())
            self.assertEqual(len(names), 50)
            self.assertEqual(names[0], "20261021-120005_tag7")
            self.assertEqual(names[-1], "20261021-120054_tag7")

    def test_same_second_gets_a_suffix(self):
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        now = datetime(2026, 10, 21, 12, 0, 0)
        with tempfile.TemporaryDirectory() as folder:
            first = tagreference_log.write_protocol(Path(folder), 7, "{}", {}, [], None, now=now)
            second = tagreference_log.write_protocol(Path(folder), 7, "{}", {}, [image], None, now=now)
            self.assertEqual((first.name, second.name), ("20261021-120000_tag7", "20261021-120000_tag7_2"))

    def test_latest_overlay(self):
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertIsNone(tagreference_log.latest_overlay(directory))
            self.assertIsNone(tagreference_log.latest_overlay(directory / "gibt-es-nicht"))
            self.assertIsNone(tagreference_log.latest_overlay(None))
            old = tagreference_log.write_protocol(
                directory, 7, "{}", {}, [], image, now=datetime(2026, 10, 21, 12, 0, 0)
            )
            tagreference_log.write_protocol(  # neuer, aber ohne Overlay (saveDebug ohne Bild)
                directory, 7, "{}", {}, [], None, now=datetime(2026, 10, 21, 12, 0, 5)
            )
            self.assertEqual(tagreference_log.latest_overlay(directory), old / "overlay.jpg")


class _NoPublisher:
    latest = None
    _viewers = 0


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class TagReferenceHttpTest(unittest.IsolatedAsyncioTestCase):
    async def serve(self, directory):
        server = MjpegServer(_NoPublisher(), 0, host="127.0.0.1", tag_reference_dir=directory)
        await server.start()
        self.addAsyncCleanup(server.stop)
        return server

    async def test_404_without_a_measurement_and_200_with(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for case in (None, directory):
                server = await self.serve(case)
                _, writer, head = await _get(server.port, "/tagreference.jpg")
                writer.close()
                self.assertIn(b"404", head)
            image = np.full((8, 8, 3), 200, dtype=np.uint8)
            tagreference_log.write_protocol(directory, 7, "{}", {}, [], image)
            expected = (next(directory.iterdir()) / "overlay.jpg").read_bytes()
            server = await self.serve(directory)
            reader, writer, head = await _get(server.port, "/tagreference.jpg?t=123")
            body = await reader.read()
            writer.close()
            self.assertIn(b"200 OK", head)
            self.assertIn(b"image/jpeg", head)
            self.assertIn(b"Access-Control-Allow-Origin: *", head)
            self.assertIn(b"no-store", head)
            self.assertEqual(body, expected)


if __name__ == "__main__":
    unittest.main()
