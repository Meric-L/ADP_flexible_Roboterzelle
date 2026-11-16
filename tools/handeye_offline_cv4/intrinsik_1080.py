"""Intrinsik 1920x1080 offline aus einer Hand-Auge-Rohaufnahme (ChArUco, gemessener Massstab)."""
import json, glob, sys
from pathlib import Path
import numpy as np, cv2
S = Path(sys.argv[1])
F = 274.8 / 275.0
cfg = json.loads((S / "config.json").read_text())
b = cfg["board"]
d = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, b["dictionary"]))
board = cv2.aruco.CharucoBoard((b["cols"], b["rows"]), b["squareSizeM"] * F, b["markerSizeM"] * F, d)
det = cv2.aruco.CharucoDetector(board)
obj, img, meta = [], [], []
for f in sorted(S.glob("poses/*/camera.png")):
    st = json.loads((f.parent / "robot_state.json").read_text())
    if (f.parent / "quality.json").exists() and json.loads((f.parent / "quality.json").read_text()).get("valid") is False:
        continue
    g = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
    cc, ci, mc, mi = det.detectBoard(g)
    n = 0 if ci is None else len(ci)
    if n >= 12:
        o, p = board.matchImagePoints(cc, ci)
        obj.append(o.astype(np.float32)); img.append(p.astype(np.float32)); meta.append((f.parent.name, st["poseGroup"], n))
size = (1920, 1080)
print("Bilder mit >=12 Ecken:", len(obj), "| davon Gruppe F:", sum(m[1] == "F" for m in meta))
P = np.concatenate([i.reshape(-1, 2) for i in img])
H, _, _ = np.histogram2d(P[:, 0], P[:, 1], bins=[12, 6], range=[[0, 1920], [0, 1080]])
print("Abdeckung 12x6-Raster: %d/72; x %.0f-%.0f, y %.0f-%.0f" % ((H > 0).sum(), P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max()))
PF = np.concatenate([i.reshape(-1, 2) for i, m in zip(img, meta) if m[1] != "F"])
HF, _, _ = np.histogram2d(PF[:, 0], PF[:, 1], bins=[12, 6], range=[[0, 1920], [0, 1080]])
print("  ohne Gruppe F: %d/72" % (HF > 0).sum())
def evalK(K, D, sel=None):
    e = []
    for i, (o, p) in enumerate(zip(obj, img)):
        if sel is not None and not sel(i): continue
        ok, rv, tv = cv2.solvePnP(o, p, K, D)
        pr = cv2.projectPoints(o, rv, tv, K, D)[0].reshape(-1, 2)
        e.append(np.sum((pr - p.reshape(-1, 2)) ** 2, 1))
    return float(np.sqrt(np.mean(np.concatenate(e))))
res = {}
for name, flags in {"voll (k1,k2,p1,p2,k3)": 0, "k1,k2,p1,p2": cv2.CALIB_FIX_K3,
                    "k1,k2": cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST}.items():
    rms, K, D, rv, tv, sdi, sde, pve = cv2.calibrateCameraExtended(obj, img, size, None, None, flags=flags)
    sd = sdi.ravel()
    loo = []
    for i in range(len(obj)):
        _, K2, D2, _, _ = cv2.calibrateCamera(obj[:i] + obj[i+1:], img[:i] + img[i+1:], size, None, None, flags=flags)
        ok, r, t = cv2.solvePnP(obj[i], img[i], K2, D2)
        pr = cv2.projectPoints(obj[i], r, t, K2, D2)[0].reshape(-1, 2)
        loo.append(np.sqrt(np.mean(np.sum((pr - img[i].reshape(-1, 2)) ** 2, 1))))
    res[name] = (K, D)
    print("%-22s RMS %.3f px | LOO %.3f px | fx %.1f±%.1f fy %.1f±%.1f cx %.1f±%.1f cy %.1f±%.1f | D %s" % (
        name, rms, np.mean(loo), K[0, 0], sd[0], K[1, 1], sd[1], K[0, 2], sd[2], K[1, 2], sd[3], np.round(D.ravel()[:5], 3)))
KW = np.array([[1369.82702636719, 0, 986.091064453125], [0, 1365.98706054688, 547.630920410156], [0, 0, 1]])
KU = np.array([[602.9 * 2.25, 0, 331.8 * 2.25 + 240], [0, 603.3 * 2.25, 244.2 * 2.25], [0, 0, 1]]); DU = np.array([0.118, -0.245, 0, 0, 0])
pi = cfg["intrinsics"]; KP, DP = np.array(pi["cameraMatrix"]), np.array(pi["distortionCoefficients"]).ravel()
print("%-22s RMS %.3f px (je Bild PnP) | fx %.1f fy %.1f cx %.1f cy %.1f | D %s" % ("Pi online 10:34", evalK(KP, DP), KP[0,0], KP[1,1], KP[0,2], KP[1,2], np.round(DP[:5], 3)))
print("%-22s RMS %.3f px (je Bild PnP)" % ("Werk 1920x1080", evalK(KW, np.zeros(5))))
print("%-22s RMS %.3f px (je Bild PnP) | fx %.1f cx %.1f cy %.1f" % ("umgerechnet 640->1080", evalK(KU, DU), KU[0,0], KU[0,2], KU[1,2]))
K, D = res["k1,k2"]
print("k1,k2 je Gruppe RMS: ", {g: round(evalK(K, D, lambda i, g=g: meta[i][1] == g), 3) for g in "ABCDEF"})
KPf = res["voll (k1,k2,p1,p2,k3)"]
print("Pi online je Gruppe RMS:", {g: round(evalK(KP, DP, lambda i, g=g: meta[i][1] == g), 3) for g in "ABCDEF"})
tilts = []
for o, p in zip(obj, img):
    ok, rv, tv = cv2.solvePnP(o, p, K, D); R = cv2.Rodrigues(rv)[0]; tilts.append(np.degrees(np.arccos(abs(R[2, 2]))))
print("Kippung [Grad]: min %.0f, Median %.0f, max %.0f; >=30 Grad: %d Bilder" % (min(tilts), np.median(tilts), max(tilts), sum(t >= 30 for t in tilts)))
json.dump({k: {"K": v[0].tolist(), "D": v[1].ravel().tolist()} for k, v in res.items()}, open(Path(sys.argv[2]), "w"), indent=1)
