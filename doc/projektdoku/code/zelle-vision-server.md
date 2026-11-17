# Zelle: vision server (OPC UA, Pi side)

Detailed reference for `src/vision_server/` in the ADP repository (branch `main`, as of 2026-11-17). Overview: [code-dokumentation.md](../code-dokumentation.md). Interface details: [vision-server-interface.md](../vision-server-interface.md).


The runtime of the vision server: `runner/` assembles the OPC UA address space methods and nodes into one `VisionMachine` (or runs it as its own process). `layer2/` holds the Layer 2 run on the hand-camera Pi (planning and run logic, numpy/asyncua-free run flow). `discovery/` announces the server on the network (mDNS and the cell's Local Discovery Server). `nodesets/` contains vendored OPC UA companion specifications.

### `src/vision_server/runner/`

- Package that builds the vision system into an initialized OPC UA server (`install_vision_machine`, `install.py`) or runs it standalone (`run`, `standalone.py`).
- One module per OPC UA method group or node group; the module table is in the package docstring of `runner/__init__.py`.
- Shared state for the install steps is held in `Wiring` (`wiring.py`). Method handlers read it only when called.
- The order of the install steps is fixed in `install.py`; it determines the order in which methods are created and thus the entries under `VisionProgram`.
- Hand-camera-only and ceiling-camera-only parts are gated by the presence of address-space nodes (for example `space.hand_eye_status`, `space.layer2_target`, `space.cell_watch_status`), not by a profile name.
- Details: `doc/projektdoku/vision-server-interface.md`.

### `src/vision_server/runner/__init__.py`

- Package docstring with the module table; public names `VisionMachine`, `install_vision_machine`, `run` (`__all__`).
- Re-exports many `_install_*` helpers and constants, so existing import paths (used by tests) keep working.

### `src/vision_server/runner/install.py`

- `install_vision_machine(server, config)`: must run after `server.init()` and before `server.start()`.
- Order: address space, optional Part 2 asset model, state machines, event generators, result store, detection sources, `JobRunner`, then job methods, `_open_sources` (enters Operational only if every source opened), `_start_services` (calibration session, live stream, camera health, MJPEG), calibration, tag map, hand-Pi sessions, measurements, image saves, cell watch, Part 10 program.
- Starts the event-loop lag watchdog and returns a `VisionMachine`.

### `src/vision_server/runner/wiring.py`

- `Wiring` dataclass: server, space, config, states, results, sources, jobs, plus fields set during install (`opened`, `apriltag_source`, `camera_stream`, `annotator`, `calibration_session`, `layer2_run`, `hand_eye_session`, `methods`).
- Lock queries used by the handlers: `calibration_busy()`, `calibration_processing()`, `hand_eye_active()`, `layer2_active()`.
- `methods` maps method names to nodes; `part10.py` mirrors them under `VisionProgram`.

### `src/vision_server/runner/machine.py`

- `VisionMachine`: frozen dataclass holding the running components and the optional sessions (calibration, Layer 2, hand-eye, cell watch, camera health, MJPEG server, assets, program).
- `VisionMachine.aclose()`: best-effort, idempotent shutdown in a fixed order (calibration, Layer 2, hand-eye, cell watch, camera health, lag watchdog, MJPEG, stream, running job, sources).
- `_watch_loop_lag()`: logs a warning when the event loop is blocked for more than `LOOP_LAG_WARN_S` (0.75 s), checked every `LOOP_LAG_INTERVAL_S` (0.25 s).

### `src/vision_server/runner/uamethods.py`

- Reply helpers `code_reply(code)` (Int32) and `text_reply(text, code)` (String, Int32). Replies must be tuples of `ua.Variant`.
- `add_vision_method(...)`: creates a method node with NodeId `<vision system name>.<method>` in the own namespace and records it in `wiring.methods`.
- `json_method(...)`: frame for methods with a JSON request: parse, then the Operational check, then the busy check, then `run`. Used by `MeasureBoard`, `MeasureTags`, `MeasureTagReference`, `PlanTagReferenceView` and `SetTagReferencePreview`.
- `repo_relative(path)`: path relative to the repo root (POSIX), otherwise the absolute path.

### `src/vision_server/runner/sources.py`

- `_camera_owner(sources, opened)`: first opened source (by sorted profile id) that has a `camera` attribute. Used by the live stream, camera health and image saves.
- `_open_source(source)`: awaits `source.open()`; returns `False` and logs if it raises.

### `src/vision_server/runner/jobs.py`

- `install_job_methods(wiring)`: links six handlers to the nodeset methods: `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`, `Reset`.
- Start methods return `(JobId, Error)` as String and Int32 and refuse with BUSY during a calibration.
- `Stop` aborts a running Layer 2 run first, then calls `jobs.stop()`. `Halt` stops the job and calls `states.halt()`. `Reset` calls `states.enter_operational()`.

### `src/vision_server/runner/tag_map.py`

- `install_tag_map(wiring)`: publishes the loaded map to `TagMapJson` and creates `SetTagMap` (only with an opened AprilTag source and the node present).
- `SetTagMap` is refused with BUSY during a job, a calibration session, a Layer 2 run or a hand-eye session. It parses with `tagloc.tagmap.tag_map_from_json`, applies the map to the source, then writes the file at `config.apriltag.tag_map_path` as a local cache (INTERNAL if the write fails).

### `src/vision_server/runner/calibration.py`

- `_build_calibration_session(...)`: a `CalibrationSession` when `config.apriltag` is set and the AprilTag source is open with a camera.
- Methods: `StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration`, `AbortCalibration`. `FinishCalibration` returns at once; evaluation runs in the background while the live stream is paused and `CalibrationProgress` is written every `CALIBRATION_PROGRESS_INTERVAL_S` (1.0 s).
- `StartCalibration` takes one input, `OptionsJson` (String) (added 17.11.2026). Empty uses the configured board; otherwise the board is parsed by `parse_start_options`. An unusable board answers `INVALID_ARGUMENT` (2), starts nothing, and writes the reason to `CalibrationProgress.startError`.
- `ActiveCalibrationInfo` is written at start-up and after a live recalibration (`connect_live_calibration` applies the calibration to the source and the annotator).
- Details: `doc/projektdoku/vision-server-interface.md`.

### `src/vision_server/runner/livestream.py`

- `_build_annotator(source)`: builds the `AprilTagStreamAnnotator` with its own detector, with corner refinement switched off. It uses the source's calibration and tag map.
- `_start_camera_stream(...)`: starts `CameraStreamPublisher` on the shared camera; returns the publisher and the annotator.
- `_start_mjpeg_server(...)`: starts `MjpegServer` on `camera_stream.http_port` and publishes the port. On a bind error it logs and continues without it.
- Related: `doc/projektdoku/arbeitsplaene/livestream-mjpeg-und-watchdog.md`.

### `src/vision_server/runner/health.py`

- `_start_camera_health(...)`: if the asset model has `device_health` nodes, creates `CameraHealthPublisher` for the owning camera and writes `DeviceHealth`. Without a camera it writes `FAILURE`.
- Health alarms are attached through `HealthAlarms` when the alarm nodes exist. The camera's give-up handler is set to `camera.exit_process`.
- `_camera_config_of(...)`: returns the camera configuration the source actually uses.

### `src/vision_server/runner/measure_board.py`

- `_install_measure_board(...)`: creates `MeasureBoard` (JSON request, JSON result, Int32). Runs on both Pis and is refused with BUSY during a job or while a calibration is being evaluated.
- Calls `apriltag_source.measure_board_view(request)`; `DETECTION_FAILED` if the result has an error. Request parsing via `measureboard.parse_request`.

### `src/vision_server/runner/measure_tags.py`

- `_install_measure_tags(...)` (hand Pi): creates `MeasureTags`. Increments `apriltag_source.measure_calls` while measuring, which pauses the stream overlay. Writes `LastTagMeasurement` for display and returns the result directly.
- `_Activity`: a flag shared with the tag reference so that the two block each other.
- Runs during a hand-eye session; blocked by a job, a calibration evaluation and a running tag reference.

### `src/vision_server/runner/tag_reference.py`

- `_install_tag_reference(...)` (hand Pi): creates `MeasureTagReference`, `PlanTagReferenceView` and `SetTagReferencePreview`. Behavior and error codes are in the module docstring.
- `MeasureTagReference` writes `LastTagReference`, sets the stream preview, and writes a measurement log when `saveDebug` is set and `config.tag_reference_debug_dir` is configured.
- `PlanTagReferenceView` computes from the calibration without an image and is never BUSY.
- The numpy-dependent `tagreference` and `tagreference_log` modules are imported inside functions.

### `src/vision_server/runner/save_raw_image.py`

- `_install_save_raw_image(...)`: creates `SaveRawImage` (no inputs; returns the repo-relative path and Error). Saves the newest full frame of the shared camera as PNG into `config.raw_image_dir`.
- BUSY during a job; INVALID_STATE if there is no frame.
- Details: `doc/projektdoku/arbeitsplaene/rohbild-speichern.md`.

### `src/vision_server/runner/save_use_case_image.py`

- `_install_save_use_case_image(...)`: creates `SaveUseCaseImage` (inputs `Series` Int32 >= 1 and `MetadataJson`; outputs `FileName`, `ResultJson`, Error). Refused with BUSY during a job.
- Waits for a frame captured at least `USE_CASE_MIN_FRAME_AGE_S` (0.3 s) after the call, for up to `USE_CASE_FRESH_FRAME_TIMEOUT_S` (5 s).
- Metadata: `_build_use_case_pi_meta(...)`, `_reference_from_metadata(...)` (invalid `tagSizeM` falls back to 0.08 m). The measurement (`use_case_eval.live.measure_for_save`) runs before the file is written to `config.use_case_dir`.
- Details: `doc/projektdoku/arbeitsplaene/use-case-foto.md`, `use-case-messung.md`.

### `src/vision_server/runner/layer2.py`

- `_install_layer2_run(...)` (hand Pi): wires `layer2/run.py` `Layer2Run` to the server through `Layer2Ports`. Starts jobs via `jobs.start_single_job`, reads anchor, hand-eye and tag map from the AprilTag source, reads the result from `results.json_node`, and publishes `Layer2Target` and `Layer2Status`.
- Creates `StartLayer2Run` and `ReportRobotPose` and registers the job-finish listener.
- Details: `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md`.

### `src/vision_server/runner/hand_eye.py`

- `_install_hand_eye_session(...)` (hand Pi): creates `HandEyeSession` from `handeye_session.py` (numpy imported inside the function) and four methods: `StartHandEyeCalibration`, `CaptureHandEyeSample`, `FinishHandEyeCalibration`, `AbortHandEyeCalibration`. All reply with Error only.
- Raw captures go under `data/rohbilder/calibration` in the repo. New intrinsics are applied live to the source and the annotator.
- Details: `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`.

### `src/vision_server/runner/cell_watch.py`

- `_install_cell_watch(...)` (ceiling camera): builds a dedicated detector and a `CameraPoseKeeper` that persists `cellwatch_camera_pose.json` next to the calibration file, then a `CellWatch` from `cellwatch.py`.
- `_make_observe(...)`: reads the newest frame of the shared camera and evaluates it in a thread via `observe_cell`. It skips a frame whose timestamp was already seen.
- Methods: `StartCellWatch` (requires Operational only), `StopCellWatch`, `PauseCellWatch` (reason and maximum pause in seconds), `ResumeCellWatch`.
- Details: `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md`.

### `src/vision_server/runner/part10.py`

- `install_part10_program(wiring)`: installs `VisionProgram` (`vision_program.py`) on the same `JobRunner`, mirrors the result, state and optional nodes, and mirrors `wiring.methods`.
- If the vision system is not Ready, the program is put into Halted with a message.

### `src/vision_server/runner/standalone.py`

- `run(config)`: starts its own `Server`, configures it, installs the vision machine, and waits for SIGTERM/SIGINT. `machine.aclose()` runs in `finally`.
- Per its docstring, meant for development and isolated tests. In production the vision system is part of the cell's robot server.

### `src/vision_server/layer2/`

- Layer 2 run on the hand-camera Pi: anchor at a world tag, then precise measurement of each module. `plan.py` computes the targets (numpy, `tagloc`); `run.py` executes the step chain (no asyncua).
- `run.py` is imported inside `runner/layer2.py`, and it imports `plan.py`. Importing them lazily keeps the rest of the server importable without numpy.
- Details: `doc/projektdoku/layer2-regeln.md`, `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md`.

### `src/vision_server/layer2/__init__.py`

- Package docstring only; no code and no re-exports.

### `src/vision_server/layer2/plan.py`

- `plan_run(request, tag_map, pose_flange_cam)`: returns the ordered `PlannedStep` list. Step 0 is the nearest measured world tag (anchor), then the modules, nearest first. If `approach_standoff_m > 0`, an approach step precedes each step.
- `plan_step(...)`: tries tilt steps up to `max_tilt_deg` and picks the most frontal target within reach, preferring 90 % of the reach limit.
- Parsing: `parse_run_request` (`wsc.vision.layer2.run/1`), `parse_report` (`wsc.vision.layer2.report/1`). Output helpers: `pose_parameters` (seven job-parameter strings), `tag_in_world_from_detection`, `find_detection`.
- Defaults: standoff 0.3 m, approach standoff 0.6 m, reach 0.85 m, max tilt 60 degrees.

### `src/vision_server/layer2/run.py`

- `Layer2Run`: state machine `idle`, `waitingForRobot`, `measuring`, `finished`, `aborted`. Entry points: `start`, `report`, `on_job_finished` (job-finish listener), `abort`, `publish_idle`.
- Publishes `Layer2Target` and `Layer2Status` as JSON (`target_json`, `status_json`; schemas `wsc.vision.layer2.target/1` and `.status/1`).
- An anchor step succeeds only if the job leaves a new `RobotAnchor`. After anchoring, the remaining steps are re-planned. Unreachable steps are skipped; a failed anchor aborts the run.
- All server access goes through the `Layer2Ports` dataclass, so the flow can be tested with fakes.

### `src/vision_server/discovery/`

- Two independent announcements, started in `server.py` `main()` and both fault-tolerant (a failure logs a warning and the server keeps running):
  - mDNS (`mdns.py`), on by default; `OPCUA_MDNS=0` turns it off.
  - Registration with the cell's Local Discovery Server (`lds.py`), on by default; `OPCUA_LDS_URL=""` turns it off, another URL redirects it.
- `OPCUA_ADVERTISE_IP` forces the announced LAN address. The endpoint names the LAN IPv4, while the server still listens on `0.0.0.0`.

### `src/vision_server/discovery/__init__.py`

- Package docstring describing both paths and their switches. Re-exports the modules `lds` and `mdns` (`__all__`).

### `src/vision_server/discovery/mdns.py`

- `announce(name, port, path, address=None, caps="DA")`: async context manager that registers the `_opcua-tcp._tcp.local.` service on the LAN IPv4 and unregisters on exit. Yields the announced IP, or `None`.
- `enabled()`, `detect_lan_ipv4()` (uses `OPCUA_ADVERTISE_IP`, otherwise the routing table via an unsent UDP connect; `None` on loopback), `sanitize_instance_name()`.
- The `zeroconf` package is imported lazily and is optional.

### `src/vision_server/discovery/lds.py`

- `register(server, url=None, renew_seconds=60)`: async context manager around `server.register_to_discovery`. It refuses if the endpoint host is `0.0.0.0`, `::` or missing, and unregisters on exit.
- `lds_url()` reads `OPCUA_LDS_URL`, default `CELL_LDS_URL` (`opc.tcp://10.10.38.27:4840/`). `advertised_endpoint(port, path)` builds the endpoint with the LAN IPv4.
- Rationale for the LDS path is in the module docstring; the project doc is `doc/projektdoku/arbeitsplaene/lds-registrierung.md`.

### `src/vision_server/nodesets/`

- Vendored OPC UA companion-specification NodeSet2 XML files (Device Integration, Machinery, and OPC 40100-2 Asset Management and Condition Monitoring, plus the MachineVision Part 1 set). They are not project code and are loaded at start-up so the server runs without network access.
- Versions are pinned on purpose; `nodesets/README.md` lists them, the import order (DI, Machinery, AMCM) and the measurement procedure to repeat after an update.
- Paths are defined in `src/vision_server/config.py` (`DEFAULT_NODESET_PATH`, `DEFAULT_AMCM_NODESET_PATHS`). Node IDs used by the runtime are in `src/vision_server/nodeset_ids.py`.



Paths are relative to the repository root. Units are grouped by role; the group labels are not headings.

**Group: server entry and configuration**

### `src/vision_server/server.py`
- Installation point for the cell's Raspberry Pi: its identity, camera backend, AprilTag, asset and stream presets, plus the mDNS announcement and LDS registration. The address space is built by `runner.install_vision_machine`.
- Entry points: `main()` (run as `python3 -m vision_server.server`, see `deploy/opcua-server.service`), `vision_config()`, `apriltag_config()`, `asset_config()`, `subpixel_corner_settings()`.
- Inputs: `PI_*` preset dicts keyed by Pi name, and environment variables read with `getenv` (e.g. `VISION_CAMERA_BACKEND`, `VISION_SUBPIXEL_CORNER_REFINEMENT`, `OPCUA_MDNS_NAME`).
- Background: `doc/projektdoku/code-dokumentation.md`, `doc/projektdoku/vision-server-interface.md`.

### `src/vision_server/__main__.py`
- Command-line entry for `python -m vision_server`. It builds a `VisionServerConfig` from arguments and runs `runner.standalone.run`.
- Entry points: `build_config(argv)`, `main(argv) -> int`.
- Options: `--port` (default 4841), `--endpoint`, `--nodeset`, `--profile` (forces one profile for all recipes), `--log-level`, and the identity options.
- Does not start mDNS or LDS announcement; see Flags.

### `src/vision_server/config.py`
- `VisionServerConfig`, a frozen dataclass with all operating parameters of one server instance.
- Key fields: `job_timeout` (30 s), `stop_timeout` (5 s), `continuous_interval_s` (1 s), `recipe_profiles`, `nodeset_path`, `camera_stream` and `apriltag` (`None` switches the feature off).
- Helpers: `known_recipe_ids`, `detection_profiles`, `profile_for(recipe_id)`. Defaults: `DEFAULT_NODESET_PATH`, `DEFAULT_AMCM_NODESET_PATHS`, `DEFAULT_RECIPE_PROFILES`.
- Field comments in the file explain the values.

### `src/vision_server/profiles.py`
- Profile dataclasses with stdlib types only: `AprilTagProfileConfig`, `CameraStreamConfig`, `AssetConfig`. Matrices are not stored here; `K` and `D` come from the calibration file.
- `CAMERA_BACKENDS` = `picamera2`, `opencv`, `realsense`.
- Default paths: `DEFAULT_CALIBRATION_PATH` (`data/calibration/camera.json`), `DEFAULT_TAG_MAP_PATH` (`config/tagmap.json`), `DEFAULT_HAND_EYE_PATH` (`None`).
- `AprilTagProfileConfig.calibration_board_spec()` (added 17.11.2026): the one place that turns the `calibration_board_*` fields into a `tagloc.boards.BoardSpec`. Used by the calibration session and `MeasureBoard`'s default board.
- `CameraStreamConfig.stream_enabled=False` only suppresses the stream nodes; `resolution` still configures the job camera, so never set the whole `camera_stream` to `None` to switch the stream off (the fallback default would change the job resolution).
- `AprilTagProfileConfig.detection_roi` hides tags outside the rectangle, world tags included; corners stay in full-frame coordinates.

**Group: job, state, address space and events**

### `src/vision_server/job.py`
- Validation and execution of single jobs and of continuous operation. Keeps the states and result nodes consistent.
- Entry points: `build_job_request(...)` (validates MeasId, PartId, RecipeId, ProductId and Parameters; raises `VisionJobError`), `JobRequest`, `JobRunner` with `start_single_job`, `start_continuous`, `stop(abort=...)`, `cancel_running`, `add_finish_listener`, `busy`.
- Calls the detection source for the recipe's profile and publishes through `ResultStore`.

### `src/vision_server/state_machine.py`
- Binds the two OPC 40100 state machines (outer and inner) to the nodeset instance.
- Entry point: `VisionStateMachines.bind(space)`. Transitions include `enter_operational`, `to_single_execution`, `to_continuous_execution`, `halt`, `abort_to_ready`, `stop_to_ready`, `to_error`, `recover`.
- Transitions are recorded in the event message, not in a `Transition` object.

### `src/vision_server/address_space.py`
- Imports the nodesets (OPC 40100-1, and optionally the AMCM part 2) and creates the `VisionMachine` instance.
- Entry points: `configure_server()`, `attach_vision_system()`, which returns a `VisionAddressSpace` dataclass with the resolved nodes.
- Optional nodes (livestream, calibration progress, layer 2, hand-eye, cell watch, tag reference) are `None` when their feature is not configured. Namespace indices are resolved at runtime, never hardcoded.

### `src/vision_server/nodeset_ids.py`
- NodeId numbers and namespace URIs for OPC 40100-1, OPC 40100-2 (AMCM), DI and Machinery.
- Entry points: `node_id(identifier, namespace_index)`, the `DeviceHealth` IntEnum, and constants `EVENT_*`, `STATE_*`, `OUTER_STATE_NAMES`.
- Nodeset versions are pinned in `src/vision_server/nodesets/README.md`.

### `src/vision_server/events.py`
- Creates the four job event generators (`job_started`, `acquisition_done`, `result_ready`, `ready`) on the VisionSystem node.
- Entry points: `create_event_generators(space) -> VisionEvents`, `fire_result_ready(...)`, which sends the JSON payload.
- asyncua delivers events only to subscriptions on the source node, so the generators must sit on that node.

### `src/vision_server/result_management.py`
- Stores job results in the address space: the `LatestResult` (ResultType) and `LatestResultJson` nodes.
- Entry points: `ResultStore.create(space)`, `publish(PublishedResult)`, `result_for_job(job_id)`. `PublishedResult` is the value object per job.
- Keeps the last 20 payloads in memory (`HISTORY`), keyed by JobId. The cell watch uses this as its reference.

### `src/vision_server/vision_program.py`
- OPC UA Part 10 `VisionProgram` (a `Program` subtype), an operator interface for the same job as the 40100 methods. It has no job state of its own.
- Entry point: `install_vision_program(...)`. Modes: `idle`, `single`, `continuous`. Sets: `ParameterSet`, `ResultSet`.
- Uses `JobRunner.add_finish_listener` to return from Running to Ready.

### `src/vision_server/errors.py`
- `VisionErrorCode` (IntEnum: OK, INVALID_STATE, INVALID_ARGUMENT, BUSY, UNKNOWN_RECIPE, DETECTION_FAILED, INTERNAL, CANCELLED) for the `Error` output of the 40100 methods.
- `VisionJobError(code, message)` carries a code and a message to the method layer.

### `src/vision_server/aio.py`
- Standard-library asyncio helpers: `cancel_and_wait(task)` and `stop_event_on_signals(signals)`, which sets an `asyncio.Event` on SIGTERM or SIGINT.
- Imports neither asyncua nor server code.

### `src/vision_server/__init__.py`
- Empty package marker (0 bytes). No code, no docstring.

**Group: payload**

### `src/vision_server/payload.py`
- JSON payload for `ResultContent`, schema `wsc.vision.detections/1`.
- Entry points: `build_result_payload(...)`, `build_error_payload(...)`. Shared envelope in `_envelope`.
- Envelope fields: `lengthUnit` m, `angleUnit` rad, `rotation` quaternion_xyzw. `frameConvention` and `configurationId` are added only when set. `DEFAULT_FRAME_ID` = `world`.
- Field reference: `doc/projektdoku/vision-server-interface.md`.

**Group: camera and asset health**

### `src/vision_server/camera.py`
- One physical camera, one capture loop, several readers. `SharedCamera` keeps the hardware open and caches the latest frame.
- Entry points: `SharedCamera.open()`, `latest_frame`, `status(now)`, `factory_calibration()`, `close()`. Types: `CameraFrame`, `CameraStatus`.
- Helpers: `validate_camera_controls`, `picamera2_video_configuration`, `yuv420_to_bgr`, `realsense_color_profiles`.
- Watchdog: each capture is bounded, the camera is reopened on a hang, and the process exits as a last resort so systemd restarts it. Details in the module docstring.

### `src/vision_server/camera_health.py`
- Translates the watchdog status (`CameraStatus`) into OPC 40100-2 `DeviceHealth` and writes it to the asset view. Changes raise the DI alarms.
- Entry points: `device_health(status, stale_frame_s=...)`, `write_device_health(nodes, value)`, `HealthAlarms`, `CameraHealthPublisher` (`start`, `stop`, `publish_now`).

### `src/vision_server/asset_model.py`
- OPC 40100-2 (AMCM) view: what the system consists of (computing device, image sensor, lens) and a `Health` block.
- Entry point: `attach_asset_model(...)`, which returns `VisionAssetNodes`. Identification fields: Manufacturer, Model, SerialNumber, SoftwareRevision.
- Only the folders that are filled are created. The health values are written by `camera_health.py`. No runtime coupling to the job path, per the module docstring.

**Group: calibration**

### `src/vision_server/calibration_session.py`
- Interactive camera calibration against the already running shared camera, driven by the frontend. Captures are triggered manually.
- Entry points: `CalibrationSession` with `start(board=None)`, `capture()`, `finish()` (returns `(VisionErrorCode, dict)`), `abort()`, `progress()`, `set_on_calibrated(callback)`, property `board_spec`, and `start_error` (why the last start options were refused).
- `parse_start_options(text)` (added 17.11.2026): turns the `OptionsJson` of `StartCalibration` (schema `wsc.vision.calibration.start/1`) into a `BoardSpec`, or `None` for the configured board. Board rules are those of the hand-eye calibration (`handeye_session._parse_board`, `check_raw_board`). Raises `ValueError` with a German message.
- The board of a session is used for evaluation, the archive (`session.json`, `board`), `progress()` (`board`) and the stream overlay. Without a session it is the configured board.
- Constants: `MIN_SAMPLES_FOR_CALIBRATION` = 3, `RMS_WARNING_PX` = 0.5, `CAPTURE_DETECT_TIMEOUT_S` = 20. Archive schema `adp.calibration.captures/1`. Start options schema `START_SCHEMA` = `wsc.vision.calibration.start/1`.
- Used by `runner/calibration.py`. Background: `doc/projektdoku/arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md`.

### `src/vision_server/handeye_session.py`
- Hand-eye calibration over OPC UA, solving `T_flange_cam` on the hand Pi. The frontend moves the robot and reports the flange pose for each capture. The Pi measures the target in camera coordinates and solves with `tagloc.handeye.solve_hand_eye` (Park-Martin).
- Entry points: `HandEyeSession.start`, `capture`, `finish`, `abort`, `status_json`; parsers `parse_start`, `parse_sample`, `check_raw_board`; `save_with_backup`.
- Constants: `RECOMMENDED_SAMPLES` = 8, `REQUIRED_AXES` = 3. Schemas `wsc.vision.handeye.{start,sample,status}/1`.
- Used by `runner/hand_eye.py`. Background: `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`.
- Only the images of the current calibration are kept (since 17.11.2026, like the ceiling camera): `start` deletes the earlier session folders (named by a UTC stamp) under the dataset folder (`data/handeye/sessions/`) and, for a raw-image session, under `data/rohbilder/calibration/`. Calibration files and their `.bak` backups are not touched.

**Group: measurement (hand Pi, ceiling camera, raw images)**

### `src/vision_server/measureboard.py`
- `MeasureBoard` request and result: centers the calibration chessboard before a hand-eye capture. The Pi only measures and sends no motion commands.
- Entry points: `parse_request(text, parse_board)`, `build_result(...)`, `dumps(result)`. Types: `BoardRequest`, `FrameBoard`.
- Schemas `wsc.vision.measureboard.request/1` and `.result/1`. Board detection is in `detection/apriltag/board.py`.

### `src/vision_server/measuretag.py`
- `MeasureTags` request and result: tag poses `T_cam_tag` in the camera frame, from images that arrive after the call. No world frame, no CAD offset, no robot knowledge.
- Entry points: `parse_request(text)`, `build_result(...)`, `near_edge`, `view_angle_deg`, `SizeLookup`, `dumps`.
- Constants: `DEFAULT_FRAMES` = 3, `MAX_FRAMES` = 10, frame convention `z_forward_x_right_y_down`, error code `NO_FRAME`.
- Measurement is in `detection/apriltag/tags.py`.

### `src/vision_server/cellwatch.py`
- Cell watch (layer 1, ceiling camera). Reports module tags that have moved, appeared or disappeared against a reference, and only after confirmation.
- Entry points: `CellWatch` (`start`, `stop`, `pause`, `resume`, `tick`, `status_json`, `aclose`), `CellWatchTracker`, `CameraPoseKeeper`, `CellWatchOptions`, `parse_start`, `reference_from_payload`, `camera_pose_from_payload`.
- States: `off`, `watching`, `changed`, `paused`, `failed`. Schemas `wsc.vision.cellwatch.{start,status}/1`.
- Per-image evaluation is in `detection/apriltag/cell.py`. Used by `runner/cell_watch.py`.

### `src/vision_server/raw_image.py`
- Saves a raw camera image as lossless PNG for the `SaveRawImage` method.
- Entry point: `save_raw_image(image, directory, frame_id, now=None) -> Path`, writing `<frame_id>_<time>.png`. Raises `OSError` on failure.
- `FILE_STAMP_FORMAT` = `%Y%m%d-%H%M%S`. Used by `runner/save_raw_image.py` and `tagreference_log.py`.

### `src/vision_server/use_case_image.py`
- Use-case photo per trial: a PNG plus a metadata JSON, and optionally a measurement image (`SaveUseCaseImage`).
- Entry points: `save_use_case_image(...)`, `next_trial_number(directory)`.
- Layout: `<base>/reihe_NNN/versuch_NNN.png|json`, plus `versuch_NNN_messung.png`. Schema `adp.usecase.trial/1`. Used by `runner/save_use_case_image.py`. Background: `doc/projektdoku/arbeitsplaene/use-case-foto.md`.

**Group: tag reference**

### `src/vision_server/tagreference.py`
- `MeasureTagReference` and `PlanTagReferenceView`. Corrects a catalog start value `T_module_tag` using reference edges measured in the image. The math is in `tagloc.reference_edges`. No robot kinematics.
- Entry points: `parse_request(text)` (returns `TagReferenceRequest`), `combine_tag_pose`, `keep_guess_rotation`, `summarize_lines`, `reference_warnings`, `build_result`, `build_view`, `dumps`.
- Limits: frames 1 to 10, `MAX_LINES` = 4, search band 3 to 50 mm. Errors include `NO_FRAME` and `TAG_NOT_SEEN`.
- Detection side: `detection/apriltag/reference.py`. Background: `doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md`.

### `src/vision_server/tagreference_log.py`
- On-disk log of each `MeasureTagReference` run: a folder `<YYYYmmdd-HHMMSS>_tag<id>/` with `request.json`, `result.json`, `frame<k>.png` and `overlay.jpg`.
- Entry points: `write_protocol(directory, tag_id, request_json, result, frames, overlay)`, `prune(directory, keep=MAX_FOLDERS)`, `latest_overlay(directory)`. `MAX_FOLDERS` = 50.
- A write error is raised as `OSError` for the caller to log as a warning; the measurement itself does not fail. Read by `mjpeg_server.py` for `/tagreference.jpg`.

### `src/vision_server/tagreference_preview.py`
- Plain data for the live preview during tag referencing: `ReferencePreview` (request and expiry time), `ReferenceView`, and `build_status(...)`.
- Constants: `PREVIEW_TTL_S` = 120, `PREVIEW_INTERVAL_S` = 1/3, `MEASURE_HOLD_S` = 5. Schema `wsc.vision.tagreference.preview/1`.
- Objects are immutable and swapped by attribute assignment, so no lock is needed. Used by `stream_overlay.py`.

**Group: streaming**

### `src/vision_server/camera_stream.py`
- Publishes the shared camera's frames as a Base64 JPEG on the node `LatestCameraFrame`. The writable node `CameraStreamMode` selects the overlay: `off`, `apriltag`, `undistorted`, `tagreference` (the mode `calibration` was removed on 17.11.2026).
- Entry points: `CameraStreamPublisher` (`start`, `stop`, `latest`, `next_frame`, `viewer`). It also publishes calibration progress and preview status nodes.
- Takes an annotator object for the overlay. Camera health is not checked here; the image freezes when the camera hangs.

### `src/vision_server/stream_overlay.py`
- Overlay for the livestream: draws detected tags or the tag reference preview onto a copy of the frame.
- Removed 17.11.2026: the `calibration` mode (board corners in the preview image). `set_calibration_session` and `calibration_progress` stay: the publisher still reads the attached session's progress from them.
- Entry points: `AprilTagStreamAnnotator` with `annotate(image, mode)`, `apply_calibration`, `set_calibration_session`, `set_reference_preview`, `set_reference_measuring`, `close`.
- Rules: never modifies `SharedCamera.latest_frame`; own detection runs at most every `overlay_detect_interval_s`, and the last result is redrawn in between.

### `src/vision_server/mjpeg_server.py`
- Serves the live stream as MJPEG over HTTP directly from the Pi to the browser, bypassing the Base64 OPC UA path.
- Entry points: `MjpegServer` (`start`, `stop`, `port`). Paths: `/stream.mjpg`, `/snapshot.jpg` (503 without a current image), `/tagreference.jpg` (404 without a measurement).
- The port is announced to the frontend through the node `CameraStreamHttpPort`. Mode is still set via OPC UA.

**Group: detection (`src/vision_server/detection/`)**

### `src/vision_server/detection/`
- Detection sources behind the job path. `base.py` defines the interface, `__init__.py` is the registry, `script_runner.py` runs an external script, and `apriltag/` is the AprilTag source split into mixins.
- Registered profiles: `calibration` (script `src/jobs/calibrate.py`) and `apriltag`. Sources are built once at startup, so a bad profile name stops the server before any job runs.

### `src/vision_server/detection/__init__.py`
- Registry: `DETECTION_SOURCES` maps profile ids to factories. `build_detection_sources(config)` creates one instance per profile that recipes reference. Unknown profiles raise `ValueError`.
- Factories import their module inside the function, so OpenCV is not on the import chain of the cell server.
- `JOBS_DIR` points to `src/jobs/`. Re-exports `Detection`, `DetectionRequest`, `DetectionSource`.

### `src/vision_server/detection/base.py`
- Detection interface: `Detection` (result), `DetectionRequest` (job inputs), and the ABC `DetectionSource`.
- `DetectionSource` methods: `acquire_and_detect()`, `open()`, `close()`, `run_blocking()`, `shutdown_executor()`. Attributes: `profile_id`, `is_simulated`, `frame_id`, `frame_convention`.
- Knows nothing about OPC UA. One instance lives for the whole process, so the camera stays open across jobs.

### `src/vision_server/detection/script_runner.py`
- `ScriptDetectionSource`: runs an external Python script as a subprocess and returns its detections. Used for the `calibration` profile.
- Entry points: `acquire_and_detect()`, `_terminate()` (grace period `TERMINATE_GRACE_S`).
- Covered by `tests/test_script_runner.py`.

### `src/vision_server/detection/apriltag/__init__.py`
- `AprilTagDetectionSource`: one class for the ceiling camera and the eye-in-hand camera. The two differ only in their profile and in what they see.
- Composed from the mixins `ResourcesMixin`, `CaptureMixin`, `JobMixin`, `TagMeasureMixin`, `BoardMeasureMixin`, `ReferenceMixin` and `CellObserveMixin`. A table in the docstring maps each module to its task.
- Re-exports `TagSnapshot`, `robot_pose_from_parameters`, `OBSERVE_MAX_SIDE`, `POLL_INTERVAL_S`, `SOURCE_BY_FRAME`, `PAYLOAD_DIGITS`. Background: `doc/projektdoku/apriltag-lokalisierung.md`.

### `src/vision_server/detection/apriltag/resources.py`
- `ResourcesMixin`: `open()` loads calibration, tag map, hand-eye and the detector, and `close()` releases them.
- `apply_tag_map`, `apply_calibration`, `apply_hand_eye` swap parts at runtime. Each discards the anchor, which was computed against the old values.
- Properties: `calibration`, `intrinsics_source`.

### `src/vision_server/detection/apriltag/capture.py`
- `CaptureMixin`: fetches fresh frames from the shared camera (`_next_frame`, `_latest_timestamp`), matches the calibration to the image size (`_calibration_for`), and remembers tags for the overlay (`_remember_tags`).
- Shared by all measurement paths: job, MeasureTags, MeasureBoard, reference edges and cell watch.
- Module-level: `captured_at()`, `POLL_INTERVAL_S`.

### `src/vision_server/detection/apriltag/job.py`
- `JobMixin.acquire_and_detect()` implements the `DetectionSource` interface. `_locate()` evaluates one image and localizes the modules.
- Helpers: `robot_pose_from_parameters`, `_observed_world_tags`, `_merge_world_tags`, `_localization_summary`. Builds the `cameraLocalization` payload part.
- Keeps the camera pose measured or carried forward (`_carry_with_anchor`), and records it in the payload.

### `src/vision_server/detection/apriltag/tags.py`
- `TagMeasureMixin`: `measure_tags()` (MeasureTags) and `measure_tag()` (hand-eye) return tag poses in the camera frame from fresh images. No map offset, no anchor.
- Helpers: `_detect_in_camera`, `_measure_tag`.

### `src/vision_server/detection/apriltag/board.py`
- `BoardMeasureMixin`: `measure_board()` returns `T_cam_board` for hand-eye. `measure_board_view()` gives the centering aid when only part of the grid is visible.
- Also `capture_board_raw()`, `default_board_spec()`, `_diagnose_board()`. Constant `OBSERVE_MAX_SIDE`. `default_board_spec()` returns `calibration_board_spec()` (the configured board) and is used when a `MeasureBoard` request has no `board` (since 17.11.2026).
- Background: `doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`.

### `src/vision_server/detection/apriltag/reference.py`
- `ReferenceMixin`: `measure_tag_reference()` measures tag position against edges in the raw image, and `plan_tag_reference_view()` plans a camera pose where tag and edges fit.
- Edge geometry from `tagloc.reference_edges`. Debug view via `_show_reference_debug`. Response schema is in `tagreference.py`.

### `src/vision_server/detection/apriltag/cell.py`
- `CellObserveMixin.observe_cell()`: a side-effect-free evaluation of one image for the cell watch. Module poses are reported in the world frame, without anchor or drift.
- `_with_stored_pose` uses the camera pose that the cell watch stored.

### `src/vision_server/detection/apriltag/snapshot.py`
- `TagSnapshot`: the tags of one image, held for the livestream overlay.
- Used by `stream_overlay.py` and `capture.py`.

**Group: tools (`src/vision_server/tools/`)**

### `src/vision_server/tools/`
- Command-line diagnostic and test clients run by hand on the Pi or a dev machine. Nothing in the server imports them.
- Files: `calibration_client.py`, `diagnose_board.py`, `handshake_client.py`, `list_realsense_profiles.py`, `measure_framerate.py`, `pi_load_profiler.py`, `stream_viewer.py`. `__init__.py` is empty.

### `src/vision_server/tools/calibration_client.py`
- Test client for an interactive calibration. Calls `StartCalibration`, `FinishCalibration` and `AbortCalibration` and logs progress live.
- Entry points: `run`, `main`, `_start_session`. Ctrl+C finishes the session; `--abort` ends it without saving.
- Method interface: `doc/projektdoku/vision-server-interface.md` (section 12); frontend side: `doc/frontend-integration-kalibrierung.md`.

### `src/vision_server/tools/diagnose_board.py`
- Explains why calibration does not find the chessboard. Sets `CameraStreamMode` to `off`, fetches a raw image from the running server, and tries several cols/rows combinations with `tagloc.boards.detect_board`.
- Entry points: `run`, `main`, `_parse_combo`. Restores the previous stream mode afterwards.
- Also saves the raw image for offline analysis (`--save-to`).

### `src/vision_server/tools/handshake_client.py`
- Shows the handshake a backend must implement: subscribe first, then call `StartSingleJob`, then wait for `ResultReadyEvent`. It does not poll.
- Entry points: `run`, `main`. Helpers: `EventCollector`, `_payload_of`.

### `src/vision_server/tools/list_realsense_profiles.py`
- Lists the RealSense colour resolution, FPS and format combinations the connected sensor supports, in `bgr8`.
- Entry point: `main()`, which calls `vision_server.camera.realsense_color_profiles`. Opens no pipeline, so the server can keep running.

### `src/vision_server/tools/measure_framerate.py`
- Measures the raw frame rate the camera hardware delivers (min, max, average). Bypasses the `capture_fps` throttle of `SharedCamera`.
- Entry points: `run`, `main`. Helpers: `_measure`, `_summarise`. Imports `SharedCamera` and `vision_config` from `vision_server.server`.
- The camera can only be opened once, so the server must be stopped first.

### `src/vision_server/tools/pi_load_profiler.py`
- Live and summary view of CPU and RAM per process and per thread on the Pi. Pure standard library: reads `/proc`, plus the CPU temperature and throttle flags when available. Optional `py-spy` snapshot.
- Entry points: `run`, `main`. Data classes `ProcSample`, `ProcStats`, `ThreadSample`, `ThreadStats`.
- Purpose: check whether processing 12 MP images loads the Pi enough to drop the frame rate.

### `src/vision_server/tools/stream_viewer.py`
- Local OpenCV window for the camera stream, for a Pi with a monitor attached. Reads `LatestCameraFrame` and defaults to `CameraStreamMode="off"` (was `"calibration"` until 17.11.2026).
- Entry points: `run`, `main`. Space triggers a calibration capture; `q` or Ctrl+C quits.
- Positioning aid only, not a measure of whether a capture was usable.

