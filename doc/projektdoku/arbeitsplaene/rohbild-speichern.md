# Save raw image: OPC UA method SaveRawImage

**Status:** done — 19.10.2026
**Owner:** Meric, `Agent: Save raw image`
**Topic:** vision-server / frontend
**Branch:** `apriltag` (frontend: `untested` in repo webskillcomposition)

## Goal

A button in the frontend saves the current full camera frame losslessly as PNG
under `data/rohbilder/` on the Pi. The image serves as raw material, e.g. for
re-checking the detection or for documentation. Previously there was only the
lossy live stream.

## Already read

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/README.md`, `_vorlage.md`
- `doc/projektdoku/vision-server-interface.md`

## Affected files

- `src/vision_server/raw_image.py` (new)
- `src/vision_server/profiles.py` or `config.py` (`raw_image_dir`)
- `src/vision_server/runner.py` (`_install_save_raw_image`)
- `tests/test_raw_image.py` (new)
- `.gitignore`, `data/rohbilder/.gitkeep`
- `doc/projektdoku/vision-server-interface.md` (new section)
- Frontend (repo webskillcomposition): button "📷 Rohbild speichern" (save raw image) in
  `frontend/src/features/opcua-server/components/SaveRawImageButton.tsx`, in
  the bar next to "Ansicht" (view) (`CameraView.tsx`), hook
  `model/useSaveRawImage.ts`, binding `rawImage.saveNodeId` in `config/piServers.ts`.

## Interfaces

### OPC UA

| Element | NodeId / BrowsePath | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| `SaveRawImage` | `ns=<own_idx>;s=<vision_system_name>.SaveRawImage` | Method | Call | saves the current full camera frame as PNG |

Additionally mirrored under `VisionProgram` (like the calibration methods).

Signature: `SaveRawImage() -> (FileName: String, Error: Int32)`

- `FileName`: path relative to the repo root with `/`, e.g.
  `data/rohbilder/cam_ceiling_20261019-143012.png`. If the target directory
  is not below the repo root, the absolute path. On error `""`.
- `Error` (`VisionErrorCode`): `0` OK, saved; `1` INVALID_STATE (no camera
  frame yet); `3` BUSY (a job is running); `6` INTERNAL (write failed).

### Python

```python
# Module: src/vision_server/raw_image.py
def save_raw_image(image, directory: Path, frame_id: str,
                   now: datetime | None = None) -> Path:
    """Creates directory, writes <frame_id>_<YYYYmmdd-HHMMSS>.png (on
    collision _2, _3, ...), never overwriting. Raises OSError if
    cv2.imwrite fails."""
```

Configuration: `VisionServerConfig.raw_image_dir: Path | None`, default
`<Repo>/data/rohbilder`. `None` = the method is not created.

### Network / operations

No new ports or services. The method exists only if an opened detection source
has a camera. `frame_id` = `config.apriltag.frame_id`; without AprilTag
configuration, `config.vision_system_name`.

### Failure cases

- No frame: `INVALID_STATE`, nothing written.
- Job running: `BUSY`. Encoding a 12 MP PNG must not endanger the job timeout.
- Disk full, no write permission: `INTERNAL`, exception in the log.

## Approach

1. Plan and docs.
2. `raw_image.py`, config, handler in `runner.py`.
3. Adjust `.gitignore` (`data/*`, `!data/rohbilder/`).
4. Tests, full suite.

## Open questions

- Cleaning up old raw images (space on the SD card) is not part of this task.

## Deviations from the plan

No deviation from the interface. Actual state: the field `raw_image_dir` is in
`VisionServerConfig` (`config.py`); the default uses `_REPO_ROOT` from
`profiles.py`. The method is created in `install_vision_machine` before the
VisionProgram is built, if `_camera_owner(...)` provides a source and
`raw_image_dir` is not `None`.

Addendum 19.10.2026: the button was first placed as 📷 at the top of the live
image (`CameraPreview.tsx`). On request it now sits clearly visible at the
bottom of the bar next to "Ansicht" (view). If the frontend calls
`SaveRawImage` on a Pi with an older server version, the relay
(`backend/opcua/method_calls.py`, `call_raw_method`) now reports
"method not found on the server" instead of a `NoneType` error, and the
frontend shows the hint to update the Pi software.

## After completion

- Status set to `done`, actual interfaces entered.
- Index line updated.
- Section added to `vision-server-interface.md`.
