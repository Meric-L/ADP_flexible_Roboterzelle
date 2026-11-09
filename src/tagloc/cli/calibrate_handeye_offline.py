"""Rohbilder einer Hand-Auge-Fahrt wiederholt offline auswerten."""

import argparse
from pathlib import Path

from ..handeye_offline import run_experiments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="Sitzungsordner unter data/rohbilder/calibration")
    parser.add_argument("--pnp", choices=("iterative", "ippe"), default="iterative")
    parser.add_argument("--max-pnp-rms-px", type=float, default=1.5)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    report = run_experiments(args.session, pnp=args.pnp,
                             max_pnp_rms_px=args.max_pnp_rms_px, repeats=args.repeats)
    print(f"{len(report['runs'])} Experimente, {len(report['excluded'])} Ausschluesse")
    print(args.session / "results" / f"experiments_{args.pnp}.json")


if __name__ == "__main__":
    main()
