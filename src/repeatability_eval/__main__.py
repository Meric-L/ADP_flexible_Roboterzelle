"""CLI: `python -m repeatability_eval <session_ordner> [--exclude-ids 3,17] [--no-write]`."""

import argparse
import sys
from pathlib import Path

from .session import evaluate_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repeatability_eval", description=__doc__)
    parser.add_argument("session_ordner", type=Path)
    parser.add_argument("--exclude-ids", default="", help="Komma-getrennte Ecken-IDs, z. B. 3,17")
    parser.add_argument("--no-write", action="store_true", help="keine Dateien schreiben")
    args = parser.parse_args(argv)
    try:
        exclude = {int(t) for t in args.exclude_ids.split(",") if t.strip()}
    except ValueError:
        parser.error("--exclude-ids erwartet ganze Zahlen")
    try:
        res = evaluate_session(args.session_ordner, exclude_ids=exclude, write=not args.no_write)
    except ValueError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    print(f"Session {args.session_ordner}: {res['nVerwendet']} von {res['nBilder']} Bildern verwendet")
    print(f"Basis-Ecken: {len(res['basisIds'])} ({res['basisRegel']})")
    for phase, s in res["serien"].items():
        if s.get("rpUm") is None:
            print(f"  {phase}: {s.get('grund', 'keine Auswertung')}")
            continue
        line = f"  {phase}: RP = {s['rpUm']:.1f} um (n={s['nAufnahmen']})"
        if "noiseRatio" in s:
            ratio = s["noiseRatio"]
            line += (
                f", Rauschverhaeltnis {ratio:.3f}, gueltig: {'ja' if s['valid'] else 'nein'}"
                if ratio is not None else ", Rauschverhaeltnis unbekannt, gueltig: nein"
            )
        print(line)
    print(f"Ausgeschlossene IDs: {res['ausgeschlosseneIds'] or 'keine'}; streuende IDs: {res['streuendeIds'] or 'keine'}")
    if res["ausgeschlosseneBilder"]:
        print(f"Ausgeschlossene Bilder: {len(res['ausgeschlosseneBilder'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
