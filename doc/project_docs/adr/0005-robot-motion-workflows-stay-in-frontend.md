# 0005 — Vision workflows with robot motion stay in the WSC frontend

- **Status:** accepted
- **Date:** 2026-11-18

## Context

Layer 2 approach, tag survey, hand-eye auto calibration, ceiling board calibration and the
cell-watch pause combine robot moves with Pi measurements.

## Decision

- These workflows are sequenced in the React frontend. The Pi measures and computes; the
  backend discovers devices and relays calls.
- The frontend solves IK (`createRobotDriver`, `FlangeKinematics`, `planMove`) and sends joint
  angles via `go_to`; the robot servers offer no Cartesian skill.

## Consequences

- Autonomous runs need an open browser.
- No Pi-side planner and no backend orchestrator exist; the stage-2 interfaces do not exist in code.

## Alternatives considered

- Pi plans, backend drives the robot (branch `vision-workflows-stufe2`): tried, dropped after live tests.
- IK in the backend: new dependency, kinematics validation needed.

Sources: [`../work-plans/pi-workflows-robot-target.md`](../work-plans/pi-workflows-robot-target.md) (dropped),
[`../topics/vision-system-integration.md`](../topics/vision-system-integration.md) §5.3, §6.3,
[`../topics/layer2-rules.md`](../topics/layer2-rules.md).
