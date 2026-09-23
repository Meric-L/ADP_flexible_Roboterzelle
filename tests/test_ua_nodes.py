"""Feste NodeIds der additiven Knoten (doc/vision-server-interface.md §13.1).

Die Knoten entstehen seit dem Refactoring ueber `ua_nodes.add_named_variable`;
dieser Test haelt fest, dass NodeId, BrowseName, Datentyp und
Beschreibbarkeit dabei gleich geblieben sind.
"""

import asyncio
import unittest

from asyncua import Server, ua

from vision_server.config import VisionServerConfig
from vision_server.profiles import AprilTagProfileConfig, CameraStreamConfig

WRITABLE = ua.AccessLevel.CurrentWrite


class AdditiveKnotenTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from vision_server.address_space import attach_vision_system, configure_server
        from vision_server.result_management import ResultStore

        asyncio.get_running_loop().set_debug(False)
        config = VisionServerConfig(
            endpoint="opc.tcp://127.0.0.1:48530/test/",
            camera_stream=CameraStreamConfig(),
            apriltag=AprilTagProfileConfig(),
        )
        self.server = Server()
        await configure_server(self.server, config)
        self.space = await attach_vision_system(self.server, config)
        self.results = await ResultStore.create(self.space)
        self.idx = self.space.own_idx

    async def assert_variable(self, node, identifier, vtype, *, writable=False):
        self.assertEqual(node.nodeid, ua.NodeId(identifier, self.idx))
        self.assertEqual(
            await node.read_browse_name(),
            ua.QualifiedName(identifier.rsplit(".", 1)[1], self.idx),
        )
        self.assertEqual((await node.read_data_value()).Value.VariantType, vtype)
        access = await node.read_attribute(ua.AttributeIds.AccessLevel)
        self.assertEqual(bool(access.Value.Value & (1 << WRITABLE)), writable, identifier)

    async def test_nodeids_der_vision_machine(self):
        space = self.space
        await self.assert_variable(
            space.latest_camera_frame, "VisionMachine.LatestCameraFrame", ua.VariantType.String
        )
        await self.assert_variable(
            space.camera_stream_mode,
            "VisionMachine.CameraStreamMode",
            ua.VariantType.String,
            writable=True,
        )
        await self.assert_variable(
            space.camera_stream_http_port,
            "VisionMachine.CameraStreamHttpPort",
            ua.VariantType.Int32,
        )
        await self.assert_variable(
            space.calibration_progress,
            "VisionMachine.CalibrationProgress",
            ua.VariantType.String,
        )
        self.assertEqual(await space.calibration_progress.read_value(), '{"running": false}')
        await self.assert_variable(
            space.active_calibration_info,
            "VisionMachine.ActiveCalibrationInfo",
            ua.VariantType.String,
        )
        await self.assert_variable(
            self.results.json_node, "VisionMachine.LatestResultJson", ua.VariantType.String
        )


if __name__ == "__main__":
    unittest.main()
