# Part 10 program layer

The OPC UA Part 10 "Programs" view of the vision server: `VisionProgram` lets a generic client start, halt and reset vision jobs and read their state without knowing OPC 40100.

## What it is

| Part | Location |
| --- | --- |
| Generic framework: `Program`, `ProgramException`, `ProgramStateMachine` | `src/ua_program/program.py` |
| Vision implementation: `VisionProgram`, `install_vision_program` | `src/vision_server/vision_program.py` |
| Installation into the server, status nodes, `BusyReason` | `src/vision_server/runner/part10.py` (`install_part10_program`) |

`VisionProgram` keeps no job state; it calls the same `JobRunner` as `StartSingleJob`/`Stop`, so both interfaces always see the same job.

## ProgramStateMachine

- `ProgramStateMachine` binds the ns=0 states and transitions of `ProgramStateMachineType` (`i=2391`) to an instance.
- States: `Halted` (11), `Ready` (12), `Running` (13), `Suspended` (14).
- `Program` is abstract: a subclass implements `start`, `halt`, `reset`, `suspend`, `resume` as `async ... -> str | None`. `None` = state changes; a string = rejection, sent as `ProgramTransitionEvent` with severity 500.
- Instances get string NodeIds (`ns=<vision>;s=VisionProgram.Start`), so a client need not browse.

## VisionProgram

| Node (below `ns=<vision>;s=VisionProgram`) | Meaning |
| --- | --- |
| `Start`, `Halt`, `Reset`, `Suspend`, `Resume` | methods without inputs |
| `ParameterSet/RecipeId`, `ParameterSet/Continuous` | writable inputs, set before `Start` |
| `ResultSet/JobId`, `ErrorCode`, `ExecutionMode` | job outputs; `ExecutionMode` is `idle`, `single` or `continuous` |
| `ResultSet/LatestResultJson` | reference to `VisionMachine.LatestResultJson` |
| `Status/BusyReason` | empty when free, else a German reason |
| `Status/DeviceHealth`, `CameraStreamMode`, `CalibrationProgress`, `HandEyeStatus`, `Layer2Status`, `CellWatchStatus`, ... | `Organizes` references to existing nodes (those present on this Pi) |
| vision methods (`StartCalibration`, `SaveRawImage`, ...) | `HasComponent` references to the `VisionMachine` methods, with named arguments |
| `Manufacturer`, `Model`, `SerialNumber` | properties; `SerialNumber` = `visionSystemId` |

- `Start` reads `RecipeId` and `Continuous`, starts a job and moves to `Running`; if the job runner rejects it, the state stays and the text carries the error name.
- At job end the program returns to `Ready` (also for a failed job; the reason is in `ResultSet/ErrorCode`). It does not go to `Halted`.
- `Halt` aborts the job and goes to `Halted`; `Reset` returns to `Ready`.
- `Suspend` and `Resume` always reject: a vision job cannot be paused.
- If the vision system did not become operational, the program starts in `Halted`.
- `BusyReason` (`runner/part10.py`, `runner/install.py`): `Job laeuft`, `Kamera-Kalibrierung laeuft`, `Layer-2-Lauf laeuft`, `Hand-Auge-Kalibrierung laeuft`; refreshed every 0.2 s.
- `VisionErrorCode` (`errors.py`) is an OPC UA Enumeration; wire value Int32:

| Code | Name | Code | Name |
| --- | --- | --- | --- |
| 0 | `OK` | 4 | `UNKNOWN_RECIPE` |
| 1 | `INVALID_STATE` | 5 | `DETECTION_FAILED` |
| 2 | `INVALID_ARGUMENT` | 6 | `INTERNAL` |
| 3 | `BUSY` | 7 | `CANCELLED` |

## How a client uses it

1. Find the Pi: mDNS `_opcua-tcp._tcp.local.` (instance = `visionSystemId`, TXT `path`), or via the aggregation server.
2. Resolve the namespace index of `http://launch-rm.de/vision` at runtime.
3. Treat every Part 10 program object directly under `Objects` as a device; `Status` and `ResultSet` children are its variables, its methods its actions. The WSC backend does exactly this ([webskill-backend](../webskill-backend/README.md)).
4. Subscribe to events on `VisionProgram`, write `RecipeId` (and `Continuous`), call `Start`, wait for `Running -> Ready`, read `ResultSet` and `LatestResultJson`.
5. Check `Status/BusyReason` before starting other work.

The 40100 events do not reach a subscription on `VisionProgram`; the program raises its own `ProgramTransitionEvent`.

## Where to read more

| Topic | Document |
| --- | --- |
| Program interface, flow, events, errors, discovery | [interfaces/part10-program-interface.md](../../interfaces/part10-program-interface.md) |
| Vision server interface | [interfaces/vision-server-interface-short.md](../../interfaces/vision-server-interface-short.md), [interfaces/vision-server-interface.md](../../interfaces/vision-server-interface.md) |
| Per-function reference | [code/cell-vision-server.md](../../code/cell-vision-server.md) |
| Vision server component | [vision-server](../vision-server/README.md) |
| Client side | [webskill-backend](../webskill-backend/README.md), [webskill-frontend](../webskill-frontend/README.md) |
| Integration | [usage/integration.md](../../usage/integration.md) |
