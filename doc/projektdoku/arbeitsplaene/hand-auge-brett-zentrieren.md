# Hand-eye: center board (MeasureBoard) and capture quality

**Status:** done — 22.10.2026 (not tested on real hardware)
**Owner:** `Agent: Hand-eye center board on hand Pi`; frontend: session `opcua-1a`
**Topic:** apriltag
**Branch:** apriltag

## Goal

Before `CaptureHandEyeSample`, the controller should bring the calibration
chessboard (6x7 inner corners, 23 mm) centrally into the camera image. For this
it needs a measurement that also yields something when the board is only
partially visible. In addition, each capture should show its quality and never
use an image from before the call.

## Scope

1. **First image after the call:** `measure_tag`, `measure_board` and `MeasureBoard` only use images that arrive after the call. The most recently buffered image only counts for the timestamp.
2. **`reprojectionErrorPx` per capture:** `samples[i].reprojectionErrorPx` in `HandEyeStatus`, mean over the images of the capture, also for discarded captures. `null` if nothing was found.
3. **`MeasureBoard(RequestJson) -> (ResultJson, Error)`:** measures the board without motion commands. See `vision-server-interface.md`, Section 15.4.

A check for a too-small grid size is deliberately **not** built in: there is only
one calibration board (6x7), and the size is not checked per call.

## Affected files

- `src/vision_server/measureboard.py` (new): request, result JSON
- `src/tagloc/boards.py`: `partial_patterns`, `detect_partial_board`
- `src/vision_server/detection/apriltag.py`: `_latest_timestamp`, 3-tuple returns with info, `measure_board_view`, `default_board_spec`
- `src/vision_server/handeye_session.py`: `reprojectionErrorPx` per capture
- `src/vision_server/runner.py`: `_install_measure_board`
- `tests/test_measureboard.py` (new), `tests/test_apriltag_source.py`

## Interface `MeasureBoard`

`ns=<vision>;s=VisionMachine.MeasureBoard`, `(RequestJson: String) -> (ResultJson: String, Error: Int32)`. Only on the hand Pi. Replies only after the measurement. Also runs **during** a hand-eye session.

Request `wsc.vision.measureboard.request/1`, all fields optional (`""`/`{}` valid):

```json
{ "schema": "wsc.vision.measureboard.request/1",
  "board": {"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": 0.023},
  "frames": 3 }
```

`frames`: 1 to 10, default 3. Without `board`, the board from the configuration applies.

| Error | When |
| --- | --- |
| `OK` | measured, even if nothing was found (`found: false`) |
| `DETECTION_FAILED` | no fresh camera image (`error: "NO_FRAME"`) |
| `INVALID_ARGUMENT` | JSON or board invalid |
| `INVALID_STATE` | vision system not `Operational` |
| `BUSY` | job or camera calibration running |

Result `wsc.vision.measureboard.result/1` (always valid JSON): see Section 15.4 of the interface description.

## Tests

`tests/test_measureboard.py` (rendered 6x7 boards in several poses, partial grid
at the image edge, session, OPC UA), adapted `MeasureTagTest`. Full suite: only
the known `test_mjpeg_server` fan-out test fails.
