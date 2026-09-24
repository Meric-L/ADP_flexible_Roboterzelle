"""Tests fuer `tagloc.detector`s Subpixel-Eckenverfeinerung als Konfig-Flag.

`cv2.aruco.CORNER_REFINE_APRILTAG` verbessert die Pose-Qualitaet auf Distanz,
kostet aber laut OpenCV-Doku merklich mehr Rechenzeit -- auf pi1 mit
Full-Res-Overlay mitverantwortlich fuer Job-Timeouts (siehe
`AprilTagProfileConfig.subpixel_corner_refinement`). Default jetzt aus, per
Flag wieder einschaltbar. Skips instead of failing without OpenCV, gleiches
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


if __name__ == "__main__":
    unittest.main()
