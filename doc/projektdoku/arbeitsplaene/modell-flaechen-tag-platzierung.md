# Place AprilTags on the dominant faces and edges of the module model

**Status:** done — 29.09.2026 (provisional). The function runs in the module catalog and is committed in `webskillcomposition` (`839aa58`). It can be developed further; see "Open questions".
**Owner:** Meric, `Agent: place tags on model faces`
**Topic:** frontend
**Branch:** `feature/modulkatalog` (repo `webskillcomposition`)

## Goal

Where an AprilTag is glued to a module (`ModuleTag.position` / `rotationDeg` in the
module frame), the module catalog dialog no longer reads it off the **outline box**,
but measures it on the **3D model itself**. The box is unusable for tables with a
robot and attachments.

From the loaded model (URDF, DAE, glTF, STL) the **dominant planar faces** are
computed, plus for each face its **long outer edges** and **corners**. The procedure is:

1. Select a face.
2. Click a corner or two edges.
3. Enter the distances measured on the real module.

The tag then lies flush on the face.

**Item profiles:** Coplanar sub-faces with gaps up to an adjustable width (default
10 mm) are **merged into one face**. This way a frame of aluminum profiles with slots
and joints is recognized as one face with continuous outer edges.

In addition, there are two options:
- **Edges outside the face:** The reference edges in the "two edges" mode may be
  **any model edges**, including ones outside the selected face, e.g. the table edge
  under a profile or the edge of a robot foot. They are projected onto the face, and
  the distance is measured within the face.
- **z-offset:** The tag can be offset by a **distance in front of the face**, e.g. for
  a bracket, a spacer or a plate that the model does not contain.

Previously there were only the face presets of the outline box (`TAG_FACE_PRESETS`)
and numbers entered by hand. The existing edge picking (`modelEdges.ts`) only set the
module origin and the X axis, not the tag.

## Already read

- `CLAUDE.md` (work plan obligation, rules for changes)
- `doc/projektdoku/arbeitsplaene/README.md` (no open `frontend` plan on this topic)
- `doc/projektdoku/apriltag-referenz.md`: tag frame convention (+X right, +Y top edge,
  +Z out of the tag), `tagToModule`
- `webskillcomposition/frontend/src/features/cell-modules/config/moduleCatalog.ts`:
  `ModuleTag`, face presets, module frame (+Z up)

## Affected files

All paths are in `webskillcomposition/frontend/src/features/cell-modules/`.

**New:**
- `model/modelPlanes.ts`: face, edge and corner search, pure math without three.js
- `model/tagPlacement.ts`: tag pose from corner + distances, or from two edges
- `model/planeWorker.ts`: Web Worker around `findPlanes`
- `components/ModelPlanes.tsx`: collecting meshes, worker call, overlay, picking, dimension lines
- Tests: `model/modelPlanes.test.ts`, `model/tagPlacement.test.ts`

**Changed:**
- `config/moduleCatalog.ts`: `ModuleTag.placement`, `TagPlacement`, `TagPlacementEdge`
- `model/tagGeometry.ts`: `quaternionFromBasis`
- `model/catalogStore.ts`: import and export `placement` (defective entries are discarded)
- `model/catalogValidation.ts`: no outline-box warning for placed tags
- `components/ModulePreview.tsx`: `PlaneTool`, overlay and dimension lines in `ModuleScene`
- `components/ModuleCatalogModal.tsx`: button „Auf Modell platzieren“ (Place on model),
  `TagPlacementPanel`, carrying `placement` along when the origin or X axis is set anew
- Tests: `model/catalogStore.test.ts`, `model/catalogValidation.test.ts`

**Additionally:** The submodule `frontend/public/urdf` (`misc/urdf.git`) is checked out.
It contains only robot URDFs; `fr3_description_with_wagon` served as test model.

## Interfaces

### Data: `ModuleTag.placement` in the module catalog (`wsc.cellModules.catalog/1`)

The field is optional and is stored with the catalog in `localStorage` and exported
as JSON. It is only a **protocol**: `position` and `rotationDeg` remain authoritative.
Pi, tag map and layer 2 are not affected.

- **Lengths** in meters, directions as unit vectors in the module frame.
- **Manual override:** If position or rotation is changed by hand, `placement` is removed.
- **Origin or X axis set anew:** `placement` is shifted or rotated along with it.

```json
{
  "tagId": 4, "sizeM": 0.1,
  "position": [-0.2, -0.15, 0.8], "rotationDeg": [0, 0, 0],
  "placement": {
    "mode": "corner",
    "normal": [0, 0, 1],
    "edgeA": { "point": [-0.3, -0.2, 0.8], "direction": [1, 0, 0], "inward": [0, 1, 0] },
    "distancesM": [0.1, 0.05],
    "inPlaneRotationDeg": null,
    "offsetM": 0.005
  }
}
```

| Field | Meaning |
| --- | --- |
| `mode` | `"corner"`: `distancesM` = [along `edgeA` from `edgeA.point`, perpendicular into the face]. `"edges"`: `distancesM` = [perpendicular distance to edge A, to edge B]. |
| `normal` | Outward normal of the face. Tag +Z points along this normal, or opposite to it with `flipped`. |
| `edgeA`, `edgeB` | Reference edges, **projected onto the face**: a point on the edge (for `corner`, the corner itself), the direction (for `corner`, away from the corner) and `inward` (within the face, perpendicular to the edge, toward the face). `edgeB` exists only for `edges`. For edges outside the face, `inward` points to the face centroid; negative distances are allowed. |
| `inPlaneRotationDeg` | `null` = top edge of the print is "up": on horizontal faces (\|n_z\| ≥ cos 45°) tag +Y points toward module +Y, otherwise toward module +Z. This corresponds exactly to the `TAG_FACE_PRESETS`. A value = rotation from `edgeA.direction` to tag +X about the tag Z axis, in degrees. |
| `flipped` | optional, `true` = tag faces against the normal; for models with faces wound inward |
| `offsetM` | optional, distance of the tag in front of the face in m, along the tag +Z axis (viewing direction). Missing = 0. The distances in `distancesM` refer to the foot point on the face. |

### TypeScript: Face search (`model/modelPlanes.ts`)

```ts
function findPlanes(meshes: MeshInput[], overrides?: Partial<PlaneOptions>): ModelPlane[];
// Largest faces first, at most maxPlanes. Does not throw; empty array if nothing fits.

interface MeshInput {
  positions: ArrayLike<number>;        // xyz per vertex
  index?: ArrayLike<number> | null;    // 3 per triangle; missing = triangle soup
  matrix?: ArrayLike<number> | null;   // column-major 4x4 (three.js Matrix4.elements) into the module frame
}

interface ModelPlane {
  id: number;
  normal: Vec3; offset: number;        // normal·p = offset
  area: number;                        // material area in m² (sum of the triangles)
  centroid: Vec3;
  u: Vec3; v: Vec3;                    // u along the longest edge, v = normal × u
  extent: [number, number];            // extent along u and v, m
  triangles: Float32Array;             // 9 numbers per triangle, module frame (for drawing)
  segments: PlaneSegment[];            // long outer edges, longest first
  corners: PlaneCorner[];
}
interface PlaneSegment { id: number; start: Vec3; end: Vec3; length: number; direction: Vec3; inward: Vec3 }
interface PlaneCorner  { id: number; point: Vec3; incoming: number; outgoing: number; angleDeg: number; virtual: boolean }
// The boundary runs so that the face lies on the left (seen from the normal).
// incoming = segment that runs into the corner; outgoing = the one that runs out.
// angleDeg < 0 = inner corner; virtual = rounded or chamfered corner:
// the point where the straight edges would meet.
```

`PlaneOptions` with default values (`DEFAULT_PLANE_OPTIONS`):

| Option | Default | Meaning |
| --- | --- | --- |
| `angleToleranceDeg` | 2 | normal deviation up to which a triangle belongs to the plane |
| `distanceToleranceM` | 0.003 | distance from the plane up to which a triangle belongs to the plane |
| `minAreaM2` | 0.0025 | smaller faces are discarded (25 cm²) |
| `minWidthM` | 0.03 | narrower faces are discarded, e.g. the facets of round parts |
| `maxPlanes` | 12 | number of faces offered |
| `simplifyToleranceM` | 0.001 | collinearity tolerance of the edge pieces |
| `minSegmentM` | 0.02 | shorter edges count as rounding or chamfer |
| `maxCornerGapM` | 0.06 | maximum distance of the edge ends from the corner; allows rounded corners up to about this radius |
| `minCornerAngleDeg` | 20 | minimum kink for a corner |
| `mergeGapM` | 0.010 | gap width up to which sub-faces are merged (slots, joints); adjustable in the panel |

### TypeScript: Tag pose (`model/tagPlacement.ts`)

```ts
function tagPoseFromPlacement(p: TagPlacement): { position: Vec3; rotationDeg: Vec3 };
//   rotationDeg: rpy in catalog format. Throws for mode "edges" if the edges are
//   less than 10° rotated against each other ("… (fast) parallel …" = "… (almost) parallel …").
function placementFromCorner(plane, corner, along: "incoming" | "outgoing", previous?): TagPlacement;
//   previous: distances, rotation, flipped and offsetM are taken over.
function placementFromEdges(plane, first: EdgeLine, second: EdgeLine, previous?): TagPlacement;
//   EdgeLine = { start: Vec3; end: Vec3 }: a long face edge or an arbitrary mesh edge.
function edgeInPlane(plane, line: EdgeLine): TagPlacementEdge;
//   Projects the edge onto the face. An own edge of the face keeps its `inward`,
//   others point to the face centroid. Throws if the edge is less than 10° away
//   from the face normal ("… (fast) senkrecht auf der Fläche …" = "… (almost) perpendicular to the face …").
function placementFootPoint(p): Vec3;                     // tag center in the face (without offsetM)
function placementPosition(p): Vec3;                      // foot point + offsetM · tag +Z
function shiftPlacement(p, offset: Vec3): TagPlacement;   // new module origin
function rotatePlacement(p, turn: Quat): TagPlacement;    // module frame rotated; freezes "top" on horizontal faces
function placementMatches(tag): boolean;                  // still matches position/rotationDeg to the protocol?
```

In `model/tagGeometry.ts` there is a new
`quaternionFromBasis(x, y, z): Quat`, the rotation from orthonormal columns.

### Worker (`model/planeWorker.ts`)

- **Request:** `PlaneRequest = { meshes: MeshInput[]; options?: Partial<PlaneOptions> }`
- **Response:** `PlaneResponse = { planes, triangleCount, ms } | { error }`
- The `triangles` buffers are transferred.
- The frontend starts one worker per request. The result is cached per placed model
  object and `mergeGapM` (`useModelPlanes` in `components/ModelPlanes.tsx`).

### Failure cases

- **No matching face:** the panel reports it; the position is entered by hand.
- **Worker fails:** the message appears in the panel; the dialog remains usable.
- **Two (almost) parallel edges** in the "two edges" mode: error message in the panel;
  the tag remains unchanged.
- **An edge is (almost) perpendicular to the face:** error message; only the first
  edge remains selected.
- **No model or load error:** the button „Auf Modell platzieren“ (Place on model) is disabled.
- **Skinned meshes** are skipped, because their vertices do not match the displayed pose.

## Approach (algorithm)

1. **Candidates:** For each triangle, the normal n, offset d = n·p and area are
   computed and counted in buckets by (quantized normal, quantized offset). Only
   triangles from buckets with significant area go into the next steps. Most triangles
   of a CAD model are tessellation of round parts, so the effort stays manageable.
2. **Plane clusters:** Buckets are processed in descending order of area. A bucket
   defines a plane; adjacent triangles within the angle and distance tolerance join it.
   A refit follows.
3. **Bodies:** Within a plane, triangles with shared (welded, 0.1 mm) vertices form a body.
4. **Merging (item profiles):** Bodies whose boundary edges are at most `mergeGapM`
   apart become **one face**.
   - Implemented via a hash grid of the boundary edges and the segment distance.
   - Table tops of table legs with a larger spacing remain separate.
5. **Outer contour:** A boundary edge is **not** counted as outline exactly where, within
   `mergeGapM`, another boundary edge **faces** it.
   - "Facing" means: the outer normals point ≥ 45° against each other, and the opposite
     edge, projected perpendicularly, lies in front of the edge. This covers slot lips,
     joints and T-joints (congruent edges).
   - The hidden region is computed **exactly as an interval**. It ends exactly at the
     vertex of the opposite edge, e.g. where a hole begins.
6. **Edges:** The outline pieces are distributed onto common straight lines. The line is
   continuously fitted to all pieces, weighted by their length (principal axis of the
   endpoints). Pieces with gaps ≤ `mergeGapM` are joined into one edge, so that the
   front edge of a profile frame, interrupted by slots, becomes one edge. Then the
   `minSegmentM` filter applies.
7. **Corners:** For pairs of long edges with a kink ≥ `minCornerAngleDeg`, the
   intersection of the lines is computed. It is a corner if it lies at most
   `maxCornerGapM` from the end of one edge and from the start of the other. Rounded
   corners thereby become the imagined intersection point (`virtual`), i.e. the point
   where the tape measure is placed.
8. **Filter:** What remains are faces with area ≥ `minAreaM2` and narrow extent ≥
   `minWidthM`, sorted by area; the first `maxPlanes`.

**Operation** (Module catalog → edit module → at the tag, „Auf Modell platzieren“ (Place on model)):

1. Click the colored faces in the image. Hidden faces become visible when the view is rotated.
2. Choose „Messen ab einer Ecke“ (Measure from a corner) or „ab zwei Kanten“ (from two edges).
   - For a corner, choose A/B to select along which edge is measured.
   - Small dots mark rounded corners.
   - In the two-edges mode, **every sharp edge of the model counts**, including ones
     outside the face. The mesh edges of the part under the cursor are shown in orange,
     the long edges of other faces in gray.
   - If the clicked mesh edge lies on a long face edge (≤ 1 mm), the whole long edge is taken.
   - Each long face edge has an invisible snap zone, a tube with about half the marker
     radius (at least 3 mm). The edge of the selected face can thereby be hit even where
     it is not a single mesh edge, and also just next to the silhouette. The tubes of the
     selected face are 25 % thicker and win if two edges coincide. Hidden tubes are
     cut off by the model in front.
   - A mesh edge outside this zone counts only if the cursor is at most about
     0.6 × marker radius away from it. Nothing is highlighted in the middle of a face.
3. Enter the distances to the **tag center** in cm. Optionally set a custom rotation,
   „Tag schaut in die Gegenrichtung“ (Tag faces the other direction), or the
   **distance to the face** in mm (z-offset) „Abstand zur Fläche“ (Distance to face).

**View:**
- The left mouse button rotates the view.
- The middle and right mouse buttons move the model.
- The mouse wheel zooms.
- Selected reference edges are drawn in strong red (`#ff1a1a`, 5 px); the edge under the
  cursor in bright red (3 px).
- All edge lines (face edges, mesh edges, selection) are screen-space lines **with depth
  test**, pulled slightly toward the camera via polygon offset (`SurfaceLines` in
  `components/ModelEdges.tsx`). Only visible edges are marked; what the model occludes
  stays occluded.
- In edge mode there are no points. Corner points appear only in corner mode.

In the panel, „Spalten überbrücken bis (mm)“ (Bridge gaps up to (mm)) can be set. A change
triggers a new face search after 400 ms. The dimension lines (yellow) show the
measurement in the image. „Abbrechen“ (Cancel) restores the tag.

## Measurements

Measured with `wagon.stl` from `fr3_description_with_wagon`: 357 632 triangles,
in meters, Node/vitest, one thread.

| State | `mergeGapM` 0 | 10 mm | 20 mm |
| --- | --- | --- | --- |
| without merging (first version) | 0.9 s | — | — |
| with merging (current) | 1.8 s | 2.4 s | 3.5 s |

In the browser the search runs in the Web Worker; the dialog remains usable. The visual
`link0.dae` of the wagon is 250 MB; there, even loading the model takes time.

## Open questions

- **State as of 29.09.2026:** According to the user, it works so far. Further development
  is possible, e.g. in the operation of edge selection and for faces as reference.
  A first attempt at faces as reference was withdrawn at the user's request.
- **Not yet systematically checked:** the comparison of the distances with a tape
  measure on the real module, and the accuracy on real item-profile models.
- **Table URDFs:** The submodule `misc/urdf.git` contains only robots; there are no
  table URDFs there yet.
- **Congruent coplanar bodies** (one face twice in the model, e.g. a sticker body) are
  not merged if their edges lie far apart.
- **Oblique V gap** whose edges point less than 45° against each other is not recognized
  as a gap. This does not matter for slots and joints.
- **Already red before:** `src/entities/robot/model/store.test.ts` („keeps offline robots
  selected …“) (keeps offline robots selected …) already fails on the state before this
  work, so it has nothing to do with it.
- **Topic doc:** `apriltag-referenz.md` still lacks its own section "Tag position on the
  model" (data format `placement`).

## Deviations from the plan

- **Merging geometric instead of 2D raster.** Planned were morphological closing on a
  ~1 mm grid and connected components. The raster was too slow on the wagon: 57 M cells,
  4.5 s. In addition, cell rounding made the gap width imprecise; a 20 mm gap was bridged
  with 2.5 mm cells. The hash grid of the boundary edges with exact interval computation
  (steps 4 and 5) keeps the gap width exact and scales with the number of boundary edges
  instead of with the area.
- **No more chaining with Douglas-Peucker:** the edges arise from collinear merging of the
  outline pieces, the corners pairwise via line intersections.
- **`placement` contains no field `offset`:** the edge point and the normal define the
  plane. Distances are stored in meters (`distancesM`) and shown in cm in the UI.
- **Addition at the user's request:** edges outside the face and z-offset (`offsetM`) were
  added. The field `edgeIds` in the UI selection was therefore replaced by
  `edges: EdgeLine[]`. `PlaneTool.onEdgePick` now takes an `EdgeLine` instead of a segment id.
- **No face list in the panel:** the face is selected only in the image, as the user specified.

## After completion

- [x] Status set to `done (provisional)`, code committed in `webskillcomposition` (`839aa58`).
- [x] Line in the index `doc/projektdoku/arbeitsplaene/README.md` updated.
- [ ] Take over the data format `placement` into `apriltag-referenz.md`, as its own section.
- Further development: add a new section to the existing plan, or create a follow-up plan
  and link it here.

## Addendum 19.10.2026: Rotate tag by 90° with a button

In the module catalog, buttons ↺ / ↻ appear in two places; they rotate an AprilTag by
±90° about its normal (looking at the tag: ↺ = counterclockwise, ↻ = clockwise):

1. **In the placement panel** (step 4), before the checkbox „Oberkante des Drucks nach oben“
   (Printed top edge up): calls `turnPlacement(placement, ±1)`.
2. **In the tag row** (next to „Auf Modell platzieren“ (Place on model)): rotates placed tags
   via `turnPlacement`; for tags without a placement, `turnRotationDegAboutTagZ` is applied
   to `rotationDeg`.

Rotation always starts from the current, **visible position**:
- If `inPlaneRotationDeg` is `null` („Oberkante oben“ (Top edge up)), the angle is computed from the
  axes of the placement (via `placementAxes`), and ±90° are added — afterwards it is a
  number in (−180°, 180°].
- The checkbox „Oberkante des Drucks nach oben“ (Printed top edge up) is cleared automatically.
- The tag does not jump; otherwise the placement remains unchanged.

**Data format unchanged:** The field `placement` stays as described; only `inPlaneRotationDeg`
changes. Pi, tag map and layer 2 are not affected; only `position` and `rotationDeg` still
go there. Details: [`tag-drehen-90-grad.md`](tag-drehen-90-grad.md).

## Addendum 19.10.2026: UI in English, rotation field removed

The operation above names the German labels. Since `4b01ab3`, the module catalog's UI is
in English. Assignment of the texts cited in this document:

| In the document (DE) | In the UI (EN) |
| --- | --- |
| Modulkatalog (Button, Dialog) | Module catalog |
| Auf Modell platzieren | Place on model |
| Messen ab einer Ecke / ab zwei Kanten | Measure from a corner / two edges |
| Tag schaut in die Gegenrichtung | Tag faces the other direction |
| Abstand zur Fläche (mm) | Distance to face (mm) |
| Spalten überbrücken bis (mm) | Bridge gaps up to (mm) |
| Oberkante des Drucks nach oben | Printed top edge up |
| Abbrechen | Cancel |

The field „Drehung ab der Kante“ (Rotation from the edge) no longer exists. `inPlaneRotationDeg`
remains in the data format, but is only set via ↺ / ↻ or the checkbox. Unchecking the
checkbox keeps the position. Details: [`tag-drehen-90-grad.md`](tag-drehen-90-grad.md).

## Addendum 20.10.2026: Survey distances with the camera

The reference edges selected with „Place on model“ will in future be surveyed by the hand
camera instead of with the tape measure. The catalog value remains the starting value and
determines the search window; the result then overwrites `position`/`rotationDeg`, while
`placement` remains as the record of the starting value. The procedure and interface are
described in [`tag-einmessen-bezugskanten.md`](tag-einmessen-bezugskanten.md). For this, the
frontend must send the real 3D edges (EdgeLine, not projected onto the face), because the
camera searches exactly these edges in the image.
