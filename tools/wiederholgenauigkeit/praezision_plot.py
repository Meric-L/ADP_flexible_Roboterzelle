"""Schlichter Plot zur Praezision der Hand-Kamera aus der Rauschserie vom 09.11.2026.

Roboter steht still, 27 Bilder des ChArUco-Bretts (Rauschlauf 1). Je Bild PnP mit der
Pi-Intrinsik; gezeigt wird die Lage der Brettmitte in Kamerakoordinaten relativ zum Mittel.
Aufruf aus der Repo-Wurzel:
    PYTHONPATH=src python tools/wiederholgenauigkeit/praezision_plot.py [--out doc/auswertung/kamera_praezision.png]
"""
import argparse
import glob
import json

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from repeatability_eval.charuco import build_board, detect_corners, object_points_mm, spec_from_dict

SESSION = "data/rohbilder/roboterwiderholgenauigkeit/2026-11-09_160215/02_rauschen"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="doc/auswertung/kamera_praezision.png")
    args = ap.parse_args()
    files = sorted(glob.glob(SESSION + "/r_0*.json"))[:27]  # Rauschlauf 1 (r_028.. ist ein zweiter Lauf)
    centres = []
    for f in files:
        d = json.load(open(f))
        spec = spec_from_dict(d["client"]["board"])
        cal = d["pi"]["calibration"]["content"]
        K, dist = np.array(cal["cameraMatrix"]), np.array(cal["distortionCoefficients"])
        gray = cv2.cvtColor(cv2.imread(f[:-5] + ".png"), cv2.COLOR_BGR2GRAY)
        ids, corners = detect_corners(gray, spec, build_board(spec))
        obj = np.c_[object_points_mm(ids, spec), np.zeros(len(ids))]
        _, rv, tv = cv2.solvePnP(obj, corners.reshape(-1, 1, 2), K, dist)
        mid = np.array([spec.cols * spec.square_size_m * 500, spec.rows * spec.square_size_m * 500, 0])
        centres.append(cv2.Rodrigues(rv)[0] @ mid + tv.ravel())
    C = np.array(centres)  # mm
    dev = (C - C.mean(0)) * 1000  # um
    sx, sy, sz = dev.std(0, ddof=1)
    dist_mm = np.linalg.norm(C.mean(0))

    fig, ax = plt.subplots(figsize=(6.4, 5.2), dpi=150)
    ax.scatter(dev[:, 0], dev[:, 1], s=28, color="#2a78d6", zorder=3)
    lim = max(12, np.abs(dev[:, :2]).max() * 1.3)
    ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_aspect("equal")
    ax.axhline(0, color="#999", lw=0.8); ax.axvline(0, color="#999", lw=0.8)
    ax.grid(color="#e2e6e9", lw=0.6)
    ax.set_xlabel("Δx [µm]"); ax.set_ylabel("Δy [µm]")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.suptitle("Präzision der Hand-Kamera", fontsize=14, fontweight="bold", x=0.03, ha="left")
    text = (f"Roboter steht still, {len(dev)} Bilder in 40 s, Abstand {dist_mm:.0f} mm\n"
            f"σx = {sx:.1f} µm   σy = {sy:.1f} µm   σz (Tiefe) = {sz:.1f} µm").replace(".", ",")
    fig.text(0.03, 0.885, text, ha="left", va="top", fontsize=10, color="#333")
    fig.subplots_adjust(top=0.80, left=0.14, right=0.97, bottom=0.11)
    fig.savefig(args.out)
    print(text)
    print("geschrieben:", args.out)


if __name__ == "__main__":
    main()
