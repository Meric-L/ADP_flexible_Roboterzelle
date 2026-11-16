"""Tests for the subpixel corner refinement of `tagloc.detector` as a config flag.

`cv2.aruco.CORNER_REFINE_APRILTAG` costs measurable CPU (py-spy on pi1: about 46%
of the profile in `_locate -> detect`) but is not needed for decodability.
`AprilTagProfileConfig.subpixel_corner_refinement` (default `False`) controls
this project-wide; `ArucoTagDetector`/`build_detector` also stay conservative at
constructor level (default `False`). Tests skip without OpenCV, as in
`test_tag_pipeline.py`.
"""

import unittest
from pathlib import Path

try:
    import cv2

    _ = cv2.aruco
except Exception:  # OpenCV not installed
    cv2 = None

if cv2 is not None:
    from tagloc.detector import ArucoTagDetector, build_detector

from vision_server.profiles import AprilTagProfileConfig

#: Soft ceiling-camera image with the tag36h11 IDs 0, 1, 2, 3, 583.
ROHBILD = (
    Path(__file__).resolve().parents[1] / "data" / "rohbilder" / "cam_ceiling_20261019-114704.png"
)
#: Same camera after refocusing, rebuilt scene: tag36h11 IDs 0, 1 (twice, both
#: physically present), 2, 3, 583, 586; 585 is damaged and optional.
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
    """`aprilTagQuadDecimate`: tuning lever against the CPU cost of the subpixel
    refinement (py-spy on pi1: about 46% of the profile in `_locate -> detect`),
    not yet validated at real distances."""

    def test_default_leaves_quad_decimate_untouched(self):
        detector = ArucoTagDetector("tag36h11", subpixel_corner_refinement=True)

        self.assertEqual(detector._parameters.aprilTagQuadDecimate, 0.0)

    def test_sets_quad_decimate_when_given(self):
        detector = ArucoTagDetector(
            "tag36h11", subpixel_corner_refinement=True, apriltag_quad_decimate=2.0
        )

        self.assertEqual(detector._parameters.aprilTagQuadDecimate, 2.0)

    def test_has_no_effect_without_subpixel_refinement(self):
        # `aprilTagQuadDecimate` only acts together with
        # `cornerRefinementMethod == CORNER_REFINE_APRILTAG`; without refinement it
        # is deliberately not set at all.
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
    """Freely selectable `DetectorParameters`; empty/`None` = OpenCV default."""

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
                "perspectiveRemovePixelPerCell": 8.0,  # integral float -> int
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
    """Regression on the real, soft ceiling image (4056x3040, old focus)."""

    @classmethod
    def setUpClass(cls):
        cls.gray = cv2.imread(str(ROHBILD), cv2.IMREAD_GRAYSCALE)

    def test_explicit_parameters_find_all_five_tags_of_the_blurry_image(self):
        # Hard case of the old, unsharp focus: these parameters are NO preset any
        # more; the test documents the case and checks the mechanics.
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
        # Documents the problem: without the parameters, tags 0 and 3 are missing.
        ids = {o.tag_id for o in build_detector("tag36h11", "aruco").detect(self.gray)}

        self.assertLess(len(ids), 5)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
@unittest.skipUnless(ROHBILD_SCHARF.is_file(), "Rohbild fehlt")
class CeilingSharpRawImageTest(unittest.TestCase):
    """Regression on the newly focused ceiling image with the real preset."""

    def test_ceiling_preset_finds_all_required_tags(self):
        from vision_server.server import apriltag_config

        config = apriltag_config("cam_ceiling")
        detector = build_detector(
            config.tag_family,
            config.detector_backend,
            detector_parameters=config.detector_parameters,
        )

        ids = sorted(o.tag_id for o in detector.detect(cv2.imread(str(ROHBILD_SCHARF), cv2.IMREAD_GRAYSCALE)))

        # 585 (damaged) may be missing; otherwise exactly these tags, no false alarms.
        self.assertEqual([i for i in ids if i != 585], [0, 1, 1, 2, 3, 583, 586])


class AprilTagProfileConfigDefaultTest(unittest.TestCase):
    """Needs no `cv2`; a pure dataclass default check."""

    def test_subpixel_corner_refinement_defaults_to_disabled(self):
        # Saves about 46% CPU in the job detection thread, see profiles.py.
        self.assertFalse(AprilTagProfileConfig().subpixel_corner_refinement)

    def test_apriltag_quad_decimate_defaults_to_unchanged_behaviour(self):
        # Not validated yet: the default must not change behaviour until it has been
        # measured on the Pi with real distances.
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
