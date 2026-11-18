# Analyses

Investigations of concrete problems: what went wrong, what was tried, what was measured and what
was decided. They describe the state at the time of the analysis, not necessarily the current
code. For the current code see [`../README.md`](../code/README.md).

| Analysis | Topic |
| --- | --- |
| [`camera-and-detector-analysis.md`](camera-and-detector-analysis.md) | Ceiling camera: image quality, camera settings and detector parameters |
| [`intrinsics-20261110.md`](intrinsics-20261110.md) | Hand camera intrinsics computed offline (10.11.2026) |
| [`hand-eye-1080-20261110.md`](hand-eye-1080-20261110.md) | Intrinsics and hand-eye at full resolution 1920×1080 (10.11.2026) |
| [`hand-eye-runs-comparison-20261110.md`](hand-eye-runs-comparison-20261110.md) | Repeatability of the hand-eye calibration: three runs compared (10.11.2026) |

Raw results, generated reports (HTML) and the numbers behind these analyses stay in
[`doc/auswertung/`](../../auswertung/), because the scripts in `tools/handeye_offline_cv4/` write
there.

New analysis: one file per investigation, named after the topic (and date if there will be
several), and add a row to the table above.
