# Layer 2 run: gradual approach to the tag

**Status:** done — 12.10.2026 (not tested on real hardware, see open questions)
**Owner:** `Agent: Layer 2 approach on the hand Pi`; frontend: session `opcua-f1` (`webskillcomposition`, branch `Ungetestet`)
**Topic:** apriltag
**Branch:** apriltag

## Goal

So far the robot drove in one step to 0.3 m in front of the tag. The target came
from the rough pose from Layer 1. If this pose was a few centimeters off, the tag
lay skewed in the near image or not in the image at all.

Now every anchor and measure step proceeds as follows:
1. The robot first drives to a distance of 0.6 m in front of the tag (approach).
2. The Pi detects the tag in the image and measures its pose in the process.
3. The Pi re-plans the near target from this **measured** pose.
4. Only in the near image is anchoring or measuring performed.

The basis remains [`layer2-lauf-hand-pi.md`](layer2-lauf-hand-pi.md).

## Already read

- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md` (procedure, schemas, failure cases)
- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md` (anchor, `T_flansch_cam`)
- `doc/projektdoku/vision-server-interface.md` section 14

## Affected files

- `src/vision_server/layer2/plan.py`, `src/vision_server/layer2/run.py`
- `tests/test_layer2_plan.py`, `tests/test_layer2_run.py`, `tests/test_layer2_opcua.py`
- `doc/projektdoku/vision-server-interface.md` section 14
- `doc/projektdoku/arbeitsplaene/README.md`: index line

## Interfaces

The OPC UA nodes and the methods remain unchanged. The schemas keep their
versions; all changes are additions only.

### Data

**`RunJson`** (`wsc.vision.layer2.run/1`) has a new option:

```json
"options": { "standoffM": 0.3, "reachM": 0.85, "approachStandoffM": 0.6 }
```

- `approachStandoffM`: distance of the camera in front of the tag for the approach image, in meters.
  - Default: `0.6`.
  - `0` disables the approach. The run is then exactly that of before.
  - Other values must be greater than `standoffM`, otherwise the Pi answers with `INVALID_ARGUMENT`.

**New `purpose` value `"approach"`** in `Layer2Target` and `Layer2Status.steps[]`:
- Every step with `anchor` or `measure` is preceded by a step with `approach`. `stepIndex` continues strictly sequentially.
- The list of steps is fully present in `steps[]` from the start, all with status `pending`.
- An approach step has the same `name`, `moduleId` and `tagId` as the near step after it.

Example with one module:

| stepIndex | purpose | name |
| --- | --- | --- |
| 0 | approach | World tag 583 |
| 1 | anchor | World tag 583 |
| 2 | approach | Module A |
| 3 | measure | Module A |

The client drives an `approach` step exactly like every other step: move to
`flangeInBase` and then call `ReportRobotPose` with the **reported** flange pose.
The target of the near step only changes after the approach has been evaluated.
The client therefore always drives the current `Layer2Target`.

`steps[].detail` after a successful approach, for example:
`"Tag 7 gesehen, nahes Ziel um 42 mm korrigiert"` (German runtime text: "tag 7
seen, near target corrected by 42 mm"). `result` stays `null` during the approach.

### Calculation

`view_target` remains unchanged and is called twice: once with
`approachStandoffM`, once with `standoffM`. Both images lie on the same line of sight.

After the approach image, the following applies:
- **World tag:** A world tag does not appear as a detection, but as a new `RobotAnchor` in the source. Its `T_world_base` comes from the wide image and serves only for aiming. The pose of the world tag itself remains the exact one from the tag map. The near target is planned with:

  ```
  T_base_flansch = inv(T_world_base_wide) · T_world_tag · T_tag_cam · inv(T_flansch_cam)
  ```

  The run does **not** adopt this anchor. `Layer2Status.anchor` stays `null` until the near anchor step succeeds.
- **Module tag:** The detection of the tag yields `T_world_tag`. If the tag map knows the tag, the reported pose is that of the module. Then the CAD offset is computed back: `T_world_tag = T_world_module · inv(tag_to_module)`. The near target is planned with this pose and the anchor of the run.

There is **no** iteration: there is exactly one approach per step. This way the
list of steps is already fixed at the start. In addition, the near image is
itself the measurement; a further correction before it would bring nothing.

### Flange frame

Coordinated with the frontend (session `opcua-f1`, 12.10.2026): "Flansch"
(flange) is the mechanical tool flange without gripper. The following URDF links
apply:
- `flangeInBase` (the frontend computes it via FK from the real joint angles),
- the IK for the target,
- **the robot poses of the hand-eye captures**. `T_flansch_cam` is only valid if it is calibrated to the same link.

| Robot | URDF link |
| --- | --- |
| EVA, Demo1 | `model_6` (last link; `tool_point` is the gripper tip, 17 cm in front of it) |
| UR5e | `flange` |
| FR3 | `eef_attach_point` |

For now the frontend does not send `approachStandoffM`. The default of 0.6 m therefore applies.

### Python

```python
# src/vision_server/layer2/plan.py
APPROACH = "approach"
DEFAULT_APPROACH_STANDOFF_M = 0.6
RunRequest.approach_standoff_m: float          # 0 = off
RunRequest.standoff_for(purpose) -> float
StepSpec.seen_tag: tuple[int, Pose] | None     # measured world pose, replaces the rough one
def find_detection(detections, tag_id) -> dict | None          # also via attributes.tagIds
def tag_in_world_from_detection(detection, tag_id, tag_map) -> Pose   # ValueError
```

### Failure cases

| Case | Behavior |
| --- | --- |
| World tag not seen in the approach image, job error | Approach `failed`, run `aborted` (as with the anchor) |
| Module tag not seen in the approach image, pose not in the world frame | Approach and measure step `skipped` with reason, continue |
| Job error during the approach to a module | Approach `failed`, measure step `skipped`, continue |
| `reached: false` during the approach (e.g. no IK solution) | Approach `skipped`; the near step follows with the target from the rough pose |
| Approach target out of reach | Approach `skipped` without motion; the near step follows with the rough pose |
| Near module target out of reach | Approach and measure step `skipped` without motion |

## Open questions

- **Re-anchoring by incidental capture:** If by chance a world tag lies in the image of a module step (approach or near), the AprilTag source re-anchors (`_locate`: "der optische Wert gewinnt immer", German for "the optical value always wins"). This was already the case before this change. Due to the wide approach image, however, it now happens more often, and the anchor then comes from a greater distance. `Layer2Status.anchor` remains the anchor of the run in this case. The source, however, computes the world poses of the modules with its own new anchor. A remedy would be a job parameter "nicht neu ankern" (German for "do not re-anchor"). Implement if needed.
- Not tested on real hardware. `T_flansch_cam` is still missing (see `apriltag-hand-auge-ankern.md`).

## Deviations from the plan

- The proposal from `opcua-f1` foresaw computing the tag pose in the base frame directly from `T_flansch_cam` and the reported flange pose. Implemented is the equivalent route via the world frame: the source already delivers the pose with the anchor in the world frame, and with the same base it returns to the base frame. The anchor cancels out in the process. For the world tag, the provisional anchor from the wide image serves as the measurement.
- `_evaluate_measure` now also finds the tag when the source combines several tags of a module into one detection (`attributes.tagIds`).

## After completion

- [x] Status `done`, interfaces recorded.
- [x] Index line in `README.md`.
- [x] `vision-server-interface.md` section 14 added.
- [x] Test run: 536 tests, of which 1 failure that has nothing to do with this change (`test_mjpeg_server…viewers_run_faster`, also fails without this change). New: `test_layer2_plan.py` (+3), `test_layer2_run.py` (+8, class `Layer2ApproachTest`).

## Addendum: arm no longer drives into its own cart

Repo `webskillcomposition`, branch `Ungetestet`.

### Problem

During the Layer 2 run the FR3 came very close to its cart or drove into it, even
though the exclusion zone exists (`frontend/src/features/cell-modules/model/cellClearance.ts`,
from `bounds` of ROBOT_FRANKA: x −0.20…0.89 m, y −0.41…0.36 m, table top z = 0;
matches `wagon.stl` with 1.09 × 0.72 m).

Causes:
1. Only points were checked (joint centers, flange, camera), no volume. The FR3 links have a radius of about 6–8 cm, the hand about 10 cm.
2. Hand and fingers up to `tool_point` (0.1034 m in front of the flange) were missing.
3. `legCollision` checked a fixed 10 steps, regardless of how far the motion went.

### Solution

- The arm is modeled as a chain of spheres. The segments are sampled at intervals of at most 2 cm.
- `ARM_RADIUS_M = 0.08` applies to the links, `HAND_RADIUS_M = 0.10` to flange, tool tip and camera.
- The clearance is `TABLE_CLEARANCE_M = 0.05` from the sphere surface; the floor is checked accordingly with `FLOOR_CLEARANCE_M`.
- `flangeKinematics.points()` provides flange and tool tip from the URDF. The field `toolFrom` marks from where the hand radius applies.
- `legCollision` samples so finely that no joint moves more than 0.05 rad per step (at most 200 steps).
- Link0 (foot on the cart) is not checked. The chain starts at joint 1 (z ≈ 0.333 m).
- The return value remains `"Wagen"` (cart) | `"Boden"` (floor) | `null`; the callers remain unchanged.
- Centering corrections (`centreOnTag`) still check only their own target. They are small.

### Consequence and risk

The check is stricter. Some viewpoints close to the cart are skipped ("Sichtpunkt/Weg würde den Wagen berühren — ausgelassen", German runtime text: "viewpoint/path would touch the cart — skipped"). This is intended. If there are too many of them, the radii or `TABLE_CLEARANCE_M` can be reduced specifically.

### Verification

- Unit tests (`cellClearance.test.ts`) and type check.
- Still open on the robot: no viewpoint and no path closer than about 5 cm to the cart plate. The home pose does not count as "Wagen" (cart).

## Addendum 27.10.2026: measure every tag from 3 views and merge them

### Reason

- So far the Layer 2 run measured each tag only from the centered position: one view, 2–8 images.
- Modules were only shifted. Their rotation stayed that from Layer 1, because the orientation of a single tag at ~35 cm is often uncertain and can be mirrored.
- The anchor set the robot base from this one measurement.

### Flow

- Approach and centering as before.
- Then 3 further views around the tag center, at the same distance (standoff of the viewpoint):

| View | Rotation |
| --- | --- |
| 1 | +12° about tag x |
| 2 | −12° about tag x |
| 3 | +12° about tag y |

  Same geometry as for the robot tags.
- Each view is planned with roll angles 0/90/180/−90° and IK starting values: current joints, home pose, center of range, angles of already measured views. Cart and path check as for the drive.
- Views that are unreachable or not measurable are skipped and named in the status line.
- `T_base_tag` per view = FK(read joints) · T_flange_cam · T_cam_tag.

### Merging (`fuseTagViews`)

File: `frontend/src/features/cell-modules/model/tagViews.ts` (repo `webskillcomposition`).

Input: the centered measurement and the views.

- Position = mean.
- Orientation = mean of the non-mirrored (unambiguous) views, otherwise the planned orientation.
- Spread = RMS.
- Outliers: from 3 measurements on, at most one is discarded if it lies more than 3 mm resp. more than 1° from the mean of the others and the others agree with each other to 1.5 mm / 0.5°.
- Fewer than 2 valid measurements: as before only the centered measurement ("nur 1 Ansicht", German status text: "only 1 view").

### Module result

- Shift from the mean.
- Rotation is adopted if at least 2 unambiguous views are available and the angular spread is ≤ 0.5° (`LAYER2_HEADING_MAX_SPREAD_DEG`). Then the level module pose is computed from the tag pose as in Layer 1 (`levelModulePose`: yaw angle only, height remains that of the floor).
- Otherwise the rotation from Layer 1 is kept ("Drehung aus Layer 1 behalten", German status text: "keep rotation from Layer 1").

### Anchor result

- Base correction from the mean.
- Heading only under the same condition. Previously, a single non-mirrored measurement was sufficient.

### Display in the status line

- "N Ansichten, Streuung x,x mm / y,y°" (German UI text: "N views, spread x.x mm / y.y°")
- skipped and discarded views
- for a rotated module "Δ x,x°"

### Limits

- About 20–30 s longer per viewpoint.
- A fixed offset of the hand-eye calibration does not average out. Recalibrate for that.
- Close to the cart, not all views are reachable.

### Interfaces

- No change on the Pi. Only `MeasureTags` is affected.
- Frontend, repo `webskillcomposition`, branch `Ungetestet`:

| Commit | Content |
| --- | --- |
| `eaab176` | shared part `tagViews.ts`, robot tags unchanged |
| `a9137d3` | Layer 2 run |
| `53a8c55` | cart check looser: links 6 cm, hand 8 cm, edge 2 cm |
| `6bce599` | Layer 2 proposal as boxes and base marker instead of model copies |

### Open

- Not yet checked on the robot.
- Expectation: repeatability of the module position ~1–3 mm, rotation ~0.3°.
