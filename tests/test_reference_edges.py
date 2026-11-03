"""Tag-Position aus Bezugskanten: Kantensuche, Korrektur, Ansichtsplanung.

Prueft `tagloc.reference_edges` gegen **Referenzwerte**: Der Renderer
`render_reference_scene` (tools/make_synthetic_scene.py) zeichnet einen Tag auf
einer hellen Flaeche vor dunklem Hintergrund; die Flaechenraender sind die
Bezugskanten. Die wahre `T_module_tag` ist bekannt, der Startwert wird in der
Tag-Ebene um 1 cm / 2 Grad verschoben, und die Messung muss ihn zurueckholen.

Ohne OpenCV werden die Tests uebersprungen (wie `test_tag_pipeline.py`); die
reine Mathe (Korrektur, Katalog-RPY, Eingabepruefung) laeuft mit Messungen, die
ohne Bild aus einer bekannten Korrektur gebaut werden.
"""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

try:
    import cv2
    import numpy as np

    _ = (cv2.imread, cv2.aruco, np.ndarray)

    from tagloc.detector import build_detector
    from tagloc.frames import to_gray
    from tagloc.geometry import (
        compose,
        from_rotation_translation,
        invert,
        rotation_distance_rad,
        rotation_from_rvec,
        translation_distance_m,
    )
    from tagloc.pose import estimate_tag_pose
    from tagloc.reference_edges import (
        LineMeasurement,
        ReferenceLine,
        average_corrections,
        catalog_rpy_deg,
        fit_in_plane_correction,
        measure_reference_lines,
        plan_reference_view,
    )
    from tools.make_synthetic_scene import render_reference_scene, synthetic_calibration
except Exception:  # pragma: no cover - ohne OpenCV uebersprungen
    cv2 = None

TAG_SIZE_M = 0.08
TAG_ID = 7

#: Plan: Startfehler 1 cm / 2 Grad -> Ergebnis < 0,5 mm / < 0,2 Grad.
POSITION_TOLERANCE_M = 0.0005
ANGLE_TOLERANCE_DEG = 0.2


def _rotation_z(degrees: float):
    return rotation_from_rvec([0.0, 0.0, math.radians(degrees)])


def _in_plane(dx: float, dy: float, degrees: float):
    """Starre Verschiebung/Drehung in der Tag-Ebene als 4x4."""
    return from_rotation_translation(_rotation_z(degrees), [dx, dy, 0.0])


def _guess_from_truth(pose_module_tag, start_error):
    """Startwert: das wahre `T_tag_module`, in der Tag-Ebene um `start_error` verschoben."""
    return invert(compose(start_error, invert(pose_module_tag)))


def _edge_error(pose_module_tag, pose_true, line: ReferenceLine):
    """Lage der Kante im Tag-KS unter `pose_module_tag` gegen die Wahrheit.

    Gibt `(senkrechter Abstand der Kantenmitte in der Ebene in m, Winkel in Grad)` zurueck.
    """

    def in_tag(pose):
        to_tag = invert(pose)
        start = to_tag[:3, :3] @ line.start + to_tag[:3, 3]
        end = to_tag[:3, :3] @ line.end + to_tag[:3, 3]
        return start, end

    start_a, end_a = in_tag(pose_module_tag)
    start_b, end_b = in_tag(pose_true)
    direction_a = (end_a - start_a)[:2]
    direction_b = (end_b - start_b)[:2]
    direction_b = direction_b / np.linalg.norm(direction_b)
    middle_a = 0.5 * (start_a + end_a)[:2]
    middle_b = 0.5 * (start_b + end_b)[:2]
    distance = abs(
        direction_b[0] * (middle_a - middle_b)[1] - direction_b[1] * (middle_a - middle_b)[0]
    )
    direction_a = direction_a / np.linalg.norm(direction_a)
    cross = direction_a[0] * direction_b[1] - direction_a[1] * direction_b[0]
    return float(distance), abs(math.degrees(math.atan2(cross, float(direction_a @ direction_b))))


def _analytic_measurements(pose_module_tag_guess, lines, delta):
    """Messungen ohne Bild: gemessene Kanten = `delta` angewandt auf die Startwert-Kanten."""
    to_tag = invert(pose_module_tag_guess)
    measurements = []
    for line in lines:
        start = to_tag[:3, :3] @ line.start + to_tag[:3, 3]
        end = to_tag[:3, :3] @ line.end + to_tag[:3, 3]
        middle = 0.5 * (start + end)
        unit = (end - start) / np.linalg.norm(end - start)

        def moved(point):
            return delta[:3, :3] @ point + delta[:3, 3]

        measured_direction = delta[:3, :3] @ unit
        measurements.append(
            LineMeasurement(
                id=line.id,
                found=True,
                guess_point_tag=middle,
                guess_direction_tag=unit,
                point_tag=moved(middle),
                direction_tag=measured_direction,
                offset_m=0.0,
                angle_deg=0.0,
                support_px=100.0,
                residual_px=0.1,
                contrast=50.0,
                candidates=1,
                ambiguous=False,
                reason=None,
                extent_m=float(np.linalg.norm(end - start)),
            )
        )
    return measurements


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class CorrectionMathTest(unittest.TestCase):
    """Richtung und Vorzeichen der Korrektur, ohne Bild."""

    def setUp(self) -> None:
        self.pose_true = from_rotation_translation(_rotation_z(30.0), [0.10, 0.08, 0.0])
        self.line_a = ReferenceLine("A", np.array([0.0, 0.2, 0.0]), np.array([0.0, 0.0, 0.0]))
        self.line_b = ReferenceLine("B", np.array([0.0, 0.0, 0.0]), np.array([0.3, 0.0, 0.0]))
        self.start_error = _in_plane(0.008, -0.006, 2.0)
        self.guess = _guess_from_truth(self.pose_true, self.start_error)

    def test_two_lines_recover_the_true_pose_exactly(self) -> None:
        # Die Wahrheit liegt um `start_error^-1` vom Startwert entfernt:
        # T_tag_module_wahr = D * T_tag_module_guess mit D = start_error^-1.
        delta = invert(self.start_error)
        measurements = _analytic_measurements(self.guess, [self.line_a, self.line_b], delta)
        correction = fit_in_plane_correction(measurements, self.guess)
        self.assertEqual(correction.dofs, "full")
        self.assertLess(translation_distance_m(correction.pose_module_tag, self.pose_true), 1e-9)
        self.assertLess(rotation_distance_rad(correction.pose_module_tag, self.pose_true), 1e-9)
        np.testing.assert_allclose(correction.delta_xy_m, delta[:2, 3], atol=1e-9)
        self.assertAlmostEqual(correction.delta_rot_deg, -2.0, places=6)
        self.assertLess(correction.residual_m, 1e-9)

    def test_the_sign_is_the_measured_minus_the_guess_not_the_opposite(self) -> None:
        # Messung = Startwert um +5 mm in x und +3 Grad verschoben: die Korrektur
        # muss das genau so in `delta` und in die Pose uebernehmen.
        delta = _in_plane(0.005, 0.0, 3.0)
        measurements = _analytic_measurements(self.guess, [self.line_a, self.line_b], delta)
        correction = fit_in_plane_correction(measurements, self.guess)
        expected = invert(compose(delta, invert(self.guess)))
        self.assertLess(translation_distance_m(correction.pose_module_tag, expected), 1e-9)
        self.assertAlmostEqual(correction.delta_rot_deg, 3.0, places=6)
        np.testing.assert_allclose(correction.delta_xy_m, delta[:2, 3], atol=1e-9)

    def test_a_single_line_corrects_only_across_the_edge_and_the_rotation(self) -> None:
        delta = invert(self.start_error)
        measurements = _analytic_measurements(self.guess, [self.line_b], delta)
        correction = fit_in_plane_correction(measurements, self.guess)
        self.assertEqual(correction.dofs, "partial")
        distance, angle = _edge_error(correction.pose_module_tag, self.pose_true, self.line_b)
        self.assertLess(distance, 1e-9)
        self.assertLess(angle, 1e-6)

    def test_parallel_lines_are_only_partial(self) -> None:
        other = ReferenceLine("B2", np.array([0.0, 0.2, 0.0]), np.array([0.3, 0.2, 0.0]))
        measurements = _analytic_measurements(
            self.guess, [self.line_b, other], invert(self.start_error)
        )
        self.assertEqual(fit_in_plane_correction(measurements, self.guess).dofs, "partial")

    def test_no_usable_line_leaves_the_guess_untouched(self) -> None:
        measurements = _analytic_measurements(
            self.guess, [self.line_a, self.line_b], invert(self.start_error)
        )
        unusable = [
            replace(measurements[0], ambiguous=True),
            replace(measurements[1], found=False, reason="NO_EDGE"),
        ]
        correction = fit_in_plane_correction(unusable, self.guess)
        self.assertEqual(correction.dofs, "none")
        np.testing.assert_array_equal(correction.pose_module_tag, self.guess)
        self.assertEqual(float(np.linalg.norm(correction.delta_xy_m)), 0.0)
        self.assertEqual(correction.delta_rot_deg, 0.0)
        self.assertEqual(fit_in_plane_correction([], self.guess).dofs, "none")

    def test_an_ambiguous_line_is_not_used(self) -> None:
        delta = invert(self.start_error)
        measurements = _analytic_measurements(self.guess, [self.line_a, self.line_b], delta)
        # Kante A mit falscher Messung, aber als mehrdeutig markiert: darf nicht eingehen.
        wrong = replace(
            measurements[0], point_tag=measurements[0].point_tag + np.array([0.02, 0.0, 0.0]),
            ambiguous=True,
        )
        correction = fit_in_plane_correction([wrong, measurements[1]], self.guess)
        self.assertEqual(correction.dofs, "partial")
        distance, _ = _edge_error(correction.pose_module_tag, self.pose_true, self.line_b)
        self.assertLess(distance, 1e-9)

    def test_averaging_reports_the_spread(self) -> None:
        corrections = []
        for shift_mm in (1.0, 2.0, 3.0):
            delta = _in_plane(shift_mm / 1000.0, 0.0, shift_mm * 0.1)
            measurements = _analytic_measurements(self.guess, [self.line_a, self.line_b], delta)
            corrections.append(fit_in_plane_correction(measurements, self.guess))
        mean, spread = average_corrections(corrections)
        self.assertEqual(mean.dofs, "full")
        self.assertAlmostEqual(mean.delta_rot_deg, 0.2, places=6)
        self.assertAlmostEqual(float(mean.delta_xy_m[0]), 0.002, places=6)
        self.assertAlmostEqual(spread["spreadDeg"], 0.1 * math.sqrt(2.0 / 3.0), places=6)
        self.assertGreater(spread["spreadMm"], 0.5)
        self.assertLess(spread["spreadMm"], 1.5)

    def test_a_single_frame_has_no_spread_and_the_best_dofs_win(self) -> None:
        full = fit_in_plane_correction(
            _analytic_measurements(self.guess, [self.line_a, self.line_b], _in_plane(0.002, 0.0, 0.0)),
            self.guess,
        )
        partial = fit_in_plane_correction(
            _analytic_measurements(self.guess, [self.line_b], _in_plane(0.0, 0.004, 0.0)), self.guess
        )
        _, spread = average_corrections([full])
        self.assertEqual(spread, {"spreadMm": 0.0, "spreadDeg": 0.0})
        mean, _ = average_corrections([partial, full])
        self.assertEqual(mean.dofs, "full")
        np.testing.assert_allclose(mean.delta_xy_m, full.delta_xy_m, atol=1e-12)
        with self.assertRaises(ValueError):
            average_corrections([])


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class SyntheticReferenceSceneTest(unittest.TestCase):
    """Kantensuche und Korrektur auf gerenderten Bildern."""

    #: Tag-Ebene = Flaechenebene; zwei rechtwinklige Kanten der Flaeche.
    POLYGON = [(0.0, 0.0, 0.0), (0.3, 0.0, 0.0), (0.3, 0.2, 0.0), (0.0, 0.2, 0.0)]

    @classmethod
    def setUpClass(cls) -> None:
        cls.calibration = synthetic_calibration((1600, 1200), focal_px=1600.0)
        cls.pose_true = from_rotation_translation(_rotation_z(30.0), [0.10, 0.08, 0.0])
        cls.line_a = ReferenceLine("A", np.array(cls.POLYGON[3]), np.array(cls.POLYGON[0]))
        cls.line_b = ReferenceLine("B", np.array(cls.POLYGON[0]), np.array(cls.POLYGON[1]))
        cls.start_error = _in_plane(0.008, -0.006, 2.0)  # 1 cm, 2 Grad
        cls.guess = _guess_from_truth(cls.pose_true, cls.start_error)
        cls.detector = build_detector("tag36h11", "aruco", subpixel_corner_refinement=True)

    def _view(self, pose_module_tag_guess, lines, calibration=None, tilt_deg=0.0):
        plan = plan_reference_view(
            pose_module_tag_guess,
            lines,
            calibration or self.calibration,
            tag_size_m=TAG_SIZE_M,
            tilt_deg=tilt_deg,
        )
        self.assertTrue(plan.fits)
        return invert(plan.pose_tag_cam)

    def _render(self, pose_cam_tag, pose_module_tag=None, polygon=None, calibration=None, **options):
        options.setdefault("noise_sigma", 1.0)
        image, truth = render_reference_scene(
            pose_cam_tag,
            self.pose_true if pose_module_tag is None else pose_module_tag,
            calibration or self.calibration,
            polygon or self.POLYGON,
            tag_id=TAG_ID,
            tag_size_m=TAG_SIZE_M,
            **options,
        )
        return to_gray(image), truth

    def _assert_corrected(self, correction, pose_true=None, lines=None):
        pose_true = self.pose_true if pose_true is None else pose_true
        self.assertEqual(correction.dofs, "full")
        self.assertLess(
            translation_distance_m(correction.pose_module_tag, pose_true), POSITION_TOLERANCE_M
        )
        self.assertLess(
            math.degrees(rotation_distance_rad(correction.pose_module_tag, pose_true)),
            ANGLE_TOLERANCE_DEG,
        )

    def test_the_start_error_is_recovered_with_the_true_tag_pose(self) -> None:
        lines = [self.line_a, self.line_b]
        pose_cam_tag = self._view(self.guess, lines)
        gray, _ = self._render(pose_cam_tag)
        measurements = measure_reference_lines(
            gray, self.calibration, pose_cam_tag, self.guess, lines, tag_size_m=TAG_SIZE_M
        )
        for item in measurements:
            self.assertTrue(item.found, item.reason)
            self.assertFalse(item.ambiguous)
            self.assertIsNone(item.reason)
            self.assertLess(item.residual_px, 0.3)
            self.assertGreater(item.support_px, 100.0)
        correction = fit_in_plane_correction(measurements, self.guess)
        self._assert_corrected(correction)
        # Der Startfehler war tatsaechlich da (sonst waere der Test leer).
        self.assertGreater(translation_distance_m(self.guess, self.pose_true), 0.009)

    def test_several_start_errors_in_both_directions(self) -> None:
        lines = [self.line_a, self.line_b]
        for dx, dy, degrees in ((-0.01, 0.0, -2.0), (0.0, 0.01, 1.5), (0.007, 0.007, -2.0)):
            with self.subTest(dx=dx, dy=dy, grad=degrees):
                guess = _guess_from_truth(self.pose_true, _in_plane(dx, dy, degrees))
                pose_cam_tag = self._view(guess, lines)
                gray, _ = self._render(pose_cam_tag)
                measurements = measure_reference_lines(
                    gray, self.calibration, pose_cam_tag, guess, lines, tag_size_m=TAG_SIZE_M
                )
                self._assert_corrected(fit_in_plane_correction(measurements, guess))

    def test_the_start_error_is_recovered_with_the_detected_tag_pose(self) -> None:
        # Leicht gekippt, damit die Tag-Neigung bestimmt ist (frontal nicht).
        lines = [self.line_a, self.line_b]
        pose_cam_tag = self._view(self.guess, lines, tilt_deg=15.0)
        gray, _ = self._render(pose_cam_tag)
        observations = self.detector.detect(gray)
        self.assertEqual([item.tag_id for item in observations], [TAG_ID])
        detected = estimate_tag_pose(observations[0], TAG_SIZE_M, self.calibration).pose_cam_tag
        measurements = measure_reference_lines(
            gray, self.calibration, detected, self.guess, lines, tag_size_m=TAG_SIZE_M
        )
        self._assert_corrected(fit_in_plane_correction(measurements, self.guess))

    def test_lens_distortion_is_handled(self) -> None:
        calibration = replace(
            self.calibration, distortion=np.array([-0.12, 0.03, 0.001, -0.001, 0.0])
        )
        lines = [self.line_a, self.line_b]
        pose_cam_tag = self._view(self.guess, lines, calibration)
        gray, _ = self._render(pose_cam_tag, calibration=calibration)
        measurements = measure_reference_lines(
            gray, calibration, pose_cam_tag, self.guess, lines, tag_size_m=TAG_SIZE_M
        )
        for item in measurements:
            self.assertTrue(item.found, item.reason)
        self._assert_corrected(fit_in_plane_correction(measurements, self.guess))

    def test_blur_and_noise(self) -> None:
        lines = [self.line_a, self.line_b]
        pose_cam_tag = self._view(self.guess, lines)
        gray, _ = self._render(pose_cam_tag, noise_sigma=4.0, blur_sigma_px=1.5)
        measurements = measure_reference_lines(
            gray, self.calibration, pose_cam_tag, self.guess, lines, tag_size_m=TAG_SIZE_M
        )
        self._assert_corrected(fit_in_plane_correction(measurements, self.guess))

    def test_an_edge_below_the_tag_plane_is_measured_correctly(self) -> None:
        # Tag 3 cm ueber der Flaeche: die Kanten liegen bei Tag-z = -0.03.
        raised = from_rotation_translation(_rotation_z(30.0), [0.10, 0.08, 0.03])
        lines = [self.line_a, self.line_b]
        guess = _guess_from_truth(raised, self.start_error)
        pose_cam_tag = self._view(guess, lines, tilt_deg=10.0)
        gray, _ = self._render(pose_cam_tag, pose_module_tag=raised)
        measurements = measure_reference_lines(
            gray, self.calibration, pose_cam_tag, guess, lines, tag_size_m=TAG_SIZE_M
        )
        for item in measurements:
            self.assertTrue(item.found, item.reason)
            self.assertAlmostEqual(float(item.guess_point_tag[2]), -0.03, places=6)
            self.assertAlmostEqual(float(item.point_tag[2]), -0.03, places=3)
        self._assert_corrected(fit_in_plane_correction(measurements, guess), pose_true=raised)

    def test_a_single_line_gives_a_partial_correction_across_the_edge(self) -> None:
        pose_cam_tag = self._view(self.guess, [self.line_a, self.line_b])
        gray, _ = self._render(pose_cam_tag)
        for line in (self.line_a, self.line_b):
            with self.subTest(kante=line.id):
                measurements = measure_reference_lines(
                    gray, self.calibration, pose_cam_tag, self.guess, [line], tag_size_m=TAG_SIZE_M
                )
                correction = fit_in_plane_correction(measurements, self.guess)
                self.assertEqual(correction.dofs, "partial")
                distance, angle = _edge_error(correction.pose_module_tag, self.pose_true, line)
                self.assertLess(distance, POSITION_TOLERANCE_M)
                self.assertLess(angle, ANGLE_TOLERANCE_DEG)

    def test_an_item_groove_next_to_the_edge_is_ambiguous_or_the_right_edge(self) -> None:
        # Zweite parallele Kante 5 mm neben der Kante A (Index 3: (0,0.2) -> (0,0)).
        lines = [self.line_a, self.line_b]
        pose_cam_tag = self._view(self.guess, lines)
        gray, _ = self._render(pose_cam_tag, groove_edges={3: 0.005})
        measurements = measure_reference_lines(
            gray, self.calibration, pose_cam_tag, self.guess, lines, tag_size_m=TAG_SIZE_M
        )
        groove_line, plain_line = measurements
        self.assertGreaterEqual(groove_line.candidates, 2)
        self.assertEqual(plain_line.candidates, 1)
        self.assertFalse(plain_line.ambiguous)
        correction = fit_in_plane_correction(measurements, self.guess)
        if groove_line.ambiguous:
            # Nicht verwenden: nur Kante B geht ein, Teilkorrektur.
            self.assertEqual(correction.dofs, "partial")
            distance, _ = _edge_error(correction.pose_module_tag, self.pose_true, self.line_b)
            self.assertLess(distance, POSITION_TOLERANCE_M)
        else:
            self._assert_corrected(correction)

    def test_an_edge_outside_the_image_is_not_visible(self) -> None:
        far = ReferenceLine("fern", np.array([-1.0, 0.0, 0.0]), np.array([-1.0, 0.2, 0.0]))
        pose_cam_tag = self._view(self.guess, [self.line_a, self.line_b])
        gray, _ = self._render(pose_cam_tag)
        measurements = measure_reference_lines(
            gray, self.calibration, pose_cam_tag, self.guess, [far, self.line_b],
            tag_size_m=TAG_SIZE_M,
        )
        self.assertFalse(measurements[0].found)
        self.assertEqual(measurements[0].reason, "NOT_VISIBLE")
        self.assertIsNone(measurements[0].point_tag)
        self.assertIsNone(measurements[0].offset_m)
        self.assertTrue(measurements[1].found)
        # Eine fehlende Kante senkt nur die Freiheitsgrade.
        self.assertEqual(fit_in_plane_correction(measurements, self.guess).dofs, "partial")

    def test_an_edge_without_contrast_has_no_edge(self) -> None:
        # Kante mitten auf der gleichmaessig hellen Flaeche: nichts zu finden.
        inside = ReferenceLine("mitte", np.array([0.2, 0.05, 0.0]), np.array([0.2, 0.15, 0.0]))
        pose_cam_tag = self._view(self.guess, [self.line_a, self.line_b])
        gray, _ = self._render(pose_cam_tag)
        measurements = measure_reference_lines(
            gray, self.calibration, pose_cam_tag, self.guess, [inside], tag_size_m=TAG_SIZE_M
        )
        self.assertFalse(measurements[0].found)
        self.assertEqual(measurements[0].reason, "NO_EDGE")

    def test_an_edge_perpendicular_to_the_tag_plane_is_rejected(self) -> None:
        upright = ReferenceLine("steil", np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 0.06]))
        pose_cam_tag = self._view(self.guess, [self.line_a, self.line_b])
        gray, _ = self._render(pose_cam_tag)
        measurements = measure_reference_lines(
            gray, self.calibration, pose_cam_tag, self.guess, [upright, self.line_b],
            tag_size_m=TAG_SIZE_M,
        )
        self.assertFalse(measurements[0].found)
        self.assertEqual(measurements[0].reason, "PERPENDICULAR_TO_TAG")
        self.assertTrue(measurements[1].found)
        # 9 Grad gegen die Normale ist noch "senkrecht", 12 Grad nicht.
        for degrees, expected in ((9.0, "PERPENDICULAR_TO_TAG"), (12.0, None)):
            with self.subTest(grad=degrees):
                end = np.array([0.06 * math.sin(math.radians(degrees)), 0.0, 0.06 * math.cos(math.radians(degrees))])
                line = ReferenceLine("schief", np.array([0.3, 0.1, 0.0]), end + np.array([0.3, 0.1, 0.0]))
                result = measure_reference_lines(
                    gray, self.calibration, pose_cam_tag, self.guess, [line], tag_size_m=TAG_SIZE_M
                )[0]
                self.assertEqual(result.reason == "PERPENDICULAR_TO_TAG", expected is not None)

    def test_the_result_is_in_the_catalog_format_roundtrip(self) -> None:
        lines = [self.line_a, self.line_b]
        pose_cam_tag = self._view(self.guess, lines)
        gray, _ = self._render(pose_cam_tag)
        correction = fit_in_plane_correction(
            measure_reference_lines(
                gray, self.calibration, pose_cam_tag, self.guess, lines, tag_size_m=TAG_SIZE_M
            ),
            self.guess,
        )
        roll, pitch, yaw = catalog_rpy_deg(correction.pose_module_tag)
        rebuilt = rotation_from_rvec([0, 0, math.radians(yaw)]) @ rotation_from_rvec(
            [0, math.radians(pitch), 0]
        ) @ rotation_from_rvec([math.radians(roll), 0, 0])
        np.testing.assert_allclose(rebuilt, correction.pose_module_tag[:3, :3], atol=1e-9)
        self.assertAlmostEqual(yaw, 30.0, delta=0.3)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ViewPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = synthetic_calibration((1600, 1200), focal_px=1600.0)
        self.guess = from_rotation_translation(_rotation_z(30.0), [0.10, 0.08, 0.0])
        self.line_a = ReferenceLine("A", np.array([0.0, 0.2, 0.0]), np.array([0.0, 0.0, 0.0]))
        self.line_b = ReferenceLine("B", np.array([0.0, 0.0, 0.0]), np.array([0.3, 0.0, 0.0]))

    def _project(self, pose_tag_cam, points):
        pose_cam_tag = invert(pose_tag_cam)
        camera = np.asarray(points) @ pose_cam_tag[:3, :3].T + pose_cam_tag[:3, 3]
        matrix = self.calibration.camera_matrix
        return np.column_stack(
            [
                matrix[0, 0] * camera[:, 0] / camera[:, 2] + matrix[0, 2],
                matrix[1, 1] * camera[:, 1] / camera[:, 2] + matrix[1, 2],
            ]
        ), camera[:, 2]

    def _targets_in_tag_frame(self):
        to_tag = invert(self.guess)
        corners = np.array(
            [[-0.04, 0.04, 0.0], [0.04, 0.04, 0.0], [0.04, -0.04, 0.0], [-0.04, -0.04, 0.0]]
        )
        return corners, to_tag

    def test_tag_corners_and_edge_pieces_are_inside_the_image_with_margin(self) -> None:
        for tilt in (0.0, 20.0):
            with self.subTest(kippung=tilt):
                plan = plan_reference_view(
                    self.guess, [self.line_a, self.line_b], self.calibration,
                    tag_size_m=TAG_SIZE_M, tilt_deg=tilt,
                )
                self.assertTrue(plan.fits)
                self.assertEqual(plan.uncovered, [])
                self.assertGreaterEqual(plan.standoff_m, 0.25)
                self.assertLessEqual(plan.standoff_m, 0.60)
                corners, to_tag = self._targets_in_tag_frame()
                points = [corners]
                for line in (self.line_a, self.line_b):
                    start = to_tag[:3, :3] @ line.start + to_tag[:3, 3]
                    end = to_tag[:3, :3] @ line.end + to_tag[:3, 3]
                    # tagnahes 10-cm-Stueck: der Tag-Mittelpunkt liegt 10 bzw. 8 cm neben der Kante
                    direction = (end - start) / np.linalg.norm(end - start)
                    foot = start + direction * max(0.0, min(np.linalg.norm(end - start), -start @ direction))
                    points.append(np.array([foot - 0.05 * direction, foot + 0.05 * direction]))
                pixels, depth = self._project(plan.pose_tag_cam, np.concatenate(points))
                self.assertTrue(np.all(depth > 0.0))
                self.assertTrue(np.all(pixels[:, 0] >= 0.1 * 1600 - 1.0))
                self.assertTrue(np.all(pixels[:, 0] <= 0.9 * 1600 + 1.0))
                self.assertTrue(np.all(pixels[:, 1] >= 0.1 * 1200 - 1.0))
                self.assertTrue(np.all(pixels[:, 1] <= 0.9 * 1200 + 1.0))

    def test_the_camera_looks_down_onto_the_tag_with_the_tag_top_up(self) -> None:
        plan = plan_reference_view(
            self.guess, [self.line_a, self.line_b], self.calibration, tag_size_m=TAG_SIZE_M
        )
        rotation = plan.pose_tag_cam[:3, :3]
        np.testing.assert_allclose(rotation[:, 2], [0.0, 0.0, -1.0], atol=1e-12)  # Blick: -Tag-z
        np.testing.assert_allclose(rotation[:, 0], [1.0, 0.0, 0.0], atol=1e-12)  # Bild-x = Tag-x
        self.assertAlmostEqual(float(np.linalg.det(rotation)), 1.0, places=9)
        self.assertGreater(float(plan.pose_tag_cam[2, 3]), 0.0)
        # Tag-Oberkante (+y) liegt im Bild oben.
        pixels, _ = self._project(plan.pose_tag_cam, [[0.0, 0.04, 0.0], [0.0, -0.04, 0.0]])
        self.assertLess(pixels[0, 1], pixels[1, 1])

    def test_a_tilt_moves_the_camera_off_the_tag_normal(self) -> None:
        plan = plan_reference_view(
            self.guess, [self.line_a], self.calibration, tag_size_m=TAG_SIZE_M, tilt_deg=20.0
        )
        view = plan.pose_tag_cam[:3, 2]  # Blickrichtung (Kamera-z) im Tag-KS
        angle = math.degrees(math.acos(float(np.clip(view @ [0.0, 0.0, -1.0], -1.0, 1.0))))
        self.assertAlmostEqual(angle, 20.0, places=6)

    def test_a_far_edge_does_not_fit_and_is_named(self) -> None:
        far = ReferenceLine("fern", np.array([1.2, 0.0, 0.0]), np.array([1.2, 0.2, 0.0]))
        plan = plan_reference_view(
            self.guess, [self.line_a, far], self.calibration, tag_size_m=TAG_SIZE_M
        )
        self.assertFalse(plan.fits)
        self.assertEqual(plan.uncovered, ["fern"])
        # Die Pose gilt fuer Tag + Kante A, die zusammen schon bei 25 cm passen:
        # die ferne Kante zieht den Tag nicht aus dem Bild.
        self.assertAlmostEqual(plan.standoff_m, 0.25, places=9)
        pixels, depth = self._project(
            plan.pose_tag_cam, [[-0.04, 0.04, 0.0], [0.04, -0.04, 0.0]]
        )
        self.assertTrue(np.all(depth > 0.0))
        self.assertTrue(np.all((pixels > [160.0, 120.0]) & (pixels < [1440.0, 1080.0])))

    def test_a_close_target_uses_the_minimum_standoff(self) -> None:
        plan = plan_reference_view(
            self.guess, [self.line_a], self.calibration, tag_size_m=0.01,
            line_extent_m=0.02, min_standoff_m=0.25,
        )
        self.assertTrue(plan.fits)
        self.assertGreaterEqual(plan.standoff_m, 0.25)


class CatalogRpyTest(unittest.TestCase):
    @staticmethod
    def _rebuild(roll, pitch, yaw):
        # R = Rz(yaw) * Ry(pitch) * Rx(roll), feste Achsen, wie `quaternionFromRpyDeg`.
        cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
        cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
        cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
        rx = [[1, 0, 0], [0, cr, -sr], [0, sr, cr]]
        ry = [[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]
        rz = [[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]
        import numpy

        return numpy.array(rz) @ numpy.array(ry) @ numpy.array(rx)

    @unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
    def test_roundtrip(self) -> None:
        for angles in ((0, 0, 0), (10, 20, 30), (-170, 45, 120), (90, -60, -45), (3, -89, 179)):
            with self.subTest(rpy=angles):
                recovered = catalog_rpy_deg(self._rebuild(*angles))
                np.testing.assert_allclose(self._rebuild(*recovered), self._rebuild(*angles), atol=1e-9)
        np.testing.assert_allclose(catalog_rpy_deg(self._rebuild(10, 20, 30)), [10, 20, 30], atol=1e-9)

    @unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
    def test_gimbal_lock_puts_everything_into_roll(self) -> None:
        recovered = catalog_rpy_deg(self._rebuild(25, 90, 40))
        self.assertAlmostEqual(float(recovered[1]), 90.0, places=6)
        self.assertEqual(float(recovered[2]), 0.0)
        np.testing.assert_allclose(self._rebuild(*recovered), self._rebuild(25, 90, 40), atol=1e-9)

    @unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
    def test_a_pose_is_accepted_and_known_values_match_the_convention(self) -> None:
        # Tag um 90 Grad um z gedreht: yaw = 90 (nicht roll).
        pose = from_rotation_translation(_rotation_z(90.0), [1.0, 2.0, 3.0])
        np.testing.assert_allclose(catalog_rpy_deg(pose), [0.0, 0.0, 90.0], atol=1e-9)

    @unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
    def test_invalid_input(self) -> None:
        with self.assertRaises(ValueError):
            catalog_rpy_deg(np.zeros((2, 2)))
        with self.assertRaises(ValueError):
            catalog_rpy_deg(np.full((3, 3), np.nan))


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class InvalidInputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = synthetic_calibration((640, 480), focal_px=640.0)
        self.guess = from_rotation_translation(np.eye(3), [0.1, 0.1, 0.0])
        self.pose_cam_tag = from_rotation_translation(np.diag([1.0, -1.0, -1.0]), [0.0, 0.0, 0.3])
        self.gray = np.full((480, 640), 128, dtype=np.uint8)
        self.line = ReferenceLine("A", np.array([0.0, 0.0, 0.0]), np.array([0.3, 0.0, 0.0]))

    def _measure(self, **overrides):
        arguments = dict(
            gray=self.gray, calib=self.calibration, pose_cam_tag=self.pose_cam_tag,
            pose_module_tag_guess=self.guess, lines=[self.line], tag_size_m=0.08,
        )
        arguments.update(overrides)
        return measure_reference_lines(**arguments)

    def test_a_valid_call_on_a_flat_image_finds_nothing(self) -> None:
        result = self._measure()
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0].found)

    def test_a_short_line_is_rejected(self) -> None:
        short = ReferenceLine("kurz", np.zeros(3), np.array([0.019, 0.0, 0.0]))
        with self.assertRaises(ValueError):
            self._measure(lines=[short])
        with self.assertRaises(ValueError):
            plan_reference_view(self.guess, [short], self.calibration, tag_size_m=0.08)

    def test_tag_size_must_be_positive_and_finite(self) -> None:
        for size in (0.0, -0.08, float("nan"), float("inf")):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    self._measure(tag_size_m=size)
                with self.assertRaises(ValueError):
                    plan_reference_view(self.guess, [self.line], self.calibration, tag_size_m=size)

    def test_non_finite_values_are_rejected(self) -> None:
        bad_line = ReferenceLine("nan", np.array([np.nan, 0.0, 0.0]), np.array([0.3, 0.0, 0.0]))
        with self.assertRaises(ValueError):
            self._measure(lines=[bad_line])
        bad_pose = self.guess.copy()
        bad_pose[0, 3] = np.inf
        with self.assertRaises(ValueError):
            self._measure(pose_module_tag_guess=bad_pose)
        with self.assertRaises(ValueError):
            self._measure(pose_cam_tag=np.full((4, 4), np.nan))
        with self.assertRaises(ValueError):
            self._measure(search_band_m=float("nan"))
        with self.assertRaises(ValueError):
            plan_reference_view(bad_pose, [self.line], self.calibration, tag_size_m=0.08)

    def test_a_wrong_resolution_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._measure(gray=np.full((100, 100), 128, dtype=np.uint8))

    def test_view_plan_parameters_are_checked(self) -> None:
        for override in (
            {"margin": 0.6},
            {"min_standoff_m": 0.7, "max_standoff_m": 0.3},
            {"line_extent_m": 0.0},
            {"tilt_deg": 85.0},
            {"tilt_deg": float("nan")},
        ):
            with self.subTest(**override):
                with self.assertRaises(ValueError):
                    plan_reference_view(
                        self.guess, [self.line], self.calibration, tag_size_m=0.08, **override
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
