"""Hand-Auge-Kalibrierung -- der einmalige Versatz Flansch -> Kamera.

Die Kamera sitzt starr am Roboter, also ist `T_flansch_cam` eine Konstante der
Hardware. Sie wird **einmal** bestimmt und danach nur noch gelesen.

Aufnahme der Kalibrierfahrt (getrennt vom Rechnen, damit man sie wiederholen
kann, ohne den Roboter noch einmal zu bewegen):

1. Einen Tag irgendwo hinlegen -- er muss waehrend der ganzen Fahrt **liegen
   bleiben**. Welcher, ist egal; ein Welttag bietet sich an, weil er ohnehin
   haengt.
2. Den Roboter mindestens ein Dutzend deutlich verschiedene Posen anfahren
   lassen, aus denen die Kamera den Tag sieht. **Um mehrere Achsen drehen** --
   reines Verschieben genuegt nicht, dann ist die Rotation nicht bestimmt.
3. Je Pose ein Bild speichern und die Roboterpose `T_base_flansch` notieren.

Das ergibt eine Aufnahmeliste:

    {
      "schema": "wsc.vision.handeye.samples/1",
      "tagId": 2,
      "tagSizeM": 0.100,
      "samples": [
        { "image": "pose_000.png",
          "poseBaseFlange": { "position": [0.20, 0.05, 0.50],
                              "orientation": [0, 0.131, 0, 0.991] } },
        { "image": "pose_001.png", "poseBaseFlange": { ... } }
      ]
    }

Bildpfade sind relativ zur Aufnahmeliste. Dann:

    python -m tagloc.cli.calibrate_handeye --samples fahrt/aufnahmen.json \\
        --calibration data/calibration/cam_flange.json \\
        --out data/handeye/cam_flange.json

Das Guetemass steht danach in der Ausgabe: der Tag lag fest, also muss er aus
jeder Roboterpose an derselben Stelle herauskommen. Streut das, stimmt
entweder die Kamerakalibrierung, die eingetragene Tag-Groesse oder die
gemeldete Roboterpose nicht.
"""

import argparse
import json
from pathlib import Path

from ..detector import build_detector
from ..geometry import pose_from_dict
from ..handeye import HandEye, save_hand_eye, solve_hand_eye
from ._common import describe_pose, setup_logging

SAMPLES_SCHEMA = "wsc.vision.handeye.samples/1"

#: Ueber dieser Streuung taugt die Kalibrierung nicht zum Anfahren. Kein
#: Abbruch -- die Datei wird geschrieben, damit man sie ansehen kann, aber der
#: Hinweis muss unuebersehbar sein.
SPREAD_WARNING_M = 0.005


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--samples", type=Path, required=True, help="Aufnahmeliste der Kalibrierfahrt"
    )
    parser.add_argument(
        "--calibration", type=Path, required=True, help="Kamerakalibrierung (JSON)"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Zieldatei der Hand-Auge-Kalibrierung"
    )
    parser.add_argument("--family", default="tag36h11", help="Tag-Familie")
    parser.add_argument("--backend", default="aruco", help="Detektor-Backend")
    parser.add_argument(
        "--frame-id", default="", help="Bezugsrahmen; leer = aus der Kalibrierung"
    )
    parser.add_argument(
        "--max-reproj-error-px",
        type=float,
        default=2.0,
        help="Aufnahmen mit groesserem Reprojektionsfehler werden verworfen",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def load_samples(path: Path) -> tuple[int, float, list]:
    """Read the capture list. Image paths resolve relative to the list."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema != SAMPLES_SCHEMA:
        raise SystemExit(
            f"Unbekanntes Aufnahme-Schema '{schema}' in {path} (erwartet {SAMPLES_SCHEMA})"
        )
    folder = path.parent
    samples = []
    for index, raw in enumerate(data.get("samples", [])):
        image = raw.get("image")
        robot_pose = raw.get("poseBaseFlange")
        if not image or not robot_pose:
            raise SystemExit(
                f"Aufnahme {index} in {path} braucht 'image' und 'poseBaseFlange'"
            )
        samples.append((folder / image, pose_from_dict(robot_pose)))
    if not samples:
        raise SystemExit(f"Keine Aufnahmen in {path}")
    return int(data.get("tagId", 0)), float(data.get("tagSizeM", 0.05)), samples


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    import cv2

    from ..calibration import load_calibration
    from ..frames import image_size, to_gray
    from ..pose import estimate_tag_poses

    tag_id, tag_size_m, samples = load_samples(args.samples)
    calibration = load_calibration(args.calibration)
    detector = build_detector(args.family, args.backend)

    poses_base_flange = []
    poses_cam_tag = []
    for path, pose_base_flange in samples:
        image = cv2.imread(str(path))
        if image is None:
            print(f"  {path.name}: nicht lesbar -- uebersprungen")
            continue
        if tuple(image_size(image)) != tuple(calibration.image_size):
            raise SystemExit(
                f"{path.name} ist {image_size(image)}, die Kalibrierung gilt fuer "
                f"{tuple(calibration.image_size)}"
            )
        found = [
            tag_pose
            for tag_pose in estimate_tag_poses(
                detector.detect(to_gray(image)),
                calibration,
                default_size_m=tag_size_m,
                max_reprojection_error_px=args.max_reproj_error_px,
            )
            if tag_pose.tag_id == tag_id
        ]
        if not found:
            print(f"  {path.name}: Tag {tag_id} nicht gefunden -- uebersprungen")
            continue
        tag_pose = found[0]
        print(f"  {path.name}: Tag {tag_id}, e={tag_pose.reprojection_error_px:.2f} px")
        poses_base_flange.append(pose_base_flange)
        poses_cam_tag.append(tag_pose.pose_cam_tag)

    print(f"\n{len(poses_base_flange)} von {len(samples)} Aufnahmen verwendbar")
    pose_flange_cam, spread_m, spread_deg = solve_hand_eye(poses_base_flange, poses_cam_tag)

    print(f"\nT_flansch_cam: {describe_pose(pose_flange_cam)}")
    print(
        f"Streuung des rekonstruierten Tag-Orts: "
        f"{spread_m * 1000.0:.2f} mm / {spread_deg:.3f} deg"
    )
    if spread_m > SPREAD_WARNING_M:
        print(
            f"\nWARNUNG: ueber {SPREAD_WARNING_M * 1000.0:.0f} mm Streuung. Der Tag "
            "lag fest, also passt etwas nicht zusammen -- Kamerakalibrierung, "
            "eingetragene Tag-Groesse oder die gemeldete Roboterpose. Die Datei "
            "wird trotzdem geschrieben, taugt aber nicht zum Anfahren."
        )

    save_hand_eye(
        args.out,
        HandEye(
            pose_flange_cam=pose_flange_cam,
            frame_id=args.frame_id or calibration.frame_id,
            rms_position_m=spread_m,
            rms_rotation_deg=spread_deg,
            sample_count=len(poses_base_flange),
        ),
    )
    print(f"\nGeschrieben: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
