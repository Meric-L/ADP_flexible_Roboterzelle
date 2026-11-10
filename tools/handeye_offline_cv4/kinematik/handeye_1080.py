"""Hand-Auge fuer die 1080p-Sitzung: starr und mit Kinematik-Korrektur aus den alten Sitzungen (Gegentest).

Starr = nominelle Franka-Kinematik (wie Roboter und Pi rechnen). Kinematik-Korrektur = 10 Abweichungen
vom Datenblattmodell (Nullpunkte Gelenk 2-6, Laengen d3, d5, a4, a5, a7), geschaetzt aus den vier
Sitzungen vom 09.11.2026. Schreibt die starre Loesung als Kandidat nach <Sitzung>/results/.
Aufruf aus der Repo-Wurzel:
    PYTHONPATH=src python tools/handeye_offline_cv4/kinematik/handeye_1080.py <Sitzung> <intrinsik.json> [Modell]
<intrinsik.json> wie von tools/handeye_offline_cv4/intrinsik_1080.py geschrieben.
"""
import json, sys
from pathlib import Path
import numpy as np, cv2
sys.path.insert(0, str(Path(__file__).parent))
from intr import run, S as OLD
from ba2 import fk2, NP
from tagloc import geometry
from tagloc.handeye_offline import _pose_from_vector, _vector_from_pose
from scipy.optimize import least_squares

SES = Path(sys.argv[1]).resolve() if False else Path(sys.argv[1]); IN = json.load(open(sys.argv[2])); MODEL = sys.argv[3] if len(sys.argv) > 3 else "voll (k1,k2,p1,p2,k3)"
K5, D5 = np.array(IN[MODEL]["K"]), np.array(IN[MODEL]["D"])
F = 274.8 / 275.0
cfg = json.loads((SES / "config.json").read_text()); b = cfg["board"]
dic = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, b["dictionary"]))
board = cv2.aruco.CharucoBoard((b["cols"], b["rows"]), b["squareSizeM"], b["markerSizeM"], dic)  # Nennmass, skaliert unten
det = cv2.aruco.CharucoDetector(board)
obs = []
for f in sorted(SES.glob("poses/*/camera.png")):
    st = json.loads((f.parent / "robot_state.json").read_text())
    q = f.parent / "quality.json"
    if q.exists() and json.loads(q.read_text()).get("valid") is False:
        continue
    cc, ci, _, _ = det.detectBoard(cv2.imread(str(f), cv2.IMREAD_GRAYSCALE))
    if ci is None or len(ci) < 25:
        continue
    o, p = board.matchImagePoints(cc, ci)
    o = o.reshape(-1, 3).astype(np.float64) * F; p = p.reshape(-1, 2).astype(np.float64)
    obs.append(dict(id=f.parent.name, g=st["poseGroup"], q=np.array(st["jointAnglesRad"]), T=np.array(st["T_base_flange"]), P=o, uv=p))
print("Posen fuer Hand-Auge (>=25 Ecken):", len(obs), {g: sum(o["g"] == g for o in obs) for g in "ABCDEF"})
X0 = np.array(json.loads(Path("doc/auswertung/handeye_cv4/20261109T143834Z_6852cbc7.json").read_text())["finalSolution"]["T_flange_camera"])

def fit(p=None, sel=lambda o: True):
    O = [o for o in obs if sel(o)]
    fl = (lambda o: fk2(o["q"], p)) if p is not None else (lambda o: o["T"])
    Bs = []
    for o in O:
        ok, rv, tv = cv2.solvePnP(o["P"], o["uv"], K5, D5); T = np.eye(4); T[:3, :3] = cv2.Rodrigues(rv)[0]; T[:3, 3] = tv.ravel()
        Bs.append(fl(o) @ X0 @ T)
    v0 = np.r_[_vector_from_pose(X0), _vector_from_pose(geometry.average_poses(Bs))]
    def res(v):
        X, B = _pose_from_vector(v[:6]), _pose_from_vector(v[6:12]); out = []
        for o in O:
            cb = geometry.invert(fl(o) @ X) @ B
            pr = cv2.projectPoints(o["P"], cv2.Rodrigues(cb[:3, :3])[0], cb[:3, 3], K5, D5)[0].reshape(-1, 2)
            out.append((pr - o["uv"]).reshape(-1))
        return np.concatenate(out)
    r = least_squares(res, v0, loss="soft_l1", x_scale="jac")
    rr = res(r.x).reshape(-1, 2); X = _pose_from_vector(r.x[:6]); B = _pose_from_vector(r.x[6:12])
    per = []; i = 0
    for o in O:
        n = len(o["uv"]); per.append((o["g"], np.sqrt(np.mean(np.sum(rr[i:i+n] ** 2, 1))))); i += n
    # Brettlage-Streuung (mm) ueber PnP je Pose
    dev = []
    for o in O:
        ok, rv, tv = cv2.solvePnP(o["P"], o["uv"], K5, D5); T = np.eye(4); T[:3, :3] = cv2.Rodrigues(rv)[0]; T[:3, 3] = tv.ravel()
        dev.append(np.linalg.norm((fl(o) @ X @ T)[:3, 3] - B[:3, 3]) * 1000)
    rms = float(np.sqrt(np.mean(np.sum(rr ** 2, 1))))
    return dict(rms=rms, X=X, per={g: round(float(np.sqrt(np.mean([e ** 2 for gg, e in per if gg == g]))), 2) for g in "ABCDEF" if any(gg == g for gg, _ in per)},
                spread=float(np.sqrt(np.mean(np.square(dev)))), n=len(O))

KN = np.array([[602.9, 0, 331.8], [0, 603.3, 244.2], [0, 0, 1]]); DN = np.array([0.118, -0.245, 0, 0, 0])
old = run(["S1", "S2", "S3", "S4"], KN, DN, kinfree=True, fixed_scale={"charuco": F})
p = old["p"]
print("Kinematik aus S1-S4: dq2..6 mrad", np.round(p[:5] * 1000, 2), "Laengen mm", np.round(p[5:] * 1000, 2))
print("Hand-Auge S1-S4 gemeinsam mit Kinematik: t", np.round(old["t"], 2))
for name, pp in (("starr (nominelle Kinematik)", None), ("mit Kinematik aus S1-S4", p)):
    r = fit(pp)
    if pp is None:
        X = r["X"]; q = geometry.to_position_quaternion(X) if hasattr(geometry, "to_position_quaternion") else None
        cand = {"schema": "wsc.vision.handeye/1", "frameId": "cam_flange",
                "calibrationId": f"cam_flange@offline-{SES.name}",
                "T_flange_camera": X.tolist(), "translationMm": (X[:3, 3] * 1000).tolist(),
                "rotvecDeg": np.degrees(geometry.rvec_from_rotation(X[:3, :3])).tolist(),
                "reprojectionRmsPx": r["rms"], "boardSpreadRmsMm": r["spread"], "sampleCount": r["n"],
                "intrinsics": {"K": K5.tolist(), "D": D5.tolist(), "imageSize": [1920, 1080]},
                "note": "Starr, nominelle Kinematik, Brett 274,8 mm/11 Felder. Gehoert zur Intrinsik "
                        "candidate_intrinsics_offline.json; nur gemeinsam aktivieren. NICHT aktiviert."}
        (SES / "results").mkdir(exist_ok=True)
        (SES / "results" / "candidate_handeye_offline.json").write_text(json.dumps(cand, indent=2) + "\n")
    rot = np.degrees(geometry.rvec_from_rotation(r["X"][:3, :3]))
    print("%-30s n=%d RMS %.3f px | Brettstreuung %.2f mm | t %s | rot %s | je Gruppe %s" % (name, r["n"], r["rms"], r["spread"], np.round(r["X"][:3, 3] * 1000, 2), np.round(rot, 2), r["per"]))
    rA = fit(pp, sel=lambda o: o["g"] != "F")
    print("%-30s ohne F: n=%d RMS %.3f px | t %s" % ("", rA["n"], rA["rms"], np.round(rA["X"][:3, 3] * 1000, 2)))
