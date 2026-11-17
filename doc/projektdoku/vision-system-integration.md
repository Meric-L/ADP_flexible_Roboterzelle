# Integration of two OPC UA vision systems (OPC 40100) into WebSkillComposition

## Context

**WebSkillComposition** (WSC) controls industrial robots (Franka Research 3, EVA
Automata, UR5e) skill-based via an OPC UA robotics server (OPC 40010). Two
**self-built OPC UA vision servers** following **OPC 40100 (Machine Vision)** are added:
one per Raspberry Pi (ceiling camera and hand camera). They recognise modules and deliver
a **6D pose plus module ID** per module.

Target picture:

1. The vision systems run **lean and in the background**.
2. The WSC system is a client of both worlds, listens to the vision results and derives
   robot motions from them. The robot controller itself is **not** changed.
3. The frontend shows the vision **status** and, once pose and module ID are available,
   loads the **CAD models of the modules** and places them in the 3D viewport.

Guiding principle: **minimal intervention** — zero behaviour change on existing code
paths; new behaviour goes into new files where possible.

This is an in-depth document. It records the analysis of the existing system, the
blockers it found, the decisions taken and what was actually built. Code references
to WSC point to branch `hefdi/ADP_Lokalisierung`.

Related documents:

| Topic | Document |
| --- | --- |
| Vision server interface (address space, methods, payload, events, pitfalls) | [`vision-server-interface-short.md`](vision-server-interface-short.md), in depth [`vision-server-interface.md`](vision-server-interface.md) |
| Vision system overview, discovery | [`vision-system.md`](vision-system.md) |
| WSC backend: what is ours, what existed | [`code/webskill-backend.md`](code/webskill-backend.md) |
| WSC frontend, cell modules | [`code/webskill-frontend.md`](code/webskill-frontend.md), [`code/webskill-cell-modules.md`](code/webskill-cell-modules.md) |
| WSC's own docs | `docs/architecture.md`, `docs/data-and-interface-model.md`, `docs/vision-workflows.md` in the WSC repo |
| Open points of the vision system | [`vision-system-next-steps.md`](vision-system-next-steps.md) |

---

# Part 1 — How the existing system works

Full description: WSC `docs/architecture.md` and `docs/data-and-interface-model.md`;
the split into existing and our code: [`code/webskill-backend.md`](code/webskill-backend.md).
Only what matters for the integration is listed here.

## 1.1 Architecture in brief

```
Browser (React/Three.js)          Python backend (FastAPI)         OPC UA servers
┌───────────────────────┐  WS   ┌────────────────────────┐  OPC UA  ┌──────────────┐
│ Viewport (URDF, IK)   │◄─────►│ AsyncUaServerConnection│◄────────►│ Robotics 40010│
│ ServerManager panel   │ /ws   │ (1 client per URL)     │          │ Vision 40100 │
│ AddressSpace browser  │       │ handle_client_message  │          └──────────────┘
└───────────────────────┘       │ RuntimeRegistry        │
                                └────────────────────────┘
```

- **One** WebSocket `/ws` for all servers and robots; every message carries `serverUrl`
  or `robotId`. A separate channel `/ws/surface` carries large binary data (point clouds).
- **Contract-first**: Pydantic models in `backend/src/backend/models/messages.py`, a
  hand-maintained TS mirror in `frontend/src/shared/api/messages.ts`. `ContractModel`
  (`models/base.py`) sets `extra="forbid"` — unknown fields are rejected, so frontend and
  backend change together.

## 1.2 Backend layers

| Layer | Location | Task |
| --- | --- | --- |
| Transport | `websocket/router.py` | `/ws` loop, JSON → Pydantic; live events are deferred while a command runs |
| Dispatch | `runtime/application_service.py` `handle_client_message` | long sequential `isinstance` chain, one block per command |
| OPC UA client | `opcua/server_connection.py` `AsyncUaServerConnection` | one asyncua connection per server URL, anonymous, no security |
| Discovery | `opcua/asyncua_discovery.py`, `opcua/discovery.py` | scan the address space → bindings → robot actions |
| State | `services/runtime_registry.py` `RuntimeRegistry` | servers, robots, discovery cache |

Test seam: `DEFAULT_CONNECTION_FACTORY` in `application_service.py`; tests inject
`connection_factory=FakeConnection`.

## 1.3 Robot discovery and skills

- `discover_connected_server` looks for `Objects/DeviceSet` (DI) via `find_device_set`,
  then `MotionDeviceSystem/MotionDevices`, and matches the 40010 `MotionDeviceType` by
  TypeDefinition. A server without a DeviceSet yields **no robots**.
- Skills are detected **duck-typed**: an object with `ParameterSet`/`ResultSet`/
  `CurrentState` and `Start`/`Halt`/`Reset`/… children. `build_robot_action_bindings`
  turns every discovered skill and method into a robot action.
- Skill execution (`runtime/action_execution.py`) writes the parameters, calls `Start`,
  and `_watch_skill_action_state` **polls** `CurrentState` every 250 ms.

## 1.4 What already works generically for every OPC UA server

| Capability | Command |
| --- | --- |
| Browse address space | `browseAddressSpaceRoot/Children/References/NodeDetails` |
| Subscribe to a variable | `subscribeNode` / `unsubscribeNode` |
| Subscribe to events | `subscribeEvent` / `unsubscribeEvent` |
| Call any method | `callRawMethod` (with DataType coercion) |

**Consequence:** a 40100 server can be operated without a typed vision model in the
backend — only via raw method calls, node and event subscriptions. This is the lever for
minimal intervention and is how the integration was finally built (Part 5).

## 1.5 Frontend

Feature-Sliced Design (`app/`, `entities/`, `features/`, `shared/`), Vite (rolldown),
React 19, TypeScript strict, antd 6, three.js 0.184 with @react-three/fiber/drei,
`closed-chain-ik`. No zustand/redux: pure reducers in `entities/*/model/store.ts` and
`app/model/applicationController.ts` as the single mutable owner. Tests: vitest, logic
only. Details: [`code/webskill-frontend.md`](code/webskill-frontend.md).

## 1.6 Weak spots found in the analysis

| Weak spot | State now |
| --- | --- |
| Type check in discovery is an exact TypeDefinition comparison, not subtype-aware | unchanged; irrelevant for vision (B1) |
| `handle_client_message` is a long `isinstance` chain without a dispatch table | unchanged; vision added no block |
| No security in the OPC UA client | unchanged (R15) |
| Skill states are polled, not subscribed | unchanged |
| No reconnect/watchdog | fixed (R9) |
| No mock OPC UA server in the repo | the real Pi servers and `handshake_client.py` (this repo) serve as test peers |

---

# Part 2 — OPC 40100 (Machine Vision) overview

Namespace: `http://opcfoundation.org/UA/MachineVision`

```
VisionSystemType  (subtype of BaseObjectType — NOT of DI DeviceType)
├── VisionStateMachine           States: Preoperational · Halted · Error · Operational
│   │                            Methods: Reset · Halt · SelectModeAutomatic · ConfirmAll
│   └── AutomaticModeStateMachine
│       ├── States: Initialized · Ready · SingleExecution · ContinuousExecution
│       └── Methods: PrepareRecipe/PrepareProduct · StartSingleJob · StartContinuous
│                    · Stop · Abort · SimulationMode
├── ConfigurationManagement · RecipeManagement · SafetyStateManagement   [Optional]
└── ResultManagement             [Optional] GetResultById · GetResultListFiltered · …
```

- **Events**: `JobStartedEventType`, `AcquisitionDoneEventType`, **`ResultReadyEventType`**,
  `ReadyEventType`, `RecipePreparedEventType`.
- **`ResultDataType`** ends in `resultContent: BaseDataType[]` — the 6D pose and module
  ID go there.
- `Opc.Ua.MachineVision.NodeSet2.xml` requires **only Core UA 1.04** — no DI, Machinery
  or Robotics.

Sources: [OPC 40100-1](https://reference.opcfoundation.org/specs/OPC-40100-1) ·
[7.1 VisionSystemType](https://reference.opcfoundation.org/MachineVision/v100/docs/7.1) ·
[12.17 ResultDataType](https://reference.opcfoundation.org/MachineVision/v100/docs/12.17) ·
[UA-Nodeset MachineVision](https://github.com/OPCFoundation/UA-Nodeset/tree/latest/MachineVision)

---

# Part 3 — Blockers that shaped the plan

The code analysis found five blockers. Status checked against the code:

| # | Blocker | Status |
| --- | --- | --- |
| B0 | `FakeConnection.write_node_value` in `tests/test_application_service.py` lacked `coerce_to_existing`, so a skill test was red | **resolved**: the fake accepts `coerce_to_existing` |
| B1 | A pure vision server returns no robots: discovery needs a DI `DeviceSet` | **bypassed, by design**: the backend still finds no devices on a vision server; the frontend resolves the vision nodes itself from the namespace array (`resolveVisionBinding` in `piServers.ts`) |
| B2 | `subscribe_events` did not filter by event type, so asyncua selected only `BaseEventType` fields and `ResultContent` never arrived | **resolved**: `SubscribeEventCommand.event_type_node_ids` → `AsyncUaServerConnection.subscribe_events(event_type_node_ids=…)`; the frontend passes the five 40100 event types from `piServers.ts` |
| B3 | `to_jsonable(event)` turned an asyncua `Event` into a `repr` string | **resolved**: `to_jsonable` in `opcua/method_calls.py` has an `Event` branch that returns `{eventType, <fields>}` |
| B4 | `goto` takes joint angles; the IK lives in the frontend on the loaded URDF | **resolved, frontend IK**: `createRobotDriver` (`cell-modules/model/robotDrive.ts`) solves the flange target with `FlangeKinematics` and sends joint angles via `callRobotGotoForVisualAngles`. The robot servers offer no Cartesian move skill |

B2 and B3 only work together: B2 makes the server send the field, B3 keeps it structured
on the way to the frontend.

---

# Part 4 — What our vision servers look like

The interface is documented in [`vision-server-interface-short.md`](vision-server-interface-short.md)
(current state) and [`vision-server-interface.md`](vision-server-interface.md) (address
space, job handshake, payload, all verified pitfalls). This part keeps only the decisions
that concern the integration.

## 4.1 One server per Pi, in this repo

The plan proposed a separate `vision-server/` uv project in the WSC monorepo with two
simulated servers (2D, 3D) in Docker Compose. **Built instead:** the vision server lives
in this repo under `src/vision_server/`; each Pi runs **one** server
(`opc.tcp://<pi>:4840/raspi/server/`, systemd unit `opcua-server.service`) with one
`VisionSystemType` instance `VisionMachine`, installed by `install_vision_machine`. The
two vision systems of the plan are the two Pis:

| Pi | `visionSystemId` | `frameId` | Role |
| --- | --- | --- | --- |
| ceiling | `vision-ceiling-01` | `cam_ceiling` | Layer 1 localization, cell watch |
| hand | `vision-flange-01` | `cam_flange` | Layer 2 measurement, hand-eye, tag survey |

Reason for one server per Pi: a second server process loads the 40100 address space twice
(~110 MB RSS each) and forces two sessions and subscriptions for one camera. Detection is
a `DetectionSource` (`src/vision_server/detection/base.py`); the AprilTag source is in
`detection/apriltag/`. WSC's `docker-compose.yaml` has only the `platform` service.

## 4.2 Decisions that affect the client

| Decision | Why |
| --- | --- |
| Subscribe to events **on `VisionMachine`**, not on the server object `i=2253` | asyncua delivers events only to the exact emitting node; the `HasNotifier` reference to the server object exists but does not forward |
| Custom nodes have **string NodeIds** `ns=<vision>;s=VisionMachine.<Name>` | stable, readable NodeIds; the namespace index is still resolved by URI at runtime |
| Never hard-code namespace indices | `piServers.ts` resolves `http://launch-rm.de/vision` and the MachineVision URI from the namespace array |
| `ResultManagement` methods are not used | their inputs are ExtensionObjects the asyncua client cannot build without `load_data_type_definitions()` (asyncua issue #1693); the result arrives in the event instead |
| State sequence `Preoperational → Operational`, `Initialized → Ready → SingleExecution → Ready` | the nodeset has no `Halted → Operational` transition |

Server-side asyncua pitfalls worth knowing for any further 40100 server (all handled in
`src/vision_server/`):

| Pitfall | Countermeasure |
| --- | --- |
| Event fields arrive as `None` (asyncua derives VariantType `Null` from the nodeset) | assign every field as a finished `ua.Variant(value, type)` (`events.py`) |
| A method return given as a `list` arrives nested as one Variant | always return a **tuple** of Variants (`runner/uamethods.py`) |
| `import_xml` order | after `init()`, before `start()`; set the ApplicationUri before the import |

## 4.3 Result payload: JSON string in `ResultContent[0]`

**Decision — deliberately against a custom Structured DataType.**

| Option | Client robustness | Verdict |
| --- | --- | --- |
| Custom Structured DataType | backend would need `load_data_type_definitions()` (issue #1693); otherwise raw bytes arrive | rejected |
| **JSON string** in `ResultContent[0]` | strings pass `to_jsonable` unchanged | **chosen** |
| `Double[]` | module ID is a string, no confidence/attributes | rejected |

- `ResultReadyEventType` carries `ResultContent` as a flat field, so the JSON travels
  **inside the event**; no second read. Correlation result → job via `jobId` in the payload.
- `ResultContent` comes from the nodeset with a null DataType; the server overrides it to
  `String` and writes an array (`src/vision_server/result_management.py`).
- Schema `wsc.vision.detections/1` (`src/vision_server/payload.py`, parsed in WSC by
  `parseVisionResult` in `features/opcua-server/model/visionResult.ts`). Rules: SI metres
  and radians, explicit `frameId`, quaternion `xyzw`, `schema` version in every payload.
  Field list: [`vision-server-interface.md`](vision-server-interface.md) section 6.

Shape of the payload (abridged; the interface doc is authoritative):

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-ceiling-01",
  "resultId": "…", "jobId": "…", "creationTime": "…",
  "frameId": "cam_ceiling",          // reference frame of the poses
  "detections": [
    {
      "moduleId": "…",               // key into the module catalog
      "confidence": 0.93,
      "position": [0.42, -0.11, 0.05],          // metres
      "orientation": [0.0, 0.0, 0.3827, 0.9239] // quaternion xyzw
    }
  ]
}
```

`ResultContent` is an array: `[1]` could later carry a real Structured DataType without
touching the JSON path.

---

# Part 5 — What was changed in the existing system

The plan had two stages: **stage 1** (visibility: fix B2/B3, a status card, a CAD overlay)
and **stage 2** (a typed vision model in the backend with discovery, sessions, an
orchestrator and new message types). **Stage 1 was built, in a different shape. Stage 2
was not built**: the generic commands (1.4) were enough, and the orchestration moved to
the frontend.

## 5.1 Backend (WSC)

| File | Change |
| --- | --- |
| `opcua/server_connection.py` | event subscriptions with custom event types (B2); reconnect with a fresh client and `restore_subscriptions`; keepalive `check_alive`; request timeout 120 s for slow Pi methods |
| `opcua/method_calls.py` | `to_jsonable` handles asyncua `Event` (B3) |
| `models/messages.py` | `SubscribeEventCommand.event_type_node_ids` (optional) |
| `runtime/application_service.py` | server liveness watch `_watch_server_liveness`; OPC UA events forwarded to the client |
| `api/module_catalog.py`, `services/module_catalog_store.py` | module catalog `GET`/`PUT /api/module-catalog`, stored in `backend/catalog/module_catalog.json` |

No new vision message type: vision traffic uses `callRawMethod`, `subscribeNode` and
`subscribeEvent`. Full list with share of changed lines:
[`code/webskill-backend.md`](code/webskill-backend.md).

## 5.2 Frontend (WSC)

| Area | Files |
| --- | --- |
| Pi bindings: NodeIds, method signatures, event types | `features/opcua-server/config/piServers.ts` (`resolveVisionBinding`) |
| Job flow and payload | `useVisionJob.ts`, `useContinuousVisionJob.ts`, `visionResult.ts` |
| Status | `useCameraHealth.ts` (OPC 40100-2 `DeviceHealth`), `PiStatusList.tsx`, `PiActivity.tsx`, `LastCalibrationBadge.tsx` |
| Camera image | `CameraStreamPanel.tsx`, `useMjpegStreamUrl.ts` (MJPEG over HTTP from the Pi) |
| Cell modules, CAD overlay, Layer 1/2, hand-eye | `features/cell-modules/` — see [`code/webskill-cell-modules.md`](code/webskill-cell-modules.md) |

Overview of all flows: WSC `docs/vision-workflows.md`.

## 5.3 Stage 2 — planned, not built

For the record, the rejected or deferred stage-2 ideas:

- **Typed vision discovery** (namespace gate, subtype-aware search for `VisionSystemType`,
  `VisionStateMachine` as duck-typing marker). Not needed: the frontend resolves the
  nodes from the namespace array.
- **Backend orchestrator** that subscribes to results, transforms poses and starts robot
  skills on its own. Replaced by frontend-driven runs (Part 6).
- **Disguising the vision server as a DI `MotionDeviceType`** so robot discovery finds it —
  rejected: violates 40100, and the frontend would treat the camera as a robot.

---

# Part 6 — The signal path from vision to robot

## 6.1 Chain as built

```
Vision Pi                       WSC backend                     WSC frontend
─────────                       ───────────                     ────────────
                                callRawMethod ◄──────────────── useVisionJob: StartSingleJob
JobStartedEvent ─┐
AcquisitionDone ─┼─► subscribe_events(VisionMachine, [5 types])
ResultReadyEvent ┘   to_jsonable(Event) ──── opcuaEvent ──────► parseVisionResult(ResultContent[0])
                                                                 │
                                                                 ├─ cellModules: place modules (CAD)
                                                                 └─ robotDrive: IK → go_to angles
Robotics server  ◄──────────── go_to skill ◄──────────────────── callRobotGotoForVisualAngles
```

Pose planning and sequencing run in the frontend; image processing and calibration
solving run on the Pis; the backend passes OPC UA calls and events through.

## 6.2 Hand-eye transformation

Planned: a backend module with a hand-eye calibration per vision-robot link, loaded from
a JSON file or set at runtime, and an auto-execute switch that stays off without a
calibration.

**Built instead:** the hand Pi owns the hand-eye calibration.

- Calibration is computed on the Pi (Park–Martin) via `StartHandEyeCalibration` /
  `CaptureHandEyeSample` / `FinishHandEyeCalibration`, driven from the frontend
  (`useHandEyeCalibration.ts`, `HandEyeAutoPanel.tsx`). The result `T_flansch_cam` is
  stored on the Pi in `data/handeye/cam_flange.json`.
- For a Layer 2 run the frontend reports the **robot-reported** flange pose
  (`ReportRobotPose`); the Pi transforms camera poses into the robot base.
- Details: [`vision-server-interface.md`](vision-server-interface.md) sections 14 and 15;
  anchoring at the world tag: [`arbeitsplaene/apriltag-hand-auge-ankern.md`](arbeitsplaene/apriltag-hand-auge-ankern.md).

Conventions that avoid silent errors (unchanged from the plan): `frameId` on every pose,
metres, quaternion `xyzw`, and a fixed direction for transforms (`T_base_cam` vs.
`T_cam_base`).

## 6.3 How a pose becomes motion (B4)

| Mode | How | Status |
| --- | --- | --- |
| `cartesian` | a Cartesian skill on the 40010 server | **not available**: the robot servers only offer `go_to` with joint angles |
| **frontend IK** | the frontend solves IK for the flange target and sends joint angles | **built**: `createRobotDriver` in `robotDrive.ts`, `FlangeKinematics`, `planMove` |
| backend IK | URDF parsing + IK library in the backend | rejected: new dependency, kinematics validation |

Consequence: autonomous runs need an open browser.

## 6.4 Race conditions and safety

Planned in the backend: dedupe by `resultId`, `creationTime` monotonicity, an in-flight
gate per link, dry run as default, a single writer. As built in the frontend:

- **Job correlation**: `useVisionJob.ts` subscribes before `StartSingleJob`, matches the
  result to the returned `JobId` and ignores a `ResultReadyEvent` from an earlier run.
- **No pose taken as reached unless the robot confirmed it**: a `RobotDriver` reports the
  flange pose the robot reports, never the target.
- **Explicit arming**: an automatic drive with the real robot starts only after
  `RobotRunConfirm.tsx` (checks, then confirm).
- **Pi-side locks**: concurrent jobs answer `BUSY`; calibration and Layer 2 sessions lock
  each other ([`vision-server-interface.md`](vision-server-interface.md) 12.7).

---

# Part 7 — Frontend: status and CAD overlay

## 7.1 Status

Planned: one status card per server that subscribes to the server object `i=2253`
(W2), instead of a vision entity in the store (W3, ~450 lines in core files).

**Built:** status without a new store entity — per-Pi status in `PiStatusList.tsx` and
`PiActivity.tsx`, camera health via a node subscription on `DeviceHealth`
(`useCameraHealth.ts`), job state from the events on `VisionMachine`. The subscription to
`i=2253` would receive nothing (4.2). `VisionStateMachine.CurrentState` is a variable;
it is read with `subscribeNode`, not polled.

## 7.2 CAD overlay

| Topic | Plan | Built |
| --- | --- | --- |
| Model format | glTF `.glb` via drei `useGLTF` | `CellModuleModel.tsx` loads glTF, STL and Collada (three.js loaders) |
| Location | `frontend/public/cad/modules/<moduleId>.glb` | models under `frontend/public/models/`, referenced from the module catalog |
| Registry | module ID → CAD asset table in the frontend | module catalog (`config/moduleCatalog.ts`, backend `module_catalog.json`): modules, tags, models, world-tag poses |
| Z-up → Y-up | one central, tested conversion | `worldToScenePosition` in `cellModules.ts`, tested in `cellModules.test.ts` |
| Placement | overlay component next to the robots | `placeLayer1Detections` / `placeLayer2Detections` (`cellModules.ts`), preview and import via `planLayer1Import` (`layer1Import.ts`) |

Kept from the plan: modules are **not** `Robot` instances (no joint runtime, IK or drag
controls for them); the world frame is `world` (`WORLD_FRAME_ID`), Z-up, the scene is
three.js Y-up.

## 7.3 Poses to the frontend: piggyback on `opcuaEvent`

Decision: the result rides on the existing `opcuaEvent` (its `event` field is `Any` /
`unknown`), instead of a new `visionResult` message. This kept `messages.py`,
`messages.ts`, `websocketClient.ts` and the stores unchanged; only B2/B3 were needed. A
typed message would only pay off with a backend vision model (stage 2), which was not
built.

---

# Part 8 — Risks

| # | Risk | Status |
| --- | --- | --- |
| R1 | B2 — custom event fields never arrive ("runs, but poses are empty") | resolved (Part 3) |
| R2 | B3 — `opcuaEvent` as `repr` string | resolved (Part 3) |
| R3 | B4 — no IK in the backend | resolved by frontend IK; autonomous runs need the browser |
| R4 | B0 — red test suite | resolved |
| R5 | circular import between discovery modules | moot: no vision discovery in the backend |
| R6 | discovery cache never invalidated | moot for vision: bindings are resolved per connection in the frontend |
| R7 | exception in a background task disappears silently | open in general; Pi methods report error codes and status JSON |
| R8 | orchestrator sends to a dead WS connection | moot: no backend orchestrator |
| R9 | **no reconnect/watchdog** — subscriptions die silently on disconnect | **resolved**: `_watch_server_liveness` + `check_alive`, reconnect with a fresh client, `restore_subscriptions` restores node and event subscriptions |
| R10 | backend becomes a motion source without an interlock | moot: motion is started from the frontend after `RobotRunConfirm` |
| R11 | units/conventions: mm↔m, xyzw↔wxyz, `T_base_cam`↔`T_cam_base` | ongoing: `frameId` on every pose, schema version, tests on the transforms |
| R12 | result-handle leak | moot: `ResultManagement` methods are not used (4.2) |
| R13 | asyncua issues #651 / #1693 on nodeset import | resolved: import works; JSON payload avoids #1693 |
| R14 | unbounded `opcuaEvents` array in the frontend | **open**: `entities/server/model/store.ts` appends every `opcuaEvent` and only drops a server's events on disconnect (`filterEventsForServer`) |
| R15 | anonymous, unencrypted OPC UA connections | open, project status quo (`NoSecurity`) |

---

# Part 9 — Sequence

Phase plan and its state. Phases 2–3 concern this repo, the others WSC.

| Phase | Content | State |
| --- | --- | --- |
| 0 | fix `FakeConnection.write_node_value` | done |
| 1 | spike: nodeset import, result encode/decode | done |
| 2 | server skeleton: address space, `VisionMachine` instance | done (`src/vision_server/`) |
| 3 | state machines, `StartSingleJob`, ResultManagement, events, JSON payload | done; detection via AprilTag |
| 4 | backend B2 + B3 | done |
| 5 | frontend payload parser and pose conversion + tests | done (`visionResult.ts`, `cellModules.ts`) |
| 6 | status in the server panel | done (`PiStatusList.tsx`, `useCameraHealth.ts`) |
| 7 | CAD module at the correct place in the viewport | done (`CellModuleModel.tsx`, Layer 1 import) |
| 8 | second vision server | done as second Pi (hand camera) |
| 9 | stage 2: typed vision sessions in the backend | dropped (Part 5.3) |
| 10 | hand-eye calibration, poses in the robot base | done, on the hand Pi (6.2) |
| 11 | lifecycle cleanup, survive client reload | partly: reconnect and subscription restore (R9) |
| 12 | dispatch mode decided, robot moves on vision results | done: frontend IK (6.3), Layer 2 run |

Phases 0–8 changed no existing behaviour logic beyond the additive fixes listed in 5.1.

---

# Part 10 — Verification

**Automated:**

| Repo | Command | Covers |
| --- | --- | --- |
| this repo | `PYTHONPATH=src python3 -m unittest discover -s tests -t .` | vision server, payload (`test_payload.py`), job runner, Layer 2, hand-eye |
| WSC backend | `cd backend && uv run pytest` | event subscription with types (`test_server_connection.py`), application service with `FakeConnection` |
| WSC frontend | `cd frontend && npm test` | `visionResult.test.ts`, `cellModules.test.ts` (Z-up → Y-up), cell-module logic |

**Handshake against a real Pi** (reference client in this repo):

```bash
PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py \
    --url opc.tcp://<pi>:4840/raspi/server/ --recipe-id calibration
```

Exit code `0` = handshake complete. Special cases (busy, invalid recipe, forced error):
[`vision-server-interface.md`](vision-server-interface.md) section 8.

**End-to-end by hand:**

```bash
cd backend && uv run main.py
cd frontend && npm run dev
```

1. Connect to both Pis in the frontend → server cards appear, no robots on them
   (expected, B1).
2. Start a job (Layer 1 on the ceiling Pi) → the `MessageLog` shows `opcuaEvent` entries
   as **objects** with `ResultContent` holding valid JSON.
3. The Pi status shows the job sequence; camera health is known.
4. The detected modules appear as CAD bodies in the viewport at the reported poses.
5. Both Pis run independently, no ID collision (`vision-ceiling-01` / `vision-flange-01`).

---

# Open points

| # | Point | Status |
| --- | --- | --- |
| 1 | Dispatch mode (6.3) | resolved: frontend IK; a Cartesian skill on the robot server is not available |
| 2 | Hand-eye calibration of the cameras to the robot base | resolved for the hand camera (6.2); the ceiling camera is localized via world tags |
| 3 | Reconnect/watchdog (R9) | resolved in `AsyncUaServerConnection` and `_watch_server_liveness` |
| 4 | Event cap in the frontend store (R14) | open |
| 5 | OPC UA security (R15) | open |

Further open points of the vision system: [`vision-system-next-steps.md`](vision-system-next-steps.md).
