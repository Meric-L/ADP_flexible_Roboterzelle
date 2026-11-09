"""Tests fuer die Wiederholgenauigkeit mit einem AprilTag als Ziel (`repeatability_eval.apriltag`,
Live-Messung und Session-Auswertung) an synthetischen Tag-Bildern."""

import json
import tempfile
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np

    from repeatability_eval.apriltag import (
        apriltag_spec_from_dict,
        build_apriltag_detector,
        detect_observations,
        select_observation,
        target_type,
    )
    from repeatability_eval.live import AprilTagLive, measure_for_save
    from repeatability_eval.session import evaluate_session
    from tagloc.calibration import CameraCalibration
    from vision_server.repeatability_image import save_repeatability_image
except Exception:  # pragma: no cover - numpy/OpenCV fehlen
    np = None

SIZE = (640, 480)
FX = 800.0
#: Schwarze Aussenkante des Tags im Bild (px) und in Wirklichkeit (m): Abstand FX * 0,08 / 160 = 0,4 m.
TAG_PX = 160
TAG_M = 0.08
DISTANCE_M = FX * TAG_M / TAG_PX
#: Wie die AprilTag-Quelle des Hand-Pi (`pi.apriltag` in der Bild-JSON).
PI_APRILTAG = {
    "tag_family": "tag36h11",
    "detector_backend": "aruco",
    "subpixel_corner_refinement": True,
    "corner_refinement_method": "subpix",
    "apriltag_quad_decimate": 0.0,
    "detector_parameters": {},
}
IDENTITY = {"position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0, 1.0]}


def calibration():
    return CameraCalibration(
        camera_matrix=np.array([[FX, 0, (SIZE[0] - 1) / 2], [0, FX, (SIZE[1] - 1) / 2], [0, 0, 1]]),
        distortion=np.zeros(5),
        image_size=SIZE,
        calibration_id="synthetisch",
    )


def intrinsics_meta():
    calib = calibration()
    return {
        "source": "calibration",
        "calibrationId": "synthetisch",
        "imageSize": list(SIZE),
        "cameraMatrix": calib.camera_matrix.tolist(),
        "distortionCoefficients": [0.0] * 5,
    }


#: Der Tag liegt etwas neben dem Bildhauptpunkt. Genau mittig und exakt frontal ist das
#: synthetische Quadrat fuer IPPE entartet (beide Loesungen gleich, Pose kippt weg); echte
#: Bilder treffen diesen Fall nie, schon 1e-4 px Rauschen genuegt.
OFFSET_PX = (3.3, 2.1)


def tag_image(tag_id=7, dx=0.0, dy=0.0, extra_tag=None):
    """Weisses Bild mit dem Tag nahe der Mitte (Kante TAG_PX), um (dx, dy) px verschoben, leicht weich."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    image = np.full((SIZE[1], SIZE[0]), 255, np.uint8)
    x0, y0 = (SIZE[0] - TAG_PX) // 2, (SIZE[1] - TAG_PX) // 2
    image[y0:y0 + TAG_PX, x0:x0 + TAG_PX] = cv2.aruco.generateImageMarker(dictionary, tag_id, TAG_PX)
    if extra_tag is not None:
        image[20:100, 20:100] = cv2.aruco.generateImageMarker(dictionary, extra_tag, 80)
    image = cv2.GaussianBlur(image, (0, 0), 1.0)
    matrix = np.float32([[1, 0, dx + OFFSET_PX[0]], [0, 1, dy + OFFSET_PX[1]]])
    return cv2.warpAffine(image, matrix, SIZE, flags=cv2.INTER_CUBIC, borderValue=255)


def client(tag_id=7, flange=IDENTITY, **extra):
    return {"target": {"type": "apriltag", "tagId": tag_id, "tagSizeM": TAG_M}, "board": None,
            "actual": {"flangeInBase": flange}, **extra}


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class TargetTypeTest(unittest.TestCase):
    def test_only_a_target_with_type_apriltag_selects_the_tag(self):
        self.assertEqual(target_type(client()), "apriltag")
        # Aeltere Records: `target` waren die Soll-Gelenkwinkel, ohne `type`.
        self.assertEqual(target_type({"target": {"joints": [0.1, 0.2]}, "board": {}}), "charuco")
        self.assertEqual(target_type({"board": {}}), "charuco")
        self.assertEqual(target_type({"target": {"type": "charuco"}}), "charuco")
        self.assertEqual(target_type("kein dict"), "charuco")

    def test_spec_from_the_client_format(self):
        spec = apriltag_spec_from_dict({"type": "apriltag", "tagId": None, "tagSizeM": 0.08})
        self.assertIsNone(spec.tag_id)
        self.assertEqual(spec.as_dict(), {"type": "apriltag", "tagId": None, "tagSizeM": 0.08})
        for bad in ({"type": "apriltag", "tagId": 1}, {"type": "apriltag", "tagId": -1, "tagSizeM": 0.08},
                    {"type": "apriltag", "tagId": 1, "tagSizeM": 0}, {"type": "charuco"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                apriltag_spec_from_dict(bad)

    def test_old_record_without_board_is_not_measured_as_a_tag(self):
        result = measure_for_save(tag_image(), {"target": {"joints": [1.0]}})
        self.assertEqual(result, {"ok": False, "grund": "kein Board in den Metadaten"})


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class SelectObservationTest(unittest.TestCase):
    def test_picks_the_wanted_tag_and_refuses_ambiguity(self):
        detector = build_apriltag_detector(PI_APRILTAG)
        observations = detect_observations(tag_image(7, extra_tag=3), detector)
        self.assertEqual(sorted(o.tag_id for o in observations), [3, 7])
        self.assertEqual(select_observation(observations, 7)[0].tag_id, 7)
        self.assertIsNone(select_observation(observations, None)[0])
        self.assertIn("mehrere Tags", select_observation(observations, None)[1])
        self.assertEqual(select_observation(observations, 9)[1], "Tag 9 nicht erkannt")
        self.assertEqual(select_observation([], None)[1], "kein Tag erkannt")


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class LiveAprilTagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = AprilTagLive(
            build_apriltag_detector(PI_APRILTAG), calibration=calibration, pose_flange_cam=lambda: np.eye(4)
        )

    def test_measures_the_tag_in_camera_and_base(self):
        result = measure_for_save(tag_image(), json.dumps(client()), self.live)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["target"], result["tagId"], result["nCorners"], result["nMarkers"]), ("apriltag", 7, 4, 1))
        self.assertEqual(len(result["cornersPx"]), 4)
        expected_xy = np.array(OFFSET_PX) * DISTANCE_M / FX
        np.testing.assert_allclose(result["tagInCamM"][:2], expected_xy, atol=0.2e-3)
        self.assertAlmostEqual(result["tagInCamM"][2], DISTANCE_M, delta=0.004)
        self.assertLess(result["reprojectionErrorPx"], 0.2)
        # Flansch und Hand-Auge identisch: Basis = Kamera.
        np.testing.assert_allclose(result["tagInBaseM"], result["tagInCamM"], atol=1e-9)
        self.assertAlmostEqual(result["scalePxPerMm"], TAG_PX / (TAG_M * 1000), delta=0.02)
        json.dumps(result)

    def test_base_follows_the_flange_pose(self):
        moved = {"position": [0.1, -0.2, 0.3], "orientation": [0.0, 0.0, 0.0, 1.0]}
        cam = measure_for_save(tag_image(), client(), self.live)
        base = measure_for_save(tag_image(), client(flange=moved), self.live)
        np.testing.assert_allclose(np.subtract(base["tagInBaseM"], cam["tagInCamM"]), [0.1, -0.2, 0.3], atol=1e-9)

    def test_subpixel_shift_moves_the_tag_by_shift_times_distance_over_focal_length(self):
        ref = np.array(measure_for_save(tag_image(), client(), self.live)["tagInCamM"])
        shifted = np.array(measure_for_save(tag_image(dx=0.5), client(), self.live)["tagInCamM"])
        self.assertAlmostEqual(abs(shifted[0] - ref[0]), 0.5 * DISTANCE_M / FX, delta=0.05e-3)
        self.assertLess(abs(shifted[1] - ref[1]), 0.05e-3)

    def test_without_calibration_or_hand_eye_only_the_parts_that_need_them_are_missing(self):
        bare = AprilTagLive(self.live.detector)
        result = measure_for_save(tag_image(), client(), bare)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["tagInCamM"])
        self.assertIsNone(result["tagInBaseM"])
        no_hand_eye = AprilTagLive(self.live.detector, calibration=calibration)
        result = measure_for_save(tag_image(), client(), no_hand_eye)
        self.assertIsNotNone(result["tagInCamM"])
        self.assertIsNone(result["tagInBaseM"])

    def test_never_raises(self):
        self.assertFalse(measure_for_save(tag_image(), client(tag_id=9), self.live)["ok"])
        self.assertFalse(measure_for_save(tag_image(), client(), None)["ok"])
        self.assertFalse(measure_for_save(np.full((SIZE[1], SIZE[0]), 255, np.uint8), client(), self.live)["ok"])


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class EvaluateAprilTagSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base_dir = Path(cls.tmp.name)
        rng = np.random.default_rng(11)
        pi = {"apriltag": PI_APRILTAG, "intrinsics": intrinsics_meta(), "hand_eye": {"content": {"poseFlangeCam": IDENTITY}}}

        def save(phase, image, **extra):
            save_repeatability_image(image, base_dir, "s1_apriltag", phase, json.dumps(client(tag_id=None, **extra)), pi, None)

        for k in range(10):
            save("rauschen", tag_image(dx=rng.normal(0, 0.02), dy=rng.normal(0, 0.02)), cycle=k + 1, burstIndex=1, burstCount=1)
        save("rauschen", np.full((SIZE[1], SIZE[0]), 255, np.uint8), cycle=11, burstIndex=1, burstCount=1)
        cls.shifts = []
        for cycle in range(1, 9):
            dx, dy = rng.normal(0, 1.0, 2)
            cls.shifts.append((dx, dy))
            for burst in (1, 2):
                save("zyklen_auto", tag_image(dx=dx, dy=dy), cycle=cycle, burstIndex=burst, burstCount=2)
        cls.folder = base_dir / "s1_apriltag"
        cls.result = evaluate_session(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_target_and_exclusions(self):
        self.assertIsNone(self.result["board"])
        self.assertEqual(self.result["target"]["type"], "apriltag")
        self.assertEqual(self.result["target"]["tagId"], 7)  # aufgeloest: der einzige Tag
        self.assertEqual(self.result["basisIds"], [])
        self.assertEqual([b["datei"] for b in self.result["ausgeschlosseneBilder"]], ["02_rauschen/r_011.json"])

    def test_three_repeatabilities_per_series(self):
        auto = self.result["serien"]["zyklen_auto"]
        self.assertEqual(auto["nAufnahmen"], 8)
        # Kamera: Bildversatz dx px entspricht dx * Abstand / Brennweite.
        shifts_m = np.array(self.shifts) * DISTANCE_M / FX
        centroid = shifts_m.mean(axis=0)
        dist = np.linalg.norm(shifts_m - centroid, axis=1)
        expected_um = (dist.mean() + 3 * dist.std(ddof=1)) * 1e6
        self.assertAlmostEqual(auto["kamera"]["rpUm"], expected_um, delta=0.1 * expected_um)
        # Flansch fest, Hand-Auge identisch: Basis = Kamera.
        self.assertAlmostEqual(auto["basis"]["rpUm"], auto["kamera"]["rpUm"], delta=1e-6)
        self.assertIsNotNone(auto["rpUm"])  # kalibrierfrei
        noise = self.result["serien"]["rauschen"]
        self.assertLess(noise["kamera"]["rpUm"], auto["kamera"]["rpUm"])
        self.assertTrue(auto["kamera"]["valid"])

    def test_csv_has_the_tag_columns(self):
        header = (self.folder / "auswertung" / "messwerte.csv").read_text(encoding="utf-8").splitlines()[0]
        for column in ("tagInCamX_mm", "tagInBaseZ_mm", "reprojectionErrorPx"):
            self.assertIn(column, header)


if __name__ == "__main__":
    unittest.main()
