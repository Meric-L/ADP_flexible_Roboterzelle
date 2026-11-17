# Board selection for the hand-eye raw capture

**Status:** done — 09.11.2026 (without a hardware run)
**Owner:** Meric, Codex
**Topic:** apriltag
**Branch:** `handauge-schachbrett-offline` (this repo and `webskillcomposition`)

## Goal

In the existing hand-eye window, choose between the normal chessboard and the
ChArUco board. The existing start button, the configured camera stream, the
calibrated intrinsics and the raw data directory remain the interface. The
selection applies to the entire capture and is stored in `config.json`.

## Documents read

- `CLAUDE.md`, `arbeitsplaene/README.md`
- `handauge-schachbrett-rohdaten-offline.md`
- `vision-server-interface.md`, Section 15

## Affected files

- Pi: `src/tagloc/boards.py`, `src/tagloc/handeye_offline.py`,
  `src/vision_server/handeye_session.py`, `src/vision_server/detection/apriltag/board.py`
- Frontend: `HandEyeAutoPanel.tsx`, `handEye.ts`, `boardCentring.ts`,
  `measureBoardCall.ts`
- Tests and documentation of the board selection

## Interfaces

`StartHandEyeCalibration` and `MeasureBoard` still accept in the field `board`
`{"type":"chessboard","cols":7,"rows":10,"squareSizeM":0.023}`.
For ChArUco the following applies
`{"type":"charuco","cols":11,"rows":7,"squareSizeM":0.025,"markerSizeM":0.0185,"dictionary":"DICT_5X5_100"}`.
For ChArUco, `cols` and `rows` count the squares; for the chessboard, the inner
corners. The Pi uses `CharucoDetector.detectBoard()` and `board.matchImagePoints()`.
Online, at least 25 detected corners are required. Offline, the image points per
pose are stored with their respective IDs and 3D points; the reprojection uses
these pose-dependent correspondences.

`data/rohbilder/calibration/<session>/config.json` stores the selected board
type and its dimensions. Images and `robot_state.json` remain under the same
`pose_NNN` ID. There is no additional OPC UA method or button.

## Failure cases and open checks

- Fewer than 25 ChArUco corners, missing IDs or too large a PnP error: discard
  the capture or offline pose, with a reason.
- With the current live image and the given board definition, 60 of 60 inner
  corners were detected. Hardware run and accuracy check are still pending.

## Implementation and testing

The selection is built into the existing calibration window. The online check
and `MeasureBoard` accept ChArUco; the offline evaluation processes, per pose,
the IDs actually detected. A synthetic board with 60 corners and a partially
occluded capture with 30 corners were read successfully offline. The targeted
Python and frontend tests and the TypeScript type check ran successfully. The
real capture run remains open.
