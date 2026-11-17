# Hand-eye and anchoring: once at the world tag, then all modules in reach

**Status:** done — 22.09.2026
**Owner:** `Agent: hand-eye calibration and anchoring at the world tag`
**Topic:** apriltag
**Branch:** apriltag

## Goal

The robot **anchors itself once at the start of a localization run** to the
world tag and afterwards finely surveys **all modules within its reach** —
without a world tag needing to be in the image any more. The gap between two
world-tag sightings is closed by the kinematics.

Previously this was not possible: `localize_camera` needs a world tag **in the
same image** as the module. If the hand camera is 0.3 m in front of a module,
the nearest world tag lies one to two meters away, outside the field of view —
the poses would then remain in the camera frame and thus be unusable for
"always referenced to the world tags".

## The math

The camera sits **rigidly** on the robot; only the robot as a whole moves.
`T_flansch_cam` is therefore a constant and is calibrated **once**.

```
Once (hand-eye):        T_flansch_cam

Once per run:           T_world_base = T_world_cam · inv(T_flansch_cam) · inv(T_base_flansch)
   (anchoring at the    with T_world_cam optically from the world tag
    world tag)

Per module afterwards:  T_world_cam  = T_world_base · T_base_flansch(now) · T_flansch_cam
   (no world tag needed)
```

If a world tag comes into view again, the robot is re-anchored. The deviation
between the optical value and the value computed forward is the measured
drift — it is reported, not concealed.

## Already read

- `CLAUDE.md`, `doc/arbeitsplaene/README.md` (no open `apriltag` plan)
- `doc/arbeitsplaene/apriltag-welttag-konzept.md` (predecessor, `done`)
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md`
- `doc/vision-server-interface.md` (payload, methods, `frameId` rule)
- `doc/vision-system-integration.md` section 6.2 (hand-eye, `auto_execute`)
- `doc/altlasten.md` (hand-eye as an open item)

## Affected files

- `src/tagloc/handeye.py` (new) — file format, anchor computation, solver
- `src/vision_server/profiles.py` — `hand_eye_path`
- `src/vision_server/server.py` — hand-eye only for `cam_flange`
- `src/tagloc/localize.py` — `CameraLocalization.origin`, anchor path
- `src/tagloc/cli/calibrate_handeye.py` (new) — the one-time calibration drive
- `src/vision_server/detection/apriltag.py` — hold the anchor, robot pose per job
- `tests/test_handeye.py` (new), `tests/test_layering.py`
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md`

## Interfaces

### Data — `data/handeye/cam_flange.json`

Machine-specific like the calibration, therefore under `data/` and not
versioned. Schema **`wsc.vision.handeye/1`**:

```json
{
  "schema": "wsc.vision.handeye/1",
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T10:15:00Z",
  "poseFlangeCam": {
    "position": [0.041, -0.012, 0.087],
    "orientation": [0.0, 0.0, 0.0, 1.0]
  },
  "rmsPositionM": 0.0012,
  "rmsRotationDeg": 0.08,
  "sampleCount": 14,
  "createdAt": "2026-09-22T10:15:00Z"
}
```

`rmsPositionM`/`rmsRotationDeg` are the scatter of the reconstructed target
location across all captures: if the tag is fixed, `T_base_flansch(i) ·
T_flansch_cam · T_cam_tag(i)` must yield the same pose for all `i`. If it does
not, the calibration is wrong.

### Python

```python
# Module: src/tagloc/handeye.py   (numpy only, no cv2 -- see deviations)
SCHEMA = "wsc.vision.handeye/1"

@dataclass(frozen=True)
class HandEye:
    pose_flange_cam: Pose
    frame_id: str = ""
    calibration_id: str = ""
    rms_position_m: float = float("nan")
    rms_rotation_deg: float = float("nan")
    sample_count: int = 0

def load_hand_eye(path) -> HandEye
def save_hand_eye(path, hand_eye: HandEye) -> None

@dataclass(frozen=True)
class RobotAnchor:
    """Where the robot base stands in the world frame, including provenance."""
    pose_world_base: Pose
    world_tag_id: int          # world tag at which anchoring was done
    spread_m: float            # quality of the world-tag localization during anchoring
    spread_rad: float

def anchor_world_base(pose_world_cam: Pose, pose_base_flange: Pose,
                      pose_flange_cam: Pose) -> Pose:
    """T_world_base = T_world_cam · inv(T_flansch_cam) · inv(T_base_flansch)."""

def camera_pose_from_robot(pose_world_base: Pose, pose_base_flange: Pose,
                           pose_flange_cam: Pose) -> Pose:
    """T_world_cam = T_world_base · T_base_flansch · T_flansch_cam."""

def target_spread(poses_base_flange, poses_cam_tag, pose_flange_cam
                  ) -> tuple[float, float]:
    """Scatter of the reconstructed tag location -- the quality measure."""

def solve_hand_eye(poses_base_flange, poses_cam_tag) -> tuple[Pose, float, float]:
    """Park and Martin over N robot poses to a fixed tag, in numpy.

    Returns T_flansch_cam and the scatter of the reconstructed target location.
    Requires at least 3 poses with rotation about several axes.
    """

# Module: src/tagloc/localize.py
ORIGIN_WORLD_TAGS = "world_tags"   # optical, precise
ORIGIN_ROBOT_POSE = "robot_pose"   # computed forward from anchor + kinematics
# CameraLocalization gets `origin: str`

def anchor_from_localization(localization, pose_base_flange, hand_eye) -> RobotAnchor
def localize_camera_from_anchor(anchor, pose_base_flange, hand_eye) -> CameraLocalization
def anchor_drift(anchor, localization, pose_base_flange, hand_eye) -> tuple[float, float]

# Module: src/vision_server/detection/apriltag.py
def robot_pose_from_parameters(parameters) -> Pose | None
```

CLI for the one-time calibration drive:

```
python -m tagloc.cli.calibrate_handeye --samples fahrt/aufnahmen.json \
    --calibration data/calibration/cam_flange.json \
    --out data/handeye/cam_flange.json
```

Sample list `wsc.vision.handeye.samples/1`: one image and the associated robot
pose per entry; the tag must stay in place for the entire drive.

### Assumption about the robot pose (needs confirmation)

`T_base_flansch` arrives as a **job parameter** at the hand Pi, via the
already existing field `DetectionRequest.parameters` — seven floats
`(x, y, z, qx, qy, qz, qw)`, meters and quaternion xyzw as everywhere.
Rationale: the robot stands still during the `samples_per_job` captures
anyway, so any timestamp matching problem disappears, and no new network
dependency arises between the hand Pi and the robot server.

The alternative — the hand Pi subscribes to `urn:plcm:robot-server:ur5e` as an
OPC UA client — remains retrofittable additively, without the payload changing.

If the parameter is missing and no world tag is in the image, everything
remains in the camera frame as before. No guessing.

### Failure cases

| Case | Behavior |
| --- | --- |
| No hand-eye file | Anchoring not possible; only the optical path applies, warning when opening |
| Not yet anchored, no world tag in the image | Poses in the camera frame, `frameId = cam_flange` |
| Robot pose missing on the job | Same as "not anchored" — no guessing |
| World tag visible **and** anchored | Optical value wins, drift against the anchor is reported as an attribute |
| `solve_hand_eye` with < 3 poses | `ValueError` with a plain-text message |

## Approach

1. `handeye.py`: format, `anchor_world_base`, `camera_pose_from_robot`, solver.
2. `localize.py`: `origin`, `RobotAnchor` hookup.
3. `detection/apriltag.py`: hold the anchor, evaluate the robot pose per job.
4. CLI `calibrate_handeye` for the one-time calibration drive.
5. Tests: synthetic chain, solver against known truth, drift.
6. Update the topic docs; `auto_execute` nevertheless stays `False`.

## Open questions

- Transport of the robot pose (see assumption above) — job parameter vs. OPC UA subscription.
- Whether re-anchoring happens after every module or only at the end of a run, once a world tag happens to be back in the image. For now: always, whenever it is there.

## Deviations from the plan

- **The solver computes in numpy instead of via `cv2.calibrateHandEye`.** The
  function is no longer exported to Python in OpenCV 5.0 — only the
  `CALIB_HAND_EYE_*` constants remain, so in this machine's venv (OpenCV 5.0.0)
  it is not callable. The repo deliberately carries 4.x **and** 5.x
  (`doc/altlasten.md` D3), and a version branch at a spot that runs once in the
  cell's lifetime would be worse than thirty lines of math after Park and
  Martin. Side effect: `tagloc.handeye` stays completely OpenCV-free and is
  listed in `test_layering.PURE_MODULES` instead of `LAZY_CV2_MODULES`.
- **`RobotAnchor` lives in `handeye.py`, not in `localize.py`.** Otherwise the
  two modules would have imported each other. `localize.py` only adds the
  wrapper around it (`anchor_from_localization`, `localize_camera_from_anchor`,
  `anchor_drift`).
- **`AprilTagProfileConfig.hand_eye_path` defaults to `None`.** Only the hand
  Pi gets a path set in `server.py`; the ceiling camera is not mounted on the
  robot, a path for it would be a file that never gets created, and it would
  trigger a warning on every start.
- **`NUMERIC_NOISE_RAD = 1e-6` in the test.** With mathematically exact inputs
  an angular scatter of roughly 2e-08 rad (1.2e-06 degrees) remains from the
  eigenvalue decomposition in `average_poses`. A real error lies orders of
  magnitude above that, as `test_the_spread_exposes_a_wrong_transform` shows
  for an offset shifted by 10 mm.

## After completion

- [x] Status set to `done`, actual interfaces recorded.
- [x] Entry in the index `doc/arbeitsplaene/README.md`.
- [x] Concept in `doc/apriltag-lokalisierung.md` (1.2, new 1.4, 1.5, 9),
      `doc/apriltag-referenz.md`, `doc/vision-server-interface.md`,
      `doc/altlasten.md`.
- [x] Test run: **316 tests, OK** (previously 291; `tests/test_handeye.py` adds
      25 new).

**Open and deliberately so:** `T_flansch_cam` has not yet been measured on the
real hardware. Until `data/handeye/cam_flange.json` exists, only the optical
path works — without a world tag in the image the poses remain in the camera
frame. `auto_execute` stays `False` until the calibration is verified on the
robot.

## Addendum 10.11.2026: anchor always redone after a new Layer 1 scan, module rotation always from Layer 2

**Flow (frontend layer 2, `ApproachPlanPanel.tsx` in webskillcomposition):**

- The ceiling camera (Layer 1) provides a rough base and modules in the world.
- Layer 2 anchors the robot base at a world tag with the hand camera (planned
  1.0 m, `DEFAULT_ANCHOR_STANDOFF_M`): `T_base_tag = FK(q) · T_flange_cam · T_cam_tag`.
- `baseCorrection`: base in world = `T_world_tag · inv(T_base_tag)`, only x, y
  and yaw angle; the height comes from the catalog.
- Modules are measured at 0.35 m (`DEFAULT_APPROACH_STANDOFF_M`) directly in the
  robot base frame and written into the world with the anchor base
  (`measuredModule`).
- Pick & place computes back with the base of the twin (`usePickPlaceRun`).
- If it is the same base, the anchor/world error cancels out of the relative
  position robot↔module. Then only hand-eye at 0.35 m, robot pose, tag
  measurement and the catalog offset tag→module count.

**Fixed bug:**

- After a new Layer 1 scan, the scan set the twin to the rough Layer 1 base.
- Layer 2, however, kept the old anchor correction if, according to Layer 1,
  the robot stood at most 5 cm and 5° at the old place (`robotStayedPut`,
  `ROBOT_STILL_DISTANCE_M` / `ROBOT_STILL_YAW_RAD` in `layer2.ts`).
- Then no anchor was approached, and the proposal contained no robot.
- Consequence: modules lay on the anchor base, the twin on the Layer 1 base.
  The whole earlier base correction entered the grasp as an error.

**New:**

1. Every new Layer 1 scan discards the anchor correction. The next Layer 2 run
   approaches the anchor anew; the whole chain runs again.
2. If modules are placed on the anchor base, the proposal always also contains
   the robot on this base. After "Übernehmen" (German UI button, "Accept"),
   twin and modules fit together.
3. A module's yaw angle always comes from Layer 2, even when the measurement is
   considered ambiguous. The IPPE ambiguity concerns the tilt; only the yaw
   angle is taken over (`levelModulePose`). Ambiguity now only appears as a hint
   in the progress text.
   - The anchor itself still takes over the yaw angle only for an unambiguous
     measurement.

**Where implemented:** webskillcomposition, branches `handauge-schachbrett-offline`
(`5cebd68`) and `layer2-nah-ran-2` (`999c80d`). The remote is archived; the
commits exist locally. In addition, `robotStayedPut`, `ROBOT_STILL_DISTANCE_M`
and `ROBOT_STILL_YAW_RAD` (`layer2.ts`) are removed; new is `robotOriginsEqual`
(same robot origin to 0.1 mm resp. 0.1 mrad).

**Remaining error sources robot↔module:**

- Hand-eye at 0.35 m: the run comparison of 10.11 shows a measured point in the
  image center with 0.4–0.9 mm deviation between calibration runs.
- Robot pose.
- Tag measurement.
- Systematic catalog offset tag→module, see plan `modellfehler-pruefung.md`.
