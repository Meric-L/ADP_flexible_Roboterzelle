"""Tests fuer den Kinematik-Test (`repeatability_eval.kinematics`, CLI `tools/wiederholgenauigkeit/kinematik.py`):
Rechenlogik an synthetischen Matrizen, Ende-zu-Ende an synthetischen Tag-Bildern."""

import csv
import json
import math
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
        measure_session_from_json,
        summarize_poses,
        write_session_report,
        yaw_deg,
    )
    from repeatability_eval.apriltag import build_apriltag_detector, pose_from_client
    from repeatability_eval.live import AprilTagLive, measure_for_save
    from tagloc.calibration import CameraCalibration
    from vision_server.kinematics_autorun import KINEMATICS_SESSION_SUFFIX, SessionRunCoalescer
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
#: Hand-Auge mit Drehung (90 Grad um z) und Versatz: prueft Quaternion-Reihenfolge und Verkettung.
HAND_EYE = {"position": [-0.047, -0.034, 0.060], "orientation": [0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)]}
SESSION = "2026-11-11_100000_kinematik"


def tag_image(tag_id=5):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    img = np.full((SIZE[1], SIZE[0]), 255, np.uint8)
    x0, y0 = (SIZE[0] - TAG_PX) // 2, (SIZE[1] - TAG_PX) // 2
    img[y0:y0 + TAG_PX, x0:x0 + TAG_PX] = cv2.aruco.generateImageMarker(dictionary, tag_id, TAG_PX)
    img = cv2.GaussianBlur(img, (0, 0), 1.0)
    matrix = np.float32([[1, 0, OFFSET_PX[0]], [0, 1, OFFSET_PX[1]]])
    return cv2.warpAffine(img, matrix, SIZE, flags=cv2.INTER_CUBIC, borderValue=255)


def calibration():
    return CameraCalibration(
        camera_matrix=np.array([[FX, 0, (SIZE[0] - 1) / 2], [0, FX, (SIZE[1] - 1) / 2], [0, 0, 1]]),
        distortion=np.zeros(5),
        image_size=SIZE,
        calibration_id="synthetisch",
    )


def intrinsics_meta():
    return {
        "source": "calibration",
        "calibrationId": "synthetisch",
        "imageSize": list(SIZE),
        "cameraMatrix": calibration().camera_matrix.tolist(),
        "distortionCoefficients": [0.0] * 5,
        "distortionModel": "pinhole",
    }


def run_cli(args):
    """CLI `tools/wiederholgenauigkeit/kinematik.py` im Prozess; stdout/stderr verschluckt."""
    import contextlib
    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location("kinematik_cli", _TOOLS / "kinematik.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        return cli.main([str(a) for a in args])


def read_pose_csv(path):
    with Path(path).open(encoding="utf-8-sig") as fh:
        return list(csv.reader(fh, delimiter=";"))


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class KinematicsSessionTest(unittest.TestCase):
    """Gleiches Bild aus jeder Pose, gespeichert wie auf dem Pi (Live-Messung im JSON).

    Pose 2 meldet einen um 10 mm in x versetzten Flansch -- so saehe ein Kinematikfehler aus:
    die Kamera sieht den Tag gleich, die gemeldete Flanschpose stimmt aber nicht.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base_dir = Path(cls.tmp.name)
        pi = {"apriltag": PI_APRILTAG, "intrinsics": intrinsics_meta(),
              "hand_eye": {"content": {"poseFlangeCam": HAND_EYE, "calibrationId": "he-synth"}}}
        live = AprilTagLive(build_apriltag_detector(PI_APRILTAG), calibration=calibration,
                            pose_flange_cam=lambda: pose_from_client(HAND_EYE))
        img = tag_image()

        def save(phase, pose_index, burst, flange=IDENTITY, moved=False, cycle=None, measured=True):
            client = json.dumps({
                "test": "kinematik", "poseIndex": pose_index,
                "poseLabel": "Referenz" if pose_index == 0 else f"Pose {pose_index}",
                "cycle": cycle if cycle is not None else pose_index, "burstIndex": burst, "burstCount": 2,
                "target": {"type": "apriltag", "tagId": None, "tagSizeM": TAG_M}, "board": None,
                "actual": {"joints": [0.0, -0.2, 0.0, -2.4, 0.0, 2.1, 0.8 + 0.1 * pose_index],
                           "flangeInBase": flange, "settled": True, "movedDuringCapture": moved},
            })
            measurement = measure_for_save(img, client, live) if measured else None
            save_repeatability_image(img, base_dir, SESSION, phase, client, pi, measurement)

        for k in (1, 2, 3):
            save("rauschen", 0, 1, cycle=k)
        save("zyklen_hand", 1, 1)
        save("zyklen_hand", 1, 2)
        shifted = {"position": [0.01, 0.0, 0.0], "orientation": [0.0, 0.0, 0.0, 1.0]}
        save("zyklen_hand", 2, 1, flange=shifted)
        save("zyklen_hand", 2, 2, flange=shifted, moved=True)
        # Ohne Pi-Messung im JSON (z. B. alte Software): fuer die JSON-Quelle nicht nutzbar.
        save("zyklen_hand", 3, 1, measured=False)
        cls.folder = base_dir / SESSION
        cls.from_json = measure_session_from_json(cls.folder)
        cls.from_images = measure_session(cls.folder)
        cls.result = summarize_poses(cls.from_json.images)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_images_poses_and_labels(self):
        m = self.from_json
        self.assertEqual(len(m.images), 8)
        self.assertEqual(sorted({im.pose for im in m.images}), [0, 1, 2, 3])
        self.assertEqual(m.tag_id, 5)
        self.assertEqual(m.labels[0], "Referenz")
        self.assertEqual(m.labels[2], "Pose 2")
        self.assertEqual(m.calibration_id, "synthetisch")
        self.assertIn("he-synth", m.hand_eye_id)
        unused = sorted((im.pose, im.reason) for im in m.images if not im.usable)
        self.assertEqual([pose for pose, _ in unused], [2, 3])
        self.assertEqual(unused[1][1], "keine Pi-Messung")

    def test_json_chain_matches_the_pi_base_position(self):
        # Keine Warnung "weicht von tagInBaseM ab": Quaternion-Reihenfolge und Verkettung stimmen.
        self.assertFalse(any("tagInBaseM" in w for w in self.from_json.warnings), self.from_json.warnings)
        for doc_path in sorted(self.folder.glob("*/*.json")):
            doc = json.loads(doc_path.read_text(encoding="utf-8"))
            image = next(im for im in self.from_json.images if im.name.endswith(doc_path.stem) or doc_path.stem in im.name)
            if image.t_base_tag is not None and doc["messung"]:
                np.testing.assert_allclose(image.t_base_tag[:3, 3], doc["messung"]["tagInBaseM"], atol=1e-9)

    def test_json_and_offline_redetection_agree(self):
        a = summarize_poses(self.from_json.images)["poses"]
        b = summarize_poses(self.from_images.images)["poses"]
        for nr in (0, 1, 2):
            np.testing.assert_allclose(a[nr]["positionMm"], b[nr]["positionMm"], atol=1e-6)
        self.assertIsNone(a[3]["positionMm"])  # ohne Messung im JSON
        self.assertIsNotNone(b[3]["positionMm"])  # offline aus dem Bild erkannt

    def test_tag_distance_from_size(self):
        # Abstand FX * 0,08 / 160 = 0,4 m.
        self.assertAlmostEqual(self.result["poses"][0]["camera"]["distanceMm"], 400.0, delta=1.0)

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

    def test_write_session_report_into_the_session_folder(self):
        with tempfile.TemporaryDirectory() as out:
            out = Path(out)
            paths = write_session_report(self.folder, out=out / "b.md", csv_posen=out / "p.csv", plot=out / "p.png")
            report = Path(paths["report"]).read_text(encoding="utf-8")
            self.assertIn(SESSION, report)
            self.assertIn("Messreihe", report)
            self.assertIn("Quelle: Pi-Messung aus den JSONs", report)
            rows = read_pose_csv(paths["csvPosen"])
            self.assertEqual(len(rows), 5)  # Kopf + Referenz + 3 Posen
            self.assertEqual(rows[3][rows[0].index("Δx [mm]")], "10,000")
            self.assertEqual(list(out.glob("*.tmp*")), [])  # atomar geschrieben, nichts liegen geblieben
            with self.assertRaises(ValueError):
                write_session_report(self.folder, source="unbekannt")

    def test_cli_defaults_to_the_json_and_can_redetect(self):
        with tempfile.TemporaryDirectory() as out:
            out = Path(out)
            common = ["--out", out / "b.md", "--csv-posen", out / "p.csv", "--plot", out / "p.png"]
            self.assertEqual(run_cli([self.folder, *common, "--csv", out / "bilder.csv"]), 0)
            rows = read_pose_csv(out / "p.csv")
            self.assertEqual(rows[3][rows[0].index("Δx [mm]")], "10,000")
            self.assertEqual(rows[4][rows[0].index("Bilder gültig/gesamt")], "0/1")
            self.assertTrue((out / "bilder.csv").exists())
            try:
                import matplotlib  # noqa: F401
            except ImportError:
                pass
            else:
                self.assertGreater((out / "p.png").stat().st_size, 0)

            self.assertEqual(run_cli([self.folder, *common, "--neu-erkennen", "--tag-size-mm", "80"]), 0)
            rows = read_pose_csv(out / "p.csv")
            self.assertEqual(rows[3][rows[0].index("Δx [mm]")], "10,000")
            self.assertEqual(rows[4][rows[0].index("Bilder gültig/gesamt")], "1/1")
            self.assertIn("Offline neu erkannt", (out / "b.md").read_text(encoding="utf-8"))

    def test_cli_fails_cleanly_without_a_session(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(run_cli([empty, "--stdout"]), 1)


@unittest.skipIf(np is None, "numpy/OpenCV fehlen")
class SessionRunCoalescerTest(unittest.TestCase):
    """Auswertung auf dem Pi: je Session ein Lauf, Anstoesse waehrenddessen -> genau ein Folgelauf."""

    def test_coalesces_triggers_per_session(self):
        import asyncio
        import threading

        runs = []
        first_started = threading.Event()
        release = threading.Event()

        def run(session):
            runs.append(session)
            if len(runs) == 1:
                first_started.set()
                release.wait(5)

        async def scenario():
            coalescer = SessionRunCoalescer(run)
            coalescer.trigger("a")
            await asyncio.to_thread(first_started.wait, 5)
            for _ in range(5):
                coalescer.trigger("a")  # waehrend des Laufs: nur vormerken
            coalescer.trigger("b")  # andere Session laeuft unabhaengig
            release.set()
            await coalescer.wait_idle()

        asyncio.run(scenario())
        self.assertEqual(runs.count("a"), 2)
        self.assertEqual(runs.count("b"), 1)
        self.assertEqual(runs[0], "a")

    def test_errors_are_logged_and_the_next_trigger_runs_again(self):
        import asyncio

        runs = []

        def run(session):
            runs.append(session)
            raise RuntimeError("kaputt")

        async def scenario():
            coalescer = SessionRunCoalescer(run)
            with self.assertLogs(level="ERROR"):
                coalescer.trigger("a")
                await coalescer.wait_idle()
            with self.assertLogs(level="ERROR"):
                coalescer.trigger("a")
                await coalescer.wait_idle()

        asyncio.run(scenario())
        self.assertEqual(runs, ["a", "a"])

    def test_only_kinematics_sessions_are_evaluated(self):
        self.assertTrue("2026-11-11_101500_kinematik".endswith(KINEMATICS_SESSION_SUFFIX))
        self.assertFalse("2026-11-11_101500_apriltag".endswith(KINEMATICS_SESSION_SUFFIX))


if __name__ == "__main__":
    unittest.main()
