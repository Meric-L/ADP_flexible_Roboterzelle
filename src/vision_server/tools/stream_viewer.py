"""Local live viewer for the camera stream, for a Pi with a monitor attached.

Shows `LatestCameraFrame` in an OpenCV window, by default in
`CameraStreamMode="calibration"`, as a positioning aid during a calibration session
(see `calibration_client.py`). The space bar triggers `CaptureCalibrationSample`;
captures happen only on key press. Needs a display (monitor or `ssh -X`).
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
    """Show the stream until the window is closed or 'q' is pressed."""
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
                    # The capture only stores the frame. Whether the board was
                    # actually visible is decided in `FinishCalibration` (Ctrl+C
                    # in `calibration_client.py`).
                    error = await vision.call_method(capture_node)
                    progress = json.loads(await progress_node.read_value())
                    if error == 0:
                        print(
                            f"Aufnahme uebernommen -- {progress.get('samples', 0)}/"
                            f"{progress.get('minSamples', '?')}"
                        )
                    else:
                        print(f"Aufnahme abgelehnt (Error={error}) -- laeuft eine Session?")
                elapsed = loop.time() - started
                await asyncio.sleep(max(0.0, interval - elapsed))
        except KeyboardInterrupt:
            pass
        finally:
            cv2.destroyAllWindows()
        return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and show the stream."""
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
