"""Kinematik-Experiment 1 (ueberparametrisiert, nur als Indiz): Buendelausgleich: X (Hand-Auge), B je Sitzung, optional Gelenkoffsets dq (7) und Laengen dd (7)."""
import json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
sys.path.insert(0, str(Path(__file__).parent))
from kin import load, fk, S, ROOT
from tagloc import geometry, lens
from tagloc.handeye_offline import _pose_from_vector, _vector_from_pose

E = np.eye(4); c, s = np.cos(np.radians(-45)), np.sin(np.radians(-45)); E[:2, :2] = [[c, -s], [s, c]]; E[2, 3] = -0.0002

def flange(q, dq, dd): return fk(q, dq, dd) @ E

def unpack(v, ns, mode):
    X = _pose_from_vector(v[:6]); Bs = [_pose_from_vector(v[6 + 6*i:12 + 6*i]) for i in range(ns)]
    k = 6 + 6*ns
    dq = v[k:k+7] if mode in ("dq", "dqdd") else np.zeros(7)
    dd = v[k+7:k+14] if mode == "dqdd" else np.zeros(7)
    return X, Bs, dq, dd

def residual(v, data, mode, fixed=None):
    X, Bs, dq, dd = unpack(v, len(data), mode)
    if fixed is not None: dq, dd = fixed
    out = []
    for (obs, cal), B in zip(data, Bs):
        for o in obs:
            cb = geometry.invert(flange(o["q"], dq, dd) @ X) @ B
            pr = lens.project_points(o["P"], geometry.rvec_from_rotation(cb[:3, :3]), cb[:3, 3], cal)
            out.append((pr - o["uv"]).reshape(-1))
    return np.concatenate(out)

def rms(r): return float(np.sqrt(np.mean(np.sum(r.reshape(-1, 2) ** 2, 1))))

def start(keys, data):
    he = json.loads((ROOT / f"doc/auswertung/handeye_cv4/{S[keys[0]]}.json").read_text())["finalSolution"]
    X = np.array(he["T_flange_camera"])
    v = list(_vector_from_pose(X))
    for k, (obs, cal) in zip(keys, data):
        B = geometry.average_poses([flange(o["q"], np.zeros(7), np.zeros(7)) @ X @ o["Tcb"] for o in obs])
        v += list(_vector_from_pose(B))
    return np.array(v)

def solve(keys, mode, fixed=None, loss="soft_l1"):
    data = [load(k) for k in keys]
    v0 = start(keys, data)
    extra = {"rigid": 0, "dq": 7, "dqdd": 14}[mode] if fixed is None else 0
    v0 = np.r_[v0, np.zeros(extra)]
    m = mode if fixed is None else "rigid"
    res = least_squares(residual, v0, args=(data, m, fixed), loss=loss, f_scale=1.0, x_scale="jac")
    r = residual(res.x, data, m, fixed)
    X, Bs, dq, dd = unpack(res.x, len(data), m)
    # Brettstreuung mm je Sitzung
    spread = []
    for (obs, cal), B in zip(data, Bs):
        dqq, ddd = (fixed if fixed is not None else (dq, dd))
        rec = [flange(o["q"], dqq, ddd) @ X @ o["Tcb"] for o in obs]
        spread.append(float(np.sqrt(np.mean([(1000 * np.linalg.norm(p[:3, 3] - B[:3, 3])) ** 2 for p in rec]))))
    return dict(rms=rms(r), X=X, dq=dq, dd=dd, spread=spread, x=res.x)

if __name__ == "__main__":
    for keys in (["S1"], ["S2"], ["S3"], ["S4"]):
        a = solve(keys, "rigid"); b = solve(keys, "dq"); c = solve(keys, "dqdd")
        print(keys, "RMS starr %.3f | +dq %.3f | +dq+dd %.3f px   Brettstreuung %.2f | %.2f | %.2f mm" % (a["rms"], b["rms"], c["rms"], a["spread"][0], b["spread"][0], c["spread"][0]))
        print("     dq mrad", np.round(b["dq"] * 1000, 2), " t_X mm", np.round(b["X"][:3, 3] * 1000, 2), "(starr", np.round(a["X"][:3, 3] * 1000, 2), ")")
    # gemeinsam: alle vier Sitzungen, gemeinsames X und dq, Brett je Sitzung
    for keys in (["S3", "S4"], ["S1", "S2", "S3", "S4"]):
        a = solve(keys, "rigid"); b = solve(keys, "dq"); c = solve(keys, "dqdd")
        print("gemeinsam", keys, "RMS starr %.3f | +dq %.3f | +dq+dd %.3f" % (a["rms"], b["rms"], c["rms"]), "Streuung starr", np.round(a["spread"], 2), "+dq", np.round(b["spread"], 2), "+dqdd", np.round(c["spread"], 2))
        print("     dq mrad", np.round(b["dq"] * 1000, 2), "| dqdd: dq", np.round(c["dq"] * 1000, 2), "dd mm", np.round(c["dd"] * 1000, 2), " t_X", np.round(c["X"][:3, 3] * 1000, 2))
    # Kreuzvalidierung: Offsets aus S1+S2+S3, angewandt auf S4 (nur X,B neu)
    for train, test in ((["S1", "S2", "S3"], "S4"), (["S1", "S2", "S4"], "S3"), (["S3"], "S4"), (["S4"], "S3")):
        b = solve(train, "dqdd")
        r0 = solve([test], "rigid"); r1 = solve([test], "rigid", fixed=(b["dq"], b["dd"]))
        print("CV train", train, "-> test", test, "RMS starr %.3f -> mit Trainings-Offsets %.3f px; Streuung %.2f -> %.2f mm; t_X %s" % (r0["rms"], r1["rms"], r0["spread"][0], r1["spread"][0], np.round(r1["X"][:3, 3] * 1000, 2)))
