"""Aufruf aus der Repo-Wurzel: PYTHONPATH=src python tools/handeye_offline_cv4/laeufe_vergleich.py <Sitzung> <Sitzung> ...

Vergleich mehrerer 1080p-Kalibrierlaeufe: Intrinsik und Hand-Auge je Lauf, Streuung zwischen den Laeufen."""
import json, sys
from pathlib import Path
import numpy as np, cv2
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation as Rot
from tagloc import geometry
from tagloc.handeye_offline import _pose_from_vector, _vector_from_pose

RUNS = sys.argv[1:]
F = 274.8 / 275.0
dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
board = cv2.aruco.CharucoBoard((11, 7), 0.025 * F, 0.0185 * F, dic); det = cv2.aruco.CharucoDetector(board)
SIZE = (1920, 1080)
X0 = np.array(json.load(open("data/handeye/cam_flange.json"))["poseFlangeCam"]["position"])  # nur zur Info

def load(S):
    S = Path(S); out = []
    for f in sorted(S.glob("poses/*/camera.png")):
        q = f.parent / "quality.json"
        if q.exists() and json.loads(q.read_text()).get("valid") is False: continue
        cc, ci, _, _ = det.detectBoard(cv2.imread(str(f), cv2.IMREAD_GRAYSCALE))
        if ci is None or len(ci) < 12: continue
        o, p = board.matchImagePoints(cc, ci)
        st = json.loads((f.parent / "robot_state.json").read_text())
        out.append(dict(id=f.parent.name, g=st["poseGroup"], T=np.array(st["T_base_flange"]), P=o.reshape(-1, 3).astype(np.float64), uv=p.reshape(-1, 2).astype(np.float64)))
    return out

def intrinsics(obs, flags):
    rms, K, D, *_ = cv2.calibrateCameraExtended([o["P"].astype(np.float32) for o in obs], [o["uv"].astype(np.float32) for o in obs], SIZE, None, None, flags=flags)
    return rms, K, D.ravel()

def pnp(o, K, D):
    ok, rv, tv = cv2.solvePnP(o["P"], o["uv"], K, D); T = np.eye(4); T[:3, :3] = cv2.Rodrigues(rv)[0]; T[:3, 3] = tv.ravel(); return T

def handeye(obs, K, D, X0):
    O = [o for o in obs if len(o["uv"]) >= 25]
    B0 = geometry.average_poses([o["T"] @ X0 @ pnp(o, K, D) for o in O])
    def res(v):
        X, B = _pose_from_vector(v[:6]), _pose_from_vector(v[6:]); out = []
        for o in O:
            cb = geometry.invert(o["T"] @ X) @ B
            out.append((cv2.projectPoints(o["P"], cv2.Rodrigues(cb[:3, :3])[0], cb[:3, 3], K, D)[0].reshape(-1, 2) - o["uv"]).reshape(-1))
        return np.concatenate(out)
    r = least_squares(res, np.r_[_vector_from_pose(X0), _vector_from_pose(B0)], loss="soft_l1", x_scale="jac")
    X, B = _pose_from_vector(r.x[:6]), _pose_from_vector(r.x[6:]); rr = res(r.x).reshape(-1, 2)
    rec = [o["T"] @ X @ pnp(o, K, D) for o in O]; Bm = geometry.average_poses(rec)
    dp = np.array([np.linalg.norm(p[:3, 3] - Bm[:3, 3]) for p in rec]) * 1000
    return dict(X=X, B=B, rms=float(np.sqrt(np.mean(np.sum(rr ** 2, 1)))), spread=float(np.sqrt(np.mean(dp ** 2))), n=len(O))

def angles(X):
    R = X[:3, :3]; z = R[:, 2]
    return np.degrees(np.arccos(np.clip(z[2], -1, 1))), np.degrees(np.arctan2(R[1, 0], R[0, 0]))

XS = np.array(json.load(open("data/rohbilder/calibration/20261110T104151Z_77e0dd17/results/candidate_handeye_offline.json"))["T_flange_camera"])
data = {Path(s).name: load(s) for s in RUNS}
FULL = 0; K12 = cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST
print("== Intrinsik je Lauf (volles Modell | k1,k2)")
intr = {}
for name, obs in data.items():
    r1, K1, D1 = intrinsics(obs, FULL); r2, K2, D2 = intrinsics(obs, K12); intr[name] = (K1, D1)
    print("%s n=%d | voll RMS %.3f fx %.1f fy %.1f cx %.1f cy %.1f D %s | k1k2 RMS %.3f fx %.1f cx %.1f cy %.1f k1 %.3f k2 %.3f" % (
        name[:16], len(obs), r1, K1[0, 0], K1[1, 1], K1[0, 2], K1[1, 2], np.round(D1[:5], 3), r2, K2[0, 0], K2[0, 2], K2[1, 2], D2[0], D2[1]))
allobs = [o for v in data.values() for o in v]
rA, KA, DA = intrinsics(allobs, FULL)
print("gemeinsam n=%d voll RMS %.3f fx %.1f fy %.1f cx %.1f cy %.1f D %s" % (len(allobs), rA, KA[0, 0], KA[1, 1], KA[0, 2], KA[1, 2], np.round(DA[:5], 3)))
# Kreuztest Intrinsik: Intrinsik von Lauf i auf Bilder von Lauf j (PnP je Bild)
print("== Kreuztest Intrinsik (Zeile = Intrinsik aus Lauf, Spalte = Bilder aus Lauf), RMS px")
names = list(data)
for a in names:
    row = []
    for b in names:
        K, D = intr[a]; e = []
        for o in data[b]:
            T = pnp(o, K, D); pr = cv2.projectPoints(o["P"], cv2.Rodrigues(T[:3, :3])[0], T[:3, 3], K, D)[0].reshape(-1, 2)
            e.append(np.sum((pr - o["uv"]) ** 2, 1))
        row.append(np.sqrt(np.mean(np.concatenate(e))))
    print("  %s: %s" % (a[:16], "  ".join("%.3f" % v for v in row)))
print("== Hand-Auge je Lauf")
res = {}
for mode in ("eigene Intrinsik", "gemeinsame Intrinsik"):
    for name, obs in data.items():
        K, D = intr[name] if mode == "eigene Intrinsik" else (KA, DA)
        h = handeye(obs, K, D, XS); res[(mode, name)] = h
        tilt, roll = angles(h["X"])
        print("%-21s %s n=%d RMS %.3f px | Brettstreuung %.2f mm | t %s mm | Neigung %.2f Grad, Verdrehung %.2f Grad" % (
            mode, name[:16], h["n"], h["rms"], h["spread"], np.round(h["X"][:3, 3] * 1000, 2), tilt, roll))
    T = np.array([res[(mode, n)]["X"][:3, 3] * 1000 for n in names])
    print("  %s: Mittel t %s, Streuung (sigma je Achse) %s mm, max Paarabstand %.2f mm" % (
        mode, np.round(T.mean(0), 2), np.round(T.std(0, ddof=1), 2), max(np.linalg.norm(T[i] - T[j]) for i in range(len(T)) for j in range(len(T)))))
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            Xi, Xj = res[(mode, names[i])]["X"], res[(mode, names[j])]["X"]
            dt = np.linalg.norm(Xi[:3, 3] - Xj[:3, 3]) * 1000; dr = np.degrees(np.linalg.norm(Rot.from_matrix(Xi[:3, :3].T @ Xj[:3, :3]).as_rotvec()))
            print("    %s vs %s: %.2f mm, %.3f Grad" % (names[i][9:16], names[j][9:16], dt, dr))
json.dump({f"{m}|{n}": {"t_mm": (v["X"][:3, 3] * 1000).tolist(), "T_flange_camera": v["X"].tolist(), "rms_px": v["rms"], "spread_mm": v["spread"], "n": v["n"]} for (m, n), v in res.items()} |
          {"intrinsik_gemeinsam": {"K": KA.tolist(), "D": DA.tolist(), "rms": rA}} | {f"intrinsik|{n}": {"K": v[0].tolist(), "D": v[1].tolist()} for n, v in intr.items()},
          open("doc/auswertung/handeye_cv4/laeufe_vergleich_20261110.json", "w"), indent=1)
