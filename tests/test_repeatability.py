"""Tests fuer die Roboter-Wiederholgenauigkeit: Ablage (`SaveRepeatabilityImage`) und
die kalibrierfreie Auswertung (`repeatability_eval`) an synthetischen ChArUco-Bildern."""

import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

try:
    import cv2
    import numpy as np

    from repeatability_eval.charuco import CharucoSpec, build_board, detect_corners, spec_from_dict
    from repeatability_eval.live import measure_for_save
    from repeatability_eval.metrics import (
        corner_noise,
        homography_metrics,
        mean_corner_displacement,
        noisy_ids,
        repeatability,
    )
    from repeatability_eval.session import evaluate_session
    from vision_server.repeatability_image import PHASES, SCHEMA, save_repeatability_image
except Exception:  # pragma: no cover - numpy/OpenCV fehlen
    np = None

#: 8 x 6 Felder zu 40 mm, Marker 30 mm: 35 innere Ecken, 24 Marker.
SPEC_DICT = {
    "type": "charuco",
    "cols": 8,
    "rows": 6,
    "squareSizeM": 0.04,
    "markerSizeM": 0.03,
    "dictionary": "DICT_4X4_50",
    "legacyPattern": False,
}
SIZE = (800, 600)
#: `generateImage` legt das 320 x 240 mm grosse Brett mit 60 px Rand ins Bild: 2 px/mm.
PX_PER_MM = 2.0


def board_image(spec):
    """Das Brett, leicht weichgezeichnet wie durch ein echtes Objektiv. Ein hart binaeres Bild
    laesst sich nicht sauber um Bruchteile eines Pixels verschieben (Pixel-Locking der Ecken)."""
    return cv2.GaussianBlur(build_board(spec).generateImage(SIZE, marginSize=60), (0, 0), 1.0)


def shifted(base, dx, dy, rng=None, noise=0.0, cover=None):
    """`base` um (dx, dy) px verschoben, optional verrauscht und mit einem weissen Rechteck abgedeckt."""
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    image = cv2.warpAffine(base, matrix, SIZE, flags=cv2.INTER_CUBIC, borderValue=255)
    if noise and rng is not None:
        image = np.clip(image.astype(float) + rng.normal(0, noise, image.shape), 0, 255).astype(np.uint8)
    if cover is not None:
        x0, y0, x1, y1 = cover
        image[y0:y1, x0:x1] = 255
    return image


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class CharucoSpecTest(unittest.TestCase):
    def test_round_trip_of_the_client_format(self):
        spec = spec_from_dict(SPEC_DICT)
        self.assertEqual(spec, CharucoSpec(8, 6, 0.04, 0.03, "DICT_4X4_50", False))
        self.assertEqual(spec.as_dict(), SPEC_DICT)

    def test_rejects_nonsense(self):
        for bad in (
            None,
            {**SPEC_DICT, "type": "chessboard"},
            {**SPEC_DICT, "markerSizeM": 0.05},
            {**SPEC_DICT, "cols": 1},
            {k: v for k, v in SPEC_DICT.items() if k != "rows"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                spec_from_dict(bad)


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class HomographyMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = spec_from_dict(SPEC_DICT)
        cls.base = board_image(cls.spec)

    def measure(self, image):
        ids, corners = detect_corners(image, self.spec)
        return homography_metrics(ids, corners, self.spec, SIZE)

    def test_unshifted_board_gives_its_centre_scale_and_no_rotation(self):
        m = self.measure(self.base)
        self.assertEqual(m["nCorners"], 35)
        self.assertAlmostEqual(m["scalePxPerMm"], PX_PER_MM, delta=0.002)
        self.assertAlmostEqual(m["rotationDeg"], 0.0, delta=0.01)
        self.assertLess(m["homographyRmsPx"], 0.05)
        # Bildmitte (399,5 | 299,5 px) liegt etwa auf der Brettmitte (160 | 120 mm).
        self.assertAlmostEqual(m["centerOnBoardMm"][0], 160.0, delta=0.5)
        self.assertAlmostEqual(m["centerOnBoardMm"][1], 120.0, delta=0.5)

    def test_subpixel_shift_moves_the_centre_by_shift_over_scale(self):
        ref = np.array(self.measure(shifted(self.base, 0, 0))["centerOnBoardMm"])
        for dx, dy in ((0.5, 0.0), (0.0, -0.3), (2.0, 1.0)):
            with self.subTest(dx=dx, dy=dy):
                c = np.array(self.measure(shifted(self.base, dx, dy))["centerOnBoardMm"])
                # Rueckt das Brett im Bild nach +x, sieht die Bildmitte eine um dx/Massstab kleinere Brettstelle.
                expected = -np.array([dx, dy]) / PX_PER_MM
                np.testing.assert_allclose(c - ref, expected, atol=0.01)

    def test_rotation_about_the_optical_axis_is_measured(self):
        matrix = cv2.getRotationMatrix2D((SIZE[0] / 2, SIZE[1] / 2), 1.0, 1.0)
        rotated = cv2.warpAffine(self.base, matrix, SIZE, flags=cv2.INTER_CUBIC, borderValue=255)
        self.assertAlmostEqual(abs(self.measure(rotated)["rotationDeg"]), 1.0, delta=0.02)

    def test_too_few_corners_raise(self):
        ids, corners = detect_corners(self.base, self.spec)
        with self.assertRaises(ValueError):
            homography_metrics(ids[:5], corners[:5], self.spec, SIZE)


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class StatisticsTest(unittest.TestCase):
    def test_repeatability_is_l_mean_plus_three_s_l(self):
        points = np.array([[1.0, 0.0], [-1.0, 0.0], [0.0, 3.0], [0.0, -3.0]])
        rep = repeatability(points)
        np.testing.assert_allclose(rep["centroid"], [0.0, 0.0])
        distances = np.array([1.0, 1.0, 3.0, 3.0])
        self.assertAlmostEqual(rep["lMean"], 2.0)
        self.assertAlmostEqual(rep["sL"], distances.std(ddof=1))
        self.assertAlmostEqual(rep["rp"], 2.0 + 3.0 * distances.std(ddof=1))
        self.assertEqual(rep["n"], 4)

    def test_repeatability_needs_two_points(self):
        with self.assertRaises(ValueError):
            repeatability(np.array([[1.0, 2.0]]))

    def test_corner_noise_and_noisy_ids(self):
        rng = np.random.default_rng(3)
        samples = []
        for _ in range(20):
            ids = np.array([1, 2, 3, 4, 5])
            corners = np.tile([[10.0, 20.0]], (5, 1)) + rng.normal(0, 0.01, (5, 2))
            corners[4] += rng.normal(0, 0.2, 2)  # Ecke 5: beschaedigter Tag
            samples.append((ids, corners))
        samples.append((np.array([1, 9]), np.array([[10.0, 20.0], [0.0, 0.0]])))  # 9 nur einmal
        noise = corner_noise(samples)
        self.assertNotIn(9, noise)
        self.assertGreater(noise[5], 5 * noise[1])
        self.assertEqual(noisy_ids(noise), {5})

    def test_mean_corner_displacement_uses_common_ids(self):
        ref = {1: (0.0, 0.0), 2: (10.0, 0.0)}
        self.assertAlmostEqual(
            mean_corner_displacement(np.array([1, 2, 7]), np.array([[3.0, 4.0], [10.0, 1.0], [99.0, 99.0]]), ref), 3.0
        )
        self.assertTrue(np.isnan(mean_corner_displacement(np.array([7]), np.array([[0.0, 0.0]]), ref)))


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class LiveMeasurementTest(unittest.TestCase):
    def test_measures_with_the_board_from_the_client_record(self):
        image = board_image(spec_from_dict(SPEC_DICT))
        result = measure_for_save(image, json.dumps({"board": SPEC_DICT}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["nCorners"], 35)
        self.assertEqual(result["nMarkers"], 24)
        json.dumps(result)  # JSON-tauglich fuer ResultJson

    def test_never_raises(self):
        image = board_image(spec_from_dict(SPEC_DICT))
        self.assertEqual(measure_for_save(image, "{}"), {"ok": False, "grund": "kein Board in den Metadaten"})
        self.assertFalse(measure_for_save(image, "{kaputt")["ok"])
        self.assertFalse(measure_for_save(np.full((60, 80), 255, np.uint8), {"board": SPEC_DICT})["ok"])
        self.assertFalse(measure_for_save(image, {"board": {**SPEC_DICT, "cols": "x"}})["ok"])


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class SaveRepeatabilityImageTest(unittest.TestCase):
    NOW = datetime(2026, 11, 9, 11, 0, 0)

    def test_files_per_phase_numbered_and_never_overwritten(self):
        image = np.zeros((30, 40), np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            first, n1 = save_repeatability_image(image, base, "s1", "rauschen", '{"cycle": 1}', {"host": "pi"}, {"ok": True}, self.NOW)
            second, n2 = save_repeatability_image(image, base, "s1", "rauschen", "kein json", {}, None, self.NOW)
            hand, n3 = save_repeatability_image(image, base, "s1", "zyklen_hand", "{}", {}, None, self.NOW)
            self.assertEqual((n1, n2, n3), (1, 2, 1))
            self.assertEqual(first, base / "s1" / "02_rauschen" / "r_001.png")
            self.assertEqual(second.name, "r_002.png")
            self.assertEqual(hand, base / "s1" / "04_zyklen_hand" / "h_001.png")
            # Graustufe bleibt Graustufe, verlustfrei.
            np.testing.assert_array_equal(cv2.imread(str(first), cv2.IMREAD_UNCHANGED), image)
            doc = json.loads(first.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(doc["schema"], SCHEMA)
            self.assertEqual((doc["session"], doc["phase"], doc["index"], doc["bild"]), ("s1", "rauschen", 1, "r_001.png"))
            self.assertEqual(doc["gespeichert"], "2026-11-09T11:00:00")
            self.assertEqual(doc["client"], {"cycle": 1})
            self.assertEqual(doc["pi"], {"host": "pi"})
            self.assertEqual(doc["messung"], {"ok": True})
            self.assertEqual(json.loads(second.with_suffix(".json").read_text())["client_raw"], "kein json")
            # Eine Luecke wird nicht aufgefuellt, eine vorhandene Datei nie ueberschrieben.
            (base / "s1" / "02_rauschen" / "r_005.json").write_text("{}")
            _, n4 = save_repeatability_image(image, base, "s1", "rauschen", "{}", {}, None, self.NOW)
            self.assertEqual(n4, 6)

    def test_rejects_bad_session_and_phase(self):
        image = np.zeros((10, 10), np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            for session, phase in (("../x", "rauschen"), ("", "rauschen"), ("s1", "zyklen"), ("a" * 65, "warmlauf")):
                with self.subTest(session=session, phase=phase), self.assertRaises(ValueError):
                    save_repeatability_image(image, Path(tmp), session, phase, "{}", {}, None)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_phase_folders_match_the_evaluation(self):
        from repeatability_eval.session import PHASE_FOLDERS

        self.assertEqual({phase: folder for phase, (folder, _) in PHASES.items()}, PHASE_FOLDERS)


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class EvaluateSessionTest(unittest.TestCase):
    """Eine ganze Session: Warmlauf, Rauschserie, zwei Auto-Laeufe mit gleichen Zyklusnummern, Hand-Zyklen."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base_dir = Path(cls.tmp.name)
        spec = spec_from_dict(SPEC_DICT)
        base = board_image(spec)
        rng = np.random.default_rng(7)
        # Marker im Feld oben links (2. Feld der 1. Zeile): bei Rauschbild 3 abgedeckt (beschaedigter Tag).
        square_px = 0.04 * 1000 * PX_PER_MM
        x0, y0 = int(80 + square_px), int(60)
        damaged = (x0 + 5, y0 + 5, int(x0 + square_px - 5), int(y0 + square_px - 5))

        def save(phase, image, **client):
            save_repeatability_image(image, base_dir, "s1", phase, json.dumps({"board": SPEC_DICT, **client}), {}, None)

        for k in range(4):
            save("warmlauf", shifted(base, 0.4 - 0.1 * k, 0, rng, 0.5), cycle=k + 1, burstIndex=1, burstCount=1, warmupElapsedS=60.0 * k)
        for k in range(12):
            save("rauschen", shifted(base, 0, 0, rng, 0.5, damaged if k == 3 else None), cycle=k + 1, burstIndex=1, burstCount=1)
        save("rauschen", np.full((SIZE[1], SIZE[0]), 255, np.uint8), cycle=13, burstIndex=1, burstCount=1)  # Brett weg

        cls.auto_offsets = []
        for _run in range(2):
            for cycle in (1, 2, 3):
                dx, dy = rng.normal(0, 1.0, 2)
                cls.auto_offsets.append((dx, dy))
                for burst in (1, 2):
                    save(
                        "zyklen_auto",
                        shifted(base, dx, dy, rng, 0.5),
                        cycle=cycle,
                        burstIndex=burst,
                        burstCount=2,
                        actual={"flangeInBase": {"position": [0.4 + dx * 1e-4, 0.1, 0.3]}},
                    )
        for cycle in (1, 2, 3, 4):
            dx, dy = rng.normal(0, 1.0, 2)
            save("zyklen_hand", shifted(base, dx, dy, rng, 0.5), cycle=cycle, burstIndex=1, burstCount=1)
        cls.folder = base_dir / "s1"
        cls.result = evaluate_session(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_base_ids_drop_the_corners_of_the_damaged_tag_and_ignore_the_empty_frame(self):
        base_ids = set(self.result["basisIds"])
        self.assertGreaterEqual(len(base_ids), 30)
        self.assertLess(len(base_ids), 35)
        self.assertEqual(self.result["basisRegel"], "alle Rauschbilder minus streuende Ecken")
        excluded = [image["datei"] for image in self.result["ausgeschlosseneBilder"]]
        self.assertEqual(excluded, ["02_rauschen/r_013.json"])
        self.assertEqual(self.result["nVerwendet"], self.result["nBilder"] - 1)

    def test_runs_with_repeated_cycle_numbers_are_kept_apart(self):
        auto = self.result["serien"]["zyklen_auto"]
        self.assertEqual(auto["nAufnahmen"], 6)
        self.assertEqual(self.result["serien"]["zyklen_hand"]["nAufnahmen"], 4)
        self.assertEqual(self.result["serien"]["rauschen"]["nAufnahmen"], 12)

    def test_cycle_rp_matches_the_shifts_and_the_noise_is_far_below(self):
        auto = self.result["serien"]["zyklen_auto"]
        expected = repeatability(-np.array(self.auto_offsets) / PX_PER_MM)["rp"] * 1000.0
        self.assertAlmostEqual(auto["rpUm"], expected, delta=0.05 * expected)
        noise_rp = self.result["serien"]["rauschen"]["rpUm"]
        self.assertLess(noise_rp, 20.0)
        self.assertLess(auto["noiseRatio"], 0.2)
        self.assertTrue(auto["valid"])
        self.assertIn("robotReported", auto)
        self.assertEqual(auto["robotReported"]["n"], 6)

    def test_warmup_drift_is_listed_with_its_time(self):
        warm = self.result["warmlauf"]
        self.assertEqual([w["tS"] for w in warm], [0.0, 60.0, 120.0, 180.0])
        # 0,4 px -> 0,1 px Versatz: 0,15 mm Drift bis zur letzten Aufnahme.
        self.assertAlmostEqual(warm[0]["dToLastUm"], 150.0, delta=10.0)
        self.assertAlmostEqual(warm[-1]["dToLastUm"], 0.0)

    def test_writes_result_json_and_csv(self):
        out = self.folder / "auswertung"
        stored = json.loads((out / "ergebnis.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["serien"]["zyklen_auto"]["nAufnahmen"], 6)
        with open(out / "messwerte.csv", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), self.result["nBilder"])
        auto_rows = [row for row in rows if row["phase"] == "zyklen_auto"]
        self.assertEqual([row["burst"] for row in auto_rows[:2]], ["1", "2"])

    def test_exclude_ids_are_taken_out_of_the_base(self):
        result = evaluate_session(self.folder, exclude_ids={0, 1}, write=False)
        self.assertEqual(result["ausgeschlosseneIds"], [0, 1])
        self.assertFalse({0, 1} & set(result["basisIds"]))

    def test_folder_without_images_raises(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            evaluate_session(Path(tmp), write=False)


if __name__ == "__main__":
    unittest.main()
