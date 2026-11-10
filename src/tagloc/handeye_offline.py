"""Offline-Auswertung einer Rohbild-Sitzung ohne Kamera oder Roboter."""

from __future__ import annotations

import json
from html import escape
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import geometry, lens
from .boards import CHARUCO, CHESSBOARD, BoardSpec, canonical_chessboard_corners, chessboard_object_points, detect_board, matched_board_points
from .frames import to_gray
from .handeye import HandEye, intrinsics_from_dict, save_hand_eye, solve_hand_eye


#: Erlaubte Posegruppen der Rohaufnahme (F = Bildrand, 30-45 Grad gekippt, fuer die Offline-Intrinsik).
POSE_GROUPS = ("A", "B", "C", "D", "E", "F")


@dataclass(frozen=True)
class Observation:
    pose_id: str
    pose_group: str
    flange: np.ndarray
    board_in_camera: np.ndarray
    corners: np.ndarray
    pnp_rms_px: float
    pnp_ambiguity: float | None
    object_points: np.ndarray | None = None
    corner_ids: np.ndarray | None = None


def _pose_from_vector(values):
    return geometry.from_rotation_translation(
        geometry.rotation_from_rvec(values[:3]), values[3:6]
    )


def _vector_from_pose(pose):
    return np.r_[geometry.rvec_from_rotation(pose[:3, :3]), pose[:3, 3]]


def _rms(projected, observed):
    return float(np.sqrt(np.mean(np.sum((projected - observed) ** 2, axis=1))))


def _pnp(corners, points, calibration, method):
    import cv2

    corrected = lens.undistort_points(corners, calibration)
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    if method == "ippe":
        results = cv2.solvePnPGeneric(points, corrected, matrix, None, flags=cv2.SOLVEPNP_IPPE)
        if not results[0]:
            raise ValueError("IPPE fand keine Pose")
        candidates = list(zip(results[1], results[2]))
    elif method == "iterative":
        ok, rvec, tvec = cv2.solvePnP(points, corrected, matrix, None, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            raise ValueError("solvePnP fand keine Pose")
        candidates = [(rvec, tvec)]
    else:
        raise ValueError(f"Unbekanntes PnP-Verfahren: {method}")
    ranked = []
    for rvec, tvec in candidates:
        pose = geometry.from_rotation_translation(cv2.Rodrigues(rvec)[0], tvec)
        depths = (pose[:3, :3] @ points.T + pose[:3, 3:4])[2]
        if np.min(depths) <= 0:
            continue
        error = _rms(lens.project_points(points, rvec, tvec, calibration), corners)
        ranked.append((error, pose))
    if not ranked:
        raise ValueError("Keine PnP-Loesung mit positiver Tiefe")
    ranked.sort(key=lambda item: item[0])
    ambiguity = ranked[1][0] / max(ranked[0][0], 1e-9) if len(ranked) > 1 else None
    return ranked[0][1], ranked[0][0], ambiguity


def load_observations(folder: Path, *, pnp="iterative", max_pnp_rms_px=1.5):
    """Originalbilder erneut detektieren; Ausschlussgruende und Detektion cachen."""
    import cv2

    folder = Path(folder)
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    if config.get("schema") != "wsc.vision.handeye.raw/1":
        raise ValueError("Unbekanntes Rohbild-Schema")
    if config.get("imageStream") != "configured" or config.get("intrinsics") is None:
        raise ValueError("Bild und Intrinsik des konfigurierten Kamerastroms erforderlich")
    if config.get("intrinsicsSource") != "calibration":
        raise ValueError("Sitzung nutzt keine eigene, kalibrierte Kameraintrinsik")
    board_data = config["board"]
    if board_data.get("type") not in (CHESSBOARD, CHARUCO):
        raise ValueError("Unbekannter Bretttyp")
    board = BoardSpec(type=board_data["type"], cols=int(board_data["cols"]),
                      rows=int(board_data["rows"]), square_size_m=float(board_data["squareSizeM"]),
                      marker_size_m=float(board_data.get("markerSizeM", 0.022)),
                      dictionary=board_data.get("dictionary", "DICT_4X4_50"))
    calibration = intrinsics_from_dict(config["intrinsics"])
    points = chessboard_object_points(board) if board.type == CHESSBOARD else None
    observations, excluded, cache = [], [], []
    for path in sorted((folder / "poses").glob("pose_*")):
        try:
            state = json.loads((path / "robot_state.json").read_text(encoding="utf-8"))
            quality_path = path / "quality.json"
            if quality_path.exists():
                quality = json.loads(quality_path.read_text(encoding="utf-8"))
                if quality.get("valid") is False:
                    raise ValueError(f"Nach Aufnahme verworfen: {quality.get('reason', '')}")
            if state["poseId"] != path.name:
                raise ValueError("Pose-ID stimmt nicht mit dem Ordner ueberein")
            flange = np.asarray(state["T_base_flange"], dtype=np.float64).reshape(4, 4)
            if not np.all(np.isfinite(flange)) or not np.allclose(flange[3], [0, 0, 0, 1]):
                raise ValueError("Flanschpose ungueltig")
            if not np.allclose(flange[:3, :3].T @ flange[:3, :3], np.eye(3), atol=1e-4):
                raise ValueError("Flanschrotation nicht orthonormal")
            joints = np.asarray(state["jointAnglesRad"], dtype=np.float64)
            if joints.shape != (7,) or not np.all(np.isfinite(joints)):
                raise ValueError("Sieben gemessene Gelenkwinkel erforderlich")
            image = cv2.imread(str(path / "camera.png"), cv2.IMREAD_UNCHANGED)
            if image is None:
                raise ValueError("Rohbild fehlt")
            if (image.shape[1], image.shape[0]) != calibration.image_size:
                raise ValueError("Bildaufloesung passt nicht zur Intrinsik")
            gray = to_gray(image)
            sample = detect_board(gray, board)
            if sample is None:
                raise ValueError("Brett nicht erkannt")
            if board.type == CHARUCO:
                if sample.count() < 25:
                    raise ValueError("Weniger als 25 ChArUco-Ecken erkannt")
                object_points, corners = matched_board_points(sample, board)
                ids = sample.ids.reshape(-1)
            else:
                if sample.count() != len(points):
                    raise ValueError("Vollstaendiges Schachbrett nicht erkannt")
                corners = canonical_chessboard_corners(gray, sample.corners, board).reshape(-1, 2)
                object_points, ids = points, None
            pose, error, ambiguity = _pnp(corners, object_points, calibration, pnp)
            if pnp == "ippe" and ambiguity is not None and ambiguity < 1.2:
                raise ValueError(f"Planare PnP-Lage mehrdeutig (Fehlerverhaeltnis {ambiguity:.3f})")
            if error > max_pnp_rms_px:
                raise ValueError(f"PnP-Reprojektion {error:.3f} px > {max_pnp_rms_px:.3f} px")
            group = state.get("poseGroup")
            if group not in POSE_GROUPS:
                raise ValueError("Posegruppe fehlt")
            observations.append(Observation(path.name, group, flange, pose, corners, error, ambiguity,
                                            object_points, ids))
            cache.append({"poseId": path.name, "cornersPx": corners.tolist(),
                          "cornerIds": ids.tolist() if ids is not None else None,
                          "objectPointsM": object_points.tolist(),
                          "poseGroup": group,
                          "T_camera_board": pose.tolist(), "pnpRmsPx": error,
                          "pnpAmbiguity": ambiguity})
        except (ValueError, KeyError, OSError) as error:
            excluded.append({"poseId": path.name, "reason": str(error)})
    results = folder / "results"
    results.mkdir(exist_ok=True)
    (results / f"detections_{pnp}.json").write_text(
        json.dumps({"pnp": pnp, "valid": cache, "excluded": excluded}, indent=2) + "\n",
        encoding="utf-8")
    return observations, excluded, calibration, points


def _base_board(flanges, boards, handeye):
    return geometry.average_poses([
        geometry.compose(f, handeye, b) for f, b in zip(flanges, boards)
    ])


def _solution_fields(handeye, flanges, boards):
    base_board = _base_board(flanges, boards, handeye)
    reconstructed = [geometry.compose(f, handeye, b) for f, b in zip(flanges, boards)]
    return {
        "T_flange_camera": handeye.tolist(),
        "translationMm": (handeye[:3, 3] * 1000).tolist(),
        "rotationDeg": float(np.degrees(np.linalg.norm(geometry.rvec_from_rotation(handeye[:3, :3])))),
        "boardSpreadRmsMm": float(np.sqrt(np.mean([
            (1000 * geometry.translation_distance_m(pose, base_board)) ** 2
            for pose in reconstructed]))),
        "boardSpreadRmsDeg": float(np.sqrt(np.mean([
            np.degrees(geometry.rotation_distance_rad(pose, base_board)) ** 2
            for pose in reconstructed]))),
    }


def _residuals(values, observations, calibration, points):
    handeye, base_board = _pose_from_vector(values[:6]), _pose_from_vector(values[6:])
    chunks = []
    for obs in observations:
        camera_board = geometry.invert(geometry.compose(obs.flange, handeye)) @ base_board
        projected = lens.project_points(obs.object_points if obs.object_points is not None else points, geometry.rvec_from_rotation(camera_board[:3, :3]),
                                        camera_board[:3, 3], calibration)
        chunks.append((projected - obs.corners).reshape(-1))
    return np.concatenate(chunks)


def solve_subset(observations, calibration, points, *, optimize=True):
    """Drei klassische Verfahren und eine robuste gemeinsame Reprojektion."""
    import cv2

    if len(observations) < 3:
        raise ValueError("Mindestens drei gueltige Posen erforderlich")
    flanges = [obs.flange for obs in observations]
    boards = [obs.board_in_camera for obs in observations]
    eigenvalues = np.linalg.eigvalsh(_information(flanges))
    if eigenvalues[0] < 1e-6:
        return {"error": "Rotationsvielfalt zu gering; Translation nicht eindeutig",
                "translationInformationEigenvalues": eigenvalues.tolist()}
    solutions = {}
    methods = {"park": "CALIB_HAND_EYE_PARK", "tsai": "CALIB_HAND_EYE_TSAI",
               "daniilidis": "CALIB_HAND_EYE_DANIILIDIS"}
    if hasattr(cv2, "calibrateHandEye"):
        for name, flag in methods.items():
            try:
                rotation, translation = cv2.calibrateHandEye(
                    [f[:3, :3] for f in flanges], [f[:3, 3] for f in flanges],
                    [b[:3, :3] for b in boards], [b[:3, 3] for b in boards],
                    method=getattr(cv2, flag))
                handeye = geometry.from_rotation_translation(rotation, translation)
                if not np.all(np.isfinite(handeye)):
                    raise ValueError("Nichtendliche Transformation")
                if not np.allclose(handeye[:3, :3].T @ handeye[:3, :3], np.eye(3), atol=1e-3) or \
                   np.linalg.det(handeye[:3, :3]) < 0.99:
                    raise ValueError("Keine gueltige Rotation")
                base_board = _base_board(flanges, boards, handeye)
                values = np.r_[_vector_from_pose(handeye), _vector_from_pose(base_board)]
                residual = _residuals(values, observations, calibration, points)
                solutions[name] = {**_solution_fields(handeye, flanges, boards),
                                   "reprojectionRmsPx": float(np.sqrt(np.mean(residual**2) * 2))}
            except (cv2.error, ValueError, np.linalg.LinAlgError) as error:
                solutions[name] = {"error": str(error)}
    else:
        for name in methods:
            solutions[name] = {"error": "cv2.calibrateHandEye fehlt; OpenCV 4.x installieren"}
    if "T_flange_camera" not in solutions["park"]:
        try:
            handeye, _, _ = solve_hand_eye(flanges, boards)
            base_board = _base_board(flanges, boards, handeye)
            values = np.r_[_vector_from_pose(handeye), _vector_from_pose(base_board)]
            residual = _residuals(values, observations, calibration, points)
            solutions["park_numpy"] = {**_solution_fields(handeye, flanges, boards),
                                       "reprojectionRmsPx": float(np.sqrt(np.mean(residual**2) * 2))}
        except ValueError as error:
            solutions["park_numpy"] = {"error": str(error)}
    candidates = [(item["reprojectionRmsPx"], name, item) for name, item in solutions.items()
                  if "T_flange_camera" in item]
    if not candidates:
        return {"methods": solutions, "error": "Keine stabile Initialloesung"}
    _, name, best = min(candidates)
    handeye = np.asarray(best["T_flange_camera"])
    base_board = _base_board(flanges, boards, handeye)
    result = {"methods": solutions, "initialMethod": name,
              "translationInformationEigenvalues": eigenvalues.tolist(),
              "translationInformationCondition": float(eigenvalues[-1] / max(eigenvalues[0], 1e-12))}
    if optimize:
        from scipy.optimize import least_squares

        initial = np.r_[_vector_from_pose(handeye), _vector_from_pose(base_board)]
        fitted = least_squares(_residuals, initial, args=(observations, calibration, points),
                               loss="soft_l1", f_scale=1.0, max_nfev=200)
        residual = _residuals(fitted.x, observations, calibration, points)
        result["optimized"] = {
            **_solution_fields(_pose_from_vector(fitted.x[:6]), flanges, boards),
            "T_base_board": _pose_from_vector(fitted.x[6:]).tolist(),
            "reprojectionRmsPx": float(np.sqrt(np.mean(residual**2) * 2)),
            "converged": bool(fitted.success), "cost": float(fitted.cost),
        }
    return result


def _information(flanges):
    matrix = np.zeros((3, 3))
    for i, a in enumerate(flanges):
        for b in flanges[i + 1:]:
            rotation = a[:3, :3].T @ b[:3, :3] - np.eye(3)
            matrix += rotation.T @ rotation
    return matrix


def diverse_subsets(observations, count, repeats=3):
    """Greedy D-Optimalitaet mit verschiedenen deterministischen Starts."""
    if len(observations) < count:
        return []
    result = []
    for seed in range(min(repeats, len(observations))):
        chosen = [seed * len(observations) // repeats]
        while len(chosen) < count:
            best = None
            for index, observation in enumerate(observations):
                if index in chosen:
                    continue
                flanges = [observations[i].flange for i in chosen] + [observation.flange]
                info = _information(flanges)
                score = np.linalg.slogdet(info + 1e-6 * np.eye(3))[1]
                candidate = (score, -index, index)
                if best is None or candidate > best:
                    best = candidate
            chosen.append(best[2])
        ids = tuple(sorted(chosen))
        if ids not in result:
            result.append(ids)
    return result


def run_experiments(folder, *, pnp="iterative", max_pnp_rms_px=1.5, repeats=3):
    observations, excluded, calibration, points = load_observations(
        folder, pnp=pnp, max_pnp_rms_px=max_pnp_rms_px)
    if len(observations) < 3:
        raise ValueError(f"Nur {len(observations)} gueltige Aufnahmen")
    runs = []
    for count in (15, 25, 40, 60):
        for indices in diverse_subsets(observations, count, repeats):
            subset = [observations[i] for i in indices]
            runs.append({"poseIds": [obs.pose_id for obs in subset], "poseCount": count,
                         "result": solve_subset(subset, calibration, points)})
    for label, subset in [(group, [obs for obs in observations if obs.pose_group == group])
                          for group in POSE_GROUPS]:
        # Gruppen ohne (genug) Posen, etwa F in alten Sitzungen, werden uebersprungen.
        if len(subset) >= 3:
            runs.append({"selection": f"group_{label}", "poseIds": [obs.pose_id for obs in subset],
                         "poseCount": len(subset), "result": solve_subset(subset, calibration, points)})
    for label in ("ABC", "BCD", "ABCE"):
        subset = [obs for obs in observations if obs.pose_group in label]
        if len(subset) >= 3:
            runs.append({"selection": f"groups_{label}", "poseIds": [obs.pose_id for obs in subset],
                         "poseCount": len(subset), "result": solve_subset(subset, calibration, points)})
    quality = [obs for obs in observations if obs.pnp_rms_px <= max_pnp_rms_px / 2]
    if len(quality) >= 3:
        runs.append({"selection": "quality_filtered", "poseIds": [obs.pose_id for obs in quality],
                     "poseCount": len(quality), "result": solve_subset(quality, calibration, points)})
    all_result = solve_subset(observations, calibration, points)
    runs.append({"selection": "all_valid", "poseIds": [obs.pose_id for obs in observations],
                 "poseCount": len(observations), "result": all_result})
    report = {"schema": "wsc.vision.handeye.experiments/1", "pnp": pnp,
              "maxPnpRmsPx": max_pnp_rms_px, "excluded": excluded, "runs": runs,
              "accuracyTargetMm": 0.5, "accuracyValidated": False}
    path = Path(folder) / "results" / f"experiments_{pnp}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    candidate = all_result.get("optimized")
    if candidate and candidate["converged"]:
        session_config = json.loads((Path(folder) / "config.json").read_text(encoding="utf-8"))
        save_hand_eye(Path(folder) / "results" / f"candidate_handeye_{pnp}.json", HandEye(
            pose_flange_cam=np.asarray(candidate["T_flange_camera"], dtype=np.float64),
            frame_id=session_config.get("frameId", "cam_flange"),
            rms_position_m=candidate["boardSpreadRmsMm"] / 1000,
            rms_rotation_deg=candidate["boardSpreadRmsDeg"],
            refined_reprojection_rms_px=candidate["reprojectionRmsPx"],
            sample_count=len(observations), intrinsics_source="calibration"))
    (Path(folder) / "results" / f"report_{pnp}.html").write_text(
        _html_report(report), encoding="utf-8")
    return report


def _html_report(report):
    """Kompakter, ohne Abhaengigkeiten teilbarer HTML-Vergleich."""
    rows, circles = [], []
    for index, run in enumerate(report["runs"]):
        solution = run["result"].get("optimized") or {}
        rms = solution.get("reprojectionRmsPx")
        if rms is not None:
            x = 55 + min(60, run["poseCount"]) * 9
            y = 250 - min(5, rms) * 42
            circles.append(f'<circle cx="{x}" cy="{y:.1f}" r="4" fill="#1679a7">'
                           f'<title>{escape(str(run.get("selection", "Teilmenge")))}: '
                           f'{rms:.3f} px</title></circle>')
        translation = solution.get("translationMm")
        rows.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in (
            run.get("selection", f"Teilmenge {index + 1}"), run["poseCount"],
            ", ".join(f"{v:.3f}" for v in translation) if translation else "–",
            f"{solution['rotationDeg']:.3f}" if solution else "–",
            f"{rms:.3f}" if rms is not None else "–",
            ", ".join(run["poseIds"]),
        )) + "</tr>")
    exclusions = "".join(f"<li>{escape(item['poseId'])}: {escape(item['reason'])}</li>"
                         for item in report["excluded"])
    return ("<!doctype html><html lang=\"de\"><meta charset=\"utf-8\">"
            "<title>Hand-Auge-Kalibrierung</title><style>body{font:14px system-ui;max-width:1100px;"
            "margin:2em auto;color:#173043}table{border-collapse:collapse;width:100%}"
            "td,th{border-bottom:1px solid #cad7df;padding:.5em;text-align:left}"
            "td:last-child{max-width:380px;overflow-wrap:anywhere}</style>"
            "<h1>Hand-Auge-Kalibrierung</h1><p>Offline-Vergleich; Reprojektionsfehler belegt keine "
            "absolute Genauigkeit von 0,5 mm.</p>"
            f"<p>PnP: {escape(report['pnp'])}; Ausschluesse: {len(report['excluded'])}</p>"
            "<svg width=\"700\" height=\"285\" viewBox=\"0 0 700 285\" "
            "aria-label=\"Reprojektionsfehler nach Posezahl\"><path d=\"M55 40V250H660\" "
            "stroke=\"#526a77\" fill=\"none\"/><text x=\"300\" y=\"280\">Posen</text>"
            "<text x=\"5\" y=\"30\">RMS px</text>" + "".join(circles) + "</svg>"
            "<table><thead><tr><th>Auswahl</th><th>Posen</th><th>Translation mm</th>"
            "<th>Rotation Grad</th><th>RMS px</th><th>Pose-IDs</th></tr></thead><tbody>" +
            "".join(rows) + "</tbody></table><h2>Ausgeschlossene Aufnahmen</h2><ul>" +
            exclusions + "</ul></html>\n")
