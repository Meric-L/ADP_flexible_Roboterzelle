# Integration von zwei OPC-UA-Vision-Systemen (OPC 40100) in WebSkillComposition

## Context

Das bestehende System **WebSkillComposition** steuert Industrieroboter (Franka Research 3,
EVA Automata, UR5e) skillbasiert über einen OPC-UA-Robotics-Server (OPC 40010). Ergänzt werden
sollen **zwei selbstgebaute OPC-UA-Vision-Server** nach **OPC 40100 (Machine Vision)**, die
Module erkennen und je Modul eine **6D-Pose plus Modul-ID** liefern (weitere Ergebnisarten
bleiben möglich).

Zielbild:

1. Die Vision-Systeme laufen **schlank und im Hintergrund**.
2. Das **Backend orchestriert**: es ist OPC-UA-Client beider Welten, hört auf die
   Vision-Ergebnisse und leitet daraus Bewegungsbefehle an die Robotersteuerung ab.
   Die Robotersteuerung selbst wird **nicht** verändert.
3. Das Frontend zeigt später **nur den Status** — und lädt, sobald Pose und Modul-ID vorliegen,
   die **CAD-Daten der Module** und positioniert sie anhand der Pose im 3D-Viewport.

Leitplanke: **minimale Eingriffe.** Nicht „möglichst wenige Zeilen um jeden Preis", sondern
**null Verhaltensänderung an bestehenden Codepfaden**. Alles Neue kommt in neue Dateien.

---

# Teil 1 — Wie das bestehende System funktioniert

## 1.1 Grobarchitektur

```
Browser (React/Three.js)          Python-Backend (FastAPI)         OPC-UA-Server
┌───────────────────────┐  WS   ┌────────────────────────┐  OPC UA  ┌──────────────┐
│ Viewport (URDF, IK/FK)│◄─────►│ AsyncUaServerConnection│◄────────►│ Robotics-    │
│ ServerManager-Panel   │ /ws   │ (1 Client pro URL)     │          │ Server 40010 │
│ AddressSpace-Browser  │       │ handle_client_message  │          └──────────────┘
└───────────────────────┘       │ RuntimeRegistry        │
            ▲                   └────────────────────────┘
            │ /ws/surface (Punktwolken, gechunkt, eigenes Protokoll)
```

- **Ein** WebSocket (`ws://127.0.0.1:8000/ws`) für alle Server und Roboter. Jede Nachricht trägt
  `serverUrl` bzw. `robotId` zur Zuordnung.
- **Ein zweiter, separater Kanal** `/ws/surface` mit eigenem Protokoll existiert bereits für
  große Binärdaten (Punktwolken → Oberflächenrekonstruktion). Präzedenzfall, falls Vision
  jemals Bilddaten streamen soll.
- Kommunikation ist **contract-first**: Pydantic-Modelle in
  `backend/src/backend/models/messages.py`, handgepflegtes TS-Spiegelbild in
  `frontend/src/shared/api/messages.ts`. `ContractModel` (`models/base.py`) erzeugt
  camelCase-Aliase und setzt `extra="forbid"` — **unbekannte Felder werden hart abgelehnt**,
  Frontend und Backend müssen also immer gemeinsam geändert werden.

## 1.2 Backend: vier Schichten

| Schicht | Ort | Aufgabe |
| --- | --- | --- |
| Transport | `websocket/router.py:27` | `/ws`-Schleife, JSON→Pydantic, Live-Events werden während laufender Kommandos gepuffert (`defer_live_events`) |
| Dispatch | `runtime/application_service.py:238` `handle_client_message` | 785 Zeilen sequentieller `if isinstance(message, XCommand)`-Blöcke, jeder gibt `list[ServerMessage]` zurück |
| OPC-UA-Client | `opcua/server_connection.py:124` `AsyncUaServerConnection` | genau **eine** asyncua-Verbindung pro Server-URL, lazy connect, **anonym** (keine Security konfiguriert) |
| Discovery | `opcua/asyncua_discovery.py` (804 Z.) + `opcua/discovery.py` (112 Z.) | Adressraum scannen → Rohbindungen → normalisierte Actions |

**State**: `services/runtime_registry.py` `RuntimeRegistry` mit `_servers_by_url`,
`_robots_by_id`, `_discovery_cache_by_url` (Discovery-Ergebnisse werden gecacht).

**Test-Seam**: `DEFAULT_CONNECTION_FACTORY` (`application_service.py:63`) — Tests injizieren
`connection_factory=FakeConnection`.

## 1.3 Robot-Discovery: was heute fest verdrahtet ist

`opcua/asyncua_discovery.py:24-29`:

```python
ROBOTICS_NAMESPACE_URI = "http://opcfoundation.org/UA/Robotics/"
DEVICE_INTEGRATION_NAMESPACE_URI = "http://opcfoundation.org/UA/DI/"
MOTION_DEVICE_TYPE_IDENTIFIER = 1004      # MotionDeviceType
AXIS_TYPE_IDENTIFIER = 16601              # AxisType
MOTION_DEVICE_NUMERIC_ID_RANGE = range(5010, 5020)   # Node-ID-Probe als Abkürzung
```

Ablauf `discover_connected_server` (`:740`):

1. `read_namespace_uris` → Namespace-Array lesen, Index per URI auflösen
   (`typed_node_id` baut `ns=<idx>;i=<id>`).
2. `find_device_set` (`:162`) → `Objects/DeviceSet` (DI) mit Namensfallback.
3. `find_motion_devices_container` (`:184`) → `DeviceSet/MotionDeviceSystem/MotionDevices`.
4. `discover_motion_device_nodes` (`:191`) → dreistufig: Node-ID-Probe (5010–5019) → direkte
   Kinder → BFS über den DeviceSet-Teilbaum. Match immer per **exaktem Stringvergleich** der
   TypeDefinition — **nicht subtypbewusst**.
5. Je MotionDevice: Variablen, Achsen, Methoden, Skills; zusätzlich globale Scans ab `Objects`
   (max_depth=4, dann unbegrenzt); lokal überschreibt global (`merge_bindings`).

## 1.4 Zwei Fähigkeitsebenen: `opcua` (roh) und `actions` (normalisiert)

**Roh** (`models/robot.py:33` `RobotOpcUaInterface`): `variables`, `methods`, `skills`, `axes`
— snake_case-normalisiert über `normalize_capability_name` (`:337`, `GoTo` → `go_to`).

Die **Skill-Erkennung ist ententypisiert** (`discover_skill_bindings:460`):

> Ein Objekt gilt als Skill, wenn es **mindestens eines** von
> `ParameterSet` / `ResultSet` / `CurrentState` **und mindestens eines** von
> `Start` / `Halt` / `Reset` / `Suspend` / `Resume` als Kind hat.
> Es wird **kein** `ProgramStateMachineType` geprüft.

**Normalisiert** (`opcua/discovery.py:50` `build_robot_action_bindings`) — hartkodierte Tabelle:

```python
add_skill_action("goto", "go_to");   add_skill_action("home", "home")
add_skill_action("linMoveTcp", "lin_move_tcp");  add_skill_action("rotMoveTcp", "rot_move_tcp")
add_method_action("createSession", "create_new_session");  ...
```

## 1.5 Skill-Ausführung

`runtime/action_execution.py:150` `_execute_skill_action`:

1. Eingaben normalisieren, gegen `skill.parameters` validieren.
2. Je Parameter `write_node_value(node_id, value, coerce_to_existing=True)` — liest den
   aktuellen Wert und passt den Typ an (vermeidet asyncua-Variant-Mismatches).
3. `call_raw_method(start_node_id, {"args": []})`.
4. `CurrentState` lesen, `status="running"` melden.

Danach pollt `application_service.py:161` `_watch_skill_action_state` den `CurrentState`
**alle 250 ms** und schickt bei Änderung ein `robotActionState`-Event.

## 1.6 Was heute schon generisch für **jeden** OPC-UA-Server funktioniert

Das ist der Hebel für minimale Eingriffe:

| Fähigkeit | Command | Connection-Methode |
| --- | --- | --- |
| Adressraum browsen | `browseAddressSpaceRoot/Children/References/NodeDetails` | `browse_address_space_*` |
| Variable abonnieren | `subscribeNode` / `unsubscribeNode` | `subscribe_node:210` |
| **Events abonnieren** | `subscribeEvent` / `unsubscribeEvent` | `subscribe_events:234` |
| Beliebige Methode rufen | `callRawMethod` | `call_raw_method` + volle DataType-Koersion |
| Knoten lesen/schreiben | (intern, kein Message-Typ) | `read_node_value` / `write_node_value` |

**Konsequenz:** Ein OPC-40100-Server ließe sich **heute schon ohne eine einzige
Backend-Änderung** bedienen — nur untypisiert über den Address-Space-Browser.

## 1.7 Frontend

Feature-Sliced Design mit vier Schichten (`app/`, `entities/`, `features/`, `shared/`),
Vite (rolldown) + React 19 + TS strict + antd 6 + Tailwind 4 + three.js 0.184 /
@react-three/fiber / drei 10.7 + urdf-loader + closed-chain-ik.

- **Kein zustand/redux.** Reine Reducer in `entities/*/model/store.ts`
  (`applyRobotMessage`, `applyServerMessage`), ein zentraler
  `app/model/applicationController.ts` (1138 Z.) als einziger mutabler Besitzer, der nach jeder
  Nachricht `emitState()` ruft; genau **ein** `useState` in `App.tsx:33`. Contexts sind
  reine Fassaden.
- **Einhängepunkt UI**: `features/opcua-server/components/ServerManager.tsx:59` rendert
  `<RobotManager serverUrl={...} embedded />` **innerhalb** der Server-Karte.
- **Viewport** `features/viewport/components/Viewport.tsx` (1358 Z.): `:1225` filtert
  `robots.filter(r => r.visual.urdfUrl)`, `:1345` mappt auf `<ViewportRobot>`.
  Statische Assets liegen unter `frontend/public/`, werden von Vite nach `dist/` kopiert und
  im Docker-Image über `StaticFiles(directory="./www")` (`app.py:19`) ausgeliefert.
- **Tests**: vitest mit `environment: 'node'` — nur Logik ist getestet, keine Komponenten.

## 1.8 Bekannte Schwachstellen im Bestand

- Typprüfung bei der Discovery ist exakter Stringvergleich, nicht subtypbewusst.
- `handle_client_message` ist eine 785-Zeilen-`isinstance`-Kette ohne Dispatch-Tabelle.
- Kein Mock-/Simulations-OPC-UA-Server im Repo.
- Keine Security/Authentifizierung im OPC-UA-Client.
- Skill-Zustände werden gepollt statt über OPC-UA-Events abonniert.
- **Kein Reconnect/Watchdog** — asyncua-Subscriptions sterben beim Verbindungsabriss lautlos.
- Discovery-Cache wird nie invalidiert (`runtime_registry.py`, `application_service.py:221`).

---

# Teil 2 — OPC 40100 (Machine Vision) im Überblick

Namespace: `http://opcfoundation.org/UA/MachineVision`

```
VisionSystemType  (Subtyp von BaseObjectType — NICHT von DI DeviceType!)
├── VisionStateMachine        VisionStateMachineType            [Mandatory]
│   ├── States: Preoperational · Halted · Error · Operational
│   ├── Methoden: Reset · Halt · SelectModeAutomatic · ConfirmAll
│   └── AutomaticModeStateMachine   VisionAutomaticModeStateMachineType   [Mandatory]
│       ├── States: Initialized(5) · Ready(6) · SingleExecution(7) · ContinuousExecution(8)
│       └── Methoden:
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
`processingTimes`, **`resultContent: BaseDataType[]`** ← hier landen 6D-Pose + Modul-ID.

**Wichtig, geprüft:** `Opc.Ua.MachineVision.NodeSet2.xml` deklariert als `RequiredModel`
**nur Core UA 1.04** — kein DI, kein Machinery, keine Robotics. Genau **eine** Datei importieren.

Quellen: [OPC 40100-1](https://reference.opcfoundation.org/specs/OPC-40100-1) ·
[7.1 VisionSystemType](https://reference.opcfoundation.org/MachineVision/v100/docs/7.1) ·
[8.2](https://reference.opcfoundation.org/MachineVision/v100/docs/8.2) ·
[8.3](https://reference.opcfoundation.org/MachineVision/v100/docs/8.3) ·
[12.17 ResultDataType](https://reference.opcfoundation.org/MachineVision/v100/docs/12.17) ·
[UA-Nodeset MachineVision](https://github.com/OPCFoundation/UA-Nodeset/tree/latest/MachineVision)

---

# Teil 3 — Blocker, die den Plan formen

Vier Befunde aus der Code-Analyse. Sie sind der Grund, warum die Umsetzung gestuft ist.

### B1 — Ein Vision-Server liefert heute garantiert `robots=[]`

`asyncua_discovery.py:667` bricht ab, wenn `find_device_set` `None` liefert; `:203` bricht ab,
wenn der Robotics-Namespace fehlt. Ein reiner Vision-Server erscheint als **verbundener Server
mit null Geräten**. Der ententypisierte Skill-Scan über `Objects` (`:681-691`) läuft zwar,
sein Ergebnis wird aber weggeworfen, weil es nur in der (leeren) MotionDevice-Schleife
konsumiert wird.

### B2 — `subscribe_events` filtert nicht nach Event-Typ

`server_connection.py:242` ruft `subscription.subscribe_events(node)`. asyncua defaultet damit
auf `BaseEventType` und nimmt nur dessen Felder in die SelectClauses auf (EventId, EventType,
SourceNode, Time, Message, Severity). Die Custom-Felder eines `ResultReadyEventType` — insb.
`Result` bzw. `ResultContent` — **kommen nie an**. Symptom: „funktioniert, aber alle Posen sind
leer."

### B3 — `to_jsonable(event)` zerstört das Event-Objekt

`EventSubscriptionHandler.event_notification` (`:120`) ruft `to_jsonable(event)`
(`method_calls.py:369`). Ein asyncua-`Event` hat weder `.to_string()` noch `.Value` → die letzte
Zeile `return str(value)` greift. Der `opcuaEvent`-Payload im Frontend ist heute **ein einziger
Python-`repr`-String**. Betrifft auch den bestehenden Event-Browser.

### B4 — `goto` ist Gelenkraum, die IK sitzt im Frontend

`goto` erwartet `{mode, joints[], max_speed, time, tcp_config, avoidance_zones}`.
`applicationController.ts:588` `callRobotGoto` validiert ein Gelenkwinkel-Array;
`callRobotGotoForVisualAngles` (`:610`) mappt Visualwinkel → Achswerte. Die IK selbst liegt in
`features/viewport/model/robotIk.ts` (231 Z., `closed-chain-ik`) und arbeitet auf dem
**geladenen URDF-Objekt in der Three.js-Szene** — sie ist nicht ins Backend portierbar, ohne
URDF-Parsing und eine neue Kinematik-Dependency mitzubringen.

`linMoveTcp` / `rotMoveTcp` sind in `discovery.py:99-100` gemappt, werden im Frontend aber
**nirgends verwendet**. Ob der reale 40010-Server sie exponiert, ist **offen** → siehe 6.3.

### B0 — Ein bestehender Test ist rot

`action_execution.py:184` ruft `write_node_value(..., coerce_to_existing=True)`; das Test-Double
`tests/test_application_service.py:263` kennt den Parameter nicht:

```python
async def write_node_value(self, node_id: str, value: object) -> None:
```

→ `TypeError` → gefangen in `action_execution.py:189` → `RobotActionExecutionError` → 
`application_service.py:854` gibt ein `error`-Event zurück →
`test_execute_robot_action_dispatches_skill_and_emits_runtime_state` (`:689`) erwartet
`methodResult`. **Muss als Phase 0 gefixt werden**, sonst gibt es keine grüne Baseline.
Der Fix ist eine Zeile:

```python
async def write_node_value(
    self, node_id: str, value: object, *, coerce_to_existing: bool = False
) -> None:
```

> Hinweis: `uv run pytest` war in dieser Umgebung nicht ausführbar (kein `.venv`, kein
> Netzwerk). Der Befund ist statisch hergeleitet und vor Umsetzungsbeginn einmal
> auszuführen.

---

# Teil 4 — So sollen unsere Vision-Server aussehen

## 4.1 Repo-Layout

Eigener Ordner im Monorepo, **eigenes uv-Projekt** — damit `backend/pyproject.toml` unverändert
bleibt und die Nodeset-XML nicht ins Backend-Wheel wandert. Nicht in `backend/src/backend/`,
weil das Package die Client-Rolle hat; die in `docs/architecture.md` dokumentierte Trennung
bleibt so erhalten.

```
vision-server/
  pyproject.toml                 # name = "wsc-vision-server"
  Dockerfile
  nodesets/
    Opc.Ua.MachineVision.NodeSet2.xml     # vendored, Version + Datum im Header dokumentieren
  src/vision_server/
    __main__.py            # argparse: --profile {2d,3d} --port --app-uri --name --scene
    config.py              # VisionServerConfig (frozen dataclass)
    address_space.py       # import_xml + VisionSystem-Instanz aus VisionSystemType
    state_machine.py       # VisionStateMachine + AutomaticModeStateMachine
    result_management.py   # Ergebnisablage, GetResultById, ResultReadyEvent
    events.py              # Event-Generator-Fabriken
    payload.py             # Detection -> JSON
    detection/
      base.py              # DetectionSource (ABC) -> list[Detection]
      simulated_2d.py      # Server 1
      simulated_3d.py      # Server 2
  scenes/
    scene_2d.json          # Ground Truth: welche Module liegen wo
    scene_3d.json
  tests/
```

## 4.2 Adressraum-Aufbau mit asyncua

Reihenfolge ist kritisch:

```python
server = Server()
await server.init()                                     # 1. Core-Adressraum
server.set_endpoint("opc.tcp://0.0.0.0:4841/wsc/vision/2d")
await server.set_application_uri("urn:wsc:vision:2d")   # 2. VOR dem Import: fixiert ns=1
server.set_server_name("WSC Vision 2D")

await server.import_xml("nodesets/Opc.Ua.MachineVision.NodeSet2.xml")   # 3. -> ns=2
mv  = await server.get_namespace_index("http://opcfoundation.org/UA/MachineVision")
wsc = await server.register_namespace("http://odintec.de/UA/WSC/Vision")  # 4. eigener ns

vision_system = await server.nodes.objects.add_object(
    ua.NodeId("VisionSystem2D", wsc),
    ua.QualifiedName("VisionSystem2D", wsc),
    objecttype=ua.NodeId(VISION_SYSTEM_TYPE_ID, mv),
)
```

**Ein Kniff, der dem Frontend viel Arbeit spart:** alle Events zusätzlich am **Server-Objekt**
(`i=2253`, in jedem OPC-UA-Server identisch) sichtbar machen:

```python
await server.nodes.server.add_reference(vision_system, ua.ObjectIds.HasNotifier, forward=True)
await vision_system.set_event_notifier([ua.EventNotifier.SubscribeToEvents])
```

Damit reicht **ein einziger** `subscribeEvent(serverUrl, "i=2253")` für Status *und* Ergebnisse
— keine Discovery, keine Namespace-Index-Auflösung im Frontend.

Für die Zustandsautomaten gibt es `asyncua/common/statemachine.py` (`FiniteStateMachine` mit
`install()`, `add_state()`, `add_transition()`, `change_state()`); `_state_machine_type` lässt
sich auf die 40100-Typen umbiegen. Jedes `change_state()` feuert automatisch ein
Transition-Event.

### Stolpersteine

| # | Problem | Gegenmaßnahme |
| --- | --- | --- |
| 1 | asyncua-Issue [#651](https://github.com/FreeOpcUa/opcua-asyncio/issues/651): `TypeError: object of type 'ExtObj' has no len()` beim MachineVision-Import | In aktuellem asyncua behoben. **Trotzdem als Erstes isoliert verifizieren** (Spike, s. Phase 1). Fallback `strict_mode=False`. |
| 2 | asyncua-Issue [#1693](https://github.com/FreeOpcUa/opcua-asyncio/issues/1693) (offen): `load_data_type_definitions()` scheitert bei 40100 an abstrakten Struktur-Basistypen | Betrifft die **Client**-Seite. Unser Backend ruft das heute nirgends auf — **so lassen**. Der JSON-Payload (4.3) macht es überflüssig. |
| 3 | Namespace-Index-Drift | **Nie** `ns=2;i=…` hardcoden, immer `ua.NodeId(id, mv_idx)`. |
| 4 | `ResultContent` ist `BaseDataType[]` | Variants explizit bauen: `[ua.Variant(payload, ua.VariantType.String)]` — asyncuas Auto-Erkennung für heterogene Listen ist unzuverlässig. |
| 5 | Optional Fields / EncodingMask | Alle Mandatory-Felder befüllen (`ResultId`, `IsPartial`, `ResultState`, `InternalRecipeId`, `InternalConfigurationId`, `JobId`, `CreationTime`). |
| 6 | `import_xml` muss nach `init()` und vor `start()` laufen | Reihenfolge oben einhalten. |

## 4.3 Ergebnis-Payload: JSON-String in `ResultContent[0]`

**Entscheidung — bewusst gegen einen eigenen Structured DataType.**

| Weg | Spec | Client-Robustheit | Aufwand |
| --- | --- | --- | --- |
| Eigener Structured DataType | ★★★ | ✗ Backend bräuchte `load_data_type_definitions()` → Issue #1693; ohne das kommt ein rohes `ExtensionObject` mit Bytes an, und `to_jsonable` macht daraus `str(...)` | hoch |
| **JSON-String** ★ | ★★ (`BaseDataType[]` erlaubt String explizit) | ★★★ — `to_jsonable` reicht Strings unverändert durch | minimal |
| `Double[]` | ★★ | ★★★ | ✗ Modul-ID ist ein String, keine Attribute, keine Konfidenz |

`ResultContent` ist ein **Array**. Wir belegen `[0]` mit dem JSON-String; später kann `[1]` einen
echten Structured DataType tragen, **ohne** die Frontend-Kette anzufassen. Abwärtskompatibler
Ausbaupfad.

**Schema** (`vision-server/src/vision_server/payload.py`, gespiegelt in
`frontend/src/features/vision/model/visionResultEvent.ts`):

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-2d-01",
  "resultId": "res-000123",
  "jobId": "job-42",
  "creationTime": "2026-09-09T10:11:12.345Z",
  "frameId": "world",              // Bezugsrahmen der Posen
  "lengthUnit": "m",               // immer SI-Meter
  "angleUnit": "rad",
  "rotation": "quaternion_xyzw",   // three.js-Reihenfolge, spart eine Konvertierung
  "detections": [
    {
      "moduleId": "MOD-A-042",     // -> Key in die CAD-Registry
      "instanceId": "det-1",       // stabil über Frames -> React key
      "confidence": 0.93,
      "position": [0.42, -0.11, 0.05],
      "orientation": [0.0, 0.0, 0.3827, 0.9239],
      "boundingBox": null,         // nur Server 1
      "attributes": {}             // frei erweiterbar
    }
  ]
}
```

Regeln, die Ärger sparen: **immer SI-Meter und Radiant**, **immer explizit `frameId`**,
**Quaternion `xyzw`**, **`schema`-Version im Payload** (das Frontend kann alte Frames verwerfen
statt zu crashen).

## 4.4 Job-Ablauf

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

    detections = await self.source.acquire_and_detect(parameters)   # 2D bzw. 3D
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

Sichtbare Zustandsfolge: `Preoperational → Halted → Operational/Initialized → Ready →
SingleExecution → Ready`.

## 4.5 Server 1 vs. Server 2

Gemeinsame Basisklasse `VisionSystemServer` (Nodeset-Import, Instanziierung, beide Automaten,
ResultManagement, Events, Job-Schleife) — **sie kennt keine Bildverarbeitung.** Variiert wird
über eine Strategie `DetectionSource` und Capability-Flags im Config-Objekt, **nicht** über
Server-Subklassen.

```python
@dataclass(frozen=True)
class Detection:
    module_id: str
    instance_id: str
    position: tuple[float, float, float]              # Meter, frameId-Frame
    orientation: tuple[float, float, float, float]    # Quaternion xyzw
    confidence: float
    attributes: dict[str, Any]

class DetectionSource(ABC):
    profile_id: str
    @abstractmethod
    async def acquire_and_detect(self, parameters: list[Any]) -> list[Detection]: ...
```

| | Server 1 — 2D-Erkennung | Server 2 — 3D-Posenschätzung |
| --- | --- | --- |
| Rolle | Modul-Identifikation + Lage in der Auflageebene | volle 6D-Posenschätzung |
| Pose-DOF | x, y, yaw; z konstant, roll=pitch=0 | volle 6 DOF |
| Zusatzfelder | `boundingBox`, `classScore` | `fitError`, `inlierCount`, `pointCount` |
| Betriebsarten | nur `StartSingleJob` | zusätzlich `StartContinuous`/`Stop`/`Abort` |
| Partielle Ergebnisse | nein | ja (`IsPartial=True`, dann Refinement) |
| Simulierter Fehler | Rauschen x/y/yaw, gelegentliche Fehlklassifikation | Rauschen auf 6 DOF, 180°-Symmetrie-Verwechslung |
| Latenz | ~50 ms | ~300 ms |

Beide lesen dieselbe `scenes/scene_*.json` (Ground Truth) und verrauschen sie profilspezifisch —
reproduzierbare Demo, triviale Verifikation des CAD-Overlays.

## 4.6 Instanz-Unterscheidung und Betrieb

| Ebene | Server 1 | Server 2 |
| --- | --- | --- |
| Endpoint | `opc.tcp://…:4841/wsc/vision/2d` | `opc.tcp://…:4842/wsc/vision/3d` |
| ApplicationURI | `urn:wsc:vision:2d` | `urn:wsc:vision:3d` |
| ServerName | `WSC Vision 2D` | `WSC Vision 3D` |
| BrowseName | `<wsc>:VisionSystem2D` | `<wsc>:VisionSystem3D` |
| `visionSystemId` im JSON | `vision-2d-01` | `vision-3d-01` |

`docker-compose.yaml` — additiv, der `platform`-Service bleibt unverändert:

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

Zwei Instanzen von Anfang an — so fällt sofort auf, wenn irgendwo eine Vision-ID mit einer
Server-URL verwechselt wird.

---

# Teil 5 — Was im bestehenden System geändert wird

Gestuft. **Stufe 1** macht Vision sichtbar und kostet fast nichts. **Stufe 2** bringt die
Orchestrierung und ist der eigentliche Eingriff.

## 5.1 Stufe 1 — Sichtbarkeit (Backend: 19 Zeilen, Contract-Diff: null)

Nur die Blocker B2 und B3 müssen weg. Beide Fixes sind **rein additiv**.

**B3 — `opcua/method_calls.py`, neue Funktion neben `to_jsonable` (~18 Z.):**

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

`to_jsonable` selbst bleibt **unangetastet** — sonst ändert sich das Payload-Format von
`nodeValueChanged`, `methodResult` und `addressSpaceNodeDetails.value`.

**`opcua/server_connection.py:113` — eine Zeile:**

```python
await self.on_event(event_to_jsonable(event))
```

**B2 — `opcua/server_connection.py:234`, keyword-only mit Default (~8 Z.):**

```python
async def subscribe_events(
    self, *, node_id: str, on_event: EventCallback,
    event_type_node_ids: list[str] | None = None,     # NEU
    publishing_interval_ms: float = 100.0,
) -> None:
    ...
    evtypes = [self.client.get_node(i) for i in event_type_node_ids] if event_type_node_ids else None
    handle = await subscription.subscribe_events(node, evtypes) if evtypes \
             else await subscription.subscribe_events(node)
```

Ohne diesen Parameter selektiert asyncua nur `BaseEventType`-Felder und `ResultContent` fehlt.
Da der Parameter keyword-only mit Default ist, bleiben `SubscribeEventCommand` und **alle
bestehenden Tests unverändert**.

> Die beiden Fixes greifen ineinander: B2 sorgt dafür, dass das Feld überhaupt übertragen wird,
> B3 dafür, dass es nicht auf dem Weg ins Frontend zu einem String zerfällt. Einer allein
> genügt nicht.

`OpcUaEventNotificationEvent.event` ist bereits `Any` (Backend) bzw. `unknown` (Frontend) —
**der Vertrag muss nicht erweitert werden.**

## 5.2 Stufe 1 — Frontend (3 neue Dateien, 9 geänderte Zeilen)

**Neu**, alles reine Logik bzw. isolierte Komponenten:

| Datei | Inhalt |
| --- | --- |
| `features/vision/model/visionResultEvent.ts` | `parseVisionResultEvent`, `selectLatestVisionFrames`, `selectVisionStatus` — defensiv, `null` bei allem Unerwarteten |
| `features/vision/model/poseTransform.ts` | Z-up → Y-up (siehe 7.2) |
| `features/vision/model/moduleModels.ts` | Modul-ID → CAD-Asset-Registry |
| `features/vision/components/VisionStatusCard.tsx` | Statusbadge, abonniert `i=2253` |
| `features/viewport/components/DetectedModules.tsx` | CAD-Overlay |

**Geändert:**

| Datei | Änderung | Zeilen |
| --- | --- | --- |
| `features/opcua-server/components/ServerManager.tsx:59` | `<VisionStatusCard serverUrl={server.serverUrl} />` nach `<RobotManager …/>` | +1 |
| `features/viewport/components/Viewport.tsx` | Import (:48), `detections?` in `ViewportProps` (:50), `<DetectedModules …/>` nach dem Robot-Map-Block (:1354) | +6 |
| `app/layout/DesktopLayout.tsx:98` | `detections={visionDetections}` an `<Viewport>` | +2 |
| `entities/server/model/store.ts:655` | Event-Cap (siehe unten) | +2 |

`VisionStatusCard` rendert `null`, wenn nie ein Vision-Event kam — **Roboter-Server sehen
unverändert aus, kein `isVisionServer`-Flag nötig.**

**Latenter Bug, der dabei mitgenommen werden sollte:** `entities/server/model/store.ts:655-666`
hängt jedes `opcuaEvent` **unbegrenzt** an `state.opcuaEvents` an; geleert wird nur bei
`serverDisconnected`. Bei Server 2 im Continuous-Modus wächst das Array unbeschränkt.

```ts
const MAX_OPCUA_EVENTS = 500;
opcuaEvents: [...state.opcuaEvents, { … }].slice(-MAX_OPCUA_EVENTS),
```

Der bestehende Test `store.test.ts:218-228` prüft nur ein Event und bleibt grün.

## 5.3 Stufe 2 — Typisierte Discovery und Orchestrierung

Erst hier lohnt sich die Investition, weil das Backend jetzt selbst wissen muss, *welchen*
Knoten es abonniert und *welchem* Roboter es das Ergebnis schickt.

**Geändert (Summe ≈ 60 Zeilen, alle additiv):**

| Datei | Änderung |
| --- | --- |
| `models/server.py:15` | `is_vision_server: bool = False`, `vision_system_ids: list[str] = []` |
| `opcua/discovery.py:24` | `ServerDiscoveryResult.vision_systems: list[VisionSystemSessionInfo] = []` |
| `opcua/asyncua_discovery.py:740` | in `discover_connected_server` die Vision-Discovery aufrufen (**funktionslokaler Import**, sonst Zirkelbezug) |
| `runtime/server_session.py` | `vision_by_id` + Durchreichen in `to_info()` |
| `services/runtime_registry.py` | `_vision_by_id`, `get_vision_system`, `replace_server_vision_systems` |
| `runtime/application_service.py:85` | `register_discovery_result` registriert Vision-Sessions |
| `runtime/application_service.py:245` | **genau ein** neuer `isinstance`-Block |
| `websocket/router.py` | `finally: registry.event_bus.unregister(emit_event)` |
| `pyproject.toml` | `markers = ["integration: …"]`, `addopts = "-m 'not integration'"` |

Der Trick gegen die 785-Zeilen-`if`-Kette: **keine fünf neuen Blöcke**, sondern ein Union-Guard:

```python
if isinstance(message, VisionClientCommand):     # Union-Typ, Python 3.10+
    from .vision_service import handle_vision_message
    return await handle_vision_message(message, registry=registry,
                                       connection_factory=connection_factory,
                                       emit_event=emit_event)
```

Damit bleibt `application_service.py` faktisch unangetastet und die Vision-Kommandos sind
isoliert testbar — dasselbe Muster wie das bereits ausgelagerte `action_execution.py`.

**Neu (≈ 900 LOC inkl. Tests):** `models/vision.py`, `opcua/vision_discovery.py`,
`runtime/vision_session.py`, `runtime/vision_service.py`, `runtime/vision_orchestration.py`,
`geometry/pose_transforms.py`, `services/event_bus.py` + fünf Testmodule.

### Erkennungsstrategie

Die bestehende Typprüfung ist exakter Stringvergleich (`:213`/`:236`/`:248`) — für
`VisionSystemType` (Subtyp von `BaseObjectType`, nicht im DeviceSet) untauglich. Dreistufig:

```python
MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"

async def discover_vision_system_nodes(*, client, namespace_uris) -> list[Node]:
    mv_index = namespace_index(namespace_uris, MACHINE_VISION_NAMESPACE_URI)
    if mv_index is None:                                  # (1) Gate: kostenlos
        return []
    type_node = await find_vision_system_type_node(client, mv_index)   # per BrowseName
    accepted = await collect_type_and_subtype_ids(client, type_node) if type_node else set()

    found = []
    async for node in iter_descendants_limited(client.nodes.objects, max_depth=3):
        type_id = await read_type_definition_id(node)
        if type_id is not None and type_id in accepted:   # (2) subtypbewusst über HasSubtype
            found.append(node)
        elif not accepted and await child_by_name(node, "VisionStateMachine") is not None:
            found.append(node)                            # (3) Ententyp-Fallback
    return found
```

`VisionStateMachine` ist nach 40100 **mandatory** und damit ein zuverlässiger Marker.
`collect_type_and_subtype_ids` ist bewusst generisch — damit ließe sich später auch der
Robotics-Pfad subtypbewusst machen (nicht Teil dieses Vorhabens).

Wiederverwendet werden alle vorhandenen Helfer: `child_by_name`, `read_display_name`,
`read_browse_name`, `read_type_definition_id`, `iter_descendants_limited`,
`build_method_binding`, `discover_named_variable_bindings`, `discover_method_bindings`,
`discover_skill_bindings`, `normalize_capability_name`, `namespace_index`, `merge_bindings`.

> **Verworfen:** den Vision-Server als DI-Device mit `MotionDeviceType` zu tarnen, damit die
> vorhandene Robot-Discovery greift. Das wäre spec-widrig (40100 leitet bewusst von
> `BaseObjectType` ab), und das Frontend würde die Kamera als Roboter behandeln — URDF-Laden,
> Joint-Runtime, IK-Panel. Die Eingriffe würden nur verlagert, nicht vermieden.

> **Optionaler Bonus, keine Krücke:** Unsere Server dürfen **zusätzlich** ein Skill-Objekt
> `GrabPose` mit `ParameterSet`/`ResultSet`/`CurrentState`/`Start`/`Halt`/`Reset` führen. Das
> verletzt 40100 nicht (Zusatzobjekte sind erlaubt) und entspricht dem VDMA-Skill-Pattern, das
> dieses Backend ohnehin spricht — die vorhandene Ausführungsmaschinerie wäre sofort nutzbar.

### Neue Message-Typen (Stufe 2)

Commands: `discoverVisionSystems`, `startVisionJob`, `stopVisionJob`, `bindVisionToRobot`,
`unbindVisionFromRobot`. Events: `visionSystemsDiscovered`, `visionResult`, `visionDispatch`.

**Frontend-Diff zwingend: 0 Zeilen.** `handleServerMessage`
(`applicationController.ts:737`) ist eine `if`-Kette ohne `default`-Fehlerpfad; unbekannte
`type`-Werte fallen durch und landen höchstens im `MessageLog`.

---

# Teil 6 — Der Signalweg Vision → Robotersteuerung

## 6.1 Kette

```
Vision-Server            Backend                                    Robotics-Server
─────────────            ───────                                    ───────────────
StartSingleJob
  │
  ├─ AcquisitionDoneEvent
  │
  └─ ResultReadyEvent ──► subscribe_events(node, [ResultReadyEventType])
                            │  (B2: Event-Typ MUSS mitgegeben werden)
                            ├─ event_to_jsonable        (B3)
                            ├─ ResultContent[0] -> JSON.parse
                            ├─ Dedupe / in-flight-Gate
                            ├─ transform_pose(cam -> robot_base)
                            ├─ VisionResultEvent ──────► Frontend (CAD-Overlay)
                            └─ execute_robot_action(...) ──────────► Skill-Start
                                                                     + Zustandswatch
```

Wohnort: **`backend/src/backend/runtime/vision_orchestration.py`** — `runtime/` ist bereits die
Schicht für „langlebiger Zustand + Nebenläufigkeit" (`surface_job.py`, `robot_session.py`), und
der Orchestrator ist explizit *nicht* an eine WS-Nachricht gebunden.

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

## 6.2 Hand-Auge-Transformation

`backend/src/backend/geometry/pose_transforms.py` — `numpy` und `scipy` sind **bereits**
Dependencies, keine neue Abhängigkeit:

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

Kalibrierung, in dieser Präzedenz:
1. `bindVisionToRobot.calibration` (Laufzeit, gewinnt) — testbar, kein Deployment
2. JSON-Datei, Pfad aus ENV `WSC_VISION_CONFIG` (Default `./config/vision.json`)
3. keine → `auto_execute` bleibt zwangsweise `False`

Kein Config-Framework bauen: das Repo kennt heute genau ein `os.getenv("HOST")` (`app.py:24`).
Eine Datei plus eine ENV-Variable ist das passende Maß.

## 6.3 Offener Punkt: wie die Pose zur Bewegung wird (B4)

Das Backend kann **keine** 6D-Pose an `goto` übergeben. Drei Wege, als `dispatch_mode`
konfigurierbar — **welcher es wird, ist noch zu entscheiden**:

| Modus | Wie | Voraussetzung | Aufwand |
| --- | --- | --- | --- |
| **`cartesian`** (bevorzugt) | `execute_robot_action(robot, "linMoveTcp", inputs={…pose…})` | Der 40010-Server exponiert `lin_move_tcp` | **0** — bereits in `discovery.py:99` gemappt |
| `frontendIk` | Backend publiziert nur `visionDispatch` mit `pose_base`; das Frontend löst IK und ruft `callRobotGotoForVisualAngles` | Browser muss offen sein → **kein autonomer Zellenbetrieb** | ~40 Z. Frontend |
| `backendIk` | `ikpy`/`pinocchio` + URDF-Parsing im Backend | neue Dependency, Kinematik-Validierung | hoch — für v1 abgelehnt |

**Prüfschritt vor Umsetzungsbeginn:** Am realen Robotics-Server nachsehen, ob `LinMoveTcp` /
`RotMoveTcp` existieren. Geht ohne Code — entweder über den vorhandenen Address-Space-Browser
im Frontend oder über die `discovered robot … skillNames=`-Zeile im Backend-Log
(`application_service.py:102`).

## 6.4 Race-Conditions, Sicherheit

Fünf gestaffelte Mechanismen:

1. **Dedupe nach `resultId`** — `deque(maxlen=256)`. asyncua kann bei Re-Subscription dasselbe
   Event erneut liefern.
2. **`creationTime`-Monotonie** — ältere Ergebnisse verwerfen (Out-of-Order nach Reconnect).
3. **`in_flight`-Gate + `asyncio.Lock` pro Link.** Freigabe **nicht** im `finally` des Dispatch,
   sondern erst wenn der Zustandswatch `idle` meldet — `execute_robot_action` kehrt bei Skills
   mit `status="running"` zurück (`action_execution.py:212`), die Bewegung läuft dann noch.
   Spiegelt exakt `isGotoReadyForDispatch` im Frontend (`applicationController.ts:591`).
4. **`auto_execute=False` als Default** — bestätigt: Stufe 2 wird als **Dry-Run** ausgeliefert.
   Es wird nur der *berechnete* Zielwert publiziert, der Roboter bewegt sich nicht.
   Scharfschalten ist ein expliziter, separater Schritt.
5. **Single-Writer** — nur der Orchestrator schreibt in `link.*`.

**Event-Ownership** — der eine Punkt, an dem eine Strukturänderung unvermeidlich ist: Der
`emit_event`-Closure in `websocket/router.py` gehört zu *einer* WS-Verbindung, der Orchestrator
lebt länger. Fällt der Client weg, wirft `websocket.send_text` in einem Background-Task und
asyncio schluckt die Exception still. Lösung: `services/event_bus.py` (~45 Z.) mit
Emitter-Fanout und `finally: unregister` im Router (+8 Z.).

**Jeder** `_on_result_ready`-Pfad endet in `except Exception → publish(ErrorEvent)` — niemals
roh raisen, sonst stirbt der Task lautlos.

**Result-Handles**: `GetResultById` immer mit `try/finally: ReleaseResultHandle`, sonst leckt
der Vision-Server Handles.

---

# Teil 7 — Frontend: Status und CAD-Overlay

## 7.1 Status: eine Karte, keine neue Entity

| Weg | Diff | Urteil |
| --- | --- | --- |
| W1 — rein generisch, manuell im Address-Space-Browser | 0 Z. | Funktioniert heute schon. Als Debug-Pfad wertvoll, als Feature zu wenig. |
| **W2 — Statuskarte ohne neue Entity** ★ | 1 neue Datei (~130 Z.) + 1 Zeile | **Empfohlen** |
| W3 — eigene Vision-Entity im Store | ~450 Z. über 8 Kern-Dateien, inkl. `ApplicationSnapshot` → Kaskade über `App.tsx:33` und alle Contexts | Widerspricht „minimale Eingriffe" |

Da unsere Server alle Events zum Server-Objekt propagieren (4.2), genügt **ein** Aufruf:

```tsx
useEffect(() => {
  subscribeEvent(serverUrl, "i=2253");
  return () => { unsubscribeEvent(serverUrl, "i=2253"); };
}, [serverUrl, subscribeEvent, unsubscribeEvent]);
```

→ liefert **sowohl** die State-Machine-Transition-Events (Status) **als auch** die
`ResultReadyEvent`s (Posen). Kein Browse-Roundtrip, kein neues Kommando.

> Anmerkung zum Bestand: `VisionStateMachine.CurrentState` ist eine **Variable**, kein Event.
> Wer den Zustand direkt lesen will, nimmt `subscribeNode` (push-basiert, existiert) —
> **nicht** das 250-ms-Polling aus `_watch_skill_action_state`. Das ist eine Altlast, keine
> Vorlage.

## 7.2 CAD-Overlay

**Format: glTF 2.0 binary (`.glb`).** `useGLTF` aus drei ist vorhanden (cached pro URL,
`<Clone>` für mehrere Instanzen), glTF trägt Materialien — und die Szene nutzt `<Environment>`
mit HDRI (`Viewport.tsx:1310`), wo PBR-Material korrekt aussieht und ein grauer STL-Klotz nicht.
**Kein neues npm-Paket.** STEP wird **offline** konvertiert (FreeCAD headless / Blender),
niemals zur Laufzeit; beim Export zwingend **Meter**, **Ursprung im Modul-Bezugspunkt**
(nicht Bounding-Box-Mitte), **+Z up**.

**Ablage: `frontend/public/cad/modules/<moduleId>.glb`** — bewusst **nicht** unter
`public/urdf/`, das ist ein Git-Submodule. Vite kopiert `public/` nach `dist/`, das Dockerfile
kopiert `dist` nach `/app/www`, `app.py:19` mountet es auf `/`. `/cad/modules/x.glb` ist damit
in Dev **und** Docker ohne Infra-Änderung erreichbar.

**Registry** analog `robotModels.ts:198` `resolveRobotModelFromIdentity` — exakter Match zuerst,
dann Fuzzy-Fallback:

```ts
export interface ModuleModelConfig {
  id: string; label: string; url: string;
  format?: "glb" | "stl";
  scale?: number;                                        // Quell-Einheit -> m
  modelRotation?: { x: number; y: number; z: number };   // CAD-Frame -> Modul-Frame
  fallbackColor?: string;
}
```

**Koordinatensystem — der kritische Punkt.** Die Szene ist **Y-up**
(`Viewport.tsx:1300` `up: [0,1,0]`, `gridHelper` in XZ), Robotik ist **Z-up**. Der Bestand macht
die Konvertierung **nicht zentral**, sondern pro Modell an zwei verschiedenen Stellen:
`robot.visual.origin.roll = -π/2` für FR3 (`robotModels.ts:24-32`, wirkt in `Viewport.tsx:1168`)
bzw. `mountRotation.x = -π/2` für eva/ur5e (`Viewport.tsx:1173`). **Daran nicht orientieren** —
für Vision eine explizite, getestete Zentralfunktion:

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

Konsistent mit der eva/ur5e-Konvention und mit drei Achsen-Einheitsvektoren in Vitest testbar —
genau das Muster von `axisMapping.test.ts`.

**Komponente** `features/viewport/components/DetectedModules.tsx`. Die Trennung
`DetectedModule` / `ModuleMesh` ist notwendig, weil `useGLTF` nicht bedingt aufgerufen werden
darf. Ein `<PlaceholderBox>` (Drahtgitter) macht unbekannte Modul-IDs sofort sichtbar, statt sie
still zu verschlucken.

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

`Viewport.tsx:1225` (`robots.filter(...)`) bleibt **unangetastet** — Module sind bewusst *keine*
`Robot`-Instanzen; sie in den Robot-Store zu drücken würde `jointRuntime`, IK, `DragControls`
und `JointAnglesPanel` mitziehen.

Bezugsrahmen: MVP `frameId: "world"` = Szenenursprung, plus **eine** konfigurierbare Konstante
`VISION_OVERLAY_ORIGIN` (`{x,y,z,roll,pitch,yaw}`, analog `robot.visual.origin`) als äußere
`<group>`, damit der Sensor einmalig eingemessen werden kann. Kein TF-Baum.

## 7.3 Posen ins Frontend: Huckepack auf `opcuaEvent`

| | Huckepack `opcuaEvent` | Neuer `visionResult`-Event |
| --- | --- | --- |
| `models/messages.py` | **0** | +1 Klasse, +1 Union-Eintrag |
| `shared/api/messages.ts` | **0** | +1 Variante |
| `websocketClient.ts` | **0** | +1 Wrapper |
| `applicationController.ts` | **0** | Routing + `ApplicationSnapshot` → Kaskade über `App.tsx:33` und alle Contexts |
| `entities/*/store.ts` | **0** | neuer Store + Tests |
| `application_service.py` | **0** | +Handler-Block |
| `server_connection.py` | 1 Zeile (B3-Fix, ohnehin fällig) | +Subscription-Variante |

`OpcUaEventNotificationEvent.event` ist bereits `Any`/`unknown` — **der Vertrag muss nicht
erweitert werden.** Für Stufe 1 also Huckepack; ein typisierter `visionResult` lohnt erst mit
Stufe 2, wenn das Backend ohnehin Vision-Sessions kennt.

---

# Teil 8 — Risiken

| # | Risiko | Schwere | Gegenmaßnahme |
| --- | --- | --- | --- |
| R1 | B2 — Custom-Event-Felder kommen nie an; Symptom „läuft, aber Posen leer" | **hoch** | 5.1 + Integrationstest gegen den eigenen Server |
| R2 | B3 — `opcuaEvent` ist heute ein `repr`-String | mittel | `event_to_jsonable`; `to_jsonable` **nicht** anfassen |
| R3 | B4 — keine IK im Backend | **hoch**, kann die Architektur kippen | 6.3, vor Umsetzungsbeginn klären |
| R4 | B0 — Suite ist rot, keine Baseline | blockierend, trivial | Phase 0 |
| R5 | Zirkelimport `asyncua_discovery` ↔ `vision_discovery` | niedrig | funktionslokaler Import |
| R6 | Discovery-Cache ohne Invalidierung — ein später erscheinendes Vision-System wird nie gefunden | mittel | `discoverVisionSystems` mit `use_cache=False` |
| R7 | Unbehandelte Exception im Background-Task verschwindet still | mittel | jeder Pfad endet in `except → publish(ErrorEvent)` |
| R8 | Orchestrator sendet auf tote WS-Verbindung | mittel | `EventBus` + `finally`-Unregister |
| R9 | **Kein Reconnect/Watchdog** — Subscriptions sterben beim Abriss lautlos | **hoch im Betrieb** | out of scope für v1, **vor Zellenbetrieb zwingend** |
| R10 | Backend wird zur Bewegungsquelle ohne Interlock | **hoch** | `auto_execute=False`, Dry-Run, expliziter Arm-Schritt |
| R11 | Einheiten/Konventionen: mm↔m, xyzw↔wxyz, intrinsisch↔extrinsisch, `T_base_cam`↔`T_cam_base` | **hoch**, klassischer stiller Fehler | `frameId` an jeder Pose, `translation_scale` explizit, Golden-Value-Tests |
| R12 | Result-Handle-Leck | mittel | `try/finally`, Test |
| R13 | asyncua-Issues #651 / #1693 beim Nodeset-Import | mittel | Spike als Phase 1; JSON-Payload umgeht #1693 |
| R14 | Unbegrenztes `opcuaEvents`-Array im Frontend | mittel | Event-Cap (5.2) |
| R15 | Drei anonyme, unverschlüsselte OPC-UA-Verbindungen | mittel | Status quo des Projekts, dokumentieren |

---

# Teil 9 — Reihenfolge

| Phase | Inhalt | Fertig, wenn |
| --- | --- | --- |
| **0** | `FakeConnection.write_node_value`-Fix; pytest-Marker | `uv run pytest` grün |
| **1** | **Spike (blockierend, ½ Tag):** Nodeset vendoren, 15-Zeilen-Skript `init()` → `import_xml()` → `ua.ResultDataType` existiert, Encode/Decode-Roundtrip läuft | asyncua-Issues #651/#1693 ausgeschlossen |
| **2** | `vision-server/` Grundgerüst: Config, Adressraum, VisionSystem-Instanz, `HasNotifier` → Server-Objekt | im vorhandenen `AddressSpaceTree` browsbar |
| **3** | Zustandsautomaten + `StartSingleJob` + ResultManagement + `ResultReadyEvent` mit JSON-Payload; **Server 1 fertig** | `callRawMethod` aus dem Frontend löst einen Job aus |
| **4** | Backend B2+B3-Fix (19 Z.) | Events erscheinen **strukturiert** im `MessageLog` |
| **5** | `features/vision/model/{visionResultEvent,poseTransform,moduleModels}.ts` + Vitest | reine Logik grün |
| **6** | `VisionStatusCard` + 1 Zeile `ServerManager.tsx` | Statusbadge in der Server-Karte |
| **7** | Ein `.glb` nach `public/cad/modules/`, `DetectedModules.tsx`, 6 Z. `Viewport.tsx`, 2 Z. `DesktopLayout.tsx`, Event-Cap | **Modul erscheint an der richtigen Stelle im Viewport** |
| **8** | Server 2 (`simulated_3d`, Continuous), docker-compose-Services | zwei Vision-Server parallel |
| **9** | Stufe 2: `models/vision.py`, `vision_discovery.py`, Registry, `discoverVisionSystems` | typisierte Vision-Sessions über `/ws` |
| **10** | `pose_transforms.py` + `HandEyeCalibration` + `bindVisionToRobot` im **Dry-Run** | `visionDispatch status="proposed"` mit korrekter Pose |
| **11** | `EventBus` + Lifecycle-Cleanup im Router | Orchestrator überlebt Client-Reload |
| **12** | Dispatch-Modus entschieden (6.3), `auto_execute=True` | Zellenversuch |

Phase 0–8 ändern **keine** bestehende Verhaltenslogik — nur die zwei additiven Fixes, ein
keyword-only-Default und ein Event-Cap. Das ist die konkrete Einlösung von „minimale Eingriffe".

---

# Teil 10 — Verifikation

**Drei Teststufen, aufsteigende Kosten:**

1. **Unit ohne OPC UA** (Hauptlast) — über die bestehende `connection_factory=`-Naht
   (`application_service.py:63`, Muster in `tests/test_application_service.py:134`).
   Eine `FakeVisionConnection` akzeptiert `subscribe_events(..., event_type_node_ids=..., on_event=...)`
   und ruft den Callback mit einem handgebauten Event auf.
   Fälle: Extraktion → Transformation → korrekte `execute_robot_action`-Inputs · Dedupe
   (zweimal dieselbe `resultId` → ein Dispatch) · `in_flight`-Gate · `auto_execute=False` →
   nur Event · Roboter fehlt → `ErrorEvent` statt Exception · `ReleaseResultHandle` auch bei
   Extraktionsfehler.
2. **Reine Funktionen** — `test_pose_transforms.py` (Identität, 90°-Drehungen um alle Achsen,
   mm→m, Quaternion-Roundtrip, nicht-orthonormale Rotation → `ValueError`,
   `numpy.testing.assert_allclose`); `test_vision_discovery.py` (Namespace-Gate,
   Subtyp-Sammlung, Ententyp-Fallback). Frontend: `poseTransform.test.ts`,
   `visionResultEvent.test.ts` — passt in `environment: 'node'`.
3. **Echter Server**, `@pytest.mark.integration`, in-process auf freiem Port. Testet genau das,
   was Fakes nicht können: dass `discover_vision_system_nodes` die Instanz findet und dass
   `subscribe_events` mit Event-Typ das `ResultContent` tatsächlich liefert.

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
markers = ["integration: requires a live OPC UA endpoint"]
addopts = "-m 'not integration'"
```

→ `uv run pytest` bleibt schnell und offline; `uv run pytest -m integration` fährt die volle
Kette.

**End-to-End von Hand:**

```bash
# 1. Vision-Server starten
uv run --directory vision-server wsc-vision-server --profile 2d --port 4841 \
       --app-uri urn:wsc:vision:2d --name "WSC Vision 2D"
# 2. Backend + Frontend
cd backend && uv run main.py
cd frontend && npm run dev
```

1. Im Frontend `opc.tcp://127.0.0.1:4841/wsc/vision/2d` verbinden → Server-Karte erscheint,
   Roboterliste bleibt leer (erwartet).
2. Im Address-Space-Browser zu `Objects/VisionSystem2D` navigieren, `StartSingleJob` über
   `RawMethodCallModal` aufrufen.
3. Im `MessageLog` müssen `opcuaEvent`-Einträge als **Objekt** erscheinen (nicht als
   `repr`-String) und ein `ResultContent` mit gültigem JSON tragen.
4. Die `VisionStatusCard` zeigt `Ready → SingleExecution → Ready`.
5. Das Modul erscheint als CAD-Körper im Viewport an der Pose aus `scenes/scene_2d.json` —
   Ground Truth und Darstellung müssen übereinstimmen.
6. Zweiten Server auf 4842 dazunehmen: beide Karten unabhängig, keine ID-Kollision.

---

# Offene Punkte

1. **Dispatch-Modus (6.3)** — bewusst offen gelassen. Prüfschritt: exponiert der reale
   40010-Server `LinMoveTcp`/`RotMoveTcp`? Danach entscheiden zwischen `cartesian`
   (0 Zusatzaufwand, autonom) und `frontendIk` (nutzt vorhandene IK, braucht offenen Browser).
2. **Hand-Auge-Kalibrierung** — Wie und wann werden die beiden Kameras zur Roboterbasis
   eingemessen? Bis dahin bleibt `auto_execute` zwangsweise `False`.
3. **Reconnect/Watchdog (R9)** — für v1 out of scope, aber vor produktivem Zellenbetrieb
   zwingend nachzuziehen.
