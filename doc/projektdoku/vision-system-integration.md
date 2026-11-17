# Integration of two OPC UA vision systems (OPC 40100) into WebSkillComposition

## Context

The existing system **WebSkillComposition** controls industrial robots (Franka Research 3,
EVA Automata, UR5e) skill-based via an OPC UA robotics server (OPC 40010). Two
**self-built OPC UA vision servers** following **OPC 40100 (Machine Vision)** are to be
added. They recognise modules and deliver a **6D pose plus module ID** per module (other
result types remain possible).

Target picture:

1. The vision systems run **lean and in the background**.
2. The **backend orchestrates**: it is an OPC UA client of both worlds, listens to the
   vision results, and derives motion commands for the robot controller from them. The
   robot controller itself is **not** changed.
3. Later, the frontend shows **only the status** — and, as soon as pose and module ID are
   available, loads the **CAD data of the modules** and positions them in the 3D viewport
   based on the pose.

Guiding principle: **minimal intervention.** Not "as few lines as possible at any cost",
but **zero behaviour change on existing code paths**. Everything new goes into new files.
ADP_flexible_Roboterzelle
---

# Part 1 — How the existing system works

## 1.1 Rough architecture

```
Browser (React/Three.js)          Python backend (FastAPI)         OPC UA server
┌───────────────────────┐  WS   ┌────────────────────────┐  OPC UA  ┌──────────────┐
│ Viewport (URDF, IK/FK)│◄─────►│ AsyncUaServerConnection│◄────────►│ Robotics-    │
│ ServerManager panel   │ /ws   │ (1 client per URL)     │          │ Server 40010 │
│ AddressSpace browser  │       │ handle_client_message  │          └──────────────┘
└───────────────────────┘       │ RuntimeRegistry        │
            ▲                   └────────────────────────┘
            │ /ws/surface (point clouds, chunked, own protocol)
```

- **One** WebSocket (`ws://127.0.0.1:8000/ws`) for all servers and robots. Every message
  carries `serverUrl` or `robotId` for assignment.
- A **second, separate channel** `/ws/surface` with its own protocol already exists for
  large binary data (point clouds → surface reconstruction). It is a precedent, should vision
  ever need to stream image data.
- Communication is **contract-first**: Pydantic models in
  `backend/src/backend/models/messages.py`, a hand-maintained TS mirror in
  `frontend/src/shared/api/messages.ts`. `ContractModel` (`models/base.py`) generates
  camelCase aliases and sets `extra="forbid"` — **unknown fields are rejected hard**, so
  frontend and backend must always be changed together.

## 1.2 Backend: four layers

| Layer | Location | Task |
| --- | --- | --- |
| Transport | `websocket/router.py:27` | `/ws` loop, JSON→Pydantic; live events are buffered while commands are running (`defer_live_events`) |
| Dispatch | `runtime/application_service.py:238` `handle_client_message` | 785 lines of sequential `if isinstance(message, XCommand)` blocks, each returns `list[ServerMessage]` |
| OPC UA client | `opcua/server_connection.py:124` `AsyncUaServerConnection` | exactly **one** asyncua connection per server URL, lazy connect, **anonymous** (no security configured) |
| Discovery | `opcua/asyncua_discovery.py` (804 lines) + `opcua/discovery.py` (112 lines) | scan the address space → raw bindings → normalised actions |

**State**: `services/runtime_registry.py` `RuntimeRegistry` with `_servers_by_url`,
`_robots_by_id`, `_discovery_cache_by_url` (discovery results are cached).

**Test seam**: `DEFAULT_CONNECTION_FACTORY` (`application_service.py:63`) — tests inject
`connection_factory=FakeConnection`.

## 1.3 Robot discovery: what is hard-wired today

`opcua/asyncua_discovery.py:24-29`:

```python
ROBOTICS_NAMESPACE_URI = "http://opcfoundation.org/UA/Robotics/"
DEVICE_INTEGRATION_NAMESPACE_URI = "http://opcfoundation.org/UA/DI/"
MOTION_DEVICE_TYPE_IDENTIFIER = 1004      # MotionDeviceType
AXIS_TYPE_IDENTIFIER = 16601              # AxisType
MOTION_DEVICE_NUMERIC_ID_RANGE = range(5010, 5020)   # node-ID probe as a shortcut
```

Flow of `discover_connected_server` (`:740`):

1. `read_namespace_uris` → read the namespace array, resolve the index by URI
   (`typed_node_id` builds `ns=<idx>;i=<id>`).
2. `find_device_set` (`:162`) → `Objects/DeviceSet` (DI), with a name fallback.
3. `find_motion_devices_container` (`:184`) → `DeviceSet/MotionDeviceSystem/MotionDevices`.
4. `discover_motion_device_nodes` (`:191`) → three stages: node-ID probe (5010–5019) → direct
   children → BFS over the DeviceSet subtree. Matching is always by **exact string
   comparison** of the TypeDefinition — **not subtype-aware**.
5. Per MotionDevice: variables, axes, methods, skills; additionally global scans starting at
   `Objects` (max_depth=4, then unbounded); local overrides global (`merge_bindings`).

## 1.4 Two capability levels: `opcua` (raw) and `actions` (normalised)

**Raw** (`models/robot.py:33` `RobotOpcUaInterface`): `variables`, `methods`, `skills`, `axes`
— snake_case-normalised via `normalize_capability_name` (`:337`, `GoTo` → `go_to`).

**Skill detection is duck-typed** (`discover_skill_bindings:460`):

> An object counts as a skill if it has **at least one** of
> `ParameterSet` / `ResultSet` / `CurrentState` **and at least one** of
> `Start` / `Halt` / `Reset` / `Suspend` / `Resume` as a child.
> `ProgramStateMachineType` is **not** checked.

**Normalised** (`opcua/discovery.py:50` `build_robot_action_bindings`) — hard-coded table:

```python
add_skill_action("goto", "go_to");   add_skill_action("home", "home")
add_skill_action("linMoveTcp", "lin_move_tcp");  add_skill_action("rotMoveTcp", "rot_move_tcp")
add_method_action("createSession", "create_new_session");  ...
```

## 1.5 Skill execution

`runtime/action_execution.py:150` `_execute_skill_action`:

1. Normalise inputs, validate against `skill.parameters`.
2. For each parameter, `write_node_value(node_id, value, coerce_to_existing=True)` — reads
   the current value and adjusts the type (avoids asyncua variant mismatches).
3. `call_raw_method(start_node_id, {"args": []})`.
4. Read `CurrentState`, report `status="running"`.

Afterwards, `_watch_skill_action_state` in `application_service.py:161` polls the
`CurrentState` **every 250 ms** and sends a `robotActionState` event on change.

## 1.6 What already works generically for **every** OPC UA server

This is the lever for minimal intervention:

| Capability | Command | Connection method |
| --- | --- | --- |
| Browse address space | `browseAddressSpaceRoot/Children/References/NodeDetails` | `browse_address_space_*` |
| Subscribe to variable | `subscribeNode` / `unsubscribeNode` | `subscribe_node:210` |
| **Subscribe to events** | `subscribeEvent` / `unsubscribeEvent` | `subscribe_events:234` |
| Call any method | `callRawMethod` | `call_raw_method` + full DataType coercion |
| Read/write nodes | (internal, no message type) | `read_node_value` / `write_node_value` |

**Consequence:** an OPC 40100 server could **already be operated today without a single
backend change** — only untyped, via the address-space browser.

## 1.7 Frontend

Feature-Sliced Design with four layers (`app/`, `entities/`, `features/`, `shared/`), Vite
(rolldown) + React 19 + TS strict + antd 6 + Tailwind 4 + three.js 0.184 /
@react-three/fiber / drei 10.7 + urdf-loader + closed-chain-ik.

- **No zustand/redux.** Pure reducers in `entities/*/model/store.ts`
  (`applyRobotMessage`, `applyServerMessage`), and one central
  `app/model/applicationController.ts` (1138 lines) as the only mutable owner, which calls
  `emitState()` after every message; exactly **one** `useState` in `App.tsx:33`. Contexts are
  pure facades.
- **UI attachment point**: `features/opcua-server/components/ServerManager.tsx:59` renders
  `<RobotManager serverUrl={...} embedded />` **inside** the server card.
- **Viewport** `features/viewport/components/Viewport.tsx` (1358 lines): `:1225` filters
  `robots.filter(r => r.visual.urdfUrl)`, `:1345` maps to `<ViewportRobot>`. Static assets live
  under `frontend/public/`, are copied by Vite to `dist/`, and are served in the Docker image
  via `StaticFiles(directory="./www")` (`app.py:19`).
- **Tests**: vitest with `environment: 'node'` — only logic is tested, no components.

## 1.8 Known weak spots in the existing code

- Type checking in discovery is exact string comparison, not subtype-aware.
- `handle_client_message` is a 785-line `isinstance` chain without a dispatch table.
- No mock/simulation OPC UA server in the repo.
- No security/authentication in the OPC UA client.
- Skill states are polled rather than subscribed via OPC UA events.
- **No reconnect/watchdog** — asyncua subscriptions die silently when the connection drops.
- Discovery cache is never invalidated (`runtime_registry.py`, `application_service.py:221`).

---

# Part 2 — OPC 40100 (Machine Vision) overview

Namespace: `http://opcfoundation.org/UA/MachineVision`

```
VisionSystemType  (subtype of BaseObjectType — NOT of DI DeviceType!)
├── VisionStateMachine        VisionStateMachineType            [Mandatory]
│   ├── States: Preoperational · Halted · Error · Operational
│   ├── Methods: Reset · Halt · SelectModeAutomatic · ConfirmAll
│   └── AutomaticModeStateMachine   VisionAutomaticModeStateMachineType   [Mandatory]
│       ├── States: Initialized(5) · Ready(6) · SingleExecution(7) · ContinuousExecution(8)
│       └── Methods:
│           PrepareRecipe / PrepareProduct         Initialized → Ready
│           StartSingleJob(measId, partId, recipeId, productId, parameters[])
│                                       → (jobId, error)      Ready → SingleExecution
│           StartContinuous(...)                   Ready → ContinuousExecution
│           Stop(cause, causeDescription) → error
│           Abort(cause, causeDescription) → error
│           SimulationMode(activate, cause, causeDescription) → error
├── ConfigurationManagement   ConfigurationManagementType       [Optional]
├── RecipeManagement          RecipeManagementType              [Optional]
├── ResultManagement          ResultManagementType              [Optional]
│   └── GetResultById · GetResultComponentsById · GetResultListFiltered · ReleaseResultHandle
├── SafetyStateManagement     SafetyStateManagementType         [Optional]
├── DiagnosticLevel · SystemState                               [Optional]
```

**Events**: `RecipePreparedEventType`, `JobStartedEventType`, `ReadyEventType`,
**`ResultReadyEventType`**, `AcquisitionDoneEventType`.

**`ResultDataType`**: `resultId`, `hasTransferableDataOnFile`, `isPartial`, `isSimulated`,
`resultState`, `measId`, `partId`, `externalRecipeId`, `internalRecipeId`, `productId`,
`externalConfigurationId`, `internalConfigurationId`, `jobId`, `creationTime`,
`processingTimes`, **`resultContent: BaseDataType[]`** ← the 6D pose + module ID end up here.

**Important, verified:** `Opc.Ua.MachineVision.NodeSet2.xml` declares as `RequiredModel`
**only Core UA 1.04** — no DI, no Machinery, no Robotics. Import exactly **one** file.

Sources: [OPC 40100-1](https://reference.opcfoundation.org/specs/OPC-40100-1) ·
[7.1 VisionSystemType](https://reference.opcfoundation.org/MachineVision/v100/docs/7.1) ·
[8.2](https://reference.opcfoundation.org/MachineVision/v100/docs/8.2) ·
[8.3](https://reference.opcfoundation.org/MachineVision/v100/docs/8.3) ·
[12.17 ResultDataType](https://reference.opcfoundation.org/MachineVision/v100/docs/12.17) ·
[UA-Nodeset MachineVision](https://github.com/OPCFoundation/UA-Nodeset/tree/latest/MachineVision)

---

# Part 3 — Blockers that shape the plan

Four findings from the code analysis. They are the reason the implementation is staged.

### B1 — A vision server today is guaranteed to return `robots=[]`

`asyncua_discovery.py:667` aborts if `find_device_set` returns `None`; `:203` aborts if the
Robotics namespace is missing. A pure vision server therefore appears as a **connected server
with zero devices**. The duck-typed skill scan over `Objects` (`:681-691`) does run, but its
result is discarded, because it is only consumed in the (empty) MotionDevice loop.

### B2 — `subscribe_events` does not filter by event type

`server_connection.py:242` calls `subscription.subscribe_events(node)`. asyncua then defaults
to `BaseEventType` and only includes its fields in the SelectClauses (EventId, EventType,
SourceNode, Time, Message, Severity). The custom fields of a `ResultReadyEventType` — esp.
`Result` resp. `ResultContent` — **never arrive**. Symptom: "works, but all poses are empty".

### B3 — `to_jsonable(event)` destroys the event object

`EventSubscriptionHandler.event_notification` (`:120`) calls `to_jsonable(event)`
(`method_calls.py:369`). An asyncua `Event` has neither `.to_string()` nor `.Value` → the last
line `return str(value)` takes effect. The `opcuaEvent` payload in the frontend is today **a
single Python `repr` string**. This also affects the existing event browser.

### B4 — `goto` is joint space, the IK sits in the frontend

`goto` expects `{mode, joints[], max_speed, time, tcp_config, avoidance_zones}`.
`applicationController.ts:588` `callRobotGoto` validates a joint-angle array;
`callRobotGotoForVisualAngles` (`:610`) maps visual angles → axis values. The IK itself lies in
`features/viewport/model/robotIk.ts` (231 lines, `closed-chain-ik`) and works on the **loaded
URDF object in the Three.js scene** — it cannot be ported to the backend without bringing URDF
parsing and a new kinematics dependency.

`linMoveTcp` / `rotMoveTcp` are mapped in `discovery.py:99-100`, but are **nowhere used** in
the frontend. Whether the real 40010 server exposes them is **open** → see 6.3.

### B0 — An existing test is red

`action_execution.py:184` calls `write_node_value(..., coerce_to_existing=True)`; the test
double `tests/test_application_service.py:263` does not know the parameter:

```python
async def write_node_value(self, node_id: str, value: object) -> None:
```

→ `TypeError` → caught in `action_execution.py:189` → `RobotActionExecutionError` →
`application_service.py:854` returns an `error` event → 
`test_execute_robot_action_dispatches_skill_and_emits_runtime_state` (`:689`) expects
`methodResult`. **Must be fixed as Phase 0**, otherwise there is no green baseline. The fix is
one line:

```python
async def write_node_value(
    self, node_id: str, value: object, *, coerce_to_existing: bool = False
) -> None:
```

> Note: `uv run pytest` was not runnable in this environment (no `.venv`, no network). The
> finding is derived statically and should be run once before implementation starts.

---

# Part 4 — What our vision servers should look like

## 4.1 Repo layout

Own folder in the monorepo, **own uv project** — so that `backend/pyproject.toml` stays
unchanged and the nodeset XML does not end up in the backend wheel. Not in
`backend/src/backend/`, because that package has the client role; the separation documented
in `docs/architecture.md` is thereby preserved.

```
vision-server/
  pyproject.toml                 # name = "wsc-vision-server"
  Dockerfile
  nodesets/
    Opc.Ua.MachineVision.NodeSet2.xml     # vendored, document version + date in the header
  src/vision_server/
    __main__.py            # argparse: --profile {2d,3d} --port --app-uri --name --scene
    config.py              # VisionServerConfig (frozen dataclass)
    address_space.py       # import_xml + VisionSystem instance from VisionSystemType
    state_machine.py       # VisionStateMachine + AutomaticModeStateMachine
    result_management.py   # result storage, GetResultById, ResultReadyEvent
    events.py              # event generator factories
    payload.py             # Detection -> JSON
    detection/
      base.py              # DetectionSource (ABC) -> list[Detection]
      simulated_2d.py      # Server 1
      simulated_3d.py      # Server 2
  scenes/
    scene_2d.json          # ground truth: which modules are where
    scene_3d.json
  tests/
```

## 4.2 Address-space structure with asyncua

Order is critical:

```python
server = Server()
await server.init()                                     # 1. core address space
server.set_endpoint("opc.tcp://0.0.0.0:4841/wsc/vision/2d")
await server.set_application_uri("urn:wsc:vision:2d")   # 2. BEFORE the import: fixes ns=1
server.set_server_name("WSC Vision 2D")

await server.import_xml("nodesets/Opc.Ua.MachineVision.NodeSet2.xml")   # 3. -> ns=2
mv  = await server.get_namespace_index("http://opcfoundation.org/UA/MachineVision")
wsc = await server.register_namespace("http://odintec.de/UA/WSC/Vision")  # 4. own ns

vision_system = await server.nodes.objects.add_object(
    ua.NodeId("VisionSystem2D", wsc),
    ua.QualifiedName("VisionSystem2D", wsc),
    objecttype=ua.NodeId(VISION_SYSTEM_TYPE_ID, mv),
)
```

**A trick that saves the frontend a lot of work:** also make all events visible on the
**server object** (`i=2253`, identical in every OPC UA server):

```python
await server.nodes.server.add_reference(vision_system, ua.ObjectIds.HasNotifier, forward=True)
await vision_system.set_event_notifier([ua.EventNotifier.SubscribeToEvents])
```

> **Correction (verified):** This trick does **not** work with `asyncua`. Server-side, asyncua
> matches events on delivery strictly by the exact `emitting_node`
> (`monitored_item_service.py: trigger_event`); the `HasNotifier` hierarchy is not traversed.
> A subscription to `i=2253` receives **nothing**. The client must subscribe directly on the
> `VisionSystem` node. The `HasNotifier` reference remains set anyway (spec-compliant, makes
> the system discoverable from the server object). Instead of discovery, the implementation
> gives the `VisionSystem` a **string NodeId** (`ns=<vision>;s=VisionMachine`), so that all
> child nodes also get meaningful, stable NodeIds.

For the state machines there is `asyncua/common/statemachine.py` (`FiniteStateMachine` with
`install()`, `add_state()`, `add_transition()`, `change_state()`); `_state_machine_type` can
be redirected to the 40100 types. Every `change_state()` automatically fires a transition
event.

### Pitfalls

| # | Problem | Countermeasure |
| --- | --- | --- |
| 1 | asyncua issue [#651](https://github.com/FreeOpcUa/opcua-asyncio/issues/651): `TypeError: object of type 'ExtObj' has no len()` on the MachineVision import | Fixed in current asyncua. **Still verify first, in isolation** (spike, see Phase 1). Fallback `strict_mode=False`. |
| 2 | asyncua issue [#1693](https://github.com/FreeOpcUa/opcua-asyncio/issues/1693) (open): `load_data_type_definitions()` fails on 40100 due to abstract structure base types | Affects the **client** side. Our backend calls this nowhere today — **leave it so**. The JSON payload (4.3) makes it unnecessary. |
| 3 | Namespace index drift | **Never** hard-code `ns=2;i=…`, always `ua.NodeId(id, mv_idx)`. |
| 4 | `ResultContent` is `BaseDataType[]` | **Corrected (verified):** the node comes from the nodeset with a **null NodeId as DataType**, so *every* write fails with `BadTypeMismatch` — even `[ua.Variant(payload, String)]`. First overwrite the DataType attribute to `String`, then write as an **array**: `write_value(ua.Variant([payload], ua.VariantType.String))`. This keeps the `ResultContent[0]` semantics intact. |
| 4b | Event fields arrive as `None` at the client | asyncua derives field types from the nodeset and here gets VariantType `Null`. Assign each field as a finished `ua.Variant(value, type)`, not as a raw Python value. |
| 4c | `@uamethod` handler cannot start a task | Synchronous handlers run in asyncua in a ThreadPoolExecutor without an event loop (`RuntimeError: no running event loop`). Make handlers `async def`; the check/admission logic stays synchronous so that it is atomic. |
| 4d | Method return arrives nested | `_format_call_outputs` only handles **tuples** element by element; a `list` is wrapped as *one* Variant. Always `return (Variant(...), Variant(...))`. |
| 5 | Optional fields / EncodingMask | Fill all mandatory fields (`ResultId`, `IsPartial`, `ResultState`, `InternalRecipeId`, `InternalConfigurationId`, `JobId`, `CreationTime`). |
| 6 | `import_xml` must run after `init()` and before `start()` | Keep the order above. |

## 4.3 Result payload: JSON string in `ResultContent[0]`

**Decision — deliberately against a custom Structured DataType.**

| Option | Spec | Client robustness | Effort |
| --- | --- | --- | --- |
| Custom Structured DataType | ★★★ | ✗ backend would need `load_data_type_definitions()` → issue #1693; without it, a raw `ExtensionObject` with bytes arrives, and `to_jsonable` turns it into `str(...)` | high |
| **JSON string** ★ | ★★ (`BaseDataType[]` explicitly allows String) | ★★★ — `to_jsonable` passes strings through unchanged | minimal |
| `Double[]` | ★★ | ★★★ | ✗ module ID is a string, no attributes, no confidence |

`ResultContent` is an **array**. We occupy `[0]` with the JSON string; later `[1]` can carry a
real Structured DataType, **without** touching the frontend chain. Backward-compatible
expansion path.

> **Addition (verified):** `ResultReadyEventType` (i=1024) carries **no** `Result:
> ResultDataType`, but 15 flat fields — including `ResultContent` itself. The JSON string
> therefore travels **inside the event**; a second read or a method call is not needed.
> Only `ResultContent`, `CreationTime`, `IsPartial`, `IsSimulated` and `ResultState` are
> decodable; `ResultId`/`JobId`/`InternalRecipeId`/`InternalConfigurationId` are structures
> and are sent empty — their values are in the payload (`resultId`, `jobId`). The correlation
> result→job runs via `payload["jobId"]`.

**Schema** (`vision-server/src/vision_server/payload.py`, mirrored in
`frontend/src/features/vision/model/visionResultEvent.ts`):

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-2d-01",
  "resultId": "res-000123",
  "jobId": "job-42",
  "creationTime": "2026-09-09T10:11:12.345Z",
  "frameId": "world",              // reference frame of the poses
  "lengthUnit": "m",               // always SI metres
  "angleUnit": "rad",
  "rotation": "quaternion_xyzw",   // three.js order, saves a conversion
  "detections": [
    {
      "moduleId": "MOD-A-042",     // -> key into the CAD registry
      "instanceId": "det-1",       // stable across frames -> React key
      "confidence": 0.93,
      "position": [0.42, -0.11, 0.05],
      "orientation": [0.0, 0.0, 0.3827, 0.9239],
      "boundingBox": null,         // server 1 only
      "attributes": {}             // freely extensible
    }
  ]
}
```

Rules that save trouble: **always SI metres and radians**, **always explicit `frameId`**,
**quaternion `xyzw`**, **`schema` version in the payload** (the frontend can discard old
frames instead of crashing).

## 4.4 Job flow

```python
@uamethod
async def start_single_job(self, parent, meas_id, part_id, recipe_id, product_id, parameters):
    self._job_counter += 1
    job_id = ua.Variant(f"job-{self._job_counter}", ua.VariantType.String)
    asyncio.create_task(self._run_job(job_id, meas_id, part_id, recipe_id, product_id, parameters))
    return [job_id, ua.Variant(0, ua.VariantType.Int32)]

async def _run_job(self, job_id, meas_id, part_id, recipe_id, product_id, parameters) -> None:
    t0 = time.perf_counter()
    await self.state.to_single_execution()                  # Ready -> SingleExecution
    await self.ev_job_started.trigger(message=f"Job {job_id} started")

    detections = await self.source.acquire_and_detect(parameters)   # 2D or 3D
    t_acq = time.perf_counter()
    await self.ev_acquisition.trigger(message="Acquisition done")

    result = ua.ResultDataType(
        ResultId=make_result_id(), IsPartial=False,
        IsSimulated=self.config.simulation_mode, ResultState=0,
        MeasId=meas_id, PartId=part_id,
        InternalRecipeId=recipe_id, InternalConfigurationId=self.config.configuration_id,
        ProductId=product_id, JobId=job_id,
        CreationTime=datetime.now(timezone.utc),
        ProcessingTimes=ua.ProcessingTimesDataType(
            AcquisitionDuration=(t_acq - t0) * 1000.0,
            ProcessingDuration=(time.perf_counter() - t_acq) * 1000.0),
        ResultContent=[ua.Variant(build_detection_payload(...), ua.VariantType.String)],
    )
    await self.results.store(result)
    await self.ev_result_ready.trigger(message=f"Result {result.ResultId} ready")

    await self.state.to_ready()                             # SingleExecution -> Ready
    await self.ev_ready.trigger(message="Ready")
```

> **Correction (verified):** The state sequence via `Halted` is not conformant — the nodeset
> knows **no** transition `Halted → Operational`. The correct sequence is
> `Preoperational --PreoperationalToOperationalAuto--> Operational`, and inside
> `Initialized --InitializedToReadyAuto--> Ready`. Likewise, the `ua.ResultDataType(...)` sketch
> above presupposes generated Python classes, which do not exist without
> `load_data_type_definitions()` (issue #1693) — the implementation instead writes the result
> fields node by node. The `Error` state is **not** a fixed type node (`i=5030`) but a
> mandatory child of the instance; only the states of the `AutomaticModeStateMachine`
> (i=5056–5059) come via fixed NodeIds. And `change_state()` must **not** receive a
> `Transition` object: the optional `LastTransition` node does not exist on the instance.

Visible state sequence: `Preoperational → Operational/Initialized → Ready → SingleExecution →
Ready`.

## 4.5 Server 1 vs. server 2

Shared base class `VisionSystemServer` (nodeset import, instantiation, both automata,
ResultManagement, events, job loop) — **it knows no image processing.** Variation happens via
a `DetectionSource` strategy and capability flags in the config object, **not** via server
subclasses.

```python
@dataclass(frozen=True)
class Detection:
    module_id: str
    instance_id: str
    position: tuple[float, float, float]              # metres, frameId frame
    orientation: tuple[float, float, float, float]    # quaternion xyzw
    confidence: float
    attributes: dict[str, Any]

class DetectionSource(ABC):
    profile_id: str
    @abstractmethod
    async def acquire_and_detect(self, parameters: list[Any]) -> list[Detection]: ...
```

| | Server 1 — 2D detection | Server 2 — 3D pose estimation |
| --- | --- | --- |
| Role | module identification + position in the support plane | full 6D pose estimation |
| Pose DOF | x, y, yaw; z constant, roll=pitch=0 | full 6 DOF |
| Additional fields | `boundingBox`, `classScore` | `fitError`, `inlierCount`, `pointCount` |
| Operating modes | only `StartSingleJob` | additionally `StartContinuous`/`Stop`/`Abort` |
| Partial results | no | yes (`IsPartial=True`, then refinement) |
| Simulated error | noise on x/y/yaw, occasional misclassification | noise on 6 DOF, 180° symmetry confusion |
| Latency | ~50 ms | ~300 ms |

Both read the same `scenes/scene_*.json` (ground truth) and add profile-specific noise —
a reproducible demo, trivial verification of the CAD overlay.

## 4.6 Instance distinction and operation

| Level | Server 1 | Server 2 |
| --- | --- | --- |
| Endpoint | `opc.tcp://…:4841/wsc/vision/2d` | `opc.tcp://…:4842/wsc/vision/3d` |
| ApplicationURI | `urn:wsc:vision:2d` | `urn:wsc:vision:3d` |
| ServerName | `WSC Vision 2D` | `WSC Vision 3D` |
| BrowseName | `<wsc>:VisionSystem2D` | `<wsc>:VisionSystem3D` |
| `visionSystemId` in the JSON | `vision-2d-01` | `vision-3d-01` |

`docker-compose.yaml` — additive; the `platform` service remains unchanged:

```yaml
  vision-2d:
    build: { context: ., dockerfile: vision-server/Dockerfile }
    command: ["--profile","2d","--port","4841","--app-uri","urn:wsc:vision:2d","--name","WSC Vision 2D"]
    ports: ["4841:4841"]
  vision-3d:
    build: { context: ., dockerfile: vision-server/Dockerfile }
    command: ["--profile","3d","--port","4842","--app-uri","urn:wsc:vision:3d","--name","WSC Vision 3D"]
    ports: ["4842:4842"]
```

Two instances from the start — so that a mix-up of a vision ID with a server URL is noticed
immediately.

> **Deviation (this repo):** Here **one** server runs (port 4840, endpoint `/raspi/server/`),
> into which the vision system is built via `install_vision_machine(server, config)`. Two
> server processes on one Pi load the same 40100 address space twice (measured ~110 MB RSS per
> process) and force the backend into two sessions and two subscriptions — for one camera in
> one cell, that is effort without benefit. A second profile would come as a second
> `VisionSystemType` instance in the same server, distinguished via instance names and
> `visionSystemId`. The Docker Compose variant above still applies to the WSC monorepo with
> simulated servers.

---

# Part 5 — What is changed in the existing system

Staged. **Stage 1** makes vision visible and costs almost nothing. **Stage 2** brings the
orchestration and is the actual intervention.

## 5.1 Stage 1 — Visibility (backend: 19 lines, contract diff: none)

Only blockers B2 and B3 have to go. Both fixes are **purely additive**.

**B3 — `opcua/method_calls.py`, new function next to `to_jsonable` (~18 lines):**

```python
def event_to_jsonable(event: Any) -> Any:
    fields = getattr(event, "get_event_props_as_fields_dict", None)
    if not callable(fields):
        return to_jsonable(event)
    return {
        str(name): to_jsonable(getattr(variant, "Value", variant))
        for name, variant in fields().items()
    }
```

`to_jsonable` itself stays **untouched** — otherwise the payload format of
`nodeValueChanged`, `methodResult` and `addressSpaceNodeDetails.value` would change.

**`opcua/server_connection.py:113` — one line:**

```python
await self.on_event(event_to_jsonable(event))
```

**B2 — `opcua/server_connection.py:234`, keyword-only with default (~8 lines):**

```python
async def subscribe_events(
    self, *, node_id: str, on_event: EventCallback,
    event_type_node_ids: list[str] | None = None,     # NEW
    publishing_interval_ms: float = 100.0,
) -> None:
    ...
    evtypes = [self.client.get_node(i) for i in event_type_node_ids] if event_type_node_ids else None
    handle = await subscription.subscribe_events(node, evtypes) if evtypes \
             else await subscription.subscribe_events(node)
```

Without this parameter, asyncua selects only `BaseEventType` fields and `ResultContent` is
missing. Since the parameter is keyword-only with a default, `SubscribeEventCommand` and **all
existing tests remain unchanged**.

> The two fixes depend on each other: B2 ensures that the field is transmitted at all, B3 that
> it does not decay into a string on the way to the frontend. Either one alone is not enough.

`OpcUaEventNotificationEvent.event` is already `Any` (backend) resp. `unknown` (frontend) —
**the contract does not need to be extended.**

## 5.2 Stage 1 — Frontend (3 new files, 9 changed lines)

**New**, all pure logic or isolated components:

| File | Content |
| --- | --- |
| `features/vision/model/visionResultEvent.ts` | `parseVisionResultEvent`, `selectLatestVisionFrames`, `selectVisionStatus` — defensive, `null` on anything unexpected |
| `features/vision/model/poseTransform.ts` | Z-up → Y-up (see 7.2) |
| `features/vision/model/moduleModels.ts` | module ID → CAD asset registry |
| `features/vision/components/VisionStatusCard.tsx` | status badge, subscribes to `i=2253` |
| `features/viewport/components/DetectedModules.tsx` | CAD overlay |

**Changed:**

| File | Change | Lines |
| --- | --- | --- |
| `features/opcua-server/components/ServerManager.tsx:59` | `<VisionStatusCard serverUrl={server.serverUrl} />` after `<RobotManager …/>` | +1 |
| `features/viewport/components/Viewport.tsx` | import (:48), `detections?` in `ViewportProps` (:50), `<DetectedModules …/>` after the robot map block (:1354) | +6 |
| `app/layout/DesktopLayout.tsx:98` | `detections={visionDetections}` to `<Viewport>` | +2 |
| `entities/server/model/store.ts:655` | event cap (see below) | +2 |

`VisionStatusCard` renders `null` if no vision event ever arrived — **robot servers look
unchanged, no `isVisionServer` flag needed.**

**Latent bug that should be fixed along the way:** `entities/server/model/store.ts:655-666`
appends every `opcuaEvent` **without limit** to `state.opcuaEvents`; it is cleared only on
`serverDisconnected`. With server 2 in continuous mode, the array grows without bound.

```ts
const MAX_OPCUA_EVENTS = 500;
opcuaEvents: [...state.opcuaEvents, { … }].slice(-MAX_OPCUA_EVENTS),
```

The existing test `store.test.ts:218-228` checks only one event and stays green.

## 5.3 Stage 2 — Typed discovery and orchestration

Only here does the investment pay off, because the backend must now itself know *which* node
to subscribe to and *which* robot to send the result to.

**Changed (total ≈ 60 lines, all additive):**

| File | Change |
| --- | --- |
| `models/server.py:15` | `is_vision_server: bool = False`, `vision_system_ids: list[str] = []` |
| `opcua/discovery.py:24` | `ServerDiscoveryResult.vision_systems: list[VisionSystemSessionInfo] = []` |
| `opcua/asyncua_discovery.py:740` | call the vision discovery in `discover_connected_server` (**function-local import**, otherwise circular reference) |
| `runtime/server_session.py` | `vision_by_id` + pass-through in `to_info()` |
| `services/runtime_registry.py` | `_vision_by_id`, `get_vision_system`, `replace_server_vision_systems` |
| `runtime/application_service.py:85` | `register_discovery_result` registers vision sessions |
| `runtime/application_service.py:245` | **exactly one** new `isinstance` block |
| `websocket/router.py` | `finally: registry.event_bus.unregister(emit_event)` |
| `pyproject.toml` | `markers = ["integration: …"]`, `addopts = "-m 'not integration'"` |

The trick against the 785-line `if` chain: **not five new blocks**, but one union guard:

```python
if isinstance(message, VisionClientCommand):     # union type, Python 3.10+
    from .vision_service import handle_vision_message
    return await handle_vision_message(message, registry=registry,
                                       connection_factory=connection_factory,
                                       emit_event=emit_event)
```

This leaves `application_service.py` effectively untouched, and the vision commands are
testable in isolation — the same pattern as the already extracted `action_execution.py`.

**New (≈ 900 LOC incl. tests):** `models/vision.py`, `opcua/vision_discovery.py`,
`runtime/vision_session.py`, `runtime/vision_service.py`, `runtime/vision_orchestration.py`,
`geometry/pose_transforms.py`, `services/event_bus.py` + five test modules.

### Detection strategy

The existing type check is exact string comparison (`:213`/`:236`/`:248`) — unsuitable for
`VisionSystemType` (subtype of `BaseObjectType`, not in the DeviceSet). Three stages:

```python
MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"

async def discover_vision_system_nodes(*, client, namespace_uris) -> list[Node]:
    mv_index = namespace_index(namespace_uris, MACHINE_VISION_NAMESPACE_URI)
    if mv_index is None:                                  # (1) gate: free
        return []
    type_node = await find_vision_system_type_node(client, mv_index)   # by BrowseName
    accepted = await collect_type_and_subtype_ids(client, type_node) if type_node else set()

    found = []
    async for node in iter_descendants_limited(client.nodes.objects, max_depth=3):
        type_id = await read_type_definition_id(node)
        if type_id is not None and type_id in accepted:   # (2) subtype-aware via HasSubtype
            found.append(node)
        elif not accepted and await child_by_name(node, "VisionStateMachine") is not None:
            found.append(node)                            # (3) duck-typing fallback
    return found
```

`VisionStateMachine` is **mandatory** per 40100 and thus a reliable marker.
`collect_type_and_subtype_ids` is deliberately generic — this would later also make the
robotics path subtype-aware (not part of this effort).

All existing helpers are reused: `child_by_name`, `read_display_name`, `read_browse_name`,
`read_type_definition_id`, `iter_descendants_limited`, `build_method_binding`,
`discover_named_variable_bindings`, `discover_method_bindings`, `discover_skill_bindings`,
`normalize_capability_name`, `namespace_index`, `merge_bindings`.

> **Rejected:** disguising the vision server as a DI device with `MotionDeviceType` so that the
> existing robot discovery applies. That would violate the spec (40100 deliberately derives
> from `BaseObjectType`), and the frontend would treat the camera as a robot — URDF loading,
> joint runtime, IK panel. The intervention would merely be shifted, not avoided.

> **Optional bonus, not a crutch:** our servers may **additionally** carry a skill object
> `GrabPose` with `ParameterSet`/`ResultSet`/`CurrentState`/`Start`/`Halt`/`Reset`. This does
> not violate 40100 (additional objects are allowed) and matches the VDMA skill pattern this
> backend already speaks — the existing execution machinery would be immediately usable.

### New message types (stage 2)

Commands: `discoverVisionSystems`, `startVisionJob`, `stopVisionJob`, `bindVisionToRobot`,
`unbindVisionFromRobot`. Events: `visionSystemsDiscovered`, `visionResult`, `visionDispatch`.

**Frontend diff required: 0 lines.** `handleServerMessage` (`applicationController.ts:737`) is
an `if` chain without a `default` error path; unknown `type` values fall through and at most
end up in the `MessageLog`.

---

# Part 6 — The signal path from vision to robot controller

## 6.1 Chain

```
Vision server            Backend                                    Robotics server
─────────────            ───────                                    ───────────────
StartSingleJob
  │
  ├─ AcquisitionDoneEvent
  │
  └─ ResultReadyEvent ──► subscribe_events(node, [ResultReadyEventType])
                            │  (B2: event type MUST be passed)
                            ├─ event_to_jsonable        (B3)
                            ├─ ResultContent[0] -> JSON.parse
                            ├─ dedupe / in-flight gate
                            ├─ transform_pose(cam -> robot_base)
                            ├─ VisionResultEvent ──────► frontend (CAD overlay)
                            └─ execute_robot_action(...) ──────────► skill start
                                                                     + state watch
```

Location: **`backend/src/backend/runtime/vision_orchestration.py`** — `runtime/` is already the
layer for "long-lived state + concurrency" (`surface_job.py`, `robot_session.py`), and the
orchestrator is explicitly *not* bound to a WS message.

```python
@dataclass
class VisionRobotLink:
    vision_id: str
    robot_id: str
    action_name: str
    calibration: HandEyeCalibration
    auto_execute: bool                       # Default False
    input_template: dict[str, Any]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    in_flight: bool = False
    seen_result_ids: deque[str] = field(default_factory=lambda: deque(maxlen=256))
    last_creation_time: float = 0.0
```

## 6.2 Hand-eye transformation

`backend/src/backend/geometry/pose_transforms.py` — `numpy` and `scipy` are **already**
dependencies, no new dependency:

```python
def transform_pose(pose, cal, *, grasp_offset=None) -> ObjectPose6D:
    T_cam_obj = pose_to_matrix(pose)
    T_cam_obj[:3, 3] *= cal.translation_scale          # mm -> m
    T = calibration_matrix(cal) @ T_cam_obj            # T_base_obj
    if grasp_offset is not None:
        T = T @ grasp_offset
    q = Rotation.from_matrix(T[:3, :3])
    return ObjectPose6D(frame_id=cal.target_frame,
                        position=T[:3, 3].tolist(),
                        orientation=q.as_quat().tolist(),          # scipy: xyzw
                        euler_xyz_deg=q.as_euler("xyz", degrees=True).tolist())
```

Calibration, in this order of precedence:
1. `bindVisionToRobot.calibration` (runtime, wins) — testable, no deployment
2. JSON file, path from ENV `WSC_VISION_CONFIG` (default `./config/vision.json`)
3. none → `auto_execute` is forced to stay `False`

No config framework to build: the repo today has exactly one `os.getenv("HOST")` (`app.py:24`).
One file plus one ENV variable is the right size.

## 6.3 Open point: how the pose becomes motion (B4)

The backend cannot pass a 6D pose to `goto`. Three options, configurable as `dispatch_mode` —
**which one it will be is still to be decided**:

| Mode | How | Prerequisite | Effort |
| --- | --- | --- | --- |
| **`cartesian`** (preferred) | `execute_robot_action(robot, "linMoveTcp", inputs={…pose…})` | the 40010 server exposes `lin_move_tcp` | **0** — already mapped in `discovery.py:99` |
| `frontendIk` | backend only publishes `visionDispatch` with `pose_base`; the frontend solves IK and calls `callRobotGotoForVisualAngles` | browser must be open → **no autonomous cell operation** | ~40 lines frontend |
| `backendIk` | `ikpy`/`pinocchio` + URDF parsing in the backend | new dependency, kinematics validation | high — rejected for v1 |

**Check before implementation starts:** on the real robotics server, see whether `LinMoveTcp` /
`RotMoveTcp` exist. This works without code — either via the existing address-space browser in
the frontend, or via the `discovered robot … skillNames=` line in the backend log
(`application_service.py:102`).

## 6.4 Race conditions, safety

Five layered mechanisms:

1. **Dedupe by `resultId`** — `deque(maxlen=256)`. asyncua may deliver the same event again on
   re-subscription.
2. **`creationTime` monotonicity** — discard older results (out-of-order after reconnect).
3. **`in_flight` gate + `asyncio.Lock` per link.** Release **not** in the `finally` of the
   dispatch, but only when the state watch reports `idle` — `execute_robot_action` returns for
   skills with `status="running"` (`action_execution.py:212`), the motion is then still running.
   Mirrors exactly `isGotoReadyForDispatch` in the frontend (`applicationController.ts:591`).
4. **`auto_execute=False` as default** — confirmed: stage 2 is delivered as a **dry run**. Only
   the *computed* target value is published; the robot does not move. Arming is an explicit,
   separate step.
5. **Single writer** — only the orchestrator writes to `link.*`.

---

# Part 7 — Frontend: status and CAD overlay

## 7.1 Status: one card, no new entity

| Option | Diff | Verdict |
| --- | --- | --- |
| W1 — purely generic, manually in the address-space browser | 0 lines | Works today already. Valuable as a debug path, too little as a feature. |
| **W2 — status card without new entity** ★ | 1 new file (~130 lines) + 1 line | **Recommended** |
| W3 — own vision entity in the store | ~450 lines across 8 core files, incl. `ApplicationSnapshot` → cascade via `App.tsx:33` and all contexts | Contradicts "minimal intervention" |

Since our servers propagate all events to the server object (4.2), **one** call suffices:

```tsx
useEffect(() => {
  subscribeEvent(serverUrl, "i=2253");
  return () => { unsubscribeEvent(serverUrl, "i=2253"); };
}, [serverUrl, subscribeEvent, unsubscribeEvent]);
```

→ delivers **both** the state machine transition events (status) **and** the
`ResultReadyEvent`s (poses). No browse round trip, no new command.

> Note on the existing code: `VisionStateMachine.CurrentState` is a **variable**, not an event.
> Whoever wants to read the state directly uses `subscribeNode` (push-based, exists) — **not**
> the 250 ms polling from `_watch_skill_action_state`. That is legacy, not a template.

## 7.2 CAD overlay

**Format: glTF 2.0 binary (`.glb`).** `useGLTF` from drei is available (cached per URL,
`<Clone>` for several instances), glTF carries materials — and the scene uses `<Environment>`
with an HDRI (`Viewport.tsx:1310`), where PBR material looks correct and a grey STL block does
not. **No new npm package.** STEP is converted **offline** (FreeCAD headless / Blender), never
at runtime; on export, **metres**, **origin at the module reference point** (not the
bounding-box centre), and **+Z up** are mandatory.

**Location: `frontend/public/cad/modules/<moduleId>.glb`** — deliberately **not** under
`public/urdf/`, which is a Git submodule. Vite copies `public/` to `dist/`, the Dockerfile copies
`dist` to `/app/www`, and `app.py:19` mounts it at `/`. `/cad/modules/x.glb` is thus reachable
in dev **and** Docker without any infrastructure change.

**Registry** analogous to `robotModels.ts:198` `resolveRobotModelFromIdentity` — exact match
first, then fuzzy fallback:

```ts
export interface ModuleModelConfig {
  id: string; label: string; url: string;
  format?: "glb" | "stl";
  scale?: number;                                        // source unit -> m
  modelRotation?: { x: number; y: number; z: number };   // CAD frame -> module frame
  fallbackColor?: string;
}
```

**Coordinate system — the critical point.** The scene is **Y-up** (`Viewport.tsx:1300`
`up: [0,1,0]`, `gridHelper` in XZ), robotics is **Z-up**. The existing code does the conversion
**not centrally**, but per model in two different places:
`robot.visual.origin.roll = -π/2` for FR3 (`robotModels.ts:24-32`, acts in `Viewport.tsx:1168`)
resp. `mountRotation.x = -π/2` for eva/ur5e (`Viewport.tsx:1173`). **Do not orient on this** —
for vision, an explicit, tested central function:

```ts
// features/vision/model/poseTransform.ts
export const Z_UP_TO_Y_UP = new THREE.Quaternion()
  .setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);

export function poseToSceneTransform(d: VisionDetection) {
  const position = new THREE.Vector3(...d.position).applyQuaternion(Z_UP_TO_Y_UP);
  const q = new THREE.Quaternion(...d.orientation);        // xyzw
  return { position, quaternion: Z_UP_TO_Y_UP.clone().multiply(q) };
}
```

Consistent with the eva/ur5e convention, and testable in Vitest with three axis unit vectors —
exactly the pattern of `axisMapping.test.ts`.

**Component** `features/viewport/components/DetectedModules.tsx`. The split
`DetectedModule` / `ModuleMesh` is necessary, because `useGLTF` must not be called
conditionally. A `<PlaceholderBox>` (wireframe) makes unknown module IDs immediately visible
instead of silently swallowing them.

```tsx
export default function DetectedModules({ detections }: { detections: VisionDetection[] }) {
  return (
    <group position={[VISION_ORIGIN.x, VISION_ORIGIN.y, VISION_ORIGIN.z]}
           rotation={[VISION_ORIGIN.roll, VISION_ORIGIN.pitch, VISION_ORIGIN.yaw]}>
      {detections.map((d) => <DetectedModule key={d.instanceId} detection={d} />)}
    </group>
  );
}
```

`Viewport.tsx:1225` (`robots.filter(...)`) stays **untouched** — modules are deliberately *not*
`Robot` instances; pushing them into the robot store would drag `jointRuntime`, IK, `DragControls`
and `JointAnglesPanel` along.

Reference frame: MVP `frameId: "world"` = scene origin, plus **one** configurable constant
`VISION_OVERLAY_ORIGIN` (`{x,y,z,roll,pitch,yaw}`, analogous to `robot.visual.origin`) as an
outer `<group>`, so the sensor can be surveyed once. No TF tree.

## 7.3 Poses to the frontend: piggyback on `opcuaEvent`

| | Piggyback on `opcuaEvent` | New `visionResult` event |
| --- | --- | --- |
| `models/messages.py` | **0** | +1 class, +1 union entry |
| `shared/api/messages.ts` | **0** | +1 variant |
| `websocketClient.ts` | **0** | +1 wrapper |
| `applicationController.ts` | **0** | routing + `ApplicationSnapshot` → cascade via `App.tsx:33` and all contexts |
| `entities/*/store.ts` | **0** | new store + tests |
| `application_service.py` | **0** | +handler block |
| `server_connection.py` | 1 line (B3 fix, due anyway) | +subscription variant |

`OpcUaEventNotificationEvent.event` is already `Any`/`unknown` — **the contract does not need to
be extended.** For stage 1, piggyback therefore; a typed `visionResult` only pays off with
stage 2, when the backend knows vision sessions anyway.

---

# Part 8 — Risks

| # | Risk | Severity | Countermeasure |
| --- | --- | --- | --- |
| R1 | B2 — custom event fields never arrive; symptom "runs, but poses are empty" | **high** | 5.1 + integration test against our own server |
| R2 | B3 — `opcuaEvent` is today a `repr` string | medium | `event_to_jsonable`; do **not** touch `to_jsonable` |
| R3 | B4 — no IK in the backend | **high**, can tip the architecture | 6.3, clarify before implementation starts |
| R4 | B0 — suite is red, no baseline | blocking, trivial | Phase 0 |
| R5 | circular import `asyncua_discovery` ↔ `vision_discovery` | low | function-local import |
| R6 | discovery cache without invalidation — a vision system that appears later is never found | medium | `discoverVisionSystems` with `use_cache=False` |
| R7 | unhandled exception in a background task disappears silently | medium | every path ends in `except → publish(ErrorEvent)` |
| R8 | orchestrator sends to a dead WS connection | medium | `EventBus` + `finally` unregister |
| R9 | **No reconnect/watchdog** — subscriptions die silently on disconnect | **high in operation** | out of scope for v1, **mandatory before cell operation** |
| R10 | backend becomes a motion source without an interlock | **high** | `auto_execute=False`, dry run, explicit arming step |
| R11 | units/conventions: mm↔m, xyzw↔wxyz, intrinsic↔extrinsic, `T_base_cam`↔`T_cam_base` | **high**, classic silent error | `frameId` on every pose, `translation_scale` explicit, golden-value tests |
| R12 | result-handle leak | medium | `try/finally`, test |
| R13 | asyncua issues #651 / #1693 during nodeset import | medium | spike as Phase 1; JSON payload circumvents #1693 |
| R14 | unbounded `opcuaEvents` array in the frontend | medium | event cap (5.2) |
| R15 | three anonymous, unencrypted OPC UA connections | medium | project status quo, document it |

---

# Part 9 — Sequence

| Phase | Content | Done when |
| --- | --- | --- |
| **0** | fix for `FakeConnection.write_node_value`; pytest marker | `uv run pytest` green |
| **1** | **Spike (blocking, ½ day):** vendor the nodeset, 15-line script `init()` → `import_xml()` → `ua.ResultDataType` exists, encode/decode round trip runs | asyncua issues #651/#1693 ruled out |
| **2** | `vision-server/` skeleton: config, address space, VisionSystem instance, `HasNotifier` → server object | browsable in the existing `AddressSpaceTree` |
| **3** | state machines + `StartSingleJob` + ResultManagement + `ResultReadyEvent` with JSON payload; **server 1 done** | `callRawMethod` from the frontend triggers a job |
| **4** | backend B2+B3 fix (19 lines) | events appear **structured** in the `MessageLog` |
| **5** | `features/vision/model/{visionResultEvent,poseTransform,moduleModels}.ts` + Vitest | pure logic green |
| **6** | `VisionStatusCard` + 1 line `ServerManager.tsx` | status badge in the server card |
| **7** | one `.glb` to `public/cad/modules/`, `DetectedModules.tsx`, 6 lines `Viewport.tsx`, 2 lines `DesktopLayout.tsx`, event cap | **module appears at the correct place in the viewport** |
| **8** | server 2 (`simulated_3d`, continuous), docker-compose services | two vision servers in parallel |
| **9** | stage 2: `models/vision.py`, `vision_discovery.py`, registry, `discoverVisionSystems` | typed vision sessions via `/ws` |
| **10** | `pose_transforms.py` + `HandEyeCalibration` + `bindVisionToRobot` in **dry run** | `visionDispatch status="proposed"` with correct pose |
| **11** | `EventBus` + lifecycle cleanup in the router | orchestrator survives client reload |
| **12** | dispatch mode decided (6.3), `auto_execute=True` | cell trial |

> **State (this repo):** Phase 1 completed; Phases 2 and 3 are implemented for **server 1** — own
> package `src/vision_server/` with its own endpoint/ApplicationURI, both state machines,
> `StartSingleJob` with guard/validation/error codes, ResultManagement storage, four events and
> the JSON payload in the schema from 4.3. Detection is for now the placeholder
> `detection/hello_world.py`; a real payload now only means swapping out a `DetectionSource`.
> Phases 0 and 4–12 concern the WebSkillComposition repo. Interface for its connection:
> [`vision-server-interface.md`](vision-server-interface.md).

Phases 0–8 change **no** existing behaviour logic — only the two additive fixes, a
keyword-only default and an event cap. This is the concrete fulfilment of "minimal
intervention".

---

> **Correction to 6.4/R12 (verified):** `GetResultById` & co. cannot be called from the asyncua
> client — already their **input** argument (`ResultIdDataType`, `JobIdDataType`) is an
> ExtensionObject that cannot be built without `load_data_type_definitions()` (issue #1693).
> Consequently no handles are issued, and the `ReleaseResultHandle` leak is currently moot. The
> result arrives via the `ResultReadyEvent`.

# Part 10 — Verification

**Three test levels, increasing cost:**

1. **Unit without OPC UA** (main load) — via the existing `connection_factory=` seam
   (`application_service.py:63`, pattern in `tests/test_application_service.py:134`). A
   `FakeVisionConnection` accepts `subscribe_events(..., event_type_node_ids=..., on_event=...)`
   and calls the callback with a hand-built event. Cases: extraction → transformation → correct
   `execute_robot_action` inputs · dedupe (same `resultId` twice → one dispatch) · `in_flight`
   gate · `auto_execute=False` → event only · robot missing → `ErrorEvent` instead of exception ·
   `ReleaseResultHandle` also on extraction error.
2. **Pure functions** — `test_pose_transforms.py` (identity, 90° rotations about all axes, mm→m,
   quaternion round trip, non-orthonormal rotation → `ValueError`, `numpy.testing.assert_allclose`);
   `test_vision_discovery.py` (namespace gate, subtype collection, duck-typing fallback). Frontend:
   `poseTransform.test.ts`, `visionResultEvent.test.ts` — fits into `environment: 'node'`.
3. **Real server**, `@pytest.mark.integration`, in-process on a free port. Tests exactly what fakes
   cannot: that `discover_vision_system_nodes` finds the instance, and that `subscribe_events` with
   an event type actually delivers `ResultContent`.

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
markers = ["integration: requires a live OPC UA endpoint"]
addopts = "-m 'not integration'"
```

→ `uv run pytest` stays fast and offline; `uv run pytest -m integration` runs the full chain.

**End-to-end by hand:**

```bash
# 1. Start the vision server
uv run --directory vision-server wsc-vision-server --profile 2d --port 4841 \
       --app-uri urn:wsc:vision:2d --name "WSC Vision 2D"
# 2. Backend + frontend
cd backend && uv run main.py
cd frontend && npm run dev
```

1. Connect to `opc.tcp://127.0.0.1:4841/wsc/vision/2d` in the frontend → server card appears,
   robot list stays empty (expected).
2. In the address-space browser, navigate to `Objects/VisionSystem2D`, call `StartSingleJob` via
   `RawMethodCallModal`.
3. In the `MessageLog`, `opcuaEvent` entries must appear as an **object** (not as a `repr`
   string) and carry a `ResultContent` with valid JSON.
4. The `VisionStatusCard` shows `Ready → SingleExecution → Ready`.
5. The module appears as a CAD body in the viewport at the pose from `scenes/scene_2d.json` —
   ground truth and rendering must match.
6. Add a second server on 4842: both cards independent, no ID collision.

---

# Open points

1. **Dispatch mode (6.3)** — deliberately left open. Check step: does the real 40010 server
   expose `LinMoveTcp`/`RotMoveTcp`? Then decide between `cartesian` (0 extra effort, autonomous)
   and `frontendIk` (uses the existing IK, needs an open browser).
2. **Hand-eye calibration** — How and when are the two cameras surveyed to the robot base? Until
   then, `auto_execute` stays forced to `False`.
3. **Reconnect/watchdog (R9)** — out of scope for v1, but mandatory to add before productive cell
   operation.
