"""Kinematik-Experiment 2: identifizierbares Kinematikmodell: dq2..dq6, dd3, dd5, da4, da5, da7; alle Sitzungen, Leave-one-session-out.

Aufruf aus der Repo-Wurzel: PYTHONPATH=src python tools/handeye_offline_cv4/kinematik/ba2.py
Ergebnis vom 09.11.2026: doc/auswertung/handeye_cv4/kinematik_ergebnis.md
"""
import json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
sys.path.insert(0, str(Path(__file__).parent))
import kin
from kin import load, S, ROOT
from tagloc import geometry, lens
from tagloc.handeye_offline import _pose_from_vector, _vector_from_pose
from ba import E, rms

QI = [1, 2, 3, 4, 5]          # Gelenke 2..6
def fk2(q, p):
    dq = np.zeros(7); dq[QI] = p[:5]
    dd = np.zeros(7); dd[2] = p[5]; dd[4] = p[6]
    da = np.zeros(7); da[3] = p[7]; da[4] = p[8]; da[6] = p[9]
    T = np.eye(4)
    for i in range(7):
        T = T @ kin.tf(kin.A[i] + da[i], kin.D[i] + dd[i], kin.AL[i], q[i] + dq[i])
    return T @ kin.tf(0, 0.107, 0, 0) @ E
NP = 10

def residual(v, data, kinfree, pfix):
    X = _pose_from_vector(v[:6]); ns = len(data)
    Bs = [_pose_from_vector(v[6+6*i:12+6*i]) for i in range(ns)]
    p = v[6+6*ns:6+6*ns+NP] if kinfree else pfix
    out = []
    for (obs, cal), B in zip(data, Bs):
        for o in obs:
            cb = geometry.invert(fk2(o["q"], p) @ X) @ B
            pr = lens.project_points(o["P"], geometry.rvec_from_rotation(cb[:3, :3]), cb[:3, 3], cal)
            out.append((pr - o["uv"]).reshape(-1))
    return np.concatenate(out)

def solve(keys, kinfree, pfix=np.zeros(NP)):
    data = [load(k) for k in keys]
    X = np.array(json.loads((ROOT / f"doc/auswertung/handeye_cv4/{S[keys[0]]}.json").read_text())["finalSolution"]["T_flange_camera"])
    v = list(_vector_from_pose(X))
    for obs, cal in data:
        v += list(_vector_from_pose(geometry.average_poses([fk2(o["q"], pfix) @ X @ o["Tcb"] for o in obs])))
    if kinfree: v += [0.0] * NP
    res = least_squares(residual, np.array(v), args=(data, kinfree, pfix), loss="soft_l1", x_scale="jac")
    r = residual(res.x, data, kinfree, pfix)
    p = res.x[6+6*len(keys):] if kinfree else pfix
    return dict(rms=rms(r), X=_pose_from_vector(res.x[:6]), p=p)

if __name__ == "__main__":
    allk = ["S1", "S2", "S3", "S4"]
    a = solve(allk, False); b = solve(allk, True)
    print("alle 4: RMS starr %.3f -> Kinematik %.3f px" % (a["rms"], b["rms"]))
    print("  p: dq2..6 mrad", np.round(b["p"][:5]*1000, 2), "dd3,dd5 mm", np.round(b["p"][5:7]*1000, 2), "da4,da5,da7 mm", np.round(b["p"][7:]*1000, 2))
    print("  t_X starr", np.round(a["X"][:3, 3]*1000, 2), " mit Kinematik", np.round(b["X"][:3, 3]*1000, 2))
    Xs = {}
    for test in allk:
        train = [k for k in allk if k != test]
        t = solve(train, True)
        r0 = solve([test], False); r1 = solve([test], False, pfix=t["p"])
        Xs[test] = (r0["X"][:3, 3]*1000, r1["X"][:3, 3]*1000)
        print("LOSO test %s: RMS starr %.3f -> mit Trainings-Kinematik %.3f px; t_X starr %s -> %s" % (test, r0["rms"], r1["rms"], np.round(Xs[test][0], 2), np.round(Xs[test][1], 2)))
    for j, name in ((0, "starr"), (1, "Kinematik")):
        P = np.array([Xs[k][j] for k in allk]); d = max(np.linalg.norm(P[i]-P[k]) for i in range(4) for k in range(4))
        print("Spannweite Hand-Auge-Translation (max. Paarabstand) %s: %.2f mm" % (name, d))
