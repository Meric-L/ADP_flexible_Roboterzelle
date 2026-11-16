"""Binds the two OPC 40100 state machines to the nodeset instance.

Transitions are logged in the event message, not in a `Transition` object.
"""

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
    STATE_CONTINUOUS_EXECUTION,
    STATE_SINGLE_EXECUTION,
    mv,
)

_log = logging.getLogger(__name__)


async def _load_state(node: Node) -> State:
    """Read the BrowseName and StateNumber of a state node."""
    browse_name = (await node.read_browse_name()).Name
    state_number = await (await node.get_child(["StateNumber"])).read_value()
    return State(id=None, name=browse_name, number=state_number, node=node)


async def _bind(server: Server, node: Node, name: str, emitting_node: Node, mv_idx: int) -> FiniteStateMachine:
    """Bind a FiniteStateMachine to an existing state machine node.

    `install()` would create a second state machine tree. Instead the node from
    the nodeset instance is set and only `init()` is called. Then the event
    generator is redirected. asyncua creates it in `init()` on (TransitionEventType,
    state machine node). A client subscribed on the VisionSystem would not see
    those events, because asyncua delivers events only to the exact
    `emitting_node`.
    """
    fsm = FiniteStateMachine(server, parent=node, name=name)
    fsm._state_machine_node = node
    await fsm.init(node)
    fsm._evgen = await server.get_event_generator(mv(EVENT_STATE_CHANGED, mv_idx), emitting_node)
    return fsm


class VisionStateMachines:
    """Drives the VisionStateMachine and the AutomaticModeStateMachine together.

    State changes run without a `Transition` object. The nodeset instance has no
    optional `LastTransition` node, and asyncua's `_write_transition` would fail
    on it. The transition name is carried in the event message instead.
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
        """Bind both state machines and resolve all state nodes."""
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
                ("ContinuousExecution", STATE_CONTINUOUS_EXECUTION),
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
        """Move both state machines to the operational state.

        The nodeset has no Halted -> Operational transition. The compliant start
        path is Preoperational -> Operational (PreoperationalToOperationalAuto),
        then Initialized -> Ready inside (InitializedToReadyAuto).
        """
        await self._set_outer("Preoperational", None)
        await self._set_outer("Operational", "PreoperationalToOperationalAuto: Preoperational -> Operational")
        await self._set_inner("Initialized", None)
        await self._set_inner("Ready", "InitializedToReadyAuto: Initialized -> Ready")

    async def to_single_execution(self) -> None:
        """Transition Ready -> SingleExecution (ReadyToSingleExecution)."""
        await self._set_inner("SingleExecution", "ReadyToSingleExecution: Ready -> SingleExecution")

    async def to_ready(self) -> None:
        """Transition SingleExecution -> Ready (SingleExecutionToReadyAuto)."""
        await self._set_inner("Ready", "SingleExecutionToReadyAuto: SingleExecution -> Ready")

    async def to_continuous_execution(self) -> None:
        """Transition Ready -> ContinuousExecution (ReadyToContinuousExecution)."""
        await self._set_inner(
            "ContinuousExecution", "ReadyToContinuousExecution: Ready -> ContinuousExecution"
        )

    async def continuous_to_ready(self, *, stopped: bool = True) -> None:
        """Transition ContinuousExecution -> Ready, via Stop or Abort.

        Continuous execution never ends on its own. It runs until someone stops
        it, so there is no automatic transition as there is for SingleExecution.
        """
        transition = "Stop" if stopped else "Abort"
        await self._set_inner(
            "Ready",
            f"ContinuousExecutionToReady{transition}: ContinuousExecution -> Ready",
        )

    async def halt(self) -> None:
        """Transition Operational -> Halted. The server no longer accepts jobs."""
        await self._set_outer("Halted", "OperationalToHaltedAuto: Operational -> Halted")


    async def abort_to_ready(self) -> None:
        """Transition SingleExecution -> Ready via the Abort transition."""
        await self._set_inner("Ready", "SingleExecutionToReadyAbort: SingleExecution -> Ready")

    async def stop_to_ready(self) -> None:
        """Transition SingleExecution -> Ready via the Stop transition (user cancel)."""
        await self._set_inner("Ready", "SingleExecutionToReadyStop: SingleExecution -> Ready")

    async def to_error(self, reason: str) -> None:
        """Transition Operational -> Error (OperationalToErrorAuto)."""
        await self._set_outer("Error", f"OperationalToErrorAuto: Operational -> Error ({reason})")

    async def recover(self, *, halt: bool = False) -> None:
        """Transition Error -> Operational, or Error -> Halted when `halt` is set.

        Halted needs an operator `Reset` afterwards. The server then deliberately
        stays out of operation.
        """
        if halt:
            await self._set_outer("Halted", "ErrorToHaltedAuto: Error -> Halted")
            return
        await self._set_outer("Operational", "ErrorToOperationalAuto: Error -> Operational")

    def is_ready(self) -> bool:
        """Check synchronously whether a single job is allowed."""
        return self._outer_state == "Operational" and self._inner_state == "Ready"

    def state_names(self) -> tuple[str, str]:
        """Return the current state names of both state machines."""
        return self._outer_state, self._inner_state
