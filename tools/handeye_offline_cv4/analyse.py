#!/usr/bin/env python
"""Offline-Analyse gespeicherter Hand-Auge-Sitzungen mit OpenCV 4.x (cv2.calibrateHandEye).

Liest die Sitzungen nur; load_observations arbeitet auf einer temporaeren Kopie.
Laengen in mm, Winkel in Grad, Fehler in px.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

try:
    import cv2  # noqa: E402
except ImportError:  # pragma: no cover
    sys.exit("OpenCV (cv2) nicht importierbar.")
if not hasattr(cv2, "calibrateHandEye"):
    sys.exit(f"cv2.calibrateHandEye fehlt (OpenCV {cv2.__version__}). Bitte die Offline-Umgebung mit "
             "OpenCV 4.x verwenden (tools/handeye_offline_cv4/requirements.txt).")
import scipy  # noqa: E402
from scipy.optimize import least_squares  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

from tagloc import geometry  # noqa: E402
from tagloc.handeye import solve_hand_eye  # noqa: E402
from tagloc.handeye_offline import (  # noqa: E402
    _base_board, _pose_from_vector, _residuals, _solution_fields,
    _vector_from_pose, diverse_subsets, load_observations)

DEFAULT_SESSIONS = [
    "data/rohbilder/calibration/20261109T125050Z_a5c964f0",
    "data/rohbilder/calibration/20261109T125825Z_a580eaba",
]
MAIN = ["park", "tsai", "daniilidis", "park_numpy"]
EXTRA = ["horaud", "andreff"]
FLAGS = {"park": "CALIB_HAND_EYE_PARK", "tsai": "CALIB_HAND_EYE_TSAI",
         "daniilidis": "CALIB_HAND_EYE_DANIILIDIS", "horaud": "CALIB_HAND_EYE_HORAUD",
         "andreff": "CALIB_HAND_EYE_ANDREFF"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if math.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def sp(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if np.ptp(a) == 0 or np.ptp(b) == 0:
        return {"rho": None, "p": None}
    r = spearmanr(a, b)
    return {"rho": float(r.statistic), "p": float(r.pvalue)}


# ---------------------------------------------------------------- Helfer

def rms_of(x, obs, calib, points):
    r = _residuals(x, obs, calib, points)
    return float(np.sqrt(np.mean(r ** 2) * 2))


def pose_diff(a, b):
    """a - b: Translation in mm (Vektor+Norm), Rotationswinkel in Grad."""
    d = (np.asarray(a)[:3, 3] - np.asarray(b)[:3, 3]) * 1000
    return {"dtMm": d, "dtNormMm": float(np.linalg.norm(d)),
            "dRotDeg": float(np.degrees(geometry.rotation_distance_rad(a, b)))}


def he_summary(he):
    rv = geometry.rvec_from_rotation(he[:3, :3])
    return {"T_flange_camera": he, "translationMm": he[:3, 3] * 1000,
            "rotvecDeg": np.degrees(rv), "rotationDeg": float(np.degrees(np.linalg.norm(rv)))}


def closed_forms(obs, calib, points, names):
    fl = [o.flange for o in obs]
    bo = [o.board_in_camera for o in obs]
    out = {}
    for name in names:
        try:
            if name == "park_numpy":
                he, _, _ = solve_hand_eye(fl, bo)
            else:
                R, t = cv2.calibrateHandEye([f[:3, :3] for f in fl], [f[:3, 3] for f in fl],
                                            [b[:3, :3] for b in bo], [b[:3, 3] for b in bo],
                                            method=getattr(cv2, FLAGS[name]))
                he = geometry.from_rotation_translation(R, np.asarray(t).reshape(3))
            if not np.all(np.isfinite(he)):
                raise ValueError("nicht endlich")
            B = _base_board(fl, bo, he)
            x = np.r_[_vector_from_pose(he), _vector_from_pose(B)]
            out[name] = {"he": he, "x": x, "rmsPx": rms_of(x, obs, calib, points),
                         **_solution_fields(he, fl, bo)}
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": str(e)}
    return out


def refine(obs, calib, points, x0, loss="soft_l1", max_nfev=200):
    fit = least_squares(_residuals, x0, args=(obs, calib, points), loss=loss, f_scale=1.0, max_nfev=max_nfev)
    return {"x": fit.x, "he": _pose_from_vector(fit.x[:6]), "B": _pose_from_vector(fit.x[6:12]),
            "rmsPx": rms_of(fit.x, obs, calib, points), "converged": bool(fit.success), "nfev": int(fit.nfev)}


def refined_from_best(obs, calib, points, max_nfev=200):
    cf = closed_forms(obs, calib, points, MAIN)
    ok = {k: v for k, v in cf.items() if "he" in v}
    if not ok:
        raise ValueError("keine Startloesung")
    best = min(ok, key=lambda k: ok[k]["rmsPx"])
    return cf, best, refine(obs, calib, points, ok[best]["x"], max_nfev=max_nfev)


def tr_rot(sol):
    return {"translationMm": sol["he"][:3, 3] * 1000,
            "rotationDeg": float(np.degrees(np.linalg.norm(geometry.rvec_from_rotation(sol["he"][:3, :3])))),
            "rmsPx": sol["rmsPx"]}


# ---------------------------------------------------------------- Intrinsik / Massstab

def _proj_model(x, obs, calib, points, mode):
    """Residuen; mode enthaelt 'K' (fx,fy,cx,cy), 'D' (k1,k2,p1,p2,k3), 'S' (Massstab der Objektpunkte)."""
    he, B = _pose_from_vector(x[:6]), _pose_from_vector(x[6:12])
    K = np.array(calib.camera_matrix, dtype=np.float64)
    D = np.array(calib.distortion, dtype=np.float64).reshape(-1).copy()
    i = 12
    if "K" in mode:
        K[0, 0], K[1, 1], K[0, 2], K[1, 2] = x[i:i + 4]
        i += 4
    if "D" in mode:
        D[:5] = x[i:i + 5]
        i += 5
    s = x[i] if "S" in mode else 1.0
    chunks = []
    for o in obs:
        cb = geometry.invert(geometry.compose(o.flange, he)) @ B
        pts = np.asarray(o.object_points if o.object_points is not None else points, float).reshape(-1, 3) * s
        rv = geometry.rvec_from_rotation(cb[:3, :3])
        p, _ = cv2.projectPoints(pts, rv.reshape(3, 1), cb[:3, 3].reshape(3, 1), K, D)
        chunks.append((p.reshape(-1, 2) - o.corners).reshape(-1))
    return np.concatenate(chunks)


def diag_fit(obs, calib, points, x_final, mode, max_nfev=200):
    K = np.array(calib.camera_matrix)
    D = np.array(calib.distortion).reshape(-1)
    extra, names = [], []
    if "K" in mode:
        extra += [K[0, 0], K[1, 1], K[0, 2], K[1, 2]]
        names += ["fx", "fy", "cx", "cy"]
    if "D" in mode:
        extra += list(D[:5])
        names += ["k1", "k2", "p1", "p2", "k3"]
    if "S" in mode:
        extra += [1.0]
        names += ["scale"]
    x0 = np.r_[x_final, extra]
    base = float(np.sqrt(np.mean(_proj_model(x0, obs, calib, points, mode) ** 2) * 2))
    fit = least_squares(_proj_model, x0, args=(obs, calib, points, mode), loss="soft_l1", f_scale=1.0,
                        x_scale="jac", max_nfev=max_nfev)
    rms = float(np.sqrt(np.mean(_proj_model(fit.x, obs, calib, points, mode) ** 2) * 2))
    he = _pose_from_vector(fit.x[:6])
    vals = dict(zip(names, fit.x[12:]))
    start = dict(zip(names, x0[12:]))
    return {"startRmsPx": base, "rmsPx": rms, "converged": bool(fit.success), "nfev": int(fit.nfev),
            **he_summary(he), "params": vals, "delta": {k: vals[k] - start[k] for k in names}}


# ---------------------------------------------------------------- Auswertung je Sitzung

def load_states(folder):
    states = {}
    for p in sorted((folder / "poses").glob("pose_*")):
        try:
            states[p.name] = json.loads((p / "robot_state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return states


def parity(orig_folder, obs):
    path = orig_folder / "results" / "detections_iterative.json"
    if not path.exists():
        return {"available": False}
    saved = json.loads(path.read_text(encoding="utf-8"))
    sv = {d["poseId"]: d for d in saved["valid"]}
    mine = {o.pose_id: o for o in obs}
    per = {}
    for pid in sorted(set(sv) & set(mine)):
        o, d = mine[pid], sv[pid]
        c_saved = np.asarray(d["cornersPx"], float)
        entry = {"pnpRmsPxNew": o.pnp_rms_px, "pnpRmsPxSaved": d["pnpRmsPx"],
                 "pnpRmsDiffPx": o.pnp_rms_px - d["pnpRmsPx"]}
        ids_s = d.get("cornerIds")
        if o.corner_ids is not None and ids_s is not None:
            a = {int(i): c for i, c in zip(o.corner_ids, o.corners)}
            b = {int(i): c for i, c in zip(ids_s, c_saved)}
            common = sorted(set(a) & set(b))
            entry["cornerCountNew"], entry["cornerCountSaved"] = len(a), len(b)
            entry["maxCornerDevPx"] = float(max(np.linalg.norm(a[i] - b[i]) for i in common)) if common else None
            entry["cornerIdsEqual"] = set(a) == set(b)
        else:
            same = o.corners.shape == c_saved.shape
            entry["cornerCountNew"], entry["cornerCountSaved"] = len(o.corners), len(c_saved)
            entry["maxCornerDevPx"] = float(np.max(np.linalg.norm(o.corners - c_saved, axis=1))) if same else None
            entry["cornerIdsEqual"] = same
        per[pid] = entry
    devs = [e["maxCornerDevPx"] for e in per.values() if e["maxCornerDevPx"] is not None]
    rd = [abs(e["pnpRmsDiffPx"]) for e in per.values()]
    return {"available": True, "samePoseSet": set(sv) == set(mine),
            "onlyInSaved": sorted(set(sv) - set(mine)), "onlyInNew": sorted(set(mine) - set(sv)),
            "maxCornerDevPx": max(devs) if devs else None, "maxAbsPnpRmsDiffPx": max(rd) if rd else None,
            "allCornerIdsEqual": all(e["cornerIdsEqual"] for e in per.values()), "perPose": per}


def board_dev(obs, he, B):
    out = []
    for o in obs:
        p = geometry.compose(o.flange, he, o.board_in_camera)
        d = (p[:3, 3] - B[:3, 3]) * 1000
        out.append({"vec": d, "norm": float(np.linalg.norm(d)),
                    "rotDeg": float(np.degrees(geometry.rotation_distance_rad(p, B)))})
    return out


def yaw_deg(R):
    y = math.degrees(math.atan2(R[1, 0], R[0, 0]))
    return 180.0 if y <= -180.0 else y


def analyse_session(folder, name):
    t0 = time.time()
    log(f"== {name}")
    config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / folder.name
        shutil.copytree(folder, copy)
        obs, excluded, calib, points = load_observations(copy, pnp="iterative", max_pnp_rms_px=1.5)
        states = load_states(copy)
    n = len(obs)
    res = {"session": name, "board": config["board"],
           "environment": {"cv2": cv2.__version__, "numpy": np.__version__, "scipy": scipy.__version__},
           "validPoseCount": n, "excluded": excluded}
    res["parity"] = parity(folder, obs)
    fl = [o.flange for o in obs]
    bo = [o.board_in_camera for o in obs]

    # ---- B Verfahrensvergleich
    log("B Verfahren")
    cf = closed_forms(obs, calib, points, MAIN + EXTRA)
    ok = {k: v for k, v in cf.items() if "he" in v}
    meth = {}
    for k, v in cf.items():
        if "he" not in v:
            meth[k] = v
            continue
        meth[k] = {**he_summary(v["he"]), "reprojectionRmsPx": v["rmsPx"], "boardSpreadRmsMm": v["boardSpreadRmsMm"],
                   "boardSpreadRmsDeg": v["boardSpreadRmsDeg"], "extra": k in EXTRA,
                   "diffToParkNumpy": pose_diff(v["he"], ok["park_numpy"]["he"]) if "park_numpy" in ok else None,
                   "diffToCv2Park": pose_diff(v["he"], ok["park"]["he"]) if "park" in ok else None}
    res["methods"] = meth
    main_ok = [k for k in MAIN if k in ok]
    best = min(main_ok, key=lambda k: ok[k]["rmsPx"])
    refs = {k: refine(obs, calib, points, ok[k]["x"]) for k in main_ok}
    final = refs[best]
    x_final, he_f, B_f = final["x"], final["he"], final["B"]
    lin = refine(obs, calib, points, ok[best]["x"], loss="linear")
    pw = [(pose_diff(refs[a]["he"], refs[b]["he"])) for a in refs for b in refs if a < b]
    res["refinementConvergence"] = {
        "startMethodBest": best,
        "refined": {k: {**tr_rot(v), "converged": v["converged"], "nfev": v["nfev"],
                        "diffToFinalMm": pose_diff(v["he"], he_f)} for k, v in refs.items()},
        "pairwiseMaxDtMm": max((d["dtNormMm"] for d in pw), default=0.0),
        "pairwiseMaxRotDeg": max((d["dRotDeg"] for d in pw), default=0.0),
        "linearLossFromBest": {**tr_rot(lin), "converged": lin["converged"], "nfev": lin["nfev"],
                               "diffToFinal": pose_diff(lin["he"], he_f)}}
    res["finalSolution"] = {**tr_rot(final), **he_summary(he_f), "startMethod": best, "T_base_board": B_f,
                            "boardSpread": _solution_fields(he_f, fl, bo)}

    pid = [o.pose_id for o in obs]
    times = {p: datetime.fromisoformat(states[p]["capturedAt"]).timestamp() for p in pid}
    tmin = min(datetime.fromisoformat(s["capturedAt"]).timestamp() for s in states.values() if "capturedAt" in s)
    order = sorted(range(n), key=lambda i: times[pid[i]])

    # ---- C Sweep
    log("C Sweep")
    sweep, subsets_seed1 = [], {}
    for count in (15, 25, 40, None):
        sets = [tuple(range(n))] if count is None else diverse_subsets(obs, count, repeats=3)
        for si, ids in enumerate(sets):
            sub = [obs[i] for i in ids]
            label = "alle" if count is None else f"{count}#{si}"
            try:
                c2, b2, r2 = refined_from_best(sub, calib, points)
                ent = {"count": len(ids), "label": label, "poseIds": [pid[i] for i in ids], "bestStart": b2,
                       "closed": {k: ({"translationMm": v["he"][:3, 3] * 1000, "rmsPx": v["rmsPx"]}
                                      if "he" in v else v) for k, v in c2.items()},
                       "refined": tr_rot(r2)}
            except Exception as e:  # noqa: BLE001
                ent = {"count": len(ids), "label": label, "error": str(e)}
            sweep.append(ent)
            if si == 0 and count is not None:
                subsets_seed1[count] = ids
    res["sweep"] = sweep

    # ---- D Je-Pose
    log("D Je-Pose")
    dev_f = board_dev(obs, he_f, B_f)
    B_park = _base_board(fl, bo, ok["park"]["he"]) if "park" in ok else None
    dev_p = board_dev(obs, ok["park"]["he"], B_park) if "park" in ok else None
    rows = []
    for i, o in enumerate(obs):
        s = states[o.pose_id]
        r = _proj_model(x_final, [o], calib, points, "").reshape(-1, 2)
        Tc = o.flange @ he_f
        z = Tc[:3, 2]
        row = {"poseId": o.pose_id, "poseGroup": o.pose_group, "capturedAt": s["capturedAt"],
               "tSeconds": times[o.pose_id] - tmin, "accepted": s.get("accepted"),
               "savedReprojectionErrorPx": s.get("reprojectionErrorPx"),
               "pnpRmsPx": o.pnp_rms_px, "cornerCount": int(len(o.corners)),
               "reprojRmsPx": float(np.sqrt(np.mean(np.sum(r ** 2, axis=1)))),
               "meanResidualDxPx": float(r[:, 0].mean()), "meanResidualDyPx": float(r[:, 1].mean()),
               "meanResidualPx": float(np.hypot(r[:, 0].mean(), r[:, 1].mean())),
               "maxResidualPx": float(np.max(np.linalg.norm(r, axis=1))),
               "boardDevXMm": dev_f[i]["vec"][0], "boardDevYMm": dev_f[i]["vec"][1], "boardDevZMm": dev_f[i]["vec"][2],
               "boardDevMm": dev_f[i]["norm"], "boardDevRotDeg": dev_f[i]["rotDeg"],
               "parkBoardDevMm": dev_p[i]["norm"] if dev_p else None,
               "parkBoardDevRotDeg": dev_p[i]["rotDeg"] if dev_p else None,
               "tiltDeg": math.degrees(math.acos(float(np.clip(-z[2], -1, 1)))),
               "tiltAzimuthDeg": math.degrees(math.atan2(z[1], z[0])),
               "yawDeg": yaw_deg(Tc[:3, :3]), "flangeYawDeg": yaw_deg(o.flange[:3, :3]),
               "distanceMm": float(np.linalg.norm(o.board_in_camera[:3, 3]) * 1000)}
        for j, q in enumerate(s["jointAnglesRad"]):
            row[f"q{j + 1}Deg"] = math.degrees(q)
        rows.append(row)
    res["poses"] = rows

    if dev_p:
        rf = float(np.sqrt(np.mean([d["norm"] ** 2 for d in dev_f])))
        rp = float(np.sqrt(np.mean([d["norm"] ** 2 for d in dev_p])))
        items = [{"poseId": pid[i], "group": obs[i].pose_group, "parkDevMm": dev_p[i]["norm"],
                  "finalDevMm": dev_f[i]["norm"], "finalMinusParkMm": dev_f[i]["norm"] - dev_p[i]["norm"]}
                 for i in range(n)]
        res["parkVsFinalBoardDev"] = {
            "definition": "konsistent = Brettlage-Abweichung <= RMS der jeweiligen Loesung (Park gegen gemitteltes B, final gegen B der Verfeinerung)",
            "parkRmsMm": rp, "finalRmsMm": rf,
            "consistentInParkNotInFinal": [it for it in items if it["parkDevMm"] <= rp and it["finalDevMm"] > rf],
            "consistentInFinalNotInPark": [it for it in items if it["finalDevMm"] <= rf and it["parkDevMm"] > rp],
            "top10WorseInFinal": sorted(items, key=lambda d: -d["finalMinusParkMm"])[:10],
            "top10BetterInFinal": sorted(items, key=lambda d: d["finalMinusParkMm"])[:10],
            "spearmanParkVsFinalDev": sp([d["parkDevMm"] for d in items], [d["finalDevMm"] for d in items]),
            "perPose": items}

    # ---- E Einfluss
    log("E LOO")
    loo = []
    for i in range(n):
        r = refine(obs[:i] + obs[i + 1:], calib, points, x_final, max_nfev=100)
        loo.append({"poseId": pid[i], "group": obs[i].pose_group, **pose_diff(r["he"], he_f),
                    "dRmsPx": r["rmsPx"] - final["rmsPx"], "converged": r["converged"]})
    res["leaveOneOut"] = {"perPose": loo, "top10ByDtNorm": sorted(loo, key=lambda d: -d["dtNormMm"])[:10]}
    log("E Gruppen/Zeit")
    groups = sorted({o.pose_group for o in obs})
    gl = {}
    for g in groups:
        for kind in ("without", "only"):
            sub = [o for o in obs if (o.pose_group != g) == (kind == "without")]
            if len(sub) < 6:
                gl[f"{kind}_{g}"] = {"n": len(sub), "skipped": "weniger als 6 Posen"}
                continue
            try:
                r = refine(sub, calib, points, x_final)
                gl[f"{kind}_{g}"] = {"n": len(sub), **tr_rot(r), "diffToFinal": pose_diff(r["he"], he_f),
                                     "converged": r["converged"]}
            except Exception as e:  # noqa: BLE001
                gl[f"{kind}_{g}"] = {"n": len(sub), "error": str(e)}
    res["groups"] = gl
    temporal, h = {}, n // 2
    for label, ids in (("firstHalf", order[:h]), ("secondHalf", order[h:])):
        r = refine([obs[i] for i in ids], calib, points, x_final)
        temporal[label] = {"n": len(ids), **tr_rot(r), "diffToFinal": pose_diff(r["he"], he_f)}
    temporal["cumulative"] = [
        {"k": k, **tr_rot(refine([obs[i] for i in order[:k]], calib, points, x_final))}
        for k in list(range(10, n, 5)) + [n]]
    res["temporal"] = temporal
    looD = {d["poseId"]: d for d in loo}
    expl = {}
    for count, ids in subsets_seed1.items():
        added = [{"poseId": pid[i], "group": obs[i].pose_group, "looDtNormMm": looD[pid[i]]["dtNormMm"],
                  "looDtMm": looD[pid[i]]["dtMm"]} for i in range(n) if i not in ids]
        expl[str(count)] = {"added": added,
                            "groupCounts": {g: sum(1 for a in added if a["group"] == g) for g in groups}}
    res["sweepExplanation"] = expl
    preds = ["tiltDeg", "yawDeg", "tSeconds", "distanceMm", "pnpRmsPx"]
    res["spearman"] = {c: {p: sp([r[c] for r in rows], [r[p] for r in rows]) for p in preds}
                       for c in ("reprojRmsPx", "boardDevMm")}
    gs = {}
    for g in groups:
        rr = [r for r in rows if r["poseGroup"] == g]
        yaws = [r["yawDeg"] for r in rr]
        gs[g] = {"n": len(rr), "medianReprojRmsPx": float(np.median([r["reprojRmsPx"] for r in rr])),
                 "maxReprojRmsPx": max(r["reprojRmsPx"] for r in rr),
                 "medianTiltDeg": float(np.median([r["tiltDeg"] for r in rr])),
                 "yawSpanDeg": max(yaws) - min(yaws),
                 "timeWindowS": [min(r["tSeconds"] for r in rr), max(r["tSeconds"] for r in rr)]}
    res["groupSummary"] = gs

    # ---- F Diagnose Intrinsik / Massstab
    log("F Intrinsik/Massstab")
    diag = {}
    for key, mode in (("intrinsicsFull", "KD"), ("distortionOnly", "D"), ("scaleOnly", "S"),
                      ("intrinsicsAndScale", "KDS")):
        r = diag_fit(obs, calib, points, x_final, mode)
        r["diffToFinal"] = pose_diff(r["T_flange_camera"], he_f)
        diag[key] = r
    res["diagnoseIntrinsics"] = {"baseline": tr_rot(final), "note": "Reine Diagnose, nichts gespeichert", **diag}

    # ---- G Roboter
    log("G Roboter")
    res["robotDiagnosis"] = {"spearmanBoardDevVsJoint": {
        axis: {f"q{j}": sp([r[key] for r in rows], [r[f"q{j}Deg"] for r in rows]) for j in range(1, 8)}
        for axis, key in (("x", "boardDevXMm"), ("y", "boardDevYMm"), ("z", "boardDevZMm"), ("norm", "boardDevMm"))}}
    res["runtimeS"] = time.time() - t0
    return res


def write_csv(path, rows):
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if v is None else (f"{v:.6g}" if isinstance(v, float) else v)
                        for v in (r[c] for c in cols)])


# ---------------------------------------------------------------- Text

def v3(v):
    return "(" + ", ".join(f"{x:.2f}" for x in v) + ")"


def fr(v, f="{:.2f}"):
    return f.format(v) if v is not None else "n/a"


def summary(res):
    L = []
    a = L.append
    a(f"##### {res['session']}  Brett {res['board']['type']} {res['board']['cols']}x{res['board']['rows']}  "
      f"gueltig {res['validPoseCount']}  ausgeschlossen {len(res['excluded'])}  Laufzeit {res['runtimeS']:.0f}s  "
      f"env {res['environment']}")
    for e in res["excluded"]:
        a(f"  ausgeschlossen {e['poseId']}: {e['reason']}")
    p = res["parity"]
    if p.get("available"):
        a(f"Paritaet: gleiche Posemenge {p['samePoseSet']} (nur gespeichert {p['onlyInSaved']}, nur neu {p['onlyInNew']}), "
          f"max Eckenabw. {p['maxCornerDevPx']} px, max |dPnpRms| {p['maxAbsPnpRmsDiffPx']} px, Eck-IDs gleich {p['allCornerIdsEqual']}")
    a("Verfahren: t[mm] | rotvec[deg] | winkel | RMS px | spreadMm/Grad | zu park_numpy | zu cv2-park")
    for k, m in res["methods"].items():
        if "error" in m:
            a(f"  {k}: FEHLER {m['error']}")
            continue
        a(f"  {k}{' (extra)' if m['extra'] else ''}: {v3(m['translationMm'])} | {v3(m['rotvecDeg'])} | "
          f"{m['rotationDeg']:.2f} | {m['reprojectionRmsPx']:.3f} | {m['boardSpreadRmsMm']:.2f}/{m['boardSpreadRmsDeg']:.2f} | "
          f"{m['diffToParkNumpy']['dtNormMm']:.2f}mm {m['diffToParkNumpy']['dRotDeg']:.2f}deg | "
          f"{m['diffToCv2Park']['dtNormMm']:.2f}mm {m['diffToCv2Park']['dRotDeg']:.2f}deg")
    c = res["refinementConvergence"]
    a(f"Verfeinerung soft_l1 je Start (bester Start: {c['startMethodBest']}):")
    for k, r in c["refined"].items():
        a(f"  {k}: {v3(r['translationMm'])} rot {r['rotationDeg']:.2f} RMS {r['rmsPx']:.3f} conv {r['converged']} "
          f"nfev {r['nfev']} dt zu final {r['diffToFinalMm']['dtNormMm']:.3f}mm")
    a(f"  paarweise max dt {c['pairwiseMaxDtMm']:.3f} mm, max drot {c['pairwiseMaxRotDeg']:.3f} deg")
    l = c["linearLossFromBest"]
    a(f"  loss=linear: {v3(l['translationMm'])} rot {l['rotationDeg']:.2f} RMS {l['rmsPx']:.3f} dt zu final {l['diffToFinal']['dtNormMm']:.2f} mm")
    f = res["finalSolution"]
    a(f"FINAL: {v3(f['translationMm'])} mm, rotvec {v3(f['rotvecDeg'])} deg, RMS {f['rmsPx']:.3f}")
    a("Sweep (roh je Verfahren | refined):")
    for s in res["sweep"]:
        if "error" in s:
            a(f"  {s['label']}: FEHLER {s['error']}")
            continue
        cl = " ".join(f"{k}={v3(v['translationMm'])}" if "translationMm" in v else f"{k}=ERR" for k, v in s["closed"].items())
        a(f"  {s['label']} (n={s['count']}): {cl} | refined {v3(s['refined']['translationMm'])} RMS {s['refined']['rmsPx']:.3f}")
    a("Top-10 LOO nach dt:")
    for d in res["leaveOneOut"]["top10ByDtNorm"]:
        a(f"  {d['poseId']} {d['group']}: dt {v3(d['dtMm'])} |{d['dtNormMm']:.3f}| mm, drot {d['dRotDeg']:.3f}, dRMS {d['dRmsPx']:+.4f}")
    a("Gruppen (ohne/nur):")
    for k, g in res["groups"].items():
        if "translationMm" in g:
            a(f"  {k} n={g['n']}: {v3(g['translationMm'])} rot {g['rotationDeg']:.2f} RMS {g['rmsPx']:.3f} dt zu final {g['diffToFinal']['dtNormMm']:.2f}")
        else:
            a(f"  {k} n={g['n']}: {g.get('skipped') or g.get('error')}")
    t = res["temporal"]
    for k in ("firstHalf", "secondHalf"):
        a(f"  {k} n={t[k]['n']}: {v3(t[k]['translationMm'])} RMS {t[k]['rmsPx']:.3f}")
    a("  kumulativ: " + "; ".join(f"k={r['k']} {v3(r['translationMm'])}" for r in t["cumulative"]))
    a("Sweep-Erklaerung (hinzukommende Posen je Gruppe; groesste LOO-dt):")
    for cnt, e in res["sweepExplanation"].items():
        top = sorted(e["added"], key=lambda d: -d["looDtNormMm"])[:3]
        a(f"  {cnt}->alle: +{len(e['added'])} Gruppen {e['groupCounts']}; Top LOO: " +
          ", ".join(f"{d['poseId']}({d['group']}) {d['looDtNormMm']:.2f}mm" for d in top))
    a("Spearman rho (p):")
    for cn, d in res["spearman"].items():
        a(f"  {cn}: " + ", ".join(f"{k} {v['rho']:.2f} ({v['p']:.3f})" if v["rho"] is not None else f"{k} n/a" for k, v in d.items()))
    a("Gruppen-Zusammenfassung:")
    for g, d in res["groupSummary"].items():
        a(f"  {g}: n={d['n']} medRMS {d['medianReprojRmsPx']:.3f} maxRMS {d['maxReprojRmsPx']:.3f} medTilt {d['medianTiltDeg']:.1f} "
          f"yawSpan {d['yawSpanDeg']:.1f} t {d['timeWindowS'][0]:.0f}-{d['timeWindowS'][1]:.0f}s")
    a("Intrinsik-/Massstab-Diagnose:")
    b = res["diagnoseIntrinsics"]["baseline"]
    a(f"  baseline: {v3(b['translationMm'])} RMS {b['rmsPx']:.3f}")
    for k in ("intrinsicsFull", "distortionOnly", "scaleOnly", "intrinsicsAndScale"):
        d = res["diagnoseIntrinsics"][k]
        a(f"  {k}: RMS {d['startRmsPx']:.3f}->{d['rmsPx']:.3f} t {v3(d['translationMm'])} rot {d['rotationDeg']:.2f} "
          f"dt zu final {d['diffToFinal']['dtNormMm']:.2f}mm conv {d['converged']}")
        a("    " + ", ".join(f"{n}={v:.5g} (d {d['delta'][n]:+.4g})" for n, v in d["params"].items()))
    pv = res.get("parkVsFinalBoardDev")
    if pv:
        a(f"Brettlage-Abweichung Park RMS {pv['parkRmsMm']:.2f} mm vs final {pv['finalRmsMm']:.2f} mm; "
          f"Spearman Park/final rho {fr(pv['spearmanParkVsFinalDev']['rho'])}")
        a("  konsistent Park, nicht final: " + ", ".join(f"{d['poseId']}({d['group']}) {d['parkDevMm']:.1f}->{d['finalDevMm']:.1f}" for d in pv["consistentInParkNotInFinal"]))
        a("  konsistent final, nicht Park: " + ", ".join(f"{d['poseId']}({d['group']}) {d['parkDevMm']:.1f}->{d['finalDevMm']:.1f}" for d in pv["consistentInFinalNotInPark"]))
        a("  Top-5 schlechter in final: " + ", ".join(f"{d['poseId']}({d['group']}) {d['finalMinusParkMm']:+.1f}" for d in pv["top10WorseInFinal"][:5]))
        a("  Top-5 besser in final: " + ", ".join(f"{d['poseId']}({d['group']}) {d['finalMinusParkMm']:+.1f}" for d in pv["top10BetterInFinal"][:5]))
    a("Roboter: Spearman Brettabw.(Norm) vs Gelenk rho: " + ", ".join(
        f"{j} {fr(v['rho'])}" for j, v in res["robotDiagnosis"]["spearmanBoardDevVsJoint"]["norm"].items()))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "docs/auswertung/handeye_cv4"))
    ap.add_argument("--session", action="append", help="Sitzungsordner (mehrfach); Default: beide Standardsitzungen")
    args = ap.parse_args()
    sessions = [Path(s) if Path(s).is_absolute() else ROOT / s for s in (args.session or DEFAULT_SESSIONS)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for folder in sessions:
        res = clean(analyse_session(folder, folder.name))
        results[folder.name] = res
        (out / f"{folder.name}.json").write_text(json.dumps(res, indent=1, allow_nan=False) + "\n", encoding="utf-8")
        write_csv(out / f"{folder.name}_posen.csv", res["poses"])
        print(summary(res))
        print()
    comp = {}
    for k, r in results.items():
        comp[k] = {"board": r["board"], "validPoseCount": r["validPoseCount"], "excludedCount": len(r["excluded"]),
                   "environment": r["environment"],
                   "parity": {x: r["parity"].get(x) for x in ("samePoseSet", "maxCornerDevPx", "maxAbsPnpRmsDiffPx")},
                   "methods": {m: {"translationMm": v.get("translationMm"), "rmsPx": v.get("reprojectionRmsPx")}
                               for m, v in r["methods"].items()},
                   "refined": {m: {"translationMm": v["translationMm"], "rmsPx": v["rmsPx"]}
                               for m, v in r["refinementConvergence"]["refined"].items()},
                   "final": {"translationMm": r["finalSolution"]["translationMm"], "rmsPx": r["finalSolution"]["rmsPx"],
                             "startMethod": r["finalSolution"]["startMethod"]},
                   "sweep": [{"label": s["label"], "refined": s.get("refined", {}).get("translationMm"),
                              "park": s.get("closed", {}).get("park", {}).get("translationMm")} for s in r["sweep"]],
                   "diagnoseIntrinsics": {x: {"rmsPx": r["diagnoseIntrinsics"][x]["rmsPx"],
                                              "translationMm": r["diagnoseIntrinsics"][x]["translationMm"]}
                                          for x in ("intrinsicsFull", "distortionOnly", "scaleOnly", "intrinsicsAndScale")}}
    (out / "vergleich.json").write_text(json.dumps(comp, indent=1, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
