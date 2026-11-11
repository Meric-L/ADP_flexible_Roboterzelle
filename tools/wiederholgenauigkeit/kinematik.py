"""Kinematik-Test auswerten: haengt die Tag-Lage in Roboterbasis von der Gelenkstellung ab?

Aufruf (aus dem Repo-Wurzelverzeichnis):
    PYTHONPATH=src .venv/bin/python tools/wiederholgenauigkeit/kinematik.py <session_ordner> \
        [--tag-id N] [--tag-size-mm 80] [--out bericht.md] [--csv werte.csv] \
        [--csv-posen posen.csv] [--dezimalpunkt] [--plot kinematik_posen.png] \
        [--calibration cam.json] [--handeye handeye.json]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from repeatability_eval.kinematics import (
    csv_rows, measure_session, plot_deltas, pose_table, render_report, summarize_poses,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--tag-id", type=int, default=None)
    ap.add_argument("--tag-size-mm", type=float, default=80.0)
    ap.add_argument("--out", type=Path, default=None,
                    help="Markdown-Bericht; Standard <session>/kinematik_bericht.md")
    ap.add_argument("--stdout", action="store_true", help="Bericht auf stdout statt Datei (schreibt dann nur mit --csv-posen/--plot)")
    ap.add_argument("--csv", type=Path, default=None, help="optionale CSV je Bild")
    ap.add_argument("--csv-posen", type=Path, default=None, help="CSV je Pose; Standard <session>/kinematik_posen.csv")
    ap.add_argument("--dezimalpunkt", action="store_true", help="CSV je Pose mit Punkt und Komma als Trenner")
    ap.add_argument("--plot", type=Path, default=None, help="PNG; Standard <session>/kinematik_posen.png")
    ap.add_argument("--calibration", type=Path, default=None, help="Intrinsik-JSON statt der in den Records")
    ap.add_argument("--handeye", type=Path, default=None, help="Hand-Auge-JSON statt der in den Records")
    args = ap.parse_args(argv)

    calibration = hand_eye = None
    try:
        if args.calibration:
            from tagloc.calibration import load_calibration
            calibration = load_calibration(args.calibration)
        if args.handeye:
            from tagloc.handeye import load_hand_eye
            hand_eye = load_hand_eye(args.handeye).pose_flange_cam
        res = measure_session(args.session_dir, args.tag_id, args.tag_size_mm / 1000.0, calibration, hand_eye)
    except (ValueError, OSError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    summary = summarize_poses(res.images)
    info = [f"Session: `{args.session_dir.name}`",
            f"Tag: ID {res.tag_id if res.tag_id is not None else '-'}, {args.tag_size_mm:g} mm".replace(".", ","),
            f"Datum: {res.date or '-'}",
            "Kalibrierung: " + (f"`{args.calibration.name}`" if args.calibration else "aus den Records")
            + f" (ID `{res.calibration_id or '-'}`)",
            "Hand-Auge: " + (f"`{args.handeye.name}`" if args.handeye else "aus den Records")
            + (f" (ID `{res.hand_eye_id}`)" if res.hand_eye_id and not args.handeye else "")]
    if res.labels:
        info.append("Posen: " + ", ".join(f"{k} = {v}" for k, v in sorted(res.labels.items())))
    bad = [im for im in res.images if not im.usable]
    warnings = list(res.warnings)
    if bad:
        warnings.append(f"{len(bad)} von {len(res.images)} Bildern nicht genutzt: "
                        + "; ".join(f"{im.name} ({im.reason or 'unbekannt'})" for im in bad[:10])
                        + (" ..." if len(bad) > 10 else ""))
    text = render_report(summary, info=info, warnings=warnings, labels=res.labels)
    sd = args.session_dir
    if args.stdout:
        sys.stdout.write(text)
    else:
        out = args.out or sd / "kinematik_bericht.md"
        out.write_text(text, encoding="utf-8")
        print(f"Bericht: {out}", file=sys.stderr)
    posen = args.csv_posen or sd / "kinematik_posen.csv"
    header, rows = pose_table(summary, res.labels, decimal_comma=not args.dezimalpunkt)
    with posen.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter="," if args.dezimalpunkt else ";")
        w.writerow(header)
        w.writerows(rows)
    print(f"Posen-CSV: {posen}", file=sys.stderr)
    png = args.plot or sd / "kinematik_posen.png"
    if plot_deltas(summary, png):
        print(f"Plot: {png}", file=sys.stderr)
    else:
        print("Plot uebersprungen (matplotlib fehlt oder keine Abweichungen, z. B. nur Referenz)", file=sys.stderr)
    if args.csv:
        header, rows = csv_rows(res.images)
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
