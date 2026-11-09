"""Helligkeitstest: Anfrage, Kontrast, Kennwerte, Ablage und Ablauf einer Stufe."""

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

from vision_server.camera import picamera2_exposure
from vision_server.errors import VisionErrorCode, VisionJobError
from vision_server.lighttest import (
    CELL_PX,
    RESULT_SCHEMA,
    frame_record,
    level_paths,
    michelson_contrast,
    parse_request,
    sample_indices,
    summarize,
    write_level,
)
from vision_server.runner.light_test import LightTest


def tag_image(black=40, white=200, outside=200):
    """Ein Bild mit einem schwarzen Quadrat (dem Tag-Rand) auf weissem Grund; Ecken im Uhrzeigersinn."""
    image = np.full((400, 400), outside, dtype=np.uint8)
    corners = [(100.0, 100.0), (300.0, 100.0), (300.0, 300.0), (100.0, 300.0)]
    image[100:300, 100:300] = black
    # Das Innere (6 x 6 Zellen) hell -- der Kontrast misst nur den Rand.
    cell = 200 / 8
    inner = slice(int(100 + cell), int(300 - cell))
    image[inner, inner] = white
    return image, corners


class ParseRequestTest(unittest.TestCase):
    def test_defaults_and_values(self):
        request = parse_request(json.dumps({"tagId": 5, "level": {"percent": 50, "lux": 120.5}}))
        self.assertEqual((request.tag_id, request.frames, request.sample_images, request.series), (5, 300, 10, 0))
        self.assertEqual(request.level, {"percent": 50, "lux": 120.5})
        self.assertIsNone(request.size_m)

    def test_refuses_bad_values(self):
        for bad in ({}, {"tagId": -1}, {"tagId": 1, "frames": 0}, {"tagId": 1, "frames": 5000},
                    {"tagId": 1, "sampleImages": 99}, {"tagId": 1, "series": -2}, {"tagId": 1, "sizeM": 0},
                    {"tagId": 1, "level": []}, {"schema": "x", "tagId": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_request(json.dumps(bad))
        with self.assertRaises(ValueError):
            parse_request("kein json")


class SampleIndicesTest(unittest.TestCase):
    def test_spread_with_first_and_last(self):
        self.assertEqual(sample_indices(300, 4), [0, 100, 199, 299])
        self.assertEqual(sample_indices(5, 10), [0, 1, 2, 3, 4])
        self.assertEqual(sample_indices(300, 1), [0])
        self.assertEqual(sample_indices(300, 0), [])


class ContrastTest(unittest.TestCase):
    def test_black_border_against_white_outside(self):
        image, corners = tag_image(black=40, white=200)
        contrast = michelson_contrast(image, corners)
        self.assertAlmostEqual(contrast["black"], 40, delta=1)
        self.assertAlmostEqual(contrast["white"], 200, delta=1)
        self.assertAlmostEqual(contrast["michelson"], 160 / 240, places=2)

    def test_darker_scene_same_ratio_lower_difference(self):
        image, corners = tag_image(black=10, white=50, outside=50)
        self.assertAlmostEqual(michelson_contrast(image, corners)["michelson"], 40 / 60, places=2)

    def test_tag_at_the_edge_is_not_measured(self):
        image = np.full((400, 400), 200, dtype=np.uint8)
        corners = [(0.0, 100.0), (200.0, 100.0), (200.0, 300.0), (0.0, 300.0)]
        self.assertIsNone(michelson_contrast(image, corners))
        self.assertIsNone(michelson_contrast(image, None))
        self.assertGreater(CELL_PX, 0)


def record(index, *, aruco=True, position=(0.0, 0.0, 0.5), apriltag=True, hamming=0, margin=50.0, contrast=0.6):
    return frame_record(
        index,
        "2026-11-09T12:00:00",
        100.0,
        aruco={"detected": aruco, **({"poseInCamera": {"position": list(position), "orientation": [0, 0, 0, 1]},
                                      "reprojectionErrorPx": 0.3} if aruco else {})},
        apriltag={"detected": apriltag, **({"decisionMargin": margin, "hamming": hamming} if apriltag else {})},
        contrast={"michelson": contrast, "black": 40, "white": 160} if contrast is not None else None,
    )


class SummarizeTest(unittest.TestCase):
    def test_rates_margin_hamming_jitter_contrast(self):
        records = [
            record(0, position=(0.0, 0.0, 0.500)),
            record(1, position=(0.002, 0.0, 0.500), hamming=1, margin=30.0),
            record(2, aruco=False, apriltag=False, contrast=None),
            record(3, position=(0.001, 0.0, 0.500), margin=40.0, contrast=0.4),
        ]
        summary = summarize(records, 4)
        self.assertEqual(summary["framesUsed"], 4)
        self.assertAlmostEqual(summary["aruco"]["detectionRate"], 0.75)
        self.assertAlmostEqual(summary["apriltag"]["detectionRate"], 0.75)
        self.assertAlmostEqual(summary["apriltag"]["decisionMargin"]["mean"], 40.0)
        self.assertEqual(summary["apriltag"]["hammingCounts"], {"0": 2, "1": 1})
        jitter = summary["poseJitter"]
        self.assertAlmostEqual(jitter["positionSdMm"][0], 1.0, places=6)
        self.assertAlmostEqual(jitter["rotationRmsDeg"], 0.0, places=6)
        self.assertAlmostEqual(summary["contrast"]["mean"], (0.6 + 0.6 + 0.4) / 3)

    def test_nothing_seen(self):
        summary = summarize([record(0, aruco=False, apriltag=False, contrast=None)], 300)
        self.assertEqual(summary["aruco"]["detectionRate"], 0.0)
        self.assertIsNone(summary["poseJitter"])
        self.assertIsNone(summary["contrast"])
        self.assertIsNone(summary["apriltag"]["decisionMargin"])

    def test_without_second_detector(self):
        records = [frame_record(0, "t", 1.0, aruco={"detected": False}, apriltag=None, contrast=None)]
        self.assertIsNone(summarize(records, 1)["apriltag"])


class StorageTest(unittest.TestCase):
    def test_new_series_then_levels_in_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            series, folder, level = level_paths(base, 0)
            self.assertEqual((series, folder.name, level), (1, "reihe_001", 1))
            write_level(folder, level, {"a": 1})
            self.assertEqual(level_paths(base, 1)[2], 2)
            self.assertEqual(level_paths(base, 0)[0], 2)
            with self.assertRaises(FileExistsError):
                write_level(folder, 1, {"a": 2})


class FakeCamera:
    def __init__(self, frames=None):
        self.frame_count = 0
        self.latest_frame = types.SimpleNamespace(image=np.zeros((4, 6, 3), np.uint8), timestamp=0.0)


class FakeSource:
    """So viel AprilTag-Quelle, wie der Helligkeitstest braucht."""

    def __init__(self, frames_available=1000):
        self.camera = FakeCamera()
        self.measure_calls = 0
        self._config = types.SimpleNamespace(
            tag_size_m=0.08, tag_family="tag36h11", frame_id="cam_flange", max_reproj_error_px=1.5,
            detector_parameters={}, subpixel_corner_refinement=True,
        )
        self._tag_map = None
        self._calibration = types.SimpleNamespace(calibration_id="cal-1")
        self.intrinsics_source = "calibration"
        self._frames_available = frames_available
        self._served = 0

    def _latest_timestamp(self):
        return None

    async def _next_frame(self, seen):
        if self._served >= self._frames_available:
            raise VisionJobError(VisionErrorCode.DETECTION_FAILED, "Kein Kamerabild")
        self._served += 1
        await asyncio.sleep(0)
        return types.SimpleNamespace(image=np.zeros((4, 6, 3), np.uint8), timestamp=float(self._served))

    async def run_blocking(self, func, *args):
        return func(*args)


def fake_measure(source, pupil, image, tag_id, sizes):
    seen = source._served % 2 == 1
    return (
        {"detected": seen, **({"poseInCamera": {"position": [0, 0, 0.5], "orientation": [0, 0, 0, 1]}} if seen else {})},
        {"detected": seen, "decisionMargin": 60.0, "hamming": 0} if seen else {"detected": False},
        {"michelson": 0.7, "black": 30, "white": 170},
        90.0,
    )


class LightTestRunTest(unittest.IsolatedAsyncioTestCase):
    async def run_level(self, source, request, *, abort_after=None):
        saved = []
        with tempfile.TemporaryDirectory() as tmp:
            test = LightTest(
                source,
                base_dir=Path(tmp),
                exposure=None,
                measure=fake_measure,
                save_image=lambda path, image: saved.append(path.name),
            )
            test._pupil_tried = True  # ohne pupil_apriltags
            test.start(request)
            self.assertEqual(test.status()["state"], "running")
            while test.running:
                if abort_after is not None and test.progress["frames"] >= abort_after:
                    test.abort_requested = True
                await asyncio.sleep(0)
            document = json.loads((Path(tmp) / "reihe_001" / "stufe_001.json").read_text(encoding="utf-8"))
            return test, document, saved

    async def test_records_every_frame_and_saves_the_sample(self):
        source = FakeSource()
        request = parse_request(json.dumps({"tagId": 7, "frames": 20, "sampleImages": 3, "level": {"percent": 100, "lux": 800}}))
        test, document, saved = await self.run_level(source, request)
        self.assertEqual(test.state, "done")
        self.assertEqual(document["schema"], RESULT_SCHEMA)
        self.assertEqual((document["reihe"], document["stufe"]), (1, 1))
        self.assertEqual(len(document["frames"]), 20)
        self.assertEqual(document["level"], {"percent": 100, "lux": 800})
        self.assertAlmostEqual(document["summary"]["aruco"]["detectionRate"], 0.5)
        self.assertEqual(saved, ["stufe_001_bild_01.png", "stufe_001_bild_02.png", "stufe_001_bild_03.png"])
        self.assertEqual(document["images"], saved)
        status = test.status()
        self.assertNotIn("frames", status["result"])
        self.assertTrue(status["result"]["file"].endswith("stufe_001.json"))
        self.assertEqual(source.measure_calls, 0)

    async def test_abort_keeps_what_was_measured(self):
        request = parse_request(json.dumps({"tagId": 7, "frames": 500, "sampleImages": 0}))
        test, document, _ = await self.run_level(FakeSource(), request, abort_after=5)
        self.assertEqual(test.state, "aborted")
        self.assertTrue(document["aborted"])
        self.assertLess(len(document["frames"]), 500)

    async def test_camera_gone_is_reported(self):
        request = parse_request(json.dumps({"tagId": 7, "frames": 10, "sampleImages": 0}))
        test, document, _ = await self.run_level(FakeSource(frames_available=3), request)
        self.assertEqual(test.state, "done")
        self.assertEqual(len(document["frames"]), 3)
        self.assertEqual(document["error"], "Kein Kamerabild")


class Picamera2ExposureTest(unittest.TestCase):
    class Camera:
        def __init__(self):
            self.set = []

        def capture_metadata(self):
            return {"ExposureTime": 12000, "AnalogueGain": 2.5, "ColourGains": (1.8, 1.4), "Lux": 300.0}

        def set_controls(self, controls):
            self.set.append(controls)

    def test_lock_sets_the_automatic_values(self):
        camera = self.Camera()
        result = picamera2_exposure(camera, True)
        self.assertEqual(
            camera.set,
            [{"AeEnable": False, "AwbEnable": False, "ExposureTime": 12000, "AnalogueGain": 2.5, "ColourGains": (1.8, 1.4)}],
        )
        self.assertEqual(result["values"]["ExposureTime"], 12000)
        json.dumps(result)

    def test_unlock_and_read(self):
        camera = self.Camera()
        picamera2_exposure(camera, False)
        self.assertEqual(camera.set, [{"AeEnable": True, "AwbEnable": True}])
        picamera2_exposure(camera, None)
        self.assertEqual(len(camera.set), 1)


if __name__ == "__main__":
    unittest.main()
