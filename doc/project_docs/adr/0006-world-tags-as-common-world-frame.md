# 0006 — Four fixed world tags as the common world frame

- **Status:** accepted
- **Date:** 2026-09-22

## Context

Earlier, `robot_table` and `reference` also counted as fixed anchors, so the robot was no module
and ceiling and hand measurements could not be merged.

## Decision

- Only tags with `role: "world"` **and** `poseInWorld` (four, at the cell edge) are fixed and give `T_world_cam`.
- `robot` and `module` tags are movable; the robot is treated like a module.
- The ceiling camera gives the overview and the world tag nearest to the robot; the hand camera
  localizes at that tag and measures modules precisely.
- `T_world_module = T_world_cam · T_cam_tag · T_tag_module` is evaluated only in `tagloc/localize.py`.
- Tag meaning (role, module, size) lives only in the tag map, never in code.

## Consequences

- World tags have their own printed size (`sizeM` in the map); all module tags share `tag_size_m` of the profile.
- `tagToModule` is applied in one place only (currently the frontend), so the Pi map holds none.

## Alternatives considered

- Several fixed anchor roles: robot table could not move.

Sources: [`../work-plans/apriltag-world-tag-concept.md`](../work-plans/apriltag-world-tag-concept.md),
[`../topics/apriltag-localization.md`](../topics/apriltag-localization.md) §3.3,
[`../topics/apriltag-reference.md`](../topics/apriltag-reference.md).
