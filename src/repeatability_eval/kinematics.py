"""Kinematik-Test: haengt die Tag-Lage in Roboterbasis von der Gelenkstellung ab?

Ein fester AprilTag wird aus mehreren Roboterstellungen aufgenommen (Pose 0 = Referenz
aus der Phase `rauschen`, Pose 1..n = Phase `zyklen_hand`). Je Bild
`T_base_tag = T_base_flange @ T_flange_cam @ T_cam_tag`; je Pose gemittelt und gegen die
Referenz verglichen. Die Rechenlogik (`summarize_poses`, `render_report`) arbeitet auf
fertigen Matrizen und ist unabhaengig von der Bildverarbeitung. Quellen: `measure_session_from_json`
(Standard, nutzt die Pi-Messung aus den JSONs neben den Bildern) oder `measure_session`
(Offline-Neuerkennung aus den PNGs). `write_session_report` schreibt Bericht/CSV/Plot atomar.
"""

from __future__ import annotations

import csv
import math
import os
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: Mindestzahl Posen (ohne Referenz) fuer die Korrelationstabelle.
MIN_POSES_CORRELATION = 4


@dataclass
class ImageMeasurement:
    """Ergebnis je Bild. `pose` 0 = Referenz. Matrizen 4x4 (m), Gelenke in rad."""

    name: str
    pose: int
    burst: int = 0
    t_base_tag: np.ndarray | None = None
    t_cam_tag: np.ndarray | None = None
    joints: np.ndarray | None = None
    moved: bool = False
    reason: str = ""  # leer = ok; sonst Grund fuer Ausschluss

    @property
    def usable(self) -> bool:
        return self.t_base_tag is not None and not self.moved and not self.reason


# ----------------------------------------------------------------------------- Mathematik
def rotation_angle_deg(r_a: np.ndarray, r_b: np.ndarray) -> float:
    """Drehwinkel (Grad) der Relativrotation zwischen zwei 3x3-Rotationen."""
    c = (np.trace(np.asarray(r_a).T @ np.asarray(r_b)) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, float(c)))))


def mean_rotation(rotations: list[np.ndarray]) -> np.ndarray:
    """Mittlere Rotation (chordal: Projektion der Summe auf SO(3) per SVD)."""
    s = np.sum([np.asarray(r, dtype=np.float64) for r in rotations], axis=0)
    u, _, vt = np.linalg.svd(s)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    return r


def yaw_deg(r_rel: np.ndarray) -> float:
    """Drehung um die z-Achse (Grad) einer Relativrotation (Basis-Koordinaten)."""
    return math.degrees(math.atan2(r_rel[1, 0], r_rel[0, 0]))


def pearson(x: list[float], y: list[float]) -> float:
    """Pearson-r; NaN bei < 3 Werten oder konstanter Reihe."""
    if len(x) < 3:
        return float("nan")
    a, b = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def camera_view(t_cam_tag: np.ndarray) -> dict[str, float]:
    """Tag in Kamera: Abstand, seitliche Lage (mm), Blickwinkel zur Tag-Normalen (Grad)."""
    t = np.asarray(t_cam_tag, dtype=np.float64)
    p = t[:3, 3]
    dist = float(np.linalg.norm(p))
    normal = t[:3, 2]
    cosang = abs(float(np.dot(normal, p / dist))) if dist > 0 else float("nan")
    return {
        "distanceMm": dist * 1000.0,
        "lateralXMm": float(p[0]) * 1000.0,
        "lateralYMm": float(p[1]) * 1000.0,
        "viewAngleDeg": math.degrees(math.acos(max(-1.0, min(1.0, cosang)))),
    }


# ----------------------------------------------------------------------------- Posen
def summarize_poses(images: list[ImageMeasurement]) -> dict[str, Any]:
    """Fasst Bilder je Pose zusammen und vergleicht gegen Pose 0.

    Rueckgabe: `{"poses": {nr: {...}}, "reference": nr|None, "summary": {...}|None,
    "correlation": {...}|None}`. Je Pose (`poses[nr]`): `nImages`, `nUsed`, `positionMm`
    (Mittel in Basis), `rotation` (3x3), `scatterUm` (Std je Achse), `scatter3dUm`
    (RMS-Abstand zum Mittel), `scatterDeg` (RMS-Winkel zur mittleren Rotation), `camera`
    (`camera_view` des gemittelten T_cam_tag, None ohne), `jointsDeg` (Mittel),
    `delta` (nur Pose != 0, mit Referenz: `dxMm,dyMm,dzMm,normMm,angleDeg,yawDeg,
    maxJointDeg,maxJointIndex` (0-basiert), `jointDeltaDeg` Liste). Posen ohne nutzbare Bilder
    haben `positionMm = None`.
    """
    by_pose: dict[int, list[ImageMeasurement]] = {}
    for im in images:
        by_pose.setdefault(im.pose, []).append(im)
    poses: dict[int, dict[str, Any]] = {}
    for nr in sorted(by_pose):
        group = by_pose[nr]
        used = [im for im in group if im.usable]
        entry: dict[str, Any] = {
            "nImages": len(group), "nUsed": len(used), "positionMm": None, "rotation": None,
            "scatterUm": None, "scatter3dUm": None, "scatterDeg": None, "camera": None,
            "jointsDeg": None, "delta": None,
        }
        if used:
            pos = np.array([im.t_base_tag[:3, 3] for im in used]) * 1000.0
            mean_pos = pos.mean(axis=0)
            rots = [im.t_base_tag[:3, :3] for im in used]
            r_mean = mean_rotation(rots)
            entry["positionMm"] = mean_pos
            entry["rotation"] = r_mean
            if len(used) > 1:
                entry["scatterUm"] = pos.std(axis=0, ddof=1) * 1000.0
                entry["scatter3dUm"] = float(
                    math.sqrt(np.mean(np.sum((pos - mean_pos) ** 2, axis=1)) * len(used) / (len(used) - 1))
                    * 1000.0
                )
                angles = [rotation_angle_deg(r_mean, r) for r in rots]
                entry["scatterDeg"] = float(
                    math.sqrt(sum(a * a for a in angles) / (len(used) - 1))
                )
            cams = [im.t_cam_tag for im in used if im.t_cam_tag is not None]
            if cams:
                t_mean = np.eye(4)
                t_mean[:3, :3] = mean_rotation([c[:3, :3] for c in cams])
                t_mean[:3, 3] = np.mean([c[:3, 3] for c in cams], axis=0)
                entry["camera"] = camera_view(t_mean)
            joints = [im.joints for im in used if im.joints is not None]
            if joints:
                entry["jointsDeg"] = np.degrees(np.mean(joints, axis=0))
        poses[nr] = entry

    ref = poses.get(0)
    reference = 0 if ref is not None and ref["positionMm"] is not None else None
    if reference is not None:
        for nr, entry in poses.items():
            if nr == 0 or entry["positionMm"] is None:
                continue
            d = entry["positionMm"] - ref["positionMm"]
            r_rel = entry["rotation"] @ ref["rotation"].T
            delta: dict[str, Any] = {
                "dxMm": float(d[0]), "dyMm": float(d[1]), "dzMm": float(d[2]),
                "normMm": float(np.linalg.norm(d)),
                "angleDeg": rotation_angle_deg(ref["rotation"], entry["rotation"]),
                "yawDeg": yaw_deg(r_rel),
                "maxJointDeg": None, "maxJointIndex": None, "jointDeltaDeg": None,
            }
            if entry["jointsDeg"] is not None and ref["jointsDeg"] is not None:
                dq = entry["jointsDeg"] - ref["jointsDeg"]
                k = int(np.argmax(np.abs(dq)))
                delta.update(maxJointDeg=float(abs(dq[k])), maxJointIndex=k,
                             jointDeltaDeg=[float(v) for v in dq])
            entry["delta"] = delta
    return {
        "poses": poses,
        "reference": reference,
        "summary": _summary(poses),
        "correlation": _correlation(poses),
    }


def _summary(poses: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    deltas = [e["delta"] for nr, e in poses.items() if nr != 0 and e["delta"] is not None]
    if not deltas:
        return None
    norms = np.array([d["normMm"] for d in deltas])
    out: dict[str, Any] = {
        "nPoses": len(deltas),
        "rmsMm": float(math.sqrt(np.mean(norms ** 2))),
        "maxMm": float(norms.max()),
        "maxAngleDeg": float(max(d["angleDeg"] for d in deltas)),
        "maxYawAbsDeg": float(max(abs(d["yawDeg"]) for d in deltas)),
    }
    for key in ("dxMm", "dyMm", "dzMm"):
        vals = [d[key] for d in deltas]
        out[key + "Range"] = float(max(vals) - min(vals))
    return out


def _correlation(poses: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    rows = [e["delta"] for nr, e in poses.items()
            if nr != 0 and e["delta"] is not None and e["delta"]["jointDeltaDeg"] is not None]
    if len(rows) < MIN_POSES_CORRELATION:
        return None
    n_joints = len(rows[0]["jointDeltaDeg"])
    table = []
    for j in range(n_joints):
        dq = [r["jointDeltaDeg"][j] for r in rows]
        table.append({
            "joint": j,
            "dx": pearson(dq, [r["dxMm"] for r in rows]),
            "dy": pearson(dq, [r["dyMm"] for r in rows]),
            "dz": pearson(dq, [r["dzMm"] for r in rows]),
            "norm": pearson([abs(v) for v in dq], [r["normMm"] for r in rows]),
        })
    return {"n": len(rows), "joints": table}


# ----------------------------------------------------------------------------- Bericht
def _f(value: Any, digits: int = 1, sign: bool = False) -> str:
    """Zahl mit Dezimalkomma; `-` bei None/NaN."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    text = f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"
    return text.replace(".", ",").replace("-", "−") if text.startswith("-") else text.replace(".", ",")


def render_report(result: dict[str, Any], title: str = "Kinematik-Test", info: list[str] | None = None,
                  warnings: list[str] | None = None, labels: dict[int, str] | None = None) -> str:
    """Markdown-Bericht (deutsch, Dezimalkomma) aus `summarize_poses`."""
    poses = result["poses"]
    lines = [f"# {title}", ""]
    for text in info or []:
        lines.append(f"- {text}")
    if info:
        lines.append("")
    for w in warnings or []:
        lines.append(f"> Warnung: {w}")
    if warnings:
        lines.append("")
    if result["reference"] is None:
        lines += ["Keine auswertbare Referenzpose (Pose 0, Phase `rauschen`).", ""]

    header, rows = pose_table(result, labels, decimal_comma=True)
    lines += ["## Messreihe (eine Zeile je Pose)", "", "| " + " | ".join(h.replace("|", "\\|") for h in header) + " |",
              "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(v) if v != "" else "-" for v in row) + " |" for row in rows]
    lines.append("")

    lines += ["## Tag-Lage je Pose in Roboterbasis", "",
              "| Pose | Bilder (genutzt/gesamt) | x [mm] | y [mm] | z [mm] | Streuung 3D [µm] | "
              "Streuung x/y/z [µm] | Orientierungsstreuung [°] |",
              "|---|---|---|---|---|---|---|---|"]
    for nr, e in poses.items():
        p = e["positionMm"]
        sc = e["scatterUm"]
        lines.append(
            f"| {'0 (Ref.)' if nr == 0 else nr} | {e['nUsed']}/{e['nImages']} | "
            + (" | ".join(_f(v, 2) for v in p) if p is not None else "- | - | -")
            + f" | {_f(e['scatter3dUm'], 1)} | "
            + (" / ".join(_f(v, 1) for v in sc) if sc is not None else "-")
            + f" | {_f(e['scatterDeg'], 3)} |"
        )
    lines.append("")

    lines += ["## Tag in der Kamera", "",
              "| Pose | Abstand [mm] | seitlich x [mm] | seitlich y [mm] | Blickwinkel zur Tag-Normalen [°] |",
              "|---|---|---|---|---|"]
    for nr, e in poses.items():
        c = e["camera"]
        if c is None:
            continue
        lines.append(f"| {'0 (Ref.)' if nr == 0 else nr} | {_f(c['distanceMm'], 1)} | "
                     f"{_f(c['lateralXMm'], 1)} | {_f(c['lateralYMm'], 1)} | {_f(c['viewAngleDeg'], 1)} |")
    lines.append("")

    lines += ["## Gelenkwinkel [°]", "", "| Pose | q1 | q2 | q3 | q4 | q5 | q6 | q7 |", "|---|---|---|---|---|---|---|---|"]
    for nr, e in poses.items():
        if e["jointsDeg"] is not None:
            lines.append(f"| {'0 (Ref.)' if nr == 0 else nr} | " + " | ".join(_f(v, 2) for v in e["jointsDeg"]) + " |")
    lines.append("")

    deltas = [(nr, e["delta"]) for nr, e in poses.items() if e["delta"] is not None]
    if deltas:
        lines += ["## Abweichung gegen Referenz (Pose 0)", "",
                  "| Pose | Δx [mm] | Δy [mm] | Δz [mm] | \\|Δ\\| [mm] | Drehung [°] | Gieren um Basis-z [°] | "
                  "max. \\|Δq\\| [°] | Gelenk |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for nr, d in deltas:
            joint = f"q{d['maxJointIndex'] + 1}" if d["maxJointIndex"] is not None else "-"
            lines.append(
                f"| {nr} | {_f(d['dxMm'], 2, True)} | {_f(d['dyMm'], 2, True)} | {_f(d['dzMm'], 2, True)} | "
                f"{_f(d['normMm'], 2)} | {_f(d['angleDeg'], 3)} | {_f(d['yawDeg'], 3, True)} | "
                f"{_f(d['maxJointDeg'], 1)} | {joint} |"
            )
        lines.append("")
    s = result["summary"]
    if s:
        lines += [f"## Zusammenfassung (Posen 1 bis {s['nPoses']})", "",
                  f"- RMS |Δ|: {_f(s['rmsMm'], 2)} mm, max. |Δ|: {_f(s['maxMm'], 2)} mm",
                  f"- Spannweite: x {_f(s['dxMmRange'], 2)} mm, y {_f(s['dyMmRange'], 2)} mm, "
                  f"z {_f(s['dzMmRange'], 2)} mm",
                  f"- max. Drehwinkel-Δ der Tag-Orientierung: {_f(s['maxAngleDeg'], 3)}° "
                  f"(Gieren max. {_f(s['maxYawAbsDeg'], 3)}°)", ""]
    corr = result["correlation"]
    if corr:
        lines += [f"## Zusammenhang mit den Gelenken (n = {corr['n']} Posen)", "",
                  "Pearson r der Gelenkänderung gegen die Tag-Verschiebung; zeilenweise nur Hinweis, "
                  "bei wenigen Posen kaum belastbar. Die letzte Spalte vergleicht |Δq| mit |Δ|.", "",
                  "| Gelenk | r(Δq, Δx) | r(Δq, Δy) | r(Δq, Δz) | r(\\|Δq\\|, \\|Δ\\|) |", "|---|---|---|---|---|"]
        for row in corr["joints"]:
            lines.append(f"| q{row['joint'] + 1} | {_f(row['dx'], 2)} | {_f(row['dy'], 2)} | "
                         f"{_f(row['dz'], 2)} | {_f(row['norm'], 2)} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ----------------------------------------------------------------------------- Bilder
def pose_number(phase: str, client: dict) -> int | None:
    """Posennummer: `rauschen` = 0, `zyklen_hand` = `poseIndex` bzw. `cycle`; sonst None."""
    if phase == "rauschen":
        return 0
    if phase == "zyklen_hand":
        for key in ("poseIndex", "cycle"):
            try:
                return int(client[key])
            except (KeyError, TypeError, ValueError):
                continue
    return None


@dataclass
class SessionMeasurement:
    images: list[ImageMeasurement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tag_id: int | None = None
    labels: dict[int, str] = field(default_factory=dict)
    calibration_id: str = ""
    hand_eye_id: str = ""
    date: str = ""  # `gespeichert` des ersten Bildes


def measure_session(session_dir: Path, tag_id: int | None = None, tag_size_m: float = 0.08,
                    calibration: Any = None, hand_eye: Any = None) -> SessionMeasurement:
    """Erkennt den Tag offline in allen Bildern und bildet `T_base_tag`.

    `calibration` (`CameraCalibration`) und `hand_eye` (4x4 T_flange_cam) ueberschreiben die
    in den Record-JSONs gespeicherten Werte (`pi.intrinsics`, `pi.hand_eye`).
    """
    import cv2

    from tagloc.pose import estimate_tag_pose
    from use_case_eval.live import calibration_from_meta

    from .apriltag import (
        _calibration_for,
        build_apriltag_detector,
        detect_observations,
        pose_from_client,
        select_observation,
    )
    from .session import _load_records

    records, warnings = _load_records(Path(session_dir))
    out = SessionMeasurement(warnings=list(warnings))
    records = [r for r in records if pose_number(r["phase"], r["client"]) is not None]
    if not records:
        raise ValueError("Keine Aufnahmen der Phasen rauschen/zyklen_hand gefunden")
    config = next((r["doc"]["pi"]["apriltag"] for r in records
                   if isinstance((r["doc"].get("pi") or {}).get("apriltag"), dict)), None)
    if config is None:
        raise ValueError("Keine Detektor-Einstellungen (pi.apriltag) in den Aufnahmen")
    detector = build_apriltag_detector(config)

    for rec in records:
        client, pi = rec["client"], rec["doc"].get("pi") or {}
        nr = pose_number(rec["phase"], client)
        if client.get("poseLabel"):
            out.labels.setdefault(nr, str(client["poseLabel"]))
        actual = client.get("actual") if isinstance(client.get("actual"), dict) else {}
        out.date = out.date or str(rec["doc"].get("gespeichert") or "")
        if not out.hand_eye_id:
            try:
                he_doc = pi["hand_eye"]["content"]
                out.hand_eye_id = f"{he_doc.get('calibrationId', '')} ({he_doc.get('createdAt', '')})"
            except (KeyError, TypeError):
                pass
        im = ImageMeasurement(name=rec["name"], pose=nr, burst=int(client.get("burstIndex") or 0))
        out.images.append(im)
        try:
            im.joints = np.asarray(actual["joints"], dtype=np.float64).reshape(-1)
            if not np.all(np.isfinite(im.joints)):
                im.joints = None
        except (KeyError, TypeError, ValueError):
            im.joints = None
        im.moved = bool(actual.get("movedDuringCapture"))
        if im.moved:
            im.reason = "Roboter bewegt waehrend Aufnahme"
        image = cv2.imread(str(rec["png"]), cv2.IMREAD_UNCHANGED)
        if image is None:
            im.reason = im.reason or "Bild nicht lesbar"
            continue
        obs, grund = select_observation(detect_observations(image, detector), tag_id)
        if obs is None:
            im.reason = im.reason or grund
            continue
        size = (image.shape[1], image.shape[0])
        calib = calibration
        if calib is None:
            try:
                calib = calibration_from_meta(pi["intrinsics"])
            except (KeyError, TypeError, ValueError):
                calib = None
        calib = _calibration_for(calib, size)
        if calib is None:
            im.reason = im.reason or "keine passende Intrinsik"
            continue
        he = hand_eye
        if he is None:
            try:
                he = pose_from_client(pi["hand_eye"]["content"]["poseFlangeCam"])
            except (KeyError, TypeError):
                he = None
        flange = pose_from_client(actual.get("flangeInBase"))
        if he is None or flange is None:
            im.reason = im.reason or "Hand-Auge oder Flanschpose fehlt"
            continue
        try:
            cam_tag = np.asarray(estimate_tag_pose(obs, tag_size_m, calib).pose_cam_tag, dtype=np.float64)
        except Exception as error:
            im.reason = im.reason or f"Tag-Pose fehlgeschlagen: {error}"
            continue
        if not np.all(np.isfinite(cam_tag)):
            im.reason = im.reason or "Tag-Pose nicht endlich"
            continue
        out.calibration_id = out.calibration_id or str(getattr(calib, "calibration_id", ""))
        im.t_cam_tag = cam_tag
        im.t_base_tag = np.asarray(flange, dtype=np.float64) @ np.asarray(he, dtype=np.float64) @ cam_tag
        if out.tag_id is None:
            out.tag_id = int(obs.tag_id)
    return out


def measure_session_from_json(session_dir: Path) -> SessionMeasurement:
    """Wie `measure_session`, nutzt aber die Pi-Messung aus den Bild-JSONs (kein PNG, keine Erkennung).

    `T_cam_tag` aus `messung.tagInCamM/tagInCamQuat`, `T_base_tag = T_base_flange @
    T_flange_cam @ T_cam_tag` mit `client.actual.flangeInBase` und `pi.hand_eye`. Weicht die
    Position um mehr als 0,01 mm von `messung.tagInBaseM` ab, gibt es eine Warnung.
    """
    from tagloc.geometry import from_position_quaternion

    from .apriltag import pose_from_client
    from .session import _load_records

    records, warnings = _load_records(Path(session_dir))
    out = SessionMeasurement(warnings=list(warnings))
    records = [r for r in records if pose_number(r["phase"], r["client"]) is not None]
    if not records:
        raise ValueError("Keine Aufnahmen der Phasen rauschen/zyklen_hand gefunden")
    n_mismatch = 0
    max_mismatch_mm = 0.0
    for rec in records:
        client, pi = rec["client"], rec["doc"].get("pi") or {}
        nr = pose_number(rec["phase"], client)
        if client.get("poseLabel"):
            out.labels.setdefault(nr, str(client["poseLabel"]))
        actual = client.get("actual") if isinstance(client.get("actual"), dict) else {}
        messung = rec["doc"].get("messung") if isinstance(rec["doc"].get("messung"), dict) else {}
        out.date = out.date or str(rec["doc"].get("gespeichert") or "")
        if not out.hand_eye_id:
            try:
                he_doc = pi["hand_eye"]["content"]
                out.hand_eye_id = f"{he_doc.get('calibrationId', '')} ({he_doc.get('createdAt', '')})"
            except (KeyError, TypeError):
                pass
        if not out.calibration_id:
            try:
                out.calibration_id = str(pi["intrinsics"]["calibrationId"] or "")
            except (KeyError, TypeError):
                pass
        im = ImageMeasurement(name=rec["name"], pose=nr, burst=int(client.get("burstIndex") or 0))
        out.images.append(im)
        try:
            im.joints = np.asarray(actual["joints"], dtype=np.float64).reshape(-1)
            if not np.all(np.isfinite(im.joints)):
                im.joints = None
        except (KeyError, TypeError, ValueError):
            im.joints = None
        im.moved = bool(actual.get("movedDuringCapture"))
        if im.moved:
            im.reason = "Roboter bewegt waehrend Aufnahme"
        if not messung.get("ok"):
            im.reason = im.reason or str(messung.get("grund") or "keine Pi-Messung")
            continue
        try:
            cam_tag = np.asarray(
                from_position_quaternion(messung["tagInCamM"], messung["tagInCamQuat"]), dtype=np.float64)
        except (KeyError, TypeError, ValueError, IndexError):
            cam_tag = None
        if cam_tag is None or not np.all(np.isfinite(cam_tag)):
            im.reason = im.reason or "Tag-Pose in Kamera fehlt"
            continue
        try:
            he = pose_from_client(pi["hand_eye"]["content"]["poseFlangeCam"])
        except (KeyError, TypeError):
            he = None
        flange = pose_from_client(actual.get("flangeInBase"))
        if he is None or flange is None:
            im.reason = im.reason or "Hand-Auge oder Flanschpose fehlt"
            continue
        im.t_cam_tag = cam_tag
        im.t_base_tag = np.asarray(flange, dtype=np.float64) @ np.asarray(he, dtype=np.float64) @ cam_tag
        if out.tag_id is None and messung.get("tagId") is not None:
            try:
                out.tag_id = int(messung["tagId"])
            except (TypeError, ValueError):
                pass
        try:
            pi_pos = np.asarray(messung["tagInBaseM"], dtype=np.float64).reshape(3)
        except (KeyError, TypeError, ValueError):
            continue
        if np.all(np.isfinite(pi_pos)):
            diff_mm = float(np.linalg.norm(im.t_base_tag[:3, 3] - pi_pos) * 1000.0)
            if diff_mm > 0.01:
                n_mismatch += 1
                max_mismatch_mm = max(max_mismatch_mm, diff_mm)
    if n_mismatch:
        out.warnings.append(
            f"{n_mismatch} Bilder: nachgerechnete Tag-Position weicht um mehr als 0,01 mm von "
            f"`messung.tagInBaseM` ab (max. {max_mismatch_mm:.3f} mm) - Quaternion-Konvention oder "
            "Hand-Auge pruefen")
    return out


def csv_rows(images: list[ImageMeasurement]) -> tuple[list[str], list[list[Any]]]:
    """Kopfzeile und Zeilen je Bild (Position mm, Gelenke Grad) fuer die CSV."""
    header = ["bild", "pose", "burst", "genutzt", "grund", "x_mm", "y_mm", "z_mm", "tag_dist_mm"] + [
        f"q{i}_deg" for i in range(1, 8)]
    rows = []
    for im in images:
        pos = list(im.t_base_tag[:3, 3] * 1000.0) if im.t_base_tag is not None else ["", "", ""]
        dist = float(np.linalg.norm(im.t_cam_tag[:3, 3]) * 1000.0) if im.t_cam_tag is not None else ""
        q = list(np.degrees(im.joints)) if im.joints is not None else []
        q += [""] * (7 - len(q))
        rows.append([im.name, im.pose, im.burst, int(im.usable), im.reason, *pos, dist, *q[:7]])
    return header, rows


# ----------------------------------------------------------------------------- Messreihe
def pose_table(result: dict[str, Any], labels: dict[int, str] | None = None,
               decimal_comma: bool = True) -> tuple[list[str], list[list[Any]]]:
    """Messreihe je Pose als Kopfzeile + Zeilen (Texte; Zahlen mit Komma oder Punkt)."""
    def fmt(v: Any, d: int) -> str:
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return ""
        t = f"{float(v):.{d}f}"
        return t.replace(".", ",") if decimal_comma else t

    header = (["Pose", "Label", "Bilder gültig/gesamt"] + [f"q{i} [°]" for i in range(1, 8)]
              + ["x [mm]", "y [mm]", "z [mm]", "Δx [mm]", "Δy [mm]", "Δz [mm]", "|Δ| [mm]",
                 "Winkel-Δ [°]", "Streuung [µm]", "Tag-Abstand [mm]", "Blickwinkel [°]"])
    rows = []
    for nr, e in result["poses"].items():
        q = list(e["jointsDeg"]) if e["jointsDeg"] is not None else []
        q = [fmt(v, 2) for v in q] + [""] * (7 - len(q))
        p = e["positionMm"]
        pos = [fmt(v, 2) for v in p] if p is not None else ["", "", ""]
        d = e["delta"]
        dl = ([fmt(d["dxMm"], 3), fmt(d["dyMm"], 3), fmt(d["dzMm"], 3), fmt(d["normMm"], 3), fmt(d["angleDeg"], 3)]
              if d else (["0", "0", "0", "0", "0"] if nr == 0 and p is not None else [""] * 5))
        c = e["camera"]
        rows.append([nr, (labels or {}).get(nr, "Referenz" if nr == 0 else ""),
                     f"{e['nUsed']}/{e['nImages']}", *q[:7], *pos, *dl, fmt(e["scatter3dUm"], 1),
                     fmt(c["distanceMm"], 1) if c else "", fmt(c["viewAngleDeg"], 1) if c else ""])
    return header, rows


def plot_deltas(result: dict[str, Any], path: Path) -> bool:
    """Plot Δx/Δy/Δz/|Δ| je Pose nach `path`; False (ohne Fehler), wenn matplotlib fehlt oder nichts da ist."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    items = [(nr, e["delta"]) for nr, e in result["poses"].items() if e["delta"] is not None]
    if not items:
        return False
    nrs = [nr for nr, _ in items]
    x = np.arange(len(nrs))
    fig, ax = plt.subplots(figsize=(max(6.0, 1.0 + 0.9 * len(nrs)), 4.5))
    w = 0.2
    for k, (key, name) in enumerate((("dxMm", "Δx"), ("dyMm", "Δy"), ("dzMm", "Δz"), ("normMm", "|Δ|"))):
        ax.bar(x + (k - 1.5) * w, [d[key] for _, d in items], w, label=name)
    ax.axhline(0.0, color="black", linewidth=1.0, label="Referenz (Pose 0)")
    ax.set_xticks(x)
    ax.set_xticklabels([str(n) for n in nrs])
    ax.set_xlabel("Pose")
    ax.set_ylabel("Abweichung der Tag-Lage in Basis [mm]")
    ax.set_title("Kinematik-Test: Abweichung gegen Referenzpose")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(ncol=5, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


# ----------------------------------------------------------------------------- Schreiben
def _atomic_write(path: Path, writer) -> None:
    """Schreibt ueber `<datei>.tmp` und `os.replace`, damit Leser nie eine halbe Datei sehen."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_csv(path: Path, header: list[str], rows: list[list[Any]], delimiter: str, encoding: str) -> None:
    def write(tmp: Path) -> None:
        with tmp.open("w", newline="", encoding=encoding) as fh:
            w = csv.writer(fh, delimiter=delimiter)
            w.writerow(header)
            w.writerows(rows)

    _atomic_write(path, write)


def write_session_report(session_dir: Path, *, source: str = "json", tag_id: int | None = None,
                         tag_size_m: float = 0.08, calibration: Any = None, hand_eye: Any = None,
                         out: Path | None = None, csv_posen: Path | None = None,
                         plot: Path | None = None, csv_bilder: Path | None = None,
                         decimal_comma: bool = True) -> dict[str, Any]:
    """Wertet eine Session aus und schreibt Bericht, Posen-CSV, Plot (und optional CSV je Bild).

    `source="json"`: Pi-Messung aus den JSONs (`tag_id`/`tag_size_m`/`calibration`/`hand_eye`
    wirkungslos); `"bilder"`: Offline-Neuerkennung (`measure_session`). Standardpfade in der
    Session: `kinematik_bericht.md`, `kinematik_posen.csv`, `kinematik_posen.png`. Alles atomar.
    Rueckgabe: `{"summary", "report", "csvPosen", "plot" (None ohne), "csvBilder" (None ohne)}`.
    `ValueError`, wenn keine Aufnahmen da sind.
    """
    sd = Path(session_dir)
    if source == "json":
        res = measure_session_from_json(sd)
    elif source == "bilder":
        res = measure_session(sd, tag_id, tag_size_m, calibration, hand_eye)
    else:
        raise ValueError(f"unbekannte Quelle: {source!r}")
    summary = summarize_poses(res.images)
    tag_text = f"ID {res.tag_id if res.tag_id is not None else '-'}"
    if source == "bilder":
        tag_text += f", {tag_size_m * 1000.0:g} mm".replace(".", ",")
    info = [f"Session: `{sd.name}`",
            "Quelle: " + ("Pi-Messung aus den JSONs" if source == "json" else "Offline neu erkannt"),
            f"Stand: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Tag: {tag_text}",
            f"Datum: {res.date or '-'}",
            "Kalibrierung: " + ("Datei" if calibration is not None else "aus den Records")
            + f" (ID `{res.calibration_id or '-'}`)",
            "Hand-Auge: " + ("Datei" if hand_eye is not None else "aus den Records")
            + (f" (ID `{res.hand_eye_id}`)" if res.hand_eye_id and hand_eye is None else "")]
    if res.labels:
        info.append("Posen: " + ", ".join(f"{k} = {v}" for k, v in sorted(res.labels.items())))
    bad = [im for im in res.images if not im.usable]
    warnings = list(res.warnings)
    if bad:
        warnings.append(f"{len(bad)} von {len(res.images)} Bildern nicht genutzt: "
                        + "; ".join(f"{im.name} ({im.reason or 'unbekannt'})" for im in bad[:10])
                        + (" ..." if len(bad) > 10 else ""))
    text = render_report(summary, info=info, warnings=warnings, labels=res.labels)
    written: dict[str, Any] = {"summary": summary, "report": None, "csvPosen": None, "plot": None,
                               "csvBilder": None}
    report_path = Path(out) if out else sd / "kinematik_bericht.md"
    _atomic_write(report_path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
    written["report"] = report_path
    header, rows = pose_table(summary, res.labels, decimal_comma=decimal_comma)
    posen = Path(csv_posen) if csv_posen else sd / "kinematik_posen.csv"
    _write_csv(posen, header, rows, ";" if decimal_comma else ",", "utf-8-sig")
    written["csvPosen"] = posen
    png = Path(plot) if plot else sd / "kinematik_posen.png"
    if _atomic_plot(summary, png):
        written["plot"] = png
    if csv_bilder:
        header, rows = csv_rows(res.images)
        _write_csv(Path(csv_bilder), header, rows, ",", "utf-8")
        written["csvBilder"] = Path(csv_bilder)
    return written


def _atomic_plot(summary: dict[str, Any], path: Path) -> bool:
    """`plot_deltas` ueber Temp-Datei (Endung `.png` bleibt fuer matplotlib erhalten)."""
    tmp = path.with_name(path.stem + ".tmp" + path.suffix)
    try:
        if not plot_deltas(summary, tmp):
            return False
        os.replace(tmp, path)
        return True
    finally:
        tmp.unlink(missing_ok=True)
