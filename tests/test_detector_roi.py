"""Tests for the detection region of the ceiling camera (`tagloc.detector.RoiTagDetector`).

Work plan `doc/projektdoku/arbeitsplaene/deckenkamera-bildausschnitt.md`. The region
only restricts where the detector searches; the corners must stay in full-frame
coordinates, otherwise pose and calibration would no longer fit.
"""

import os
import unittest
from unittest import mock

import numpy as np

from tagloc.detector import RoiTagDetector, build_detector
from tagloc.observations import TagObservation
from vision_server.server import apriltag_config, detection_roi_setting

try:
    import cv2

    _ = cv2.aruco
except Exception:  # OpenCV not installed
    cv2 = None


class _RecordingDetector:
    """Fake detector: remembers the image it got and returns one tag at (10, 20)."""

    family = "tag36h11"
    changed_parameters = {"a": (1, 2)}

    def __init__(self):
        self.shapes = []

    def detect(self, gray):
        self.shapes.append(np.asarray(gray).shape)
        corners = ((10.0, 20.0), (30.0, 20.0), (30.0, 40.0), (10.0, 40.0))
        return [TagObservation(tag_id=7, corners=corners)]


class RoiTagDetectorTest(unittest.TestCase):
    def test_crops_and_shifts_corners_back_to_full_frame(self):
        inner = _RecordingDetector()
        detector = RoiTagDetector(inner, (100, 50, 300, 200), (1000, 800))

        found = detector.detect(np.zeros((800, 1000), dtype=np.uint8))

        self.assertEqual(inner.shapes, [(200, 300)])
        self.assertEqual(found[0].corners[0], (110.0, 70.0))
        self.assertEqual(found[0].corners[2], (130.0, 90.0))
        self.assertEqual(found[0].tag_id, 7)

    def test_scales_region_for_a_smaller_image(self):
        inner = _RecordingDetector()
        detector = RoiTagDetector(inner, (100, 50, 300, 200), (1000, 800))

        found = detector.detect(np.zeros((400, 500), dtype=np.uint8))

        self.assertEqual(inner.shapes, [(100, 150)])
        self.assertEqual(detector.region_for(500, 400), (50, 25, 200, 125))
        self.assertEqual(found[0].corners[0], (60.0, 45.0))

    def test_passes_family_and_parameters_through(self):
        detector = RoiTagDetector(_RecordingDetector(), (0, 0, 10, 10), (100, 100))

        self.assertEqual(detector.family, "tag36h11")
        self.assertEqual(detector.changed_parameters, {"a": (1, 2)})

    def test_rejects_region_outside_the_image(self):
        with self.assertRaises(ValueError):
            RoiTagDetector(_RecordingDetector(), (900, 0, 200, 100), (1000, 800))

    def test_rejects_empty_region(self):
        with self.assertRaises(ValueError):
            RoiTagDetector(_RecordingDetector(), (0, 0, 0, 100), (1000, 800))

    def test_ceiling_region_fits_the_full_resolution(self):
        detector = RoiTagDetector(_RecordingDetector(), (917, 568, 2114, 2079), (4056, 3040))

        self.assertEqual(detector.region_for(4056, 3040), (917, 568, 3031, 2647))


@unittest.skipUnless(cv2 is not None, "OpenCV not available")
class RoiWithArucoTest(unittest.TestCase):
    @staticmethod
    def _scene(positions):
        """White image 800x600 with tag36h11 ID 5 at each (x, y) top-left position."""
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        marker = cv2.aruco.generateImageMarker(dictionary, 5, 80)
        image = np.full((600, 800), 255, dtype=np.uint8)
        for x, y in positions:
            image[y : y + 80, x : x + 80] = marker
        return image

    def test_finds_tag_inside_region_with_full_frame_corners(self):
        image = self._scene([(400, 300)])
        plain = build_detector("tag36h11", "aruco").detect(image)
        cropped = build_detector(
            "tag36h11", "aruco", roi=(300, 200, 300, 300), roi_reference_size=(800, 600)
        ).detect(image)

        self.assertEqual([tag.tag_id for tag in cropped], [5])
        np.testing.assert_allclose(cropped[0].corners, plain[0].corners, atol=1e-6)

    def test_ignores_tag_outside_region(self):
        image = self._scene([(40, 40)])

        found = build_detector(
            "tag36h11", "aruco", roi=(300, 200, 300, 300), roi_reference_size=(800, 600)
        ).detect(image)

        self.assertEqual(found, [])

    def test_roi_without_reference_size_is_rejected(self):
        with self.assertRaises(ValueError):
            build_detector("tag36h11", "aruco", roi=(0, 0, 10, 10))


class DetectionRoiSettingTest(unittest.TestCase):
    def test_preset_applies_without_environment(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VISION_DETECTION_ROI", None)
            self.assertEqual(detection_roi_setting((1, 2, 3, 4)), (1, 2, 3, 4))
            self.assertIsNone(detection_roi_setting(None))

    def test_environment_overrides_and_switches_off(self):
        with mock.patch.dict(os.environ, {"VISION_DETECTION_ROI": "10, 20, 30, 40"}):
            self.assertEqual(detection_roi_setting((1, 2, 3, 4)), (10, 20, 30, 40))
        with mock.patch.dict(os.environ, {"VISION_DETECTION_ROI": "off"}):
            self.assertIsNone(detection_roi_setting((1, 2, 3, 4)))

    def test_invalid_environment_value_raises(self):
        with mock.patch.dict(os.environ, {"VISION_DETECTION_ROI": "10,20,30"}):
            with self.assertRaises(ValueError):
                detection_roi_setting(None)

    def test_ceiling_preset_has_the_region_and_flange_has_none(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VISION_DETECTION_ROI", None)
            self.assertEqual(apriltag_config("cam_ceiling").detection_roi, (917, 568, 2114, 2079))
            self.assertIsNone(apriltag_config("cam_flange").detection_roi)


if __name__ == "__main__":
    unittest.main()
