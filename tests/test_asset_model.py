"""The plant view per OPC 40100-2 (AMCM).

Requires the nodesets under src/vision_server/nodesets/ and really imports them.
Only then can it be checked that the dependency chain DI -> Machinery -> AMCM
resolves against asyncua's base address space; a DI version that did not resolve
is documented in src/vision_server/nodesets/README.md.
"""

import unittest

from asyncua import ua

from tests._support import HAS_NODESETS, AddressSpaceCache
from vision_server.nodeset_ids import (
    DI_DEVICE_HEALTH_ENUMERATION,
    DeviceHealth,
    node_id,
)
from vision_server.profiles import AssetConfig

ASSETS = AssetConfig(
    serial_number="vision-test-01",
    image_sensor_model="Test Camera Module",
    lens_model="Test Lens 4.74mm",
)


#: Building takes several seconds (see `AddressSpaceCache`); once per variant,
#: not once per test method.
build = AddressSpaceCache().build
_WITH_ASSETS: dict[str, tuple] = {}


async def build_with_assets(assets: AssetConfig, key: str, port: int):
    """Address space plus the plant view built on it, also memoised."""
    from vision_server.asset_model import attach_asset_model

    cached = _WITH_ASSETS.get(key)
    if cached is not None:
        return cached
    server, space = await build(assets, key, port)
    _WITH_ASSETS[key] = (server, space, await attach_asset_model(space, assets))
    return _WITH_ASSETS[key]


class WithoutPartTwoTest(unittest.IsolatedAsyncioTestCase):
    async def test_leaves_the_amcm_namespace_unloaded(self):
        server, space = await build(None, "ohne", 48401)
        self.assertIsNone(space.amcm_idx)
        namespaces = await server.get_namespace_array()
        self.assertNotIn("http://opcfoundation.org/UA/MachineVision/AMCM/", namespaces)

    async def test_the_job_path_is_unaffected(self):
        _, space = await build(None, "ohne", 48402)
        self.assertIsNotNone(space.start_single_job)
        self.assertIsNotNone(space.results_folder)


@unittest.skipUnless(HAS_NODESETS, "AMCM-Nodesets nicht vorhanden")
class AssetModelTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server, self.space, self.assets = await build_with_assets(
            ASSETS, "voll", 48403
        )
        self.di_idx = await self.server.get_namespace_index(
            "http://opcfoundation.org/UA/DI/"
        )

    async def test_loads_the_whole_dependency_chain(self):
        namespaces = await self.server.get_namespace_array()
        for uri in (
            "http://opcfoundation.org/UA/DI/",
            "http://opcfoundation.org/UA/Machinery/",
            "http://opcfoundation.org/UA/MachineVision/AMCM/",
        ):
            self.assertIn(uri, namespaces)

    async def test_uses_an_explicit_string_node_id(self):
        """Automatically assigned numeric IDs shift; clients subscribe to these nodes by fixed ID."""
        self.assertEqual(
            "VisionMachine.VisionAsset", self.assets.root.nodeid.Identifier
        )

    async def test_creates_no_placeholder_template_nodes(self):
        """`<ComputingDevice>` & Co. are template types, not instances; they must not
        appear in the running address space."""
        for folder in await self.assets.root.get_children():
            for child in await folder.get_children():
                name = (await child.read_browse_name()).Name
                self.assertFalse(name.startswith("<"), f"Platzhalter: {name}")

    async def test_creates_only_the_folders_it_fills(self):
        """Empty folders for WayEncoder or climate controls would be noise."""
        names = {(await c.read_browse_name()).Name for c in await self.assets.root.get_children()}
        self.assertIn("ComputingDevices", names)
        self.assertNotIn("WayEncoders", names)

    async def test_identifies_the_system(self):
        identification = await self.assets.root.get_child(f"{self.di_idx}:Identification")
        serial = await identification.get_child(f"{self.di_idx}:SerialNumber")
        self.assertEqual("vision-test-01", await serial.read_value())

    async def test_adds_the_configured_components(self):
        self.assertIsNotNone(self.assets.computing_device)
        self.assertIsNotNone(self.assets.image_sensor)
        self.assertIsNotNone(self.assets.lens)

    async def test_leaves_out_components_without_a_model(self):
        """An empty model field means 'not known' and must not create a phantom entry."""
        _, _, assets = await build_with_assets(AssetConfig(), "minimal", 48404)
        self.assertIsNotNone(assets.computing_device)
        self.assertIsNone(assets.image_sensor)
        self.assertIsNone(assets.lens)


@unittest.skipUnless(HAS_NODESETS, "AMCM-Nodesets nicht vorhanden")
class DeviceHealthNodeTest(unittest.IsolatedAsyncioTestCase):
    """The state part of Part 2: `Health` with `DeviceHealth`.

    What is checked here is the contract with the backend: address, namespace of
    the BrowseName, DataType and start value. A swapped namespace index raises no
    error, only a node that nobody finds via BrowsePath; hence the BrowseName tests.
    """

    async def asyncSetUp(self):
        self.server, self.space, self.assets = await build_with_assets(
            ASSETS, "voll", 48403
        )
        self.di_idx = await self.server.get_namespace_index(
            "http://opcfoundation.org/UA/DI/"
        )
        self.amcm_idx = await self.server.get_namespace_index(
            "http://opcfoundation.org/UA/MachineVision/AMCM/"
        )

    async def test_adds_a_health_block_to_the_root_and_the_image_sensor(self):
        self.assertEqual(2, len(self.assets.device_health))
        self.assertEqual(
            [
                "VisionMachine.VisionAsset.Health.DeviceHealth",
                "VisionMachine.VisionAsset.ImageSensor.Health.DeviceHealth",
            ],
            [node.nodeid.Identifier for node in self.assets.device_health],
        )

    async def test_health_is_an_add_in_not_a_component(self):
        """AMCM references `Health` via `HasAddIn`; asyncua's `instantiate()` always
        sets `HasComponent`. `includesubtypes=False` is needed because `HasAddIn` is
        itself a subtype of `HasComponent`."""
        add_ins = await self.assets.root.get_references(
            ua.NodeId(ua.ObjectIds.HasAddIn), direction=ua.BrowseDirection.Forward
        )
        components = await self.assets.root.get_references(
            ua.NodeId(ua.ObjectIds.HasComponent),
            includesubtypes=False,
            direction=ua.BrowseDirection.Forward,
        )
        self.assertIn("Health", [r.BrowseName.Name for r in add_ins])
        self.assertNotIn("Health", [r.BrowseName.Name for r in components])

    async def test_the_health_block_carries_the_di_interface(self):
        health = await self.assets.root.get_child(f"{self.amcm_idx}:Health")
        interfaces = await health.get_references(
            ua.NodeId(ua.ObjectIds.HasInterface), direction=ua.BrowseDirection.Forward
        )
        self.assertIn("IDeviceHealthType", [r.BrowseName.Name for r in interfaces])

    async def test_device_health_browse_name_lives_in_the_di_namespace(self):
        for node in self.assets.device_health:
            browse_name = await node.read_browse_name()
            self.assertEqual("DeviceHealth", browse_name.Name)
            self.assertEqual(self.di_idx, browse_name.NamespaceIndex)

    async def test_device_health_carries_the_di_data_type(self):
        for node in self.assets.device_health:
            self.assertEqual(
                node_id(DI_DEVICE_HEALTH_ENUMERATION, self.di_idx),
                await node.read_data_type(),
            )

    async def test_device_health_starts_as_check_function(self):
        """At build time the camera is not even open; NORMAL would be a claim that
        stands unchallenged until the first frame."""
        for node in self.assets.device_health:
            self.assertEqual(int(DeviceHealth.CHECK_FUNCTION), await node.read_value())

    async def test_device_health_accepts_an_int32_write(self):
        """Enumerations travel as Int32 over the wire; without the explicit start-value
        VariantType there would be BadTypeMismatch."""
        node = self.assets.device_health[0]
        try:
            await node.write_value(
                ua.Variant(int(DeviceHealth.FAILURE), ua.VariantType.Int32)
            )
            self.assertEqual(int(DeviceHealth.FAILURE), await node.read_value())
        finally:
            await node.write_value(
                ua.Variant(int(DeviceHealth.CHECK_FUNCTION), ua.VariantType.Int32)
            )

    async def test_the_health_block_carries_exactly_the_two_di_members(self):
        """DI's `IDeviceHealthType` has exactly two members, both served.

        The other children of `VisionHealthInfoType` are left out: `State`
        (SEMI E10), `Temperature` and `RemainingLifeTime` would have no source.
        """
        health = await self.assets.root.get_child(f"{self.amcm_idx}:Health")
        names = {(await c.read_browse_name()).Name for c in await health.get_children()}
        self.assertEqual({"DeviceHealth", "DeviceHealthAlarms"}, names)


@unittest.skipUnless(HAS_NODESETS, "AMCM-Nodesets nicht vorhanden")
class DeviceHealthAlarmsTest(unittest.IsolatedAsyncioTestCase):
    """The second part of `IDeviceHealthType`: real OPC UA alarms."""

    async def asyncSetUp(self):
        self.server, self.space, self.assets = await build_with_assets(
            ASSETS, "voll", 48403
        )
        self.di_idx = await self.server.get_namespace_index(
            "http://opcfoundation.org/UA/DI/"
        )
        self.amcm_idx = await self.server.get_namespace_index(
            "http://opcfoundation.org/UA/MachineVision/AMCM/"
        )

    async def test_serves_the_three_states_that_can_actually_occur(self):
        self.assertEqual(
            {DeviceHealth.FAILURE, DeviceHealth.CHECK_FUNCTION, DeviceHealth.OFF_SPEC},
            set(self.assets.health_alarms),
        )

    async def test_leaves_out_maintenance_required(self):
        """Without a wear counter it could never fire; an alarm that never comes is a
        promise we do not keep."""
        self.assertNotIn(DeviceHealth.MAINTENANCE_REQUIRED, self.assets.health_alarms)
        folder = await self._folder()
        names = {(await c.read_browse_name()).Name for c in await folder.get_children()}
        self.assertEqual({"FailureAlarm", "CheckFunctionAlarm", "OffSpecAlarm"}, names)

    async def test_the_folder_browse_name_lives_in_the_di_namespace(self):
        browse_name = await (await self._folder()).read_browse_name()
        self.assertEqual("DeviceHealthAlarms", browse_name.Name)
        self.assertEqual(self.di_idx, browse_name.NamespaceIndex)

    async def test_the_alarms_are_condition_objects(self):
        """They must carry the condition fields; otherwise they are not alarms."""
        alarm = self.assets.health_alarms[DeviceHealth.FAILURE]
        names = {(await c.read_browse_name()).Name for c in await alarm.get_children()}
        for field in ("ActiveState", "AckedState", "Retain", "Severity", "Message"):
            self.assertIn(field, names)

    async def test_only_the_root_carries_alarms(self):
        """Root and image sensor carry the same value; each alarm instance costs 36
        nodes, so a duplicate would be pure doubling."""
        sensor_health = await self.assets.image_sensor.get_child(
            f"{self.amcm_idx}:Health"
        )
        names = {
            (await c.read_browse_name()).Name for c in await sensor_health.get_children()
        }
        self.assertEqual({"DeviceHealth"}, names)

    async def _folder(self):
        health = await self.assets.root.get_child(f"{self.amcm_idx}:Health")
        return await health.get_child(f"{self.di_idx}:DeviceHealthAlarms")

    async def test_without_an_image_sensor_only_the_root_carries_health(self):
        """That is why the state also hangs on the root: without a known camera model
        there is no ImageSensor node."""
        _, _, assets = await build_with_assets(AssetConfig(), "minimal", 48404)
        self.assertEqual(1, len(assets.device_health))
        self.assertEqual(
            "VisionMachine.VisionAsset.Health.DeviceHealth",
            assets.device_health[0].nodeid.Identifier,
        )


if __name__ == "__main__":
    unittest.main()
