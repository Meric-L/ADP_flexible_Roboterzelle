"""Diagnose: warum findet die Kalibrierung das Schachbrett nicht, obwohl es
im Livestream scharf zu sehen ist?

Holt ein unmarkiertes Rohbild vom laufenden Server (schaltet
`CameraStreamMode` kurz auf "off", damit kein Overlay im Weg ist, und stellt
den vorherigen Modus danach wieder her) und probiert mehrere plausible
cols/rows-Kombinationen gegen `tagloc.boards.detect_board` -- dieselbe
Funktion, die auch `CaptureCalibrationSample` und das Stream-Overlay nutzen.
Gleiches Vorgehen wie beim Hand-Pi (manuell durchgespielt), hier
automatisiert und gegen den laufenden Server statt gegen ein Einzelbild.

Speichert das Rohbild zusaetzlich lokal -- eine falsche cols/rows-Angabe ist
nicht die einzig moegliche Ursache; das Bild selbst zeigt Belichtung,
Bildausschnitt und ob das Board wirklich groß/scharf genug im Frame liegt.
"""

import argparse
import asyncio
import sys

import cv2
from asyncua import Client

from tagloc.boards import BoardSpec, detect_board
from vision_server.tools._client import add_connection_arguments, decode_frame, vision_node

#: Dieselben Kombinationen, mit denen sich am Hand-Pi das reale Board
#: (7x9, 22mm/Feld) gefunden hat -- als erster, naheliegender Satz.
DEFAULT_COMBOS = [
    (7, 9), (9, 7), (6, 8), (8, 6), (6, 9), (9, 6),
    (7, 8), (8, 7), (8, 9), (9, 8),
]


def _parse_combo(value: str) -> tuple[int, int]:
    cols, _, rows = value.partition("x")
    return (int(cols), int(rows))


async def run(args: argparse.Namespace) -> int:
    async with Client(url=args.url) as client:
        vision, own_idx = await vision_node(client, args.namespace, args.vision_system)
        frame_node = await vision.get_child(f"{own_idx}:LatestCameraFrame")
        mode_node = await vision.get_child(f"{own_idx}:CameraStreamMode")

        previous_mode = await mode_node.read_value()
        await mode_node.write_value("off")
        try:
            await asyncio.sleep(args.settle_s)  # ein paar Stream-Ticks abwarten
            encoded = await frame_node.read_value()
        finally:
            await mode_node.write_value(previous_mode)

    if not encoded:
        print("Kein Kamera-Frame verfuegbar -- laeuft der Livestream?", file=sys.stderr)
        return 1

    image = decode_frame(encoded)
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
    add_connection_arguments(parser)
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
