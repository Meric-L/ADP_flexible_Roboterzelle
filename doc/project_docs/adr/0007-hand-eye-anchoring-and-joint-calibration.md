# 0007 — Hand-eye: one constant transform, anchored once, calibrated with the intrinsics

- **Status:** accepted
- **Date:** 2026-09-22 (anchoring), 2026-11-15 (joint calibration)

## Context

The nearest world tag is often outside the hand camera image when it looks at a module.

## Decision

- The hand camera sits rigidly on the flange: `T_flange_cam` is a constant, calibrated once
  (`data/handeye/cam_flange.json`, `wsc.vision.handeye/1`).
- Per run the robot **anchors once** at a world tag:
  `T_world_base = T_world_cam · inv(T_flange_cam) · inv(T_base_flange)`. Afterwards
  `T_world_cam = T_world_base · T_base_flange(now) · T_flange_cam`; a re-sighting reports the drift.
- Intrinsics and hand-eye of the hand camera are calibrated **together** from the same raw
  images (one button „Kalibrierung“, `fullCalibration: true`). Reason: a hand-eye solution does not fit different intrinsics (about 2° vs. 0.25° axis error, 13.11.2026).
- Old calibration files are kept as `<name>.<UTC>.bak`.

## Consequences

- No Layer 2 run starts without the hand-eye file.
- The ceiling camera keeps a pure intrinsics calibration.

## Alternatives considered

- Separate hand-eye calibration button: removed, solutions drifted apart from the intrinsics.

Sources: [`../work-plans/apriltag-hand-eye-anchoring.md`](../work-plans/apriltag-hand-eye-anchoring.md),
[`../work-plans/hand-eye-over-opcua.md`](../work-plans/hand-eye-over-opcua.md),
[`../topics/layer2-rules.md`](../topics/layer2-rules.md).

History (how the decision came about): [`apriltag-hand-eye-anchoring.md`](../work-plans/apriltag-hand-eye-anchoring.md), [`hand-eye-over-opcua.md`](../work-plans/hand-eye-over-opcua.md), [`hand-eye-accuracy.md`](../work-plans/hand-eye-accuracy.md), [`calibration-lens-model-per-camera.md`](../work-plans/calibration-lens-model-per-camera.md).
