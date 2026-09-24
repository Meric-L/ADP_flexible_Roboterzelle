"""Verschiedene AprilTags in ein gemeinsames Koordinatensystem platzieren.

    python -m tagloc.cli.build_tagmap --source ./aufnahmen/zelle/ \
        --calibration data/calibration/cam_ceiling.json \
        --anchor 0 --out config/tagmap.json

Capture note: photographing every tag once isn't enough. Every image must
show at least **two** tags, and the pairs must form a connected chain to
the anchor tag -- a relation only exists between co-seen tags.
"""

import argparse
from pathlib import Path

from .. import frames as frame_sources
from ..detector import build_detector
from ..pose import estimate_tag_poses, poses_by_tag
from ..tagmap import (
    empty_tag_map,
    format_residual_report,
    load_tag_map,
    missing_tags,
    place_tags,
    residuals,
    save_tag_map,
    with_world_poses,
)
from ._common import (
    add_detection_arguments,
    add_source_arguments,
    describe_pose,
    fit_calibration,
    setup_logging,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_source_arguments(parser)
    add_detection_arguments(parser)
    parser.add_argument("--anchor", type=int, default=0, help="Tag-ID des Ursprungs")
    parser.add_argument("--out", type=Path, required=True, help="Zieldatei der Tag-Map")
    parser.add_argument(
        "--frame-id", default="world", help="Name des entstehenden Koordinatensystems"
    )
    parser.add_argument(
        "--min-tags", type=int, default=2, help="Bilder mit weniger Tags werden ignoriert"
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    from ..calibration import load_calibration

    calibration = load_calibration(args.calibration)
    # An existing map supplies roles, sizes and CAD offsets; only the world
    # poses are recomputed.
    existing = load_tag_map(args.tag_map) if args.tag_map else empty_tag_map(args.frame_id, args.anchor)
    detector = build_detector(args.family, args.backend)
    source = frame_sources.open_source(
        args.source, resolution=tuple(args.resolution) if args.resolution else None
    )

    observations: list[dict] = []
    frame_count = 0
    try:
        while True:
            image = source.read()
            if image is None:
                break
            frame_count += 1
            active = fit_calibration(
                calibration, frame_sources.image_size(image), args.allow_resolution_mismatch
            )
            tag_poses = estimate_tag_poses(
                detector.detect(frame_sources.to_gray(image)),
                active,
                tag_map=existing,
                default_size_m=args.tag_size_m,
                max_reprojection_error_px=args.max_reproj_error_px,
            )
            found = poses_by_tag(tag_poses)
            status = ", ".join(str(tag_id) for tag_id in sorted(found)) or "-"
            if len(found) < args.min_tags:
                print(f"Bild {frame_count:>3}: {len(found)} Tags ({status}) -- uebersprungen")
                continue
            observations.append(found)
            print(f"Bild {frame_count:>3}: Tags {status}")
    finally:
        source.close()

    if not observations:
        print(
            "Keine Aufnahme mit mindestens zwei Tags. Ohne gemeinsam gesehene "
            "Tags gibt es keine Relation und damit keine Karte."
        )
        return 1

    placed = place_tags(observations, anchor_tag_id=args.anchor)
    print(f"\n{len(placed)} Tags platziert, Anker = {args.anchor}")
    for tag_id in sorted(placed):
        print(f"  Tag {tag_id:>3}  {describe_pose(placed[tag_id])}")

    unreachable = missing_tags(observations, placed)
    if unreachable:
        print(
            f"\nOhne Pfad zum Anker und deshalb nicht in der Karte: {unreachable}\n"
            "  Es fehlt ein Bild, das einen dieser Tags zusammen mit einem "
            "bereits verbundenen Tag zeigt."
        )

    print("\nSchliessfehler je Kante:")
    print(format_residual_report(residuals(observations, placed)))

    updated = with_world_poses(existing, placed, default_size_m=args.tag_size_m)
    save_tag_map(args.out, updated)
    print(f"\nTag-Map geschrieben: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
