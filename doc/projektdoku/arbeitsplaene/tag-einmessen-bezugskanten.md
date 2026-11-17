# Survey tag position with the hand camera — reference edges instead of mesh matching

**Status:** done — 20.10.2026 (not tested on real hardware)
**Owner:** Meric, `Agent: survey tag position with the camera via reference edges`
**Topic:** apriltag
**Branch:** `tag-einmessen` (this repo and `webskillcomposition`)

## Goal

The tag position of a module (`T_module_tag`) is no longer measured with a folding rule, but with the hand camera via the reference edges. Afterwards the hand Pi delivers, per reference edge, the measured position in the tag frame and a corrected tag pose in the module frame. Previously the distances were measured by hand, and the measurement error went 1:1 into every module pose.

### Context

`T_module_tag` (`ModuleTag.position`/`rotationDeg` in the module catalog, frontend only; the tag map of the Pi contains only world tags) is determined by hand today: in the catalog choose face + corner or two edges (`placement`, see `modell-flaechen-tag-platzierung.md`), then measure the distances with the folding rule. The error goes 1:1 into every module pose.

The first plan (depth ICP against the whole mesh) is **discarded** (see section "Discarded"): the modules change constantly, the models are not maintained — a match against the whole model would then measure against outdated geometry.

**New approach: the camera replaces the folding rule.** Of the model only what the folding rule also needs is still used: the **two reference edges** chosen when placing in the catalog (table edge, profile end edge …). These are long-lived physical edges; the rest of the model may be outdated. The hand camera sees tag and edges **in the same image** → measurement in the tag frame, entirely without robot kinematics and hand-eye error. The catalog value remains the start value and determines the search window.

## Already read

- `CLAUDE.md` (work-plan requirement, rules for changes)
- `doc/projektdoku/arbeitsplaene/_vorlage.md`
- `doc/projektdoku/arbeitsplaene/README.md` (no open `apriltag` plan on this topic)
- `doc/projektdoku/arbeitsplaene/layer2-measuretags.md`: pattern for interfaces, intrinsics, failure cases; `measure_tags` is the basis of this measurement
- `doc/projektdoku/arbeitsplaene/modell-flaechen-tag-platzierung.md`: `placement` format, `EdgeLine`, operation "Auf Modell platzieren" ("Place on model")
- Approved plan `thema-tag-positionen-auf-modulen` (concept, interfaces, division among agents, verification)

## Affected files

This list reserves the files. Parallel changes to them are coordinated beforehand.

**New:**
- `src/tagloc/reference_edges.py`: pure functions (numpy/cv2), calculation and view planning
- `src/vision_server/tagreference.py`: parse the request, build the result JSON (like `measuretag.py`)
- `tests/test_reference_edges.py`
- `tests/test_tagreference.py` incl. OPC UA method test (pattern `tests/test_measuretag.py`)

**Changed:**
- `tools/make_synthetic_scene.py`: `render_reference_scene` (synthetic renderer)
- `src/vision_server/detection/apriltag.py`: `measure_tag_reference` (fresh images like `measure_tags`)
- `src/vision_server/runner.py`: `_install_tag_reference`, BUSY lock
- `src/vision_server/address_space.py`: nodes `MeasureTagReference`, `PlanTagReferenceView`, `LastTagReference`

## Interfaces

### Python — `src/tagloc/reference_edges.py` (new, pure functions, numpy/cv2)

```python
@dataclass(frozen=True)
class ReferenceLine:          # reference edge, module frame, metres
    id: str
    start: np.ndarray         # (3,)
    end: np.ndarray           # (3,)

@dataclass(frozen=True)
class LineMeasurement:
    id: str
    found: bool
    guess_point_tag: np.ndarray; guess_direction_tag: np.ndarray   # start value in tag frame
    point_tag: np.ndarray | None; direction_tag: np.ndarray | None # measured in tag frame
    offset_m: float | None    # perpendicular shift measured − start value, in the tag plane
    angle_deg: float | None   # rotation measured − start value about tag z
    support_px: float; residual_px: float; contrast: float
    candidates: int; ambiguous: bool
    reason: str | None        # "NOT_VISIBLE" | "NO_EDGE" | "PERPENDICULAR_TO_TAG" | None
    extent_m: float = 0.0     # length of the measured edge piece in the tag plane; weights the rotation, not in the result JSON

@dataclass(frozen=True)
class InPlaneCorrection:
    dofs: str                 # "full" | "partial" | "none"
    delta_xy_m: np.ndarray    # (2,) in tag frame
    delta_rot_deg: float
    pose_module_tag: Pose     # corrected T_module_tag
    residual_m: float

@dataclass(frozen=True)
class ViewPlan:
    pose_tag_cam: Pose; standoff_m: float; fits: bool; uncovered: list[str]

def measure_reference_lines(gray, calib, pose_cam_tag, pose_module_tag_guess,
        lines: Sequence[ReferenceLine], *, tag_size_m: float,
        search_band_m: float = 0.015, tag_margin_m: float = 0.01) -> list[LineMeasurement]
def fit_in_plane_correction(measurements: Sequence[LineMeasurement],
        pose_module_tag_guess: Pose) -> InPlaneCorrection
def average_corrections(corrections: Sequence[InPlaneCorrection]) -> tuple[InPlaneCorrection, dict]  # spread mm/deg
def plan_reference_view(pose_module_tag_guess, lines, calib, *, tag_size_m,
        line_extent_m=0.10, margin=0.10, min_standoff_m=0.25, max_standoff_m=0.60,
        tilt_deg=0.0) -> ViewPlan
def catalog_rpy_deg(rotation) -> np.ndarray   # catalog format: RPY fixed axes X,Y,Z, degrees
```

`ValueError` for invalid input (edge shorter than 2 cm, size ≤ 0, non-finite values, resolution does not match the calibration).

Reused are `tagloc.geometry` (`compose`, `invert`, `pose_to_dict`, `from_position_quaternion`), `tagloc.lens` (`project_points`, `undistort_points`) and `tagloc.pose.tag_object_points`.

### Python — `src/vision_server/tagreference.py` (new, without asyncua and without camera)

```python
parse_request(text: str) -> TagReferenceRequest          # ValueError with plain text on schema, required-field or limit violation
combine_tag_pose(tag_poses) -> tuple[np.ndarray, bool]   # (T_cam_tag, ambiguous) from the images with tag
keep_guess_rotation(correction, pose_module_tag_guess) -> InPlaneCorrection  # only the tag origin moves, rotation from start value
summarize_lines(per_frame) -> list[dict]                 # one line per edge across all images
reference_warnings(*, correction, lines, search_band_m, tag_ambiguous, intrinsics_source) -> list[str]
build_result(*, ..., error: str | None = None) -> dict   # wsc.vision.tagreference.result/1
build_view(plan=None, *, error: str | None = None) -> dict  # wsc.vision.tagreference.view/1
dumps(result: dict) -> str                               # json, allow_nan=False
```

`TagReferenceRequest` (frozen): `tag_id`, `tag_size_m` (`None` = tag map resp. config), `pose_module_tag_guess`, `lines`, `search_band_m`, `frames`, `line_extent_m`, `tilt_deg`.

### OPC UA (hand Pi only, `hand_eye_path` set; under `VisionMachine`, linked under `VisionProgram`)

| Element | NodeId | Type |
| --- | --- | --- |
| `MeasureTagReference` | `ns=<vision>;s=VisionMachine.MeasureTagReference` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` |
| `PlanTagReferenceView` | `ns=<vision>;s=VisionMachine.PlanTagReferenceView` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` — calculates only, no image |
| `LastTagReference` | `ns=<vision>;s=VisionMachine.LastTagReference` | String (JSON), for display only |

| Error | `MeasureTagReference` | `PlanTagReferenceView` |
| --- | --- | --- |
| `OK` | measured. Also if edges are missing (then `dofs` reduced resp. `none`). | planned, also with `fits: false`. |
| `DETECTION_FAILED` | `NO_FRAME`, `TAG_NOT_SEEN`; also a `ValueError` of the measurement (e.g. image size does not match the calibration) | not applicable (no image) |
| `INVALID_ARGUMENT` | request JSON resp. values invalid | as left |
| `INVALID_STATE` | vision system not `Operational` | as left |
| `BUSY` | job, camera calibration, hand-eye session, `MeasureTags` or another survey is running | no `BUSY`: calculates only, no image |

On rejection the JSON contains `"<CODE>: text"`; for measurement errors (`NO_FRAME`, `TAG_NOT_SEEN`) the bare code. `ResultJson` is always valid JSON.

`LastTagReference` is written after every measurement, also on `DETECTION_FAILED`, but not on rejections (`INVALID_ARGUMENT`, `INVALID_STATE`, `BUSY`).

### Data / payload

**Request** `wsc.vision.tagreference.request/1` (for both methods):

```json
{ "schema": "wsc.vision.tagreference.request/1",
  "tagId": 7, "tagSizeM": 0.08,
  "moduleTagGuess": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "lines": [ { "id": "A", "start": [x,y,z], "end": [x,y,z] },
             { "id": "B", "start": [x,y,z], "end": [x,y,z] } ],
  "searchBandM": 0.015, "frames": 3,
  "view": { "lineExtentM": 0.10, "tiltDeg": 10 } }
```

- `moduleTagGuess` = `T_module_tag` from the catalog (`position`/`rotationDeg` converted).
- `lines` = real 3D edges in the module frame (the `EdgeLine`s of the placement, **not** projected onto the face); 1–4 pieces.
- `tagSizeM` (optional): precedence request > tag map > `config.tag_size_m`.
- `searchBandM` (default 0.015; 0.003–0.05 m), `frames` (default 3; 1–10), `view.lineExtentM` (default 0.10; (0, 1] m), `view.tiltDeg` (default **10**; |tiltDeg| < 80).
- `lines`: `id` not empty and unique, every edge ≥ 2 cm.
- `moduleTagGuess.orientation` is a quaternion `[qx, qy, qz, qw]`: it is normalised, a zero quaternion is invalid.
- Unknown fields are ignored.

**Result** `wsc.vision.tagreference.result/1`:

```json
{ "schema": "wsc.vision.tagreference.result/1",
  "capturedAt": "…", "capturedUntil": "…", "imageSize": [640,480],
  "intrinsicsSource": "calibration|realsense_factory|placeholder", "framesUsed": 3,
  "tag": { "poseInCamera": {…}, "reprojectionErrorPx": 0.4, "viewAngleDeg": 3.1, "ambiguous": false },
  "lines": [ { "id": "A", "found": true, "offsetMm": 1.8, "angleDeg": 0.4,
               "supportPx": 210, "residualPx": 0.3, "contrast": 41, "candidates": 2,
               "ambiguous": false, "reason": null } ],
  "correction": { "dofs": "full", "deltaMm": [1.8, -0.6], "deltaRotDeg": 0.4,
                  "moduleTag": { "position": [...], "orientation": [...] },
                  "position": [...], "rotationDeg": [...],
                  "spreadMm": 0.2, "spreadDeg": 0.05, "residualMm": 0.3 },
  "warnings": [],
  "error": null }
```

- `framesUsed`: images in which the tag was detected.
- Ambiguous tag pose: if there is at least one unambiguous image, the rotation is averaged only over these images, the position over all. One pose applies to all images (the robot stands still), the correction stays `full`. If there is no unambiguous image, the edges are measured with the ambiguous pose; the orientation then remains exactly the start value (`deltaRotDeg`, `spreadDeg` = 0), only the tag origin moves, and there is a warning.
- `lines[]`: one entry per edge across all images. `found` as soon as found in one image; values (`offsetMm`, `angleDeg`, `supportPx`, ...) as mean over the unambiguous images, otherwise over the found ones; `ambiguous` as soon as ambiguous in any image; `reason` only for edges not found.
- `warnings[]`: placeholder intrinsics; tag ambiguous; `dofs` `none` (start value unchanged) or `partial`; ambiguous edge; correction beyond the search band resp. an edge shift ≥ 0.9 × search band (start value presumably wrong).
- Failure case: `correction: null`, `lines: []`, `tag: null`. `error` is the bare code for measurement errors (`TAG_NOT_SEEN`, `NO_FRAME`), `"<CODE>: text"` for rejection.

**Planning** `wsc.vision.tagreference.view/1` (from `PlanTagReferenceView`, response):

```json
{ "schema": "wsc.vision.tagreference.view/1",
  "camInTag": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "standoffM": 0.38, "fits": true, "uncovered": [], "error": null }
```

`camInTag` is the camera pose in the tag frame (camera frame OpenCV: +Z out of the lens). The camera looks along −tag z; image x = tag x, image y = −tag y. `tiltDeg` tilts about tag x through the target point (positive: camera towards −y). `fits: false`: the tag is always in the image; edges are added one after another, ordered by distance to the tag, as long as everything fits at `max_standoff`. `uncovered` names the remaining edges (possibly `"tag"`). Rejection: `camInTag: null`, `fits: false`, `uncovered: []`, `error` with `"<CODE>: text"`.

### Catalog (frontend)

The data form is described in the section "Frontend" (`ModuleTag.measurement`, `placement.lines`). `position`/`rotationDeg` are overwritten with `correction.position`/`correction.rotationDeg`; `placement` remains as the record of the start value. **Module tags are deliberately not in the Pi's tag map** (there only world tags, see header comment `moduleCatalog.ts` in `webskillcomposition`). The catalog acts only in the frontend: layer-1 placement and the `moduleTagGuess` of the layer-2 requests.

## Failure cases

| Case | Behaviour |
| --- | --- |
| Tag not in image | `DETECTION_FAILED`, `TAG_NOT_SEEN` |
| No fresh image | `DETECTION_FAILED`, `NO_FRAME` |
| Edge outside the image | `found: false`, `reason: "NOT_VISIBLE"` |
| No edge in the search band | `found: false`, `reason: "NO_EDGE"` |
| Edge ambiguous (item groove) | `ambiguous: true`; the edge enters no correction (`dofs` possibly reduced) |
| Edge (nearly) perpendicular to the tag plane | `reason: "PERPENDICULAR_TO_TAG"` |
| Correction beyond the search band resp. edge shift ≥ 0.9 × search band | warning; start value presumably wrong |
| No edge usable | `dofs: "none"`, start value unchanged, warning |
| Tag `ambiguous` | If unambiguous images exist: rotation over them, position over all, correction `full`. Otherwise: rotation exactly from the start value, only the tag origin moves (`keep_guess_rotation`), warning |

## Approach

### Calculation (per image, hand Pi)

1. AprilTag → `T_cam_tag` (existing `measure_tags` pipeline, intrinsics as for MeasureTags).
2. Each reference edge (3D segment in the module frame, from the catalog) is mapped via the start value into the tag frame: `T_tag_module_guess = inv(T_module_tag_guess)`; remember its height `h` above the tag plane (tag z).
3. Project the edge into the image (`lens.project_points`, with distortion), shorten it to the visible part, mask out the tag area + border (the tag edges are themselves strong edges).
4. Edge search in the band ±`searchBandM` (default 15 mm, converted to px): every ~4 px a cross profile on the slightly smoothed grey image, gradient maxima at subpixel precision (parabola) → candidate points; RANSAC line fit; choice by proximity to the start value × support length × contrast, same polarity along the edge. A second candidate with ≥ 70 % of the score and > 2 px distance → `ambiguous` (item grooves!).
5. Rectify support points (`lens.undistort_points`) → viewing rays → intersection with the plane `z_tag = h` → 3D line in the tag frame (PCA).
6. **Correction in the tag plane** (3 DoF: x, y, θ): rigid 2D transformation that maps the start-value edges onto the measured ones (least squares point-to-line). 2 non-parallel edges (≥ 20°) → `dofs: "full"`; 1 edge → only perpendicular + rotation (`"partial"`, along the edge unchanged); 0 → `"none"`.
   `T_tag_module_new = D · T_tag_module_guess`, `T_module_tag_new = inv(…)`.
   Tilt and distance to the face (`offsetM`) remain from the start value (the tag lies flush).
7. Multiple images (`frames`): measure edges per image, average the correction over the images with the highest `dofs` level, report the spread. Multiple views are thereby trivial — the control calls several times and averages, no forward kinematics needed.

### Placing the camera appropriately

`plan_reference_view` computes the camera pose **in the tag frame** (`T_tag_cam`): frontal (tilt angle against mirror solution; request default 10 degrees, Python default 0), centred over the tag + the pieces near the tag of both edges (default 10 cm each), distance such that everything fits into the image with 10 % border (0.25–0.60 m). The control computes

`flange target = T_base_tag (from MeasureTags/layer 2) · T_tag_cam · inv(T_flange_cam)`,

drives there and calls the measurement. If not everything fits into one image → `fits: false`, `uncovered` names the missing edges. Without them the measurement lacks that edge (`NOT_VISIBLE`); v1 is then a partial correction.

### Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests -t .` (in the environment where cv2 is installed): all old and new tests green.
- Synthetic (`reference_edges`): start error 1 cm / 2° → result < 0.5 mm / < 0.2°; groove double edge → `ambiguous` or correct edge; one edge → `partial`; edge outside → `NOT_VISIBLE`; view planning: projected tag corners and edge pieces lie in the image.
- OPC UA (`tagreference`): fake camera with synthetic image, call via asyncua client, result JSON checked.
- On the Pi (later, user): tag with folding-rule reference, target < 1–2 mm / < 0.5°. Record the result in `doc/projektdoku/`.

## Measurements (synthetic)

Synthetic scenes from `tools/make_synthetic_scene.py` (`render_reference_scene`), start error 1 cm / 2°. "Detector pose" means: tag pose from the AprilTag detection instead of the true pose. Error = deviation from the known truth. **Not measured on the Pi** (that is still outstanding).

**S1, single image, 1600 × 1200, f = 1600, tag 80 mm:**

| Case | Position error | Rotation error |
| --- | --- | --- |
| True pose, frontal | 0.024 mm | 0.014° |
| Detector pose, tilted 15° | 0.27–0.28 mm | 0.045° |
| Detector pose, frontal | 0.24 mm | 0.013° |
| Distortion k1 = −0.12 | 0.034 mm | – |
| Fisheye distortion | 0.029 mm | – |
| Blur 3 px + noise σ = 6 | 0.015 mm | – |
| Edge 3 cm below the tag plane, true pose | 0.010 mm | – |
| Edge 3 cm below the tag plane, detector pose, tilt 5–20° | 0.05–0.21 mm | – |

**S2, measurement by `measure_tag_reference`, 640 × 480, f = 600, 3 images:**

| View | Position error | Rotation error |
| --- | --- | --- |
| Tilt 10°, distance 0.296 m | 0.63 mm | 0.07° |
| Tilt 15°, distance 0.298 m | 0.23 mm | 0.10° |
| Tilt 10°, distance 0.35 m | 0.25 mm | 0.11° |

**Limits:**

- A frontal view of a raised tag yields the mirror solution of the tag pose (13°). Hence the default tilt of 10°.
- Groove with the same polarity as the edge: `ambiguous` → partial correction (`partial`).
- Almost invisible groove (< 8 grey levels contrast): the inner edge is measured silently, error 5 mm. Physically not detectable.
- Wrong tag size: all edges `NO_EDGE`.

## Frontend (webskillcomposition, branch `tag-einmessen`)

The control runs in the frontend (`webskillcomposition`) against the interfaces above. Paths of the files: see section "Files" below.

### Procedure (as of code)

1. **Module catalog:** "Place on model" saves the real 3D reference edges in `placement.lines` (corner: both corner edges A and B; edges: the two selected edges). Pending are tags without `measurement` (`pendingTags`), without world tags; the run takes only tags of modules that are placed in the cell (`TagReferencePanel`).
2. **Layer 1** (ceiling camera) places the modules roughly. From this the coarse tag pose `coarseTagInBase` (`approachTags`).
3. **Per tag** (`measureTagReferences` in `model/tagReferenceRun.ts`, one robot move after another; nothing is driven that the IK does not reach or that touches the cart or floor):
   a. **Approach:** if there is an approach angle (`approach`, from `planApproach` to the coarse value), the robot first drives there. Otherwise centring starts where it stands.
   b. **Centring:** `centreOnTag` with MeasureTags, standoff 0.35 m, start point is the layer-1 coarse value; searches if needed when the tag is not in the image. Result is `tagInBase`.
   c. **Orientation:** if the tag pose during centring is ambiguous (`ambiguous`, often mirrored at about 35 cm), only the measured position is taken over; the orientation stays the one from layer 1.
   d. **Plan view:** `PlanTagReferenceView` with all edges of the tag. For `fits: false` the edges from `uncovered` are omitted and re-planned with the rest (warning). If no edge remains, the tag fails. An `error` or missing camera pose likewise.
   e. **Reachability and path:** flange target `flangeForView(tagInBase, camInTag, flangeToCamera)`, joint angles via `planMove`. Not convergent: tag failed. A target that touches cart or floor is not driven. If the direct path is blocked, it goes via the start pose; if the path there is already blocked, the step fails.
   f. **Measure:** `MeasureTagReference` with `moduleTagGuess` and the edges from (d). Timeout 60 s.
   g. **Takeover:** only if `error` is empty, the intrinsics are not `placeholder` and `dofs` is not `none`. Then `onMeasured` (`keep` in the panel): `applyTagMeasurement` writes into the catalog; if it was saved in the meantime, it is re-applied once onto the newer state. If saving fails, the tag remains pending.

   Errors in a to g do not end the run: the tag gets its reason, the next one follows. A stop (`stopped`) sets the remaining tags to `skipped`.
4. **End:** without a stop the robot drives back to the start pose (`returnNote`); on a stop it stays where it is. Afterwards re-scan layer 1 so that the module poses use the new tag value (hint in the panel).

### Once-only rule

Requirement of the user dated 20.10.2026: every tag is surveyed only once, namely when its module was newly created or edited in the module catalog.

- `ModuleTag.measurement` missing = pending.
- Saving from the editor discards `measurement` for tags that are new or whose `tagId`, `sizeM`, `position`, `rotationDeg` or `placement` have changed. Unchanged saving keeps it.
- Import and loading leave `measurement` unchanged.
- Only pending tags of modules placed in the cell are surveyed.
- The start happens only by click (robot movement).

### Data format

```ts
interface TagReferenceLine { id: string; start: Vec3; end: Vec3 }   // module frame, m, not projected
TagPlacement.lines?: TagReferenceLine[]
interface TagCameraMeasurement { method: "camera-edges"; measuredAt: string; dofs: "full" | "partial";
  guess: { position: Vec3; rotationDeg: Vec3 }; deltaMm: [number, number]; deltaRotDeg: number;
  spreadMm: number; lines: string[] }
ModuleTag.measurement?: TagCameraMeasurement
```

`placement` stays after the measurement as the record of the start value.

### Old placements without `lines`

The edges are derived from `edgeA`/`edgeB`: length 0.15 m; for corner the second edge is laid along `inward` (90° assumption). Such placements are shown by the surface with the note "Kanten geschätzt" ("edges estimated").

### Files

Paths relative to `frontend/src/features/cell-modules/`, except `piServers.ts` (`frontend/src/features/opcua-server/config/`):

- `config/moduleCatalog.ts`: types `TagReferenceLine`, `TagCameraMeasurement`, `lines`, `measurement`
- `model/catalogStore.ts`
- `model/tagPlacement.ts`
- `model/tagMeasurementState.ts` (new): once-only rule
- `components/ModuleCatalogModal.tsx`
- `opcua-server/config/piServers.ts`: `TagReferenceBinding`
- `model/tagReferenceCall.ts` (new)
- `model/tagReferenceRun.ts` (new)
- `components/TagReferencePanel.tsx` (new), `components/TagReferenceWindow.tsx` (new, window from the settings)

**Signatures (as of implementation):**

`model/tagMeasurementState.ts`
- `DERIVED_LINE_LENGTH_M = 0.15`: length of the edges derived from `edgeA`/`edgeB`.
- `interface PendingTag { moduleId: string; tag: ModuleTag }`
- `pendingTags(catalog: CellModuleDefinition[]): PendingTag[]`: tags without `measurement`, without world tags.
- `referenceLinesOf(tag: ModuleTag): { lines: TagReferenceLine[]; derived: boolean } | null`:
  `placement.lines`, otherwise derived (`derived: true`); `null` without `placement`.
- `applyTagMeasurement(catalog, moduleId, tagId, measured): CellModuleDefinition[]`: sets
  `position`/`rotationDeg` to the measurement, `measurement.guess` to the previous value.
  Throws `Error` for unknown module or tag.
- `invalidateChangedMeasurements(previous, next, current?): CellModuleDefinition`: new tags and tags
  whose `sizeM`, `position`, `rotationDeg` or `placement` has changed lose `measurement`. Unchanged tags take the state from `current` (incl. an interim measurement); if the
  tag is missing there, it loses `measurement`. Without change `next` itself is returned.
- `describeTagMeasurement(tag: ModuleTag): string`: text for the tag line in the editor.

`model/tagReferenceCall.ts`
- `TAG_REFERENCE_REQUEST_SCHEMA`, `TAG_REFERENCE_RESULT_SCHEMA`, `TAG_REFERENCE_VIEW_SCHEMA`:
  `wsc.vision.tagreference.request/1`, `…result/1`, `…view/1`.
- `interface TagReferenceRequest { tagId; tagSizeM; moduleTagGuess: { position; rotationDeg }; lines: TagReferenceLine[]; searchBandM?; frames?; view?: { lineExtentM?; tiltDeg? } }`
- `tagReferenceRequestJson(request): string`: converts `rotationDeg` into a quaternion (`quaternionFromRpyDeg`).
- `parseTagReferenceResult(raw: unknown): TagReferenceResult | null`, `parseTagReferenceView(raw: unknown): TagReferenceView | null`:
  `null` for an unreadable response.
- `measureTagReference(controller, serverUrl, nodeId, requestJson, opts?): Promise<MeasureReferenceOutcome>`: timeout 60 s.
- `planTagReferenceView(controller, serverUrl, nodeId, requestJson, opts?): Promise<PlanViewOutcome>`: timeout 15 s.
- `opts` = `{ sleep?, timeoutMs? }` (for tests).
- `TagReferenceOutcome<Answer> = { status: "measured" | "planned"; result: Answer } | { status: "failed"; reason: string }`;
  `MeasureReferenceOutcome` and `PlanViewOutcome` built on top of it.

`model/tagReferenceRun.ts`
- `DEFAULT_CENTRING_STANDOFF_M = 0.35`
- `interface TagReferenceJob { moduleId; moduleName; tagId; tagSizeM; moduleTagGuess; lines; coarseTagInBase: Pose }`
- `TagReferenceJobResult`: `measured` (with `result`), `failed` (with `reason`, optional `result`), `skipped` (with `reason`); each with `warnings`.
- `measureTagReferences(options: TagReferenceRunOptions): Promise<TagReferenceRunOutcome>` → `{ results, halted, returnNote }`.
  Does not throw; errors are in the result per tag. Options: `jobs`, `deps`, `flangeToCamera`, `measureTags`,
  `planView`, `measureReference`, optional `approach`, `clearance`, `stopped`, `standoffM`, `onProgress`, `onMeasured`.
- `flangeForView(tagInBase: Pose, camInTag: Pose, flangeToCamera: Pose): Pose`

`config/moduleCatalog.ts`: types `TagReferenceLine`, `TagCameraMeasurement`, `TagPlacement.lines?`, `ModuleTag.measurement?`.

`opcua-server/config/piServers.ts`
- `interface TagReferenceBinding { measureNodeId; planViewNodeId; lastNodeId }`, in the hand-Pi profile as `tagReference`.
  Empty NodeIds mean: the method is missing on the Pi.

`components/TagReferencePanel.tsx`: default export `TagReferencePanel({ sceneState })`, in the window
`components/TagReferenceWindow.tsx` ("Tags einmessen · Hand-Kamera" ("Survey tags · hand camera")). It is opened in the settings under
"Zellüberwachung" ("Cell monitoring") with the button **"Tags einmessen"** ("Survey tags") next to "Modulkatalog" ("Module catalog") (`viewport/components/ViewportHeaderControls.tsx`).
Open/closed state is in `model/autolocateUi.ts` (`tagReferenceOpen`, `openTagReference()`, `closeTagReference()`, not kept across a reload). While the window is open, the layer-2 row in the autolocate area shows only a hint instead of the approach; both panels take over the robot (`useRobotHold`).

## Division of work

All agents of this repo work in the same worktree (base: current `apriltag` state,
branch `tag-einmessen`), each only on their own files. The frontend agents (S3, S4, H2 in the
docs part) work in the worktree of `webskillcomposition` on branch `tag-einmessen`. The coordination checks each diff, runs the tests
and commits (`docs:` / `feat(apriltag):`), **do not push**.

| # | Agent | Task | Files | When |
| --- | --- | --- | --- | --- |
| H1 | Haiku | Work plan (this plan, interfaces 1:1), row in `arbeitsplaene/README.md`, addendum in `modell-flaechen-tag-platzierung.md` with link; discarded mesh variant under "Discarded" | only `doc/projektdoku/…` | immediately, in parallel with S1 |
| S1 | Sonnet | `src/tagloc/reference_edges.py` (calculation 2–7, view planning, RPY), synthetic renderer `render_reference_scene` in `tools/make_synthetic_scene.py` (tag on bright surface with edges, optional groove double edge, noise, blur), `tests/test_reference_edges.py` | as named | immediately |
| S2 | Sonnet | `src/vision_server/tagreference.py` (parse/build like `measuretag.py`), `measure_tag_reference` in `detection/apriltag.py`, `_install_tag_reference` in `runner.py`, nodes in `address_space.py`, BUSY lock, `tests/test_tagreference.py` incl. OPC UA method test | as named | after S1 |
| S3 | Sonnet | Catalog page: `ModuleTag.measurement` and `placement.lines` in `config/moduleCatalog.ts`, `model/catalogStore.ts`, `model/tagPlacement.ts`, `model/tagMeasurementState.ts` (new), `components/ModuleCatalogModal.tsx`; once-only rule; derivation of old placements | webskillcomposition: as named | in parallel with S1/S2, against the interface of this plan |
| S4 | Sonnet | Calls and procedure: `opcua-server/config/piServers.ts` (`TagReferenceBinding`), `model/tagReferenceCall.ts` (new), `model/tagReferenceRun.ts` (new), `components/TagReferencePanel.tsx` (new, layer-2 window) | webskillcomposition: as named | after S3 |
| H2 | Haiku | Set plan to `done`, add the actual interfaces from the reports of S1/S2, new section in `vision-server-interface.md`, own section "Survey tag position with the camera" in `apriltag-referenz.md` (including the `placement` format, open point from the surface plan), README row; additionally docs on the frontend part (section "Frontend" of this plan, state after S3/S4) | docs only | after S2 resp. after S3/S4 |
| H3 | Haiku | Addendum "robot variant" in this plan (section "Addendum 20.10.2026", header, division of work, point "After completion"), index row | only `doc/projektdoku/…` | immediately (20.10.2026) |
| S5 | Sonnet | Robot variant in the frontend: `model/robotTagRun.ts` (new, pure), `TagCameraMeasurement` extended by `method: "robot-fk"` and the optional fields in `config/moduleCatalog.ts`, `parseMeasurement` in `model/catalogStore.ts`, `applyTagMeasurement`/`describeTagMeasurement` in `model/tagMeasurementState.ts`, block "Roboter-Tags (über Gelenkwinkel)" ("Robot tags (via joint angles)") in `components/TagReferencePanel.tsx` | webskillcomposition: as named | after S4 (panel), against the interface of the addendum |

## Discarded: depth ICP against the whole mesh

The first approach was to match the depth data of the camera against the entire 3D model
of a module (ICP). It is **discarded**, decision of the user dated 20.10.2026:

- The modules change constantly (set-ups come and go). A match against the whole model then measures against geometry that is no longer there at all.
- The models are not maintained. They may be outdated without anyone noticing.
- The reference edges, by contrast, are long-lived physical edges. Only they are measured.

## Open questions / risks

- **Height of the edge:** the height `h` comes from the start value. An error in `h` shifts the measurement sideways by `Δh · tan(viewing angle)`. Therefore measure frontally and keep the edge as close as possible to the image centre. Later optionally use the depth of the RealSense.
- **Low-contrast edges** (aluminium on aluminium): lighting and exposure via the existing camera controls.
- **Edges that do not fit into one image with the tag:** not supported in v1 (only partial correction), later chaining via forward kinematics.
- **Frontend part** (drive to view, write result into the catalog): implemented in branch
  `tag-einmessen` of `webskillcomposition`, described in section "Frontend".
- **Real 3D edges** (implemented in the branch, see section "Frontend"): the frontend sends the
  `EdgeLine`s as real 3D edges in `placement.lines`, not the edges projected onto the face from `placement.edges`.
- **Hardware tests outstanding** (as of 20.10.2026 not run on the robot):
  - Check the camera convention against `flangeToCamera` on the real robot (tag pose and flange target must match).
  - `centreOnTag` for tags lying to the side.
  - The base correction via world tag anchors (`apriltag-welttag-konzept.md`) is not used in the panel.
  - The conflict retry when saving the catalog (`keep`) has only been checked by code.
  - The status line in the editor shows the old state during a running measurement until saving.
- **Robot variant (not tested on hardware):**
  - Error from hand-eye calibration and joint angles is not measured (expected ~1–2 mm).
  - The scatter thresholds 3 mm and 1° are assumptions.
  - ±12° at 0.35 m is untested; whether the oblique view reaches `frameCount` ≥ 2 is open.
  - Tags lying to the side at the foot may be unreachable and then remain pending.
  - The cart bounds in the catalog must describe the footprint. The Demo1 default
    `boundsFromSize([0.3,0.3,0.3])` is a placeholder.
  - Joint points near the base below 0.05 m in the footprint would block every pose. This is an
    existing property of the clearance check, not introduced by this implementation.

## Deviations from the plan

- **Addendum 21.10.2026 — place of operation:** the tab "Tags einmessen" was built into `AutolocateLayer2Window.tsx`.
  This window has not been opened anywhere since `2b56b4f` (20.10.2026), so the tab was unreachable.
  Now: own window from the settings next to the module catalog (user request), webskillcomposition
  `10f73f2`, `d94673a`. The tab in the dead window is unchanged.

Filled in during implementation. If reality deviates from the plan, the plan is corrected.

Deviations, as of implementation:

- `LineMeasurement.extent_m: float = 0.0`: length of the measured edge piece in the tag plane,
  weights the rotation in the correction. Not in the result JSON.
- `view.tiltDeg` default **10** instead of 0 (frontally the tag pose tilts into the mirror solution). The
  Python default of `plan_reference_view` remains 0.
- Limits of the request: `searchBandM` 0.003–0.05 m, `frames` 1–10, `lineExtentM` (0, 1] m,
  `|tiltDeg|` < 80, `lines` 1–4 with non-empty, unique `id` and ≥ 2 cm. Quaternion is
  normalised, zero is invalid. Unknown fields are ignored. `tagSizeM` precedence: request >
  tag map > `config.tag_size_m`.
- Result: additionally `warnings: []` (placeholder intrinsics, tag ambiguous, `dofs` none/partial,
  ambiguous edge, correction close to the search band). Failure case: `correction: null`, `lines: []`,
  `tag: null`. `error` is the bare code for measurement errors, `"<CODE>: text"` for rejection.
- `framesUsed` counts images with a detected tag. `lines[]` is summarised per edge over the images;
  `ambiguous` becomes true as soon as ambiguous in any image.
- View: additionally `error` (null on success). Rejection: `camInTag: null`, `fits: false`,
  `uncovered: []`.
- Ambiguous tag pose: without an unambiguous image the orientation remains exactly the start value, only the
  tag origin moves (`keep_guess_rotation`); `deltaRotDeg` and `spreadDeg` are then 0.
- Error codes: `PlanTagReferenceView` has no `BUSY` (calculates only), but needs `Operational`
  (otherwise `INVALID_STATE`). `DETECTION_FAILED` also for a `ValueError` of the measurement (e.g. image size does not
  match the calibration). `MeasureTags` now additionally returns `BUSY` while a survey is running (**behaviour change in an existing method**).
- `LastTagReference` is written after every measurement, also on `DETECTION_FAILED`, but not on
  rejections.
- **Wrong in the plan:** "Afterwards tag map as before via `SetTagMap`" and "`tagToModule` in the
  tag map". Module tags are deliberately not in the Pi's tag map (there only world tags). The catalog
  acts only in the frontend.
- `catalog_rpy_deg` adopted like `tagGeometry.ts`: R = Rz · Ry · Rx, output `[roll, pitch, yaw]`
  in degrees; at pitch ±90° everything goes into roll, yaw = 0.
- Docs: H2 extended by the frontend part instead of creating a second H2 line; S3 and S4 new.
- Frontend: `shiftPlacement`/`rotatePlacement` do not exist in the frontend; therefore there no `placement` is adjusted for a shifted origin or rotated X axis.
- Frontend: `placementMatches` (`model/tagPlacement.ts`) accepts a measured tag as long as
  `measurement.guess` matches the `placement`. Otherwise the tag counts as manually overwritten.
- Frontend: ambiguous tag pose during centring: only the measured position is taken over, the orientation
  remains the one from layer 1 (not fixed in the plan).
- Frontend: for `fits: false` the control re-plans with the matching edges (`planTagReferenceView`
  a second time).

## Implementation and tests

- Pi (this repo, branch `tag-einmessen`): 737 tests green, of which 62 new.
- Frontend (`webskillcomposition`, branch `tag-einmessen`): 521 tests green, of which 78 new; `tsc` and `vite build` clean.
- Commits, this repo: `7c4ea3c`, `8a64a35`, `8c26b7f`, `45317d3`; `webskillcomposition`: `9647295`.

## Addendum 20.10.2026: robot variant (tags on the robot itself)

Implementation: agent S5 (Sonnet). Commits: `webskillcomposition` `e82bb61` (frontend, branch
`tag-einmessen`), this repo `15e7bb0` (docs addendum). State: frontend 560 tests green (39 new),
`tsc` and `vite build` clean. Not run on the hardware.

**Actual exports.** `model/robotTagRun.ts`: `ROBOT_TAG_STANDOFF_M`, `ROBOT_TAG_TILTS`,
`ROBOT_TAG_MIN_VIEWS`, `ROBOT_TAG_MAX_SPREAD_MM` (3), `ROBOT_TAG_MAX_SPREAD_DEG` (1),
`ROBOT_MODULE_TO_BASE`, `robotTagViews`, `robotTagJob`, `RobotTagJob`, `RobotTagMeasured`,
`RobotTagRunOptions`, `RobotTagJobResult`, `combineRobotTagViews`, `measureRobotTags`. New in
`model/tagReferenceRun.ts`: `driveClear` (drive with clearance and detour via the start pose, used by the module run and the robot run; behaviour of the module run unchanged).

**Deviations from the plan (as of implementation):**

- `ROBOT_MODULE_TO_BASE = IDENTITY_POSE`: the robot module frame is the robot base (URDF root; the
  forward kinematics calculates in the same frame; `robotBaseFrom` takes over the module pose without offset).
  The job stays generic (`moduleToBase` = T_module_base, `robotTagJob(..., moduleToBase?)`).
- `measureRobotTags` additionally has `approach?(job, seed)`. If there is an approach angle, the robot first drives there, as in the module run; otherwise centring starts where it stands.
  The result carries `returnNote?`.
- If centring is ambiguous, the views are planned around the measured position with the catalog orientation.
- The clearance check `keepOutFromRobotBounds` (`cellClearance.ts` unchanged) does not generally block views above one's own cart. Flange, camera and joint points are checked only
  if they lie in the footprint below table top + 5 cm, resp. below floor + 10 cm. `legCollision`
  as in the module run.
- Drive error: an unreachable or clearance-rejected view is skipped (warning). A drive error without blockage aborts the tag.
- Panel: one start button measures module tags first, then robot tags ("N modules, M robots"). The robot run itself needs no layer-1 result, but the button still requires a layer-1 result
  with a placed robot (`layer2Gate`). The hint "Layer 1 neu scannen" ("re-scan layer 1") also names the robot base.
- Data format: `parseMeasurement` discards a missing or unknown `method` (then the tag is measured again). Invalid optional fields (`deltaNormalMm`, `spreadDeg`, `views`) are discarded individually.
- Display: `describeTagMeasurement(tag, { robot: true })`. Measured: "measured … via robot joints · Δ …
  · n views". Not measured: "not measured yet · measured with the robot's hand camera".

Requirement of the user dated 20.10.2026: the tags of the robot (catalog entry `kind: "robot"`)
are also surveyed with the hand camera, in the same window "Tags einmessen", under the same once-only rule (only tags without `measurement`; saving a changed tag in the editor discards it).

### Goal / principle

The module origin of the robot is the robot base (`moduleCatalog.ts`, `kind: "robot"`).
Instead of reference edges, the chain via the joint angles is used:

    T_base_tag,i = FK(Gelenke_i) · T_flansch_cam · T_cam_tag,i      (tagInBaseFrom in tagServo.ts)
    T_module_tag = T_module_base · mean_i(T_base_tag,i)            (T_module_base = identity,
                                                                      unless the code shows otherwise)

Full 6 DoF. Error sources: hand-eye calibration and joint angles (~1–2 mm expected, not measured).
Averaging several views from different joint configurations removes part of this.
No layer 1 needed: the start value `T_base_tag_guess = T_base_module · T_module_tag(catalog)` is
known directly. The Pi remains unchanged; only `MeasureTags` is used.

### Procedure per robot tag

1. Start value `tagInBase` from the catalog (see above).
2. `centreOnTag` (expectedTagInBase = start value, standoff 0.35 m) → precise `tagInBase`.
   Failure → tag stays pending, reason "für die eigene Hand-Kamera nicht sichtbar/erreichbar" (not visible/reachable for the own hand camera).
3. Views: `ROBOT_TAG_VIEWS` = 3 camera poses in the tag frame (`camInTag`). The camera looks along −tag z
   (same convention as `PlanTagReferenceView`: image x = tag x, image y = −tag y), distance 0.35 m,
   tilted by +12° about tag x, −12° about tag x, +12° about tag y (tilt through the tag centre).
   Per view: `flangeForView(tagInBase, camInTag, flangeToCamera)` → `planMove` (reachability + clearance) → drive →
   **read the actual joints and compute FK** (not the setpoint) → `MeasureTags`
   (tagIds [tagId], frames 3). Valid only with `frameCount ≥ 2`, `intrinsicsSource ≠ "placeholder"`.
   Skip an unreachable view.
4. At least **2 valid views**, otherwise the tag fails (pending).
5. Mean: position over all valid views, orientation over the non-ambiguous ones (`ambiguous: false`). If all are
   ambiguous, the orientation comes from the start value, with a warning.
6. Spread: `spreadMm` (RMS of the positions around the mean), `spreadDeg` (RMS of the angular distances).
   Warning at `spreadMm > 3` or `spreadDeg > 1` (hint: check the hand-eye calibration).
7. Result into the catalog: `position`/`rotationDeg` (catalog format via `rpyDegFromQuaternion`),
   `measurement` with `method: "robot-fk"`.

### Interfaces

#### TypeScript — `model/robotTagRun.ts` (new, pure, testable, without `tagMeasurementState` import)

```ts
export const ROBOT_TAG_STANDOFF_M = 0.35;
export const ROBOT_TAG_TILTS: ReadonlyArray<{ axis: "x" | "y"; deg: number }>;   // +12 about x, −12 about x, +12 about y
export const ROBOT_TAG_MIN_VIEWS = 2;
export const ROBOT_TAG_MAX_SPREAD_MM = 3;                                        // assumption, not measured
export const ROBOT_TAG_MAX_SPREAD_DEG = 1;                                       // assumption, not measured
export const ROBOT_MODULE_TO_BASE: Pose = IDENTITY_POSE;                         // T_module_base
export function robotTagViews(standoffM?: number): Pose[];                       // camInTag per view
export function robotTagJob(moduleId: string,
  tag: { tagId: number; sizeM: number; position: Vec3; rotationDeg: Vec3 },
  moduleToBase?: Pose): RobotTagJob;
export interface RobotTagJob { moduleId: string; tagId: number; tagSizeM: number;
  moduleTagGuess: { position: Vec3; rotationDeg: Vec3 }; guessTagInBase: Pose; moduleToBase: Pose }
export interface RobotTagMeasured { moduleTag: Pose; position: Vec3; rotationDeg: Vec3;
  deltaMm: [number, number]; deltaNormalMm: number; deltaRotDeg: number;
  spreadMm: number; spreadDeg: number; views: number; warnings: string[] }
export function combineRobotTagViews(job: RobotTagJob,
  views: Array<{ tagInBase: Pose; ambiguous: boolean }>): RobotTagMeasured | { error: string };
export interface RobotTagRunOptions { jobs: RobotTagJob[]; deps: RobotDriveDeps;
  flangeToCamera: Pose; measureTags(tagIds: number[]): Promise<MeasureOutcome>;
  approach?(job: RobotTagJob, seed: number[]): number[] | null;
  clearance?(angles: number[]): "Wagen" | "Boden" | null; stopped?(): boolean; standoffM?: number;
  onProgress?(job: RobotTagJob, detail: string): void;
  onMeasured?(job: RobotTagJob, measured: RobotTagMeasured): void | Promise<void> }
export type RobotTagJobResult =
  | { job; status: "measured"; measured: RobotTagMeasured; warnings: string[] }
  | { job; status: "failed" | "skipped"; reason: string; warnings: string[] };
export function measureRobotTags(options: RobotTagRunOptions):
  Promise<{ results: RobotTagJobResult[]; halted: boolean; returnNote?: string }>;
// Does not throw: errors are in the result per tag.
```

#### Data format — extension of `TagCameraMeasurement` in `config/moduleCatalog.ts`

```ts
method: "camera-edges" | "robot-fk"
dofs: "full" | "partial"            // robot-fk always "full"
deltaMm: [number, number]           // robot-fk: shift new − start value in x/y of the start value's tag frame
deltaNormalMm?: number              // robot-fk only: shift along tag z of the start value
deltaRotDeg: number                 // robot-fk: total rotation angle between start value and new
spreadMm: number
spreadDeg?: number                  // robot-fk only
views?: number                      // robot-fk only: number of valid views
lines: string[]                     // robot-fk: []
```

`parseMeasurement` in `model/catalogStore.ts` accepts both methods and the optional fields.
`applyTagMeasurement` takes the new optional fields and `method` (default `"camera-edges"`).
`describeTagMeasurement` shows for robot-fk e.g. "measured 2026-10-20 via robot joints · Δ 1.2 mm / 0.3° · 3 views".

#### Panel — `components/TagReferencePanel.tsx`

A dedicated block "Roboter-Tags (über Gelenkwinkel)" (robot tags via joint angles) with the pending tags of the cell robot
(`useCellRobot` → catalog entry). The same start button first measures the module tags, then the robot tags
(counts shown separately). Result saved as for the modules (`saveModuleCatalog(applyTagMeasurement(...))`, one conflict retry).
A hint "Layer 1 neu scannen" (re-scan layer 1) remains, because the robot base in layer 1 is calculated from these tags.

### Failure cases

- Tag not visible or not reachable for the own camera (e.g. at the side of the foot) → pending with reason; the tag stays at the catalog value.
  Views that violate the clearance (self-collision with the robot area) are skipped.
- Fewer than 2 valid views → pending.
- No hand-eye calibration → run does not start (as before).
- All views ambiguous → orientation from the start value, warning.

### Accuracy / limits

- Hand-eye calibration and joint angles feed directly into the result. Expected ~1–2 mm, **not measured**.
  A comparison on the Pi against a folding-rule reference is still outstanding.
- Only tags the own hand camera can see. Other tags remain pending.
- The Pi remains unchanged; only the existing `MeasureTags` method is used.

## After completion

- [x] Status set to `done`, actual interfaces entered.
- [x] Row in the index `doc/projektdoku/arbeitsplaene/README.md` set to `done`.
- [x] `vision-server-interface.md`: new section for `MeasureTagReference`,
      `PlanTagReferenceView` and `LastTagReference`.
- [x] `apriltag-referenz.md`: own section "Survey tag position with the camera",
      including the `placement` data format.
- [x] Addendum 20.10.2026 in `modell-flaechen-tag-platzierung.md` with a link to this plan.
- [ ] Verified on the Pi against a folding-rule reference, result recorded in `doc/projektdoku/`.
- [x] Robot variant implemented and interfaces added (commits `e82bb61`, `15e7bb0`).

## Addendum 21.10.2026: live view and test phase

The first attempt on the robot worked "so semi gut" (German: "so-so"). Therefore a live view of the edge search is added:
in the window "Tags einmessen" one sees which lines the hand Pi draws and why it discards an edge, with a debug image and raw images on the Pi.
For the test phase each run re-measures all measurable tags (`TAG_MEASURE_ALL_TESTPHASE = true`), so that the attempt can be repeated often.
The once-only rule (only pending tags, once per new or edited module) remains the target state for later and is kept in the code.
Details in [`tag-einmessen-liveview.md`](tag-einmessen-liveview.md).

## Addendum 26.10.2026: measurement view over 5 candidates, edge cross-check of the robot tags

Occasion: during the attempt on the robot, tag 3 failed at the measurement view ("Keine Gelenkstellung … 77 mm / 10,2° daneben"
— runtime message, meaning "No joint configuration … 77 mm / 10.2° off"). Robot tag 1 reported a spread of 1.1° across the views.
Both are implemented only in the frontend (webskillcomposition, branch `tag-einmessen-liveview`, commits `4b6ed6f`, `745ea5b`).
**The Pi interface does not change.** Only the already documented request fields `view.tiltDeg` and `saveDebug` are used.

### Reachability of the view

The measurement view (camera in the tag frame from the Pi) is no longer solved only once from the current pose. `planViewMove` tries three IK start values
(current pose, start pose, middle of the joint ranges) and rotates the camera about its viewing axis: module tags 0°/180°
(the Pi centres the image section, so a half turn shows the same), robot tags 0°/±90°/180°.
The first solution without touching the cart or floor is taken.

### 5 candidate views (module tags and cross-check)

1. `PlanTagReferenceView` is called for the tilts `10, -10, 20, -20, 30°` about tag x (`TAG_REFERENCE_VIEW_TILTS_DEG`).
   Which edges fit (`fits`/`uncovered`) is decided by the first tilt; the others are planned for the same edges.
2. Each reachable view is driven to. There a short `MeasureTagReference` runs with `saveDebug: false`.
   The live view runs from the moment of driving on.
3. The best view wins (`referenceScore`). Compared in this order: `dofs` (full > partial > none), then the number of edges
   with `found && !ambiguous`, then the smaller `residualMm`, then the sum of `supportPx`.
4. Back to the best view, then (at "Schritt für Schritt" — "step by step") the confirmation and the actual measurement with log.
   If this measurement yields nothing usable, the check measurement of the same view counts, with a warning.

Unreachable or unplannable candidates are skipped and named as warnings. If none is reachable, the tag fails with the reason of the first candidate.

### Edge cross-check of the robot tags

Robot tags that have reference edges in the catalog (`placement`) are measured again over the edges after the joint-angle run
(switch "Roboter-Tags mit Kanten gegenprüfen" — "cross-check robot tags with edges", default on).
The edge measurement does not depend on hand-eye calibration and kinematics and is therefore an independent check.
**Only the `robot-fk` result continues to be saved.** The comparison is shown only in the row of the tag; the catalog format remains unchanged.

- Start value of the edge measurement: the catalog value. It is searched where the joint angles found the tag (otherwise at the catalog value).
  The module frame of the robot is the base frame.
- Comparison (`compareRobotTagWithEdges`): edges minus joint angles, in the tag frame of the joint-angle result.
  Compared are the shift in the tag plane (x, y) and the rotation about tag z. The normal is not compared, because the edges do not measure it.
  With only one edge (`partial`), only the shift perpendicular to it counts.
- Limits: 2 mm in the plane, 1° rotation. Above this, the hint in amber reads
  "Hand-Auge-Kalibrierung oder Kantenmaße im Katalog prüfen" (check hand-eye calibration or edge dimensions in the catalog).

## Addendum 27.10.2026: fuse views (weighted estimator), tilt about x and y, edge distances

### Occasion

On the robot, for tag 3 (close to the pedestal) only 1 of 5 views was reachable ("bestenfalls 112 mm / 91,6° daneben" — "at best 112 mm / 91.6° off").
Until now only the best view was taken, not averaged. Now all reachable views are measured and fused into one result.

### Views

Five candidates:

| # | Tilt | Axis | Planned by |
| --- | --- | --- | --- |
| 1 | +10° | tag x | Pi (`view.tiltDeg`) |
| 2 | −10° | tag x | Pi (`view.tiltDeg`) |
| 3 | +10° | tag y | Frontend |
| 4 | −10° | tag y | Frontend |
| 5 | +20° | tag x | Pi (`view.tiltDeg`) |

The y tilts are calculated by the frontend. It fetches the Pi plan with tilt 0° once and then rotates the camera
about the tag-y axis through the target point (target point = camera position + `standoffM` along the viewing axis; function `tiltViewAboutTagY`).
The roll angles about the viewing axis are now 0°, 180°, 90° and −90° (previously only 0° and 180°).
The IK start values additionally come from the angles of already planned views.

### Procedure

- Each reachable view is driven to and measured in full (debug log according to the setting).
- At "Schritt für Schritt" (step by step) one is asked once before the first measurement.
- No return to the best view and no additional measurement any more.
- A failed measurement of one view does not abort the tag.
- A stop takes effect before the next view.

### Estimator

Implemented in `frontend/src/features/cell-modules/model/tagReferenceFusion.ts`, function `fuseReferenceViews`.

- Calculated in the tag frame of the catalog start value. Unknowns are (dx, dy, dθ).
- A full view yields 2 position rows, a partial view 1 row perpendicular to the found edge. Two partial views with different edges together yield a full.
- The rotation about tag z is a weighted mean.
- Weights per view (inverse variance): position 1/(0.1² + `residualMm`² + `spreadMm`²) in mm⁻², rotation 1/(0.05² + `spreadDeg`²) in °⁻².
  `residualMm` is the residual error of the edge fit, `spreadMm`/`spreadDeg` the spread across the images of this one measurement
  (both from the Pi result). The base values 0.1 mm and 0.05° prevent a view with a spread of 0.0 by chance from dominating everything.
- Outliers: from 3 views on, at most one is discarded, and only if it lies more than 3σ AND more than 1 mm resp. 0.5° away from the solution.
- Height and tilt of the tag remain those of the catalog start value (as before on the Pi).
- Spread in the result = weighted RMS deviation of the views from the solution. Warning from 1 mm resp. 0.5°.
- With only one usable view, its result is taken over unchanged.

Why symmetric tilts: view-angle-dependent errors (e.g. an edge that does not lie at the assumed height, or a tilt error of the tag pose) cancel to first order.

### Display

Result line (UI text, stays German):

`Δ … · Kante A 50,5 mm (Katalog 50,0) · Kante B … · k von 5 Ansichten vereint, Streuung x mm / y°`

Meaning: Δ … · edge A 50.5 mm (catalog 50.0) · edge B … · k of 5 views fused, spread x mm / y°.

- Edge distance = distance of the tag centre from the edge line in the tag plane, perpendicular to the edge. It is measured as with a ruler on the surface;
  a height difference does not count. Compared against the catalog start value (`tagEdgeDistance.ts`).
- In the edge cross-check of the robot tags it reads "(Gelenke …)" (joints …) instead of "(Katalog …)" (catalog …).
- In the catalog, `views` is additionally saved (number of fused views). Otherwise the format remains unchanged.

### Interfaces

No change to the Pi interface. Only the existing fields `view.tiltDeg`, `standoffM` and `saveDebug` are used.
The change is in frontend commit `509fbe4` (webskillcomposition, branch `tag-einmessen-liveview`).

### Open

- Not yet checked on the robot.

## Addendum 27.10.2026: select tags to drive to

- Occasion: the user wants to choose which tags are driven to, instead of always all of them.
- In the panel "Tags einmessen", every row (module and robot tags) has a checkbox. Above it stands "Auswahl: alle / keine" ("selection: all / none")
  (from 2 selectable rows on; applies to module and robot tags together). Rows without reference edges or without a layer-1 position have no checkbox (not measurable).
- New rows are automatically ticked. Without intervention all are driven to, as before. The selection applies to the session
  (only in the window, not saved) and is kept across "Liste neu lesen" ("re-read list") and further runs.
- Only the ticked tags are driven to. The edge cross-check of the robot tags also runs only for ticked ones.
  The start button counts the selection ("… einmessen (2 Module, 1 Roboter)" — "… survey (2 modules, 1 robot)").
  If nothing is ticked, it reads "Kein Tag ausgewählt." ("No tag selected.")
- Implementation: `tickedRows` in `frontend/src/features/cell-modules/model/tagMeasurementState.ts`, panel `TagReferencePanel.tsx`.
  Frontend commit `471a73f` (webskillcomposition, branch `tag-einmessen-liveview`). No interface change.

## Addendum 27.10.2026: test phase does not save automatically

- Occasion: in test mode ("Alle Tags neu einmessen (Testphase)" — "re-survey all tags (test phase)", setting `measureAll`) every run wrote its result into the catalog.
  The next run then started from the last measurement result instead of the user's input, and "(Katalog …)" in the result line showed the last result
  (example MOD-7 tag 3: input 50,0 / 50,0 mm, shown 51,6 / 49,0 mm from the run before).
- New: in test mode a run only stages the results (module and robot tags). The result line ends with
  "· Testphase: nicht in den Katalog übernommen" ("· test phase: not taken over into the catalog"), below it a button
  "In Katalog übernehmen" ("take over into catalog"). Only the click saves (as before, via `applyTagMeasurement`);
  afterwards "· in den Katalog übernommen" ("· taken over into the catalog") and the hint to re-scan layer 1.
  On an error the button remains and "Nicht gespeichert: …" ("Not saved: …") appears.
- Staged results expire at the next run and at "Liste neu lesen" ("re-read list").
- Outside test mode, saving is immediate, as before. The edge cross-check of the robot tags never saves anyway.
- Implementation: `frontend/src/features/cell-modules/components/TagReferencePanel.tsx` (state `pendingKeeps`), switch hint in `TagReferenceWindow.tsx`.
  Frontend commit `373f386` (webskillcomposition, branch `tag-einmessen-liveview`). No interface or catalog format change.
