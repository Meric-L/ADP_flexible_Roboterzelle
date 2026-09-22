"""Die Anlagensicht nach OPC 40100-2 (AMCM).

Braucht die Nodesets unter src/vision_server/nodesets/ und importiert sie wirklich --
anders laesst sich nicht pruefen, ob die Abhaengigkeitskette DI -> Machinery ->
AMCM gegen asyncuas Basis-Adressraum aufgeht. Genau daran ist die neueste
DI-Version gescheitert, siehe src/vision_server/nodesets/README.md.
"""

import unittest

from asyncua import ua

from vision_server.config import DEFAULT_AMCM_NODESET_PATHS, VisionServerConfig
from vision_server.nodeset_ids import (
    DI_DEVICE_HEALTH_ENUMERATION,
    DeviceHealth,
    node_id,
)
from vision_server.profiles import AssetConfig

HAS_NODESETS = all(path.is_file() for path in DEFAULT_AMCM_NODESET_PATHS)

ASSETS = AssetConfig(
    serial_number="vision-test-01",
    image_sensor_model="Test Camera Module",
    lens_model="Test Lens 4.74mm",
)


#: Der Aufbau kostet mehrere Sekunden -- vier Nodeset-Importe und rund 700
#: instanziierte Knoten. Einmal je Variante, nicht einmal je Testmethode:
#: unmemoisiert dauerte diese Datei allein zwei Minuten.
_BUILT: dict[str, tuple] = {}


async def build(assets: AssetConfig | None, key: str, port: int):
    from asyncua import Server

    from vision_server.address_space import attach_vision_system, configure_server

    if key in _BUILT:
        return _BUILT[key]
    config = VisionServerConfig(
        endpoint=f"opc.tcp://127.0.0.1:{port}/test/", assets=assets
    )
    server = Server()
    await configure_server(server, config)
    _BUILT[key] = (server, await attach_vision_system(server, config))
    return _BUILT[key]


async def build_with_assets(assets: AssetConfig, key: str, port: int):
    """Adressraum samt aufgebauter Anlagensicht, ebenfalls memoisiert."""
    from vision_server.asset_model import attach_asset_model

    cached = _BUILT.get(f"{key}:assets")
    if cached is not None:
        return cached
    server, space = await build(assets, key, port)
    _BUILT[f"{key}:assets"] = (server, space, await attach_asset_model(space, assets))
    return _BUILT[f"{key}:assets"]


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
        """Automatisch vergebene numerische Ids verschieben sich; Clients
        abonnieren diese Knoten aber fest."""
        self.assertEqual(
            "VisionMachine.VisionAsset", self.assets.root.nodeid.Identifier
        )

    async def test_creates_no_placeholder_template_nodes(self):
        """`<ComputingDevice>` & Co. sind Vorlagen des Typs, keine Instanzen --
        sie duerfen im laufenden Adressraum nicht auftauchen."""
        for folder in await self.assets.root.get_children():
            for child in await folder.get_children():
                name = (await child.read_browse_name()).Name
                self.assertFalse(name.startswith("<"), f"Platzhalter: {name}")

    async def test_creates_only_the_folders_it_fills(self):
        """Leere Ordner fuer WayEncoder oder Klimaregler waeren Rauschen."""
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
        """Ein leeres Modellfeld heisst 'nicht bekannt' und darf keinen
        Phantomeintrag erzeugen."""
        _, _, assets = await build_with_assets(AssetConfig(), "minimal", 48404)
        self.assertIsNotNone(assets.computing_device)
        self.assertIsNone(assets.image_sensor)
        self.assertIsNone(assets.lens)


@unittest.skipUnless(HAS_NODESETS, "AMCM-Nodesets nicht vorhanden")
class DeviceHealthNodeTest(unittest.IsolatedAsyncioTestCase):
    """Der Zustandsteil von Part 2 -- `Health` mit `DeviceHealth`.

    Was hier geprueft wird, ist der Vertrag gegenueber dem Backend: Adresse,
    Namensraum des BrowseName, DataType und Startwert. Ein vertauschter
    Namensraumindex erzeugt keinen Fehler, nur einen Knoten, den niemand per
    BrowsePath findet -- deshalb die BrowseName-Tests.
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
        """AMCM referenziert `Health` per `HasAddIn`; asyncuas `instantiate()`
        setzt aber immer `HasComponent`. `includesubtypes=False` ist noetig,
        weil `HasAddIn` selbst ein Subtyp von `HasComponent` ist."""
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
        """Beim Aufbau ist die Kamera nicht einmal geoeffnet -- NORMAL waere
        eine Behauptung, die bis zum ersten Frame unwidersprochen stuende."""
        for node in self.assets.device_health:
            self.assertEqual(int(DeviceHealth.CHECK_FUNCTION), await node.read_value())

    async def test_device_health_accepts_an_int32_write(self):
        """Enumerationen gehen als Int32 ueber die Leitung; ohne den
        ausdruecklichen Startwert-VariantType gaebe es BadTypeMismatch."""
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

    async def test_the_health_block_stays_empty_apart_from_device_health(self):
        """Alle anderen Kinder von `VisionHealthInfoType` sind optional und
        haetten keine Quelle: DeviceHealthAlarms, State (SEMI E10),
        Temperature, RemainingLifeTime."""
        health = await self.assets.root.get_child(f"{self.amcm_idx}:Health")
        names = {(await c.read_browse_name()).Name for c in await health.get_children()}
        self.assertEqual({"DeviceHealth"}, names)

    async def test_without_an_image_sensor_only_the_root_carries_health(self):
        """Genau deshalb haengt der Zustand auch an der Wurzel: ohne bekanntes
        Kameramodell gibt es keinen ImageSensor-Knoten."""
        _, _, assets = await build_with_assets(AssetConfig(), "minimal", 48404)
        self.assertEqual(1, len(assets.device_health))
        self.assertEqual(
            "VisionMachine.VisionAsset.Health.DeviceHealth",
            assets.device_health[0].nodeid.Identifier,
        )


if __name__ == "__main__":
    unittest.main()
