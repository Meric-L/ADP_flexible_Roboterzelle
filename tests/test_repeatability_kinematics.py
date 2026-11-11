"""Tests fuer den Kinematik-Test (`repeatability_eval.kinematics`, CLI `tools/wiederholgenauigkeit/kinematik.py`):
Rechenlogik an synthetischen Matrizen, Ende-zu-Ende an synthetischen Tag-Bildern."""

import csv
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np

    from repeatability_eval.kinematics import (
        ImageMeasurement,
        measure_session,
        pearson,
        pose_number,
        pose_table,
        render_report,
        rotation_angle_deg,
        summarize_poses,
        yaw_deg,
    )
    from tagloc.calibration import CameraCalibration
    from vision_server.repeatability_image import save_repeatability_image
except Exception:  # pragma: no cover - numpy/OpenCV fehlen
    np = None

_TOOLS = Path(__file__).resolve().parent.parent / "tools" / "wiederholgenauigkeit"


def rot_z(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def transform(position_m=(0.0, 0.0, 0.0), rotation=None):
    t = np.eye(4)
    t[:3, :3] = np.eye(3) if rotation is None else rotation
    t[:3, 3] = position_m
    return t


def image(pose, position_mm, yaw=0.0, joints_deg=None, **extra):
    """Ein Bild mit Tag in Basis bei `position_mm`, um `yaw` Grad um z gedreht."""
    joints = np.radians(joints_deg if joints_deg is not None else [0, -14, 0, -137, 0, 123, 44])
    cam = transform((0.0, 0.0, 0.35))
    return ImageMeasurement(name=f"p{pose}", pose=pose, t_base_tag=transform(np.array(position_mm) / 1000.0, rot_z(yaw)),
                            t_cam_tag=cam, joints=np.asarray(joints, dtype=float), **extra)


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class MathTest(unittest.TestCase):
    def test_rotation_angle_and_yaw(self):
        self.assertAlmostEqual(rotation_angle_deg(np.eye(3), rot_z(1.5)), 1.5, places=9)
        self.assertAlmostEqual(yaw_deg(rot_z(-2.0)), -2.0, places=9)

    def test_pearson(self):
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0, places=12)
        self.assertAlmostEqual(pearson([1, 2, 3, 4], [8, 6, 4, 2]), -1.0, places=12)
        self.assertTrue(math.isnan(pearson([1, 2], [1, 2])))
        self.assertTrue(math.isnan(pearson([1, 1, 1], [1, 2, 3])))

    def test_pose_number_from_phase_and_client(self):
        self.assertEqual(pose_number("rauschen", {"cycle": 7}), 0)
        self.assertEqual(pose_number("zyklen_hand", {"poseIndex": 3, "cycle": 9}), 3)
        self.assertEqual(pose_number("zyklen_hand", {"cycle": 2}), 2)
        self.assertIsNone(pose_number("zyklen_hand", {}))
        self.assertIsNone(pose_number("zyklen_auto", {"cycle": 1}))
        self.assertIsNone(pose_number("warmlauf", {"cycle": 1}))


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class SummarizePosesTest(unittest.TestCase):
    def setUp(self):
        q_ref = [0, -14, 0, -137, 0, 123, 44]
        q_1 = [10, -14, 0, -137, 0, 123, 44]
        self.images = [
            image(0, (421.9, 16.3, -2.2), joints_deg=q_ref),
            image(0, (421.9, 16.3, -2.0), joints_deg=q_ref),
            image(1, (422.9, 16.3, -2.1), yaw=0.5, joints_deg=q_1),
            image(1, (422.9, 16.3, -2.1), yaw=0.5, joints_deg=q_1),
            # Bewegt: darf nicht ins Mittel, auch wenn die Lage weit daneben liegt.
            image(1, (500.0, 0.0, 0.0), joints_deg=q_1, moved=True, reason="bewegt"),
            # Ohne Erkennung.
            ImageMeasurement(name="leer", pose=2, reason="kein Tag"),
        ]
        self.result = summarize_poses(self.images)

    def test_reference_mean_and_scatter(self):
        ref = self.result["poses"][0]
        self.assertEqual(self.result["reference"], 0)
        np.testing.assert_allclose(ref["positionMm"], [421.9, 16.3, -2.1], atol=1e-9)
        # z: -2,2 und -2,0 mm -> Std (ddof 1) 0,1414 mm = 141,4 um; 3D genauso (nur z streut).
        self.assertAlmostEqual(ref["scatterUm"][2], 141.421, places=2)
        self.assertAlmostEqual(ref["scatter3dUm"], 141.421, places=2)
        self.assertIsNone(ref["delta"])
        self.assertAlmostEqual(ref["camera"]["distanceMm"], 350.0, places=6)
        self.assertAlmostEqual(ref["camera"]["viewAngleDeg"], 0.0, places=6)

    def test_delta_against_reference_without_moved_frame(self):
        pose = self.result["poses"][1]
        self.assertEqual((pose["nImages"], pose["nUsed"]), (3, 2))
        d = pose["delta"]
        self.assertAlmostEqual(d["dxMm"], 1.0, places=9)
        self.assertAlmostEqual(d["dyMm"], 0.0, places=9)
        self.assertAlmostEqual(d["dzMm"], 0.0, places=9)
        self.assertAlmostEqual(d["normMm"], 1.0, places=9)
        self.assertAlmostEqual(d["angleDeg"], 0.5, places=6)
        self.assertAlmostEqual(d["yawDeg"], 0.5, places=6)
        self.assertAlmostEqual(d["maxJointDeg"], 10.0, places=6)
        self.assertEqual(d["maxJointIndex"], 0)

    def test_pose_without_usable_frames_has_no_delta(self):
        pose = self.result["poses"][2]
        self.assertEqual(pose["nUsed"], 0)
        self.assertIsNone(pose["positionMm"])
        self.assertIsNone(pose["delta"])

    def test_summary_and_no_correlation_below_four_poses(self):
        s = self.result["summary"]
        self.assertEqual(s["nPoses"], 1)
        self.assertAlmostEqual(s["rmsMm"], 1.0, places=9)
        self.assertAlmostEqual(s["dxMmRange"], 0.0, places=9)
        self.assertIsNone(self.result["correlation"])

    def test_no_reference_no_deltas(self):
        result = summarize_poses([image(1, (1, 2, 3)), image(2, (1, 2, 4))])
        self.assertIsNone(result["reference"])
        self.assertIsNone(result["summary"])
        self.assertTrue(all(p["delta"] is None for p in result["poses"].values()))

    def test_correlation_finds_a_joint_driven_error(self):
        # Fehler waechst linear mit q1: dx = 0,1 mm je Grad; die anderen Gelenke bleiben.
        images = [image(0, (400, 0, 0))]
        for k, q1 in enumerate([5, 10, 20, 40], start=1):
            images.append(image(k, (400 + 0.1 * q1, 0, 0), joints_deg=[q1, -14, 0, -137, 0, 123, 44]))
        result = summarize_poses(images)
        corr = result["correlation"]
        self.assertEqual(corr["n"], 4)
        self.assertAlmostEqual(corr["joints"][0]["dx"], 1.0, places=9)
        self.assertAlmostEqual(corr["joints"][0]["norm"], 1.0, places=9)
        self.assertTrue(math.isnan(corr["joints"][1]["dx"]))  # q2 konstant
        self.assertAlmostEqual(result["summary"]["dxMmRange"], 3.5, places=9)
        self.assertAlmostEqual(result["summary"]["maxMm"], 4.0, places=9)

    def test_pose_table_is_the_measurement_series(self):
        header, rows = pose_table(self.result)
        self.assertEqual(len(rows), 3)  # Referenz, Pose 1, Pose 2
        self.assertTrue(all(len(row) == len(header) for row in rows))
        col = {name: i for i, name in enumerate(header)}
        self.assertEqual(rows[0][col["Label"]], "Referenz")
        self.assertEqual(rows[0][col["x [mm]"]], "421,90")
        self.assertEqual(rows[1][col["Bilder gültig/gesamt"]], "2/3")
        self.assertEqual(rows[1][col["Δx [mm]"]], "1,000")
        self.assertEqual(rows[1][col["q1 [°]"]], "10,00")
        self.assertEqual(rows[2][col["x [mm]"]], "")
        _, rows_point = pose_table(self.result, decimal_comma=False)
        self.assertEqual(rows_point[1][col["Δx [mm]"]], "1.000")

    def test_report_contains_series_and_summary(self):
        text = render_report(self.result, info=["Session: `s`"], warnings=["1 Bild nicht genutzt"])
        self.assertIn("Messreihe", text)
        self.assertIn("Session: `s`", text)
        self.assertIn("1 Bild nicht genutzt", text)
        self.assertIn("422,9", text)


# --- Ende zu Ende: synthetische Bilder ueber die echte Pi-Ablage -------------------------------

SIZE = (640, 480)
FX = 800.0
TAG_PX = 160
TAG_M = 0.08
OFFSET_PX = (3.3, 2.1)  # nicht exakt mittig, sonst ist IPPE entartet (siehe test_repeatability_apriltag)
PI_APRILTAG = {
    "tag_family": "tag36h11",
    "detector_backend": "aruco",
    "subpixel_corner_refinement": True,
    "corner_refinement_method": "subpix",
    "apriltag_quad_decimate": 0.0,
    "detector_parameters": {},
}
IDENTITY = {"position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0, 1.0]}


def tag_image(tag_id=5):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    img = np.full((SIZE[1], SIZE[0]), 255, np.uint8)
    x0, y0 = (SIZE[0] - TAG_PX) // 2, (SIZE[1] - TAG_PX) // 2
    img[y0:y0 + TAG_PX, x0:x0 + TAG_PX] = cv2.aruco.generateImageMarker(dictionary, tag_id, TAG_PX)
    img = cv2.GaussianBlur(img, (0, 0), 1.0)
    matrix = np.float32([[1, 0, OFFSET_PX[0]], [0, 1, OFFSET_PX[1]]])
    return cv2.warpAffine(img, matrix, SIZE, flags=cv2.INTER_CUBIC, borderValue=255)


def intrinsics_meta():
    return {
        "source": "calibration",
        "calibrationId": "synthetisch",
        "imageSize": list(SIZE),
        "cameraMatrix": [[FX, 0, (SIZE[0] - 1) / 2], [0, FX, (SIZE[1] - 1) / 2], [0, 0, 1]],
        "distortionCoefficients": [0.0] * 5,
        "distortionModel": "pinhole",
    }


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class KinematicsSessionTest(unittest.TestCase):
    """Gleiches Bild aus jeder Pose; Pose 2 meldet einen um 10 mm in x versetzten Flansch.

    Genau so saehe ein Kinematikfehler aus: die Kamera sieht den Tag gleich, die gemeldete
    Flanschpose stimmt aber nicht -> 10 mm Abweichung in der Basis.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base_dir = Path(cls.tmp.name)
        pi = {"apriltag": PI_APRILTAG, "intrinsics": intrinsics_meta(),
              "hand_eye": {"content": {"poseFlangeCam": IDENTITY, "calibrationId": "he-synth"}}}
        img = tag_image()

        def save(phase, pose_index, burst, flange=IDENTITY, moved=False, cycle=None):
            client = {
                "test": "kinematik", "poseIndex": pose_index, "poseLabel": "Referenz" if pose_index == 0 else f"Pose {pose_index}",
                "cycle": cycle if cycle is not None else pose_index, "burstIndex": burst, "burstCount": 2,
                "target": {"type": "apriltag", "tagId": None, "tagSizeM": TAG_M}, "board": None,
                "actual": {"joints": [0.0, -0.2, 0.0, -2.4, 0.0, 2.1, 0.8 + 0.1 * pose_index],
                           "flangeInBase": flange, "settled": True, "movedDuringCapture": moved},
            }
            save_repeatability_image(img, base_dir, "2026-11-11_100000_kinematik", phase, json.dumps(client), pi, None)

        for k in (1, 2, 3):
            save("rauschen", 0, 1, cycle=k)
        save("zyklen_hand", 1, 1)
        save("zyklen_hand", 1, 2)
        shifted = {"position": [0.01, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0, 1.0]}
        save("zyklen_hand", 2, 1, flange=shifted)
        save("zyklen_hand", 2, 2, flange=shifted, moved=True)
        cls.folder = base_dir / "2026-11-11_100000_kinematik"
        cls.measured = measure_session(cls.folder)
        cls.result = summarize_poses(cls.measured.images)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_images_poses_and_labels(self):
        m = self.measured
        self.assertEqual(len(m.images), 7)
        self.assertEqual(sorted({im.pose for im in m.images}), [0, 1, 2])
        self.assertEqual(m.tag_id, 5)
        self.assertEqual(m.labels[0], "Referenz")
        self.assertEqual(m.labels[2], "Pose 2")
        self.assertEqual(m.calibration_id, "synthetisch")
        self.assertIn("he-synth", m.hand_eye_id)
        moved = [im for im in m.images if not im.usable]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].pose, 2)

    def test_tag_distance_from_size(self):
        # Abstand FX * 0,08 / 160 = 0,4 m.
        ref = self.result["poses"][0]
        self.assertAlmostEqual(ref["positionMm"][2], 400.0, delta=1.0)
        self.assertAlmostEqual(ref["camera"]["distanceMm"], 400.0, delta=1.0)

    def test_same_view_same_flange_no_delta(self):
        d = self.result["poses"][1]["delta"]
        self.assertLess(d["normMm"], 1e-6)
        self.assertLess(d["angleDeg"], 1e-6)

    def test_reported_flange_offset_shows_as_delta(self):
        d = self.result["poses"][2]["delta"]
        self.assertAlmostEqual(d["dxMm"], 10.0, places=6)
        self.assertAlmostEqual(d["dyMm"], 0.0, places=6)
        self.assertAlmostEqual(d["dzMm"], 0.0, places=6)
        self.assertAlmostEqual(d["maxJointDeg"], math.degrees(0.2), places=6)
        self.assertEqual(d["maxJointIndex"], 6)

    def test_cli_writes_report_series_and_plot(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("kinematik_cli", _TOOLS / "kinematik.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as out:
            out = Path(out)
            args = [str(self.folder), "--out", str(out / "b.md"), "--csv-posen", str(out / "p.csv"),
                    "--plot", str(out / "p.png"), "--csv", str(out / "bilder.csv")]
            stderr = sys.stderr
            try:
                sys.stderr = open(out / "stderr.txt", "w")
                self.assertEqual(cli.main(args), 0)
            finally:
                sys.stderr.close()
                sys.stderr = stderr
            report = (out / "b.md").read_text(encoding="utf-8")
            self.assertIn("2026-11-11_100000_kinematik", report)
            self.assertIn("Messreihe", report)
            with (out / "p.csv").open(encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh, delimiter=";"))
            self.assertEqual(len(rows), 4)  # Kopf + 3 Posen
            self.assertEqual(rows[3][rows[0].index("Δx [mm]")], "10,000")
            self.assertTrue((out / "bilder.csv").exists())
            try:
                import matplotlib  # noqa: F401
            except ImportError:
                return
            self.assertTrue((out / "p.png").stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
