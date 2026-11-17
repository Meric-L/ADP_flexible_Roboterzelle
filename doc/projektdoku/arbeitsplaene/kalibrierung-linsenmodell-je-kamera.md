# Calibration: lens model per camera (fisheye for layer 1, pinhole for layer 2)

**Status:** done — 29.09.2026 (code and tests; recalibration of LV1 on the Pi still pending)
**Owner:** `Agent: Linsenmodell je Kamera (Kannala-Brandt / Brown-Conrady)`
**Topic:** vision
**Branch:** apriltag

## Goal

Each camera is calibrated with the lens model that proved to be the best in the image tests:

| Layer | Camera | Pi | Resolution | Model |
| --- | --- | --- | --- | --- |
| LV1 | `cam_ceiling` (HQ camera, ceiling) | Pi 4 | 4056 × 3040 | OpenCV fisheye (Kannala-Brandt, 4 coefficients k1–k4) |
| LV2 | `cam_flange` (hand) | Pi 5 | 640 × 480 | OpenCV pinhole (Brown-Conrady, 5 coefficients k1, k2, p1, p2, k3) |

Until now only the pinhole model existed (`cv2.calibrateCamera`). The model is then stored in the
calibration file, and **all** consumers (pose estimation, overlay, scaling) follow the file, not the config.

## Already read

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md` (Meric, in progress): concerns resolution/live stream of `cam_ceiling`, **not** the lens model — no overlap in the files except the preset in `server.py` (only one additional key, existing ones remain untouched)
- `doc/projektdoku/vision-server-interface.md` section 12
- `doc/frontend-integration-kalibrierung.md`

## Affected files

- `src/tagloc/calibration.py` — field `model`, schema `/2`, reading of `/1`
- `src/tagloc/lens.py` (new) — model-dependent OpenCV calls in one place
- `src/tagloc/boards.py` — `calibrate_from_samples(..., model=...)`
- `src/tagloc/pose.py` — undistortion via `lens.undistort_points`
- `src/tagloc/overlay.py` — axis cross via `lens.project_points`
- `src/tagloc/cli/calibrate.py` — option `--model`
- `src/vision_server/profiles.py` — `AprilTagProfileConfig.calibration_model`
- `src/vision_server/server.py` — preset per camera
- `src/vision_server/calibration_session.py` — pass the model through, report it in the result
- `src/vision_server/runner.py` — `ActiveCalibrationInfo.model`
- `tests/test_lens.py` (new), `tests/test_calibration_io.py`, `tests/test_calibration_session.py`
- `doc/projektdoku/vision-server-interface.md`, `doc/frontend-integration-kalibrierung.md`

## Interfaces

### Data: calibration file (`data/calibration/<frame_id>.json`)

New schema `wsc.vision.calibration/2`; the only difference from `/1` is the field `distortionModel`:

```jsonc
{
  "schema": "wsc.vision.calibration/2",
  "distortionModel": "fisheye",          // "pinhole" | "fisheye"
  "distortionCoefficients": [k1, k2, k3, k4],   // pinhole: [k1, k2, p1, p2, k3]
  // everything else unchanged: calibrationId, frameId, imageSize, cameraMatrix,
  // rmsReprojectionError, sampleCount, board, createdAt
}
```

- Files with schema `/1` continue to be read and count as `pinhole` — existing calibrations remain valid without recalibration.
- `/2` is always written. An old code state rejects a `/2` file loudly (`Unbekanntes Kalibrierschema` (unknown calibration schema)) instead of silently interpreting fisheye coefficients as pinhole.

### Python

```python
# Module: src/tagloc/calibration.py
PINHOLE = "pinhole"; FISHEYE = "fisheye"; DISTORTION_MODELS = (PINHOLE, FISHEYE)

@dataclass(frozen=True)
class CameraCalibration:
    ...                      # unchanged
    model: str = PINHOLE     # new, last field

# Module: src/tagloc/lens.py (new, needs cv2)
def undistort_points(points, calibration) -> np.ndarray       # (N,2), pixels, ideal pinhole with P=K
def project_points(object_points, rvec, tvec, calibration) -> np.ndarray   # (N,2), pixels, with distortion
def calibrate(object_points, image_points, image_size, model) -> tuple[float, K, D]
    # raises ValueError for unknown model, cv2.error on numerical failure

# Module: src/tagloc/boards.py
def calibrate_from_samples(samples, image_size, spec, board=None, *,
                           frame_id="", model=PINHOLE) -> CameraCalibration
```

`scale_to_resolution` applies unchanged to both models (the coefficients refer to normalised
coordinates; only `K` is scaled).

### Configuration

`AprilTagProfileConfig.calibration_model: str = "pinhole"` — the model with which
`CalibrationSession` **recalibrates**. Presets in `server.py`:
`cam_ceiling` → `"fisheye"`, `cam_flange` → `"pinhole"`.
Overridable via env `VISION_CALIBRATION_MODEL=pinhole|fisheye`.

For pose estimation only the model **in the file** counts.

### OPC UA (additions only, no change to existing fields)

- `CalibrationProgress` gets `"model"` during a session (capture, evaluation, result), `result.model` in every result,
  also on failure (addendum 29.09.2026, so that the frontend can display the model in advance and also on failure).
- `FinishCalibration`'s `Summary` carries `model` on immediate rejection.
- `ActiveCalibrationInfo` gets `"model": "fisheye" | "pinhole"`.

### CLI

`python -m tagloc.cli.calibrate ... --model fisheye` (default `pinhole`).

### Failure cases

- Unknown model in config/CLI → `ValueError` when calculating, in the session as `result.error = 5` with a message.
- Fisheye calibration numerically badly conditioned (`CALIB_CHECK_COND` triggers) → one-time retry without this check, with a warning in the log.
  If that also fails, `result.error = 5` as before.
- Unknown `distortionModel` in a file → `ValueError` on loading (the source stays shut, as with any broken calibration).

## Approach

1. `calibration.py` + `lens.py` + tests (round trip of the file, undistort ↔ project for both models, synthetic fisheye calibration recovers K/D)
2. Switch `boards.py`, `pose.py`, `overlay.py` over to `lens`
3. Config, preset, session, runner, CLI
4. Docs (`vision-server-interface.md` section 12, frontend briefing)
5. On the Pis: **recalibrate LV1** (the existing pinhole file applies until a new fisheye file has been written). LV2 does not need
   recalibrating — it was already pinhole with 5 coefficients there.

## Open questions

- Which flags did the image tests use for fisheye? Implemented is
  `RECOMPUTE_EXTRINSIC | CHECK_COND | FIX_SKEW` (OpenCV tutorial standard).
- Remove `allow_resolution_mismatch` for `cam_ceiling` (Meric) after the recalibration at 4056 × 3040 — it belongs to that plan.

## Deviations from the plan

- **Array shapes for `cv2.fisheye.calibrate`:** `(1, N, 3)` / `(1, N, 2)` per capture instead of `(N, 1, 3)`. The latter
  fails under OpenCV 5 with "Sizes of input arguments do not match" (measured 29.09.2026).
- **Flag constants:** OpenCV 5 no longer provides the fisheye flags under `cv2.fisheye.*`, but under `cv2.*` and with
  different numeric values. `lens._fisheye_flag` therefore looks them up at runtime; hard-coded values would silently be wrong
  on the Pi (OpenCV 4) or on the development computer (OpenCV 5).
- **Retry without `CHECK_COND`** only on an "ill-conditioned" message, not on every `cv2.error` — otherwise it would hide real errors.
- **`overlay.draw_tag_axes`** reads `model` via `getattr` (missing = pinhole), because callers and test fakes pass
  calibration objects without the field.
- In addition: `src/jobs/calibrate.py` (calibration check job) names the model.
- Side finding, not part of this work: `tests/test_mjpeg_server.py::
  test_node_stays_at_stream_fps_while_viewers_run_faster` fails even without these changes (timing behaviour).

## State of the tests

All tests green except the MJPEG test named above, which was already red before.
New: `tests/test_lens.py` — synthetic, without camera: undistort ↔ project for both models, tag pose at the image edge
from a fisheye image, fisheye calibration recovers K and k1..k4 from 20 synthetic chessboard captures (RMS < 0.05 px),
pinhole yields exactly 5 coefficients.

**Not tested:** on the Pis themselves (their OpenCV version, real images).

## After completion

- Status set to `done`, actual interfaces entered.
- Index line `doc/projektdoku/arbeitsplaene/README.md` updated.
- Topic knowledge carried over into `doc/projektdoku/vision-server-interface.md`.
