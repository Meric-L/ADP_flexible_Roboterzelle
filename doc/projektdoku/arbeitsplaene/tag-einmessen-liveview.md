# Tag survey: live view of the edge search and test phase "all tags"

**Status:** done — 21.10.2026 (tests green, not tested on real hardware)
**Owner:** Meric, `Agent: Live view tag survey`
**Topic:** apriltag
**Branch:** `tag-einmessen-liveview` (this repo and `webskillcomposition`), branched off `tag-einmessen`

## Goal

User requirement (21.10.2026, translated from the original German):

> "I need a live view to see how it draws the lines and what exactly it does —
> the first attempt worked semi-well. For now, all possible tags should always be
> re-scanned, so that we can try it more often. For later, it should be noted
> that this only has to be done once per new module."

Addendum from the same day: "Everything should be toggleable again, just as it
already is for scanning and the like."

Afterwards, the window "Tags einmessen" (tag survey) shows live in the camera
image which reference edges the hand Pi searches for, which lines it draws, and
why it discards an edge. After each measurement, debug images and raw images
are stored on the Pi, and in the test phase a run re-measures all measurable
tags, so that the trial can be repeated often. Before, the edge search was a
black box: only the final result was visible, and a run only checked the pending
tags. All layers and switches can be toggled in the window.

## Already read

- `doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md` (predecessor plan,
  style, context of the reference edges and the robot variant)
- `doc/projektdoku/vision-server-interface.md` §10 (camera live stream, overlay
  modes) and §19 (`MeasureTagReference` / `PlanTagReferenceView`)
- `doc/projektdoku/arbeitsplaene/stream-overlay-schlank.md` (overlay mode
  `apriltag`, style of the stream overlay)
- `doc/projektdoku/arbeitsplaene/livestream-tags-volle-aufloesung.md` (tags in the
  live stream at full resolution)
- `doc/projektdoku/arbeitsplaene/rohbild-speichern.md` (raw images under `data/`,
  gitignored)
- Specification: `/home/meric/.claude/jobs/41073b2a/tmp/liveview-spec.md` (A, B, C)

## Affected files

Pi (ADP_flexible_Roboterzelle):

- `src/tagloc/reference_edges.py` (debug data, `debug` parameter, `_debug_step`)
- `src/tagloc/reference_overlay.py` (new: drawing, `LAYER_KEYS`, `effective_layers`)
- `src/tagloc/modes.py` (overlay mode `tagreference`)
- `src/vision_server/stream_overlay.py` (preview state, evaluation at most 3 Hz, expiry)
- `src/vision_server/tagreference_preview.py` (new: `ReferencePreview`, `build_status`, constants)
- `src/vision_server/tagreference_log.py` (new: measurement log, cleanup)
- `src/vision_server/tagreference.py` (request with `overlay` and `saveDebug`, `debugDir`)
- `src/vision_server/detection/apriltag.py` (debug image of the measurement, raw-image sink)
- `src/vision_server/camera_stream.py` (status writing, `expire_reference_preview`)
- `src/vision_server/mjpeg_server.py` (`GET /tagreference.jpg`)
- `src/vision_server/runner.py` (`SetTagReferencePreview`, log call, `debugDir`)
- `src/vision_server/address_space.py` (node `TagReferencePreviewStatus`)
- `src/vision_server/config.py` (`tag_reference_debug_dir`)
- Tests under `tests/` (among others `tests/test_tagreference_liveview.py`)

Frontend (webskillcomposition, `frontend/src/features/cell-modules/`):

- `opcua-server/config/piServers.ts` (bindings, B1)
- `model/tagReferenceCall.ts` (calls and parsers, B2)
- `model/tagReferencePreview.ts` (new: preview control, renewal, B3/B4)
- `model/tagReferenceRun.ts` (phases, preview, confirmation, B3)
- `model/robotTagRun.ts` (phases with `view`, B3)
- `model/tagReferenceLiveView.ts`, `model/tagReferenceProgress.ts`, `model/tagReferenceSettings.ts` (new; live view, progress, switches, C2)
- `model/tagMeasurementState.ts` (`TAG_MEASURE_ALL_TESTPHASE`, `measurableTags`, `selectTagReferenceTags`, initial value, B5)
- `components/TagReferenceWindow.tsx` (window, B4)
- `components/TagReferencePanel.tsx` (list, controls, test-phase note, B4/B5)
- `components/TagReferenceLiveParts.tsx` (live values, edge table, debug image)
- Tests (vitest, B6)

This list reserves the files. Anyone who wants to touch them in parallel should
agree on it beforehand.

## Interfaces

State after implementation, checked against the code (21.10.2026). Deviations
from the original plan are listed under "Deviations from the plan".

### OPC UA (hand Pi)

| Element | NodeId / BrowsePath | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| Method `SetTagReferencePreview` | `ns=<vision>;s=VisionMachine.SetTagReferencePreview` | Method, `(RequestJson: String) -> (Error: Int32)` | Call | Preview for one tag on or off. Hand Pi only (`hand_eye_path`). Linked under VisionProgram. |
| Status `TagReferencePreviewStatus` | `ns=<vision>;s=VisionMachine.TagReferencePreviewStatus` | String (JSON) | Read / subscription | State of the preview, after each evaluation (at most 3 Hz). |
| `MeasureTagReference` | existing, see `vision-server-interface.md` §19 | — | Call | Sets the preview automatically to its own request. Signature unchanged; request extended by `saveDebug` and `overlay`, result by `debugDir`. |

**`SetTagReferencePreview`**

- Input `RequestJson`: `wsc.vision.tagreference.request/1`, same parser as for
  `MeasureTagReference`. Fields `frames` and `view` are ignored. One tag is
  specified; then the preview is on for that tag. `""` switches the preview off.
  Optional `overlay` (see below, layers).
- Return `Error: Int32`:
  - `OK`: preview switched on or off.
  - `INVALID_ARGUMENT`: invalid JSON, missing tag, `overlay` or a value in it not an object or not a bool.
  - `INVALID_STATE`: server not `Operational`, or the live stream has no overlay (no annotator).
  - Never `BUSY`.
- The preview expires if it has not been renewed for 120 s (`PREVIEW_TTL_S`). The
  check also runs outside the `tagreference` mode (`expire_reference_preview`).

**Node `TagReferencePreviewStatus`**

Written after each preview evaluation (at most 3 Hz, `PREVIEW_INTERVAL_S`). JSON content:

```json
{
  "schema": "wsc.vision.tagreference.preview/1",
  "active": true,
  "tagId": 7,
  "tagSeen": true,
  "updatedAt": "2026-10-21T10:00:00+00:00",
  "overlay": { "guess": true, "band": true, "candidates": true, "rejected": true,
               "chosen": true, "mask": true, "labels": true, "tagBox": true },
  "lines": [
    { "id": "A", "found": true, "offsetMm": 1.8, "angleDeg": 0.4,
      "ambiguous": false, "reason": null, "supportPx": 212, "contrast": 0.37 }
  ]
}
```

`tagId` is `null` and `lines` empty when no preview is active (`active: false`).
`reason` is `NO_EDGE`, `NOT_VISIBLE` or `null`. `overlay` holds the effective
layers (missing keys = true). `tagSeen` is `false` as long as the tag has not
been seen. `updatedAt` is ISO 8601 with offset.

### Python

```python
# Module: src/tagloc/reference_edges.py
def measure_reference_lines(..., debug: list[dict] | None = None) -> LineMeasurement:
    """If debug is a list, one dict is appended per edge (see below).
    Result and LineMeasurement are bit-identical with and without debug."""
```

The existing parameters stay unchanged (`...`). The debug dict per edge, pixels
of the **raw image**, distorted, full resolution:

```python
{
    "id": str,
    "reason": str | None,
    "guessPx": [[u, v], ...],        # visible part of the projected starting-value edge
    "profilesPx": [[u, v], ...],     # profile centres
    "normalsPx": [[nx, ny], ...],
    "bandPx": float,                 # half-width of the search band
    "edgePointsPx": [[u, v], ...],   # all candidate points (gradient maxima)
    "candidates": [
        {"inliersPx": [[u, v], ...], "score": float, "chosen": bool}
    ],
    "measuredPx": [[u, v], [u, v]] | None,      # chosen line, endpoints in the raw image
    "measuredPolyPx": [[u, v], ...],            # chosen line as polygon with border (only when found)
    "found": bool,
    "supportPx": float,              # supporting length of the chosen line
    "contrast": float,
    "ambiguous": bool,
    "offsetMm": float | None,
    "angleDeg": float | None,
}
```

In addition, once per call the entry `{"id": "_tag", "maskOutlinePx": [[u, v], ...]}`
(outline of tag and border). `draw_reference_debug` and `build_status` skip this entry.

```python
# Module: src/tagloc/reference_overlay.py (new)
LAYER_KEYS = ("guess", "band", "candidates", "rejected", "chosen", "mask", "labels", "tagBox")

def effective_layers(layers: Mapping | None) -> dict[str, bool]:
    """Missing keys = True, unknown ones are ignored."""

def draw_reference_debug(image: np.ndarray, debug: list[dict], *,
                         tag_corners_px=None, title: str = "",
                         layers: Mapping[str, bool] | None = None) -> np.ndarray:
    """BGR. Draws on a copy of the image and returns it.
    layers None = all layers on. The title always remains."""
```

Colours: starting-value edge yellow dashed, band edges thin yellow, candidate
points cyan, discarded RANSAC lines (inliers) orange, chosen line green (red
when `ambiguous`), tag mask outline grey, tag frame green. One label per edge
at the edge, ASCII, e.g. `A  +1.8 mm  0.4 deg` or `A  NO_EDGE` / `A  ambiguous`.
Title top left in a dark box, e.g. `Tag 7 - Vorschau` (preview) or
`Tag 7 - Messung 3 Bilder` (measurement, 3 images). Line widths for 640 px width,
scaled with the image width.

```python
# Module: src/vision_server/tagreference.py
# parse_request(text) -> TagReferenceRequest
#   Fields: ..., overlay: dict (layers, missing = True), save_debug: bool (default True).
#   ValueError on: no JSON, overlay not an object, layer not a bool, saveDebug not a bool.
# build_result(..., debug_dir: str | None = None) -> dict   # "debugDir" always present, null on rejection

# Module: src/vision_server/tagreference_preview.py
PREVIEW_SCHEMA = "wsc.vision.tagreference.preview/1"
PREVIEW_TTL_S = 120.0       # expired without renewal
PREVIEW_INTERVAL_S = 1 / 3  # at most 3 Hz
PREVIEW_HOLD_S = 1.0        # preview image between two evaluations
MEASURE_HOLD_S = 5.0        # debug image of a measurement
def build_status(*, active, tag_id, tag_seen, debug=(), layers=None) -> dict: ...

# Module: src/vision_server/tagreference_log.py
MAX_FOLDERS = 50
OVERLAY_NAME = "overlay.jpg"
def write_protocol(directory: Path, tag_id: int, request_json: str, result: dict,
                   frames: list, overlay) -> Path: ...   # raises OSError on write error
def prune(directory: Path, keep: int = MAX_FOLDERS, protect: Path | None = None) -> None: ...
def latest_overlay(directory: Path | None) -> Path | None: ...
```

Overlay mode: `tagloc/modes.py` `OVERLAY_MODES = ("off", "apriltag", "calibration", "undistorted",
"tagreference")` (`calibration` removed 17.11.2026), display "Tag einmessen" (tag survey). Stream node `CameraStreamMode`, source full frame as for `apriltag`.

### Measurement log and HTTP

- Each `MeasureTagReference` with `saveDebug: true` (default) creates
  `data/tagreference/<YYYYmmdd-HHMMSS>_tag<id>/` (config `tag_reference_debug_dir`,
  default `<repo>/data/tagreference`; `None` = write nothing). Name pattern
  `^\d{8}-\d{6}_tag\d+(_\d+)?$`; the suffix `_n` is added on a name collision.
- Content: `request.json`, `result.json` (with `debugDir`), `frame<k>.png` (raw
  images, lossless), `overlay.jpg` (debug of the last image).
- Cleanup: at most `MAX_FOLDERS` = 50 own folders (only folders matching the name
  pattern), by mtime; the newly created folder is never deleted. Foreign folders
  are left untouched.
- Write error → log warning, measurement continues, `debugDir: null`.
- `ResultJson` gets `"debugDir": "<repo-relative path>" | null` (`null` on rejection).
- HTTP `GET /tagreference.jpg` (on the MJPEG port, path as above): returns
  `overlay.jpg` from the **newest** measurement folder. 404 if there is none. CORS
  `*`, `Cache-Control: no-store`.

### Frontend (webskillcomposition)

```ts
// opcua-server/config/piServers.ts
interface TagReferenceBinding {
  previewNodeId: string;        // ...SetTagReferencePreview
  previewStatusNodeId: string;  // ...TagReferencePreviewStatus
}
// CAMERA_OVERLAY_MODES extended by "tagreference" (label "Tag einmessen")
function buildTagReferenceImageUrl(serverUrl: string, port: number): string;
// -> "http://<host>:<port>/tagreference.jpg"

// cell-modules/model/tagReferenceCall.ts
function setTagReferencePreview(
  controller, serverUrl: string, nodeId: string,
  requestJson: string,            // "" = off
  opts?: { sleep?, timeoutMs? }   // default timeout 10 s
): Promise<{ status: "done" } | { status: "failed"; reason: string }>;
function parseTagReferencePreviewStatus(raw: string): TagReferencePreviewStatus;
// TagReferenceResult.debugDir?: string | null

// cell-modules/model/tagReferenceRun.ts and robotTagRun.ts
type TagReferencePhase = "approach" | "centre" | "plan" | "drive_view" | "measure" | "return";
onPhase?(job, phase, view?: { index: number; count: number }): void;  // view only for robot
preview?(job, lines: TagReferenceLine[] | null): void | Promise<void>; // module run only
confirm?(job, phase: "measure"): Promise<boolean>;  // false = skip job
onResult?(result): void;

// cell-modules/model/tagReferencePreview.ts
export const PREVIEW_RENEW_MS = 60_000;   // preview is renewed before the Pi expires it (120 s)
```

Settings (`cell-modules/model/tagReferenceSettings.ts`): localStorage key
`wsc.tagReference.settings`, all accesses in try/catch, stored values with the
wrong type fall back to the default. `overlayRequest(layers)` returns `null` as
long as all layers are on (the request then stays as before), otherwise the
`overlay` object.

Test phase (`cell-modules/model/tagMeasurementState.ts`):

```ts
export const TAG_MEASURE_ALL_TESTPHASE = true;  // default value of the switch "Alle Tags neu einmessen" (re-survey all tags)
export function measurableTags(catalog): PendingTag[];   // all non-world tags
export function selectTagReferenceTags(catalog, options: { measureAll: boolean; ... }): ...;
```

### Network / operation

- No new ports. The overlay mode runs via the existing MJPEG server; the path
  `/tagreference.jpg` is on the same port.
- No new systemd units.
- Log folder `data/tagreference/` on the Pi, gitignored.

### Failure cases

- Timeout when setting the preview (10 s in the frontend): the run shows a note
  as a hint, the measurement continues.
- Preview not set or expired (120 s without renewal): overlay shows normal tag
  frames and title "keine Einmess-Vorschau" (no survey preview).
- Tag not in the image: title "Tag 7 nicht im Bild" (tag 7 not in image), normal tag frames.
- Server not `Operational` or no live-stream overlay: `INVALID_STATE`, never `BUSY`.
- Invalid request JSON, missing tag, invalid `overlay`: `INVALID_ARGUMENT`.
- `GET /tagreference.jpg` without a previous measurement: 404.
- Error writing the log: log warning, measurement continues, `debugDir: null`.
- Debug generation fails: log entry, measurement continues.
- Camera missing in overlay mode: title with a hint, stream stays empty, no crash.
- Aborting the run or closing the window during a run: preview is switched off with `""`.
- Robot run: does not call `preview` (no edges), instead shows its own hint.

## C. Addendum 21.10.2026: everything switchable

User requirement: "Everything should be toggleable again, just as it already is for scanning and the like."

### C1 Pi: overlay layers and log switchable

- The request of `SetTagReferencePreview` and `MeasureTagReference` optionally
  receives `"overlay": {"guess": bool, "band": bool, "candidates": bool, "rejected": bool, "chosen": bool,
  "mask": bool, "labels": bool, "tagBox": bool}`. Missing keys = `true`. Unknown keys
  are ignored. Not an object or not a bool as value → `INVALID_ARGUMENT`.
- `MeasureTagReference` optionally receives `"saveDebug": bool` (default `true`).
  With `false`: no log folder, `debugDir: null`, `/tagreference.jpg` keeps showing the last state.
- `TagReferencePreviewStatus` contains `"overlay": {...}` (effective layers).

### C2 Frontend: settings as toggles

In the window "Tags einmessen" a collapsible section "Einstellungen" (settings)
with toggles. Saved under the localStorage key `wsc.tagReference.settings`.

- **"Live-Ansicht"** (live view; overlay mode `tagreference` in the stream on/off; off = previous mode)
- **"Vorschau während des Laufs"** (preview during the run; preview calls on/off)
- Layers: **"Startwert-Kante"** (starting-value edge), **"Suchband"** (search band),
  **"Kandidatenpunkte"** (candidate points), **"Verworfene Linien"** (discarded lines),
  **"Gewählte Linie"** (chosen line), **"Tag-Maske"** (tag mask), **"Beschriftung"**
  (labels), **"Tag-Rahmen"** (tag frame) (→ `overlay` object; a change takes effect
  immediately, by re-setting the active preview with the new `overlay`)
- **"Schritt für Schritt"** (step by step; pause before each measurement)
- **"Alle Tags neu einmessen (Testphase)"** (re-survey all tags, test phase), default on
  (default value = `TAG_MEASURE_ALL_TESTPHASE`)
- **"Modul-Tags"** (module tags) and **"Roboter-Tags"** (robot tags) (which groups the run measures)
- **"Mess-Protokoll speichern"** (save measurement log) (`saveDebug`)
- **"Debug-Bild nach der Messung anzeigen"** (show debug image after the measurement),
  **"Kantenwerte live anzeigen"** (show edge values live)

Tests: default values, parsing of stored values, derivation of the `overlay`
object from the toggles, group selection modules/robots (`selectTagReferenceTags`).

## Deviations from the plan

Result of the implementation compared with the plan and the specification.

**Pi**

- The preview evaluation runs in the **same single-thread pool** as
  `stream-overlay-detect`, not in its own pool. The detector is not built for two
  threads, so both run one after the other.
- The debug dict per edge additionally contains `found`, `supportPx`, `contrast`
  and `measuredPolyPx` (chosen line with border). In the status: `overlay`
  (effective layers).
- The font is **ASCII** ("deg", "-" instead of "°" and "–"), because the OpenCV
  Hershey font cannot render umlauts or the degree sign.
- Title **top left in a dark box** instead of `draw_status_bar`.
- Log tag frame = projected outline of the tag corners (as in the stream).
- `INVALID_STATE` also when the live stream has no overlay (no annotator).
- `debugDir` is in every result, `null` on rejection.
- The debug image of a measurement is kept in the stream for 5 s (`MEASURE_HOLD_S`).
- `/tagreference.jpg` only serves from the newest measurement folder, otherwise 404.
- Cleanup only of own folders (name pattern), by mtime; the new folder is protected.
- The expiry of the preview (120 s) is also checked outside the `tagreference`
  mode (`expire_reference_preview`).

**Frontend**

- `preview?(job, lines | null)` instead of `requestJson`: the module run builds the
  request itself from the edges; `tagReferencePreview.ts` calls
  `setTagReferencePreview` with the finished JSON.
- The preview is set after `onPhase("plan")` and re-set with the remaining edges
  when `fits: false`.
- Robot run: before each of the three views `confirm` asks ("Tag überspringen"
  (skip tag) discards the job). No preview for robot tags (no edges).
- In addition: `onResult` and the return move also run after a skip.
- "Letzte Messung" (last measurement) is one block; older measurements are in the Pi log.
- The test phase is a toggle with default `TAG_MEASURE_ALL_TESTPHASE` (see C2).
- The preview timeout (10 s) is only a note, not an abort of the measurement.
- The preview is renewed every 60 s (`PREVIEW_RENEW_MS`, Pi expiry 120 s).
- Closing the window during a run stops the run like "Halt" (stop) and switches
  the preview off for good; afterwards no preview is switched on again.
- Tag selection as a pure function `selectTagReferenceTags` (testable without React).

## Review

- **Opus review** on 21.10.2026 against this plan and the specification:
  **approved with conditions**.
- All conditions have been resolved (commits `b004346` Pi, `f5d7856` frontend).
  The five main points:
  1. `/tagreference.jpg` only serves from the newest measurement.
  2. Cleanup only deletes own log folders (name pattern), by mtime; the new folder is protected.
  3. Tag frames in `tagreference` mode no longer freeze.
  4. Labels readable (box), lines with border.
  5. The debug path can never make the measurement fail (errors are logged).
- Frontend conditions: preview renewal every 60 s, closing stops run and preview,
  live values hidden when live view is off, own hint for robot views, tag
  selection as a pure function, overlay mode reset via `enterLiveView`.

## Implementation and tests

- **Pi:** 797 tests green (34 new compared with 763). Re-checked on 21.10.2026
  with `unittest` via the project venv: `Ran 797 tests … OK`.
- **Frontend:** 595 tests green (35 new compared with 560), `tsc` and `vite build`
  without errors (according to the implementation).
- **Pi commits** (branch `tag-einmessen-liveview`): `146724c` (plan), `95eb2a6`
  (implementation), `b004346` (review findings, latest state). A plan commit of the
  same name `3c7b6e0` is not on this branch.
- **Frontend commits** (branch `tag-einmessen-liveview`): `aec572a` (implementation),
  `f5d7856` (review findings).
- Not committed in this documentation step: the state of this plan lies in the worktree.

## Test phase and later once-only rule

**Test phase (now):** The switch "Alle Tags neu einmessen (Testphase)" (re-survey
all tags, test phase) in the window is **on** by default. The default value comes
from `TAG_MEASURE_ALL_TESTPHASE = true` in `tagMeasurementState.ts`. Each run then
re-measures all measurable tags (`measurableTags`). The panel shows "Alle Tags
einmessen (n) — Testphase" (survey all tags (n), test phase) and the note
"Testphase: alle Tags werden bei jedem Lauf neu eingemessen" (test phase: all tags
are re-surveyed on every run). Second measurements keep the original starting value
(`guess: tag.measurement?.guess ?? {position, rotationDeg}`), so that
`placementMatches` does not break.

**Later (target state, user requirement):** Each tag is surveyed only **once per
new or edited module**. For this, switch the switch off or set
`TAG_MEASURE_ALL_TESTPHASE = false`. Then `pendingTags` applies with the once-only
rule (`invalidateChangedMeasurements`). This rule stays in the code and is not
removed. The label then reads "Ausstehende Tags einmessen (n)" (survey pending tags (n)).

## Open questions

- **Computing time of the preview on the Pi:** test machine 52–62 ms per evaluation.
  Not measured on the hand Pi whether 3 Hz alongside the live stream is enough. If
  necessary, lower to 1–2 Hz (`PREVIEW_INTERVAL_S`).
- **Legibility on the real stream:** line widths and font are estimated for 640 px
  and have not yet been checked on the real stream.
- **Label overlap:** the label may cover the edge. Not yet checked.
- **DOM tests in the frontend:** none exist (vitest with `environment: node`). The
  components are only tested via the pure functions in `model/`.
- **Space needed by the log folder:** 50 folders with raw images (PNG) can add up
  to many MB. Whether this fits on the Pi's SD card has not been checked.
- **Starting value for a second measurement:** whether the original starting value
  is always the correct reference during a test-phase measurement when a module was
  moved by hand is open.

## After completion

- [x] Status set to `done`, actual interfaces entered.
- [x] Row in the index `doc/projektdoku/arbeitsplaene/README.md` set to `done`.
- [x] `vision-server-interface.md`: section "19.1 Live view and measurement log"
      (live view and measurement log) added, row `tagreference` in the overlay
      table (§10).
- [x] `apriltag-referenz.md`: section on the live view of the edge search and the
      test phase added (§14).
- [ ] Addendum in `tag-einmessen-bezugskanten.md` with a link to this plan (not part of this task).
- [ ] Checked on the Pi with the real stream, overlay line widths and computing load evaluated.
