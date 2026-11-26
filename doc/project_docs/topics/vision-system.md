# Vision server — operation and maintenance

How the vision server is run, configured and maintained on the two Pis. Other views of the same
server:

| Need | Document |
| --- | --- |
| Use the interface as a client | [`vision-server-interface-short.md`](../interfaces/vision-server-interface-short.md) (full reference: [`vision-server-interface.md`](../interfaces/vision-server-interface.md)) |
| Find code: modules, functions | [`code/cell-vision-server.md`](../code/cell-vision-server.md) |
| Part 10 program and mDNS | [`part10-program-interface.md`](../interfaces/part10-program-interface.md) |
| Integration into webskillcomposition, plan vs. built | [`vision-system-integration.md`](vision-system-integration.md) |

## In one paragraph

Each Pi runs **one** OPC UA server (`src/vision_server/server.py`, port 4840, endpoint
`opc.tcp://<pi>:4840/raspi/server/`). It carries two views of the same jobs: `Machines/VisionMachine`
(OPC 40100 Machine Vision) and `VisionProgram` (OPC UA Part 10). The vision package is an add-in
(`install_vision_machine(server, config)`) on that server, not a second process: a second process
would load the 40100 address space twice (about 110 MB RSS each) and force clients into two
sessions. Recipes: `apriltag` (AprilTag localisation) and `calibration` (readiness check of the
camera calibration); an empty `RecipeId` is rejected (`UNKNOWN_RECIPE`).

## Starting

| How | When |
| --- | --- |
| `PYTHONPATH=src python3 -m vision_server.server` | development on a PC |
| `<repo>/.venv/bin/python -m vision_server.server` via systemd unit `opcua-server.service` | production on the Pis (`Restart=always`, `RestartSec=5`); template and install guide in [`deploy/`](../../../deploy/README.md) |
| `PYTHONPATH=src python3 -m vision_server --port 4841` | vision package alone, for isolated tests (not the production path) |
| `PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py --url opc.tcp://127.0.0.1:4840/raspi/server/ --recipe-id calibration` | run one job handshake against a running server |

- **On the Pi do not start in the foreground**: it collides with the service (port 4840 busy).
  Use `sudo systemctl restart opcua-server.service` and test with a separate client.
- Logs: `journalctl -u opcua-server.service`.
- The endpoint listens on `0.0.0.0:4840` but announces the LAN IPv4 (`OPCUA_ADVERTISE_IP` forces an
  interface). Without a LAN IPv4 the server runs without mDNS and LDS. Security: `NoSecurity`.
- At start it announces itself twice, both withdrawn on shutdown; if one fails the server keeps
  running and logs a warning:
  - **mDNS** (`discovery/mdns.py`): `_opcua-tcp._tcp.local.`, instance name = vision identity.
  - **LDS registration** (`discovery/lds.py`) at `opc.tcp://10.10.38.27:4840/`, renewed every 60 s.
    Only through this does the cell's aggregation server list the Pi.

## Configuration

Per-Pi defaults are presets in `src/vision_server/server.py` (`PI_IDENTITIES`, `PI_APRILTAG_PRESETS`,
`PI_CAMERA_STREAM_PRESETS`, `PI_ASSET_PRESETS`), selected by hostname. Environment variables
override them; set them per Pi with a drop-in (`sudo systemctl edit opcua-server.service`,
`Environment='NAME=value'`), then restart the service.

| Variable | Effect |
| --- | --- |
| `VISION_SYSTEM_ID`, `VISION_FRAME_ID` | identity and camera frame (default: hostname mapping, else `vision-<host>` / `world`) |
| `VISION_CAMERA_BACKEND` | `picamera2` (default), `realsense` (hand Pi preset) or `opencv` |
| `VISION_CAMERA_CONTROLS` | libcamera controls as JSON object, see [Camera controls](#camera-controls-picamera2) |
| `VISION_DETECTION_ROI` | detection region `x,y,width,height` or `off`, see [Detection region](#detection-region-of-the-ceiling-camera) |
| `VISION_APRILTAG_DETECTOR_PARAMS` | detector parameters as JSON object, merged per key into the preset (`null` removes one) |
| `VISION_SUBPIXEL_CORNER_REFINEMENT` | `0`/`none` off, `1` on, `apriltag`/`subpix` on with that method (preset: on, `subpix`) |
| `VISION_APRILTAG_QUAD_DECIMATE` | detector decimation (default `0.0`) |
| `VISION_CALIBRATION_MODEL` | overrides the preset's lens model (pinhole/fisheye), e.g. for a comparison run |
| `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` | detection runs without a real calibration (poses not to scale); for testing only, never default |
| `VISION_SAVE_CALIBRATION_CAPTURES=1` | archives every calibration capture as PNG under `data/calibration/<frame_id>_captures/` (extra SD-card load) |
| `VISION_DISABLE_STREAM=1` | no live stream (the job camera keeps running) |
| `OPCUA_MDNS=0` | no mDNS announcement |
| `OPCUA_MDNS_NAME` | mDNS instance name (default: vision identity) |
| `OPCUA_ADVERTISE_IP` | force the announced IPv4 |
| `OPCUA_LDS_URL` | LDS URL; empty = do not register |
| `OPCUA_APPLICATION_URI` | ApplicationUri (default `urn:plcm:camera-server:<name>`) |

Unknown values in `VISION_DETECTION_ROI`, `VISION_SUBPIXEL_CORNER_REFINEMENT` or unknown camera
controls raise `ValueError` at startup, not during operation.

## Camera watchdog

`capture_array()` can hang without an error when libcamera stops delivering frames. Therefore
(`camera.py`, defaults in `profiles.py`):

- every capture has `frame_timeout_s` (3 s); on a hang the camera is reopened;
- after `max_reopen_attempts` (2) reopenings without a frame in between, the camera counts as failed
  (`FAILURE`). The OPC UA server stays up, keeps `FAILURE`, and retries opening the camera every
  `failed_retry_interval_s` (5 s). The first good frame resets everything: `DeviceHealth` goes to
  `NORMAL`, and subscribers see failure and recovery live, without a reconnect;
- only if the camera stays failed for `give_up_after_s` (300 s; `0` = exit at once as before,
  `None` = never) does the process write a last `FAILURE` and exit; systemd restarts it. This is the
  last resort against a wedged libcamera or driver. A camera missing at cold start is not retried:
  `FAILURE` stays and the process keeps running;
- the camera state is published as OPC 40100-2 `DeviceHealth` plus DI alarms (`camera_health.py`).
  Mapping, in this order: `gave_up` or not running → `FAILURE`; `reopen_attempts > 0` (until the next
  good frame) → `FAILURE`; no camera handle or no frame yet (first open and warm-up only) →
  `CHECK_FUNCTION` ("Kamera wird geoeffnet"); frame older than `stale_frame_s` → `OFF_SPEC`;
  otherwise `NORMAL`.
  The live stream keeps publishing the last image, so a frozen picture is no error signal; the
  frontend's camera light is.

Search the log: `journalctl -u opcua-server.service | grep -E "haengt|neu geoeffnet|Kamerazustand|beende den Prozess"`.
The exit message (`beende den Prozess`) appears only after `give_up_after_s` has run out.
Frequent entries usually mean hardware (ribbon cable).

## Camera controls (Picamera2)

Ceiling camera only; RealSense and OpenCV ignore the field (one warning per opening).

- Source: preset `PI_CAMERA_STREAM_PRESETS["cam_ceiling"]["camera_controls"]`, overridden per key by
  `VISION_CAMERA_CONTROLS` (JSON object, `null` removes a preset key), e.g.
  `{"ExposureValue": -0.5, "Sharpness": 1.5, "NoiseReductionMode": "Fast"}`.
- Merged into the Picamera2 defaults (`FrameDurationLimits`, `NoiseReductionMode`), not replacing them.
- Types: `ExposureTime` µs (int), `AnalogueGain`, `ExposureValue`, `Sharpness`, `Contrast`,
  `Brightness`, `Saturation` (float), `AeEnable` (bool); enum controls (`NoiseReductionMode`,
  `AeMeteringMode`, `AeExposureMode`, `AeConstraintMode`, `AwbMode`) by name.
- An unknown name raises `ValueError` on opening and lists the valid names.
- After warm-up the actual values are logged: `journalctl -u opcua-server.service | grep "Kamera-Metadaten"`.
- Hardware: M12 lens with fixed aperture; only focus (by hand) and the ISP controls are adjustable.

Background: [`work-plans/camera-controls-and-detector-parameters.md`](../work-plans/camera-controls-and-detector-parameters.md),
[`analyses/camera-and-detector-analysis.md`](../analyses/camera-and-detector-analysis.md).

## Detection region of the ceiling camera

- `cam_ceiling` searches tags only in the cell area: `detection_roi = (917, 568, 2114, 2079)`
  (x, y, width, height at 4056×3040) in `PI_APRILTAG_PRESETS`. Override: `VISION_DETECTION_ROI`.
- The frame stays complete: only the detector sees the region (`tagloc.detector.RoiTagDetector`),
  corners are shifted back to full-frame coordinates. Calibration, poses, overlay and saved images
  are unaffected.
- Job, cell watch, live-stream overlay and the intrinsic calibration (`CalibrationSession`) use the
  same region; `MeasureBoard` does not.
- Live stream modes `off` and `apriltag` show only the region; `undistorted` and
  `tagreference` show the whole image.
- Tags outside the region, world tags included, are not found.

Background: [`work-plans/ceiling-camera-crop.md`](../work-plans/ceiling-camera-crop.md).

## Known limitations

- `frameId` is `world` once a world tag of the tag map is visible, otherwise the camera frame.
- Linked state-machine methods: `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`,
  `Reset`. `SimulationMode` and `SelectModeAutomatic` exist but do nothing.
- `ResultManagement` methods (`GetResultById` etc.) are not implemented: their inputs are
  ExtensionObjects the asyncua client cannot build (asyncua issue #1693). Structure-typed event
  fields (`ResultId`, `JobId`, …) are sent empty; the values are in the JSON payload.
- Events reach only a subscription on the emitting node (`VisionMachine`); `HasNotifier` does not
  bubble with asyncua. A pure Part 10 client gets results via the value change of
  `VisionProgram/ResultSet/LatestResultJson`.
- One vision instance per server; a second (3D) profile does not exist.
- The ceiling camera captures at 4056×3040; live stream and overlay use a second ISP stream
  (960×720). `allow_resolution_mismatch` is still set in the `cam_ceiling` preset: remove it once
  the ceiling camera is calibrated at 4056×3040.

## Maintenance

### Add a detection source (recipe)

1. New source under `src/vision_server/detection/` (subclass of `DetectionSource`; every OpenCV call
   through `run_blocking()`, otherwise the shared event loop freezes).
2. Registry entry with lazy import in `detection/__init__.py` (cv2 must not be on the import chain).
3. One line in `DEFAULT_RECIPE_PROFILES` (`config.py`).

`job.py`, `payload.py`, `events.py`, `result_management.py`, `state_machine.py` and
`address_space.py` stay unchanged.

### asyncua pitfalls in the server code

Checked against the nodeset and the asyncua source; keep them in mind when changing the address
space or events:

| # | Pitfall | Consequence in our code |
| --- | --- | --- |
| 1 | `ResultReadyEventType` has 15 flat fields, no `Result: ResultDataType` | payload travels in the event in `ResultContent[0]`; no second read |
| 2 | `ResultManagement` inputs are ExtensionObjects (issue #1693) | methods not implemented |
| 3 | no transition `Halted → Operational` | start sequence `Preoperational → Operational`, inside `Initialized → Ready` |
| 4 | outer `VisionStateMachine` states are instance children; `AutomaticModeStateMachine` states exist only on the type (i=5056–5059) | outer states by BrowseName, inner by NodeId |
| 5 | `LastTransition` does not exist on the instance | state changes without a `Transition` object; transition name in the event message |
| 6 | `ResultContent` has a null DataType | DataType overridden to `String`, written as array |
| 7 | event fields need finished `ua.Variant` objects | raw Python values would arrive as `None` |
| 8 | `@uamethod` handlers that start a task must be `async` | sync handlers run in a thread pool without an event loop |
| 9 | method return values must be a `tuple` | a `list` arrives as one nested Variant |
| 10 | no event bubbling via `HasNotifier` | all event generators emit from `VisionMachine` |

### Tests

`PYTHONPATH=src python3 -m unittest discover -s tests -t .` (on the Pis with the project venv).
`tests/test_part10_fassade.py` checks that the address space has exactly one `VisionSystemType`
instance under `Objects/Machines`.
