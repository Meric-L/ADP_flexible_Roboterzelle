"""Testclient: interaktive Kalibrierung ueber StartCalibration/FinishCalibration/
AbortCalibration ausloesen, Fortschritt live mitloggen.

Fuer Pis, an denen das Frontend die Kalibrierung noch nicht anbietet -- ruft
dieselben additiven Methoden wie ein spaeteres Frontend, siehe
doc/vision-server-interface.md Abschnitt 12. Laeuft, bis Strg+C gedrueckt
wird; das beendet die Session per `FinishCalibration` (Standard) oder mit
`--abort` ohne zu speichern.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys

from asyncua import Client, ua

_log = logging.getLogger("calibration-client")


async def run(args: argparse.Namespace) -> int:
    """Startet eine Session, loggt den Fortschritt, beendet sie bei Strg+C."""
    async with Client(url=args.url) as client:
        own_idx = await client.get_namespace_index(args.namespace)
        vision = client.get_node(ua.NodeId(args.vision_system, own_idx))
        start_node = await vision.get_child(f"{own_idx}:StartCalibration")
        finish_node = await vision.get_child(f"{own_idx}:FinishCalibration")
        abort_node = await vision.get_child(f"{own_idx}:AbortCalibration")
        progress_node = await vision.get_child(f"{own_idx}:CalibrationProgress")

        # Ein Ausgabewert: `call_method` liefert ihn direkt, keine Liste
        # (asyncua/common/methods.py, `call_method`).
        error = await vision.call_method(start_node)
        print(f"StartCalibration -> Error={error}")
        if error != 0:
            print(f"FEHLER: Start abgelehnt mit Error={error}", file=sys.stderr)
            return 1

        print("Session laeuft. Board vor die Kamera halten und langsam bewegen.")
        print("Strg+C zum Beenden (FinishCalibration; mit --abort ohne zu speichern).\n")

        # Strg+C waehrend `asyncio.sleep` wird von `asyncio.run()` VOR dieser
        # Coroutine abgefangen -- eine `except KeyboardInterrupt` hier drin
        # wird nie erreicht, der Prozess stirbt sofort ohne FinishCalibration.
        # Ein echter Signal-Handler setzt stattdessen nur ein Event, auf das
        # der Loop reagieren kann.
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, stop.set)
        except NotImplementedError:
            pass  # z. B. Windows -- Strg+C bricht dann wie zuvor hart ab

        while not stop.is_set():
            progress = json.loads(await progress_node.read_value())
            print(
                f"Aufnahmen {progress.get('samples', 0)}/{progress.get('minSamples', '?')}"
                f"   Abdeckung x {progress.get('coverageX', 0.0) * 100:.0f}%"
                f" y {progress.get('coverageY', 0.0) * 100:.0f}%"
            )
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=args.poll_interval)
        print()

        if args.abort:
            error = await vision.call_method(abort_node)
            print(f"AbortCalibration -> Error={error}")
            return 0 if error == 0 else 1

        summary, error = await vision.call_method(finish_node)
        print(f"FinishCalibration -> Error={error}")
        print(json.dumps(json.loads(summary), indent=2, ensure_ascii=False))
        return 0 if error == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """Liest die Kommandozeile und fuehrt die Kalibrier-Session aus."""
    parser = argparse.ArgumentParser(description="Interaktive Kalibrierung gegen den Vision-Server")
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4840/raspi/server/")
    parser.add_argument("--namespace", default="http://launch-rm.de/vision")
    parser.add_argument("--vision-system", default="VisionMachine")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument(
        "--abort", action="store_true", help="Session bei Strg+C ohne zu speichern beenden"
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
