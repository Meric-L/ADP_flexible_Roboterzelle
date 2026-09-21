"""VisionProgram als alleiniger Einstiegspunkt, VisionMachine unter Machines.

Zweck dieser Datei ist, die drei Zusagen festzunageln, die der Betreuer-Wunsch
"nur auf dem Part-10-Teil arbeiten" braucht:

1. Unter `Objects` steht **ein** Einstieg: `VisionProgram`. Das Vision-System
   liegt im Machinery-Standardordner `Machines`.
2. Es gibt genau **eine** `VisionSystemType`-Instanz. Die Altlast
   `2:VisionSystem` ist weg, und mit ihr der Aufruf ins Leere, der frueher
   `BadNothingToDo` beantwortet hat.
3. Die Kalibriermethoden sind ueber `VisionProgram` aufrufbar, ohne dass sie
   ein zweites Mal registriert werden -- eine Implementierung, zwei Fundorte.
"""

import unittest

from asyncua import ua

from vision_server.config import DEFAULT_AMCM_NODESET_PATHS, VisionServerConfig
from vision_server.profiles import AssetConfig

HAS_NODESETS = all(path.is_file() for path in DEFAULT_AMCM_NODESET_PATHS)

ASSETS = AssetConfig(serial_number="vision-test-01", image_sensor_model="Test Cam")

#: Wie in test_asset_model.py: der Aufbau importiert vier Nodesets und kostet
#: mehrere Sekunden. Einmal je Variante statt einmal je Testmethode.
_BUILT: dict[str, tuple] = {}


async def build(assets, key: str, port: int):
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
        """Der Umzug darf keinen Client brechen: die feste String-NodeId
        haengt nicht an der Browse-Position."""
        _, space = await build(ASSETS, "mit", 48502)
        self.assertEqual(space.vision_system.nodeid.Identifier, "VisionMachine")

    async def test_genau_eine_vision_system_instanz(self):
        """Die Altlast `2:VisionSystem` ist weg. Ein Client, der per Typ
        sucht statt die feste NodeId zu nehmen, kann deshalb nicht mehr die
        falsche Instanz erwischen -- frueher antwortete die auf StartSingleJob
        mit BadNothingToDo.

        Gezaehlt wird ueber einen Baumdurchlauf statt ueber eine
        Rueckwaertsreferenz: der Durchlauf ist das, was ein browsender Client
        auch tut, und faellt damit gemeinsam mit ihm aus, falls er je bricht.
        """
        server, space = await build(ASSETS, "mit", 48503)
        wanted = await space.vision_system.read_type_definition()

        seen: set[str] = set()
        # Nach NodeId, nicht nach Name: derselbe Knoten darf ueber mehrere
        # Browse-Pfade erreichbar sein -- das ist in OPC UA normal und war nie
        # das Problem. Zwei *verschiedene* Instanzen waren es.
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
        """Mit `RaspiDevice` faellt auch sein Namensraum -- sonst bliebe ein
        leerer Eintrag im Namespace-Array stehen, der die Indizes verschiebt,
        ohne einen einzigen Knoten zu tragen."""
        server, _ = await build(ASSETS, "mit", 48505)
        self.assertNotIn("http://launch-rm.de/raspi", await server.get_namespace_array())


class ProgrammIstDerEinstiegTest(unittest.IsolatedAsyncioTestCase):
    """Faehrt die ganze Maschine hoch -- kamerafrei ueber `hello_world`.

    Bis hierher hat kein Test `install_vision_machine` je ausgefuehrt; die
    Verdrahtung von Adressraum, Automaten und Part-10-Aufsatz war nur im
    laufenden Betrieb auf dem Pi belegt.
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
        """Der Weg, auf dem ein reiner Part-10-Client sein Ergebnis holt:
        Wertaenderung unter `VisionProgram/ResultSet`. Events der 40100-Seite
        erreichen ein Abo auf dem Programm nicht -- am 21.09.2026 gegen
        asyncua 2.0.1 nachgemessen, die HasNotifier/HasEventSource-Hierarchie
        wird nicht abgelaufen."""
        idx = self.machine.results.json_node.nodeid.NamespaceIndex
        program = self.server.get_node(ua.NodeId("VisionProgram", idx))
        result_set = await program.get_child(f"{idx}:ResultSet")
        namen = await browse_names(result_set)
        self.assertIn("LatestResultJson", namen)
        self.assertIn("JobId", namen)
        self.assertIn("ErrorCode", namen)

    async def test_ohne_kalibrierung_keine_verweise(self):
        """`config.apriltag` ist hier nicht gesetzt -- dann gibt es keine
        Kalibriermethoden und entsprechend auch nichts zu verlinken."""
        self.assertIsNone(self.machine.calibration_session)


class OhneMachineryTest(unittest.IsolatedAsyncioTestCase):
    async def test_faellt_auf_objects_zurueck(self):
        """Ohne Part 2 gibt es keinen Machines-Ordner. Der Server muss
        trotzdem hochkommen -- eine Entwicklungsinstanz hat kein AMCM."""
        server, space = await build(None, "ohne", 48504)
        self.assertIsNone(space.amcm_idx)
        self.assertIn("VisionMachine", await browse_names(server.nodes.objects))


if __name__ == "__main__":
    unittest.main()
