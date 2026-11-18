# 0010 — Tag map lives on the Pi, set by an OPC UA method

- **Status:** accepted
- **Date:** 2026-09-22

## Context

Adding a module should need no intervention on the Pi.

## Decision

- The tag map stays a local file (`config/tagmap.json`) on each Pi, because `localize_camera`
  needs `T_world_tag` and `sizeM` per image and the Pi must measure without a backend.
- The backend is the maintenance point: it sets the map via the `SetTagMap(TagMapJson)` method;
  the Pi writes the file and applies it immediately (rejected with `BUSY` during jobs and calibrations).
- The map holds only the world tags; module size is stated once in the profile (`tag_size_m`).

## Consequences

- A new module needs one entry in the frontend catalog only.

## Alternatives considered

- Map only in the backend: the Pi would deliver raw `T_cam_tag`, every consumer would need the chaining, against the one-place rule (see [0006](0006-world-tags-as-common-world-frame.md)).

Sources: [`../work-plans/apriltag-tagmap-over-opcua.md`](../work-plans/apriltag-tagmap-over-opcua.md),
[`../topics/apriltag-localization.md`](../topics/apriltag-localization.md) §4.3.
