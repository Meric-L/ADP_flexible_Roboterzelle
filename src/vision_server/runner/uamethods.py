"""Building blocks for the OPC UA methods of the vision system: replies, registration, JSON wrapper.

Every method replies with `Error` (Int32) and, where there is a result, a string
(JSON) before it. Asyncua does not validate the return value. It must be a tuple
of `ua.Variant`, because a list would be packed as one single variant.

`json_method` is the common frame of the methods that take a JSON request
(`MeasureBoard`, `MeasureTags`, `MeasureTagReference`, `PlanTagReferenceView`,
`SetTagReferencePreview`): read the request, check the operating state, check
the lock, then do the actual work. Error texts and status codes are defined by
the callers; the frame only ensures the same order of checks.
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
    """Reply with only `Error` (Int32)."""
    return (ua.Variant(int(code), ua.VariantType.Int32),)


def text_reply(text: str, code: VisionErrorCode) -> tuple[ua.Variant, ua.Variant]:
    """Reply with a string (usually JSON) and `Error` (Int32)."""
    return (
        ua.Variant(text, ua.VariantType.String),
        ua.Variant(int(code), ua.VariantType.Int32),
    )


def repo_relative(path: Path) -> str:
    """Return the path relative to the repo (POSIX). Outside the repo, the absolute path."""
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
    """Add a method under the vision system and record the node in `methods`.

    The NodeId is `<vision system name>.<method>` in our own namespace. It is
    explicit instead of the running number that `add_method(own_idx, ...)` would
    assign. That number shifts as soon as someone inserts a node before it, and the
    frontend addresses the methods by fixed addresses. The entries of `methods`
    also appear under `VisionProgram`, in the order of creation.
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
    """Return the handler of a method that takes a JSON request as its only argument.

    The order of a call is exactly this:

    1. `parse(text)` reads the request. A `ValueError` is logged as
       `"<name> abgelehnt: ..."` and answered with `INVALID_ARGUMENT`.
    2. Not `Operational`: `INVALID_STATE` ("Vision-System nicht betriebsbereit").
    3. `busy()` (if given) is true: `BUSY` with `busy_message`.
    4. `run(request, text)` does the work and returns the finished reply.

    `refuse(code, message)` builds the reply for the rejections 1 to 3 (with the
    result JSON of the method, or only the code). `text` is the argument. A missing
    value counts as an empty string.
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
