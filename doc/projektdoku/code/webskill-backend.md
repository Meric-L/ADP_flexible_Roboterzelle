# webskillcomposition: backend

Detailed reference for the backend of webskillcomposition, branch `ADP_Lokalisierung`, as of 2026-11-16. Overview: [code-dokumentation.md](../code-dokumentation.md).


Scope: `backend/main.py`, `backend/src/backend/**`, `backend/catalog/`, `backend/tests/`. Architecture context: `webskillcomposition/docs/architecture.md` (docs/architecture.md) (project structure, system overview). Message and data contracts: `webskillcomposition/docs/data-and-interface-model.md` (docs/data-and-interface-model.md) (client commands, server events, robot session shape, action semantics).

### `backend/main.py`

- Process entry point. Puts `backend/src` on `sys.path`, imports `main` from `backend.app`, and runs it when executed as a script.
- `main()` (in `backend/app.py`) sets up the robot log and starts uvicorn on `0.0.0.0:8000` with `backend.app:app`.
- Setup and run instructions: `webskillcomposition/docs/development.md` (docs/development.md).

### `backend/src/backend/app.py`

- FastAPI application factory. `create_app(module_catalog_store=None)` builds the app, stores a `RuntimeRegistry` and a `ModuleCatalogStore` on `app.state`, and registers the catalog router and the two WebSocket routes.
- Entry points: `create_app()`, the module-level `app`, and `main()`.
- Routes: `/api/module-catalog` (REST), `/ws` (JSON API), `/ws/surface` (point-cloud upload and surface jobs).
- Serves `./www` at `/` only when the `HOST` environment variable is set.

### `backend/src/backend/api/`

- HTTP API. Currently only `module_catalog.py`.
- `GET /api/module-catalog` returns the stored catalog. `PUT /api/module-catalog` takes `{baseRevision, catalog}`.
- Invalid catalog returns 422. A revision conflict returns 409 with the current catalog in the body.

### `backend/src/backend/services/`

- `module_catalog_store.py`: `ModuleCatalogStore` reads and writes the catalog file under a lock. Each write must name the revision it is based on. `validate_catalog` checks the schema `wsc.cellModules.catalog/1` and a 5 MiB size cap. `default_catalog_path()` uses `backend/catalog/` unless `WSC_DATA_DIR` is set. See [the catalog unit](#backendcatalog).
- `runtime_registry.py`: `RuntimeRegistry` is the in-memory lookup of `ServerSession`, robots, and `SurfaceJob` objects. It is shared by all WebSocket connections.

### `backend/src/backend/runtime/`

- `application_service.py`: `handle_client_message` dispatches each parsed client command. It covers connect, discover, subscribe, and call. It also runs the server liveness watch and the action-state watch. Timeouts: connect 120 s, discover 600 s, long-action warning 60 s.
- `action_execution.py`: `execute_robot_action` and `transition_robot_action` run method and skill actions over an `AsyncUaServerConnection`.
- `robot_session.py`, `server_session.py`: dataclass state holders for a robot and a server, with `to_info()` for the wire models.
- `surface_job.py`: `SurfaceJob` holds one upload (`PcdAssembly`) and its `SurfaceProcessingConfig`.
- Details: `webskillcomposition/docs/data-and-interface-model.md` (docs/data-and-interface-model.md) (action layer, robot session shape).

### `backend/src/backend/opcua/`

- `server_connection.py`: `AsyncUaServerConnection` wraps the asyncua `Client`. It handles connect, disconnect, discovery, and the data-change and event subscriptions.
- `asyncua_discovery.py`: browses the OPC UA address space. It finds motion devices, skills, methods, and node details.
- `discovery.py`: transport-free builders. `build_robot_session_infos` and the `build_*_action_binding` functions turn discovery results into robot models.
- `method_calls.py`: converts JSON inputs to OPC UA argument types (`coerce_*`, `read_method_arguments`). Provides `call_robot_method`, `call_raw_method`, and `to_jsonable`.
- Details: `webskillcomposition/docs/data-and-interface-model.md` (docs/data-and-interface-model.md) (raw OPC UA capability layer).

### `backend/src/backend/websocket/`

- `router.py`: `websocket_endpoint` for `/ws`. Parses each text frame with `parse_client_message_json`, calls `handle_client_message`, and sends the resulting events.
- `client_hub.py`: `ClientHub` tracks the pages connected now. `LiveEmitter` sends live values from long-running subscriptions to the current pages, not to the page that started them.
- `surface_router.py`: `websocket_surface_endpoint` for `/ws/surface`. Handles begin and finish upload, abort, and job processing, then streams results.

### `backend/src/backend/models/`

- Pydantic contracts. `base.py` defines `ContractModel` with camelCase aliases (`to_camel`).
- `messages.py`: `ClientMessage` and `ServerMessage` unions plus `parse_client_message_json` and `parse_server_message_json`.
- `robot.py`, `server.py`, `opcua.py`, `address_space.py`: robot, server, binding, and address-space models.
- `surface.py`: surface commands, events, and `SurfaceProcessingConfig`.
- `__init__.py` re-exports the public names.

### `backend/src/backend/geometry/`

- `pointcloud_transport.py`: PCD2 chunk encoding and decoding (`encode_pcd2_chunk`, `decode_pcd2_chunk`, `iter_encoded_pcd2_chunks`, `quantize_points_to_pcd2_chunks`). `PcdAssembly` collects chunks into a point cloud.
- `surface_reconstruction.py`: voxel field from points, flood fill of outside air, shell extraction, and `compute_surface_points_from_xyz`. Needs SciPy at runtime (`_require_scipy`).

### `backend/src/backend/robot_log.py`

- `setup_robot_log()` writes `backend/data/logs/robot.log` with rotation (5 MiB, 4 backups). Only the `robot_log` logger and asyncua warnings and errors go there.
- `describe_inputs()` formats method inputs for log lines.
- Called from `main()` only, not from `create_app`, so tests do not write the log.

### `backend/catalog/`

- `module_catalog.json`: the cell's module catalog, tracked in git. The frontend owns its content. The backend validates and stores it through `ModuleCatalogStore`.
- Not a Python package. Its contents are not described here.
- `backend/data/` (not tracked, listed in `backend/.gitignore`) holds the runtime robot log and a second `module_catalog.json`. The default code path uses `backend/catalog/` unless `WSC_DATA_DIR` is set.

### `backend/tests/`

- `test_application_service.py`: `application_service` command handling, skill-state mapping, and discovery registration.
- `test_asyncua_discovery.py`: value-type and skill-owned-method filtering in asyncua discovery.
- `test_client_hub.py`: `ClientHub` delivery of events.
- `test_messages.py`: message and surface-message parsing and camelCase JSON.
- `test_method_calls.py`: argument validation and coercion for OPC UA methods.
- `test_module_catalog_api.py`: `/api/module-catalog` (save, read, revision conflict, file location).
- `test_robot_log.py`: `describe_inputs` and `setup_robot_log`.
- `test_robot_models.py`: robot IDs, robot session binding, and action normalization.
- `test_runtime_registry.py`: `RuntimeRegistry` indexing of robots and surface jobs.
- `test_server_connection.py`: `AsyncUaServerConnection` with the asyncua layer.
- `test_surface_websocket_router.py`: `/ws/surface` upload and processing end to end.
- `test_websocket_router.py`: `/ws` error handling, discovery, and the demo-server snapshot.

