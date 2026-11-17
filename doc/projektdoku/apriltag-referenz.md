# AprilTag functionality — reference

Function reference for the package `src/tagloc/`: what each module can do, what the
file formats look like, how the scripts are operated, and what arrives in the OPC UA
result.

Neighbouring documents: [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md)
(concept and why it is built this way), [`apriltag-e2e-test.md`](apriltag-e2e-test.md)
(how to test it end to end, including with the Pi camera),
[`vision-server-interface.md`](vision-server-interface.md) (OPC UA side).

---

## 1. Terms and conventions

| Term | Meaning |
|---|---|
| `T_a_b` | Pose of `b` expressed in `a`; as a 4×4 matrix. Chaining: `T_a_c = T_a_b · T_b_c` |
| Pose | `np.ndarray` of shape `(4, 4)`, `float64`, homogeneous. No dedicated type — numpy is enough, and every function says in its name which frame is meant |
| Units | **Meters** and **radians**, throughout, without exception |
| Quaternion | **xyzw**, as in the payload (`"rotation": "quaternion_xyzw"`) and as three.js expects it — *not* wxyz |
| Camera frame | OpenCV optical frame: +X right in the image, +Y down, +Z along the optical axis forward. As `frameConvention` the string `z_forward_x_right_y_down` |
| World frame | defined by the anchor tag of the tag map, `frameId: "world"` |

**Rule:** A frame name alone does not say where +Z points. Every camera-relative pose
therefore carries `frameConvention`. Whoever reads the payload never assumes `frameId`;
they read it.

---

## 2. Module overview

| Module | Dependencies | Task |
|---|---|---|
| `tagloc.identity` | **stdlib only** | `"<name>#<mtime>"` of calibration and tag map, for `configurationId` |
| `tagloc.modes` | **stdlib only** | the three overlay modes of the live stream and their labels |
| `tagloc.observations` | numpy types | `TagObservation` and `TagPose` — the types between image and mathematics |
| `tagloc.geometry` | numpy | build, chain, invert and average poses; convert to position+quaternion |
| `tagloc.calibration` | numpy, `json` | read/write calibration file, check and scale resolution |
| `tagloc.tagmap` | numpy, `json` | read/write tag map; place tags from co-observations in a shared frame |
| `tagloc.localize` | numpy | tag poses + tag map → module poses |
| `tagloc.boards` | cv2 | find chessboard/ChArUco in a frame, compute calibration |
| `tagloc.detector` | cv2 *or* `pupil_apriltags` | find tags in the grayscale image → `TagObservation` |
| `tagloc.pose` | cv2 | `TagObservation` → `T_cam_tag` including reprojection error and ambiguity |
| `tagloc.overlay` | cv2 | draw detection results into the image — live stream and CLI |
| `tagloc.frames` | cv2 / picamera2 | frame sources: Pi camera, webcam, image folder, single image, `SharedCamera` |
| `tagloc.cli.*` | everything | four command-line tools |

The dependency direction is a hard rule, not a question of style:

- `identity` and `modes` do **not even need numpy**. That is why the vision server can
  build the `configurationId` already when constructing the source, and the live-stream
  publisher can normalise the mode without numpy or OpenCV being on the import chain.
- `geometry`, `calibration`, `tagmap`, `localize` and `observations` do **not import
  cv2**. They are thus importable and testable on every machine, independently of the
  installed OpenCV version (locally 5.x, Pi 4.x, with a differing `aruco` API).
- `tagloc` imports **nothing** from `vision_server`. The connection runs in one
  direction only.

---

## 3. `tagloc.geometry` — poses and chaining

```python
Pose = np.ndarray   # (4, 4), float64

def identity() -> Pose
def from_rotation_translation(R: np.ndarray, t: Sequence[float]) -> Pose
def from_rvec_tvec(rvec: Sequence[float], tvec: Sequence[float]) -> Pose
def from_position_quaternion(position: Sequence[float],
                             orientation_xyzw: Sequence[float]) -> Pose

def compose(*poses: Pose) -> Pose
def invert(pose: Pose) -> Pose

def to_position_quaternion(pose: Pose) -> tuple[
    tuple[float, float, float],                  # position in meters
    tuple[float, float, float, float],           # quaternion xyzw, w >= 0
]

def translation_distance_m(a: Pose, b: Pose) -> float
def rotation_distance_rad(a: Pose, b: Pose) -> float
def average_poses(poses: Sequence[Pose]) -> Pose
def pose_to_dict(pose: Pose) -> dict[str, list[float]]
    # {"position": [x, y, z], "orientation": [x, y, z, w]}
def pose_from_dict(data) -> Pose   # inverse of pose_to_dict
```

`pose_to_dict`/`pose_from_dict` are the shared JSON hinge for **every** data format in
the project that stores a pose: calibration, tag map, hand-eye file and the CLI exchange
format of `transform` (section 9.3).

Notes that matter in operation:

- `from_rvec_tvec` computes Rodrigues **in numpy**, not via `cv2.Rodrigues`. This keeps
  the module cv2-free.
- `to_position_quaternion` normalises the sign to `w >= 0`. Without this normalisation,
  two mathematically identical rotations would produce different numbers, and every
  golden-value test would fail at random.
- `average_poses` averages the translation arithmetically and the rotation via the
  principal eigenvector of the accumulated quaternion matrix — component-wise averaging
  of quaternions is wrong. It is needed for `samples_per_job`.

---

## 4. `tagloc.calibration` — camera calibration as data

```python
@dataclass(frozen=True)
class CameraCalibration:
    calibration_id: str
    frame_id: str
    image_size: tuple[int, int]
    camera_matrix: np.ndarray          # (3, 3)
    distortion: np.ndarray             # (5,)
    rms_reprojection_error: float
    sample_count: int
    board: Mapping[str, Any]

    @property
    def camera_params(self) -> tuple[float, float, float, float]:   # fx, fy, cx, cy

def load_calibration(path: Path) -> CameraCalibration
def save_calibration(path: Path, calibration: CameraCalibration) -> None
def check_resolution(calibration: CameraCalibration, image_size: tuple[int, int]) -> None
def scale_to_resolution(calibration: CameraCalibration,
                        image_size: tuple[int, int]) -> CameraCalibration

PLACEHOLDER_CALIBRATION_ID = "placeholder-unkalibriert"
def default_calibration(resolution: tuple[int, int], frame_id: str = "") -> CameraCalibration
```

- `default_calibration` returns a calibration roughly estimated from the field-of-view
  width — **no substitute** for a real calibration run (poses are then plausibly oriented,
  but not dimensionally accurate). It exists only to keep detection, overlay and the job
  path runnable without a calibration file; active only with
  `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` or
  `AprilTagProfileConfig.allow_placeholder_calibration=True` (section 10.1).
  `calibration_id` is then `PLACEHOLDER_CALIBRATION_ID`, recognisable in the payload.

- `calibration_identity(path)` (from `tagloc.identity`, re-exported here) forms
  `"<name>#<mtime>"`, **without opening the file** and without numpy. This is the value
  that goes into the payload as `configurationId` and answers, in case of an error, the
  question of which calibration it was.
- `check_resolution` raises `ValueError` if the calibration resolution and the image size
  do not match. Without this check, all poses are wrong by the scale factor — plausibly
  wrong, and therefore inconspicuous.
- `scale_to_resolution` is the deliberate way out when working deliberately at a
  different resolution: `fx, fy, cx, cy` are scaled, the distortion coefficients stay.

### Board detection (`tagloc.boards.detect_board`)

For `chessboard` (currently both Pis), `detect_board` first tries
`cv2.findChessboardCornersSB` — the newer "sector-based" detector. It is more robust
against poor light, blur and strong tilt than the classic method, and already sub-pixel
accurate, so no additional `cornerSubPix()` is needed. It falls back to the classic
`cv2.findChessboardCorners` + `cornerSubPix` (11×11 window) if this OpenCV version does
not know SB (Pi: OpenCV 4.x) or SB does not find the board in this frame.

**Deliberately without `CALIB_CB_ACCURACY`:** the flag promises better corner accuracy,
but according to the OpenCV documentation it is an "own, slower algorithm". Measured at
~4000 px width (ceiling-camera format, 12 MP), it took 4.45 s versus 0.87 s without it
(factor 5), and 0.06 s with the classic method (factor 73). The calibration capture always
runs on the **full** camera frame; with the flag, the OPC UA request on the Pi ran into
the timeout (found live on 2026-09-23, same day as its introduction).

**Not obvious:** SB returns the corner points in the **exact reverse order** compared to
the classic method (checked empirically with a synthetic test image, 2026-09-23 — after
reversing, the positions agree to < 0.1 px). `detect_board` compensates for this
(`corners[::-1]`) before it returns the corners. Had this been taken over unfiltered, the
image-to-world-point assignment in `_chessboard_object_points` would have been silently
swapped: `calibrate_from_samples` would have run through, but with a wrong rather than
merely imprecise result, without any error being noticed. Regression guard for this:
`tests/test_tag_pipeline.py`,
`SyntheticCalibrationTest.test_corner_order_matches_the_classic_convention`.

**Can still hang on Pi hardware even after `CALIB_CB_ACCURACY` was removed:** found live
on the ceiling camera (12 MP), 2026-09-28 — a single call produced no result for over
five minutes (no error, no timeout log, simply never finished; never observed on the hand
Pi at 640×480). The actual cause of the hang has not been clarified, only contained:
`CalibrationSession._process()` (`calibration_session.py`) limits each `detect_board`
call with `CAPTURE_DETECT_TIMEOUT_S` (20 s) and then discards the affected single-worker
pool, instead of letting it block every further capture — details and rationale there.

This finding was at the same time the trigger for a larger change: capture (`capture()`)
and evaluation (`_process()`, `detect_board` per image + `calibrate_from_samples`) have
since been decoupled (`FinishCalibration` only starts the evaluation in the background, see
`vision-server-interface.md` section 12). `detect_board` therefore no longer runs in the
OPC UA response path of `CaptureCalibrationSample`, but only after `FinishCalibration`,
where a hang costs "only" a single capture (skipped, see `CAPTURE_DETECT_TIMEOUT_S`)
instead of blocking the entire session. Whether `detect_board` should in future also
downscale the full sensor frame for the calibration itself (analogous to the existing
downscale of live stream/job) remains an open question — now with significantly lower
risk, since a hang no longer paralyses the whole session.

### File format `data/calibration/<frame_id>.json`

```jsonc
{
  "schema": "wsc.vision.calibration/1",
  "calibrationId": "cam_ceiling@2026-09-17T14:03:11Z",
  "frameId": "cam_ceiling",
  "imageSize": [2028, 1520],
  "cameraMatrix": [[1520.4, 0, 1012.8], [0, 1519.7, 758.2], [0, 0, 1]],
  "distortionCoefficients": [-0.2841, 0.0912, 0.0003, -0.0001, -0.0148],
  "rmsReprojectionError": 0.31,
  "sampleCount": 22,
  "board": { "type": "charuco", "cols": 7, "rows": 5,
             "squareSizeM": 0.030, "markerSizeM": 0.022, "dictionary": "DICT_4X4_50" },
  "createdAt": "2026-09-17T14:03:11Z"
}
```

The file belongs to **one physical camera**, not to the repo — `data/` is in `.gitignore`,
and that is correct here. Each Pi generates its own.

---

## 5. `tagloc.tagmap` — tags in a shared coordinate system

```python
@dataclass(frozen=True)
class TagEntry:
    tag_id: int
    role: str                       # "world" (fixed) | "robot" | "module" (movable)
    size_m: float
    module_id: str = ""
    instance_id: str = ""
    pose_in_world: Pose | None = None      # None = movable, is measured
    tag_to_module: Pose = identity()       # constant CAD offset

@dataclass(frozen=True)
class TagMap:
    frame_id: str
    anchor_tag_id: int
    tag_family: str
    entries: Mapping[int, TagEntry]

    def __getitem__(self, tag_id: int) -> TagEntry
    def reference_poses(self) -> Mapping[int, Pose]      # everything with a known world pose
    def module_entries(self) -> Sequence[TagEntry]       # role == "module"

def load_tag_map(path: Path) -> TagMap
def save_tag_map(path: Path, tag_map: TagMap) -> None
def empty_tag_map(frame_id: str = "world", anchor_tag_id: int = 0) -> TagMap
    # empty map -- operation without a map remains a normal case
```

### The placement functions

```python
def place_tags(observations: Sequence[Mapping[int, Pose]],
               *, anchor_tag_id: int) -> dict[int, Pose]
def merge_tag_poses(poses: Sequence[Pose]) -> Pose
def residuals(observations: Sequence[Mapping[int, Pose]],
              placed: Mapping[int, Pose]) -> dict[tuple[int, int], tuple[float, float]]
def with_world_poses(tag_map: TagMap, placed: Mapping[int, Pose]) -> TagMap

def observed_tag_ids(observations: Sequence[Observation]) -> set[int]
    # all tag IDs ever seen
def relative_poses(observations: Sequence[Observation]
                   ) -> dict[tuple[int, int], list[Pose]]
    # camera-free relations T_a_b from co-observations -- precursor of place_tags
def missing_tags(observations: Sequence[Observation],
                 placed: Mapping[int, Pose]) -> list[int]
    # observed tags without a path to the anchor
def format_residual_report(
    residual_map: Mapping[tuple[int, int], tuple[float, float]]
) -> str
    # human-readable summary of the closure errors, largest first
```

These four are the building blocks from which `place_tags`/`residuals` are composed;
`cli/build_tagmap.py` uses them directly for the step-by-step construction of the map
(section 9.4).

`place_tags` is the core. Input is a sequence of captures; each capture is a dict
`tag_id → T_cam_tag`, i.e. everything that was visible together in **one** image. From
this:

1. Each pair `(a, b)` from the same capture yields the camera-free relation
   `T_a_b = invert(T_cam_a) · T_cam_b`. The camera pose cancels out — which is why the
   camera does not need to be known.
2. These relations form a graph: nodes are tag IDs, edges are co-observations.
3. A breadth-first search from the anchor tag chains the relations into poses in a single
   frame. The anchor gets the identity.
4. Multiple paths to the same tag are merged via `merge_tag_poses`.

Tags without a path to the anchor do **not** appear in the result — they are reported,
not silently dropped.

`residuals` returns, per edge, `(position error in m, angle error in rad)` between the
measured relation and the relation reconstructed from the map. **This is the quality
figure of the map.** A series of images that closes a ring must close; if it does not,
the calibration or the entered tag size is wrong.

> **Accuracy limit.** Averaging is over the neighbours that are already placed at the time
> of the visit, not over all edges of the finished graph. The result is deterministic, but
> not a global adjustment: errors accumulate over long chains. For a cell with few tags
> this is enough; `residuals` is the measure of whether it still is. The next step would
> be a weighted least-squares adjustment over all edges — needed at the latest once the
> closure errors approach the tolerances.

### File format `config/tagmap.json`

```jsonc
{
  "schema": "wsc.vision.tagmap/2",
  "frameId": "world",
  "anchorTagId": 0,
  "tagFamily": "tag36h11",
  "tags": [
    // Four world tags at the edge — the only fixed items.
    { "tagId": 0, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 1, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 2, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 1.800, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 3, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 1.800, 0], "orientation": [0, 0, 0, 1] } },

    // The robot: movable, several tags on the same base.
    { "tagId": 20, "role": "robot", "sizeM": 0.080,
      "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [0, 0, -0.120], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null },

    { "tagId": 7, "role": "module", "sizeM": 0.050,
      "moduleId": "MOD-A", "instanceId": "mod-a-1",
      "tagToModule": { "position": [0, 0, -0.040], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null }
  ]
}
```

`validate_tag_map(tag_map) -> list[str]` returns the objections to the map (exactly four
world tags, each one surveyed; no movable tag with a world pose; anchor is a world tag; at
least one robot tag). An empty list means all is in order. Deliberately a report instead
of an exception — a cell under construction may be measured; `AprilTagDetectionSource.open`
logs and carries on.

`nearest_world_tag(tag_map, pose_world) -> tuple[int, float] | None` returns the nearest
world tag and its distance in meters.

A map in schema `/1` is **migrated, not rejected**, on load: `reference` becomes `world`
(it was always a fixed anchor with a world pose), `robot_table` becomes the movable `robot`
and **loses its world pose** in the process. Every reinterpretation is logged individually
— nothing happens silently. Module names, instance IDs and CAD offsets are kept. To switch
permanently: have it rewritten once with `build_tagmap`; `save_tag_map` always writes `/2`.

An *unknown* schema and a **typo in the role** continue to raise `ValueError` — otherwise a
tag would silently be neither anchor nor module.

`AprilTagDetectionSource.open` catches these errors and carries on **without a map**: the
tags are then reported as `TAG-<id>` in the camera frame. An unusable text file must not
shut down the entire vision system.

`config/` is versioned — the tag map describes the cell layout and belongs in the repo. Not
in `data/`, `concept/` or `hardware/`: those are all gitignored, and files there would be
silently invisible to `git add`.

---

## 6. `tagloc.detector` and `tagloc.pose` — from image to pose

```python
@dataclass(frozen=True)
class TagObservation:
    tag_id: int
    corners: tuple[tuple[float, float], ...]   # exactly 4, fixed order
    decision_margin: float | None = None

class TagDetector(Protocol):
    family: str
    def detect(self, gray: np.ndarray) -> list[TagObservation]: ...

def build_detector(family: str = "tag36h11", backend: str = "aruco") -> TagDetector
```

Two implementations behind the same protocol: `ArucoTagDetector` (`cv2.aruco`, the
standard path) and `PupilAprilTagDetector` (`pupil_apriltags`, for the transition and for
comparison measurements). Pose estimation deliberately does **not** live in the detector —
otherwise pose quality would depend on the backend and would not be comparable.

```python
@dataclass(frozen=True)
class TagPose:
    tag_id: int
    pose_cam_tag: Pose
    reprojection_error_px: float = float("nan")
    ambiguity_ratio: float = 0.0    # error of the best / error of the second-best
                                    # solution, in (0, 1]; near 1 = ambiguous
    observation: TagObservation | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ambiguous(self) -> bool  # ambiguity_ratio > 0.6

    def is_usable(self, max_reprojection_error_px: float | None = None) -> bool
        # False for NaN/Inf reprojection error (otherwise payload.py would abort
        # with INTERNAL instead of DETECTION_FAILED at allow_nan=False);
        # otherwise False only if above the threshold

def undistort_corners(corners, calibration: CameraCalibration) -> np.ndarray
def estimate_tag_pose(observation: TagObservation, size_m: float,
                      calibration: CameraCalibration) -> TagPose
def estimate_tag_poses(observations: Sequence[TagObservation],
                       calibration: CameraCalibration, *,
                       tag_map: TagMap | None = None,
                       default_size_m: float = 0.05,
                       max_reprojection_error_px: float | None = None,
                       ) -> list[TagPose]
```

Two points that the prototype currently gets wrong and that are fixed here:

- **The corners are undistorted.** `detect_apriltags.py` (prototype, removed on 30.10.2026)
  passes `D` only to `drawFrameAxes` and estimates the pose on the distorted image
  ([`altlasten.md`](arbeitsplaene/altlasten.md), "Explicitly not on this list").
- **Ambiguity is reported.** A flat-seen square marker has two almost equally good
  solutions. `solvePnPGeneric` with `SOLVEPNP_IPPE_SQUARE` returns both; the ratio of the
  residual errors is `ambiguity_ratio`. An ambiguous tag is not wrong, but it must not be
  approached unchecked — which is why `ambiguous` appears in the payload.

`estimate_tag_poses` takes the tag size **per tag from the tag map**, not from a global
constant. World board and module tags have different sizes; a wrong size scales the
distance linearly and otherwise goes unnoticed. For several tags in one image, the four
corners are undistorted together, and camera matrix, zero distortion and object points of
equal tag size are prepared once. `estimate_tag_pose` for a single tag follows the same
computation path; unsolvable tags are still skipped individually.

---

## 7. `tagloc.localize` — from tag to module

```python
@dataclass(frozen=True)
class ModuleLocation:
    module_id: str
    instance_id: str
    pose: Pose
    frame_id: str
    tag_id: int
    reprojection_error_px: float
    ambiguous: bool
    confidence: float

    role: str                       # "module" | "robot", from the tag map
    source: str                     # "ceiling" | "flange"
    reference_tag_id: int           # nearest world tag, -1 = unknown
    pose_in_reference_tag: Pose | None    # T_worldtag_module
    attributes: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class CameraLocalization:
    pose_world_cam: Pose
    world_tag_ids: tuple[int, ...]  # all contributing world tags
    primary_tag_id: int             # the nearest world tag
    spread_m: float                 # spread of the individual estimates
    spread_rad: float
    origin: str = ORIGIN_WORLD_TAGS # or ORIGIN_ROBOT_POSE, see below

def localize_camera(tag_poses: Sequence[TagPose], tag_map: TagMap, *,
                    max_reprojection_error_px: float = 3.0) -> CameraLocalization | None
def locate_modules(tag_poses: Sequence[TagPose], tag_map: TagMap, *,
                   localization: CameraLocalization | None, frame_id: str,
                   source: str = "",
                   max_reprojection_error_px: float = 3.0) -> list[ModuleLocation]
def merge_by_module(locations, *, tag_map: TagMap | None = None) -> list[ModuleLocation]
def merge_locations(*groups) -> list[ModuleLocation]
def world_tag_for_robot(tag_map: TagMap, locations) -> tuple[int, float] | None

def confidence_from(tag_pose: TagPose, *,
                    max_reprojection_error_px: float = 3.0) -> float
    # from reprojection error + ambiguity, in [0, 1] -- deliberately not constant 1.0,
    # which would be information without content
def merge_samples(samples: Sequence[Sequence[ModuleLocation]]) -> list[ModuleLocation]
    # several captures of the same job -> one result per module; the number of
    # averaged captures goes into attributes as sampleCount
def robot_locations(locations: Sequence[ModuleLocation]) -> list[ModuleLocation]
    # role == "robot", usually exactly one hit
def expected_but_missing(tag_map: TagMap,
                         located: Sequence[ModuleLocation]) -> list[str]
    # modules from the map that were not found (stop criterion from
    # concept/offene_punkte.md no. 6, without additional configuration)
```

`merge_samples` is the function that `detection/apriltag/job.py` actually uses to average
several job captures — not to be confused with `merge_by_module` (which averages several
tags of **one** capture into a module pose).

`localize_camera` determines `T_world_cam` from all visible world tags — **only** the role
`world` counts. Several world tags are averaged weighted by confidence, so that a distant,
obliquely seen tag does not pull as hard as one that fills the image; if none is visible →
`None`. `primary_tag_id` is the camera-nearest world tag — the one the hand camera aligns
to. `spread_m`/`spread_rad` say how far apart the individual estimates lie: if the four
world tags contradict each other, the survey is wrong, and the number says so instead of
vanishing into the mean.

`locate_modules` then computes, for each module and robot tag,

```
pose = T_world_cam · T_cam_tag · T_tag_module        # if localization is given
pose =               T_cam_tag · T_tag_module        # otherwise, in the camera frame
```

sets `frame_id` accordingly, and additionally records the nearest world tag together with
`T_worldtag_module`. **This is the only place in the entire code where this chaining
exists** — ceiling and hand camera call the same function; they differ only in whether
`localization` is set.

`merge_by_module` averages measurements of the same `(moduleId, instanceId)` — this is how
several tags on the robot become one robot pose. `merge_locations` merges ceiling and hand
results; the more precise source wins. `world_tag_for_robot` answers which world tag is
closest to the robot.

`camera_pose_from_reference_tags(tag_poses, tag_map) -> Pose | None` is retained as a slim
wrapper for the CLI tools that only need the matrix.

### Hand-eye and anchoring — `tagloc.handeye`

The camera sits rigidly on the robot, so `T_flansch_cam` is a hardware constant and is
calibrated **once**. It bridges the gap between two world-tag sightings: the hand camera
sees the world tag only when anchoring; afterwards the kinematics carry the pose.

```python
SCHEMA = "wsc.vision.handeye/1"          # data/handeye/<frame_id>.json

@dataclass(frozen=True)
class HandEye:
    pose_flange_cam: Pose
    frame_id: str = ""
    calibration_id: str = ""
    rms_position_m: float = float("nan")   # spread of the reconstructed tag location
    rms_rotation_deg: float = float("nan")
    sample_count: int = 0

@dataclass(frozen=True)
class RobotAnchor:
    pose_world_base: Pose
    world_tag_id: int = -1     # world tag at which anchoring was done
    spread_m: float = 0.0      # quality of the localization when anchoring
    spread_rad: float = 0.0

def anchor_world_base(pose_world_cam, pose_base_flange, pose_flange_cam) -> Pose
def camera_pose_from_robot(pose_world_base, pose_base_flange, pose_flange_cam) -> Pose
def target_spread(poses_base_flange, poses_cam_tag, pose_flange_cam) -> tuple[float, float]
def solve_hand_eye(poses_base_flange, poses_cam_tag) -> tuple[Pose, float, float]
def load_hand_eye(path) -> HandEye
def save_hand_eye(path, hand_eye) -> None
```

### File format `data/handeye/<frame_id>.json`

```jsonc
{
  "schema": "wsc.vision.handeye/1",
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T09:14:03Z",
  "poseFlangeCam": { "position": [0.02, -0.015, 0.045],
                     "orientation": [0, 0, 0.707, 0.707] },
  "rmsPositionM": 0.0031,
  "rmsRotationDeg": 0.42,
  "sampleCount": 14,
  "createdAt": "2026-09-22T09:14:03Z"
}
```

Like the camera calibration, this belongs to the physical hardware, not to the repo
(`data/` is gitignored). `rmsPositionM`/`rmsRotationDeg` may be `null` (NaN cannot be
written as JSON) if no reliable spread is available.

`solve_hand_eye` computes according to **Park and Martin** in numpy, not via
`cv2.calibrateHandEye`: OpenCV 5.0 no longer exports the function to Python (only the
`CALIB_HAND_EYE_*` constants remain), and the repo deliberately supports 4.x as well as 5.x.
This also keeps `tagloc.handeye` OpenCV-free — the vision server loads the anchor without
pulling OpenCV onto the import chain.

The quality measure is `target_spread`: the tag of the calibration run was fixed, so
`T_base_flansch(i) · T_flansch_cam · T_cam_tag(i)` must yield the same pose for every `i`.
If it spreads, the camera calibration, the tag size or the reported robot pose is wrong.

The following in `tagloc.localize` depend on this:

```python
def anchor_from_localization(localization, pose_base_flange, hand_eye) -> RobotAnchor
def localize_camera_from_anchor(anchor, pose_base_flange, hand_eye) -> CameraLocalization
def anchor_drift(anchor, localization, pose_base_flange, hand_eye) -> tuple[float, float]
```

`CameraLocalization.origin` says where the pose comes from: `ORIGIN_WORLD_TAGS` (optically
measured; always wins when a world tag is in the image) or `ORIGIN_ROBOT_POSE` (propagated
from the anchor). The two do not have the same accuracy, which is why it is in the payload.

`confidence` is formed from reprojection error and ambiguity, not set to a constant `1.0`.

---

## 8. `tagloc.overlay` — marking detection results in the image

```python
OVERLAY_MODES = ("off", "apriltag", "undistorted", "tagreference")     # from tagloc.modes; "calibration" removed 17.11.2026
def normalise_mode(mode) -> str

def draw_tag_outline(image, tag_pose: TagPose, color) -> None
def draw_tag_axes(image, tag_pose, calibration, axis_length_m: float) -> None
def draw_tag_boxes(image, tag_poses) -> image
    # lean, used by the live stream: green box + "#<id>", nothing else
def draw_tag_overlay(image, tag_poses, calibration, *,
                     tag_map=None, default_size_m=0.05, show_distance=True)
def draw_board_overlay(image, board_sample, *, coverage=None)
def draw_status_bar(image, lines: Sequence[str])
def summarise(tag_poses, tag_map=None) -> str

def has_display() -> bool
    # whether any display is reachable at all (Linux: $DISPLAY/$WAYLAND_DISPLAY)

class Window:
    def __init__(self, title: str, *, enabled: bool = True) -> None
    @property
    def enabled(self) -> bool
    def show(self, image, wait_ms: int = 1) -> str   # key pressed, short, "" = none
    def close(self) -> None
```

`Window` catches the fact that `cv2.imshow` on a Pi without a screen, or in an SSH session
without X forwarding, **crashes mid-run** — the first failure is logged once, after which
detection continues without a window (the goal is detection, not the picture).
`cli/detect.py` and `cli/calibrate.py` use it for their interactive windows.

The usual depiction for markers is the **axis cross in the tag** — X red, Y green, Z blue —
in addition to the outline and the ID. That is exactly what `draw_tag_overlay` draws. The
split has a reason: the outline shows **that** something was detected, the axis cross shows
**how** the pose lies. A twisted cross is noticed immediately; a wrong number in a table
is not.

> **State 21.10.2026:** This applies to `draw_tag_overlay` and thus to the CLI
> (`python -m tagloc.cli.detect`). The **live stream** (mode `apriltag`) instead uses
> `draw_tag_boxes`: per tag only a green box and `#<id>`, without axis cross, module name,
> distance, reprojection error, orange colouring and without the status bar. The modes
> `calibration`/`undistorted` are unchanged (with status bar).

Labelled (`draw_tag_overlay`) per tag are: ID, module name from the map, distance and
reprojection error. **Ambiguous poses are drawn orange instead of green** — that is the
information one searches for when debugging.

The module only draws; it computes nothing: poses come in finished. That is why the same
code can run in the live stream, in the CLI window and in a still image.

Line thickness, font size and spacing are **proportional to the image width** (`_scale_for`,
reference 640 px, never scaled down) instead of fixed pixel values. The live stream still
detects AprilTags on the full camera frame (up to 4056 px on the ceiling camera), but for
transmission it first scales the image and the corner coordinates, and then draws at this
output resolution. This avoids copying and labelling the large full frame on every tick.

`normalise_mode` lives in `tagloc.modes` and works without numpy, because the live-stream
publisher needs it even when no detection is configured at all. An unknown, empty or
mistyped mode falls back to `apriltag` instead of halting the stream.

---

## 9. The command-line tools

All of them accept `--source` either as a camera or as an image folder. An image folder
makes every call reproducible and hardware-free.

### 9.1 Calibration

```bash
# interactive at the camera: SPACE = capture, C = compute, S = save, Q = quit
PYTHONPATH=src python3 -m tagloc.cli.calibrate \
    --source camera:0 --board charuco --out data/calibration/cam_ceiling.json \
    --frame-id cam_ceiling

# headless from an image folder — same compute core, no GUI
PYTHONPATH=src python3 -m tagloc.cli.calibrate \
    --source ./aufnahmen/decke/ --board charuco --out data/calibration/cam_ceiling.json
```

Important options: `--board chessboard|charuco`, `--cols/--rows`, `--square-size-m`,
`--marker-size-m`, `--dictionary`, `--min-samples`. Printed are the RMS reprojection error
and the image coverage in x and y — a calibration that covers only the image centre has a
usable RMS and unusable distortion coefficients.

### 9.2 Detection

```bash
PYTHONPATH=src python3 -m tagloc.cli.detect \
    --source camera:0 --calibration data/calibration/cam_ceiling.json \
    --tag-map config/tagmap.json --overlay

# as JSON into a file, e.g. for further processing or a report
PYTHONPATH=src python3 -m tagloc.cli.detect \
    --source ./aufnahmen/szene/ --calibration … --tag-map … --json posen.json
```

Prints per tag: ID, pose (position + quaternion xyzw), reprojection error, ambiguity flag.
With `--tag-map`, additionally the module assignment and, once reference tags are visible,
the world pose.

### 9.3 Coordinate transformation

```bash
# transform a single pose with a known camera pose
PYTHONPATH=src python3 -m tagloc.cli.transform \
    --camera-pose roboterpose.json \
    --position 0.12 -0.04 0.85 --orientation 0 0 0 1

# whole result file from detect, camera pose from the reference tags of the input
PYTHONPATH=src python3 -m tagloc.cli.transform \
    --input posen.json --tag-map config/tagmap.json \
    --from-reference --out posen_welt.json

# reverse direction
PYTHONPATH=src python3 -m tagloc.cli.transform \
    --camera-pose roboterpose.json --invert \
    --position 1.20 0.33 0.04 --orientation 0 0 0.3827 0.9239
```

There is **no** `--from` flag. The origin of the transformation `T_ziel_quelle` is either
`--camera-pose <file>` (JSON with `position`/`orientation`) or `--from-reference` (computes
`T_world_cam` itself from the reference tags of the `--input` file; requires `--tag-map`);
with neither, the transformation is the identity. `--to` only names the target frame for
the output (default `world`), not the source. Further options: `--position`/`--orientation`
for a single pose without `--input`, `--invert` to reverse, `--out` to write. Pure
computation, no camera, no cv2. The tool is also the fastest way to settle a convention
question before it gets baked into production code.

### 9.3a Hand-eye calibration

```bash
PYTHONPATH=src python3 -m tagloc.cli.calibrate_handeye \
    --samples fahrt/aufnahmen.json \
    --calibration data/calibration/cam_flange.json \
    --out data/handeye/cam_flange.json
```

Options: `--samples` (capture list, see below, required), `--calibration` (camera
calibration, required), `--out` (target file, required), `--family` (tag family, default
`tag36h11`), `--backend` (detector backend, default `aruco`), `--frame-id` (reference frame,
empty = from the calibration), `--max-reproj-error-px` (captures above this are discarded,
default 2.0), `-v`/`--verbose`.

Capture list (`--samples`, separate from the computation, so that it can be repeated without
a new robot run):

```jsonc
{
  "schema": "wsc.vision.handeye.samples/1",
  "tagId": 2,
  "tagSizeM": 0.100,
  "samples": [
    { "image": "pose_000.png",
      "poseBaseFlange": { "position": [0.20, 0.05, 0.50],
                          "orientation": [0, 0.131, 0, 0.991] } },
    { "image": "pose_001.png", "poseBaseFlange": { "...": "..." } }
  ]
}
```

Image paths are relative to the capture list. Procedure: lay one tag down in a fixed
position (it must stay in place for the whole run), have the robot move through at least a
dozen clearly different poses (**rotate about several axes**; pure translation does not
determine the rotation), and note one image plus `T_base_flansch` per pose. The quality
measure (`target_spread`) is then in the output: the tag was fixed, so it must come out at
the same spot from every robot pose. If it spreads beyond `SPREAD_WARNING_M` (5 mm), the
file is still written, but with a conspicuous warning.

### 9.4 Building the tag map

```bash
PYTHONPATH=src python3 -m tagloc.cli.build_tagmap \
    --source ./aufnahmen/zelle/ --calibration data/calibration/cam_ceiling.json \
    --anchor 0 --sizes config/tagsizes.json --out config/tagmap.json
```

Takes an image series in which neighbouring tags each overlap pairwise, places all tags
relative to the anchor, and writes the map. Also output are the closure errors per edge and
a list of the tags that had no path to the anchor.

**Capture note:** It is not enough to photograph each tag once. Every image must show at
least two tags, and the pairs must form a connected chain to the anchor.

### 9.5 Helper tools under `tools/`

```bash
# true-to-scale print sheet as SVG: tags or ChArUco board, with a 100 mm scale bar
python tools/make_tag_sheet.py --ids 0 1 2 3 7 12 --size-mm 100 --out tags.svg
python tools/make_tag_sheet.py --charuco --cols 7 --rows 5 \
    --square-mm 30 --marker-mm 22 --out board.svg

# synthetic scene with known ground truth — the whole chain without a camera
python tools/make_synthetic_scene.py --mode tags --count 12 \
    --out /tmp/bilder --calibration /tmp/calib.json
```

`make_tag_sheet.py` deliberately outputs SVG, not PNG or PDF: an SVG carries real
millimetre dimensions and stays true to scale when printed at "100 %". Every sheet carries a
100 mm scale bar for measuring afterwards — a printer that quietly scales to 96 % would
otherwise produce a 4 % distance error, and everything else would look plausible.

---

## 10. In the vision server

### 10.1 Profile and recipe

| | Value |
|---|---|
| Profile | `apriltag` |
| Recipe | `RecipeId = "apriltag"` |
| Class | `AprilTagDetectionSource` in `src/vision_server/detection/apriltag/` (package; `__init__.py` composes the class from mixins) |
| Configuration | `AprilTagProfileConfig` in `src/vision_server/profiles.py` |
| `IsSimulated` | `False` |

Layer 1 and Layer 2 use the **same profile**; each Pi runs its own server with its own
`AprilTagProfileConfig`, set in `src/vision_server/server.py`.

| Field | Layer 1 (`pi-decke`) | Layer 2 (`pi-hand`) |
|---|---|---|
| `frame_id` | `world` | `cam_flange` |
| `frame_convention` | `z_forward_x_right_y_down` | `z_forward_x_right_y_down` |
| `calibration_path` | `data/calibration/cam_ceiling.json` | `data/calibration/cam_flange.json` |
| `tag_map_path` | `config/tagmap.json` | `config/tagmap.json` |
| `tag_size_m` | 0.080 (until 13.10.2026: 0.100) | 0.080 (until 13.10.2026: 0.050) |
| `resolution` | 2028×1520 | 640×480 |
| `samples_per_job` | 3 | 5 |
| `max_reproj_error_px` | 3.0 | 1.5 |
| `hand_eye_path` | `None` (no camera on the flange) | `data/handeye/cam_flange.json` |

`resolution` on Layer 2 must match `CameraStreamConfig.realsense_resolution` (default
640×480) — the actual frames come via the shared `SharedCamera`, not directly from
`AprilTagProfileConfig`. A mismatch only shows up at the first job as a `ValueError`
("Kalibrierung gilt für …" — calibration applies to …).

`tag_size_m` remains the fallback for tags that are not in the map; if a tag is in the map,
its `sizeM` wins.

**Without a calibration file**, `open()` normally fails and the server stays in
`Preoperational` (intended: meaningless numbers should not leave unnoticed). For tests
**before** the real calibration run (section 3.3 in the test plan) there is an explicit,
temporary emergency exit: `AprilTagProfileConfig.allow_placeholder_calibration = True` — set
via the environment variable `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` in
`src/vision_server/server.py`, default off. The source then starts with a roughly estimated
intrinsic (`tagloc.calibration.default_calibration`, computed from an assumed field-of-view
width of 70°). Detector, overlay and job path can thus be checked; the poses are, however,
**not dimensionally accurate**. `calibration_id` in the result is then
`"placeholder-unkalibriert"` (placeholder, uncalibrated) — remove the environment variable
again before rollout.

### 10.2 What arrives in the result

The payload schema remains `wsc.vision.detections/1` — **unchanged**; new keys are allowed
additively, a version jump would break the backend.

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-ceiling-01",
  "jobId": "job-42",
  "resultState": 0,
  "frameId": "world",
  "frameConvention": "z_forward_x_right_y_down",
  "configurationId": "tag36h11@cam_ceiling#1757500000+tagmap#1757499100",
  "lengthUnit": "m", "angleUnit": "rad", "rotation": "quaternion_xyzw",
  "detections": [
    {
      "moduleId": "MOD-A",
      "instanceId": "mod-a-1",
      "confidence": 0.94,
      "position": [1.204, 0.336, 0.041],
      "orientation": [0.0, 0.0, 0.3827, 0.9239],
      "boundingBox": null,
      "attributes": {
        "tagId": "7",
        "reprojErrorPx": "0.42",
        "ambiguous": "false",
        "sampleCount": "3",
        "frameTimestamp": "1757500123.417",
        "recipeId": "apriltag",
        "source": "ceiling",
        "role": "module",
        "referenceTagId": "0",
        "referencePosition": "[1.204, 0.336, 0.041]",
        "referenceOrientation": "[0.0, 0.0, 0.3827, 0.9239]",
        "referenceDistanceM": "1.258",
        "worldTagIds": "[0, 1]",
        "cameraSpreadM": "0.004",
        "cameraSpreadDeg": "0.31",
        "cameraPoseOrigin": "world_tags"
      }
    }
  ]
}
```

Complete list of attributes that can actually occur (all `attributes` values are strings;
OPC UA has no mixed types here):

| Field | When set | Meaning |
| --- | --- | --- |
| `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`, `frameTimestamp`, `recipeId` | always | as above |
| `source` | always | `"ceiling"` \| `"flange"` |
| `role` | always | `"module"` \| `"robot"`, from the tag map |
| `tagIds`, `tagCount` | several tags of the same module instance measured | which tags contributed |
| `referenceTagId` | a world tag known as reference | nearest world tag |
| `referencePosition`, `referenceOrientation`, `referenceDistanceM` | see above | pose relative to this world tag |
| `worldTagIds`, `cameraSpreadM`, `cameraSpreadDeg`, `cameraPoseOrigin` | camera localized (`localize_camera`/anchor) | contributing world tags, spread, `"world_tags"` (optically measured) or `"robot_pose"` (propagated from the anchor) |
| `cameraPoseSolver`, `cameraReprojectionErrorPx` | camera localized optically | `"joint"` (joint PnP over all world-tag corners, from two world tags on), `"single"` (one world tag) or `"average"` (fallback: mean of the individual tags); RMS of the solution in px |
| `anchorWorldTagId` | hand-eye anchor set | world tag at which anchoring was done |
| `anchorDriftM`, `anchorDriftDeg` | see above, comparable with the current sighting | deviation of anchor vs. current measurement |
| `missingModules` | modules from the tag map not found | comma-separated list |

- `configurationId` carries tag family, calibration identity and tag-map identity. So if a
  pose is wrong, it becomes clear afterwards which calibration and which map were in effect.
- No tag found → no empty result, but `VisionJobError` with `DETECTION_FAILED`
  (`resultState = 5`).
- `Stop` during a job → `resultState = 7` (`CANCELLED`), as handled by `JobRunner.stop()`.

### 10.3 Operating rules of the source

- **Every** cv2 operation runs via `run_blocking`. Run directly on the event loop, it would
  freeze the state machine, the events and the cell server's 1 Hz loop.
- The source has its **own** capture timeout (`capture_timeout_s`). The runner's job
  timeout cannot kill a hanging worker thread.
- The camera is shared via `SharedCamera`, not opened on its own — Picamera2 allows only one
  open access per camera, and the live stream holds it.
- `open()` loads calibration and tag map. If they are missing, the source does not open, and
  the server stays in Preoperational. This is intended: a vision server without calibration
  delivers numbers without meaning.

---

## 11. Testing

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

| Test file | Checks | Requires |
|---|---|---|
| `tests/test_tag_geometry.py` | chaining, inversion, quaternion convention, averaging — with golden values | numpy |
| `tests/test_tag_map.py` | `place_tags` on constructed co-observations, closure errors, unreachable tags, file round trip | numpy |
| `tests/test_calibration_io.py` | write/read, identity, resolution check, scaling | numpy |
| `tests/test_tag_pipeline.py` | **end to end on a synthetically rendered image**: detector → pose → placement → module pose against the known ground truth; tolerance 2 mm / 1° / 1 px, measured 0.29 mm / 0.50° / 0.063 px | cv2 (otherwise `skipUnless`) |
| `tests/test_apriltag_source.py` | `run_blocking`, capture timeout, `configurationId`, `frame_id`/`frame_convention`, payload attributes, `DETECTION_FAILED` | — |

Test images are **generated inside the test** (`tools/make_synthetic_scene.py`), not
committed: `.gitignore` excludes `*.jpg`, and the repo deliberately works without fixture
files. Mocking uses hand-written fakes (`FakeCamera`, `FakeDetector`), not `unittest.mock` —
that is the idiom of this repo.

---

## 12. When something is wrong

| Symptom | Probable cause |
|---|---|
| Distance systematically off by a fixed factor | wrong `sizeM` in the tag map, or the calibration belongs to a different resolution → `check_resolution` |
| Pose flips back and forth between two states | ambiguous tag, `ambiguous: true`. Look at the tag more steeply, or add a second tag |
| Poses at the image edge clearly worse than in the centre | calibration does not cover the edge — check the coverage values from `calibrate`, not only the RMS |
| `place_tags` returns fewer tags than were captured | no path to the anchor; an image showing two already-connected tags together is missing |
| Closure errors in `residuals` large | calibration or a recorded tag size is wrong; the map is only usable once it closes |
| Server stays in Preoperational | the source's `open()` failed — usually a missing calibration or tag-map file; the log message names the path |
| Live stream gone although configured | no open source holds a `SharedCamera` |
| Layer 2 returns poses in the camera frame instead of `world`, although a world tag should be visible | `AprilTagProfileConfig.hand_eye_path` is missing or points to no file — without a hand-eye calibration, nothing anchors |
| `anchorDriftM`/`anchorDriftDeg` in the payload grow over time | anchor is stale, or the robot has moved a lot since the last anchoring — re-anchor (bring the world tag back into the image) |
| `calibrate_handeye` aborts with „Keine Aufnahmen" (no captures) or similar | capture list empty, wrong schema, or all captures discarded by `--max-reproj-error-px` |
| `solve_hand_eye` result with large spread (`target_spread` > `SPREAD_WARNING_M`) | too few or too similar robot poses (rotation not varied enough), or camera calibration/tag size wrong |
| Job reports `DETECTION_FAILED` although a tag was visible | `TagPose.is_usable()` discarded the pose — NaN/Inf reprojection error, or above `max_reprojection_error_px` |

---

## 13. Detector parameters for blurred tags (ceiling camera)

The 12 MP image of the ceiling camera is optically soft: edges 4–8 px wide with cells of
6–13 px. With the OpenCV defaults, only the tags [1, 2, 583] were found in
`data/rohbilder/cam_ceiling_20261019-114704.png`; tag 0 had 4 bit errors with 3 correctable,
and tag 3 (contrast only 60 grey levels) was decoded incorrectly. Three `DetectorParameters`
fix this:

| `AprilTagProfileConfig` / `build_detector` | OpenCV | Default | `cam_ceiling` |
|---|---|---|---|
| `error_correction_rate` | `errorCorrectionRate` | 0.6 | 1.0 |
| `perspective_remove_margin_per_cell` | `perspectiveRemoveIgnoredMarginPerCell` | 0.13 | 0.3 |
| `perspective_remove_pixel_per_cell` | `perspectiveRemovePixelPerCell` | 4 | 8 |

Result: [0, 1, 2, 3, 583] in 0.1 s, no extra cost. A margin of 0.2–0.4 and ppc 8–10 give
the same. `None` keeps the OpenCV default (all other profiles, `cam_flange` included). Job,
stream overlay and cell monitoring receive the same values; the CLI tools have
`--error-correction-rate`, `--perspective-remove-margin`, `--perspective-remove-ppc`. The
`pupil` backend ignores them.

**Risk:** at 1.0, up to 5 instead of 3 bit errors are corrected; the theoretical false-alarm
risk per quadrilateral candidate rises from about 0.03 % to about 1.5 % (the border check
before it filters most of it; on the 40 tag-free chessboard images in `data/LV1`, zero false
alarms). If ghost IDs appear in operation, lower `error_correction_rate` toward 0.8.

`subpixel_corner_refinement` stays off: on this image, with default parameters, it loses
tags (only [583]) and takes 2.7 s.

Regression test: `tests/test_detector.py` (`CeilingRawImageTest`).

### Addendum 19.10.2026: values discarded, parameters freely configurable

The camera has been refocused. The values 1.0 / 0.3 / 8 belonged to the blurred image and
are **no longer set**: `cam_ceiling` runs with the OpenCV default. The three individual
keywords (`error_correction_rate`, `perspective_remove_margin_per_cell`,
`perspective_remove_pixel_per_cell`) and the CLI options for them have been removed. In
their place comes a generic mapping `detector_parameters` (attribute name of
`cv2.aruco.DetectorParameters` → value):

- Preset: `PI_APRILTAG_PRESETS[...]["detector_parameters"]` in `server.py`.
- Environment variable `VISION_APRILTAG_DETECTOR_PARAMS` (JSON object), e.g.
  `'{"errorCorrectionRate": 0.8}'`; per key the variable wins, `null` removes a preset entry.
- CLI: `--detector-param NAME=VALUE`, repeatable.
- Unknown names and values of the wrong type raise `ValueError` at startup.

Details and interfaces:
[`arbeitsplaene/kamera-controls-und-detektor-parameter.md`](arbeitsplaene/kamera-controls-und-detektor-parameter.md).
The regression test on the old raw image now passes the values explicitly, thereby
documenting the edge case.

## 14. Surveying tag position with the camera (hand Pi only)

Work plan with rationale and interfaces: [`arbeitsplaene/tag-einmessen-bezugskanten.md`](arbeitsplaene/tag-einmessen-bezugskanten.md). OPC UA methods: [`vision-server-interface.md`](vision-server-interface.md), section 19.

### Principle

- **The camera replaces the tape measure.** The module catalog provides the starting value
  `T_module_tag` (position, rotation). The two reference edges (`placement.lines`) are real
  3D segments in the module frame. The hand camera sees the tag and both edges in the
  **same** image. Measurement therefore takes place in the tag frame, without robot
  kinematics and without hand-eye error.
- **Correction only in the tag plane**, three degrees of freedom: x, y and θ (rotation about
  tag z). Tilt and distance to the surface stay as in the starting value (the tag lies flush).
- **`dofs`:** `full` = two non-parallel edges (≥ 20°), all three quantities. `partial` = one
  edge: perpendicular to it and the rotation; along the edge the starting value stays.
  `none` = no usable edge, the starting value stays.
- **Edge height `h`** above the tag plane comes from the starting value. An error in `h`
  shifts the measurement laterally by `Δh · tan(viewing angle)`. Therefore measure head-on
  and keep the edge near the image centre. The RealSense depth can optionally be used later.
- **Default tilt 10°:** head-on to a raised tag, the tag pose yields the mirror solution
  (about 13°). The request default is `tiltDeg` = 10; the Python default
  `plan_reference_view(tilt_deg=0.0)` stays at 0.
- **Measured values:** synthetic only (`tools/make_synthetic_scene.py`, measurement via
  `measure_tag_reference`): 0.23 to 0.63 mm and 0.07 to 0.11° with 3 images. Not yet measured
  on the Pi.

### Python API `src/tagloc/reference_edges.py`

Data classes: `ReferenceLine` (id, start, end; module frame, m), `LineMeasurement`,
`InPlaneCorrection` (dofs, delta_xy_m, delta_rot_deg, pose_module_tag, residual_m),
`ViewPlan` (pose_tag_cam, standoff_m, fits, uncovered).

- `measure_reference_lines(gray, calib, pose_cam_tag, pose_module_tag_guess, lines, *, tag_size_m, search_band_m=0.015, tag_margin_m=0.01) -> list[LineMeasurement]`: one
  measurement per image.
- `fit_in_plane_correction(measurements, pose_module_tag_guess) -> InPlaneCorrection`
- `average_corrections(corrections) -> (InPlaneCorrection, dict)`: averages several images;
  the dict reports the spread in mm and degrees.
- `plan_reference_view(pose_module_tag_guess, lines, calib, *, tag_size_m, line_extent_m=0.10, margin=0.10, min_standoff_m=0.25, max_standoff_m=0.60, tilt_deg=0.0) -> ViewPlan`
- `catalog_rpy_deg(rotation) -> ndarray`: rotation in catalog format (RPY in degrees).
- `ValueError` for invalid inputs: edge shorter than 2 cm, size ≤ 0, non-finite values,
  resolution not matching the calibration.
- `cv2` is only imported inside the functions.

### Data format in the module catalog

The fields of `ModuleTag.placement` are described in
[`arbeitsplaene/modell-flaechen-tag-platzierung.md`](arbeitsplaene/modell-flaechen-tag-platzierung.md)
under "Data: `ModuleTag.placement`" (`mode`, `normal`, `edgeA`, `edgeB`, `distancesM`,
`inPlaneRotationDeg`, `flipped`, `offsetM`). New here:

- **`placement.lines`** (optional): `TagReferenceLine[]` with `id` (`"A"`, `"B"`), `start`,
  `end`. The edges **unprojected**, module frame, meters. The camera searches for exactly
  these lines. If the field is missing, the frontend derives the edges from `edgeA`/`edgeB`
  (length 0.15 m; for `corner` the second edge lies along `inward`), and the surface marks
  them as „Kanten geschätzt“ (edges estimated).
- **`measurement`** (`TagCameraMeasurement`, missing = not yet surveyed). The example shows
  only the format; the numbers are invented:

```json
{ "tagId": 4, "sizeM": 0.1,
  "position": [-0.2, -0.15, 0.8], "rotationDeg": [0, 0, 0],
  "placement": { "mode": "corner", "normal": [0, 0, 1],
                 "edgeA": { "point": [-0.3, -0.2, 0.8], "direction": [1, 0, 0], "inward": [0, 1, 0] },
                 "distancesM": [0.1, 0.05], "inPlaneRotationDeg": null, "offsetM": 0.005,
                 "lines": [ { "id": "A", "start": [-0.3, -0.2, 0.8], "end": [0.0, -0.2, 0.8] },
                            { "id": "B", "start": [-0.3, -0.2, 0.8], "end": [-0.3, 0.0, 0.8] } ] },
  "measurement": { "method": "camera-edges", "measuredAt": "2026-10-20T10:31:00Z", "dofs": "full",
                   "guess": { "position": [-0.2, -0.15, 0.8], "rotationDeg": [0, 0, 0] },
                   "deltaMm": [1.8, -0.6], "deltaRotDeg": 0.4, "spreadMm": 0.2, "lines": ["A", "B"] } }
```

Measured values overwrite `position`/`rotationDeg`. `placement` remains as a record of the
starting value, and `measurement.guess` holds the starting value. The Pi still receives only
world tags; module tags are not in its tag map.

This closes the open item "Adopt `placement` format into `apriltag-referenz.md`" from
[`arbeitsplaene/modell-flaechen-tag-platzierung.md`](arbeitsplaene/modell-flaechen-tag-platzierung.md).

### Once-only rule

Every tag is surveyed **once**, when its module is newly created or edited (requirement of
20.10.2026).

- Saving from the editor discards `measurement` for new tags and for tags whose `tagId`,
  `sizeM`, `position`, `rotationDeg` or `placement` have changed. Saving unchanged keeps it.
- Import and load leave `measurement` unchanged.
- Only pending tags of modules placed in the cell are surveyed. Start happens only by click,
  because the robot moves.

### Tags on the robot itself (via joint angles)

The robot's tags (catalog entry `kind: "robot"`) are measured in the same window „Tags einmessen“
(survey tags) (Settings → Zellüberwachung (cell monitoring), next to „Modulkatalog“ (module
catalog)), without reference edges and without layer 1. The Pi remains unchanged: only
`MeasureTags` is used (`frames` 3). Implementation in the frontend (`webskillcomposition`,
`model/robotTagRun.ts`), work plan
[`arbeitsplaene/tag-einmessen-bezugskanten.md`](arbeitsplaene/tag-einmessen-bezugskanten.md),
addendum „Roboter-Variante“ (robot variant).

**Principle**

- The robot base is the robot's module frame: `ROBOT_MODULE_TO_BASE` = identity (URDF root;
  the forward kinematics computes in the same frame, `robotBaseFrom` takes over the module
  pose without offset). Thus `T_module_tag = T_module_base · T_base_tag`, and the catalog
  value is the starting value `T_base_tag`.
- For each view, `T_base_tag = FK(Gelenke) · T_flansch_cam · T_cam_tag` applies
  (`tagInBaseFrom`, `model/tagServo.ts`). The **actual** joints after the move are read, not
  the setpoints.
- Full 6 DoF. The error feeds directly into the result: hand-eye calibration and joint
  angles. Several views from different joint configurations average out part of it.

**Views (3)**

- First, as in the module run, centring takes place (`centreOnTag`, distance 0.35 m). Then
  three camera positions lie on a sphere around the tag centre, each 0.35 m away and aimed at
  the tag: tilted by +12° and −12° about tag x, and by +12° about tag y (`ROBOT_TAG_TILTS`,
  `robotTagViews`). Convention as in `PlanTagReferenceView`: the camera looks along −tag z.
- For each view: `flangeForView` → `planMove` → move with clearance → read actual joints →
  FK → `MeasureTags`.
- A view is valid only with `frameCount` ≥ 2, not `placeholder` intrinsics, without a Pi
  error, and with the tag in the image. If it is unreachable or violates the clearance, it is
  skipped with a warning. A motion error without a blockage aborts the tag.

**Averaging**

- Position: average over all valid views.
- Orientation: average over the non-ambiguous views (`ambiguous: false`). If all are
  ambiguous, the orientation stays from the catalog, with a warning.
- At least **2** valid views (`ROBOT_TAG_MIN_VIEWS`), otherwise the tag remains pending.
- Spread: `spreadMm` (RMS of the positions about the mean), `spreadDeg` (RMS of the angles to
  the mean). Above 3 mm or 1° there is a warning („Hand-Auge-Kalibrierung prüfen“ (check
  hand-eye calibration)). The thresholds are assumptions and not measured on the hardware.

**Data format**

`ModuleTag.measurement` (`TagCameraMeasurement`) with `method: "robot-fk"`. The value
`"camera-edges"` applies to the module tags. Fields:

- `dofs` is always `"full"` for `robot-fk`; `lines` is `[]`.
- `deltaMm`: new minus starting value, in tag x and tag y of the starting value, mm.
- `deltaNormalMm` (only `robot-fk`, optional): the same shift along tag z of the starting
  value, mm.
- `deltaRotDeg`: the total angle of rotation between starting value and result.
- `spreadMm`, `spreadDeg` (only `robot-fk`, optional), `views` (only `robot-fk`, optional):
  number of valid views.

`parseMeasurement` in `model/catalogStore.ts` discards a missing or unknown `method` (then
the tag is measured again). It discards invalid optional fields individually.
`describeTagMeasurement(tag, { robot: true })` shows "measured … via robot joints · Δ … mm /
…° · n views", and for unmeasured tags "not measured yet · measured with the robot's hand
camera".

**Limits**

- Hand-eye calibration and joint angles feed in directly. ~1–2 mm are expected, not measured.
- Only tags that the own hand camera sees and can reach. Tags lying sideways at the base may
  be unreachable and remain pending.
- The clearance (`keepOutFromRobotBounds`) does not generally block views above the robot's
  own cart: flange, camera and joint points are checked only below table top + 5 cm within
  the footprint, or below floor + 10 cm. The cart bounds in the catalog must describe the
  footprint.
- If `frameCount` is missing in the result (`null`), the view counts as valid. The Pi always
  supplies the field.
- ±12° at 0.35 m, and whether the oblique view reaches `frameCount` ≥ 2, are untested.

### Live view of the edge search

Since 21.10.2026 the stream shows during surveying what the edge search is doing (plan
[`arbeitsplaene/tag-einmessen-liveview.md`](arbeitsplaene/tag-einmessen-liveview.md)). The
computation remains the same: `measure_reference_lines(..., debug=...)` only fills a list;
the result and `LineMeasurement` are bit-identical with and without `debug`.

**What you see**, in the order of the search:

1. **Starting-value edge** (yellow dashed): the edge from the module catalog, projected into
   the image, visible part only.
2. **Search band** (thin yellow borders): area in which edges are searched for (`bandPx`).
3. **Candidate points** (cyan): all gradient maxima in the band.
4. **Rejected lines** (orange): RANSAC lines from the candidates that were not chosen
   (inliers per line).
5. **Chosen line** (green, red when `ambiguous`), with a halo; label per edge with Δ mm and
   degrees, or `NO_EDGE` or „mehrdeutig“ (ambiguous).
6. **Tag mask** (grey) and tag frame, as otherwise in the stream.

The layers can be switched off individually (`overlay` in the request, eight switches, see
[`vision-server-interface.md`](vision-server-interface.md) §19.1). Without an active preview,
the stream runs in mode `tagreference` with the title „keine Einmess-Vorschau“ (no survey
preview) and normal tag frames.

**Debug data per edge** (raw-image pixels, distorted, full resolution): `guessPx`,
`profilesPx`, `normalsPx`, `bandPx`, `edgePointsPx`, `candidates` (with `inliersPx`, `score`,
`chosen`), `measuredPx`, `measuredPolyPx`, plus `found`, `supportPx`, `contrast`, `ambiguous`,
`offsetMm`, `angleDeg`. Once per call `_tag` with `maskOutlinePx`. The drawing
(`tagloc/reference_overlay.py`) computes nothing; it only draws this data.

**Computational load:** the preview evaluates at most 3 Hz (`PREVIEW_INTERVAL_S`), on the same
single-thread pool as the stream's own detection (the detector is not built for two threads).
On the test machine 52–62 ms per evaluation, not measured on the hand Pi.

**Measurement log:** each measurement writes a folder under `data/tagreference/` containing
the request, the result, the raw images (PNG) and the debug image. The last measurement is
retrievable at `GET /tagreference.jpg`. Details and cleanup rule:
`vision-server-interface.md` §19.1.

**Test phase "all tags" (frontend, state 21.10.2026):** the switch „Alle Tags neu einmessen (Testphase)“
(re-survey all tags (test phase)) is on by default. Each run then re-measures all measurable
tags, so that the trial can be repeated often. This is an **exception to the once-only rule**
above. Target state (user's requirement): switch off, or `TAG_MEASURE_ALL_TESTPHASE = false`
in `tagMeasurementState.ts`; then only the once-only rule applies again.
