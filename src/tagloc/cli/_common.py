"""Gemeinsame Argumente, Ladepfade und Ausgabeformate fuer die CLI-Werkzeuge.

Leitlinie fuer die Fehlermeldungen hier: Eine Meldung sagt, **was zu tun ist**,
nicht nur, was fehlt. Ein Traceback aus `load_calibration` beantwortet nicht
"und jetzt?"; `raise SystemExit` mit dem naechsten Befehl tut es.
"""

import argparse
import json
import logging
from pathlib import Path

from ..calibration import CameraCalibration, load_calibration
from ..geometry import Pose, pose_from_dict, pose_to_dict
from ..tagmap import DEFAULT_TAG_FAMILY, TagMap, empty_tag_map, load_tag_map

#: Austauschformat zwischen `detect` und `transform`.
CLI_SCHEMA = "wsc.vision.detections.cli/1"


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def require_cv2():
    """Importiert OpenCV oder erklaert, wie man es bekommt.

    Der Probezugriff auf `cv2.imread` ist Absicht: Eine kaputte Installation
    bleibt als leeres Namespace-Paket importierbar und scheiterte sonst erst
    mitten im Lauf, wenn eine Kamera schon offen ist.
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


def add_decoder_arguments(parser: argparse.ArgumentParser) -> None:
    """Frei waehlbare Detektor-Parameter, ohne Angabe gilt der OpenCV-Standard."""
    parser.add_argument(
        "--detector-param",
        action="append",
        default=[],
        metavar="NAME=WERT",
        help=(
            "OpenCV-DetectorParameters-Attribut setzen, z. B. errorCorrectionRate=0.8 "
            "(wiederholbar; Wert als JSON, sonst als Text)"
        ),
    )


def add_detection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--calibration", type=Path, required=True, help="Kalibrierdatei (JSON)"
    )
    parser.add_argument("--tag-map", type=Path, default=None, help="Tag-Map (JSON)")
    parser.add_argument("--family", default=DEFAULT_TAG_FAMILY, help="Tag-Familie")
    parser.add_argument(
        "--backend", default="aruco", choices=("aruco", "pupil", "auto"), help="Detektor"
    )
    add_decoder_arguments(parser)
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


def parse_detector_params(items) -> dict:
    """`["NAME=WERT", ...]` -> `{NAME: WERT}`, Wert per `json.loads`, sonst Text.

    `SystemExit` mit einer Meldung, die sagt, was zu tun ist, bei einem Eintrag
    ohne `=`.
    """
    parameters = {}
    for item in items or []:
        name, separator, raw = item.partition("=")
        if not separator or not name.strip():
            raise SystemExit(
                f"--detector-param erwartet NAME=WERT (z. B. errorCorrectionRate=0.8), "
                f"nicht '{item}'"
            )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        parameters[name.strip()] = value
    return parameters


def detector_options(args) -> dict:
    """Die Detektor-Parameter aus den CLI-Argumenten als Keywords fuer `build_detector`."""
    return {"detector_parameters": parse_detector_params(args.detector_param)}


def open_source(args, *, loop: bool = False):
    """Oeffnet das Argument `--source` und erklaert bei einem Fehler die erlaubten Formen."""
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
    """Liefert, ob `--source` eine Live-Kamera statt gespeicherter Bilder meint."""
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
    """Laedt Kalibrierung und Tag-Karte; nimmt eine leere Karte, wenn keine angegeben wurde."""
    calibration = load_calibration_or_exit(args.calibration)
    tag_map = load_tag_map_or_exit(args.tag_map) if args.tag_map else empty_tag_map()
    return calibration, tag_map


def fit_calibration(calibration: CameraCalibration, size, allow_mismatch: bool):
    """Prueft die Aufloesung und skaliert nur, wenn es ausdruecklich erlaubt ist.

    Eine Kalibrierung fuer die falsche Aufloesung verschiebt jede Pose um den
    Skalierungsfaktor -- plausibel verschoben, also unbemerkt. Deshalb hier
    ein Abbruch, nicht nur eine Warnung.
    """
    from ..calibration import check_resolution, scale_to_resolution

    if tuple(calibration.image_size) == tuple(size):
        return calibration
    if not allow_mismatch:
        try:
            check_resolution(calibration, size)
        except ValueError as error:
            raise SystemExit(
                f"{error}\n"
                "  Entweder in der Aufloesung der Kalibrierung aufnehmen "
                "(--resolution BREITE HOEHE)\n"
                "  oder das Skalieren mit --allow-resolution-mismatch ausdruecklich erlauben."
            ) from error
    return scale_to_resolution(calibration, size)


def tag_poses_to_json(tag_poses, frame_id: str, tag_map: TagMap | None = None) -> dict:
    """Liefert das Erkennungsergebnis als JSON-faehiges dict."""
    entries = []
    for tag_pose in tag_poses:
        entry = tag_map.get(tag_pose.tag_id) if tag_map is not None else None
        entries.append(
            {
                "tagId": tag_pose.tag_id,
                "moduleId": entry.module_id if entry is not None else "",
                "pose": pose_to_dict(tag_pose.pose_cam_tag),
                "reprojErrorPx": round(float(tag_pose.reprojection_error_px), 4),
                "ambiguous": bool(tag_pose.is_ambiguous),
            }
        )
    return {"schema": CLI_SCHEMA, "frameId": frame_id, "poses": entries}


def poses_from_json(path: Path) -> tuple[str, list[tuple[int, Pose]]]:
    """Liest eine Datei, die `detect --json` geschrieben hat."""
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
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def describe_pose(pose: Pose) -> str:
    """Liefert eine Pose als kompakte Zeile."""
    data = pose_to_dict(pose)
    position = " ".join(f"{value:8.4f}" for value in data["position"])
    orientation = " ".join(f"{value:7.4f}" for value in data["orientation"])
    return f"pos [{position}] m   quat [{orientation}] xyzw"
