# Vision system — current state

This file describes what is **actually implemented** in the repo. For the backend interface see
[`vision-server-interface.md`](vision-server-interface.md), for the full target plan
[`vision-system-integration.md`](vision-system-integration.md).

## Purpose

**One** OPC UA server on the Raspberry Pi (`src/vision_server/server.py`, port 4840,
endpoint `opc.tcp://<pi>:4840/raspi/server/`) with **two views of the same job**:

| Node | Content |
| --- | --- |
| `Machines/VisionMachine` | **OPC 40100 (Machine Vision)** with the full job flow, implemented in the package `src/vision_server/` |
| `VisionProgram` | **OPC UA Part 10**, generic operator interface on the same `JobRunner` — the entry point for the frontend |

The endpoint path is still called `/raspi/server/`, although the `RaspiDevice` that gave it its
name has been removed: it appears in the mDNS announcement, the LDS registration and every client
configuration.

The vision system implements phases 2 and 3 from part 9 of the plan. The former placeholder recipe
`hello-world` has been dropped; a start without `RecipeId` is an error (`UNKNOWN_RECIPE`). Detection:
the recipe `apriltag` delivers real AprilTag poses including the world-tag reference and hand-eye
anchoring (see [`apriltag-referenz.md`](apriltag-referenz.md)), and `calibration` is a real readiness
check of the camera calibration. Details on both recipes and on the part-2 asset view (AMCM) are in
[`vision-server-interface.md`](vision-server-interface.md).

The vision package is built as an **add-in** (`install_vision_machine(server, config)`) and attaches
its nodes to an existing server. A second server process would be wasteful: it would load the
40100 address space a second time (measured ~110 MB RSS per process) and force the backend into two
sessions. For development and isolated tests the package can additionally be started standalone
(`python -m vision_server`), but that is not the production path.

## Files

| File | Content |
| --- | --- |
| [`src/vision_server/`](../../src/vision_server/) | vision server (package, see module table below) |
| [`src/vision_server/tools/handshake_client.py`](../../src/vision_server/tools/handshake_client.py) | test client: reference implementation of the handshake |
| [`src/vision_server/server.py`](../../src/vision_server/server.py) | the cell's server: Pi identity, mDNS/LDS, installation of the vision system |
| [`src/vision_server/nodesets/Opc.Ua.MachineVision.NodeSet2.xml`](../../src/vision_server/nodesets/Opc.Ua.MachineVision.NodeSet2.xml) | vendored official OPC 40100 nodeset; loaded by **both** servers |
| [`requirements.txt`](../../requirements.txt) | among others `asyncua` |

## Structure of the vision server

| Module | Task |
| --- | --- |
| `__main__.py` | argparse, configuration, start |
| `server.py` | the cell's server: Pi identity, mDNS/LDS, installation of the vision system + Part-10 program |
| `config.py` | `VisionServerConfig` (frozen dataclass), recipe profiles |
| `nodeset_ids.py` | NodeId constants of the nodesets |
| `address_space.py` | nodeset import (MachineVision, DI, Machinery, AMCM), `VisionMachine` instance, `HasNotifier` |
| `asset_model.py` | part-2 asset view (AMCM), `VisionAsset` including the state block (`DeviceHealth`, see `camera_health.py`) |
| `state_machine.py` | binding of both 40100 state machines |
| `events.py` | event generators, `ResultReadyEvent` with payload |
| `result_management.py` | result nodes + JSON mirror nodes |
| `payload.py` | JSON schema `wsc.vision.detections/1` |
| `errors.py` | `VisionJobError`, error codes for the `Error` output |
| `job.py` | validation, state guard, job flow, error path |
| `profiles.py` | `AprilTagProfileConfig` among others — camera, hand-eye path, tag map per Pi |
| `runner/` | package: `install_vision_machine()` (installation, `install.py`) and `run()` (standalone, `standalone.py`); one module per task, see module docstring and `code/zelle-vision-server.md` |
| `vision_program.py` | Part-10 program facade `VisionProgram`, see [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) |
| `camera.py` | `SharedCamera` — one capture loop, shared by detection and live stream |
| `camera_stream.py` | writes camera frames as Base64 JPEG into `LatestCameraFrame` |
| `camera_health.py` | camera state as `DeviceHealth` (OPC 40100-2) |
| `stream_overlay.py` | marks detected tags in the live stream image |
| `calibration_session.py` | `CalibrationSession` — interactive camera calibration via OPC UA |
| `discovery/mdns.py` | mDNS announcement |
| `discovery/lds.py` | LDS registration with the aggregation server |
| `detection/` | strategy `DetectionSource`: `apriltag/` (package, real AprilTag detection), `script_runner.py` (calibration check, recipe `calibration`) |

Connecting real image recognition = a new file in `detection/` plus a registry entry; the server core
knows no image processing. Details on all modules and the complete interface are in
[`vision-server-interface.md`](vision-server-interface.md) section 1.

## Address space (port 4840)

```
Objects/
├── Machines/                            ns=<machinery>;i=1001  (standard folder Machinery)
│   └── VisionMachine                    ns=<vision>;s=VisionMachine  (type: <mv>:VisionSystemType)
│       ├── VisionStateMachine           Preoperational | Halted | Error | Operational
│       │   └── AutomaticModeStateMachine  Initialized | Ready | SingleExecution | ContinuousExecution
│       │       └── StartSingleJob       linked, see vision-server-interface.md 7.5
│       ├── ResultManagement
│       │   └── Results/LatestResult     (type: <mv>:ResultType, overwritten per job)
│       │       └── ResultContent[0]     String  <- JSON payload
│       └── LatestResultJson             String  <- the same JSON, simple node
└── VisionProgram                        ns=<vision>;s=VisionProgram  (type: ProgramStateMachineType)
    ├── ParameterSet/                    RecipeId, Continuous  (writable)
    └── ResultSet/                       JobId, ErrorCode, ExecutionMode
                                         + references to LatestResultJson, among others
```

Shown abridged — the complete, current tree including the part-2 asset view (`VisionAsset` under
AMCM), the four calibration methods (`StartCalibration`, among others), `LatestCameraFrame` and
`CameraStreamMode` is in [`vision-server-interface.md`](vision-server-interface.md) section 1.

`VisionMachine` sits in the Machinery standard folder `Machines` instead of directly under `Objects`.
The reason is not technical but readability: whoever browses the server should see **one** entry
point. For clients this changes nothing — the string NodeId does not depend on the browse position.
Without the loaded Machinery nodeset (i.e. without `config.assets`), the structure falls back to
`Objects`.

The former raspi namespace has been dropped along with `RaspiDevice`; at the same time, with part 2
(AMCM), three further nodesets were added (DI, Machinery, AMCM). The namespace indices have thereby
shifted several times — currently (import order MachineVision → DI → Machinery → AMCM → own vision
namespace): ns=2 MachineVision, ns=4 Machinery, ns=6 the own vision namespace, see
[`vision-server-interface.md`](vision-server-interface.md) section 1. Whoever resolves them via
`get_namespace_index(uri)` notices none of the shifts.

The `VisionMachine` instance gets a **string NodeId** (`ns=<vision>;s=VisionMachine`), whereby
`instantiate()` also creates all children with descriptive, stable NodeIds (e.g.
`…;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob`). This spares the
backend any discovery.

The states of the outer `VisionStateMachine` are mandatory children of the instance and are fetched
by BrowseName. The states of the `AutomaticModeStateMachine`
(`Initialized`/`Ready`/`SingleExecution`/`ContinuousExecution`) are **not** instance children — they
exist only as fixed nodes on the type `VisionAutomaticModeStateMachineType` (i=5056–5059) and are
fetched by NodeId.

## Runtime behavior

- Endpoint `opc.tcp://<LAN-IPv4>:4840/raspi/server/`, listening on `0.0.0.0:4840`
  (`Server.socket_address`). The separation is necessary because the endpoint lands as DiscoveryUrl
  at the discovery server, where `0.0.0.0` would be worthless; via `127.0.0.1` the server remains
  reachable anyway. If no LAN IPv4 can be determined, it stays at `opc.tcp://0.0.0.0:4840/…` and the
  server runs without LDS registration. `SecurityPolicy: NoSecurity`.
- At start, two announcements side by side, both withdrawn on orderly shutdown:
  - **mDNS** (`src/vision_server/discovery/mdns.py`): `_opcua-tcp._tcp.local.`, instance name is the
    vision identity. For clients in the subnet. **On by default** (again since 21.10.2026; from
    23.09. to 21.10.2026 it was off); disable with `OPCUA_MDNS=0`.
  - **LDS registration** (`src/vision_server/discovery/lds.py` via `Server.register_to_discovery()`):
    **on by default** (again since 21.10.2026; from 23.09. to 21.10.2026 it was off, and the server
    did not appear in the aggregation server). Registers at `opc.tcp://10.10.38.27:4840/` and renews
    every 60 s. Disable with `OPCUA_LDS_URL=""`, redirect with another URL. **Only through this** does
    the cell's aggregation server take up the module — mDNS alone is not enough for it, see
    [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) §2.
  If one of the two fails, the server keeps running and logs a warning.
- At vision-system start: `Preoperational → Operational`, inside it `Initialized → Ready`.
- `StartSingleJob` checks state and inputs **synchronously** and acknowledges with `(JobId, Error)`;
  the job itself runs asynchronously and reports via events.
- State sequence of a job: `Ready → SingleExecution → Ready`, plus the events `StateChangedEvent`,
  `JobStartedEvent`, `AcquisitionDoneEvent`, `ResultReadyEvent`, `ReadyEvent` — all emitted by the
  `VisionMachine` node.
- The result JSON travels **in the `ResultReadyEvent`** (`ResultContent[0]`); it is additionally
  written to the result nodes. No polling needed.
- Only **one** job at a time; a second call is rejected with `BUSY`.
- Errors in detection lead via `Error` back to `Operational`, and are reported as a
  `ResultReadyEvent` with `resultState != 0`.

## Starting

```bash
pip install -r requirements.txt

# Cell server (vision system + Part-10 program + mDNS/LDS)
PYTHONPATH=src python3 -m vision_server.server

# Run the handshake once
PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py \
    --url opc.tcp://127.0.0.1:4840/raspi/server/ --recipe-id calibration

# Vision system only, isolated (development)
PYTHONPATH=src python3 -m vision_server --port 4841 --log-level INFO
```

Two switches change the address space or the behavior:

| Variable/Option | Effect |
| --- | --- |
| `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` | AprilTag detection also runs without a real camera calibration (poses then not to scale) — a deliberate, temporary bypass for testing detection/overlay/job path before the real calibration; never active by default, see [`apriltag-referenz.md`](apriltag-referenz.md) |
| `VisionServerConfig.assets` (`config.assets`) | `None` = Part 2 (AMCM) not loaded, saves ~13 MB RSS / ~1.6 s start time; if set, the server additionally loads the DI/Machinery/AMCM nodesets and creates `VisionAsset` |

### Two ways to the same server

| Way | Purpose |
| --- | --- |
| `python3 -m vision_server.server` | development and manual work. Needs `PYTHONPATH=src` or `WorkingDirectory=…/src`. |
| `<Repo>/.venv/bin/python -m vision_server.server` | **the systemd unit on the Pis** (`PYTHONPATH=<Repo>/src`). Template and installation guide: [`deploy/`](../../deploy/README.md). |

On the Pi, the server runs productively as the systemd service `opcua-server.service`
(`Restart=always`, `RestartSec=5`, `/etc/systemd/system/opcua-server.service`, template in `deploy/`).
**Important when testing on the Pi:** no foreground start, that collides with the service (port 4840
occupied) — instead use `systemctl restart opcua-server.service` and test against it with a separate
client.

**Camera watchdog** (`camera.py`): `capture_array()` can hang forever without an error message when
libcamera no longer delivers a frame (Pi 1, 2026-09-22: the live stream showed a frozen image, jobs
failed with „Kein Kamerabild innerhalb von 5.0 s“ (no camera image within 5.0 s)). Every capture
therefore has `frame_timeout_s` (3 s); on a hang the camera is reopened; after `max_reopen_attempts`
(2) unsuccessful reopenings the process terminates hard and systemd restarts it.

**The watchdog monitors only the capture, not the live stream.** Externally it reports via OPC
40100-2: `DeviceHealth` says how the camera is doing, and every state change additionally fires the
matching DI alarm (`camera_health.py`). The live stream is a pure publisher — it keeps publishing even
a stale image. When the camera hangs, the live picture therefore stands still; **that** it stands
still is shown by the traffic light in the frontend, not by the missing image. Fully described in
[`vision-server-interface.md`](vision-server-interface.md) section 11.4.

Search the journal afterwards with
`journalctl -u opcua-server.service | grep -E "haengt|neu geoeffnet|Kamerazustand|beende den Prozess"`.
If the messages pile up, the cause is usually hardware (flat ribbon cable).

## Verified state

Run end-to-end locally against asyncua 2.0.1, in the production configuration (`src/vision_server/server.py`
on 4840) and with a separate client: happy path incl. payload and event order, `BUSY` for two
simultaneous calls, `INVALID_ARGUMENT` for an overlong `MeasId`, `UNKNOWN_RECIPE` for an unknown
recipe, error path via the `Error` state with subsequent resumption.

After removing the CPU temperature demo (legacy items A1–A6), additionally checked: the address space
contains exactly **one** `VisionSystemType` instance, it hangs under `Objects/Machines`, and the
namespace `http://launch-rm.de/raspi` is gone. Recorded in `tests/test_part10_fassade.py`.

## Known limitations

- There is no default job: an empty `RecipeId` is rejected. The recipe `apriltag` delivers real
  poses, `calibration` a real readiness check.
- `frameId` follows the detection source: `world` as soon as a reference tag from the tag map is
  visible, otherwise the camera frame. The hand-eye calibration for layer 2 is implemented
  (`AprilTagProfileConfig.hand_eye_path`, chained in `detection/apriltag/`) — what remains open is
  only the real survey of the hand-eye file per cell, see
  [`vision-server-interface.md`](vision-server-interface.md) section 9.
- Linked are `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt` and `Reset`.
  `SimulationMode` and `SelectModeAutomatic` exist in the address space, but deliberately do nothing —
  reasoning per method in [`vision-server-interface.md`](vision-server-interface.md) section 7.5.
- The `ResultManagement` methods (`GetResultById` & Co.) are not implemented and cannot be called from
  the asyncua client either — they expect structure ExtensionObjects as input (asyncua issue #1693).
- The structure-typed event fields (`ResultId`, `JobId`, …) are sent empty; their values are in the
  JSON payload.
- Events reach only a subscription directly on `VisionMachine`. The `HasNotifier` reference is set,
  but with asyncua it causes no server-side event bubbling.
- Only one vision instance — a second profile (3D) does not exist yet.
- Events reach **only** a subscription on the emitting node. A pure Part-10 client that subscribes
  only to `VisionProgram` therefore does not see the 40100 events; it fetches its result via the value
  change of `VisionProgram/ResultSet/LatestResultJson`. Re-measured on 21.09.2026 against asyncua
  2.0.1 — a `HasEventSource` reference changes nothing there either.
- The ceiling camera (Pi 1) records at full sensor resolution 4056×3040; the live stream and overlay
  run on a second stream scaled by the ISP (960×720), see
  [`vision-server-interface.md`](vision-server-interface.md) section 10.3. A calibration for 2028×1520
  is extrapolated until recalibration (`allow_resolution_mismatch` in the preset `cam_ceiling`) —
  after recalibration at 4056×3040, remove the switch again.

## Camera controls (Picamera2)

For the ceiling camera (Picamera2), libcamera controls can be set without a code change: field
`CameraStreamConfig.camera_controls`, preset `PI_CAMERA_STREAM_PRESETS["cam_ceiling"]["camera_controls"]`
(empty for now, values follow after measurement), and environment variable `VISION_CAMERA_CONTROLS`
(JSON object; per key the variable wins, `null` removes a preset entry):

```
VISION_CAMERA_CONTROLS='{"ExposureValue": -0.5, "Sharpness": 1.5, "NoiseReductionMode": "Fast"}'
```

- The controls are passed as `controls=` into the video configuration and are merged with the
  Picamera2 defaults (`FrameDurationLimits`, `NoiseReductionMode`), not replaced.
- Types: `ExposureTime` in µs (int), `AnalogueGain` (float), `AeEnable` (bool), `ExposureValue`,
  `Sharpness`, `Contrast`, `Brightness`, `Saturation` (float). The enum controls `NoiseReductionMode`,
  `AeMeteringMode`, `AeExposureMode`, `AeConstraintMode`, `AwbMode` may be given by name (`"HighQuality"`,
  `"Off"`, `"Fast"`).
- A name the camera does not know (`Picamera2.camera_controls`), or an unknown enum name, raises
  `ValueError` on opening; the message lists the valid names.
- After warmup the server logs the actual metadata (`ExposureTime`, `AnalogueGain`, `DigitalGain`,
  `Lux`, `ColourTemperature`, `FocusFoM`):
  `journalctl -u opcua-server.service | grep "Kamera-Metadaten"`. Fixed exposure values are chosen
  from this.
- RealSense and OpenCV ignore the field (one-time warning per opening).

**Hardware note:** The ceiling camera has an M12 lens with a fixed aperture. Only the focus (on the
lens, by hand) and the ISP/exposure controls above are adjustable.

Setting it on the Pi: drop-in via `sudo systemctl edit opcua-server.service` with
`Environment='VISION_CAMERA_CONTROLS=...'`, then `sudo systemctl restart opcua-server.service`.
Interfaces: [`arbeitsplaene/kamera-controls-und-detektor-parameter.md`](arbeitsplaene/kamera-controls-und-detektor-parameter.md).
