"""AprilTags erkennen -- an der Kamera oder auf abgelegten Bildern.

    python -m tagloc.cli.detect --source camera:0 \
        --calibration data/calibration/cam_ceiling.json \
        --tag-map config/tagmap.json --overlay

    # reproducible, no hardware needed: image folder instead of camera
    python -m tagloc.cli.detect --source ./aufnahmen/szene/ \
        --calibration data/calibration/cam_ceiling.json --json posen.json

Per tag: ID, pose (position and quaternion xyzw), reprojection error and
ambiguity. With `--tag-map`, also the module assignment and, once a world
tag is visible, the world pose and the world tag the module sits closest to.

`--overlay` shows a window; without a screen (Pi over SSH) that's reported
once and the run continues without a display. `--save-overlay FOLDER` saves
the marked images instead -- the practical way on a headless Pi.
"""

import argparse
import math
import sys
from pathlib import Path

from .. import frames as frame_sources
from ..detector import build_detector
from ..localize import locate_modules, localize_camera
from ..overlay import Window, draw_status_bar, draw_tag_overlay, summarise
from ..pose import estimate_tag_poses
from ._common import (
    add_detection_arguments,
    add_source_arguments,
    describe_pose,
    fit_calibration,
    is_camera_source,
    load_inputs,
    open_source,
    require_cv2,
    setup_logging,
    tag_poses_to_json,
    write_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_source_arguments(parser)
    add_detection_arguments(parser)
    parser.add_argument("--overlay", action="store_true", help="Fenster mit Markierungen")
    parser.add_argument(
        "--overlay-wait-ms",
        type=int,
        default=None,
        help="Wartezeit je Bild im Fenster; 0 = auf Tastendruck warten "
        "(Standard: 0 im Bildordner, 1 an der Kamera)",
    )
    parser.add_argument(
        "--save-overlay",
        type=Path,
        default=None,
        help="Markierte Bilder in diesen Ordner schreiben (braucht kein Display)",
    )
    parser.add_argument(
        "--json", type=Path, default=None, help="Posen des letzten Bildes als JSON schreiben"
    )
    parser.add_argument("--limit", type=int, default=None, help="Nur so viele Frames")
    parser.add_argument(
        "--frame-id", default="", help="Bezugsrahmen der Ausgabe; leer = aus der Kalibrierung"
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit muss mindestens 1 sein")
    cv2 = require_cv2()

    calibration, tag_map = load_inputs(args)
    detector = build_detector(args.family, args.backend)
    source = open_source(args)

    live = is_camera_source(args.source)
    frame_id = args.frame_id or calibration.frame_id or "cam"
    window = Window("tagloc detect", enabled=args.overlay)
    if args.overlay and not window.enabled:
        print(
            "Kein Bildschirm verfuegbar -- weiter ohne Fenster. "
            "Markierte Bilder bekommt man mit --save-overlay ORDNER.",
            file=sys.stderr,
        )
    wait_ms = args.overlay_wait_ms if args.overlay_wait_ms is not None else (1 if live else 0)
    if args.save_overlay is not None:
        args.save_overlay.mkdir(parents=True, exist_ok=True)

    last_poses: list = []
    processed = 0
    try:
        while args.limit is None or processed < args.limit:
            image = source.read()
            if image is None:
                break
            processed += 1
            size = frame_sources.image_size(image)
            active = fit_calibration(calibration, size, args.allow_resolution_mismatch)
            observations = detector.detect(frame_sources.to_gray(image))
            tag_poses = estimate_tag_poses(
                observations,
                active,
                tag_map=tag_map,
                default_size_m=args.tag_size_m,
                max_reprojection_error_px=args.max_reproj_error_px,
            )
            last_poses = tag_poses

            print(f"--- Frame {processed}: {summarise(tag_poses, tag_map)}")
            for tag_pose in tag_poses:
                marker = " MEHRDEUTIG" if tag_pose.is_ambiguous else ""
                print(
                    f"  Tag {tag_pose.tag_id:>3}  {describe_pose(tag_pose.pose_cam_tag)}"
                    f"  e={tag_pose.reprojection_error_px:.2f}px{marker}"
                )

            # Without a world tag the chain stays in the camera frame. That's
            # normal operation for the eye-in-hand camera between two world
            # tags, not a special case: modules are still resolved, just
            # relative to the camera.
            localization = localize_camera(
                tag_poses, tag_map, max_reprojection_error_px=args.max_reproj_error_px
            )
            target_frame = tag_map.frame_id if localization is not None else frame_id
            if localization is not None:
                print(
                    f"  Kamerapose im {target_frame}-KS: "
                    f"{describe_pose(localization.pose_world_cam)}"
                )
                print(
                    f"  Welttags {list(localization.world_tag_ids)}, "
                    f"naechster {localization.primary_tag_id}, "
                    f"Streuung {localization.spread_m * 1000.0:.2f} mm / "
                    f"{math.degrees(localization.spread_rad):.3f} deg"
                )
            for location in locate_modules(
                tag_poses,
                tag_map,
                localization=localization,
                frame_id=target_frame,
                max_reprojection_error_px=args.max_reproj_error_px,
            ):
                reference = (
                    f"  @Welttag {location.reference_tag_id}"
                    if location.reference_tag_id >= 0
                    else ""
                )
                print(
                    f"  Modul {location.module_id:<10} [{location.frame_id}] "
                    f"{describe_pose(location.pose)}  conf={location.confidence}{reference}"
                )

            if args.overlay or args.save_overlay is not None:
                draw_tag_overlay(
                    image, tag_poses, active, tag_map=tag_map, default_size_m=args.tag_size_m
                )
                draw_status_bar(
                    image,
                    [
                        f"Quelle {args.source}   Frame {processed}",
                        summarise(tag_poses, tag_map),
                        "q = beenden",
                    ],
                )
            if args.save_overlay is not None:
                cv2.imwrite(str(args.save_overlay / f"overlay_{processed:03d}.png"), image)
            if window.show(image, wait_ms) == "q":
                print("Abbruch mit q")
                break
    finally:
        source.close()
        window.close()

    if args.json:
        write_json(args.json, tag_poses_to_json(last_poses, frame_id, tag_map))
        print(f"JSON geschrieben: {args.json}")
    if processed == 0:
        print("Keine Frames gelesen -- ist --source richtig?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
