"""`VisionProgram` as the only entry point, with `VisionMachine` under Machines.

Pins three guarantees: `Objects` holds exactly one entry point, `VisionProgram`,
with the vision system in the Machinery `Machines` folder; there is exactly one
`VisionSystemType` instance, so a client searching by type cannot hit a stale
duplicate; and the calibration methods are callable through `VisionProgram`
without being registered a second time.
"""

import unittest

from asyncua import ua

from tests._support import HAS_NODESETS, AddressSpaceCache
from vision_server.config import VisionServerConfig
from vision_server.profiles import AssetConfig

ASSETS = AssetConfig(serial_number="vision-test-01", image_sensor_model="Test Cam")

#: As in test_asset_model.py: building imports four nodesets and takes several
#: seconds. Once per variant instead of once per test method.
build = AddressSpaceCache().build


async def browse_names(node) -> set[str]:
    return {(await child.read_browse_name()).Name for child in await node.get_children()}


@unittest.skipUnless(HAS_NODESETS, "AMCM-Nodesets nicht vorhanden")
class VisionMachineUnterMachinesTest(unittest.IsolatedAsyncioTestCase):
    async def test_haengt_im_machinery_ordner_statt_unter_objects(self):
        server, space = await build(ASSETS, "mit", 48501)
        self.assertNotIn("VisionMachine", await browse_names(server.nodes.objects))

        machinery_idx = await server.get_namespace_index(
            "http://opcfoundation.org/UA/Machinery/"
        )
        machines = await server.nodes.objects.get_child(f"{machinery_idx}:Machines")
        self.assertIn("VisionMachine", await browse_names(machines))

    async def test_nodeid_bleibt_unveraendert(self):
        """The move must not break a client: the fixed string NodeId does not
        depend on the browse position."""
        _, space = await build(ASSETS, "mit", 48502)
        self.assertEqual(space.vision_system.nodeid.Identifier, "VisionMachine")

    async def test_genau_eine_vision_system_instanz(self):
        """The old `2:VisionSystem` instance is gone. A client that searches by type
        instead of using the fixed NodeId therefore cannot hit the wrong instance;
        the old one answered StartSingleJob with BadNothingToDo.

        Counted by a tree walk instead of a back reference: the walk is what a
        browsing client does too, so it fails together with that client if it breaks.
        """
        server, space = await build(ASSETS, "mit", 48503)
        wanted = await space.vision_system.read_type_definition()

        seen: set[str] = set()
        # By NodeId, not by name: the same node may be reachable over several browse
        # paths, which is normal in OPC UA. Two *different* instances would be the problem.
        found: set[str] = set()

        async def walk(node, depth: int) -> None:
            key = node.nodeid.to_string()
            if depth > 3 or key in seen:
                return
            seen.add(key)
            for child in await node.get_children():
                try:
                    if await child.read_type_definition() == wanted:
                        found.add(child.nodeid.to_string())
                except Exception:
                    pass
                await walk(child, depth + 1)

        await walk(server.nodes.objects, 0)
        self.assertEqual(len(found), 1, f"gefunden: {sorted(found)}")
        self.assertTrue(
            next(iter(found)).endswith("s=VisionMachine"), f"gefunden: {found}"
        )

    async def test_raspi_namensraum_ist_weg(self):
        """With `RaspiDevice` its namespace goes too; otherwise an empty entry would
        stay in the namespace array, shifting the indices without carrying a node."""
        server, _ = await build(ASSETS, "mit", 48505)
        self.assertNotIn("http://launch-rm.de/raspi", await server.get_namespace_array())


class ProgrammIstDerEinstiegTest(unittest.IsolatedAsyncioTestCase):
    """Brings up the whole machine, without a camera.

    No other test has run `install_vision_machine` so far; the wiring of address
    space, state machine and the part-10 layer was only proven in live operation
    on the Pi.
    """

    async def asyncSetUp(self):
        from asyncua import Server

        from vision_server.address_space import configure_server
        from vision_server.runner import install_vision_machine

        self.config = VisionServerConfig(endpoint="opc.tcp://127.0.0.1:48510/test/")
        self.server = Server()
        await configure_server(self.server, self.config)
        self.machine = await install_vision_machine(self.server, self.config)

    async def asyncTearDown(self):
        await self.machine.aclose()

    async def test_unter_objects_steht_das_programm(self):
        namen = await browse_names(self.server.nodes.objects)
        self.assertIn("VisionProgram", namen)

    async def test_ergebnis_json_haengt_unter_dem_resultset(self):
        """The path a pure part-10 client takes to get its result: a value change
        under `VisionProgram/ResultSet`. Events of the 40100 side do not reach a
        subscription on the program; measured against asyncua 2.0.1, the
        HasNotifier/HasEventSource hierarchy is not followed."""
        idx = self.machine.results.json_node.nodeid.NamespaceIndex
        program = self.server.get_node(ua.NodeId("VisionProgram", idx))
        result_set = await program.get_child(f"{idx}:ResultSet")
        namen = await browse_names(result_set)
        self.assertIn("LatestResultJson", namen)
        self.assertIn("JobId", namen)
        self.assertIn("ErrorCode", namen)

    async def test_ohne_kalibrierung_keine_verweise(self):
        """`config.apriltag` is not set here, so there are no calibration methods
        and nothing to link."""
        self.assertIsNone(self.machine.calibration_session)


class OhneMachineryTest(unittest.IsolatedAsyncioTestCase):
    async def test_faellt_auf_objects_zurueck(self):
        """Without Part 2 there is no Machines folder. The server must still come
        up; a development instance has no AMCM."""
        server, space = await build(None, "ohne", 48504)
        self.assertIsNone(space.amcm_idx)
        self.assertIn("VisionMachine", await browse_names(server.nodes.objects))


if __name__ == "__main__":
    unittest.main()
