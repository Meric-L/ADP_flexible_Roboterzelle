"""Schreibt die Offline-Kalibrierung der Sitzung 20261110T104151Z_77e0dd17 als aktive Dateien.

data/calibration/cam_flange.json (Intrinsik) und data/handeye/cam_flange.json (Hand-Auge), mit den
tagloc-Speicherfunktionen. --aufloesung 1920x1080 (Messaufloesung) oder 640x480: dann exakt auf den
640x480-Strom der D415 umgerechnet (mittlerer 1440x1080-Ausschnitt, um 2,25 verkleinert:
fx/2,25, cx'=(cx-240)/2,25, cy'=cy/2,25; Verzeichnung in normierten Koordinaten unveraendert).
Aufruf aus der Repo-Wurzel: PYTHONPATH=src python <skript> --aufloesung 1920x1080 [--sitzung <ordner>]
"""
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, cv2
from tagloc import geometry
from tagloc.calibration import CameraCalibration, save_calibration, load_calibration
from tagloc.handeye import HandEye, save_hand_eye, load_hand_eye

ap = argparse.ArgumentParser()
ap.add_argument("--aufloesung", choices=["1920x1080", "640x480"], required=True)
ap.add_argument("--sitzung", default="data/rohbilder/calibration/20261110T104151Z_77e0dd17")
ap.add_argument("--quelle", default=None, help="Ordner mit candidate_*.json (Standard: <sitzung>/results)")
a = ap.parse_args()
S = Path(a.sitzung); R = Path(a.quelle) if a.quelle else S / "results"
ci = json.loads((R / "candidate_intrinsics_offline.json").read_text())
ch = json.loads((R / "candidate_handeye_offline.json").read_text())
K = np.array(ci["cameraMatrix"], float); D = np.array(ci["distortionCoefficients"], float)
X = np.array(ch["T_flange_camera"], float)
SCALE = 274.8 / 275.0

# Brettstreuung je Pose (Messaufloesung, nominelle Kinematik) fuer die Kennzahlen der Hand-Auge-Datei.
dic = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
board = cv2.aruco.CharucoBoard((11, 7), 0.025 * SCALE, 0.0185 * SCALE, dic); det = cv2.aruco.CharucoDetector(board)
recon = []
if S.exists():
    for f in sorted(S.glob("poses/*/camera.png")):
        q = f.parent / "quality.json"
        if q.exists() and json.loads(q.read_text()).get("valid") is False:
            continue
        cc, cid, _, _ = det.detectBoard(cv2.imread(str(f), cv2.IMREAD_GRAYSCALE))
        if cid is None or len(cid) < 25:
            continue
        o, p = board.matchImagePoints(cc, cid); ok, rv, tv = cv2.solvePnP(o, p, K, D)
        Tcb = np.eye(4); Tcb[:3, :3] = cv2.Rodrigues(rv)[0]; Tcb[:3, 3] = tv.ravel()
        T = np.array(json.loads((f.parent / "robot_state.json").read_text())["T_base_flange"])
        recon.append(T @ X @ Tcb)
if recon:
    B = geometry.average_poses(recon)
    dp = np.array([np.linalg.norm(r[:3, 3] - B[:3, 3]) for r in recon])
    dr = np.array([np.degrees(geometry.rotation_distance_rad(r, B)) for r in recon])
    rmsP, maxP, rmsR, maxR = float(np.sqrt(np.mean(dp**2))), float(dp.max()), float(np.sqrt(np.mean(dr**2))), float(dr.max())
else:
    rmsP = maxP = rmsR = maxR = float("nan")

reproj = float(ch["reprojectionRmsPx"])
if a.aufloesung == "640x480":
    K = K.copy(); K[0, 0] /= 2.25; K[1, 1] /= 2.25; K[0, 2] = (K[0, 2] - 240) / 2.25; K[1, 2] /= 2.25
    size, suffix, reproj = (640, 480), "-640x480-umgerechnet", reproj / 2.25
else:
    size, suffix = (1920, 1080), ""
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
cal_id = f"cam_flange@2026-11-10T10:41:51Z-offline{suffix}"
calib = CameraCalibration(camera_matrix=K, distortion=D, image_size=size, frame_id="cam_flange",
                          calibration_id=cal_id, rms_reprojection_error=float(ci["rmsReprojectionError"]) * (size[0] / 1920),
                          sample_count=int(ci["sampleCount"]),
                          board={"type": "charuco", "cols": 11, "rows": 7, "squareSizeM": 0.025 * SCALE,
                                 "markerSizeM": 0.0185 * SCALE, "dictionary": "DICT_5X5_100"})
he = HandEye(pose_flange_cam=X, frame_id="cam_flange", calibration_id=cal_id, rms_position_m=rmsP,
             rms_rotation_deg=rmsR, sample_count=int(ch["sampleCount"]), max_position_m=maxP, max_rotation_deg=maxR,
             refined_reprojection_rms_px=reproj, board_scale=SCALE, intrinsics_source="calibration")
Path("data/calibration").mkdir(parents=True, exist_ok=True); Path("data/handeye").mkdir(parents=True, exist_ok=True)
save_calibration(Path("data/calibration/cam_flange.json"), calib)
save_hand_eye(Path("data/handeye/cam_flange.json"), he)
# Nachkontrolle: mit den Ladefunktionen des Pi lesen.
c2 = load_calibration(Path("data/calibration/cam_flange.json")); h2 = load_hand_eye(Path("data/handeye/cam_flange.json"))
print("Intrinsik", c2.image_size, np.round(c2.camera_matrix[[0, 1, 0, 1], [0, 1, 2, 2]], 2), "D", np.round(c2.distortion, 3))
print("Hand-Auge t mm", np.round(h2.pose_flange_cam[:3, 3] * 1000, 2), "Brettstreuung %.2f mm / %.2f Grad (max %.2f mm / %.2f Grad)" % (rmsP * 1000, rmsR, maxP * 1000, maxR))
