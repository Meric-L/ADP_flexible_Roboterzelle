# AprilTag localization and OPC 40100 — presentation basis

One slide per `##` section. The lines under **Talking points** are speaker notes,
not slide content. **All numbers in this document are measured**, not
estimated; where something is unverified, this is stated explicitly.

Sources per statement: [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md),
[`apriltag-referenz.md`](apriltag-referenz.md),
[`apriltag-e2e-test.md`](apriltag-e2e-test.md),
[`vision-server-interface.md`](vision-server-interface.md).

---

## 1 — Goal

**Localize modules fitted with AprilTags in the cell.**

Two cameras, two Raspberry Pis, one shared coordinate frame.

> **Talking points:** This is the practical part of the work. The theoretical part
> compares localization methods; here the point is to build one of them so that the
> cell can actually use it.

---

## 2 — The guiding idea

> **An AprilTag carries a number, not a meaning.**

The camera always delivers only one thing: `T_cam_tag` — where the tag is relative to
the camera, in meters, with full orientation.

That tag 7 is module `MOD-A`, and that its origin lies 40 mm below the tag center, is
stated in **one file**, not in the code.

> **Talking points:** This is the decision from which everything else follows. The
> code knows only tags, poses and chaining. A new module means: one entry in the tag
> map, no code change.

---

## 3 — Three roles, one code path

| Role | Hardware | Pose |
|---|---|---|
| **World tag** | four tags at the edge of the cell | **fixed**, stored in the map |
| **Robot tag** | tags on the robot | **movable — is measured** |
| **Module tag** | tag on the module | **movable — is measured** |

Only the world tag is fixed. The robot is a module like any other — just the one that
is always used.

Reference and measurement tags run through the same code. The difference: is the
world pose stored in the map, or is it `null`?

---

## 4 — The chain

```
                  T_world_cam              T_cam_tag        T_tag_module
  World frame <───────────────────  Camera frame ──────────> Tag frame ──────────> Module frame
              from reference tags         measured             constant (CAD)
              in the same image
```

```
T_world_module = T_world_cam · T_cam_tag · T_tag_module
```

**Exactly one function in the code performs this chaining** (`localize.locate_modules`).

> **Talking points:** Unit and convention errors — mm vs. m, xyzw vs. wxyz,
> `T_base_cam` vs. `T_cam_base` — are the classic silent errors in such systems. A
> single place that does the chaining makes them testable.

---

## 5 — Layer 1 and layer 2 are the same functionality

| | Layer 1 — ceiling | Layer 2 — flange |
|---|---|---|
| Task | coarse module position | precise 6-DOF pose |
| Camera | fixed mount | moves with the robot |
| `T_world_cam` | from reference tags in the image | from the robot pose |
| Result `frameId` | `world` | `cam_flange` |

**One profile, one class, two configurations.** The differences are solely configuration
and the origin of **one** matrix.

---

## 6 — Architecture: cut by dependencies

```
tagloc/
  identity, modes            only stdlib — not even numpy
  geometry, calibration,
  tagmap, localize           numpy, no OpenCV
  boards, detector, pose,
  overlay, frames            OpenCV, but imported only inside the function
  cli/                       four tools
```

Hard rule, **verified by measurement in the test** (`tests/test_layering.py`, checks
`sys.modules` in a subprocess):

- The server builds the `configurationId` before anything is loaded
- The computation layer is independent of the installed OpenCV version
- `tagloc` imports from `vision_server` **nowhere**

---

## 7 — Bringing tags into a shared coordinate frame

Two tags in **one** image yield a camera-free relation:

```
T_a_b = T_cam_a⁻¹ · T_cam_b        the camera pose cancels out
```

These relations span a graph; a breadth-first search from the anchor chains them into a
map.

**`residuals()` returns the loop-closure errors per edge — the quality figure of the map.**

> **Talking points:** That is why you do not have to survey every tag individually. You
> photograph the cell so that neighboring tags overlap pairwise. If the ring does not
> close, the calibration or a tag size is wrong.

---

## 8 — Measured: the chain against known ground truth

Synthetically rendered scene, true poses exactly known:

| Quantity | measured | bound |
|---|---|---|
| Position | max **0.29 mm** | 2 mm |
| Angle | median **0.031°**, max 0.50° | 1° |
| Reprojection error | max **0.063 px** | 1 px |
| Loop-closure error of the map | **0.00–0.02 mm / 0.000°** | 1 mm / 0.05° |

> **Talking points:** The one angle outlier at 0.50° is not an ambiguity — the second
> IPPE solution there is 82 times worse. It is the pixel quantization of the rendered
> marker at a 34° oblique view. The bound is therefore justified at 1°, not at the 0.5°
> from the concept.

---

## 9 — OPC 40100: the two parts

| | **Part 1** (40100-1) | **Part 2** (40100-2) |
|---|---|---|
| Question | *How do I operate the system?* | *What is it made of, and how is it doing?* |
| Content | state machines, jobs, recipes, results | camera, lens, computing device, state |
| Target audience | cell, PLC, MES | service and maintenance |
| Version in use | 1.0.0 (2019) | 1.00.0 (17.05.2024) |

> **Common misunderstanding:** The „NodeSet2" in the file name is the **XML file
> format**, not the part number. Every companion spec delivers `*.NodeSet2.xml` — even a
> pure Part 1 file.

---

## 10 — Part 1: what is linked

The nodeset brings **all** methods along with the type definition. Whether they do
anything is decided by `link_method`.

**Linked:** `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`, `Reset`

**Deliberately not**, each with its documented reason:

| Method | Why not |
|---|---|
| `SimulationMode` | there is no simulated data source |
| `SelectModeAutomatic` | there is only one operating mode |
| Configuration- / RecipeManagement | presupposes a recipe data model that the cell does not have |

> **Talking points:** A linked method that does nothing would be worse than a clearly
> unlinked one — the latter honestly answers `BadNothingToDo`.

---

## 11 — Part 2: the asset view

```
ns=<vision>;s=VisionMachine.VisionAsset
├── Identification        manufacturer, model, serial number, software version
├── ComputingDevices/ComputingDevice
├── ImageSensors/ImageSensor
└── Lenses/Lens
```

Only what is really installed is created. **An empty model field means „nicht bekannt“
(unknown) and creates no entry** — from a maintenance point of view, a gap is more
honest than an invented model.

---

## 12 — Part 2: measured first, then built

Part 2 brings **DI 1.04.0** and **Machinery 1.03.0** with it — four nodesets instead of one.

| | RSS | Startup time |
|---|---|---|
| Part 1 only | 108.5 MB | 0.65 s |
| with Part 2 | 121.3 MB | +1.6 s |
| asset view instantiated | +2.9 MB | |

**About 16 MB and just under two seconds.** Can be switched off: without `assets`, none
of it is loaded.

> **Talking points:** The Pi also serves the camera, so the first question was not „how
> do I build this“, but „does this fit at all“. The measurement tool is in the repo and
> runs on the Pi just the same.

---

## 13 — What the measurement revealed as pitfalls

| Finding | Consequence |
|---|---|
| **DI 1.05.0 cannot be imported** — requires UA base 1.05.04, fails in asyncua with `BadParentNodeIdInvalid` | versions pinned exactly to the `RequiredModel` entries |
| **asyncua creates placeholders as real nodes** (`<VisionItem>`, modelling rule `MandatoryPlaceholder`) | deleting them afterwards cost **9 s for 26 nodes** → now only what is filled is built: ~50 instead of ~700 nodes |
| **The namespace index shifts** from 3 to 6 | clients must resolve it at runtime |

---

## 14 — Three bugs we found along the way

**1. Numeric NodeIds in the live stream.** The stream nodes got automatically assigned
IDs (`ns=3;i=1`). These shift as soon as someone creates a node before them — and the
backend subscribes to them hard-coded. Now explicit string IDs.

**2. NaN would have killed the job.** `nan > schranke` is `False` in Python: the quality
filter let degenerate poses through, and `payload` serializes with `allow_nan=False`.
The job would not have failed as `DETECTION_FAILED`, but as `INTERNAL`.

**3. The frontend offered a job that no longer exists.** `image-recognition` remained in
the list after the QR removal and ended in `Error=4`.

> **Talking points:** All three would have been hard to find in operation — they fail
> silently or with a misleading message.

---

## 15 — Live stream as a debugging tool

Switchable via a writable OPC UA node:

| Mode | What you see |
|---|---|
| `off` | raw image |
| `apriltag` | outline, **axis cross in the tag**, ID, module name, distance, reprojection error |
| `calibration` | detected board corners and image coverage |

Off by default and hidden behind the settings: it costs computing time on the Pi and
does not belong in the picture permanently.

> **Talking points:** The axis cross is the actual information — a twisted cross is
> noticed immediately, a wrong number in a table is not.

> **Note, as of 21.10.2026:** In mode `apriltag`, the live stream now shows only a green
> box and the tag ID. The axis cross (together with module name, distance and
> reprojection error) is still available via the CLI (`python -m tagloc.cli.detect --overlay`).

---

## 16 — State of testing

| | |
|---|---|
| Tests total | **204, green, no skips** |
| of which math with golden values | chaining, quaternions, averaging |
| end-to-end on rendered image | detector → pose → map → module pose |
| architecture rules | measured on `sys.modules` in a subprocess |

**Unverified:** everything that requires real hardware. The numbers are desktop numbers;
OpenCV 4.x runs on the Pi, for which runtime switches exist that nobody has executed
there yet.

---

## 17 — Open points

| Point | State |
|---|---|
| **Hand-eye calibration** for layer 2 | open — until then layer 2 delivers in the camera frame, `auto_execute` stays `False` |
| Acceptance on the Pi camera | instructions exist (`apriltag-e2e-test.md`, four stages with abort criteria) |
| Handover format layer 1 → layer 2 | proposal: 3D pose in the world frame, schema stays unchanged |
| Stream above 5 fps | would only work with a different transport — OPC UA caps at 10 fps |

> **Talking points:** The hand-eye calibration is the only open question that entails
> code. Everything else is decisions, not investigations.

---

## 18 — Where everything is

| Document | Content |
|---|---|
| `doc/apriltag-lokalisierung.md` | concept and implementation plan |
| `doc/apriltag-referenz.md` | function and CLI reference, file formats |
| `doc/apriltag-e2e-test.md` | test instructions up to the Pi camera |
| `doc/vision-server-interface.md` | OPC UA interface, Part 1 and Part 2 |
| `doc/altlasten.md` | what is still open, and why (legacy items) |
| `src/vision_server/nodesets/README.md` | why the nodeset versions are pinned |
