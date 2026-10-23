"""Brett-Messung fuer die Hand-Auge-Kalibrierung: zu kleine Rasterangabe erkennen,
erstes Bild nach dem Aufruf, Reprojektionsfehler je Aufnahme, MeasureBoard.

Die Bilder sind nachgerechnete 6x7-Bretter (7x8 Felder, 23 mm) aus 40 cm mit
einer RealSense-aehnlichen Kamera -- gedreht, geneigt und aus dem Bild
geschoben.
"""

import json
import math
import tempfile
import types
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np
    from asyncua import Server, ua

    from tagloc import geometry as g
    from tagloc import tagmap
    from tagloc.boards import (
        BoardSpec,
        detect_partial_board,
        partial_patterns,
    )
    from tagloc.calibration import PINHOLE, CameraCalibration
    from vision_server.detection.apriltag import AprilTagDetectionSource
    from vision_server.errors import VisionErrorCode
    from vision_server.handeye_session import HandEyePorts, HandEyeSession, _parse_board
    from vision_server.measureboard import RESULT_SCHEMA, FrameBoard, build_result, parse_request

    from tests.test_apriltag_source import FakeCamera, profile_config
    from tests.test_handeye_session import sample_json
except Exception:  # pragma: no cover - cv2/numpy/asyncua fehlen
    cv2 = None

K = None if cv2 is None else np.array([[615.0, 0, 320.0], [0, 615.0, 240.0], [0, 0, 1]])
SQUARE = 0.023
REAL_COLS, REAL_ROWS = 6, 7
TEXTURE_PX, MARGIN = 40, 2


def _texture():
    image = np.full(
        ((REAL_ROWS + 1 + 2 * MARGIN) * TEXTURE_PX, (REAL_COLS + 1 + 2 * MARGIN) * TEXTURE_PX), 255, np.uint8
    )
    for r in range(REAL_ROWS + 1):
        for c in range(REAL_COLS + 1):
            if (r + c) % 2 == 0:
                y, x = (MARGIN + r) * TEXTURE_PX, (MARGIN + c) * TEXTURE_PX
                image[y:y + TEXTURE_PX, x:x + TEXTURE_PX] = 0
    return image


TEXTURE = None if cv2 is None else _texture()


def render(roll_deg=0.0, tilt_deg=0.0, distance=0.40, shift=(0.0, 0.0)):
    """Ein Bild des 6x7-Bretts (640x480, Graustufen)."""
    centre = np.array([(REAL_COLS - 1) / 2 * SQUARE, (REAL_ROWS - 1) / 2 * SQUARE, 0.0])
    pose = g.compose(
        g.from_rvec_tvec((0, 0, math.radians(roll_deg)), (shift[0], shift[1], distance)),
        g.from_rvec_tvec((math.radians(tilt_deg), 0, 0), (0, 0, 0)),
    )
    R, t = pose[:3, :3], pose[:3, 3]

    def project(x, y):
        p = R @ (np.array([x, y, 0.0]) - centre) + t
        uv = K @ p
        return uv[:2] / uv[2]

    height, width = TEXTURE.shape
    src = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    dst = np.float32(
        [project((u / TEXTURE_PX - MARGIN - 1) * SQUARE, (v / TEXTURE_PX - MARGIN - 1) * SQUARE) for u, v in src]
    )
    image = cv2.warpPerspective(
        TEXTURE, cv2.getPerspectiveTransform(src, dst), (640, 480), borderValue=255, flags=cv2.INTER_AREA
    )
    return cv2.GaussianBlur(image, (0, 0), 0.8)


def spec(cols=REAL_COLS, rows=REAL_ROWS):
    return BoardSpec("chessboard", cols, rows, SQUARE)


#: Ohne Angabe gilt die Brettgroesse aus der Konfiguration -- im Test die
#: Standardwerte (9x6), nicht das gerenderte 6x7-Brett.
BOARD = '"board": {"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": 0.023}'


@unittest.skipUnless(cv2 is not None, "cv2/numpy/asyncua fehlen")
class DetectionHelpersTest(unittest.TestCase):
    def test_board_on_a_black_carrier_is_found_on_the_inverted_image(self):
        """Helle Felder auf schwarzem Traeger: Am Originalbild scheitert die
        Erkennung (echtes Bild, 22.10.2026), am invertierten nicht."""
        from unittest import mock

        from tagloc import boards

        image = render()
        sentinel = boards.BoardSample(corners=np.zeros((REAL_COLS * REAL_ROWS, 1, 2)), ids=None)
        calls = []

        def fake(gray, board_spec):
            calls.append(int(gray[0, 0]))
            # nur das invertierte Bild (Ecke 255 - Original) liefert etwas
            return sentinel if int(gray[0, 0]) == 255 - int(image[0, 0]) else None

        with mock.patch.object(boards, "_detect_chessboard", fake):
            found = boards.detect_board(image, spec())
            self.assertIs(found, sentinel)
        self.assertEqual(len(calls), 2)  # erst Original, dann invertiert

    def test_search_on_a_reduced_image_is_as_accurate(self):
        """`max_side`: schneller suchen, im vollen Bild nachschaerfen. Die Pose
        stimmt mit der aus der Suche am vollen Bild ueberein (Millimeter), der
        Reprojektionsfehler ist nicht schlechter."""
        from tagloc.boards import canonical_chessboard_corners, detect_board, estimate_board_pose

        calibration = CameraCalibration(K, np.zeros(5), (640, 480), model=PINHOLE)
        for roll, tilt in ((0, 0), (20, 10), (60, 35)):
            with self.subTest(roll=roll, tilt=tilt):
                image = render(roll, tilt)
                poses = []
                for max_side in (None, 320):
                    sample = detect_board(image, spec(), max_side=max_side)
                    self.assertIsNotNone(sample)
                    corners = canonical_chessboard_corners(image, sample.corners, spec())
                    poses.append(estimate_board_pose(corners, spec(), calibration))
                (full_pose, full_error), (fast_pose, fast_error) = poses
                self.assertLess(np.linalg.norm(full_pose[:3, 3] - fast_pose[:3, 3]), 0.003)
                self.assertLessEqual(fast_error, max(full_error, 0.2))
        self.assertIsNone(detect_board(np.full((480, 640), 128, np.uint8), spec(), max_side=320))

    def test_partial_patterns(self):
        self.assertEqual(partial_patterns(spec()), [(5, 6), (4, 5), (3, 4), (3, 3)])
        self.assertEqual(partial_patterns(spec(5, 6)), [(4, 5), (3, 4), (3, 3)])
        self.assertEqual(partial_patterns(spec(3, 4)), [])

    def test_a_board_cut_by_the_image_edge_gives_a_partial_grid(self):
        image = render(shift=(-0.19, 0.0))  # nach links aus dem Bild geschoben
        found = detect_partial_board(image, spec())
        self.assertIsNotNone(found)
        corners, cols, rows = found
        self.assertLess(cols * rows, REAL_COLS * REAL_ROWS)
        self.assertTrue(np.all(corners[:, 0] < 320))  # nur im sichtbaren linken Teil

    def test_nothing_visible_gives_nothing(self):
        self.assertIsNone(detect_partial_board(np.full((480, 640), 128, np.uint8), spec()))


@unittest.skipUnless(cv2 is not None, "cv2/numpy/asyncua fehlen")
class SourceTest(unittest.IsolatedAsyncioTestCase):
    def source(self, folder, images, **overrides):
        camera = FakeCamera()
        source = AprilTagDetectionSource(
            profile_config(Path(folder), frame_id="cam_flange", **overrides), camera=camera
        )
        source._calibration = CameraCalibration(K, np.zeros(5), (640, 480), model=PINHOLE)
        source._tag_map = tagmap.empty_tag_map()
        for image in images:
            camera.push_frame(image)
        return source, camera

    async def test_measure_board_uses_only_frames_after_the_call_and_reports_the_error(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder, [render()] * 3, samples_per_job=2)
            pose, reason, info = await source.measure_board(spec())
        self.assertEqual(reason, "")
        # Der Ursprung ist eine Ecke des Rasters; die Brettmitte liegt auf der optischen Achse.
        centre = pose[:3, 3] + pose[:3, :3] @ [(REAL_COLS - 1) / 2 * SQUARE, (REAL_ROWS - 1) / 2 * SQUARE, 0]
        np.testing.assert_allclose(centre, [0, 0, 0.40], atol=0.01)
        self.assertLess(info["reprojectionErrorPx"], 1.0)
        # Das erste Bild liegt VOR dem Aufruf und zaehlt nur fuer den Zeitstempel.
        self.assertEqual(len(camera.consumed), 3)

    async def test_a_bad_fit_still_reports_its_error(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [render()] * 3, samples_per_job=2, max_reproj_error_px=0.0001)
            pose, reason, info = await source.measure_board(spec())
        self.assertIsNone(pose)
        self.assertIn("passt schlecht", reason)
        self.assertGreater(info["reprojectionErrorPx"], 0.0001)

    async def test_diagnose_reports_the_largest_grid(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [render()] * 3)
            request = parse_request('{' + BOARD + ', "frames": 1, "diagnose": true}', _parse_board)
            result = await source.measure_board_view(request)
            plain = await source.measure_board_view(parse_request('{' + BOARD + ', "frames": 1}', _parse_board))
        diagnosis = result["diagnosis"]
        self.assertEqual(diagnosis["largestGrid"], {"cols": REAL_COLS, "rows": REAL_ROWS})
        self.assertTrue(diagnosis["sbExact"])
        self.assertEqual(diagnosis["imageSize"], [640, 480])
        self.assertNotIn("diagnosis", plain)

    def test_diagnose_must_be_boolean(self):
        with self.assertRaises(ValueError):
            parse_request('{"diagnose": "ja"}', _parse_board)

    async def test_view_complete_partial_nothing_and_no_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [render()] * 3, samples_per_job=2)
            result = await source.measure_board_view(parse_request('{' + BOARD + ', "frames": 2}', _parse_board))
            self.assertTrue(result["complete"])
            self.assertEqual((result["frameCount"], result["framesUsed"]), (2, 2))
            self.assertIsNotNone(result["poseInCamera"])
            self.assertEqual(result["intrinsicsSource"], "calibration")
            self.assertEqual(len(result["cornersPx"]), 42)
            # Brett mittig: kaum Abstand vom Bildmittelpunkt.
            self.assertLess(abs(result["offsetPx"][0]), 15)
            self.assertLess(abs(result["offsetPx"][1]), 15)
            self.assertEqual(result["nearEdge"], [])

        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [render(shift=(-0.19, 0))] * 2)
            result = await source.measure_board_view(parse_request('{' + BOARD + ', "frames": 1}', _parse_board))
            self.assertTrue(result["found"])
            self.assertFalse(result["complete"])
            self.assertIsNone(result["poseInCamera"])
            self.assertIsNotNone(result["partial"])
            self.assertLess(result["offsetPx"][0], 0)  # Brett liegt links vom Bildmittelpunkt

        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [np.full((480, 640), 128, np.uint8)] * 2)
            result = await source.measure_board_view(parse_request('{' + BOARD + ', "frames": 1}', _parse_board))
            self.assertEqual((result["found"], result["complete"], result["error"]), (False, False, None))

        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [render()])  # nur das Vorab-Bild
            result = await source.measure_board_view(parse_request('{' + BOARD + ', "frames": 1}', _parse_board))
            self.assertEqual(result["error"], "NO_FRAME")




@unittest.skipUnless(cv2 is not None, "cv2/numpy/asyncua fehlen")
class ResultTest(unittest.TestCase):
    def frame(self, **kwargs):
        from datetime import datetime, timezone

        return FrameBoard(datetime(2026, 10, 22, tzinfo=timezone.utc), (640, 480), **kwargs)

    def build(self, frames, **kwargs):
        return build_result(
            frames, spec=spec(), frames_requested=2, frame_id="cam_flange",
            intrinsics_source="calibration", calibration_id="x", max_error_px=1.5, **kwargs,
        )

    def test_complete_result(self):
        corners = np.array([[10.0 + 20 * c, 300.0 + 15 * r] for r in range(7) for c in range(6)])
        pose = g.from_rvec_tvec((0, 0, 0), (0.0, 0.0, 0.4))
        result = self.build([self.frame(complete=True, pose=pose, reprojection_error_px=0.5, corners=corners)] * 2)
        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertTrue(result["complete"] and result["poseOk"])
        self.assertEqual(result["board"], {"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": SQUARE})
        self.assertAlmostEqual(result["distanceM"], 0.4)
        # Das Brett liegt unten links: offsetPx zeigt vom Bildmittelpunkt dorthin (x rechts, y unten).
        self.assertLess(result["offsetPx"][0], 0)
        self.assertGreater(result["offsetPx"][1], 0)
        self.assertEqual(result["nearEdge"], ["left"])
        json.dumps(result, allow_nan=False)
        # Ein schlechter Fit wird gemeldet, aber nicht als ok.
        bad = self.build([self.frame(complete=True, pose=pose, reprojection_error_px=2.5, corners=corners)])
        self.assertFalse(bad["poseOk"])

    def test_partial_and_empty_results(self):
        corners = np.array([[5.0, 100.0], [30.0, 100.0], [5.0, 130.0], [30.0, 130.0]])
        result = self.build([self.frame(partial=(corners, 3, 3))])
        self.assertTrue(result["found"] and not result["complete"])
        self.assertIsNone(result["poseInCamera"])
        self.assertEqual(result["partial"]["gridCols"], 3)
        self.assertEqual(result["partial"]["cornerCount"], 4)
        self.assertEqual(result["partial"]["bboxPx"], [5.0, 100.0, 30.0, 130.0])
        self.assertEqual(result["nearEdge"], ["left"])
        self.assertIsNone(result["cornersPx"])
        nothing = self.build([self.frame()])
        self.assertEqual((nothing["found"], nothing["partial"], nothing["centerPx"]), (False, None, None))
        empty = self.build([], error="NO_FRAME")
        self.assertEqual((empty["error"], empty["imageSize"], empty["found"]), ("NO_FRAME", None, False))

    def test_request_parsing(self):
        request = parse_request("", _parse_board)
        self.assertEqual((request.board, request.frames), (None, 3))
        request = parse_request(
            '{"board": {"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": 0.023}, "frames": 1}',
            _parse_board,
        )
        self.assertEqual((request.board.cols, request.board.rows, request.frames), (6, 7, 1))
        for text in ("kaputt", '{"schema": "falsch"}', '{"frames": 0}', '{"frames": 11}', '{"frames": true}',
                     '{"board": {"type": "chessboard", "cols": 6, "rows": 6, "squareSizeM": 0.023}}',
                     '{"board": {"type": "tag"}}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_request(text, _parse_board)

@unittest.skipUnless(cv2 is not None, "cv2/numpy/asyncua fehlen")
class SessionTest(unittest.IsolatedAsyncioTestCase):
    """`CaptureHandEyeSample`: Reprojektionsfehler je Aufnahme."""

    async def run_capture(self, measure):
        statuses = []

        async def publish(status):
            statuses.append(json.loads(status))

        with tempfile.TemporaryDirectory() as folder:
            ports = HandEyePorts(
                measure_tag=measure, busy=lambda: False, path=Path(folder) / "h.json",
                apply=lambda hand_eye: None, publish=publish, measure_board=measure,
            )
            session = HandEyeSession(ports)
            await session.start(
                '{"schema": "wsc.vision.handeye.start/1", "board": '
                '{"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": 0.023}}'
            )
            code = await session.capture(sample_json(g.identity()))
        return code, statuses[-1]

    async def test_the_error_is_reported_for_accepted_and_rejected_samples(self):
        async def accepted(spec_or_tag):
            return g.identity(), "", {"reprojectionErrorPx": 0.42}

        code, status = await self.run_capture(accepted)
        self.assertEqual(code, VisionErrorCode.OK)
        self.assertEqual(status["samples"][0]["reprojectionErrorPx"], 0.42)

        async def rejected(spec_or_tag):
            return None, "Board-Pose passt schlecht (2.5 px)", {"reprojectionErrorPx": 2.5}

        code, status = await self.run_capture(rejected)
        self.assertEqual(code, VisionErrorCode.DETECTION_FAILED)
        self.assertFalse(status["samples"][0]["accepted"])
        self.assertEqual(status["samples"][0]["reprojectionErrorPx"], 2.5)

        async def two_values(spec_or_tag):
            return g.identity(), ""  # aeltere Messfunktion ohne Info

        code, status = await self.run_capture(two_values)
        self.assertEqual(code, VisionErrorCode.OK)
        self.assertIsNone(status["samples"][0]["reprojectionErrorPx"])

@unittest.skipUnless(cv2 is not None, "cv2/numpy/asyncua fehlen")
class OpcUaTest(unittest.IsolatedAsyncioTestCase):
    async def test_method_answers_with_the_result(self):
        from vision_server.runner import _install_measure_board

        server = Server()
        await server.init()
        idx = await server.register_namespace("http://launch-rm.de/vision")
        vision_system = await server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        space = types.SimpleNamespace(vision_system=vision_system, own_idx=idx)
        config = types.SimpleNamespace(vision_system_name="VisionMachine")
        outcome = {"error": None}
        seen = []

        async def measure_board_view(request):
            seen.append(request)
            return {"schema": RESULT_SCHEMA, "found": True, "complete": True, "error": outcome["error"]}

        busy = {"value": False}
        methods = {}
        await _install_measure_board(
            space, config, types.SimpleNamespace(measure_board_view=measure_board_view), methods,
            types.SimpleNamespace(state_names=lambda: ("Operational", "Ready")), lambda: busy["value"],
        )
        self.assertEqual(list(methods), ["MeasureBoard"])
        method = server.get_node(ua.NodeId("VisionMachine.MeasureBoard", idx))

        result_json, error = await vision_system.call_method(method, "{}")
        self.assertEqual(error, 0)
        self.assertTrue(json.loads(result_json)["complete"])
        self.assertEqual((seen[0].board, seen[0].frames), (None, 3))

        result_json, error = await vision_system.call_method(method, "kaputt")
        self.assertEqual(error, int(VisionErrorCode.INVALID_ARGUMENT))
        self.assertEqual(json.loads(result_json)["error"], "INVALID_ARGUMENT")

        outcome["error"] = "NO_FRAME"
        _, error = await vision_system.call_method(method, "{}")
        self.assertEqual(error, int(VisionErrorCode.DETECTION_FAILED))

        busy["value"] = True
        _, error = await vision_system.call_method(method, "{}")
        self.assertEqual(error, int(VisionErrorCode.BUSY))


if __name__ == "__main__":
    unittest.main()
