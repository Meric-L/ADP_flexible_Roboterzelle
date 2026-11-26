# Vision server

The OPC UA server (OPC 40100 Machine Vision) on each of the cell's two Raspberry Pis: it drives the camera, localizes the modules via AprilTags and offers calibration and measurement functions.

## What it is

- Package `src/vision_server/`, started with `python -m vision_server.server` (systemd unit `opcua-server.service`).
- Same code on both Pis; `server.py` selects identity and camera backend per hostname (`PI_IDENTITIES`, `PI_CAMERA_BACKENDS`).
- One server, one tree: `VisionMachine` (40100) and `VisionProgram` (Part 10, see [part10](../part10/README.md)) operate the same jobs.
- The detection math lives in `src/tagloc/` ([tagloc](../tagloc/README.md)); the server is its OPC UA adapter.

| Pi | `visionSystemId` | Camera backend | Pi-only functions |
| --- | --- | --- | --- |
| ceiling (`ADP-Roboter-Lokalisierung`) | `vision-ceiling-01` | Picamera2 | cell watch |
| hand (`ADP-HandInEye-Kamera-Pi`) | `vision-flange-01` | Intel RealSense | Layer 2 run, hand-eye, `MeasureTags`, tag survey |

## How it works

**Startup.** `server.py` builds a `VisionServerConfig` and calls `runner.install_vision_machine` (`runner/install.py`), which runs the steps in a fixed order (one module per step in `runner/`, shared state in `runner/wiring.py`). The sources are opened first; only if all open does the state machine enter `Operational` (`VisionStateMachines` in `state_machine.py`). Then `server.py` announces the server via mDNS and registers it with the discovery server (`discovery/mdns.py`, `discovery/lds.py`).

**Camera.** `SharedCamera` (`camera.py`) is one camera shared by detection and live stream. `camera_health.py` publishes `DeviceHealth` (NAMUR NE 107) from the frame age; if the camera stays failed the server keeps running with `FAILURE` and retries opening it; only after `give_up_after_s` (300 s) without a good frame the process exits and systemd restarts it.

**Jobs and recipes.** `JobRunner` (`job.py`) runs one job at a time (`StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`, `Reset`). The `RecipeId` selects a detection source (`detection/__init__.py`):

| `RecipeId` | Source | Does |
| --- | --- | --- |
| `apriltag` | `AprilTagDetectionSource` (`detection/apriltag/`) | localizes all tagged modules |
| `calibration` | `ScriptDetectionSource` (`detection/script_runner.py`) | readiness check via `src/jobs/calibrate.py` |

**Results.** The JSON (`wsc.vision.detections/1`, built in `payload.py`) goes out as `ResultReadyEvent` and into `LatestResultJson`; `ResultStore` (`result_management.py`) keeps the latest results. Errors use `VisionErrorCode` (`errors.py`).

**Cell watch** (ceiling). `cellwatch.py` compares the ceiling camera with a reference and reports only confirmed moved/appeared/disappeared modules. Methods in `runner/cell_watch.py`; status node `CellWatchStatus`.

**Calibration.** `calibration_session.py` runs interactive camera calibration (`StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration`); the result is saved under `data/calibration/<frameId>.json` and used at once. The tag map (`config/tagmap.json`) is set via `SetTagMap`.

**Hand-eye** (hand Pi). `handeye_session.py` determines the camera pose on the flange from robot poses reported by the client; saved under `data/handeye/cam_flange.json`.

**Layer 2** (hand Pi). `layer2/plan.py` plans flange targets, `layer2/run.py` runs the steps; the client drives the robot and reports poses (`StartLayer2Run`, `Layer2Target`, `ReportRobotPose`). Measurement helpers: `measureboard.py`, `measuretag.py`, `tagreference.py`. Concept: [layers.md](../../layers.md).

**Livestream.** `CameraStreamPublisher` (`camera_stream.py`) publishes `LatestCameraFrame` (Base64 JPEG); `MjpegServer` (`mjpeg_server.py`) serves `/stream.mjpg` and `/snapshot.jpg` (port in `CameraStreamHttpPort`); `stream_overlay.py` draws the overlay selected by `CameraStreamMode`.

## Main modules

| Module | Task |
| --- | --- |
| `server.py` | per-Pi identity, camera, profiles; mDNS and LDS |
| `runner/` | assembly of the address space and all OPC UA methods |
| `address_space.py`, `nodeset_ids.py`, `nodesets/` | nodeset and node lookup |
| `job.py`, `state_machine.py`, `result_management.py` | job execution, 40100 states, results |
| `detection/` | detection sources; AprilTag adapter around `tagloc` |
| `camera.py`, `camera_health.py`, `camera_stream.py`, `mjpeg_server.py` | camera, watchdog, live image |
| `calibration_session.py`, `handeye_session.py`, `cellwatch.py` | long-running sessions |
| `layer2/` | Layer 2 planning and run |
| `vision_program.py` | Part 10 program ([part10](../part10/README.md)) |
| `tools/handshake_client.py` | reference client for the whole job handshake |

## Deployment and discovery

- systemd unit `opcua-server.service`: template [`deploy/opcua-server.service`](../../../../deploy/opcua-server.service), steps in [`deploy/README.md`](../../../../deploy/README.md); setup in [installation](../../usage/installation.md).
- mDNS service `_opcua-tcp._tcp.local.`, instance name = `visionSystemId`, TXT `path=/raspi/server/` and `caps=DA`; endpoint `opc.tcp://<pi>:4840/raspi/server/`.
- LDS registration at `opc.tcp://10.10.38.27:4840/` (default, `OPCUA_LDS_URL`) so the aggregation server finds the Pi.

## Where to read more

| Topic | Document |
| --- | --- |
| Per-function reference | [code/cell-vision-server.md](../../code/cell-vision-server.md) |
| Interface in brief | [interfaces/vision-server-interface-short.md](../../interfaces/vision-server-interface-short.md) |
| Interface, full reference | [interfaces/vision-server-interface.md](../../interfaces/vision-server-interface.md) |
| Part 10 and discovery | [interfaces/part10-program-interface.md](../../interfaces/part10-program-interface.md) |
| Vision system, integration | [topics/vision-system.md](../../topics/vision-system.md), [topics/vision-system-integration.md](../../topics/vision-system-integration.md) |
| Layer 2 rules | [topics/layer2-rules.md](../../topics/layer2-rules.md) |
| Calibration from the frontend | [topics/frontend-integration-calibration.md](../../topics/frontend-integration-calibration.md) |
| Quickstart, integration | [usage/quickstart.md](../../usage/quickstart.md), [usage/integration.md](../../usage/integration.md) |
