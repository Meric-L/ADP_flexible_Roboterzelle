# Vision system — state and next steps (for agents)

Short orientation, so that an agent without prior context knows where the
vision-server project currently stands. Detailed current state:
[`vision-system.md`](vision-system.md). Backend interface:
[`vision-server-interface.md`](vision-server-interface.md). Complete plan:
[`vision-system-integration.md`](vision-system-integration.md) (part 9 = phase plan,
part 4 = target architecture).

## Current state

- [x] **Phase 1 (spike)**: `Opc.Ua.MachineVision.NodeSet2.xml` is vendored,
  can be loaded and instantiated via `import_xml()`. The known
  `asyncua` import risks (issue #651) are ruled out.
- [x] **Phase 2**: own package `src/vision_server/` with its own namespace
  (`http://launch-rm.de/vision`), `VisionMachine` instance under a stable
  string NodeId and `HasNotifier` from the server object. Module split according to
  part 4.1, but in the `src/` layout of this repo instead of as its own uv project.
  **Deviation from parts 4.1/4.6:** *no* separate server process. The package is
  built into the cell's server (`src/vision_server/server.py`, port 4840) via
  `install_vision_machine(server, config)`. The split from the plan was intended for
  the WSC monorepo with two simulated servers (2D+3D); with a
  camera on a Pi it only costs duplicate address space (~110 MB RSS per
  process measured) and forces the backend to use two sessions. Standalone start
  (`python -m vision_server`) is kept for development.
- [x] **Phase 3 (server 1, with placeholder detection)**: state machines,
  `StartSingleJob` with guard and validation, ResultManagement storage,
  four events, JSON payload in the schema from part 4.3, error path via the
  `Error` state. Detection is a `DetectionSource` strategy; the active one is
  `hello_world.py`.
- [x] The Raspi server runs on the Pi as the systemd service
  `opcua-server.service` (`Restart=always`, `RestartSec=5`, not versioned in this
  repo). A `vision-server.service` is planned analogously for the vision server. **Caution when testing manually**: a foreground start
  collides with the running service (port occupied) — use `systemctl restart`
  or choose a free port.
- [x] The old hello-world smoke test in `src/vision_server/server.py` has been removed —
  its task is now fulfilled by the built-in vision system on the same server.
- [x] On 21.09.2026 the rest of the CPU temperature demo was also removed:
  `RaspiDevice`, the old `VisionSystem` instance, `CpuTemperatureResult`, the
  polling loop and the namespace `http://launch-rm.de/raspi`. The
  address space has since contained **exactly one** `VisionSystemType` instance, and it
  lies under `Objects/Machines`. A type search has thereby become harmless;
  the fixed NodeId `ns=<vision>;s=VisionMachine` nevertheless remains the
  recommended way, because it costs no browse round.
- [x] **Phase 4 (real detection, 22.09.2026)**: the recipe `apriltag` delivers
  real AprilTag poses (`detection/apriltag.py`), including the world-tag concept
  (work plan `apriltag-welttag-konzept.md`) and hand-eye calibration/
  anchoring for layer 2 (work plan `apriltag-hand-auge-ankern.md`, both
  status done). Details: [`apriltag-referenz.md`](apriltag-referenz.md).
- [x] **Camera live stream and interactive calibration**: `camera.py`
  (shared capture loop), `camera_stream.py`/`stream_overlay.py`
  (`LatestCameraFrame`, `CameraStreamMode`) as well as `calibration_session.py`
  (`StartCalibration`/`CaptureCalibrationSample`/`FinishCalibration`/
  `AbortCalibration`) — see [`vision-server-interface.md`](vision-server-interface.md)
  sections 10/12.
- [x] **Part 2 (AMCM) and Part-10 program facade**: `asset_model.py`
  (`VisionAsset`) as well as `src/ua_program/program.py` +
  `vision_server/vision_program.py` (`VisionProgram`) — see
  [`vision-server-interface.md`](vision-server-interface.md) section 11 and
  [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).
- [x] **mDNS announcement and LDS registration** (`discovery/mdns.py`,
  `discovery/lds.py`): the cell's aggregation server automatically adds both
  vision servers, see
  [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) §2.

### Verified (locally, asyncua 2.0.1, production setup on port 4840)

Happy path (payload, event order, correlation via `jobId`,
`ResultState`/`IsPartial`/`IsSimulated`/`CreationTime` in the event, both
result nodes) · `BUSY` with two simultaneous calls ·
`INVALID_ARGUMENT` with an overlong `MeasId` · `UNKNOWN_RECIPE` with an unknown
recipe · error path `--parameter force-error` with `resultState=5`, passage
via the `Error` state and working resumption.

## Verified facts that contradicted the docs

These points have been checked against the nodeset and the asyncua source code; the
old statements in parts 2/4.2/4.4 of the plan are wrong:

1. **`ResultReadyEventType` (i=1024) has no `Result: ResultDataType`**,
   but 15 flat fields. Without `load_data_type_definitions()` only
   `ResultContent` (`BaseDataType`, ValueRank 1), `CreationTime`,
   `IsPartial`, `IsSimulated`, `ResultState` can be decoded. ⇒ The payload travels **in the event**
   in `ResultContent[0]`; a second read is unnecessary.
2. **The `ResultManagement` methods are unusable from the asyncua client** —
   not because of the return value, but because already the **input**
   (`ResultIdDataType`, `JobIdDataType`) is an ExtensionObject (issue #1693).
   No handle ⇒ the handle leak from part 6.4 is moot.
3. **There is no transition `Halted → Operational`.** The previously
   documented sequence `Preoperational → Halted → Operational` is not
   conformant. Correct: `Preoperational → Operational`
   (`PreoperationalToOperationalAuto`), inside `Initialized → Ready`.
4. **The `Error` state is not `i=5030`.** The states of the outer
   `VisionStateMachine` are mandatory children of the instance
   (`get_child(f"{mv}:Error")`); `i=5030` is the **type** node and would
   act across instances. Only the states of the `AutomaticModeStateMachine`
   (i=5056–5059) come via a fixed NodeId.
5. **`LastTransition` does not exist on the instance.** asyncua's
   `change_state(..., transition=...)` writes there and would fail ⇒
   state changes without a `Transition` object, transition name in the
   event message.
6. **`ResultContent` comes with a null NodeId as DataType.** Therefore *every* write fails
   with `BadTypeMismatch` — including the `[ua.Variant(...)]` write recommended in part 4.2
   (stumbling block 4). Only the DataType override to `String` makes the **array** write possible, which
   keeps the `ResultContent[0]` semantics intact.
7. **Event fields need finished `ua.Variant` objects.** asyncua derives the field type
   from the nodeset and gives these fields VariantType `Null`;
   a raw Python value reaches the client as `None`.
8. **`@uamethod` handlers must be `async`** if they start a task:
   synchronous handlers run in a ThreadPoolExecutor without an event loop.
9. **Method return values must be a `tuple`.** A `list` is packed by
   asyncua as *one* Variant, and the client then receives nested
   Variants. This also affects `src/vision_server/server.py`.
10. **No event bubbling via `HasNotifier`** (already known): asyncua
    matches `emitting_node` exactly, a subscription to `i=2253` receives nothing. All
    generators — including that of the state machine — must emit from the
    `VisionSystem` node.

## Seam for detection — done

Before a real detection could be plugged in, a number of hand-over points had to be
built. They now exist:

- **RecipeId selects the source** (`config.recipe_profiles`, see
  `DEFAULT_RECIPE_PROFILES` in `config.py`). Currently permitted are `""`
  (→ `hello_world`), `"hello-world"`, `"calibration"` (→ readiness check,
  `script_runner.py`) and `"apriltag"` (→ real detection). The former
  recipe `"image-recognition"` (QR-code precursor) no longer exists, and
  `"calibration"` is **no longer** rejected.
- **`DetectionRequest`** instead of only `parameters`, and `run_blocking()` on the
  base class: every OpenCV operation belongs there, otherwise the
  shared event loop freezes.
- **`open()`/`close()`** plus signal handlers in both entry points. A
  source that does not open leaves the state machine in Preoperational, instead of
  answering every job with `DETECTION_FAILED`.
- **Job timeout** (currently 20 s, `config.job_timeout`). Previously a
  hanging camera could permanently jam the server; the only rescue was
  `systemctl restart`.
- **`frameId`, `frameConvention`, `configurationId`, `IsSimulated`** come
  from the source, no longer from module constants.
- **JSON safety**: numpy values and `NaN` no longer silently break the payload.
- **`vision_system_id` per Pi** via env, hostname mapping or fallback.
- **Test framework** (now well over 300 tests, as of 22.09.2026 316):
  `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

Thus a new detection source is: one file under `detection/`, a registry
entry with lazy import, **one line** in `DEFAULT_RECIPE_PROFILES`
and an `AprilTagProfileConfig` per Pi. Nothing in `job.py`, `payload.py`,
`events.py`, `result_management.py`, `state_machine.py` or
`address_space.py` needs to change for this.

## Next steps

1. **Run through on the Pi** — `git pull`, `systemctl restart
   opcua-server.service`, then run the test client against it. Important,
   because so far only verified locally against asyncua 2.0.1; an older version may
   be installed on the Pi (`requirements.txt` only pins `>=1.1.5`).
2. **Joint test with the team colleague (backend)** — walk through the interface once;
   the basis is
   [`vision-server-interface.md`](vision-server-interface.md).
3. ~~Remove legacy `2:VisionSystem`~~ — **done on 21.09.2026**. ~~Move nodeset XML
   to `src/vision_server/nodesets/`~~ — **likewise done**
   (legacy item C5, see [`altlasten.md`](arbeitsplaene/altlasten.md)).
4. ~~**Real result payload**~~ — **done**: `detection/apriltag.py`
   delivers real poses in the schema `wsc.vision.detections/1`; the coordinate system
   and calibration have been clarified via the world-tag concept and the hand-eye calibration
   (see [`apriltag-referenz.md`](apriltag-referenz.md)).
5. **Then the 3D profile** — still open. As a second `VisionSystemType`
   instance in the *same* server (own instance name, own `visionSystemId`),
   according to part 4.5. A second server process as in part 4.6 is not
   needed for this.

> Phase 0 and phases 4–12 from part 9 concern the **WebSkillComposition
> backend/frontend** (separate repo, does **not** exist here). For this
> repo only phases 1–3 are relevant.

## Open decisions (not yet made)

- **Dispatch mode** (part 6.3): how a detected pose becomes a robot motion
  (`cartesian` via `LinMoveTcp` vs. `frontendIk`) — depends on whether the
  real robotics server exposes `LinMoveTcp`/`RotMoveTcp`. Concerns the
  backend side, not this server.
- ~~**Hand-eye calibration** — not yet specified.~~ **Done**
  (work plan [`apriltag-hand-auge-ankern.md`](arbeitsplaene/apriltag-hand-auge-ankern.md)):
  `AprilTagProfileConfig.hand_eye_path` + `detection/apriltag.py` chain
  camera → flange → robot base and deliver the drift metadata in the payload.
  An `auto_execute` flag does not exist in the code (nor in plans any more) —
  the backend itself decides, based on the delivered poses/drift values,
  whether to move automatically.
- **Conceptual gaps from the localisation itself** — coordinate system
  chain world tag → camera → robot, format of the layer 1 → layer 2 hand-over,
  repositioning strategy: largely clarified by the world-tag concept
  (work plan [`apriltag-welttag-konzept.md`](arbeitsplaene/apriltag-welttag-konzept.md)),
  see [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md).
  Remaining open points: [`concept/offene_punkte.md`](../../concept/offene_punkte.md).
  These determine what `frameId`/`position`/`orientation` must concretely mean in the result payload.
- **Fill structure-typed event fields** once asyncua issue #1693
  is fixed. The JSON payload remains the authoritative source even then.
