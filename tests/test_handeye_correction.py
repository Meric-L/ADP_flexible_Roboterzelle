"""Punkte-Pruefung: korrigierte Kameralage uebernehmen (`ApplyHandEyeCorrection`).

Prueft die Datei-Seite (Sicherung, neue Kennung, Guetezahlen bleiben,
Protokoll), die Ablehnungen und die OPC-UA-Methode samt Sitzungsstatus.
"""

import json
import math
import tempfile
import types
import unittest
from pathlib import Path

try:
    import numpy as np
    from asyncua import Server, ua

    from tagloc import geometry
    from tagloc.handeye import HandEye, load_hand_eye, save_hand_eye
    from vision_server.errors import VisionErrorCode
    from vision_server.handeye_correction import (
        REQUEST_SCHEMA,
        CorrectionRefused,
        apply_correction,
        parse_correction,
    )
    from vision_server.handeye_session import START_SCHEMA
    from vision_server.runner import _install_hand_eye_correction, _install_hand_eye_session

    from tests.test_handeye_session import FLANGE_CAM
except Exception:  # pragma: no cover - asyncua oder numpy fehlen
    np = None

BASE_ID = "cam_flange@2026-11-02T11:49:12Z"


def write_calibration(path):
    save_hand_eye(
        path,
        HandEye(
            pose_flange_cam=FLANGE_CAM,
            frame_id="cam_flange",
            calibration_id=BASE_ID,
            rms_position_m=0.001,
            rms_rotation_deg=0.1,
            sample_count=14,
            loo_error_rms_m=0.0019,
        ),
    )


def shifted(dx=0.0, dy=0.0, dz=0.0, turn_deg=0.0):
    """Die Kameralage, um `(dx, dy, dz)` im Flansch-KS verschoben und um die Kamera-z gedreht."""
    pose = np.array(FLANGE_CAM, dtype=np.float64)
    pose[:3, 3] += [dx, dy, dz]
    angle = math.radians(turn_deg)
    turn = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
    pose[:3, :3] = pose[:3, :3] @ turn
    return pose


def request_json(pose, based_on=BASE_ID, check=None):
    data = {"schema": REQUEST_SCHEMA, "basedOnCalibrationId": based_on, "poseFlangeCam": geometry.pose_to_dict(pose)}
    if check is not None:
        data["check"] = check
    return json.dumps(data)


@unittest.skipUnless(np is not None, "asyncua oder numpy fehlen")
class ParseTest(unittest.TestCase):
    def test_a_valid_request(self):
        request = parse_correction(request_json(shifted(0.001), check={"punkte": 9}))
        self.assertEqual(request.based_on_calibration_id, BASE_ID)
        np.testing.assert_allclose(request.pose_flange_cam, shifted(0.001), atol=1e-12)
        self.assertEqual(request.check, {"punkte": 9})
        self.assertEqual(parse_correction(request_json(FLANGE_CAM)).check, {})

    def test_invalid_requests(self):
        good = json.loads(request_json(FLANGE_CAM))
        for broken in (
            {**good, "schema": "wsc.vision.handeye.start/1"},
            {**good, "basedOnCalibrationId": ""},
            {key: value for key, value in good.items() if key != "basedOnCalibrationId"},
            {**good, "poseFlangeCam": {"position": [0, 0, 0]}},
            {**good, "poseFlangeCam": {"position": [0, 0, 0], "orientation": [0, 0, 0, 0]}},
            {**good, "check": [1, 2]},
        ):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                parse_correction(json.dumps(broken))
        with self.assertRaises(ValueError):
            parse_correction("kaputt")


@unittest.skipUnless(np is not None, "asyncua oder numpy fehlen")
class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "cam_flange.json"

    def tearDown(self):
        self.folder.cleanup()

    def apply(self, text):
        return apply_correction(parse_correction(text), self.path)

    def backups(self):
        return sorted(self.path.parent.glob("cam_flange.json.*.bak"))

    def test_the_new_pose_is_written_and_the_old_file_kept(self):
        write_calibration(self.path)
        outcome = self.apply(request_json(shifted(0.002, -0.001), check={"punkte": 9}))

        loaded = load_hand_eye(self.path)
        np.testing.assert_allclose(loaded.pose_flange_cam, shifted(0.002, -0.001), atol=1e-9)
        self.assertNotEqual(loaded.calibration_id, BASE_ID)
        # Die Guetezahlen bleiben die der urspruenglichen Kalibrierung.
        self.assertEqual(loaded.sample_count, 14)
        self.assertAlmostEqual(loaded.loo_error_rms_m, 0.0019)
        np.testing.assert_allclose(outcome.hand_eye.pose_flange_cam, loaded.pose_flange_cam)

        result = outcome.result
        self.assertTrue(result["applied"])
        self.assertEqual(result["previousCalibrationId"], BASE_ID)
        self.assertEqual(result["calibrationId"], loaded.calibration_id)
        np.testing.assert_allclose(result["deltaPositionM"], [0.002, -0.001, 0.0], atol=1e-9)
        self.assertAlmostEqual(result["deltaRotationDeg"], 0.0, places=6)
        self.assertIn("korrigiert", result["message"])

        [backup] = self.backups()
        self.assertEqual(str(backup), result["backupPath"])
        self.assertEqual(load_hand_eye(backup).calibration_id, BASE_ID)

        log = json.loads(Path(result["logPath"]).read_text(encoding="utf-8"))
        self.assertEqual(Path(result["logPath"]).parent.name, "corrections")
        self.assertEqual(log["basedOnCalibrationId"], BASE_ID)
        self.assertEqual(log["check"], {"punkte": 9})
        self.assertEqual(log["result"]["calibrationId"], loaded.calibration_id)

    def test_the_same_correction_twice_is_refused(self):
        write_calibration(self.path)
        self.apply(request_json(shifted(0.002)))
        with self.assertRaises(CorrectionRefused) as refused:
            self.apply(request_json(shifted(0.002)))
        self.assertEqual(refused.exception.code, VisionErrorCode.INVALID_STATE)
        self.assertEqual(len(self.backups()), 1)

    def test_a_correction_for_another_calibration_changes_nothing(self):
        write_calibration(self.path)
        with self.assertRaises(CorrectionRefused) as refused:
            self.apply(request_json(shifted(0.002), based_on="cam_flange@2026-10-01T08:00:00Z"))
        self.assertEqual(refused.exception.code, VisionErrorCode.INVALID_STATE)
        self.assertIn("inzwischen geaendert", refused.exception.message)
        self.assertEqual(load_hand_eye(self.path).calibration_id, BASE_ID)
        self.assertEqual(self.backups(), [])

    def test_too_large_is_no_correction(self):
        write_calibration(self.path)
        for pose in (shifted(0.025), shifted(turn_deg=3.0)):
            with self.subTest(pose=pose[:3, 3]), self.assertRaises(CorrectionRefused) as refused:
                self.apply(request_json(pose))
            self.assertEqual(refused.exception.code, VisionErrorCode.INVALID_ARGUMENT)
        self.apply(request_json(shifted(0.015, turn_deg=1.5)))  # knapp darunter geht

    def test_without_a_calibration_there_is_nothing_to_correct(self):
        with self.assertRaises(CorrectionRefused) as refused:
            self.apply(request_json(FLANGE_CAM))
        self.assertEqual(refused.exception.code, VisionErrorCode.INVALID_STATE)
        self.assertFalse(self.path.exists())


class FakeSource:
    def __init__(self):
        self.hand_eye = None

    async def measure_tag(self, tag_id):
        return None, "nicht gebraucht"

    def apply_hand_eye(self, hand_eye):
        self.hand_eye = hand_eye


@unittest.skipUnless(np is not None, "asyncua oder numpy fehlen")
class OpcUaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "cam_flange.json"
        self.server = Server()
        await self.server.init()
        idx = await self.server.register_namespace("http://launch-rm.de/vision")
        vision_system = await self.server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        self.idx = idx
        self.space = types.SimpleNamespace(
            vision_system=vision_system,
            own_idx=idx,
            hand_eye_status=await vision_system.add_variable(
                ua.NodeId("VisionMachine.HandEyeStatus", idx),
                ua.QualifiedName("HandEyeStatus", idx),
                "",
                ua.VariantType.String,
            ),
        )
        config = types.SimpleNamespace(
            vision_system_name="VisionMachine",
            apriltag=types.SimpleNamespace(hand_eye_path=self.path, frame_id="cam_flange"),
        )
        self.source = FakeSource()
        self.busy = False
        self.methods = {}
        busy = lambda: self.busy  # noqa: E731
        session = await _install_hand_eye_session(self.space, config, self.source, self.methods, busy)
        await _install_hand_eye_correction(
            self.space, config, self.source, self.methods,
            types.SimpleNamespace(state_names=lambda: ("Operational", "Ready")), session, busy,
        )

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def call(self, name, *args):
        node = self.server.get_node(ua.NodeId(f"VisionMachine.{name}", self.idx))
        return await self.space.vision_system.call_method(node, *args)

    async def status(self):
        return json.loads(await self.space.hand_eye_status.read_value())

    async def test_a_correction_over_opcua(self):
        self.assertIn("ApplyHandEyeCorrection", self.methods)
        write_calibration(self.path)

        result_json, error = await self.call("ApplyHandEyeCorrection", request_json(shifted(0.0, 0.003)))
        self.assertEqual(error, 0)
        result = json.loads(result_json)
        self.assertTrue(result["applied"])
        np.testing.assert_allclose(self.source.hand_eye.pose_flange_cam, shifted(0.0, 0.003), atol=1e-9)
        status = await self.status()
        self.assertEqual(status["state"], "idle")
        self.assertIn("korrigiert", status["message"])
        self.assertIsNone(status["result"])
        self.assertEqual(status["current"]["calibrationId"], result["calibrationId"])
        np.testing.assert_allclose(
            status["current"]["poseFlangeCam"]["position"], shifted(0.0, 0.003)[:3, 3], atol=1e-6
        )

        # Auf die alte Kalibrierung gerechnet: abgelehnt, mit Ergebnis-JSON.
        result_json, error = await self.call("ApplyHandEyeCorrection", request_json(shifted(0.0, 0.003)))
        self.assertEqual(error, int(VisionErrorCode.INVALID_STATE))
        self.assertEqual(json.loads(result_json)["applied"], False)

        result_json, error = await self.call("ApplyHandEyeCorrection", "kaputt")
        self.assertEqual(error, int(VisionErrorCode.INVALID_ARGUMENT))
        self.assertEqual(json.loads(result_json)["error"], "INVALID_ARGUMENT")

    async def test_busy_and_an_open_session_block_it(self):
        write_calibration(self.path)
        self.busy = True  # Job, Kamera-Kalibrierung oder Layer-2-Lauf
        _, error = await self.call("ApplyHandEyeCorrection", request_json(shifted(0.001)))
        self.assertEqual(error, int(VisionErrorCode.BUSY))
        self.busy = False

        start = json.dumps({"schema": START_SCHEMA, "tagId": 12, "tagInBase": None})
        self.assertEqual(await self.call("StartHandEyeCalibration", start), 0)
        _, error = await self.call("ApplyHandEyeCorrection", request_json(shifted(0.001)))
        self.assertEqual(error, int(VisionErrorCode.BUSY))
        self.assertEqual(load_hand_eye(self.path).calibration_id, BASE_ID)
        self.assertEqual(await self.call("AbortHandEyeCalibration"), 0)

        _, error = await self.call("ApplyHandEyeCorrection", request_json(shifted(0.001)))
        self.assertEqual(error, 0)


if __name__ == "__main__":
    unittest.main()
