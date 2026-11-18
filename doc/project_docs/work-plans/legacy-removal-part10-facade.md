# Remove legacy items A1–A6/B2/B4/B6 and make VisionProgram the sole facade

**Status:** done — 22.09.2026
**Owner:** `Agent: remove RaspiDevice/VisionSystem, Part-10 facade`
**Topic:** opcua
**Branch:** worktree-altlasten-abbau (from `feature/vision-server`)

## Goal

Afterwards the address space contains **exactly one** vision system and **one**
entry point for the frontend. `RaspiDevice` and the empty legacy instance
`2:VisionSystem` are removed in both repos, `VisionMachine` lies under
`Objects/Machines` in a standards-compliant way instead of next to
`VisionProgram`, and a client that speaks only OPC UA Part 10 gets by without a
single access to `VisionMachine`.

Trigger: The supervisor was confused by four equal-ranking objects under
`Objects` and wants to work exclusively on the Part 10 part.

## Already read

- `doc/legacy.md` — A1–A6 (CPU temperature demo), B2/B4/B6 (WSC), removal chain
- `doc/vision-system.md` — address space, order of setup
- `doc/vision-server-interface.md` — 40100 interface, §7.4
- `doc/part10-program-interface.md` — Part 10 address space, namespace table
- `doc/work-plans/README.md` — no competing plan on the topic `opcua`

## Affected files

**Cell** (this repo)

- `src/OPCUA/server.py` → `src/vision_server/server.py` (moved)
- `src/OPCUA/print_setpoint.py` (deleted)
- `src/ua_lds.py` → `src/vision_server/discovery/lds.py` (moved)
- `src/ua_mdns.py` → `src/vision_server/discovery/mdns.py` (moved)
- `src/OPCUA/*.xml`, `src/OPCUA/nodesets/` → `src/vision_server/nodesets/`
- `src/vision_server/address_space.py`
- `src/vision_server/asset_model.py`
- `src/vision_server/runner.py`
- `src/vision_server/vision_program.py`
- `tests/test_part10_fassade.py` (new)
- `doc/legacy.md`, `doc/vision-system.md`, `doc/vision-server-interface.md`,
  `doc/part10-program-interface.md`

**WSC** (`webskillcomposition`, branch `Ungetestet`)

- `backend/src/backend/config/pi_relay.py`
- `backend/src/backend/runtime/application_service.py`
- `backend/src/backend/runtime/runtime_registry.py`
- `frontend/src/features/opcua-server/config/piServers.ts`
- `frontend/src/features/opcua-server/model/visionResult.test.ts`
- `frontend/src/features/autolocate/components/AutolocateModulesModal.tsx`

## Interfaces

### What is removed

| Removed | Previous NodeId | Replacement |
| --- | --- | --- |
| `RaspiDevice` including `CpuTemperature`, `Counter`, `Setpoint` | `ns=2;i=1` … `i=4` | none — demo without a functional purpose |
| `2:VisionSystem` (empty 40100 instance) | `ns=2;i=5` | `VisionMachine` was always the real system |
| `CpuTemperatureResult` | `ns=2;i=…` under `Results` | none |
| Namespace `http://launch-rm.de/raspi` | `ns=2` | dropped entirely |

**Consequence for all clients:** the namespace indices move **down by one**.

```
before                                    after
ns=2  http://launch-rm.de/raspi           (dropped)
ns=3  …/UA/MachineVision                  ns=2  …/UA/MachineVision
ns=4  …/UA/DI/                            ns=3  …/UA/DI/
ns=5  …/UA/Machinery/                     ns=4  …/UA/Machinery/
ns=6  …/UA/MachineVision/AMCM/            ns=5  …/UA/MachineVision/AMCM/
ns=7  http://launch-rm.de/vision          ns=6  http://launch-rm.de/vision
```

Anyone using `get_namespace_index(uri)` notices nothing. Anyone who hard-wired
an index ends up at nothing — in WSC this only affects the fallback constant
`DEFAULT_MACHINE_VISION_NAMESPACE_INDEX` (3 → 2).

### Address space afterwards

```
Objects/
├── Machines/                     (OPC UA Machinery, standard folder)
│   └── VisionMachine             ns=<vision>;s=VisionMachine   OPC 40100
└── VisionProgram                 ns=<vision>;s=VisionProgram   OPC UA Part 10
    ├── CurrentState / Start / Halt / Reset / Suspend / Resume
    ├── ParameterSet/  RecipeId, Continuous          (writable)
    ├── ResultSet/     JobId, ErrorCode, ExecutionMode
    │                  LatestResultJson, LatestCameraFrame,
    │                  CameraStreamMode, CalibrationProgress   (references)
    ├── StartCalibration                             (reference, see below)
    ├── CaptureCalibrationSample                     (reference)
    ├── FinishCalibration                            (reference)
    └── AbortCalibration                             (reference)
```

`VisionMachine` remains fully intact and reachable via its fixed string NodeId.
It just no longer hangs directly under `Objects`.

### New under VisionProgram

The four calibration methods are **not registered a second time**; instead
they are additionally made visible under `VisionProgram` via a `HasComponent`
reference — the same pattern as the `Organizes` references already present in
the `ResultSet`. There remains exactly one method node with exactly one
implementation.

| Method | NodeId | Input | Output |
| --- | --- | --- | --- |
| `StartCalibration` | `ns=<vision>;s=VisionMachine.StartCalibration` | — | `Error: Int32` |
| `CaptureCalibrationSample` | `ns=<vision>;s=VisionMachine.CaptureCalibrationSample` | — | `Error: Int32` |
| `FinishCalibration` | `ns=<vision>;s=VisionMachine.FinishCalibration` | — | `Summary: String`, `Error: Int32` |
| `AbortCalibration` | `ns=<vision>;s=VisionMachine.AbortCalibration` | — | `Error: Int32` |

Calling works via **both** objects, verified on 21.09.2026 against asyncua 2.0.1:

```python
await program_node.call_method(method_node)   # objectId = VisionProgram
await machine_node.call_method(method_node)   # objectId = VisionMachine
```

As a side effect, the three methods get stable string NodeIds; previously
asyncua assigned them running numbers that shifted with every new node.

### Results for a pure Part 10 client

**Measured on 21.09.2026 (asyncua 2.0.1): events do not propagate through the
`HasNotifier`/`HasEventSource` hierarchy.** A subscription on `VisionProgram`
therefore does *not* see the 40100 events of `VisionMachine`, not even with a
`HasEventSource` reference in between. Reproduced with two objects, two
subscriptions and two generators: every event reaches only the subscription on
the emitting node.

Additionally emitting the 40100 events on `VisionProgram` would be genuine
duplication — two events per cause. The route for a Part 10 client is therefore:

1. Subscribe to `ProgramTransitionEvent` (`ns=0;i=2378`) on `VisionProgram` —
   tells **that** a job is finished (`Running -> Ready`).
2. Subscribe to value changes of `VisionProgram/ResultSet/LatestResultJson` —
   delivers **what** was detected, schema `wsc.vision.detections/1`.
3. Read `ResultSet/ErrorCode` if the transition reports an error.

As a result, a Part 10 client needs not a single 40100 NodeId. The 40100 events
remain unchanged on `VisionMachine` for 40100 clients.

### Python

```python
# Module: src/vision_server/address_space.py
async def _vision_parent(server: Server) -> Node:
    """Objects/Machines if the Machinery nodeset is loaded, otherwise Objects.

    Does not raise: without Part 2 there is no Machines folder, and the
    vision system must still be created.
    """

# Module: src/vision_server/vision_program.py
async def install_vision_program(
    server, parent, own_idx, jobs, *,
    known_recipes: frozenset[str] | None = None,
    mirror_nodes: dict[str, Node] | None = None,
    mirror_methods: dict[str, Node] | None = None,   # new
) -> VisionProgram:
    """`mirror_methods` are additionally made visible under the program via
    HasComponent -- references, not a second registration."""
```

### Failure cases

| Case | Behavior |
| --- | --- |
| Machinery nodeset not loaded (`config.assets is None`) | `VisionMachine` hangs directly under `Objects` as before; logged as info, not an error |
| `config.apriltag is None` | no calibration methods, therefore also no references — `mirror_methods` stays empty |
| Client hard-wires `ns=3` for MachineVision | ends up at nothing; the only known place is the WSC fallback constant, which is changed along with it |

## Approach

1. Cell: strip down `server.py`, delete `print_setpoint.py`.
2. Cell: `address_space.py` — parent node `Objects/Machines`.
3. Cell: `asset_model.py` — switch the hard-coded `"VisionMachine."` to `config.vision_system_name`.
4. Cell: `vision_program.py` + `runner.py` — `mirror_methods`.
5. Cell: test `tests/test_part10_fassade.py` — call via both objects, parent node, no second `VisionSystemType` instance.
6. Cell: bring docs up to date.
7. WSC: remove relay, temperature display, `legacy` branch.

## Open questions

- Moving under `Machines` depends on `config.assets` being set. On both Pis this is the case. For a development instance without Part 2, the fallback remains.

## Deviations from the plan

**1. Events cannot be mirrored — measured, not assumed.**
The plan assumed that a `HasEventSource` reference would suffice for a
subscription on `VisionProgram` to also see the 40100 events. That is wrong.
On 21.09.2026 against asyncua 2.0.1, reproduced with two objects, two
subscriptions and two generators: every event reaches **only** the subscription
on the emitting node, with and without the reference. A control case ran, so the
test setup was not at fault.

Consequence: the 40100 events stay on `VisionMachine`. A pure Part 10 client
gets its result via the value change of `VisionProgram/ResultSet/LatestResultJson`
instead of via an event. Additionally emitting them on `VisionProgram` would have
been genuine duplication — two events per cause — and that is exactly what this
refactoring was supposed to end.

**2. Method mirroring works via reference.** Also measured beforehand: a
`HasComponent` reference is enough; the call succeeds with both objects as
`objectId`, without a second registration.

**3. Additionally done, because it directly followed:**

* `origin/feature/vision-server` (20617c7) pulled in — the switch to
  `Server.register_to_discovery()` and four more commits. One conflict in
  `server.py`, both times comments respectively the deleted demo versus their
  removal.
* **Package move:** `src/OPCUA/` is dissolved. After the legacy cleanup
  `server.py` contained only Pi configuration, mDNS and LDS — which belongs next
  to the package that uses it. This also resolves legacy item **C5** (nodesets).

**4. B4 and B5 were already gone.** The plan foresaw removing the temperature
display and `MOCK_MODULES` from the autolocate popup. Both were no longer present
on 22.09.2026 — the popup had in the meantime evolved further.

**5. Not done:** `legacyCameraFrameNodeId` in the frontend stays. The name is
misleading — it means the numeric camera node of the *older Pi build*, not the
removed `2:VisionSystem`. As long as the two Pis are switched over at staggered
times (and that is exactly the current state, see D5), the fallback is useful.

**6. Operational sequence the plan did not have:** The systemd unit on both Pis
starts `src/OPCUA/server.py`. This path no longer exists; the unit is nowhere
versioned (legacy D5) and must be switched manually to
`python3 -m vision_server.server` with `WorkingDirectory=<repo>/src`.

## After completion

- [x] Status set to `done`, actual interfaces recorded.
- [x] Entry in the index `doc/work-plans/README.md` updated.
- [x] `doc/legacy.md`: A1–A6, B2, B4, B5, B6, C5 struck out; B1 trimmed to the
      frontend remainder; D5 marked as acute.
- [x] Topic docs updated: `vision-system.md`, `vision-server-interface.md`,
      `part10-program-interface.md`,
      `apriltag-reference.md`, `presentation.md`,
      `README.md`.

### Checked

| What | Result |
| --- | --- |
| Cell: `PYTHONPATH=src python3 -m unittest discover -s tests -t .` | 249 tests, green |
| Cell: server started at the new entry point | nodesets from the package, `ns=6;s=VisionMachine` under `Machines`, `VisionProgram` next to it, mDNS active |
| WSC frontend: `npx vitest run src/features/opcua-server` | 16 tests, green |
| WSC frontend: `npx tsc --noEmit` | no findings |
| WSC frontend: full suite | 121 green, 1 red — `entities/robot/model/store.test.ts:188`, pre-existing (legacy D8), no robot file touched |
| WSC backend | `pytest` is not installed in the venv (legacy D7: the suite did not pass anyway). Instead, imports of `application_service` and `runtime_registry` were checked — they import cleanly |

**Open:** The systemd units on both Pis. Until they are switched over, the
service there does not start anymore after a pull.
