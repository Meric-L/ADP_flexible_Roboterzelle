"""Shared arguments, loading paths and output formats for the CLI tools.

Guideline for the error messages here: a message states **what to do**, not
just what's missing. A traceback from `load_calibration` doesn't answer
"now what?"; `raise SystemExit` with the next command does.
"""

import argparse
import json
import logging
from pathlib import Path

from .. import jsonio
from ..calibration import CameraCalibration, load_calibration
from ..geometry import Pose, pose_from_dict, pose_to_dict
from ..tagmap import TagMap, empty_tag_map, load_tag_map

#: Exchange format between `detect` and `transform`.
CLI_SCHEMA = "wsc.vision.detections.cli/1"


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def require_cv2():
    """Import OpenCV, or explain how to get it.

    The probe access to `cv2.imread` is deliberate: a broken install stays
    importable as an empty namespace package and would otherwise only fail
    mid-run, once a camera is already open.
    """
    try:
        import cv2

        _ = cv2.imread
    except Exception as error:  # ImportError oder AttributeError
        raise SystemExit(
            f"OpenCV ist nicht nutzbar ({error}).\n"
            "  Installieren mit: pip install opencv-contrib-python\n"
            "  Ohne OpenCV laeuft nur 'python -m tagloc.cli.transform' (reine Rechnung)."
        ) from error
    return cv2


def add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        default="camera:0",
        help="'camera[:n]', 'picamera', 'realsense', ein Bildordner oder eine Bilddatei",
    )
    parser.add_argument(
        "--resolution",
        type=int,
        nargs=2,
        metavar=("BREITE", "HOEHE"),
        default=None,
        help="Gewuenschte Kameraaufloesung",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-Ausgaben")


def add_detection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--calibration", type=Path, required=True, help="Kalibrierdatei (JSON)"
    )
    parser.add_argument("--tag-map", type=Path, default=None, help="Tag-Map (JSON)")
    parser.add_argument("--family", default="tag36h11", help="Tag-Familie")
    parser.add_argument(
        "--backend", default="aruco", choices=("aruco", "pupil", "auto"), help="Detektor"
    )
    parser.add_argument(
        "--tag-size-m",
        type=float,
        default=0.05,
        help="Rueckfall-Tag-Groesse fuer Tags, die nicht in der Karte stehen",
    )
    parser.add_argument(
        "--max-reproj-error-px",
        type=float,
        default=3.0,
        help="Tags mit groesserem Reprojektionsfehler werden verworfen",
    )
    parser.add_argument(
        "--allow-resolution-mismatch",
        action="store_true",
        help="Intrinsik auf die Bildgroesse skalieren statt abzubrechen",
    )


def open_source(args, *, loop: bool = False):
    """Open the `--source` argument, explaining the accepted forms on error."""
    from .. import frames as frame_sources

    try:
        return frame_sources.open_source(
            args.source,
            resolution=tuple(args.resolution) if args.resolution else None,
            loop=loop,
        )
    except (FileNotFoundError, RuntimeError) as error:
        raise SystemExit(
            f"{error}\n"
            "  --source nimmt: 'camera:0' (Webcam), 'picamera' (Pi), einen Ordner\n"
            "  mit Bildern oder eine einzelne Bilddatei. Bilder ohne Kamera erzeugt\n"
            "  'python tools/make_synthetic_scene.py --out ORDNER'."
        ) from error


def is_camera_source(spec) -> bool:
    """Return whether `--source` means a live camera rather than stored images."""
    return str(spec).startswith(("camera", "picamera", "realsense"))


def load_calibration_or_exit(path: Path) -> CameraCalibration:
    try:
        return load_calibration(path)
    except (FileNotFoundError, ValueError, KeyError) as error:
        raise SystemExit(
            f"Kalibrierung nicht verwendbar: {error}\n"
            "  Neu rechnen mit: python -m tagloc.cli.calibrate --source ORDNER "
            f"--out {path}"
        ) from error


def load_tag_map_or_exit(path: Path) -> TagMap:
    try:
        return load_tag_map(path)
    except (FileNotFoundError, ValueError, KeyError) as error:
        raise SystemExit(
            f"Tag-Map nicht verwendbar: {error}\n"
            "  Neu bauen mit: python -m tagloc.cli.build_tagmap --source ORDNER "
            f"--calibration KALIB.json --anchor 0 --out {path}"
        ) from error


def load_inputs(args) -> tuple[CameraCalibration, TagMap]:
    """Load calibration and tag map; use an empty map if none was given."""
    calibration = load_calibration_or_exit(args.calibration)
    tag_map = load_tag_map_or_exit(args.tag_map) if args.tag_map else empty_tag_map()
    return calibration, tag_map


def fit_calibration(calibration: CameraCalibration, size, allow_mismatch: bool):
    """Check the resolution and scale only if explicitly allowed.

    A calibration for the wrong resolution makes every pose off by the
    scale factor -- plausibly off, so unnoticed. Hence an abort here, not
    just a warning.
    """
    from ..calibration import fit_to_resolution

    try:
        return fit_to_resolution(calibration, size, allow_scaling=allow_mismatch)
    except ValueError as error:
        if allow_mismatch:
            raise  # Seitenverhaeltnis passt nicht -- kein Fall fuer den Hinweis unten
        raise SystemExit(
            f"{error}\n"
            "  Entweder in der Aufloesung der Kalibrierung aufnehmen "
            "(--resolution BREITE HOEHE)\n"
            "  oder das Skalieren mit --allow-resolution-mismatch ausdruecklich erlauben."
        ) from error


def _pose_entry(
    tag_id,
    pose: Pose,
    *,
    module_id: str = "",
    reproj_error_px: float = 0.0,
    ambiguous: bool = False,
) -> dict:
    """Ein Eintrag der `poses`-Liste im CLI-Austauschformat."""
    return {
        "tagId": int(tag_id),
        "moduleId": module_id,
        "pose": pose_to_dict(pose),
        "reprojErrorPx": round(float(reproj_error_px), 4),
        "ambiguous": bool(ambiguous),
    }


def tag_poses_to_json(tag_poses, frame_id: str, tag_map: TagMap | None = None) -> dict:
    """Return the detection result as a JSON-ready dict."""
    entries = []
    for tag_pose in tag_poses:
        entry = tag_map.get(tag_pose.tag_id) if tag_map is not None else None
        entries.append(
            _pose_entry(
                tag_pose.tag_id,
                tag_pose.pose_cam_tag,
                module_id=entry.module_id if entry is not None else "",
                reproj_error_px=tag_pose.reprojection_error_px,
                ambiguous=tag_pose.is_ambiguous,
            )
        )
    return {"schema": CLI_SCHEMA, "frameId": frame_id, "poses": entries}


def poses_to_json(pairs, frame_id: str) -> dict:
    """Same, for plain `(tag_id, Pose)` pairs -- the output of `transform`."""
    return {
        "schema": CLI_SCHEMA,
        "frameId": frame_id,
        "poses": [_pose_entry(tag_id, pose) for tag_id, pose in pairs],
    }


def poses_from_json(path: Path) -> tuple[str, list[tuple[int, Pose]]]:
    """Read a file written by `detect --json`."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(
            f"Eingabedatei nicht lesbar: {error}\n"
            "  Erwartet wird die Ausgabe von: python -m tagloc.cli.detect "
            "--json DATEI ..."
        ) from error
    if data.get("schema") != CLI_SCHEMA:
        raise SystemExit(
            f"Unerwartetes Schema in {path}: {data.get('schema')} (erwartet {CLI_SCHEMA}).\n"
            "  Die Datei muss von 'python -m tagloc.cli.detect --json DATEI' stammen."
        )
    try:
        return str(data.get("frameId", "")), [
            (int(item["tagId"]), pose_from_dict(item["pose"]))
            for item in data.get("poses", [])
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(
            f"Eintrag in {path} unvollstaendig: {error}\n"
            '  Jeder Eintrag braucht "tagId" und "pose" mit "position" und "orientation".'
        ) from error


def write_json(path, payload: dict) -> None:
    """Schreibt eine CLI-Ausgabe; atomar, siehe `tagloc.jsonio.write_json`."""
    jsonio.write_json(path, payload)


def describe_pose(pose: Pose) -> str:
    """Return a pose as a compact line."""
    data = pose_to_dict(pose)
    position = " ".join(f"{value:8.4f}" for value in data["position"])
    orientation = " ".join(f"{value:7.4f}" for value in data["orientation"])
    return f"pos [{position}] m   quat [{orientation}] xyzw"
