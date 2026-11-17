# World-tag concept: four fixed world tags, everything else movable

**Status:** done — 22.09.2026
**Owner:** `Agent: Coordinate transformation moved onto the world-tag concept`
**Topic:** apriltag
**Branch:** apriltag

## Goal

The coordinate chain reflects how the cell is actually built: **only the four
world tags at the edge of the cell are fixed**, everything else is movable —
including the robot, which is itself a module and is always used. The ceiling
camera sees world tags, robot and modules at the same time and from that
delivers the rough overview, including the answer **which world tag is closest
to the robot**. The hand camera then localizes itself at exactly this world tag
and measures the modules precisely — always referenced to the world tags.

Previously this was not possible: `REFERENCE_ROLES` declared, besides `world`,
also `robot_table` and `reference` as **fixed** anchors. The robot table thereby
counted as immovable, the robot did not appear as a module at all, and there was
neither a concept of "the nearest world tag" nor a merging of ceiling and hand
measurement.

## Already read

- `CLAUDE.md` (binding rules, language, work-plan obligation)
- `doc/arbeitsplaene/README.md` (duplicate-work check — no `apriltag` plan open)
- `doc/apriltag-lokalisierung.md` (concept, sections 1–2, 6, 9)
- `doc/apriltag-referenz.md` (conventions, `tagloc` interfaces, tag-map schema)
- `doc/vision-server-interface.md` (payload, `frameId` rule, result nodes)
- `doc/vision-system.md`, `doc/vision-system-next-steps.md` (current state, open items)

## Affected files

- `src/tagloc/tagmap.py` — roles, validation, `nearest_world_tag`
- `src/tagloc/geometry.py` — `average_poses` with optional weights
- `src/tagloc/localize.py` — `CameraLocalization`, world-tag reference, fusion
- `src/vision_server/detection/apriltag.py` — origin, new attributes
- `src/tagloc/cli/transform.py`, `src/tagloc/cli/detect.py` — call sites
- `config/tagmap.example.json` — four world tags, robot tags, module
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md` — update the concept
- `tests/test_tag_map.py`, `tests/test_tag_pipeline.py`, `tests/test_apriltag_source.py`
- `tests/test_welttags.py` (new) — roles, nearest world tag, fusion

## Interfaces

### Roles in the tag map

| Role | World pose | Meaning |
| --- | --- | --- |
| `world` | **fixed, mandatory** | One of exactly four world tags at the edge of the cell. Only this role provides `T_world_cam`. |
| `robot` | **`null`** | Tag on the robot. Movable. Several tags may share the same `moduleId`; they are averaged onto a robot frame. |
| `module` | **`null`** | Tag on a module. Movable. |

The roles `robot_table` and `reference` are dropped without replacement — they
were the cause of the wrong concept. The robot table is no longer a fixed anchor.

### Data / payload

Tag map, schema **`wsc.vision.tagmap/2`** (`config/tagmap.example.json`):

```json
{
  "schema": "wsc.vision.tagmap/2",
  "frameId": "world",
  "anchorTagId": 0,
  "tagFamily": "tag36h11",
  "tags": [
    { "tagId": 0, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [0,0,0], "orientation": [0,0,0,1] } },
    { "tagId": 1, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [2.4,0,0], "orientation": [0,0,0,1] } },
    { "tagId": 2, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [2.4,1.8,0], "orientation": [0,0,0,1] } },
    { "tagId": 3, "role": "world", "sizeM": 0.1, "poseInWorld": { "position": [0,1.8,0], "orientation": [0,0,0,1] } },
    { "tagId": 20, "role": "robot", "sizeM": 0.08, "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [0,0,-0.12], "orientation": [0,0,0,1] }, "poseInWorld": null },
    { "tagId": 21, "role": "robot", "sizeM": 0.08, "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [-0.15,0,-0.12], "orientation": [0,0,0,1] }, "poseInWorld": null },
    { "tagId": 7, "role": "module", "sizeM": 0.05, "moduleId": "MOD-A", "instanceId": "mod-a-1",
      "tagToModule": { "position": [0,0,-0.04], "orientation": [0,0,0,1] }, "poseInWorld": null }
  ]
}
```

Schema `/1` is **migrated** (`reference` → `world`, `robot_table` → `robot`
without world pose), each reinterpretation logged individually. Originally it
was a hard rejection; in practice that brought the entire detection to a
standstill — see `apriltag-tagmap-robustheit.md`.

Result payload per detection, additional `attributes` (additive, existing
fields unchanged):

```json
{
  "source": "ceiling | flange",
  "referenceTagId": 2,
  "referencePosition": [0.31, -0.08, 0.0],
  "referenceOrientation": [0.0, 0.0, 0.0, 1.0],
  "worldTagIds": [0, 1, 2, 3],
  "cameraSpreadM": 0.0021,
  "cameraSpreadDeg": 0.14
}
```

`referenceTagId` is the world tag that is closest to **this module**;
`referencePosition`/`referenceOrientation` are `T_worldtag_module`. The shared
world frame remains in `position`/`orientation` — the four world tags span
**one** world frame, surveyed once via `build_tagmap`.

### Python

```python
# Module: src/tagloc/tagmap.py
WORLD_ROLE = "world"; ROBOT_ROLE = "robot"; MODULE_ROLE = "module"
REFERENCE_ROLES = frozenset({WORLD_ROLE})      # only the world tag is fixed
MOVABLE_ROLES  = frozenset({MODULE_ROLE, ROBOT_ROLE})
EXPECTED_WORLD_TAG_COUNT = 4

def validate_tag_map(tag_map: TagMap) -> list[str]:
    """Returns the complaints about the map, empty = OK.

    Checks: exactly four world tags, each with a world pose; no movable tag with
    a world pose; the anchor is a world tag.
    """

def nearest_world_tag(tag_map: TagMap, pose_world: Pose) -> tuple[int, float] | None:
    """World tag with the smallest distance to `pose_world`, and this distance in m.

    `None` if the map knows no world tag with a world pose.
    """

# Module: src/tagloc/localize.py
@dataclass(frozen=True)
class CameraLocalization:
    pose_world_cam: Pose
    world_tag_ids: tuple[int, ...]   # all contributing world tags
    primary_tag_id: int              # the nearest/best world tag
    spread_m: float                  # spread of the individual estimates
    spread_rad: float

def localize_camera(tag_poses, tag_map, *, max_reprojection_error_px=3.0
                    ) -> CameraLocalization | None:
    """`T_world_cam` from the visible world tags. `None` without a world tag in the image.

    Weighted by confidence; `primary_tag_id` is the world tag with the smallest
    camera distance — the one the hand camera aligns with.
    """

def locate_modules(tag_poses, tag_map, *, localization, frame_id,
                   source="", max_reprojection_error_px=3.0) -> list[ModuleLocation]:
    """T_world_module = T_world_cam @ T_cam_tag @ T_tag_module. Only place in the chain."""

def merge_by_module(locations) -> list[ModuleLocation]:
    """Averages measurements of the same (moduleId, instanceId) — several robot tags."""

def merge_locations(*groups) -> list[ModuleLocation]:
    """Merges ceiling and hand result; the more precise source wins."""
```

`ModuleLocation` gets `reference_tag_id: int`, `pose_in_reference_tag: Pose | None`
and `source: str`.

### Network / operations

Unchanged. Both Pis continue to publish independently under their previous
endpoints; the fusion is a pure function in `tagloc` that the consumer (cell
server/frontend) calls. No new connection between the Pis. `config/tagmap.json`
remains the same file for both Pis.

### Failure cases

| Case | Behavior |
| --- | --- |
| No world tag in the image | `localize_camera` → `None`; poses remain in the camera frame, `frameId` = camera frame (rule unchanged) |
| Map does not have exactly four world tags | `validate_tag_map` complains; `open()` logs a warning but starts — a cell under construction may measure |
| Movable tag with `poseInWorld` | complaint; the world pose is **ignored**, not used |
| Tag-map schema `/1` | `ValueError` with a migration hint on loading |
| World tags contradict each other | `spread_m`/`spread_rad` in the payload; no abort — the number belongs to the result |

## Approach

1. `tagmap.py`: roles, schema `/2`, `validate_tag_map`, `nearest_world_tag`.
2. `localize.py`: `CameraLocalization`, `localize_camera`, world-tag reference in
   `ModuleLocation`, `merge_by_module`, `merge_locations`.
3. `detection/apriltag.py`: origin from `frame_id`, new attributes, averaging of
   several robot tags.
4. `config/tagmap.example.json` to the new schema.
5. Tests: `tests/test_welttags.py` new, adjust existing tests.
6. Update the topic docs `apriltag-lokalisierung.md` and `apriltag-referenz.md`.

## Open questions

- The hand-eye calibration (`T_flansch_cam`) remains open. It is **not needed** for
  this concept: the hand camera localizes itself purely optically at the world
  tag. It is only needed if the robot is to infer its own flange pose from the
  camera pose.
- The real world poses of the four world tags are surveyed once via
  `python -m tagloc.cli.build_tagmap`; the numbers in the example map are
  placeholders.

## Deviations from the plan

- **`geometry.average_poses` got an optional parameter `weights`.** The plan said
  "weighted by confidence" without saying where. The Markley averaging knew no
  weights. The parameter is optional, and without it the function computes
  bit-identically to before — all 26 existing geometry tests pass unchanged. For
  this, `geometry.py` was not listed under "Affected files"; it is added here
  afterwards.
- **`ModuleLocation` additionally got `role`.** Without the role in the result,
  the robot could not be distinguished downstream from a module, and
  `world_tag_for_robot` would have had to guess.
- **Order in `acquire_and_detect`:** `expected_but_missing` must run **before**
  `merge_by_module`. It compares tag IDs, and merging several robot tags into one
  result would otherwise make the swallowed tags appear as missing.
- **`SPREAD_TOLERANCE_M = 0.008` instead of the initially assumed 3 mm.** Measured
  across all four views of the synthetic scene: spread in the worst case 4.21 mm
  at 0.444° (view 0, the most oblique), against an error of the **averaged** camera
  pose of 0.77 mm. The spread is precisely not an accuracy measure but the
  disagreement of the individual estimates — the weighted averaging is
  significantly better than its worst member.
- **Additionally touched topic docs:**
  `doc/vision-server-interface.md` and `doc/praesentation.md` still carried the
  old roles in tables and instructions.

## After completion

- [x] Status set to `done`, actual interfaces recorded.
- [x] Entry in the index `doc/arbeitsplaene/README.md` updated.
- [x] Concept in `doc/apriltag-lokalisierung.md` (sections 1.1–1.4, 3.2, 9) and
      `doc/apriltag-referenz.md` (sections 5, 7) adopted.
- [x] Test run: `PYTHONPATH=src .venv/bin/python3 -m unittest discover -s tests -t .`
      — **291 tests, OK** (previously 260; `tests/test_welttags.py` adds 31 new).

What remains open is the one-time survey of the four real world tags via
`python -m tagloc.cli.build_tagmap`; `config/tagmap.json` does not exist yet,
only the template `config/tagmap.example.json`.
