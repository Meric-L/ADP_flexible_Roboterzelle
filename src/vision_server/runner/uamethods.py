"""Bausteine fuer die OPC-UA-Methoden des Vision-Systems: Antworten, Anlage, JSON-Wrapper.

Alle Methoden antworten mit `Error` (Int32) und, wo es ein Ergebnis gibt,
davor mit einem String (JSON). Asyncua validiert die Rueckgabe nicht; sie muss
ein Tupel aus `ua.Variant` sein -- eine Liste wuerde als ein einziger Variant
verpackt.

`json_method` ist der gemeinsame Rahmen der Methoden mit einer JSON-Anfrage
(`MeasureBoard`, `MeasureTags`, `MeasureTagReference`, `PlanTagReferenceView`,
`SetTagReferencePreview`): Anfrage lesen, Betriebszustand pruefen, Sperre
pruefen, dann die eigentliche Arbeit. Fehlertexte und Statuscodes stehen bei
den Aufrufern; der Rahmen sorgt nur fuer die gleiche Reihenfolge der Pruefungen.
"""

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode
from ..profiles import _REPO_ROOT
from ..state_machine import VisionStateMachines

_log = logging.getLogger(__package__)


def code_reply(code: VisionErrorCode) -> tuple[ua.Variant]:
    """Antwort mit nur `Error` (Int32)."""
    return (ua.Variant(int(code), ua.VariantType.Int32),)


def text_reply(text: str, code: VisionErrorCode) -> tuple[ua.Variant, ua.Variant]:
    """Antwort mit einem String (meist JSON) und `Error` (Int32)."""
    return (
        ua.Variant(text, ua.VariantType.String),
        ua.Variant(int(code), ua.VariantType.Int32),
    )


def repo_relative(path: Path) -> str:
    """Pfad relativ zum Repo (POSIX); ausserhalb des Repos der absolute Pfad."""
    try:
        return path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


async def add_vision_method(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    methods: dict[str, Node],
    name: str,
    handler: Callable,
    inputs: list[ua.VariantType],
    outputs: list[ua.VariantType],
) -> Node:
    """Legt eine Methode unter dem Vision-System an und merkt den Knoten in `methods`.

    Die NodeId ist `<Name des Vision-Systems>.<Methode>` im eigenen Namensraum --
    explizit statt der laufenden Nummer, die `add_method(own_idx, ...)` vergeben
    wuerde: die verschiebt sich, sobald jemand davor einen Knoten einfuegt, und
    das Frontend spricht die Methoden ueber feste Adressen an. Die Eintraege von
    `methods` erscheinen auch unter `VisionProgram`, in der Reihenfolge der Anlage.
    """
    node = await space.vision_system.add_method(
        ua.NodeId(f"{config.vision_system_name}.{name}", space.own_idx),
        ua.QualifiedName(name, space.own_idx),
        handler,
        inputs,
        outputs,
    )
    methods[name] = node
    return node


def json_method(
    name: str,
    states: VisionStateMachines,
    *,
    parse: Callable[[str], Any],
    refuse: Callable[[VisionErrorCode, str], tuple],
    run: Callable[[Any, str], Awaitable[tuple]],
    busy: Callable[[], bool] | None = None,
    busy_message: str = "",
) -> Callable:
    """Handler einer Methode mit einer JSON-Anfrage als einzigem Argument.

    Ablauf eines Aufrufs, in genau dieser Reihenfolge:

    1. `parse(text)` liest die Anfrage; ein `ValueError` wird mit
       `"<name> abgelehnt: ..."` geloggt und mit `INVALID_ARGUMENT` beantwortet
    2. nicht `Operational`: `INVALID_STATE` ("Vision-System nicht betriebsbereit")
    3. `busy()` (falls angegeben) wahr: `BUSY` mit `busy_message`
    4. `run(anfrage, text)` macht die Arbeit und liefert die fertige Antwort

    `refuse(code, message)` baut die Antwort der Ablehnungen 1 bis 3 (mit dem
    Ergebnis-JSON der Methode oder nur dem Code). `text` ist das Argument, ein
    fehlender Wert gilt als leerer String.
    """

    @uamethod
    async def method(parent, request_json: str):
        text = request_json or ""
        try:
            request = parse(text)
        except ValueError as error:
            _log.warning("%s abgelehnt: %s", name, error)
            return refuse(VisionErrorCode.INVALID_ARGUMENT, str(error))
        if states.state_names()[0] != "Operational":
            return refuse(VisionErrorCode.INVALID_STATE, "Vision-System nicht betriebsbereit")
        if busy is not None and busy():
            return refuse(VisionErrorCode.BUSY, busy_message)
        return await run(request, text)

    return method
