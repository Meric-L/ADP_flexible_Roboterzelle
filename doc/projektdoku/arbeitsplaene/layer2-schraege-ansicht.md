# Layer 2: oblique view of tags that lie out of reach head-on

**Status:** done — 13.10.2026 (not tested on real hardware)
**Owner:** John Glanz, `Agent: Layer 2 oblique view`
**Topic:** apriltag
**Branch:** apriltag

## Goal

The planner has so far always set the hand camera **head-on** in front of a tag,
i.e. for a world tag on the floor, vertically above it. The FR3, however, stands
on a cart, about 1 m above the floor, and the world tags lie 0.9–1 m beside the
base. There, even the approach (0.6 m head-on) was already out of reach, and the
anchor failed.

Now the camera tilts around the tag center toward the base, in steps from 15°
up to `maxTiltDeg` (default 60°). Distance and view onto the tag center stay the
same. The most head-on view that lies within reach with some margin is taken.

## Already read

- `arbeitsplaene/layer2-lauf-hand-pi.md` (calculation `view_target`, options)
- `arbeitsplaene/layer2-annaeherung.md` (approach step, `approachStandoffM`)
- `vision-server-interface.md` Section 14

## Affected files

- `src/vision_server/layer2/plan.py`: `view_target(..., tilt_deg)`, `plan_step(..., max_tilt_deg)`, option `maxTiltDeg`
- `src/vision_server/layer2/run.py`: re-planning passes `max_tilt_deg` through; `Layer2Target.tiltDeg`
- `tests/test_layer2_plan.py`: `TiltedViewTest`

## Interfaces

### Data

**`RunJson`**, additionally `options`:

```json
"options": { "standoffM": 0.3, "approachStandoffM": 0.6, "reachM": 0.85, "maxTiltDeg": 60 }
```

- `maxTiltDeg` is between 0 and 80. `0` always means head-on, the old behavior. A value outside this range gives `INVALID_ARGUMENT`.

**`Layer2Target`**, additionally:
- `"tiltDeg": 45.0` states how obliquely the camera looks at the tag. `0` means head-on.

### Python

```python
# src/vision_server/layer2/plan.py
def view_target(pose_world_tag, pose_world_base, pose_flange_cam, standoff_m, tilt_deg=0.0) -> ViewTarget
def plan_step(spec, pose_world_base, pose_flange_cam, standoff_m, reach_m, max_tilt_deg=0.0) -> PlannedStep
ViewTarget.tilt_deg: float
RunRequest.max_tilt_deg: float = 60.0
```

### Calculation

```
T_tag_cam(tilt) = Rot(a, tilt) · T_tag_cam(frontal),   a = z × (base in tag frame, laid into the tag plane)
```

The rotation is around the tag center. The camera thus stays `standoff_m` away
from the tag and looks at its center.

Choice of view:
1. The smallest tilt angle with `reach ≤ 0.9 · reachM`.
2. If there is none, the smallest with `reach ≤ reachM`.
3. Otherwise the view with the smallest reach. It is then marked `reachable: false` and skipped.

### Failure cases

As before. A tag that is also not reachable at an angle stays `skipped` with
„außer Reichweite“ (out of reach).

## Open questions

- Reach is measured from the base origin, not from the shoulder. Whether the FR3 actually reaches a pose with the required orientation is only checked later by the IK in the frontend („Geist-Vorschau“, ghost preview).

## After completion

- [x] Status `done`.
- [x] Index line in `README.md`.
- [x] `vision-server-interface.md` Section 14.1.
- [x] Test run (see commit).
