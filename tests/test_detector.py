"""Tests fuer `tagloc.detector`s Subpixel-Eckenverfeinerung als Konfig-Flag.

`cv2.aruco.CORNER_REFINE_APRILTAG` kostet messbar CPU (py-spy auf pi1: ~46%
des Profils in `_locate -> detect`), ist fuer Dekodierbarkeit aber nicht
noetig. `AprilTagProfileConfig.subpixel_corner_refinement` (Default
`False`) steuert das projektweit; `ArucoTagDetector`/`build_detector`
selbst bleiben auf Konstruktor-Ebene ebenfalls konservativ (Default
`False`). Skips instead of failing without OpenCV, gleiches Muster wie
`test_tag_pipeline.py`.
"""

import unittest
from pathlib import Path

try:
    import cv2

    _ = cv2.aruco
except Exception:  # OpenCV nicht installiert
    cv2 = None

if cv2 is not None:
    from tagloc.detector import ArucoTagDetector, build_detector

from vision_server.profiles import AprilTagProfileConfig
from vision_server.server import apriltag_config

#: Weiches Deckenkamerabild mit den tag36h11-IDs 0, 1, 2, 3, 583 (19.10.2026).
ROHBILD = (
    Path(__file__).resolve().parents[1] / "data" / "rohbilder" / "cam_ceiling_20261019-114704.png"
)


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


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class DecoderParametersTest(unittest.TestCase):
    """Dekodier-Parameter fuer weiche Bilder, `None` = OpenCV-Standard."""

    def test_none_leaves_opencv_defaults(self):
        detector = ArucoTagDetector("tag36h11")
        standard = cv2.aruco.DetectorParameters()

        parameters = detector._parameters
        self.assertEqual(parameters.errorCorrectionRate, standard.errorCorrectionRate)
        self.assertEqual(
            parameters.perspectiveRemoveIgnoredMarginPerCell,
            standard.perspectiveRemoveIgnoredMarginPerCell,
        )
        self.assertEqual(
            parameters.perspectiveRemovePixelPerCell, standard.perspectiveRemovePixelPerCell
        )

    def test_sets_parameters_when_given(self):
        detector = ArucoTagDetector(
            "tag36h11",
            error_correction_rate=1.0,
            perspective_remove_margin_per_cell=0.3,
            perspective_remove_pixel_per_cell=8,
        )

        parameters = detector._parameters
        self.assertEqual(parameters.errorCorrectionRate, 1.0)
        self.assertAlmostEqual(parameters.perspectiveRemoveIgnoredMarginPerCell, 0.3)
        self.assertEqual(parameters.perspectiveRemovePixelPerCell, 8)

    def test_build_detector_passes_them_through(self):
        detector = build_detector("tag36h11", "aruco", error_correction_rate=0.8)

        self.assertAlmostEqual(detector._parameters.errorCorrectionRate, 0.8)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
@unittest.skipUnless(ROHBILD.is_file(), "Rohbild fehlt")
class CeilingRawImageTest(unittest.TestCase):
    """Regression auf dem echten, weichen Deckenbild (4056x3040)."""

    @classmethod
    def setUpClass(cls):
        cls.gray = cv2.imread(str(ROHBILD), cv2.IMREAD_GRAYSCALE)

    def test_cam_ceiling_config_finds_all_five_tags(self):
        config = apriltag_config("cam_ceiling")
        detector = build_detector(
            config.tag_family,
            config.detector_backend,
            error_correction_rate=config.error_correction_rate,
            perspective_remove_margin_per_cell=config.perspective_remove_margin_per_cell,
            perspective_remove_pixel_per_cell=config.perspective_remove_pixel_per_cell,
        )

        ids = {observation.tag_id for observation in detector.detect(self.gray)}

        self.assertEqual(ids, {0, 1, 2, 3, 583})

    def test_opencv_defaults_miss_tags(self):
        # Dokumentiert das Problem: ohne die Parameter fehlen Tag 0 und 3.
        ids = {o.tag_id for o in build_detector("tag36h11", "aruco").detect(self.gray)}

        self.assertLess(len(ids), 5)


class AprilTagProfileConfigDefaultTest(unittest.TestCase):
    """Braucht kein `cv2` -- reiner Dataclass-Default-Check."""

    def test_subpixel_corner_refinement_defaults_to_disabled(self):
        # Spart ~46% CPU im Job-Erkennungs-Thread, siehe profiles.py.
        self.assertFalse(AprilTagProfileConfig().subpixel_corner_refinement)

    def test_apriltag_quad_decimate_defaults_to_unchanged_behaviour(self):
        # Noch nicht validiert -- Default darf das Verhalten nicht aendern,
        # bis auf dem Pi mit echten Distanzen gemessen wurde.
        self.assertEqual(AprilTagProfileConfig().apriltag_quad_decimate, 0.0)

    def test_decoder_parameters_default_to_opencv_standard(self):
        config = AprilTagProfileConfig()

        self.assertIsNone(config.error_correction_rate)
        self.assertIsNone(config.perspective_remove_margin_per_cell)
        self.assertIsNone(config.perspective_remove_pixel_per_cell)

    def test_only_ceiling_preset_sets_decoder_parameters(self):
        ceiling = apriltag_config("cam_ceiling")
        flange = apriltag_config("cam_flange")

        self.assertEqual(
            (
                ceiling.error_correction_rate,
                ceiling.perspective_remove_margin_per_cell,
                ceiling.perspective_remove_pixel_per_cell,
            ),
            (1.0, 0.3, 8),
        )
        self.assertIsNone(flange.error_correction_rate)


if __name__ == "__main__":
    unittest.main()
