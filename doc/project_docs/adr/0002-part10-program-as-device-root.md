# 0002 — Part 10 `VisionProgram` as the device root the backend discovers generically

- **Status:** accepted
- **Date:** 2026-11-17

## Context

The WSC frontend used hand-written bindings per Pi method and variable. The backend only
discovered robots (`MotionDeviceType`).

## Decision

- `Objects/VisionProgram` (`ProgramStateMachineType`) is the one entry point per Pi. It has the
  Part 10 methods, `ParameterSet`, `ResultSet`, `Status/*` and references to all vision methods.
- The backend treats every object under `Objects` with a program skill as a **device** and finds
  actions and variables structurally. `kind` is `vision` if the MachineVision namespace is
  present, else `device`; robots keep `kind: "robot"`.
- The frontend calls devices through one generic path (`visionDevice.ts`, `deviceCall.ts`, `useDevice.ts`).
- `Start` returns only after the state is `Running`, or `Ready` with `ErrorCode` ≠ 0.
- `BusyReason` (German, `""` = free) tells why the Pi refuses new work.

## Consequences

- A new Pi or method needs no frontend change; actions are keyed by `normalize_capability_name`.
- `ResultSet` holds only `JobId`, `ErrorCode`, `ExecutionMode`, `LatestResultJson`.

## Alternatives considered

- Disguise the vision server as a DI `MotionDeviceType`: violates 40100, the frontend would treat the camera as a robot.
- Keep per-method frontend bindings: duplicated knowledge, drifts from the Pi.

Sources: [`../work-plans/pi-backend-discovery.md`](../work-plans/pi-backend-discovery.md),
[`../interfaces/part10-program-interface.md`](../interfaces/part10-program-interface.md),
[`../components/part10/README.md`](../components/part10/README.md), [`../layers.md`](../layers.md).
