# Vision Server — Interface in Brief

The vision server is an OPC UA server (OPC 40100 Machine Vision) that runs on each of
the cell's two Raspberry Pis. It drives the Pi's camera, localizes the cell's modules
via AprilTags and offers camera calibration, hand-eye calibration and measurement
functions to the webskillcomposition backend and frontend. This page describes the
current interface without history.

In-depth reference: [vision-server-interface.md](vision-server-interface.md)

## 1. Two Pis, one interface

Both Pis run the same code (`src/vision_server/`, systemd unit `opcua-server.service`)
and expose the same interface; only per-host presets in `server.py` differ.

| Pi (hostname) | `visionSystemId` | `frameId` | Camera | Pi-only features |
| --- | --- | --- | --- | --- |
| `ADP-Roboter-Lokalisierung` (ceiling) | `vision-ceiling-01` | `cam_ceiling` | Picamera2, HQ camera, 4056×3040 | cell monitoring |
| `ADP-HandInEye-Kamera-Pi` (hand) | `vision-flange-01` | `cam_flange` | Intel RealSense, 1920×1080 | Layer 2 run, hand-eye, `MeasureTags`, tag survey |

Address space (custom nodes have string NodeIds `ns=<vision>;s=VisionMachine.<Name>`):

```
Objects/
├── Machines/VisionMachine                 <mv>:VisionSystemType, event source
│   ├── VisionStateMachine                 Preoperational | Halted | Error | Operational
│   │   ├── Halt, Reset
│   │   └── AutomaticModeStateMachine      Initialized | Ready | SingleExecution | ContinuousExecution
│   │       └── StartSingleJob, Stop, StartContinuous, Abort
│   ├── ResultManagement/Results/LatestResult/ResultContent[0]
│   ├── LatestResultJson                   last result (JSON)
│   ├── VisionAsset                        OPC 40100-2 asset view with DeviceHealth
│   ├── LatestCameraFrame, CameraStreamMode, CameraStreamHttpPort
│   └── custom methods and status nodes (sections 7-9)
└── VisionProgram                          OPC UA Part 10 view of the same jobs
```

`VisionProgram` lets a generic Part 10 client start the same jobs. Its `Start` method waits until
the state is `Running`, or returns in `Ready` with a non-zero error code. `ResultSet` contains only
`JobId`, `ErrorCode`, `ExecutionMode` and the reference to `LatestResultJson`. Other available
operational nodes and `DeviceHealth` are `Organizes` references below `Status`; `Status/BusyReason`
is empty when the Pi is free. The program properties `Manufacturer`, `Model` and `SerialNumber`
identify the device; `SerialNumber` is the `visionSystemId`. See
[`part10-program-interface.md`](part10-program-interface.md).

## 2. Connecting

| | Value |
| --- | --- |
| Endpoint | `opc.tcp://<pi>:4840/raspi/server/`, security `NoSecurity` |
| Vision namespace | `http://launch-rm.de/vision` |
| 40100 namespace | `http://opcfoundation.org/UA/MachineVision` |
| ApplicationUri | `urn:plcm:camera-server:ceiling-01` / `urn:plcm:camera-server:roboter-hand-01` |

- Resolve namespace indices at runtime with `get_namespace_index(uri)`; never
  hard-code them.
- The path `/raspi/server/` is historical but stays, because it is part of the mDNS
  announcement, the LDS registration and every client configuration.
- **Discovery:** mDNS service `_opcua-tcp._tcp.local.` with instance name =
  `visionSystemId` and TXT `path`, `caps`; additionally the server registers with the
  cell's discovery server `opc.tcp://10.10.38.27:4840/`, which the aggregation server
  uses. Details: [`vision-system.md`](../topics/vision-system.md).

## 3. Running a job

**Subscribe first, then call** — a job can finish quickly and events are not replayed.

```
Client                                      Vision server
   |  subscribe_events(VisionMachine, [types]) |
   |------------- StartSingleJob ------------->|  synchronous validation
   |<------ (JobId: String, Error: Int32) -----|  Error != 0 => no events
   |<---- StateChangedEvent  Ready -> SingleExecution
   |<---- JobStartedEvent / AcquisitionDoneEvent
   |<---- ResultReadyEvent     ResultContent[0] = JSON result
   |<---- StateChangedEvent  SingleExecution -> Ready, ReadyEvent
```

Event types to subscribe (namespace `<mv>`): `i=1024` ResultReady, `i=1013`
JobStarted, `i=1025` AcquisitionDone, `i=1023` Ready, `i=1018` StateChanged.

### `StartSingleJob`

Inputs `(MeasId, PartId, RecipeId, ProductId, Parameters)` — all exchanged as strings
(Ids at most 128 characters), `Parameters` a string array with at most 16 entries.
Outputs `(JobId: String, Error: VisionErrorCode)`; `Error` is an OPC UA Enumeration with an
Int32 wire value and `JobId` looks like `job-000001`. The call
does not block: it only acknowledges acceptance. The nodeset declares structured Id
types; plain strings are used because asyncua clients cannot build those
ExtensionObjects.

### Jobs (`RecipeId`)

| `RecipeId` | Pi | What it does | Parameters |
| --- | --- | --- | --- |
| `apriltag` | both | Localizes all tagged modules and returns one detection per module with its pose (world frame if a surveyed world tag or the hand-eye anchor is available, otherwise camera frame) | optional 7 numbers `x y z qx qy qz qw`: flange pose `T_base_flange` (m, quaternion xyzw), used on the hand Pi |
| `calibration` | both | Readiness check: reports calibration Id, resolution, model, RMS, age and tag map (subprocess `src/jobs/calibrate.py`) | none |

An empty or unknown `RecipeId` is rejected with `Error=4`. Per job the server averages
`samples_per_job` consecutive frames at the same pose (ceiling 3, hand 5). Detection
attributes and the camera-pose logic:
[long reference, section 5](vision-server-interface.md#available-jobs-recipeid).

### Other job methods

| Method | Effect |
| --- | --- |
| `Stop(Cause, CauseDescription) -> Error` | Aborts the running job (and a Layer 2 run); a `ResultReadyEvent` with `resultState=7` follows. Returns `0` also when nothing was running, `6` if the abort takes longer than 5 s. Inputs are ignored. |
| `Abort` | Like `Stop`, via the Abort transition. |
| `StartContinuous` | Same inputs as `StartSingleJob`; repeats until `Stop`/`Abort`, results `<jobId>-0001`, `-0002`, …; ends on the first failed run. |
| `Halt` / `Reset` | Stop and go to `Halted` (no more jobs) / back to `Operational`. |

### Error codes

Shared by all methods of the vision server (`VisionErrorCode`):

`VisionErrorCode` is an OPC UA Enumeration DataType with German `EnumValues` descriptions. The
`ErrorCode` result variable and every method output named `Error` declare this DataType; the values
on the wire remain Int32. All method input and output arguments have names and descriptions.

| Code | Name | Meaning | EnumValues description (German) | Events? |
| --- | --- | --- | --- | --- |
| 0 | `OK` | accepted / done | OK | yes |
| 1 | `INVALID_STATE` | not `Operational`/`Ready`, or no session/frame | Im aktuellen Zustand nicht möglich (z. B. noch kein Kamerabild). | no |
| 2 | `INVALID_ARGUMENT` | bad input or JSON | Ungültiger Parameter. | no |
| 3 | `BUSY` | a job, calibration or other exclusive action is running | Der Pi ist beschäftigt – danach erneut versuchen. | no |
| 4 | `UNKNOWN_RECIPE` | `RecipeId` empty or unknown | Unbekanntes Rezept. | no |
| 5 | `DETECTION_FAILED` | nothing detected, no image, or job timeout (30 s) | Erkennung fehlgeschlagen. | `resultState=5` |
| 6 | `INTERNAL` | unexpected server error | Interner Fehler auf dem Pi. | `resultState=6` |
| 7 | `CANCELLED` | aborted by `Stop`/`Abort` | Abgebrochen. | `resultState=7` |

Codes 1–4 only come back in the return value. Codes 5–7 arrive as a
`ResultReadyEvent`, so a client never needs a timeout to see a failure. Only one job
runs at a time.

## 4. The result

`ResultContent[0]` of the event and the node `LatestResultJson` carry the same JSON
(schema `wsc.vision.detections/1`):

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-ceiling-01",
  "resultId": "res-job-000001",
  "jobId": "job-000001",                 // match with the StartSingleJob output
  "creationTime": "2026-11-17T09:16:59.612+00:00",
  "resultState": 0,                      // 0 = ok, otherwise an error code
  "frameId": "world",                    // read it every time
  "lengthUnit": "m", "angleUnit": "rad", "rotation": "quaternion_xyzw",
  "detections": [
    { "moduleId": "…", "instanceId": "…", "confidence": 1.0,
      "position": [x, y, z], "orientation": [qx, qy, qz, qw],
      "boundingBox": null, "attributes": { … } }
  ]
}
```

- On failure: same envelope plus `errorCode` and `errorText` (German), `detections: []`.
- Optional keys: `frameConvention` (e.g. `z_forward_x_right_y_down` = OpenCV camera
  frame), `configurationId`, `worldTags` (world tags seen in the job) and
  `cameraLocalization` (how the camera pose was obtained). Field details:
  [long reference, section 6](vision-server-interface.md#6-result-payload).
- Parser rules: check `schema`; treat `resultState != 0` as an error; positions in
  meters, orientation as quaternion xyzw; never assume `frameId`.

## 5. Pitfalls

All verified; details in [long reference, section 7](vision-server-interface.md#7-pitfalls-all-verified).

1. **Pass an event type list** to `subscribe_events`; without it events arrive but
   `ResultContent` is `None`.
2. **Subscribe on `VisionMachine`**, not on the server object (`i=2253`); asyncua does
   not forward events there. The structured event fields `JobId`/`ResultId` are empty;
   use the JSON payload.
3. **`ResultManagement` methods** (`GetResultById` & Co.) are not implemented; read
   `LatestResultJson` as a fallback.
4. **Use the fixed NodeId** `ns=<vision>;s=VisionMachine`; there is exactly one 40100
   instance.
5. **Linked methods** are `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`,
   `Reset`. The other nodeset methods (`SimulationMode`, `SelectModeAutomatic`,
   recipe and configuration management, …) answer `BadNothingToDo`.

Reference client for the whole handshake:
`PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py --url opc.tcp://<pi>:4840/raspi/server/ --recipe-id calibration`.

## 6. Live image and camera state

### Live stream

| Node / URL | Meaning |
| --- | --- |
| `http://<pi>:<port>/stream.mjpg` | MJPEG stream for an `<img>` tag (at most 4 viewers) |
| `http://<pi>:<port>/snapshot.jpg` | newest single image |
| `CameraStreamHttpPort` (Int32) | `<port>`, default 8080; `0` = HTTP stream not available |
| `LatestCameraFrame` (String) | Base64 JPEG at ~5 fps; fallback if HTTP is unreachable |
| `CameraStreamMode` (String, writable) | overlay mode, see below |

Overlay modes for `CameraStreamMode` (published images are at most 960 px wide):

- `off` ("Rohbild") — unchanged image.
- `apriltag` ("AprilTags markieren", default) — green box and `#<id>` per detected tag,
  taken from the newest full-resolution result.
- `undistorted` ("Entzerrt") — image undistorted with the active calibration.
- `tagreference` ("Tag einmessen") — edge search of a running tag survey (section 9).
- Any other value falls back to `apriltag`.

More: [long reference, section 10](vision-server-interface.md#10-camera-live-stream).

### Camera state: `DeviceHealth`

`ns=<vision>;s=VisionMachine.VisionAsset.Health.DeviceHealth` (Int32, NAMUR NE 107):

| Value | Name | Meaning |
| --- | --- | --- |
| 0 | `NORMAL` | fresh camera image |
| 1 | `FAILURE` | camera dead; the server process exits and systemd restarts it |
| 2 | `CHECK_FUNCTION` | start value; camera opening or being reopened |
| 3 | `OFF_SPEC` | image older than 2 s, hang suspected |

- When the camera hangs, the live image **freezes** instead of disappearing; only
  `DeviceHealth` tells the state.
- After `FAILURE`, expect the connection to drop and reconnect.
- The value is written only on change. Matching alarms (`FailureAlarm`,
  `OffSpecAlarm`, `CheckFunctionAlarm`) are emitted by `VisionMachine`. Details:
  [long reference, section 11.4](vision-server-interface.md#114-state-devicehealth).

## 7. Calibration methods

Custom methods have named input and output arguments (the names below are the OPC UA
argument names). JSON arguments and results are strings.

### Camera calibration (both Pis)

Flow: `StartCalibration` → hold the board in different poses and call
`CaptureCalibrationSample` each time → `FinishCalibration` starts the evaluation in
the background → wait for `result` in `CalibrationProgress`. A new calibration is
used immediately; the old file is kept as a backup.

| Method / node | Inputs → outputs | Notes |
| --- | --- | --- |
| `StartCalibration` | `OptionsJson` → `Error` | empty = configured board (chessboard 7×10 inner corners, 23 mm); otherwise `wsc.vision.calibration.start/1` with `board` |
| `CaptureCalibrationSample` | – → `Error` | stores one frame; `1` = no session or no frame |
| `FinishCalibration` | – → `Summary`, `Error` | `0` = evaluation started (not finished!); `5` = fewer than 3 captures |
| `AbortCalibration` | – → `Error` | ends the session without saving |
| `CalibrationProgress` | read | `running`, `processing`, `samples`, `minSamples`, `model`, `board`, `startError`, and `result` (`rms`, `coverageX/Y`, `warning` above 0.5 px) |
| `ActiveCalibrationInfo` | read | the calibration currently used for poses (`calibrationId`, `createdAt`, `rms`, `model`, `placeholder`) |

Jobs and camera calibration exclude each other (`BUSY`). Lens model: ceiling
`fisheye`, hand `pinhole`. Details:
[long reference, section 12](vision-server-interface.md#12-interactive-calibration-frontend-driven).

### Tag map

| Method / node | Inputs → outputs | Notes |
| --- | --- | --- |
| `SetTagMap` | `TagMapJson` → `Error` | content of `config/tagmap.json` (`wsc.vision.tagmap/2`); active at once and saved; `2` invalid, `3` busy, `6` saving failed (map still active) |
| `TagMapJson` | read | the currently loaded map |

### Hand-eye calibration (hand Pi)

Determines the camera pose on the robot flange (`T_flange_cam`). Start with a fixed
target (tag or board), call `CaptureHandEyeSample` with the flange pose reported by
the robot for each robot pose, then `FinishHandEyeCalibration` computes, saves and
activates the result. Every start deletes the session folders of earlier hand-eye
calibrations.

| Method / node | Inputs → outputs | Notes |
| --- | --- | --- |
| `StartHandEyeCalibration` | `OptionsJson` → `Error` | `wsc.vision.handeye.start/1`; tag or `board`; `captureOnly: true` records raw images only, `fullCalibration: true` also computes the intrinsics on the Pi |
| `CaptureHandEyeSample` | `SampleJson` → `Error` | `wsc.vision.handeye.sample/1` with `flangeInBase`; `5` = capture discarded |
| `FinishHandEyeCalibration` | – → `Error` | `1` = too few or too similar poses (session stays open) |
| `AbortHandEyeCalibration` | – → `Error` | discard |
| `HandEyeStatus` | read | `wsc.vision.handeye.status/1`: `state`, samples, `result`, saved calibration `current` |

Details: [long reference, section 15](vision-server-interface.md#15-hand-eye-calibration-via-opc-ua-hand-pi-only).

### `MeasureBoard` (both Pis)

`RequestJson` → `ResultJson`, `Error`. Measures the calibration board in the camera
image without moving the robot (`wsc.vision.measureboard.request/1`, optional `board`,
`frames` 1–10, default 3). The result says whether the board was `found`/`complete`,
its pose in the camera frame and `offsetPx` from the image center, so the controller
can center the board before a capture. Details:
[long reference, section 15.4](vision-server-interface.md#154-measureboard-center-the-board-before-the-capture).

## 8. Measurement runs

### Layer 2 run (hand Pi)

The hand Pi plans a fine measurement of the Layer 1 modules; the client only drives
the robot.

| Method / node | Inputs → outputs | Notes |
| --- | --- | --- |
| `StartLayer2Run` | `RequestJson` → `Error` | robot base and Layer 1 modules (`wsc.vision.layer2.run/1`) |
| `Layer2Target` | read | next flange target (`flangeInBase`), `""` when none |
| `ReportRobotPose` | `RequestJson` → `Error` | reached flange pose (or `reached: false`); the Pi then runs an `apriltag` job |
| `Layer2Status` | read | progress per step; ends with `finished` or `aborted` |

`Stop` aborts the run. Details:
[long reference, section 14](vision-server-interface.md#14-layer-2-run-hand-pi-only).

### Cell monitoring (ceiling Pi)

Continuously checks whether modules were moved, added or removed and reports only
confirmed changes.

| Method / node | Inputs → outputs | Notes |
| --- | --- | --- |
| `StartCellWatch` | `OptionsJson` → `Error` | `wsc.vision.cellwatch.start/1`, optional `referenceJobId` (one of the last 20 results) |
| `StopCellWatch` | – → `Error` | |
| `PauseCellWatch` | `Reason`, `MaxPauseSeconds` → `Error` | e.g. while the robot moves; `<= 0` = 600 s, max 3600 s, resumes on its own |
| `ResumeCellWatch` | – → `Error` | |
| `CellWatchStatus` | read | `state` (`off`, `watching`, `changed`, `paused`, `failed`), `changes`, `pending`, `visibleTagIds`, `message` |

The monitoring pauses automatically during a job or calibration. Details:
[long reference, section 16](vision-server-interface.md#16-cell-monitoring-ceiling-camera-only).

### `MeasureTags` (hand Pi)

`RequestJson` → `ResultJson`, `Error` (`wsc.vision.measuretag.request/1`). Measures
tag poses in the camera frame (`T_cam_tag`), averaged over 3 frames by default (1–10)
taken after the call; `nearEdge` tells which image edges a tag is close to. The last
result is in `LastTagMeasurement`. Details:
[long reference, section 17](vision-server-interface.md#17-measuretags-tags-in-camera-coordinates-hand-pi-only).

## 9. Tag survey and image capture

### Tag survey (hand Pi)

Measures a module tag's position against reference edges of the module in the same
image (replaces measuring with a folding ruler).

| Method / node | Inputs → outputs | Notes |
| --- | --- | --- |
| `PlanTagReferenceView` | `RequestJson` → `ResultJson`, `Error` | computes where the camera should look from (`camInTag`, `fits`); takes no image |
| `MeasureTagReference` | `RequestJson` → `ResultJson`, `Error` | measures; result `correction` with `dofs`, `deltaMm`, `deltaRotDeg` |
| `SetTagReferencePreview` | `OptionsJson` → `Error` | live edge search in stream mode `tagreference` (`""` = off, expires after 120 s) |
| `LastTagReference`, `TagReferencePreviewStatus` | read | last measurement / preview state |

Request `wsc.vision.tagreference.request/1`: `tagId`, `moduleTagGuess` (tag pose from
the module catalog) and 1–4 edges `lines`. Details:
[long reference, section 19](vision-server-interface.md#19-survey-tag-position-measuretagreference--plantagreferenceview-hand-pi-only).

### Saving images (both Pis)

| Method | Inputs → outputs | Notes |
| --- | --- | --- |
| `SaveRawImage` | – → `FileName`, `Error` | full camera frame as PNG under `data/rohbilder/`; `1` no image yet, `3` job running, `6` write failed |
| `SaveUseCaseImage` | `Series` (Int32), `MetadataJson` → `FileName`, `ResultJson`, `Error` | use-case trial photo under `data/use-case/reihe_NNN/versuch_NNN.png` plus JSON, with an image measurement in `ResultJson`; `1` no fresh image within 5 s, `2` `Series < 1`, `3` job running, `6` storage error |

Details: [long reference, section 18](vision-server-interface.md#18-saverawimage-save-the-full-camera-image-as-png)
and [section 18a](vision-server-interface.md#18a-saveusecaseimage-use-case-photo-per-trial).
