# Set camera controls and detector parameters without code changes

**Status:** done — 19.10.2026
**Owner:** Meric (`Agent: camera controls`)
**Topic:** vision
**Branch:** apriltag

## Goal

The ceiling camera has been refocused, and the fixed decoding values from
[`apriltag-detection-blurred-tags.md`](apriltag-detection-blurred-tags.md)
(1.0 / 0.3 / 8, measured on the blurred image) no longer apply. Afterwards
**OpenCV detector parameters** and **Picamera2 camera controls** can be set
via preset or environment variable, without changing code. Only the mechanism
is built; the concrete values follow after measurement (both presets are empty for now).

## Already read

- `CLAUDE.md`, `doc/project_docs/work-plans/README.md` (no other plan
  on the topic)
- `doc/project_docs/work-plans/apriltag-detection-blurred-tags.md`
- `doc/project_docs/topics/apriltag-reference.md` (section 13), `vision-system.md`

## Affected files

- `src/tagloc/detector.py`, `src/tagloc/cli/_common.py`, `detect.py`,
  `build_tagmap.py`, `calibrate_handeye.py`
- `src/vision_server/profiles.py`, `server.py`, `camera.py`, `runner.py`,
  `detection/apriltag.py`
- `tests/test_detector.py`, `test_camera.py`, `test_camera_owner.py`,
  `test_cellwatch.py`, `test_server_config.py` (new or matching file)
- `doc/project_docs/topics/apriltag-reference.md` (addendum), `vision-system.md`
  (new section), `work-plans/apriltag-detection-blurred-tags.md`
  (addendum), `work-plans/README.md`

## Interfaces

### Python

```python
# Module: src/tagloc/detector.py
class ArucoTagDetector:
    def __init__(self, family="tag36h11", *, parameters=None,
                 subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                 detector_parameters: Mapping[str, Any] | None = None) -> None: ...

def build_detector(family="tag36h11", backend="aruco", *,
                   subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                   detector_parameters: Mapping[str, Any] | None = None) -> TagDetector: ...
```

`detector_parameters`: attribute name of `cv2.aruco.DetectorParameters` → value,
e.g. `{"errorCorrectionRate": 0.8, "adaptiveThreshWinSizeMax": 33}`. Each
entry is set before the `ArucoDetector` is built. The value is converted to the type of the
existing attribute (int/float/bool). Raises `ValueError` for an unknown name
(typos show up at start) and for a value that does not fit the type. `pupil`
ignores the mapping.
Order: `subpixel_corner_refinement` sets `cornerRefinementMethod` first;
an entry `cornerRefinementMethod` in `detector_parameters` wins.

```python
# Module: src/vision_server/profiles.py
AprilTagProfileConfig.detector_parameters: dict[str, Any]   # default {}
CameraStreamConfig.camera_controls: dict[str, Any]          # default {}, Picamera2 only

# Module: src/vision_server/camera.py
def picamera2_video_configuration(config) -> dict          # contains "controls" if set
def resolve_camera_controls(controls, namespace) -> dict   # string enums -> libcamera values
def validate_camera_controls(controls, available) -> dict  # check names, align types
```

Both fields are merged from the preset table plus env override in
`server.apriltag_config()` or `server.vision_config()` respectively.

### Environment variables (data format)

Both are a **JSON object**. Per key, the environment variable wins over the
preset; `null` removes a preset entry.

| Variable | Affects | Example |
| --- | --- | --- |
| `VISION_APRILTAG_DETECTOR_PARAMS` | `AprilTagProfileConfig.detector_parameters` (job, stream overlay, cell monitoring) | `'{"errorCorrectionRate": 0.8}'` |
| `VISION_CAMERA_CONTROLS` | `CameraStreamConfig.camera_controls` (Picamera2 only) | `'{"ExposureValue": -0.5, "Sharpness": 1.5, "NoiseReductionMode": "Fast"}'` |

Camera controls: `ExposureTime` in µs (int), `AnalogueGain` (float),
`AeEnable` (bool), `ExposureValue`, `Sharpness`, `Contrast`, `Brightness`,
`Saturation` (float). The enum controls `NoiseReductionMode`,
`AeMeteringMode`, `AeExposureMode`, `AeConstraintMode`, `AwbMode` may be given
as a name (`"HighQuality"`, `"Off"`, `"Fast"`); whole numbers remain numbers.
Valid names are returned by `Picamera2().camera_controls`.

The existing switches (`VISION_SUBPIXEL_CORNER_REFINEMENT`,
`VISION_APRILTAG_QUAD_DECIMATE`, …) remain unchanged.

CLI (`detect`, `build_tagmap`, `calibrate_handeye`): repeatable
`--detector-param NAME=VALUE`, value parsed via `json.loads`, otherwise taken as a string, e.g.
`--detector-param errorCorrectionRate=0.8`.

### Network / operation

On the Pis the server runs as the systemd service `opcua-server.service`
(`/etc/systemd/system/opcua-server.service`, see `vision-system.md`). There the
variables are set with `sudo systemctl edit opcua-server.service`
(drop-in `/etc/systemd/system/opcua-server.service.d/override.conf`):

```ini
[Service]
Environment='VISION_CAMERA_CONTROLS={"ExposureValue": -0.5, "NoiseReductionMode": "Fast"}'
Environment='VISION_APRILTAG_DETECTOR_PARAMS={"errorCorrectionRate": 0.8}'
```

Then `sudo systemctl restart opcua-server.service`. For trying things out in a
terminal: set the variable before `python3 src/OPCUA/server.py` (stop the service beforehand). At start-up the server logs
which detector parameters deviate from the default, which camera controls are applied and, after the warm-up, the
actual metadata (`ExposureTime`, `AnalogueGain`, `DigitalGain`,
`Lux`, `ColourTemperature`, `FocusFoM`) — from this one selects fixed
exposure values (`journalctl -u opcua-server.service | grep -i "Kamera-Metadaten"` (camera metadata)).

### Failure cases

| Case | Behaviour |
| --- | --- |
| env variable not valid JSON or not an object | `ValueError` at start, message names the variable |
| Unknown detector parameter / wrong type | `ValueError` when building the detector (start of the job) |
| Unknown camera control name | `ValueError` when opening the camera, message lists the names of the camera; camera is closed again |
| Unknown enum name | `ValueError`, message lists the valid names |
| Backend RealSense/OpenCV with `camera_controls` | ignored, one warning in the log |
| Stream overlay with `cornerRefinementMethod` in the mapping | entry is removed for the stream detector (subpixel deliberately stays off there, CPU competition with the job) |
| Metadata not readable after warm-up | warning, camera keeps running |

## Approach

1. Plan (this document).
2. Replace old individual parameters with `detector_parameters`, empty preset,
   env override, CLI.
3. `camera_controls`: config, preset, env, `camera.py`.
4. Tests, topic docs (addenda), README.

## Open questions

- Concrete values for `cam_ceiling` (detector and camera) follow after the
  measurement on the newly focused image; until then status `in progress`.
- Ground truth for `data/rohbilder/cam_ceiling_20261019-142133.png` is being
  clarified; the regression test on it will be added afterwards.

## Deviations from the plan

- Affected files: tests are in `test_server_config.py` (env merge),
  `test_camera.py` (controls, enum resolution) and `test_detector.py`; the CLI
  tools `detect.py`, `build_tagmap.py`, `calibrate_handeye.py` remained
  unchanged, they use `_common.detector_options(args)`.
- Additional interface: `tagloc.detector.apply_detector_parameters(parameters, values)`
  (returns the deviating entries), `ArucoTagDetector.changed_parameters`
  (for the start log), `server.merged_json_object_env(name, preset)`,
  `camera.format_camera_metadata`, `camera.CAMERA_ENUM_CONTROLS`.
- `detector_parameters` and `camera_controls` are fields with `hash=False`; the
  dataclasses remain hashable.
- The metadata is read in `SharedCamera._open_camera` directly after the warm-up,
  before the capture loop starts (no race for requests).
- The warning for RealSense/OpenCV appears on every opening (including re-opening),
  not strictly only once.

## Addendum 19.10.2026: values in preset `cam_ceiling`

After the settings test (14 captures, report
[`../camera-and-detector-analysis.md`](../analyses/camera-and-detector-analysis.md)) the following are set in
`src/vision_server/server.py`:

- `PI_CAMERA_STREAM_PRESETS["cam_ceiling"]["camera_controls"]` =
  `{"ExposureValue": -0.5, "Sharpness": 1.5, "Contrast": 1.15, "NoiseReductionMode": "Fast"}`
  -- rank 1 by detection reserve, no burning out of the tags.
- `PI_APRILTAG_PRESETS["cam_ceiling"]["detector_parameters"]` =
  `{"perspectiveRemovePixelPerCell": 8}` -- small reserve without false alarms;
  `errorCorrectionRate` stays at the OpenCV default.
- Environment variables continue to override on top of this, per key.
- Regression test `tests/test_detector.py::CeilingSharpRawImageTest`: the preset
  finds exactly 0, 1, 1, 2, 3, 583, 586 on `data/rohbilder/cam_ceiling_20261019-142133.png`
  (585 damaged, optional).
- Open: check on the Pi (OpenCV 4.x, libcamera enum `NoiseReductionMode`),
  recalibration with these controls.
