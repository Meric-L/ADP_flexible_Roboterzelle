# Rotate an AprilTag in the module catalog by 90° using a button

**Status:** done — 19.10.2026, addendum 19.10.2026 (`4b01ab3`)
**Owner:** Meric, `Agent: rotate tag by 90°`
**Topic:** frontend
**Branch:** `Ungetestet` (repo `webskillcomposition`), commit `862e49a`

## Goal

In the module catalog (webskillcomposition, `frontend/src/features/cell-modules/`) add two buttons ↺ / ↻ that rotate an AprilTag by ±90° about its own normal (tag +Z). The convention: ↺ = +90° (counter-clockwise when looking at the tag), ↻ = −90° (same convention as `spin()` in the tag tool `frontend/src/tag-tool/TagToolApp.tsx`).

The buttons appear in two places:
1. In the placement panel "Tag auf eine Fläche des Modells setzen" (set tag on a face of the model) (step 4, next to "Oberkante des Drucks nach oben" (printed top edge up))
2. In every tag row next to "Auf Modell platzieren" (place on model) (also for tags without a model, i.e. face preset or custom rotation)

**Previous problem:** the rotation was only possible via a checkbox + number field "Drehung ab der Kante (°)" (rotation from the edge (°)). Unticking the checkbox set 0° and made the tag jump. With the new buttons the current rotation is preserved and adjusted incrementally. (Number field removed since `4b01ab3`, see addendum.)

## Already read

- `CLAUDE.md` (work-plan obligation, rules for changes)
- `doc/projektdoku/arbeitsplaene/README.md` (no open `frontend` plan on this topic)
- `doc/projektdoku/arbeitsplaene/modell-flaechen-tag-platzierung.md` (data format `placement`, interface style)
- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md` (AprilTag context)
- `doc/projektdoku/apriltag-referenz.md` §1 (tag frame convention: +X right, +Y top edge, +Z out of the tag)

## Affected files

All paths are in `webskillcomposition/frontend/src/features/cell-modules/`.

**Changed:**
- `model/tagPlacement.ts`: new function `turnPlacement`
- `model/tagGeometry.ts`: new function `turnRotationDegAboutTagZ`
- `components/ModuleCatalogModal.tsx`: render buttons, handle clicks
- `model/tagPlacement.test.ts`: test for `turnPlacement`
- `model/tagGeometry.test.ts`: test for `turnRotationDegAboutTagZ`

**Not affected:**
- Data format `wsc.cellModules.catalog/1` — unchanged
- Downstream interfaces: only `position` and `rotationDeg` still go to the Pi (layer 2, `wsc.vision.layer2.run/1`, `poseInModule`); `placement` never leaves the browser.
- Pi, tag map, layer 2 — not affected.

## Interfaces

### TypeScript: rotation in `model/tagPlacement.ts`

```ts
export function turnPlacement(placement: TagPlacement, quarterTurns: 1 | -1): TagPlacement;
```

Rotates a placement by ±90° about the tag +Z axis (normal of the face):
- **Calculation:** the current angle is taken from `placementAxes` (the axes from the placement) as `edgeA.direction` projected into the face, then `atan2` is used about the tag Z axis.
- **Also works for** `inPlaneRotationDeg === null` ("top edge up"): the implicit angle is calculated from the axes and ±90° is added.
- **Normalisation:** the result is normalised to (−180°, 180°], then rounded to 1e-6°.
- **Return value:** `{ ...placement, inPlaneRotationDeg: <number> }` — the new placement differs only in the angle field.
- **Consequence:** `null` becomes a numeric value; the checkbox "Oberkante des Drucks nach oben" (printed top edge up) is then wrong and should be off; the tag does not jump.

**Example:**
```js
turnPlacement({ mode: "corner", normal: [0, 0, 1], inPlaneRotationDeg: null, ... }, 1)
// → { ..., inPlaneRotationDeg: 90 }

turnPlacement({ ..., inPlaneRotationDeg: 90 }, -1)
// → { ..., inPlaneRotationDeg: 0 }

turnPlacement({ ..., inPlaneRotationDeg: 180 }, 1)
// → { ..., inPlaneRotationDeg: -90 }  (normalised to (−180°, 180°])
```

### TypeScript: rotation in `model/tagGeometry.ts`

```ts
export function turnRotationDegAboutTagZ(rotationDeg: Vec3, quarterTurns: 1 | -1): Vec3;
```

Rotates an rpy rotation vector by ±90° about the tag +Z axis:
- **Calculation:** quaternion from `rotationDeg`, then multiplication from the right with the 90° rotation about the tag Z axis (`multiplyQuaternions`), result converted back to rpy.
- **Normalisation:** the result is rounded to 1e-6°.
- **Return value:** new `rotationDeg` vector [r, p, y].
- **Use:** for tags without a placement record (only rpy given, not `inPlaneRotationDeg`).

**Example:**
```js
turnRotationDegAboutTagZ([0, 0, 0], 1)
// → [0, 0, 90]

turnRotationDegAboutTagZ([0, 0, 0], -1)
// → [0, 0, -90]

turnRotationDegAboutTagZ([90, 0, 30], 1)
// → new rpy after a 90° rotation about tag +Z
```

### Data format `placement` (unchanged)

The format remains as described in `modell-flaechen-tag-platzierung.md`. After a rotation via button only `inPlaneRotationDeg` changes:

```json
{
  "mode": "corner",
  "normal": [0, 0, 1],
  "edgeA": { "point": [0, 0, 0], "direction": [1, 0, 0], "inward": [0, 1, 0] },
  "distancesM": [0.1, 0.05],
  "inPlaneRotationDeg": 90,
  "offsetM": 0.005
}
```

### UI: buttons and state transitions

**Component `TurnButtons` in `components/ModuleCatalogModal.tsx`:**
- Two buttons ↺ / ↻, style `button-ghost`.
- `title` and `aria-label`: "Um 90° gegen den Uhrzeigersinn drehen (Blick auf den Tag)" / "Um 90° im Uhrzeigersinn drehen (Blick auf den Tag)" (German UI strings; English since `4b01ab3`, see addendum).

**In the TagPlacementPanel (step 4):**
- Buttons before the checkbox "Oberkante des Drucks nach oben" (printed top edge up).
- Click: `onPlacement(turnPlacement(placement, ±1))`.

**In the tag row (TagEditor):**
- Buttons directly after "Auf Modell platzieren" (place on model).
- Disabled while a tag is being placed.
- **Placed tag** (`placementMatches`): `turnPlacement(placement, ±1)` on the placement record, then pose via `tagPoseFromPlacement`.
- **Otherwise** (no placement): `turnRotationDegAboutTagZ(rotationDeg, ±1)` on the rpy vector; `placement` is removed. If no face preset fits any more, the "Fläche" (face) selection jumps to "Eigene Drehung…" (custom rotation…) with r/p/y shown. (Labels in English since `4b01ab3`, see addendum.)

### Failure cases

- **Numerical imprecision:** all results are rounded to 1e-6° to avoid floating-point artefacts.

## Approach (implemented)

1. Both functions calculate a new angle from the current pose and add ±90°, normalised to (−180°, 180°].
2. `turnPlacement` reads the axes from the placement (via `placementAxes`), not from face presets.
3. Buttons in `ModuleCatalogModal.tsx` in the placement panel before the checkbox and in the tag row after "Auf Modell platzieren".
4. Placed tags: `turnPlacement` + `tagPoseFromPlacement`; otherwise `turnRotationDegAboutTagZ` on `rotationDeg`.
5. Tests: `turnPlacement` with four axis orientations (level, wall, flipped, pre-turned 30°), reverse rotation, four full turns, normalisation (180 + 90 → −90, −90 − 90 → 180).
6. Same for `turnRotationDegAboutTagZ`: flat tag [0,0,0] → [0,0,90], vertical about tag +Z (Y → X), four full turns.

## Open questions

- None. Decisions: buttons in both places; rotation always from the visible position.

## Deviations from the plan

No substantive deviations; the implementation follows the plan exactly.

**Tests:**
- `tagPlacement.test.ts`: four axis orientations (`level`, `wall`, `flipped`, `turned`), reverse rotation and four full turns, normalisation to (−180°, 180°].
- `tagGeometry.test.ts`: flat tag [0,0,0] ↔ ±90°, vertical [90,0,0] about tag +Z (Y becomes X or −X respectively), four full turns equivalent to the identity.

## After completion

- [x] Status set to `done` — 19.10.2026.
- [x] Add actual function signatures.
- [x] Clarify interfaces (axes, atan2, normalisation, multiplyQuaternions).
- [x] Document UI placement and behaviour.

## Addendum 19.10.2026: field "Drehung ab der Kante" removed, UI in English

Commit `4b01ab3` (webskillcomposition, branch `Ungetestet`):

1. **Number field removed.** The field "Drehung ab der Kante (°)" (rotation from the edge (°)) in the placement panel no longer exists. The in-face rotation is now set only with ↺ / ↻.
2. **Checkbox stays, position stays.** The checkbox is now labelled "Printed top edge up". Unticking sets `inPlaneRotationDeg` to `inPlaneRotationDegOf(placement)` instead of 0°; the tag no longer jumps.
3. **Module catalog in English.** Dialog, validation, import and sync messages, error messages from `tagPlacement.ts`, `TAG_FACE_PRESETS` and the default module names are in English. Data format `wsc.cellModules.catalog/1` and interfaces to the Pi are unchanged. Already saved catalogs (backend `/api/module-catalog`, localStorage) keep their German module names until they are saved again or reset.

New exported function in `model/tagPlacement.ts`:

```ts
export function inPlaneRotationDegOf(placement: TagPlacement): number;
```

Angle from `edgeA.direction` (projected into the face) to tag +X about tag +Z, in (−180°, 180°], rounded to 1e-6°. `turnPlacement` builds on this; the behaviour of `turnPlacement` is unchanged.

**Translations (DE → EN):**

| German | English |
| --- | --- |
| Modulkatalog | Module catalog |
| Auf Modell platzieren | Place on model |
| Wird platziert… | Placing… |
| Tag entfernen | Remove tag |
| Fläche | Face |
| Eigene Drehung… | Custom rotation… |
| Modellfläche | Model face |
| Oberkante des Drucks nach oben | Printed top edge up |
| Tag schaut in die Gegenrichtung | Tag faces the other direction |
| Abstand zur Fläche (mm) | Distance to face (mm) |
| Spalten überbrücken bis (mm) | Bridge gaps up to (mm) |
| Messen ab einer Ecke / zwei Kanten | Measure from a corner / two edges |
| Andere Fläche | Other face |
| Abbrechen / Fertig | Cancel / Done |
| Kantenlänge schwarz (cm) | Black edge length (cm) |
| Oben (liegend) / Vorne (−Y) / Hinten (+Y) / Rechts (+X) / Links (−X) | Top (lying) / Front (−Y) / Back (+Y) / Right (+X) / Left (−X) |
| Welt-Tag | World tag |
| Um 90° gegen den Uhrzeigersinn drehen (Blick auf den Tag) | Turn 90° counter-clockwise (looking at the tag) |
| Um 90° im Uhrzeigersinn drehen (Blick auf den Tag) | Turn 90° clockwise (looking at the tag) |
| Drehung ab der Kante (°) | removed, no equivalent |
