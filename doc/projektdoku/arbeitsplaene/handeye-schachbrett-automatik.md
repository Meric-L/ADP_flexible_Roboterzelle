# Hand-eye calibration with a chessboard, the robot moves the poses itself

**Status:** done — 13.10.2026 (not tested on real hardware)
**Owner:** John Glanz, `Agent: Hand-eye automatically with chessboard`
**Topic:** apriltag
**Branch:** apriltag

## Goal

So far the hand-eye calibration (`handeye-ueber-opcua.md`) is done against a single AprilTag, and the operator moves each pose by hand. With this plan
1. the Pi can measure a **calibration chessboard** instead of a tag: the laser-engraved board with 6×7 squares of 23 mm, i.e. 5×6 inner corners. Many corners give a more precise and unambiguous pose.
2. the frontend (`webskillcomposition`, branch `Ungetestet`) generates the poses itself, shows them beforehand as a ghost robot and then runs through them. For each pose it reports the capture; at the end it triggers „Berechnen“ (calculate).

## Already read

- `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md` (session, methods, status, error codes; open question "Automatic approach")
- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md` (solver, file format)
- `doc/projektdoku/arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md` (undistortion for both lens models)
- `src/tagloc/boards.py` (board detection, corner order of `findChessboardCornersSB`)

## Affected files

- `src/tagloc/boards.py`: `chessboard_is_unambiguous`, `canonical_chessboard_corners`, `estimate_board_pose` (new)
- `src/vision_server/detection/apriltag.py`: `measure_board` (new)
- `src/vision_server/handeye_session.py`: target is a tag **or** a board
- `src/vision_server/runner.py`: wire up the port `measure_board`
- `tests/test_boards_pose.py` (new), `tests/test_handeye_session.py` (board cases)
- `doc/projektdoku/vision-server-interface.md` section 15: addendum on the board

## Interfaces

### OPC UA

No new nodes or methods. `StartHandEyeCalibration(OptionsJson)` additionally accepts a board. All other methods, codes and locks remain as described in `handeye-ueber-opcua.md`.

### Data

**`OptionsJson`**, schema `wsc.vision.handeye.start/1`. Exactly **one** of `tagId` and `board` must be given:

```json
{ "schema": "wsc.vision.handeye.start/1",
  "board": { "type": "chessboard", "cols": 5, "rows": 6, "squareSizeM": 0.023 } }
```

- `cols`/`rows` are the **inner corners**: a board with 6×7 squares has 5×6.
- `cols + rows` must be odd. Only then can the board be told apart from the original rotated by 180°. Otherwise the Pi replies `INVALID_ARGUMENT`.
- With `tagId` the old form applies unchanged, including `tagInBase`.

**`HandEyeStatus`**: additionally the field `target`.

```json
"target": { "type": "tag", "tagId": 12 }
"target": { "type": "chessboard", "cols": 5, "rows": 6, "squareSizeM": 0.023 }
```

- For a board, `tagId` is `-1`.
- `samples[].camToTag` is then `T_cam_board`.

**Board frame:**
- Origin at the first inner corner, defined such that the square between the first four corners is **black**.
- +x along the row, +y towards the next row.
- +z away from the camera, i.e. into the board.

### Python

```python
# src/tagloc/boards.py
def chessboard_is_unambiguous(spec: BoardSpec) -> bool
def canonical_chessboard_corners(gray, corners, spec) -> np.ndarray     # (N, 2)
def estimate_board_pose(corners, spec, calibration) -> tuple[Pose, float]  # (T_cam_board, px); ValueError

# src/vision_server/detection/apriltag.py
async def AprilTagDetectionSource.measure_board(spec) -> tuple[Pose | None, str]

# src/vision_server/handeye_session.py
HandEyePorts.measure_board: Callable[[BoardSpec], Awaitable[tuple[Pose | None, str]]] | None
```

### Failure cases

| Case | Behaviour |
| --- | --- |
| Board not completely in the image | Capture `accepted: false`, `DETECTION_FAILED`, reason in the status, session continues |
| Reprojection error > `max_reproj_error_px` (wrong square size or wrong grid) | as above, reason names the error in px |
| `cols + rows` even, `tagId` **and** `board`, or neither | `Start` → `INVALID_ARGUMENT` |

### Frontend (for orientation only, code in `webskillcomposition`)

- The operator specifies the board centre in the robot base CS, the distance camera–board and the camera offset along flange z.
- The frontend generates about 12 camera poses: above the centre, tilted and rotated about the viewing axis; the camera always looks at the centre. From these it calculates flange poses and the IK. Unreachable poses are dropped.
- The poses are shown beforehand as a ghost robot.
- After „Start“ the frontend moves to each pose with `go_to`, waits until the robot stands still, and calls `CaptureHandEyeSample` with the **reached** flange pose. At the end it calls `FinishHandEyeCalibration`.

## Open questions

- Not tested on real hardware. The poses assume that the camera looks roughly along flange z. If it sits differently, it will not see the board in some poses: those captures are discarded, the calibration stays correct, it is just shorter.

## Deviations from the plan

- `estimate_board_pose` takes both IPPE solutions and refines them with Levenberg–Marquardt (`solvePnPRefineLM`). On the rendered board, frontally, the first solution was otherwise 2.5° resp. 2 mm off; now below 0.2 mm and 0.2°.
- `HandEyePorts.measure_board` is optional (`None`); the runner reads it with `getattr`. A server without the function rejects board captures with a reason instead of crashing.
- Frontend (`webskillcomposition`, branch `Ungetestet`): window „Hand-Kamera kalibrieren“ (calibrate hand camera), mode „Schachbrett, automatisch“ (chessboard, automatic) (`HandEyeAutoPanel.tsx`, `handEyeAutoPoses.ts`). There are 11 viewing poses: rotated ±30° about the viewing axis vertically, plus rings with 20° and 30° tilt.

## After completion

- [x] Status `done`, interfaces entered.
- [x] Index line in `README.md`.
- [x] `vision-server-interface.md` section 15.1.
- [x] Test run: 567 tests. Individual time-dependent live stream tests (`test_camera_stream`, `test_mjpeg_server`) fail sporadically in the full run; run individually they pass, independent of this change. New: `test_boards_pose.py` (4), `HandEyeBoardTest` (4).

## Addendum 28.10.2026: board size

The laser-engraved board has **7×8 squares** of 23 mm, so **6×7 inner corners**. That is what the code says: `src/vision_server/server.py` (comment "New board since 19.10.2026: 6x7 inner corners, 23 mm/square") and `DEFAULT_BOARD` in the frontend (`cols: 6, rows: 7, squareSizeM: 0.023`).

The statements "6×7 squares / 5×6 inner corners" further up in this document (section Goal, point 1, and the explanation of `cols`/`rows`) are thus superseded. They remain in place; this addendum is authoritative.

`tests/test_boards_pose.py` deliberately renders a 5×6-inner-corner board (`SPEC`). This is a synthetic test image for the pose calculation and not a contradiction to the hardware. The comment above `SPEC` still says "6x7 squares, i.e. 5x6 inner corners" for the lab board. That is wrong there; it was not changed.

## Addendum 28.10.2026 (2): target 7×10, board adjustable

The addendum "Board size" above (6×7 inner corners) is superseded for the hand-eye target. The target with 8×11 squares of 23 mm, i.e. **7×10 inner corners** (`cols` 7, `rows` 10, `squareSizeM` 0.023), is now authoritative. The Pi presets `cam_ceiling` and `cam_flange` have been set to this since commit `c615b36`. In the frontend the board is adjustable in the hand-eye window; the default is 7×10 / 23 mm.

The finding, the frontend module, the poses and the live stream are described in `handeye-genauigkeit.md`, in the section "Addendum 28.10.2026: Target 7×10, board adjustable, live stream". The statements in this document above remain unchanged.
