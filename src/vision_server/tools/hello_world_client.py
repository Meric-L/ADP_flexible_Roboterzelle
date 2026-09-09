"""Testclient: StartSingleJob aufrufen und das Ergebnis per Event abholen.

Zeigt den Handshake, den ein Backend implementieren muss: erst abonnieren, dann
die Methode rufen, dann auf das ResultReadyEvent warten. Es wird nicht gepollt.
"""

import argparse
import asyncio
import json
import logging
import sys

from asyncua import Client, ua

MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"
EVENT_TYPE_IDS = {
    1013: "JobStartedEvent",
    1018: "StateChangedEvent",
    1023: "ReadyEvent",
    1024: "ResultReadyEvent",
    1025: "AcquisitionDoneEvent",
}

_log = logging.getLogger("hello-world-client")


class EventCollector:
    """Sammelt eingehende Events in einer Queue."""

    def __init__(self, type_names: dict[ua.NodeId, str]) -> None:
        self._type_names = type_names
        self.queue: asyncio.Queue = asyncio.Queue()
        self.seen: list[str] = []

    def event_notification(self, event) -> None:
        """Callback der Subscription; laeuft im Event-Loop des Clients."""
        name = self._type_names.get(event.EventType, str(event.EventType))
        self.seen.append(name)
        self.queue.put_nowait((name, event))

    async def expect(self, name: str, timeout: float):
        """Wartet auf das naechste Event des angegebenen Typs."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"{name} nicht empfangen; gesehen: {self.seen}")
            got_name, event = await asyncio.wait_for(self.queue.get(), remaining)
            if got_name == name:
                return event


def _payload_of(event) -> dict:
    """Liest das JSON-Payload aus ResultContent[0]."""
    content = event.ResultContent
    if isinstance(content, ua.Variant):
        content = content.Value
    if not content:
        raise ValueError(
            "ResultContent ist leer — wurde subscribe_events ohne Event-Typ-Liste aufgerufen?"
        )
    return json.loads(content[0])


async def run(args: argparse.Namespace) -> int:
    """Fuehrt einen Einzeljob aus und gibt das Ergebnis-Payload aus."""
    async with Client(url=args.url) as client:
        mv_idx = await client.get_namespace_index(MACHINE_VISION_NAMESPACE_URI)
        own_idx = await client.get_namespace_index(args.namespace)
        vision = client.get_node(ua.NodeId(args.vision_system, own_idx))
        state_machine = await vision.get_child(f"{mv_idx}:VisionStateMachine")
        automatic = await state_machine.get_child(f"{mv_idx}:AutomaticModeStateMachine")
        start_node = await automatic.get_child(f"{mv_idx}:StartSingleJob")

        collector = EventCollector(
            {ua.NodeId(ident, mv_idx): name for ident, name in EVENT_TYPE_IDS.items()}
        )
        subscription = await client.create_subscription(100, collector)
        handle = await subscription.subscribe_events(
            vision, [client.get_node(ua.NodeId(ident, mv_idx)) for ident in EVENT_TYPE_IDS]
        )
        try:
            job_id, error = await automatic.call_method(
                start_node,
                ua.Variant(args.meas_id, ua.VariantType.String),
                ua.Variant("", ua.VariantType.String),
                ua.Variant(args.recipe_id, ua.VariantType.String),
                ua.Variant("", ua.VariantType.String),
                ua.Variant(list(args.parameter), ua.VariantType.String),
            )
            print(f"StartSingleJob -> JobId={job_id!r} Error={error}")
            if error != 0:
                print(f"FEHLER: Aufruf abgelehnt mit Error={error}", file=sys.stderr)
                return 1

            event = await collector.expect("ResultReadyEvent", args.timeout)
            payload = _payload_of(event)
            print(f"ResultState={event.ResultState} Message={event.Message.Text}")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            if payload["jobId"] != job_id:
                print(
                    f"FEHLER: jobId {payload['jobId']!r} passt nicht zu {job_id!r}", file=sys.stderr
                )
                return 1
            await collector.expect("ReadyEvent", args.timeout)
            print(f"Events: {collector.seen}")
            return 0
        except TimeoutError as timeout_error:
            print(f"FEHLER: {timeout_error}", file=sys.stderr)
            return 1
        finally:
            await subscription.unsubscribe(handle)
            await subscription.delete()


def main(argv: list[str] | None = None) -> int:
    """Liest die Kommandozeile und fuehrt den Test aus."""
    parser = argparse.ArgumentParser(description="Hello-World-Test gegen den Vision-Server")
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4841/vision/machine/")
    parser.add_argument("--namespace", default="http://launch-rm.de/vision")
    parser.add_argument("--vision-system", default="VisionMachine")
    parser.add_argument("--recipe-id", default="hello-world")
    parser.add_argument("--meas-id", default="")
    parser.add_argument("--parameter", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
