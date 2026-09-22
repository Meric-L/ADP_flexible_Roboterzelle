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
import base64
import json
import sys

import cv2
import numpy as np
from asyncua import Client, ua

SPACE = 32


async def run(args: argparse.Namespace) -> int:
    """Zeigt den Stream an, bis das Fenster geschlossen oder 'q' gedrueckt wird."""
    async with Client(url=args.url) as client:
        own_idx = await client.get_namespace_index(args.namespace)
        vision = client.get_node(ua.NodeId(args.vision_system, own_idx))
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
                    jpeg = base64.b64decode(encoded)
                    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if image is not None:
                        cv2.imshow("Vision-Stream", image)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == SPACE:
                    error = await vision.call_method(capture_node)
                    progress = json.loads(await progress_node.read_value())
                    if error == 0:
                        print(
                            f"Aufnahme uebernommen -- {progress.get('samples', 0)}/"
                            f"{progress.get('minSamples', '?')}, Abdeckung x "
                            f"{progress.get('coverageX', 0.0) * 100:.0f}% y "
                            f"{progress.get('coverageY', 0.0) * 100:.0f}%"
                        )
                    else:
                        print(f"Kein Board gefunden (Error={error}) -- nochmal versuchen.")
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
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4840/raspi/server/")
    parser.add_argument("--namespace", default="http://launch-rm.de/vision")
    parser.add_argument("--vision-system", default="VisionMachine")
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
