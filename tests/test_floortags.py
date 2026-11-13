"""MeasureFloorTags: Tags auf dem Boden, entzerrt und im Rohbild -- in einer kuenstlichen Szene."""

import json
import types
import unittest

import numpy as np

from tagloc.calibration import CameraCalibration
from tagloc.detector import TagObservation
from tagloc.geometry import from_rotation_translation, invert
from tagloc.lens import project_points
from tagloc.pose import tag_object_points
from tagloc.tagmap import tag_map_from_json
from vision_server.floortags import parse_request, summarize
from vision_server.runner.measure_floor_tags import floor_frame

CALIBRATION = CameraCalibration(
    camera_matrix=np.array([[1500.0, 0, 1014.0], [0, 1500.0, 760.0], [0, 0, 1]]),
    distortion=np.array([-0.15, 0.05, 0.0, 0.0, 0.0]),
    image_size=(2028, 1520),
    calibration_id="test",
)
# Die Kamera 2,5 m ueber dem Boden, senkrecht nach unten: Kamera-x = Welt-x, Kamera-y = Welt -y.
WORLD_CAM = from_rotation_translation(np.diag([1.0, -1.0, -1.0]), [0.8, 0.9, 2.5])
WORLD_TAGS = {583: (0.0, 0.0), 584: (1.6, 0.0), 585: (1.6, 1.75), 586: (0.0, 1.75)}
RULER = {10 + k: (0.8 + 0.25 * k, 0.9) for k in range(4)}


def observation(tag_id, xy, size, world_cam=None):
    world_tag = from_rotation_translation(np.eye(3), [xy[0], xy[1], 0.0])
    cam_tag = invert(WORLD_CAM if world_cam is None else world_cam) @ world_tag
    import cv2

    rvec, _ = cv2.Rodrigues(cam_tag[:3, :3])
    corners = project_points(tag_object_points(size), rvec, cam_tag[:3, 3], CALIBRATION)
    return TagObservation(tag_id=tag_id, corners=tuple(map(tuple, corners)))


def tag_map():
    return tag_map_from_json(
        json.dumps(
            {
                "schema": "wsc.vision.tagmap/2",
                "tags": [
                    {"tagId": i, "role": "world", "sizeM": 0.2,
                     "poseInWorld": {"position": [x, y, 0.0], "orientation": [0, 0, 0, 1]}}
                    for i, (x, y) in WORLD_TAGS.items()
                ],
            }
        )
    )


class FakeSource:
    def __init__(self, world_cam=None):
        observations = [observation(i, xy, 0.2, world_cam) for i, xy in WORLD_TAGS.items()]
        observations += [observation(i, xy, 0.08, world_cam) for i, xy in RULER.items()]
        self._detector = types.SimpleNamespace(detect=lambda gray: observations)
        self._tag_map = tag_map()
        self._config = types.SimpleNamespace(tag_size_m=0.08, max_reproj_error_px=3.0)

    def _calibration_for(self, image):
        return CALIBRATION


class ParseRequestTest(unittest.TestCase):
    def test_defaults_and_refusals(self):
        request = parse_request("")
        self.assertEqual((request.frames, request.height_m, request.tag_ids), (5, 0.0, None))
        self.assertEqual(parse_request('{"tagIds": [10, 11], "heightM": 0.003}').tag_ids, frozenset({10, 11}))
        for bad in ('{"frames": 0}', '{"frames": 99}', '{"heightM": "x"}', '{"tagIds": [-1]}', '{"schema": "x"}', "kein json"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_request(bad)


class FloorFrameTest(unittest.TestCase):
    def setUp(self):
        image = np.zeros((1520, 2028, 3), np.uint8)
        frame = floor_frame(FakeSource(), image, parse_request(""))
        self.summary = summarize([frame, frame], 2)["variants"]

    def positions(self, variant):
        return {t["tagId"]: np.array(t["position"]) for t in self.summary[variant]["tags"]}

    def test_undistorted_puts_the_tags_where_they_lie(self):
        found = self.positions("entzerrt")
        for tag_id, (x, y) in {**RULER, **WORLD_TAGS}.items():
            self.assertLess(np.hypot(found[tag_id][0] - x, found[tag_id][1] - y), 0.001, tag_id)
        camera = self.summary["entzerrt"]["camera"]
        self.assertEqual(camera["solvers"], ["joint"])
        np.testing.assert_allclose(camera["positionInWorld"], [0.8, 0.9, 2.5], atol=0.002)
        ruler = next(t for t in self.summary["entzerrt"]["tags"] if t["tagId"] == 10)
        self.assertAlmostEqual(ruler["edgeM"], 0.08, places=3)
        self.assertFalse(ruler["world"])

    def test_the_raw_picture_measures_the_ruler_off(self):
        found = self.positions("roh")
        last = np.hypot(*(found[13][:2] - found[10][:2]))
        # Die Verzeichnung (Tonne) staucht nach aussen: die 75 cm messen im Rohbild merklich daneben.
        self.assertGreater(abs(last - 0.75), 0.002)
        self.assertTrue(all(t["world"] for t in self.summary["roh"]["tags"] if t["tagId"] in WORLD_TAGS))


class ArucoTest(unittest.TestCase):
    """Echte ArUco-Marker (6x6) in einem gezeichneten Bild, eine ID doppelt."""

    def image(self):
        import cv2

        from tagloc.detector import predefined_dictionary

        canvas = np.full((600, 1200), 255, np.uint8)
        dictionary = predefined_dictionary("DICT_6X6_250")
        for index, tag_id in enumerate((4, 7, 7, 9)):
            marker = cv2.aruco.generateImageMarker(dictionary, tag_id, 160)
            x = 60 + index * 280
            canvas[200:360, x : x + 160] = marker
        return canvas

    def test_finds_the_dictionary_and_keeps_both_markers_of_one_id(self):
        from vision_server.runner.measure_floor_tags import choose_aruco

        detector = choose_aruco(self.image(), ("DICT_4X4_1000", "DICT_5X5_1000", "DICT_6X6_1000"))
        self.assertEqual(detector.dictionary, "DICT_6X6_1000")
        found = detector.detect(self.image())
        self.assertEqual(sorted(o.tag_id for o in found), [4, 7, 7, 9])
        # Ecken im Uhrzeigersinn ab oben links, wie TagObservation.
        first = min(found, key=lambda o: o.corners[0][0])
        self.assertLess(first.corners[0][0], first.corners[1][0])
        self.assertLess(first.corners[1][1], first.corners[2][1])

    def test_a_marker_inside_an_apriltag_is_left_out(self):
        from vision_server.runner.measure_floor_tags import outside

        marker = TagObservation(tag_id=1, corners=((10, 10), (20, 10), (20, 20), (10, 20)))
        apriltag = TagObservation(tag_id=583, corners=((0, 0), (40, 0), (40, 40), (0, 40)))
        self.assertFalse(outside(marker, [apriltag]))
        self.assertTrue(outside(marker, []))


class ClusterTest(unittest.TestCase):
    def test_one_id_in_two_places_becomes_two_markers(self):
        def frame(shift):
            tags = [
                {"tagId": 7, "position": np.array([1.0 + shift, 1.0, 0.0]), "edgeM": 0.05, "world": False},
                {"tagId": 7, "position": np.array([1.5 + shift, 1.0, 0.0]), "edgeM": 0.05, "world": False},
                {"tagId": 9, "position": np.array([1.25, 1.0, 0.0]), "edgeM": 0.05, "world": False},
            ]
            camera = {"positionInWorld": np.array([1.0, 1.0, 2.5]), "solver": "joint", "reprojectionErrorPx": 0.3, "worldTagIds": [583]}
            return {name: {"camera": camera, "tags": tags} for name in ("entzerrt", "roh")}

        tags = summarize([frame(0.0), frame(0.002)], 2)["variants"]["entzerrt"]["tags"]
        self.assertEqual([t["label"] for t in tags], ["7", "7b", "9"])
        self.assertEqual([t["count"] for t in tags], [2, 2, 2])
        self.assertAlmostEqual(tags[0]["position"][0], 1.001, places=6)
        self.assertAlmostEqual(tags[1]["position"][0], 1.501, places=6)


class CameraReferenceTest(unittest.TestCase):
    """Bezug "camera": ohne Welttags, Ebene aus den Lineal-Markern, Abstaende ab der optischen Achse."""

    def setUp(self):
        image = np.zeros((1520, 2028, 3), np.uint8)
        request = parse_request('{"reference": "camera", "tagSizeM": 0.08}')
        # 3 Grad geneigt wie eine echte Kamera: genau frontal waeren die beiden IPPE-Loesungen gleich.
        tilt = np.radians(3.0)
        rotation = np.diag([1.0, -1.0, -1.0]) @ np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)], [0, np.sin(tilt), np.cos(tilt)]])
        frame = floor_frame(FakeSource(from_rotation_translation(rotation, [0.8, 0.9, 2.5])), image, request)
        self.summary = summarize([frame], 1)["variants"]

    def test_measures_from_the_optical_axis_without_world_tags(self):
        tags = {t["tagId"]: np.array(t["position"]) for t in self.summary["entzerrt"]["tags"]}
        self.assertEqual(sorted(tags), sorted(RULER))  # keine Welttags
        camera = self.summary["entzerrt"]["camera"]
        self.assertAlmostEqual(camera["planeDistanceM"], 2.5, delta=0.003)
        self.assertAlmostEqual(camera["tiltDeg"], 3.0, delta=0.2)
        self.assertEqual(camera["scaleTagId"], 10)  # der Marker an der Bildmitte gibt den Massstab
        # Die Tags je 25 cm auseinander, in der Ebene gemessen.
        for k, tag_id in enumerate(sorted(RULER)[1:], start=1):
            gap = float(np.hypot(*(tags[tag_id][:2] - tags[tag_id - 1][:2])))
            self.assertAlmostEqual(gap, 0.25, delta=0.002)
        # Die optische Achse trifft die Ebene 2,5 m * tan 3 Grad neben dem Punkt unter der Kamera (Tag 10).
        self.assertAlmostEqual(float(np.hypot(*tags[10][:2])), 2.5 * np.tan(np.radians(3.0)), delta=0.004)

    def test_the_raw_picture_still_measures_short_outwards(self):
        tags = {t["tagId"]: np.array(t["position"]) for t in self.summary["roh"]["tags"]}
        self.assertGreater(abs(float(np.hypot(*(tags[13][:2] - tags[10][:2]))) - 0.75), 0.002)


class SaveMeasurementTest(unittest.TestCase):
    def test_keeps_the_pictures_and_the_result_in_a_new_folder(self):
        import tempfile
        from pathlib import Path

        from vision_server.floortags import save_measurement

        self.assertEqual(parse_request("").save_images, 1)
        self.assertEqual(parse_request('{"saveImages": 0}').save_images, 0)
        with self.assertRaises(ValueError):
            parse_request('{"saveImages": -1}')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "Linealtest"
            image = np.zeros((20, 30, 3), np.uint8)
            folder, names = save_measurement(base, [image, image], {"schema": "x", "framesUsed": 2})
            self.assertEqual(Path(folder).name, "messung_001")
            self.assertEqual(names, ["bild_01.png", "bild_02.png"])
            saved = json.loads((Path(folder) / "messung.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["bilder"], names)
            self.assertEqual(saved["framesUsed"], 2)
            self.assertEqual(Path(save_measurement(base, [image], {})[0]).name, "messung_002")


if __name__ == "__main__":
    unittest.main()
