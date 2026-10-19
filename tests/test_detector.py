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

#: Weiches Deckenkamerabild mit den tag36h11-IDs 0, 1, 2, 3, 583 (19.10.2026).
ROHBILD = (
    Path(__file__).resolve().parents[1] / "data" / "rohbilder" / "cam_ceiling_20261019-114704.png"
)
#: Dieselbe Kamera nach dem Neufokussieren, umgebaute Szene: tag36h11-IDs 0, 1
#: (zweimal, beide physisch da), 2, 3, 583, 586; 585 ist beschaedigt und optional.
ROHBILD_SCHARF = (
    Path(__file__).resolve().parents[1] / "data" / "rohbilder" / "cam_ceiling_20261019-142133.png"
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
class DetectorParametersTest(unittest.TestCase):
    """Frei waehlbare `DetectorParameters`, leer/`None` = OpenCV-Standard."""

    def test_none_and_empty_leave_opencv_defaults(self):
        standard = cv2.aruco.DetectorParameters()

        for mapping in (None, {}):
            parameters = ArucoTagDetector("tag36h11", detector_parameters=mapping)._parameters
            self.assertEqual(parameters.errorCorrectionRate, standard.errorCorrectionRate)
            self.assertEqual(
                parameters.adaptiveThreshWinSizeMax, standard.adaptiveThreshWinSizeMax
            )

    def test_sets_the_given_attributes(self):
        detector = ArucoTagDetector(
            "tag36h11",
            detector_parameters={
                "errorCorrectionRate": 0.8,
                "adaptiveThreshWinSizeMax": 33,
                "perspectiveRemoveIgnoredMarginPerCell": 0.3,
            },
        )

        parameters = detector._parameters
        self.assertAlmostEqual(parameters.errorCorrectionRate, 0.8)
        self.assertEqual(parameters.adaptiveThreshWinSizeMax, 33)
        self.assertAlmostEqual(parameters.perspectiveRemoveIgnoredMarginPerCell, 0.3)

    def test_unknown_name_raises_value_error(self):
        with self.assertRaises(ValueError) as caught:
            ArucoTagDetector("tag36h11", detector_parameters={"errorCorrectionrate": 0.8})

        self.assertIn("errorCorrectionrate", str(caught.exception))

    def test_values_are_converted_to_the_attribute_type(self):
        detector = ArucoTagDetector(
            "tag36h11",
            detector_parameters={
                "errorCorrectionRate": 1,  # int -> float
                "perspectiveRemovePixelPerCell": 8.0,  # ganzzahliges float -> int
                "detectInvertedMarker": 1,  # 0/1 -> bool
            },
        )

        parameters = detector._parameters
        self.assertIsInstance(parameters.errorCorrectionRate, float)
        self.assertEqual(parameters.errorCorrectionRate, 1.0)
        self.assertIsInstance(parameters.perspectiveRemovePixelPerCell, int)
        self.assertEqual(parameters.perspectiveRemovePixelPerCell, 8)
        self.assertIs(parameters.detectInvertedMarker, True)

    def test_wrong_type_raises_value_error(self):
        for name, value in (
            ("errorCorrectionRate", "viel"),
            ("perspectiveRemovePixelPerCell", 8.5),
            ("detectInvertedMarker", "ja"),
            ("adaptiveThreshWinSizeMax", True),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    ArucoTagDetector("tag36h11", detector_parameters={name: value})

    def test_changed_parameters_lists_only_deviations(self):
        standard = cv2.aruco.DetectorParameters().errorCorrectionRate

        detector = ArucoTagDetector(
            "tag36h11",
            detector_parameters={"errorCorrectionRate": standard, "adaptiveThreshWinSizeMax": 33},
        )

        self.assertEqual(list(detector.changed_parameters), ["adaptiveThreshWinSizeMax"])

    def test_explicit_corner_refinement_wins_over_the_subpixel_flag(self):
        detector = ArucoTagDetector(
            "tag36h11",
            subpixel_corner_refinement=True,
            detector_parameters={"cornerRefinementMethod": cv2.aruco.CORNER_REFINE_NONE},
        )

        self.assertEqual(
            detector._parameters.cornerRefinementMethod, cv2.aruco.CORNER_REFINE_NONE
        )

    def test_build_detector_passes_them_through(self):
        detector = build_detector(
            "tag36h11", "aruco", detector_parameters={"errorCorrectionRate": 0.8}
        )

        self.assertAlmostEqual(detector._parameters.errorCorrectionRate, 0.8)

    def test_build_detector_rejects_unknown_names(self):
        with self.assertRaises(ValueError):
            build_detector("tag36h11", "aruco", detector_parameters={"gibtsNicht": 1})


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
@unittest.skipUnless(ROHBILD.is_file(), "Rohbild fehlt")
class CeilingRawImageTest(unittest.TestCase):
    """Regression auf dem echten, weichen Deckenbild (4056x3040, alter Fokus)."""

    @classmethod
    def setUpClass(cls):
        cls.gray = cv2.imread(str(ROHBILD), cv2.IMREAD_GRAYSCALE)

    def test_explicit_parameters_find_all_five_tags_of_the_blurry_image(self):
        # Haertefall des alten, unscharfen Fokus (19.10.2026, vor dem
        # Neufokussieren): diese Parameter sind KEIN Preset mehr, der Test
        # dokumentiert den Fall und prueft die Mechanik.
        detector = build_detector(
            "tag36h11",
            "aruco",
            detector_parameters={
                "errorCorrectionRate": 1.0,
                "perspectiveRemoveIgnoredMarginPerCell": 0.3,
                "perspectiveRemovePixelPerCell": 8,
            },
        )

        ids = {observation.tag_id for observation in detector.detect(self.gray)}

        self.assertEqual(ids, {0, 1, 2, 3, 583})

    def test_opencv_defaults_miss_tags(self):
        # Dokumentiert das Problem: ohne die Parameter fehlen Tag 0 und 3.
        ids = {o.tag_id for o in build_detector("tag36h11", "aruco").detect(self.gray)}

        self.assertLess(len(ids), 5)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
@unittest.skipUnless(ROHBILD_SCHARF.is_file(), "Rohbild fehlt")
class CeilingSharpRawImageTest(unittest.TestCase):
    """Regression auf dem neu fokussierten Deckenbild mit dem echten Preset."""

    def test_ceiling_preset_finds_all_required_tags(self):
        from vision_server.server import apriltag_config

        config = apriltag_config("cam_ceiling")
        detector = build_detector(
            config.tag_family,
            config.detector_backend,
            detector_parameters=config.detector_parameters,
        )

        ids = sorted(o.tag_id for o in detector.detect(cv2.imread(str(ROHBILD_SCHARF), cv2.IMREAD_GRAYSCALE)))

        # 585 (beschaedigt) darf fehlen, sonst genau diese Tags -- keine Fehlalarme.
        self.assertEqual([i for i in ids if i != 585], [0, 1, 1, 2, 3, 583, 586])


class AprilTagProfileConfigDefaultTest(unittest.TestCase):
    """Braucht kein `cv2` -- reiner Dataclass-Default-Check."""

    def test_subpixel_corner_refinement_defaults_to_disabled(self):
        # Spart ~46% CPU im Job-Erkennungs-Thread, siehe profiles.py.
        self.assertFalse(AprilTagProfileConfig().subpixel_corner_refinement)

    def test_apriltag_quad_decimate_defaults_to_unchanged_behaviour(self):
        # Noch nicht validiert -- Default darf das Verhalten nicht aendern,
        # bis auf dem Pi mit echten Distanzen gemessen wurde.
        self.assertEqual(AprilTagProfileConfig().apriltag_quad_decimate, 0.0)

    def test_detector_parameters_default_to_empty(self):
        self.assertEqual(AprilTagProfileConfig().detector_parameters, {})

    def test_default_mappings_are_not_shared_between_instances(self):
        first = AprilTagProfileConfig()
        first.detector_parameters["errorCorrectionRate"] = 0.8

        self.assertEqual(AprilTagProfileConfig().detector_parameters, {})

    def test_config_stays_hashable(self):
        hash(AprilTagProfileConfig(detector_parameters={"errorCorrectionRate": 0.8}))


if __name__ == "__main__":
    unittest.main()
