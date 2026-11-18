# WSC backend

The webskillcomposition (WSC) backend: an OPC UA client with a WebSocket API for the browser frontend. It discovers the robot and the vision Pis as devices and runs their actions.

Code: `backend/src/backend/` in the WSC repo (`/home/meric/ADP/webskillcomposition`, branch `geraete-discovery-stufe1`).

## Role

| Direction | What |
| --- | --- |
| Frontend to backend | JSON commands over `/ws` (FastAPI, `websocket/router.py`) |
| Backend to devices | OPC UA via asyncua (`opcua/server_connection.py`, `AsyncUaServerConnection`) |
| Backend to frontend | JSON events; live values go to all connected pages (`websocket/client_hub.py`) |
| Other routes | `/ws/surface` (point-cloud jobs), `/api/module-catalog` (REST, catalog file) |

The backend holds no workflow logic. Sequencing, IK and robot driving run in the browser ([frontend README](../webskill-frontend/README.md)).

## Discovery: robots and devices

| Step | Where | What |
| --- | --- | --- |
| Announce | Pi | mDNS service `_opcua-tcp._tcp.local.` |
| Browse | `opcua/network_discovery.py` | `NetworkDiscovery` builds the URL from address, port and TXT `path`; `WSC_MDNS=0` switches it off |
| Report | `websocket/` | event `serversAnnounced` (`servers: [{ url, name }]`); command `listAnnouncedServers`; broadcast on change |
| Connect and discover | `connectServer`, `discoverRobots` | `ServerDiscoveryResult` has `robots` and `devices`, both registered as sessions |

- Robot: a Robotics `MotionDevice`, `kind: "robot"`.
- Program device: every Part 10 program object directly under `Objects` (`discover_program_device_descriptors`, `opcua/asyncua_discovery.py`). `kind` is `vision` if the MachineVision namespace exists, else `device`.
- A program device offers the program as action `vision_program` (kind `vision`), its methods as method actions, and the children of `Status` and `ResultSet` as variables (for example `busy_reason`). Keys are the snake_case of the BrowseName.
- Every device is a `RobotSessionInfo`; `robotId` is the device id for all kinds.

## Action execution

| Action kind | Behaviour (`runtime/action_execution.py`) |
| --- | --- |
| Method | Outputs by declared argument name in `result.outputs`. A non-zero `Error` output fails the action with `errorCode` and the enum description. |
| Skill or program | `ResultSet` snapshot taken before `Start`; a watcher polls the state (`_watch_skill_action_state`, `runtime/application_service.py`, 0.25 s). |

- Program watcher: ends when the state is idle after a non-idle state was seen, a result value changed, or 1.5 s passed. The final `ResultSet` goes into `RobotActionState.result`. A non-zero `error_code` gives `failed`.
- A refused start of a device program starts no watcher.
- Skill state text maps to `idle`, `halted`, `reset`, `failed` or `running`.
- Timeouts: connect 120 s, discover 600 s, long-action warning 60 s.
- A liveness watch reconnects with a fresh client and restores subscriptions.

| Error `code` (event `error`) | OPC UA cause |
| --- | --- |
| `methodMissing` | `BadNodeIdUnknown`, `BadMethodInvalid` |
| `nothingToDo` | `BadNothingToDo`, `BadInvalidState` |
| `methodCallFailed`, `actionExecutionFailed`, `rawMethodCallFailed` | any other failure |

## WebSocket message model

Pydantic models with camelCase JSON (`models/messages.py`); each message has `type` and optional `requestId`.

| Family | Commands | Events |
| --- | --- | --- |
| Server | `connectServer`, `disconnectServer`, `discoverRobots`, `listAnnouncedServers` | `serverConnected`, `serverDisconnected`, `robotsDiscovered`, `serversAnnounced` |
| Robot and device | `executeRobotAction`, `haltRobotAction`, `resetRobotAction`, `callRobotMethod`, `subscribeRobotJoints`, `subscribeRobotMode` | `robotInfo`, `robotActionState`, `robotJointState`, `robotModeChanged`, `methodResult` |
| Raw OPC UA | `callRawMethod`, `writeNodeValue`, `subscribeNode`, `subscribeEvent`, `browseAddressSpace*` | `nodeValueChanged`, `opcuaEvent`, `addressSpace*` |
| Errors | | `error` (with `code`) |

`robotActionState.data` carries `status`, `result` (final `ResultSet` or `{ outputs }`) and `errorCode`. Full contracts: `docs/data-and-interface-model.md`.

## Run

Command `uv run backend` in `backend/` (uvicorn on `0.0.0.0:8000`). Setup, environment (`WSC_DATA_DIR`, `WSC_MDNS`) and the frontend: [installation](../../usage/installation.md), [quickstart](../../usage/quickstart.md).

## Ours and pre-existing

| Part | Origin |
| --- | --- |
| App, runtime, models, OPC UA client, `/ws`, surface jobs | WSC team |
| Module catalog (`services/module_catalog_store.py`, `api/`), `robot_log.py`, `client_hub.py` | ours |
| Reconnect, keepalive, `network_discovery.py`, Part 10 program devices, method outputs by name, program watcher, error codes | ours (extensions) |

Per-module origin: [code/webskill-backend.md](../../code/webskill-backend.md).

## Where to read more

| Topic | Document |
| --- | --- |
| Per-module reference | [code/webskill-backend.md](../../code/webskill-backend.md) |
| Pi side of the connection | [vision-server-interface-short.md](../../interfaces/vision-server-interface-short.md), [part10-program-interface.md](../../interfaces/part10-program-interface.md) |
| Integration and layers | [integration](../../usage/integration.md), [layers](../../layers.md) |
| Vision server and Part 10 | [vision-server](../vision-server/README.md), [part10](../part10/README.md) |
| WSC docs | `docs/data-and-interface-model.md`, `docs/architecture.md`, `docs/development.md`, `docs/vision-workflows.md` |
