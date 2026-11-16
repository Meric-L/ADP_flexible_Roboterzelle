"""Part 10 `VisionProgram`: a generic operator interface for the vision job.

It keeps no job state of its own and calls the same `JobRunner` as the 40100
methods `StartSingleJob` and `Stop`, so both interfaces always see the same job.
Inputs are written to `ParameterSet` before `Start()`, outputs are read from
`ResultSet`.

Deliberate deviation from Part 10: a failed job returns to `Ready`, not `Halted`,
as the 40100 state machine does. The reason is in `ResultSet/ErrorCode`.
"""

import asyncio
import logging

from asyncua import Node, Server, ua

from ua_program import Program, ProgramException

from .errors import VisionErrorCode
from .job import JobRunner

_log = logging.getLogger(__name__)

#: BrowseName and string NodeId of the program instance.
PROGRAM_NAME = "VisionProgram"

PARAMETER_SET = "ParameterSet"
RESULT_SET = "ResultSet"

#: Execution mode in `ResultSet/ExecutionMode`. Part 10 knows only one `Running`
#: state and cannot tell single from continuous operation. This node supplies the
#: information that the 40100 state machine already tracks in `SingleExecution`
#: and `ContinuousExecution`.
MODE_IDLE = "idle"
MODE_SINGLE = "single"
MODE_CONTINUOUS = "continuous"


class VisionProgram(Program):
    """Part 10 view of the vision job."""

    # All methods take no inputs, as with the other modules of the cell.
    reset_inputs: list = []
    halt_inputs: list = []
    suspend_inputs: list = []
    resume_inputs: list = []
    start_inputs: list = []

    def __init__(self, server: Server, nsid: int, jobs: JobRunner) -> None:
        super().__init__(server, nsid)
        self._jobs = jobs
        self._parameters: dict[str, Node] = {}
        self._results: dict[str, Node] = {}
        #: Set while a job ends in response to our own halt or reset.
        #: Without this flag, the completion observer would trigger `Running -> Ready`
        #: while `_transition` is switching to `Halted` -- two state changes for the
        #: same cause.
        self._expect_stop = False
        jobs.add_finish_listener(self._on_job_finished)

    # ---------------------------------------------------------------- Part 10

    async def start(self) -> str | None:
        """Start a job with what is in `ParameterSet`.

        `None` means accepted (the state changes to `Running`). A string means
        rejected (the state stays, and the text travels as an event).
        """
        recipe_id = await self._read_parameter("RecipeId", "")
        continuous = bool(await self._read_parameter("Continuous", False))

        if continuous:
            job_id, error = self._jobs.start_continuous("", "", recipe_id, "", [])
            mode = MODE_CONTINUOUS
        else:
            job_id, error = self._jobs.start_single_job("", "", recipe_id, "", [])
            mode = MODE_SINGLE

        await self._write_results(
            job_id, error, mode if error is VisionErrorCode.OK else MODE_IDLE
        )
        if error is not VisionErrorCode.OK:
            _log.warning("Start ueber das Programm abgelehnt: %s", error.name)
            return f"Start abgelehnt: {error.name} ({int(error)})"
        _log.info("Job %s ueber das Programm gestartet (%s)", job_id, mode)
        return None

    async def halt(self) -> str | None:
        """Abort a running job and move to `Halted`."""
        self._expect_stop = True
        try:
            code = await self._jobs.stop()
        except Exception as error:  # pragma: no cover - defensive
            self._expect_stop = False
            return f"Halt fehlgeschlagen: {error}"
        await self._write_results(None, code, MODE_IDLE)
        if code is not VisionErrorCode.OK:
            self._expect_stop = False
            return f"Halt fehlgeschlagen: {code.name} ({int(code)})"
        return None

    async def reset(self) -> str | None:
        """Make the program operational again from `Halted`."""
        self._expect_stop = True
        await self._jobs.cancel_running()
        self._expect_stop = False
        await self._write_results(None, VisionErrorCode.OK, MODE_IDLE)
        return None

    async def suspend(self) -> str:
        """Not possible: a vision job cannot be paused."""
        return "Ein Vision-Job kann nicht pausiert werden; Halt verwenden."

    async def resume(self) -> str:
        """Not possible: the `Suspended` state is never reached."""
        return "Ein Vision-Job kann nicht fortgesetzt werden."

    # ------------------------------------------------------------ Job end

    def _on_job_finished(self, job_id: str, code: VisionErrorCode) -> None:
        """Called synchronously by the `JobRunner` at the end of every job.

        This also applies when the job was started via the 40100 methods. The
        program therefore mirrors the state independently of which interface
        triggered it.
        """
        if self._expect_stop:
            self._expect_stop = False
            return
        if self.state_machine._current_state.number != 13:  # 13 = Running
            return
        asyncio.create_task(self._finish(job_id, code))

    async def _finish(self, job_id: str, code: VisionErrorCode) -> None:
        await self._write_results(job_id, code, MODE_IDLE)
        message = (
            f"Job {job_id} abgeschlossen"
            if code is VisionErrorCode.OK
            else f"Job {job_id} beendet: {code.name} ({int(code)})"
        )
        try:
            await self.running_to_ready(message)
        except ProgramException:
            # A halt has meanwhile already moved the state.
            _log.debug("Zustand bereits gewechselt, kein Running->Ready fuer %s", job_id)

    # -------------------------------------------------- Parameters / results

    def attach_nodes(self, parameters: dict[str, Node], results: dict[str, Node]) -> None:
        """Attach the nodes created by the installer."""
        self._parameters = parameters
        self._results = results

    async def _read_parameter(self, name: str, default):
        """Read an input parameter. A read error must not cause a start; the default is returned instead."""
        node = self._parameters.get(name)
        if node is None:
            return default
        try:
            value = await node.read_value()
        except Exception:  # pragma: no cover - defensiv
            _log.exception("Parameter '%s' nicht lesbar, nehme Standardwert", name)
            return default
        return default if value is None else value

    async def _write_results(
        self, job_id: str | None, code: VisionErrorCode, mode: str
    ) -> None:
        """Write JobId, ErrorCode and ExecutionMode, where present."""
        if not self._results:
            return
        try:
            if job_id is not None and (node := self._results.get("JobId")):
                await node.write_value(job_id, ua.VariantType.String)
            if node := self._results.get("ErrorCode"):
                await node.write_value(int(code), ua.VariantType.Int32)
            if node := self._results.get("ExecutionMode"):
                await node.write_value(mode, ua.VariantType.String)
        except Exception:  # pragma: no cover - defensiv
            _log.exception("Ergebnisknoten des Programms liessen sich nicht schreiben")


async def install_vision_program(
    server: Server,
    parent: Node,
    own_idx: int,
    jobs: JobRunner,
    *,
    known_recipes: frozenset[str] | None = None,
    mirror_nodes: dict[str, Node] | None = None,
    mirror_methods: dict[str, Node] | None = None,
) -> VisionProgram:
    """Attach the Part 10 program next to the vision system.

    Args:
        server: The OPC UA server.
        parent: Node under which the program hangs (`Objects`).
        own_idx: The vision namespace. The program shares it with `VisionMachine`.
        jobs: The same `JobRunner` that the 40100 methods use.
        known_recipes: Allowed RecipeIds. They become the description of the
            parameter node, so a generic client can find them without this
            documentation.
        mirror_nodes: Existing result nodes that should also be reachable under
            `ResultSet`. **References** are set, not copies: exactly one node
            with exactly one value remains.
        mirror_methods: Existing method nodes that should also be callable
            directly under the program (the calibration methods). These are
            likewise only `HasComponent` references: one method, one
            implementation, two places where it is found. Verified with
            asyncua 2.0.1: the call succeeds with the program
            *and* with the vision system as `objectId`, without registering the
            method twice.

    Returns:
        The installed `VisionProgram`.
    """
    program = await VisionProgram.init(
        server,
        parent,
        own_idx,
        PROGRAM_NAME,
        jobs,
        nodeid=ua.NodeId(PROGRAM_NAME, own_idx),
    )
    program_node = program.state_machine._state_machine_node

    parameter_set = await program_node.add_object(
        ua.NodeId(f"{PROGRAM_NAME}.{PARAMETER_SET}", own_idx),
        ua.QualifiedName(PARAMETER_SET, own_idx),
    )
    result_set = await program_node.add_object(
        ua.NodeId(f"{PROGRAM_NAME}.{RESULT_SET}", own_idx),
        ua.QualifiedName(RESULT_SET, own_idx),
    )

    recipes = ", ".join(sorted(r for r in (known_recipes or frozenset()) if r))
    parameters: dict[str, Node] = {}
    for name, initial, variant, description in (
        (
            "RecipeId",
            "",
            ua.VariantType.String,
            f"Erkennungsrezept, Pflicht (leer = Fehler). Bekannt: {recipes}" if recipes else "Erkennungsrezept",
        ),
        (
            "Continuous",
            False,
            ua.VariantType.Boolean,
            "False = Einzeljob, True = Dauerbetrieb bis Halt.",
        ),
    ):
        node = await parameter_set.add_variable(
            ua.NodeId(f"{PROGRAM_NAME}.{PARAMETER_SET}.{name}", own_idx),
            ua.QualifiedName(name, own_idx),
            initial,
            variant,
        )
        # Writable, otherwise nobody could set a parameter.
        await node.set_writable()
        await node.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.Variant(ua.LocalizedText(description))),
        )
        parameters[name] = node

    results: dict[str, Node] = {}
    for name, initial, variant in (
        ("JobId", "", ua.VariantType.String),
        ("ErrorCode", 0, ua.VariantType.Int32),
        ("ExecutionMode", MODE_IDLE, ua.VariantType.String),
    ):
        results[name] = await result_set.add_variable(
            ua.NodeId(f"{PROGRAM_NAME}.{RESULT_SET}.{name}", own_idx),
            ua.QualifiedName(name, own_idx),
            initial,
            variant,
        )
    program.attach_nodes(parameters, results)

    for name, node in (mirror_nodes or {}).items():
        await result_set.add_reference(node, ua.ObjectIds.Organizes, forward=True)
        _log.debug("Ergebnisknoten '%s' zusaetzlich unter %s verlinkt", name, RESULT_SET)

    for name, node in (mirror_methods or {}).items():
        await program_node.add_reference(node, ua.ObjectIds.HasComponent, forward=True)
        _log.debug("Methode '%s' zusaetzlich unter %s aufrufbar", name, PROGRAM_NAME)

    _log.info(
        "Part-10-Programm '%s' als %s angelegt",
        PROGRAM_NAME,
        program_node.nodeid.to_string(),
    )
    return program
