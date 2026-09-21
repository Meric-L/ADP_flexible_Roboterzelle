"""Rudimentaere Umsetzung von OPC-UA-Programmen (Spec Part 10).

Uebernommen aus der Vorlage des Betreuers (`betreuer/OPC_UA_Program.py`,
Robert Knobloch, robert.knobloch@stud.tu-darmstadt.de, 2024).

Gegenueber der Vorlage geaendert -- jede Aenderung war noetig, damit sich das
Framework ueberhaupt bzw. sauber einbauen laesst:

1. `Program.__init__` ist konkret. Die Vorlage ruft in `init()`
   `cls(server, nsid, *args, **kwargs)` auf, ihr Beispiel `testProgram.py`
   definiert aber `__init__(self)` ohne Argumente und ruft `super().__init__()`
   auf einer ABC ganz ohne `__init__`. In dieser Kombination war sie nicht
   startbar.
2. `Resume` bekommt `resume_inputs`. Die Vorlage uebergab dort `reset_inputs`.
3. Optionale sprechende String-NodeIds (`nodeid=`). Damit bekommen Instanz und
   Methoden stabile, ratbare Ids (`ns=4;s=VisionProgram.Start`) und ein Client
   muss nicht browsen. Ohne `nodeid` bleibt es beim automatischen numerischen
   Verhalten der Vorlage.
4. Die fuenf gleichfoermigen Bloecke zum Ersetzen der Methoden sind zu einer
   Schleife zusammengezogen. Verhalten unveraendert.
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
    """Zugang zur Program-Funktionalitaet der UA-Spec Teil 10.

    Eigene Logik haengt in `start`/`halt`/`reset`/`suspend`/`resume`. Gibt eine
    dieser Methoden `None` zurueck, gilt sie als erfolgreich und der Automat
    wechselt den Zustand; gibt sie einen String zurueck, bleibt der Zustand und
    der String reist als `ProgramTransitionEvent` mit Severity 500 zum Client.

    Mit `init()` konstruieren, nicht direkt.
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
        """Legt das Programm im Adressraum an.

        Parameters
        ----------
        server
            Der OPC-UA-Server, dem das Programm hinzugefuegt wird.
        parent_node
            Knoten, unter dem das Programm als Kind haengt.
        nsid
            Namensraum, in den das Programm eingefuegt wird.
        name
            BrowseName der Instanz; ohne Angabe der Klassenname.
        nodeid
            Optionale feste NodeId der Instanz. Mit einer String-NodeId
            erben alle Kinder sprechende Ids.
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
        # Nur eine Steueraufgabe gleichzeitig: das Lock wird im Methoden-
        # Handler genommen und in `_transition` wieder freigegeben.
        self._lock = asyncio.Lock()
        self._control_task = None

        await self.state_machine.install(
            ua.ObjectIds.ProgramStateMachineType, True, nodeid=nodeid
        )
        if not self.state_machine._state_machine_node:
            logger.error("ProgramStateMachine wurde nicht erfolgreich installiert!")
            raise RuntimeError("ProgramStateMachine wurde nicht erfolgreich installiert!")

        # Die instanziierten Methoden tragen keine Eingabeargumente. Fuer
        # eigene Signaturen (z. B. `Start(RecipeId, Continuous)`) muessen sie
        # geloescht und neu angelegt werden.
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

    def get_current_state(self) -> State:
        return self.state_machine._current_state

    async def running_to_halted(self, message=None):
        """Haelt das laufende Programm an (z. B. im Fehlerfall)."""
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
        """Setzt das laufende Programm zurueck auf `Ready`."""
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
        """Setzt das pausierte Programm zurueck auf `Ready`."""
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
        """Laeuft im Hintergrund je Methodenaufruf.

        Fuehrt die benutzerdefinierte Funktion aus, prueft ihren Erfolg und
        wechselt den Zustand.
        """
        try:
            try:
                result: str | None = await func(*args)
            except Exception as e:
                result = str(e)
            if result is not None:
                if not isinstance(result, str):
                    raise TypeError("Program function returns wrong type")
                # Der Zustandswechsel hat nicht geklappt -- als Event melden.
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
        self._control_task = asyncio.create_task(
            self._transition(self.start, sm.running, sm.ready_to_running, *args)
        )

    async def _suspend_method(self, _parent_nodeid: ua.NodeId, *args):
        await self._lock.acquire()
        sm = self.state_machine
        match sm._current_state.number:
            case 13:
                self._control_task = asyncio.create_task(
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
                self._control_task = asyncio.create_task(
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
                self._control_task = asyncio.create_task(
                    self._transition(self.halt, sm.halted, sm.ready_to_halted, *args)
                )
            case 13:
                self._control_task = asyncio.create_task(
                    self._transition(self.halt, sm.halted, sm.running_to_halted, *args)
                )
            case 14:
                self._control_task = asyncio.create_task(
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
                self._control_task = asyncio.create_task(
                    self._transition(self.reset, sm.ready, sm.halted_to_ready, *args)
                )
            case _:
                self._lock.release()
                return ua.StatusCode(ua.StatusCodes.BadInvalidState)


class ProgramStateMachine(FiniteStateMachine):
    """Program State Machine nach OPC-UA Teil 10.

    Zustaende und Uebergaenge sind feste Knoten des Basis-Namensraums (ns=0);
    es wird kein Nodeset importiert.
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
        """NodeId einer neu angelegten Methode.

        Traegt die Instanz eine String-NodeId, erbt die Methode sie als
        `<Instanz>.<Methode>` -- sonst faellt es auf die automatische
        numerische Vergabe der Vorlage zurueck.
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
        """Legt alle noetigen Knoten, Zustaende und Uebergaenge an."""
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
        # Nicht `set_available_transitions()`: in asyncua 2.0.1 schreibt die
        # Methode den Wert und wirft danach **immer** ValueError, weil das
        # `raise` nicht im else-Zweig steht (statemachine.py:357-363). Die
        # Vorlage des Betreuers lief noch gegen eine aeltere Version.
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
        """Markiert nur die Methoden als ausfuehrbar, die es sein duerfen."""
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
        """Wechselt den Zustand und zieht die Ausfuehrbarkeit nach."""
        await super().change_state(state, transition, event_msg, severity)
        await self.check_method_executability()
