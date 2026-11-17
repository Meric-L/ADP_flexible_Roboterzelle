# Hand-Eye Calibration with Chessboard Raw Data and Offline Comparison

**Status:** done — 09.11.2026 (no hardware run)
**Owner:** Meric, Codex
**Topic:** apriltag
**Branch:** `handauge-schachbrett-offline` (this repo and `webskillcomposition`)

## Goal

The existing automatic hand-eye calibration is being reworked: the frontend
moves to up to 60 safe, geometrically distinct poses. The hand Pi
stores, per pose, the raw image of the already configured camera stream and the
measured robot state. The calculation then runs offline as often as needed. The
existing button "Kalibrierung starten" (start calibration) starts the new capture
run; there is no additional start button.

The chessboard has 8 × 11 squares, i.e. 7 × 10 inner corners, with a 0.023 m
square size. The camera intrinsics stay fixed. 0.5 mm is a development target,
not an accuracy that can be demonstrated from reprojection alone.

## Already read

- `CLAUDE.md`, `arbeitsplaene/README.md`
- `handeye-schachbrett-automatik.md`, `handeye-ueber-opcua.md`, `handeye-genauigkeit.md`
- `src/tagloc/boards.py`, `src/tagloc/handeye.py`, `src/vision_server/handeye_session.py`
- `webskillcomposition/frontend/src/features/cell-modules/model/handEyeAutoPoses.ts`

## Affected files

- Pi: `src/vision_server/detection/apriltag/board.py`,
  `handeye_session.py`, `runner/hand_eye.py`
- Offline: `src/tagloc/handeye_offline.py`,
  `src/tagloc/cli/calibrate_handeye_offline.py`, `requirements.txt`
- Frontend: `HandEyeAutoPanel.tsx`, `handEyeAutoPoses.ts`, `handEye.ts`
- Tests: raw data and solver tests; frontend pose tests

## Interfaces

### OPC UA

The existing methods and NodeIds remain. `StartHandEyeCalibration`
additionally accepts `captureOnly: true` for `board`. In this mode
`FinishHandEyeCalibration` only closes the data set; it does not calculate or
activate any transformation. `AbortHandEyeCalibration` keeps raw images that have
already been written. The `HandEyeStatus` contains `sessionPath`.

In capture mode, `CaptureHandEyeSample` additionally receives `poseId` in the format
`pose_NNN`, `poseGroup` (`A`–`E`) and `jointAnglesRad` (seven angles). The flange
pose `flangeInBase` comes from FK over these measured joint angles.

### Data

Under `data/rohbilder/calibration/<UTC>_<ID>/` are `config.json`,
`poses/pose_NNN/camera.png`, `poses/pose_NNN/robot_state.json` and `results/`.
`config.json` contains the board, coordinate conventions, intrinsics and image stream.
`robot_state.json` contains exactly the same `poseId` as the folder, the
4×4 matrix `T_base_flange`, joint angles, timestamp, group,
capture status and online reprojection error. Images are written immediately after
each capture. Offline results are stored only under `results/`.

### Python and operation

`tagloc.handeye_offline.load_observations(folder, pnp, max_pnp_rms_px)` reads
all original images again and caches corners as well as PnP diagnostics.
`solve_subset(...)` calculates Park, Tsai and Daniilidis and refines
`T_flange_camera` and `T_base_board` with SciPy against the image points.
`run_experiments(...)` stores several distributed subsets and groups.
Under `results/` are the detection cache, experiment JSON, an HTML comparison
and, when the overall solution has converged, `candidate_handeye_<pnp>.json` in the
existing hand-eye file format. This candidate file is not activated.

Call: `python -m tagloc.cli.calibrate_handeye_offline <session>`; with
`--pnp ippe` the alternative planar PnP method is examined.
Results do not become active automatically. No new ports or services.

## Failure cases and limits

- Missing raw image, wrong image size or incomplete 7×10 grid:
  exclude the capture with a reason.
- Duplicate or invalid pose ID and missing joint angles: reject the capture.
- On a write error `INTERNAL`; data written up to that point is retained.
- No real robot motion without the existing safety and motion releases.
- An independent accuracy validation is not part of this step.

## Open check on hardware

Check the configured RealSense stream and the associated intrinsics file against the
actual resolution; the software cannot perform this check without the device.

## State as of 09.11.2026: comparison with `cv2.calibrateHandEye` (OpenCV 4.14, offline)

Both sessions (`20261109T125050Z_a5c964f0` ChArUco, `20261109T125825Z_a580eaba`
chessboard) were evaluated again in a separate environment (`tools/handeye_offline_cv4/`,
OpenCV 4.14; the Pis stay on OpenCV 5). Report:
`doc/auswertung/calibration_cv4_vergleich.html`, raw figures under
`doc/auswertung/handeye_cv4/`.

- cv2 Park is identical to `park_numpy`; Tsai/Daniilidis deviate by ≤ 0.45 mm.
  The missing `calibrateHandEye` under OpenCV 5 did not falsify the results.
- The result remains the reprojection refinement (the same solution from every start);
  closed-form methods serve only as a starting value, and their translation is poorly
  determined for small rotation angles (6.7 and 8.3 mm off, respectively).
- The error per pose is a rigid offset and grows with capture time;
  a model with linearly drifting board position explains a large part in session 1
  (drift 4.1 mm), and little in session 2 (2.0 mm). Cause not established.
- 0.5 mm is not achievable with this data. Next step (requires a robot motion,
  only after release): capture with a warmed-up robot, a repeated
  reference pose and mixed group order.

## Addendum 09.11.2026 (2): sessions after warm-up and kinematics as a source of error

The two open points from the state above (warmed-up robot, mixed group order) have been implemented in new sessions.

### New sessions

- `20261109T142717Z_a7d466a1` (S3, chessboard 7×10): 53 of 55 poses valid, after 15 min warm-up. Groups mixed in rotation, group change 1.00 (cold S1: 0.32).
- `20261109T143834Z_6852cbc7` (S4, ChArUco 11×7): 47 of 47 poses valid, after 15 min warm-up. Groups mixed in rotation, group change 0.98 (cold S2: 0.28).
- All four sessions recalculated offline with `analyse.py` and `drift.py --session`.
- Reports: `doc/auswertung/handeye_warm_vergleich.html` (generator `tools/handeye_offline_cv4/warm_report.py`), individual reports `calibration_schachbrett_2.html` and `calibration_charuco_2.html`.

### Final solutions cold vs. warm

Translation T_flange_camera in mm (x / y / z):

- S1 cold: −47.43 / −33.82 / 57.08; RMS 2.01 px
- S2 cold: −47.07 / −35.21 / 57.10; RMS 1.58 px
- S3 warm: −46.79 / −35.45 / 57.28; RMS 1.89 px
- S4 warm: −47.01 / −34.93 / 56.89; RMS 1.54 px

Findings:

- Range across all four sessions: 1.76 mm (outlier S1, largest pair S1–S3). Pair S3–S4 (both warm): 0.69 mm. Pair S1–S2 (both cold): 1.43 mm.
- Linear board drift explains only 5 to 7 % when warm (S3 7.4 %, S4 5.2 %); when cold in S1 27 %.
- The second half remains worse: RMS ratio second to first half S3 1.84, S4 2.10 (cold S1 1.72, S2 1.37).
- In S4, after removing tilt and distance, a partial correlation RMS–time of 0.53 remains.
- Warm-up alone does not bring 0.5 mm. The 0.5 mm target is still not demonstrated.

### Kinematics as a source of error (experiment, `tools/handeye_offline_cv4/kinematik/`)

- `T_base_flange` is exactly the nominal Franka forward kinematics of the measured joint angles. The fixed remainder is Rz −45° and z −0.2 mm. The target/actual deviation of the joints from the repeatability measurement therefore plays no role, because the measured angles are used.
- However, the nominal parameters deviate from the real robot. A joint bundle adjustment with separable kinematic parameters (zero offsets of joints 2 to 6, lengths d3, d5, a4, a5, a7) reduces the reprojection error in every held-out session (leave-one-session-out):
  - S1: 2.01 → 1.28 px
  - S2: 1.58 → 1.07 px
  - S3: 1.89 → 1.37 px
  - S4: 1.54 → 1.06 px
- Estimated values in the joint fit: zero offsets 0.0 / −1.8 / −4.2 / 2.0 / 4.3 mrad; lengths 0.7 / 1.6 / 1.1 / −2.8 / −3.5 mm.
- Range of the hand-eye translation across the sessions: 1.76 mm → 1.34 mm.
- An over-parameterised model (all 7 zero offsets and lengths) is not separable from hand-eye and board position and yields nonsensical translations (t_X up to 122.7 mm). It serves only as an indication.

### Assessment

- Joint fit across all four sessions: RMS 1.79 → 1.16 px. Kinematics thus explain roughly 60 % of the error variance (1 − (1.16/1.79)² ≈ 0.58).
- The remainder is just over 1 px, compared with a PnP level of 0.25 px.

### Next steps (open)

- Co-calibrate kinematics: extend the model, possibly account for deflection under gravity for joints 2 and 4, co-estimate intrinsics.
- Alternatively, local calibration in the working orientation range. The flat groups A and E already close with 0.45 to 0.55 px.
- Measure accuracy independently: approach an AprilTag from different poses and evaluate the scatter of `tagInBase` (AprilTag mode of the repeatability test).
- For new captures, use a true random order so that the tilt does not correlate with time.

## Addendum 10.11.2026: pose group F (image edge) for the offline intrinsics

### Reason

- The intrinsics of the hand camera are determined anew offline (`doc/auswertung/intrinsik_20261110.md`).
- This requires views with the board in the image corners and at the image edges, as well as strong tilt. The tilt determines the focal length.
- Until now every view aimed at the board centre. The hand-eye raw capture therefore delivers, in one automatic run, lossless images for intrinsics and hand-eye.

### Interface change

Effective immediately; replaces the statements on `poseGroup` and the number of poses above.

- `CaptureHandEyeSample.poseGroup` is `A`–`F`.
- Pi (`handeye_session.py`) and offline reader (`tagloc.handeye_offline.load_observations`) use the shared constant `tagloc.handeye_offline.POSE_GROUPS = ("A","B","C","D","E","F")`.
- Other values: `INVALID_ARGUMENT` (Pi) or exclusion of the capture with the reason "Posegruppe fehlt" (pose group missing) (offline).

### Frontend (webskillcomposition)

Files: `handEyeAutoPoses.ts`, `HandEyeAutoPanel.tsx`.

- Group F has 16 candidates.
- 8 image positions: 4 corners, 4 edge midpoints. The board centre lies at ±0.30·image width or ±0.28·image height from the image centre.
- Per image position two views: one with 30° tilt, one with 45° tilt. One tilts in the offset direction, the other perpendicular to it (which tilt gets which direction changes from position to position). 8 × 2 = 16.
- Only for the ChArUco board, since the chessboard must be fully in the image.
- With ChArUco up to 76 poses (quota F 16), with chessboard still up to 60.
- Mixed order includes F.
- Helper function `projectBoardCentrePx` and constant `GUESS_FOCAL_PX = 605` (estimate, only for aiming).

### Offline intrinsics

- Call: `python tools/handeye_offline_cv4/intrinsik_offline.py <session folder> [--nur-gruppe F]`. The script reads `poses/*/camera.png` directly.

### First finding without group F

- Session `20261109T143834Z_6852cbc7` (47 PNG, tilt 1–39°) alone yields: fx 603.3, fy 603.5, cx 332.0, cy 244.2; k1 0.092, k2 −0.125; RMS 0.11 px.
- This agrees with the 18 individual captures from 10.11. (fx 602.9, fy 603.3, cx 331.8, cy 244.2).
- The factory value fx 608.8 is therefore about 1 % off the measurement. Group F is meant to confirm this with stronger tilt.

### Commits

- ADP `6327a98` (Pi and offline).
- Frontend `6c59c40` on branch `handauge-schachbrett-offline` in `webskillcomposition` (remote archived, commit exists locally).

## Addendum 10.11.2026: hand camera at full resolution 1920×1080

- `CameraStreamConfig.realsense_resolution` (1920, 1080), `realsense_fps` 8; `cam_flange` preset `resolution` likewise (1920, 1080). Previously 640×480 @ 15 Hz, a 4:3 crop (1440×1080, scaled down by 2.25, horizontal 55° instead of 70° field of view).
- 8 Hz, because the D415 over USB 2 (Pi cable, `Usb Type Descriptor 2.1`) only offers 1920×1080 at this rate. With a USB 3 cable there are 6/15/30 Hz; then adjust `realsense_fps`, 8 Hz is not offered there.
- The intrinsics `data/calibration/cam_flange.json` still apply to 640×480 and no longer fit. They are determined anew; until then tag detection and the online check of the hand-eye raw capture reject the images ("Kalibrierung gilt für 640x480" (calibration applies to 640x480)). The intrinsics capture itself does not need intrinsics.
- Frontend group F (`handEyeAutoPoses.ts`): target values for 1920×1080, `GUESS_FOCAL_PX` 1357 (603 px at 640×480 times 2.25).

## Addendum 11.11.2026: full calibration on the Pi (`fullCalibration`)

1. **Full calibration:** `StartHandEyeCalibration` with `fullCalibration: true` captures raw images like `captureOnly`. `FinishHandEyeCalibration` calculates on the Pi the intrinsics (`cv2.calibrateCamera`, full model) and then the hand-eye (Park, then joint reprojection refinement with scipy). Both results are written together with `.bak` backup to `data/calibration/cam_flange.json` and `data/handeye/cam_flange.json` and applied live. If writing fails, both are restored. Details: [vision-server-interface.md](../vision-server-interface.md), section 15.6.
2. **Board dimensions free:** chessboard and ChArUco have freely enterable squares, dimensions and dictionary (section 15.7 of the interface documentation). The lab board is used with the re-measured values (square 24.982 mm, marker 18.487 mm) without a scaling factor. The frontend (`webskillcomposition`, branch `ADP_Lokaliserung`) remembers the entries in the browser.
3. **Status:** `HandEyeStatus` has the field `fullCalibration`. With `fullCalibration`, `result` contains `kind: "intrinsicsAndHandEye"` with intrinsics, hand-eye, `excluded` and `backups`.
4. **Dependencies:** `requirements.txt` names `opencv-contrib-python>=4.8.0` without the upper bound `<5`, because the Pis run with OpenCV 5. `scipy>=1.11` is needed on the Pi; if it is missing, the message is "scipy fehlt auf dem Pi (pip install scipy)" (scipy missing on the Pi (pip install scipy)).

Note: the offline scripts under `tools/handeye_offline_cv4/` are not carried over to this branch. They are on `handauge-schachbrett-offline`.

Deliberately not used are the kinematic correction of the flange pose and the factory intrinsics. The flange pose is the nominal FK from the measured joint angles. The kinematic correction worsened the 1080p session: with correction 4.78 px instead of 3.63 px without.
