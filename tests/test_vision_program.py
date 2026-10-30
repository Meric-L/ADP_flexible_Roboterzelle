"""Part-10-Programm ueber OPC UA: `VisionProgram` und `ua_program.Program`.

Charakterisiert, was ein generischer Client sieht (Vorlage:
`test_ua_methoden.py` aus `apriltag-refactoring`, Commit dfcfa4d, auf den
heutigen Code zugeschnitten):

* feste NodeIds von Methoden, `ParameterSet` und `ResultSet`,
* Start/Halt/Reset samt `BadInvalidState` ausserhalb des erlaubten Zustands,
* `Executable` je Zustand,
* Abschluss eines Jobs (Running -> Ready), auch wenn ihn die 40100-Seite
  gestartet hat,
* nicht betriebsbereites System: Programm steht auf `Halted`,
* die uebrigen Uebergaenge (Suspend/Resume, abgelehnte Funktion mit Event,
  `running_to_*`-Helfer) an einem eigenen kleinen `Program`.

Aufgerufen wird ueber die interne Session des Servers -- derselbe
Methodendienst wie fuer einen entfernten Client, nur ohne TCP. Kamerafrei:
die Erkennungsquelle ist eine Attrappe.
"""

import asyncio
import unittest
from dataclasses import replace
from unittest import mock

from asyncua import Server, ua

from ua_program import Program, ProgramException
from vision_server.config import VisionServerConfig
from vision_server.detection.base import Detection, DetectionRequest, DetectionSource
from vision_server.errors import VisionErrorCode

RECIPE = "test"
AUTO = "VisionMachine.VisionStateMachine.AutomaticModeStateMachine"


class FakeSource(DetectionSource):
    """Liefert nach `delay` eine Detektion; `open()` kann scheitern."""

    profile_id = "fake"

    def __init__(self, *, delay: float = 0.15, fail_open: bool = False) -> None:
        self._delay = delay
        self._fail_open = fail_open

    async def open(self) -> None:
        if self._fail_open:
            raise RuntimeError("keine Kamera")

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        await asyncio.sleep(self._delay)
        return [
            Detection(
                module_id="M",
                instance_id="det-1",
                position=(0.0, 0.0, 0.0),
                orientation=(0.0, 0.0, 0.0, 1.0),
                confidence=1.0,
            )
        ]


async def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Bedingung nicht rechtzeitig erreicht")
        await asyncio.sleep(0.02)


class CallMixin:
    """Methodenaufrufe ueber die interne Session des Servers."""

    server: Server
    idx: int

    def nid(self, identifier: str) -> ua.NodeId:
        return ua.NodeId(identifier, self.idx)

    async def call(self, obj: str, method: str, *args) -> ua.CallMethodResult:
        request = ua.CallMethodRequest()
        request.ObjectId = self.nid(obj)
        request.MethodId = self.nid(method)
        request.InputArguments = [
            arg if isinstance(arg, ua.Variant) else ua.Variant(arg) for arg in args
        ]
        return (await self.server.iserver.isession.call([request]))[0]

    def assert_invalid_state(self, result: ua.CallMethodResult) -> None:
        self.assertEqual(result.StatusCode.value, ua.StatusCodes.BadInvalidState)

    async def state_of(self, program: str) -> str:
        node = self.server.get_node(self.nid(program))
        current = await node.get_child("0:CurrentState")
        return (await current.read_value()).Text

    async def wait_state(self, program: str, name: str) -> None:
        deadline = asyncio.get_running_loop().time() + 5.0
        while await self.state_of(program) != name:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"{program} nicht nach {name} (steht auf {await self.state_of(program)})")
            await asyncio.sleep(0.02)

    async def executable(self, program: str) -> dict[str, bool]:
        values = {}
        for name in ("Start", "Halt", "Reset", "Suspend", "Resume"):
            node = self.server.get_node(self.nid(f"{program}.{name}"))
            values[name] = (await node.read_attribute(ua.AttributeIds.Executable)).Value.Value
        return values


class MachineTestCase(CallMixin, unittest.IsolatedAsyncioTestCase):
    """Baut je Test die ganze Maschine mit einer Attrappen-Quelle."""

    port = 0
    source: DetectionSource

    def make_source(self) -> DetectionSource:
        return FakeSource()

    async def asyncSetUp(self):
        from vision_server.address_space import configure_server
        from vision_server.runner import install_vision_machine

        # Der Debug-Modus des Loops verdreifacht die Aufbauzeit der Maschine.
        asyncio.get_running_loop().set_debug(False)
        self.config = replace(
            VisionServerConfig(endpoint=f"opc.tcp://127.0.0.1:{self.port}/test/"),
            recipe_profiles=((RECIPE, "fake"),),
            continuous_interval_s=0.05,
        )
        self.source = self.make_source()
        self.server = Server()
        await configure_server(self.server, self.config)
        with mock.patch(
            "vision_server.runner.install.build_detection_sources",
            return_value={"fake": self.source},
        ):
            self.machine = await install_vision_machine(self.server, self.config)
        self.idx = self.machine.results.json_node.nodeid.NamespaceIndex

    async def asyncTearDown(self):
        await self.machine.aclose()

    async def write_parameter(self, name: str, value, vtype) -> None:
        node = self.server.get_node(self.nid(f"VisionProgram.ParameterSet.{name}"))
        await node.write_value(ua.Variant(value, vtype))

    async def result(self, name: str):
        node = self.server.get_node(self.nid(f"VisionProgram.ResultSet.{name}"))
        return await node.read_value()

    async def program_call(self, name: str) -> ua.CallMethodResult:
        return await self.call("VisionProgram", f"VisionProgram.{name}")


class VisionProgramTest(MachineTestCase):
    port = 48530

    async def test_nodeids_ready_und_ablehnungen(self):
        for name in ("Start", "Halt", "Reset", "Suspend", "Resume"):
            node = self.server.get_node(self.nid(f"VisionProgram.{name}"))
            self.assertEqual(await node.read_node_class(), ua.NodeClass.Method, name)
        for name in (
            "ParameterSet.RecipeId",
            "ParameterSet.Continuous",
            "ResultSet.JobId",
            "ResultSet.ErrorCode",
            "ResultSet.ExecutionMode",
        ):
            node = self.server.get_node(self.nid(f"VisionProgram.{name}"))
            self.assertEqual(await node.read_node_class(), ua.NodeClass.Variable, name)
        program = self.server.get_node(self.nid("VisionProgram"))
        self.assertEqual(
            await program.read_type_definition(),
            ua.NodeId(ua.ObjectIds.ProgramStateMachineType),
        )

        self.assertEqual(await self.state_of("VisionProgram"), "Ready")
        self.assertEqual(
            await self.executable("VisionProgram"),
            {"Start": True, "Halt": True, "Reset": False, "Suspend": False, "Resume": False},
        )
        for name in ("Reset", "Suspend", "Resume"):
            self.assert_invalid_state(await self.program_call(name))
        self.assertEqual(await self.state_of("VisionProgram"), "Ready")

    async def test_start_mit_unbekanntem_rezept_wird_abgelehnt(self):
        await self.write_parameter("RecipeId", "gibt-es-nicht", ua.VariantType.String)
        # Der Aufruf selbst ist Good; die Ablehnung steht im Zustand und im Ergebnis.
        self.assertTrue((await self.program_call("Start")).StatusCode.is_good())
        await wait_until(lambda: not self.machine.program._lock.locked())
        self.assertEqual(await self.state_of("VisionProgram"), "Ready")
        self.assertEqual(await self.result("ErrorCode"), int(VisionErrorCode.UNKNOWN_RECIPE))
        self.assertEqual(await self.result("ExecutionMode"), "idle")

    async def test_einzeljob_laeuft_nach_running_und_zurueck_nach_ready(self):
        await self.write_parameter("RecipeId", RECIPE, ua.VariantType.String)
        result = await self.program_call("Start")
        self.assertTrue(result.StatusCode.is_good())
        self.assertEqual(result.OutputArguments, [])
        await self.wait_state("VisionProgram", "Running")
        self.assertEqual(await self.result("ExecutionMode"), "single")
        self.assertEqual(
            await self.executable("VisionProgram"),
            {"Start": False, "Halt": True, "Reset": False, "Suspend": True, "Resume": False},
        )
        # Start und Reset im Zustand Running: BadInvalidState.
        self.assert_invalid_state(await self.program_call("Start"))
        self.assert_invalid_state(await self.program_call("Reset"))
        self.assert_invalid_state(await self.program_call("Resume"))
        # Suspend wird angenommen, aber von `VisionProgram.suspend` abgelehnt.
        self.assertTrue((await self.program_call("Suspend")).StatusCode.is_good())
        await self.wait_state("VisionProgram", "Ready")
        self.assertEqual(await self.result("JobId"), "job-000001")
        self.assertEqual(await self.result("ErrorCode"), 0)
        self.assertEqual(await self.result("ExecutionMode"), "idle")

    async def test_dauerbetrieb_halt_und_reset(self):
        await self.write_parameter("RecipeId", RECIPE, ua.VariantType.String)
        await self.write_parameter("Continuous", True, ua.VariantType.Boolean)
        self.assertTrue((await self.program_call("Start")).StatusCode.is_good())
        await self.wait_state("VisionProgram", "Running")
        self.assertEqual(await self.result("ExecutionMode"), "continuous")
        await asyncio.sleep(0.1)
        self.assertEqual(await self.state_of("VisionProgram"), "Running")

        self.assertTrue((await self.program_call("Halt")).StatusCode.is_good())
        await self.wait_state("VisionProgram", "Halted")
        self.assertFalse(self.machine.jobs.busy)
        self.assertEqual(await self.result("ErrorCode"), 0)
        self.assertEqual(await self.result("ExecutionMode"), "idle")
        self.assertEqual(
            await self.executable("VisionProgram"),
            {"Start": False, "Halt": False, "Reset": True, "Suspend": False, "Resume": False},
        )
        for name in ("Start", "Halt", "Suspend", "Resume"):
            self.assert_invalid_state(await self.program_call(name))

        self.assertTrue((await self.program_call("Reset")).StatusCode.is_good())
        await self.wait_state("VisionProgram", "Ready")

    async def test_halt_in_ready_fuehrt_nach_halted(self):
        self.assertTrue((await self.program_call("Halt")).StatusCode.is_good())
        await self.wait_state("VisionProgram", "Halted")

    async def test_job_ueber_40100_laesst_das_programm_auf_ready(self):
        """Ein ueber 40100 gestarteter Job kommt am Programm nur als
        Ready -> Ready an: `_on_job_finished` tut nichts ausserhalb von Running."""
        job_id, code = self.machine.jobs.start_single_job("", "", RECIPE, "", [])
        self.assertEqual(code, VisionErrorCode.OK)
        await wait_until(lambda: not self.machine.jobs.busy)
        await asyncio.sleep(0.05)
        self.assertEqual(await self.state_of("VisionProgram"), "Ready")

    async def test_fehlgeschlagener_job_endet_auf_ready_nicht_halted(self):
        self.source._delay = 0.01

        async def failing(request):
            from vision_server.errors import VisionJobError

            raise VisionJobError(VisionErrorCode.DETECTION_FAILED, "nichts gefunden")

        self.source.acquire_and_detect = failing
        await self.write_parameter("RecipeId", RECIPE, ua.VariantType.String)
        self.assertTrue((await self.program_call("Start")).StatusCode.is_good())
        await wait_until(lambda: not self.machine.jobs.busy)
        # Das Programm steht schon vor dem Start auf Ready; erst das Ergebnis
        # (`_finish` schreibt es vor dem Zustandswechsel) zeigt das Job-Ende.
        deadline = asyncio.get_running_loop().time() + 5.0
        while await self.result("ErrorCode") == 0:
            self.assertLess(asyncio.get_running_loop().time(), deadline, "kein ErrorCode")
            await asyncio.sleep(0.02)
        self.assertEqual(await self.result("ErrorCode"), int(VisionErrorCode.DETECTION_FAILED))
        await self.wait_state("VisionProgram", "Ready")
        self.assertEqual(await self.result("ExecutionMode"), "idle")


class NichtBetriebsbereitTest(MachineTestCase):
    """Quelle geht nicht auf: 40100 bleibt Preoperational, Programm Halted."""

    port = 48531

    def make_source(self) -> DetectionSource:
        return FakeSource(fail_open=True)

    async def test_programm_steht_auf_halted(self):
        self.assertEqual(await self.state_of("VisionProgram"), "Halted")
        program = self.server.get_node(self.nid("VisionProgram"))
        last = await program.get_child("0:LastTransition")
        self.assertEqual((await last.read_value()).Text, "ReadyToHalted")
        self.assertFalse(self.machine.states.is_ready())
        self.assert_invalid_state(await self.program_call("Start"))


# ================================================== ua_program.Program allein


class DemoProgram(Program):
    """Ein Programm, dessen Funktionen sich vom Test steuern lassen."""

    start_inputs: list = []
    halt_inputs: list = []
    reset_inputs: list = []
    suspend_inputs: list = []
    resume_inputs: list = []

    def __init__(self, server, nsid) -> None:
        super().__init__(server, nsid)
        self.refuse: dict[str, str] = {}
        self.raise_in: set[str] = set()
        self.calls: list[str] = []

    async def _run(self, name: str) -> str | None:
        self.calls.append(name)
        if name in self.raise_in:
            raise RuntimeError(f"{name} kaputt")
        return self.refuse.get(name)

    async def start(self) -> str | None:
        return await self._run("start")

    async def halt(self) -> str | None:
        return await self._run("halt")

    async def reset(self) -> str | None:
        return await self._run("reset")

    async def suspend(self) -> str | None:
        return await self._run("suspend")

    async def resume(self) -> str | None:
        return await self._run("resume")


class ProgramUebergaengeTest(CallMixin, unittest.IsolatedAsyncioTestCase):
    """Die Zustandsmaschine selbst: alle neun Uebergaenge von Teil 10."""

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.server = Server()
        await self.server.init()
        self.idx = await self.server.register_namespace("http://test/program")
        self.program = await DemoProgram.init(
            self.server,
            self.server.nodes.objects,
            self.idx,
            "Demo",
            nodeid=ua.NodeId("Demo", self.idx),
        )

    async def method(self, name: str) -> ua.CallMethodResult:
        return await self.call("Demo", f"Demo.{name}")

    async def settle(self) -> None:
        await wait_until(lambda: not self.program._lock.locked())

    async def test_start_ist_im_ausgangszustand_ready(self):
        self.assertEqual(await self.state_of("Demo"), "Ready")
        self.assertEqual(self.program.state_machine._current_state.number, 12)

    async def test_ready_running_suspended_running_halted_ready(self):
        steps = (
            ("Start", "Running"),
            ("Suspend", "Suspended"),
            ("Resume", "Running"),
            ("Halt", "Halted"),
            ("Reset", "Ready"),
        )
        for method, expected in steps:
            self.assertTrue((await self.method(method)).StatusCode.is_good(), method)
            await self.wait_state("Demo", expected)
            await self.settle()
        self.assertEqual(self.program.calls, ["start", "suspend", "resume", "halt", "reset"])

    async def test_halt_aus_ready_und_aus_suspended(self):
        self.assertTrue((await self.method("Halt")).StatusCode.is_good())
        await self.wait_state("Demo", "Halted")
        await self.settle()
        await self.method("Reset")
        await self.wait_state("Demo", "Ready")
        await self.settle()

        await self.method("Start")
        await self.wait_state("Demo", "Running")
        await self.settle()
        await self.method("Suspend")
        await self.wait_state("Demo", "Suspended")
        await self.settle()
        self.assertEqual(
            await self.executable("Demo"),
            {"Start": False, "Halt": True, "Reset": False, "Suspend": False, "Resume": True},
        )
        await self.method("Halt")
        await self.wait_state("Demo", "Halted")

    async def test_abgelehnte_funktion_laesst_den_zustand_stehen(self):
        self.program.refuse["start"] = "noch nicht"
        self.assertTrue((await self.method("Start")).StatusCode.is_good())
        await self.settle()
        self.assertEqual(await self.state_of("Demo"), "Ready")

    async def test_ausnahme_in_der_funktion_wird_zur_ablehnung(self):
        self.program.raise_in.add("start")
        self.assertTrue((await self.method("Start")).StatusCode.is_good())
        await self.settle()
        self.assertEqual(await self.state_of("Demo"), "Ready")

    async def test_falscher_zustand_ist_bad_invalid_state_ohne_aufruf(self):
        for name in ("Reset", "Suspend", "Resume"):
            self.assert_invalid_state(await self.method(name))
        self.assertEqual(self.program.calls, [])
        # Das Lock bleibt nicht haengen: ein gueltiger Aufruf geht danach.
        self.assertTrue((await self.method("Start")).StatusCode.is_good())
        await self.wait_state("Demo", "Running")

    async def test_zweiter_aufruf_waehrend_einer_laeuft_wartet_aufs_lock(self):
        gate = asyncio.Event()

        async def slow_start() -> None:
            await gate.wait()

        self.program.start = slow_start
        first = asyncio.create_task(self.method("Start"))
        await wait_until(lambda: self.program._lock.locked())
        second = asyncio.create_task(self.method("Halt"))
        await asyncio.sleep(0.05)
        self.assertFalse(second.done())  # wartet auf das Lock der Start-Aufgabe
        gate.set()
        self.assertTrue((await first).StatusCode.is_good())
        self.assertTrue((await second).StatusCode.is_good())
        # Halt lief erst nach dem Start, im Zustand Running.
        await self.wait_state("Demo", "Halted")
        self.assertEqual(self.program.calls, ["halt"])

    async def test_interne_uebergaenge(self):
        await self.method("Start")
        await self.wait_state("Demo", "Running")
        await self.settle()
        await self.program.running_to_halted("Stoerung")
        self.assertEqual(await self.state_of("Demo"), "Halted")
        with self.assertRaises(ProgramException):
            await self.program.running_to_halted()

        await self.method("Reset")
        await self.wait_state("Demo", "Ready")
        await self.settle()
        await self.method("Start")
        await self.wait_state("Demo", "Running")
        await self.settle()
        await self.program.running_to_ready()
        self.assertEqual(await self.state_of("Demo"), "Ready")

        await self.method("Start")
        await self.wait_state("Demo", "Running")
        await self.settle()
        await self.method("Suspend")
        await self.wait_state("Demo", "Suspended")
        await self.settle()
        with self.assertRaises(ProgramException):
            await self.program.running_to_ready()
        await self.program.suspended_to_ready()
        self.assertEqual(await self.state_of("Demo"), "Ready")
        with self.assertRaises(ProgramException):
            await self.program.suspended_to_ready()

    async def test_nodeids_sind_sprechend(self):
        for name in ("Start", "Halt", "Reset", "Suspend", "Resume"):
            node = self.server.get_node(self.nid(f"Demo.{name}"))
            self.assertEqual(await node.read_node_class(), ua.NodeClass.Method, name)


if __name__ == "__main__":
    unittest.main()
