# Camera condition monitoring per OPC 40100-2 (condition monitoring)

**Status:** done — 22.09.2026
**Owner:** `Agent: camera state as DeviceHealth (OPC 40100-2)`
**Topic:** opcua
**Branch:** feature/vision-server

## Goal

A client sees via OPC UA whether the camera is working. The camera watchdog (`camera.py`, taken over
from branch John in `c5207f1`, own plan [`livestream-mjpeg-and-watchdog.md`](livestream-mjpeg-and-watchdog.md))
already detects hanging and failing captures — but reports this only to the log. This plan turns that
into a value in the asset view according to OPC 40100-2, which a generic client without knowledge of
this repo can read.

Before this, a frozen camera stayed invisible from outside: the live stream kept sending the same old
frame, the state machine stayed at `Ready`, and only a running job failed with `DETECTION_FAILED`.
Since the watchdog, the hang is detected and fixed — visible via OPC UA, however, it was not.

> **Duplicate work, documented openly.** This run had initially ported the watchdog itself from branch
> John, because at the start it was not yet on `feature/vision-server`. In parallel, a second run did
> the same. The version merged here is **John's** (`c5207f1`); the own port was discarded. What
> remains is the part-2 layer, which exists nowhere else.

## Already read

- `doc/vision-server-interface.md` — section 11 (OPC 40100-2: asset view), section 10 (camera live stream)
- `doc/vision-system.md` — module table, address space, runtime behavior, starting
- `doc/legacy.md` — A3 (`RaspiDevice/Counter`, retired heartbeat), D5 (systemd unit not versioned)
- `doc/project_docs/topics/vision-system.md` — state and verified asyncua facts
- `doc/work-plans/README.md` — duplicate-work check: no plan with status `in progress`, the topic is free
- `src/vision_server/nodesets/README.md` — pinned nodeset versions

## Affected files

- `src/vision_server/camera.py`
- `src/vision_server/camera_health.py` (new)
- `src/vision_server/asset_model.py`
- `src/vision_server/nodeset_ids.py`
- `src/vision_server/profiles.py`
- `src/vision_server/runner.py`
- `tools/measure_nodeset_import.py`
- `tests/test_shared_camera.py` (new), `tests/test_camera_health.py` (new)
- `tests/test_asset_model.py`, `tests/test_camera_stream.py`, `tests/test_camera_owner.py`
- `doc/vision-server-interface.md`, `doc/vision-system.md`, `doc/legacy.md`

## Interfaces

### OPC UA

Both nodes lie in the vision namespace (`http://launch-rm.de/vision`) and carry fixed string NodeIds.
The image sensor node exists only if `AssetConfig.image_sensor_model` is set; the root node always,
as soon as part 2 is loaded.

| Element | NodeId | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| `DeviceHealth` (asset) | `ns=<vision>;s=VisionMachine.VisionAsset.Health.DeviceHealth` | `Int32` (`DeviceHealthEnumeration`) | Read / subscribe | state of the vision system |
| `DeviceHealth` (camera) | `ns=<vision>;s=VisionMachine.VisionAsset.ImageSensor.Health.DeviceHealth` | `Int32` (`DeviceHealthEnumeration`) | Read / subscribe | state of the camera |

BrowsePaths from `VisionMachine`: `<amcm>:VisionAsset` → `<amcm>:Health` → `<di>:DeviceHealth`, or
`<amcm>:VisionAsset` → `<amcm>:ImageSensors` → `<amcm>:ImageSensor` → `<amcm>:Health` → `<di>:DeviceHealth`.
`<amcm>` = `http://opcfoundation.org/UA/MachineVision/AMCM/`, `<di>` = `http://opcfoundation.org/UA/DI/`.
Resolve indices at runtime via `get_namespace_index`, never hard-code them.

`Health` hangs via **`HasAddIn`** on its owner (not `HasComponent` — asyncua sets this wrongly during
instantiation and it is swapped afterwards). Type definition: `<amcm>:VisionHealthInfoType`.

Values (DI `DeviceHealthEnumeration`, NAMUR NE 107):

| Value | Name | When |
| --- | --- | --- |
| 0 | `NORMAL` | fresh camera image |
| 1 | `FAILURE` | camera never opened, or finally given up (process ends right away) |
| 2 | `CHECK_FUNCTION` | warmup, or the camera is currently being reopened |
| 3 | `OFF_SPEC` | image older than `stale_frame_s` — hang detected, not yet escalated |
| 4 | `MAINTENANCE_REQUIRED` | is never written (no wear counter present) |

Written **only on state change**. A subscription thus sees real transitions; in normal operation the
node is quiet after the first frame.

Not instantiated and therefore not subscribable either: `DeviceHealthAlarms`, `Health/State`
(SEMI E10), `Health/Temperature`, `Health/RemainingLifeTime`, `Maintenance`.

### Python

```python
# Module: src/vision_server/camera.py
@dataclass(frozen=True)
class CameraStatus:
    """Snapshot of what the watchdog already knows anyway."""
    running: bool
    has_handle: bool
    last_outcome: str            # "none" | "ok" | "error" | "hung"
    consecutive_failures: int
    reopen_attempts: int
    gave_up: bool
    frame_age_s: float | None

class SharedCamera:
    @property
    def is_open(self) -> bool: ...
    def status(self, now: float) -> CameraStatus:
        """State at time `now` (same `loop.time()` time base)."""
    def set_give_up_handler(
        self, handler: Callable[[], Awaitable[None] | None]
    ) -> None:
        """Replaces what happens on final give-up."""

# Module: src/vision_server/camera_health.py
def device_health(status: CameraStatus, *, stale_frame_s: float) -> DeviceHealth:
    """Maps the camera state to DI's DeviceHealthEnumeration. Pure."""

async def write_device_health(nodes: Sequence[Node], value: DeviceHealth) -> None:
    """Writes the same value to all state nodes. Never raises."""

class CameraHealthPublisher:
    """Mirrors the state of a `SharedCamera` to `Health/DeviceHealth`."""
    def __init__(self, camera: SharedCamera, nodes: Sequence[Node],
                 config: CameraStreamConfig) -> None: ...
    @property
    def health(self) -> DeviceHealth: ...
    def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish_now(self) -> None: ...
    def give_up_handler(
        self, then: Callable[[], None]
    ) -> Callable[[], Awaitable[None]]:
        """First write FAILURE (with time limit), then `then` -- e.g. os._exit."""

# Module: src/vision_server/asset_model.py
async def _add_health(server, owner, amcm_idx, di_idx, own_idx) -> Node | None:
    """Attaches `Health` to a component, returns its `DeviceHealth`.
    Never raises -- the asset view must not endanger the job path."""
```

`VisionAssetNodes` additionally gets `device_health: tuple[Node, ...] = ()`.

### Data / payload

None. `DeviceHealth` is a scalar `Int32`, not JSON — unlike `LatestResultJson` or `CalibrationProgress`.

### Network / operations

No new ports or services. New fields in `CameraStreamConfig` (`profiles.py`), default values from
branch `John`:

| Field | Default | Meaning |
| --- | --- | --- |
| `frame_timeout_s` | 3.0 | how long a single capture may take |
| `max_capture_failures` | 3 | consecutive errors until reopening |
| `max_reopen_attempts` | 2 | reopenings without image until process abort |
| `stale_frame_s` | 2.0 | from this age a frame counts as stale |
| `overlay_timeout_s` | 2.0 | time limit for an overlay run |
| `health_interval_s` | 0.5 | cadence of the state polling for `DeviceHealth` |

**Important for operation:** If the watchdog finally gives up, the process terminates with
`os._exit(1)` and relies on systemd restarting it (`opcua-server.service`, `Restart=always`,
`RestartSec=5`). In a foreground start during development this safety net does not exist — there the
process is then simply gone. That is intended, but one has to know it.

### Failure cases

| Case | Behavior |
| --- | --- |
| Part 2 not loaded (`assets is None`) | no health nodes, no publisher task. The watchdog runs anyway |
| State node cannot be created | `_add_health` logs and returns `None`; the asset view otherwise remains complete |
| No `image_sensor_model` configured | only the root node carries `Health` |
| Health node present, but no camera open | once `FAILURE`, no task |
| Write error at the node | is caught and logged, the task continues |
| Camera hangs | `OFF_SPEC` → reopening (`CHECK_FUNCTION`) → on success `NORMAL` |
| Camera hopeless | `FAILURE`, then process abort and systemd restart |
| Shutdown | publisher is stopped **before** stream and sources, so that orderly closing does not register as a hang |

## Approach

1. Create this plan, line in `README.md`.
2. `profiles.py`: six fields.
3. `camera.py`: `is_open` and idempotent `open()` (from `3a33640`), then the watchdog from `18150a3`.
   Backend dispatch remains untouched.
4. `camera.py`: counters at instance level, `CameraStatus`, `status()`, `set_give_up_handler`,
   awaitable `on_give_up`.
5. `camera_stream.py`: stale detection and overlay time limit. `max_stream_width` and the
   `CalibrationProgress` block remain.
6. `nodeset_ids.py`: constants and the `DeviceHealth` enum.
7. `asset_model.py`: `_add_health` for root and image sensor.
8. `camera_health.py` including tests.
9. `runner.py`: `_start_camera_health`, field in `VisionMachine`, `aclose()`.
10. Repair `tools/measure_nodeset_import.py`, measure the cost.
11. Update the topic doc, set the status here to `done`.

## Open questions

- **Aggregation at the root.** Today root and sensor node carry the same value: there is exactly one
  state source. If a second component with its own state is added, the root needs a real rule — and
  for that a ranking across the NE-107 states, which DI does not define. Deliberately not anticipated.
- **Threshold for `OFF_SPEC` instead of `FAILURE`.** A stale image is strictly speaking an invalid
  output signal. `OFF_SPEC` was chosen because hangs demonstrably heal themselves, and an HMI could
  otherwise not distinguish between „hakt gerade“ (stuttering right now) and „tot“ (dead). Changeable
  in exactly one table row in `camera_health.device_health()`.
- **Legacy D5** (systemd unit nowhere versioned) now weighs more heavily: the hard process abort relies
  on `Restart=always` in a file that nobody can read in the repo.
- **`Maintenance`** (`VisionMaintenanceInfoType`) would be the next part of part 2. Without maintenance
  intervals and calibration history, however, there would be nothing to write into it.

## Deviations from the plan

- **`health_interval_s` is 0.5 s instead of the planned 1.0 s.** The value was guessed; on
  recalculation it turned out that the `OFF_SPEC` window is only `frame_timeout_s − stale_frame_s` =
  1.0 s wide. With per-second polling it would regularly have been missed. A test
  (`DefaultsAreConsistentTest`) now records that the default values fit together.
- **`camera.exit_process()` is public** (at John `_exit_process`). `runner.py` passes the function on as
  the conclusion of the `give_up_handler` — a cross-module import of a private name would be worse.
- **`is_open` and the idempotent `open()` stem from commit `3a33640`**, not from `18150a3` as initially
  assumed. Without them `_reopen()` runs into nothing; they were therefore ported as well.
- **The reference swap requires real `ua.NodeId` objects.** With the bare number
  (`ua.ObjectIds.HasAddIn` = 17604), asyncua's `_to_nodeid` raises a `ValueError`, because it builds a
  `TwoByteNodeId` from it. On the first attempt this ran silently into the `except` branch, and `Health`
  still hung via `HasComponent`. Found only because the address space was checked by hand.
- **`tools/measure_nodeset_import.py` was broken** (still pointed to `src/OPCUA/nodesets`) and was
  repaired along the way — without the tool there would be no measured figures for the docs.
- **The calibration progress moves out of the frame branch.** With a hanging camera, one wants to see
  in the progress precisely that the session does not advance.

### Addendum 23.09.2026 — the reporting path was only half built

The first version served from DI's `IDeviceHealthType` only the state variable. The standard foresees
two members; the second, `DeviceHealthAlarms`, was missing. In parallel the live stream evaluated the
image age a second time and emptied its node — a second truth about „lebt die Kamera“ (is the camera
alive), bypassing the companion spec.

Followed up:

- `DeviceHealthAlarms` with `FailureAlarm`, `CheckFunctionAlarm` and `OffSpecAlarm`, fired on every
  state change, at most one active. Emitted by `VisionMachine` (clients subscribe there anyway),
  `SourceNode` names the component. Severity 900/500/300.
- `MaintenanceRequiredAlarmType` stays out — without a wear counter it could never fire.
- The alarms sit only at the root: 36 nodes per instance (measured), and root as well as image sensor
  carry the same value.
- Live-stream staleness (`_mark_stale`) removed without replacement. `stale_frame_s` is thereby
  unambiguously the OFF_SPEC threshold of the capture.
- Frontend (WSC repo): traffic light in the camera panel from `DeviceHealth`, because the standing image
  no longer carries the statement.

Measured: asset view 19 → 23 (variable only) → 132 nodes (with alarms), construction 0.151 → 0.179 →
0.327 s. End-to-end against a real client: seven alarm events via a real subscription.

**Known limit:** `ConditionRefresh` is not served — whoever connects after an alarm does not get it
re-delivered and must read `DeviceHealth`. Stated this way in 11.4.

### Measured

Desktop, asyncua 2.0.1, two runs per variant:

| | Nodes of the asset view | Build time |
| --- | --- | --- |
| without state block | 19 | 0.150 s / 0.169 s |
| with state block | 23 | 0.179 s / 0.186 s |

Four nodes, about 20 ms; the RSS difference was below the measurement resolution. Checked end-to-end
against real nodes: `CHECK_FUNCTION → NORMAL → OFF_SPEC → FAILURE`.

**Still open:** acceptance on the device. The route via a real hang (pulling the flat ribbon cable,
observing state changes and timings in UaExpert) is still outstanding — until then it is proven that
the state machine computes, not that it sees the real hang.

## After completion

- Status set to `done`, actual interfaces entered.
- Line in the index `doc/work-plans/README.md` updated.
- Lasting domain knowledge carried over to `doc/vision-server-interface.md` (section 11.4) and
  `doc/vision-system.md`.
