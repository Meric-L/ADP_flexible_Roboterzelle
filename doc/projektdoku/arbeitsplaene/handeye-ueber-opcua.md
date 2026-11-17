# Hand-eye calibration via OPC UA

**Status:** done — 13.10.2026 (not tested on real hardware)
**Owner:** `Agent: Hand-eye calibration via OPC UA`; frontend: session `opcua-f1` (`webskillcomposition`, branch `Ungetestet`, window „Hand-Kamera kalibrieren“ (calibrate hand camera))
**Topic:** apriltag
**Branch:** apriltag

## Goal

`T_flansch_cam` can so far only be determined via the CLI (`tagloc.cli.calibrate_handeye`). With this change it is done from the frontend at the push of a button:
1. The operator moves the robot into several poses above a lying tag.
2. For each pose the frontend reports the flange pose.
3. For this the Pi measures the tag, calculates `T_flansch_cam` at the end, saves the result and uses it immediately, without a restart.

Without this file no layer 2 run starts (`layer2-lauf-hand-pi.md`).

## Already read

- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md`: solver, file format `wsc.vision.handeye/1`
- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md`, `layer2-annaeherung.md`: pattern of ports/fakes, flange links
- `doc/projektdoku/vision-server-interface.md` section 12: camera calibration, locks

## Affected files

- `src/vision_server/handeye_session.py` (new)
- `src/vision_server/detection/apriltag.py`: new methods `measure_tag` and `apply_hand_eye`; `_calibration_for` extracted from `_locate`
- `src/vision_server/address_space.py`: node `HandEyeStatus`
- `src/vision_server/runner.py`: methods, wiring, locks, `aclose`
- `tests/test_handeye_session.py`, `tests/test_handeye_opcua.py` (new); `tests/test_apriltag_source.py` (`MeasureTagTest`)
- `doc/projektdoku/vision-server-interface.md`: section 15

## Interfaces

### OPC UA

All elements lie under `VisionMachine` in the namespace `http://launch-rm.de/vision`. They exist only on the hand Pi (`hand_eye_path` set). They are additionally linked under `VisionProgram`.

| Element | NodeId | Type | Direction |
| --- | --- | --- | --- |
| StartHandEyeCalibration | `ns=<vision>;s=VisionMachine.StartHandEyeCalibration` | `(OptionsJson: String) -> (Error: Int32)` | Call |
| CaptureHandEyeSample | `ns=<vision>;s=VisionMachine.CaptureHandEyeSample` | `(SampleJson: String) -> (Error: Int32)` | Call |
| FinishHandEyeCalibration | `ns=<vision>;s=VisionMachine.FinishHandEyeCalibration` | `() -> (Error: Int32)` | Call |
| AbortHandEyeCalibration | `ns=<vision>;s=VisionMachine.AbortHandEyeCalibration` | `() -> (Error: Int32)` | Call |
| HandEyeStatus | `ns=<vision>;s=VisionMachine.HandEyeStatus` | String (JSON) | Read/Subscribe |

**All methods only reply once they are finished.**
- `CaptureHandEyeSample` takes `samples_per_job` images (default 3) and averages the tag pose. This takes about as long as one job.
- `FinishHandEyeCalibration` calculates and saves. With a handful of poses this takes milliseconds.

| Method | Code | When |
| --- | --- | --- |
| all | `OK` | accepted or completed |
| Start | `BUSY` | session already running; job, camera calibration or layer 2 run active |
| Start, Capture | `INVALID_ARGUMENT` | JSON or schema wrong, `tagId` missing or negative |
| Capture | `INVALID_STATE` | no session in state `capturing` |
| Capture | `BUSY` | a capture is still measuring, or a job occupies the camera |
| Capture | `DETECTION_FAILED` | capture discarded: tag not seen, ambiguous or no camera image. The reason is in `samples[].detail` and `message`. The session continues. |
| Capture | `CANCELLED` | aborted during the measurement. |
| Finish | `INVALID_STATE` | no session; **or** too few or too similar poses. The reason is in `message`, the session stays `capturing`, and further captures are possible. |
| Finish | `INTERNAL` | calculated, but not saved (file error). The state becomes `failed`. |
| Abort | `INVALID_STATE` | no session active |

### Data

**`OptionsJson`**, schema `wsc.vision.handeye.start/1`:

```json
{ "schema": "wsc.vision.handeye.start/1", "tagId": 12,
  "tagInBase": { "position": [..], "orientation": [..] } }
```

`tagInBase` (`T_base_tag` from the catalog entry of the robot) is optional, `null` is allowed. It serves only as a cross-check.

**`SampleJson`**, schema `wsc.vision.handeye.sample/1`:

```json
{ "schema": "wsc.vision.handeye.sample/1", "flangeInBase": { "position": [..], "orientation": [..] } }
```

`flangeInBase` is the **reported** pose: FK from the real joint angles, on the flange link. The links are EVA/Demo1 `model_6`, UR5e `flange`, FR3 `eef_attach_point`, as in `layer2-annaeherung.md`. If one calibrates here on a different link than at layer 2, the offset is wrong.

**`HandEyeStatus`**, schema `wsc.vision.handeye.status/1`:

```json
{ "schema": "wsc.vision.handeye.status/1",
  "state": "capturing", "message": "5 Aufnahmen, Drehung um 2 Achsen -- berechenbar, mehr wird genauer",
  "tagId": 12, "tagInBase": {..} | null,
  "acceptedCount": 5, "rotationSpreadDeg": 41.3, "rotationAxes": 2,
  "samples": [ { "index": 0, "accepted": true, "detail": "Tag 12 gemessen",
                 "flangeInBase": {..}, "camToTag": {..} | null } ],
  "result": null }
```

- `state` is one of: `idle`, `capturing`, `solving`, `finished`, `failed`.
- `rotationSpreadDeg`: the largest rotation between two accepted flange poses.
- `rotationAxes` (0–3): about how many independent axes were clearly rotated, with at least 5° of rotation per axis. Below 2 the rotation is not determined, and `message` says so.
- `message` during `capturing`: the hint for the operator, i.e. how many captures are still missing (minimum 3, recommended 8) and whether a further axis has to be rotated.
- `result` after `finished`:

```json
{ "poseFlangeCam": {..}, "rmsPositionM": 0.0012, "rmsRotationDeg": 0.21, "sampleCount": 8,
  "maxPositionM": 0.0031, "maxRotationDeg": 0.52, "rejectedCount": 1, "rejectedSamples": [3],
  "knownTagCheckM": 0.004 | null, "knownTagCheckDeg": 0.6 | null,
  "path": ".../data/handeye/cam_flange.json", "backupPath": ".../cam_flange.json.20261013T101500Z.bak" | null }
```

- `rmsPositionM`/`rmsRotationDeg`: the **actual RMS** of the deviation of the reconstructed board or tag position from the mean, over the captures used. `maxPositionM`/`maxRotationDeg` is the **largest** deviation. Until 21.10.2026 the maximum was shown here under the name RMS (`tagloc.handeye.target_spread`). Both only measure the consistency of the position across the captures, not the accuracy of `T_flansch_cam`.
- **Outliers** (from 22.10.2026, `tagloc.handeye.solve_hand_eye_robust`): the residual of a capture is its distance to the mean **of the others** (leave-one-out). If the largest lies above median + 3 × MAD (at least 2 mm or 0.3°), exactly that capture is discarded and the solution is recomputed, until nothing stands out any more. At most a quarter of the captures are discarded, never leaving fewer than 4. If the recomputation fails (the remaining ones do not rotate enough), the last good solution remains. The residual of each capture is in the status under `samples[i]` after `FinishHandEyeCalibration`: `residualM`, `residualDeg`, `rejected` (previously `null`, also for captures not accepted). `result.rejectedCount` and `result.rejectedSamples` (indices in `samples[]`) summarize it, `sampleCount` counts only the ones used. The file `cam_flange.json` additionally contains `maxPositionM`/`maxRotationDeg`; old files without these fields can still be loaded.
- `knownTagCheckM`/`Deg`: only with `tagInBase`. This is the deviation of `mean(T_base_flansch · T_flansch_cam · T_cam_tag)` from `tagInBase`. A large value argues more against the catalog than against the calibration.

### Python

```python
# src/vision_server/handeye_session.py   (numpy, tagloc; no asyncua)
class HandEyeSession:
    active: bool                                   # capturing or solving
    async def start(options_json) -> VisionErrorCode
    async def capture(sample_json) -> VisionErrorCode
    async def finish() -> VisionErrorCode
    async def abort() -> VisionErrorCode
def rotation_diversity(poses_base_flange) -> tuple[float, int]   # (degrees, axes)
def save_with_backup(path, hand_eye) -> Path | None

# src/vision_server/detection/apriltag.py
async def AprilTagDetectionSource.measure_tag(tag_id) -> tuple[Pose | None, str]
def AprilTagDetectionSource.apply_hand_eye(hand_eye) -> None    # discards the anchor
```

`measure_tag` measures purely in the camera CS: without world CS and without CAD offset from the tag map. Only the tag size comes from the map. It discards ambiguous poses (`is_ambiguous`), because a wrongly tilted measurement spoils the whole calibration.

### Failure cases

| Case | Behaviour |
| --- | --- |
| Tag not seen, ambiguous, no camera image | Capture `accepted: false` with reason, `DETECTION_FAILED`, session continues |
| Fewer than 3 accepted poses, rotation about only one axis | `Finish` → `INVALID_STATE`, reason in `message`, session stays open |
| File not writable | `Finish` → `INTERNAL`, state `failed`, old calibration remains active |
| Abort during a capture | This capture replies `CANCELLED`, state `idle` |
| Server shuts down | Session is aborted, nothing saved |

## Open questions

- **Automatic approach** of the poses around the known tag comes later, in the frontend. `tagInBase` and `rotationAxes` are already there for it.
- Not tested on real hardware. How good the calibration gets depends on the FK of the frontend (joint angles → `model_6`/`flange`/`eef_attach_point`).

## Deviations from the plan

- In addition to the proposal of `opcua-f1`: `rotationAxes`, `acceptedCount`, `tagInBase`, `knownTagCheckDeg`, `path` and `backupPath` in the status or result.
- A failed calculation (too few or too similar poses) does **not** lead to `failed`: the session stays open so that no capture is lost. `failed` only occurs if saving fails.

## After completion

- [x] Status `done`, interfaces entered.
- [x] Index line in `README.md`.
- [x] `vision-server-interface.md` section 15.
- [x] Test run: 547 tests, of which 1 failure that has nothing to do with this change (`test_mjpeg_server…viewers_run_faster`, also fails without this change). New: `test_handeye_session.py` (6), `test_handeye_opcua.py` (1), `MeasureTagTest` (4).
