"""Test client: run an interactive calibration via StartCalibration/FinishCalibration/
AbortCalibration and log the progress live.

For Pis where the frontend does not offer the calibration yet. It calls the same
additive methods that a later frontend will use, see
doc/projektdoku/vision-server-interface.md section 12. It runs until Ctrl+C is
pressed; this ends the session via `FinishCalibration` (default), or with
`--abort` without saving.
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

#: Error codes from doc/projektdoku/vision-server-interface.md ("Fehlercodes (Error)") --
#: not imported from `vision_server`; this script stays a pure OPC UA client,
#: independent of the server code.
BUSY = 3


async def _start_session(vision, start_node, abort_node) -> int:
    """Call `StartCalibration`; on `BUSY`, call `AbortCalibration` once and retry.

    This handles a session left hanging by an earlier client that crashed. The
    session lives in the server process, not in the client, so a hard-killed
    client does not clean it up.
    """
    error = await vision.call_method(start_node)
    if error == BUSY:
        print("StartCalibration -> BUSY: vermutlich eine haengengebliebene")
        print("Session eines frueheren Laufs -- breche sie ab und starte neu.")
        await vision.call_method(abort_node)
        error = await vision.call_method(start_node)
    return error


async def run(args: argparse.Namespace) -> int:
    """Start a session, log its progress, and end it on Ctrl+C."""
    async with Client(url=args.url) as client:
        own_idx = await client.get_namespace_index(args.namespace)
        vision = client.get_node(ua.NodeId(args.vision_system, own_idx))
        start_node = await vision.get_child(f"{own_idx}:StartCalibration")
        finish_node = await vision.get_child(f"{own_idx}:FinishCalibration")
        abort_node = await vision.get_child(f"{own_idx}:AbortCalibration")
        progress_node = await vision.get_child(f"{own_idx}:CalibrationProgress")

        error = await _start_session(vision, start_node, abort_node)
        print(f"StartCalibration -> Error={error}")
        if error != 0:
            print(f"FEHLER: Start abgelehnt mit Error={error}", file=sys.stderr)
            return 1

        print("Session laeuft. Board vor die Kamera halten und langsam bewegen.")
        print(
            "Aufnahmen kommen von woanders (z. B. stream_viewer.py per Leertaste). "
            "Strg+C beendet die Aufnahme-Phase und stoesst die Auswertung an "
            "(FinishCalibration; mit --abort ohne zu speichern/auszuwerten).\n"
        )

        # Ctrl+C during `asyncio.sleep` is caught by `asyncio.run()` BEFORE this
        # coroutine sees it -- an `except KeyboardInterrupt` here would never be
        # reached, and the process would die at once without FinishCalibration.
        # A real signal handler instead only sets an event, which the loop can
        # react to.
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, stop.set)
        except NotImplementedError:
            pass  # e.g. Windows -- Ctrl+C then aborts hard, as before

        while not stop.is_set():
            progress = json.loads(await progress_node.read_value())
            print(f"Aufnahmen {progress.get('samples', 0)}/{progress.get('minSamples', '?')}")
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
        if error != 0:
            return 1

        # `FinishCalibration` only starts the evaluation and returns at once (see
        # calibration_session.py) -- the actual result arrives via
        # `CalibrationProgress` once `processing` is `False` again.
        print("Auswertung laeuft im Hintergrund ...")
        result = None
        while result is None:
            await asyncio.sleep(args.poll_interval)
            progress = json.loads(await progress_node.read_value())
            result = progress.get("result")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if "warning" in result:
            print(f"ACHTUNG: {result['warning']}")
        return 0 if result.get("error", 0) == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the calibration session."""
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
