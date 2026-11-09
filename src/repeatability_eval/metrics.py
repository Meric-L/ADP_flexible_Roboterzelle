"""Reine Kennzahlen der kalibrierfreien Wiederholgenauigkeit (numpy/cv2)."""

import math
from typing import Any

import numpy as np

from .charuco import CharucoSpec, object_points_mm

#: Mindestzahl Ecken fuer die Homographie.
MIN_CORNERS = 6


def homography_metrics(
    ids: np.ndarray, corners: np.ndarray, spec: CharucoSpec, image_size: tuple[int, int]
) -> dict[str, Any]:
    """Homographie Bild-px -> Board-mm und Kennwerte an der Bildmitte.

    Liefert `centerOnBoardMm` [x, y], `rotationDeg`, `scalePxPerMm`,
    `homographyRmsPx` (Rueckprojektion Board -> Bild), `nCorners`.
    `ValueError` bei weniger als 6 Ecken oder nicht berechenbarer Homographie.
    """
    import cv2

    ids = np.asarray(ids, dtype=int).reshape(-1)
    corners = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    if len(ids) < MIN_CORNERS:
        raise ValueError(f"Zu wenige Ecken fuer die Homographie: {len(ids)} < {MIN_CORNERS}")
    board_mm = object_points_mm(ids, spec)
    h, _ = cv2.findHomography(corners, board_mm, 0)
    if h is None or not np.all(np.isfinite(h)):
        raise ValueError("Homographie nicht berechenbar")
    w, hgt = image_size
    c = np.array([(w - 1) / 2.0, (hgt - 1) / 2.0])
    v = h @ np.array([c[0], c[1], 1.0])
    if abs(v[2]) < 1e-12:
        raise ValueError("Bildmitte liegt auf der Fluchtgeraden der Homographie")
    q = v[:2] / v[2]
    jac = (h[:2, :2] - np.outer(q, h[2, :2])) / v[2]
    rotation = math.degrees(math.atan2(jac[1, 0], jac[0, 0]))
    det = abs(float(np.linalg.det(jac)))
    if det <= 0:
        raise ValueError("Entartete Homographie")
    mm_per_px = math.sqrt(det)
    back = cv2.perspectiveTransform(board_mm.reshape(-1, 1, 2), np.linalg.inv(h)).reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((back - corners) ** 2, axis=1))))
    return {
        "centerOnBoardMm": [float(q[0]), float(q[1])],
        "rotationDeg": float(rotation),
        "scalePxPerMm": 1.0 / mm_per_px,
        "homographyRmsPx": rms,
        "nCorners": int(len(ids)),
    }


def repeatability(points: np.ndarray) -> dict[str, Any]:
    """Wiederholgenauigkeit nach ISO 9283: `rp = lMean + 3 * sL`.

    `points` (M, k); `ValueError` bei M < 2.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim == 1:
        pts = pts.reshape(-1, 1)
    if pts.shape[0] < 2:
        raise ValueError("Wiederholgenauigkeit braucht mindestens 2 Messungen")
    centroid = pts.mean(axis=0)
    dist = np.linalg.norm(pts - centroid, axis=1)
    l_mean = float(dist.mean())
    s_l = float(dist.std(ddof=1))
    return {
        "centroid": centroid.tolist(),
        "distances": dist.tolist(),
        "lMean": l_mean,
        "sL": s_l,
        "rp": l_mean + 3.0 * s_l,
        "stdPerAxis": pts.std(axis=0, ddof=1).tolist(),
        "n": int(pts.shape[0]),
    }


def corner_noise(samples: list[tuple[np.ndarray, np.ndarray]]) -> dict[int, float]:
    """Radiale Streuung (px) je Ecken-ID; nur IDs in mindestens 2 Bildern.

    Streuung = Wurzel aus der Summe der Stichprobenvarianzen von x und y.
    """
    positions: dict[int, list[np.ndarray]] = {}
    for ids, corners in samples:
        corners = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
        for i, p in zip(np.asarray(ids).reshape(-1), corners):
            positions.setdefault(int(i), []).append(p)
    noise: dict[int, float] = {}
    for i, plist in positions.items():
        if len(plist) >= 2:
            arr = np.array(plist)
            noise[i] = float(math.sqrt(float(arr.var(axis=0, ddof=1).sum())))
    return noise


def noisy_ids(noise: dict[int, float], factor: float = 3.0) -> set[int]:
    """IDs, deren Streuung groesser als `factor` mal der Median ist."""
    if not noise:
        return set()
    median = float(np.median(list(noise.values())))
    return {i for i, v in noise.items() if v > factor * median}


def mean_corner_displacement(
    ids: np.ndarray, corners: np.ndarray, ref: dict[int, tuple[float, float]]
) -> float:
    """Mittlerer Abstand (px) der Ecken zur Referenz ueber die gemeinsamen IDs; NaN ohne gemeinsame."""
    corners = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    dists = [
        math.hypot(p[0] - ref[int(i)][0], p[1] - ref[int(i)][1])
        for i, p in zip(np.asarray(ids).reshape(-1), corners)
        if int(i) in ref
    ]
    return float(np.mean(dists)) if dists else float("nan")
