"""Kinematik-Experiment 3: Hand-Auge offline mit verschiedenen Intrinsiken, optional Brettmassstab, Verzeichnung, Kinematik.

WERK = Werks-Intrinsik der RealSense D415 (Seriennr. 002422062052), Color 640x480, ausgelesen
mit rs-enumerate-devices -c am 10.11.2026 (Verzeichnung Inverse Brown-Conrady, alle 0).
Aufruf aus der Repo-Wurzel: PYTHONPATH=src python tools/handeye_offline_cv4/kinematik/intr.py
Ergebnis: doc/auswertung/handeye_cv4/intrinsik_ergebnis.md
"""
import json, sys
from pathlib import Path
import numpy as np, cv2
from scipy.optimize import least_squares
sys.path.insert(0, str(Path(__file__).parent))
import kin
from kin import load, S, ROOT
from ba import E
from ba2 import fk2, NP
from tagloc import geometry
from tagloc.handeye_offline import _pose_from_vector, _vector_from_pose

WERK = np.array([[608.81201171875, 0, 331.596038818359], [0, 607.105346679688, 243.391510009766], [0, 0, 1]])
BOARD = {"S1": "charuco", "S2": "schach", "S3": "schach", "S4": "charuco"}

def rms(r): return float(np.sqrt(np.mean(np.sum(r.reshape(-1, 2) ** 2, 1))))

def run(keys, K, dist0, *, free_scale=False, free_dist=False, kinfree=False, pfix=None, fixed_scale=None):
    data = [load(k)[0] for k in keys]
    boards = sorted({BOARD[k] for k in keys})
    X0 = np.array(json.loads((ROOT / f"doc/auswertung/handeye_cv4/{S[keys[0]]}.json").read_text())["finalSolution"]["T_flange_camera"])
    pf = np.zeros(NP) if pfix is None else pfix
    v = list(_vector_from_pose(X0))
    for obs in data:
        Bs = []
        for o in obs:
            ok, rv, tv = cv2.solvePnP(o["P"], o["uv"], K, dist0)
            Tcb = np.eye(4); Tcb[:3, :3] = cv2.Rodrigues(rv)[0]; Tcb[:3, 3] = tv.ravel()
            Bs.append(fk2(o["q"], pf) @ X0 @ Tcb)
        v += list(_vector_from_pose(geometry.average_poses(Bs)))
    n0 = len(v)
    if free_scale: v += [0.0] * len(boards)
    if free_dist: v += [0.0, 0.0]
    if kinfree: v += [0.0] * NP
    def unpack(x):
        i = n0; sc = {b: (fixed_scale or {}).get(b, 1.0) for b in boards}
        if free_scale:
            for b in boards: sc[b] = 1 + x[i]; i += 1
        d = dist0.copy()
        if free_dist: d = d.copy(); d[0] += x[i]; d[1] += x[i + 1]; i += 2
        p = x[i:i + NP] if kinfree else pf
        return sc, d, p
    def res(x):
        X = _pose_from_vector(x[:6]); sc, d, p = unpack(x); out = []
        for si, (k, obs) in enumerate(zip(keys, data)):
            B = _pose_from_vector(x[6 + 6 * si:12 + 6 * si])
            for o in obs:
                cb = geometry.invert(fk2(o["q"], p) @ X) @ B
                pr = cv2.projectPoints(o["P"] * sc[BOARD[k]], cv2.Rodrigues(cb[:3, :3])[0], cb[:3, 3], K, d)[0].reshape(-1, 2)
                out.append((pr - o["uv"]).reshape(-1))
        return np.concatenate(out)
    r = least_squares(res, np.array(v), loss="soft_l1", x_scale="jac")
    sc, d, p = unpack(r.x)
    return dict(rms=rms(res(r.x)), t=_pose_from_vector(r.x[:6])[:3, 3] * 1000, X=_pose_from_vector(r.x[:6]), scale=sc, dist=d, p=p)

if __name__ == "__main__":
    alt_cfg = json.loads((kin.C / S["S1"] / "config.json").read_text())["intrinsics"]
    KA, DA = np.array(alt_cfg["cameraMatrix"]), np.array(alt_cfg["distortionCoefficients"])
    Z = np.zeros(5)
    keys = ["S1", "S2", "S3", "S4"]
    print("== je Sitzung starr: RMS px | t_X mm")
    T = {}
    for name, K, D, kw in (("alt", KA, DA, {}), ("werk", WERK, Z, {}), ("werk+Massstab frei", WERK, Z, dict(free_scale=True)),
                           ("werk+k1k2 frei", WERK, Z, dict(free_dist=True)), ("werk+Massstab+k1k2", WERK, Z, dict(free_scale=True, free_dist=True))):
        T[name] = []
        for k in keys:
            r = run([k], K, D, **kw); T[name].append(r["t"])
            extra = (" scale %s" % {b: round(v, 5) for b, v in r["scale"].items()}) if kw.get("free_scale") else ""
            extra += (" k1 %.3f k2 %.3f" % (r["dist"][0], r["dist"][1])) if kw.get("free_dist") else ""
            print("  %-22s %s RMS %.3f  t %s%s" % (name, k, r["rms"], np.round(r["t"], 2), extra))
        P = np.array(T[name]); print("  %-22s Spannweite t_X (max Paarabstand) %.2f mm" % (name, max(np.linalg.norm(P[i] - P[j]) for i in range(4) for j in range(4))))
    print("== Werk + Kinematik, Leave-one-session-out")
    for name, K, D in (("alt", KA, DA), ("werk", WERK, Z)):
        ts = []
        for test in keys:
            tr = run([k for k in keys if k != test], K, D, kinfree=True)
            r0 = run([test], K, D); r1 = run([test], K, D, pfix=tr["p"])
            ts.append(r1["t"])
            print("  %-5s test %s: RMS starr %.3f -> mit Kinematik %.3f  t %s" % (name, test, r0["rms"], r1["rms"], np.round(r1["t"], 2)))
        P = np.array(ts); print("  %-5s Spannweite t_X mit Kinematik %.2f mm" % (name, max(np.linalg.norm(P[i] - P[j]) for i in range(4) for j in range(4))))
