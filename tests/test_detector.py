"""Tests fuer `tagloc.detector`s Subpixel-Eckenverfeinerung als Konfig-Flag.

`cv2.aruco.CORNER_REFINE_APRILTAG` entscheidet bei kleinen/entfernten Tags
teilweise, OB die ID ueberhaupt dekodierbar ist, nicht nur wie genau die
Pose ist -- ohne sie fand Layer 1 (Deckenkamera) live auf pi1 keine Tags
mehr. `AprilTagProfileConfig.subpixel_corner_refinement` (Default `True`)
steuert das projektweit; `ArucoTagDetector`/`build_detector` selbst bleiben
auf Konstruktor-Ebene bewusst konservativ (Default `False`), Aufrufer
entscheiden explizit. Skips instead of failing without OpenCV, gleiches
Muster wie `test_tag_pipeline.py`.
"""

import unittest

try:
    import cv2

    _ = cv2.aruco
except Exception:  # OpenCV nicht installiert
    cv2 = None

if cv2 is not None:
    from tagloc.detector import ArucoTagDetector, build_detector

from vision_server.profiles import AprilTagProfileConfig


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class SubpixelCornerRefinementTest(unittest.TestCase):
    def test_default_leaves_corner_refinement_untouched(self):
        detector = ArucoTagDetector("tag36h11")

        self.assertNotEqual(
            detector._parameters.cornerRefinementMethod,
            cv2.aruco.CORNER_REFINE_APRILTAG,
        )

    def test_enabled_sets_apriltag_corner_refinement(self):
        detector = ArucoTagDetector("tag36h11", subpixel_corner_refinement=True)

        self.assertEqual(
            detector._parameters.cornerRefinementMethod,
            cv2.aruco.CORNER_REFINE_APRILTAG,
        )

    def test_build_detector_passes_the_flag_through(self):
        detector = build_detector("tag36h11", "aruco", subpixel_corner_refinement=True)

        self.assertEqual(
            detector._parameters.cornerRefinementMethod,
            cv2.aruco.CORNER_REFINE_APRILTAG,
        )

    def test_build_detector_default_stays_off(self):
        detector = build_detector("tag36h11", "aruco")

        self.assertNotEqual(
            detector._parameters.cornerRefinementMethod,
            cv2.aruco.CORNER_REFINE_APRILTAG,
        )


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class AprilTagQuadDecimateTest(unittest.TestCase):
    """`aprilTagQuadDecimate` -- Tuning-Hebel gegen die CPU-Kosten der
    Subpixel-Verfeinerung (py-spy auf pi1: ~46% des Profils in
    `_locate -> detect`), noch nicht auf echten Distanzen validiert."""

    def test_default_leaves_quad_decimate_untouched(self):
        detector = ArucoTagDetector("tag36h11", subpixel_corner_refinement=True)

        self.assertEqual(detector._parameters.aprilTagQuadDecimate, 0.0)

    def test_sets_quad_decimate_when_given(self):
        detector = ArucoTagDetector(
            "tag36h11", subpixel_corner_refinement=True, apriltag_quad_decimate=2.0
        )

        self.assertEqual(detector._parameters.aprilTagQuadDecimate, 2.0)

    def test_has_no_effect_without_subpixel_refinement(self):
        # `aprilTagQuadDecimate` wirkt nur zusammen mit
        # `cornerRefinementMethod == CORNER_REFINE_APRILTAG` -- ohne
        # Verfeinerung wird es bewusst gar nicht erst gesetzt.
        detector = ArucoTagDetector(
            "tag36h11", subpixel_corner_refinement=False, apriltag_quad_decimate=2.0
        )

        self.assertEqual(detector._parameters.aprilTagQuadDecimate, 0.0)

    def test_build_detector_passes_it_through(self):
        detector = build_detector(
            "tag36h11",
            "aruco",
            subpixel_corner_refinement=True,
            apriltag_quad_decimate=1.5,
        )

        self.assertEqual(detector._parameters.aprilTagQuadDecimate, 1.5)


class AprilTagProfileConfigDefaultTest(unittest.TestCase):
    """Braucht kein `cv2` -- reiner Dataclass-Default-Check."""

    def test_subpixel_corner_refinement_defaults_to_enabled(self):
        # Regressionstest: mit `False` fand Layer 1 (Deckenkamera, ~2 m
        # Distanz) live auf pi1 keine Tags mehr, siehe Kommentar in
        # profiles.py.
        self.assertTrue(AprilTagProfileConfig().subpixel_corner_refinement)

    def test_apriltag_quad_decimate_defaults_to_unchanged_behaviour(self):
        # Noch nicht validiert -- Default darf das Verhalten nicht aendern,
        # bis auf dem Pi mit echten Distanzen gemessen wurde.
        self.assertEqual(AprilTagProfileConfig().apriltag_quad_decimate, 0.0)


if __name__ == "__main__":
    unittest.main()
