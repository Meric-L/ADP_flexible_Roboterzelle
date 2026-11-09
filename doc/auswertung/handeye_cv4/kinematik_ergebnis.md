# Kinematik-Experiment 09.11.2026 (Konsolenausgabe)

Skripte: `tools/handeye_offline_cv4/kinematik/` (ba.py, ba2.py). RMS in px, Translationen in mm.

```
=== ba.py (ueberparametrisiert: dq1..7, dd1..7; Parameter mit X/B nicht trennbar) ===
['S1'] RMS starr 2.013 | +dq 1.530 | +dq+dd 0.855 px   Brettstreuung 1.97 | 2.00 | 1.31 mm
     dq mrad [ 3.03 -1.44 -1.25 -0.07  1.23 -3.92 15.57]  t_X mm [-47.29 -35.88  54.98] (starr [-47.43 -33.82  57.08] )
['S2'] RMS starr 1.580 | +dq 1.014 | +dq+dd 0.830 px   Brettstreuung 1.90 | 1.98 | 1.58 mm
     dq mrad [ 1.10e-01  7.00e-02 -2.30e+00 -2.00e-02  2.05e+00 -2.82e+00  5.50e+01]  t_X mm [-48.01 -34.13  55.46] (starr [-47.03 -35.21  57.12] )
['S3'] RMS starr 1.888 | +dq 1.590 | +dq+dd 0.969 px   Brettstreuung 2.14 | 2.22 | 1.80 mm
     dq mrad [ -3.6   -1.02  -1.69  -0.35   1.27  -2.81 -73.87]  t_X mm [-43.5  -40.73  55.37] (starr [-46.79 -35.45  57.29] )
['S4'] RMS starr 1.541 | +dq 1.119 | +dq+dd 0.739 px   Brettstreuung 1.72 | 1.72 | 1.19 mm
     dq mrad [33.24 -1.18 -2.5   0.2   1.68 -3.07 -4.61]  t_X mm [-45.84 -37.17  55.44] (starr [-47.01 -34.93  56.89] )
gemeinsam ['S3', 'S4'] RMS starr 1.756 | +dq 1.439 | +dq+dd 0.907 Streuung starr [2.08 1.73] +dq [2.25 1.69] +dqdd [1.82 1.25]
     dq mrad [ 8.700e-01 -9.400e-01 -2.010e+00 -8.000e-02  1.450e+00 -2.930e+00
 -9.335e+01] | dqdd: dq [-11.83   0.17   0.36  -3.02  -7.65   3.4  -40.56] dd mm [ -5.74   1.39   0.7   -2.74  -1.56  -2.71 -66.1 ]  t_X [-49.64 -36.56 122.7 ]
gemeinsam ['S1', 'S2', 'S3', 'S4'] RMS starr 1.789 | +dq 1.394 | +dq+dd 0.912 Streuung starr [1.98 1.88 2.07 1.72] +dq [2.14 1.95 2.28 1.68] +dqdd [1.18 1.67 1.76 1.28]
     dq mrad [ 8.18 -0.78 -1.8   0.27  1.66 -3.56 38.84] | dqdd: dq [ 5.000e-02  4.000e-01  1.900e-01 -2.880e+00 -7.250e+00  2.270e+00
  6.439e+01] dd mm [-6.93  1.36  0.72 -2.05 -0.77 -3.01  1.72]  t_X [-52.9  -31.39  54.95]
CV train ['S1', 'S2', 'S3'] -> test S4 RMS starr 1.541 -> mit Trainings-Offsets 0.818 px; Streuung 1.72 -> 1.25 mm; t_X [-51.59 -34.2   78.27]
CV train ['S1', 'S2', 'S4'] -> test S3 RMS starr 1.888 -> mit Trainings-Offsets 1.072 px; Streuung 2.14 -> 1.74 mm; t_X [-51.41 -34.21  68.53]
CV train ['S3'] -> test S4 RMS starr 1.541 -> mit Trainings-Offsets 0.818 px; Streuung 1.72 -> 1.31 mm; t_X [-51.66 -33.76  51.2 ]
CV train ['S4'] -> test S3 RMS starr 1.888 -> mit Trainings-Offsets 1.109 px; Streuung 2.14 -> 1.84 mm; t_X [-51.   -34.76  50.47]

=== ba2.py (identifizierbares Modell) ===
alle 4: RMS starr 1.789 -> Kinematik 1.158 px
  p: dq2..6 mrad [ 0.01 -1.82 -4.18  2.    4.28] dd3,dd5 mm [0.69 1.55] da4,da5,da7 mm [ 1.08 -2.79 -3.48]
  t_X starr [-47.01 -34.88  57.09]  mit Kinematik [-46.05 -35.84  57.43]
LOSO test S1: RMS starr 2.013 -> mit Trainings-Kinematik 1.275 px; t_X starr [-47.43 -33.82  57.08] -> [-46.37 -35.28  57.58]
LOSO test S2: RMS starr 1.580 -> mit Trainings-Kinematik 1.070 px; t_X starr [-47.03 -35.21  57.12] -> [-46.07 -35.58  57.46]
LOSO test S3: RMS starr 1.888 -> mit Trainings-Kinematik 1.372 px; t_X starr [-46.79 -35.45  57.29] -> [-45.85 -36.47  57.24]
LOSO test S4: RMS starr 1.541 -> mit Trainings-Kinematik 1.055 px; t_X starr [-47.01 -34.93  56.89] -> [-46.04 -35.97  57.43]
Spannweite Hand-Auge-Translation (max. Paarabstand) starr: 1.76 mm
Spannweite Hand-Auge-Translation (max. Paarabstand) Kinematik: 1.34 mm
```
