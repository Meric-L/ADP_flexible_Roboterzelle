# Analyses

Investigations of concrete problems: what went wrong, what was tried, what was measured and what
was decided. They describe the state at the time of the analysis, not necessarily the current
code. For the current code see [`../code-dokumentation.md`](../code-dokumentation.md).

| Analysis | Topic |
| --- | --- |
| [`kamera-und-detektor-analyse.md`](kamera-und-detektor-analyse.md) | Ceiling camera: image quality, camera settings and detector parameters |
| [`intrinsik_20261110.md`](intrinsik_20261110.md) | Hand camera intrinsics computed offline (10.11.2026) |
| [`handauge_1080_20261110.md`](handauge_1080_20261110.md) | Intrinsics and hand-eye at full resolution 1920×1080 (10.11.2026) |
| [`handauge_laeufe_vergleich_20261110.md`](handauge_laeufe_vergleich_20261110.md) | Repeatability of the hand-eye calibration: three runs compared (10.11.2026) |

Raw results, generated reports (HTML) and the numbers behind these analyses stay in
[`doc/auswertung/`](../../auswertung/), because the scripts in `tools/handeye_offline_cv4/` write
there.

New analysis: one file per investigation, named after the topic (and date if there will be
several), and add a row to the table above.
