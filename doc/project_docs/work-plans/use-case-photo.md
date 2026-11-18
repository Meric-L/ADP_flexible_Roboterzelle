# Use-case photo: OPC UA method SaveUseCaseImage

**Status:** in progress — 29.10.2026
**Owner:** Meric, `Agent: Use-Case-Foto`
**Topic:** vision
**Branch:** `usecase-foto`

## Goal

After a cube has been placed in Use Case 1 of the web app, the robot moves the
hand camera (hand Pi, PI_2) into the photo pose: 20 cm vertically above the
target center of the placement, from the hand-eye calibration. A photo is taken
there. Image and metadata end up on the Pi per trial, and the image is measured
directly (Δx, Δy, Δθ, see `use-case-measurement.md`). This makes it possible to
evaluate the accuracy and repeatability of the placement. Previously there was
no storage for this; the trials could not be traced individually.

## Already read

- `CLAUDE.md`
- `doc/project_docs/work-plans/README.md`, `_template.md`, `save-raw-image.md`
- `doc/project_docs/interfaces/vision-server-interface.md` (section 18 SaveRawImage, 13.6 operations)

## Affected files

- `src/vision_server/use_case_image.py` (new): file storage, trial number, JSON
- `src/vision_server/config.py`: field `use_case_dir`
- `src/vision_server/runner.py`: `_install_save_use_case_image`, `_build_use_case_pi_meta`
- `src/use_case_eval/live.py` (new): hookup of the measurement to the method (`measure_for_save`, sticker offset, `pi.intrinsics`)
- `tests/test_use_case_image.py` (new)
- `doc/project_docs/interfaces/vision-server-interface.md` (section 18a)
- `doc/project_docs/work-plans/use-case-measurement.md` (measurement, target)
- Frontend (web app): trial-series field, call of the method after Use Case 1,
  handover of the client JSON (schema `wsc.usecase.trial/1`)

## Interfaces

### OPC UA

| Element | NodeId / BrowsePath | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| `SaveUseCaseImage` | `ns=<own_idx>;s=<vision_system_name>.SaveUseCaseImage` | Method | Call | stores camera image and metadata as a trial |

Created on the vision system and — like `SaveRawImage` — mirrored via
`calibration_methods`. Resolve the namespace index via the URI, never hard-code it.

Signature: `SaveUseCaseImage(Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error: Int32)`

- `Series`: trial series from the web app, must be `>= 1`.
- `MetadataJson`: JSON text from the web client. The server does not check the content.
- `FileName`: PNG path relative to the repo root with `/`, e.g.
  `data/use-case/reihe_003/versuch_012.png`. If the target lies outside the repo
  root, the absolute path. On error `""`.
- `ResultJson`: image measurement of the trial as JSON text (fields and meaning
  see `use-case-measurement.md`). On error codes `""`. A measurement error is **not**
  an error code: the photo is stored, and `ResultJson` then contains
  `{"ok": false, "grund": "..."}`.
- `Error` (`VisionErrorCode`):

| Code | Name | Meaning |
| --- | --- | --- |
| `0` | OK | stored |
| `1` | INVALID_STATE | no fresh image (≥ 0.3 s after the call) within 5 s |
| `2` | INVALID_ARGUMENT | `Series` < 1 |
| `3` | BUSY | a job is running |
| `6` | INTERNAL | saving failed, exception in the log |

Codes 4, 5 and 7 do not occur with this method.

### Python

```python
# Module: src/vision_server/use_case_image.py
SCHEMA = "adp.usecase.trial/1"

def next_trial_number(directory: Path) -> int:
    """Highest existing trial number + 1; empty or missing: 1. Gaps remain."""

def save_use_case_image(image, base_dir: Path, series: int, client_meta: str,
                        pi_meta: dict, now: datetime | None = None,
                        measurement: dict | None = None,
                        annotated=None) -> tuple[Path, int]:
    """Writes PNG and JSON, returns (PNG path, trial number).
    `measurement` is written into the JSON as field `messung`, `annotated`
    (check image) as `versuch_NNN_messung.png` (best effort).
    Raises ValueError for series < 1, OSError on write errors.
    Never overwrites: exclusive creation, on collision the next number."""

# Module: src/use_case_eval/live.py (hookup to SaveUseCaseImage)
def measure_for_save(image, calibration, sticker_offset_mm=(0.0, 0.0)) -> tuple[dict, Any]:
    """Measures without exceptions; returns (measurement_dict, check image or None).
    Without calibration: {"ok": False, "grund": "Keine Kamerakalibrierung verfuegbar"}
    (German runtime message: "no camera calibration available")."""
```

Configuration: `VisionServerConfig.use_case_dir: Path | None`, default
`<Repo>/data/use-case`. `None` = method is not created. The method is created
in `install_vision_machine` if a camera source is present and `use_case_dir`
is not `None`.

### Data: storage

```
data/use-case/
└── reihe_003/
    ├── versuch_001.png
    ├── versuch_001.json
    ├── versuch_001_messung.png   (check image, only if a measurement is available)
    └── versuch_002.png
```

In addition, `data/use-case/aufkleber_versatz.json` (one file per use-case
folder, not per series) holds the sticker offset, if calibrated.

- Folder `reihe_NNN` and files `versuch_NNN` have three digits; with more than
  999 trials the number simply keeps growing.
- The trial number is the highest existing number (PNG or JSON) plus 1. Deleted
  trials are not filled in as long as a higher one exists.
- If writing the JSON fails, the PNG is removed again.

### Data: JSON per trial

```json
{
  "schema": "adp.usecase.trial/1",
  "reihe": 3,
  "versuch": 12,
  "bild": "versuch_012.png",
  "gespeichert": "2026-10-29T14:03:12",
  "pi": { "...": "see below" },
  "client": { "...": "object from MetadataJson, schema wsc.usecase.trial/1" }
}
```

| Field | Meaning |
| --- | --- |
| `schema` | identifier `adp.usecase.trial/1` |
| `reihe` | `Series` |
| `versuch` | consecutive number within the series |
| `bild` | file name of the PNG next to the JSON |
| `gespeichert` | time of saving, local time of the Pi, ISO 8601 without time zone, to the second |
| `pi` | metadata of the Pi (see below) |
| `messung` | image measurement (`TrialMeasurement`, same fields as `ResultJson`). Computed before saving; on measurement error `{"ok": false, "grund": ...}` |
| `client` | `MetadataJson` as an object. Schema `wsc.usecase.trial/1` (target pose, table, route, actual joints, etc.) |
| `client_raw` | instead of `client`: the text, if `MetadataJson` is not a valid JSON object |

Fields of `pi`, each only present if determinable:

- `frame_timestamp`, `frame_age_s` (age of the image at saving, s), `image_shape` (`[H, W, C]`)
- `frame_id`, `vision_system_name`, `vision_system_id`, `hostname`, `endpoint`
- `camera_stream` (camera settings as an object)
- `intrinsics` (only when a calibration is loaded): `source` (`calibration` /
  `realsense_factory` / `placeholder`), `calibrationId`, `imageSize`
  (`[width, height]`), `cameraMatrix` (3x3), `distortionCoefficients`,
  `distortionModel` (`pinhole` / `fisheye`). This way the offline evaluation uses
  the same intrinsics as the live measurement.
- `apriltag` (profile as an object), `calibration` and `hand_eye` (each `{path, exists, content}`)

`content` is the parsed file (JSON or YAML), otherwise its text. If a file is
missing, `exists: false` is given and there is no `content`.

### Network / operations

No new ports or services. The server remains `opcua-server.service`. Image
encoding with OpenCV (`cv2`), only imported at call time. `frame_id` =
`config.apriltag.frame_id`; without AprilTag configuration
`config.vision_system_name`.

### Failure cases

- No fresh image within 5 s (e.g. camera hangs): `INVALID_STATE`, nothing written.
- `Series` < 1: `INVALID_ARGUMENT`, nothing written.
- Job running: `BUSY`, immediately, without waiting for an image.
- Disk full, no write permission, PNG encoding failed: `INTERNAL`, exception in
  the log, no partial file left over (PNG is deleted on JSON error).
- Invalid `MetadataJson`: no error, the trial is saved with `client_raw`.
- Missing Pi metadata: individual fields are missing, the trial is saved anyway.
- Measurement error (no cube marker, too few sheet markers, no calibration,
  exception in the measurement): no error code. The photo is saved,
  `messung.ok` is `false` with `grund`; without a check image only the
  `_messung.png` is missing.

## Approach

1. Plan and topic doc (this file, section 18a in `vision-server-interface.md`).
2. `use_case_image.py`, config field, handler in `runner.py`.
3. Tests in `tests/test_use_case_image.py` (trial number, gaps, collision, `client_raw`, series < 1, error codes).
4. Frontend: trial-series field and call after Use Case 1.
5. Commissioning on the hand Pi (see below), trial run with a real placement.

## Commissioning

On the hand Pi (PI_2):

```bash
cd <repo on the Pi>
git pull                                  # get current version with SaveUseCaseImage
sudo systemctl restart opcua-server.service
```

Afterwards check whether the method is present on the vision system (browse via
the client, e.g. `tools/handshake_client.py` as a template). If it is missing, an
older server version is still running or `use_case_dir` is `None`. Then update the
Pi software and restart the server again.

## Test

`tests/test_use_case_image.py` (unittest) covers:

- Storage: trial number without and with gaps, new series starts at 1, collision
  (never overwrite), JSON content, `client_raw`, non-serializable values,
  `Series` < 1, encoding errors.
- OPC UA method on a real asyncua server: saving, waiting for an image after the
  call, timeout → `INVALID_STATE`, `INVALID_ARGUMENT`, `BUSY`, `INTERNAL`.

- Measurement and storage with measurement: `messung` in the JSON, `ResultJson` on
  success, check image, measurement error without abort, measurement without
  calibration. Details: `use-case-measurement.md`.

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_use_case_eval tests.test_use_case_image
```

State of the worktree: both modules together 45 tests, all green (about 100 s,
part of them are synthetic measurements).

Manually on the Pi: call the method twice with the same series, then check
`data/use-case/reihe_NNN/`: two PNG, two JSON, numbers consecutive.

## Open questions

- Cleaning up old trials on the SD card is not part of this task.

## Deviations from the plan

- **Output extended:** `SaveUseCaseImage` now returns `(FileName, ResultJson, Error)`.
  `ResultJson` is the image measurement as JSON (empty on error codes). The state
  `(FileName, Error)` from commit `d0a5e90` is thereby superseded.
- **Measurement during saving:** The measurement runs before saving (in a thread)
  and appears as `messung` in the JSON. A measurement error does not make saving fail.
- **Photo pose:** No longer the flange 30 cm above the placement, but the camera
  20 cm vertically above the target center, computed from `poseFlangeCam` of the
  hand-eye calibration. Image axes parallel to the table x-axis. Details in the
  work plan `use-case-measurement.md`. Frontend: webskillcomposition, branch `usecase-foto`.

## After completion

- Status set to `done`, actual interfaces recorded.
- Entry in the index `doc/project_docs/work-plans/README.md` updated.
- Lasting topic knowledge transferred to the appropriate topic doc (section 18a
  in `vision-server-interface.md` is already set up).
