# Part 10 program and mDNS — interface for backend and frontend

This file describes **two new capabilities** of the vision server and what backend and frontend have to do for them. It is usable
without knowledge of this repo.

State: 2026-09-21, all details verified against a running server (asyncua 2.0.1, Python 3.12).

> **Nothing breaks.** Both are **additive**. The existing OPC 40100 interface from [`vision-server-interface.md`](vision-server-interface.md) —
> `StartSingleJob`, `Stop`, the 40100 events, `LatestResultJson`, the payload schema `wsc.vision.detections/1` — remains valid unchanged and
> keeps working. A backend that runs today keeps running unchanged after this update.

---

## 1. Why this was built

Until now the vision server has been the special case on the network: to operate it, a client must understand OPC 40100 (Machine Vision) —
two nested state machines, `StartSingleJob` with five typed arguments, a project-specific error code table. This knowledge cannot be reused
for any other module in the cell.

The other modules, by contrast, use **OPC UA Part 10 "Programs"**. Checked on 2026-09-21 in the lab subnet:

| Module | Endpoint | Programs |
| --- | --- | --- |
| Conveyor | `opc.tcp://10.10.38.41:4840/conveyor/` | `RunContinuous`, `MoveDistance`, `MoveUntilSensor` — all of type `ProgramStateMachineType` |
| CardDispenser | `opc.tcp://10.10.38.40:4840/card-dispenser/` | (DI/Machinery, no programs in the checked area) |

The vision server therefore **additionally** gets a Part 10 program in the same form. A frontend that can operate the conveyor skills can thus
also start and stop the vision and display its state, without new code.

**What becomes generic:** start, halt, reset, state display, button release, state-change events.
**What stays vision-specific:** the detection result (poses, tag IDs, reference frames) and the camera live stream. Part 10 says nothing about
results; for those, the route from `vision-server-interface.md` remains authoritative.

---

## 2. Connection: mDNS instead of a hard-coded IP

The cell server now announces itself on the local network. The hard-coded Pi addresses (`10.10.38.104` / `.109` in
`backend/src/backend/config/pi_relay.py` and `frontend/src/features/opcua-server/config/piServers.ts`, see legacy item B1) are therefore
no longer needed.

| | Value |
| --- | --- |
| Service type | `_opcua-tcp._tcp.local.` |
| Instance name | the vision identity of the Pi, e.g. `vision-ceiling-01`, `vision-flange-01` |
| Hostname | `<instanzname>.local.` |
| Port | `4840` |
| TXT `path` | `/raspi/server/` |
| TXT `caps` | `DA` |

**Assembling the endpoint URL:** `opc.tcp://<adresse>:<port><path>` — i.e. `opc.tcp://10.10.38.104:4840/raspi/server/`. **Read the path from
the TXT entry**, do not assume it; the neighbours use other paths (`/conveyor/`, `/card-dispenser/`).

This is what a search on the network looks like (really measured on 21.09.2026; the Pi addresses come via DHCP and change, so always read the
entry instead of copying the IP):

```
vision-ceiling-01._opcua-tcp._tcp.local.   -> opc.tcp://10.10.38.104:4840/raspi/server/   caps=DA
vision-flange-01._opcua-tcp._tcp.local.    -> opc.tcp://10.10.38.109:4840/raspi/server/   caps=DA
Conveyor-Conveyor._opcua-tcp._tcp.local.   -> opc.tcp://10.10.38.41:4840/conveyor/        caps=DA
CardDispenser-Black-CDBlack._opcua-tcp._tcp.local. -> opc.tcp://10.10.38.40:4840/card-dispenser/  caps=DA
```

### The aggregation server does *not* find us by itself

> **Correction of 21.09.2026.** This section used to say that the aggregation server searches mDNS itself and that `RegisterServer2` is not
> needed — the same claim appears in `betreuer/OPC UA-mDNS-Kurzanleitung.md`. That is not true and cost an afternoon. The measurement is below.

The cell operates an `AggregationServer` under `opc.tcp://10.10.38.27:48400/` (`roboteach.plcm.tu-darmstadt.de/agg-server`), which collects
the modules. On the **same machine**, an **open62541 Local Discovery Server (LDS)** runs under `opc.tcp://10.10.38.27:4840/`.

The aggregation server lists exactly the modules that are registered in the LDS — and one gets into the LDS only through an active
**`RegisterServer`** call (`RegisterServer2` does so too). An mDNS announcement alone does not get entered there.

**How this was measured (21.09.2026):**

- Both Pis were cleanly visible via mDNS and directly reachable via OPC UA, with the correct ApplicationUri — the aggregation server nevertheless
  did not list them.
- `FindServers` at the LDS returned exactly the modules that the aggregation server showed under `Objects`.
- The aggregation server restarted at 15:56 and, on the fresh scan, took up all five registered modules. Our Pis had been running and
  transmitting for ten minutes at that point — they were left out. So it is not down to the scan time.
- The names in the LDS inventory (`Festo Conveyor OPC UA Server-10-10-38-41`, `OJIES-Aggregation-LDS-reuther`) do **not appear anywhere on the
  wire** in a 15 s Avahi search: that is its registration inventory, not its mDNS reception.
- Cross-check: a single `RegisterServer2` request for `urn:plcm:camera-server:ceiling-01` — **31 seconds later** the module, together with all
  nine namespaces, stood in the aggregation server. After deregistering it was gone again.

**Implemented in `src/vision_server/discovery/lds.py`**, called from `src/vision_server/server.py` alongside the mDNS announcement. Both paths
remain side by side: the LDS brings us into the aggregation server, mDNS serves clients in the subnet directly.

| | Value |
| --- | --- |
| Discovery server | `opc.tcp://10.10.38.27:4840/` |
| Service | `RegisterServer`, **without** `MdnsDiscoveryConfiguration` |
| Connection | sessionless, `NoSecurity` |
| Renewal | every 60 s — **required**, see below |
| Registered DiscoveryUrl | `opc.tcp://<LAN-IPv4>:4840/raspi/server/` |
| Disable / redirect | `OPCUA_LDS_URL=""` resp. `OPCUA_LDS_URL=opc.tcp://host:4840/` |

**Registering once is not enough.** This is not a precaution but a measured fact: the LDS was restarted on 21.09.2026 at 15:56
(`LastCounterResetTime` jumps along). Conveyor has been running since 08.09. and CardDispenser since 07.09. — both have **not** restarted, and
both are back in the registration inventory after the LDS restart. A registration that was sent only once at one's own start lived in the old
LDS process and would be gone. Therefore the neighbouring modules renew periodically.

You cannot see this in their code — and in ours no longer either: `asyncua.Server.register_to_discovery()` starts the renewal loop itself,
default interval **60 s** (`period`). On each pass it builds a fresh channel, so it survives an LDS restart too. Whoever uses the method gets
this for free and considers it non-existent.

What it does **not** do: deregister. `Server.stop()` only aborts the loop and disconnects. Without `unregister_from_discovery()` the entry would
stay until it expires, and the aggregation server would show a module it can no longer reach. `discovery.lds.register()` calls it in the
`finally`.

How long a registration survives without renewal we have **not** measured — open62541 removes old entries after its own timeout. The
widespread statement "at least every 10 minutes" comes from the docstring of `asyncua`, not from a measurement on this cell.

**Trap 1:** `register_to_discovery()` enters `server.endpoint.geturl()` as the DiscoveryUrl. An endpoint on `0.0.0.0` — obvious, so that the
server is reachable via every interface — thus reports `0.0.0.0` to the aggregation server, and it connects into the void.

Both at once works with **`Server.socket_address`**: the endpoint names the LAN IPv4, while listening continues on `0.0.0.0`. The attribute is
meant exactly for this ("used when the IP address of the network interface is different from the endpoint IP offered to the client during
discovery"). `server.py` sets both; the URL is built by `discovery.lds.advertised_endpoint()`.

Verified on 21.09.2026 locally with the real `server.py`: the log reports `Server startet auf opc.tcp://10.10.38.110:4840/raspi/server/`
(German log message: "server starts on") and `Listening on 0.0.0.0:4840`, reachable via `127.0.0.1` **and** the LAN IP. This matters because
`print_setpoint.py` and the handshake client (`tools/handshake_client.py`) on the Pi go via `127.0.0.1`.

**Trap 2 — observed once, not reproduced:** when registering via `RegisterServer2` with `MdnsDiscoveryConfiguration`, the DiscoveryUrl
`opc.tcp://10.10.38.104.local:4840/…` once appeared in the LDS's `FindServersOnNetwork` — a `.local` appended to an IP, which does not resolve.
A later registration by the same code path (`flange-01`), however, produced a clean entry; the effect is therefore **not deterministic**;
probably a timing artefact when the LDS resolves the mDNS name. The aggregation server is not affected by it anyway, since it takes the
registered URL. We nevertheless register with the plain `RegisterServer` — not because the error is proven, but because this is exactly the
configuration with which Conveyor, CardDispenser and the robots demonstrably run. One variable fewer.

There a module appears under its **ApplicationUri**, not under its mDNS name. Existing entries and ours:

| Module | ApplicationUri |
| --- | --- |
| UR5e | `urn:plcm:robot-server:ur5e` |
| CardDispenser | `urn:smart-business-card-factory:card-dispenser-system` |
| Conveyor | `urn:smart-business-card-factory:conveyor-system` |
| **Vision ceiling** | **`urn:plcm:camera-server:ceiling-01`** |
| **Vision robot hand** | **`urn:plcm:camera-server:roboter-hand-01`** |

The aggregation server appends the ApplicationUri to every namespace of the module (e.g. `http://launch-rm.de/vision/urn:plcm:camera-server:ceiling-01`)
and places the module as an object under `Objects` in its own ns=1. Anyone who accesses our nodes via the aggregation server therefore resolves
the namespace via the **composite** URI — directly on the Pi it remains `http://launch-rm.de/vision`.

To note:

- **No fixed IP needed.** The address is determined at start; a DHCP reservation in the router is enough. If the lease changes during
  operation, the announcement points into the void until the service is restarted.
- **mDNS ends at the subnet boundary** (UDP multicast `224.0.0.251:5353`, TTL 1). From another VLAN or from the guest WiFi one finds nothing,
  although the server is running. This is the most common "it does not work" case. The LDS registration is **not** affected by this — it is a
  normal TCP connection to `10.10.38.27:4840`.
- **Manual entry must remain.** The existing `ConnectOpcUa` input remains the fallback when mDNS is blocked.
- On orderly shutdown the announcement **and** the registration are withdrawn; after a hard abort the announcement remains in client caches
  until the TTL expires, and the LDS entry remains until its deadline — the aggregation server then still shows the module, but can no longer
  reach it.
- **The aggregation server does not clean up namespaces.** Franka and EVA still had namespaces after they disappeared, but no object any more.
  A missing object is the reliable sign, not a missing namespace.

---

## 3. Namespace — never hard-code

| Namespace | URI |
| --- | --- |
| Vision (this document) | `http://launch-rm.de/vision` |
| OPC 40100 | `http://opcfoundation.org/UA/MachineVision` |
| Machinery (carries `Objects/Machines`) | `http://opcfoundation.org/UA/Machinery/` |

Always use `await client.get_namespace_index("<uri>")`.

> **Caution, the index has shifted twice.** The vision namespace originally sat at ns=4, moved to ns=7 with the Part 2 nodesets (DI, Machinery,
> AMCM), and since the removal of the CPU temperature demo has been at **ns=6** — with `RaspiDevice` its namespace was dropped as well. Current
> order, read on 21.09.2026 from a running server:

```
ns=0  http://opcfoundation.org/UA/
ns=1  urn:plcm:camera-server:<einbauort>                  <- ApplicationUri
ns=2  http://opcfoundation.org/UA/MachineVision           <- OPC 40100
ns=3  http://opcfoundation.org/UA/DI/
ns=4  http://opcfoundation.org/UA/Machinery/              <- carries Objects/Machines
ns=5  http://opcfoundation.org/UA/MachineVision/AMCM/
ns=6  http://launch-rm.de/vision                          <- VisionMachine, VisionProgram
```

Anyone who has hard-wired one of these indices reaches nothing. This is the only point in this document that can **break** existing code — and
it affects the 40100 path just as much. Therefore resolve it via the URI without exception.

---

## 4. Address space of the program

All NodeIds are descriptive string IDs and stable across restarts. Browsing is possible, but not necessary.

```
Objects/
├── VisionProgram                     ns=<vision>;s=VisionProgram   (Type: ProgramStateMachineType, ns=0;i=2391)
│   ├── CurrentState                  LocalizedText: Halted | Ready | Running | Suspended
│   ├── LastTransition                LocalizedText
│   ├── AvailableStates               NodeId[]
│   ├── AvailableTransitions          NodeId[]
│   ├── Start                         Method, **no** inputs
│   ├── Halt                          Method, no inputs
│   ├── Reset                         Method, no inputs
│   ├── Suspend                       Method, no inputs (always rejects)
│   ├── Resume                        Method, no inputs (always rejects)
│   ├── ParameterSet                  <- write inputs HERE, BEFORE the start
│   │   ├── RecipeId     String       **writable**
│   │   └── Continuous   Boolean      **writable**
│   └── ResultSet                     <- outputs
│       ├── JobId           String
│       ├── ErrorCode       Int32
│       ├── ExecutionMode   String    idle | single | continuous
│       ├── LatestResultJson          reference to the same node as below
│       ├── LatestCameraFrame         reference (Base64 JPEG, only with camera)
│       ├── CameraStreamMode          reference, **writable**
│       └── CalibrationProgress       reference (JSON, only with AprilTag profile)
├── StartCalibration                  reference to the 40100 method, input OptionsJson (String, empty = board of the configuration)
├── CaptureCalibrationSample          reference; Error: Int32
├── FinishCalibration                 reference; Summary: String, Error: Int32
└── AbortCalibration                  reference; Error: Int32

Machines/
└── VisionMachine                     ns=<vision>;s=VisionMachine   (OPC 40100, unchanged)
    └── ... StartSingleJob, Stop, ResultManagement, LatestResultJson, LatestCameraFrame ...
```

**`VisionProgram` is self-sufficient.** Everything a client needs to run and evaluate a job hangs below it — as a reference, not a copy. There
is no second value and no second implementation. Since 21.09.2026 `VisionMachine` sits in the Machinery standard folder `Machines` and no
longer alongside it at the same level, so that when browsing it is clear where to start.

Fixed NodeIds:

| Node | NodeId |
| --- | --- |
| Program | `ns=<vision>;s=VisionProgram` |
| State | `ns=<vision>;s=VisionProgram` → child `0:CurrentState` |
| Start | `ns=<vision>;s=VisionProgram.Start` |
| Halt | `ns=<vision>;s=VisionProgram.Halt` |
| Reset | `ns=<vision>;s=VisionProgram.Reset` |
| Recipe (write) | `ns=<vision>;s=VisionProgram.ParameterSet.RecipeId` |
| Continuous operation (write) | `ns=<vision>;s=VisionProgram.ParameterSet.Continuous` |
| JobId | `ns=<vision>;s=VisionProgram.ResultSet.JobId` |
| Error code | `ns=<vision>;s=VisionProgram.ResultSet.ErrorCode` |
| Execution mode | `ns=<vision>;s=VisionProgram.ResultSet.ExecutionMode` |
| Result JSON | `ns=<vision>;s=VisionMachine.LatestResultJson` |

**Generic discovery** also works: browse for instances of `ProgramStateMachineType` (`ns=0;i=2391`). This is the route that also works for the
conveyor — its programs have numeric IDs (`ns=7;i=6100`) and can only be found this way.

### Implementation: `src/ua_program` + `vision_server.vision_program`

`VisionProgram` is not a standalone solution, but the concrete instance of a generic, reusable Part 10 program framework:

- **`src/ua_program/program.py`** (package exports in `src/ua_program/__init__.py`: `Program`, `ProgramException`, `ProgramStateMachine`) —
  taken over in origin from a supervisor's template (the docstring names the four deviations from it: concrete `__init__`, `resume_inputs`
  instead of a template bug, optional string NodeIds, consolidated method loop). `Program` is an abstract base class: `init()` creates the
  instance in the address space; `start`/`halt`/`reset`/`suspend`/`resume` are to be implemented by the subclass (`async … -> str | None`;
  `None` = state change happens, `str` = rejection with this message). `ProgramStateMachine` binds the fixed ns=0 state/transition nodes
  (`i=2406…2424`) to an instance of `ProgramStateMachineType` (`ua.NodeId(2391,0)`), including a documented workaround for an asyncua 2.0.1 bug
  in `set_available_transitions()`.
- **`src/vision_server/vision_program.py`** — `class VisionProgram(Program)`, the vision-specific implementation: wires Start/Halt/Reset/Suspend/Resume
  to the `JobRunner` from `runner/install.py` and creates `ParameterSet`/`ResultSet` as described above.

Anyone who wants to build a further Part 10 program for another module in this repo inherits from `ua_program.Program` instead of starting from
scratch.

---

## 5. Flow: running a job

**The order is binding: first subscribe, then write parameters, then `Start()`.** `Start` takes no arguments — just like with the other modules.

```
Frontend                                    VisionProgram
   |  1. subscribe_events(VisionProgram, [i=2378])
   |  2. write ParameterSet/RecipeId   = "apriltag"
   |  3. write ParameterSet/Continuous = false
   |------------------- Start() ---------------------->|
   |                                                   |  guard + validation (synchronous)
   |<-- ProgramTransitionEvent  Ready -> Running ------|  accepted
   |      (on rejection: NO state change,
   |       instead an event with Severity 500)
   |                                                   |
   |<-- (the 40100 events continue unchanged)          |
   |<-- ProgramTransitionEvent  Running -> Ready ------|  done
   |  4. read ResultSet/JobId, ResultSet/ErrorCode
   |  5. read ResultSet/LatestResultJson               |  the actual result
```

> **Important for a pure Part 10 client.** The 40100 events do **not** reach a subscription on `VisionProgram`. Measured against asyncua 2.0.1
> on 21.09.2026: events go exclusively to subscriptions on the emitting node; the `HasNotifier`/`HasEventSource` hierarchy is not followed on the
> server side. A `HasEventSource` reference from `VisionProgram` to `VisionMachine` changes nothing — checked.
>
> Emitting the 40100 events additionally on `VisionProgram` would be a real duplication (two events per cause), so we do not do it. The complete
> Part 10 route without a single 40100 NodeId is instead:
>
> 1. Subscribe to `ProgramTransitionEvent` on `VisionProgram` — says **that** the job is done.
> 2. Subscribe to value changes of `VisionProgram/ResultSet/LatestResultJson` — delivers **what** was detected (schema `wsc.vision.detections/1`).
> 3. On error, read `ResultSet/ErrorCode`.
>
> Whoever wants the 40100 events continues to subscribe to `VisionMachine` directly. Both work side by side.

Example (asyncua):

```python
ns = await client.get_namespace_index("http://launch-rm.de/vision")
prog = client.get_node(ua.NodeId("VisionProgram", ns))

sub = await client.create_subscription(100, handler)
await sub.subscribe_events(prog, [client.get_node(ua.NodeId(2378, 0))])

recipe = client.get_node(ua.NodeId("VisionProgram.ParameterSet.RecipeId", ns))
await recipe.write_value("apriltag", ua.VariantType.String)

await prog.call_method(client.get_node(ua.NodeId("VisionProgram.Start", ns)))
```

### States

| State | No. | ns=0 NodeId | Meaning here |
| --- | --- | --- | --- |
| `Halted` | 11 | `i=2406` | vision system not operational (e.g. camera did not come up) or halted via `Halt` |
| `Ready` | 12 | `i=2400` | ready to capture |
| `Running` | 13 | `i=2402` | a job is running |
| `Suspended` | 14 | `i=2404` | **never reached** |

Transitions are the standard nodes `ns=0;i=2408` … `i=2424`.

### Which button may be pressed

Do not rebuild this yourself: the server maintains the **`Executable` attribute** of each method. The frontend reads it and greys out buttons
accordingly — the same logic as for every other module.

| State | executable |
| --- | --- |
| `Halted` | `Reset` |
| `Ready` | `Start`, `Halt` |
| `Running` | `Halt`, `Suspend` |
| `Suspended` | `Halt`, `Resume` |

### `Suspend` / `Resume`

They exist because Part 10 prescribes them, but they **always reject**: a vision job takes 250 ms to 20 s and cannot sensibly be paused. The call
does not change the state and yields an event with Severity 500 and the text "Ein Vision-Job kann nicht pausiert werden; Halt verwenden."
(runtime text, meaning "A vision job cannot be paused; use Halt."). The frontend should not offer either button at all.

---

## 6. Events

| | |
| --- | --- |
| Event type | `ProgramTransitionEventType`, `ns=0;i=2378` |
| Emitting node | **`VisionProgram`** |

**Two traps, both verified:**

1. **The event type list is mandatory.** `subscribe_events(prog)` without a second argument does deliver events, but `FromState`, `ToState` and
   `Transition` are then `None` — only `Message` is filled. With `subscribe_events(prog, [client.get_node(ua.NodeId(2378, 0))])` all fields are
   present. The same trap as with the 40100 events.
2. **Subscribe on `VisionProgram`, not on the server object.** asyncua does not bubble events on the server side.

Measured fields of a successful job:

```
FromState=Ready    ToState=Running  Transition=ReadyToRunning  Message="ReadyToRunning"
FromState=Running  ToState=Ready    Transition=RunningToReady  Message="RunningToReady ; Job job-000001 abgeschlossen"
```

With a **rejected** start there is no state change, only an event with `Severity=500` and e.g.
`Message="Start abgelehnt: UNKNOWN_RECIPE (4)"` (runtime text, meaning "start rejected").

---

## 7. Errors

`Start()` has — as with all Part 10 programs — **no return value**. The frontend learns whether the job was accepted by two routes:

1. **Immediately:** if a state change to `Running` comes, it is accepted. If an event with `Severity=500` comes instead, it is rejected; the reason
   is stated in plain text in `Message`.
2. **Machine-readable:** `ResultSet/ErrorCode` carries the code.

| Code | Name | Meaning |
| --- | --- | --- |
| 0 | `OK` | accepted resp. cleanly finished |
| 1 | `INVALID_STATE` | vision system not operational |
| 2 | `INVALID_ARGUMENT` | parameters unusable |
| 3 | `BUSY` | a job is already running |
| 4 | `UNKNOWN_RECIPE` | `RecipeId` not known |
| 5 | `DETECTION_FAILED` | detection failed or timeout |
| 6 | `INTERNAL` | unexpected server error |
| 7 | `CANCELLED` | cancelled by `Halt` |

**Deliberate deviation from Part 10:** a *failed* job (5, 6) does **not** lead to `Halted`, but back to `Ready`. Otherwise the frontend would have
to press `Reset` after every failed detection, and the two state machines would diverge — the 40100 state machine likewise returns to `Ready`
after an error. `Halted` is reached only through **`Halt`**, or when the system did not come up at all at start.

---

## 8. Fetching the result

Part 10 defines no result transport. **Nothing changes here** compared to [`vision-server-interface.md`](vision-server-interface.md) section 6:

- The JSON string is in `ns=<vision>;s=VisionMachine.LatestResultJson` (additionally linked under `ResultSet` — **the same node**, not two).
- Schema unchanged `wsc.vision.detections/1`: `detections[]` with `moduleId`, `position` (metres), `orientation` (quaternion `xyzw`), `frameId`,
  `frameConvention`, `attributes`.
- Whoever already has the 40100 `ResultReadyEvent` evaluation keeps it. The payload travels there unchanged.
- Mapping result → job via `payload["jobId"]`, comparable with `ResultSet/JobId`.
- Live stream still via `VisionMachine.LatestCameraFrame` (Base64 JPEG).

---

## 9. Available recipes (`ParameterSet/RecipeId`)

The server names the permitted values itself: in the **description** of the node `ParameterSet/RecipeId`. A generic frontend can therefore read
them out instead of knowing them.

Currently at this state:

| `RecipeId` | Job |
| --- | --- |
| `"calibration"` | check the cell's measurement readiness |
| `"apriltag"` | **localise modules** — real poses from the camera |

`apriltag` has meanwhile been merged and is thus the actual detection. The former QR detection `image-recognition` has been dropped; whoever still
has it hard-wired receives `ErrorCode=4` (`UNKNOWN_RECIPE`). **That is exactly why the recipe selection should be built data-driven from the
description instead of being hard-wired** — the list has already changed once.

At this interface the real detection changes **nothing**: the same `Start()`, the same `ParameterSet`, the same payload schema. Only the values in
`detections[]` are real, and `attributes` additionally carries `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`. An AprilTag job takes
considerably longer than the placeholder (several captures per job), and the state correspondingly stays longer in `Running`.

---

## 10. What backend and frontend concretely have to do

None of this is required to keep running — everything is a gain.

**Must (otherwise something breaks):**

- [ ] Replace `ns=4` for the vision namespace **everywhere** with `get_namespace_index("http://launch-rm.de/vision")`. The index is now 7
      (section 3).

**Should (this is what it was built for):**

- [ ] Build an mDNS search for `_opcua-tcp._tcp.local.`; assemble the endpoint URL from address, port and TXT `path` (section 2).
- [ ] Remove the hard-coded Pi addresses (`pi_relay.py`, `piServers.ts`), keep manual entry as fallback.
- [ ] Build a **generic program component**: find instances of `ProgramStateMachineType`, render `ParameterSet` as a form, offer
      `Start`/`Halt`/`Reset`, release buttons according to `Executable`, display `CurrentState`, subscribe to `ProgramTransitionEvent`. The same
      component then also serves the conveyor and CardDispenser.
- [ ] Feed the recipe selection from the description of `ParameterSet/RecipeId`.

**Can stay as it is:**

- [ ] All of the 40100 evaluation: `StartSingleJob`, `Stop`, `ResultReadyEvent`, `LatestResultJson`, `LatestCameraFrame`, payload parser.

---

## 11. Pitfalls

| # | Point |
| --- | --- |
| 11.1 | **Write parameters before the start.** `Start()` reads `ParameterSet` only at the time of the call. Whoever reverses the order starts with the old values. |
| 11.2 | **`ParameterSet` is state, not a call argument.** Two clients that write different recipes at the same time overwrite each other. With only one frontend this is uncritical, but deliberate — it is the convention of the cell. |
| 11.3 | **Only one job at a time.** A second `Start` is rejected with `BUSY` (3) — regardless of whether via the program or via `StartSingleJob`. Both interfaces share the same job. |
| 11.4 | **Both interfaces show the same job.** If a job is started via `StartSingleJob`, `VisionProgram` also goes to `Running` and at the end back to `Ready`. The frontend may therefore mix them. |
| 11.5 | **`Halt` without a running job** is allowed and nevertheless goes to `Halted`. Back only via `Reset`. |
| 11.6 | **`Start()` has no return value** — unlike `StartSingleJob`, which returns `(JobId, Error)`. Whoever needs the JobId immediately reads `ResultSet/JobId` after the state change. |
| 11.7 | **`caps=DA`.** The supervisor's quick guide names `NA`; we announce `DA`, because Conveyor and CardDispenser do so and a filter would otherwise overlook us. |

---
