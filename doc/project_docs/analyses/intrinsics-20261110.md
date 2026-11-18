# Hand camera intrinsics offline, 10.11.2026

Data: 18 calibration captures `data/calibration/cam_flange_pending/kalib_001..018.jpg` (JPEG, hand Pi, RealSense D415 Color 640×480),
ChArUco 11×7, DICT_5X5_100, measured scale 274.8 mm over 11 squares (square 24.982 mm, marker 18.487 mm).
Scripts: `tools/handeye_offline_cv4/intrinsik_offline.py`, `tools/handeye_offline_cv4/kinematik/intr_neu.py`. Nothing activated.

## Intrinsics

| Model | RMS | Leave-one-out | fx / fy | cx / cy | Distortion |
|---|---|---|---|---|---|
| new, k1+k2 (recommended) | 0.131 px | 0.132 px | 602.9 / 603.3 | 331.8 / 244.2 | k1 0.118, k2 −0.245 |
| new, k1+k2+p1+p2 | 0.126 px | 0.128 px | 602.8 / 603.1 | 332.9 / 241.7 | k1 0.119, k2 −0.247 |
| new, full (k3) | 0.114 px | 0.119 px | 603.4 / 603.8 | 333.2 / 241.4 | k3 −1.2 |
| factory K fixed, k1+k2 free | 0.192 px | – | 608.8 / 607.1 | 331.6 / 243.4 | k1 0.111, k2 −0.236 |
| factory intrinsics unchanged | 0.386 px | – | 608.8 / 607.1 | 331.6 / 243.4 | 0 |
| old `cam_flange.json` (23.09.2026) | 0.324 px | – | 607.5 / 606.4 | 323.5 / 263.4 | k1 0.17, k2 −0.65, k3 0.64 |

- Principal point confirms the factory value to ~1 px; the old cy (263.4) was 20 px off.
- Distortion is not zero (factory specification 0).
- Focal length ≈ 603 px instead of 608.8 (factory): −1%, corresponding to ~1% depth. Tilts of the captures only 1–31° (median ~12°); to confirm, capture again with 30–45° tilt.
- Coverage 42/48 grid squares (x 52–599 px, y 45–446 px).

## Effect on hand-eye (four sessions 09.11.2026, ChArUco scale corrected)

| | S1 | S2 | S3 | S4 | Span of t | Hand-eye z |
|---|---|---|---|---|---|---|
| old, rigid | 2.01 | 1.58 | 1.89 | 1.54 px | 1.76 mm | ≈ 57.1 mm |
| new, rigid | 2.00 | 1.52 | 1.84 | 1.51 px | 1.77 mm | ≈ 59.9 mm |
| old + kinematics (LOSO) | 1.28 | 1.07 | 1.37 | 1.06 px | 1.34 mm | ≈ 57.4 mm |
| new + kinematics (LOSO) | 1.23 | 0.97 | 1.31 | 0.98 px | 1.50 mm | ≈ 59.2 mm |

The new intrinsics improve the reprojection by only 2–9%; the largest share remains the robot kinematics.
The hand-eye depth shifts by ~2.8 mm, essentially via the focal length: therefore confirm the focal length before activating.

## Console output `intrinsik_offline.py`

```
Bilder mit Brett: 18 von 18 | Ecken je Bild: [60, 60, 60, 49, 60, 60, 58, 54, 41, 60, 58, 50, 60, 60, 57, 60, 60, 60]
Abdeckung 8x6-Raster: 42/48 belegt; x 52-599, y 45-446
voll (k1,k2,p1,p2,k3)    RMS 0.114 px | LOO 0.119 px | fx 603.4±0.5 fy 603.8±0.5 cx 333.2±0.3 cy 241.4±0.2 | D [ 6.700e-02  2.240e-01 -2.000e-03  1.000e-03 -1.215e+00]
k1,k2,p1,p2 (k3=0)       RMS 0.126 px | LOO 0.128 px | fx 602.8±0.5 fy 603.1±0.5 cx 332.9±0.3 cy 241.7±0.3 | D [ 0.119 -0.247 -0.002  0.001  0.   ]
k1,k2 (p=0,k3=0)         RMS 0.131 px | LOO 0.132 px | fx 602.9±0.6 fy 603.3±0.6 cx 331.8±0.2 cy 244.2±0.2 | D [ 0.118 -0.245  0.     0.     0.   ]
Werk-K fest, k1,k2 frei  RMS 0.192 px | D [ 0.111 -0.236  0.     0.     0.   ]
Werk unveraendert        RMS 0.386 px (je Bild PnP)
alt cam_flange.json      RMS 0.324 px (je Bild PnP) | fx 607.5 fy 606.4 cx 323.5 cy 263.4
Kippung Brett gegen Bildebene [Grad]: [1, 2, 4, 5, 8, 9, 9, 10, 11, 12, 15, 16, 17, 19, 19, 24, 29, 31]
```
