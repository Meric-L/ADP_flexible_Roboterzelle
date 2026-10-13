"""Hand-Auge-Kalibrierung ueber OPC UA: Methoden, Statusknoten, Datei.

Ohne Kamera: die Quelle ist ein Fake, der OPC-UA-Teil
(`runner._install_hand_eye_session`) ist echt.
"""

import json
import tempfile
import types
import unittest
from pathlib import Path

try:
    import numpy as np
    from asyncua import Server, ua

    from tagloc import geometry
    from vision_server.errors import VisionErrorCode
    from vision_server.handeye_session import SAMPLE_SCHEMA, START_SCHEMA
    from vision_server.runner import _install_hand_eye_session

    from tests.test_handeye_session import FLANGE_CAM, cam_to_tag, flange_poses
except Exception:  # pragma: no cover - asyncua oder numpy fehlen
    np = None


class FakeSource:
    def __init__(self):
        self.flange = None
        self.hand_eye = None

    async def measure_tag(self, tag_id):
        return cam_to_tag(self.flange), ""

    def apply_hand_eye(self, hand_eye):
        self.hand_eye = hand_eye


@unittest.skipUnless(np is not None, "asyncua oder numpy fehlen")
class HandEyeOpcUaTest(unittest.IsolatedAsyncioTestCase):
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
        self.session = await _install_hand_eye_session(
            self.space, config, self.source, self.methods, lambda: self.busy
        )

    async def asyncTearDown(self):
        self.folder.cleanup()

    async def call(self, name, *args):
        node = self.server.get_node(ua.NodeId(f"VisionMachine.{name}", self.idx))
        return await self.space.vision_system.call_method(node, *args)

    async def status(self):
        return json.loads(await self.space.hand_eye_status.read_value())

    async def test_a_calibration_over_opcua(self):
        self.assertEqual(
            set(self.methods),
            {
                "StartHandEyeCalibration",
                "CaptureHandEyeSample",
                "FinishHandEyeCalibration",
                "AbortHandEyeCalibration",
            },
        )
        self.assertEqual((await self.status())["state"], "idle")

        self.busy = True  # Job oder Layer-2-Lauf aktiv
        start = json.dumps({"schema": START_SCHEMA, "tagId": 12, "tagInBase": None})
        self.assertEqual(await self.call("StartHandEyeCalibration", start), int(VisionErrorCode.BUSY))
        self.busy = False
        self.assertEqual(await self.call("StartHandEyeCalibration", start), 0)

        for flange in flange_poses():
            self.source.flange = flange
            sample = json.dumps(
                {"schema": SAMPLE_SCHEMA, "flangeInBase": geometry.pose_to_dict(flange)}
            )
            self.assertEqual(await self.call("CaptureHandEyeSample", sample), 0)
        self.assertEqual((await self.status())["acceptedCount"], 6)

        self.assertEqual(await self.call("FinishHandEyeCalibration"), 0)
        status = await self.status()
        self.assertEqual(status["state"], "finished")
        self.assertTrue(self.path.is_file())
        np.testing.assert_allclose(self.source.hand_eye.pose_flange_cam, FLANGE_CAM, atol=1e-6)

        self.assertEqual(
            await self.call("AbortHandEyeCalibration"), int(VisionErrorCode.INVALID_STATE)
        )


if __name__ == "__main__":
    unittest.main()
