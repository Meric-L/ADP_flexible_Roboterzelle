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


class AprilTagProfileConfigDefaultTest(unittest.TestCase):
    """Braucht kein `cv2` -- reiner Dataclass-Default-Check."""

    def test_subpixel_corner_refinement_defaults_to_enabled(self):
        # Regressionstest: mit `False` fand Layer 1 (Deckenkamera, ~2 m
        # Distanz) live auf pi1 keine Tags mehr, siehe Kommentar in
        # profiles.py.
        self.assertTrue(AprilTagProfileConfig().subpixel_corner_refinement)


if __name__ == "__main__":
    unittest.main()
