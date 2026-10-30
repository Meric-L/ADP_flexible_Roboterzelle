"""Gespeicherte Layer-2-Rohbilder erneut auswerten und Messstrategien vergleichen."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import cv2
import numpy as np

from tagloc.calibration import load_calibration
from tagloc.detector import build_detector
from tagloc.frames import to_gray
from tagloc.geometry import (
    average_poses,
    compose,
    pose_from_dict,
    pose_to_dict,
    rotation_distance_rad,
    translation_distance_m,
)
from tagloc.pose import estimate_tag_poses
from tagloc.handeye import load_hand_eye

SCHEMA = "adp.layer2.information-gain/1"
RESULT_SCHEMA = "adp.layer2.information-gain.result/1"
STRATEGIES = ("ein_bild", "drei_bilder", "drei_ansichten_je_ein_bild", "drei_ansichten")


def _required_path(folder: Path, name: str) -> Path:
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError(f"Pfad verlaesst den Session-Ordner: {name}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _pose(data, label: str):
    try:
        pose = pose_from_dict(data)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Ungueltige Pose {label}: {error}") from error
    if not np.all(np.isfinite(pose)):
        raise ValueError(f"Nicht-endliche Pose {label}")
    return pose


def _combine(poses: list, flange, hand_eye):
    """Wie MeasureTags: Position aus allen Bildern, Drehung nur aus eindeutigen."""
    if not poses:
        return None
    clear = [item.pose_cam_tag for item in poses if not item.is_ambiguous]
    camera_tag = np.array(average_poses(clear or [item.pose_cam_tag for item in poses]))
    camera_tag[:3, 3] = np.mean([item.pose_cam_tag[:3, 3] for item in poses], axis=0)
    return {"pose": compose(flange, hand_eye, camera_tag), "orientationValid": bool(clear)}


def _fuse(views: list[dict]):
    """Positionsmittel aller Ansichten, Quaternionmittel der eindeutigen."""
    if not views:
        return None
    clear = [view["pose"] for view in views if view["orientationValid"]]
    result = np.array(average_poses(clear or [view["pose"] for view in views]))
    result[:3, 3] = np.mean([view["pose"][:3, 3] for view in views], axis=0)
    return {"pose": result, "orientationValid": bool(clear)}


def _errors(estimate, reference):
    return {
        "positionMm": round(1000 * translation_distance_m(estimate["pose"], reference), 4),
        "orientationDeg": (
            round(math.degrees(rotation_distance_rad(estimate["pose"], reference)), 4)
            if estimate["orientationValid"] else None
        ),
    }


def _summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median": round(float(np.median(values)), 4) if values else None,
        "p90": round(float(np.percentile(values, 90)), 4) if values else None,
        "mean": round(float(np.mean(values)), 4) if values else None,
    }


def _paired_summary(values: list[float]) -> dict:
    """Mittelwert plus reproduzierbares Bootstrap-Intervall ueber ganze Trials."""
    result = _summary(values)
    result["mean95Ci"] = None
    if len(values) >= 2:
        random = np.random.default_rng(20261030)
        samples = random.choice(np.asarray(values), size=(5000, len(values)), replace=True)
        bounds = np.percentile(np.mean(samples, axis=1), [2.5, 97.5])
        result["mean95Ci"] = [round(float(bound), 4) for bound in bounds]
    return result


def summarise(trials: list[dict], has_reference: bool) -> dict:
    """Erkennungsquote und gepaarte Fehlerdifferenz je Trial, nie je Einzelbild."""
    result = {}
    for strategy in STRATEGIES:
        usable = [trial["strategies"][strategy] for trial in trials if trial["strategies"][strategy] is not None]
        row = {"successfulTrials": len(usable), "totalTrials": len(trials)}
        if has_reference:
            row["positionMm"] = _summary([entry["error"]["positionMm"] for entry in usable])
            row["orientationDeg"] = _summary([
                entry["error"]["orientationDeg"] for entry in usable
                if entry["error"]["orientationDeg"] is not None
            ])
        else:
            poses = [pose_from_dict(entry["tagInBase"]) for entry in usable]
            if poses:
                centre = average_poses(poses)
                row["repeatabilityPositionMm"] = _summary([
                    1000 * translation_distance_m(pose, centre) for pose in poses
                ])
                clear = [pose_from_dict(entry["tagInBase"]) for entry in usable if entry["orientationValid"]]
                if clear:
                    centre_rotation = average_poses(clear)
                    row["repeatabilityOrientationDeg"] = _summary([
                        math.degrees(rotation_distance_rad(pose, centre_rotation)) for pose in clear
                    ])
        result[strategy] = row
    if has_reference:
        result["pairedGain"] = {}
        for strategy in STRATEGIES[1:]:
            pairs = [
                (trial["strategies"]["ein_bild"], trial["strategies"][strategy])
                for trial in trials
                if trial["strategies"]["ein_bild"] is not None
                and trial["strategies"][strategy] is not None
            ]
            result["pairedGain"][strategy] = {
                "positionMm": _paired_summary([
                    before["error"]["positionMm"] - after["error"]["positionMm"]
                    for before, after in pairs
                ]),
                "orientationDeg": _paired_summary([
                    before["error"]["orientationDeg"] - after["error"]["orientationDeg"]
                    for before, after in pairs
                    if before["error"]["orientationDeg"] is not None
                    and after["error"]["orientationDeg"] is not None
                ]),
            }
        pairs = [
            (trial["strategies"]["drei_bilder"], trial["strategies"]["drei_ansichten_je_ein_bild"])
            for trial in trials
            if trial["strategies"]["drei_bilder"] is not None
            and trial["strategies"]["drei_ansichten_je_ein_bild"] is not None
        ]
        result["pairedGain"]["ansichten_gegen_drei_bilder_gleiches_budget"] = {
            "positionMm": _paired_summary([
                before["error"]["positionMm"] - after["error"]["positionMm"]
                for before, after in pairs
            ]),
            "orientationDeg": _paired_summary([
                before["error"]["orientationDeg"] - after["error"]["orientationDeg"]
                for before, after in pairs
                if before["error"]["orientationDeg"] is not None
                and after["error"]["orientationDeg"] is not None
            ]),
        }
    return result


def evaluate(session_path: Path) -> dict:
    session_path = session_path.resolve()
    folder = session_path.parent
    data = json.loads(session_path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"Erwartet schema {SCHEMA}")
    tag_id = data["tagId"]
    tag_size_m = data["tagSizeM"]
    if type(tag_id) is not int or tag_id < 0 or not isinstance(tag_size_m, (int, float)) or tag_size_m <= 0:
        raise ValueError("tagId oder tagSizeM ungueltig")
    calibration = load_calibration(_required_path(folder, data["calibration"]))
    hand_eye_file = load_hand_eye(_required_path(folder, data["handEye"]))
    if calibration.frame_id and hand_eye_file.frame_id and calibration.frame_id != hand_eye_file.frame_id:
        raise ValueError("Kamera- und Hand-Auge-Datei haben verschiedene frameId-Werte")
    hand_eye = hand_eye_file.pose_flange_cam
    reference = _pose(data["referenceTagInBase"], "referenceTagInBase") if "referenceTagInBase" in data else None
    detector = build_detector(
        data.get("tagFamily", "tag36h11"),
        data.get("detectorBackend", "aruco"),
        subpixel_corner_refinement=bool(data.get("subpixelCornerRefinement", False)),
        corner_refinement_method=data.get("cornerRefinementMethod", "subpix"),
        apriltag_quad_decimate=float(data.get("apriltagQuadDecimate", 0.0)),
        detector_parameters=data.get("detectorParameters"),
    )
    max_reprojection_error_px = float(data.get("maxReprojectionErrorPx", 3.0))
    trials = []
    if not data.get("trials"):
        raise ValueError("Mindestens ein Trial ist erforderlich")
    for trial_index, trial in enumerate(data["trials"], 1):
        if len(trial.get("views", [])) != 3:
            raise ValueError(f"Trial {trial_index}: genau drei Ansichten erforderlich")
        view_rows = []
        view_estimates = []
        first_estimates = []
        for view_index, view in enumerate(trial["views"], 1):
            if len(view.get("images", [])) != 3:
                raise ValueError(f"Trial {trial_index}, Ansicht {view_index}: genau drei Bilder erforderlich")
            flange = _pose(view["flangeInBase"], f"Trial {trial_index}, Ansicht {view_index}")
            observations = []
            image_rows = []
            for name in view["images"]:
                path = _required_path(folder, name)
                image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                if image is None:
                    raise ValueError(f"Bild nicht lesbar: {path}")
                if (image.shape[1], image.shape[0]) != calibration.image_size:
                    raise ValueError(f"Bildgroesse passt nicht zur Kalibrierung: {path}")
                matches = estimate_tag_poses(
                    detector.detect(to_gray(image)), calibration,
                    default_size_m=float(tag_size_m),
                    max_reprojection_error_px=max_reprojection_error_px,
                )
                pose = next((item for item in matches if item.tag_id == tag_id), None)
                observations.append(pose)
                image_rows.append({
                    "file": name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "detected": pose is not None,
                    "ambiguous": pose.is_ambiguous if pose is not None else None,
                    "reprojectionErrorPx": (
                        round(float(pose.reprojection_error_px), 4)
                        if pose is not None and math.isfinite(pose.reprojection_error_px) else None
                    ),
                })
            available = [item for item in observations if item is not None]
            view_estimates.append(_combine(available, flange, hand_eye) if len(available) >= 2 else None)
            first_estimates.append(_combine([observations[0]], flange, hand_eye) if observations[0] is not None else None)
            view_rows.append({"index": view_index, "images": image_rows, "detectedFrames": len(available)})
        single = first_estimates[0]
        diverse = _fuse(first_estimates) if all(view is not None for view in first_estimates) else None
        multi = _fuse(view_estimates) if all(view is not None for view in view_estimates) else None
        estimates = {
            "ein_bild": single,
            "drei_bilder": view_estimates[0],
            "drei_ansichten_je_ein_bild": diverse,
            "drei_ansichten": multi,
        }
        strategies = {
            name: ({
                "tagInBase": pose_to_dict(item["pose"]),
                "orientationValid": item["orientationValid"],
                **({"error": _errors(item, reference)} if reference is not None else {}),
            } if item is not None else None)
            for name, item in estimates.items()
        }
        trials.append({"trial": trial.get("id", trial_index), "views": view_rows, "strategies": strategies})
    return {
        "schema": RESULT_SCHEMA,
        "source": str(session_path),
        "referenceAvailable": reference is not None,
        "trials": trials,
        "summary": summarise(trials, reference is not None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="Session-JSON mit drei Ansichten je Trial")
    parser.add_argument("--out", type=Path, required=True, help="Ziel fuer Ergebnis-JSON")
    args = parser.parse_args()
    result = evaluate(args.session)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
