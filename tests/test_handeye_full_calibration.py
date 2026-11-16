"""Volle Kalibrierung der Handkamera aus einer Rohaufnahme (`fullCalibration`).

Aus denselben Rohbildern erst die Intrinsik, dann Hand-Auge; beides wird
geschrieben und live uebernommen. Die Bilder sind synthetisch: ein ChArUco-Brett
mit nachgemessenen (krummen) Massen, gerendert ueber bekannte Intrinsik und
bekanntes Hand-Auge -- die Rechnung muss beides wiederfinden.
"""

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from tagloc import geometry
from tagloc.boards import BoardSpec, build_board
from tagloc.calibration import CameraCalibration, load_calibration
from tagloc.handeye import load_hand_eye
from tagloc.handeye_offline import calibrate_session
from vision_server.errors import VisionErrorCode
from vision_server.handeye_session import HandEyePorts, HandEyeSession

#: Labor-Brett mit den nachgemessenen Massen (274,8 mm / 11 Felder).
SPEC = BoardSpec(type="charuco", cols=11, rows=7, square_size_m=0.024982,
                 marker_size_m=0.018487, dictionary="DICT_5X5_100")
SIZE = (1280, 720)
K_TRUE = np.array([[900.0, 0, 646.0], [0, 902.0, 355.0], [0, 0, 1]])
X_TRUE = geometry.from_rotation_translation(
    cv2.Rodrigues(np.array([0.05, -0.03, 0.12]))[0], np.array([-0.047, -0.034, 0.060]))
BOARD_IN_BASE = geometry.from_rotation_translation(
    cv2.Rodrigues(np.array([np.pi, 0, 0]))[0], np.array([0.5, 0.0, 0.0]))
#: Irgendeine falsche Intrinsik, wie sie waehrend der Aufnahme aktiv sein kann.
CAL_ACTIVE = CameraCalibration(np.array([[1000.0, 0, 640], [0, 1000.0, 360], [0, 0, 1]]),
                               np.zeros(5), SIZE, frame_id="cam_flange")


def _render_views(count, seed=3):
    """`count` Paare (T_base_flange, Bild), Brett jeweils ganz im Bild."""
    rng = np.random.default_rng(seed)
    width_m, height_m = SPEC.cols * SPEC.square_size_m, SPEC.rows * SPEC.square_size_m
    # Genau 100 px je Feld: die Textur passt ohne Rundung auf das Brett.
    tw, th = 100 * SPEC.cols, 100 * SPEC.rows
    texture = build_board(SPEC).generateImage((tw, th), marginSize=0)
    corners_board = np.array([[0, 0, 0], [width_m, 0, 0], [width_m, height_m, 0], [0, height_m, 0]])
    centre_in_base = geometry.compose(BOARD_IN_BASE, geometry.from_rotation_translation(
        np.eye(3), [width_m / 2, height_m / 2, 0]))[:3, 3]
    views = []
    while len(views) < count:
        rotation = cv2.Rodrigues(np.array([np.pi, 0, 0]) + rng.normal(0, 0.25, 3))[0]
        camera_target = centre_in_base + np.r_[rng.normal(0, 0.03, 2), rng.uniform(0.33, 0.45)]
        flange = geometry.from_rotation_translation(rotation, camera_target)
        # Flansch so verschieben, dass die Kamera ueber der Brettmitte steht.
        camera = geometry.compose(flange, X_TRUE)
        flange[:3, 3] += camera_target - camera[:3, 3]
        cam_board = geometry.invert(geometry.compose(flange, X_TRUE)) @ BOARD_IN_BASE
        depth = (cam_board[:3, :3] @ corners_board.T + cam_board[:3, 3:4])[2]
        if depth.min() <= 0.1:
            continue
        pixels, _ = cv2.projectPoints(corners_board, cv2.Rodrigues(cam_board[:3, :3])[0],
                                      cam_board[:3, 3], K_TRUE, None)
        pixels = pixels.reshape(-1, 2)
        if pixels.min() < 20 or pixels[:, 0].max() > SIZE[0] - 20 or pixels[:, 1].max() > SIZE[1] - 20:
            continue
        # Pixelmitten liegen auf ganzen Zahlen: die Bildkante der Textur ist -0,5.
        edges = np.array([[-0.5, -0.5], [tw - 0.5, -0.5], [tw - 0.5, th - 0.5], [-0.5, th - 0.5]], np.float32)
        homography = cv2.getPerspectiveTransform(edges, pixels.astype(np.float32))
        image = cv2.warpPerspective(texture, homography, SIZE, flags=cv2.INTER_LINEAR, borderValue=255)
        views.append((flange, image))
    return views


VIEWS = _render_views(16)


def _write_session(root: Path, views) -> Path:
    folder = root / "sitzung"
    (folder / "poses").mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({
        "schema": "wsc.vision.handeye.raw/1", "frameId": "cam_flange", "imageStream": "configured",
        "board": SPEC.as_dict(), "intrinsics": None, "fullCalibration": True}))
    for index, (flange, image) in enumerate(views):
        pose = folder / "poses" / f"pose_{index:03d}"
        pose.mkdir()
        cv2.imwrite(str(pose / "camera.png"), image)
        (pose / "robot_state.json").write_text(json.dumps({
            "poseId": pose.name, "T_base_flange": flange.tolist(),
            "jointAnglesRad": [0.0] * 7, "poseGroup": "ABCDEF"[index % 6]}))
    return folder


class CalibrateSessionTest(unittest.TestCase):
    def test_recovers_intrinsics_and_hand_eye_from_the_same_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_session(Path(tmp), VIEWS)
            result = calibrate_session(folder)
            K = result.calibration.camera_matrix
            self.assertEqual(result.calibration.image_size, SIZE)
            np.testing.assert_allclose(K[[0, 1], [0, 1]], K_TRUE[[0, 1], [0, 1]], rtol=0.01)
            np.testing.assert_allclose(K[:2, 2], K_TRUE[:2, 2], atol=5.0)
            self.assertLess(result.intrinsic_rms_px, 0.5)
            self.assertEqual(result.intrinsic_image_count, len(VIEWS))
            # Hand-Auge mit nomineller Flanschpose; auf 1 mm / 0,2 Grad.
            self.assertLess(geometry.translation_distance_m(result.hand_eye_pose, X_TRUE), 0.001)
            self.assertLess(np.degrees(geometry.rotation_distance_rad(result.hand_eye_pose, X_TRUE)), 0.2)
            self.assertGreaterEqual(result.handeye_pose_count, 10)
            self.assertEqual(result.calibration.calibration_id, "cam_flange@sitzung-pi")
            # Die nachgemessenen Masse stehen unveraendert in der Kalibrierung.
            self.assertAlmostEqual(result.calibration.board["squareSizeM"], 0.024982)
            self.assertAlmostEqual(result.calibration.board["markerSizeM"], 0.018487)
            results = folder / "results"
            self.assertTrue((results / "intrinsics_pi.json").is_file())
            self.assertTrue((results / "handeye_pi.json").is_file())

    def test_too_few_images_is_a_clear_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _write_session(Path(tmp), VIEWS[:4])
            with self.assertRaises(ValueError):
                calibrate_session(folder)


class _FakePi:
    """Ports einer Hand-Auge-Sitzung; liefert die gerenderten Bilder der Reihe nach."""

    def __init__(self, tmp: Path, views):
        self.views = list(views)
        self.statuses, self.applied, self.applied_intrinsics = [], [], []
        self.ports = HandEyePorts(
            measure_tag=self._measure, capture_board_raw=self._measure, busy=lambda: False,
            path=tmp / "handeye" / "cam_flange.json", apply=self.applied.append,
            publish=self._publish, raw_sessions_dir=tmp / "rohbilder",
            intrinsics_path=tmp / "calibration" / "cam_flange.json",
            apply_intrinsics=self._apply_intrinsics)

    async def _measure(self, _spec):
        _, image = self.views.pop(0)
        return geometry.identity(), "", {
            "image": image, "calibration": CAL_ACTIVE, "intrinsicsSource": "realsense_factory",
            "capturedAt": "2026-11-11T10:00:00+00:00", "reprojectionErrorPx": 0.3}

    async def _publish(self, text):
        self.statuses.append(json.loads(text))

    async def _apply_intrinsics(self, calibration):
        self.applied_intrinsics.append(calibration)


def _start(**extra) -> str:
    return json.dumps({"schema": "wsc.vision.handeye.start/1", "board": SPEC.as_dict(), **extra})


def _sample(index, flange) -> str:
    return json.dumps({"schema": "wsc.vision.handeye.sample/1", "poseId": f"pose_{index:03d}",
                       "poseGroup": "ABCDEF"[index % 6], "flangeInBase": geometry.pose_to_dict(flange),
                       "jointAnglesRad": [0.0] * 7})


class FullCalibrationSessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_finish_writes_and_activates_intrinsics_and_hand_eye(self):
        with tempfile.TemporaryDirectory() as tmp:
            pi = _FakePi(Path(tmp), VIEWS)
            session = HandEyeSession(pi.ports)
            self.assertEqual(await session.start(_start(fullCalibration=True)), VisionErrorCode.OK)
            for index, (flange, _) in enumerate(VIEWS):
                self.assertEqual(await session.capture(_sample(index, flange)), VisionErrorCode.OK)
            self.assertEqual(await session.finish(), VisionErrorCode.OK)

            status = pi.statuses[-1]
            self.assertTrue(status["fullCalibration"])
            result = status["result"]
            self.assertEqual(result["kind"], "intrinsicsAndHandEye")
            self.assertAlmostEqual(result["intrinsics"]["fx"], 900.0, delta=9.0)
            self.assertEqual(list(result["intrinsics"]["imageSize"]), list(SIZE))
            np.testing.assert_allclose(result["handEye"]["translationMm"], X_TRUE[:3, 3] * 1000, atol=1.0)
            self.assertEqual(result["backups"], {"intrinsics": None, "handEye": None})
            self.assertIn("aktiviert", status["message"])

            # Beide Dateien geschrieben, zusammengehoerig und live uebernommen.
            calibration = load_calibration(Path(tmp) / "calibration" / "cam_flange.json")
            hand_eye = load_hand_eye(Path(tmp) / "handeye" / "cam_flange.json")
            self.assertEqual(hand_eye.calibration_id, calibration.calibration_id)
            self.assertEqual(hand_eye.intrinsics_source, "calibration")
            self.assertEqual(len(pi.applied_intrinsics), 1)
            self.assertEqual(len(pi.applied), 1)
            self.assertLess(geometry.translation_distance_m(pi.applied[0].pose_flange_cam, X_TRUE), 0.001)

            # Die Werks-/Platzhalter-Intrinsik der Aufnahme landet nicht im Datensatz.
            config = json.loads((Path(result["sessionPath"]) / "config.json").read_text())
            self.assertIsNone(config["intrinsics"])
            self.assertAlmostEqual(config["board"]["squareSizeM"], 0.024982)

    async def test_too_few_images_keeps_session_open_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pi = _FakePi(Path(tmp), VIEWS[:3])
            session = HandEyeSession(pi.ports)
            self.assertEqual(await session.start(_start(fullCalibration=True)), VisionErrorCode.OK)
            for index, (flange, _) in enumerate(VIEWS[:3]):
                await session.capture(_sample(index, flange))
            self.assertEqual(await session.finish(), VisionErrorCode.INVALID_STATE)
            self.assertTrue(pi.statuses[-1]["message"].startswith("Nicht berechnet"))
            self.assertTrue(session.active)
            self.assertFalse((Path(tmp) / "calibration" / "cam_flange.json").exists())
            self.assertFalse((Path(tmp) / "handeye" / "cam_flange.json").exists())
            self.assertEqual(pi.applied + pi.applied_intrinsics, [])


class BoardOptionsTest(unittest.IsolatedAsyncioTestCase):
    """Schachbrett und ChArUco mit eingetragenen Massen; nur Unplausibles wird abgelehnt."""

    async def _code(self, board=None, **extra):
        with tempfile.TemporaryDirectory() as tmp:
            session = HandEyeSession(_FakePi(Path(tmp), []).ports)
            options = {"schema": "wsc.vision.handeye.start/1", **extra}
            if board is not None:
                options["board"] = board
            return await session.start(json.dumps(options))

    async def test_accepts_measured_charuco_and_other_chessboards(self):
        self.assertEqual(await self._code(SPEC.as_dict(), fullCalibration=True), VisionErrorCode.OK)
        self.assertEqual(await self._code(SPEC.as_dict(), captureOnly=True), VisionErrorCode.OK)
        chess = BoardSpec(type="chessboard", cols=9, rows=6, square_size_m=0.0251).as_dict()
        self.assertEqual(await self._code(chess, fullCalibration=True), VisionErrorCode.OK)
        other = {**SPEC.as_dict(), "cols": 8, "rows": 5, "squareSizeM": 0.03, "markerSizeM": 0.022,
                 "dictionary": "DICT_4X4_50"}
        self.assertEqual(await self._code(other, fullCalibration=True), VisionErrorCode.OK)

    async def test_rejects_implausible_options(self):
        bad = VisionErrorCode.INVALID_ARGUMENT
        self.assertEqual(await self._code(SPEC.as_dict(), fullCalibration=True, captureOnly=True), bad)
        self.assertEqual(await self._code(None, fullCalibration=True), bad)
        for change in ({"markerSizeM": 0.024982}, {"dictionary": "DICT_9X9_1"},
                       {"cols": 15, "rows": 15, "dictionary": "DICT_4X4_50"},
                       {"squareSizeM": 0.2, "markerSizeM": 0.1}, {"cols": 2}):
            with self.subTest(change=change):
                self.assertEqual(await self._code({**SPEC.as_dict(), **change}, fullCalibration=True), bad)


if __name__ == "__main__":
    unittest.main()
