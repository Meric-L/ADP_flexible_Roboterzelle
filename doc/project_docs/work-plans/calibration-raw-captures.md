# Archive calibration captures losslessly (raw images per session)

**Status:** done — 10.11.2026
**Owner:** Meric, `Agent: Kalibrier-Rohbilder`
**Topic:** vision
**Branch:** `handauge-schachbrett-offline`

## Goal

With `VISION_SAVE_CALIBRATION_CAPTURES=1` the intrinsic calibration ("Kamera kalibrieren" (calibrate camera))
saves every capture losslessly as PNG in its own folder per session, so that the intrinsics can be
recalculated offline. Previously it was archived as JPEG (quality 92), and every new session
overwrote `kalib_001.jpg` … of the previous one. Reason: the stored intrinsics of the hand camera
(`cam_flange.json`, 23.09.2026) deviate from the factory value of the RealSense D415 (cy 263.4 instead of
243.4 px). An offline recalculation requires lossless raw images, and no session may overwrite the previous one.

Procedure for the user: set the variable, take captures, choose "Abbrechen" (cancel) instead of "Fertig" (done).
Then nothing is calculated and nothing is activated; the images remain in the archive.

## Already read

- `CLAUDE.md` (section "Bevor du anfängst" (before you start), work-plan obligation)
- `doc/project_docs/work-plans/_template.md`, `doc/project_docs/work-plans/README.md`
- `doc/project_docs/interfaces/vision-server-interface.md`, section 12.9 (debug: save captures as images) and section 12 (calibration, procedure)
- `doc/project_docs/code/README.md`, table of environment variables (`VISION_SAVE_CALIBRATION_CAPTURES`) and configuration list `calibration_capture_dir`

## Affected files

The list reserves the files.

- `src/vision_server/calibration_session.py`: archive per session (`_start_archive()`), PNG capture (`_ARCHIVE_IMAGE_SUFFIX = ".png"`), `archive_dir`, `progress["archiveDir"]`
- `src/vision_server/server.py`: comment on `calibration_capture_dir` (PNG per session instead of JPEG), path unchanged `data/calibration/<frame_id>_captures/`
- `tests/test_calibration_session.py`: tests for folder stamp, collision suffix, `session.json`, write errors, unreadable calibration file
- `doc/project_docs/interfaces/vision-server-interface.md` (addendum to 12.9), `doc/project_docs/code/README.md` (two places)

## Interfaces

### OPC UA

No new nodes, methods or ports. The method `CaptureCalibrationSample` and the progress display
`CalibrationProgress` remain unchanged. The field `archiveDir` is added to the JSON progress (see data).

### Python

```python
# Module: src/vision_server/calibration_session.py

class CalibrationSession:
    def __init__(self, ..., now: Callable[[], datetime] | None = None) -> None:
        """`now` is the injectable clock (default: UTC now). Intended only for tests
        and the archive stamp."""

    @property
    def archive_dir(self) -> Path | None:
        """Session folder of the archive, or None if the archive is off for this
        session (variable not set or error when creating it)."""

    @property
    def progress(self) -> dict:
        """Additionally contains `archiveDir` (str or None)."""

    def start(self) -> None:
        """Starts a session. If the archive is active, creates the session folder
        and `session.json`. Errors when creating do not raise; they appear as a
        warning in the log, and the archive is off for this session."""
```

`start()` therefore raises no exception because of the archive. `capture()` remains unchanged
and still returns `bool`.

### Data / payload

Storage (archive):

```
data/calibration/<frame_id>_captures/<UTC-Stempel YYYYmmddTHHMMSSZ>/
    session.json
    kalib_001.png
    kalib_002.png
    ...
```

- Stamp: UTC, format `YYYYmmddTHHMMSSZ`. On collision within the same second, suffix
  `_2`, `_3`, … (i.e. `<stamp>_2/`).
- Counter `kalib_NNN` per session starting at 001, three digits.
- Compression: PNG level 1 (lossless, fast).

Working folder (unchanged, basis of the online evaluation):

```
data/calibration/<frame_id>_pending/kalib_NNN.jpg    (JPEG, unchanged)
```

`session.json`:

```json
{
  "schema": "adp.calibration.captures/1",
  "frameId": "string, e.g. cam_flange",
  "startedAt": "ISO-8601 UTC with Z, e.g. 2026-11-10T09:12:03Z",
  "imageFormat": "png",
  "pendingFormat": "jpg",
  "board": {
    "type": "chessboard",
    "cols": "int, inner corners (hand Pi: 7)",
    "rows": "int, inner corners (hand Pi: 10)",
    "squareSizeM": "float, metres",
    "markerSizeM": "float, metres (only relevant for ChArUco)",
    "dictionary": "string (only relevant for ChArUco)"
  },
  "model": "pinhole | fisheye",
  "activeCalibration": "content of the calibration file active at start, or null"
}
```

`archiveDir` in the progress (`CalibrationProgress`): absolute path of the session folder as a string,
or `null` if the archive is off.

### Network / operation

- Environment variable unchanged: `VISION_SAVE_CALIBRATION_CAPTURES=1`
  (systemd unit or shell before the start). Without it there is no archive.
- No new port, no new service, no new systemd unit.
- On the 12 MP ceiling camera PNG is significantly larger and slower than JPEG.
  Set the variable there only when needed, and empty the folders by hand afterwards
  (`data/` is gitignored, no cleanup mechanism).

### Failure cases

- Write error of an archive image: log entry. The capture remains usable in the working folder `_pending`,
  the calibration continues normally.
- Folder or `session.json` cannot be created (e.g. disk full, no write permission): warning in the log,
  `archiveDir` is `null`, `start()` does not fail.
- Active calibration file not readable or not valid JSON: warning in the log, `activeCalibration: null` in `session.json`.
- Cancel ("Abbrechen"): nothing is calculated or activated. The archive remains in place.

## Approach

1. Create the plan and define the interfaces (this plan).
2. Implementation in the session module: stamp with injectable clock, archive folder with collision suffix,
   `session.json`, PNG storage alongside the JPEG pending.
3. Tests in `tests/test_calibration_session.py`.
4. Docs addenda (topic doc 12.9, code handbook).

## Open questions

- Storage requirement on the Pi for long sessions with PNG: not yet measured.
  Do not activate by default on the ceiling camera.
- The hand camera intrinsics (`cam_flange.json`) are to be recalculated offline with the raw images.
  This is follow-up work and not part of this plan.

## Deviations from the plan

None yet. To be added during implementation.

## After completion

- Status set to `done`, actual interfaces entered.
- Index line `doc/project_docs/work-plans/README.md` updated.
- Lasting topic knowledge carried over into `doc/project_docs/interfaces/vision-server-interface.md` (12.9) and
  `doc/project_docs/code/README.md`.
