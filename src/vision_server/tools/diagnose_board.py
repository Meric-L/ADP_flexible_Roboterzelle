"""Diagnose: why calibration does not find the chessboard although it is sharp in the live stream.

Fetches an unmarked raw image from the running server. It briefly sets
`CameraStreamMode` to "off" so that no overlay is in the way, then restores the
previous mode. It tries several plausible cols/rows combinations against
`tagloc.boards.detect_board` -- the same function that `CaptureCalibrationSample`
and the stream overlay use. This follows the same procedure as on the hand Pi
(played through manually), here automated and run against the running server
instead of a single image.

It also saves the raw image locally. A wrong cols/rows value is not the only
possible cause. The image itself shows the exposure, the framing, and whether
the board is large and sharp enough in the frame.
"""

import argparse
import asyncio
import base64
import sys

import cv2
import numpy as np
from asyncua import Client, ua

from tagloc.boards import BoardSpec, detect_board

#: Plausible combinations as the first, obvious set: first the current board
#: (7x10 inner corners, 23 mm per square), then the previous one (7x9, 22 mm per square).
DEFAULT_COMBOS = [
    (7, 10), (10, 7), (7, 9), (9, 7), (6, 8), (8, 6), (6, 9), (9, 6),
    (7, 8), (8, 7), (8, 9), (9, 8),
]


def _parse_combo(value: str) -> tuple[int, int]:
    cols, _, rows = value.partition("x")
    return (int(cols), int(rows))


async def run(args: argparse.Namespace) -> int:
    async with Client(url=args.url) as client:
        own_idx = await client.get_namespace_index(args.namespace)
        vision = client.get_node(ua.NodeId(args.vision_system, own_idx))
        frame_node = await vision.get_child(f"{own_idx}:LatestCameraFrame")
        mode_node = await vision.get_child(f"{own_idx}:CameraStreamMode")

        previous_mode = await mode_node.read_value()
        await mode_node.write_value("off")
        try:
            await asyncio.sleep(args.settle_s)  # wait a few stream ticks
            encoded = await frame_node.read_value()
        finally:
            await mode_node.write_value(previous_mode)

    if not encoded:
        print("Kein Kamera-Frame verfuegbar -- laeuft der Livestream?", file=sys.stderr)
        return 1

    image = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        print("Frame liess sich nicht dekodieren.", file=sys.stderr)
        return 1

    cv2.imwrite(args.save_to, image)
    print(f"Rohbild gespeichert: {args.save_to} ({image.shape[1]}x{image.shape[0]})\n")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found_any = False
    print("cols x rows:")
    for cols, rows in args.combos:
        sample = detect_board(gray, BoardSpec(type="chessboard", cols=cols, rows=rows))
        print(f"  {cols}x{rows}: {'GEFUNDEN' if sample is not None else 'nein'}")
        found_any = found_any or sample is not None

    if not found_any:
        print(
            f"\nKeine der Kombinationen gefunden. {args.save_to} ansehen: "
            "Board ausreichend groß und scharf im Bild? Blendung/Schatten auf "
            "dem Papier? Falls das Bild gut aussieht, war die Annahme "
            "'gleiches Blatt wie am Hand-Pi' vermutlich falsch -- mit "
            "--combo COLSxROWS gezielt weitere Kombinationen probieren."
        )
    return 0 if found_any else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probiert Board-Geometrien gegen ein Live-Kamerabild durch"
    )
    parser.add_argument("--url", default="opc.tcp://127.0.0.1:4840/raspi/server/")
    parser.add_argument("--namespace", default="http://launch-rm.de/vision")
    parser.add_argument("--vision-system", default="VisionMachine")
    parser.add_argument("--save-to", default="board_diagnose.jpg")
    parser.add_argument("--settle-s", type=float, default=1.0)
    parser.add_argument(
        "--combo",
        dest="combos",
        action="append",
        type=_parse_combo,
        help="Zusaetzliche Kombination COLSxROWS (mehrfach angebbar); ohne "
        "Angabe wird DEFAULT_COMBOS probiert",
    )
    args = parser.parse_args(argv)
    if args.combos is None:
        args.combos = DEFAULT_COMBOS
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
