# Ceiling camera calibration: choose the board in the frontend (also ChArUco)

**Status:** done (without hardware run) — 17.11.2026
**Owner:** Meric, `Agent: Layer 1 calibration with selectable board`
**Topic:** vision
**Branch:** `worktree-kalibrierung-layer1-board` (cell), `layer1-kalibrierung-board` (webskillcomposition)

## Goal

The ceiling camera (layer 1) can be calibrated with the same board as the hand
camera in layer 2, including the ChArUco board. The frontend sets which board
(type, squares, square size, marker size, dictionary) is used at the start, as in
the hand-eye calibration. Until now the board was fixed in `AprilTagProfileConfig`.

In addition, in the frontend (webskillcomposition only): teach an arbitrary number of robot
poses for the ceiling camera calibration instead of exactly 16.

## Already read

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/vision-server-interface.md`, section 12 (calibration) and the section on
  the stream overlay `calibration`
- `doc/projektdoku/arbeitsplaene/handauge-charuco-auswahl.md` (board JSON of the
  hand-eye calibration, reused here)
- `doc/projektdoku/part10-programm-schnittstelle.md` (reference to `StartCalibration`)

## Affected files

- `src/vision_server/calibration_session.py`
- `src/vision_server/runner/calibration.py`
- `src/vision_server/stream_overlay.py`
- `tests/test_calibration_session.py`, `tests/test_calibration_start_board.py`
- `doc/projektdoku/vision-server-interface.md` (section 12.1, new paragraph)

## Interfaces

### OPC UA

`StartCalibration` gets **one** input argument:

| Input | Type | Meaning |
| --- | --- | --- |
| `OptionsJson` | `String` | empty or `{}`: board from the configuration (as before). Otherwise `wsc.vision.calibration.start/1` with `board` (see below). |

| Output | Type | Meaning |
| --- | --- | --- |
| `Error` | `Int32` | `0` OK, `1` INVALID_STATE, `2` INVALID_ARGUMENT (options not valid JSON / board unusable, reason in the log and in `CalibrationProgress.startError`), `3` BUSY |

Compatibility: a direct asyncua call without an argument continues to work (default value
empty). However, the webskillcomposition backend checks the argument count strictly against
`InputArguments`: an old frontend (calls with `[]`) is rejected by a new Pi, and a new
frontend (calls with `[json]`) by an old Pi. Pi and frontend are updated together.

`CalibrationProgress` gets the field `board`: the board of the running or most recently run
session (`BoardSpec.as_dict()`, i.e. `type`, `cols`, `rows`, `squareSizeM`, and for ChArUco
additionally `markerSizeM`, `dictionary`). After a rejected start option, additionally
`startError` (text), until the next successful start.

`MeasureBoard` remains unchanged; during the robot run of the ceiling camera the frontend
sends the selected board along (`board` in the request, previously `null`).

### Data / payload

```json
{
  "schema": "wsc.vision.calibration.start/1",
  "board": {
    "type": "charuco",
    "cols": 11, "rows": 7,
    "squareSizeM": 0.025, "markerSizeM": 0.0185,
    "dictionary": "DICT_5X5_100"
  }
}
```

`board` follows the same rules as in `wsc.vision.handeye.start/1`
(`handeye_session._parse_board` and `check_raw_board`): chessboard with `cols`/`rows` =
inner corners, `cols + rows` odd, square 5–100 mm; ChArUco with `cols`/`rows` = squares
(at least 3×3), marker smaller than the square, dictionary from
`DICT_{4X4,5X5,6X6,7X7}_{50,100,250,1000}` with enough markers. If `board` is missing, the
board from the configuration applies.

### Python

```python
# Module: src/vision_server/calibration_session.py
def parse_start_options(text: str) -> BoardSpec | None:
    """`OptionsJson` of StartCalibration -> board, None = board from the configuration.
    Raises ValueError with a plain-text message."""

class CalibrationSession:
    def start(self, board: BoardSpec | None = None) -> None: ...
    @property
    def board_spec(self) -> BoardSpec: ...   # board of the running/last session
```

The evaluation (`_process`, `_compute_and_save`), the archive (`session.json`, field `board`)
and the stream overlay in mode `calibration` take the board of the session. The saved
calibration file carries it, as before, in the field `board`.

### Failure cases

- Invalid options: `INVALID_ARGUMENT`, no session started, a previous session remains untouched.
- ChArUco on the ceiling camera: detection runs on the full image (`detect_board` only
  downscales chessboards). An image without detected corners counts in the evaluation, as
  before, as „Board nicht gefunden“ (board not found).

## Approach

1. Pi: `parse_start_options`, `CalibrationSession.start(board)`, board in progress, archive
   and overlay; `StartCalibration` with `OptionsJson`.
2. Tests for parser, session and method.
3. Frontend: board setting (chessboard/ChArUco) in the calibration window of the ceiling
   camera, stored together with the layer 2 setting; start with options; `MeasureBoard`
   with the board.
4. Frontend: teach poses without a fixed count; „Fertig“ (done) saves.

## Open questions

- How fast is the ChArUco detection on the full 12 MP image of the ceiling camera on the Pi?
  (`MeasureBoard` before each capture.) Not yet measured on the hardware.

## Deviations from the plan

None.
