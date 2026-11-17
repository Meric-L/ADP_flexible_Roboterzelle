"""Minimal implementation of OPC UA programs (Spec Part 10).

Taken over from the supervisor's template (`OPC_UA_Program.py`, Robert
Knobloch, robert.knobloch@stud.tu-darmstadt.de, 2024). The template is not in
the repo (`betreuer/` was removed on 23.09.2026; the file was never tracked).

Changes compared to the template. Each one was needed so that the framework
can be used at all, or cleanly:

1. `Program.__init__` is concrete. The template calls
   `cls(server, nsid, *args, **kwargs)` in `init()`, but its example
   `testProgram.py` defines `__init__(self)` without arguments and calls
   `super().__init__()` on an ABC that has no `__init__` at all. In this
   combination it could not be started.
2. `Resume` takes `resume_inputs`. The template passed `reset_inputs` there.
3. Optional descriptive string NodeIds (`nodeid=`). The instance and its
   methods then get stable, guessable ids (`ns=4;s=VisionProgram.Start`), and
   a client does not have to browse. Without `nodeid`, the template's automatic
   numeric behavior is kept.
4. The five identical blocks that replace the methods are merged into one
   loop. Behavior unchanged.
"""

from abc import ABC, abstractmethod
from typing import Union
import asyncio
import logging

from asyncua.common.statemachine import FiniteStateMachine, State, Transition
from asyncua.common.event_objects import ProgramTransitionEvent
from asyncua import ua, Node, Server, uamethod

logger = logging.getLogger(__name__)


class ProgramException(Exception):
    pass


class Program(ABC):
    """Access to the Program functionality of UA Spec Part 10.

    Custom logic goes into `start`/`halt`/`reset`/`suspend`/`resume`. If one of
    these methods returns `None`, it counts as successful and the state machine
    changes state. If it returns a string, the state stays and the string is
    sent to the client as a `ProgramTransitionEvent` with severity 500.

    Construct with `init()`, not directly.
    """

    @classmethod
    async def init(
        cls,
        server: Server,
        parent_node: Node,
        nsid: int,
        name: str | None = None,
        *args,
        nodeid: ua.NodeId | None = None,
        **kwargs,
    ):
        """Create the program in the address space.

        Args:
            server: The OPC UA server the program is added to.
            parent_node: Node under which the program is attached as a child.
            nsid: Namespace index the program is inserted into.
            name: BrowseName of the instance. Defaults to the class name.
            nodeid: Optional fixed NodeId for the instance. With a string NodeId,
                all children inherit descriptive ids.
        """
        self = cls(server, nsid, *args, **kwargs)
        await self._init(server, parent_node, nsid, name, nodeid)
        return self

    def __init__(self, server: Server, nsid: int) -> None:
        self._server = server
        self.nsid = nsid

    async def _init(self, server, parent_node, nsid, name, nodeid=None):
        self.nsid = nsid
        machine_name = name if name is not None else self.__class__.__name__

        self.state_machine = ProgramStateMachine(server, parent_node, nsid, machine_name)
        # Only one control task at a time: the lock is taken in the method
        # handler and released again in `_transition`.
        self._lock = asyncio.Lock()
        # Strong references to running control tasks; asyncio only keeps weak ones.
        self._control_tasks: set[asyncio.Task] = set()

        await self.state_machine.install(
            ua.ObjectIds.ProgramStateMachineType, True, nodeid=nodeid
        )
        if not self.state_machine._state_machine_node:
            logger.error("ProgramStateMachine wurde nicht erfolgreich installiert!")
            raise RuntimeError("ProgramStateMachine wurde nicht erfolgreich installiert!")

        # The instantiated methods carry no input arguments. For custom
        # signatures (e.g. `Start(RecipeId, Continuous)`) they must be deleted
        # and added again.
        for method_name, attr, handler, inputs in (
            ("Start", "_start_method", self._start_method, self.start_inputs),
            ("Suspend", "_suspend_method", self._suspend_method, self.suspend_inputs),
            ("Resume", "_resume_method", self._resume_method, self.resume_inputs),
            ("Halt", "_halt_method", self._halt_method, self.halt_inputs),
            ("Reset", "_reset_method", self._reset_method, self.reset_inputs),
        ):
            await getattr(self.state_machine, attr).delete()
            replacement = await self.state_machine._state_machine_node.add_method(
                self.state_machine.method_nodeid(method_name),
                ua.QualifiedName(method_name, nsid),
                uamethod(handler),
                list(inputs),
                [],
            )
            setattr(self.state_machine, attr, replacement)

        methods = await self.state_machine._state_machine_node.get_methods()
        logger.info("Programm '%s': Methoden %s", machine_name, methods)

        await self.state_machine.check_method_executability()

    @abstractmethod
    async def reset(self, *args) -> str | None: ...

    @property
    @abstractmethod
    def reset_inputs(self) -> list: ...

    @abstractmethod
    async def start(self, *args) -> str | None: ...

    @property
    @abstractmethod
    def start_inputs(self) -> list: ...

    @abstractmethod
    async def halt(self, *args) -> str | None: ...

    @property
    @abstractmethod
    def halt_inputs(self) -> list: ...

    @abstractmethod
    async def suspend(self, *args) -> str | None: ...

    @property
    @abstractmethod
    def suspend_inputs(self) -> list: ...

    @abstractmethod
    async def resume(self, *args) -> str | None: ...

    @property
    @abstractmethod
    def resume_inputs(self) -> list: ...

    async def running_to_halted(self, message=None):
        """Halt the running program (e.g. on error)."""
        if not self.state_machine._current_state.number == 13:
            raise ProgramException("Program not in running state")
        if message is None:
            message = "internal change"
        await self.state_machine.change_state(
            self.state_machine.halted,
            self.state_machine.running_to_halted,
            f"{self.state_machine.running_to_halted.name} ; {message}",
        )

    async def running_to_ready(self, message=None):
        """Reset the running program to `Ready`."""
        if not self.state_machine._current_state.number == 13:
            raise ProgramException("Program not in running state")
        if message is None:
            message = "internal change"
        await self.state_machine.change_state(
            self.state_machine.ready,
            self.state_machine.running_to_ready,
            f"{self.state_machine.running_to_ready.name} ; {message}",
        )

    async def suspended_to_ready(self, message=None):
        """Reset the suspended program to `Ready`."""
        if not self.state_machine._current_state.number == 14:
            raise ProgramException("Program not in suspended state")
        if message is None:
            message = "internal change"
        await self.state_machine.change_state(
            self.state_machine.ready,
            self.state_machine.suspended_to_ready,
            f"{self.state_machine.suspended_to_ready.name} ; {message}",
        )

    async def _transition(self, func, new_state, transition, *args):
        """Run in the background for each method call.

        Runs the user-defined function, checks its result and changes the state.
        """
        try:
            try:
                result: str | None = await func(*args)
            except Exception as e:
                # The error text is passed as the result to the state change. It
                # is also logged, so the cause is not lost.
                logger.warning("Programmfunktion warf einen Fehler: %s", e, exc_info=True)
                result = str(e)
            if result is not None:
                if not isinstance(result, str):
                    raise TypeError("Program function returns wrong type")
                # The state change did not work -- report it as an event.
                self.state_machine._evgen.event.Message = ua.LocalizedText(
                    result, self.state_machine.locale
                )
                self.state_machine._evgen.event.Severity = 500
                self.state_machine._evgen.event.ToState = ua.LocalizedText(
                    new_state.name, self.state_machine.locale
                )
                if transition:
                    self.state_machine._evgen.event.Transition = ua.LocalizedText(
                        transition.name, self.state_machine.locale
                    )
                self.state_machine._evgen.event.FromState = ua.LocalizedText(
                    self.state_machine._current_state.name
                )
                await self.state_machine._evgen.trigger()
            else:
                await self.state_machine.change_state(new_state, transition, transition.name)
        finally:
            self._lock.release()

    async def _start_method(self, _parent_nodeid: ua.NodeId, *args):
        await self._lock.acquire()
        sm = self.state_machine
        if sm._current_state.number != 12:  # 12 = Ready
            self._lock.release()
            logger.error(
                "Start abgelehnt: Programm nicht im Ready-Zustand (aktuell: %s)",
                sm._current_state.name,
            )
            return ua.StatusCode(ua.StatusCodes.BadInvalidState)
        await self._transition(self.start, sm.running, sm.ready_to_running, *args)

    def _spawn_transition(self, transition) -> None:
        """Run a control transition as a task and keep it referenced until it ends."""
        task = asyncio.create_task(transition)
        self._control_tasks.add(task)
        task.add_done_callback(self._control_tasks.discard)

    async def _suspend_method(self, _parent_nodeid: ua.NodeId, *args):
        await self._lock.acquire()
        sm = self.state_machine
        match sm._current_state.number:
            case 13:
                self._spawn_transition(
                    self._transition(self.suspend, sm.suspended, sm.running_to_suspended, *args)
                )
            case _:
                self._lock.release()
                return ua.StatusCode(ua.StatusCodes.BadInvalidState)

    async def _resume_method(self, _parent_nodeid: ua.NodeId, *args):
        await self._lock.acquire()
        sm = self.state_machine
        match sm._current_state.number:
            case 14:
                self._spawn_transition(
                    self._transition(self.resume, sm.running, sm.suspended_to_running, *args)
                )
            case _:
                self._lock.release()
                return ua.StatusCode(ua.StatusCodes.BadInvalidState)

    async def _halt_method(self, _parent_nodeid: ua.NodeId, *args):
        await self._lock.acquire()
        sm = self.state_machine
        match sm._current_state.number:
            case 12:
                self._spawn_transition(
                    self._transition(self.halt, sm.halted, sm.ready_to_halted, *args)
                )
            case 13:
                self._spawn_transition(
                    self._transition(self.halt, sm.halted, sm.running_to_halted, *args)
                )
            case 14:
                self._spawn_transition(
                    self._transition(self.halt, sm.halted, sm.suspended_to_halted, *args)
                )
            case _:
                self._lock.release()
                return ua.StatusCode(ua.StatusCodes.BadInvalidState)

    async def _reset_method(self, _parent_nodeid: ua.NodeId, *args):
        await self._lock.acquire()
        sm = self.state_machine
        match sm._current_state.number:
            case 11:
                self._spawn_transition(
                    self._transition(self.reset, sm.ready, sm.halted_to_ready, *args)
                )
            case _:
                self._lock.release()
                return ua.StatusCode(ua.StatusCodes.BadInvalidState)


class ProgramStateMachine(FiniteStateMachine):
    """Program state machine according to OPC UA Part 10.

    States and transitions are fixed nodes of the base namespace (ns=0). No
    nodeset is imported.
    """

    def __init__(self, server=None, parent=None, idx=None, name=None):
        super().__init__(server, parent, idx, name)
        if name is None:
            self._name = "ProgramStateMachine"
        self._state_machine_type = ua.NodeId(2391, 0)
        self.evtype = ProgramTransitionEvent()

        self.snode11 = self._server.get_node(2406)
        self.snode12 = self._server.get_node(2400)
        self.snode13 = self._server.get_node(2402)
        self.snode14 = self._server.get_node(2404)
        self.tnode1 = self._server.get_node(2408)
        self.tnode2 = self._server.get_node(2410)
        self.tnode3 = self._server.get_node(2412)
        self.tnode4 = self._server.get_node(2414)
        self.tnode5 = self._server.get_node(2416)
        self.tnode6 = self._server.get_node(2418)
        self.tnode7 = self._server.get_node(2420)
        self.tnode8 = self._server.get_node(2422)
        self.tnode9 = self._server.get_node(2424)

        self.halted = State(self.snode11.nodeid, "Halted", 11, self.snode11)
        self.ready = State(self.snode12.nodeid, "Ready", 12, self.snode12)
        self.running = State(self.snode13.nodeid, "Running", 13, self.snode13)
        self.suspended = State(self.snode14.nodeid, "Suspended", 14, self.snode14)

        self.halted_to_ready = Transition(self.tnode1.nodeid, "HaltedToReady", 1, self.tnode1)
        self.ready_to_running = Transition(self.tnode2.nodeid, "ReadyToRunning", 2, self.tnode2)
        self.running_to_halted = Transition(self.tnode3.nodeid, "RunningToHalted", 3, self.tnode3)
        self.running_to_ready = Transition(self.tnode4.nodeid, "RunningToReady", 4, self.tnode4)
        self.running_to_suspended = Transition(
            self.tnode5.nodeid, "RunningToSuspended", 5, self.tnode5
        )
        self.suspended_to_running = Transition(
            self.tnode6.nodeid, "SuspendedToRunning", 6, self.tnode6
        )
        self.suspended_to_halted = Transition(
            self.tnode7.nodeid, "SuspendedToHalted", 7, self.tnode7
        )
        self.suspended_to_ready = Transition(
            self.tnode8.nodeid, "SuspendedToReady", 8, self.tnode8
        )
        self.ready_to_halted = Transition(self.tnode9.nodeid, "ReadyToHalted", 9, self.tnode9)

    def method_nodeid(self, name: str):
        """Return the NodeId of a newly added method.

        If the instance has a string NodeId, the method inherits it as
        `<instance>.<method>`. Otherwise it falls back to the template's
        automatic numeric assignment.
        """
        base = self._state_machine_node.nodeid
        if isinstance(base.Identifier, str):
            return ua.NodeId(f"{base.Identifier}.{name}", base.NamespaceIndex)
        return self._idx

    async def install(
        self,
        program_type: ua.NodeId,
        optionals: bool = False,
        nodeid: ua.NodeId | None = None,
    ):
        """Create all required nodes, states and transitions."""
        self._optionals = optionals

        self._state_machine_node = await self._parent.add_object(
            nodeid if nodeid is not None else self._idx,
            ua.QualifiedName(self._name, self._idx),
            objecttype=program_type,
            instantiate_optional=optionals,
        )
        if self._optionals:
            self._last_transition_node = await self._state_machine_node.get_child(
                ["LastTransition"]
            )
            self._last_transition_transitiontime_node = (
                await self._last_transition_node.get_child("TransitionTime")
            )

        await self.init(self._state_machine_node)

        await self.set_available_states(
            [
                self.snode11.nodeid,
                self.snode12.nodeid,
                self.snode13.nodeid,
                self.snode14.nodeid,
            ]
        )
        # Not `set_available_transitions()`: in asyncua 2.0.1 that method writes
        # the value and then **always** raises ValueError, because the `raise`
        # is not in the else branch (statemachine.py:357-363). The supervisor's
        # template still ran against an older version.
        if self._optionals:
            transitions_node = await self._state_machine_node.get_child(
                ["AvailableTransitions"]
            )
            await transitions_node.write_value(
                [
                    self.tnode1.nodeid,
                    self.tnode2.nodeid,
                    self.tnode3.nodeid,
                    self.tnode4.nodeid,
                    self.tnode5.nodeid,
                    self.tnode6.nodeid,
                    self.tnode7.nodeid,
                    self.tnode8.nodeid,
                    self.tnode9.nodeid,
                ],
                varianttype=ua.VariantType.NodeId,
            )

        self._start_method = await self._state_machine_node.get_child("Start")
        self._suspend_method = await self._state_machine_node.get_child("Suspend")
        self._resume_method = await self._state_machine_node.get_child("Resume")
        self._halt_method = await self._state_machine_node.get_child("Halt")
        self._reset_method = await self._state_machine_node.get_child("Reset")

        await self._write_state(self.ready)
        self._current_state = self.ready
        await self.check_method_executability()

    async def check_method_executability(self):
        """Mark only the methods that may be executed in the current state as executable."""
        executable = {
            11: {"reset": True, "halt": False, "suspend": False, "resume": False, "start": False},
            12: {"reset": False, "halt": True, "suspend": False, "resume": False, "start": True},
            13: {"reset": False, "halt": True, "suspend": True, "resume": False, "start": False},
            14: {"reset": False, "halt": True, "suspend": False, "resume": True, "start": False},
        }.get(self._current_state.number)
        if executable is None:
            return
        for key, node in (
            ("reset", self._reset_method),
            ("halt", self._halt_method),
            ("suspend", self._suspend_method),
            ("resume", self._resume_method),
            ("start", self._start_method),
        ):
            await node.write_attribute(
                ua.AttributeIds.Executable, ua.DataValue(ua.Variant(executable[key]))
            )

    async def change_state(
        self,
        state: State,
        transition: Transition = None,
        event_msg: Union[str, ua.LocalizedText] = None,
        severity: int = 500,
    ):
        """Change the state and update the executable flags of the methods."""
        await super().change_state(state, transition, event_msg, severity)
        await self.check_method_executability()
