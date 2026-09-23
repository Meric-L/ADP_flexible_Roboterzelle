"""Charakterisierung der OPC-UA-Methoden, so wie ein Client sie sieht.

Bis hierher war keiner der Methoden-Handler getestet: weder die 40100-Seite
in `runner.install_vision_machine` (StartSingleJob, Stop, StartContinuous,
Abort, Halt, Reset, die vier Kalibriermethoden) noch der Part-10-Aufsatz
(`VisionProgram` auf `ua_program.Program`). Diese Tests sind **gegen den
unveraenderten Code** geschrieben und nageln fest, was nach
`doc/vision-server-interface.md` §12/§13.1 und
`doc/part10-programm-schnittstelle.md` §4/§5 nach aussen zugesagt ist:

* feste NodeIds der Methoden,
* Rueckgabewerte samt Variant-Typ (`Error` ist Int32, `JobId` String),
* welche Methode in welchem Zustand `BadInvalidState` liefert,
* die Sperre zwischen Kalibrier-Session und Jobs.

Ein Umbau der Handler darf keinen davon brechen.

Aufgerufen wird ueber die interne Session des Servers -- derselbe
Methodendienst wie fuer einen entfernten Client, nur ohne TCP. Kamerafrei:
nur `hello_world`, die Kalibrier-Session ist eine Attrappe.
"""

import asyncio
import json
import unittest
from dataclasses import replace
from unittest import mock

from asyncua import Server, ua

from vision_server.config import VisionServerConfig
from vision_server.detection.base import DetectionSource
from vision_server.errors import VisionErrorCode
from vision_server.profiles import AprilTagProfileConfig

#: Ohne `apriltag`: dessen Quelle braucht eine Kamera und bliebe zu, der
#: Automat dann in Preoperational.
RECIPES = (("", "hello_world"), ("hello-world", "hello_world"))

AUTO = "VisionMachine.VisionStateMachine.AutomaticModeStateMachine"


def base_config(port: int, **overrides) -> VisionServerConfig:
    return replace(
        VisionServerConfig(endpoint=f"opc.tcp://127.0.0.1:{port}/test/"),
        recipe_profiles=RECIPES,
        detection_latency=0.2,
        continuous_interval_s=0.05,
        **overrides,
    )


class FakeCalibrationSession:
    """Steht fuer `CalibrationSession`, ohne Kamera und ohne Rechnen."""

    def __init__(self) -> None:
        self.running = False
        self.capture_found = True
        self.on_calibrated = None

    def start(self) -> None:
        self.running = True

    async def capture(self) -> bool:
        return self.capture_found

    async def finish(self):
        self.running = False
        return VisionErrorCode.OK, {"rms": 0.3, "samples": 15}

    async def abort(self) -> None:
        self.running = False

    def set_on_calibrated(self, callback) -> None:
        self.on_calibrated = callback


class FailingSource(DetectionSource):
    """Quelle, deren `open()` scheitert -- wie eine fehlende Kamera."""

    profile_id = "hello_world"

    async def open(self) -> None:
        raise RuntimeError("keine Kamera")

    async def acquire_and_detect(self, request):  # pragma: no cover - nie erreicht
        return []


class MachineTestCase(unittest.IsolatedAsyncioTestCase):
    """Baut je Test eine ganze Maschine; Aufrufe ueber den Methodendienst."""

    port = 0
    config_overrides: dict = {}

    async def asyncSetUp(self):
        from vision_server.address_space import configure_server
        from vision_server.runner import install_vision_machine

        # IsolatedAsyncioTestCase schaltet den Debug-Modus des Loops ein; der
        # verdreifacht die Aufbauzeit der Maschine und bringt hier nichts.
        asyncio.get_running_loop().set_debug(False)
        self.config = base_config(self.port, **self.config_overrides)
        self.server = Server()
        await configure_server(self.server, self.config)
        self.machine = await self.install(install_vision_machine)
        self.idx = self.machine.results.json_node.nodeid.NamespaceIndex

    async def install(self, install_vision_machine):
        return await install_vision_machine(self.server, self.config)

    async def asyncTearDown(self):
        await self.machine.aclose()

    # ------------------------------------------------------------ Helfer

    def nid(self, identifier: str) -> ua.NodeId:
        return ua.NodeId(identifier, self.idx)

    async def call(self, obj: str, method: str, *args) -> ua.CallMethodResult:
        """Ruft eine Methode und liefert das rohe Ergebnis mit Variants."""
        request = ua.CallMethodRequest()
        request.ObjectId = self.nid(obj)
        request.MethodId = self.nid(method)
        request.InputArguments = [
            arg if isinstance(arg, ua.Variant) else ua.Variant(arg) for arg in args
        ]
        return (await self.server.iserver.isession.call([request]))[0]

    async def call_40100(self, name: str, *args) -> ua.CallMethodResult:
        return await self.call(
            "VisionMachine", f"{AUTO}.{name}" if "." not in name else name, *args
        )

    async def start_job(self, name="StartSingleJob", recipe="hello-world"):
        return await self.call_40100(
            name, "", "", recipe, "", ua.Variant([], ua.VariantType.String)
        )

    async def cause_call(self, method: str) -> ua.CallMethodResult:
        return await self.call(
            "VisionMachine",
            method,
            ua.Variant(0, ua.VariantType.Int32),
            ua.Variant("", ua.VariantType.String),
        )

    def assert_outputs(self, result, *expected):
        """Prueft Status Good sowie Wert **und** Variant-Typ jeder Ausgabe."""
        self.assertTrue(result.StatusCode.is_good(), result.StatusCode)
        self.assertEqual(
            [(v.Value, v.VariantType) for v in result.OutputArguments], list(expected)
        )

    def assert_error(self, result, code: VisionErrorCode):
        """Letzte Ausgabe ist `Error` als Int32 mit dem erwarteten Code."""
        self.assertTrue(result.StatusCode.is_good(), result.StatusCode)
        error = result.OutputArguments[-1]
        self.assertEqual((error.Value, error.VariantType), (int(code), ua.VariantType.Int32))

    async def wait_until(self, predicate, timeout: float = 5.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                self.fail("Bedingung nicht rechtzeitig erreicht")
            await asyncio.sleep(0.02)

    async def wait_idle(self):
        await self.wait_until(lambda: not self.machine.jobs.busy)


# ====================================================================== 40100


class VisionMachineMethodenTest(MachineTestCase):
    port = 48520

    async def result_payload(self) -> dict:
        return json.loads(await self.machine.results.json_node.read_value())

    async def test_nodeids_einzeljob_stop_und_busy(self):
        for suffix in (
            f"{AUTO}.StartSingleJob",
            f"{AUTO}.Stop",
            f"{AUTO}.StartContinuous",
            f"{AUTO}.Abort",
            "VisionMachine.VisionStateMachine.Halt",
            "VisionMachine.VisionStateMachine.Reset",
        ):
            node = self.server.get_node(self.nid(suffix))
            self.assertEqual(await node.read_node_class(), ua.NodeClass.Method, suffix)

        self.assertEqual(self.machine.states.state_names(), ("Operational", "Ready"))
        # Stop ohne laufenden Job: OK, kein Zustandsguard.
        self.assert_outputs(
            await self.cause_call(f"{AUTO}.Stop"), (0, ua.VariantType.Int32)
        )
        self.assert_outputs(
            await self.start_job(recipe="gibt-es-nicht"),
            ("", ua.VariantType.String),
            (int(VisionErrorCode.UNKNOWN_RECIPE), ua.VariantType.Int32),
        )
        self.assertFalse(self.machine.jobs.busy)

        result = await self.start_job()
        self.assert_outputs(
            result, ("job-000001", ua.VariantType.String), (0, ua.VariantType.Int32)
        )
        # Zweiter Start waehrend der erste laeuft: BUSY, leere JobId.
        self.assert_outputs(
            await self.start_job(),
            ("", ua.VariantType.String),
            (int(VisionErrorCode.BUSY), ua.VariantType.Int32),
        )
        self.assert_outputs(
            await self.start_job("StartContinuous"),
            ("", ua.VariantType.String),
            (int(VisionErrorCode.BUSY), ua.VariantType.Int32),
        )
        await self.wait_idle()
        self.assertEqual(self.machine.states.state_names(), ("Operational", "Ready"))
        payload = await self.result_payload()
        self.assertEqual((payload["jobId"], payload["resultState"]), ("job-000001", 0))

        # Abbruch mitten im Job: Stop liefert OK, Ergebnis traegt CANCELLED.
        self.assert_error(await self.start_job(), VisionErrorCode.OK)
        await asyncio.sleep(0.05)
        self.assert_outputs(
            await self.cause_call(f"{AUTO}.Stop"), (0, ua.VariantType.Int32)
        )
        self.assertFalse(self.machine.jobs.busy)
        self.assertEqual(self.machine.states.state_names(), ("Operational", "Ready"))
        payload = await self.result_payload()
        self.assertEqual(
            (payload["jobId"], payload["resultState"]),
            ("job-000002", int(VisionErrorCode.CANCELLED)),
        )

    async def test_dauerbetrieb_abort_halt_reset(self):
        result = await self.start_job("StartContinuous")
        self.assert_outputs(
            result, ("job-000001", ua.VariantType.String), (0, ua.VariantType.Int32)
        )
        await asyncio.sleep(0.1)
        self.assertEqual(
            self.machine.states.state_names(), ("Operational", "ContinuousExecution")
        )
        self.assert_outputs(
            await self.cause_call(f"{AUTO}.Abort"), (0, ua.VariantType.Int32)
        )
        self.assertFalse(self.machine.jobs.busy)
        self.assertEqual(self.machine.states.state_names(), ("Operational", "Ready"))
        payload = await self.result_payload()
        self.assertEqual(payload["resultState"], int(VisionErrorCode.CANCELLED))
        self.assertTrue(payload["jobId"].startswith("job-000001"))

        self.assert_error(await self.start_job("StartContinuous"), VisionErrorCode.OK)
        await asyncio.sleep(0.05)
        # Halt beendet den laufenden Dauerbetrieb und haelt an.
        self.assert_outputs(
            await self.cause_call("VisionMachine.VisionStateMachine.Halt"),
            (0, ua.VariantType.Int32),
        )
        self.assertFalse(self.machine.jobs.busy)
        self.assertEqual(self.machine.states.state_names()[0], "Halted")
        for method in ("StartSingleJob", "StartContinuous"):
            self.assert_outputs(
                await self.start_job(method),
                ("", ua.VariantType.String),
                (int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
            )
        self.assert_outputs(
            await self.cause_call("VisionMachine.VisionStateMachine.Reset"),
            (0, ua.VariantType.Int32),
        )
        self.assertEqual(self.machine.states.state_names(), ("Operational", "Ready"))
        self.assert_error(await self.start_job(), VisionErrorCode.OK)
        await self.wait_idle()


# ================================================================ Kalibrierung


class KalibrierMethodenTest(MachineTestCase):
    port = 48521
    config_overrides = {"apriltag": AprilTagProfileConfig()}

    async def install(self, install_vision_machine):
        self.session = FakeCalibrationSession()
        with mock.patch(
            "vision_server.runner._build_calibration_session", return_value=self.session
        ):
            return await install_vision_machine(self.server, self.config)

    async def test_nodeids_signaturen_und_ohne_session(self):
        expected = {
            "StartCalibration": [ua.NodeId(ua.ObjectIds.Int32)],
            "CaptureCalibrationSample": [ua.NodeId(ua.ObjectIds.Int32)],
            "FinishCalibration": [
                ua.NodeId(ua.ObjectIds.String),
                ua.NodeId(ua.ObjectIds.Int32),
            ],
            "AbortCalibration": [ua.NodeId(ua.ObjectIds.Int32)],
        }
        vision = self.server.get_node(self.nid("VisionMachine"))
        vision_children = {c.nodeid for c in await vision.get_children()}
        program = self.server.get_node(self.nid("VisionProgram"))
        program_children = {c.nodeid for c in await program.get_children()}
        for name, outputs in expected.items():
            node = self.server.get_node(self.nid(f"VisionMachine.{name}"))
            self.assertEqual(await node.read_node_class(), ua.NodeClass.Method)
            self.assertEqual(
                await node.read_browse_name(), ua.QualifiedName(name, self.idx)
            )
            out = await (await node.get_child("0:OutputArguments")).read_value()
            self.assertEqual([arg.DataType for arg in out], outputs, name)
            self.assertIn(node.nodeid, vision_children, name)
            # Ein Knoten, zwei Fundorte (part10 §4).
            self.assertIn(node.nodeid, program_children, name)
        self.assertIs(self.machine.calibration_session, self.session)
        # Die Live-Uebernahme nach erfolgreicher Kalibrierung ist verdrahtet.
        self.assertIsNotNone(self.session.on_calibrated)

        # Ohne laufende Session: INVALID_STATE, Summary bleibt gueltiges JSON.
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.CaptureCalibrationSample"),
            (int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
        )
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.FinishCalibration"),
            ('{"message": "keine Session aktiv"}', ua.VariantType.String),
            (int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
        )
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.AbortCalibration"),
            (int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
        )

    async def test_session_sperrt_jobs_und_umgekehrt(self):
        # Waehrend ein Job laeuft: StartCalibration BUSY.
        self.assert_error(await self.start_job(), VisionErrorCode.OK)
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.StartCalibration"),
            (int(VisionErrorCode.BUSY), ua.VariantType.Int32),
        )
        await self.wait_idle()

        # Aufruf ueber das Programm als ObjectId -- derselbe Handler.
        self.assert_outputs(
            await self.call("VisionProgram", "VisionMachine.StartCalibration"),
            (0, ua.VariantType.Int32),
        )
        self.assertTrue(self.session.running)
        # Zweite Session: BUSY.
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.StartCalibration"),
            (int(VisionErrorCode.BUSY), ua.VariantType.Int32),
        )
        # Jobs waehrend der Session: BUSY mit leerer JobId (interface §12.7).
        for method in ("StartSingleJob", "StartContinuous"):
            self.assert_outputs(
                await self.start_job(method),
                ("", ua.VariantType.String),
                (int(VisionErrorCode.BUSY), ua.VariantType.Int32),
            )
        self.assertFalse(self.machine.jobs.busy)

        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.CaptureCalibrationSample"),
            (0, ua.VariantType.Int32),
        )
        self.session.capture_found = False
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.CaptureCalibrationSample"),
            (int(VisionErrorCode.DETECTION_FAILED), ua.VariantType.Int32),
        )
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.FinishCalibration"),
            ('{"rms": 0.3, "samples": 15}', ua.VariantType.String),
            (0, ua.VariantType.Int32),
        )
        self.assertFalse(self.session.running)

        # Neue Session, dann Abort.
        self.assert_error(
            await self.call("VisionMachine", "VisionMachine.StartCalibration"),
            VisionErrorCode.OK,
        )
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.AbortCalibration"),
            (0, ua.VariantType.Int32),
        )
        self.assertFalse(self.session.running)
        self.assert_error(await self.start_job(), VisionErrorCode.OK)
        await self.wait_idle()

        # Ausserhalb von Ready: INVALID_STATE, keine Session.
        self.assert_error(
            await self.cause_call("VisionMachine.VisionStateMachine.Halt"),
            VisionErrorCode.OK,
        )
        self.assert_outputs(
            await self.call("VisionMachine", "VisionMachine.StartCalibration"),
            (int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
        )
        self.assertFalse(self.session.running)


# ==================================================================== Part 10


class VisionProgramTest(MachineTestCase):
    port = 48522

    async def program_state(self) -> str:
        program = self.server.get_node(self.nid("VisionProgram"))
        current = await program.get_child("0:CurrentState")
        return (await current.read_value()).Text

    async def wait_program(self, name: str):
        deadline = asyncio.get_running_loop().time() + 5.0
        while await self.program_state() != name:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"Programm nicht nach {name} (steht auf {await self.program_state()})")
            await asyncio.sleep(0.02)

    async def result(self, name: str):
        node = self.server.get_node(self.nid(f"VisionProgram.ResultSet.{name}"))
        return await node.read_value()

    async def write_parameter(self, name: str, value, vtype):
        node = self.server.get_node(self.nid(f"VisionProgram.ParameterSet.{name}"))
        await node.write_value(ua.Variant(value, vtype))

    async def program_call(self, name: str) -> ua.CallMethodResult:
        return await self.call("VisionProgram", f"VisionProgram.{name}")

    def assert_invalid_state(self, result):
        self.assertEqual(result.StatusCode.value, ua.StatusCodes.BadInvalidState)

    async def executable(self) -> dict[str, bool]:
        values = {}
        for name in ("Start", "Halt", "Reset", "Suspend", "Resume"):
            node = self.server.get_node(self.nid(f"VisionProgram.{name}"))
            values[name] = (
                await node.read_attribute(ua.AttributeIds.Executable)
            ).Value.Value
        return values

    async def test_nodeids_ready_und_ablehnungen(self):
        for name in ("Start", "Halt", "Reset", "Suspend", "Resume"):
            node = self.server.get_node(self.nid(f"VisionProgram.{name}"))
            self.assertEqual(await node.read_node_class(), ua.NodeClass.Method, name)
        for name in ("ParameterSet.RecipeId", "ParameterSet.Continuous",
                     "ResultSet.JobId", "ResultSet.ErrorCode", "ResultSet.ExecutionMode"):
            node = self.server.get_node(self.nid(f"VisionProgram.{name}"))
            self.assertEqual(await node.read_node_class(), ua.NodeClass.Variable, name)
        program = self.server.get_node(self.nid("VisionProgram"))
        self.assertEqual(
            await program.read_type_definition(),
            ua.NodeId(ua.ObjectIds.ProgramStateMachineType),
        )

        self.assertEqual(await self.program_state(), "Ready")
        self.assertEqual(
            await self.executable(),
            {"Start": True, "Halt": True, "Reset": False, "Suspend": False, "Resume": False},
        )
        self.assert_invalid_state(await self.program_call("Reset"))
        self.assert_invalid_state(await self.program_call("Suspend"))
        self.assert_invalid_state(await self.program_call("Resume"))
        self.assertEqual(await self.program_state(), "Ready")

        # Auch ueber den gewoehnlichen `call_method`-Weg als Ausnahme sichtbar.
        with self.assertRaises(ua.UaStatusCodeError) as caught:
            await program.call_method(self.nid("VisionProgram.Reset"))
        self.assertEqual(caught.exception.code, ua.StatusCodes.BadInvalidState)

        # Abgelehnter Start (unbekanntes Rezept): Status Good, Zustand bleibt.
        await self.write_parameter("RecipeId", "gibt-es-nicht", ua.VariantType.String)
        self.assertTrue((await self.program_call("Start")).StatusCode.is_good())
        await self.wait_until(lambda: not self.machine.program._lock.locked())
        self.assertEqual(await self.program_state(), "Ready")
        self.assertEqual(await self.result("ErrorCode"), int(VisionErrorCode.UNKNOWN_RECIPE))
        self.assertEqual(await self.result("ExecutionMode"), "idle")

        # Ein ueber 40100 gestarteter Job laesst das Programm auf Ready.
        self.assert_error(await self.start_job(), VisionErrorCode.OK)
        await self.wait_idle()
        await asyncio.sleep(0.05)
        self.assertEqual(await self.program_state(), "Ready")

    async def test_einzeljob_ueber_das_programm(self):
        await self.write_parameter("RecipeId", "hello-world", ua.VariantType.String)
        result = await self.program_call("Start")
        self.assertTrue(result.StatusCode.is_good())
        self.assertEqual(result.OutputArguments, [])
        await self.wait_program("Running")
        self.assertEqual(await self.result("ExecutionMode"), "single")
        self.assertEqual(
            await self.executable(),
            {"Start": False, "Halt": True, "Reset": False, "Suspend": True, "Resume": False},
        )
        # Start im Zustand Running: BadInvalidState.
        self.assert_invalid_state(await self.program_call("Start"))
        self.assert_invalid_state(await self.program_call("Reset"))
        self.assert_invalid_state(await self.program_call("Resume"))
        # Suspend wird angenommen, aber abgelehnt: Zustand bleibt.
        self.assertTrue((await self.program_call("Suspend")).StatusCode.is_good())
        await self.wait_program("Ready")
        self.assertEqual(await self.result("JobId"), "job-000001")
        self.assertEqual(await self.result("ErrorCode"), 0)
        self.assertEqual(await self.result("ExecutionMode"), "idle")

    async def test_dauerbetrieb_halt_reset(self):
        await self.write_parameter("Continuous", True, ua.VariantType.Boolean)
        self.assertTrue((await self.program_call("Start")).StatusCode.is_good())
        await self.wait_program("Running")
        self.assertEqual(await self.result("ExecutionMode"), "continuous")
        await asyncio.sleep(0.1)
        self.assertEqual(await self.program_state(), "Running")

        self.assertTrue((await self.program_call("Halt")).StatusCode.is_good())
        await self.wait_program("Halted")
        self.assertFalse(self.machine.jobs.busy)
        self.assertEqual(await self.result("ErrorCode"), 0)
        self.assertEqual(await self.result("ExecutionMode"), "idle")
        self.assertEqual(
            await self.executable(),
            {"Start": False, "Halt": False, "Reset": True, "Suspend": False, "Resume": False},
        )
        for name in ("Start", "Halt", "Suspend", "Resume"):
            self.assert_invalid_state(await self.program_call(name))

        self.assertTrue((await self.program_call("Reset")).StatusCode.is_good())
        await self.wait_program("Ready")


class NichtBetriebsbereitTest(MachineTestCase):
    """Quelle geht nicht auf: 40100 bleibt Preoperational, Programm Halted."""

    port = 48523

    async def install(self, install_vision_machine):
        with mock.patch(
            "vision_server.runner.build_detection_sources",
            return_value={"hello_world": FailingSource()},
        ):
            return await install_vision_machine(self.server, self.config)

    async def test_halted_und_invalid_state(self):
        program = self.server.get_node(self.nid("VisionProgram"))
        current = await program.get_child("0:CurrentState")
        self.assertEqual((await current.read_value()).Text, "Halted")
        last = await program.get_child("0:LastTransition")
        self.assertEqual((await last.read_value()).Text, "ReadyToHalted")
        self.assertFalse(self.machine.states.is_ready())
        self.assert_outputs(
            await self.start_job(),
            ("", ua.VariantType.String),
            (int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
        )
        result = await self.call("VisionProgram", "VisionProgram.Start")
        self.assertEqual(result.StatusCode.value, ua.StatusCodes.BadInvalidState)


if __name__ == "__main__":
    unittest.main()
