# Backend discovers the Pis as devices (Part 10 as entry point)

**Status:** in progress — 17.11.2026 (WP1 Pi done)
**Owner:** Agent: orchestration (Claude); Pi side and WSC backend: Codex; WSC frontend: Sonnet agents
**Topic:** opcua
**Branch:** Pi: `vision-part10-discovery` (this repo); webskillcomposition: `geraete-discovery-stufe1` (local, from `hefdi/ADP_Lokalisierung`)

## Goal

The webskillcomposition (WSC) backend discovers both vision Pis on its own and exposes them like
robots: as devices with actions and variables. Today the backend only discovers Robotics
`MotionDevices`, so the frontend binds every Pi method and node by hand
(`frontend/src/features/opcua-server/config/piServers.ts`) and repeats the same call/wait/parse
code in many hooks. Afterwards the frontend uses one generic device path; the Pi carries the
error texts and the busy reason; the hand-written bindings are gone.

This plan is the binding contract for everyone implementing it. Deviations go into
"Deviations from the plan" and "Decisions" before the code changes.

## Already read

- `doc/projektdoku/vision-server-interface-short.md`, `doc/projektdoku/part10-programm-schnittstelle.md`
- `doc/projektdoku/code/webskill-backend.md`, `doc/projektdoku/code/webskill-frontend.md`
- WSC `backend/src/backend/opcua/asyncua_discovery.py`, `runtime/action_execution.py`,
  `runtime/application_service.py`; Pi `src/vision_server/vision_program.py`,
  `src/vision_server/runner/part10.py`, `src/ua_program/program.py`

## Affected files

Pi (this repo, Codex session P1):
- `src/ua_program/program.py`
- `src/vision_server/vision_program.py`, `src/vision_server/errors.py`
- `src/vision_server/runner/part10.py`, `runner/uamethods.py`, `runner/install.py`, the
  `add_vision_method` call sites in `runner/*.py`
- `tests/` (Part 10, uamethods, errors, busy reason)
- `doc/projektdoku/vision-server-interface-short.md`, `doc/projektdoku/part10-programm-schnittstelle.md`

WSC backend (Codex session B1): `backend/src/backend/opcua/{asyncua_discovery,discovery,method_calls,network_discovery}.py`,
`runtime/{action_execution,application_service}.py`, `models/messages.py` (announced servers only),
`backend/pyproject.toml`, `backend/tests/`.

WSC base types (orchestration, before B1 and the frontend): `backend/src/backend/models/{robot,opcua}.py`,
`frontend/src/entities/robot/model/types.ts`, `frontend/src/shared/api/messages.ts`.

WSC frontend (Sonnet): `frontend/src/app/model/applicationController.ts`,
`frontend/src/entities/server/model/store.ts`, `frontend/src/features/opcua-server/**`,
the Pi call helpers in `frontend/src/features/cell-modules/model/`, the components that use them.

## Interfaces

### OPC UA (Pi)

`Objects/VisionProgram` (type `ProgramStateMachineType`, i=2391) is the device root.

| Element | BrowsePath under `VisionProgram` | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| `Start`, `Halt`, `Resume`, `Suspend`, `Reset` | `<method>` | Method, no arguments | Call | Part 10 program methods. **`Start` returns only after the state is `Running`, or `Ready` with `ResultSet/ErrorCode` ≠ 0** (the transition is awaited, not scheduled). |
| `CurrentState` | `CurrentState` | LocalizedText | Read | `Ready`, `Running`, `Suspended`, `Halted` |
| `RecipeId`, `Continuous` | `ParameterSet/…` | String, Boolean | Write | Job parameters, written before `Start` |
| `JobId` | `ResultSet/JobId` | String | Read | Id of the last started job |
| `ErrorCode` | `ResultSet/ErrorCode` | `VisionErrorCode` (Int32 enum) | Read | 0 = last job OK |
| `ExecutionMode` | `ResultSet/ExecutionMode` | String | Read | unchanged |
| `LatestResultJson` | `ResultSet/LatestResultJson` (reference) | String | Read | Result JSON `wsc.vision.detections/1` |
| status nodes | `Status/<Name>` (Organizes references to the existing nodes) | as today | Read / Write where writable today | `CameraStreamMode` (writable), `CameraStreamHttpPort`, `DeviceHealth`, `LatestCameraFrame`, `CalibrationProgress`, `ActiveCalibrationInfo`, `TagMapJson`, `HandEyeStatus`, `Layer2Target`, `Layer2Status`, `CellWatchStatus`, `LastTagMeasurement`, `LastTagReference`, `TagReferencePreviewStatus` — only those the Pi offers |
| `BusyReason` | `Status/BusyReason` | String | Read | German reason why the Pi refuses new work; `""` = free. One of `Job laeuft`, `Kamera-Kalibrierung laeuft`, `Layer-2-Lauf laeuft`, `Hand-Auge-Kalibrierung laeuft`. |
| `Manufacturer`, `Model`, `SerialNumber` | properties of `VisionProgram` | String | Read | `SerialNumber` = `visionSystemId` (`vision-ceiling-01`, `vision-flange-01`) |
| vision methods | method children (references, as today) | Method | Call | all methods of `VisionMachine` (`SaveRawImage`, `MeasureTags`, `StartCalibration`, …) |

`ResultSet` contains **only** `JobId`, `ErrorCode`, `ExecutionMode` and the reference to
`LatestResultJson`. Nothing else is mirrored into `ResultSet` (no camera frame there).

**Method arguments are named.** Every input and output argument has a Name (today all names are
empty). Names are the existing English argument names of the interface doc, e.g.
`SaveRawImage() → (FileName: String, Error: VisionErrorCode)`,
`MeasureTags(RequestJson: String) → (ResultJson: String, Error: VisionErrorCode)`,
`SaveUseCaseImage(Series: Int32, MetadataJson: String) → (FileName, ResultJson, Error)`.
Every error output is named `Error`.

**`VisionErrorCode`** becomes an OPC UA Enumeration DataType (EnumValues with German
descriptions); the value on the wire stays Int32:

| Value | Name | Description (German, shown to operators) |
| --- | --- | --- |
| 0 | OK | OK |
| 1 | INVALID_STATE | Im aktuellen Zustand nicht möglich (z. B. noch kein Kamerabild). |
| 2 | INVALID_ARGUMENT | Ungültiger Parameter. |
| 3 | BUSY | Der Pi ist beschäftigt – danach erneut versuchen. |
| 4 | UNKNOWN_RECIPE | Unbekanntes Rezept. |
| 5 | DETECTION_FAILED | Erkennung fehlgeschlagen. |
| 6 | INTERNAL | Interner Fehler auf dem Pi. |
| 7 | CANCELLED | Abgebrochen. |

The 40100 `VisionMachine` methods and events stay unchanged for 40100 clients; WSC no longer uses
`StartSingleJob`/`StartContinuous` or the 40100 events.

### Python (WSC backend)

```python
# backend/src/backend/opcua/asyncua_discovery.py
async def discover_program_device_descriptors(client, namespace_context) -> list[MotionDeviceDescriptor]:
    """Every Object directly under Objects that passes build_skill_binding_from_node is one device.

    kind = "vision" if "http://opcfoundation.org/UA/MachineVision" is in the namespace array,
    else "device". Actions: the program skill as action key "vision_program" for kind vision
    (normalize_capability_name(BrowseName) otherwise), plus every method child
    (discover_method_bindings_from_nodes → filter_skill_owned_methods), keyed by
    normalize_capability_name (SaveRawImage → "save_raw_image"). Variables: the children of
    <P>/Status and <P>/ResultSet, keyed by normalize_capability_name
    (BusyReason → "busy_reason", LatestResultJson → "latest_result_json").
    Device id: make_robot_id(server_url, <P>.nodeId). Info: Manufacturer/Model/SerialNumber
    properties of <P>.
    """
```

- `discover_connected_server` returns robot descriptors **and** program-device descriptors.
- `read_method_arguments` resolves Enumeration DataTypes (EnumValues, else EnumStrings), cached
  per DataType, into `MethodArgument.enum_values`.
- `discover_methods` and `discover_skills` (unused) are deleted.

### Data / payload (WSC wire contract)

Base types (added before B1 and the frontend start):

```python
# models/robot.py
class RobotSessionInfo:            # existing; "robot id" now means device id
    kind: Literal["robot", "vision", "device"] = "robot"
class RobotActionState:            # existing
    result: dict[str, Any] | None = None   # final ResultSet values (skill) or named outputs (method)
    error_code: int | None = None
# models/opcua.py
class EnumText: name: str; description: str
class MethodArgument:              # existing
    enum_values: dict[int, EnumText] | None = None
```

Frontend mirrors (`entities/robot/model/types.ts`, `shared/api/messages.ts`): `kind`,
`RobotActionState.result`, `RobotActionState.errorCode`, `MethodArgument.enumValues`
(camelCase on the wire as everywhere).

**Skill action (`executeRobotAction` with the program skill):**
1. Snapshot the ResultSet values, write the ParameterSet inputs, call `Start`.
2. The watcher ends only when the state is idle **and** at least one holds: a non-idle state was
   seen, a ResultSet value changed (e.g. `JobId`), or 1.5 s passed since `Start`.
3. At the end the ResultSet is read into `state.result` (keys normalized, e.g.
   `{"job_id": …, "error_code": 0, "latest_result_json": "…"}`).
4. `error_code` ≠ 0 → status `failed`, `error_code` set, `message` = the enum description.
   Otherwise the final status stays `idle` (robot `go_to` semantics unchanged).

**Method action (`executeRobotAction` with a method key):**
- `state.result = {"outputs": {Name: value, …}}` using the OutputArguments names.
- An output named `Error` with value ≠ 0 → status `failed`, `error_code`, `message` = enum
  description, `result.outputs` still filled.
- OPC UA Bad status → ErrorEvent `code`: `BadNodeIdUnknown`/`BadMethodInvalid` → `methodMissing`;
  `BadNothingToDo`/`BadInvalidState` → `nothingToDo`.

**Announced servers (mDNS):**
- The backend browses `_opcua-tcp._tcp.local.` (TXT `path`), new module
  `opcua/network_discovery.py`, dependency `zeroconf`.
- New ClientMessage `listAnnouncedServers` → event `serversAnnounced { servers: [{ url, name }] }`;
  the backend also broadcasts `serversAnnounced` whenever the set changes.
- The frontend connects and discovers announced servers (replaces the hard-coded Pi URLs).
  `WSC_MDNS=0` switches browsing off.

### Network / operations

- Pis announce themselves already (mDNS `_opcua-tcp._tcp.local.`, instance name = `visionSystemId`).
- The new Pi build must be deployed on both Pis before the new frontend is used.

### Failure cases

- Pi refuses (`Error` ≠ 0) → action `failed` with German enum text.
- Method missing on an old Pi → `methodMissing`.
- Job fails → `ResultSet/ErrorCode` ≠ 0 → skill action `failed`.
- Pi busy → `Status/BusyReason` non-empty; the frontend shows it and does not start work.

## Approach

1. Orchestration: this plan, branches, base types (done before the runs start).
2. Codex P1 (Pi) and Codex B1 (WSC backend) in parallel.
3. Sonnet: frontend core (`visionDevice.ts`, `deviceCall.ts`, `useDevice.ts`), then the
   `opcua-server` and `cell-modules` migration in parallel; old code deleted in the same step.
4. Orchestration: review, all test suites, test parity, dead-code check, smoke test.

## Test mapping (TS → Python, same case names)

| TS test (frontend) | Cases | Goes to |
| --- | --- | --- |
| `features/opcua-server/model/piMethodCall.test.ts` | 3 | Error/Bad mapping → WSC `backend/tests/test_device_actions.py`; timeout/no device → `features/opcua-server/model/deviceCall.test.ts` |
| `features/opcua-server/model/useSaveRawImage.test.ts` | 7 | output parsing → `test_device_actions.py` (named outputs); error texts → Pi `tests/test_error_enum.py` (enum descriptions) |
| `features/opcua-server/model/piActivity.test.ts` | 4 | replaced, not ported: the client-side owner lock is gone; the Pi's `BusyReason` is tested in Pi `tests/test_busy_reason.py` |
| `features/cell-modules/model/tagReferenceCall.test.ts` (call part) | 10 | refusal message, code name, missing method → `test_device_actions.py`; no node id, timeout, unreadable answer → `deviceCall.test.ts` |

All other existing tests stay unchanged (except assertions that check the Pi interface where this
contract changes it on purpose, e.g. the mirror location in `tests/test_part10_fassade.py`).

## Decisions

- 17.11.2026: Error texts are generic per code (enum descriptions above), not per method. The
  method-specific texts in the frontend (e.g. "Noch kein Kamerabild" for SaveRawImage) are
  replaced by the generic text plus `Status/BusyReason`.
- 17.11.2026: MeasureTags, SaveRawImage, MeasureBoard stay synchronous methods (no extra Part 10
  programs); only the cancellable vision job is a program.
- 17.11.2026: Devices reuse the robot model (`kind` field) instead of a parallel session type;
  `robot_id` stays the wire name.

## Open questions

- none

## Deviations from the plan

- `SaveUseCaseImage.Series` stays Int32 (the first draft of this contract said String by
  mistake; corrected 17.11.2026). Otherwise the Pi implementation matches the interfaces above.

## After completion

- Status set to `done`, actual interfaces entered.
- Line in `arbeitsplaene/README.md` updated.
- Interface carried over into `vision-server-interface-short.md` and `part10-programm-schnittstelle.md`.
