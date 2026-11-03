"""MeasureTags: Tags in Kamerakoordinaten, nur aus frischen Bildern."""

import json
import math
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    import numpy as np
    from asyncua import Server, ua

    from tagloc import geometry, tagmap
    from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID, CameraCalibration
    from vision_server.camera import REALSENSE_FACTORY_CALIBRATION_ID, _realsense_calibration
    from vision_server.detection.apriltag import AprilTagDetectionSource
    from vision_server.errors import VisionErrorCode
    from vision_server.measuretag import (
        NO_FRAME,
        RESULT_SCHEMA,
        FrameMeasurement,
        SizeLookup,
        build_result,
        parse_request,
        view_angle_deg,
    )

    from tests.test_apriltag_source import FakeCamera, FakeDetector, profile_config, tag_pose
except Exception:  # pragma: no cover - numpy/asyncua fehlen
    np = None

WALL = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc) if np is not None else None


@unittest.skipUnless(np is not None, "numpy fehlt")
class RequestTest(unittest.TestCase):
    def test_defaults_and_options(self):
        request = parse_request("")
        self.assertIsNone(request.tag_ids)
        self.assertEqual(request.frames, 3)
        request = parse_request(
            '{"schema": "wsc.vision.measuretag.request/1", "tagIds": [7, 9], '
            '"tagSizeM": {"7": 0.08}, "frames": 1}'
        )
        self.assertEqual(request.tag_ids, frozenset({7, 9}))
        self.assertEqual(request.sizes_m, {7: 0.08})
        self.assertEqual(request.frames, 1)

    def test_broken_requests(self):
        for text in (
            "kaputt",
            '{"schema": "falsch"}',
            '{"tagIds": "7"}',
            '{"tagIds": [-1]}',
            '{"tagSizeM": {"x": 0.1}}',
            '{"tagSizeM": {"7": 0}}',
            '{"frames": 0}',
            '{"frames": 11}',
            '{"frames": true}',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_request(text)


def ambiguous_pose(tag_id, translation, *, tilt_deg, ratio):
    """Tag-Pose mit gekippter Orientierung und Mehrdeutigkeit `ratio`."""
    pose = tag_pose(tag_id, translation, ambiguity=ratio)
    rotation = geometry.from_rvec_tvec((math.radians(tilt_deg), 0, 0), translation)
    return types.SimpleNamespace(
        tag_id=pose.tag_id,
        pose_cam_tag=rotation,
        reprojection_error_px=pose.reprojection_error_px,
        ambiguity_ratio=ratio,
        is_ambiguous=pose.is_ambiguous,
        observation=pose.observation,
    )


@unittest.skipUnless(np is not None, "numpy fehlt")
class ResultTest(unittest.TestCase):
    def sizes(self, **request):
        tag_map = tagmap.TagMap(entries={9: tagmap.TagEntry(9, "module", 0.05)})
        return SizeLookup({int(k[1:]): v for k, v in request.items()}, tag_map, 0.08)

    def test_position_from_all_frames_orientation_only_from_clear_ones(self):
        frames = [
            FrameMeasurement(WALL, (640, 480), (tag_pose(7, (0.01, 0.0, 0.35)),)),
            FrameMeasurement(WALL, (640, 480), (ambiguous_pose(7, (0.03, 0.0, 0.35), tilt_deg=20, ratio=0.9),)),
            FrameMeasurement(WALL, (640, 480), ()),
        ]
        result = build_result(
            frames, frames_requested=3, frame_id="cam_flange", sizes=self.sizes(),
            intrinsics_source="calibration", calibration_id="cam_flange@1",
        )
        self.assertEqual(result["schema"], RESULT_SCHEMA)
        self.assertEqual((result["framesRequested"], result["framesUsed"]), (3, 3))
        self.assertEqual(result["frameConvention"], "z_forward_x_right_y_down")
        [tag] = result["tags"]
        self.assertEqual(tag["frameCount"], 2)
        np.testing.assert_allclose(tag["poseInCamera"]["position"], [0.02, 0.0, 0.35])
        # Die gekippte Loesung aus dem mehrdeutigen Bild zaehlt fuer die Drehung nicht.
        np.testing.assert_allclose(tag["poseInCamera"]["orientation"], [0, 0, 0, 1], atol=1e-9)
        self.assertFalse(tag["ambiguous"])
        self.assertAlmostEqual(tag["ambiguityRatio"], 0.9)
        self.assertEqual((tag["sizeM"], tag["sizeSource"]), (0.08, "default"))
        self.assertEqual(len(tag["cornersPx"]), 4)
        self.assertAlmostEqual(tag["distanceM"], math.hypot(0.02, 0.35))
        json.dumps(result, allow_nan=False)

    def test_only_ambiguous_frames_are_flagged(self):
        frames = [FrameMeasurement(WALL, (640, 480), (ambiguous_pose(7, (0, 0, 0.35), tilt_deg=10, ratio=0.95),))]
        [tag] = build_result(
            frames, frames_requested=1, frame_id="cam_flange", sizes=self.sizes(),
            intrinsics_source="calibration", calibration_id="",
        )["tags"]
        self.assertTrue(tag["ambiguous"])

    def test_size_sources(self):
        sizes = self.sizes(t7=0.1)
        self.assertEqual(sizes.size_and_source(7), (0.1, "request"))
        self.assertEqual(sizes.size_and_source(9), (0.05, "tagmap"))
        self.assertEqual(sizes.size_and_source(3), (0.08, "default"))
        self.assertEqual(sizes.size_for(9, 0.5), 0.05)  # Schnittstelle wie TagMap

    def test_near_edge(self):
        from vision_server.measuretag import near_edge

        size = (640, 480)
        self.assertEqual(near_edge([[300, 200], [340, 200], [340, 240], [300, 240]], size), [])
        self.assertEqual(near_edge([[10, 200], [50, 200], [50, 240], [10, 240]], size), ["left"])
        self.assertEqual(
            near_edge([[600, 460], [635, 460], [635, 478], [600, 478]], size), ["right", "bottom"]
        )
        # Im Ergebnis je Tag, aus dem letzten Bild (SQUARE liegt oben links).
        frames = [FrameMeasurement(WALL, (640, 480), (tag_pose(7, (0, 0, 0.35)),))]
        [tag] = build_result(
            frames, frames_requested=1, frame_id="cam_flange", sizes=self.sizes(),
            intrinsics_source="calibration", calibration_id="",
        )["tags"]
        self.assertEqual(tag["nearEdge"], ["left", "top"])

    def test_view_angle(self):
        frontal = geometry.from_rvec_tvec((math.pi, 0, 0), (0, 0, 0.35))
        self.assertAlmostEqual(view_angle_deg(frontal), 0.0, places=6)
        tilted = geometry.from_rvec_tvec((math.pi + math.radians(30), 0, 0), (0, 0, 0.35))
        self.assertAlmostEqual(view_angle_deg(tilted), 30.0, places=6)


class FakeCameraSource(AprilTagDetectionSource):
    """Pose vom Fake-Detektor statt aus OpenCV -- der Rest ist Produktionscode."""

    def _detect_in_camera(self, image, sizes, tag_ids):
        tag_poses = self._detector.estimate(self._detector.detect(image))
        if tag_ids is not None:
            tag_poses = [t for t in tag_poses if t.tag_id in tag_ids]
        return tag_poses, (640, 480)


@unittest.skipUnless(np is not None, "numpy fehlt")
class SourceTest(unittest.IsolatedAsyncioTestCase):
    def source(self, folder, tag_poses):
        camera = FakeCamera()
        source = FakeCameraSource(profile_config(Path(folder), frame_id="cam_flange"), camera=camera)
        source._detector = FakeDetector(tag_poses)
        source._tag_map = tagmap.empty_tag_map()
        source._calibration = types.SimpleNamespace(calibration_id="cam_flange@1")
        return source, camera

    async def test_only_frames_after_the_call_are_used(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder, [tag_pose(7, (0, 0, 0.35)), tag_pose(9, (0.1, 0, 0.4))])
            for image in ("aus-der-fahrt", "neu-1", "neu-2", "neu-3"):
                camera.push_frame(image)
            result = await source.measure_tags(parse_request('{"tagIds": [7]}'))
        self.assertEqual(source._detector.images, ["neu-1", "neu-2", "neu-3"])
        self.assertEqual([tag["tagId"] for tag in result["tags"]], [7])
        self.assertEqual(result["tags"][0]["frameCount"], 3)
        self.assertEqual(result["frameId"], "cam_flange")
        self.assertEqual(result["intrinsicsSource"], "calibration")
        self.assertIsNone(result["error"])
        self.assertLessEqual(result["capturedAt"], result["capturedUntil"])

    async def test_no_tag_is_no_error_and_no_frame_is(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder, [])
            camera.push_frame("alt")
            camera.push_frame("neu")
            result = await source.measure_tags(parse_request('{"frames": 1}'))
            self.assertEqual((result["tags"], result["error"]), ([], None))
            result = await source.measure_tags(parse_request('{"frames": 1}'))
        self.assertEqual(result["error"], NO_FRAME)
        self.assertEqual(result["framesUsed"], 0)

    def test_intrinsics_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [])
        for calibration_id, expected in (
            ("cam_flange@1", "calibration"),
            (PLACEHOLDER_CALIBRATION_ID, "placeholder"),
            (REALSENSE_FACTORY_CALIBRATION_ID, "realsense_factory"),
        ):
            source._calibration = types.SimpleNamespace(calibration_id=calibration_id)
            self.assertEqual(source.intrinsics_source, expected)


@unittest.skipUnless(np is not None, "numpy fehlt")
class FallbackCalibrationTest(unittest.IsolatedAsyncioTestCase):
    def source(self, folder, factory, **overrides):
        async def factory_calibration(frame_id):
            return factory

        closed = []

        async def close():
            closed.append(True)

        camera = types.SimpleNamespace(factory_calibration=factory_calibration, close=close)
        source = AprilTagDetectionSource(profile_config(Path(folder), **overrides), camera=camera)
        return source, closed

    async def test_factory_intrinsics_win_over_the_placeholder(self):
        factory = CameraCalibration(np.eye(3), np.zeros(5), (640, 480), calibration_id=REALSENSE_FACTORY_CALIBRATION_ID)
        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, factory, allow_placeholder_calibration=True)
            await source._fallback_calibration(FileNotFoundError("fehlt"), None)
        self.assertIs(source._calibration, factory)
        self.assertEqual(source.intrinsics_source, "realsense_factory")

    async def test_without_factory_placeholder_or_refusal(self):
        from tagloc.calibration import default_calibration

        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, None, allow_placeholder_calibration=True)
            await source._fallback_calibration(FileNotFoundError("fehlt"), default_calibration)
            self.assertEqual(source.intrinsics_source, "placeholder")

            source, closed = self.source(folder, None)
            with self.assertRaises(FileNotFoundError):
                await source._fallback_calibration(FileNotFoundError("fehlt"), default_calibration)
        self.assertEqual(closed, [True])  # Kamera wieder zu, wie vorher ohne Kalibrierung


@unittest.skipUnless(np is not None, "numpy fehlt")
class RealsenseIntrinsicsTest(unittest.TestCase):
    def fake_rs(self, model, coeffs):
        intrinsics = types.SimpleNamespace(
            fx=610.0, fy=609.0, ppx=321.0, ppy=239.0, width=640, height=480, model=model, coeffs=coeffs
        )
        stream = types.SimpleNamespace(
            as_video_stream_profile=lambda: types.SimpleNamespace(get_intrinsics=lambda: intrinsics)
        )
        pipeline = types.SimpleNamespace(
            get_active_profile=lambda: types.SimpleNamespace(get_stream=lambda kind: stream)
        )
        module = types.SimpleNamespace(
            stream=types.SimpleNamespace(color="color"),
            distortion=types.SimpleNamespace(brown_conrady="bc", inverse_brown_conrady="ibc"),
        )
        return module, pipeline

    def calibration(self, model, coeffs):
        module, pipeline = self.fake_rs(model, coeffs)
        previous = sys.modules.get("pyrealsense2")
        sys.modules["pyrealsense2"] = module
        try:
            return _realsense_calibration(pipeline, "cam_flange")
        finally:
            if previous is None:
                del sys.modules["pyrealsense2"]
            else:
                sys.modules["pyrealsense2"] = previous

    def test_matrix_and_opencv_compatible_coefficients(self):
        calibration = self.calibration("bc", [0.1, -0.05, 0.001, 0.0, 0.01])
        np.testing.assert_allclose(calibration.camera_matrix, [[610, 0, 321], [0, 609, 239], [0, 0, 1]])
        np.testing.assert_allclose(calibration.distortion, [0.1, -0.05, 0.001, 0.0, 0.01])
        self.assertEqual(calibration.image_size, (640, 480))
        self.assertEqual(calibration.calibration_id, REALSENSE_FACTORY_CALIBRATION_ID)

    def test_a_foreign_distortion_model_is_not_taken_over(self):
        np.testing.assert_allclose(self.calibration("ibc", [0.0] * 5).distortion, [0.0] * 5)
        np.testing.assert_allclose(self.calibration("ibc", [0.2, 0, 0, 0, 0]).distortion, [0.0] * 5)


@unittest.skipUnless(np is not None, "numpy/asyncua fehlen")
class MeasureTagsOpcUaTest(unittest.IsolatedAsyncioTestCase):
    async def test_method_answers_with_the_result(self):
        from vision_server.runner import _install_measure_tags

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
            return {"schema": RESULT_SCHEMA, "tags": [{"tagId": 7}], "error": None}

        seen_during = []

        async def measure_tags(request):  # noqa: F811 -- merkt sich den Zaehler waehrend der Messung
            seen_during.append(source.measure_calls)
            if request.tag_ids == [13]:
                raise RuntimeError("Messung kaputt")
            return {"schema": RESULT_SCHEMA, "tags": [{"tagId": 7}], "error": None}

        source = types.SimpleNamespace(measure_tags=measure_tags, measure_calls=0)
        busy = {"value": False}
        methods = {}
        await _install_measure_tags(
            space, config, source, methods,
            types.SimpleNamespace(state_names=lambda: ("Operational", "Ready")),
            lambda: busy["value"],
        )
        self.assertEqual(list(methods), ["MeasureTags"])
        method = server.get_node(ua.NodeId("VisionMachine.MeasureTags", idx))

        result_json, error = await vision_system.call_method(method, '{"tagIds": [7]}')
        self.assertEqual(error, 0)
        self.assertEqual(json.loads(result_json)["tags"][0]["tagId"], 7)
        self.assertEqual(json.loads(await node.read_value())["tags"][0]["tagId"], 7)

        result_json, error = await vision_system.call_method(method, "kaputt")
        self.assertEqual(error, int(VisionErrorCode.INVALID_ARGUMENT))
        self.assertTrue(json.loads(result_json)["error"].startswith("INVALID_ARGUMENT"))
        # Waehrend der Messung zaehlt sie als laufend (Stream-Overlay haelt an), danach nicht mehr.
        self.assertEqual(seen_during, [1])
        self.assertEqual(source.measure_calls, 0)
        # Auch ein Fehler in der Messung stellt den Zaehler zurueck.
        try:
            await vision_system.call_method(method, '{"tagIds": [13]}')
        except Exception:
            pass  # asyncua meldet die Ausnahme je nach Version als Fehlerstatus
        self.assertEqual(len(seen_during), 2)  # die fehlerhafte Messung lief wirklich
        self.assertEqual(source.measure_calls, 0)
        busy["value"] = True
        _, error = await vision_system.call_method(method, "{}")
        self.assertEqual(error, int(VisionErrorCode.BUSY))


if __name__ == "__main__":
    unittest.main()
