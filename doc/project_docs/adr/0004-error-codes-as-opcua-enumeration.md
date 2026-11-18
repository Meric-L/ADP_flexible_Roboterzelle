# 0004 — Error codes as OPC UA enumeration, named method arguments

- **Status:** accepted
- **Date:** 2026-11-17

## Context

The frontend carried the error texts itself and method arguments were anonymous, so a generic
caller could neither label inputs nor explain a failure.

## Decision

- `VisionErrorCode` is an OPC UA Enumeration DataType (`ns=<vision>;s=VisionErrorCode`) with
  `EnumValues`; the value on the wire is **Int32**.
- The German description of each value is the text shown to operators (e.g. `3 BUSY`:
  „Der Pi ist beschäftigt – danach erneut versuchen.“). The Pi carries the texts.
- Every method input and output has a Name and Description (`add_vision_method`); every error
  output is named `Error`.
- The backend resolves `EnumValues` into `MethodArgument.enum_values` and sets
  `error_code` plus the description as `message` on a failed action.

## Consequences

- Texts change on the Pi only; the frontend shows what it gets.
- Codes 0–7 are fixed: OK, INVALID_STATE, INVALID_ARGUMENT, BUSY, UNKNOWN_RECIPE, DETECTION_FAILED, INTERNAL, CANCELLED.

## Alternatives considered

- Plain Int32 with a frontend text table: duplicates texts, drifts.

Sources: [`../work-plans/pi-backend-discovery.md`](../work-plans/pi-backend-discovery.md),
[`../interfaces/vision-server-interface-short.md`](../interfaces/vision-server-interface-short.md).

History (how the decision came about): [`pi-backend-discovery.md`](../work-plans/pi-backend-discovery.md), [`hand-eye-over-opcua.md`](../work-plans/hand-eye-over-opcua.md).
