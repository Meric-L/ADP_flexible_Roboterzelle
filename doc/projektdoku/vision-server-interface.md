# Vision Server — System Layout and Backend Interface

This file describes the standalone OPC UA vision server (OPC 40100,
Machine Vision) and **what a backend must implement** in order to get a result
from it. It can be used without knowledge of this repo.

**Only looking for the addresses and signatures?** Section 13 summarizes the
complete interface in tables — nodes, methods, events, error codes, operation.
The rest of this document explains the why.

> **Supplementary, since 2026-09-21:** The same job can additionally be started
> via a generic **OPC UA Part 10 program** — the same operating style that the
> Conveyor and CardDispenser in this cell use. The server also announces itself
> via mDNS and is found automatically by the cell's aggregation server. Both
> are additive; everything described below remains valid unchanged. See
> [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).
>
> **Note:** The vision namespace is now on **ns=6**. It first moved from ns=4 to
> ns=7 (the Part 2 nodesets came in between), and then to ns=6, because the
> namespace `http://launch-rm.de/raspi` was also dropped along with
> `RaspiDevice`. That is exactly why, without exception: **always resolve
> namespace indices via the URI, never hard-code them.**

Current state: The placeholder recipe `hello-world` has been removed; a start
without `RecipeId` is rejected with `Error=4` (`UNKNOWN_RECIPE`).
`apriltag` is the real detection — it controls the Pi camera, localizes modules
equipped with AprilTags and returns real poses; `calibration` checks the
measurement readiness of this cell. The complete job flow (state machines,
events, result storage, error path) is identical for all three, and the payload
format `wsc.vision.detections/1` has remained **unchanged**.

> The earlier QR code detection (`image-recognition`) has been removed. It was
> the feasibility proof for the camera pipeline; what remains of it is the
> `SharedCamera` underneath. See [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md)
> Section 6.

## 1. System layout

**One** OPC UA server on the Raspberry Pi (systemd service `opcua-server.service`,
port 4840). It carries **one** functional tree: the vision system, in two views
of the same job.

```
Raspberry Pi — opc.tcp://<pi>:4840/raspi/server/
├── Machines/          Standard folder from OPC UA Machinery
│   └── VisionMachine  OPC 40100 VisionSystemType        <- this document
└── VisionProgram      OPC UA Part 10, operator interface  <- part10-programm-schnittstelle.md
```

Both views operate on **the same** `JobRunner`; there is no second job state.
Anyone who only wants to speak Part 10 gets by without a single 40100 NodeId —
see [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).

> **The path `/raspi/server/` stays**, although the Raspi interface is gone. It
> appears in the mDNS announcement, in the LDS registration and in every client
> configuration; renaming it would break every existing connection without
> improving anything.

Removed (legacy A1–A6): `RaspiDevice` with CPU temperature, counter and setpoint,
the empty second instance `2:VisionSystem`, `CpuTemperatureResult`, and the
namespace `http://launch-rm.de/raspi`.

Address space of the vision system:

```
Objects/
└── Machines/                                ns=<machinery>;i=1001  (standard folder)
    └── VisionMachine                        ns=<vision>;s=VisionMachine   (type: <mv>:VisionSystemType)
        ├── VisionStateMachine               Preoperational | Halted | Error | Operational
        │   └── AutomaticModeStateMachine    Initialized | Ready | SingleExecution | ContinuousExecution
        │       ├── StartSingleJob           <- start a job
        │       ├── Stop                     <- abort the running job, see section 5
        │       ├── StartContinuous          <- continuous operation, see section 7.5
        │       ├── Abort                    <- like Stop, via the Abort transition
        │       └── SimulationMode           (not linked, see section 7.5)
        ├── ResultManagement
        │   ├── Results/LatestResult         (type: <mv>:ResultType, overwritten per job)
        │   │   └── ResultContent[0]         JSON string of the last result
        │   └── GetResultById | ReleaseResultHandle | ...   (not implemented, see 7.3)
        ├── VisionAsset                      OPC 40100-2, see section 11
        ├── StartCalibration                 ns=<vision>;s=VisionMachine.StartCalibration
        ├── CaptureCalibrationSample         ns=<vision>;s=VisionMachine.CaptureCalibrationSample
        ├── FinishCalibration                ns=<vision>;s=VisionMachine.FinishCalibration
        ├── AbortCalibration                 ns=<vision>;s=VisionMachine.AbortCalibration
        ├── CalibrationProgress              ns=<vision>;s=VisionMachine.CalibrationProgress
        ├── SetTagMap                        ns=<vision>;s=VisionMachine.SetTagMap
        ├── TagMapJson                       ns=<vision>;s=VisionMachine.TagMapJson
        ├── ActiveCalibrationInfo            ns=<vision>;s=VisionMachine.ActiveCalibrationInfo
        ├── LatestResultJson                 ns=<vision>;s=VisionMachine.LatestResultJson
        │                                    the same JSON string, as a simple string node
        ├── LatestCameraFrame                ns=<vision>;s=VisionMachine.LatestCameraFrame
        │                                    Base64 JPEG of the camera live stream, see section 10
        ├── CameraStreamMode                 ns=<vision>;s=VisionMachine.CameraStreamMode
        │                                    **writable**: off | apriltag | calibration
        └── CameraStreamHttpPort             ns=<vision>;s=VisionMachine.CameraStreamHttpPort
                                             Int32: port of the MJPEG stream, 0 = none (section 10.2)
```

The four calibration methods and the five value nodes are **additionally**
reachable under `VisionProgram` — as references, not copies. Each stays one node
with one value or one implementation.

Internal structure (Python package `src/vision_server/`):

| Module | Task |
| --- | --- |
| `__main__.py` | Entry point `python3 -m vision_server.server` |
| `config.py` | `VisionServerConfig`, nodeset paths, env variables, recipe profiles |
| `address_space.py` | Nodeset import (MachineVision, DI, Machinery, AMCM), `VisionMachine` instance under `Machines`, `HasNotifier` |
| `asset_model.py` | Part 2 asset view (AMCM), `VisionAsset`, see section 11 |
| `state_machine.py` | both 40100 state machines |
| `events.py` | event generators, `ResultReadyEvent` with payload |
| `result_management.py` | result nodes + JSON mirror |
| `job.py` | validation, guard, job flow, error path |
| `errors.py` | `VisionJobError`, error codes (section 5) |
| `nodeset_ids.py` | fixed NodeIds/BrowseNames for nodeset imports and events |
| `profiles.py` | `AprilTagProfileConfig` and other per-Pi profiles (camera, hand-eye path, tag map) |
| `detection/` | strategy `DetectionSource`; `apriltag/` (AprilTags, package), `script_runner.py` (calibration check) |
| `camera.py` | `SharedCamera` — one capture loop, shared by detection and live stream |
| `camera_stream.py` | Marks and encodes each frame once; writes it as Base64 JPEG to `LatestCameraFrame` and passes it to the MJPEG server, see section 10 |
| `mjpeg_server.py` | Live stream as MJPEG over HTTP (`/stream.mjpg`, `/snapshot.jpg`), see section 10.2 |
| `calibration_session.py` | `CalibrationSession` — interactive camera calibration via OPC UA, see section 12 |
| `stream_overlay.py` | Marks detected tags in the live stream image, see section 10 |
| `runner/` | Package: wires the address space, state machines and job flow into the running server (`install.py`), one module per method group |
| `vision_program.py` | Part 10 program facade `VisionProgram` based on `src/ua_program`, see [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) |
| `discovery/mdns.py`, `discovery/lds.py` | mDNS announcement and LDS registration, see [`vision-system.md`](vision-system.md) |
| `tagloc/` (own package) | The localization itself: calibration, detection, poses, tag map. See [`apriltag-referenz.md`](apriltag-referenz.md) |
| `payload.py` | JSON schema `wsc.vision.detections/1` |

Connecting a real detection = **one new file in `detection/` plus one registry
entry**. The server core and this interface remain untouched.

## 2. Connection data

| | Value |
| --- | --- |
| Endpoint | `opc.tcp://<pi>:4840/raspi/server/` |
| Security | `NoSecurity` (no authentication/encryption) |
| ServerName | `Raspberry Pi OPC UA Server` |
| Namespace Vision | `http://launch-rm.de/vision` (currently **ns=6**, see the note above) |
| Namespace 40100 | `http://opcfoundation.org/UA/MachineVision` (currently ns=2) |
| Namespace Machinery | `http://opcfoundation.org/UA/Machinery/` (currently ns=4, holds `Machines`) |

**Never hard-code namespace indices** — always use
`await client.get_namespace_index("<uri>")`. The indices shift as soon as
something new is added to the address space.

Important NodeIds (readable string IDs, stable across restarts and changes):

| Node | NodeId |
| --- | --- |
| Vision system | `ns=<vision>;s=VisionMachine` |
| StartSingleJob | `ns=<vision>;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob` |
| State (outer) | `ns=<vision>;s=VisionMachine.VisionStateMachine.CurrentState` |
| State (inner) | `ns=<vision>;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.CurrentState` |
| Last result (JSON) | `ns=<vision>;s=VisionMachine.LatestResultJson` |
| Camera live stream (Base64 JPEG) | `ns=<vision>;s=VisionMachine.LatestCameraFrame` — only present if `camera_stream` is configured (see section 10) |
| Port of the MJPEG stream | `ns=<vision>;s=VisionMachine.CameraStreamHttpPort` — Int32, 0 = no HTTP stream (see section 10.2) |

## 3. What the backend must be able to do

Four capabilities, nothing more:

| # | Capability | Concretely |
| --- | --- | --- |
| B1 | Resolve namespace index from URI | `get_namespace_index(uri)` for both URIs from section 2 |
| B2 | **Event subscription with event type list** | `subscription.subscribe_events(vision_node, [<event type node>])` — the type list is **mandatory**, see 7.1 |
| B3 | Method call with 5 inputs / 2 outputs | `automatic_node.call_method(start_node, meas, part, recipe, product, parameters)` |
| B4 | Read event fields and parse JSON | `event.ResultContent[0]` → `json.loads(...)` |

Not needed: `load_data_type_definitions()`, Structured DataTypes, result handles,
polling, discovery.

A vision server delivers **no robot list** — a robot discovery in the backend
must be allowed to stay empty for this endpoint without treating the connection
as faulty.

## 4. Handshake: one job

**Order is binding — subscribe first, then call.** A job finishes after ~250 ms;
there is no event replay.

```
Backend                                     Vision server
   |  1. connect + namespace indices            |
   |  2. subscribe_events(VisionMachine, [...]) |
   |------------- StartSingleJob -------------->|  Guard + validation (synchronous)
   |<------ (JobId: String, Error: Int32) ------|  Error != 0  => end, no events
   |                                            |
   |<---- StateChangedEvent Ready->SingleExecution
   |<---- JobStartedEvent            Message = JobId
   |<---- AcquisitionDoneEvent       Message = JobId
   |<---- ResultReadyEvent           ResultContent[0] = JSON  <-- the result
   |<---- StateChangedEvent SingleExecution->Ready
   |<---- ReadyEvent                 Message = JobId
```

Result → job assignment via `payload["jobId"]`, **not** via the event fields
`JobId`/`ResultId` (see 7.2).

Event types that should be subscribed to (NodeIds in the 40100 namespace):

| NodeId | Type | Meaning |
| --- | --- | --- |
| `ns=<mv>;i=1024` | `ResultReadyEventType` | **carries the result payload** |
| `ns=<mv>;i=1013` | `JobStartedEventType` | job accepted and started |
| `ns=<mv>;i=1025` | `AcquisitionDoneEventType` | capture done (missing in the error case) |
| `ns=<mv>;i=1023` | `ReadyEventType` | job completed, ready for capture again |
| `ns=<mv>;i=1018` | `StateChangedEventType` | state change, with `FromState`/`ToState` |

Decodable fields of the `ResultReadyEvent`: `ResultContent` (string array),
`ResultState` (Int32), `IsPartial`, `IsSimulated`, `CreationTime`, `Message`.

## 5. `StartSingleJob`

Node: `AutomaticModeStateMachine/StartSingleJob`. The call is **non-blocking** —
it acknowledges acceptance; the result arrives via event.

| Argument | Nodeset type | **Actually expected** |
| --- | --- | --- |
| `MeasId` | `MeasIdDataType` | String (empty allowed) |
| `PartId` | `PartIdDataType` | String (empty allowed) |
| `RecipeId` | `RecipeIdExternalDataType` | String; **selects the job**, see below |
| `ProductId` | `ProductIdDataType` | String (empty allowed) |
| `Parameters` | `BaseDataType[]` | String array, max. 16 entries |

| Output | Nodeset type | **Actually returned** |
| --- | --- | --- |
| `JobId` | `JobIdDataType` | String, e.g. `job-000001`; empty on rejection |
| `Error` | `Int32` | error code, see below |

### Available jobs (`RecipeId`)

Each `RecipeId` selects a detection profile (`detection/`). All of them run
through the same `StartSingleJob` call and the same event/payload flow from
sections 4 and 6.

| `RecipeId` | Job | Implementation | Result |
| --- | --- | --- | --- |
| `"calibration"` | Check the measurement readiness of this cell | subprocess, `src/jobs/calibrate.py` | `attributes.message` = calibration Id, resolution, RMS, capture count, age, tag map |
| `"apriltag"` | **Localize modules** | in-process, `detection/apriltag.py`, reads from the shared camera (section 10) | one detection per recognized module tag, with real pose |

`apriltag` captures `samples_per_job` images (ceiling 3, flange 5), averages the
poses and discards tags above `max_reproj_error_px`. This results in the
`job_timeout` (section 9) of 20 s. The camera is not opened separately for this —
it is already running for the live stream (section 10) and is only read along.

`attributes` of an AprilTag detection: `tagId`, `reprojErrorPx`, `ambiguous`,
`sampleCount`, `frameTimestamp`, `recipeId`; if expected modules are missing from
the tag map, additionally `missingModules`. If no tag is in the image, the job
does **not** return an empty success result, but `Error=5` (`DETECTION_FAILED`).

`frameId` depends on the image: if the camera sees one of the four world tags
from the tag map, the source delivers poses in the world CS; otherwise in the
camera CS. **Therefore never assume `frameId`, but read it** — together with
`frameConvention`, which states where +Z points.

Only the world tags are fixed. Each detection additionally carries `source`
(`ceiling`/`flange`), `role` (`module`/`robot`), the nearest world tag
`referenceTagId` including `referencePosition`/`referenceOrientation`
(`T_worldtag_module`) and `referenceDistanceM`, as well as `worldTagIds`,
`cameraSpreadM` and `cameraSpreadDeg` as the quality of the camera localization.

**How the camera pose is computed** (`cameraPoseSolver`): If two or more world
tags are in the image, the Pi computes `T_world_cam` from **one joint PnP over
all corners of all world tags** (`joint`). With four tags that is 16 points
across the whole cell. Previously a separate pose was computed per tag from four
corners on a 20 cm base and then averaged (`average`). From a height of 2–3 m the
tilt is poorly determined: in the simulation with the four world tags of the
cell, the joint PnP gave an error of about 4.5 mm and 0.09°, the mean about
37–41 mm and 0.8°. Ambiguous tags are included, because the ambiguity belongs to
the single-tag solution, not to the corners. A single world tag stays on the
single-tag path (`single`). `cameraReprojectionErrorPx` is the RMS of the
solution over all corners. `cameraSpreadM`/`Deg` now indicate how far the
single-tag estimates deviate from the joint pose. This is a diagnostic, not the
pose itself. In `worldTags`, each tag additionally carries
`jointReprojectionErrorPx`, the error of its corners under the joint pose. A
world tag that does not match the map stands out there with the largest value.

`cameraPoseOrigin` says **where** the camera pose comes from: `world_tags` means
optically measured, `robot_pose` means propagated from the anchor and the
kinematics. The two are not equally accurate — anyone who moves to the pose must
know the difference. Also `anchorWorldTagId` (world tag of the anchor) and, once a
world tag has been in the image again, `anchorDriftM`/`anchorDriftDeg` as the
accumulated deviation of the anchor.

The caller passes the robot pose `T_base_flansch` as a job parameter: seven
doubles `(x, y, z, qx, qy, qz, qw)`, meters and quaternion xyzw. If it is
missing, the optical path is used. The Hand Pi does **not** read it itself from
the robot server — the robot is standing still during the captures anyway, so no
dependency arises between the two servers.

Layer 1 and Layer 2 use **the same** recipe and the same profile. They differ
only in the `AprilTagProfileConfig` that `server.py` sets per Pi.

An unknown or empty `RecipeId` is rejected immediately with `Error=4`
(`UNKNOWN_RECIPE`); the error message lists the known recipes. There is no
default job — whoever does not choose a job gets an error.

The selected source determines `frameId`, `frameConvention`, `configurationId`
and `IsSimulated` of the result. After the job, the `RecipeId` is stored in
`InternalRecipeId` on the result node and in `attributes.recipeId`.

**Deliberate deviation:** The nodeset declares the Ids as structures. An asyncua
client can neither build nor read such ExtensionObjects without
`load_data_type_definitions()` (asyncua issue #1693), so strings are exchanged
here instead. Accordingly, a UA browser such as UaExpert shows a string where the
type expects a structure — this is expected, not an error.

### `Stop`

Node: `AutomaticModeStateMachine/Stop`. Aborts a running job **really** (not just
formally) — the subprocess is terminated (`terminate()`, after 2 s `kill()`, see
section 9), the automaton returns to `Ready`, and a `ResultReadyEvent` with
`resultState=7` (`CANCELLED`) is fired. The call blocks until this is complete —
a `StartSingleJob` right after it never meets a job that is still cleaning up.

| Argument | Nodeset type | **Actually expected** |
| --- | --- | --- |
| `Cause` | `Int32` | not evaluated |
| `CauseDescription` | `String` | not evaluated |

| Output | Nodeset type | **Actually returned** |
| --- | --- | --- |
| `Error` | `Int32` | `0` if aborted or if nothing was running at all; `!= 0` if the abort itself failed |

Fire-and-forget capable: a `Stop` without a running job (e.g. because the job had
already finished) returns `Error=0` just like a successful abort — the frontend
does not need to know the state beforehand. Only if the job could not be aborted
within `stop_timeout` (5 s, `config.py`) does `Error=6` (`INTERNAL`) come back.

### Error codes (`Error`)

The nodeset defines no enum for `Error`; these codes are project-specific and
must be mirrored in the backend.

| Code | Name | Meaning | Do events follow? |
| --- | --- | --- | --- |
| 0 | `OK` | Job accepted | yes |
| 1 | `INVALID_STATE` | Automaton not in `Operational`/`Ready` | no |
| 2 | `INVALID_ARGUMENT` | Id too long, wrong type, too many parameters | no |
| 3 | `BUSY` | a job is already running | no |
| 4 | `UNKNOWN_RECIPE` | `RecipeId` not known | no |
| 5 | `DETECTION_FAILED` | detection failed | yes, with `resultState=5` |
| 6 | `INTERNAL` | unexpected server error | yes, with `resultState=6` |
| 7 | `CANCELLED` | job aborted by `Stop` | yes, with `resultState=7` |

Rule of thumb: **1–4 are rejected immediately in the return value** (the
automaton stays `Ready`, no event follows). **5–7 happen during execution** and
arrive as a `ResultReadyEvent` with `resultState != 0` — so an event-driven
client never learns of a detection error (or abort) only via timeout.

Concurrency: only **one** job ever runs. A second call while a job is running is
deterministically rejected with `BUSY`, instead of overwriting states.

## 6. Result payload

JSON string in `ResultContent[0]` (schema `wsc.vision.detections/1`) — identical
in the event, on the result node and in `LatestResultJson`.

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-hello-01",
  "resultId": "res-job-000001",
  "jobId": "job-000001",              // correlation with the StartSingleJob return value
  "creationTime": "2026-09-09T19:16:59.612+00:00",
  "resultState": 0,                   // 0 = ok, otherwise error code from section 5
  "frameId": "world",                 // reference frame of the poses, see below
  "lengthUnit": "m",                  // always SI meters
  "angleUnit": "rad",
  "rotation": "quaternion_xyzw",      // three.js order
  "detections": [
    {
      "moduleId": "HELLO-WORLD",      // later: key into the CAD registry
      "instanceId": "det-1",
      "confidence": 1.0,
      "position": [0.0, 0.0, 0.0],
      "orientation": [0.0, 0.0, 0.0, 1.0],
      "boundingBox": null,
      "attributes": { "message": "Hello World" }
    }
  ]
}
```

Error case — same schema, additionally `errorCode`/`errorText`, `detections`
empty:

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "jobId": "job-000004",
  "resultState": 5,
  "errorCode": 5,
  "errorText": "Erkennung durch Parameter 'force-error' fehlgeschlagen",
  "detections": []
}
```

**Additive keys** (only with a non-empty value; the schema remains
`wsc.vision.detections/1`):

| Key | Meaning |
| --- | --- |
| `frameConvention` | Axis convention behind `frameId`, e.g. `"z_forward_x_right_y_down"` = OpenCV optical frame: +X right in the image, +Y down, +Z forward along the optical axis. The pose is `T_cam_tag`, depth positive. **Not** the robotics convention (REP-103) and **not** a world system. |
| `configurationId` | Identity of the effective configuration, e.g. calibration file plus modification time — answers the question, for wrong poses, which calibration that was. |
| `worldTags` | The world tags the camera saw in this job: `[{tagId, position, orientation, reprojectionErrorPx, ambiguous, sampleCount, jointReprojectionErrorPx?}]`. `jointReprojectionErrorPx` exists only with a joint solution (see above). The pose is the **observed** one, `T_world_cam · T_cam_tag`, averaged over the images of the job and always in the world CS of the tag map. Its deviation from the map's `poseInWorld` is the error measure "eingemessen gegen gesehen" (surveyed vs. observed). In `detections`, world tags still do not appear. The field exists only if a camera pose was determined from world tags. It **also** comes along in the **error payload**: an image with only world tags ends in `DETECTION_FAILED` and is nevertheless exactly the image for checking the map. |
| `cameraLocalization` | How the camera pose of this job came about (last image): `{solver, reprojectionErrorPx, spreadM, spreadDeg, worldTagIds}`. `solver` is `joint`, `single`, `average` or `anchor` (propagated from the hand-eye anchor, in which case `reprojectionErrorPx: null`). The same values as `cameraPoseSolver`/`cameraReprojectionErrorPx`/`cameraSpread*` on the detections, but at top level and **also in the error payload**: an image with only world tags has no detections. Missing if there is no camera pose. For `solver: "joint"` additionally `poseInWorld` (the camera pose, `T_world_cam`) and `tagErrorsPx` (corner error per world tag): the cell monitoring takes the pose from this, see section 16. |

`frameId` now comes **from the detection source**, no longer fixed by the
module: one instance can serve a placeholder source in the world frame and a
camera source in the optical frame at the same time. Without `frameConvention`,
a camera-relative pose is not interpretable — a frame name alone does not say
where +Z points.

Rules for the parser: check `schema` and discard unknown versions instead of
crashing; treat `resultState != 0` as an error; interpret `position` in meters
and `orientation` as quaternion `xyzw`; never assume `frameId`, but read it.

The placeholder deliberately already provides the final structure: when real
detection is added, only the values in `detections` change, not the schema — the
backend parser then does not need to be touched.

## 7. Pitfalls (all verified)

### 7.1 Without an event type list, no payload arrives

`subscribe_events(node)` without a second argument builds the select clauses only
from `BaseEventType`. The events then arrive, but `ResultContent` is `None` — it
looks as if "the server sends nothing". Always pass the type list:

```python
sub = await client.create_subscription(100, handler)
await sub.subscribe_events(
    vision_node,
    [client.get_node(ua.NodeId(i, mv_idx)) for i in (1024, 1013, 1025, 1023, 1018)],
)
```

The select clauses also do **not** restrict *which* events are delivered: everything
that `VisionMachine` emits arrives. Filter on the client side by `event.EventType`.

### 7.2 The subscription must sit on `VisionMachine`, not on the server object

The server sets, per spec, a `HasNotifier` reference from the server object to
`VisionMachine`, but **asyncua does not bubble events on the server side**:
delivery strictly matches the exact `emitting_node`. A subscription on
`i=2253`/`client.nodes.server` therefore receives **nothing**.

Also affected: the structure fields `ResultId`, `JobId`, `InternalRecipeId`,
`InternalConfigurationId` of the `ResultReadyEvent` are deliberately sent empty,
because a client could not decode them. Their values are in the JSON payload.
`Message` additionally carries the `ResultId` as plain text.

### 7.3 `ResultManagement` methods are not usable

`GetResultById`, `GetResultComponentsById` and `GetResultListFiltered` exist in the
address space (from the nodeset), but are **not implemented**, and in any case
cannot be called from the client: they expect `ResultIdDataType` or
`JobIdDataType` as an **input** ExtensionObject. Therefore there are no result
handles either, and consequently no `ReleaseResultHandle` leak to prevent.

The result instead comes in the event (primary). Fallback, in case a client does
not process array fields in events: upon the `ResultReadyEvent`, **one**
`read_value()` on `LatestResultJson` — still event-driven, no polling.

### 7.4 There is exactly one 40100 instance — the duplication has been removed

**Done.** Previously, next to `VisionMachine` there was an older, empty
`VisionSystemType` instance `ns=2;s=…VisionSystem`, which carried only
`CpuTemperatureResult`. Anyone who collected instances by type found two systems;
a call to the unlinked `StartSingleJob` of the legacy item (`ns=2;i=150`) was
answered with `BadNothingToDo`. That cost real time at least once.

Both are gone: the legacy instance and with it `RaspiDevice` and the namespace
`http://launch-rm.de/raspi`. A type search now finds exactly one system. Verified
by `tests/test_part10_fassade.py`, which walks through the address space and
insists on **one** instance.

The recommendation nevertheless remains: use the fixed NodeId
`ns=<vision>;s=VisionMachine`. It is stable across restarts and costs no browse
round.

### 7.5 Which methods are linked — and which are not

The nodeset brings **all** methods of the spec as part of the type definition;
they are in the address space as soon as the `VisionSystemType` instance exists.
Whether they do anything is decided by `server.link_method` in
`runner/jobs.py`; the additional methods are created by `runner/` itself. An
unlinked method answers `BadNothingToDo` at the OPC UA status level — **not**
with `Error != 0` in the output.

**Linked:**

| Method | Effect |
| --- | --- |
| `StartSingleJob` | one run, result, back to `Ready` |
| `StartContinuous` | runs until `Stop` or `Abort`; each run gets its own result under `<jobId>-0001`, `-0002`, … A failed run ends continuous operation — otherwise the same fault would produce the same message every second. Pause in between: `continuous_interval_s` (default 1 s) |
| `Stop` | aborts the running job, result with `resultState=7` (`CANCELLED`) |
| `Abort` | like `Stop`, but via the Abort transition. For us this is the only difference: there is no intermediate state that an abort could discard |
| `Halt` | ends the running job and moves to `Halted`. Afterwards the server no longer accepts jobs |
| `Reset` | back to `Operational`, via `Preoperational` — the nodeset knows no `Halted -> Operational` transition |

**Not linked, with reasons:**

| Method | Why not |
| --- | --- |
| `SimulationMode` | would need a simulated data source per profile, which does not exist |
| `SelectModeAutomatic` | there is only one operating mode. A method that always returns `OK` and switches nothing would be more misleading than a clearly unimplemented one |
| `ConfirmAll`, `Sync` of the StepModels | belong to the step-chain model, which we do not use |
| **`ConfigurationManagement`** in full | would need a configuration data model that the cell does not have. `configurationId` in the result names the effective calibration |
| **`RecipeManagement`** in full | our `RecipeId` is a routing key to a detection profile, not a managed recipe object |
| `GetResultById`, `ReleaseResultHandle`, `GetResultListFiltered` | the result comes in the event and is stored at the node; handle management would be effort without a consumer (see 7.3) |

These gaps are decisions, not oversights.

## 8. Walk through the flow once

```bash
# On the Pi the server runs as a service; after a code update:
sudo systemctl restart opcua-server.service

# Reference implementation of the handshake (from any machine):
PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py \
    --url opc.tcp://<pi>:4840/raspi/server/ --recipe-id calibration
```

The client prints `StartSingleJob -> JobId=... Error=0`, the payload and the event
sequence; exit code `0` means: handshake completed fully. It is the shortest
template for the backend connection.

Special cases to demonstrate:

| Call | Expectation |
| --- | --- |
| two calls at the same time | one `Error=0`, one `Error=3` (BUSY) |
| `--meas-id` with >128 characters | `Error=2`, no events |
| `--recipe-id does-not-exist` | `Error=4`, no events |
| `--parameter force-error` | `Error=0`, then a `ResultReadyEvent` with `resultState=5`; the automaton returns via `Error` to `Operational` and is ready to capture again |

## 9. Open / next steps

- **Calibration**: `calibration` is now a **readiness check** and reports the
  calibration Id, resolution, RMS, capture count, age and tag map. The calibration
  itself remains operator-guided (`python -m tagloc.cli.calibrate`) — it needs
  someone who guides a board through the image field, and therefore cannot
  sensibly be triggered remotely.
- **Job timeout**: a detection that takes longer than `job_timeout` (20 s) is
  aborted and reported as `DETECTION_FAILED`; the automaton returns to `Ready`.
  However, a blocked worker thread also causes the *next* job of the same profile
  to run into the timeout — that is why the AprilTag source has its own, shorter
  capture timeout via `capture_timeout_s`.
- **Coordinate system**: poses are now real. `frameId` is `world` as soon as a
  reference tag from the tag map is in the image, otherwise the source's camera
  CS. **The hand-eye calibration for Layer 2 is implemented** (work plan
  [`apriltag-hand-auge-ankern.md`](arbeitsplaene/apriltag-hand-auge-ankern.md),
  status done): `src/vision_server/detection/apriltag.py` loads the chain
  camera → flange from `AprilTagProfileConfig.hand_eye_path` (see section 10.1 in
  [`apriltag-referenz.md`](apriltag-referenz.md)) and chains it with the robot
  pose of the job to `T_world_cam`. Without a world tag in the image, the chain
  anchors to the last measured value; the deviation from it is provided by the
  source in the payload as `anchorDriftM`/`anchorDriftDeg` (section 6). Only the
  real surveying/verification of the hand-eye file per cell remains open, no
  longer the implementation.
- **Second camera / 3D profile**: would be attached as a second `VisionSystemType`
  instance in the same server (own instance name and own `visionSystemId`), same
  interface. A second server process is not planned — it would load the same
  40100 address space a second time (~110 MB).
- **Populate the structure fields** of the events once asyncua issue #1693 is
  fixed. The payload then also remains the authoritative source.

## 10. Camera live stream

Transport path as agreed with the backend: **no new method call, no lifecycle**.
As soon as the server is running and `camera_stream` is configured, it
continuously writes (default: 5 images/s) the newest camera frame as a
Base64-encoded JPEG into a simple string node:

```
ns=<vision>;s=VisionMachine.LatestCameraFrame
```

The backend subscribes to this node like any other value — the same
infrastructure as for `LatestResultJson`, no new protocol. The node value is
**not** JSON, but the raw Base64 string of the JPEG; a client decodes
`atob(value)` or `base64.b64decode(value)` and gets the JPEG bytes directly.

Behavior:

- The node exists **only** if the server was configured with `camera_stream`
  (the case on the Pi via `server.py`; by default **not** for a local
  `python -m vision_server`, where the camera is usually missing).
- If the camera is not running (error on opening), the node does exist but stays
  empty (`""`) — it is not an error state of the automaton, purely stream-local.
- The live stream and the `apriltag` job share **the same** camera (`SharedCamera`
  in `camera.py`): the job only reads along the cached frames and does not open
  the hardware again. During a running job the stream therefore stays active
  unchanged; there is no pausing.
- Resolution, frame rate and JPEG quality are set in `CameraStreamConfig`
  (`profiles.py`) — default 1280×720, capture 15 fps, node 5 fps, HTTP stream
  15 fps, quality 70.
- Since the MJPEG stream (10.2), the node is only the **fallback path**. Base64
  over OPC UA, backend, WebSocket and React store is suitable for a control image,
  not for video.
- **Downscaled for the stream if wider than `max_stream_width`** (default 960 px)
  — detection and calibration still see the full camera frame; only the published
  copy becomes smaller. Without this, e.g. `cam_ceiling` (2028×1520, see below)
  cost a JPEG encode of a ~3 MP image per tick and the frame rate dropped
  noticeably.
- **The camera backend differs per Pi**; `CameraStreamConfig.backend`
  (`"picamera2"` | `"realsense"` | `"opencv"`) makes this explicit: `server.py`
  selects it via `PI_CAMERA_BACKENDS` (hostname → backend, fallback
  `"picamera2"`, override via env var `VISION_CAMERA_BACKEND`). Currently:
  `ADP-Roboter-Lokalisierung` → Picamera2 (ceiling camera),
  `ADP-HandInEye-Kamera-Pi` → RealSense. For detection and the live stream the
  backend is invisible — both only read `CameraFrame` (BGR array) from
  `SharedCamera.latest_frame`.
- **RealSense has its own, deliberately conservative defaults** (`realsense_resolution`
  640×480, `realsense_fps` 15, independent of `resolution`/`stream_fps` of the
  other backends): the RSUSB/libuvc connection needed on the Pi (the kernel does
  not bring a usable UVC driver for RealSense) limits the bandwidth; `1280×720@30`
  failed there with `RuntimeError: Couldn't resolve requests`. If
  `pipeline.start()` fails, `_open_realsense` additionally logs the color profiles
  actually supported by the connected camera — so the value can be raised in a
  targeted way if needed, instead of guessing.
- Which source feeds the stream is decided by the property: the server takes the
  first opened source that holds a `SharedCamera`. Previously the fixed profile
  name `image_recognition` stood here; renaming it would have silently killed the
  stream.

### 10.1 Overlay — what is marked in the image

The stream is a **debugging tool**: it should show not only that an image arrives,
but also what the detection sees in it. A second, **writable** node selects the
mode:

```
ns=<vision>;s=VisionMachine.CameraStreamMode     Data type String, writable
```

| Value | Display in the frontend | What is marked |
| --- | --- | --- |
| `off` | "Rohbild" (raw image) | nothing — the unchanged camera image |
| `apriltag` (default) | "AprilTags markieren" (mark AprilTags) | for each recognized tag only a **green box** (outline) and the tag ID `#<id>` (`tagloc.overlay.draw_tag_boxes`), no status bar. The axis cross, module name, distance, reprojection error and the orange coloring of ambiguous poses are no longer shown by the stream; ambiguity still appears in the job results (`ambiguous`, `ambiguityRatio`, `confidence`). The detailed overlay with axis cross is provided by the CLI `python -m tagloc.cli.detect --overlay` |
| `calibration` | "Kalibrierboard markieren" (mark calibration board) | the found board corners and the image coverage |
| `undistorted` | "Entzerrt" (undistorted) | nothing — the image **undistorted** with the active calibration (lens model of the camera, `alpha`/`balance` = 1: whole image with curved black borders). Without a real calibration (placeholder or no file) the raw image with the note "Keine Kalibrierung -- Rohbild" (no calibration -- raw image) |
| `tagreference` | "Tag einmessen" (survey tag) | only with a running survey preview (`SetTagReferencePreview`, see §19.1): the edge search of the selected tag edges — start-value edge, search band, candidate points, discarded and selected line, label with Δ mm and °, tag frame. The layers can be switched individually (§19.1). Without a preview: tag frame like `apriltag`, with the title "keine Einmess-Vorschau" (no survey preview) |

Behavior:

- An **invalid or empty value does not stop the stream** — it falls back to
  `apriltag`. The image is more important than the marking.
- The overlay recalculates at most every `overlay_interval_s` (default 0.5 s) and
  in between keeps drawing the last result. With a fixed-mounted camera you see
  nothing of this, but the Pi's CPU does.
- Overlay and job use **the same loaded calibration and tag map**. If the stream
  shows something different from the job result, it is therefore not due to two
  different configurations.
- In `calibration` mode the overlay searches with **the same board geometry** as
  `CaptureCalibrationSample` (section 12.2) — not from the loaded calibration file.
  Since 17.11.2026 this is the board of the running session (`StartCalibration`
  with `OptionsJson`, section 12.1); without a session or without a specification,
  it is the board from `AprilTagProfileConfig`. With a placeholder calibration
  (section 9, `allow_placeholder_calibration`) there is no board field there;
  assuming a different geometry would have left the corner detection in the stream
  empty, although the actual capture finds the corners.
- Also only in `calibration` mode: the corner search runs on a copy downscaled to
  `max_stream_width`, not on the full camera frame — with `cam_ceiling`
  (2028×1520) the full resolution cost noticeable time on the Pi and pushed down
  the frame rate.
- In `undistorted` mode the image is first downscaled to `max_stream_width` and
  then undistorted (`cv2.remap`). The Pi computes the maps once per resolution and
  keeps them in the cache (`tagloc.lens.undistortion_maps`). A new calibration
  (`FinishCalibration`) empties the cache. This is only a display: jobs, Layer 2
  and hand-eye still compute with the raw image and only undistort the corner
  points.
- Drawing happens on a copy; the shared frame remains unchanged.
- An error in the overlay does not end the stream — then the unmarked image comes
  through, and the error appears in the log.

The frontend only needs a selection field that writes this node on change. The
labels are provided server-side in `tagloc.modes.OVERLAY_MODE_LABELS`. Procedure
and acceptance: [`apriltag-e2e-test.md`](apriltag-e2e-test.md) section 4.

---

### 10.2 MJPEG over HTTP — the smooth live image

In addition to the node, the Pi serves the stream directly via HTTP, as
`multipart/x-mixed-replace` (MJPEG). A browser plays this in an `<img>` without a
library; backend and OPC UA are not involved.

```
http://<pi>:<port>/stream.mjpg     endless stream, ~15 fps
http://<pi>:<port>/snapshot.jpg    the newest single image (503 as long as none is available)
```

- **Port**: given in the node `CameraStreamHttpPort` (default 8080). It is only set
  once the server is actually listening; until then, and if the port is occupied,
  0 is shown there. The client takes the address `<pi>` from its OPC UA endpoint
  URL — the Pi does not reliably know its externally reachable address.
- **The same images** as in the node, with the same overlay (`CameraStreamMode`
  applies to both). Each frame is marked and encoded exactly once.
- **Rate**: without viewers the publisher only ticks at `stream_fps`; as soon as
  an HTTP connection is open, at `http_fps`. At most 4 simultaneous viewers;
  further ones get 503.
- **Camera hangs** (watchdog, see `vision-system.md`): no new images arrive, the
  connection stays open, and the browser shows the last image.
- **Fallback**: if the port is 0 or the URL is not reachable (firewall, old Pi
  build), the frontend continues to use `LatestCameraFrame`.

### 10.3 Two image streams at the ceiling camera

The ceiling camera (Pi 1, HQ camera IMX477) captures at the full sensor resolution
**4056×3040**. From the same frame, the Pi camera additionally provides a second
stream scaled by the ISP (`lores`, 960×720), practically without CPU load — but it
is now only used where detection fidelity plays no role:

| Who | Image | Resolution |
| --- | --- | --- |
| AprilTag job, calibration session | `CameraFrame.image` | 4056×3040 |
| Live stream/overlay "AprilTags markieren" (mark AprilTags) (MJPEG and node) | `CameraFrame.image` | 4056×3040, scaled down to `max_stream_width` (960 px) for publishing |
| Live stream "Rohbild" (raw image) (`off`) | `CameraFrame.image` | 4056×3040, scaled down to `max_stream_width` (960 px) for publishing |
| Overlay "Kalibrierboard markieren" (mark calibration board) (`calibration`) | `CameraFrame.preview` | 960×720 |

> **Changed 2026-09-22:** The modes "AprilTags markieren" and "Rohbild" originally
> ran on `CameraFrame.preview` (see below; original reason: frame rate). For
> "AprilTags markieren" this meant that stream and job evaluated different
> images — a tag that appeared unmarked in the stream was therefore not reliable
> evidence that the job had missed it as well. For "Rohbild" the debug image did
> not show what the Pi actually sees (focus, exposure, image section), but already
> a downscaled copy. Both modes now use `CameraFrame.image`; the overlay in
> "AprilTags markieren" mode is only downscaled for transmission after detection —
> so the hits themselves are identical to the job. The frame rate drops noticeably
> as a result (full 12 MP processing/encoding per tick instead of ~0.7 MP), a
> deliberate trade-off; for "Rohbild" without detection only the larger encode
> matters. "Kalibrierboard markieren" stays with the small preview image for speed
> reasons (its own, separately tuned downscale before `detect_board`).

Both images come from **one** request and therefore always originate from the same
capture. If an overlay computes on `preview` (960×720), the calibration is
converted accordingly (`scale_to_resolution`); the aspect ratio deviates by 0.07 %
in the process, below the 0.1 % limit.

Settings (`CameraStreamConfig`, preset in `server.py`,
`PI_CAMERA_STREAM_PRESETS`): `preview_resolution` (960×720, now only for
"Kalibrierboard markieren" and the preview fallback), `capture_fps` 10 (upper limit
of the IMX477 at full resolution), `buffer_count` 2 (six buffers of 37 MB each do
not fit into the CMA memory), `overlay_timeout_s` **8.0** instead of the global
default of 2.0 (`profiles.py`) — detection plus drawing at 4056×3040 reliably
exceeded 2.0 s, and the overlay then fell back to the unmarked raw image on every
tick (bug: overlay text was missing completely on Pi 1, found 2026-09-22). Pi 2
keeps the tight default — there nothing changed in the detection resolution.
Without `preview_resolution` — hand Pi with RealSense, OpenCV — the stream
downscales itself as before; there job and stream resolution are identical anyway
(640×480).

Main stream in format `RGB888`: Picamera2 stores it as B,G,R, i.e. directly as
OpenCV BGR — the former color conversion on every frame is dropped.

### 10.4 "AprilTags markieren" shows the result at full resolution

> **Since 21.10.2026.** Work plan:
> [`arbeitsplaene/livestream-tags-volle-aufloesung.md`](arbeitsplaene/livestream-tags-volle-aufloesung.md).
> For the mode `apriltag`, it replaces the statements on the computation cycle in
> 10.1 (`overlay_interval_s`) and on the detection resolution in 10.3.

The stream still only transmits the small image (`max_stream_width`, 960 px). The
marked tags, however, come from a detection on the **full** frame (ceiling camera
4056×3040). Before that, from 24.09. to 21.10.2026, the overlay itself detected on
the small image. This demonstrably showed different tags than the job. Measured on
`data/rohbilder/cam_ceiling_20261019-142133.png` on the PC:

| Resolution | `detect()` | IDs found |
| --- | --- | --- |
| 4056×3040 (job) | 153–166 ms | 0, 1, 1, 2, 3, 583, 586 |
| 960×720 (old overlay) | 26–32 ms | 0, 1, 1, 2, 583, **584** |

The overlay does **not** detect at the stream's cycle. Detecting on 12 MP every
0.5 s cost the Pi one core and let jobs run into the 20 s timeout (py-spy,
24.09.2026). Instead, the newest result is drawn, which arises anyway:

| Source | When | Additional CPU |
| --- | --- | --- |
| Job | every image of an AprilTag job | none |
| Cell monitoring | every 2 s, as long as it is active | none |
| Measurement | `MeasureTag`/`MeasureTags`, hand-eye captures | none |
| Overlay itself | only if the newest result is older than `overlay_detect_interval_s` (default 5 s) | on the Pi roughly 1 s per run |

The overlay's own detection runs in the background; `annotate()` does not wait for
it. At most one runs at a time, and none while a job or a camera calibration is
running. This is the same signal with which cell monitoring pauses. It uses the
stream detector: the same parameters as the job, only without corner refinement
(for `cam_ceiling` it has been active in the job since 10.11.2026, method
`subpix`). With `overlay_detect_interval_s = 0`, the overlay never detects on its
own and only shows results from job, cell monitoring and measurement.

The pixel corners are converted from the resolution of the result to the stream
size. Only the green box with `#<id>` is drawn (`draw_tag_boxes`, see 10.1), without
axis cross and without status bar.

Resolution, source and age of the displayed result are therefore not shown in the
image. Before the first result, the image comes without marking. With moving
modules, the marking can lag behind the image by several seconds.

Python: `AprilTagDetectionSource.latest_tags` (`TagSnapshot` with `tag_poses`,
`image_size`, `origin` = `job`/`cellwatch`/`measure`/`overlay`, `taken_at`),
`AprilTagStreamAnnotator(full_resolution_tags=..., detect_interval_s=...)`,
`AprilTagStreamAnnotator.set_busy(...)`.

## 11. OPC 40100-2: Asset view

Part 1 answers **how to operate the system**. Part 2 — *Asset Management
and Condition Monitoring*, published 17.05.2024 — answers **what it consists of**:
computing unit, image sensor, lens, each with identification. For service and
maintenance, not for operation.

```
ns=<vision>;s=VisionMachine.VisionAsset
├── Identification        Manufacturer, Model, SerialNumber, SoftwareRevision
├── ComputingDevices/ComputingDevice
├── ImageSensors/ImageSensor
└── Lenses/Lens
```

Only what is listed in the `AssetConfig` of the Pi is created (`src/vision_server/server.py`,
`PI_ASSET_PRESETS`). An empty model field means "not known" and creates **no**
entry — an invented model would be worse in a maintenance view than a gap.

### 11.1 What this costs

Part 2 brings **DI 1.04.0** and **Machinery 1.03.0**; the server therefore loads
four nodesets instead of one. Measured (desktop, asyncua 2.0.1):

| | RSS | Startup time |
| --- | --- | --- |
| Part 1 only | 108.5 MB | 0.65 s |
| with Part 2 | 121.3 MB | +1.6 s |
| Asset view instantiated | +2.9 MB | |

Roughly **16 MB and just under two seconds**. Without `assets` in the
`VisionServerConfig`, none of this is loaded. Re-measure:
`PYTHONPATH=src python3 tools/measure_nodeset_import.py`.

### 11.2 Two traps

**The nodeset versions are pinned.** The newest DI (1.05.0) cannot be imported
with asyncua 2.0.1 — it requires UA base 1.05.04 and fails with
`BadParentNodeIdInvalid`. The newest Machinery would additionally pull in `IA`.
Exactly the versions that AMCM names as `RequiredModel` were chosen. Details in
[`src/vision_server/nodesets/README.md`](../../src/vision_server/nodesets/README.md).

**The namespace index shifts.** With Part 2, `http://launch-rm.de/vision` no longer
sits at index 3, but at 6. Clients must resolve it at runtime via
`get_namespace_index`. Anyone who hard-codes an index only notices when someone
adds a nodeset.

### 11.3 Why asyncua creates placeholders

`<VisionItem>` & Co. carry the modelling rule `MandatoryPlaceholder`. asyncua
therefore instantiates them as real nodes, although they are templates of the
type. Deleting them afterwards cost **9 s for 26 nodes** — recursive deletion is
pathologically slow there. `asset_model.py` therefore only creates the folders it
fills: around 50 nodes instead of 700.

### 11.4 State: `DeviceHealth`

The standard title of Part 2 is *Asset Management **and Condition
Monitoring***. 11.1–11.3 describe the first half — what the system consists of.
This is the second: **how it is doing.**

Concretely, it answers the question that could not be asked over OPC UA before:
*is the camera still running, or has it frozen?* A hanging `capture_array()`
remained invisible until then — the live stream kept sending the same old frame,
the state machine stayed at `Ready`, and only a running job failed with
`DETECTION_FAILED`.

```
ns=<vision>;s=VisionMachine.VisionAsset
├── <amcm>:Health                                      (HasAddIn)
│   └── <di>:DeviceHealth   Int32
│       ns=<vision>;s=VisionMachine.VisionAsset.Health.DeviceHealth
├── <di>:Identification
├── <amcm>:ComputingDevices/ComputingDevice
├── <amcm>:ImageSensors/ImageSensor
│   └── <amcm>:Health
│       └── <di>:DeviceHealth   Int32
│           ns=<vision>;s=VisionMachine.VisionAsset.ImageSensor.Health.DeviceHealth
└── <amcm>:Lenses/Lens
```

**Both nodes carry the same value.** That is not aggregation, but a fact: there is
exactly one state source, the camera. The node at the root exists nonetheless,
because `ImageSensor` only exists if `image_sensor_model` is set in the
`AssetConfig` — otherwise there would be no reporting point at all. If a second
component with its own state is added, the root needs a real rule; that requires a
ranking over the NE 107 states, which DI does **not** define, and is therefore only
written then. For a frontend this means today: **subscribe to the root address** —
it exists independently of the configuration.

#### The values

`DeviceHealth` is DI's `DeviceHealthEnumeration` per NAMUR NE 107 and travels over
the wire as `Int32`.

| Value | Name | When | Color in HMI |
| --- | --- | --- | --- |
| 0 | `NORMAL` | Fresh camera image | green |
| 1 | `FAILURE` | Camera never opened, or finally given up — the process terminates right away | red |
| 2 | `CHECK_FUNCTION` | Warm-up, or the camera is being reopened | yellow |
| 3 | `OFF_SPEC` | Image older than `stale_frame_s` — hang detected, not yet escalated | yellow |
| 4 | `MAINTENANCE_REQUIRED` | **Is never written** — there is no wear counter | — |

The typical course of a hang, with the default values from `CameraStreamConfig`:

```
NORMAL ──(image older than stale_frame_s = 2 s)──> OFF_SPEC
       ──(watchdog triggers after frame_timeout_s = 3 s, reopens)──> CHECK_FUNCTION
       ──(camera delivers again)──> NORMAL
       ──(2 reopenings without an image)──> FAILURE, then process abort
```

The **`OFF_SPEC` window is `frame_timeout_s − stale_frame_s` wide**, so with the
default values 1.0 s. Therefore the publisher polls every `health_interval_s` =
0.5 s rather than once per second: at 1 Hz the window would regularly be missed.
If `stale_frame_s` is set above `frame_timeout_s`, `OFF_SPEC` disappears entirely —
a test in `tests/test_camera_health.py` pins this down.

Why a stale image is `OFF_SPEC` and not `FAILURE`: hangs demonstrably heal
themselves. With `OFF_SPEC`, an HMI can distinguish "hakt gerade" (hanging right
now) from "tot" (dead), and gets roughly one second of advance warning before the
watchdog intervenes. Anyone who wants it differently changes exactly one line in
`camera_health.device_health()`.

#### What a client must observe

- **Written only on state change.** A subscription sees real transitions; in
  normal operation the node stays silent after the first image. Whoever polls
  always gets the valid value — the node is always populated.
- **The start value is `CHECK_FUNCTION`**, not `NORMAL`. While the address space is
  being built, the camera is not yet open; `NORMAL` would be an assertion that
  would stand unchallenged until the first image.

- **`FAILURE` may be the last sign of life.** If the watchdog gives up, `FAILURE`
  is written and the process ends immediately afterwards, so that systemd restarts
  it. The session therefore breaks off right after. A client should expect the
  connection to drop after `FAILURE` and reconnect.
- **Resolve namespace indices at runtime** (11.2). The BrowseName of `DeviceHealth`
  lies in the **DI** namespace, that of `Health` in the **AMCM** namespace, and the
  NodeIds in the vision namespace — three different indices on one path.

#### Alarms — the second part of the standard

DI's `IDeviceHealthType` has **exactly two** members: the variable above and a
folder with real OPC UA alarms. Both are served.

```
VisionAsset/Health
├── <di>:DeviceHealth                Int32
└── <di>:DeviceHealthAlarms
    ├── <di>:FailureAlarm            FailureAlarmType
    ├── <di>:CheckFunctionAlarm      CheckFunctionAlarmType
    └── <di>:OffSpecAlarm            OffSpecAlarmType
```

| Alarm | Severity | When |
| --- | --- | --- |
| `FailureAlarm` | 900 | `DeviceHealth` becomes `FAILURE` |
| `OffSpecAlarm` | 500 | `DeviceHealth` becomes `OFF_SPEC` |
| `CheckFunctionAlarm` | 300 | `DeviceHealth` becomes `CHECK_FUNCTION` |

Rules a client can rely on:

- There is **at most one active** alarm — the state is one of five. On a change,
  the previous alarm is cleared with `ActiveState = Inactive` and `Retain = false`
  before the new one fires with `Active`/`true`.
- **`NORMAL` fires no alarm**; it only clears the previous one. Operation without
  faults is silent.
- **Emitted by `VisionMachine`**, not by the health node. Reason: a client
  subscribes to exactly this node anyway (section 7.2), and a second subscription
  point would be a new requirement for every client. `SourceNode` nevertheless names
  the affected component — the image sensor, if its model is known, otherwise the
  asset root.
- `Message` carries the plain text including the trigger, e.g.
  *"Kamera liefert kein Bild (hung)"* (camera delivers no image).

The alarm objects sit **only at the root**, not additionally at the image sensor:
both state nodes carry the same value, and each instance costs 36 nodes. The root
also always exists — even without a configured camera model.

> **Limitation to be aware of:** `ConditionRefresh` is **not** served. A client
> that connects *after* an alarm does not get it delivered afterwards. Therefore
> `DeviceHealth` remains the reliable source for "what is the state now"; the
> alarms are the event path for "what has changed".

#### What is not created

`MaintenanceRequiredAlarmType` is deliberately **not** instantiated: without a wear
counter it could never fire, and an alarm that never comes is a promise we cannot
keep. Likewise `State` (SEMI E10), `Temperature` and `RemainingLifeTime` are
missing — all optional and without a source. `State` would be the next sensible
step; it needs the structured DataTypes, which `load_data_type_definitions()` still
fails on for 40100 (asyncua #1693). `Maintenance` (`VisionMaintenanceInfoType`)
would remain empty without maintenance intervals and calibration history.

#### The live stream says nothing about the camera

Previously the server emptied the `LatestCameraFrame` node as soon as an image was
older than `stale_frame_s` — the frontend then showed "Warte auf Bild" (waiting for
image). That was a **second truth** about "is the camera alive", alongside
`DeviceHealth` and bypassing the companion spec. It has been removed.

Consequently, when the camera hangs, **the live image freezes** instead of
disappearing. MJPEG continues to deliver the last image, and `/snapshot.jpg` no
longer answers with `503`. Only `DeviceHealth` answers whether the camera is
working — in the frontend as a traffic light next to the image.

#### Cost

Measured (desktop, asyncua 2.0.1, two runs per variant):

| | Nodes of the asset view | Build time |
| --- | --- | --- |
| without state block | 19 | 0.151 s |
| `DeviceHealth` only | 23 | 0.179 s |
| with alarms | 132 | 0.327 s |

The alarms therefore account for the lion's share: **36 nodes per instance**,
because `AlarmConditionType` brings the full condition tree along (`ActiveState`,
`AckedState`, `Retain`, `Severity`, `Message`, `EventId` …). Three instances plus
the folder come to around **110 nodes and 0.15 s**. That is exactly why they sit
once at the root and not additionally per component — that would be 216 nodes for
the same information.

The RSS difference was below the measurement resolution (0.1 MB). No additional
nodesets — DI and AMCM are loaded with `assets` anyway. Re-measure:
`PYTHONPATH=src python3 tools/measure_nodeset_import.py`.

#### Relation to legacy A3

With `RaspiDevice/Counter` the last sign of life has disappeared from the address
space (`doc/altlasten.md`, A3); the loop-lag watchdog remained as a replacement,
but it only writes to the log. `DeviceHealth` closes this gap — and is explicitly
**not** a resurrected counter: A3 was a value *without a source* that merely proved
some loop was running. Here there is a source, the value is in the standard, and in
the idle state nothing at all is written.

---

## 12. Interactive calibration (frontend-driven)

So far, calibration has run exclusively through the standalone CLI tool
(`tagloc.cli.calibrate` via SSH) — it opens the camera exclusively, so the server
must be stopped for it. These four methods plus one node allow the same **while
the server is running**, from a settings menu: start a session, hold the board in
front of the camera, trigger captures, then `FinishCalibration` — the result
appears shortly afterwards in `CalibrationProgress` (section 12.5).

As with the live stream, the session only reads along from the already running
`SharedCamera` (the same one that the `apriltag` job and the live stream use) — no
second, exclusive camera access, and no need to stop the server.

**Capture and evaluation are decoupled (since 2026-09-28).**
`CaptureCalibrationSample` now only remembers the current camera frame — no corner
detection in this call anymore. Reason: on the ceiling camera (12 MP), corner
detection on the Pi hardware could take so long (up to a genuine hang, found live)
that the OPC UA request ran into the timeout or the connection broke off entirely —
with noticeable consequences for every further capture. `FinishCalibration`
(section 12.3) starts the actual evaluation (corner detection per capture + camera
calibration) as a **background task** and returns immediately; the frontend
monitors `processing`/`result` in `CalibrationProgress`. The price: during capture
there is no live feedback anymore on whether a particular capture actually shows
the board, and no running coverage display — both only become apparent after
`FinishCalibration`. The live stream (`CameraStreamMode="calibration"`) still shows
board corners on the small preview image while positioning (unchanged, its own,
cheaper detection run); this helps with alignment, but says nothing about which
captures were actually accepted. **During the evaluation the server pauses the live
stream** (`LatestCameraFrame`/MJPEG only deliver the last image before the pause in
the meantime) to give the Pi the full CPU for it — afterwards it starts again
automatically and shows the result.

**A newly saved calibration takes effect immediately, without a server restart.**
Directly after saving, the running detection (`apriltag` job) and the stream
overlay adopt the new values — the next job after a successful calibration already
uses them.

**Board geometry has been selectable since 17.11.2026** (a board parameter is sent
along with `StartCalibration`, section 12.1). Without this specification, the board
from the configuration still applies (`AprilTagProfileConfig` in `profiles.py`, per
Pi in `PI_APRILTAG_PRESETS` in `src/vision_server/server.py`).

### 12.1 `StartCalibration`

Resets collected captures and begins a new session. Captures thereafter come
exclusively via `CaptureCalibrationSample` (section 12.2) — no automatic capturing.

| Input | Type | Meaning |
| --- | --- | --- |
| `OptionsJson` | `String` | since 17.11.2026. Empty or `{}`: board from the configuration (as before). Otherwise JSON with `schema` `wsc.vision.calibration.start/1` and `board` (see below). A direct asyncua call without an argument continues to work (default value empty) |

| Output | Type | Meaning |
| --- | --- | --- |
| `Error` | `Int32` | `0` (`OK`), `1` (`INVALID_STATE`, automaton not `Ready`), `2` (`INVALID_ARGUMENT`, since 17.11.2026: `OptionsJson` not valid JSON, wrong schema or unusable board; no session started, a previous one remains untouched, reason in `CalibrationProgress.startError`), `3` (`BUSY`, a job is already running, a session is active, or an evaluation from a previous `FinishCalibration` is still running) |

**Choosing the board (since 17.11.2026).** `board` follows the same rules as the
board of the hand-eye calibration (`wsc.vision.handeye.start/1`, functions
`handeye_session._parse_board` and `check_raw_board`):

- Chessboard (`"type": "chessboard"`): `cols`/`rows` = inner corners, `cols + rows`
  odd, square size 5–100 mm (`squareSizeM`).
- ChArUco (`"type": "charuco"`): `cols`/`rows` = squares (at least 3×3),
  `markerSizeM` smaller than `squareSizeM`, `dictionary` from
  `DICT_{4X4,5X5,6X6,7X7}_{50,100,250,1000}` with enough markers.

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

If `board` is missing (or `OptionsJson` is empty), the board from the configuration
applies. The session's board controls the evaluation, the archive (`session.json`,
field `board`), the overlay in `calibration` mode and the field `board` in
`CalibrationProgress`.

**Compatibility:** The webskillcomposition backend strictly checks the argument
count against `InputArguments`. An old frontend (calling with `[]`) is rejected by a
new Pi, and a new frontend (calling with `[json]`) by an old Pi. Pi and frontend are
therefore updated together.

### 12.2 `CaptureCalibrationSample`

Remembers **one** camera frame for the later evaluation. Triggered manually — no
time interval, no motion comparison, no detection in this call (see above): the
operator sees the live image (live stream or `stream_viewer.py`) and decides
themselves when a pose looks good before triggering. Almost always immediate and
fast — only one image is remembered, nothing is evaluated.

| Output | Type | Meaning |
| --- | --- | --- |
| `Error` | `Int32` | `0` (`OK`, capture accepted), `1` (`INVALID_STATE`, no session active or no camera frame currently available) |

### 12.3 `FinishCalibration`

Ends the capture phase and starts the evaluation **in the background** — returns
immediately, without waiting for the result. The result then appears in
`CalibrationProgress` (`processing` → `result`, section 12.5).

| Output | Type | Meaning |
| --- | --- | --- |
| `Summary` | `String` (JSON) | On acceptance `{"message": "Auswertung gestartet", "samples": N}` (message: "evaluation started"; this is still **not** the result — that comes via `CalibrationProgress`). On immediate rejection (too few captures) `{"message": "...", "samples": N}` |
| `Error` | `Int32` | `0` (`OK`, evaluation was started), `1` (`INVALID_STATE`, no session active), `5` (`DETECTION_FAILED`, fewer than 3 captures made — in that case nothing was started) |

`Error=OK` here therefore only means "the evaluation is running now", not "the
calibration is finished and good". The actual result (`rms`,
`coverageX`/`coverageY`, possibly `warning`) and any failure *during* the
evaluation end up exclusively in `CalibrationProgress["result"]` (section 12.5,
where the possible failure reasons are also listed). A frontend that only displays
the `FinishCalibration` return value shows the operator a false "Erfolg" (success)
while the evaluation is still running or ultimately fails — `CalibrationProgress`
must be monitored.

### 12.4 `AbortCalibration`

Ends the session **without** saving — for the case that the operator made a
mistake or wants to start over. Can only be called during the capture phase, not
while an evaluation from a previous `FinishCalibration` is still running (then it
already returns `INVALID_STATE`, since `running` is already `false` — the running
evaluation itself cannot be aborted; it runs to completion).

| Output | Type | Meaning |
| --- | --- | --- |
| `Error` | `Int32` | `0` (`OK`), `1` (`INVALID_STATE`, no session active) |

### 12.5a `SetTagMap` — set the cell's tag map

```
ns=<vision>;s=<VisionSystemName>.SetTagMap

SetTagMap(TagMapJson : String) -> (Error : Int32)
```

This makes the **backend the maintainer of the tag map**: a new tag size or a
moved world tag no longer requires file access on the Pi. The argument is exactly
the file content of `config/tagmap.json` (schema `wsc.vision.tagmap/2`) — what the
backend sends can be stored unchanged, and vice versa.

| `Error` | When |
| --- | --- |
| `OK` (0) | map accepted, file written, detection uses it immediately |
| `INVALID_ARGUMENT` (2) | not valid JSON, unknown schema, unknown role |
| `BUSY` (3) | a job or a calibration session is running |
| `INTERNAL` (6) | file not writable — the map is **nevertheless active**; only a restart falls back to the old one |

**Check first, then apply:** a rejected map leaves the running one untouched.
Objections from `validate_tag_map` (e.g. only one world tag instead of four) are
**not** an error — they are logged and the map applies.

The anchor of the hand camera is **discarded** in this process: it was computed
against the world poses of the old map and would be silently wrong relative to the
new one. The next look at a world tag re-anchors it.

Written to `config/tagmap.json`, so that after a restart the Pi continues measuring
without a backend.

### 12.5b `TagMapJson` (read only)

```
ns=<vision>;s=VisionMachine.TagMapJson              Data type String (JSON)
```

The **currently loaded** map, in the same format. Deliberately not writable: a tag
map must be checked before it applies, and a write access could not reject an
unusable map. For that there is `SetTagMap`.

### 12.5 `CalibrationProgress` (read only)

```
ns=<vision>;s=VisionMachine.CalibrationProgress     Data type String (JSON)
```

Written on every publish tick of the live stream (no separate task) — so it also
runs when `CameraStreamMode` is currently `off`, and also while the live stream
itself is paused (evaluation phase, see above — the progress node is a separate
write path, independent of the image).

| Field | Meaning |
| --- | --- |
| `running` | `bool` — capture phase active? Becomes `false` with `FinishCalibration` **before** the result is determined |
| `processing` | `bool` — is the background evaluation from a `FinishCalibration` currently running? |
| `samples` | Number of images captured so far (**not** checked whether the board is visible on them — that is only decided during the evaluation) |
| `minSamples` | Minimum number from which `FinishCalibration` starts an evaluation at all (config, default 15) |
| `model` | Lens model of this session, `"pinhole"` or `"fisheye"` — already set during capture (section 12.10). Missing at rest without a session |
| `board` | since 17.11.2026: board of the running or most recently run session (`BoardSpec.as_dict()`: `type`, `cols`, `rows`, `squareSizeM`, for ChArUco additionally `markerSizeM`, `dictionary`). Set for every session; also before the first start, the board from the configuration. Older Pis do not know the field |
| `startError` | since 17.11.2026: reason why the last `StartCalibration` option was rejected (plain text for the operator). Remains set until the next start succeeds; otherwise the field is missing |
| `result` | **only present once the evaluation is finished** (see below) |

At rest (no session ever started, or after `AbortCalibration`):
`{"running": false, "processing": false}`, without `result` (a Pi with a session
additionally supplies the field `board`, see above). A typical sequence:

```jsonc
// during capture
{"running": true, "processing": false, "samples": 12, "minSamples": 15}
// FinishCalibration was called, evaluation running
{"running": false, "processing": true, "samples": 18, "minSamples": 15}
// done
{
  "running": false, "processing": false, "samples": 18, "minSamples": 15,
  "result": {
    "error": 0, "rms": 2.069, "model": "fisheye", "samples": 16,
    "coverageX": 0.84, "coverageY": 0.9,
    "path": "data/calibration/cam_ceiling.json",
    "backup": "data/calibration/cam_ceiling.json.20261115T093000Z.bak",
    "warning": "RMS 2.069 px ueber dem Zielwert 0.5 px -- ..."
  }
}
```

**Old calibration is kept (since 15.11.2026):** The new calibration replaces the
file under `path`; the previous one is copied beforehand next to it as
`<name>.<UTC>.bak` (same scheme as for the hand-eye calibration). Every backup gets
its own name; none is overwritten. `result.backup` names the backup; without an old
file the field is missing.

Two numbers can diverge here: `samples` (above, in the root object) is the number of
**captured** images (18), `result.samples` the number of images on which the board
was actually **found** during the evaluation (16 — two were e.g. blurred, or the
board was not in the image section). Only the latter flows into the calibration.

`result.error` is the same `Error` code as for `FinishCalibration` (0 = `OK`). A
failure *during* the evaluation appears in the same way in `result`, with
`error != 0` and `message` instead of `rms`/`samples`/`coverageX`/`coverageY` — for
two possible reasons: either too few of the captured images actually showed the
board (`result.samples` would remain below 3), or `cv2.calibrateCamera` itself
fails numerically. The latter can happen even though enough captures and good image
coverage were present: **coverage alone says nothing about the actual accuracy** — a
board that was never tilted fills the image area but leaves the focal length
undetermined (test plan section 3.3).
`result.warning` therefore additionally appears if the RMS reprojection error
exceeds 0.5 px; it does **not** prevent saving — the file is written anyway, merely
with the note that it is less accurate than recommended. The frontend should
display this warning visibly, not just log it.

For Pis with a connected monitor there are three command-line tools under
`src/vision_server/tools/` (as a reference for the frontend team, not part of the
frontend): `calibration_client.py` starts/ends a session, logs `CalibrationProgress`
and waits for `result` after `FinishCalibration`; `stream_viewer.py` shows the live
stream in a local window and triggers `CaptureCalibrationSample` with the space bar
— together they form the handshake that a frontend must reproduce. `diagnose_board.py`
is pure troubleshooting: it fetches an unmarked raw image from the running server and
tries several plausible `cols`/`rows` combinations against `detect_board`, in case
the board is visible in the stream but not recognized — e.g. because the geometry
assumed in `PI_APRILTAG_PRESETS` does not match the board actually mounted.

### 12.6 `ActiveCalibrationInfo` (read only)

```
ns=<vision>;s=VisionMachine.ActiveCalibrationInfo     Data type String (JSON)
```

Unlike `CalibrationProgress` (progress of *a running session*, which disappears with
the next `StartCalibration`), this node describes **what is actually being used for
poses right now** — it stays in place regardless of what happens with a session, and
is the right source for a permanent "Zuletzt kalibriert am ..." (last calibrated on
...) display in the frontend. Written at start (what `open()` loaded) and after every
successful interactive calibration (the same moment at which it is also adopted live,
see above).

```jsonc
{
  "placeholder": false,
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T10:00:00+00:00",
  "createdAt": "2026-09-22T10:00:00+00:00",
  "rms": 0.2945,
  "samples": 21,
  "board": {"type": "chessboard", "cols": 7, "rows": 9, "squareSizeM": 0.022, ...},
  "model": "pinhole",
  "path": "data/calibration/cam_flange.json"
}
```

| Field | Meaning |
| --- | --- |
| `placeholder` | `true` if it has never been calibrated for real (`allow_placeholder_calibration`, section 9) — then `rms`/`createdAt` are `null`, and poses are not dimensionally accurate |
| `calibrationId`, `createdAt` | from the calibration file, stable across restarts |
| `rms`, `samples`, `board` | as in the `result` object from `CalibrationProgress`/`FinishCalibration` |
| `model` | lens model of the active calibration, `"pinhole"` or `"fisheye"` (section 12.10) |
| `path` | path of the file on the Pi (diagnostics, no backend meaning) |

### 12.7 Locks

`StartCalibration` rejects (`BUSY`) as long as a job is running **or** a
calibration session/evaluation is running (`CalibrationSession.busy` — both the
capture AND the background evaluation after `FinishCalibration` count, see section
12.3: a new start during a still-running evaluation would clear its working folder
out from under it). Conversely, `StartSingleJob`/`StartContinuous` also reject
(`BUSY`) as long as a calibration session/evaluation is running — both share the
camera and the detector, so running them at the same time makes no sense. There is
**no separate state-machine state** for this: the nodeset knows no matching state
for "calibration running", the automaton stays in `Ready`, and the lock works purely
via the busy flags — the same `BUSY` semantics as between two jobs (section 5, error
codes).

### 12.8 Status

**Calibration board since 19.10.2026:** Both Pis calibrate with the new chessboard,
**6×7 inner corners, 23 mm per square** (`cols=6`, `rows=7` in `server.py`, both
cameras). `cols`/`rows` count the **inner corners**, not the squares: the board has
7×8 squares. With the old setting (7×9, 22 mm) the new board is not found, and the
overlay display "Board gefunden" (board found) in `calibration` mode then stays
empty. The numbers below belong to the calibrations with the old board (7×9, 22 mm).
Already saved calibration files remain valid; they carry their board in the field
`board`. After the switch, recalibrate both cameras with the new board so that the
documentation in the file is correct too. Cols + rows = 13 is odd, so the board is
also suitable for automatic hand-eye calibration with a chessboard (section 15.1).

Verified in real operation for Layer 2 (Hand Pi, `ADP-HandInEye-Kamera-Pi`, RealSense)
(`chessboard`, 7×9, 22 mm, RMS 0.2945 px with 21 captures via the CLI tool).

Layer 1 (ceiling camera, `ADP-Roboter-Lokalisierung`, Picamera2) has been run through
once via the interactive route (`CaptureCalibrationSample` + stream viewer) — board
geometry (7×9, 22 mm) confirmed via `diagnose_board.py`, `data/calibration/cam_ceiling.json`
exists. On the first attempt the RMS was 2.069 px (coverage 84 %/90 %) — well above
the target value, because the camera is not yet firmly mounted and the board was not
tilted enough. As soon as the camera is firmly mounted, recalibration is required,
this time focusing on tilt/distance variation instead of just image coverage (see the
`warning` in section 12.5). The code itself is independent of the Pi.

### 12.9 Debug: saving captures as images

A purely diagnostic tool, not part of the interface itself — for investigating
calibration problems live on a Pi, without backend logs being available. **Off by
default** (since 2026-09-28; `VISION_SAVE_CALIBRATION_CAPTURES=1` switches it on) —
more on this below. When enabled, `AprilTagProfileConfig.calibration_capture_dir`
additionally archives permanently, in addition to the session's own working folder
(`_pending_dir`, see section 12), every capture made via `CaptureCalibrationSample`
as a JPEG under `data/calibration/<frame_id>_captures/kalib_001.jpg`,
`kalib_002.jpg`, … — the same naming convention as `tagloc.cli.calibrate --capture-to`
(there PNG, here deliberately JPEG, see below). The counter restarts at 1 with every
`StartCalibration`. Since `capture()` no longer checks whether the board is visible,
owing to the decoupling of capture and evaluation, every capture is now counted and
archived, not only those that are actually usable later. `data/` has been under
version control since 02.11.2026 (`3535115`): the captures show up in `git status`,
so only commit them deliberately. There is no automatic cleanup mechanism — empty
it by hand when disk space on the Pi runs low.

**Two performance bugs found live on the ceiling camera (12 MP); Hand Pi (640x480)
never affected:**

1. **2026-09-23 — blocked `CaptureCalibrationSample`.** Saving initially ran
   synchronously in `capture()`, before the OPC UA response went out — `cv2.imwrite`
   at 12 MP can take several seconds, causing the same timeout ("Failed to send
   request to OPC UA server") that `CALIB_CB_ACCURACY` had caused earlier (section 4,
   `apriltag-referenz.md`). Fix: saving has since run as a background task;
   `capture()` returns as soon as the capture has been accepted.
2. **2026-09-28 — Pi hangs after every photo.** The background task continued to run
   through the same single-worker pool as `detect_board` (`_pool()`). On the SD card,
   writing a 12 MP image takes longer than an operator needs between two captures —
   every further `capture()` then had to wait in exactly this queue for the
   still-running save of the *previous* capture before `detect_board` even started.
   This piled up with every photo and presumably also put pressure on the
   concurrently running `calibration` stream preview (SD card/CPU load overall, not
   the overlay itself). Fix: a dedicated single-worker pool just for saving
   (`_save_pool()`, `calibration_session.py`), separate from `detect_board`. In
   addition, JPEG instead of PNG (faster to encode, smaller to write) — for the
   purpose (visual inspection, possibly recomputing the RMS), the lossy compression
   does not noticeably change the detected corner positions of a chessboard pattern.

After both findings, the default was switched to `None` instead of remaining
unconditionally active for both Pis — a debugging tool that affects the very
calibration it is meant to investigate does not belong in standard operation. If
needed, switch it on specifically with `VISION_SAVE_CALIBRATION_CAPTURES=1` (systemd
unit or shell before the start), and switch it off again once the investigation is
complete.

**Superseded by the decoupling of capture and evaluation (2026-09-28, see section
12):** `capture()` no longer calls `detect_board` at all, so `_pool()` is no longer
occupied by `CaptureCalibrationSample` — bug 2 could no longer occur in this form.
The actual trigger for the decoupling was a third, more serious finding from the same
day: `detect_board` itself hung on the ceiling camera during corner detection on the
full 12 MP frame for several minutes (no error, no return value). With only one
worker in the pool, that blocked every subsequent capture; a server restart was the
only way out. This file remains as a record, because it shows how the problem was
gradually narrowed down, not because the described mechanism still runs in this form.

**Addendum 10.11.2026: archive lossless per session.** The archive now lies under
`data/calibration/<frame_id>_captures/<UTC-Stempel>/` (timestamp `YYYYmmddTHHMMSSZ`,
with collision suffix `_2`, `_3`) and writes the captures as PNG (`kalib_NNN.png`,
lossless) instead of JPEG. Each session has its own folder with a `session.json`
(schema `adp.calibration.captures/1`, including, among others, board, model and the
active calibration file at session start), so that earlier sessions are no longer
overwritten. The working folder `data/calibration/<frame_id>_pending/` remains JPEG,
so that the online evaluation stays fast. For the procedure and the interfaces, see
the work plan [`arbeitsplaene/kalibrier-aufnahmen-roh.md`](arbeitsplaene/kalibrier-aufnahmen-roh.md).

### 12.10 Lens model per camera (since 2026-09-29)

Each camera is calibrated with the lens model that performed best in the image tests
(work plan `arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md`):

| Layer | Camera | Resolution | Model | Coefficients |
| --- | --- | --- | --- | --- |
| LV1 | `cam_ceiling` (Pi 4, HQ camera) | 4056 × 3040 | `fisheye` — OpenCV fisheye, Kannala-Brandt | 4: k1, k2, k3, k4 |
| LV2 | `cam_flange` (Pi 5) | 640 × 480 | `pinhole` — OpenCV standard, Brown-Conrady | 5: k1, k2, p1, p2, k3 |

**Who decides what:**

- **Recalibrating** (`CalibrationSession`, `tagloc.cli.calibrate`) uses the model from
  the config: `AprilTagProfileConfig.calibration_model`, set per preset in
  `server.py`, overridable for comparison via `VISION_CALIBRATION_MODEL=pinhole|fisheye`.
  CLI: `--model`.
- **Poses, overlay, scaling** are determined solely by the model **in the loaded
  calibration file**. An older pinhole file of the ceiling camera therefore remains
  in effect until it has been recalibrated there.

**File:** schema `wsc.vision.calibration/2` with the new field
`"distortionModel": "pinhole" | "fisheye"`. Files with schema `/1` continue to be
read and count as `pinhole`. A fisheye file with a coefficient count other than 4,
or an unknown model, is rejected on loading — the source then stays down, as with
any broken calibration.

**OPC UA:** only additional fields, all `"pinhole"` or `"fisheye"`:

| Where | Field | When |
| --- | --- | --- |
| `CalibrationProgress` (12.5) | `model` | during a session: capture, evaluation, result |
| `CalibrationProgress` (12.5) | `result.model` | in every `result`, **also on failure** |
| `FinishCalibration` (12.3) | `Summary.model` | on immediate rejection (too few captures) |
| `ActiveCalibrationInfo` (12.6) | `model` | always: model of the active calibration |

At rest without a session, `model` is missing from `CalibrationProgress`; in that
case display `ActiveCalibrationInfo.model`.

**Technical details:** all model-dependent OpenCV calls are in `tagloc/lens.py`
(`undistort_points`, `project_points`, `calibrate`). Fisheye computes with
`cv2.fisheye.calibrate` and the flags `RECOMPUTE_EXTRINSIC | CHECK_COND |
FIX_SKEW`. If `CHECK_COND` reports a poorly conditioned capture, the calculation is
repeated once without this check (warning in the log). The flag constants are
looked up at runtime: OpenCV 4 provides them under `cv2.fisheye.*`, OpenCV 5 under
`cv2.*` with **different numeric values**.

---

## 13. Interface at a glance

These tables are the **complete contract**. Everything that another subsystem needs
from the vision server is listed here; the sections before explain the why and the
pitfalls. Whoever develops against this list needs neither the code nor this repo.

The vision server is thereby **closed**: whatever comes afterwards builds *on top
of* it and does not change it. An addition to these tables is an interface change
and goes through `doc/arbeitsplaene/` (see `CLAUDE.md`).

### 13.1 Addresses

All NodeIds are fixed string IDs. `<vision>` is the index of
`http://launch-rm.de/vision`, `<mv>` that of
`http://opcfoundation.org/UA/MachineVision`, and `<amcm>` and `<di>` those of the
Part 2 namespaces. **Resolve indices at runtime via the URI, never hard-code them**
(section 11.2) — in this project they have already shifted three times.

| Purpose | NodeId | Access |
| --- | --- | --- |
| Vision system (root, event source) | `ns=<vision>;s=VisionMachine` | event subscription |
| Start job | `…s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob` | Call |
| Abort job | `…AutomaticModeStateMachine.Stop` | Call |
| Start continuous operation | `…AutomaticModeStateMachine.StartContinuous` | Call |
| Abort continuous operation | `…AutomaticModeStateMachine.Abort` | Call |
| Halt / clear fault | `…VisionStateMachine.Halt` or `.Reset` | Call |
| Last result (JSON) | `ns=<vision>;s=VisionMachine.LatestResultJson` | Read / subscribe |
| Last result (40100 route) | `…ResultManagement/Results/LatestResult` → `ResultContent[0]` | Read |
| Live stream image | `ns=<vision>;s=VisionMachine.LatestCameraFrame` | Subscribe |
| Overlay mode | `ns=<vision>;s=VisionMachine.CameraStreamMode` | **Write** |
| Calibration progress | `ns=<vision>;s=VisionMachine.CalibrationProgress` | Subscribe |
| Control calibration | `…VisionMachine.{StartCalibration,CaptureCalibrationSample,FinishCalibration,AbortCalibration}` | Call |
| **State of the system** | `ns=<vision>;s=VisionMachine.VisionAsset.Health.DeviceHealth` | Read / subscribe |
| State alarms | `…VisionAsset.Health.DeviceHealthAlarms.{FailureAlarm,CheckFunctionAlarm,OffSpecAlarm}` | Event subscription on `VisionMachine` |
| **State of the camera** | `ns=<vision>;s=VisionMachine.VisionAsset.ImageSensor.Health.DeviceHealth` | Read / subscribe |
| Part 10 program | `ns=<vision>;s=VisionProgram` | see 13.5 |

Nodes with a caveat: `LatestCameraFrame` and `CameraStreamMode` only if
`camera_stream` is configured; `CalibrationProgress` and the calibration methods
only if `apriltag` is configured; the `VisionAsset` nodes only if `assets` are
configured. A subscription to a missing node simply delivers no value.

### 13.2 `StartSingleJob`

Input `(MeasId, PartId, RecipeId, ProductId, Parameters)` — all strings,
`Parameters` a string array with at most 16 entries. Output
`(JobId: String, Error: Int32)`. The call is **non-blocking**: it only acknowledges
acceptance; the result arrives via event.

`RecipeId` selects the job: `calibration` (readiness check) or `apriltag` (real
detection). Empty or unknown -> `Error=4`. Details in section 5.

| `Error` | Name | Meaning |
| --- | --- | --- |
| 0 | `OK` | accepted |
| 1 | `INVALID_STATE` | automaton not `Ready` |
| 2 | `INVALID_ARGUMENT` | e.g. overlong `MeasId` or too many `Parameters` |
| 3 | `BUSY` | a job or a calibration session is already running |
| 4 | `UNKNOWN_RECIPE` | `RecipeId` not permitted |
| 5 | `DETECTION_FAILED` | detection failed (also: no camera image) |
| 6 | `INTERNAL` | unexpected error |
| 7 | `CANCELLED` | job aborted |

### 13.3 Events

All emitted by the `VisionMachine` node, types in the **MachineVision** namespace.
Without an explicit event type list in the subscription, **no** payload arrives
(section 7.1), and the subscription must sit on `VisionMachine`, not on the server
object (7.2).

| Event | When |
| --- | --- |
| `JobStartedEvent` | job accepted |
| `StateChangedEvent` | every state change of both automata |
| `AcquisitionDoneEvent` | image capture done |
| `ResultReadyEvent` | result available — **carries the JSON in `ResultContent[0]`** |
| `ReadyEvent` | ready for capture again |

In addition, the **state alarms** per OPC 40100-2, with types in the **DI**
namespace, likewise emitted by `VisionMachine`:

| Event | Severity | When |
| --- | --- | --- |
| `FailureAlarmType` | 900 | camera delivers no image |
| `OffSpecAlarmType` | 500 | camera image is stale |
| `CheckFunctionAlarmType` | 300 | camera is being opened or restarted |

At most one is active; `NORMAL` clears the previous one without firing a new one.
`SourceNode` names the affected component. Full details in 11.4.

Correlation via `jobId`. No polling needed: the result travels along in the event.

### 13.4 Payload

`ResultReadyEvent` and `LatestResultJson` carry the same JSON, according to schema
`wsc.vision.detections/1`. Field meanings are in section 6 — the format has been
**unchanged** since its introduction and remains so.

### 13.5 Part 10 program

The same job via the cell's generic operating style (`ProgramStateMachineType`), as
used by Conveyor and CardDispenser:

| Node | Direction | Content |
| --- | --- | --- |
| `VisionProgram/ParameterSet/RecipeId` | write | as above |
| `VisionProgram/ParameterSet/Continuous` | write | `Boolean` — continuous operation |
| `VisionProgram/ResultSet/JobId` | read | current or last job Id |
| `VisionProgram/ResultSet/ErrorCode` | read | error code from 13.2 |
| `VisionProgram/ResultSet/ExecutionMode` | read | execution mode |

In addition, `ResultSet` mirrors `LatestResultJson`, `LatestCameraFrame`,
`CameraStreamMode` and `CalibrationProgress`, so that a generic client finds
everything in one place. Complete details in
[`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).

### 13.6 Operation

| | |
| --- | --- |
| Endpoint | `opc.tcp://<LAN-IPv4>:4840/raspi/server/`, security `NoSecurity` |
| systemd | `opcua-server.service`, `Restart=always`, `RestartSec=5` |
| Discoverable via | mDNS (`_opcua-tcp._tcp.local.`) **and** LDS registration with the aggregation server; for cell operation the LDS registration counts |
| Two instances | ceiling Pi (`cam_ceiling`, Picamera2) and hand Pi (`cam_flange`, RealSense) — **same interface**, different `visionSystemId` and `frameId` |

### 13.7 What a client must be able to do at minimum

1. Connect, and resolve namespace indices via the URIs.
2. Subscribe to `VisionMachine`, **with** an event type list.
3. Call `StartSingleJob`, evaluate `Error`, note the `JobId`.
4. Receive `ResultReadyEvent`, read `ResultContent[0]` as JSON, and assign it via `jobId`.
5. Survive a connection drop and reconnect — the server can deliberately terminate
   itself when the camera hangs (11.4) and is then restarted by systemd.
6. **Read the camera state from `DeviceHealth`, not from the image.** When the camera
   hangs, the live image freezes instead of disappearing (11.4). Whoever wants to
   show a traffic light subscribes to the state node; whoever wants to react to
   transitions additionally subscribes to the alarms.

Point 5 is the only one still missing in the current backend implementation
(`vision-system-integration.md`, risk R9). Point 6 is implemented in the frontend
(traffic light in the camera panel).

## 14. Layer 2 run (Hand Pi only)

The hand Pi performs the fine measurement itself:
1. It first moves to the world tag and anchors there (`apriltag-hand-auge-ankern.md`).
2. It re-plans each module with the **measured** base.
3. It measures each module.

Frontend and backend only pass on poses. Planning and flow live in
`src/vision_server/layer2/`. The work plan with all JSON formats is
[`arbeitsplaene/layer2-lauf-hand-pi.md`](arbeitsplaene/layer2-lauf-hand-pi.md).

The nodes exist only if `AprilTagProfileConfig.hand_eye_path` is set, i.e. on the Pi
with `frame_id = cam_flange`. All of them sit under `VisionMachine` and are
additionally linked under `VisionProgram`:

| Element | NodeId | Signature / Type |
| --- | --- | --- |
| `StartLayer2Run` | `ns=<vision>;s=VisionMachine.StartLayer2Run` | `(RunJson: String) -> (Error: Int32)` |
| `ReportRobotPose` | `ns=<vision>;s=VisionMachine.ReportRobotPose` | `(ReportJson: String) -> (Error: Int32)` |
| `Layer2Target` | `ns=<vision>;s=VisionMachine.Layer2Target` | String, JSON `wsc.vision.layer2.target/1`, `""` when there is no target |
| `Layer2Status` | `ns=<vision>;s=VisionMachine.Layer2Status` | String, JSON `wsc.vision.layer2.status/1` |

**Flow from the client's perspective:**
1. Call `StartLayer2Run`. Pass the robot base and the modules from Layer 1, with the
   tag positions from the module catalog (`wsc.vision.layer2.run/1`).
2. Subscribe to `Layer2Target`. If a target is there, drive the robot with the flange
   to `flangeInBase`.
3. Call `ReportRobotPose` with the flange pose **reported by the robot**. If the move
   failed, call it with `reached: false`. The Pi then starts a normal `apriltag` job
   with this pose as parameter. The same events and the same `LatestResultJson` arise
   as with `StartSingleJob`.
4. After the job, the next target is in `Layer2Target`. `Layer2Status` carries, per
   step, `status`, `detail` and, for modules, the result (`result.frameId`,
   `result.detections`).
5. At the end, `state` is `finished` or `aborted`. `Stop` aborts the run at any time.

**One calculation for world tag and module.** The camera stands 0.3 m in front of the
tag and looks at it. From this, the flange is derived with `inv(T_flansch_cam)`, and
the drive target with `inv(T_world_base)`. The only difference is the origin of the
tag pose:
- World tag: from the tag map, namely the one nearest to the base.
- Module: module pose from Layer 1 times tag position. The tag that points most
  toward the base is taken.

**Approach** ([`arbeitsplaene/layer2-annaeherung.md`](arbeitsplaene/layer2-annaeherung.md)):
before each anchor and measurement step comes a step with `purpose: "approach"`. In
this step the camera stands 0.6 m in front of the tag (`options.approachStandoffM`,
`0` = off). The image of this step measures the tag, and the Pi re-plans the close
target from it. Anchoring and measuring happen only in the close image. The client
drives the approach like any other step.

**Flange frame:** `flangeInBase` is the pose of the mechanical tool flange without
gripper, and exactly this link is what `T_flansch_cam` is calibrated against. The links
are: EVA/Demo1 `model_6`, UR5e `flange`, FR3 `eef_attach_point`.

**When the anchor applies:** `locate_modules` skips world tags. The anchor job
therefore often ends with `DETECTION_FAILED`. It nevertheless counts as successful if
afterwards a **new** `RobotAnchor` is present in the source. Without an anchor, the
run aborts, because no world poses would be created.

**Error codes:**

| Code | When |
| --- | --- |
| `INVALID_ARGUMENT` | JSON invalid |
| `INVALID_STATE` | no hand-eye file; no surveyed world tag; `ReportRobotPose` without a waiting target or with a wrong `stepIndex` |
| `BUSY` | job, calibration, or a run already active |

**Locks:** During a run, `StartCalibration` and `SetTagMap` answer with `BUSY`,
because a new tag map discards the anchor. `StartSingleJob` remains allowed, for
debugging.

**Still open:** the robot servers do not currently know a Cartesian motion command.
As long as this is the case, a client can only operate the run as a dry run: it
reports the target as reached itself. The Pi then anchors at a pose that nobody has
driven.

### 14.1 Oblique view

If the frontal view of a tag is out of reach, the planner tilts the camera around the
tag center toward the robot base, in steps up to `options.maxTiltDeg` (default 60°,
`0` = always frontal). Distance and view of the tag center remain the same.
`Layer2Target.tiltDeg` says how obliquely the camera looks. Details are in
[`arbeitsplaene/layer2-schraege-ansicht.md`](arbeitsplaene/layer2-schraege-ansicht.md).

## 15. Hand-eye calibration via OPC UA (Hand Pi only)

This makes it possible to determine `T_flansch_cam` from the frontend, without the
CLI. The flow follows the pattern of the camera calibration (section 12). All JSON
formats are in the work plan [`arbeitsplaene/handeye-ueber-opcua.md`](arbeitsplaene/handeye-ueber-opcua.md).
The nodes exist only with `hand_eye_path`, i.e. on the Pi with `frame_id = cam_flange`.
They sit under `VisionMachine` and are additionally linked under `VisionProgram`.

| Element | Signature / Type |
| --- | --- |
| `StartHandEyeCalibration` | `(OptionsJson: String) -> (Error: Int32)`, `wsc.vision.handeye.start/1` |
| `CaptureHandEyeSample` | `(SampleJson: String) -> (Error: Int32)`, `wsc.vision.handeye.sample/1` |
| `FinishHandEyeCalibration` | `() -> (Error: Int32)` |
| `AbortHandEyeCalibration` | `() -> (Error: Int32)` |
| `HandEyeStatus` | String, JSON `wsc.vision.handeye.status/1` |

**Flow:**
1. Choose the tag that stays in place.
2. Move the robot into various poses. For each pose, call `CaptureHandEyeSample` with
   the **reported** flange pose (flange links as in section 14).
3. Call `FinishHandEyeCalibration`. The Pi computes according to Park–Martin; the
   position of the tag does not need to be known for this. It writes the result to
   `data/handeye/cam_flange.json` and copies the old file beforehand next to it. The
   new calibration applies immediately, without a restart. An existing anchor is
   discarded in the process.

If `tagInBase` is known from the catalog, the Pi only checks it against
(`knownTagCheckM`/`Deg`). It does not enter the calculation.

**Locks:** During a session, `StartLayer2Run`, `StartCalibration` and `SetTagMap`
answer with `BUSY`.

### 15.1 Chessboard instead of tag

Instead of a tag, the Pi can measure a calibration chessboard as a fixed target.
Details are in [`arbeitsplaene/handeye-schachbrett-automatik.md`](arbeitsplaene/handeye-schachbrett-automatik.md).
The laser-engraved board with 6×7 squares of 23 mm is used. **Superseded as of
11.11.2026:** the board dimensions can be entered freely (section 15.7); the numbers
here are only an example.

```json
{ "schema": "wsc.vision.handeye.start/1",
  "board": { "type": "chessboard", "cols": 5, "rows": 6, "squareSizeM": 0.023 } }
```

- `cols`/`rows` are the **inner corners**. `cols + rows` must be odd, otherwise
  `INVALID_ARGUMENT` is returned, because only then can the board, rotated by 180°,
  be told apart from the original.
- The board frame is the same in every image: origin at the first inner corner whose
  square is black, +z into the board (`tagloc.boards.canonical_chessboard_corners`).
- Every capture needs the **entire** board in the image. Otherwise it is discarded
  (`DETECTION_FAILED`), and the session continues.
- The status then has `tagId = -1` and `target = {"type": "chessboard", ...}`.
  `samples[].camToTag` is `T_cam_board`.

### 15.2 Quality figures and outliers (from 22.10.2026)

- `result.rmsPositionM`/`rmsRotationDeg` are the **actual RMS**, and
  `maxPositionM`/`maxRotationDeg` the **maximum** of the deviation of the board or tag
  pose from the mean over the captures used. Previously the maximum was listed under
  the name RMS.
- After `FinishHandEyeCalibration`, each capture has `samples[i].residualM`,
  `residualDeg` and `rejected`. A capture whose pose deviates from the mean of the
  others by more than median + 3 × MAD (at least 2 mm / 0.3°) is discarded, and the
  calibration is re-solved (at most a quarter of the captures, never below 4). `result.rejectedCount`
  and `result.rejectedSamples` summarize this; `sampleCount` counts only the ones used.
- The file and `current` additionally carry `maxPositionM`/`maxRotationDeg`.

### 15.3 Saved calibration in the status

`HandEyeStatus` has the field `current`. It contains the calibration from
`data/handeye/cam_flange.json`: `{poseFlangeCam, calibrationId, rmsPositionM, rmsRotationDeg, sampleCount}`;
without a file, `null`.

Unlike `result`, `current` is also there after a server restart and without a running
session. The frontend thus shows the hand camera at the flange of the robot twin.

### 15.4 `MeasureBoard`: center the board before the capture

With `MeasureBoard`, the controller brings the chessboard to the center of the image
before it calls `CaptureHandEyeSample`. The Pi only measures and sends no motion
commands. Details in the work plan [`arbeitsplaene/hand-auge-brett-zentrieren.md`](arbeitsplaene/hand-auge-brett-zentrieren.md).

| Element | Signature / Type |
| --- | --- |

| --- | --- |
| `MeasureBoard` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, `wsc.vision.measureboard.request/1` → `…result/1` |

- **Request:** `{"board": {"type","cols","rows","squareSizeM"}?, "frames": 1..10 (3)?}`. Without `board`, the board from the configuration applies.
- **Images:** Only images that arrive **after** the call count. This also applies to `MeasureTags` and `CaptureHandEyeSample`.
- **Error:** `OK` (also with `found: false`), `DETECTION_FAILED` (`error: "NO_FRAME"`), `INVALID_ARGUMENT` (JSON/board invalid), `INVALID_STATE` (not `Operational`), `BUSY` (job or camera calibration). Also runs during an open hand-eye session.
- **Result JSON:** `found`, `complete`, `frameCount`, `framesRequested`, `framesUsed`, `imageSize`, `capturedAt`/`capturedUntil`, `frameId`, `frameConvention`, `intrinsicsSource`, `calibrationId`, `board`, `error`, `message`.
  - **Whole board** (`complete: true`): `poseInCamera {position, orientation}`, `poseOk` (error below the limit of the hand-eye capture), `reprojectionErrorPx`, `distanceM`, `viewAngleDeg`, `cornersPx` (all corners), `centerPx`, `offsetPx`, `nearEdge`.
  - **Board only partially visible** (`found: true`, `complete: false`): `partial {gridCols, gridRows, cornerCount, bboxPx}`, plus `centerPx`, `offsetPx`, `nearEdge`. No pose, because the origin of a partial grid is arbitrary.
  - **Nothing found:** `found: false`, no error.
  - `offsetPx` points from the image center to the centroid of the corners (x to the right, y down). The camera has to move in that direction for the board to move to the center.
- **Board on black backing:** Light squares on a black backing without a light quiet zone (the lab board) are only found by detection on the inverted gray image. `detect_board` therefore tries the inverted image after the original. Corners and origin remain the same; the square color for the origin is still read from the original image.
- **Computation time:** The search runs on an image downscaled to 640 px (longest edge); the corners are then refined in the full image (same corners to < 0.3 px). The partial grid is only searched in the last image. Without a board in the image, the search is the expensive part: one failed attempt on the full image cost seconds, on the Pi a multiple of that. `MeasureTags` and `MeasureBoard` also run during an open hand-eye session (since 22.10.2026).
- **Troubleshooting `"diagnose": true`:** The result then contains `diagnosis` for the last image: `largestGrid {cols, rows}` (largest grid that the fast detector finds around the specified value), `sbExact`, `classicExact`, `sbSwapped`, `tried`, `imageSize`, `grayMin/Max/Mean`, `saturatedFraction`. It costs several detection attempts and is only for analysis.
- **`samples[i].reprojectionErrorPx`** in `HandEyeStatus`: mean reprojection error of the images of this capture, also for discarded ones. `null` if nothing was found.

### Addendum 28.10.2026: refinement and prediction error

Applies from Pi commit `c494f71` (branch `apriltag`) and frontend `6e19c66`, `f5c81ba`. Work plan: [`arbeitsplaene/handeye-genauigkeit.md`](arbeitsplaene/handeye-genauigkeit.md) (status in progress; measurement on the setup is still pending). Sections 15 to 15.4 above continue to apply, insofar as they do not contradict this. The board specification "6×7 Felder" (6×7 squares) in 15.1 is outdated: the board has 7×8 squares, i.e. 6×7 inner corners (see `arbeitsplaene/handeye-schachbrett-automatik.md`, addendum 28.10.2026).

- **Refinement:** With a board, `T_flansch_cam` is refined together with the board pose and the board scale against the pixels of the board corners (`refine_hand_eye_robust`, starting from Park-Martin). Outliers are discarded based on the pixel error per capture. Without a board or without corners, it remains Park-Martin, with a note in `result.warnings`.
- **`FinishHandEyeCalibration`:**
  - `INVALID_STATE` with at least 3 accepted captures and fewer than 3 rotation axes (from 5°). The session remains open; the record gets `outcome: "refused"`.
  - `INVALID_STATE` with fewer than 3 usable poses. The session remains open; record `outcome: "failed"`.
  - `INTERNAL` and state `failed` if the calibration was computed but could not be saved.
  - Otherwise `OK`, state `finished`.
- **`HandEyeStatus`, new fields** (schema `wsc.vision.handeye.status/1`):
  - Top-level `intrinsicsSource`: `"calibration"`, `"realsense_factory"`, `"placeholder"` or `null` (unknown).
  - `samples[i]`: `looErrorPx`, `looErrorM`. Prediction error without this capture; only after `Finish` and not for discarded captures, otherwise `null`.
  - `result`: `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`, `refinementConverged`, `targetInBase`, `warnings`, `sessionPath`. The refined values are `null` without refinement. `targetInBase` is always set: `{"position": [x, y, z], "orientation": [x, y, z, w]}` in meters, base CS. With a board it is the board origin (first inner corner, +z into the board); otherwise the mean over `F · X · C`.
  - `warnings`: list of German texts. These include notes on the factory or placeholder intrinsics, on missing refinement, and on `|boardScale − 1| > 0.3 %` („Brett nachmessen / Intrinsik pruefen“).
  - `current`: additionally `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`, `intrinsicsSource`, each from `data/handeye/cam_flange.json`.
- **`data/handeye/cam_flange.json`:** the same additional keys, only if present.
- **Session record:** `data/handeye/sessions/<UTC-Start>/session.json` (schema `wsc.vision.handeye.session/1`, `outcome` finished, refused or failed) and `sample_NNN.png` per capture. Recompute offline:

  ```
  python -m tagloc.cli.calibrate_handeye --session data/handeye/sessions/<UTC-Start> [--out data/handeye/cam_flange.json] [--no-scale]
  ```

- **Tag path:** Both IPPE solutions are refined with `solvePnPRefineLM`; the solution with the smaller reprojection error wins. The ambiguity ratio remains computed from the raw IPPE errors.
- **Subpixel corners:** The presets `cam_flange` and `cam_ceiling` (since 10.11.2026, work plan [`deckenkamera-subpixel-ecken.md`](arbeitsplaene/deckenkamera-subpixel-ecken.md)) use them with method `subpix`. `VISION_SUBPIXEL_CORNER_REFINEMENT` overrides this: `0`, `none`, `false` switch it off; `1`, `true` switch it on; `apriltag` or `subpix` switch it on with this method. Any other value aborts the start with `ValueError`.

### 15.5 Raw image capture and offline calibration (09.11.2026)

The existing frontend button "Kalibrierung starten" (start calibration) uses
`captureOnly: true` in `StartHandEyeCalibration` for a chessboard. (Since 11.11.2026
there is additionally `fullCalibration: true`, section 15.6. `captureOnly` and
`fullCalibration` together are invalid.) The Pi uses the **normally configured camera
stream**, together with its calibrated intrinsics.
`CaptureHandEyeSample` additionally receives `poseId: "pose_NNN"`, `poseGroup`
(`A`–`E`) and `jointAnglesRad` (seven angles); `flangeInBase` remains the FK of the
measured joint angles. Each capture is saved immediately under
`data/rohbilder/calibration/<UTC>_<ID>/poses/pose_NNN/`: `camera.png` is the unchanged
image, and `robot_state.json` contains the pose ID, the 4×4 transformation
`T_base_flange`, the joint angles and the image timestamp. `config.json` contains the
board, coordinate conventions and intrinsics.

`HandEyeStatus.sessionPath` names the session folder. In this mode,
`FinishHandEyeCalibration` only closes the capture; neither computation nor activation
takes place online. `AbortHandEyeCalibration` keeps the images written so far. If
joints move during a capture, `CaptureHandEyeSample` with `invalidatePoseId` and
`reason` can mark the same pose as invalid; `quality.json` documents this, while the
raw image and `robot_state.json` remain unchanged. The offline evaluation excludes such
captures. The existing tag and board calls without `captureOnly` keep their semantics.

Offline call: `python -m tagloc.cli.calibrate_handeye_offline <sitzungsordner>
[--pnp iterative|ippe]`. Results are located exclusively in `results/`: detection
cache, experiment JSON, HTML comparison and, when the overall solution converges,
`candidate_handeye_<pnp>.json`. The candidate file is compatible with the existing
hand-eye file format and is not activated automatically.

### 15.6 Full calibration on the Pi: `fullCalibration` (11.11.2026)

The hand-eye calibration is now the **entire calibration of the hand camera**:
intrinsics and hand-eye are computed on the Pi from the same raw images and adopted
together.

- **Start:** `StartHandEyeCalibration` with `"fullCalibration": true` in the options, in
  addition to `board`. The Pi records raw images as with `captureOnly` (folder
  `data/rohbilder/calibration/<UTC>_<id>/`, per pose `camera.png` and `robot_state.json`,
  groups A–F). Without a calibration board, the specification is invalid.
- `captureOnly: true` continues to exist: it only records and computes nothing.
  `captureOnly` and `fullCalibration` together are invalid.
- **Computation in `FinishHandEyeCalibration`** (on the Pi, without factory intrinsics,
  without starting value):
  - (a) Intrinsics: `cv2.calibrateCamera` over all images with at least 12 ChArUco
    corners or a complete chessboard. Full model k1, k2, p1, p2, k3. At least 10 images.
  - (b) Hand-eye with these intrinsics: PnP per pose (at least 25 ChArUco corners or a
    full chessboard, PnP RMS at most 1.5 px). Starting solution Park (or `park_numpy`
    under OpenCV 5), then joint reprojection refinement of hand-eye and board pose with
    scipy `least_squares` (loss `soft_l1`). At least 10 poses.
  - The flange pose is the nominal FK from the measured joint angles. There is no
    kinematic correction.
  - Results are additionally located under `<sitzung>/results/intrinsics_pi.json` and
    `<sitzung>/results/handeye_pi.json`.
- **Too few images or poses:** `INVALID_STATE`, message „Nicht berechnet: …“ (not
  computed: …). The session remains open; captures can be added. If scipy is missing on
  the Pi, the message reads „scipy fehlt auf dem Pi (pip install scipy)“ (scipy is
  missing on the Pi).
- **Writing:** The Pi writes `data/calibration/cam_flange.json` and
  `data/handeye/cam_flange.json`, each with a `.bak` backup of the old file and the same
  `calibrationId` `cam_flange@<sitzung>-pi`, and adopts both live. Both belong together:
  if writing fails, the Pi restores both files from the backup.
- **`config.json` of the session** contains `intrinsics: null` for `fullCalibration`,
  because the intrinsics only come into being at completion.
- **Status:** `HandEyeStatus` has the field `fullCalibration` (bool). With
  `fullCalibration`, `result` contains:

  ```json
  {"kind": "intrinsicsAndHandEye", "sessionPath": "...",
   "intrinsics": {"imageSize", "fx", "fy", "cx", "cy", "distortion", "rmsPx", "sampleCount", "calibrationId"},
   "handEye": {"translationMm", "rotationDeg", "reprojectionRmsPx", "boardSpreadRmsMm", "boardSpreadRmsDeg", "sampleCount", "initialMethod"},
   "excluded": [{"poseId", "reason"}],
   "backups": {"intrinsics", "handEye"}}
  ```

### 15.7 Board dimensions freely enterable (11.11.2026)

The fixed board specifications (chessboard 7×10 or 23 mm, ChArUco 11×7 with 25 mm or
18.5 mm) have been replaced. This applies to `board` in `StartHandEyeCalibration` and
to `MeasureBoard`:

- **Chessboard:** `cols`/`rows` inner corners, at least 3. `cols + rows` must be odd
  (otherwise the 180° rotation is not unambiguous). `squareSizeM` 0.005 to 0.100 m.
- **ChArUco:** `cols`/`rows` squares, at least 3. `squareSizeM` 0.005 to 0.100 m.
  `markerSizeM` positive and smaller than `squareSizeM`. `dictionary` from `DICT_4X4`,
  `DICT_5X5`, `DICT_6X6`, `DICT_7X7`, each with the suffix `_50`, `_100`, `_250` or
  `_1000`. The marker count `floor(cols·rows/2)` must not exceed the size of the
  dictionary.
- The entered dimensions are used **without a scale factor**. The lab board has been
  re-measured: square 24.982 mm, marker 18.487 mm.
- **Frontend** (`webskillcomposition`, branch `ADP_Lokaliserung`): for ChArUco, the
  squares across/up (cols/rows), square size, marker size and dictionary can be entered.
  The browser remembers the entries.

## 16. Cell monitoring (ceiling camera only)

The ceiling camera continuously evaluates whether a module has been moved, added or
removed, and reports only **confirmed** changes. Formats, rules and defaults are in the
work plan [`arbeitsplaene/zellen-ueberwachung.md`](arbeitsplaene/zellen-ueberwachung.md).
The nodes exist only with AprilTag and without `hand_eye_path`. They sit under
`VisionMachine` and are additionally linked under `VisionProgram`.

| Element | Signature / Type |
| --- | --- |
| `StartCellWatch` | `(OptionsJson: String) -> (Error: Int32)`, `wsc.vision.cellwatch.start/1` |
| `StopCellWatch` | `() -> (Error: Int32)` |
| `PauseCellWatch` | `(ReasonText: String, MaxPauseS: Double) -> (Error: Int32)` (since 22.10.2026) |
| `ResumeCellWatch` | `() -> (Error: Int32)` (since 22.10.2026) |
| `CellWatchStatus` | String, JSON `wsc.vision.cellwatch.status/1` |

- **Reference:** the result of a layer-1 job (`referenceJobId`, one of the last 20) or,
  with `null`, the current stable observation ("Ignorieren" (ignore)).
- **Valid images:** Only images with a jointly solved camera pose (`joint`, at least two
  world tags) count. Others are "keine Aussage" (no statement), not "Tag fehlt" (tag
  missing).
- **Evaluation:** Computed per tag with the median over a time window, with a
  confirmation time and hysteresis.
- **Resources:** The monitoring uses the frames of the shared camera and its own
  detector. During a job or a calibration, it is set to `paused`.
- **`visibleTagIds`** (additive, int array): all tag IDs, world and module tags, that
  were recognized in the image of the last check. The field is updated at every check,
  **also during `paused`**, if the image does not permit a statement: then the operator
  sees which world tag is missing. `null` as long as no image has been checked yet, and
  with `off`. If monitoring is paused due to a job or a calibration, no image is checked.
  The field then shows the last state.
- **One world tag suffices (`minWorldTags`, default 1):** Monitoring runs as soon as at
  least one world tag is visible. The camera is firmly mounted, and its pose does not
  change. Therefore the Pi estimates it only from images with **at least two** world tags
  (joint PnP, ~6 mm), remembers it (mean of the last 20), and uses it when exactly one
  world tag is visible. Estimated anew from a single tag, it would be inaccurate by
  centimeters from 2.5 m (simulated: median 40 mm, outliers up to 1.5 m due to tilted
  solutions, 31–38 % of images over 5 cm). With the remembered pose, the module error in
  the simulation was 12 mm median, at most 46 mm, never over 5 cm. The single visible
  world tag is only checked for consistency: if its corners lie more than
  `storedPoseMarginPx` (default 2 px) above the value they had when the pose was stored,
  the camera has probably moved. The image then does not count, and after 5 such images
  in succession the Pi discards the pose and again needs two world tags. Measured, this
  detects shifts from about 7 mm and rotations from 0.1°. `minWorldTags: 2` is the old
  behavior. A new tag map (`SetTagMap`) discards the remembered pose.
- **The remembered pose survives restarts (since 22.10.2026):** Otherwise monitoring
  paused after every restart until two world tags had been visible at the same time once.
  Two ways:
  - **File:** The pose lies in `cellwatch_camera_pose.json` next to the camera calibration
    (`data/calibration/`), together with the tag map against which it was estimated, and
    the baseline per world tag. It is saved as soon as 5 poses are available, thereafter
    at most every 10 min (SD card). It is restored at startup.
  - **Reference scan:** `cameraLocalization` with `solver: "joint"` additionally has
    `poseInWorld` and `tagErrorsPx`. `StartCellWatch` with `referenceJobId` adopts this
    pose if none is remembered yet, or if it deviates significantly (> 15 mm / 0.5°) from
    the remembered one, because then the camera was moved. If it matches the remembered
    one, the pose averaged over many images remains.
  - **Safeguarded:** A restored or adopted pose is checked for consistency in exactly the
    same way. If the camera was moved during the pause, the one world tag does not fit,
    and the pose is dropped along with the file. A changed tag map (`SetTagMap`) likewise
    invalidates it. A missing or unusable file is not an error; then the Pi pauses as
    before, until two world tags have been visible.
  - **Status:** `cameraPose: {source, poseCount, savedAt}`. `source` is `measured`
    (measured from ≥ 2 world tags), `file` (restored), `scan` (from the reference scan) or
    `null` (no pose; then the Pi pauses with only one world tag).
- **Pauses due to localization** all name the "Welt-Tag" (world tag) in `message`. The
  frontend recognizes the pause from this, using these texts: "kein Welt-Tag im Bild" (no
  world tag in the image), "Kamerapose noch nicht bekannt – erst müssen 2 Welt-Tags
  gleichzeitig zu sehen sein" (camera pose not yet known – 2 world tags must first be
  visible at the same time) (start with only one world tag), "Welt-Tag N passt nicht zur
  gespeicherten Kamerapose … Kamera bewegt?" (world tag N does not match the stored camera
  pose … camera moved?), and "… Pose verworfen" (… pose discarded).
- **`pending`** (additive): what currently looks like a change but is not yet confirmed,
  `[{tagId, moduleId, kind, since, distanceM, rotationDeg, confirmInS}]`. This lets the
  frontend immediately show "verschoben um 6 cm, Meldung in 3 s" (moved 6 cm, message in
  3 s). `since` is, for `moved`, the start of the uninterrupted deviation; for
  `disappeared`, the last sighting (entry only after `persistS` without sighting); for
  `appeared`, the first sighting. `confirmInS` is the time until the message, if the
  situation stays this way; for `disappeared` it is estimated, because the number of
  missed images depends on the detection rate. After confirmation, the entry moves to
  `changes`.
- **Rotation for flat modules:** If a module lies flat (vertical axis ≤ 30° to world Z),
  its yaw angle counts even with an ambiguous pose. From a top view this is almost always
  the case. For vertical surfaces, ambiguous still means only position.
- **Pause from outside (`PauseCellWatch` / `ResumeCellWatch`, since 22.10.2026):** The
  frontend pauses monitoring while the robot is moving, because the arm and gripper
  occlude tags and would simulate changes. `PauseCellWatch("Roboter fährt", 0)` sets
  `state: "paused"` with `message = ReasonText` (empty: "Ueberwachung pausiert"
  (monitoring paused)). While the pause applies, the Pi evaluates **no** image and records
  nothing in the history. `MaxPauseS <= 0` means 600 s; more than 3600 s is cut down to
  3600. After expiry, monitoring continues on its own, even without `ResumeCellWatch`
  (frontend gone, robot still). `ResumeCellWatch` ends the pause. Afterwards all timers
  restart, all tags count as just seen, candidates (`pending`) are discarded, and
  confirmed messages (`changes`) remain until the usual hysteresis clears them. Both are
  idempotent: a second `PauseCellWatch` replaces reason and deadline, and
  `ResumeCellWatch` without a pause is `OK`. If monitoring is not running (`off`,
  `failed`), the response is `INVALID_STATE`; `NaN`/`inf` as `MaxPauseS` is
  `INVALID_ARGUMENT`. Re-arming does not lift the pause, `StopCellWatch` does. The status
  has `manualPause: {reason, resumesAt}` for this, or `null`. The pause due to a job or
  calibration remains unaffected by it.
- **Re-arming:** A new `StartCellWatch` takes over the image history and thereby the
  detection rates. With the same `referenceJobId`, candidates, messages and the
  `referenceId` also remain. This makes it possible to change times and thresholds
  without "disappeared" then needing up to 2 min.
- **`tagVisibility`** (additive): per tag from the reference, or per tag seen,
  `{tagId, seenRatio, framesChecked, lastSeenAt, unreliable}`. `seenRatio` is the share of
  valid images in the last `missingPersistS` seconds in which the tag was recognized. This
  is the stable answer to "is the tag in the image?", unlike
  `visibleTagIds`, which shows only the last image and flickers for a tag at the detection
  limit. `unreliable` is set if the tag is recognized but in fewer than 50 % of the
  images. Then `message` (without alarm) says "unsicher erkannt: Tag 2 (20 %) …"
  (recognized unreliably: tag 2 (20 %) …).
- **Why it is "missing" although the scan finds the tag:** A layer-1 scan evaluates 3–5
  images and reports a tag as soon as it was recognized in one of them. Monitoring checks
  individual images. A tag at the detection limit (small, far away, oblique, at the image
  edge, glare) is therefore present in the scan and often not in monitoring.
- **Timer for `disappeared`:** It measures the time since the tag was last seen. How many
  valid images without the tag are needed depends on its detection rate before the gap:
  enough that an accidental miss is below 0.001 % (`(1 − Rate)^n < 10⁻⁵`), at least 3, at
  most 60. As the rate, cautiously the lower bound of the 95 % confidence interval over the
  history is taken; without history it is 0, and then the upper limit applies. At a 2 s
  cycle there are many gaps per hour, hence this small threshold. In the simulation (20
  hours each), at detection rates of 20 %, 40 %, 60 % and 95 % there were no false alarms.
  A really removed tag was reported after about 22 s (95 %), 32 s (60 %), 52 s (40 %), or
  2 min (20 %). Each individual sighting resets it. Time in `paused` counts, but it does
  not count as "fehlt" (missing): triggering happens only when, afterwards, at least 3
  valid images without the tag are additionally present and it was not seen in the last
  `persistS` window.

## 17. MeasureTags: tags in camera coordinates (Hand Pi only)

For the new layer-2 split, the work is separated: the controller plans, drives and
regulates, while the hand Pi only measures. When the robot is at rest, the controller
calls `MeasureTags`. Formats and rules are in the work plan
[`arbeitsplaene/layer2-measuretags.md`](arbeitsplaene/layer2-measuretags.md).

| Element | Signature / Type |
| --- | --- |
| `MeasureTags` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, `wsc.vision.measuretag.request/1` → `…result/1` |
| `LastTagMeasurement` | String, the last result, for display only |

- **Measurement:** The Pi measures `T_cam_tag` (camera CS as in OpenCV), without world
  CS and without CAD offset. It only uses images that arrive **after** the call, and by
  default averages over 3 of them.
- **Ambiguous orientation:** It is returned and marked with `ambiguous`. In that case,
  control the position, not the rotation.
- **`nearEdge`:** states, per tag, which image edges it comes close to (within 5 %). The
  controller uses this when searching for tags that are initially out of reach, in order
  to pan further. The Pi deliberately does not send motion commands.
- **Origin of the intrinsics:** `intrinsicsSource` says where they come from. Without a
  calibration file, the RealSense uses its factory intrinsics (`realsense_factory`). This
  then also applies to the jobs and the live stream of this Pi.

## 18. SaveRawImage: save the full camera image as PNG

Saves the current full image of the shared camera losslessly as PNG on the Pi, as raw
material for recalculation. Planning and background are in the work plan
[`arbeitsplaene/rohbild-speichern.md`](arbeitsplaene/rohbild-speichern.md).

| Element | Signature / Type |
| --- | --- |
| `SaveRawImage` | `() -> (FileName: String, Error: Int32)`, mirrored on the vision system and under `VisionProgram` |

- **Storage:** `data/rohbilder/<frame_id>_<YYYYmmdd-HHMMSS>.png`. If the name already
  exists, the server appends `_2`, `_3`, … ; a file is never overwritten. `frame_id` is
  that of the AprilTag profile, otherwise the name of the vision system.
- **`FileName`:** path relative to the repo root with `/`, empty on error.
- **`Error`:** `0` saved, `1` no camera image yet, `3` a job is running (the PNG encoding
  of a 12 MP image must not endanger the job timeout), `6` writing failed.
- **Configuration:** `VisionServerConfig.raw_image_dir`; `None` means the method is not
  created. The folder lies under `data/`, which has been versioned since 02.11.2026, so
  the PNGs show up in `git status`. Only commit large raw images deliberately.

## 18a. SaveUseCaseImage: use-case photo per trial

After a cube has been placed in use case 1 of the web app, the robot moves the hand
camera (hand Pi, PI_2) to the photo pose: 20 cm vertically above the target center of the
placement area, computed from the hand-eye calibration (`poseFlangeCam`). The method
stores image and metadata per trial on the Pi and measures the placement area directly
from the image (Δx, Δy, Δθ against the target), so that accuracy and repeatability can be
evaluated. Capture and trial procedure are in the work plan
[`arbeitsplaene/use-case-foto.md`](arbeitsplaene/use-case-foto.md); target, measurement
method and key figures are in the work plan
[`arbeitsplaene/use-case-messung.md`](arbeitsplaene/use-case-messung.md).

| Element | Signature / Type |
| --- | --- |
| `SaveUseCaseImage` | `(Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error: Int32)`, on the vision system (`ns=<own_idx>;s=<vision_system_name>.SaveUseCaseImage`) and, like `SaveRawImage`, mirrored under `VisionProgram` |

- **Storage:** `data/use-case/reihe_NNN/versuch_NNN.png` and `versuch_NNN.json`, each
  zero-padded to three digits (`reihe_001`, `versuch_012`). The trial number is the highest
  existing number in the series plus 1; gaps are not filled. The files are created
  exclusively; a file is never overwritten. If a measurement with a control image exists,
  `versuch_NNN_messung.png` is placed next to it (marker, tolerance circles and measured
  values drawn in).
- **`Series`:** trial series from the field of the web app. Must be `>= 1`, otherwise
  `Error=2`.
- **`MetadataJson`:** JSON sent by the web client, schema `wsc.usecase.trial/1` (target
  pose, table, route, actual joints, etc.). If it is not a valid JSON object, the text is
  stored unchanged as `client_raw`. The server does not check the content.
- **Image selection:** The method takes a camera image that was created at least 0.3 s
  after the call (hand and image settle). It waits at most 5 s for this. During a running
  job, `BUSY` is returned immediately.
- **`FileName`:** path of the PNG relative to the repo root with `/`, empty on error.
- **`ResultJson`:** image measurement as JSON text (`TrialMeasurement.to_dict()`; fields
  see work plan `use-case-messung.md`). Empty (`""`) on error codes. The measurement runs
  before saving (in a thread, so without blocking the server) and ends up unchanged in the
  field `messung` of the trial JSON. **A measurement error never makes the saving fail:**
  the photo is stored anyway, and the measurement then appears as
  `{"ok": false, "grund": "..."}` in `ResultJson` and in `messung`. Without camera
  calibration, the reason reads `Keine Kamerakalibrierung verfuegbar` (no camera
  calibration available).
- **Sticker offset:** Before the measurement, the server reads
  `data/use-case/aufkleber_versatz.json` (`{"x_mm": ..., "y_mm": ...}`, position of the
  marker center relative to the cube center in the marker system) and subtracts it from
  the measurement. If the file is missing, `(0, 0)` applies. The value is determined with
  `tools/use_case_auswertung.py <reihe> --aufkleber-kalibrieren N1 N2`.
- **`Error`:** `0` saved, `1` INVALID_STATE (no fresh image within 5 s), `2`
  INVALID_ARGUMENT (series < 1), `3` BUSY (job running), `6` INTERNAL (storage error,
  exception in the log). The codes `4`, `5` and `7` do not occur with this method. For
  every error code, `FileName` and `ResultJson` are empty.
- **JSON file:** schema `adp.usecase.trial/1`. Fields: `reihe`, `versuch`, `bild` (file
  name of the PNG), `gespeichert` (local time, ISO 8601, to the second), `pi` (Pi metadata:
  `frame_timestamp`, `frame_age_s`, `image_shape`, `frame_id`, `vision_system_name`,
  `vision_system_id`, `hostname`, `endpoint`, `camera_stream`, `apriltag`, `calibration`,
  `hand_eye`; fields that cannot be determined are missing), plus `messung` (image
  measurement, see `ResultJson`; always present for new trials, also as
  `{"ok": false, "grund": ...}`), `client` (parsed object) or `client_raw` (text). The
  complete description of the fields is in the work plan.

- **`pi.intrinsics`** (only with a loaded calibration): the camera matrix and distortion
  actually used, so that the offline evaluation uses the same intrinsics. Fields: `source`
  (`calibration` = calibration file, `realsense_factory` = factory intrinsics of the camera,
  `placeholder` = placeholder), `calibrationId`, `imageSize` (`[width, height]` in px),
  `cameraMatrix` (3x3), `distortionCoefficients`, `distortionModel` (`pinhole` or
  `fisheye`). `source` has the same origin as in the hand-eye session (`intrinsicsSource`).
- **Configuration:** `VisionServerConfig.use_case_dir`, default `<Repo>/data/use-case`.
  `None` means the method is not created. The method is created on every Pi with an open
  camera; the web app calls it on the hand Pi (PI_2). The folder lies under `data/`, which
  has been versioned since 02.11.2026 (`3535115`); the images therefore show up in
  `git status` and are only committed deliberately.

## 19. Survey tag position: MeasureTagReference / PlanTagReferenceView (Hand Pi only)

The hand camera measures the position of a module tag relative to two reference edges of
the module that lie in the same image. It replaces the folding ruler (Zollstock). The
controller asks with `PlanTagReferenceView` where the camera should go (computes only, no
image), drives there, and calls `MeasureTagReference`. Procedure and rationale are in the
work plan [`arbeitsplaene/tag-einmessen-bezugskanten.md`](arbeitsplaene/tag-einmessen-bezugskanten.md),
the method in [`apriltag-referenz.md`](apriltag-referenz.md) (section 14).

| Element | NodeId | Signature / Type |
| --- | --- | --- |
| `MeasureTagReference` | `ns=<vision>;s=VisionMachine.MeasureTagReference` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, measures with images |
| `PlanTagReferenceView` | `ns=<vision>;s=VisionMachine.PlanTagReferenceView` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, computes only |
| `LastTagReference` | `ns=<vision>;s=VisionMachine.LastTagReference` | String (JSON), for display only |

The methods sit under `VisionMachine` and are linked under `VisionProgram`. They exist
only on the hand Pi (`hand_eye_path` set).

**Error codes per method** (numbers as in section 6, `VisionErrorCode`):

| Error | `MeasureTagReference` | `PlanTagReferenceView` |
| --- | --- | --- |
| 0 `OK` | measured; also if edges are missing (then `dofs` reduced or `none`) | planned, also with `fits: false` |
| 5 `DETECTION_FAILED` | `NO_FRAME`, `TAG_NOT_SEEN`; also a `ValueError` of the measurement (e.g. image size does not match the calibration) | not applicable (no image) |
| 2 `INVALID_ARGUMENT` | request JSON or values invalid | as left |
| 1 `INVALID_STATE` | vision system not `Operational` | as left |
| 3 `BUSY` | job, camera calibration, hand-eye session, `MeasureTags` or another survey is running | no `BUSY`: computes only, no image |

- On rejection, the JSON contains `"<CODE>: Text"`; for measurement errors (`NO_FRAME`,
  `TAG_NOT_SEEN`) the bare code. `ResultJson` is always valid JSON.
- `LastTagReference` is written after every measurement, also with `DETECTION_FAILED`, but
  not for rejections (`INVALID_ARGUMENT`, `INVALID_STATE`, `BUSY`).
- **BUSY rules:** While `MeasureTagReference` runs, `MeasureTags` answers with `BUSY`.
  Conversely, `MeasureTagReference` reports `BUSY` as long as `MeasureTags` runs. Behavior
  change: `MeasureTags` now additionally answers with `BUSY` while a survey is running.
  `PlanTagReferenceView` is not blocked by this.

**Request** `wsc.vision.tagreference.request/1` (for both methods):

```json
{ "schema": "wsc.vision.tagreference.request/1",
  "tagId": 7, "tagSizeM": 0.08,
  "moduleTagGuess": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "lines": [ { "id": "A", "start": [x,y,z], "end": [x,y,z] },
             { "id": "B", "start": [x,y,z], "end": [x,y,z] } ],
  "searchBandM": 0.015, "frames": 3,
  "view": { "lineExtentM": 0.10, "tiltDeg": 10 } }
```

- `moduleTagGuess`: `T_module_tag` from the catalog (starting value). `lines`: 3D edges in
  the module CS, 1 to 4 of them, each ≥ 2 cm, `id` unique.
- Optional: `tagSizeM` (priority: request > tag map > `config.tag_size_m`), `searchBandM`
  (default 0.015; 0.003 to 0.05 m), `frames` (default 3; 1 to 10), `view.lineExtentM`
  (default 0.10; (0, 1] m), `view.tiltDeg` (default **10**; |tiltDeg| < 80).
- `moduleTagGuess.orientation` is normalized; a zero quaternion is invalid. Unknown fields
  are ignored.

**Result** `wsc.vision.tagreference.result/1` (from `MeasureTagReference`):

- `capturedAt`, `capturedUntil`, `imageSize`, `intrinsicsSource` (`calibration` |
  `realsense_factory` | `placeholder`), `framesUsed` (images with a detected tag).
- `tag`: `poseInCamera`, `reprojectionErrorPx`, `viewAngleDeg`, `ambiguous`. `null` in the
  error case.
- `lines[]`: per edge `id`, `found`, `offsetMm`, `angleDeg`, `supportPx`, `residualPx`,
  `contrast`, `candidates`, `ambiguous`, `reason` (`NOT_VISIBLE` | `NO_EDGE` |
  `PERPENDICULAR_TO_TAG`, only for edges not found).
- `correction`: `dofs` (`full` | `partial` | `none`), `deltaMm`, `deltaRotDeg`, `moduleTag`
  as well as `position` and `rotationDeg` (catalog format), `spreadMm`, `spreadDeg`,
  `residualMm`. `null` in the error case.
- `warnings[]` and `error` (`null` on success).
- Ambiguous tag pose: without an unambiguous image, the orientation remains exactly the
  starting value; only the tag origin moves, and there is a warning.

**Planning** `wsc.vision.tagreference.view/1` (from `PlanTagReferenceView`):

```json
{ "schema": "wsc.vision.tagreference.view/1",
  "camInTag": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "standoffM": 0.38, "fits": true, "uncovered": [], "error": null }
```

- `camInTag`: camera pose in the tag CS (OpenCV, +Z out of the lens). The camera looks
  along −tag z.
- `fits: false`: The tag is always in the image; edges are then added one after another by
  distance, for as long as everything fits at `max_standoff`. `uncovered` names the
  remaining edges (possibly `"tag"`).
- Rejection: `camInTag: null`, `fits: false`, `uncovered: []`, `error` as `"<CODE>: Text"`.

The request neither writes to the Pi's tag map nor changes world tags. Module tags and
their pose exist only in the module catalog of the frontend. The flange target is
computed by the controller itself.

### 19.1 Live view and measurement log

Since 21.10.2026 (plan [`arbeitsplaene/tag-einmessen-liveview.md`](arbeitsplaene/tag-einmessen-liveview.md))
the edge search can be seen live in the live stream, and each measurement is stored as a
log on the Pi.

| Element | NodeId | Signature / Type |
| --- | --- | --- |
| `SetTagReferencePreview` | `ns=<vision>;s=VisionMachine.SetTagReferencePreview` | `(RequestJson: String) -> (Error: Int32)` |
| `TagReferencePreviewStatus` | `ns=<vision>;s=VisionMachine.TagReferencePreviewStatus` | String (JSON), subscription possible, at most 3 Hz |
| `GET /tagreference.jpg` | MJPEG port (`http://<host>:<port>/tagreference.jpg`) | JPEG, `Cache-Control: no-store`, CORS `*` |

- **`SetTagReferencePreview`**: `RequestJson` is `wsc.vision.tagreference.request/1`
  (parser as for `MeasureTagReference`; `frames` and `view` are ignored). One tag on, `""`
  off. Error: `OK`, `INVALID_ARGUMENT` (invalid JSON, missing tag, invalid `overlay`),
  `INVALID_STATE` (not `Operational`, or no live-stream overlay). Never `BUSY`. Hand Pi
  only; linked under `VisionProgram`. The preview expires after 120 s without renewal. The
  check also runs outside the `tagreference` mode.
- **`MeasureTagReference`** automatically sets the preview to its own request. The
  signature remains unchanged.
- **Status** `TagReferencePreviewStatus`, schema `wsc.vision.tagreference.preview/1`:
  `{ "schema", "active", "tagId", "tagSeen", "updatedAt", "overlay", "lines": [ {"id", "found", "offsetMm",
  "angleDeg", "ambiguous", "reason", "supportPx", "contrast"} ] }`. Without an active preview:
  `active: false`, `tagId: null`, `lines: []`.
- **Overlay layers** (`overlay` in the request, keys `guess`, `band`, `candidates`,
  `rejected`, `chosen`, `mask`, `labels`, `tagBox`): missing keys = `true`, unknown ones are
  ignored, and a value that is not a bool results in `INVALID_ARGUMENT`. The status shows
  the effective assignment.
- **`saveDebug`** in the request of `MeasureTagReference` (default `true`): `false` creates
  no log, and `debugDir` is then `null`.
- **Measurement log**: per measurement `data/tagreference/<YYYYmmdd-HHMMSS>_tag<id>/`
  (config `tag_reference_debug_dir`, `None` = off) with `request.json`, `result.json`,
  `frame<k>.png` (raw images, lossless) and `overlay.jpg`. At most 50 own folders, cleaned
  up by mtime; the new folder is kept. `ResultJson.debugDir` is the repo-relative path or
  `null`; on write errors there is only a log warning.
- **`GET /tagreference.jpg`** returns `overlay.jpg` from the newest measurement folder.
  404 if no measurement exists.
- **Stream mode `tagreference`**: at most 3 Hz; the last debug image is held for up to 1 s
  between two evaluations. A measurement holds its debug image in the stream for 5 s. Tag
  not in the image: title „Tag 7 nicht im Bild“ (tag 7 not in the image).

**Frontend** (`webskillcomposition`): `setTagReferencePreview` in `tagReferenceCall.ts`
(timeout 10 s); the preview is renewed every 60 s (`PREVIEW_RENEW_MS`). The switches are in
the window "Tags einmessen" (survey tags) and are stored in localStorage under
`wsc.tagReference.settings`. Details: work plan, section C.

## Hand-eye raw capture: chessboard or ChArUco (09.11.2026)

The existing start button of the hand-eye calibration uses `captureOnly: true`. The board
can be selected in the same window. **As of 11.11.2026 superseded:** the board
specifications below are no longer fixed; board and dimensions can be freely entered
(section 15.7). The previous chessboard specification was
`{"type":"chessboard","cols":7,"rows":10,"squareSizeM":0.023}`; `cols`/`rows` count inner
corners here. The previous ChArUco board was
`{"type":"charuco","cols":11,"rows":7,"squareSizeM":0.025,"markerSizeM":0.0185,"dictionary":"DICT_5X5_100"}`
(form unchanged, values freely selectable since 11.11.2026); here `cols`/`rows` count
squares. This `board` specification also applies to `MeasureBoard` and is stored unchanged
in the raw session under `data/rohbilder/calibration/<Sitzung>/config.json`. Images
continue to lie as `poses/pose_NNN/camera.png` next to the corresponding `robot_state.json`.

ChArUco detection requires at least 25 assigned inner corners per capture. Their IDs and
3D object points are stored per pose during offline detection and used for PnP and the
reprojection optimization. Camera intrinsics and the image stream remain the already
configured values. There is no new OPC UA method and no automatic activation of the
offline solution.

Since 11.11.2026 there is additionally `fullCalibration: true` (section 15.6).

