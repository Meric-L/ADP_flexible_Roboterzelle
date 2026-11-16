"""Compute the offline intrinsics from hand-camera calibration images (ChArUco 11x7, DICT_5X5_100).

The board uses the measured scale (274.8 mm over 11 squares instead of 275.0 mm: square 24.982 mm,
marker 18.487 mm). Compares three distortion models (incl. leave-one-out) with the factory intrinsics
of the D415 and the old cam_flange.json. Nothing is saved or activated.

Usage, from the repository root:
    python tools/handeye_offline_cv4/intrinsik_offline.py [image pattern | hand-eye session folder] [--nur-gruppe F]
A session folder (with config.json and poses/) supplies poses/*/camera.png; --nur-gruppe filters by
poseGroup in poses/*/robot_state.json. Default image pattern: data/calibration/cam_flange_pending/kalib_*.jpg.
"""
import cv2, numpy as np, glob, json, sys, os, argparse
F = 274.8 / 275.0
SQ, MK = 0.025 * F, 0.0185 * F
d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
board = cv2.aruco.CharucoBoard((11, 7), SQ, MK, d)
det = cv2.aruco.CharucoDetector(board)
ap = argparse.ArgumentParser()
ap.add_argument('muster', nargs='?', default='data/calibration/cam_flange_pending/kalib_*.jpg')
ap.add_argument('--nur-gruppe', default=None, help='nur Posen dieser poseGroup (nur bei Sitzungsordner)')
args = ap.parse_args()
if os.path.isdir(args.muster) and os.path.isfile(os.path.join(args.muster, 'config.json')) \
        and os.path.isdir(os.path.join(args.muster, 'poses')):
    fs = sorted(glob.glob(os.path.join(args.muster, 'poses', '*', 'camera.png')))
    if args.nur_gruppe:
        def _gruppe(f):
            try:
                return json.load(open(os.path.join(os.path.dirname(f), 'robot_state.json'))).get('poseGroup')
            except (OSError, ValueError):
                return None
        fs = [f for f in fs if _gruppe(f) == args.nur_gruppe]
else:
    if args.nur_gruppe:
        sys.exit('--nur-gruppe geht nur mit einem Sitzungsordner')
    fs = sorted(glob.glob(args.muster))
if not fs:
    sys.exit('keine Bilder gefunden')
obj, img, names = [], [], []
for f in fs:
    g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
    cc, ci, mc, mi = det.detectBoard(g)
    n = 0 if ci is None else len(ci)
    if n >= 12:
        o, p = board.matchImagePoints(cc, ci)
        obj.append(o.astype(np.float32)); img.append(p.astype(np.float32)); names.append((f[-12:], n))
print("Bilder mit Brett:", len(obj), "von", len(fs), "| Ecken je Bild:", [n for _, n in names])
P = np.concatenate([i.reshape(-1, 2) for i in img])
H, _, _ = np.histogram2d(P[:, 0], P[:, 1], bins=[8, 6], range=[[0, 640], [0, 480]])
print("Abdeckung 8x6-Raster: %d/48 belegt; x %.0f-%.0f, y %.0f-%.0f" % ((H > 0).sum(), P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max()))
size = (640, 480)
KW = np.array([[608.81201171875, 0, 331.596038818359], [0, 607.105346679688, 243.391510009766], [0, 0, 1]])
alt = json.load(open('data/calibration/cam_flange.json'))
KA, DA = np.array(alt['cameraMatrix']), np.array(alt['distortionCoefficients']).ravel()
def evalK(K, D):
    """Return the RMS reprojection error in px with per-image PnP for intrinsics K, D."""
    errs = []
    for o, p in zip(obj, img):
        ok, rv, tv = cv2.solvePnP(o, p, K, D)
        pr = cv2.projectPoints(o, rv, tv, K, D)[0].reshape(-1, 2)
        errs.append(np.sum((pr - p.reshape(-1, 2)) ** 2, 1))
    return float(np.sqrt(np.mean(np.concatenate(errs))))
res = {}
variants = {
    "voll (k1,k2,p1,p2,k3)": 0,
    "k1,k2,p1,p2 (k3=0)": cv2.CALIB_FIX_K3,
    "k1,k2 (p=0,k3=0)": cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST,
}
for name, flags in variants.items():
    rms, K, D, rv, tv, sdi, sde, pve = cv2.calibrateCameraExtended(obj, img, size, None, None, flags=flags)
    sd = sdi.ravel()
    # Leave-one-out: calibrate without image i, measure the error on image i (PnP with foreign intrinsics)
    loo = []
    for i in range(len(obj)):
        o2 = obj[:i] + obj[i + 1:]; p2 = img[:i] + img[i + 1:]
        _, K2, D2, _, _ = cv2.calibrateCamera(o2, p2, size, None, None, flags=flags)
        ok, r, t = cv2.solvePnP(obj[i], img[i], K2, D2)
        pr = cv2.projectPoints(obj[i], r, t, K2, D2)[0].reshape(-1, 2)
        loo.append(np.sqrt(np.mean(np.sum((pr - img[i].reshape(-1, 2)) ** 2, 1))))
    res[name] = (K, D)
    print("%-24s RMS %.3f px | LOO %.3f px | fx %.1f±%.1f fy %.1f±%.1f cx %.1f±%.1f cy %.1f±%.1f | D %s" % (
        name, rms, np.mean(loo), K[0, 0], sd[0], K[1, 1], sd[1], K[0, 2], sd[2], K[1, 2], sd[3], np.round(D.ravel()[:5], 3)))
# Factory K fixed, only k1, k2 free
rms, K, D, *_ = cv2.calibrateCameraExtended(obj, img, size, KW.copy(), np.zeros(5), flags=cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_FOCAL_LENGTH | cv2.CALIB_FIX_PRINCIPAL_POINT | cv2.CALIB_FIX_K3 | cv2.CALIB_ZERO_TANGENT_DIST)
print("%-24s RMS %.3f px | D %s" % ("Werk-K fest, k1,k2 frei", rms, np.round(D.ravel()[:5], 3)))
print("%-24s RMS %.3f px (je Bild PnP)" % ("Werk unveraendert", evalK(KW, np.zeros(5))))
print("%-24s RMS %.3f px (je Bild PnP) | fx %.1f fy %.1f cx %.1f cy %.1f" % ("alt cam_flange.json", evalK(KA, DA), KA[0, 0], KA[1, 1], KA[0, 2], KA[1, 2]))
# Tilt angles of the views (k1, k2 model)
K, D = res["k1,k2 (p=0,k3=0)"]
tilts = []
for o, p in zip(obj, img):
    ok, rv, tv = cv2.solvePnP(o, p, K, D); R = cv2.Rodrigues(rv)[0]
    tilts.append(np.degrees(np.arccos(abs(R[2, 2]))))
print("Kippung Brett gegen Bildebene [Grad]:", np.round(sorted(tilts), 0).astype(int).tolist())
