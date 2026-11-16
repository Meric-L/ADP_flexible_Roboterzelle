"""MeasureTagReference / PlanTagReferenceView: request, result, source, OPC UA.

The source gets a fake camera that delivers **rendered** images
(`render_reference_scene`: a tag on a bright surface, the surface borders are the
reference edges). The true `T_module_tag` is known, the start value is shifted in
the tag plane, and the measurement must recover it, with the real detector and
the real edge search, only without a camera.

Without OpenCV/numpy/asyncua the tests are skipped.
"""

import asyncio
import json
import math
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path

try:
    import cv2
    import numpy as np
    from asyncua import Server, ua

    _ = (cv2.imread, cv2.aruco, np.ndarray)

    from tagloc import geometry, tagmap
    from tagloc.detector import build_detector
    from tagloc.geometry import (
        compose,
        from_rotation_translation,
        invert,
        pose_to_dict,
        rotation_distance_rad,
        rotation_from_rvec,
        translation_distance_m,
    )
    from tagloc.reference_edges import ReferenceLine, catalog_rpy_deg, plan_reference_view
    from tools.make_synthetic_scene import render_reference_scene, synthetic_calibration
    from vision_server.detection.apriltag import AprilTagDetectionSource
    from vision_server.errors import VisionErrorCode
    from vision_server.runner import _Activity, _install_measure_tags, _install_tag_reference
    from vision_server.tagreference import (
        NO_FRAME,
        RESULT_SCHEMA,
        TAG_NOT_SEEN,
        VIEW_SCHEMA,
        build_result,
        build_view,
        combine_tag_pose,
        dumps,
        keep_guess_rotation,
        parse_request,
        summarize_lines,
    )

    from tests.test_apriltag_source import FakeCamera, profile_config
except Exception:  # pragma: no cover - numpy/cv2/asyncua missing
    np = None

TAG_ID = 7
TAG_SIZE_M = 0.08
#: Surface in the module frame; edge A: (0,0.2)->(0,0), edge B: (0,0)->(0.3,0).
POLYGON = [(0.0, 0.0, 0.0), (0.3, 0.0, 0.0), (0.3, 0.2, 0.0), (0.0, 0.2, 0.0)]


def request_text(**overrides) -> str:
    """A valid request, with individual fields overridable (`None` removes them)."""
    data = {
        "schema": "wsc.vision.tagreference.request/1",
        "tagId": TAG_ID,
        "moduleTagGuess": {"position": [0.1, 0.08, 0.0], "orientation": [0, 0, 0, 1]},
        "lines": [
            {"id": "A", "start": [0, 0.2, 0], "end": [0, 0, 0]},
            {"id": "B", "start": [0, 0, 0], "end": [0.3, 0, 0]},
        ],
    }
    data.update(overrides)
    return json.dumps({key: value for key, value in data.items() if value is not None})


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class RequestTest(unittest.TestCase):
    def test_defaults(self):
        request = parse_request(request_text())
        self.assertEqual(request.tag_id, TAG_ID)
        self.assertIsNone(request.tag_size_m)
        self.assertEqual(request.frames, 3)
        self.assertAlmostEqual(request.search_band_m, 0.015)
        self.assertAlmostEqual(request.line_extent_m, 0.10)
        # Not 0: frontal, the tag pose tends to flip into the mirror solution.
        self.assertAlmostEqual(request.tilt_deg, 10.0)
        self.assertEqual([line.id for line in request.lines], ["A", "B"])
        np.testing.assert_allclose(request.pose_module_tag_guess[:3, 3], [0.1, 0.08, 0.0])
        np.testing.assert_allclose(request.pose_module_tag_guess[:3, :3], np.eye(3), atol=1e-12)

    def test_all_options(self):
        request = parse_request(
            request_text(
                tagSizeM=0.1,
                searchBandM=0.02,
                frames=5,
                view={"lineExtentM": 0.2, "tiltDeg": -5},
                moduleTagGuess={"position": [0, 0, 0], "orientation": [0, 0, 1, 1]},
            )
        )
        self.assertEqual(
            (request.tag_size_m, request.search_band_m, request.frames, request.line_extent_m, request.tilt_deg),
            (0.1, 0.02, 5, 0.2, -5.0),
        )
        # Quaternion is normalised: 90 degrees about z.
        np.testing.assert_allclose(request.pose_module_tag_guess[:3, 0], [0, 1, 0], atol=1e-12)
        self.assertEqual(parse_request(request_text(view=None)).tilt_deg, 10.0)
        self.assertEqual(parse_request(request_text(view={"tiltDeg": 0})).tilt_deg, 0.0)

    def test_up_to_four_lines(self):
        line = {"start": [0, 0, 0], "end": [0.1, 0, 0]}
        four = [{"id": name, **line} for name in "ABCD"]
        self.assertEqual(len(parse_request(request_text(lines=four)).lines), 4)

    def test_broken_requests(self):
        line = {"id": "A", "start": [0, 0, 0], "end": [0.1, 0, 0]}
        bad = {
            "kein JSON": "kaputt",
            "leer": "",
            "Liste": "[]",
            "Schema": request_text(schema="falsch"),
            "tagId fehlt": request_text(tagId=None),
            "tagId negativ": request_text(tagId=-1),
            "tagId Text": request_text(tagId="7"),
            "tagId bool": request_text(tagId=True),
            "tagSizeM null-ig": request_text(tagSizeM=0),
            "tagSizeM Text": request_text(tagSizeM="0.08"),
            "Startwert fehlt": request_text(moduleTagGuess=None),
            "Startwert Position": request_text(moduleTagGuess={"position": [0, 0], "orientation": [0, 0, 0, 1]}),
            "Startwert Quaternion": request_text(moduleTagGuess={"position": [0, 0, 0], "orientation": [0, 0, 1]}),
            "Nullquaternion": request_text(moduleTagGuess={"position": [0, 0, 0], "orientation": [0, 0, 0, 0]}),
            "keine Kanten": request_text(lines=[]),
            "fuenf Kanten": request_text(lines=[{**line, "id": str(i)} for i in range(5)]),
            "Kante ohne id": request_text(lines=[{"start": [0, 0, 0], "end": [0.1, 0, 0]}]),
            "doppelte id": request_text(lines=[line, line]),
            "Kante zu kurz": request_text(lines=[{**line, "end": [0.01, 0, 0]}]),
            "Kante ohne Ende": request_text(lines=[{"id": "A", "start": [0, 0, 0]}]),
            "Suchband klein": request_text(searchBandM=0.001),
            "Suchband gross": request_text(searchBandM=0.06),
            "frames 0": request_text(frames=0),
            "frames 11": request_text(frames=11),
            "frames bool": request_text(frames=True),
            "view Text": request_text(view="x"),
            "Auszug 0": request_text(view={"lineExtentM": 0}),
            "Kippung 80": request_text(view={"tiltDeg": 80}),
        }
        for name, text in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                parse_request(text)


def _rotation_z(degrees: float):
    return rotation_from_rvec([0.0, 0.0, math.radians(degrees)])


def _in_plane(dx: float, dy: float, degrees: float):
    return from_rotation_translation(_rotation_z(degrees), [dx, dy, 0.0])


class SceneSource:
    """Shared scene: true pose, start value, source with fake camera.

    Not a test case, only helper functions; a mixin for the test classes.
    """

    #: Start error in the tag plane: just under 1 cm, 2 degrees.
    START_ERROR = (0.008, -0.006, 2.0)
    IMAGE_SIZE = (640, 480)
    FOCAL_PX = 600.0
    LINES = (
        ReferenceLine("A", np.array([0.0, 0.2, 0.0]), np.array([0.0, 0.0, 0.0])) if np is not None else None,
        ReferenceLine("B", np.array([0.0, 0.0, 0.0]), np.array([0.3, 0.0, 0.0])) if np is not None else None,
    )

    def make_scene(self, tilt_deg=10.0, noise_sigma=1.0, frames=3, min_standoff_m=0.25):
        self.calibration = synthetic_calibration(self.IMAGE_SIZE, focal_px=self.FOCAL_PX, frame_id="cam_flange")
        self.pose_true = from_rotation_translation(_rotation_z(30.0), [0.10, 0.08, 0.0])
        self.guess = invert(compose(_in_plane(*self.START_ERROR), invert(self.pose_true)))
        self.plan = plan_reference_view(
            self.guess, self.LINES, self.calibration, tag_size_m=TAG_SIZE_M, tilt_deg=tilt_deg,
            min_standoff_m=min_standoff_m,
        )
        self.pose_cam_tag = invert(self.plan.pose_tag_cam)
        self.images = []
        for index in range(frames):
            image, _ = render_reference_scene(
                self.pose_cam_tag,
                self.pose_true,
                self.calibration,
                POLYGON,
                tag_id=TAG_ID,
                tag_size_m=TAG_SIZE_M,
                noise_sigma=noise_sigma,
                seed=20261020 + index,
            )
            self.images.append(image)

    def request(self, **overrides):
        position, orientation = geometry.to_position_quaternion(self.guess)
        overrides.setdefault("tagSizeM", TAG_SIZE_M)
        return parse_request(
            request_text(
                moduleTagGuess={"position": list(position), "orientation": list(orientation)},
                lines=[
                    {"id": line.id, "start": list(line.start), "end": list(line.end)} for line in self.LINES
                ],
                **overrides,
            )
        )

    def source(self, folder, source_class=None, **config):
        camera = FakeCamera()
        source = (source_class or AprilTagDetectionSource)(
            profile_config(Path(folder), frame_id="cam_flange", **config), camera=camera
        )
        source._detector = build_detector("tag36h11", "aruco", subpixel_corner_refinement=True)
        source._tag_map = tagmap.empty_tag_map()
        source._calibration = self.calibration
        return source, camera

    @staticmethod
    def feed(camera, images):
        """First an old image (already in the buffer at call time), then the fresh ones.

        `FakeCamera.latest_frame` fetches the next image on each access;
        `measure_tag_reference` reads it once in advance, so that is the old one.
        """
        camera.push_frame("aus-der-fahrt")
        for image in images:
            camera.push_frame(image)

    def assert_true_pose(self, correction, position_m, angle_deg):
        pose = geometry.from_position_quaternion(
            correction["moduleTag"]["position"], correction["moduleTag"]["orientation"]
        )
        self.assertLess(translation_distance_m(pose, self.pose_true), position_m)
        self.assertLess(math.degrees(rotation_distance_rad(pose, self.pose_true)), angle_deg)


class AmbiguousSource(AprilTagDetectionSource):
    """Real detection, but tag poses are reported as ambiguous and with a wrong rotation.

    `ambiguous_frames`: indices of the images (0-based) that are falsified this way.
    The rotation is tilted by 10 degrees about tag x, like a mirror solution.
    """

    ambiguous_frames: set = set()

    def _detect_in_camera(self, image, sizes, tag_ids):
        tag_poses, size = super()._detect_in_camera(image, sizes, tag_ids)
        index = getattr(self, "_calls", 0)
        self._calls = index + 1
        if index in self.ambiguous_frames:
            tilt = geometry.from_rvec_tvec((math.radians(10.0), 0.0, 0.0), (0.0, 0.0, 0.0))
            tag_poses = [
                replace(item, pose_cam_tag=compose(item.pose_cam_tag, tilt), ambiguity_ratio=0.95)
                for item in tag_poses
            ]
        return tag_poses, size


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class SourceTest(SceneSource, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.make_scene()

    async def test_the_start_error_is_recovered(self):
        request = self.request()
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            self.feed(camera, self.images)
            result = await source.measure_tag_reference(request)
        self.assertIsNone(result["error"], result)
        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertEqual(result["framesUsed"], 3)
        self.assertEqual(result["imageSize"], [640, 480])
        self.assertEqual(result["intrinsicsSource"], "calibration")
        self.assertLessEqual(result["capturedAt"], result["capturedUntil"])
        self.assertFalse(result["tag"]["ambiguous"])
        from vision_server.measuretag import view_angle_deg

        self.assertAlmostEqual(
            result["tag"]["viewAngleDeg"], view_angle_deg(self.pose_cam_tag), delta=0.5
        )
        self.assertEqual([line["id"] for line in result["lines"]], ["A", "B"])
        for line in result["lines"]:
            self.assertTrue(line["found"], line)
            self.assertFalse(line["ambiguous"])
            self.assertIsNone(line["reason"])
        correction = result["correction"]
        self.assertEqual(correction["dofs"], "full")
        self.assertEqual(result["warnings"], [])
        self.assert_true_pose(correction, 0.001, 0.3)
        # Catalogue format: position/rotationDeg describe the same pose.
        self.assertEqual(correction["position"], correction["moduleTag"]["position"])
        pose = geometry.from_position_quaternion(
            correction["moduleTag"]["position"], correction["moduleTag"]["orientation"]
        )
        np.testing.assert_allclose(correction["rotationDeg"], catalog_rpy_deg(pose), atol=1e-9)
        # The start value really was off (otherwise the test would be empty).
        self.assertGreater(translation_distance_m(self.guess, self.pose_true), 0.009)
        # Correction in mm, in the tag frame: as large as the start error (sign: D = start error^-1).
        self.assertGreater(math.hypot(*correction["deltaMm"]), 5.0)
        self.assertLess(correction["spreadMm"], 0.5)
        self.assertLess(correction["residualMm"], 0.5)
        json.loads(dumps(result))

    async def test_accuracy_at_640x480(self):
        """Realistic hand camera: 640x480, f 600 px, tag 80 mm, ~0.35 m, tilted by 10 degrees.

        Target: < 1 mm and < 0.3 degrees. The values reached are in the message.
        """
        errors = []
        for tilt, minimum in ((10.0, 0.25), (15.0, 0.25), (10.0, 0.35)):
            self.make_scene(tilt_deg=tilt, min_standoff_m=minimum)
            with tempfile.TemporaryDirectory() as folder:
                source, camera = self.source(folder)
                self.feed(camera, self.images)
                result = await source.measure_tag_reference(self.request())
            self.assertIsNone(result["error"], result)
            pose = geometry.from_position_quaternion(
                result["correction"]["moduleTag"]["position"],
                result["correction"]["moduleTag"]["orientation"],
            )
            errors.append(
                (
                    tilt,
                    self.plan.standoff_m,
                    1000.0 * translation_distance_m(pose, self.pose_true),
                    math.degrees(rotation_distance_rad(pose, self.pose_true)),
                )
            )
        for tilt, standoff, millimetres, degrees in errors:
            with self.subTest(kippung=tilt, abstand=standoff):
                self.assertTrue(0.25 <= standoff <= 0.6, standoff)
                # Reached: 0.25 to 0.63 mm and 0.07 to 0.11 degrees.
                self.assertLess(millimetres, 1.0, errors)
                self.assertLess(degrees, 0.3, errors)

    async def test_ambiguous_tag_in_some_frames_uses_the_clear_rotation(self):
        # Image 0 ambiguous with a wrong rotation, the others clear: the rotation of
        # the clear ones applies to all, the correction stays full.
        class Source(AmbiguousSource):
            ambiguous_frames = {0}

        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder, Source)
            self.feed(camera, self.images)
            result = await source.measure_tag_reference(self.request())
        self.assertIsNone(result["error"], result)
        self.assertFalse(result["tag"]["ambiguous"])
        self.assertEqual(result["correction"]["dofs"], "full")
        self.assert_true_pose(result["correction"], 0.0012, 0.35)

    async def test_ambiguous_tag_in_all_frames_keeps_the_guess_rotation(self):
        class Source(AmbiguousSource):
            ambiguous_frames = {0, 1, 2}

        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder, Source)
            self.feed(camera, self.images)
            result = await source.measure_tag_reference(self.request())
        self.assertIsNone(result["error"], result)
        self.assertTrue(result["tag"]["ambiguous"])
        correction = result["correction"]
        pose = geometry.from_position_quaternion(
            correction["moduleTag"]["position"], correction["moduleTag"]["orientation"]
        )
        # Rotation is exactly the start value; only the position is corrected.
        np.testing.assert_allclose(pose[:3, :3], self.guess[:3, :3], atol=1e-9)
        self.assertEqual(correction["deltaRotDeg"], 0.0)
        self.assertEqual(correction["spreadDeg"], 0.0)
        self.assertLess(translation_distance_m(pose, self.pose_true), translation_distance_m(self.guess, self.pose_true))
        self.assertLess(translation_distance_m(pose, self.pose_true), 0.003)
        self.assertTrue(any("mehrdeutig" in warning for warning in result["warnings"]))

    async def test_missing_line_reduces_the_degrees_of_freedom(self):
        far = {"id": "fern", "start": [-1.0, 0.0, 0.0], "end": [-1.0, 0.2, 0.0]}
        lines = [far, {"id": "B", "start": [0, 0, 0], "end": [0.3, 0, 0]}]
        position, orientation = geometry.to_position_quaternion(self.guess)
        request = parse_request(
            request_text(
                moduleTagGuess={"position": list(position), "orientation": list(orientation)},
                lines=lines,
                tagSizeM=TAG_SIZE_M,
            )
        )
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            self.feed(camera, self.images)
            result = await source.measure_tag_reference(request)
        self.assertIsNone(result["error"])
        self.assertEqual(result["correction"]["dofs"], "partial")
        self.assertEqual(
            [(line["id"], line["found"], line["reason"]) for line in result["lines"]],
            [("fern", False, "NOT_VISIBLE"), ("B", True, None)],
        )
        self.assertIsNone(result["lines"][0]["offsetMm"])
        self.assertTrue(any("Teilkorrektur" in warning for warning in result["warnings"]))

    async def test_a_wrong_guess_beyond_the_band_is_flagged(self):
        # Edge A sits 2 cm beside the start value, search band only 5 mm: nothing found or a warning.
        self.guess = invert(compose(_in_plane(0.02, 0.0, 0.0), invert(self.pose_true)))
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            self.feed(camera, self.images)
            result = await source.measure_tag_reference(self.request(searchBandM=0.005))
        self.assertIsNone(result["error"])
        self.assertNotEqual(result["correction"]["dofs"], "full")

    async def test_placeholder_intrinsics_are_flagged(self):
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            source._calibration = replace(self.calibration, calibration_id=PLACEHOLDER_CALIBRATION_ID)
            self.feed(camera, self.images)
            result = await source.measure_tag_reference(self.request())
        self.assertEqual(result["intrinsicsSource"], "placeholder")
        self.assertTrue(any("Platzhalter" in warning for warning in result["warnings"]))

    async def test_tag_not_seen(self):
        blank = np.full((480, 640, 3), 120, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            self.feed(camera, [blank] * 3)
            result = await source.measure_tag_reference(self.request())
        self.assertEqual(result["error"], TAG_NOT_SEEN)
        self.assertEqual(result["framesUsed"], 0)
        self.assertIsNone(result["correction"])
        self.assertEqual(result["lines"], [])
        self.assertIsNone(result["tag"])
        json.loads(dumps(result))

    async def test_a_tag_with_another_id_is_not_the_tag(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            self.feed(camera, self.images)
            result = await source.measure_tag_reference(parse_request(request_text(tagId=9)))
        self.assertEqual(result["error"], TAG_NOT_SEEN)

    async def test_no_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            camera.push_frame("alt")
            _ = camera.latest_frame  # already in the buffer before the call: does not count
            result = await source.measure_tag_reference(self.request())
        self.assertEqual(result["error"], NO_FRAME)
        self.assertEqual(result["framesUsed"], 0)
        self.assertIsNone(result["capturedAt"])
        self.assertIsNone(result["correction"])
        json.loads(dumps(result))

    async def test_tag_size_from_request_wins(self):
        # A wrongly given tag (half the size) visibly shifts the measurement: the request
        # size must arrive (priority over tag map and default).
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            self.feed(camera, self.images)
            right = await source.measure_tag_reference(self.request(tagSizeM=TAG_SIZE_M))
            self.feed(camera, self.images)
            wrong = await source.measure_tag_reference(self.request(tagSizeM=TAG_SIZE_M / 2))
        right_z = right["tag"]["poseInCamera"]["position"][2]
        wrong_z = wrong["tag"]["poseInCamera"]["position"][2]
        self.assertAlmostEqual(wrong_z / right_z, 0.5, delta=0.02)

    def test_plan_view(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder)
        view = source.plan_tag_reference_view(self.request())
        self.assertEqual(view["schema"], VIEW_SCHEMA)
        self.assertTrue(view["fits"])
        self.assertEqual(view["uncovered"], [])
        self.assertTrue(0.25 <= view["standoffM"] <= 0.6)
        self.assertIsNone(view["error"])
        expected = plan_reference_view(
            self.guess, self.LINES, self.calibration, tag_size_m=TAG_SIZE_M, tilt_deg=10.0
        )
        np.testing.assert_allclose(
            view["camInTag"]["position"], pose_to_dict(expected.pose_tag_cam)["position"], atol=1e-9
        )
        # The tilt from the request takes effect (default 10, not 0).
        frontal = source.plan_tag_reference_view(self.request(view={"tiltDeg": 0}))
        self.assertNotAlmostEqual(
            frontal["camInTag"]["position"][1], view["camInTag"]["position"][1], places=3
        )
        far = parse_request(
            request_text(
                tagSizeM=TAG_SIZE_M,
                lines=[{"id": "fern", "start": [-1.0, 0, 0], "end": [-1.0, 0.2, 0]}],
            )
        )
        self.assertFalse(source.plan_tag_reference_view(far)["fits"])
        self.assertEqual(source.plan_tag_reference_view(far)["uncovered"], ["fern"])


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class ResultTest(unittest.TestCase):
    def test_error_result_is_valid_json_with_empty_fields(self):
        result = build_result(error="INVALID_ARGUMENT: kaputt")
        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertEqual(
            (result["correction"], result["lines"], result["tag"], result["warnings"]),
            (None, [], None, []),
        )
        self.assertEqual(result["error"], "INVALID_ARGUMENT: kaputt")
        json.loads(dumps(result))

    def test_result_fields(self):
        from tagloc.reference_edges import InPlaneCorrection, LineMeasurement

        from tests.test_apriltag_source import tag_pose

        guess = from_rotation_translation(np.eye(3), [0.1, 0.0, 0.0])
        pose = compose(guess, from_rotation_translation(_rotation_z(10.0), [0.002, -0.001, 0.0]))
        correction = InPlaneCorrection("full", np.array([0.0018, -0.0006]), 0.4, pose, 0.0003)

        def measurement(**kwargs):
            values = dict(
                id="A", found=True, guess_point_tag=np.zeros(3), guess_direction_tag=np.array([1.0, 0, 0]),
                point_tag=np.zeros(3), direction_tag=np.array([1.0, 0, 0]), offset_m=0.0018, angle_deg=0.4,
                support_px=210.0, residual_px=0.3, contrast=41.0, candidates=2, ambiguous=False,
                reason=None, extent_m=0.1,
            )
            values.update(kwargs)
            return LineMeasurement(**values)

        line_frames = [
            [measurement(), measurement(id="B", found=False, reason="NO_EDGE", offset_m=None, angle_deg=None,
                                         point_tag=None, direction_tag=None, support_px=0.0, candidates=0)],
            [measurement(offset_m=0.0022), measurement(id="B", found=False, reason="NOT_VISIBLE", offset_m=None,
                                                       angle_deg=None, point_tag=None, direction_tag=None,
                                                       support_px=0.0, candidates=0)],
        ]
        tag_poses = [tag_pose(7, (0.0, 0.0, 0.35), error_px=0.4), tag_pose(7, (0.0, 0.0, 0.35), error_px=0.6)]
        frontal = geometry.from_rvec_tvec((math.pi, 0, 0), (0.0, 0.0, 0.35))  # tag z points at the camera
        result = build_result(
            captured_at=__import__("datetime").datetime(2026, 10, 20, 12, 0, 0),
            captured_until=__import__("datetime").datetime(2026, 10, 20, 12, 0, 1),
            image_size=(640, 480),
            intrinsics_source="calibration",
            frames_used=2,
            tag_poses=tag_poses,
            tag_pose=frontal,
            tag_ambiguous=False,
            line_frames=line_frames,
            correction=correction,
            spread={"spreadMm": 0.2, "spreadDeg": 0.05},
            warnings=["x"],
        )
        self.assertEqual(result["imageSize"], [640, 480])
        self.assertEqual(result["framesUsed"], 2)
        self.assertEqual(result["intrinsicsSource"], "calibration")
        self.assertEqual(result["capturedAt"], "2026-10-20T12:00:00.000")
        self.assertEqual(result["warnings"], ["x"])
        self.assertIsNone(result["error"])
        tag = result["tag"]
        self.assertAlmostEqual(tag["reprojectionErrorPx"], 0.5)
        self.assertAlmostEqual(tag["viewAngleDeg"], 0.0, places=6)
        self.assertFalse(tag["ambiguous"])
        np.testing.assert_allclose(tag["poseInCamera"]["position"], [0, 0, 0.35])
        found, missing = result["lines"]
        self.assertEqual(found["id"], "A")
        self.assertTrue(found["found"])
        self.assertAlmostEqual(found["offsetMm"], 2.0)  # mean of 1.8 and 2.2 mm, in mm
        self.assertAlmostEqual(found["angleDeg"], 0.4)
        self.assertEqual((found["supportPx"], found["candidates"], found["reason"]), (210.0, 2, None))
        self.assertEqual((missing["found"], missing["offsetMm"], missing["reason"]), (False, None, "NOT_VISIBLE"))
        data = result["correction"]
        self.assertEqual(data["dofs"], "full")
        np.testing.assert_allclose(data["deltaMm"], [1.8, -0.6])
        self.assertAlmostEqual(data["deltaRotDeg"], 0.4)
        self.assertEqual((data["spreadMm"], data["spreadDeg"]), (0.2, 0.05))
        self.assertAlmostEqual(data["residualMm"], 0.3)
        np.testing.assert_allclose(data["moduleTag"]["position"], pose[:3, 3])
        self.assertEqual(data["position"], data["moduleTag"]["position"])
        np.testing.assert_allclose(data["rotationDeg"], [0, 0, 10], atol=1e-9)
        json.loads(dumps(result))

    def test_summary_flags_ambiguity_conservatively(self):
        from tagloc.reference_edges import LineMeasurement

        def measurement(ambiguous, offset):
            return LineMeasurement(
                id="A", found=True, guess_point_tag=np.zeros(3), guess_direction_tag=np.array([1.0, 0, 0]),
                point_tag=np.zeros(3), direction_tag=np.array([1.0, 0, 0]), offset_m=offset, angle_deg=0.0,
                support_px=100.0, residual_px=0.2, contrast=30.0, candidates=2 if ambiguous else 1,
                ambiguous=ambiguous, reason=None,
            )

        [line] = summarize_lines([[measurement(True, 0.010)], [measurement(False, 0.002)]])
        self.assertTrue(line["ambiguous"])
        self.assertAlmostEqual(line["offsetMm"], 2.0)  # only the clear image counts
        self.assertEqual(line["candidates"], 2)
        self.assertEqual(summarize_lines([]), [])

    def test_view_result(self):
        self.assertEqual(
            build_view(error="INVALID_ARGUMENT: x"),
            {"schema": VIEW_SCHEMA, "camInTag": None, "standoffM": None, "fits": False, "uncovered": [],
             "error": "INVALID_ARGUMENT: x"},
        )
        calibration = synthetic_calibration((640, 480), focal_px=600.0)
        plan = plan_reference_view(
            from_rotation_translation(np.eye(3), [0.1, 0.08, 0.0]),
            [ReferenceLine("A", np.array([0.0, 0.2, 0.0]), np.array([0.0, 0.0, 0.0]))],
            calibration, tag_size_m=TAG_SIZE_M,
        )
        view = build_view(plan)
        self.assertEqual(set(view), {"schema", "camInTag", "standoffM", "fits", "uncovered", "error"})
        self.assertEqual(view["camInTag"], pose_to_dict(plan.pose_tag_cam))
        json.loads(dumps(view))

    def test_combine_tag_pose_orientation_only_from_clear_frames(self):
        from tests.test_apriltag_source import tag_pose

        clear = tag_pose(7, (0.01, 0.0, 0.35))
        tilted = replace(
            tag_pose(7, (0.03, 0.0, 0.35), ambiguity=0.95),
            pose_cam_tag=geometry.from_rvec_tvec((math.radians(20), 0, 0), (0.03, 0.0, 0.35)),
        )
        pose, ambiguous = combine_tag_pose([clear, tilted])
        self.assertFalse(ambiguous)
        np.testing.assert_allclose(pose[:3, 3], [0.02, 0.0, 0.35])
        np.testing.assert_allclose(pose[:3, :3], np.eye(3), atol=1e-9)
        pose, ambiguous = combine_tag_pose([tilted])
        self.assertTrue(ambiguous)

    def test_keep_guess_rotation_moves_only_the_origin(self):
        from tagloc.reference_edges import InPlaneCorrection

        guess = from_rotation_translation(_rotation_z(30.0), [0.1, 0.08, 0.0])
        # True pose: rotated by 3 degrees, origin shifted by (3, -2) mm (in the start value's tag frame).
        true = compose(guess, from_rotation_translation(_rotation_z(3.0), [0.003, -0.002, 0.0]))
        delta = invert(compose(invert(guess), true))  # D with T_tag_module_new = D * T_tag_module_guess
        # D acts in the tag frame: T_module_tag_new = guess * D^-1  =>  D^-1 = inv(guess) * true
        full = InPlaneCorrection(
            "full", delta[:2, 3].copy(), math.degrees(math.atan2(delta[1, 0], delta[0, 0])), true, 0.0
        )
        kept = keep_guess_rotation(full, guess)
        np.testing.assert_allclose(kept.pose_module_tag[:3, :3], guess[:3, :3], atol=1e-12)
        np.testing.assert_allclose(kept.pose_module_tag[:3, 3], true[:3, 3], atol=1e-12)
        self.assertEqual(kept.delta_rot_deg, 0.0)
        # delta_xy is the pure shift that yields the same position.
        np.testing.assert_allclose(
            guess[:3, :3] @ np.array([-kept.delta_xy_m[0], -kept.delta_xy_m[1], 0.0]) + guess[:3, 3],
            true[:3, 3],
            atol=1e-12,
        )
        none = InPlaneCorrection("none", np.zeros(2), 0.0, guess.copy(), 0.0)
        np.testing.assert_array_equal(keep_guess_rotation(none, guess).pose_module_tag, guess)


@unittest.skipUnless(np is not None, "numpy/cv2/asyncua fehlen")
class OpcUaTest(SceneSource, unittest.IsolatedAsyncioTestCase):
    async def start(
        self, source, busy, blocked_by=lambda: False, state="Operational", activity=None,
        annotator=None, debug_dir=None,
    ):
        server = Server()
        await server.init()
        idx = await server.register_namespace("http://launch-rm.de/vision")
        vision_system = await server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        node = await vision_system.add_variable(
            ua.NodeId("VisionMachine.LastTagReference", idx),
            ua.QualifiedName("LastTagReference", idx), "", ua.VariantType.String,
        )
        space = types.SimpleNamespace(vision_system=vision_system, own_idx=idx, last_tag_reference=node)
        config = types.SimpleNamespace(
            vision_system_name="VisionMachine", tag_reference_debug_dir=debug_dir
        )
        methods = {}
        await _install_tag_reference(
            space, config, source, methods,
            types.SimpleNamespace(state_names=lambda: (state, "Ready")),
            busy, blocked_by=blocked_by, activity=activity, annotator=annotator,
        )
        self.assertEqual(
            list(methods), ["SetTagReferencePreview", "MeasureTagReference", "PlanTagReferenceView"]
        )
        self.set_preview_method = server.get_node(ua.NodeId("VisionMachine.SetTagReferencePreview", idx))
        return (
            vision_system,
            node,
            server.get_node(ua.NodeId("VisionMachine.MeasureTagReference", idx)),
            server.get_node(ua.NodeId("VisionMachine.PlanTagReferenceView", idx)),
        )

    async def test_methods_with_a_rendered_scene(self):
        self.make_scene()
        busy = {"value": False}
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder)
            vision_system, node, measure, plan = await self.start(source, lambda: busy["value"])
            request = request_text(
                tagSizeM=TAG_SIZE_M,
                moduleTagGuess=pose_to_dict(self.guess),
                lines=[{"id": line.id, "start": list(line.start), "end": list(line.end)} for line in self.LINES],
            )

            async def feed():
                # Only after the start of the call, otherwise these would be "old" images.
                await asyncio.sleep(0.02)
                for image in self.images:
                    camera.push_frame(image)

            source._config = replace(source._config, capture_timeout_s=2.0)
            feeder = asyncio.create_task(feed())
            result_json, error = await vision_system.call_method(measure, request)
            await feeder
            self.assertEqual(error, int(VisionErrorCode.OK), result_json)
            result = json.loads(result_json)
            self.assertEqual(result["schema"], RESULT_SCHEMA)
            self.assertEqual(result["correction"]["dofs"], "full")
            self.assert_true_pose(result["correction"], 0.001, 0.3)
            self.assertEqual(json.loads(await node.read_value()), result)

            view_json, error = await vision_system.call_method(plan, request)
            self.assertEqual(error, int(VisionErrorCode.OK))
            view = json.loads(view_json)
            self.assertEqual(view["schema"], VIEW_SCHEMA)
            self.assertTrue(view["fits"])
            self.assertIsNone(view["error"])

            # No tag in the image: DETECTION_FAILED, ResultJson stays valid, LastTagReference shows it.
            blank = np.full((480, 640, 3), 120, dtype=np.uint8)

            async def feed_blank():
                await asyncio.sleep(0.02)
                for _ in range(3):
                    camera.push_frame(blank)

            feeder = asyncio.create_task(feed_blank())
            result_json, error = await vision_system.call_method(measure, request)
            await feeder
            self.assertEqual(error, int(VisionErrorCode.DETECTION_FAILED))
            self.assertEqual(json.loads(result_json)["error"], TAG_NOT_SEEN)
            self.assertEqual(json.loads(await node.read_value())["error"], TAG_NOT_SEEN)

            # No fresh image: NO_FRAME.
            source._config = replace(source._config, capture_timeout_s=0.05)
            result_json, error = await vision_system.call_method(measure, request)
            self.assertEqual(error, int(VisionErrorCode.DETECTION_FAILED))
            self.assertEqual(json.loads(result_json)["error"], NO_FRAME)

    async def test_refusals(self):
        self.make_scene()
        busy = {"value": False}
        blocked = {"value": False}
        calls = []

        async def measure(request, artifacts=None):
            calls.append(request)
            return build_result(error=None, frames_used=0)

        def plan(request):
            calls.append(request)
            return build_view(error=None)

        source = types.SimpleNamespace(measure_tag_reference=measure, plan_tag_reference_view=plan)
        vision_system, node, measure_method, plan_method = await self.start(
            source, lambda: busy["value"], lambda: blocked["value"]
        )
        good = request_text()

        for method, schema in ((measure_method, RESULT_SCHEMA), (plan_method, VIEW_SCHEMA)):
            result_json, error = await vision_system.call_method(method, "kaputt")
            self.assertEqual(error, int(VisionErrorCode.INVALID_ARGUMENT))
            result = json.loads(result_json)
            self.assertEqual(result["schema"], schema)
            self.assertTrue(result["error"].startswith("INVALID_ARGUMENT"))
            _, error = await vision_system.call_method(method, "")
            self.assertEqual(error, int(VisionErrorCode.INVALID_ARGUMENT))
        self.assertEqual(calls, [])
        self.assertEqual(await node.read_value(), "")  # refusals do not overwrite the display

        busy["value"] = True
        result_json, error = await vision_system.call_method(measure_method, good)
        self.assertEqual(error, int(VisionErrorCode.BUSY))
        self.assertTrue(json.loads(result_json)["error"].startswith("BUSY"))
        self.assertEqual(calls, [])
        # Planning needs no image and disturbs nothing: never BUSY.
        _, error = await vision_system.call_method(plan_method, good)
        self.assertEqual(error, int(VisionErrorCode.OK))
        busy["value"] = False
        blocked["value"] = True
        _, error = await vision_system.call_method(measure_method, good)
        self.assertEqual(error, int(VisionErrorCode.BUSY))
        blocked["value"] = False
        _, error = await vision_system.call_method(measure_method, good)
        self.assertEqual(error, int(VisionErrorCode.OK))

    async def test_not_operational_is_invalid_state(self):
        source = types.SimpleNamespace(
            measure_tag_reference=lambda request, artifacts=None: None, plan_tag_reference_view=lambda request: None
        )
        vision_system, _, measure_method, plan_method = await self.start(
            source, lambda: False, state="Preoperational"
        )
        for method in (measure_method, plan_method):
            result_json, error = await vision_system.call_method(method, request_text())
            self.assertEqual(error, int(VisionErrorCode.INVALID_STATE))
            self.assertTrue(json.loads(result_json)["error"].startswith("INVALID_STATE"))

    async def test_a_running_measurement_blocks_the_others(self):
        # During a measurement, further measurements and MeasureTags are BUSY, and vice versa.
        release = asyncio.Event()

        async def measure(request, artifacts=None):
            await release.wait()
            return build_result(error=None)

        source = types.SimpleNamespace(
            measure_tag_reference=measure, plan_tag_reference_view=lambda request: build_view(error=None)
        )
        reference = _Activity()
        tags = _Activity()
        vision_system, _, measure_method, _ = await self.start(
            source, lambda: False, lambda: tags.active or reference.active, activity=reference
        )
        first = asyncio.create_task(vision_system.call_method(measure_method, request_text()))
        await asyncio.sleep(0.05)
        self.assertTrue(reference.active)
        _, error = await vision_system.call_method(measure_method, request_text())
        self.assertEqual(error, int(VisionErrorCode.BUSY))
        release.set()
        _, error = await first
        self.assertEqual(error, int(VisionErrorCode.OK))
        self.assertFalse(reference.active)
        tags.active = True  # MeasureTags running
        _, error = await vision_system.call_method(measure_method, request_text())
        self.assertEqual(error, int(VisionErrorCode.BUSY))

    async def test_measure_tags_is_blocked_while_a_reference_runs(self):
        server = Server()
        await server.init()
        idx = await server.register_namespace("http://launch-rm.de/vision")
        vision_system = await server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        node = await vision_system.add_variable(
            ua.NodeId("VisionMachine.LastTagMeasurement", idx),
            ua.QualifiedName("LastTagMeasurement", idx), "", ua.VariantType.String,
        )
        space = types.SimpleNamespace(vision_system=vision_system, own_idx=idx, last_tag_measurement=node)
        config = types.SimpleNamespace(vision_system_name="VisionMachine")

        async def measure_tags(request):
            return {"schema": "x", "tags": [], "error": None}

        reference = _Activity()
        await _install_measure_tags(
            space, config, types.SimpleNamespace(measure_tags=measure_tags, measure_calls=0), {},
            types.SimpleNamespace(state_names=lambda: ("Operational", "Ready")), lambda: False,
            activity=_Activity(), blocked_by=lambda: reference.active,
        )
        method = server.get_node(ua.NodeId("VisionMachine.MeasureTags", idx))
        _, error = await vision_system.call_method(method, "{}")
        self.assertEqual(error, int(VisionErrorCode.OK))
        reference.active = True
        _, error = await vision_system.call_method(method, "{}")
        self.assertEqual(error, int(VisionErrorCode.BUSY))


if __name__ == "__main__":
    unittest.main()
