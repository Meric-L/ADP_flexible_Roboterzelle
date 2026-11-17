# AprilTag localisation — concept and implementation plan

How modules fitted with AprilTags are localised in the cell, and how the functionality needed for this is cut so that layer 1 and layer 2
share it and so that it stays testable end-to-end without hardware.

Neighbouring documents: [`apriltag-referenz.md`](apriltag-referenz.md) (function reference),
[`apriltag-e2e-test.md`](apriltag-e2e-test.md) (test guide incl. Pi camera),
[`vision-server-interface.md`](vision-server-interface.md) (OPC UA interface),
[`altlasten.md`](arbeitsplaene/altlasten.md), [`concept/offene_punkte.md`](../../concept/offene_punkte.md).

> **State:** implemented. What is written here describes the built state; sections 6 and 9 list what of it is still open.

---

## 1. Introduction: what AprilTags are in this project

An AprilTag in this project is **not a recognition feature, but a measuring point with an identity**. The camera delivers exactly one
thing from it: the relation `T_cam_tag` — where the tag is relative to the camera, in metres and with full orientation. Everything else is
chaining of such relations.

From this follows the guiding idea of the whole implementation:

> **A tag carries a number, not a meaning.** That tag 7 is the module `MOD-A` and that its origin lies 40 mm below the tag centre is stated
> in a file (the tag map), not in the code. The code knows only tags, poses and chaining.

### 1.1 Three roles of tags

**Only the world tag is fixed.** Everything else in the cell is movable — the modules and the robot alike. The robot is a module like any
other; it is merely the one that is always used.

| Role | Hardware | Purpose | Pose |
|---|---|---|---|
| **World tag** (`world`) | four AprilTags in the border area of the cell | span the world frame; every camera that sees one of them knows its own pose from it | **fixed**, stored in the tag map |
| **Robot tag** (`robot`) | AprilTags on the robot, several allowed | *is* the robot pose, offset by the CAD offset tag→robot base | **movable — measured, not read** |
| **Module tag** (`module`) | AprilTag on the module | *is* the module pose, offset by the constant CAD offset tag→module origin | **movable — measured, not read** |

World tags (reference) and measurement tags (modules, robot) run through exactly the same code. They differ only in whether their world
pose is stored in the tag map or comes from there as `null`.

The four world tags are an expectation, not a limit of the code: `localize_camera` also works with a single one. `validate_tag_map`
flags a deviating count, so that a missing tag is noticed instead of silently costing accuracy.

Several tags on the robot are intentional: from the ceiling the arm easily hides a single tag. Each carries its own CAD offset to the same
robot base; `merge_by_module` averages them into one pose.

The former roles `robot_table` and `reference` are dropped without replacement. They declared the robot table a **fixed** anchor — exactly
the reasoning error that this concept corrects.

### 1.2 The chain

```
                       T_world_cam                T_cam_tag          T_tag_module
   world frame  <───────────────────────  camera frame ───────────>  tag frame ──────────>  module frame
               from the world tags in the          measured              constant,
               same image — the same for                                 from the tag map
               both cameras
```

`T_world_cam` can come from **two** sources, and the result says in `cameraPoseOrigin` which one:

| Origin | How | When |
|---|---|---|
| `world_tags` | optically: `T_world_cam = T_world_tag · inv(T_cam_tag)` | as soon as a world tag lies in the image — the measurement, always wins |
| `robot_pose` | `T_world_cam = T_world_base · T_base_flange · T_flange_cam` | in between, when no world tag is visible |

The second route is the reason why **hand-eye calibration is needed**. The hand camera does not see the world tag permanently: if it
stands 0.3 m in front of a module, the nearest world tag lies one to two metres away, outside the field of view. The world tag is
therefore needed **once at the start of a localisation run** — for anchoring — after which the kinematics carries the pose. See section 1.5.

In addition, each module gets the world tag nearest to it and its pose relative to that tag (`T_worldtag_module`). The cell is built around
the world tags, so this is the number against which a module is set up and checked. The shared world frame is kept alongside it — the four
world tags span **one** frame, surveyed once via `build_tagmap`.

The module pose sought is simply

```
T_world_module = T_world_cam · T_cam_tag · T_tag_module
```

Exactly **one** function in the code may perform this chaining. It is pure mathematics, knows neither camera nor OPC UA, and is therefore
checkable with golden values. This is the answer to risk R11 from [`vision-system-integration.md`](vision-system-integration.md)
(mm↔m, xyzw↔wxyz, `T_base_cam`↔`T_cam_base` — the classic silent error).

### 1.3 Why both cameras are the same functionality

| | Ceiling camera (`ADP-Roboter-Lokalisierung`) | Hand camera (`ADP-HandInEye-Kamera-Pi`) |
|---|---|---|
| Task | overview: where is the robot, where are the modules, which world tag is nearest to the robot | exact 6-DOF pose of a module |
| Camera | fixed mount | moves with the robot |
| View | world tags, robot and modules **at the same time** | one world tag and the module from close up |
| Origin of `T_world_cam` | from the world tags in the same image | from the world tag in the same image |
| Tag size | large (module tags seen from a distance) | small (close-up) |
| `source` in the payload | `ceiling` | `flange` |
| Result `frameId` | `world`, as soon as a world tag is in the image | `world`, as soon as a world tag is in the image |

The differences are **solely configuration and what the camera gets to see**. Detection, pose estimation, outlier filter, averaging, tag map
evaluation and payload construction are identical. Hence: one library, one `DetectionSource`, two configurations — no camera-specific code.

If the hand camera currently sees no world tag — between two world tags —, its poses remain in the camera frame with `frameConvention` set.
This is not a makeshift but the honest case: without an anchor in the image, a world pose would be guessed.

### 1.4 Anchoring: once at the world tag, then all modules within reach

The camera sits **rigidly** on the robot; only the robot as a whole moves. `T_flange_cam` is thus a constant of the hardware and is calibrated
**once** (`python -m tagloc.cli.calibrate_handeye`), not per run.

```
Once:                T_flange_cam                      hand-eye, constant

Once per run:        T_world_base = T_world_cam · inv(T_flange_cam) · inv(T_base_flange)
                     with T_world_cam optically from the world tag

Per module after:    T_world_cam  = T_world_base · T_base_flange(now) · T_flange_cam
                     no world tag in the image needed
```

If a world tag comes back into the image later, re-anchoring takes place — and the deviation between the optical value and the value
calculated forward is the accumulated **drift** (`anchorDriftM`/`anchorDriftDeg` in the payload). An anchor that quietly drifts away is
exactly the kind of error that otherwise only shows up when the gripper misses its target.

The robot pose `T_base_flange` arrives as a job parameter at the hand Pi (seven floats, metres and quaternion xyzw). If it is missing, or the
hand-eye file is missing, everything stays on the optical route: without a world tag in the image, then simply in the camera frame.
Nothing is guessed — a wrong robot pose shifts every module pose without the result showing it.

### 1.5 The sequence in the cell

1. **Ceiling camera**: sees the world tags, the robot and the modules in one image. It supplies the rough overview and is the only one that
   can answer which world tag is nearest to the robot (`world_tag_for_robot`) — because only it sees both at the same time.
2. **Robot**: points its hand-in-eye camera at exactly this world tag and **anchors** itself to it (`anchor_from_localization`) — once per run.
3. **Hand camera**: then drives to each module within reach and measures it precisely. The world tag no longer needs to be in the image; the
   camera pose comes from the anchor and the kinematics (`localize_camera_from_anchor`). The results nevertheless remain in the world frame
   and referenced to the world tags.
4. **Merging**: `merge_locations(decke, hand)` merges the two images of the cell. Where both measured the same module, the hand measurement
   wins; what only the ceiling saw is kept. Deliberately a pure function and not a second network connection between the Pis: both servers
   continue to publish independently, and the consumer calls them.

---

## 2. Module scope

New package `src/tagloc/` (tag localisation). Not `src/apriltag/`, because this folder name collides with the PyPI package `apriltag`, and
the package contains more than tag detection (calibration, transformations, tag map).

```
src/tagloc/
  identity.py      Stdlib          "<name>#<mtime>" for configurationId
  modes.py         Stdlib          the three overlay modes of the live stream
  observations.py  numpy types     TagObservation, TagPose
  geometry.py      numpy           poses, chaining, quaternions, averaging
  calibration.py   numpy           read/write calibration file, resolution check
  tagmap.py        numpy           read/write tag map, place tags in a frame
  localize.py      numpy           observations + tag map -> module poses
  handeye.py       numpy           hand-eye calibration (Park/Martin) and anchor data type
  boards.py        cv2             chessboard/ChArUco on a frame, calibration calculation
  detector.py      cv2 | pupil     detector adapter -> TagObservation
  pose.py          cv2             TagObservation -> T_cam_tag incl. reprojection error
  overlay.py       cv2             draw detection results into the image
  frames.py        cv2/picamera2   frame sources: Pi camera, webcam, image folder, single image
  cli/
    calibrate.py           calibration script
    calibrate_handeye.py   hand-eye calibration script
    detect.py              AprilTag detection script
    transform.py           coordinate transformation script
    build_tagmap.py        place tags in a common frame
```

In addition, there is `src/vision_server/stream_overlay.py` as the bridge between the library and the live stream, as well as
`tools/make_tag_sheet.py` (true-to-size print sheet) and `tools/make_synthetic_scene.py` (scene with known ground truth).

Two modules were added later, because the rule "the server must not have cv2 on its import chain" proved too weak: `identity` and `modes`
do **not even need numpy**. The server builds the `configurationId` when it constructs the source and normalises the stream mode — both
before anything is loaded.

### 2.1 Layering by testability

The order is deliberate: **the higher up, the fewer dependencies, the more strictly testable.**

| Layer | Modules | Needs | Testable with |
|---|---|---|---|
| Mathematics | `geometry` | numpy | golden values, no IO |
| Data | `calibration`, `tagmap` | numpy, stdlib `json` | tempdir, round trip |
| Image | `boards`, `detector`, `pose` | cv2 | synthetically rendered image |
| Assembly | `localize` | numpy | fake poses |
| Capture | `frames` | cv2 / picamera2 | image folder instead of camera |
| Adapter | `cli/*`, `detection/apriltag.py` | everything | fake camera, fake detector |

**Hard rule:** `geometry`, `calibration`, `tagmap` and `localize` import **no cv2**. This is not a matter of style — it means that the
vision-server core can form the calibration identity for `configurationId` without loading OpenCV, and that these tests run on every
machine, including with the OpenCV version conflict from [`altlasten.md`](arbeitsplaene/altlasten.md) D2 (locally 5.0.0.93, Pi 4.x, deviating `aruco`
API). `from_rvec_tvec` therefore implements Rodrigues in five lines of numpy instead of calling `cv2.Rodrigues`.

### 2.2 Detector adapter

The prototype uses `pupil_apriltags`; the planned architecture per [`profiles.py`](../../src/vision_server/profiles.py) and
[`altlasten.md`](arbeitsplaene/altlasten.md) D3 is `cv2.aruco`. Instead of deciding now and rebuilding later, both go behind one protocol:

```python
@dataclass(frozen=True)
class TagObservation:
    tag_id: int
    corners: tuple[tuple[float, float], ...]   # 4 image corners, fixed order
    decision_margin: float | None = None

class TagDetector(Protocol):
    family: str
    def detect(self, gray: np.ndarray) -> list[TagObservation]: ...
```

`ArucoTagDetector` (standard path) and `PupilAprilTagDetector` (transition, comparison measurement) implement it; `build_detector(family, backend)`
selects one. Pose estimation lies **deliberately not in the detector** but in `pose.py` — otherwise pose quality would depend on the backend
and would not be comparable. Tests inject a fake that returns a list of `TagObservation`; this makes the whole chain runnable without cv2.

### 2.3 Placing tags in a common coordinate system

This is the function that turns individual measurements into a map:

```python
def place_tags(
    observations: Sequence[Mapping[int, Pose]],   # per capture: tag_id -> T_cam_tag
    *, anchor_tag_id: int,
) -> dict[int, Pose]:
```

Method: every capture that shows two tags **simultaneously** yields the camera-free relation `T_a_b = T_cam_a⁻¹ · T_cam_b`. These relations span
a graph (nodes = tag IDs, edges = co-observations). A breadth-first search from the anchor tag chains them into poses in a single frame.
Tags without a path to the anchor stay out and are reported, not silently omitted.

Two companions to this:

- `merge_tag_poses(poses)` — several paths to the same tag yield several estimates; averaging is done via quaternion mean instead of
  component-wise.
- `residuals(observations, placed)` — closure error per edge in mm and degrees. **This is the quality figure of the map.** A board that was
  captured from two directions must close; if it does not, the calibration or the tag size is wrong.

The procedure for the operator: capture an image series in which neighbouring tags overlap pairwise → `build_tagmap.py` → map with closure
errors → map to `config/tagmap.json`.

---

## 3. Data formats

### 3.1 Calibration — `data/calibration/<name>.json`

Away from the OpenCV `FileStorage` YAML, towards JSON. Reasons:

- readable without cv2 → identity and resolution check without an OpenCV import
- `rms`, image size, board geometry and timestamp fit in; today `rms` only ends up on stdout and `image_width/height` are written but
  **never read**
- diffable, and readable by hand in case of an error

```jsonc
{
  "schema": "wsc.vision.calibration/1",
  "calibrationId": "cam_ceiling@2026-09-17T14:03:11Z",
  "frameId": "cam_ceiling",
  "imageSize": [2028, 1520],
  "cameraMatrix": [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
  "distortionCoefficients": [k1, k2, p1, p2, k3],
  "rmsReprojectionError": 0.31,
  "sampleCount": 22,
  "board": { "type": "charuco", "cols": 7, "rows": 5,
             "squareSizeM": 0.030, "markerSizeM": 0.022, "dictionary": "DICT_4X4_50" },
  "createdAt": "2026-09-17T14:03:11Z"
}
```

Storage under `data/` is right: the calibration belongs to **one physical camera**, not to the repo. `.gitignore:3` excludes `data/` — this is
desired here, because every Pi generates its own.

`check_resolution()` raises if the calibration belongs to a different resolution than the current image. This is not theory:
`AprilTagProfileConfig.resolution` is set to `(2028, 1520)`, while the calibration prototype runs via `cv2.VideoCapture(0)` with the default
resolution. Without a check, all poses would be wrong by the scale factor — and plausibly wrong, hence unnoticed.

### 3.2 Tag map — `config/tagmap.json`

```jsonc
{
  "schema": "wsc.vision.tagmap/2",
  "frameId": "world",
  "anchorTagId": 0,
  "tagFamily": "tag36h11",
  "tags": [
    // The four world tags in the border area of the cell — the only thing that is fixed.
    { "tagId": 0, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 1, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 2, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 1.800, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 3, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 1.800, 0], "orientation": [0, 0, 0, 1] } },

    // The robot is a module: movable, several tags on the same base.
    { "tagId": 20, "role": "robot", "sizeM": 0.080,
      "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [0, 0, -0.120], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null },
    { "tagId": 21, "role": "robot", "sizeM": 0.080,
      "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [-0.150, 0, -0.120], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null },

    { "tagId": 7, "role": "module", "sizeM": 0.050,
      "moduleId": "MOD-A", "instanceId": "mod-a-1",
      "tagToModule": { "position": [0, 0, -0.040], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null }
  ]
}
```

`poseInWorld: null` means "movable, is measured". `role` decides the use: **only** `world` provides `T_world_cam`; `module` and `robot` are
localised. The positions above are placeholders — the real world poses of the four world tags are surveyed once via
`python -m tagloc.cli.build_tagmap`.

#### Who maintains what: Pi measures, frontend names

The tag map contains two different kinds of information, and they belong **not** in the same place:

| Field | Purpose | Location |
|---|---|---|
| `poseInWorld` of the world tags | the Pi calculates `T_world_cam` from it | **Pi** — without it no world pose arises |
| `sizeM` | enters `solvePnP`; a wrong size scales the distance linearly | **Pi** — pure optics |
| `tagFamily` | detector setting | **Pi** |
| `moduleId`, `instanceId` | which module a tag belongs to | **Consumer** |
| `tagToModule` | CAD offset tag → module origin | **Consumer** |
| Name, box size, 3D model | presentation | **Consumer** |

The upper part is measurement technology and depends on the printed tags and the survey of the cell; the lower part is meaning. The world
poses cannot be moved out: `localize_camera` runs per image in the detection job on the Pi, and the chaining is to stand in exactly one
place (section 1.2).

#### Tag sizes: two kinds, two places

The **world tags are printed in a different size from all other tags**, and all module tags have the same size among themselves. From this
follows where a size is maintained:

| Tag | Size comes from | Changed when |
|---|---|---|
| **World tag** | `sizeM` of its entry in `config/tagmap.json` | only when the cell is rebuilt |
| **Module tag** | `tag_size_m` of the profile (`PI_APRILTAG_PRESETS`) | once, applies to all modules |
| deviating special case | own entry with `sizeM` in the tag map | via `SetTagMap`, without file access |

`TagMap.size_for` implements this: if a tag is in the map, its `sizeM` wins; otherwise the profile value applies. A new module therefore needs
**no entry on the Pi** — it inherits the module size and is reported as `TAG-<id>` until the consumer gives it a name.

A wrong tag size scales the measured distance linearly and otherwise goes unnoticed. Therefore: **measure the printed edge**, do not enter
the ordered one — and per kind, only in one place.

**Caution, double offset.** `tagToModule` may appear in *one* place only. The frontend of WebSkillComposition calculates the offset itself
(`cell-modules/model/cellModules.ts`, `moduleWorldPose`) and assumes that the Pi sends the **tag** pose. If the same offset also stands in
the tag map, the Pi applies it too — the module then lands offset by twice the amount, plausible-looking and wrong. As long as the consumer
maintains the offset, it stays out of the Pi's map.

`config/tagmap.json` is therefore very small in operation: the world tags, and nothing else. The example file does show `moduleId` and
`tagToModule`, because it documents the **schema** — for operation with this frontend, neither field is needed.

For the first setup test the four world tags are not yet needed: one world tag at the origin (identity pose, which then *is* the world frame)
and one module tag suffice. The template for this is
[`config/tagmap.test.example.json`](../../config/tagmap.test.example.json); `validate_tag_map` then flags the missing world tags, but does not
abort (see [E2E test guide](apriltag-e2e-test.md), "Kleiner Aufbautest" — "small setup test").

A map in schema `/1` is **migrated** on load: `reference` → `world`, `robot_table` → `robot`, losing its world pose. Every reinterpretation is
logged individually, so that nothing happens silently — that was the reason to reject them entirely at first. In practice, however, the
rejection silenced the entire detection, because `AprilTagDetectionSource.open` failed on it. An unusable map now lets the server continue
**without a map**: the tags are reported as `TAG-<id>` in the camera frame, so the frontend gets its placeholder boxes.

**New folder `config/`**, because the tag map describes the cell layout and must be versioned — `data/`, `concept/` and `hardware/` are all
gitignored, and new files there would be silently invisible to `git add` ([`altlasten.md`](arbeitsplaene/altlasten.md) D1).

---

## 4. The four tools

All four accept `--source` as a camera **or as an image folder**. This is the central trick for testability: the complete path is
reproducible on stored images, without Pi and without camera.

| Script | Task | Usable without hardware |
|---|---|---|
| `python -m tagloc.cli.calibrate` | camera calibration, interactive or headless from an image folder | yes, `--source ./bilder/` |
| `python -m tagloc.cli.detect` | detect tags, output poses, optional overlay | yes |
| `python -m tagloc.cli.transform` | convert a pose from frame A to frame B, apply tag map | yes, pure calculation |
| `python -m tagloc.cli.build_tagmap` | place tags from an image series in a common frame | yes |

The essential difference from the prototype (`src/apriltag/`, removed on 30.10.2026): `calibrate_camera.py` had **no** way to calibrate from
existing images without a GUI — the entire state lives in the keyboard loop. The calibration core is extracted as
`calibrate_from_samples(samples, image_size, board)`; the live UI becomes a thin layer on top of it.

---

## 5. Integration into the vision server

The server core remains untouched. Specifically: nothing in `job.py`, `payload.py`, `events.py`, `result_management.py`,
`state_machine.py`, `address_space.py`.

### 5.1 New detection source

`src/vision_server/detection/apriltag.py`:

```python
class AprilTagDetectionSource(DetectionSource):
    profile_id = "apriltag"
    is_simulated = False

    def __init__(self, config: AprilTagProfileConfig, *,
                 camera=None, detector=None, calibration=None, tag_map=None) -> None:
```

- `frame_id` / `frame_convention` from the config instead of hard-coded
- `configuration_id` = tag family + calibration identity + tag map identity →
  closes [`altlasten.md`](arbeitsplaene/altlasten.md) C4 (today the field is built, but empty)
- `open()` loads calibration and tag map, builds the detector, opens the camera
- `acquire_and_detect()` takes `samples_per_job` frames, runs **every** cv2 operation via `run_blocking`, averages the poses, discards tags
  via `max_reproj_error_px`, and respects `capture_timeout_s` as its **own** timeout (`asyncio.wait_for` cannot kill the worker thread)
- no detection → `VisionJobError(DETECTION_FAILED, …)`

Attributes per detection: `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`, `frameTimestamp`, `recipeId`. Exactly these keys are already
anticipated by `tests/test_payload.py:117-139` — the payload schema `wsc.vision.detections/1` remains unchanged; new keys are allowed additively.

### 5.2 Registry and routing

```python
# detection/__init__.py — import inside the factory, cv2 must not be on the import chain
DETECTION_SOURCES = { "hello_world": …, "calibration": …, "apriltag": _apriltag }

# config.py
DEFAULT_RECIPE_PROFILES = (
    ("", "hello_world"),
    ("hello-world", "hello_world"),
    ("calibration", "calibration"),
    ("apriltag", "apriltag"),
)
```

**Layer 1 and layer 2 get no profile of their own.** Each Pi runs its own server with exactly one `AprilTagProfileConfig`; the difference lies
in `src/vision_server/server.py`, where `PI_IDENTITIES` already distinguishes between `pi-decke` and `pi-hand`:

| Field | Layer 1 (`pi-decke`) | Layer 2 (`pi-hand`) |
| --- | --- | --- |
| `frame_id` | `world` | `cam_flange` |
| `calibration_path` | `data/calibration/cam_ceiling.json` | `data/calibration/cam_flange.json` |
| `tag_size_m` | 0.100 | 0.050 |
| `resolution` | 2028×1520 | 640×480 |
| `samples_per_job` | 3 | 5 |
| `max_reproj_error_px` | 3.0 | 1.5 |

One profile, one codebase, two configuration lines — this is the reuse that was required.

### 5.3 Decouple the camera live stream

`runner.py:49-74` accesses the live-stream handle **by name** via `sources.get("image_recognition")` and its attribute `.camera`. If the QR
source is removed, the live stream dies silently (only a `_log.error`). Therefore **before** anything else:

```python
def _camera_owner(sources, opened) -> SharedCamera | None:
    """First opened source that holds a shared camera."""
    for profile in sorted(sources):
        camera = getattr(sources[profile], "camera", None)
        if camera is not None and opened.get(profile):
            return camera
    return None
```

The stream thus depends on a property instead of a name, and the AprilTag source inherits it without further action.

---

## 6. The QR-code test is removed

QR was the feasibility proof for the camera path: real hardware, real image, real result in the 40100 payload. The proof is delivered;
AprilTags are the marker technology actually planned. The removal, in this order:

| # | What | Where | |
|---|---|---|---|
| 1 | Decouple the live stream from the profile name (**first**, otherwise the stream dies) | `src/vision_server/runner.py` | done |
| 2 | Delete the QR tests; take over the generic cases to `tests/test_apriltag_source.py` | `tests/test_image_recognition.py` | done |
| 3 | Delete the source, remove factory and registry entry | `detection/image_recognition.py`, `detection/__init__.py` | done |
| 4 | Replace recipe `("image-recognition", …)` with `("apriltag", "apriltag")` | `src/vision_server/config.py` | done |
| 5 | Adjust expectation lists to `["apriltag", "calibration", "hello_world"]` | `tests/test_recipe_routing.py` | done |
| 6 | Remove `qr_scan_duration_s` from the shared camera config | `src/vision_server/profiles.py` | done |
| 7 | `job_timeout` re-justified: `samples_per_job × capture_timeout_s` plus reserve → 20.0 | `src/vision_server/config.py` | done |
| 8 | Delete the orphaned QR scanner ([`altlasten.md`](arbeitsplaene/altlasten.md) C6) | `src/apriltag/caputure.py` | done |
| 9 | Update the docs: sections 1, 4 (jobs), 9, 10 | [`vision-server-interface.md`](vision-server-interface.md) | done |

> The complete removal was carried out, because otherwise QR would have stood in the production path without a purpose. Everything can be
> restored via `git checkout 5036408 -- <file>`.

Not affected: `job.py`, `payload.py`, `events.py`, `result_management.py`, `state_machine.py`, `address_space.py`.

---

## 7. End-to-end testability

The test framework remains `unittest` from the standard library, the idiom remains hand-written fakes instead of `unittest.mock`, and the
command remains:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Four levels, each runnable on its own:

**Level 1 — mathematics, without anything.**
`tests/test_tag_geometry.py`, `tests/test_tag_map.py`, `tests/test_calibration_io.py`.
Golden values: known `rvec`/`tvec` → expected quaternion (xyzw, sign normalised); `invert(compose(a, b))` as round trip; `place_tags` on
synthetically constructed co-observations whose result can be computed by hand; calibration round trip via a tempdir. No cv2, no camera.

**Level 2 — image, without camera. This is the actual E2E test.**
`tools/make_synthetic_scene.py` renders, with `cv2.aruco.generateImageMarker` and `cv2.warpPerspective`, a scene with **known** tag poses.
The test runs the full chain over it — detector → `estimate_tag_pose` → `place_tags` → `locate_modules` — and compares with the truth.
If cv2 is missing, `unittest.skipUnless` applies.

Measured over all tags and views: position at most **0.29 mm** (limit 2 mm), angle median **0.031°** with one outlier of **0.50°** (limit 1°),
reprojection error at most **0.063 px** (limit 1 px). The outlier is not an ambiguity — for it the second IPPE solution is 82× worse — but
the pixel quantisation of the rendered marker at a 34° oblique view. The angle limit is therefore 1° and not the 0.5° from the concept: at 0.5
it sat exactly on the edge of the distribution. A truly wrong pose is off by double digits (the discarded alternative: 67.8°), so the limit
still cleanly separates right from wrong.

Test images are **generated in the test, not committed**: `.gitignore:14` excludes `*.jpg`, and the repo is in any case kept so that no
fixture files exist.

**Level 3 — server, without camera.**
`tests/test_apriltag_source.py` with `FakeCamera` (`open`/`close`/`push_frame`/`latest_frame`) and a fake detector, following the pattern of
today's `tests/test_image_recognition.py`: the source is never `open()`ed; detector and calibration are injected. Checked are the use of
`run_blocking`, the capture timeout, the filled `configurationId`, `frame_id`/`frame_convention` from the config, the attributes in the payload,
and the `DETECTION_FAILED` path without a tag.

**Level 4 — real hardware.** `python -m tagloc.cli.detect --source camera --overlay`, described in
[`apriltag-referenz.md`](apriltag-referenz.md). Not part of the suite.

---

## 8. What Sven did

Sven Nachtigal (`NobbisCode`) built the camera and job side of the vision server between **09. and 11.09.2026** in **11 commits** — without
its own branch, first on `main`, then directly on `feature/vision-server`; the merge point with Meric's parallel line of work is `9f8ea16`.

| Area | Commits | Result |
|---|---|---|
| OPC UA base framework | `375311f`, `5e63042`, `29820d6`, `bf00021`, `685f3a1` | creates `src/OPCUA/server.py` in the first place — the file into which `install_vision_machine()` was later hooked; plus the debug client `print_setpoint.py`, `.gitignore` and the `asyncua` version |
| Multi-job capability | `f2aadf9`, `f835e46` | the single-job server (only hello world) becomes a server with several `RecipeId` profiles; new: `detection/script_runner.py`, a `DetectionSource` that starts a Python script as a subprocess, plus the placeholders under `src/jobs/` |
| Real camera and QR | `3a7b7d9`, `104194e`, `77540d1` | first QR in a subprocess, then the switch to `SharedCamera`: exactly one Picamera2 open, one capture loop, several readers. Plus the camera live stream as Base64 JPEG in the node `LatestCameraFrame` (`camera_stream.py`, `CameraStreamConfig`) and the in-process source `detection/image_recognition.py` |
| Job abort | `5036408` | `Stop` of the `AutomaticModeStateMachine` actually implemented: subprocess `terminate()`, after 2 s `kill()`, return to `Ready`, `ResultReadyEvent` with `resultState=7` (`CANCELLED`), `stop_timeout` |

Created by him: `src/OPCUA/server.py`, `src/OPCUA/print_setpoint.py`, `src/vision_server/camera.py`, `camera_stream.py`,
`detection/script_runner.py`, `detection/image_recognition.py`, as well as the tests `test_camera_stream.py`, `test_image_recognition.py`,
`test_script_runner.py` (and extensions to `test_job_runner.py`).

He documented exclusively [`vision-server-interface.md`](vision-server-interface.md) — around 115 of 462 lines, including the sections
"Verfügbare Jobs (`RecipeId`)" (available jobs), "`Stop`" and, in full, "Kamera-Livestream" (camera live stream).

**Relation to this plan:** Sven did not work on AprilTags himself — but `SharedCamera` and `ImageRecognitionDetectionSource` are exactly the
infrastructure and template on which the AprilTag source builds. The QR detection that is removed here was the proof that this path carries;
what remains is the camera underneath it. His `script_runner.py` also stays in operation: the `calibration` job continues to run through it.

---

## 9. Implementation order

Steps 1–7 and 9 are implemented, the suite is green.

| # | Step | State |
|---|---|---|
| 1 | Decouple the live stream (`runner.py`) + test | done, `tests/test_camera_owner.py` |
| 2 | `geometry.py` + golden-value tests | done |
| 3 | `calibration.py`, `tagmap.py` incl. `place_tags` + tests | done |
| 4 | `boards`, `detector`, `pose`, `localize`, `frames`, `overlay` + synthetic E2E test | done |
| 5 | CLI scripts; calibration core extracted from `calibrate_camera.py` and parameterised | done |
| 6 | `detection/apriltag.py` + registry + recipe + tests | done |
| 7 | QR removal per section 6 | done |
| 8 | Dissolve `src/apriltag/` | done (30.10.2026) |
| 9 | Configuration per Pi in `server.py`, update docs | done |

On step 8: the prototype folder initially remained and was marked as superseded in `MANUAL_TEST.md`. Deleting it before the new chain had run
once on the real Pi camera would remove the only means of comparison. `src/jobs/calibrate.py`, by contrast, is already converted — it is now a
readiness check instead of a placeholder. **Prototype `src/apriltag/` removed on 30.10.2026** (deletion condition met, see
[`altlasten-zelle.md`](arbeitsplaene/altlasten-zelle.md) T1).

Two things that were missing from the original plan were added:

- **Live-stream overlay with switchable mode** — the writable node `CameraStreamMode` and `stream_overlay.py`; see
  [`vision-server-interface.md`](vision-server-interface.md) section 10.1.
- **Explicit string NodeIds** for `LatestCameraFrame` and `CameraStreamMode`. Otherwise `add_variable(own_idx, …)` assigns numeric ids
  automatically (`ns=3;i=1`), which shift as soon as someone creates a node in front of them — and the backend subscribes to these nodes
  firmly.

---

## 10. Open points and what this plan suggests for them

The questions from [`concept/offene_punkte.md`](../../concept/offene_punkte.md) remain decisions of the team. What this draft suggests:

| Open point | Suggestion from this draft |
|---|---|
| When is the world tag referenced? | **at every layer-1 scan.** The ceiling camera sees the board anyway; `T_world_cam` is re-determined per job and logged, so that drift becomes visible instead of propagating silently. |
| Format of the hand-over from layer 1 to layer 2 | **3D pose in the world frame**, i.e. `position` + `orientation` of the existing payload — no pixel coordinate. The schema `wsc.vision.detections/1` thus stays unchanged. |
| Who transforms world frame → robot base? | **the ceiling camera measures it**. The robot tags carry the CAD offset tag → robot base, so `T_world_base` arises as a measurement result. It is no longer stored as fixed: the robot is movable. |
| Role of the robot-table tag | **dropped.** The table was never a fixed anchor — that was the reasoning error. The robot carries its own tags with the role `robot` and is measured like every module. |
| Which world tag applies to the robot? | the nearest one, determined from the ceiling image (`world_tag_for_robot`). Only the ceiling camera sees robot and world tags at the same time, so only it can answer this question. |
| How do ceiling and hand measurements combine? | `merge_locations(decke, hand)` — pure function in `tagloc`. The more precise source (`flange`) wins; what only the ceiling saw is kept. No connection between the two Pis is needed. |
| Hand-eye calibration for the hand camera | **calculated and available on the tool side**, but not yet performed on the real hardware. `T_flange_cam` is a constant (camera rigid on the robot) and is determined once via `tagloc.cli.calibrate_handeye`. As long as no measured file exists, only the optical route acts, and `auto_execute` stays `False` — automatic driving to detected poses may only be enabled once the calibration on the robot is proven. |
| Repositioning strategy layer 2 | **not part of this library.** `tagloc` reports "kein Tag gefunden" (no tag found) as `DETECTION_FAILED`; what the robot then does belongs in the flow control. |
| Target number of modules | derivable from the tag map: every entry with `role: "module"` is an expected module. The abort criterion "alle Modul-Tags gesehen" (all module tags seen) can thus be formulated without additional configuration. |
