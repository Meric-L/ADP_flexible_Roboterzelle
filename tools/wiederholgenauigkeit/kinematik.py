"""Kinematik-Test auswerten: haengt die Tag-Lage in Roboterbasis von der Gelenkstellung ab?

Standard: nutzt die Pi-Messung aus den JSONs neben den Bildern. `--neu-erkennen` erkennt den
Tag offline neu aus den PNGs (dann gelten --tag-id/--tag-size-mm/--calibration/--handeye).
Der Pi haelt diese Auswertung waehrend einer Messung selbst aktuell (Session `*_kinematik`).

Aufruf (aus dem Repo-Wurzelverzeichnis):
    PYTHONPATH=src .venv/bin/python tools/wiederholgenauigkeit/kinematik.py <session_ordner> \
        [--neu-erkennen] [--tag-id N] [--tag-size-mm 80] [--out bericht.md] [--csv werte.csv] \
        [--csv-posen posen.csv] [--dezimalpunkt] [--plot kinematik_posen.png] \
        [--calibration cam.json] [--handeye handeye.json]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repeatability_eval.kinematics import write_session_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--neu-erkennen", action="store_true", help="Tag offline aus den PNGs neu erkennen statt JSON-Messung")
    ap.add_argument("--tag-id", type=int, default=None)
    ap.add_argument("--tag-size-mm", type=float, default=80.0)
    ap.add_argument("--out", type=Path, default=None,
                    help="Markdown-Bericht; Standard <session>/kinematik_bericht.md")
    ap.add_argument("--csv", type=Path, default=None, help="optionale CSV je Bild")
    ap.add_argument("--csv-posen", type=Path, default=None, help="CSV je Pose; Standard <session>/kinematik_posen.csv")
    ap.add_argument("--dezimalpunkt", action="store_true", help="CSV je Pose mit Punkt und Komma als Trenner")
    ap.add_argument("--plot", type=Path, default=None, help="PNG; Standard <session>/kinematik_posen.png")
    ap.add_argument("--calibration", type=Path, default=None, help="Intrinsik-JSON statt der in den Records")
    ap.add_argument("--handeye", type=Path, default=None, help="Hand-Auge-JSON statt der in den Records")
    ap.add_argument("--stdout", action="store_true", help="Bericht zusaetzlich auf stdout")
    args = ap.parse_args(argv)

    calibration = hand_eye = None
    try:
        if args.calibration:
            from tagloc.calibration import load_calibration
            calibration = load_calibration(args.calibration)
        if args.handeye:
            from tagloc.handeye import load_hand_eye
            hand_eye = load_hand_eye(args.handeye).pose_flange_cam
        res = write_session_report(
            args.session_dir, source="bilder" if args.neu_erkennen else "json",
            tag_id=args.tag_id, tag_size_m=args.tag_size_mm / 1000.0, calibration=calibration,
            hand_eye=hand_eye, out=args.out, csv_posen=args.csv_posen, plot=args.plot,
            csv_bilder=args.csv, decimal_comma=not args.dezimalpunkt)
    except (ValueError, OSError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    print(f"Bericht: {res['report']}", file=sys.stderr)
    print(f"Posen-CSV: {res['csvPosen']}", file=sys.stderr)
    if res["plot"]:
        print(f"Plot: {res['plot']}", file=sys.stderr)
    else:
        print("Plot uebersprungen (matplotlib fehlt oder keine Abweichungen, z. B. nur Referenz)", file=sys.stderr)
    if args.stdout:
        sys.stdout.write(res["report"].read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
