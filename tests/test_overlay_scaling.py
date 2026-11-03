"""Tests fuer `tagloc.overlay`s Skalierung von Strichstaerke/Schriftgroesse.

Das Livestream-Overlay zeichnet nicht mehr auf einer festen Breite: "off"
zeigt weiterhin den vollen Frame (bis 4056 px an der Deckenkamera), "apriltag"
erkennt und zeichnet seit der Umstellung auf Software-Downscale (siehe
`stream_overlay.py.annotate`) auf `detection_max_width` (typischerweise
~960 px). Ohne Skalierung blieb ein 2-px-Strich bei genauer Betrachtung/Zoom
praktisch unsichtbar. Diese Tests laufen mit echtem `cv2` auf echten Arrays
(kein Mock der Zeichenfunktionen), gleiches Muster wie `test_stream_overlay.py`.
"""

import unittest

try:
    import cv2
    import numpy as np

    from tagloc import geometry
    from tagloc.observations import TagObservation, TagPose
    from tagloc.overlay import (
        _REFERENCE_WIDTH,
        _scale_for,
        draw_board_overlay,
        draw_status_bar,
        COLOR_OK,
        draw_tag_boxes,
        draw_tag_overlay,
    )

    _ = cv2.circle
except Exception:  # OpenCV/numpy nicht verfuegbar
    cv2 = None
    np = None


def _tag_pose(tag_id: int = 1):
    corners = ((100.0, 100.0), (140.0, 100.0), (140.0, 140.0), (100.0, 140.0))
    return TagPose(
        tag_id=tag_id,
        pose_cam_tag=geometry.from_rvec_tvec((0.0, 0.0, 0.0), (0.0, 0.0, 0.3)),
        reprojection_error_px=0.5,
        ambiguity_ratio=0.0,
        observation=TagObservation(tag_id=tag_id, corners=corners),
    )


class FakeCalibration:
    """Reicht fuer `draw_tag_axes`s `drawFrameAxes`-Aufruf."""

    camera_matrix = ((500.0, 0.0, 320.0), (0.0, 500.0, 240.0), (0.0, 0.0, 1.0))
    distortion = (0.0, 0.0, 0.0, 0.0, 0.0)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ScaleForTest(unittest.TestCase):
    def test_is_one_at_the_reference_width(self):
        image = np.zeros((480, _REFERENCE_WIDTH, 3), dtype=np.uint8)
        self.assertEqual(_scale_for(image), 1.0)

    def test_is_one_below_the_reference_width(self):
        image = np.zeros((240, 320, 3), dtype=np.uint8)
        self.assertEqual(_scale_for(image), 1.0)

    def test_scales_proportionally_above_the_reference_width(self):
        image = np.zeros((3040, 4056, 3), dtype=np.uint8)
        self.assertAlmostEqual(_scale_for(image), 4056 / _REFERENCE_WIDTH)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class DrawingSmokeTest(unittest.TestCase):
    """Kein Pixel-Vergleich (zu bruechig) -- nur: laeuft ohne Fehler und
    veraendert das Bild tatsaechlich, bei kleiner wie bei grosser Aufloesung."""

    def test_draw_tag_overlay_on_a_large_frame(self):
        image = np.zeros((3040, 4056, 3), dtype=np.uint8)

        result = draw_tag_overlay(image, [_tag_pose()], FakeCalibration())

        self.assertTrue((result != 0).any())

    def test_draw_tag_boxes_draws_green_even_for_an_ambiguous_pose(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        base = _tag_pose()
        pose = TagPose(
            tag_id=base.tag_id,
            pose_cam_tag=base.pose_cam_tag,
            reprojection_error_px=0.5,
            ambiguity_ratio=0.99,
            observation=base.observation,
        )
        self.assertTrue(pose.is_ambiguous)

        result = draw_tag_boxes(image, [pose])

        self.assertIs(result, image)
        self.assertEqual(tuple(int(v) for v in image[100, 120]), COLOR_OK)

    def test_draw_tag_boxes_skips_poses_without_observation(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        pose = TagPose(tag_id=3, pose_cam_tag=_tag_pose().pose_cam_tag)

        result = draw_tag_boxes(image, [pose])

        self.assertFalse(result.any())

    def test_draw_status_bar_on_a_large_frame(self):
        image = np.zeros((3040, 4056, 3), dtype=np.uint8)

        result = draw_status_bar(image, ["Modus: AprilTag", "3 Tags erkannt"])

        self.assertTrue((result != 0).any())

    def test_draw_board_overlay_on_a_large_frame(self):
        image = np.zeros((3040, 4056, 3), dtype=np.uint8)

        result = draw_board_overlay(image, None, coverage=(0.5, 0.6))

        self.assertTrue((result != 0).any())


if __name__ == "__main__":
    unittest.main()
