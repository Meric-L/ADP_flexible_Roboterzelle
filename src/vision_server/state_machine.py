"""Bindung der beiden 40100-Zustandsautomaten an die Nodeset-Instanz."""

import logging

from asyncua import Server
from asyncua.common.node import Node
from asyncua.common.statemachine import FiniteStateMachine, State

from .address_space import VisionAddressSpace
from .nodeset_ids import (
    EVENT_STATE_CHANGED,
    OUTER_STATE_NAMES,
    STATE_INITIALIZED,
    STATE_READY,
    STATE_SINGLE_EXECUTION,
    mv,
)

_log = logging.getLogger(__name__)


async def _load_state(node: Node) -> State:
    """Liest BrowseName und StateNumber eines Zustandsknotens."""
    browse_name = (await node.read_browse_name()).Name
    state_number = await (await node.get_child(["StateNumber"])).read_value()
    return State(id=None, name=browse_name, number=state_number, node=node)


async def _bind(server: Server, node: Node, name: str, emitting_node: Node, mv_idx: int) -> FiniteStateMachine:
    """Bindet eine FiniteStateMachine an einen vorhandenen Automatenknoten.

    `install()` wuerde einen zweiten Automatenbaum anlegen; stattdessen wird der
    aus der Nodeset-Instanziierung vorhandene Knoten gesetzt und nur `init()`
    aufgerufen. Anschliessend wird der Event-Generator umgebogen: asyncua legt
    ihn in `init()` auf (TransitionEventType, Automatenknoten) — ein Client, der
    auf dem VisionSystem abonniert, wuerde davon nichts sehen, weil asyncua
    Events strikt nach exaktem `emitting_node` ausliefert.
    """
    fsm = FiniteStateMachine(server, parent=node, name=name)
    fsm._state_machine_node = node
    await fsm.init(node)
    fsm._evgen = await server.get_event_generator(mv(EVENT_STATE_CHANGED, mv_idx), emitting_node)
    return fsm


class VisionStateMachines:
    """Fuehrt VisionStateMachine und AutomaticModeStateMachine gemeinsam.

    Zustandswechsel werden ohne `Transition`-Objekt ausgefuehrt: die
    Nodeset-Instanz besitzt keinen optionalen `LastTransition`-Knoten, asyncuas
    `_write_transition` wuerde daran scheitern. Der Uebergangsname steht
    stattdessen in der Event-Nachricht.
    """

    def __init__(
        self,
        vision_fsm: FiniteStateMachine,
        automatic_fsm: FiniteStateMachine,
        outer: dict[str, State],
        inner: dict[str, State],
    ) -> None:
        self._vision_fsm = vision_fsm
        self._automatic_fsm = automatic_fsm
        self._outer = outer
        self._inner = inner
        self._outer_state = ""
        self._inner_state = ""

    @classmethod
    async def bind(cls, space: VisionAddressSpace) -> "VisionStateMachines":
        """Bindet beide Automaten und loest alle Zustandsknoten auf."""
        vision_fsm = await _bind(
            space.server,
            space.vision_state_machine,
            "VisionStateMachine",
            space.vision_system,
            space.mv_idx,
        )
        automatic_fsm = await _bind(
            space.server,
            space.automatic_state_machine,
            "AutomaticModeStateMachine",
            space.vision_system,
            space.mv_idx,
        )
        outer = {
            name: await _load_state(
                await space.vision_state_machine.get_child(f"{space.mv_idx}:{name}")
            )
            for name in OUTER_STATE_NAMES
        }
        inner = {
            name: await _load_state(space.server.get_node(mv(identifier, space.mv_idx)))
            for name, identifier in (
                ("Initialized", STATE_INITIALIZED),
                ("Ready", STATE_READY),
                ("SingleExecution", STATE_SINGLE_EXECUTION),
            )
        }
        return cls(vision_fsm, automatic_fsm, outer, inner)

    async def _set_outer(self, name: str, message: str | None) -> None:
        await self._vision_fsm.change_state(self._outer[name], event_msg=message)
        self._outer_state = name

    async def _set_inner(self, name: str, message: str | None) -> None:
        await self._automatic_fsm.change_state(self._inner[name], event_msg=message)
        self._inner_state = name

    async def enter_operational(self) -> None:
        """Faehrt beide Automaten in den betriebsbereiten Zustand.

        Der Nodeset kennt keinen Uebergang Halted -> Operational; der konforme
        Startpfad ist Preoperational -> Operational (PreoperationalToOperationalAuto)
        und innen Initialized -> Ready (InitializedToReadyAuto).
        """
        await self._set_outer("Preoperational", None)
        await self._set_outer("Operational", "PreoperationalToOperationalAuto: Preoperational -> Operational")
        await self._set_inner("Initialized", None)
        await self._set_inner("Ready", "InitializedToReadyAuto: Initialized -> Ready")

    async def to_single_execution(self) -> None:
        """Ready -> SingleExecution (ReadyToSingleExecution)."""
        await self._set_inner("SingleExecution", "ReadyToSingleExecution: Ready -> SingleExecution")

    async def to_ready(self) -> None:
        """SingleExecution -> Ready (SingleExecutionToReadyAuto)."""
        await self._set_inner("Ready", "SingleExecutionToReadyAuto: SingleExecution -> Ready")

    async def abort_to_ready(self) -> None:
        """SingleExecution -> Ready ueber den Abort-Uebergang."""
        await self._set_inner("Ready", "SingleExecutionToReadyAbort: SingleExecution -> Ready")

    async def to_error(self, reason: str) -> None:
        """Operational -> Error (OperationalToErrorAuto)."""
        await self._set_outer("Error", f"OperationalToErrorAuto: Operational -> Error ({reason})")

    async def recover(self, *, halt: bool = False) -> None:
        """Error -> Operational, oder mit `halt` Error -> Halted.

        Halted braucht anschliessend einen Operator-`Reset`; der Server bleibt
        dann bewusst nicht mehr betriebsbereit.
        """
        if halt:
            await self._set_outer("Halted", "ErrorToHaltedAuto: Error -> Halted")
            return
        await self._set_outer("Operational", "ErrorToOperationalAuto: Error -> Operational")

    def is_ready(self) -> bool:
        """Prueft synchron, ob ein Einzeljob erlaubt ist."""
        return self._outer_state == "Operational" and self._inner_state == "Ready"

    def state_names(self) -> tuple[str, str]:
        """Liefert die aktuellen Zustandsnamen beider Automaten."""
        return self._outer_state, self._inner_state
