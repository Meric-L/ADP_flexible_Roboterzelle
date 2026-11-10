# Intrinsik-Experiment 10.11.2026 (Konsolenausgabe)

Vergleich alte Intrinsik (cam_flange.json vom 23.09.2026) gegen Werks-Intrinsik der D415 (Color 640x480: fx 608,81, fy 607,11, cx 331,60, cy 243,39, Verzeichnung 0). Brett laut Nutzer mit Stahllineal 274,6 +- 2 mm statt 275,0 mm (ChArUco, 11 Felder). Skript: `tools/handeye_offline_cv4/kinematik/intr.py`.

```
== je Sitzung starr: RMS px | t_X mm
  alt                    S1 RMS 2.013  t [-47.43 -33.82  57.08]
  alt                    S2 RMS 1.580  t [-47.03 -35.21  57.12]
  alt                    S3 RMS 1.888  t [-46.79 -35.45  57.29]
  alt                    S4 RMS 1.541  t [-47.01 -34.93  56.89]
  alt                    Spannweite t_X (max Paarabstand) 1.76 mm
  werk                   S1 RMS 2.023  t [-47.22 -33.56  58.99]
  werk                   S2 RMS 1.596  t [-46.86 -34.87  58.94]
  werk                   S3 RMS 1.907  t [-46.61 -35.17  59.18]
  werk                   S4 RMS 1.549  t [-46.76 -34.68  59.01]
  werk                   Spannweite t_X (max Paarabstand) 1.74 mm
  werk+Massstab frei     S1 RMS 2.017  t [-47.24 -33.46  60.7 ] scale {'charuco': np.float64(0.99562)}
  werk+Massstab frei     S2 RMS 1.588  t [-46.92 -34.85  60.69] scale {'schach': np.float64(0.99534)}
  werk+Massstab frei     S3 RMS 1.876  t [-46.69 -35.14  61.4 ] scale {'schach': np.float64(0.99419)}
  werk+Massstab frei     S4 RMS 1.549  t [-46.81 -34.7   59.92] scale {'charuco': np.float64(0.99763)}
  werk+Massstab frei     Spannweite t_X (max Paarabstand) 1.90 mm
  werk+k1k2 frei         S1 RMS 2.004  t [-47.23 -33.55  57.01] k1 0.076 k2 -0.078
  werk+k1k2 frei         S2 RMS 1.546  t [-46.91 -34.95  56.42] k1 0.101 k2 -0.118
  werk+k1k2 frei         S3 RMS 1.852  t [-46.64 -35.21  56.72] k1 0.098 k2 -0.092
  werk+k1k2 frei         S4 RMS 1.511  t [-46.76 -34.7   57.09] k1 0.065 k2 0.021
  werk+k1k2 frei         Spannweite t_X (max Paarabstand) 1.79 mm
  werk+Massstab+k1k2     S1 RMS 2.002  t [-47.24 -33.49  58.31] scale {'charuco': np.float64(0.99691)} k1 0.076 k2 -0.104
  werk+Massstab+k1k2     S2 RMS 1.546  t [-46.95 -34.94  57.67] scale {'schach': np.float64(0.99698)} k1 0.097 k2 -0.124
  werk+Massstab+k1k2     S3 RMS 1.834  t [-46.69 -35.2   58.46] scale {'schach': np.float64(0.99579)} k1 0.095 k2 -0.110
  werk+Massstab+k1k2     S4 RMS 1.511  t [-46.78 -34.71  57.4 ] scale {'charuco': np.float64(0.99925)} k1 0.065 k2 0.015
  werk+Massstab+k1k2     Spannweite t_X (max Paarabstand) 1.81 mm
== Werk + Kinematik, Leave-one-session-out
  alt   test S1: RMS starr 2.013 -> mit Kinematik 1.275  t [-46.37 -35.28  57.58]
  alt   test S2: RMS starr 1.580 -> mit Kinematik 1.070  t [-46.07 -35.58  57.46]
  alt   test S3: RMS starr 1.888 -> mit Kinematik 1.372  t [-45.85 -36.47  57.24]
  alt   test S4: RMS starr 1.541 -> mit Kinematik 1.055  t [-46.04 -35.97  57.43]
  alt   Spannweite t_X mit Kinematik 1.34 mm
  werk  test S1: RMS starr 2.023 -> mit Kinematik 1.275  t [-46.3  -35.11  60.29]
  werk  test S2: RMS starr 1.596 -> mit Kinematik 1.082  t [-46.02 -35.38  60.09]
  werk  test S3: RMS starr 1.907 -> mit Kinematik 1.383  t [-45.77 -36.29  59.98]
  werk  test S4: RMS starr 1.549 -> mit Kinematik 1.046  t [-45.99 -35.88  60.34]
  werk  Spannweite t_X mit Kinematik 1.33 mm
```
