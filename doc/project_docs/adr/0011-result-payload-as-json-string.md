# 0011 — Detection result as JSON string in `ResultContent[0]`

- **Status:** accepted
- **Date:** 2026-09-09

## Context

The backend needs poses and module IDs from a 40100 result. asyncua clients cannot decode custom
structures without `load_data_type_definitions()` (asyncua issue #1693).

## Decision

- The result is one JSON string (schema `wsc.vision.detections/1`) in `ResultContent[0]` and in `ResultSet/LatestResultJson`.
- Every pose carries a `frameId`; metres, quaternion `xyzw`.
- The `ResultManagement` methods are not used; the result arrives in the event.

## Consequences

- Clients need only a JSON parser; the schema is versioned in the string.

## Alternatives considered

- Custom Structured DataType: raw bytes arrive without type loading, rejected.
- `Double[]`: no module ID (string), no confidence or attributes, rejected.

Sources: [`../topics/vision-system-integration.md`](../topics/vision-system-integration.md) §4.2–4.3,
[`../interfaces/vision-server-interface-short.md`](../interfaces/vision-server-interface-short.md).

History (how the decision came about): [`legacy-removal-part10-facade.md`](../work-plans/legacy-removal-part10-facade.md), [`opcua-camera-condition-monitoring.md`](../work-plans/opcua-camera-condition-monitoring.md).
