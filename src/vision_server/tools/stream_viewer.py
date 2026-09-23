"""Lokaler Live-Viewer fuer den Kamera-Stream -- fuer Pis mit angeschlossenem
Monitor, ohne dass dafuer ein Frontend laufen muss.

Liest `LatestCameraFrame` (Base64-JPEG) und zeigt es in einem OpenCV-Fenster
an. Setzt standardmaessig `CameraStreamMode="calibration"`, damit das Bild
dieselbe Board-Erkennung und Abdeckungs-Anzeige traegt wie im Frontend
(`stream_overlay.py`) -- praktisch, um waehrend einer laufenden
`CalibrationSession` (gestartet z. B. per `calibration_client.py`) direkt zu
sehen, ob das Board erkannt wird oder ob z. B. das Bild unscharf ist. Beide
laufen parallel, weil beide nur aus der bereits offenen `SharedCamera`
mitlesen -- kein exklusiver Kamerazugriff wie bei `tagloc.cli.calibrate`.

Leertaste im Fenster loest `CaptureCalibrationSample` aus -- Aufnahmen
passieren nur auf Knopfdruck, kein automatisches Zeitintervall: der Operator
sieht das Bild und entscheidet selbst, wann die Pose gut ist.

Braucht ein Display (angeschlossener Monitor + Desktopumgebung, oder `ssh -X`).
"""

import argparse
import asyncio
import json
import sys

import cv2
from asyncua import Client

from vision_server.tools._client import (
    add_connection_arguments,
    decode_frame,
    format_progress,
    vision_node,
)

SPACE = 32


async def run(args: argparse.Namespace) -> int:
    """Zeigt den Stream an, bis das Fenster geschlossen oder 'q' gedrueckt wird."""
    async with Client(url=args.url) as client:
        vision, own_idx = await vision_node(client, args.namespace, args.vision_system)
        frame_node = await vision.get_child(f"{own_idx}:LatestCameraFrame")
        capture_node = await vision.get_child(f"{own_idx}:CaptureCalibrationSample")
        progress_node = await vision.get_child(f"{own_idx}:CalibrationProgress")

        if args.mode:
            mode_node = await vision.get_child(f"{own_idx}:CameraStreamMode")
            await mode_node.write_value(args.mode)
            print(f"CameraStreamMode -> {args.mode}")

        print("Leertaste = Aufnahme, 'q' oder Strg+C = beenden.")
        loop = asyncio.get_running_loop()
        interval = 1.0 / args.fps
        try:
            while True:
                started = loop.time()
                encoded = await frame_node.read_value()
                if encoded:
                    image = decode_frame(encoded)
                    if image is not None:
                        cv2.imshow("Vision-Stream", image)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == SPACE:
                    error = await vision.call_method(capture_node)
                    progress = json.loads(await progress_node.read_value())
                    if error == 0:
                        print(f"Aufnahme uebernommen -- {format_progress(progress)}")
                    else:
                        print(f"Kein Board gefunden (Error={error}) -- nochmal versuchen.")
                    result = progress.get("result")
                    if result is not None:
                        # Abdeckungs-Schwelle erreicht -- Session hat sich
                        # selbst beendet (siehe CalibrationSession.capture()).
                        print(f"\nAbdeckung erreicht, automatisch abgeschlossen: {result}")
                        if "warning" in result:
                            print(f"ACHTUNG: {result['warning']}")
                        break
                elapsed = loop.time() - started
                await asyncio.sleep(max(0.0, interval - elapsed))
        except KeyboardInterrupt:
            pass
        finally:
            cv2.destroyAllWindows()
        return 0


def main(argv: list[str] | None = None) -> int:
    """Liest die Kommandozeile und zeigt den Stream an."""
    parser = argparse.ArgumentParser(description="Lokaler Live-Viewer des Kamera-Streams")
    add_connection_arguments(parser)
    parser.add_argument(
        "--mode",
        default="calibration",
        help="CameraStreamMode vor der Anzeige setzen ('off'/'apriltag'/'calibration'); "
        "leerer String laesst den aktuellen Modus unangetastet",
    )
    parser.add_argument("--fps", type=float, default=10.0)
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
