"""Kamerakalibrierung -- interaktiv an der Kamera oder headless aus Bildern.

    # interactive: SPACE = capture, C = compute, S = save, Q = quit
    python -m tagloc.cli.calibrate --source camera:0 --board charuco \
        --out data/calibration/cam_ceiling.json --frame-id cam_ceiling

    # headless, same computation core
    python -m tagloc.cli.calibrate --source ./aufnahmen/decke/ --board charuco \
        --out data/calibration/cam_ceiling.json --frame-id cam_ceiling

`--capture-to FOLDER` additionally saves camera images without computing,
producing the image set a calibration can later be reproduced from on a PC.
"""

import argparse
from pathlib import Path

from .. import frames as frame_sources
from ..boards import (
    CHARUCO,
    CHESSBOARD,
    BoardSpec,
    build_board,
    calibrate_from_samples,
    compute_coverage,
    detect_board,
)
from ..calibration import save_calibration
from ..overlay import draw_board_overlay, draw_status_bar
from ._common import add_source_arguments, is_camera_source, open_source, setup_logging

MIN_SAMPLES = 15


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_source_arguments(parser)
    parser.add_argument("--board", choices=(CHESSBOARD, CHARUCO), default=CHESSBOARD)
    parser.add_argument("--cols", type=int, default=9, help="Schachbrett: innere Ecken")
    parser.add_argument("--rows", type=int, default=6)
    parser.add_argument("--square-size-m", type=float, default=0.030)
    parser.add_argument("--marker-size-m", type=float, default=0.022)
    parser.add_argument("--dictionary", default="DICT_4X4_50")
    parser.add_argument("--out", type=Path, required=True, help="Zieldatei (JSON)")
    parser.add_argument("--frame-id", default="", help="Bezugsrahmen dieser Kamera")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    parser.add_argument(
        "--capture-to", type=Path, default=None, help="Aufnahmen als Bilder ablegen"
    )
    parser.add_argument("--no-gui", action="store_true", help="Kein Fenster, alles automatisch")
    return parser


def _spec(args) -> BoardSpec:
    return BoardSpec(
        type=args.board,
        cols=args.cols,
        rows=args.rows,
        square_size_m=args.square_size_m,
        marker_size_m=args.marker_size_m,
        dictionary=args.dictionary,
    )


def _finish(samples, size, spec, board, args) -> int:
    if len(samples) < args.min_samples:
        print(
            f"Nur {len(samples)} von {args.min_samples} Aufnahmen -- "
            "das reicht fuer eine belastbare Kalibrierung nicht."
        )
        if len(samples) < 3:
            return 1
    calibration = calibrate_from_samples(samples, size, spec, board, frame_id=args.frame_id)
    coverage = compute_coverage(samples, size)
    save_calibration(args.out, calibration)
    print(f"\nRMS-Reprojektionsfehler : {calibration.rms_reprojection_error:.4f} px")
    print(f"Aufnahmen               : {calibration.sample_count}")
    print(f"Abdeckung               : x {coverage[0] * 100:.0f} %  y {coverage[1] * 100:.0f} %")
    if min(coverage) < 0.6:
        print(
            "  Achtung: geringe Abdeckung. RMS kann trotzdem gut aussehen, die "
            "Verzeichnung am Bildrand ist dann aber unbrauchbar."
        )
    print(f"Geschrieben             : {args.out}")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    spec = _spec(args)
    board = build_board(spec)
    source = open_source(args)
    headless = args.no_gui or not is_camera_source(args.source)

    samples: list = []
    size: tuple[int, int] = (0, 0)
    saved = 0
    if args.capture_to is not None:
        Path(args.capture_to).mkdir(parents=True, exist_ok=True)

    try:
        while True:
            image = source.read()
            if image is None:
                break
            size = frame_sources.image_size(image)
            sample = detect_board(frame_sources.to_gray(image), spec, board)

            if headless:
                if sample is not None:
                    samples.append(sample)
                    print(f"Aufnahme {len(samples):>3}: {sample.count()} Ecken")
                else:
                    print("Board nicht erkannt, Bild uebersprungen")
                continue

            import cv2

            preview = image.copy()
            draw_board_overlay(preview, sample, coverage=compute_coverage(samples, size))
            draw_status_bar(
                preview,
                [
                    f"Aufnahmen {len(samples)}/{args.min_samples}",
                    "SPACE = aufnehmen   C = rechnen+speichern   Q = Ende",
                ],
            )
            cv2.imshow("tagloc calibrate", preview)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord(" ") and sample is not None:
                samples.append(sample)
                print(f"Aufnahme {len(samples):>3}: {sample.count()} Ecken")
                if args.capture_to is not None:
                    saved += 1
                    cv2.imwrite(str(Path(args.capture_to) / f"kalib_{saved:03d}.png"), image)
            elif key == ord(" "):
                print("Board nicht erkannt -- nicht aufgenommen")
            if key == ord("c") and len(samples) >= 3:
                break
    finally:
        source.close()
        if not headless:
            import cv2

            cv2.destroyAllWindows()

    if not samples:
        print("Keine verwertbare Aufnahme -- nichts zu rechnen.")
        return 1
    return _finish(samples, size, spec, board, args)


if __name__ == "__main__":
    raise SystemExit(main())
