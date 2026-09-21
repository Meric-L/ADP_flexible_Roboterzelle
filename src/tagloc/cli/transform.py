"""Koordinatentransformation -- Posen von einem KS in ein anderes.

Pure computation: no camera, no OpenCV. The fastest tool to settle a
convention question before it bites in production code.

    # transform a single pose with a known camera pose
    python -m tagloc.cli.transform --camera-pose roboterpose.json \
        --position 0.12 -0.04 0.85 --orientation 0 0 0 1

    # a whole result file from detect, camera pose from reference tags
    python -m tagloc.cli.transform --input posen.json --tag-map config/tagmap.json \
        --from-reference --out posen_welt.json

    # the reverse direction
    python -m tagloc.cli.transform --camera-pose roboterpose.json --invert \
        --position 1.20 0.33 0.04 --orientation 0 0 0.3827 0.9239

The transform file (`--camera-pose`) contains `T_target_source`, typically
`T_world_cam`:

    {"position": [1.0, 0.2, 1.8], "orientation": [0, 0, 0, 1]}
"""

import argparse
import json
from pathlib import Path

from ..geometry import compose, identity, invert, pose_from_dict, pose_to_dict
from ..localize import camera_pose_from_reference_tags
from ..observations import TagPose
from ..tagmap import load_tag_map
from ._common import describe_pose, poses_from_json, setup_logging, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=None, help="Datei von 'detect --json'")
    parser.add_argument("--position", type=float, nargs=3, default=None, metavar=("X", "Y", "Z"))
    parser.add_argument(
        "--orientation", type=float, nargs=4, default=(0.0, 0.0, 0.0, 1.0), metavar=("X", "Y", "Z", "W")
    )
    parser.add_argument(
        "--camera-pose", type=Path, default=None, help="JSON mit T_ziel_quelle"
    )
    parser.add_argument("--tag-map", type=Path, default=None, help="Tag-Map (JSON)")
    parser.add_argument(
        "--from-reference",
        action="store_true",
        help="T_world_cam aus den Referenz-Tags der Eingabedatei bestimmen",
    )
    parser.add_argument("--invert", action="store_true", help="Transformation umkehren")
    parser.add_argument("--to", dest="to_frame", default="world", help="Name des Ziel-KS")
    parser.add_argument("--out", type=Path, default=None, help="Ergebnis als JSON schreiben")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _load_transform(args, poses) -> tuple:
    """Return `(T_target_source, origin description)`."""
    if args.camera_pose is not None:
        data = json.loads(Path(args.camera_pose).read_text(encoding="utf-8"))
        return pose_from_dict(data), str(args.camera_pose)
    if args.from_reference:
        if args.tag_map is None:
            raise SystemExit("--from-reference braucht --tag-map")
        tag_map = load_tag_map(args.tag_map)
        tag_poses = [TagPose(tag_id, pose) for tag_id, pose in poses]
        transform = camera_pose_from_reference_tags(tag_poses, tag_map)
        if transform is None:
            known = sorted(tag_map.reference_poses())
            raise SystemExit(
                "Kein Referenz-Tag in der Eingabe. Die Karte kennt als Referenz: "
                f"{known or 'keinen'}"
            )
        return transform, "Referenz-Tags"
    return identity(), "Identitaet (keine Transformation angegeben)"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    source_frame = "quelle"
    poses: list[tuple[int, object]] = []
    if args.input is not None:
        source_frame, poses = poses_from_json(args.input)
    elif args.position is not None:
        poses = [(-1, pose_from_dict({"position": args.position, "orientation": args.orientation}))]
    else:
        raise SystemExit("Entweder --input oder --position angeben")

    transform, origin = _load_transform(args, poses)
    if args.invert:
        transform = invert(transform)

    print(f"Transformation aus {origin}")
    print(f"  T_{args.to_frame}_{source_frame}: {describe_pose(transform)}")
    print()

    results = []
    for tag_id, pose in poses:
        transformed = compose(transform, pose)
        label = f"Tag {tag_id}" if tag_id >= 0 else "Pose"
        print(f"{label:<10} {describe_pose(transformed)}")
        results.append({"tagId": tag_id, "pose": pose_to_dict(transformed)})

    if args.out is not None:
        write_json(
            args.out,
            {
                "schema": "wsc.vision.detections.cli/1",
                "frameId": args.to_frame,
                "poses": [
                    {"tagId": item["tagId"], "moduleId": "", "pose": item["pose"],
                     "reprojErrorPx": 0.0, "ambiguous": False}
                    for item in results
                ],
            },
        )
        print(f"\nGeschrieben: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
