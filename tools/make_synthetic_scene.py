"""Render synthetic scenes: images with **known** ground truth, no camera.

Nur die Kommandozeile -- das Rendern selbst liegt in `tagloc.synthetic`
(siehe dort) und wird hier fuer bestehende Aufrufer weiter exportiert.

    # image series with tags plus matching calibration
    python tools/make_synthetic_scene.py --out /tmp/szene --count 20 \
        --calibration /tmp/szene/kalibrierung.json

    # chessboard views for the calibration path
    python tools/make_synthetic_scene.py --out /tmp/board --count 20 --mode chessboard

The tools then run without hardware:

    PYTHONPATH=src python3 -m tagloc.cli.calibrate --source /tmp/board \
        --out /tmp/kalib.json
    PYTHONPATH=src python3 -m tagloc.cli.build_tagmap --source /tmp/szene \
        --calibration /tmp/szene/kalibrierung.json --anchor 0 --out /tmp/tagmap.json

`cv2` is imported only inside functions -- `--help` and importing this
module work without OpenCV.
"""

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tagloc.boards import BoardSpec  # noqa: E402
from tagloc.calibration import save_calibration  # noqa: E402
from tagloc.modes import DEFAULT_TAG_FAMILY  # noqa: E402
from tagloc.synthetic import (  # noqa: E402,F401 - Weiterexport fuer bestehende Aufrufer
    IMAGE_PATTERN,
    SCHEMA,
    TRUTH_FILE,
    TagPlacement,
    chessboard_object_points,
    chessboard_views,
    default_tag_layout,
    look_at,
    marker_patch,
    orbit_views,
    render_chessboard,
    render_tags,
    synthetic_calibration,
    synthetic_from,
    tag_pose,
    write_chessboard_scene,
    write_tag_scene,
)


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synthetische Bilder mit bekannter Wahrheit rendern (ohne Kamera)"
    )
    parser.add_argument("--out", type=Path, required=True, help="Zielordner der Bilder")
    parser.add_argument("--count", type=int, default=20, help="Anzahl der Ansichten")
    parser.add_argument(
        "--mode", choices=("tags", "chessboard"), default="tags", help="Was gerendert wird"
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        default=None,
        help="Die passende synthetische Kalibrierung hierhin schreiben",
    )
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument(
        "--focal-px", type=float, default=None, help="Brennweite in Pixeln (Standard: Bildbreite)"
    )
    parser.add_argument("--frame-id", default="cam_synth", help="Bezugsrahmen der Kalibrierung")
    parser.add_argument("--family", default=DEFAULT_TAG_FAMILY, help="Tag-Familie")
    parser.add_argument(
        "--supersample", type=int, default=2, help="Ueberabtastung beim Rendern (1 = aus)"
    )
    parser.add_argument("--cols", type=int, default=9, help="Schachbrett: innere Ecken je Zeile")
    parser.add_argument("--rows", type=int, default=6, help="Schachbrett: innere Ecken je Spalte")
    parser.add_argument("--square-size-m", type=float, default=0.030)
    parser.add_argument(
        "--distance-m", type=float, default=0.55, help="Schachbrett: mittlerer Abstand"
    )
    parser.add_argument("--seed", type=int, default=20260917, help="Zufallssaat der Board-Posen")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.count < 1:
        raise SystemExit("--count muss mindestens 1 sein")

    calibration = synthetic_calibration(
        (args.width, args.height), focal_px=args.focal_px, frame_id=args.frame_id
    )
    if args.calibration is not None:
        save_calibration(args.calibration, calibration)
        print(f"Kalibrierung geschrieben: {args.calibration}")

    if args.mode == "tags":
        document = write_tag_scene(
            args.out, args.count, calibration, family=args.family, supersample=args.supersample
        )
        print(f"{len(document['views'])} Ansichten mit {len(document['tags'])} Tags")
        print("Anker-Tag:", document["anchorTagId"])
    else:
        spec = BoardSpec(
            type="chessboard",
            cols=args.cols,
            rows=args.rows,
            square_size_m=args.square_size_m,
        )
        document = write_chessboard_scene(
            args.out,
            args.count,
            calibration,
            spec,
            distance_m=args.distance_m,
            supersample=args.supersample,
            seed=args.seed,
        )
        print(f"{len(document['views'])} Schachbrettansichten, Board {spec.cols}x{spec.rows}")

    print(f"Bilder und {TRUTH_FILE} in: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
