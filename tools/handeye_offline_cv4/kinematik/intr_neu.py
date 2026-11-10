"""Hand-Auge der vier Sitzungen vom 09.11.2026 mit der Offline-Intrinsik vom 10.11.2026 (k1,k2) und
korrigiertem ChArUco-Massstab, starr und mit Kinematik (Leave-one-session-out). Aufruf aus der Repo-Wurzel:
PYTHONPATH=src python tools/handeye_offline_cv4/kinematik/intr_neu.py"""
import sys, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from intr import run
KN = np.array([[602.9, 0, 331.8], [0, 603.3, 244.2], [0, 0, 1]]); DN = np.array([0.118, -0.245, 0, 0, 0])
SC = {"charuco": 274.8 / 275.0}
keys = ["S1", "S2", "S3", "S4"]
ts = []
for k in keys:
    r = run([k], KN, DN, fixed_scale=SC); ts.append(r["t"])
    print("neu starr %s RMS %.3f t %s" % (k, r["rms"], np.round(r["t"], 2)))
P = np.array(ts); print("Spannweite starr %.2f mm" % max(np.linalg.norm(P[i] - P[j]) for i in range(4) for j in range(4)))
ts = []
for test in keys:
    tr = run([k for k in keys if k != test], KN, DN, kinfree=True, fixed_scale=SC)
    r1 = run([test], KN, DN, pfix=tr["p"], fixed_scale=SC); ts.append(r1["t"])
    print("neu+Kinematik test %s RMS %.3f t %s" % (test, r1["rms"], np.round(r1["t"], 2)))
P = np.array(ts); print("Spannweite mit Kinematik %.2f mm" % max(np.linalg.norm(P[i] - P[j]) for i in range(4) for j in range(4)))
