# 0001 — OPC UA with the OPC 40100 Machine Vision model for the vision Pis

- **Status:** accepted
- **Date:** 2026-09-09

## Context

Two Raspberry Pis (ceiling camera, hand camera) deliver detections to the WSC backend. The
backend is an OPC UA client of the robots (40010) already; the vision side needs a
standard-conformant, discoverable interface.

## Decision

- Each Pi runs an OPC UA server (asyncua) that imports the vendored OPC 40100 nodeset
  (`VisionMachine`, state machine, `ResultManagement`, `DeviceHealth`).
- The vision logic sits in `src/vision_server/`; custom nodes use string NodeIds
  `ns=<vision>;s=VisionMachine.<Name>`.
- Clients resolve namespace indices by URI at runtime, never hard-coded.

## Consequences

- Any 40100 client can use the Pi; the 40100 methods and events stay unchanged for them.
- asyncua pitfalls (event fields, tuple returns, `import_xml` order) are handled in the server code.
- The WSC backend uses its own, simpler path on top: see [0002](0002-part10-program-as-device-root.md).

## Alternatives considered

- Own non-standard address space: no interoperability, rejected in the integration plan.

Sources: [`../topics/vision-system-integration.md`](../topics/vision-system-integration.md) §4.2,
[`../interfaces/vision-server-interface-short.md`](../interfaces/vision-server-interface-short.md),
[`../topics/vision-system.md`](../topics/vision-system.md).

History (how the decision came about): [`legacy-removal-part10-facade.md`](../work-plans/legacy-removal-part10-facade.md), [`opcua-camera-condition-monitoring.md`](../work-plans/opcua-camera-condition-monitoring.md).
