"""Tests fuer die Bildmessung der Wuerfelablage (`use_case_eval`).

Die Messung wird an synthetischen Bildern mit bekannter Wahrheit geprueft
(`use_case_eval.synthetic`: RealSense-aehnlich 640x480, fx = 466, Kamera rund
20 cm ueber der Wuerfeloberseite, mit Supersampling gerendert).
"""

import json
import tempfile
import unittest
from pathlib import Path

try:
    import cv2  # noqa: F401
    import numpy as np

    from use_case_eval import live, measure, synthetic, target
    from use_case_eval.measure import TrialMeasurement
except Exception:  # pragma: no cover - numpy/OpenCV fehlen
    np = None

#: Kamera ueber dem Blatt (mm): 20 cm ueber der Wuerfeloberseite.
CAMERA_HEIGHT_MM = 220.0
#: Ziel der Messgenauigkeit auf synthetischen Bildern.
POSITION_TOLERANCE_MM = 0.1
ANGLE_TOLERANCE_DEG = 0.2


def angle_error(measured: float, true: float) -> float:
    """Differenz zweier Gierwinkel modulo 90 Grad (Wuerfel ist 90-Grad-symmetrisch)."""
    return (measured - true + 45.0) % 90.0 - 45.0


def render(dx, dy, dtheta, camera=(0.0, 0.0, 0.0, 0.0), sticker=(0.0, 0.0), **kwargs):
    """Bild einer Ablage; `camera` = (x, y, Gier, Neigung) der Kamera ueber dem Blatt."""
    x, y, yaw, tilt = camera
    pose = synthetic.look_down_pose(x, y, CAMERA_HEIGHT_MM, yaw, tilt)
    return synthetic.render_trial(
        CALIBRATION, pose, dx, dy, dtheta, sticker_offset_mm=sticker, supersample=4, **kwargs
    )


if np is not None:
    CALIBRATION = synthetic.make_calibration()


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class TargetTest(unittest.TestCase):
    def test_zones_follow_the_circles_on_the_sheet(self):
        # Die Kreise: Wuerfeldiagonale 28,3 mm + 2 x 1 / 3 / 5 mm.
        self.assertEqual(target.zone_for_radius(14.14), "gruen")
        self.assertEqual(target.zone_for_radius(15.15), "gruen")
        self.assertEqual(target.zone_for_radius(15.16), "gelb")
        self.assertEqual(target.zone_for_radius(17.15), "gelb")
        self.assertEqual(target.zone_for_radius(19.15), "rot")
        self.assertEqual(target.zone_for_radius(19.16), "ausserhalb")

    def test_cube_corners_turn_about_the_middle(self):
        corners = target.cube_corners_xy((1.0, 2.0), 0.0)
        np.testing.assert_allclose(corners, [[-9, 12], [11, 12], [11, -8], [-9, -8]])
        turned = target.cube_corners_xy((0.0, 0.0), 45.0)
        np.testing.assert_allclose(np.hypot(turned[:, 0], turned[:, 1]), [10 * np.sqrt(2)] * 4)
        np.testing.assert_allclose(turned[0], [-10 * np.sqrt(2), 0.0], atol=1e-12)

    def test_sheet_markers_lie_square_around_the_middle(self):
        self.assertEqual(sorted(target.SHEET_MARKERS), [0, 1, 2, 3])
        corners = target.sheet_marker_corners(0)
        # Reihenfolge wie OpenCV: oben-links, oben-rechts, unten-rechts, unten-links (y oben).
        np.testing.assert_allclose(corners, [[-55, 55, 0], [-35, 55, 0], [-35, 35, 0], [-55, 35, 0]])

    def test_sheet_markers_stay_inside_the_scale_ring(self):
        for marker_id in target.SHEET_MARKERS:
            radii = np.hypot(*target.sheet_marker_corners(marker_id)[:, :2].T)
            self.assertLess(radii.max(), target.RING_INNER_RADIUS_MM)


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class MeasureTrialTest(unittest.TestCase):
    def assertMeasured(self, measurement, dx, dy, dtheta):
        self.assertTrue(measurement.ok, measurement.grund)
        self.assertLessEqual(abs(measurement.dx_mm - dx), POSITION_TOLERANCE_MM)
        self.assertLessEqual(abs(measurement.dy_mm - dy), POSITION_TOLERANCE_MM)
        self.assertLessEqual(abs(angle_error(measurement.dtheta_deg, dtheta)), ANGLE_TOLERANCE_DEG)

    def test_measures_a_centred_cube_as_zero(self):
        m = measure.measure_trial(render(0, 0, 0), CALIBRATION)
        self.assertMeasured(m, 0, 0, 0)
        self.assertEqual(m.zone, "gruen")
        self.assertEqual(m.sheet_markers, [0, 1, 2, 3])

    def test_measures_offset_and_turn_from_any_camera_pose(self):
        rng = np.random.default_rng(7)
        for _ in range(10):
            dx, dy, dtheta = rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-30, 30)
            camera = (rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(0, 360), rng.uniform(-3, 3))
            with self.subTest(dx=dx, dy=dy, dtheta=dtheta, camera=camera):
                self.assertMeasured(measure.measure_trial(render(dx, dy, dtheta, camera), CALIBRATION), dx, dy, dtheta)

    def test_turn_is_folded_to_a_quarter(self):
        # 70 Grad sieht aus wie -20 Grad: der Wuerfel ist 90-Grad-symmetrisch.
        m = measure.measure_trial(render(0, 0, 70), CALIBRATION)
        self.assertTrue(m.ok, m.grund)
        self.assertAlmostEqual(m.dtheta_deg, -20.0, delta=ANGLE_TOLERANCE_DEG)
        self.assertGreaterEqual(m.dtheta_deg, -45.0)
        self.assertLess(m.dtheta_deg, 45.0)

    def test_reports_where_the_camera_stood(self):
        m = measure.measure_trial(render(1.0, 0.0, 0.0, camera=(3.0, -2.0, 30.0, 2.0)), CALIBRATION)
        self.assertTrue(m.ok, m.grund)
        self.assertAlmostEqual(m.camera_height_mm, CAMERA_HEIGHT_MM, delta=1.0)
        self.assertAlmostEqual(m.camera_tilt_deg, 2.0, delta=0.2)
        self.assertLess(m.reprojection_rms_px, 0.5)
        self.assertIsNotNone(m.nadir_offset_mm)
        self.assertLess(m.nadir_offset_mm, 20.0)

    def test_zone_is_what_one_sees_on_the_sheet(self):
        # Aeusserste Ecke bei Versatz d in x (ohne Drehung): hypot(10 + d, 10).
        for dx, zone in [(0.5, "gruen"), (2.5, "gelb"), (4.5, "rot"), (7.0, "ausserhalb")]:
            with self.subTest(dx=dx):
                self.assertEqual(measure.measure_trial(render(dx, 0, 0), CALIBRATION).zone, zone)

    def test_sticker_offset_is_taken_off_in_the_markers_turn(self):
        # Hoechstens 1 mm: der 18-mm-Aufkleber muss ganz auf der 20-mm-Oberseite liegen.
        sticker = (0.6, -0.4)
        image = render(1.0, -1.0, 25.0, sticker=sticker)
        self.assertMeasured(measure.measure_trial(image, CALIBRATION, sticker), 1.0, -1.0, 25.0)
        # Ohne Versatz liegt die gemessene Mitte genau um den gedrehten Versatz daneben.
        naive = measure.measure_trial(image, CALIBRATION)
        expected = np.array([1.0, -1.0]) + target.rotation_2d(25.0) @ np.array(sticker)
        np.testing.assert_allclose([naive.dx_mm, naive.dy_mm], expected, atol=POSITION_TOLERANCE_MM)

    def test_two_sheet_markers_are_enough(self):
        m = measure.measure_trial(render(1.0, 2.0, 5.0, with_sheet_markers=[0, 2]), CALIBRATION)
        self.assertEqual(m.sheet_markers, [0, 2])
        self.assertMeasured(m, 1.0, 2.0, 5.0)

    def test_fails_with_a_reason_instead_of_raising(self):
        cases = {
            "Wuerfelmarker": render(0, 0, 0, with_cube=False),
            "Blattmarker": render(0, 0, 0, with_sheet_markers=[1]),
            "Bildgroesse": np.zeros((240, 320), dtype=np.uint8),
            "Wuerfelmarker ": np.zeros((480, 640, 3), dtype=np.uint8),
        }
        for word, image in cases.items():
            with self.subTest(word=word):
                m = measure.measure_trial(image, CALIBRATION)
                self.assertFalse(m.ok)
                self.assertIn(word.strip(), m.grund)

    def test_survives_json(self):
        m = measure.measure_trial(render(0.4, 0.2, 3.0), CALIBRATION)
        data = json.loads(json.dumps(m.to_dict()))
        for key in ("ok", "grund", "dx_mm", "dy_mm", "dtheta_deg", "r_mm", "zone", "sheet_markers",
                    "reprojection_rms_px", "camera_height_mm", "camera_tilt_deg", "nadir_offset_mm"):
            self.assertIn(key, data)
        self.assertEqual(TrialMeasurement.from_dict(data).to_dict(), data)

    def test_annotate_draws_on_a_copy(self):
        image = render(0, 0, 0)
        before = image.copy()
        drawn = measure.annotate(image, measure.measure_trial(image, CALIBRATION))
        self.assertEqual(drawn.shape[:2], image.shape[:2])
        self.assertTrue(np.array_equal(image, before))


def trial(dx, dy, dtheta, ok=True):
    return TrialMeasurement.from_dict(
        {"ok": ok, "grund": None if ok else "kaputt", "dx_mm": dx, "dy_mm": dy, "dtheta_deg": dtheta,
         "r_mm": float(np.hypot(dx, dy)), "zone": target.zone_for_radius(float(np.hypot(abs(dx) + 10, abs(dy) + 10))),
         "sheet_markers": [0, 1, 2, 3]}
    )


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class SummarizeTest(unittest.TestCase):
    def test_accuracy_and_repeatability_after_iso_9283(self):
        trials = [trial(1.0, 0.0, 1.0), trial(3.0, 0.0, -1.0), trial(2.0, 1.0, 0.0), trial(2.0, -1.0, 0.0)]
        s = measure.summarize(trials)
        self.assertEqual((s["n"], s["n_gesamt"]), (4, 4))
        self.assertAlmostEqual(s["dx_mean_mm"], 2.0)
        self.assertAlmostEqual(s["dy_mean_mm"], 0.0)
        # Genauigkeit: Abstand des Mittels vom Soll.
        self.assertAlmostEqual(s["AP_mm"], 2.0)
        # Wiederholgenauigkeit: alle vier liegen 1 mm vom Mittel -> l = 1, S_l = 0.
        self.assertAlmostEqual(s["l_mean_mm"], 1.0)
        self.assertAlmostEqual(s["RP_mm"], 1.0)
        self.assertAlmostEqual(s["dtheta_mean_deg"], 0.0, places=9)
        self.assertAlmostEqual(s["AP_theta_deg"], 0.0, places=9)
        self.assertAlmostEqual(s["RP_theta_deg"], 3.0 * np.std([1, -1, 0, 0], ddof=1))
        self.assertAlmostEqual(s["dx_std_mm"], np.std([1, 3, 2, 2], ddof=1))

    def test_turns_around_plus_minus_45_average_there_not_to_zero(self):
        s = measure.summarize([trial(0, 0, 44.0), trial(0, 0, -44.0)])
        self.assertAlmostEqual(abs(s["dtheta_mean_deg"]), 45.0, places=6)
        self.assertAlmostEqual(s["RP_theta_deg"], 3.0 * np.std([-1.0, 1.0], ddof=1), places=6)

    def test_failed_trials_are_counted_but_not_averaged(self):
        s = measure.summarize([trial(1.0, 1.0, 0.0), trial(9.0, 9.0, 9.0, ok=False)])
        self.assertEqual((s["n"], s["n_gesamt"], s["n_fehlgeschlagen"]), (1, 2, 1))
        self.assertAlmostEqual(s["AP_mm"], np.hypot(1, 1))
        self.assertEqual(sum(s["zonen"].values()), 1)

    def test_nothing_measured_gives_counts_only(self):
        s = measure.summarize([trial(0, 0, 0, ok=False)])
        self.assertEqual(s["n"], 0)
        self.assertNotIn("AP_mm", s)


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class StickerOffsetTest(unittest.TestCase):
    def test_turning_the_cube_half_round_reveals_the_sticker_offset(self):
        sticker = (0.6, -0.4)
        first = measure.measure_trial(render(0.7, -0.4, 10.0, sticker=sticker), CALIBRATION)
        second = measure.measure_trial(render(0.7, -0.4, 190.0, sticker=sticker), CALIBRATION)
        np.testing.assert_allclose(measure.sticker_offset_from_pair(first, second), sticker, atol=0.1)

    def test_refuses_a_quarter_turn_and_failed_measurements(self):
        first = measure.measure_trial(render(0, 0, 0), CALIBRATION)
        quarter = measure.measure_trial(render(0, 0, 90), CALIBRATION)
        with self.assertRaises(ValueError):
            measure.sticker_offset_from_pair(first, quarter)
        with self.assertRaises(ValueError):
            measure.sticker_offset_from_pair(first, trial(0, 0, 0, ok=False))


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class LiveTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)

    def test_sticker_offset_file_round_trips_and_defaults_to_zero(self):
        self.assertEqual(live.load_sticker_offset(self.directory), (0.0, 0.0))
        live.write_sticker_offset(self.directory, (1.25, -0.5))
        self.assertEqual(live.load_sticker_offset(self.directory), (1.25, -0.5))
        (self.directory / live.STICKER_OFFSET_FILE).write_text("{kaputt")
        self.assertEqual(live.load_sticker_offset(self.directory), (0.0, 0.0))

    def test_intrinsics_round_trip_through_the_trial_json(self):
        meta = json.loads(json.dumps(live.intrinsics_meta(CALIBRATION, "realsense_factory")))
        self.assertEqual(meta["source"], "realsense_factory")
        self.assertEqual(meta["imageSize"], [640, 480])
        back = live.calibration_from_meta(meta)
        np.testing.assert_allclose(back.camera_matrix, CALIBRATION.camera_matrix)
        np.testing.assert_allclose(back.distortion, CALIBRATION.distortion)
        self.assertEqual(back.image_size, CALIBRATION.image_size)
        self.assertEqual(back.model, CALIBRATION.model)

    def test_measure_for_save_never_raises(self):
        result, annotated = live.measure_for_save(render(0, 0, 0), None)
        self.assertEqual((result["ok"], annotated), (False, None))
        self.assertIn("kalibrierung", result["grund"].lower())
        result, annotated = live.measure_for_save(render(0.5, 0, 0), CALIBRATION)
        self.assertTrue(result["ok"], result.get("grund"))
        self.assertIsNotNone(annotated)
        result, _ = live.measure_for_save(np.zeros((480, 640, 3), dtype=np.uint8), CALIBRATION)
        self.assertFalse(result["ok"])


def load_evaluation_tool():
    """`tools/use_case_auswertung.py` als Modul (tools/ ist kein Paket)."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "use_case_auswertung.py"
    spec = importlib.util.spec_from_file_location("use_case_auswertung", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(np is not None, "numpy/OpenCV fehlen")
class EvaluationToolTest(unittest.TestCase):
    """Die Reihenauswertung ueber den echten Speicherweg (`save_use_case_image`)."""

    def setUp(self):
        import contextlib
        import io

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "use_case"
        self.series = self.base / "reihe_001"
        self.tool = load_evaluation_tool()
        self.quiet = lambda: contextlib.redirect_stdout(io.StringIO())

    def save(self, dx, dy, dtheta, sticker=(0.0, 0.0), measure_live=True):
        from vision_server.use_case_image import save_use_case_image

        image = render(dx, dy, dtheta, sticker=sticker)
        pi_meta = {"intrinsics": live.intrinsics_meta(CALIBRATION, "realsense_factory")}
        measurement = live.measure_for_save(image, CALIBRATION)[0] if measure_live else None
        return save_use_case_image(image, self.base, 1, "{}", pi_meta, measurement=measurement)

    def run_tool(self, *args):
        with self.quiet():
            return self.tool.main([str(self.series), *args])

    def test_writes_csv_and_figures_for_the_series(self):
        import csv

        placed = [(1.0, 0.0, 2.0), (1.4, 0.4, 1.0), (0.6, -0.4, 0.0)]
        for dx, dy, dtheta in placed:
            self.save(dx, dy, dtheta)
        self.assertEqual(self.run_tool(), 0)
        with open(self.series / "auswertung.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([row["versuch"] for row in rows], ["1", "2", "3"])
        self.assertAlmostEqual(float(rows[1]["dx_mm"]), 1.4, delta=POSITION_TOLERANCE_MM)
        summary = json.loads((self.series / "auswertung.json").read_text(encoding="utf-8"))["kennzahlen"]
        self.assertEqual(summary["n"], 3)
        self.assertAlmostEqual(summary["AP_mm"], 1.0, delta=POSITION_TOLERANCE_MM)
        self.assertAlmostEqual(summary["dtheta_mean_deg"], 1.0, delta=ANGLE_TOLERANCE_DEG)

    def test_measures_trials_saved_without_a_measurement_and_stores_it(self):
        png, _ = self.save(0.8, 0.0, 0.0, measure_live=False)
        self.assertEqual(self.run_tool("--bilder"), 0)
        stored = json.loads(png.with_suffix(".json").read_text(encoding="utf-8"))["messung"]
        self.assertTrue(stored["ok"], stored.get("grund"))
        self.assertAlmostEqual(stored["dx_mm"], 0.8, delta=POSITION_TOLERANCE_MM)
        self.assertTrue((self.series / "versuch_001_messung.png").is_file())

    def test_sticker_calibration_from_a_half_turn_corrects_the_series(self):
        sticker = (0.6, -0.4)
        self.save(0.0, 0.0, 5.0, sticker=sticker)
        self.save(0.0, 0.0, 185.0, sticker=sticker)
        self.save(1.0, 0.5, 0.0, sticker=sticker)
        self.assertEqual(self.run_tool("--aufkleber-kalibrieren", "1", "2"), 0)
        np.testing.assert_allclose(live.load_sticker_offset(self.base), sticker, atol=POSITION_TOLERANCE_MM)
        # Neu gemessen mit dem Versatz liegt Versuch 3 richtig.
        self.assertEqual(self.run_tool("--neu-messen"), 0)
        third = json.loads((self.series / "versuch_003.json").read_text(encoding="utf-8"))["messung"]
        self.assertAlmostEqual(third["dx_mm"], 1.0, delta=POSITION_TOLERANCE_MM)
        self.assertAlmostEqual(third["dy_mm"], 0.5, delta=POSITION_TOLERANCE_MM)

    def test_an_empty_folder_is_an_error(self):
        self.series.mkdir(parents=True)
        with contextlib_redirect_stderr():
            self.assertEqual(self.run_tool(), 1)


def contextlib_redirect_stderr():
    import contextlib
    import io

    return contextlib.redirect_stderr(io.StringIO())


if __name__ == "__main__":
    unittest.main()
