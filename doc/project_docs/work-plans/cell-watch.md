# Cell monitoring with the ceiling camera

**Status:** done — 13.10.2026 (not tested on real hardware)
**Owner:** `Agent: Cell monitoring (layer 1)`; frontend: session `opcua-f1` (`webskillcomposition`, branch `Ungetestet`)
**Topic:** apriltag
**Branch:** apriltag

## Goal

The ceiling camera (Pi 1) continuously evaluates whether a module has been moved, added or removed. It reports only **confirmed** changes, so that not every outlier triggers an alarm. The frontend then shows an alarm with „Layer 1 jetzt ausführen“ (run layer 1 now) and with „Ignorieren“ (ignore).

## Already read

- `doc/project_docs/interfaces/vision-server-interface.md` (payload, `worldTags`, `cameraLocalization`, locks)
- `doc/project_docs/work-plans/layer2-run-hand-pi.md`, `hand-eye-over-opcua.md` (pattern of ports and fakes)

## Affected files

- `src/vision_server/cellwatch.py` (new): tracker, flow, JSON
- `src/vision_server/detection/apriltag.py`: `observe_cell`, an evaluation without side effects
- `src/vision_server/result_management.py`: `result_for_job`, the last 20 results
- `src/vision_server/address_space.py`: node `CellWatchStatus`
- `src/vision_server/runner.py`: methods, wiring, `aclose`
- `tests/test_cellwatch.py` (new)
- `doc/project_docs/interfaces/vision-server-interface.md`: section 16

## Interfaces

### OPC UA

Everything lies under `VisionMachine`, additionally linked under `VisionProgram`. The nodes exist **only on the ceiling camera**, i.e. with AprilTag and without `hand_eye_path`.

| Element | NodeId | Type |
| --- | --- | --- |
| StartCellWatch | `ns=<vision>;s=VisionMachine.StartCellWatch` | `(OptionsJson: String) -> (Error: Int32)` |
| StopCellWatch | `ns=<vision>;s=VisionMachine.StopCellWatch` | `() -> (Error: Int32)` |
| PauseCellWatch | `ns=<vision>;s=VisionMachine.PauseCellWatch` | `(ReasonText: String, MaxPauseS: Double) -> (Error: Int32)` (addendum 22.10.2026, see `vision-server-interface.md` section 16) |
| ResumeCellWatch | `ns=<vision>;s=VisionMachine.ResumeCellWatch` | `() -> (Error: Int32)` (addendum 22.10.2026) |
| CellWatchStatus | `ns=<vision>;s=VisionMachine.CellWatchStatus` | String (JSON), Read/Subscribe |

| Method | Code | When |
| --- | --- | --- |
| Start | `OK` | armed. A repeated call replaces the reference. |
| Start | `INVALID_ARGUMENT` | JSON or option invalid, `referenceJobId` unknown (not among the last 20 results) |
| Start | `INVALID_STATE` | reference job failed or not in the world CS; vision system not `Operational` |
| Stop | `OK` / `INVALID_STATE` | off / was not running |

After a restart the monitoring is at `off`. The frontend then arms it again.

### Data

**`OptionsJson`**, schema `wsc.vision.cellwatch.start/1`. All fields are optional.

```json
{ "referenceJobId": "job-000123" | null, "movedThresholdM": 0.05, "rotatedThresholdDeg": 10,
  "minWorldTags": 1, "storedPoseMarginPx": 2,
  "persistS": 5, "missingPersistS": 20, "intervalS": 2, "maxCameraErrorPx": 6 }
```

- `referenceJobId`: the JobId of the result that is shown in the viewport. The reference is, **per tag**, the pose of the detection to which this tag belongs. For combined detections (`attributes.tagIds`) every tag gets the same pose.
- `null` („Ignorieren“ (ignore)): the reference is the medians of the last `persistS` seconds, only tags from the majority of the images. If the observation is not yet sufficient, `referenceId` stays `null` until it is.

**`CellWatchStatus`**, schema `wsc.vision.cellwatch.status/1`:

```json
{ "schema": "wsc.vision.cellwatch.status/1",
  "state": "off|watching|changed|paused|failed", "message": "MOD-A verschoben",
  "referenceId": "cw-000002" | null, "referenceJobId": "job-000123" | null,
  "referenceSource": "job" | "observation" | null,
  "lastCheckAt": "2026-10-13T12:00:00+00:00" | null, "watchedTagIds": [7, 20, 21],
  "visibleTagIds": [7, 583, 584] | null,
  "pending": [ { "tagId": 3, "moduleId": "MOD-A", "kind": "moved|disappeared|appeared",
                 "since": "ISO", "distanceM": 0.06 | null, "rotationDeg": 4.0 | null, "confirmInS": 3.2 } ],
  "tagVisibility": [ { "tagId": 2, "seenRatio": 0.2, "framesChecked": 10,
                       "lastSeenAt": "ISO" | null, "unreliable": true } ],
  "options": { "movedThresholdM": 0.05, ... },
  "changes": [ { "tagId": 7, "moduleId": "MOD-A", "kind": "moved|appeared|disappeared",
                 "referencePosition": [x,y,z] | null, "currentPosition": [x,y,z] | null,
                 "distanceM": 0.12 | null, "rotationDeg": 4.0 | null, "since": "ISO" } ] }
```

- `referenceId` changes with every new reference.
- `changes` contains only confirmed changes. The positions are module poses (with CAD offset) in the world CS.
- `paused` applies during a job or a camera calibration, without a camera image, or when the image does not allow a statement. The reason is in `message`; afterwards it continues by itself.

### Rules (`CellWatchTracker`)

- **Valid image:** camera pose from the joint PnP (`joint`, at least two world tags) with RMS ≤ `maxCameraErrorPx`, **or** (`minWorldTags` 1, default) exactly one world tag that matches the stored camera pose (see "One world tag is enough"). Anything else is "no statement": it counts neither as seen nor as missing. The default is 6 px: on Pi 1 the joint PnP with a calibration of 0.43 px was stable at 3.3 px; the rest is about 1 cm inaccuracy of the tag map. On a pause due to too large an error, `message` names the world tag with the largest `jointReprojectionErrorPx`, i.e. the candidate for re-measuring.
- **moved:** The median of the position (or rotation about the vertical axis) of the last `persistS` seconds deviates by more than the threshold for longer than `persistS`. For ambiguous tags only the position counts, unless the module lies flat (vertical axis ≤ 30° to world Z): then rotation counts as well, because the two solutions differ only in tilt. Refined on 19.10.2026: a rotated module was never reported, because from above almost every flat tag is ambiguous.
- **appeared:** A tag outside the reference has been visible for at least `persistS`, namely in the majority of the images of the window.
- **disappeared:** A reference tag is missing for longer than `missingPersistS`, namely in so many valid images in a row that a random miss at its previous detection rate is below 0.001 % (at least 3, at most 60; the rate is taken cautiously as the lower 95 % bound). Refined on 15.10.2026: a tag at the detection limit (detected only in every fifth image) triggered a false alarm after a 20 s gap, although the scan found it.
- **Hysteresis:**
  - moved clears only when the tag has been below half the threshold for `persistS`.
  - disappeared clears only when the tag is back in the majority of the images.
  - appeared clears only when the tag has not been seen for `persistS`.
- **World tags:** they are not monitored. `locate_modules` does not return them at all.

### One world tag is enough (19.10.2026)

Seeing two world tags at the same time was often not given, because one is frequently occluded. The camera is firmly mounted. Therefore: from images with at least two world tags, the Pi estimates the pose (joint PnP) and stores it. With exactly one visible world tag it uses the stored pose and only checks the tag for consistency.

- **Why not recalculate from one tag:** from 2.5 m the pose from a single tag is inaccurate by centimetres (simulated: module error median 29–40 mm, 95 % below ~90–100 mm, outliers up to 1.5 m due to tilted solutions, 24–38 % of the images over 5 cm). With the stored pose: median 12 mm, 95 % below 33 mm, at most 46 mm, 0 % over 5 cm. The rest is the measurement error of the module tag itself. In the simulation the module tag was 19.8 cm; with 8 cm tags the error is larger. This is independent of the number of world tags, however.
- **Consistency check:** the corners of the one world tag are projected under the stored pose and compared with the observed ones. The comparison is made against the value the same tag had when the pose was stored (it contains the map error of ~1 cm). More than `storedPoseMarginPx` (2 px) above that means: camera moved? Measured: unmoved at most ~1 px, 5 mm displacement ~1.4 px, 10 mm ~3.4 px, 0.1° ~1.4 px.
- **Pauses** (`message` always says „Welt-Tag“ (world tag)):
  - no world tag in the image,
  - start, or after a discard, with only one world tag („Kamerapose noch nicht bekannt“ (camera pose not yet known)),
  - the world tag does not match the stored pose („Kamera bewegt?“ (camera moved?)).
  After 5 non-matching images in a row the pose is discarded, and two world tags are needed simultaneously again.
- **Survives restarts (22.10.2026):** the pose lies in `data/calibration/cellwatch_camera_pose.json` (schema `wsc.vision.cellwatch.camerapose/1`: pose, baseline per world tag, tag-map key, count). Saved from 5 poses on, afterwards at most every 10 min, atomically (temporary file, then replace). It is loaded on start. In addition, the reference scan delivers it: `cameraLocalization.poseInWorld`/`tagErrorsPx` with `solver: "joint"`; `StartCellWatch` takes it over if none is stored or the camera has moved (> 15 mm / 0.5°). A restored pose is also checked for consistency: if the camera was moved during the pause, it is dropped along with the file. Status: `cameraPose: {source: measured|file|scan|null, poseCount, savedAt}`. If writing fails, a warning comes once per interval, no crash.
- **Limits:** if the tag map changes, the pose is discarded. If a new joint result deviates by more than 15 mm or 0.5°, the history starts afresh instead of averaging. `minWorldTags: 2` switches back to the old behaviour.

### Resources

- The monitoring reads `SharedCamera.latest_frame`, i.e. the same images as the live stream and jobs, and does not capture an image of its own. An image that has already been evaluated is not evaluated again.
- It computes with its **own detector** in its own thread (`asyncio.to_thread`). `observe_cell` changes neither the anchor nor `world_tags` nor `_localization`.
- During a job it pauses, so that a layer 1 job always has full CPU.
- `intervalS` is 2 instead of 1, because detection at 2028×1520 on Pi 1 is expensive.

## Open questions

- How much CPU the monitoring costs on Pi 1 (a full-frame detection every 2 s) has not been measured. If it is too expensive: increase `intervalS` or run the detection on the reduced stream resolution.
- Not tested on real hardware. The thresholds are proposals.

## After completion

- [x] Status `done`, interfaces entered.
- [x] Index line in `README.md`.
- [x] `vision-server-interface.md` section 16.
- [x] Tests: `tests/test_cellwatch.py` (17).
