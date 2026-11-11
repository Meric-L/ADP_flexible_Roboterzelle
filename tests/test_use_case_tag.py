"""Tests fuer die Bildmessung der Wuerfelablage gegen einen AprilTag (Bezug "tag").

Der Wuerfel liegt auf einem 80-mm-Tag; gemessen wird gegen den Aussenrand des
schwarzen Tag-Quadrats, nicht gegen die (vom Wuerfel verdeckten) Tag-Bits.
Synthetische Bilder mit bekannter Wahrheit (`use_case_eval.synthetic.render_tag_scene`).
"""

import json
import math
import unittest

try:
    import cv2  # noqa: F401
    import numpy as np

    from use_case_eval import live, measure, synthetic
except Exception:  # pragma: no cover - numpy/OpenCV fehlen
    np = None

try:
    from vision_server.runner.save_use_case_image import _reference_from_metadata
except Exception:  # pragma: no cover - asyncua fehlt
    _reference_from_metadata = None

#: Kamera ueber der Tag-Ebene (mm): 20 cm ueber der Wuerfeloberseite.
CAMERA_HEIGHT_MM = 220.0
#: Handkamera seit 10.11.2026: D415-Farbsensor in voller Aufloesung 1920x1080
#: (`profiles.realsense_resolution`), fx rund 1390 px. Bei 640x480 reicht der
#: 1-mm-Rand zwischen Aufkleber und Wuerfelkante (gut 2 px) vor dem schwarzen
#: Tag-Muster nicht fuer die Kantenverfeinerung des Aufklebers (Fehler bis ~1 Grad).
FULL_HD = {"fx": 1390.0, "size": (1920, 1080)}
POSITION_TOLERANCE_MM = 0.05
ANGLE_TOLERANCE_DEG = 0.15


def angle_error(measured: float, true: float) -> float:
    """Differenz zweier Gierwinkel modulo 90 Grad."""
    return (measured - true + 45.0) % 90.0 - 45.0


def tag_x_in_image(camera_yaw_deg: float) -> list[float]:
    """Richtung der Tag-x-Achse im Bild bei `look_down_pose(yaw)` (x rechts, y unten)."""
    yaw = math.radians(camera_yaw_deg)
    return [math.cos(yaw), math.sin(yaw)]


def tag_reference(**extra):
    return {"kind": "tag", "tagSizeM": 0.08, "tagId": 3, **extra}


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class MeasureAgainstTagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calibration = synthetic.make_calibration(**FULL_HD)
        # Rendered once (a full-HD picture takes seconds): the cube 1 mm off the tag's middle along x.
        cls.near_middle = cls.render(1.0, 0.0, 0.0)

    @classmethod
    def render(cls, dx, dy, dtheta, camera_yaw=0.0, camera_xy=(0.0, 0.0), **kwargs):
        pose = synthetic.look_down_pose(camera_xy[0], camera_xy[1], CAMERA_HEIGHT_MM, camera_yaw)
        return synthetic.render_tag_scene(cls.calibration, pose, dx, dy, dtheta, supersample=2, **kwargs)

    def assert_measures(self, result, dx, dy, dtheta):
        self.assertTrue(result.ok, result.grund)
        self.assertLess(abs(result.dx_mm - dx), POSITION_TOLERANCE_MM, (result.dx_mm, dx))
        self.assertLess(abs(result.dy_mm - dy), POSITION_TOLERANCE_MM, (result.dy_mm, dy))
        self.assertLess(abs(angle_error(result.dtheta_deg, dtheta)), ANGLE_TOLERANCE_DEG, (result.dtheta_deg, dtheta))

    def test_measures_the_cube_against_the_tag_in_the_tags_axes_however_the_camera_is_turned(self):
        for yaw, (dx, dy, dtheta) in [
            (0.0, (3.0, -2.0, 10.0)),
            (30.0, (-4.0, 2.0, -20.0)),
            (90.0, (1.5, 2.5, 4.0)),
            (180.0, (2.0, 2.0, 5.0)),
            (-90.0, (-2.5, -1.0, -8.0)),
        ]:
            with self.subTest(camera_yaw=yaw):
                image = self.render(dx, dy, dtheta, camera_yaw=yaw)
                result = measure.measure_trial(
                    image, self.calibration, reference=tag_reference(tagXInImage=tag_x_in_image(yaw))
                )
                self.assert_measures(result, dx, dy, dtheta)

    def test_also_with_the_camera_off_the_tags_middle(self):
        image = self.render(2.0, -1.0, 3.0, camera_yaw=15.0, camera_xy=(6.0, -4.0))
        result = measure.measure_trial(image, self.calibration, reference=tag_reference(tagXInImage=tag_x_in_image(15.0)))
        self.assert_measures(result, 2.0, -1.0, 3.0)
        self.assertGreater(result.nadir_offset_mm, 3.0)

    def test_without_the_axis_the_tags_x_follows_the_picture(self):
        # Camera turned half round, no `tagXInImage`: x lies along the picture's x, so dx/dy turn over.
        image = self.render(2.0, 1.0, 5.0, camera_yaw=180.0)
        result = measure.measure_trial(image, self.calibration, reference=tag_reference())
        self.assert_measures(result, -2.0, -1.0, 5.0)

    def test_says_what_it_measured_against(self):
        result = measure.measure_trial(self.near_middle, self.calibration, reference=tag_reference(tagXInImage=[1.0, 0.0]))
        self.assert_measures(result, 1.0, 0.0, 0.0)
        self.assertEqual(result.reference, "tag")
        self.assertEqual(result.tag_id, 3)
        self.assertAlmostEqual(result.tag_size_mm, 80.0)
        self.assertTrue(result.tag_edges_refined)
        self.assertTrue(result.cube_edge_refined)
        self.assertEqual(len(result.tag_corners_px), 4)
        self.assertEqual(result.sheet_markers, [])
        self.assertEqual(result.zone, "gruen")
        self.assertAlmostEqual(result.camera_height_mm, CAMERA_HEIGHT_MM, delta=1.0)
        # Survives JSON: the Pi sends it as ResultJson.
        data = json.loads(json.dumps(result.to_dict()))
        self.assertEqual(data["reference"], "tag")
        self.assertEqual(data["tag_size_mm"], 80.0)

    def test_the_tag_size_sets_the_scale(self):
        # The same picture read as a 100-mm tag: every length 1.25 times as long.
        result = measure.measure_trial(self.near_middle, self.calibration, reference=tag_reference(tagSizeM=0.1, tagXInImage=[1.0, 0.0]))
        self.assertTrue(result.ok, result.grund)
        self.assertAlmostEqual(result.camera_height_mm, CAMERA_HEIGHT_MM * 1.25, delta=3.0)
        self.assertGreater(result.dx_mm, 1.15)

    def test_without_a_reference_it_still_measures_against_the_sheet(self):
        result = measure.measure_trial(self.near_middle, self.calibration)
        self.assertFalse(result.ok)
        self.assertEqual(result.reference, "sheet")
        self.assertIn("Blattmarker", result.grund)

    def test_fails_with_a_reason_when_there_is_no_tag_round_the_cube(self):
        pose = synthetic.look_down_pose(0.0, 0.0, CAMERA_HEIGHT_MM)
        sheet_only = synthetic.render_trial(self.calibration, pose, 0.0, 0.0, 0.0, supersample=2)
        result = measure.measure_trial(sheet_only, self.calibration, reference=tag_reference())
        self.assertFalse(result.ok)
        self.assertEqual(result.reference, "tag")
        self.assertIn("Tag-Rand nicht gefunden", result.grund)

    def test_fails_without_the_cube(self):
        image = self.render(0.0, 0.0, 0.0, with_cube=False)
        result = measure.measure_trial(image, self.calibration, reference=tag_reference())
        self.assertFalse(result.ok)
        self.assertIn("Wuerfelmarker", result.grund)

    def test_measure_for_save_passes_the_reference_on(self):
        data, _annotated = live.measure_for_save(self.near_middle, self.calibration, (0.0, 0.0), tag_reference(tagXInImage=[1.0, 0.0]))
        self.assertTrue(data["ok"], data.get("grund"))
        self.assertEqual(data["reference"], "tag")
        self.assertAlmostEqual(data["dx_mm"], 1.0, delta=POSITION_TOLERANCE_MM)


@unittest.skipIf(_reference_from_metadata is None, "vision_server (asyncua) fehlt")
class ReferenceFromMetadataTest(unittest.TestCase):
    def test_reads_a_tag_reference(self):
        metadata = json.dumps({"series": 1, "reference": {"kind": "tag", "tagId": 3, "tagSizeM": 0.08, "tagXInImage": [0.6, -0.8]}})
        self.assertEqual(
            _reference_from_metadata(metadata),
            {"kind": "tag", "tagSizeM": 0.08, "tagId": 3, "tagXInImage": [0.6, -0.8]},
        )

    def test_is_none_for_the_sheet_and_for_anything_else(self):
        for metadata in ["", "{kaputt", "[1]", "null", json.dumps({"series": 1}), json.dumps({"reference": {"kind": "blatt"}}), json.dumps({"reference": "tag"})]:
            with self.subTest(metadata=metadata):
                self.assertIsNone(_reference_from_metadata(metadata))

    def test_falls_back_on_doubtful_values(self):
        metadata = json.dumps({"reference": {"kind": "tag", "tagId": True, "tagSizeM": 5, "tagXInImage": [0, 0]}})
        self.assertEqual(_reference_from_metadata(metadata), {"kind": "tag", "tagSizeM": 0.08, "tagId": None, "tagXInImage": None})
        metadata = json.dumps({"reference": {"kind": "tag", "tagSizeM": "0.08", "tagXInImage": [1, "x"]}})
        self.assertEqual(_reference_from_metadata(metadata), {"kind": "tag", "tagSizeM": 0.08, "tagId": None, "tagXInImage": None})


if __name__ == "__main__":
    unittest.main()
