# webskillcomposition: backend

Reference for the backend of webskillcomposition, branch `ADP_Lokalisierung`.
Overview: [code-dokumentation.md](../code-dokumentation.md).

Scope: `backend/main.py`, `backend/src/backend/**`, `backend/catalog/`, `backend/tests/`.

## What is new and what already existed

The backend existed before our project. It was written by the webskillcomposition team (OPC UA
client, runtime, models, WebSocket API, point-cloud surface jobs). Our group (Meric Lotz, John
Glanz, Sven Nachtigal (git: NobbisCode), Shaming Zhang) added the module catalog, the robot log and the client hub,
extended the OPC UA connection so it works reliably with the two vision Pis, and added the discovery of the Pis as devices (Part 10 program objects, mDNS).



### Documentation sources

| Document | Written by | Covers |
| --- | --- | --- |
| `webskillcomposition/docs/architecture.md` | webskillcomposition team | project structure, system overview |
| `webskillcomposition/docs/data-and-interface-model.md` | webskillcomposition team | client commands, server events, robot session shape, action semantics, raw OPC UA layer |
| `webskillcomposition/docs/development.md` | webskillcomposition team, small additions by us | setup and running |
| `webskillcomposition/docs/vision-workflows.md` | us | vision, catalog and tag workflows of `ADP_Lokalisierung`, module catalog API |
| this page | us | module reference with origin, our backend changes |
| [`../vision-server-interface-short.md`](../vision-server-interface-short.md) | us | the Pi side of the OPC UA connection |

## Module reference

Origin per heading: **new**, **extended** (existing module with our additions), **existing**.

### `backend/main.py` — existing

- Process entry point. Puts `backend/src` on `sys.path`, imports `main` from `backend.app`, and runs it when executed as a script.
- `main()` (in `backend/app.py`) sets up the robot log and starts uvicorn on `0.0.0.0:8000` with `backend.app:app`.
- Setup and run instructions: `webskillcomposition/docs/development.md`.

### `backend/src/backend/app.py` — extended

- FastAPI application factory. `create_app(module_catalog_store=None)` builds the app, stores a `RuntimeRegistry` and a `ModuleCatalogStore` on `app.state`, and registers the catalog router and the two WebSocket routes.
- Entry points: `create_app()`, the module-level `app`, and `main()`.
- Routes: `/api/module-catalog` (REST, new), `/ws` (JSON API), `/ws/surface` (point-cloud upload and surface jobs).
- Creates a `NetworkDiscovery` (`app.state.network_discovery`), started and stopped with the app. A change in the announced servers is broadcast to all pages as `serversAnnounced` through the client hub.
- Serves `./www` at `/` only when the `HOST` environment variable is set.

### `backend/src/backend/api/` — new

- HTTP API. Currently only `module_catalog.py`.
- `GET /api/module-catalog` returns the stored catalog. `PUT /api/module-catalog` takes `{baseRevision, catalog}`.
- Invalid catalog returns 422. A revision conflict returns 409 with the current catalog in the body.

### `backend/src/backend/services/` - new

- `module_catalog_store.py` — **new**: `ModuleCatalogStore` reads and writes the catalog file under a lock. Each write must name the revision it is based on. `validate_catalog` checks the schema `wsc.cellModules.catalog/1` and a 5 MiB size cap. `default_catalog_path()` uses `backend/catalog/` unless `WSC_DATA_DIR` is set. See [the catalog unit](#backendcatalog--new).
- `runtime_registry.py` — existing: `RuntimeRegistry` is the in-memory lookup of `ServerSession`, robots, and `SurfaceJob` objects. It is shared by all WebSocket connections.

### `backend/src/backend/runtime/` — existing, extended

- `application_service.py` (extended): `handle_client_message` dispatches each parsed client command. It covers connect, discover, `listAnnouncedServers`, subscribe, and call. It also runs the server liveness watch (new) and the action-state watch. Timeouts: connect 120 s, discover 600 s, long-action warning 60 s.
  - Discovery registers robots and devices (`result.robots` and `result.devices`) as sessions.
  - Program devices: the watcher snapshots the `ResultSet` before `Start`, ends when the state is idle and a non-idle state was seen, a result value changed, or 1.5 s passed, then reads the final `ResultSet` into `RobotActionState.result` (`_complete_program_action`). A non-zero `error_code` gives `failed` with the `VisionErrorCode` description as `message`.
  - A refused start of a device program starts no watcher (its state would overwrite the failure).
  - OPC UA Bad status to error `code`: `BadNodeIdUnknown`/`BadMethodInvalid` give `methodMissing`; `BadNothingToDo`/`BadInvalidState` give `nothingToDo` (`_method_status_error_code`).
- `action_execution.py` (extended): `execute_robot_action` and `transition_robot_action` run method and skill actions over an `AsyncUaServerConnection`. A method action returns its outputs by name in `result.outputs` and fails on a non-zero `Error` output with `error_code` and the enum description. A skill action returns the `ResultSet` snapshot taken before `Start`.
- `robot_session.py` (existing), `server_session.py` (extended): dataclass state holders for a robot and a server, with `to_info()` for the wire models.
- `surface_job.py` (existing): `SurfaceJob` holds one upload (`PcdAssembly`) and its `SurfaceProcessingConfig`.
- Details: `webskillcomposition/docs/data-and-interface-model.md` (action layer, robot session shape).

### `backend/src/backend/opcua/` — existing, extended

- `server_connection.py` (extended): `AsyncUaServerConnection` wraps the asyncua `Client`. It handles connect, disconnect, discovery, and the data-change and event subscriptions. new: reconnect with a fresh client and restore of subscriptions, keepalive, custom event types.
- `asyncua_discovery.py` (extended): browses the OPC UA address space. It finds motion devices, skills, methods, and node details.
  - `discover_program_device_descriptors`: every Part 10 program object directly under `Objects` is a device. `kind` is `vision` when the MachineVision namespace is present, else `device`. Actions: the program (key `vision_program` for kind `vision`) and its method children; variables: the children of `Status` and `ResultSet`. A method only referenced by the program gets its own name as path.
  - `read_method_arguments` resolves Enumeration DataTypes (`EnumValues`, else `EnumStrings`) into `MethodArgument.enum_values`, cached per session and DataType.
- `discovery.py` (extended): transport-free builders. `build_robot_session_infos` and the `build_*_action_binding` functions turn discovery results into robot models. `ServerDiscoveryResult` has `robots` and `devices`.
- `network_discovery.py` (new): `NetworkDiscovery` browses mDNS `_opcua-tcp._tcp.local.` with `zeroconf`, builds the URL from address, port and TXT `path`, and reports the sorted set of `AnnouncedServer(url, name)` on every change. `WSC_MDNS=0` switches it off.
- `method_calls.py` (extended): converts JSON inputs to OPC UA argument types (`coerce_*`, `read_method_arguments`). Provides `call_robot_method`, `call_raw_method`, `to_jsonable`, and `map_method_outputs` (outputs by declared argument name).
- Details: `webskillcomposition/docs/data-and-interface-model.md` (raw OPC UA capability layer).

### `backend/src/backend/websocket/` - extended

- `router.py` — extended: `websocket_endpoint` for `/ws`. Parses each text frame with `parse_client_message_json`, calls `handle_client_message`, and sends the resulting events.
- `client_hub.py` — **new**: `ClientHub` tracks the pages connected now. `LiveEmitter` sends live values from long-running subscriptions to the current pages, not to the page that started them.
- `surface_router.py` — existing: `websocket_surface_endpoint` for `/ws/surface`. Handles begin and finish upload, abort, and job processing, then streams results.

### `backend/src/backend/models/` — existing, extended

- Pydantic contracts. `base.py` defines `ContractModel` with camelCase aliases (`to_camel`).
- `messages.py` (extended): `ClientMessage` and `ServerMessage` unions plus `parse_client_message_json` and `parse_server_message_json`. New: `ListAnnouncedServersCommand`, `ServersAnnouncedEvent`, `AnnouncedServer`.
- `robot.py` (extended): `RobotSessionInfo.kind` (`robot`, `vision`, `device`; `robot_id` is the device id for every kind), `RobotActionState.result` and `error_code`.
- `opcua.py` (extended): `EnumText` and `MethodArgument.enum_values`.
- `server.py`, `address_space.py`: server and address-space models.
- `surface.py`: surface commands, events, and `SurfaceProcessingConfig`.
- `__init__.py` re-exports the public names.

### `backend/src/backend/geometry/` — existing

- `pointcloud_transport.py`: PCD2 chunk encoding and decoding (`encode_pcd2_chunk`, `decode_pcd2_chunk`, `iter_encoded_pcd2_chunks`, `quantize_points_to_pcd2_chunks`). `PcdAssembly` collects chunks into a point cloud.
- `surface_reconstruction.py`: voxel field from points, flood fill of outside air, shell extraction, and `compute_surface_points_from_xyz`. Needs SciPy at runtime (`_require_scipy`).

### `backend/src/backend/robot_log.py` — new

- `setup_robot_log()` writes `backend/data/logs/robot.log` with rotation (5 MiB, 4 backups). Only the `robot_log` logger and asyncua warnings and errors go there.
- `describe_inputs()` formats method inputs for log lines.
- Called from `main()` only, not from `create_app`, so tests do not write the log.

### `backend/catalog/` — new

- `module_catalog.json`: the cell's module catalog, tracked in git. The frontend owns its content. The backend validates and stores it through `ModuleCatalogStore`.
- Not a Python package. Its contents are not described here.
- `backend/data/` (not tracked, listed in `backend/.gitignore`) holds the runtime robot log and a second `module_catalog.json`. The default code path uses `backend/catalog/` unless `WSC_DATA_DIR` is set.

### `backend/tests/` — mixed

| Test | Origin |
| --- | --- |
| `test_client_hub.py`: `ClientHub` delivery of events | new |
| `test_module_catalog_api.py`: `/api/module-catalog` (save, read, revision conflict, file location) | new |
| `test_robot_log.py`: `describe_inputs` and `setup_robot_log` | new |
| `test_device_actions.py`: method outputs by name, enum error texts, program watcher and `ResultSet`, `methodMissing`/`nothingToDo` | new |
| `test_network_discovery.py`: `NetworkDiscovery` (browse and broadcast changes, switch-off, real mDNS announcement) | new |
| `test_program_discovery.py`: Part 10 program devices, kinds, variables, enum arguments | new |
| `test_server_connection.py`: `AsyncUaServerConnection` with the asyncua layer | mostly new (67 %) |
| `test_application_service.py`: command handling, skill-state mapping, discovery registration | existing, extended (12 %) |
| `test_method_calls.py`: argument validation and coercion for OPC UA methods | existing, extended (12 %) |
| `test_asyncua_discovery.py`: value-type and skill-owned-method filtering | existing |
| `test_messages.py`: message and surface-message parsing, camelCase JSON | existing |
| `test_robot_models.py`: robot IDs, session binding, action normalization | existing |
| `test_runtime_registry.py`: `RuntimeRegistry` indexing | existing |
| `test_surface_websocket_router.py`: `/ws/surface` upload and processing end to end | existing |
| `test_websocket_router.py`: `/ws` error handling, discovery, demo-server snapshot | existing |
