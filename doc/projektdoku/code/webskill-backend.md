# webskillcomposition: backend

Reference for the backend of webskillcomposition, branch `ADP_Lokalisierung`, as of 2026-11-17.
Overview: [code-dokumentation.md](../code-dokumentation.md).

Scope: `backend/main.py`, `backend/src/backend/**`, `backend/catalog/`, `backend/tests/`.

## What is ours and what already existed

The backend existed before our project. It was written by the webskillcomposition team (OPC UA
client, runtime, models, WebSocket API, point-cloud surface jobs). Our group (Meric Lotz, John
Glanz, Sven Nachtigal (git: NobbisCode), Shaming Zhang) added the module catalog, the robot log and the client hub,
and extended the OPC UA connection so it works reliably with the two vision Pis.

Share of lines on `ADP_Lokalisierung` (`git blame -w -M -C`, translation-only commit `a2509f2`
ignored): **about 21 % of all backend lines, 14 % of the code** (without tests and catalog).

| Part | Origin | Our share | What we did | Documented in |
| --- | --- | ---: | --- | --- |
| `api/module_catalog.py` | **ours** | 100 % | REST API for the module catalog | `vision-workflows.md` (Module catalog), this page |
| `services/module_catalog_store.py` | **ours** | 100 % | revision-checked catalog storage | `vision-workflows.md`, this page |
| `catalog/module_catalog.json` | **ours** | 100 % | the cell's catalog (data) | `vision-workflows.md` |
| `robot_log.py` | **ours** | 100 % | rotating robot log file | this page |
| `websocket/client_hub.py` | **ours** | 99 % | live values to all connected pages, not only the starting one | this page |
| `opcua/server_connection.py` | extended | 21 % | reconnect with a fresh client and restore of all subscriptions, keepalive (`check_alive`), event subscriptions with custom event types, `read_namespace_uris`, longer request timeout for slow Pi methods | this page; base: `data-and-interface-model.md` |
| `runtime/application_service.py` | extended | 17 % | server liveness watch, re-subscribe robot joints after reconnect, skill state after halt/reset, OPC UA events to the client, robots from the catalog | this page; base: `data-and-interface-model.md` |
| `runtime/server_session.py` | extended | 15 % | liveness task handling | this page |
| `websocket/router.py` | extended | 23 % | sends events through the client hub | this page |
| `app.py` | extended | 26 % | catalog router and store, robot log in `main()` | this page |
| `opcua/asyncua_discovery.py` | extended | 12 % | more robust name lookup (`children_by_names`, display/browse name) | `data-and-interface-model.md` |
| `opcua/method_calls.py` | existing | 5 % | JSON conversion of results (`to_jsonable`, `coerce_array_value`) | `data-and-interface-model.md` |
| `opcua/discovery.py`, `runtime/action_execution.py`, `runtime/robot_session.py`, `runtime/surface_job.py`, `services/runtime_registry.py` | existing | 0–2 % | — | `architecture.md`, `data-and-interface-model.md` |
| `models/` | existing | 1 % | — | `data-and-interface-model.md` |
| `geometry/`, `websocket/surface_router.py` | existing | 0 % | — | `architecture.md` |
| `backend/tests/` | mixed | 23 % | `test_client_hub.py`, `test_module_catalog_api.py`, `test_robot_log.py` (100 %), `test_server_connection.py` (67 %) | this page |

Notes on the numbers: `git blame` credits a line to whoever changed it last. Lines we only
reformatted count as ours, lines of ours that others changed later count as theirs. The calling
of Pi methods (`CallRawMethodCommand` → `call_raw_method`) already existed; we use it for the Pis.

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

Origin per heading: **ours**, **extended** (existing module with our additions), **existing**.

### `backend/main.py` — existing

- Process entry point. Puts `backend/src` on `sys.path`, imports `main` from `backend.app`, and runs it when executed as a script.
- `main()` (in `backend/app.py`) sets up the robot log and starts uvicorn on `0.0.0.0:8000` with `backend.app:app`.
- Setup and run instructions: `webskillcomposition/docs/development.md`.

### `backend/src/backend/app.py` — extended

- FastAPI application factory. `create_app(module_catalog_store=None)` builds the app, stores a `RuntimeRegistry` and a `ModuleCatalogStore` on `app.state`, and registers the catalog router and the two WebSocket routes.
- Entry points: `create_app()`, the module-level `app`, and `main()`.
- Routes: `/api/module-catalog` (REST, ours), `/ws` (JSON API), `/ws/surface` (point-cloud upload and surface jobs).
- Serves `./www` at `/` only when the `HOST` environment variable is set.

### `backend/src/backend/api/` — ours

- HTTP API. Currently only `module_catalog.py`.
- `GET /api/module-catalog` returns the stored catalog. `PUT /api/module-catalog` takes `{baseRevision, catalog}`.
- Invalid catalog returns 422. A revision conflict returns 409 with the current catalog in the body.

### `backend/src/backend/services/`

- `module_catalog_store.py` — **ours**: `ModuleCatalogStore` reads and writes the catalog file under a lock. Each write must name the revision it is based on. `validate_catalog` checks the schema `wsc.cellModules.catalog/1` and a 5 MiB size cap. `default_catalog_path()` uses `backend/catalog/` unless `WSC_DATA_DIR` is set. See [the catalog unit](#backendcatalog--ours).
- `runtime_registry.py` — existing: `RuntimeRegistry` is the in-memory lookup of `ServerSession`, robots, and `SurfaceJob` objects. It is shared by all WebSocket connections.

### `backend/src/backend/runtime/` — existing, extended

- `application_service.py` (extended): `handle_client_message` dispatches each parsed client command. It covers connect, discover, subscribe, and call. It also runs the server liveness watch (ours) and the action-state watch. Timeouts: connect 120 s, discover 600 s, long-action warning 60 s.
- `action_execution.py` (existing): `execute_robot_action` and `transition_robot_action` run method and skill actions over an `AsyncUaServerConnection`.
- `robot_session.py` (existing), `server_session.py` (extended): dataclass state holders for a robot and a server, with `to_info()` for the wire models.
- `surface_job.py` (existing): `SurfaceJob` holds one upload (`PcdAssembly`) and its `SurfaceProcessingConfig`.
- Details: `webskillcomposition/docs/data-and-interface-model.md` (action layer, robot session shape).

### `backend/src/backend/opcua/` — existing, extended

- `server_connection.py` (extended): `AsyncUaServerConnection` wraps the asyncua `Client`. It handles connect, disconnect, discovery, and the data-change and event subscriptions. Ours: reconnect with a fresh client and restore of subscriptions, keepalive, custom event types.
- `asyncua_discovery.py` (extended): browses the OPC UA address space. It finds motion devices, skills, methods, and node details.
- `discovery.py` (existing): transport-free builders. `build_robot_session_infos` and the `build_*_action_binding` functions turn discovery results into robot models.
- `method_calls.py` (existing): converts JSON inputs to OPC UA argument types (`coerce_*`, `read_method_arguments`). Provides `call_robot_method`, `call_raw_method`, and `to_jsonable`.
- Details: `webskillcomposition/docs/data-and-interface-model.md` (raw OPC UA capability layer).

### `backend/src/backend/websocket/`

- `router.py` — extended: `websocket_endpoint` for `/ws`. Parses each text frame with `parse_client_message_json`, calls `handle_client_message`, and sends the resulting events.
- `client_hub.py` — **ours**: `ClientHub` tracks the pages connected now. `LiveEmitter` sends live values from long-running subscriptions to the current pages, not to the page that started them.
- `surface_router.py` — existing: `websocket_surface_endpoint` for `/ws/surface`. Handles begin and finish upload, abort, and job processing, then streams results.

### `backend/src/backend/models/` — existing

- Pydantic contracts. `base.py` defines `ContractModel` with camelCase aliases (`to_camel`).
- `messages.py`: `ClientMessage` and `ServerMessage` unions plus `parse_client_message_json` and `parse_server_message_json`.
- `robot.py`, `server.py`, `opcua.py`, `address_space.py`: robot, server, binding, and address-space models.
- `surface.py`: surface commands, events, and `SurfaceProcessingConfig`.
- `__init__.py` re-exports the public names.

### `backend/src/backend/geometry/` — existing

- `pointcloud_transport.py`: PCD2 chunk encoding and decoding (`encode_pcd2_chunk`, `decode_pcd2_chunk`, `iter_encoded_pcd2_chunks`, `quantize_points_to_pcd2_chunks`). `PcdAssembly` collects chunks into a point cloud.
- `surface_reconstruction.py`: voxel field from points, flood fill of outside air, shell extraction, and `compute_surface_points_from_xyz`. Needs SciPy at runtime (`_require_scipy`).

### `backend/src/backend/robot_log.py` — ours

- `setup_robot_log()` writes `backend/data/logs/robot.log` with rotation (5 MiB, 4 backups). Only the `robot_log` logger and asyncua warnings and errors go there.
- `describe_inputs()` formats method inputs for log lines.
- Called from `main()` only, not from `create_app`, so tests do not write the log.

### `backend/catalog/` — ours

- `module_catalog.json`: the cell's module catalog, tracked in git. The frontend owns its content. The backend validates and stores it through `ModuleCatalogStore`.
- Not a Python package. Its contents are not described here.
- `backend/data/` (not tracked, listed in `backend/.gitignore`) holds the runtime robot log and a second `module_catalog.json`. The default code path uses `backend/catalog/` unless `WSC_DATA_DIR` is set.

### `backend/tests/` — mixed

| Test | Origin |
| --- | --- |
| `test_client_hub.py`: `ClientHub` delivery of events | ours |
| `test_module_catalog_api.py`: `/api/module-catalog` (save, read, revision conflict, file location) | ours |
| `test_robot_log.py`: `describe_inputs` and `setup_robot_log` | ours |
| `test_server_connection.py`: `AsyncUaServerConnection` with the asyncua layer | mostly ours (67 %) |
| `test_application_service.py`: command handling, skill-state mapping, discovery registration | existing, extended (12 %) |
| `test_method_calls.py`: argument validation and coercion for OPC UA methods | existing, extended (12 %) |
| `test_asyncua_discovery.py`: value-type and skill-owned-method filtering | existing |
| `test_messages.py`: message and surface-message parsing, camelCase JSON | existing |
| `test_robot_models.py`: robot IDs, session binding, action normalization | existing |
| `test_runtime_registry.py`: `RuntimeRegistry` indexing | existing |
| `test_surface_websocket_router.py`: `/ws/surface` upload and processing end to end | existing |
| `test_websocket_router.py`: `/ws` error handling, discovery, demo-server snapshot | existing |
