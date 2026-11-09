"""Auswertung einer Wiederholgenauigkeits-Session (Ordner mit den vier Phasenordnern)."""

import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from .apriltag import (
    apriltag_spec_from_dict,
    build_apriltag_detector,
    detect_observations,
    measure_observation,
    pose_from_client,
    select_observation,
    target_type,
)
from .charuco import _detect, build_board, spec_from_dict
from .metrics import (
    corner_noise,
    homography_metrics,
    mean_corner_displacement,
    noisy_ids,
    repeatability,
)

#: Schema der Bild-JSONs (siehe `vision_server/repeatability_image.py`).
SCHEMA = "adp.repeatability.image/1"
#: Phase -> Ordnername (wie `vision_server.repeatability_image.PHASES`).
PHASE_FOLDERS = {
    "warmlauf": "01_warmlauf",
    "rauschen": "02_rauschen",
    "zyklen_auto": "03_zyklen_auto",
    "zyklen_hand": "04_zyklen_hand",
}
#: Anteil der Basis-Ecken, der in einem Bild vorhanden sein muss.
MIN_BASE_FRACTION = 0.9
#: Grenzwert des Rauschverhaeltnisses fuer `valid`.
MAX_NOISE_RATIO = 0.2
MIN_CORNERS = 6


def _clean(value: Any) -> Any:
    """Macht Werte JSON-tauglich (NaN/inf -> None, numpy -> Python, Mengen -> Listen)."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value) if isinstance(value, (set, frozenset)) else value
        return [_clean(v) for v in items]
    if isinstance(value, np.ndarray):
        return _clean(value.tolist())
    if isinstance(value, np.generic):
        return _clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _load_records(folder: Path) -> tuple[list[dict], list[str]]:
    """Liest alle Bild-JSONs; `(Aufnahmen sortiert nach Phase/Index, Warnungen)`."""
    records: list[dict] = []
    warnings: list[str] = []
    for phase, name in PHASE_FOLDERS.items():
        directory = folder / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                warnings.append(f"{name}/{path.name}: nicht lesbar")
                continue
            if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
                warnings.append(f"{name}/{path.name}: unbekanntes Schema")
                continue
            client = doc.get("client") if isinstance(doc.get("client"), dict) else {}
            records.append({
                "phase": phase,
                "index": int(doc.get("index", 0)),
                "doc": doc,
                "client": client,
                "png": directory / str(doc.get("bild", path.with_suffix(".png").name)),
                "name": f"{name}/{path.name}",
            })
    order = list(PHASE_FOLDERS)
    records.sort(key=lambda r: (order.index(r["phase"]), r["index"]))
    return records, warnings


def _stats_mm(centers: np.ndarray) -> dict[str, Any]:
    rep = repeatability(centers)
    return {
        "n": rep["n"],
        "centroidMm": rep["centroid"],
        "lMeanMm": rep["lMean"],
        "sLMm": rep["sL"],
        "rpMm": rep["rp"],
        "rpUm": rep["rp"] * 1000.0,
        "lMeanUm": rep["lMean"] * 1000.0,
        "sLUm": rep["sL"] * 1000.0,
        "stdPerAxisUm": [v * 1000.0 for v in rep["stdPerAxis"]],
    }


def _position_stats(
    points: list, quats: list | None = None, reproj: list | None = None
) -> dict[str, Any]:
    """RP einer 3D-Position (Punkte in mm, je Gruppe oder None) in um; `rpUm` None bei < 2 Punkten.

    Mit `quats` zusaetzlich `rotationStdArcsec` (Streuung des Winkels zur mittleren
    Orientierung), mit `reproj` `reprojectionMeanPx`.
    """
    valid = [i for i, p in enumerate(points) if p is not None]
    out: dict[str, Any] = {"n": len(valid)}
    if len(valid) < 2:
        out.update({"rpUm": None, "grund": "weniger als 2 Aufnahmen mit Wert"})
        return out
    rep = repeatability(np.array([points[i] for i in valid]))
    out.update({
        "rpMm": rep["rp"],
        "rpUm": rep["rp"] * 1000.0,
        "lMeanUm": rep["lMean"] * 1000.0,
        "sLUm": rep["sL"] * 1000.0,
        "stdPerAxisUm": [v * 1000.0 for v in rep["stdPerAxis"]],
    })
    if quats is not None:
        from tagloc.geometry import average_poses, from_position_quaternion, rotation_distance_rad

        poses = [from_position_quaternion([0.0, 0.0, 0.0], quats[i]) for i in valid if quats[i] is not None]
        if len(poses) >= 2:
            mean = average_poses(poses)
            angles = np.array([rotation_distance_rad(mean, p) for p in poses])
            out["rotationStdArcsec"] = float(math.degrees(math.sqrt(float((angles**2).sum()) / (len(poses) - 1))) * 3600.0)
    if reproj is not None:
        values = [r for r in reproj if r is not None]
        if values:
            out["reprojectionMeanPx"] = float(np.mean(values))
    return out


def _series_stats(groups: list[dict]) -> dict[str, Any]:
    """Kennzahlen einer Serie aus Gruppen (`center`, `rotation`, `scale`, `robot`)."""
    out: dict[str, Any] = {"nAufnahmen": len(groups)}
    if len(groups) < 2:
        out["grund"] = "weniger als 2 gueltige Aufnahmen"
        out["rpUm"] = None
        return out
    out.update(_stats_mm(np.array([g["center"] for g in groups])))
    rot = np.array([g["rotation"] for g in groups])
    rot = (rot - rot[0] + 180.0) % 360.0 - 180.0  # Winkelsprung bei +-180 vermeiden
    out["rotationStdArcsec"] = float(rot.std(ddof=1) * 3600.0)
    scale = np.array([g["scale"] for g in groups])
    out["scaleRelStdPpm"] = float(scale.std(ddof=1) / scale.mean() * 1e6)
    for key, field in (("kamera", "cam"), ("basis", "base")):
        if any(field in g for g in groups):
            out[key] = _position_stats([g.get(field) for g in groups], [g.get("cam_quat") for g in groups]
                                       if key == "kamera" else None,
                                       [g.get("reproj") for g in groups] if key == "kamera" else None)
    robot = [g["robot"] for g in groups if g.get("robot") is not None]
    if len(robot) >= 2:
        rep = repeatability(np.array(robot) * 1000.0)
        out["robotReported"] = {
            "n": rep["n"], "rpMm": rep["rp"], "lMeanMm": rep["lMean"], "sLMm": rep["sL"],
            "stdPerAxisMm": rep["stdPerAxis"],
        }
    return out


def _robot_position(client: dict) -> list[float] | None:
    try:
        pos = client["actual"]["flangeInBase"]["position"]
        vec = [float(pos[0]), float(pos[1]), float(pos[2])]
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return vec if all(math.isfinite(v) for v in vec) else None


def evaluate_session(
    folder: Path, *, exclude_ids: set[int] | None = None, write: bool = True
) -> dict:
    """Wertet eine Session aus; Ergebnis als dict (bei `write` zusaetzlich unter `auswertung/`).

    Wirft `ValueError`, wenn keine Aufnahmen oder keine Board-Angabe vorliegen.
    """
    import cv2

    folder = Path(folder)
    exclude = {int(i) for i in (exclude_ids or set())}
    records, warnings = _load_records(folder)
    if not records:
        raise ValueError(f"Keine Aufnahmen in {folder}")
    tag_target = next(
        (r["client"]["target"] for r in records if target_type(r["client"]) == "apriltag"), None
    )
    spec = board = tag_spec = None
    if tag_target is not None:
        tag_spec = apriltag_spec_from_dict(tag_target)
    else:
        board_dict = None
        for rec in records:
            target = rec["client"].get("target")
            if isinstance(rec["client"].get("board"), dict):
                board_dict = rec["client"]["board"]
                break
            if isinstance(target, dict) and target.get("type") == "charuco":
                board_dict = target
                break
        if board_dict is None:
            raise ValueError("Keine Board-Angabe (client.board) in den Aufnahmen")
        spec = spec_from_dict(board_dict)
        board = build_board(spec)

    # 2. Erkennung
    detector = calibration = hand_eye_default = None
    if tag_spec is not None:
        detector, calibration, hand_eye_default = _apriltag_setup(records, warnings)
    for rec in records:
        rec["ids"] = np.empty(0, dtype=int)
        rec["corners"] = np.empty((0, 2))
        rec["size"] = None
        rec["read_error"] = None
        image = cv2.imread(str(rec["png"]), cv2.IMREAD_UNCHANGED)
        if image is None:
            rec["read_error"] = "Bild nicht lesbar"
            continue
        rec["size"] = (image.shape[1], image.shape[0])
        if tag_spec is not None:
            rec["observations"] = detect_observations(image, detector)
        else:
            rec["ids"], rec["corners"], _ = _detect(image, spec, board)

    tag_id = None
    if tag_spec is not None:
        tag_id = _apriltag_metrics(records, tag_spec, calibration, hand_eye_default)
    readable = [r for r in records if r["read_error"] is None]
    noise_recs = [r for r in readable if r["phase"] == "rauschen"]

    # 3. Basis-ID-Menge
    noise: dict[int, float] = {}
    noisy: set[int] = set()
    if tag_spec is not None:
        base_rule = f"AprilTag: 4 Ecken des Tags {tag_id}"
        base = set()
        noise = corner_noise(
            [(r["ids"], r["corners"]) for r in noise_recs if r["used"]]
        )
    elif noise_recs:
        # Rauschbilder ganz ohne Erkennung zaehlen nicht mit: eines davon
        # wuerde sonst die ganze Basismenge leeren.
        detected = [set(map(int, r["ids"])) for r in noise_recs if len(r["ids"]) > 0]
        base = set.intersection(*detected) if detected else set()
        noise = corner_noise([(r["ids"], r["corners"]) for r in noise_recs])
        noisy = noisy_ids(noise)
        base_rule = "alle Rauschbilder minus streuende Ecken"
        base = base - noisy
    else:
        counts: dict[int, int] = {}
        for r in readable:
            for i in set(map(int, r["ids"])):
                counts[i] = counts.get(i, 0) + 1
        base = {i for i, c in counts.items() if c >= MIN_BASE_FRACTION * max(len(readable), 1)}
        base_rule = "Ecken in mindestens 90 % aller Bilder"
    base -= exclude

    # Bildauswahl und Metriken
    for rec in [] if tag_spec is not None else records:
        rec["used"] = False
        rec["metrics"] = None
        rec["displacement"] = float("nan")
        rec["grund"] = rec["read_error"] or ""
        if rec["read_error"]:
            continue
        mask = np.array([int(i) in base for i in rec["ids"]], dtype=bool)
        rec["common_ids"] = rec["ids"][mask]
        rec["common_corners"] = rec["corners"][mask]
        n_common = int(mask.sum())
        if not base:
            rec["grund"] = "Basis-Eckenmenge leer"
        elif n_common < MIN_BASE_FRACTION * len(base):
            rec["grund"] = f"nur {n_common} von {len(base)} Basis-Ecken erkannt"
        elif n_common < MIN_CORNERS:
            rec["grund"] = f"zu wenige Ecken ({n_common})"
        else:
            try:
                rec["metrics"] = homography_metrics(
                    rec["common_ids"], rec["common_corners"], spec, rec["size"]
                )
                rec["used"] = True
            except ValueError as error:
                rec["grund"] = str(error)

    # 4. Referenz
    used_noise = [r for r in noise_recs if r["used"]]
    ref_source = used_noise or [r for r in records if r["used"]][:1]
    acc: dict[int, list] = {}
    for r in ref_source:
        for i, p in zip(r["common_ids"], r["common_corners"]):
            acc.setdefault(int(i), []).append(p)
    ref = {i: tuple(np.mean(v, axis=0)) for i, v in acc.items()}
    for rec in records:
        if rec["used"]:
            rec["displacement"] = mean_corner_displacement(
                rec["common_ids"], rec["common_corners"], ref
            )

    # 5./6. Gruppen und Serien
    def group_of(recs: list[dict], by_cycle: bool) -> list[dict]:
        # Ein Zyklus = aufeinanderfolgende Aufnahmen (nach Index) mit gleichem
        # `cycle`; `burstIndex` 1 beginnt immer einen neuen. Nicht nur nach
        # `cycle` gruppieren: ein zweiter Lauf in derselben Session zaehlt wieder
        # ab 1 und darf nicht mit dem ersten gemittelt werden.
        runs: list[list[dict]] = []
        previous_cycle: Any = object()
        for r in sorted(recs, key=lambda x: x["index"]):
            cycle = r["client"].get("cycle") if by_cycle else None
            burst_index = r["client"].get("burstIndex")
            if cycle is None or cycle != previous_cycle or burst_index in (None, 1) or not runs:
                runs.append([])
            runs[-1].append(r)
            previous_cycle = cycle
        result = []
        for members in ([r for r in run if r["used"]] for run in runs):
            if not members:
                continue
            m = [x["metrics"] for x in members]
            rot = np.array([x["rotationDeg"] for x in m])
            rot = (rot - rot[0] + 180.0) % 360.0 - 180.0 + rot[0]
            robots = [p for p in (_robot_position(x["client"]) for x in members) if p]
            extra: dict[str, Any] = {}
            if tag_spec is not None:
                extra = _group_extras(m)
            result.append({
                **extra,
                "center": np.mean([x["centerOnBoardMm"] for x in m], axis=0),
                "rotation": float(rot.mean()),
                "scale": float(np.mean([x["scalePxPerMm"] for x in m])),
                "robot": np.mean(robots, axis=0) if robots else None,
                "cycle": members[0]["client"].get("cycle"),
                "nBilder": len(members),
            })
        return result

    series: dict[str, Any] = {}
    series_groups: dict[str, list[dict]] = {}
    for phase in ("rauschen", "zyklen_auto", "zyklen_hand"):
        recs = [r for r in records if r["phase"] == phase]
        if not recs:
            continue
        groups = group_of(recs, by_cycle=phase != "rauschen")
        series_groups[phase] = groups
        series[phase] = _series_stats(groups)

    # 7. Gueltigkeit
    rp_noise = series.get("rauschen", {}).get("rpMm")
    for phase in ("zyklen_auto", "zyklen_hand"):
        s = series.get(phase)
        if s is None:
            continue
        rp = s.get("rpMm")
        if rp_noise is not None and rp:
            s["noiseRatio"] = rp_noise / rp
            s["valid"] = bool(s["noiseRatio"] <= MAX_NOISE_RATIO)
        else:
            s["noiseRatio"] = None
            s["valid"] = False
        for key in ("kamera", "basis"):
            sub, sub_noise = s.get(key), series.get("rauschen", {}).get(key)
            if sub is None:
                continue
            noise_rp = (sub_noise or {}).get("rpMm")
            if noise_rp is not None and sub.get("rpMm"):
                sub["noiseRatio"] = noise_rp / sub["rpMm"]
                sub["valid"] = bool(sub["noiseRatio"] <= MAX_NOISE_RATIO)
            else:
                sub["noiseRatio"] = None
                sub["valid"] = False

    # 8. Warmlauf
    warm = [r for r in records if r["phase"] == "warmlauf" and r["used"]]
    warm_all = [r for r in records if r["phase"] == "warmlauf"]
    t0 = None
    if warm_all:
        try:
            t0 = datetime.fromisoformat(warm_all[0]["doc"]["gespeichert"])
        except (KeyError, ValueError, TypeError):
            t0 = None

    def elapsed(r: dict) -> float | None:
        v = r["client"].get("warmupElapsedS")
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return (datetime.fromisoformat(r["doc"]["gespeichert"]) - t0).total_seconds()
        except (KeyError, ValueError, TypeError):
            return None

    noise_mean = (
        np.mean([r["metrics"]["centerOnBoardMm"] for r in used_noise], axis=0) if used_noise else None
    )
    last = np.array(warm[-1]["metrics"]["centerOnBoardMm"]) if warm else None
    warmup = []
    for r in warm:
        c = np.array(r["metrics"]["centerOnBoardMm"])
        warmup.append({
            "index": r["index"],
            "tS": elapsed(r),
            "centerMm": c.tolist(),
            "dToLastUm": float(np.linalg.norm(c - last) * 1000.0),
            "dToNoiseMeanUm": float(np.linalg.norm(c - noise_mean) * 1000.0)
            if noise_mean is not None else None,
        })

    excluded_images = [
        {"phase": r["phase"], "index": r["index"], "datei": r["name"], "grund": r["grund"]}
        for r in records if not r["used"]
    ]
    result: dict[str, Any] = {
        "schema": "adp.repeatability.result/1",
        "ordner": str(folder),
        "board": spec.as_dict() if spec is not None else None,
        "target": spec.as_dict() if spec is not None else {**tag_spec.as_dict(), "tagId": tag_id},
        "basisIds": sorted(base),
        "basisRegel": base_rule,
        "ausgeschlosseneIds": sorted(exclude),
        "streuendeIds": sorted(noisy),
        "ausgeschlosseneBilder": excluded_images,
        "warnungen": warnings,
        "referenzEcken": len(ref),
        "rauschstreuungJeEckePx": noise,
        "serien": series,
        "warmlauf": warmup,
        "nBilder": len(records),
        "nVerwendet": sum(1 for r in records if r["used"]),
    }
    if write:
        out = folder / "auswertung"
        out.mkdir(parents=True, exist_ok=True)
        result["plots"] = _write_plots(out, series_groups, warmup)
        (out / "ergebnis.json").write_text(
            json.dumps(_clean(result), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        _write_csv(out / "messwerte.csv", records)
    return _clean(result)


def _mm_or_blank(vec: Any) -> list:
    """Vektor in m als drei mm-Werte fuer die CSV; leer, wenn nicht vorhanden."""
    return ["", "", ""] if vec is None else [v * 1000.0 for v in vec]


def _apriltag_setup(records: list[dict], warnings: list[str]):
    """`(Detektor, Intrinsik | None, Hand-Auge-Pose T_flange_cam | None)` aus `pi.*` der Aufnahmen.

    Der Detektor stammt aus `pi.apriltag` der ersten Aufnahme, die es hat (`ValueError`, wenn
    keine). Fehlende Intrinsik/Hand-Auge: Warnung, die Kamera-/Basis-Metriken entfallen.
    """
    from use_case_eval.live import calibration_from_meta

    def first(getter):
        for r in records:
            try:
                value = getter(r["doc"].get("pi") or {})
            except (AttributeError, KeyError, TypeError):
                value = None
            if value:
                return value
        return None

    config = first(lambda pi: pi["apriltag"])
    if not isinstance(config, dict):
        raise ValueError("Keine Detektor-Einstellungen (pi.apriltag) in den Aufnahmen")
    detector = build_apriltag_detector(config)
    calibration = None
    meta = first(lambda pi: pi["intrinsics"])
    if meta:
        try:
            calibration = calibration_from_meta(meta)
        except (KeyError, TypeError, ValueError):
            calibration = None
    if calibration is None:
        warnings.append("Keine Intrinsik (pi.intrinsics): Kamera- und Basis-Metriken entfallen")
    hand_eye = pose_from_client(first(lambda pi: pi["hand_eye"]["content"]["poseFlangeCam"]))
    if hand_eye is None:
        warnings.append("Keine Hand-Auge-Pose (pi.hand_eye): Basis-Metrik entfaellt")
    return detector, calibration, hand_eye


def _apriltag_metrics(records: list[dict], spec, calibration, hand_eye_default) -> int | None:
    """Setzt je Aufnahme `used`/`metrics`/`grund`/`ids`/`corners`/`common_*`; liefert die Tag-ID.

    Ohne `spec.tag_id`: je Bild muss genau ein Tag erkannt sein; ausgewertet wird die
    haeufigste ID, Bilder mit einer anderen sind ausgeschlossen.
    """
    from collections import Counter

    picks: dict[int, tuple[Any, str]] = {}
    for n, rec in enumerate(records):
        if rec["read_error"] is None:
            picks[n] = select_observation(rec["observations"], spec.tag_id)
    tag_id = spec.tag_id
    if tag_id is None:
        counts = Counter(o.tag_id for o, _ in picks.values() if o is not None)
        tag_id = counts.most_common(1)[0][0] if counts else None
    for n, rec in enumerate(records):
        rec["used"] = False
        rec["metrics"] = None
        rec["displacement"] = float("nan")
        rec["grund"] = rec["read_error"] or ""
        rec["common_ids"] = rec["ids"]
        rec["common_corners"] = rec["corners"]
        if rec["read_error"]:
            continue
        observation, grund = picks[n]
        if observation is None:
            rec["grund"] = grund
            continue
        if observation.tag_id != tag_id:
            rec["grund"] = f"anderer Tag ({observation.tag_id}) als {tag_id}"
            continue
        client = rec["client"]
        actual = client.get("actual") if isinstance(client.get("actual"), dict) else {}
        try:
            hand_eye = pose_from_client(rec["doc"]["pi"]["hand_eye"]["content"]["poseFlangeCam"])
        except (KeyError, TypeError):
            hand_eye = None
        try:
            rec["metrics"] = measure_observation(
                observation, type(spec)(tag_id, spec.tag_size_m), rec["size"], calibration,
                hand_eye if hand_eye is not None else hand_eye_default,
                pose_from_client(actual.get("flangeInBase")),
            )
        except ValueError as error:
            rec["grund"] = str(error)
            continue
        rec["used"] = True
        rec["ids"] = np.arange(4)
        rec["corners"] = np.asarray(observation.corners, dtype=np.float64).reshape(4, 2)
        rec["common_ids"], rec["common_corners"] = rec["ids"], rec["corners"]
    return tag_id


def _group_extras(metrics: list[dict]) -> dict[str, Any]:
    """Mittelwerte der Kamera-/Basis-Positionen (mm) einer Gruppe; None, wenn ein Bild sie nicht hat."""
    out: dict[str, Any] = {}
    for key, field in (("cam", "tagInCamM"), ("base", "tagInBaseM")):
        vecs = [m.get(field) for m in metrics]
        out[key] = (
            np.mean(vecs, axis=0) * 1000.0 if all(v is not None for v in vecs) else None
        )
    quats = [m.get("tagInCamQuat") for m in metrics]
    out["cam_quat"] = None
    if all(q is not None for q in quats):
        from tagloc.geometry import average_poses, from_position_quaternion, to_position_quaternion

        mean = average_poses([from_position_quaternion([0.0, 0.0, 0.0], q) for q in quats])
        out["cam_quat"] = list(to_position_quaternion(mean)[1])
    errors = [m["reprojectionErrorPx"] for m in metrics if m.get("reprojectionErrorPx") is not None]
    out["reproj"] = float(np.mean(errors)) if errors else None
    return out


def _write_csv(path: Path, records: list[dict]) -> None:
    header = [
        "phase", "index", "cycle", "burst", "nCorners", "centerX_mm", "centerY_mm",
        "rotationDeg", "scalePxPerMm", "homographyRmsPx", "meanDisplacementPx", "used", "grund",
        "tagInCamX_mm", "tagInCamY_mm", "tagInCamZ_mm",
        "tagInBaseX_mm", "tagInBaseY_mm", "tagInBaseZ_mm", "reprojectionErrorPx",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for r in records:
            m = r["metrics"] or {}
            center = m.get("centerOnBoardMm", ["", ""])
            writer.writerow([
                r["phase"], r["index"], r["client"].get("cycle", ""), r["client"].get("burstIndex", ""),
                m.get("nCorners", len(r["ids"])), center[0], center[1],
                m.get("rotationDeg", ""), m.get("scalePxPerMm", ""), m.get("homographyRmsPx", ""),
                "" if math.isnan(r["displacement"]) else r["displacement"],
                int(r["used"]), r["grund"],
                *_mm_or_blank(m.get("tagInCamM")), *_mm_or_blank(m.get("tagInBaseM")),
                "" if m.get("reprojectionErrorPx") is None else m["reprojectionErrorPx"],
            ])


def _write_plots(out: Path, series_groups: dict[str, list[dict]], warmup: list[dict]) -> str:
    """Schreibt Diagramme, falls matplotlib da ist; sonst Hinweistext."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return "matplotlib fehlt"
    written = []
    for phase, groups in series_groups.items():
        if len(groups) < 2:
            continue
        pts = np.array([g["center"] for g in groups])
        rep = repeatability(pts)
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(pts[:, 0], pts[:, 1], s=12)
        c = np.array(rep["centroid"])
        ax.add_patch(plt.Circle(c, rep["rp"], fill=False, color="red", label=f"RP = {rep['rp'] * 1000:.1f} um"))
        ax.plot(*c, "k+")
        ax.set_aspect("equal")
        ax.set_xlabel("x [mm]")
        ax.set_ylabel("y [mm]")
        ax.set_title(phase)
        ax.legend()
        fig.savefig(out / f"streu_{phase}.png", dpi=120)
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(6, 3.5))
        ax.plot((pts - c)[:, 0] * 1000, ".-", label="x")
        ax.plot((pts - c)[:, 1] * 1000, ".-", label="y")
        ax.set_xlabel("Aufnahme")
        ax.set_ylabel("Abweichung [um]")
        ax.set_title(f"Verlauf {phase}")
        ax.legend()
        fig.tight_layout()
        fig.savefig(out / f"verlauf_{phase}.png", dpi=120)
        plt.close(fig)
        written += [f"streu_{phase}.png", f"verlauf_{phase}.png"]
    if len(warmup) >= 2:
        fig, ax = plt.subplots(figsize=(6, 3.5))
        ax.plot([w["tS"] if w["tS"] is not None else i for i, w in enumerate(warmup)],
                [w["dToLastUm"] for w in warmup], ".-")
        ax.set_xlabel("Zeit [s]")
        ax.set_ylabel("Abstand zur letzten Aufnahme [um]")
        ax.set_title("Warmlauf-Drift")
        fig.tight_layout()
        fig.savefig(out / "warmlauf_drift.png", dpi=120)
        plt.close(fig)
        written.append("warmlauf_drift.png")
    return ", ".join(written) if written else "keine Daten"
