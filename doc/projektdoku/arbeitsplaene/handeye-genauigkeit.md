# Hand-eye calibration: eliminate the fixed offset when approaching

**Status:** in progress — 28.10.2026
**Owner:** Meric, `Agent: hand-eye accuracy`
**Topic:** apriltag
**Branch:** `apriltag` (Pi), `handeye-genauigkeit` in webskillcomposition (frontend)

## Goal

When approaching tags and targets, the FR3 systematically misses: a fixed offset that is the same on every approach (size not yet measured; target ≤ 1 mm). The approach computation `T_base_tag = F · X · C` (`tagServo.ts:100`) uses the flange pose `F` from the URDF FK, the hand-eye `X = T_flange_cam` from the hand Pi, and the tag measurement `C` from `MeasureTags`. A fixed offset comes from errors that shift all views equally. The current quality figure (scatter of the board positions) therefore does not reveal it.

Afterwards the calibration should make the offset visible and keep it small: `X` and the board scale are refined jointly against the pixel measurement, the tag path measures more accurately, and the calibration reports a true prediction error instead of just a consistency figure. This was not possible before, because the quality figure does not detect a fixed offset.

Scope (user, 28.10.2026): the Pi runs on `apriltag`. Board = 7×8 squares, i.e. **6×7 inner corners**, 23 mm. No new hardware, exposure stays, pure software optimization.

## Already read

Reviewed for this plan (CLAUDE.md, section „Bevor du anfängst“ (Before you start)):

- `CLAUDE.md` (repo root): rules, work plan obligation, interface section
- `.claude/skills/projektdoku/SKILL.md`: flow reading, plan, implementation, commit
- `doc/projektdoku/arbeitsplaene/_vorlage.md`: format of the plan
- `doc/projektdoku/arbeitsplaene/README.md`: duplicate-work check; index with plans on hand-eye and board
- `doc/projektdoku/arbeitsplaene/handeye-schachbrett-automatik.md`: style example, board frame; names 5×6 inner corners, the board has 6×7 (see open questions)
- `doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`: style example, `MeasureBoard`, `reprojectionErrorPx`
- Approved plan `/home/meric/.claude/plans/glittery-riding-hartmanis.md`: findings, implementation A/B, verification

Not yet reviewed (to be read before implementation): `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`, `doc/projektdoku/vision-server-interface.md` (section 15), `doc/projektdoku/apriltag-lokalisierung.md`, `doc/projektdoku/arbeitsplaene/altlasten.md`.

## Affected files

This list reserves the files. Coordinate any parallel changes to them beforehand.

Hand Pi (repo ADP_flexible_Roboterzelle, branch `apriltag`):

- `src/vision_server/handeye_session.py`: store corner points, intrinsics and joints per capture; `intrinsicsSource`; session record; `finish` check for rotation axes
- `src/tagloc/handeye.py`: new function `refine_hand_eye` (Levenberg-Marquardt in numpy, Huber, LOO)
- `src/tagloc/cli/calibrate_handeye.py`: `--session <dir>`, offline recalculation
- `src/tagloc/pose.py`: refine IPPE_SQUARE solutions with `cv2.solvePnPRefineLM` (tag path)
- `src/tagloc/detector.py`: subpixel corners for `cam_flange` on by default (choice by measurement in the test)
- `data/handeye/sessions/<UTC-Zeit>/`: session records (JSON + one image per capture)
- `tests/test_handeye_refine.py` (new), `tests/test_tag_pipeline.py`, `tests/test_handeye_session.py`

Frontend (repo webskillcomposition, branch `handeye-genauigkeit` from `Ungetestet`):

- `robotDrive.ts`: arrival after 3 quiet polls, re-read joints after the capture
- `HandEyeAutoPanel.tsx`: standstill logic, check „Brettursprung anfahren“ (Approach board origin)
- `handEye.ts`: parser for the new fields
- `HandEyeCalibrationWindow.tsx`: display of the quality figures and warning notes

Docs (only in implementation step C, not in this plan commit):

- `doc/projektdoku/arbeitsplaene/handeye-schachbrett-automatik.md`: correct the board (6×7 inner corners)
- `doc/projektdoku/vision-server-interface.md`: section 15, new status and result fields

## Interfaces

State after implementation: Pi `c494f71` (branch `apriltag`), frontend `6e19c66` and `f5c81ba` (webskillcomposition, branch `handeye-genauigkeit`). The earlier draft is superseded; everything here was checked against the code.

### OPC UA

No new nodes or methods. Only the behavior of `FinishHandEyeCalibration` and the content of `HandEyeStatus` changed. `StartHandEyeCalibration`, `CaptureHandEyeSample`, `AbortHandEyeCalibration` and the locks remain as in `handeye-ueber-opcua.md` and section 15 of `vision-server-interface.md`.

- `FinishHandEyeCalibration() -> (Error: Int32)`:
  - `INVALID_STATE` if at least 3 captures are accepted but their flange poses differ by fewer than 3 rotation axes (`REQUIRED_AXES = 3`; an axis counts from 5°, `rotation_diversity`). The session stays open in state `capturing`, the status gives the reason, and the record is written with `outcome: "refused"`.
  - `INVALID_STATE` also with fewer than 3 usable poses (the solver raises `ValueError`). Then the session stays open and the record has `outcome: "failed"`.
  - `INTERNAL` if the calibration was computed but could not be saved. The session then goes to `failed`.
  - `INVALID_STATE` also if the session is not in state `capturing` or a measurement is currently running.
  - On success: `OK`, state `finished`, record with `outcome: "finished"`.

### Python

**`src/tagloc/handeye.py`** (refinement against the board corners, board only)

```python
refine_hand_eye(
    poses_base_flange: Sequence[Pose],      # T_base_flange per capture
    corners_px: Sequence[np.ndarray],       # per capture (N, 2), undistorted (P = K)
    object_points: np.ndarray,              # (N, 3), board frame, nominal size
    camera_matrix: np.ndarray,              # (3, 3)
    *,
    initial_flange_cam: Pose,               # start X = T_flange_cam (Park-Martin)
    initial_base_board: Pose,               # start T_base_board (estimate_base_board)
    initial_scale: float = 1.0,
    estimate_scale: bool = True,            # also estimate the board scale
    huber_delta_px: float = 1.0,
    max_iterations: int = 60,
) -> HandEyeRefinement
```

- `HandEyeRefinement` (frozen dataclass): `pose_flange_cam` (X), `pose_base_board` (board origin = first inner corner, +z into the board), `board_scale` (1.0 = square size as given; with `estimate_scale=False` the start value), `reprojection_rms_px` (RMS of the 2D distance over all corners), `iterations`, `converged`, `sample_rms_px` (RMS per capture in px).
- Levenberg-Marquardt with a numerical Jacobian in numpy, Huber weighting (IRLS). Unknowns are X (6), T_base_board (6) and optionally s (1).
- `ValueError`: fewer than 3 captures; pose and corner sets of different length; number of corners does not match `object_points`.

```python
refine_hand_eye_robust(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    poses_cam_board: Sequence[Pose],        # T_cam_board per capture
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    *,
    estimate_scale: bool = True,
    reject: bool = True,
    huber_delta_px: float = 1.0,
) -> tuple[HandEyeRefinement, tuple[int, ...], HandEyeSolution]
```

- Returns `(refinement of the remaining captures, indices of the rejected input captures in the order of rejection, Park-Martin start as HandEyeSolution)`.
- Outlier rule: after each refinement, the RMS pixel error per capture is checked. Threshold: `max(1.0 px, median + 3 · 1.4826 · MAD)`. If the worst capture lies above it, it is removed and refinement is repeated with a warm start.
- Limits: at most 25 % of the captures (`int(n · 0.25)`); at least 4 always remain; the remaining poses must still differ by at least 3 axes. When a limit is reached, the loop ends without further rejection.
- `ValueError` as for `solve_hand_eye` (see below) and as for `refine_hand_eye`.

```python
leave_one_out_errors(
    poses_base_flange, corners_px, poses_cam_board, object_points, camera_matrix,
    full: HandEyeRefinement,                # overall solution as warm start
    *,
    estimate_scale: bool = True,
    huber_delta_px: float = 1.0,
) -> LeaveOneOutResult
```

- For each capture `i`: refine without `i`, predict the corners of `i`.
- `LeaveOneOutResult`: `error_px` (per capture, RMS in px), `error_m` (per capture, mean 3D distance in the camera frame in m; board points scaled with `s` so that the scale error does not count as prediction error), `rms_px`, `rms_m` (RMS over all captures).
- `ValueError`: fewer than 4 captures.

Helper functions in `tagloc.handeye`: `rotation_diversity(poses) -> (largest rotation in degrees, number of axes)`, `estimate_base_board(...)`, `reconstruction_spread(...)`. Constants: `MIN_AXES = 3`, `AXIS_THRESHOLD_DEG = 5.0`, `MIN_SAMPLES = 3`, `HUBER_DELTA_PX = 1.0`.

`solve_hand_eye` and `solve_hand_eye_robust` (Park-Martin) remain unchanged. `solve_hand_eye_robust` raises `ValueError` for too few or too similar poses.

**`src/vision_server/handeye_session.py`**

- `HandEyeSession.finish() -> VisionErrorCode`: flow and codes see the OPC UA section above. With a board, `_refine_board` is used (`refine_hand_eye_robust`, leave-one-out over the remaining captures if more than 3 remain). Without a board, or if the refinement is not possible, it stays with Park-Martin.
- Session ports `HandEyePorts` (excerpt): `measure_board: Callable[[BoardSpec], Awaitable[tuple]] | None`, `intrinsics_source: Callable[[], str | None] | None`, `sessions_dir: Path | None` (default: `sessions/` next to `hand_eye_path`).

**`src/vision_server/detection/apriltag.py`, `measure_board(spec) -> (Pose | None, reason, info)`**

- Averages over `samples_per_job` fresh images; for `cam_flange` that is 5 (`server.py`, preset).
- `Info` always contains `reprojectionErrorPx`. On success additionally:
  - `corners`: corners `(N, 2)` in the distorted image, mean over the images used,
  - `calibration`: the `CameraCalibration` used,
  - `intrinsicsSource`: `"calibration"`, `"realsense_factory"`, `"placeholder"` or `null`,
  - `image`: the last image used (for `sample_NNN.png`).
- Without `corners`/`calibration` there is no refinement, only Park-Martin.

**`src/tagloc/cli/calibrate_handeye.py`**

```
python -m tagloc.cli.calibrate_handeye --session <folder|session.json> [--out <file>] [--no-scale]
python -m tagloc.cli.calibrate_handeye --samples <list.json> --calibration <cam.json> --out <file>   # old way, without refinement
```

- `--session`: reads `session.json` (schema `wsc.vision.handeye.session/1`), computes Park-Martin, robust, then the refinement including leave-one-out again. Without `--out` only output; with `--out` the calibration file is written.
- `--no-scale`: do not estimate the board scale.
- If corners or intrinsics are missing in the record (e.g. a tag session), the CLI computes only Park-Martin.
- The output contains a warning if `|boardScale − 1| > 0.3 %`.

**`src/tagloc/pose.py`, `_solve_tag_pose`**

- Both IPPE_SQUARE solutions are refined with `cv2.solvePnPRefineLM`. The one with the smaller reprojection wins.
- `ambiguity` (ratio to the second solution) is still computed from the **raw** IPPE errors, so that `AMBIGUITY_THRESHOLD` (0.6) does not falsely trigger after the refinement.

**Environment variable `VISION_SUBPIXEL_CORNER_REFINEMENT`** (`src/vision_server/server.py`, `subpixel_corner_settings`)

| Value | Effect |
| --- | --- |
| `0`, `none`, `false` | subpixel corners off |
| `1`, `true` | on, with the method of the preset |
| `apriltag` | on, method `apriltag` |
| `subpix` | on, method `subpix` |
| empty | preset: `cam_flange` on with `subpix`, other presets off |
| anything else | `ValueError` at start |

### Data / payload

**`HandEyeStatus`**, schema `wsc.vision.handeye.status/1`. New fields are marked (new), otherwise unchanged.

- Top level:
  - `intrinsicsSource` (new): `"calibration"`, `"realsense_factory"`, `"placeholder"` or `null` (unknown).
  - `samples[i]` (new): `looErrorPx`, `looErrorM`. Only after `Finish` and only for non-rejected captures, otherwise `null`.
- `result` (only after a successful `Finish`, otherwise `null`):
  - existing: `poseFlangeCam`, `rmsPositionM`, `rmsRotationDeg`, `maxPositionM`, `maxRotationDeg`, `sampleCount`, `rejectedCount`, `rejectedSamples`, `knownTagCheckM`, `knownTagCheckDeg`, `path`, `backupPath`.
  - new `refinedReprojectionRmsPx`: RMS of the joint refinement in px. `null` without a board or without refinement.
  - new `boardScale`: board scale against the nominal size (1.0 = correct). `null` without refinement.
  - new `looErrorRmsPx`, `looErrorRmsM`: RMS of the leave-one-out prediction error, in px and m (camera frame). `null` if fewer than 4 captures remain.
  - new `refinementConverged`: `true` / `false` / `null`.
  - new `targetInBase`: `{position: [x, y, z], orientation: [x, y, z, w]}` in m, base frame. With a board, the board origin (first inner corner, +z into the board) from the refinement, otherwise the mean of `F · X · C`. In tag mode the tag. So **not** `null` without refinement.
  - new `warnings`: list of German texts (Pi wording with ASCII replacements such as „--“). Possible are:
    - Intrinsics `realsense_factory`: "Intrinsik: Werkswerte der Kamera, keine eigene Kalibrierung -- Massstab und Verzeichnung koennen abweichen; Kamera neu kalibrieren." (Intrinsics: camera factory values, no own calibration -- scale and distortion may deviate; recalibrate the camera.)
    - Intrinsics `placeholder`: "Intrinsik: Platzhalter, die Posen sind nicht massshaltig." (Intrinsics: placeholder, the poses are not dimensionally accurate.)
    - No refinement possible: "Keine gemeinsame Verfeinerung gegen die Bildpunkte moeglich -- Ergebnis nur aus Park-Martin." (No joint refinement against the image points possible -- result from Park-Martin only.)
    - `|boardScale − 1| > 0.3 %`: "Brettmassstab weicht X,X % vom Nennmass ab -- Brett nachmessen / Intrinsik pruefen." (Board scale deviates X,X % from the nominal size -- re-measure the board / check the intrinsics.)
    - The list is empty if there is nothing to report.
  - new `sessionPath`: folder of the record, or `null` if it could not be written.
- `current` (new, in addition to the existing fields): `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`, `intrinsicsSource`. Each from `cam_flange.json`, `null` if not saved.

**`data/handeye/cam_flange.json`** (schema `wsc.vision.handeye/1`): existing keys unchanged. Additionally, only if present: `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`, `intrinsicsSource`. Before overwriting, the old file is backed up as `cam_flange.json.<UTC>.bak`.

**Session record** `data/handeye/sessions/<UTC-Start>/` (`<UTC-Start>` = `YYYYMMDDTHHMMSSZ`, time of `Start`):

- `session.json`, schema `wsc.vision.handeye.session/1`:
  - `outcome`: `"finished"`, `"refused"` (fewer than 3 rotation axes) or `"failed"` (solver error, save error).
  - `createdAt`, `frameId`, `tagId`, `tagInBase`, `board` (`type`, `cols`, `rows`, `squareSizeM`), `intrinsicsSource`.
  - `intrinsics`: `cameraMatrix`, `distortionCoefficients`, `distortionModel`, `imageSize`, `calibrationId`, `frameId`; `null` without a calibration.
  - `samples[]`: `index`, `accepted`, `detail`, `flangeInBase`, `camToTarget`, `reprojectionErrorPx`, `cornersPx` (N × 2 in the distorted image), `image` (file name or `null`).
  - `result`: as in the status.
- `sample_NNN.png` per capture with image (number = `index`, three digits).
- A second attempt of the same session overwrites the folder.

**Pose format** everywhere: `{"position": [x, y, z], "orientation": [x, y, z, w]}`, meters and quaternion `xyzw`.

### Network / operations

No new ports, services or mDNS names. The records lie on the hand Pi under `data/handeye/sessions/`; `data/` is not versioned. The offline run (`--session`) needs only the repo and numpy/OpenCV, no network.

### Frontend (webskillcomposition, branch `handeye-genauigkeit`)

For orientation only, not part of the Pi contract:

- **Arrival** (`robotDrive.ts`): only after 3 quiet readings in a row (`ARRIVAL_STILL_READS = 3`, 250 ms apart). Before, one was enough.
- **Standstill during capture** (`HandEyeAutoPanel.tsx`, `jointSettling.ts`): 5 joint readings 100 ms apart. Scatter per joint at most `JOINT_SETTLE_TOLERANCE_RAD = 0.0002` rad, otherwise repeated up to 3 times (800 ms pause). Then the pose is skipped; the hint „Nicht ruhig genug“ (Not steady enough) names it.
- **Re-reading**: after `CaptureHandEyeSample`, the joints are read again. If a joint has moved by more than 0.0002 rad, the capture is repeated **once**. The suspicious capture stays on the Pi (the Pi has no rollback); the hint names its number.
- **Approach board origin** (`HandEyeAutoPanel.tsx`, logic in `handEyeBoardOrigin.ts`): after the calibration, in two stages: first 60 mm, then 10 mm in front of the first inner corner along the board normal (+z into the board, tool points into the board). Tool tip = flange + 0.1034 m (URDF, otherwise default value). The operator measures the distance of the tip to the corner.
- **Display** (`HandEyeCalibrationWindow.tsx`): prediction error (`looErrorRmsM`), board scale (yellow from 0.3 %), intrinsics hint (`realsense_factory`, `placeholder` and unknown sources; `calibration` counts as good), `result.warnings`.
- **Board size** (`DEFAULT_BOARD`): 6 × 7 inner corners, 23 mm.

### Failure cases

| Case | Behavior |
| --- | --- |
| `Finish`, at least 3 captures but fewer than 3 rotation axes | `INVALID_STATE`, session stays open, record `refused`, reason in the status |
| `Finish`, fewer than 3 usable poses | `INVALID_STATE`, session stays open, record `failed` |
| `Finish`, saving fails | `INTERNAL`, state `failed`, record `failed` |
| Intrinsics `realsense_factory` or `placeholder` | `intrinsicsSource` set, warning in `result.warnings` |
| `|boardScale − 1| > 0.3 %` | warning „Brettmassstab weicht …“ (Board scale deviates …) |
| Outlier capture (pixel error above threshold) | rejected, index in `result.rejectedSamples`, refinement recomputed without it |
| No corners or intrinsics in the capture | Park-Martin, warning „Keine gemeinsame Verfeinerung …“ (No joint refinement …) |
| Refinement throws unexpectedly | Park-Martin, error written to the log (calibration does not fail) |
| Board not fully in the image | capture rejected (`DETECTION_FAILED`), session continues |
| Joints moved after the capture | capture repeated once (frontend); the first one stays on the Pi |
| `refine_hand_eye` with fewer than 3 captures | `ValueError` |
| `leave_one_out_errors` with fewer than 4 captures | `ValueError` (in the server: no LOO, field `null`) |
| `VISION_SUBPIXEL_CORNER_REFINEMENT` unknown | `ValueError` at start |

## Approach

Implementation in two parts, worked on in parallel, each by an implementation agent. Tests are written and run by the main agent.

**A — Hand Pi (branch `apriltag`):**

1. Store corner points per capture (`HandEyeSession.capture`): board corners as the mean over 5 fresh frames, intrinsics used. Session as record in `data/handeye/sessions/<UTC-Zeit>/`.
2. Joint refinement `refine_hand_eye` in `src/tagloc/handeye.py`: start = result of `solve_hand_eye_robust`; Levenberg-Marquardt in numpy (no scipy on the Pi) over `X` (6), `T_base_board` (6) and optionally the scale `s` (1). Residuals: reprojection of all board corners of all captures in pixels, without distortion on undistorted points as in `boards.py`. Huber weighting. Reused: `compose`, `invert`, `rvec_from_rotation`, `average_poses` from `src/tagloc/geometry.py`.
3. Leave-one-out prediction error: refine `X` without capture `i`, predict the corners in `i` → `looErrorPx` and `looErrorM` per capture, RMS in the result.
4. Status and checks: `intrinsicsSource` in the status; note for `realsense_factory`; note for `|boardScale − 1| > 0.3 %`; `finish` refuses with fewer than 3 rotation axes.
5. Tag path: refine both IPPE_SQUARE solutions in `src/tagloc/pose.py` with `cv2.solvePnPRefineLM`; the smaller reprojection wins (pattern from `boards.py`, `estimate_board_pose`). For `cam_flange`, subpixel corners on by default (`detector.py`).

**B — Frontend (webskillcomposition, branch `handeye-genauigkeit`):**

1. Standstill: arrival after 3 quiet polls (`robotDrive.ts`, `HandEyeAutoPanel.tsx`). `settledJoints` discards poses with scatter above 0.0002 rad. After `CaptureHandEyeSample`, re-read the joints; on deviation, discard the capture and repeat it.
2. Display: `looErrorMm` replaced by `looErrorRmsM`, `refinedReprojectionRmsPx`, `boardScale`, `intrinsicsSource` with warning notes (`handEye.ts`, `HandEyeCalibrationWindow.tsx`).
3. Check „Brettursprung anfahren“ in `HandEyeAutoPanel`: after `Finish`, move 10 mm above the `targetInBase` origin (board normal), via the existing `driver.moveFlangeTo` / `planMove`. The operator measures the offset. This separates the hand-eye error from the tag measurement error.

**Findings that justify the plan:**

1. Translation from fixed rotation (`solve_hand_eye`): Park-Martin for R, then unweighted `lstsq` for t. A rotation error of 0.1° yields about 0.5–1 mm translation error. There is no joint refinement against the pixel measurement.
2. Scale not checked: the board square size (23 mm nominal) and the intrinsics (still from the old board 7×9/22 mm; silent fallback to factory intrinsics possible) scale all `t_C` equally and create a bias in `t_X`. With known robot translations, the scale is observable and can be co-estimated.
3. Tag path unrefined: `CORNER_REFINE_NONE` (`profiles.py`, `subpixel_corner_refinement=False`), IPPE_SQUARE without `solvePnPRefineLM`, first solution (`src/tagloc/pose.py`). The board path already does it correctly.
4. Quality figure is not accuracy: consistency instead of prediction error; no value that shows an offset; `intrinsicsSource` missing from the status.
5. Smaller points: standstill = 1 quiet poll (`robotDrive.ts:343`); joints not re-read after the capture; `Finish` does not enforce 3 rotation axes; `handeye-schachbrett-automatik.md` names the wrong board (5×6 inner corners).

## Verification

**Pi:**

- `tests/test_handeye_refine.py` (new): synthetic corners with 0.3 px noise → refined `X` more accurate than Park-Martin. Built-in scale error of 1 % → `boardScale` ≈ 1.01, and the translation bias of `X` disappears. Outlier capture → Huber holds. LOO error plausible.
- `tests/test_tag_pipeline.py`: rendered tag more accurate with LM than without.
- `tests/test_handeye_session.py`: fewer than 3 axes is refused; `intrinsicsSource` in the status.
- Execution: `PYTHONPATH=src python -m unittest discover -s tests -t .`
- Run 28.10.2026 with the project venv (`.venv/bin/python`): 865 tests OK. Frontend tests not checked in this run.

**Frontend:**

- vitest for standstill, re-reading of the joints and the status parser.
- `npm test` and `npx tsc --noEmit`.

**At the setup (user):**

- Calibration drive → read `looErrorRmsM` and `boardScale`.
- Measure „Brettursprung anfahren“ and tag approach before and after. Target: offset ≤ 1 mm.

## Results (synthetic, 28.10.2026)

Simulation with 640 × 480 pixels, fx = 466, 15 captures, 30 random seeds. The numbers are medians and 90th percentiles (p90) over the seeds, not measured at the setup. No evaluation script is in the repo; `tools/make_synthetic_scene.py` only generates the scenes.

- **Translation `t_X` (hand-eye), Park-Martin vs. refined:**
  - Park-Martin: median 1.55 mm, p90 2.3 mm.
  - Refined at 0.3 px noise: median 0.20 mm, p90 0.6 mm.
- **Board scale 1 % too large:**
  - Park-Martin: 4.8 mm offset in `t_X`.
  - Refined with scale estimation: 0.20 mm, scale accurate to 0.06 %.
- **Board correct, scale estimation switched on:** costs some accuracy (p90 0.25 mm → 0.6 mm). Therefore `--no-scale` exists, for when the board is demonstrably correct.
- **Tag corners rendered** (median of the reprojection): `none` 0.83 px, `apriltag` 0.70 px, `subpix` 0.35 px.
- **Tag pose with `solvePnPRefineLM`** (frontal): flip share 9.8 % → 0 %, rotation error 2.3° → 1.3° (reprojection 0.3 px).
- **Why the outlier check runs after the refinement:** The old check on the Park-Martin scatter discarded good captures on a scale error, because the scale inflates the scatter.

The numbers say nothing about the fixed offset on the real FR3; only the measurement at the setup clarifies that (see open questions).

## Open questions

- **Measurement at the setup is pending.** Whether the fixed offset is below 1 mm has not yet been measured. Procedure: calibration drive; read `boardScale` (deviation above 0.3 %: re-measure the board or recalibrate the intrinsics); read `looErrorRmsM`; measure „Brettursprung anfahren“ and tag approach before and after.
- **Pi state:** The Pi needs the state `c494f71` on `apriltag`. Without it, the new status fields are missing and the frontend shows no prediction errors.
- **Status remains `in progress`** until the measurement at the setup is available.
- Board size: addenda are in `handeye-schachbrett-automatik.md` (end) and in `vision-server-interface.md`, section 15. The older statements there (6×7 **squares**, example `cols 5 / rows 6`) remain unchanged.

## Deviations from the plan

- **Python API:** `refine_hand_eye` takes poses, corner sets, board points and K directly and returns `HandEyeRefinement`, not `HandEyeSample`/`RefineResult`. The outlier check sits in `refine_hand_eye_robust` (pixel error per capture, see Interfaces).
- **`HandEyeSession.finish()`** returns `VisionErrorCode`, not a `HandEyeResult`. The result is in the status under `result`.
- **Rotation axis check** applies only if at least 3 captures are accepted. Below that, the solver reports the situation.
- **Subpixel corners** are not the default in `detector.py`, but in the preset `cam_flange` (`server.py`), and can be overridden via `VISION_SUBPIXEL_CORNER_REFINEMENT`.
- **Session record** additionally knows `outcome: "refused"` and `"failed"`. For `refused` and `failed`, the session stays open.
- **Frontend:** The standstill check takes 5 readings (previously not specified); the repetition is 3 times. The board origin check moves in two stages, 60 mm and then 10 mm before the corner, instead of only 10 mm. `looErrorMm` is called `looErrorRmsM` (meters).
- **Interface comment in the frontend:** `handEye.ts` names the value `"file"` for `intrinsicsSource`. The Pi delivers `calibration`. The logic (`f5c81ba`) is correct; only the comment is outdated.
- **Tests:** The Pi tests are in `tests/test_handeye_refine.py`, `tests/test_handeye_session_refine.py` and `tests/test_subpixel_settings.py`. `tests/test_tag_pipeline.py` and `tests/test_handeye_session.py` still pass. Frontend: `jointSettling.test.ts`, `handEyeBoardOrigin.test.ts`, `HandEyeCalibrationWindow.test.ts`, `handEye.test.ts`.

## After completion

- Status set to `done`, actual interfaces entered.
- Line in the index `doc/projektdoku/arbeitsplaene/README.md` updated.
- Lasting domain knowledge transferred into the appropriate topic doc under `doc/projektdoku/` (not just left in this plan).

## Addendum 28.10.2026: Target 7×10, board adjustable, live stream

**Finding at the setup.** During the hand-eye session, every capture was discarded with the message „Board-Pose passt schlecht (18.0 px)“ (Board pose fits poorly (18.0 px)). Cause: the frontend searched for 6×7 inner corners. The target has 8×11 squares, i.e. 7×10 inner corners at 23 mm. The search therefore found a partial grid with wrongly assigned corners, and the reprojection error stayed far above the threshold. The Pi presets `cam_ceiling` and `cam_flange` in `src/vision_server/server.py` have been at 7×10 inner corners, 23 mm per square, since commit `c615b36` (colleague, 28.10.2026).

**Frontend** (`webskillcomposition`, branch `handeye-brett`, commit `54a96c8`): The board is adjustable in the hand-eye window: inner corners across, inner corners up, square size in mm. The default is 7×10 / 23 mm. The input is validated: integers, across ≥ 3, up ≥ 2, sum odd, square 5–100 mm. The setting is remembered in the browser under the localStorage key `wsc.handEye.board/1`. The module is `model/handEyeBoard.ts` with `HAND_EYE_DEFAULT_BOARD`, `validateHandEyeBoard`, `loadHandEyeBoard` and `saveHandEyeBoard`.

**Poses recomputed** (simulation, new solver, 7×10 board, 640 × 480, noise 0.3 px):
- The 18 auto views rotate about all three axes. RMS of the pair rotations: 28° / 29° / 56°.
- The board lies entirely in the image in all 18 views.
- Errors of `t_X`: median 0.30 mm, p90 0.60 mm. Scale: 0.06 %.
- A set with stronger tilts and three distances (15 views) was worse: 0.54 mm and 1.01 mm respectively. The poses therefore stay as they are.

**Live stream in calibration mode** (`CameraStreamMode` = `calibration`, commit `6e03cca`) — *removed 17.11.2026; the mode no longer exists, and only the `calibration_progress` hook of the annotator remains:*
- No more black text outline. The texts are drawn with `outline=False`.
- Top left shows only „{n} Ecken“ (n corners) in board color, or „0 Ecken“ (0 corners) if the board is not detected.
- Bottom left shows only „Kalibriermodus“ (calibration mode). The frontend shows the session progress.
- Found corners are marked larger (radius 6 instead of 4, each times the scale, at least 3 px).
- `draw_board_overlay` in `src/tagloc/overlay.py` has the new switches `outline` (default `True`) and `compact` (default `False`). The CLI view calls it with the default values and is unchanged.
- The pause during a measurement or hand-eye session (`src/vision_server/runner.py`) applies in the code only to mode `apriltag`: then the stream shows the raw image (`src/vision_server/stream_overlay.py`, `annotate`). In mode `calibration`, the overlay is not suspended.

**Tests:**
- Frontend: `handEyeBoard.test.ts`.
- Pi: `tests/test_stream_calibration_overlay.py` (2 tests; they need OpenCV and are otherwise skipped).
- Pi suite: 867 tests, OK (run 28.10.2026 with the project venv, `PYTHONPATH=src python -m unittest discover -s tests -t .`).

**Next steps at the setup:**
1. Recalibrate the intrinsics of the hand camera with the 7×10 target.
2. In the hand-eye window, check 7×10 / 23 mm and trigger „Brett prüfen“ (Check board).
3. Carry out the calibration drive.
4. Read `boardScale` and the prediction error.
5. Run „Brettursprung anfahren“ and measure the offset.
