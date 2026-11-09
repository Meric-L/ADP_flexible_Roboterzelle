"""Drift-Test: starres Hand-Auge-Modell gegen Modelle mit zeitlicher Drift.

Nimmt die Je-Pose-Fehler als starren Versatz ernst und prueft, ob er sich
durch eine gleichmaessige Bewegung ueber die Aufnahmezeit erklaeren laesst:

* ``rigid``      -- X = T_flange_camera und B = T_base_board fest (wie bisher).
* ``boardDrift`` -- B wandert linear mit der Zeit (6 Raten: Brett oder Basis).
* ``mountDrift`` -- X wandert linear mit der Zeit (Kamerahalterung am Flansch).

Fuer jedes Modell: Gesamt-RMS, RMS erste/zweite Haelfte, Hand-Auge-Translation
(bei Drift: zur Sitzungsmitte), Driftbetrag ueber die Sitzung, und die
Translation ueber die D-optimalen Teilmengen (15/25/40/alle). Nur Auswertung,
nichts wird gespeichert oder aktiviert.

Aufruf (separate Umgebung, siehe requirements.txt):
    PYTHONPATH=src python tools/handeye_offline_cv4/drift.py [--out docs/auswertung/handeye_cv4]
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

from tagloc import geometry, lens
from tagloc.handeye_offline import (
    _base_board, _pose_from_vector, _vector_from_pose, diverse_subsets, load_observations,
    solve_subset,
)

ROOT = Path(__file__).resolve().parents[2]
SESSIONS = [
    ROOT / "data/rohbilder/calibration/20261109T125050Z_a5c964f0",
    ROOT / "data/rohbilder/calibration/20261109T125825Z_a580eaba",
]


def _times(folder, obs):
    stamps = {}
    for o in obs:
        state = json.loads((folder / "poses" / o.pose_id / "robot_state.json").read_text())
        stamps[o.pose_id] = datetime.fromisoformat(state["capturedAt"]).timestamp()
    t = np.array([stamps[o.pose_id] for o in obs])
    return t - 0.5 * (t.min() + t.max())  # Sitzungsmitte = 0


def _increment(rate, t):
    """Lineare Drift als Pose: Rotationsvektor und Translation, je mal t."""
    return _pose_from_vector(rate * t)


def _residuals(values, obs, times, calib, points, model):
    handeye, board = _pose_from_vector(values[:6]), _pose_from_vector(values[6:12])
    rate = values[12:18] if model != "rigid" else None
    chunks = []
    for o, t in zip(obs, times):
        x_i = handeye @ _increment(rate, t) if model == "mountDrift" else handeye
        b_i = board @ _increment(rate, t) if model == "boardDrift" else board
        camera_board = geometry.invert(geometry.compose(o.flange, x_i)) @ b_i
        projected = lens.project_points(
            o.object_points if o.object_points is not None else points,
            geometry.rvec_from_rotation(camera_board[:3, :3]), camera_board[:3, 3], calib)
        chunks.append((projected - o.corners).reshape(-1))
    return np.concatenate(chunks)


def _per_pose_rms(residual, obs):
    out, start = [], 0
    for o in obs:
        n = 2 * len(o.corners)
        part = residual[start:start + n]
        out.append(float(np.sqrt(np.mean(part ** 2) * 2)))
        start += n
    return np.array(out)


def fit(obs, times, calib, points, model, start=None):
    if start is None:
        result = solve_subset(obs, calib, points)
        handeye = np.asarray(result["optimized"]["T_flange_camera"])
        board = np.asarray(result["optimized"]["T_base_board"])
        start = np.r_[_vector_from_pose(handeye), _vector_from_pose(board)]
    x0 = np.r_[start[:12], np.zeros(6)] if model != "rigid" else start[:12]
    fitted = least_squares(_residuals, x0, args=(obs, times, calib, points, model),
                           loss="soft_l1", f_scale=1.0, max_nfev=300, x_scale="jac")
    residual = _residuals(fitted.x, obs, times, calib, points, model)
    per_pose = _per_pose_rms(residual, obs)
    span = float(times.max() - times.min())
    out = {
        "model": model,
        "reprojectionRmsPx": float(np.sqrt(np.mean(residual ** 2) * 2)),
        "rmsFirstHalfPx": float(np.sqrt(np.mean(per_pose[times <= 0] ** 2))),
        "rmsSecondHalfPx": float(np.sqrt(np.mean(per_pose[times > 0] ** 2))),
        "translationMm": (_pose_from_vector(fitted.x[:6])[:3, 3] * 1000).tolist(),
        "converged": bool(fitted.success),
        "parameters": int(len(fitted.x)),
        "perPoseRmsPx": dict(zip([o.pose_id for o in obs], per_pose.round(4).tolist())),
    }
    if model != "rigid":
        rate = fitted.x[12:18]
        out["driftOverSessionMm"] = (rate[3:] * span * 1000).tolist()
        out["driftOverSessionNormMm"] = float(np.linalg.norm(rate[3:]) * span * 1000)
        out["driftOverSessionDeg"] = float(np.degrees(np.linalg.norm(rate[:3]) * span))
    return out, fitted.x


def analyse(folder):
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / folder.name
        shutil.copytree(folder, copy)
        obs, _, calib, points = load_observations(copy, pnp="iterative", max_pnp_rms_px=1.5)
    times = _times(folder, obs)
    models, starts = {}, {}
    for model in ("rigid", "boardDrift", "mountDrift"):
        models[model], starts[model] = fit(obs, times, calib, points, model)
    # Translation ueber dieselben D-optimalen Teilmengen wie in handeye_offline.
    sweep = []
    subsets = [(c, idx) for c in (15, 25, 40) for idx in diverse_subsets(obs, c, 3)]
    subsets.append((len(obs), tuple(range(len(obs)))))
    for count, idx in subsets:
        sub = [obs[i] for i in idx]
        entry = {"poseCount": count}
        for model in ("rigid", "boardDrift"):
            sol, _ = fit(sub, times[list(idx)], calib, points, model, start=starts["rigid"])
            entry[model] = {"translationMm": sol["translationMm"],
                            "reprojectionRmsPx": sol["reprojectionRmsPx"]}
        sweep.append(entry)
    return {"session": folder.name, "poseCount": len(obs),
            "sessionSpanS": float(times.max() - times.min()),
            "models": models, "sweep": sweep}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "docs/auswertung/handeye_cv4")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = [analyse(folder) for folder in SESSIONS]
    (args.out / "drift.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for r in report:
        print(f"== {r['session']} ({r['poseCount']} Posen, {r['sessionSpanS']:.0f} s)")
        for m in r["models"].values():
            drift = (f"  Drift {m['driftOverSessionNormMm']:.2f} mm / {m['driftOverSessionDeg']:.3f} deg "
                     f"{np.round(m['driftOverSessionMm'], 2)}" if "driftOverSessionMm" in m else "")
            print(f"  {m['model']:10s} RMS {m['reprojectionRmsPx']:.3f} px "
                  f"(1. Haelfte {m['rmsFirstHalfPx']:.3f}, 2. {m['rmsSecondHalfPx']:.3f}) "
                  f"t {np.round(m['translationMm'], 2)}{drift}")
        for s in r["sweep"]:
            print(f"  Sweep {s['poseCount']:2d}: starr {np.round(s['rigid']['translationMm'], 2)} "
                  f"| Brettdrift {np.round(s['boardDrift']['translationMm'], 2)}")


if __name__ == "__main__":
    main()
