# Integration

How a device or program is integrated through OPC UA Part 10 so that the WSC backend discovers it without Pi-specific code.

Sources of truth: [`work-plans/pi-backend-discovery.md`](../work-plans/pi-backend-discovery.md) (Interfaces), WSC `backend/src/backend/opcua/asyncua_discovery.py` and `network_discovery.py`. Address space in full: [`interfaces/part10-program-interface.md`](../interfaces/part10-program-interface.md).

## Steps

1. Announce the server by mDNS (`_opcua-tcp._tcp.local.`, TXT `path`, `caps`).
2. Put one Part 10 program object directly under `Objects`.
3. Give it `ParameterSet`, `ResultSet`, `CurrentState` and at least one of `Start`/`Halt`/`Reset`/`Suspend`/`Resume`.
4. Add `Status` variables and vision-style methods with named arguments.
5. Report failures as `VisionErrorCode`-style enumerations.

## 1. mDNS announcement

| Entry | Value |
| --- | --- |
| Service type | `_opcua-tcp._tcp.local.` |
| Instance name | device identity (Pis: `visionSystemId`) |
| Port | `4840` |
| TXT `path` | endpoint path (Pis: `/raspi/server/`) |
| TXT `caps` | `DA` |

- The backend (`network_discovery.py`) builds `opc.tcp://<address>:<port><path>` from the entry and sends `serversAnnounced` to the frontend, which connects and discovers.
- `WSC_MDNS=0` switches browsing off; manual URL entry stays as fallback.
- The server itself must announce its LAN IPv4, not `0.0.0.0` (Pi implementation: `src/vision_server/discovery/mdns.py`).

## 2. Discovery rule

`discover_program_device_descriptors` treats **every object directly under `Objects`** that passes `build_skill_binding_from_node` as one device.

| Check | Required |
| --- | --- |
| Node class | Object |
| Children (by browse name) | at least one of `ParameterSet`, `ResultSet`, `CurrentState` |
| Methods | at least one of `Start`, `Halt`, `Reset`, `Suspend`, `Resume` |
| Name | non-empty after `normalize_capability_name` |

Objects that fail the check are skipped. The type (`ProgramStateMachineType`, `ns=0;i=2391`) is not tested, the structure is.

## 3. What the backend derives

| Result | Rule |
| --- | --- |
| `kind` | `vision` if the namespace array contains `http://opcfoundation.org/UA/MachineVision`, else `device` |
| Device id | `make_robot_id(server_url, <program NodeId>)` |
| Info | properties `Manufacturer`, `Model`, `SerialNumber` of the program |
| Program action | key `vision_program` for kind `vision`, otherwise the normalized BrowseName |
| Method actions | every method child of the program (not only referenced), key = normalized BrowseName (`SaveRawImage` becomes `save_raw_image`) |
| Variables | children of `Status` and `ResultSet`, key = normalized name (`BusyReason` becomes `busy_reason`, `LatestResultJson` becomes `latest_result_json`) |

A method that is only referenced by the program gets its own name as discovery path.

## 4. Running an action

| Action | Behaviour |
| --- | --- |
| Program (skill) | snapshot `ResultSet`, write `ParameterSet` inputs, call `Start`; the watcher ends when the state is idle again; `ResultSet` is read into `result` |
| Program failure | `ErrorCode` != 0 sets status `failed`, `error_code`, and `message` = enum description |
| Method | `result.outputs` = output arguments by name; an output named `Error` != 0 sets status `failed` |
| Busy | `Status/BusyReason` non-empty means the device refuses new work; empty means free |

## 5. Named arguments and enum outputs

- Every input and output argument has a name and a description (`add_vision_method` in `src/vision_server/runner/uamethods.py`).
- Error outputs are named `Error` and typed with an Enumeration DataType. The backend (`read_enum_values`) reads `EnumValues` (else `EnumStrings`) into `MethodArgument.enum_values`, so the UI shows the device's own text.

| Value | Name | `EnumValues` description (German) |
| --- | --- | --- |
| 0 | `OK` | OK |
| 1 | `INVALID_STATE` | Im aktuellen Zustand nicht möglich (z. B. noch kein Kamerabild). |
| 2 | `INVALID_ARGUMENT` | Ungültiger Parameter. |
| 3 | `BUSY` | Der Pi ist beschäftigt – danach erneut versuchen. |
| 4 | `UNKNOWN_RECIPE` | Unbekanntes Rezept. |
| 5 | `DETECTION_FAILED` | Erkennung fehlgeschlagen. |
| 6 | `INTERNAL` | Interner Fehler auf dem Pi. |
| 7 | `CANCELLED` | Abgebrochen. |

## Build a program for a new module

- Inherit from `ua_program.Program` (`src/ua_program/program.py`) and implement `start`/`halt`/`reset`/`suspend`/`resume` (`None` = accept, `str` = reject with this message).
- Pattern to copy: `src/vision_server/vision_program.py` (`ParameterSet`, `ResultSet`, `Status`, properties).
- Resolve namespace indices by URI, never hard-code them.

More: [`components/part10/README.md`](../components/part10/README.md), [`components/webskill-backend/README.md`](../components/webskill-backend/README.md), [`adr/README.md`](../adr/README.md).
