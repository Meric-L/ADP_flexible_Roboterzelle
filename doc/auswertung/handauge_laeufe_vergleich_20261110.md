# Repeatability of calibration: three runs from 10.11.2026

Three independent hand-eye raw captures at 1920×1080, ChArUco (measured scale 274.8 mm / 11 squares), each with image-edge group F, and no images discarded due to arm movement. Each run was computed offline on its own (own intrinsics, hand-eye rigid), additionally with shared intrinsics. Script: `tools/handeye_offline_cv4/laeufe_vergleich.py`, numbers: `doc/auswertung/handeye_cv4/laeufe_vergleich_20261110.json`.

| Run | Poses | Accepted online |
|---|---|---|
| L1 `20261110T104151Z_77e0dd17` (10:42 UTC) | 59 | 49 (Pi still with old intrinsics) |
| L2 `20261110T112640Z_7919557e` (11:27 UTC) | 41 | 41 |
| L3 `20261110T113752Z_dca1c37b` (11:38 UTC) | 57 (55 evaluable) | 54 |

## Intrinsics (full model)

| Run | RMS | fx / fy | cx / cy | k1 / k2 / k3 |
|---|---|---|---|---|
| L1 | 0.274 px | 1356.7 / 1357.3 | 988.3 / 547.6 | 0.138 / −0.427 / 0.367 |
| L2 | 0.226 px | 1358.9 / 1358.9 | 987.8 / 549.0 | 0.126 / −0.382 / 0.298 |
| L3 | 0.240 px | 1357.4 / 1357.4 | 988.3 / 548.8 | 0.124 / −0.356 / 0.257 |
| shared (155 images) | 0.253 px | 1357.8 / 1357.9 | 988.1 / 547.5 | 0.131 / −0.402 / 0.336 |

Span of fx 2.2 px (0.16%), cx 0.5 px, cy 1.4 px. Cross-test (intrinsics of one run applied to the images of another): at most 0.05 px worse than its own. The intrinsics are reproducible.

## Hand-eye

| Run | Reprojection | Board scatter | t (mm) | Tilt / rotation |
|---|---|---|---|---|
| L1 | 3.63 px | 1.41 mm | −47.32 / −34.24 / 60.26 | 0.36° / 90.52° |
| L2 | 2.77 px | 1.00 mm | −46.38 / −36.06 / 59.85 | 0.17° / 90.60° |
| L3 | 5.41 px | 1.68 mm | −47.55 / −35.69 / 60.10 | 0.28° / 90.55° |

- Translation: mean −47.08 / −35.33 / 60.07 mm, σ per axis 0.62 / 0.96 / 0.20 mm, largest pairwise distance 2.09 mm (L1–L2); L1–L3 1.48 mm, L2–L3 1.25 mm.
- Rotation: pairwise differences 0.13–0.25°.
- With shared intrinsics practically identical (difference ≤ 0.2 mm): the scatter comes from the hand-eye estimation, not from the intrinsics.

## What this means for a measured point

Translation and tilt of the solutions partly compensate each other. What matters is where a point measured by the camera ends up in the flange:

| Distance | Image center: max. distance between two runs / RMS about mean | Image corners and edges |
|---|---|---|
| 300 mm | 0.91 / 0.44 mm | 1.26–1.30 / 0.56–0.58 mm |
| 380 mm (typical working distance) | 0.61 / 0.31 mm | 1.20–1.29 / 0.51–0.57 mm |
| 500 mm | 0.32 / 0.15 mm | 1.29–1.54 / 0.54–0.64 mm |

At the image center at working distance, the runs lie within ~0.6 mm (RMS 0.3 mm); at the image edges within ~1.3 mm. The scatter *within* a run (board scatter 1.0–1.7 mm) remains the larger error source; L3 is the weakest run.
