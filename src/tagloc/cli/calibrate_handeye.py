"""Hand-eye calibration -- the one-time offset flange -> camera.

The hand camera is rigidly mounted on the robot, so ``T_flansch_cam`` is a constant of
the hardware. It is determined **once** and only read afterwards.

Recording the calibration run (kept separate from the computation, so it can be
repeated without moving the robot again):

1. Place a board somewhere. It must **stay still** for the whole run. Which board does
   not matter; a world tag is a good choice because it is mounted anyway.
2. Move the robot to at least a dozen clearly different poses from which the camera
   sees the board. **Rotate around several axes**: pure translation is not enough,
   because then the rotation is not determined.
3. Save one image per pose and note the robot pose ``T_base_flansch``.

This produces a sample list:

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

Image paths are relative to the sample list. Then run:

    python -m tagloc.cli.calibrate_handeye --samples fahrt/aufnahmen.json \\
        --calibration data/calibration/cam_flange.json \\
        --out data/handeye/cam_flange.json

From a session of the hand Pi (``data/handeye/sessions/<Zeit>/``, written by
``FinishHandEyeCalibration``), the same calibration is recomputed with

    python -m tagloc.cli.calibrate_handeye --session data/handeye/sessions/<Zeit> \\
        [--out data/handeye/cam_flange.json]

This runs a closed-form solution, a robust fit, and then a joint refinement against the
board corners, including the leave-one-out prediction error. Without ``--out`` the
result is only computed and displayed.

The quality measure is printed at the end. The tag is fixed, so it must come out at the
same position from every robot pose. If the spread is large, one of the following is
wrong: the camera calibration, the tag size entered, or the reported robot pose.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from ..detector import build_detector
from ..geometry import pose_from_dict
from ..handeye import (
    BOARD_SCALE_WARNING,
    BoardRefinement,
    HandEye,
    RefinementError,
    UnknownSessionSchema,
    load_session,
    save_hand_eye,
    solve_hand_eye,
    solve_hand_eye_robust,
)
from ..tagmap import DEFAULT_TAG_FAMILY
from ._common import add_decoder_arguments, describe_pose, detector_options, setup_logging

SAMPLES_SCHEMA = "wsc.vision.handeye.samples/1"

#: Above this spread the calibration is not fit for moving the robot. Not an abort: the
#: file is still written so it can be inspected, but the warning must be unmissable.
SPREAD_WARNING_M = 0.005


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for this tool."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--session",
        type=Path,
        help="Datensatz einer Sitzung (Ordner oder session.json): neu rechnen, "
        "statt Bilder auszuwerten",
    )
    parser.add_argument("--samples", type=Path, help="Aufnahmeliste der Kalibrierfahrt")
    parser.add_argument("--calibration", type=Path, help="Kamerakalibrierung (JSON)")
    parser.add_argument(
        "--out",
        type=Path,
        help="Zieldatei der Hand-Auge-Kalibrierung (bei --session optional)",
    )
    parser.add_argument(
        "--no-scale",
        action="store_true",
        help="Bei --session den Brettmassstab nicht mitschaetzen",
    )
    parser.add_argument("--family", default=DEFAULT_TAG_FAMILY, help="Tag-Familie")
    add_decoder_arguments(parser)
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
    """Read the sample list. Image paths are resolved relative to the list.

    Returns:
        A tuple ``(tag_id, tag_size_m, samples)``, where ``samples`` is a list of
        ``(image_path, pose_base_flange)`` pairs.

    Raises:
        SystemExit: If the schema is unknown, a sample is incomplete, or the list is
            empty.
    """
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


def solve_session(path: Path, *, estimate_scale: bool = True, out: Path | None = None) -> int:
    """Recompute a saved session and print the key figures.

    The computation lives in ``tagloc.handeye`` (``refine_board_session``, the same path
    as on the hand Pi). This function only handles the output.

    Args:
        path: Session folder or ``session.json`` file.
        estimate_scale: Whether to also estimate the board scale during refinement.
        out: Target file for the hand-eye calibration. If ``None``, nothing is written.

    Returns:
        Exit code ``0``.

    Raises:
        SystemExit: If the session schema is unknown.
    """
    try:
        dataset = load_session(path)
    except UnknownSessionSchema as error:
        raise SystemExit(str(error)) from error
    print(
        f"Sitzung {dataset.path.parent.name}: {len(dataset.flanges)} von "
        f"{dataset.total_count} Aufnahmen"
    )
    print(f"Intrinsik: {dataset.intrinsics_source}")

    closed, spread_m, spread_deg = solve_hand_eye(dataset.flanges, dataset.targets)
    print(f"\nGeschlossene Loesung (Park-Martin): {describe_pose(closed)}")
    print(f"  Streuung des Ziel-Orts: {spread_m * 1000.0:.2f} mm / {spread_deg:.3f} deg")
    robust = solve_hand_eye_robust(dataset.flanges, dataset.targets)
    print(
        f"\nRobust (Streuung der Brettlage; bei Brett nur Vergleich): "
        f"{describe_pose(robust.pose_flange_cam)}"
    )
    print(
        f"  RMS {robust.rms_position_m * 1000.0:.2f} mm / {robust.rms_rotation_deg:.3f} deg, "
        f"{robust.used_count} Aufnahmen, verworfen: {list(robust.rejected) or 'keine'}"
    )

    refined = None
    if not dataset.can_refine:
        print("\nKeine Ecken/Intrinsik im Datensatz (Tag-Sitzung?) -- keine Verfeinerung.")
        solution = robust
    else:
        # Outliers are rejected after refinement, in pixels, not by the Park-Martin
        # spread: a scale error would inflate that spread.
        try:
            refined = dataset.refine(estimate_scale=estimate_scale)
        except RefinementError as error:
            # Computation errors propagate as usual, as a traceback of the cause.
            raise error.__cause__ from None
        solution = refined.solution
        _print_refinement(refined)

    print(
        f"\nStreuung der Brettlage: RMS {solution.rms_position_m * 1000.0:.2f} mm / "
        f"{solution.rms_rotation_deg:.3f} deg, max {solution.max_position_m * 1000.0:.2f} mm"
    )
    if out is not None:
        save_hand_eye(
            out,
            HandEye(
                pose_flange_cam=solution.pose_flange_cam,
                frame_id=dataset.frame_id,
                rms_position_m=solution.rms_position_m,
                rms_rotation_deg=solution.rms_rotation_deg,
                sample_count=solution.used_count,
                max_position_m=solution.max_position_m,
                max_rotation_deg=solution.max_rotation_deg,
                intrinsics_source=dataset.intrinsics_source or "",
                **(refined.hand_eye_fields() if refined is not None else {}),
            ),
        )
        print(f"\nGeschrieben: {out}")
    return 0


def _print_refinement(refined: BoardRefinement) -> None:
    """Print the board-session refinement: outliers, pose, and prediction error."""
    refinement = refined.refinement
    print(f"\nVerworfen (Pixel-Ausreisser): {list(refined.solution.rejected) or 'keine'}")
    print(f"\nVerfeinert: {describe_pose(refinement.pose_flange_cam)}")
    print(
        f"  Reprojektion {refinement.reprojection_rms_px:.3f} px, Brettmassstab "
        f"{refinement.board_scale:.5f}, {refinement.iterations} Schritte, "
        f"{'konvergiert' if refinement.converged else 'NICHT konvergiert'}"
    )
    print(f"  Brett im Basis-KS: {describe_pose(refinement.pose_base_board)}")
    shift = np.linalg.norm(
        refinement.pose_flange_cam[:3, 3] - refined.closed_form.pose_flange_cam[:3, 3]
    )
    print(f"  Verschiebung von t_X gegen Park-Martin: {shift * 1000.0:.2f} mm")
    loo = refined.loo
    if loo is not None:
        print(
            f"\nLeave-one-out-Vorhersagefehler: {loo.rms_px:.3f} px, {loo.rms_m * 1000.0:.2f} mm (RMS)"
        )
        for index, error_px, error_m in zip(refined.kept, loo.error_px, loo.error_m):
            print(f"  Aufnahme {index}: {error_px:.3f} px, {error_m * 1000.0:.2f} mm")
    if abs(refinement.board_scale - 1.0) > BOARD_SCALE_WARNING:
        print("\nHINWEIS: Brettmassstab weicht ueber 0,3 % ab -- Brett nachmessen / Intrinsik pruefen.")


def main(argv=None) -> int:
    """Run the CLI: a saved session if ``--session`` is given, otherwise the sample run.

    Args:
        argv: Argument list, or ``None`` to use ``sys.argv``.

    Returns:
        Exit code ``0`` on success.

    Raises:
        SystemExit: If required arguments are missing or an input is invalid.
    """
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    if args.session is not None:
        return solve_session(args.session, estimate_scale=not args.no_scale, out=args.out)
    if args.samples is None or args.calibration is None or args.out is None:
        raise SystemExit("--samples, --calibration und --out sind noetig (oder --session).")

    import cv2

    from ..calibration import load_calibration
    from ..frames import image_size, to_gray
    from ..pose import estimate_tag_poses

    tag_id, tag_size_m, samples = load_samples(args.samples)
    calibration = load_calibration(args.calibration)
    detector = build_detector(args.family, args.backend, **detector_options(args))

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
