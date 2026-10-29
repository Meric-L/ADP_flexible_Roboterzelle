"""Auswertung einer Use-Case-Versuchsreihe: Bildmessung und Kennzahlen (ISO 9283 angelehnt).

Liest `versuch_NNN.png/.json` aus einem Reihenordner (`data/use_case/reihe_NNN`),
nimmt die live gespeicherte Messung aus der JSON (oder misst neu), schreibt
`auswertung.csv` und `auswertung.json` in den Reihenordner und druckt eine Kurzfassung.
Intrinsik: `pi.intrinsics` der Versuchs-JSON, sonst `--intrinsik <Kalibrierdatei>`.

    python tools/use_case_auswertung.py data/use_case/reihe_001
    python tools/use_case_auswertung.py data/use_case/reihe_001 --neu-messen --bilder
    # Aufkleber-Versatz: Wuerfel messen, von Hand um 180 Grad drehen, erneut messen
    python tools/use_case_auswertung.py data/use_case/reihe_001 --aufkleber-kalibrieren 1 2
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_TRIAL_PNG = re.compile(r"^versuch_(\d+)\.png$")
CSV_FIELDS = (
    "versuch", "ok", "grund", "dx_mm", "dy_mm", "dtheta_deg", "r_mm", "zone",
    "reprojection_rms_px", "blattmarker", "camera_height_mm", "camera_tilt_deg", "nadir_offset_mm",
)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json_atomic(path: Path, data: dict) -> None:
    """JSON ueber Temp-Datei und `os.replace` schreiben (nie halb beschrieben)."""
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _calibration(document: dict, fallback):
    """Kalibrierung aus der Versuchs-JSON (`pi.intrinsics`) oder der `--intrinsik`-Datei."""
    from use_case_eval.live import calibration_from_meta

    meta = (document.get("pi") or {}).get("intrinsics")
    if meta:
        try:
            return calibration_from_meta(meta)
        except (KeyError, ValueError, TypeError):
            pass
    return fallback


def _measure_image(png: Path, calibration, offset):
    """Misst ein PNG; `None` als Messung, wenn Bild oder Kalibrierung fehlen."""
    import cv2

    from use_case_eval.measure import TrialMeasurement, measure_trial

    if calibration is None:
        return TrialMeasurement(ok=False, grund="Keine Intrinsik (weder in der JSON noch --intrinsik)")
    image = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
    if image is None:
        return TrialMeasurement(ok=False, grund=f"Bild nicht lesbar: {png.name}")
    return measure_trial(image, calibration, offset)


def _resolve(reference: str, folder: Path) -> Path:
    """Bilddatei oder Versuchsnummer innerhalb des Reihenordners."""
    path = Path(reference)
    if path.suffix.lower() == ".png" and path.is_file():
        return path
    if reference.isdigit():
        candidate = folder / f"versuch_{int(reference):03d}.png"
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Weder Bilddatei noch Versuchsnummer in {folder}: {reference}")


def _fallback_calibration(path: Path | None):
    if path is None:
        return None
    from tagloc.calibration import load_calibration

    return load_calibration(path)


def calibrate_sticker(folder: Path, before: str, after: str, fallback) -> int:
    """Aufkleber-Versatz aus zwei Aufnahmen (vor/nach 180 Grad) bestimmen und speichern."""
    from use_case_eval.live import write_sticker_offset
    from use_case_eval.measure import sticker_offset_from_pair, summarize  # noqa: F401

    results = []
    for reference in (before, after):
        png = _resolve(reference, folder)
        document = _read_json(png.with_suffix(".json"))
        measurement = _measure_image(png, _calibration(document, fallback), (0.0, 0.0))
        if not measurement.ok:
            print(f"Messung von {png.name} fehlgeschlagen: {measurement.grund}", file=sys.stderr)
            return 1
        results.append(measurement)
    try:
        offset = sticker_offset_from_pair(*results)
    except ValueError as error:
        print(f"Aufkleber-Kalibrierung nicht moeglich: {error}", file=sys.stderr)
        return 1
    path = write_sticker_offset(folder.parent, offset)
    print(f"Aufkleber-Versatz (Markersystem): x = {offset[0]:+.3f} mm, y = {offset[1]:+.3f} mm")
    print(f"Geschrieben: {path}")
    print("Fuer bestehende Reihen: danach mit --neu-messen auswerten.")
    return 0


def evaluate(folder: Path, remeasure: bool, images: bool, fallback) -> int:
    from use_case_eval.live import load_sticker_offset
    from use_case_eval.measure import TrialMeasurement, annotate, summarize

    offset = load_sticker_offset(folder.parent)
    trials = sorted(
        (int(m.group(1)), entry)
        for entry in folder.iterdir()
        if (m := _TRIAL_PNG.match(entry.name))
    )
    if not trials:
        print(f"Keine versuch_NNN.png in {folder}", file=sys.stderr)
        return 1

    rows, measurements = [], []
    for number, png in trials:
        json_path = png.with_suffix(".json")
        document = _read_json(json_path)
        stored = document.get("messung")
        need_new = remeasure or images or not isinstance(stored, dict) or "ok" not in stored
        if not need_new:
            measurement = TrialMeasurement.from_dict(stored)
        else:
            measurement = _measure_image(png, _calibration(document, fallback), offset)
            if (remeasure or not isinstance(stored, dict)) and document:
                document["messung"] = measurement.to_dict()
                _write_json_atomic(json_path, document)
        if images:
            import cv2

            image = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
            if image is not None:
                cv2.imwrite(str(folder / f"versuch_{number:03d}_messung.png"), annotate(image, measurement))
        measurements.append(measurement)
        data = measurement.to_dict()
        data["versuch"] = number
        data["blattmarker"] = " ".join(str(i) for i in measurement.sheet_markers)
        rows.append(data)

    summary = summarize(measurements)
    with open(folder / "auswertung.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {k: ("" if row.get(k) is None else round(row[k], 4) if isinstance(row.get(k), float) else row.get(k))
                 for k in CSV_FIELDS}
            )
    document = {
        "reihe": folder.name,
        "aufkleber_versatz_mm": list(offset),
        "kennzahlen": summary,
        "versuche": [{k: v for k, v in row.items() if k != "blattmarker"} for row in rows],
    }
    _write_json_atomic(folder / "auswertung.json", document)

    print(f"{folder.name}: {summary['n']} von {summary['n_gesamt']} Versuchen gemessen")
    if summary["n"]:
        print(f"  dx = {summary['dx_mean_mm']:+.3f} +- {summary['dx_std_mm']:.3f} mm")
        print(f"  dy = {summary['dy_mean_mm']:+.3f} +- {summary['dy_std_mm']:.3f} mm")
        print(f"  dtheta = {summary['dtheta_mean_deg']:+.3f} +- {summary['dtheta_std_deg']:.3f} Grad")
        print(f"  AP = {summary['AP_mm']:.3f} mm   RP = {summary['RP_mm']:.3f} mm")
        print(f"  AP_theta = {summary['AP_theta_deg']:.3f} Grad   RP_theta = {summary['RP_theta_deg']:.3f} Grad")
        print("  Zonen: " + ", ".join(f"{k} {v}" for k, v in summary["zonen"].items()))
    for row in rows:
        if not row["ok"]:
            print(f"  Versuch {row['versuch']:03d}: {row['grund']}")
    print(f"Geschrieben: {folder / 'auswertung.csv'}, {folder / 'auswertung.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reihenordner", type=Path, help="Ordner einer Versuchsreihe, z. B. data/use_case/reihe_001")
    parser.add_argument("--neu-messen", action="store_true",
                        help="alle Bilder neu messen (mit aktuellem Aufkleber-Versatz) und die JSON aktualisieren")
    parser.add_argument("--bilder", action="store_true",
                        help="Kontrollbilder versuch_NNN_messung.png schreiben (misst dafuer neu)")
    parser.add_argument("--aufkleber-kalibrieren", nargs=2, metavar=("VOR", "NACH"),
                        help="Aufkleber-Versatz aus zwei Aufnahmen (Bilddatei oder Versuchsnummer) vor und "
                             "nach 180-Grad-Drehung des Wuerfels bestimmen und in aufkleber_versatz.json speichern")
    parser.add_argument("--intrinsik", type=Path, default=None,
                        help="Kalibrierdatei, falls die Versuchs-JSON keine Intrinsik enthaelt")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    folder = args.reihenordner.resolve()
    if not folder.is_dir():
        print(f"Kein Ordner: {folder}", file=sys.stderr)
        return 1
    fallback = _fallback_calibration(args.intrinsik)
    if args.aufkleber_kalibrieren:
        return calibrate_sticker(folder, *args.aufkleber_kalibrieren, fallback)
    return evaluate(folder, args.neu_messen, args.bilder, fallback)


if __name__ == "__main__":
    raise SystemExit(main())
