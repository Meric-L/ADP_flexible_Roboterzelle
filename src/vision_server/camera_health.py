"""Camera state as `DeviceHealth` per OPC 40100-2 (AMCM).

The watchdog in `camera.py` knows whether the camera delivers images, hangs, or is
currently being reopened. This module translates that into DI's
`DeviceHealthEnumeration` and writes it to the state nodes of the asset view
(`asset_model.py`). This way a generic OPC UA client without knowledge of this repo
can see whether the camera is working.

Why a separate module and a separate task, instead of hooking this onto the live
stream like the calibration progress:

* The live stream is a debugging aid and may be absent (`camera_stream` not
  configured), while the detection source does hold a camera. A maintenance view
  must not depend on that.
* The cadences do not match: the stream deliberately writes on every tick, this node
  only on a state change.

On the similarity to the retired legacy item A3 (`RaspiDevice/Counter`, a 1 Hz
counter): A3 was a value *without a source* that merely proved some loop was running.
Here there is a source, and in the idle state nothing at all is written -- after the
first frame the node stays silent until the state really changes.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from asyncua import Server, ua
from asyncua.common.node import Node

from .aio import cancel_and_wait
from .camera import CameraStatus, SharedCamera
from .nodeset_ids import DeviceHealth
from .profiles import CameraStreamConfig

_log = logging.getLogger(__name__)

#: Time limit for the last FAILURE write before the process is terminated. We are on the
#: way out -- a hanging write must not prevent the termination, otherwise exactly the
#: process that systemd is supposed to restart would stay stuck.
GIVE_UP_WRITE_TIMEOUT_S = 1.0

#: Severity per state (OPC UA: 1-1000). FAILURE is the failure, OFF_SPEC the reported
#: deviation, CHECK_FUNCTION the announced intervention -- in descending urgency, so that
#: a HMI can sort them without knowing NE 107.
ALARM_SEVERITY: dict[DeviceHealth, int] = {
    DeviceHealth.FAILURE: 900,
    DeviceHealth.OFF_SPEC: 500,
    DeviceHealth.CHECK_FUNCTION: 300,
}

#: Plain-text message per state for the `Message` field of the alarm.
ALARM_MESSAGE: dict[DeviceHealth, str] = {
    DeviceHealth.FAILURE: "Kamera liefert kein Bild",
    DeviceHealth.OFF_SPEC: "Kamerabild ist veraltet",
    DeviceHealth.CHECK_FUNCTION: "Kamera wird geoeffnet oder neu gestartet",
}


def device_health(status: CameraStatus, *, stale_frame_s: float) -> DeviceHealth:
    """Map the camera state to DI's `DeviceHealthEnumeration`.

    The order of the checks is the statement: first the final state, then the
    transient one, finally the normal case. Reasoning per line from NAMUR NE 107
    resp. OPC 10000-100:

    * `gave_up` -- the reopen budget is exhausted, the process terminates right
      away. No valid output signal, and nobody is working on it: FAILURE.
    * not `running` -- the camera was never opened or the source did not come up.
      The same case, different cause.
    * no handle -- a `_reopen` is in progress. NE 107 literally names CHECK_FUNCTION
      "output signal temporarily invalid due to on-going work on the device".
    * no frame yet -- warm-up. Not an error, just no image yet; that is why
      CHECK_FUNCTION is also the initial value of the nodes.
    * frame too old -- the watchdog has detected a stall but does not escalate yet.
      The self-diagnosis reports a deviation: OFF_SPEC. FAILURE would be defensible,
      but stalls demonstrably heal themselves (see
      `test_a_frame_between_hangs_resets_the_reopen_budget`); with OFF_SPEC an HMI can
      distinguish "currently stalling" from "dead" and gets roughly `frame_timeout_s`
      of advance warning. Whoever wants it otherwise changes this one line.

    `MAINTENANCE_REQUIRED` is never returned: that would need a wear counter, and
    `RemainingLifeTime` is deliberately not created.
    """
    if status.gave_up:
        return DeviceHealth.FAILURE
    if not status.running:
        return DeviceHealth.FAILURE
    if not status.has_handle:
        return DeviceHealth.CHECK_FUNCTION
    if status.frame_age_s is None:
        return DeviceHealth.CHECK_FUNCTION
    if status.frame_age_s > stale_frame_s:
        return DeviceHealth.OFF_SPEC
    return DeviceHealth.NORMAL


async def write_device_health(nodes: Sequence[Node], value: DeviceHealth) -> None:
    """Write the same value to all state nodes. Never raises.

    A node that cannot be written is a cosmetic defect in an auxiliary view -- it must
    not take down the job path or the remaining nodes with it.
    """
    for node in nodes:
        try:
            await node.write_value(ua.Variant(int(value), ua.VariantType.Int32))
        except Exception:
            _log.exception(
                "DeviceHealth an %s nicht schreibbar", node.nodeid.to_string()
            )


class HealthAlarms:
    """Fire the matching DI alarm for each state and clear the previous one.

    The second part of DI's `IDeviceHealthType`. The state variable says *what*
    currently applies; the alarm reports the transition -- with timestamp, source,
    severity and message text, without a client having to poll.

    At most **one** alarm is active at any time, since the state is one of five.
    `NORMAL` fires no alarm, it only clears the previous one.

    Emitted by the passed-in `emitter` -- in practice `VisionMachine`, because clients
    subscribe to exactly this node anyway (see
    `doc/project_docs/interfaces/vision-server-interface.md` 7.2). `SourceNode` still names the
    component concerned.

    Never raises: a failed alarm must not take down the state variable or the job path.
    """

    def __init__(
        self,
        server: Server,
        emitter: Node,
        alarms: Mapping[DeviceHealth, Node],
        source: Node | None = None,
    ) -> None:
        self._server = server
        self._emitter = emitter
        self._alarms = dict(alarms)
        self._source = source
        self._generators: dict[DeviceHealth, Any] = {}
        self._active: DeviceHealth | None = None

    async def _generator(self, value: DeviceHealth):
        """Return the event generator for an alarm type, built on first use."""
        if value not in self._generators:
            node = self._alarms[value]
            etype = await node.read_type_definition()
            self._generators[value] = await self._server.get_event_generator(
                self._server.get_node(etype), self._emitter
            )
        return self._generators[value]

    async def _fire(self, value: DeviceHealth, *, active: bool, reason: str) -> None:
        """Trigger the alarm for `value`, activating or clearing it."""
        if value not in self._alarms:
            return
        generator = await self._generator(value)
        event = generator.event
        event.Severity = ALARM_SEVERITY.get(value, 500)
        event.Retain = active
        event.ActiveState = ua.LocalizedText("Active" if active else "Inactive")
        event.Message = ua.LocalizedText(
            f"{ALARM_MESSAGE.get(value, value.name)} ({reason})"
            if active
            else f"{ALARM_MESSAGE.get(value, value.name)} behoben"
        )
        if self._source is not None:
            event.SourceNode = self._source.nodeid
        await generator.trigger()

    async def set(self, value: DeviceHealth, reason: str) -> None:
        """Adopt the new state: clear the old alarm, fire the new one."""
        if value == self._active:
            return
        try:
            if self._active is not None:
                await self._fire(self._active, active=False, reason=reason)
            if value is not DeviceHealth.NORMAL:
                await self._fire(value, active=True, reason=reason)
            self._active = value
        except Exception:
            _log.exception("Zustandsalarm fuer %s nicht ausloesbar", value.name)


class CameraHealthPublisher:
    """Mirror the state of a `SharedCamera` to `Health/DeviceHealth`.

    Writes only on state changes: in normal operation exactly once, when the first
    frame arrives, and silence thereafter.
    """

    def __init__(
        self,
        camera: SharedCamera,
        nodes: Sequence[Node],
        config: CameraStreamConfig,
        alarms: HealthAlarms | None = None,
    ) -> None:
        self._camera = camera
        self._nodes = tuple(nodes)
        self._config = config
        #: The standard-conforming event path; `None` if no alarms could be created --
        #: then the variable remains the only reporting path.
        self._alarms = alarms
        #: `None` means "nothing written yet" -- the first pass therefore always writes,
        #: even if it yields CHECK_FUNCTION and the nodes were already created with it.
        #: A subscriber that joins later should get the same value as one from the start.
        self._health: DeviceHealth | None = None
        self._task: asyncio.Task | None = None

    @property
    def health(self) -> DeviceHealth | None:
        """The last written value, `None` before the first pass."""
        return self._health

    def start(self) -> None:
        """Start the background publish loop."""
        self._task = asyncio.create_task(self._publish_loop())

    async def stop(self) -> None:
        """Stop the background publish loop."""
        if self._task is not None:
            await cancel_and_wait(self._task)
            self._task = None

    async def publish_now(self) -> None:
        """Run one pass outside the regular cadence. Never raises."""
        try:
            status = self._camera.status(asyncio.get_running_loop().time())
            value = device_health(status, stale_frame_s=self._config.stale_frame_s)
            if value == self._health:
                return
            _log.info(
                "Kamerazustand %s -> %s (%s)",
                "-" if self._health is None else self._health.name,
                value.name,
                status.last_outcome,
            )
            await write_device_health(self._nodes, value)
            if self._alarms is not None:
                await self._alarms.set(value, status.last_outcome)
            self._health = value
        except Exception:
            _log.exception("Kamerazustand konnte nicht veroeffentlicht werden")

    async def _publish_loop(self) -> None:
        while True:
            await self.publish_now()
            await asyncio.sleep(self._config.health_interval_s)

    def give_up_handler(
        self, then: Callable[[], None]
    ) -> Callable[[], Awaitable[None]]:
        """Build the camera's `on_give_up` handler: FAILURE, then `then`.

        Needed because the default handler tears down the process immediately with
        `os._exit` -- a polling task would never see `gave_up`. The write therefore runs
        with a time limit, and `then` is called in any case.
        """

        async def handler() -> None:
            try:
                await asyncio.wait_for(
                    write_device_health(self._nodes, DeviceHealth.FAILURE),
                    timeout=GIVE_UP_WRITE_TIMEOUT_S,
                )
                self._health = DeviceHealth.FAILURE
            except Exception:
                _log.exception("Letztes FAILURE konnte nicht geschrieben werden")
            if self._alarms is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        self._alarms.set(DeviceHealth.FAILURE, "aufgegeben"),
                        timeout=GIVE_UP_WRITE_TIMEOUT_S,
                    )
            then()

        return handler


__all__ = [
    "CameraHealthPublisher",
    "HealthAlarms",
    "device_health",
    "write_device_health",
]
