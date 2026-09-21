"""Part-10-Programm als generische Bedienoberflaeche des Vision-Jobs.

Dies ist ein **Aufsatz**, keine zweite Implementierung: `VisionProgram` haelt
keinen eigenen Job-Zustand, sondern ruft denselben `JobRunner`, den auch die
40100-Methoden `StartSingleJob`/`Stop` benutzen. Beide Oberflaechen stehen
gleichberechtigt nebeneinander und sehen jederzeit denselben Job.

Warum ueberhaupt: `ProgramStateMachineType` liegt im Basis-Namensraum und ist
in jedem OPC-UA-Server vorhanden. Ein Client bedient Start/Halt/Reset und liest
den Zustand, ohne OPC 40100 zu kennen.

Form nach dem Vorbild der uebrigen Zellmodule (am 2026-09-21 am Conveyor
`opc.tcp://10.10.38.41:4840/conveyor/` nachgesehen: `RunContinuous`,
`MoveDistance`, `MoveUntilSensor`):

* `Start()` nimmt **keine** Argumente.
* Eingaben stehen als beschreibbare Variablen in `ParameterSet` und werden
  **vor** dem Start geschrieben.
* Ausgaben stehen in `ResultSet`.

Bewusste Abweichung von Teil 10: Ein fehlgeschlagener Job fuehrt **nicht** nach
`Halted`, sondern zurueck nach `Ready`. Sonst muesste das Frontend nach jeder
misslungenen Erkennung erst `Reset` aufrufen, und die beiden Automaten liefen
auseinander -- der 40100-Automat kehrt nach einem Fehler ebenfalls nach
`Ready`/`Operational` zurueck. Der Grund steht in `ResultSet/ErrorCode` und
reist als `ProgramTransitionEvent`.
"""

import asyncio
import logging

from asyncua import Node, Server, ua

from ua_program import Program, ProgramException

from .errors import VisionErrorCode
from .job import JobRunner

_log = logging.getLogger(__name__)

#: BrowseName und String-NodeId der Programminstanz.
PROGRAM_NAME = "VisionProgram"

PARAMETER_SET = "ParameterSet"
RESULT_SET = "ResultSet"

#: Ausfuehrungsart in `ResultSet/ExecutionMode`. Teil 10 kennt nur ein
#: `Running` und kann Einzel- und Dauerbetrieb nicht unterscheiden; dieser
#: Knoten holt die Information nach, die der 40100-Automat in
#: `SingleExecution` bzw. `ContinuousExecution` ohnehin fuehrt.
MODE_IDLE = "idle"
MODE_SINGLE = "single"
MODE_CONTINUOUS = "continuous"


class VisionProgram(Program):
    """Teil-10-Sicht auf den Vision-Job."""

    # Alle Methoden ohne Eingaben -- wie bei den uebrigen Modulen der Zelle.
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
        #: Gesetzt, solange ein Job auf unser eigenes Halt/Reset hin endet.
        #: Ohne dieses Flag wuerde der Abschluss-Beobachter `Running -> Ready`
        #: ausloesen, waehrend `_transition` gerade nach `Halted` wechselt --
        #: zwei Zustandswechsel auf dieselbe Ursache.
        self._expect_stop = False
        jobs.add_finish_listener(self._on_job_finished)

    # ---------------------------------------------------------------- Teil 10

    async def start(self) -> str | None:
        """Startet einen Job mit dem, was in `ParameterSet` steht.

        `None` heisst angenommen (Zustand wechselt nach `Running`), ein String
        heisst abgelehnt (Zustand bleibt, der Text reist als Event).
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
        """Bricht einen laufenden Job ab und geht nach `Halted`."""
        self._expect_stop = True
        try:
            code = await self._jobs.stop()
        except Exception as error:  # pragma: no cover - defensiv
            self._expect_stop = False
            return f"Halt fehlgeschlagen: {error}"
        await self._write_results(None, code, MODE_IDLE)
        if code is not VisionErrorCode.OK:
            self._expect_stop = False
            return f"Halt fehlgeschlagen: {code.name} ({int(code)})"
        return None

    async def reset(self) -> str | None:
        """Macht das Programm aus `Halted` wieder betriebsbereit."""
        self._expect_stop = True
        await self._jobs.cancel_running()
        self._expect_stop = False
        await self._write_results(None, VisionErrorCode.OK, MODE_IDLE)
        return None

    async def suspend(self) -> str:
        """Nicht moeglich: ein Vision-Job laesst sich nicht anhalten."""
        return "Ein Vision-Job kann nicht pausiert werden; Halt verwenden."

    async def resume(self) -> str:
        """Nicht moeglich -- der Zustand `Suspended` wird nie erreicht."""
        return "Ein Vision-Job kann nicht fortgesetzt werden."

    # ------------------------------------------------------------ Job-Ende

    def _on_job_finished(self, job_id: str, code: VisionErrorCode) -> None:
        """Wird vom `JobRunner` synchron am Ende jedes Jobs gerufen.

        Auch dann, wenn der Job ueber die 40100-Methoden gestartet wurde --
        das Programm bildet den Zustand also unabhaengig davon ab, welche
        Oberflaeche ihn ausgeloest hat.
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
            # Zwischenzeitlich hat ein Halt den Zustand schon verschoben.
            _log.debug("Zustand bereits gewechselt, kein Running->Ready fuer %s", job_id)

    # -------------------------------------------------- Parameter / Ergebnisse

    def attach_nodes(self, parameters: dict[str, Node], results: dict[str, Node]) -> None:
        """Haengt die vom Installer angelegten Knoten ein."""
        self._parameters = parameters
        self._results = results

    async def _read_parameter(self, name: str, default):
        """Liest einen Eingabeparameter; ein Lesefehler darf nicht starten lassen."""
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
        """Schreibt JobId, ErrorCode und ExecutionMode, soweit vorhanden."""
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
) -> VisionProgram:
    """Haengt das Part-10-Programm neben das Vision-System.

    Parameters
    ----------
    parent
        Knoten, unter dem das Programm haengt (`Objects`).
    own_idx
        Der Vision-Namensraum; das Programm teilt ihn mit `VisionMachine`.
    jobs
        Derselbe `JobRunner`, den auch die 40100-Methoden benutzen.
    known_recipes
        Zugelassene RecipeIds; landen als Beschreibung am Parameterknoten,
        damit ein generischer Client sie ohne diese Doku findet.
    mirror_nodes
        Vorhandene Ergebnisknoten, die zusaetzlich unter `ResultSet`
        auffindbar sein sollen. Es werden **Referenzen** gesetzt, keine
        Kopien -- es bleibt genau ein Knoten mit genau einem Wert.
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
            f"Erkennungsrezept; leer = Standard. Bekannt: {recipes}" if recipes else "Erkennungsrezept",
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
        # Beschreibbar, sonst koennte niemand einen Parameter setzen.
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

    _log.info(
        "Part-10-Programm '%s' als %s angelegt",
        PROGRAM_NAME,
        program_node.nodeid.to_string(),
    )
    return program
