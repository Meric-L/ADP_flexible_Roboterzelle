# Legacy — state and removal conditions

> **Superseded (30.10.2026)** by [`altlasten-zelle.md`](altlasten-zelle.md) (this repo) and [`altlasten-wsc.md`](altlasten-wsc.md) (webskillcomposition). Open items from here have been re-checked there and carried over under their old IDs. This document is kept as a historical record.

Running list of the things we have added during the project that
**do not belong to localization**: demo setups, test instruments, placeholders and
deployment workarounds. The purpose is that none of it quietly becomes productive.

Each entry names the condition under which it can be removed. Only once that condition
is met is it deleted — and then the entry is also struck from here.

**Removed on 21.09.2026:** the complete CPU temperature demo (A1–A6) together with its
counterparts in the university repo (B2, B6). B4 and B5 were already gone at that point. See [`arbeitsplaene/altlasten-abbau-part10-fassade.md`](altlasten-abbau-part10-fassade.md).

Two repos are affected:

- **Cell** = `ADP_flexible_Roboterzelle` (this repo), branch `feature/vision-server`
- **WSC** = `webskillcomposition` (university repo), branch `Ungetestet`

State as of: 2026-09-21. Tests: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

---

## A. CPU temperature demo — **completely removed (21.09.2026)**

The original setup with which the OPC UA route was first made to run at all.
Functionally none of it had anything to do with the cell. All of it gone.

| # | What | Done |
|---|---|---|
| ~~A1~~ | ~~`2:VisionSystem` — second, empty `VisionSystemType` instance~~ | The address space now contains exactly **one** instance; recorded in `tests/test_part10_fassade.py` |
| ~~A2~~ | ~~`CpuTemperatureResult`~~ | Dropped together with A1 |
| ~~A3~~ | ~~`RaspiDevice/Counter`~~ | The replacement in the log is the loop-lag watchdog in `runner/machine.py`; the **externally visible** heartbeat since 22.09.2026 is `VisionAsset/Health/DeviceHealth` (OPC 40100-2, see `vision-server-interface.md` 11.4) — not a counter without a source, but a standardized state that is only written when it changes |
| ~~A4~~ | ~~`RaspiDevice/Setpoint`~~ | Dropped together with B2 |
| ~~A5~~ | ~~1 Hz endless loop in the server~~ | `main()` now only waits for the stop signal |
| ~~A6~~ | ~~`print_setpoint.py`~~ | Deleted |

With `RaspiDevice` its namespace `http://launch-rm.de/raspi` has also gone. **All
namespace indices have thereby shifted down by one** — the vision namespace is now
at ns=6. Anyone using `get_namespace_index(uri)` will notice nothing; anyone who
hard-wired an index will end up pointing at nothing.

`VisionMachine` has since been placed in the machinery standard folder `Objects/Machines` instead of
directly under `Objects`. The reason was not technical but readability: under `Objects`
there were four peer objects, and during maintenance it was unclear which one was the
entry point. Now only `VisionProgram` remains there on the project side.

---

## B. Pi-specific additions in the university backend/frontend

Everything below we contributed to the WSC repo. It is hard-wired to our two Pis
and does not belong in a university repo that other groups continue to use.

| # | What | Where | Why it is there | Removal condition |
|---|---|---|---|---|
| B1 | **Hard-coded Pi addresses** `10.10.38.104` / `.109` | WSC: `frontend/src/features/opcua-server/config/piServers.ts:1-6` | Fastest route to a working connection | Server selection via the existing `ConnectOpcUa` input or via mDNS instead of constants. **Partly done** 22.09.2026: the backend part has been dropped together with `config/pi_relay.py` (the file contained only relay configuration). In the frontend the constants are still used in six places — `ServerManager`, `CameraStreamPanel`, `AutolocateModulesModal`, `ViewportHeaderControls`, `sceneState` |
| ~~B2~~ | ~~CPU temperature relay Pi1↔Pi2~~ | — | — | **Done** 21.09.2026: removed without replacement, along with `_relay_tasks` in `runtime_registry.py`. The target nodes no longer exist |
| B3 | **"First Layer" / "Second Layer" as labels for two fixed URLs** | WSC: `piServers.ts:4-7`, `ServerManager.tsx:5`, `:100-103` | Make the two connection points distinguishable in the UI | When the layer assignment comes from the server data instead of the URL |
| ~~B4~~ | ~~"Test: First Layer CPU-Temperatur" in the autolocate popup~~ | — | — | **Done**: was already gone when removed on 22.09.2026 — the popup had in the meantime evolved further. The node it subscribed to does not exist anyway |
| ~~B5~~ | ~~`MOCK_MODULES` — three invented robots at position 0/0/0~~ | — | — | **Done**: no longer present in the popup on 22.09.2026. Like B4, dropped in the course of the autolocate work |
| ~~B6~~ | ~~`legacy` branch in `resolveVisionBinding`~~ | — | — | **Done** 21.09.2026: obsolete together with A1. The `unknown` branch has remained — it still distinguishes "nothing known yet" from "wrong server" |
| B7 | **Deployment workaround** `if os.getenv("HOST"): mount StaticFiles("./www")` and `host="0.0.0.0"` | WSC: `backend/src/backend/app.py:18-19`, `:30` | So that the built frontend is served along with it | Not introduced by us — to be clarified with the supervisors when handing back to the university repo |
| B8 | **Branch name `Ungetestet`** | WSC | Honestly named working branch | At merge into `dev`. The name now says less than it should — backend and vision route are verified against both Pis, the frontend has typecheck and unit tests |

---

## C. Vision server: non-functional placeholders

In the vision server itself, but independent of how good the detection becomes.

| # | What | Where | Why it is there | Removal condition |
|---|---|---|---|---|
| ~~C1~~ | ~~`IsSimulated = True` hard-wired~~ | — | — | **Done**: now comes from the detection source (`DetectionSource.is_simulated`) |
| C2 | **`IsPartial = False`** hard-wired | Cell: `src/vision_server/events.py:69`, `result_management.py:91` | Only full results exist | Only relevant if partial results are ever sent |
| ~~C3~~ | ~~`vision_system_id` identical on both Pis~~ | — | — | **Done**: `vision_identity()` in `src/vision_server/server.py` (env → hostname mapping → fallback). The hostnames in `PI_IDENTITIES` are still guesses and have to be checked on the Pis |
| ~~C4~~ | ~~`configuration_id` is set by no source~~ | — | — | **Done**: `AprilTagDetectionSource` fills it with tag family, calibration and tag-map identity (`tagloc.identity`, without numpy and without cv2) |
| ~~C5~~ | ~~Nodeset XML under `src/OPCUA/`~~ | — | — | **Done** 22.09.2026: all four nodesets are in `src/vision_server/nodesets/`, `config.py` points there via `_NODESET_DIR`. `src/OPCUA/` no longer exists |
| ~~C6~~ | ~~`caputure.py` — QR scanner in the AprilTag folder~~ | — | — | **Done**: deleted. The Pi camera now addresses `SharedCamera`; for CLI calls `tagloc.frames.PiCameraSource` |
| C7 | **Address of the discovery server hard-coded in the code** — `opc.tcp://10.10.38.27:4840/` | Cell: `src/vision_server/discovery/lds.py` (`DEFAULT_LDS_URL`) | Without registration there the aggregation server does not accept us, and the cell has exactly this one LDS | When the address comes from a configuration file. Since 21.10.2026 registration is on again by default (it was off from 23.09. to 21.10.2026); `OPCUA_LDS_URL=""` switches it off, another URL redirects it without a code change |

---

## D. Environment, build and repo hygiene

Not legacy in the narrower sense, but pitfalls that have already cost us time.

| # | What | Where | Problem |
|---|---|---|---|
| D1 | **`.gitignore` swallows whole file types** — `*.txt`, `*.sh`, `*.bat`, `concept/`, `data/`, `hardware/` | Cell: `.gitignore` | Files already tracked keep running, but **new** `.sh`/`.txt` files and everything new under `concept/` are silently invisible to `git add`. Affects every future setup or deploy script |
| ~~D2~~ | ~~Broken `.venv/`~~ | — | **Done**: recreated from `requirements.txt` (OpenCV 5.0.0, numpy 2.5.3, asyncua 2.0.1, pupil-apriltags). The entire suite now runs with it **without a single skip**. The defective directory lies next to it as `.venv.kaputt/` and can be deleted. Still open: only OpenCV **5.0.0** has been verified; the Pi runs 4.x. `tagloc.detector`/`boards` have runtime switches for this, but nobody has run them there yet |
| ~~D3~~ | ~~`requirements.txt` does not reflect reality~~ | — | **Partly done**: the file now explains why `picamera2` (ceiling Pi) comes via apt (venv needs `--system-site-packages`) and what `pupil-apriltags` is still for — since `tagloc.detector` is an optional second backend, no longer a requirement. `pyrealsense2` (hand Pi) is likewise deliberately not included: verified live on the Pi that Intel provides no ARM/aarch64 wheels on PyPI for it — it has to be installed there separately (for us it was under `/usr/local/lib/python3.13/dist-packages/`, likewise made visible via `--system-site-packages`). An upper bound on OpenCV is still deliberately missing, because the runtime switches cover 4.x and 5.x |
| D4 | **`setup.sh` / `setup.bat` are not a project setup** | Cell | They only set up the ShareLaTeX remote and two git aliases. The name suggests something else; a real setup script does not exist |
| D5 | **systemd unit and venv are versioned nowhere** | — | `opcua-server.service`, the repo path on the Pi, the Python version and the venv creation are in no repo. Re-setting up a Pi is currently undocumented. **On 22.09.2026 this bit us:** the package move deleted `src/OPCUA/server.py`, and after a reboot the service no longer started — nobody could look in the repo to see what the unit points to. Fixed with D11 instead of with two manual interventions |
| D11 | **`src/OPCUA/server.py` is a starter without logic** | Cell: `src/OPCUA/server.py` (15 lines) | Holds the entry point that the unversioned systemd unit points to. The server itself lies in `src/vision_server/server.py`. It can go once D5 is done — i.e. the unit lies in the repo and is rolled out with it; then `ExecStart` there is to be switched to `-m vision_server.server` in a traceable way |
| D6 | **Node 18 is not sufficient for the WSC frontend** | WSC: `frontend/package.json` | `vite`/`vitest` require `^20.19.0 \|\| >=22.12.0`. Under Node 18 vitest does not start, and `npm install` **silently** skips the native `@rolldown/binding-linux-x64-gnu` — you get a broken `node_modules` without an error message |
| D7 | **`tests/test_asyncua_discovery.py` aborts during collection** | WSC: backend | Imports `discover_variables`, which no longer exists in `asyncua_discovery.py`. As a result `uv run pytest` does not run through at all. Not caused by us |
| D8 | **One failing frontend test** | WSC: `entities/robot/model/store.test.ts:188` | Pre-existing on the branch, reproducible without our changes. As long as it is red, `npm test` is no useful signal |
| ~~D9~~ | ~~The supervisors' short guide to discovery is wrong~~ | — | **Done**: the guide said the aggregation server searches mDNS itself and that registration was not necessary — that cost an afternoon. It was corrected on 21.09.2026 and now lies **in the repo** under `betreuer/OPC UA-mDNS-Kurzanleitung.md` (previously only locally and untracked), so that other groups do not run into the same afternoon. The two `.py` files in the same folder remain untracked |
| D10 | **An endpoint on `0.0.0.0` registers itself as `0.0.0.0`** | `asyncua` 2.0.1: `client/client.py`, `register_server` | `register_to_discovery()` enters `server.endpoint.geturl()` as DiscoveryUrl; the aggregation server takes the address literally and connects into the void. Solved via `Server.socket_address` — the endpoint names the LAN IPv4, listening continues on `0.0.0.0`, see `src/vision_server/server.py`. **Cost trap:** we initially overlooked the attribute and built the registration record by hand; that was unnecessary |
| D11 | **`Server.stop()` does not deregister from the discovery server** | `asyncua` 2.0.1: `server/server.py`, `stop()` | It only aborts the renewal loop and disconnects. Without a dedicated `unregister_from_discovery()` the entry remains until it expires, and the aggregation server shows a module it can no longer reach. `src/vision_server/discovery/lds.py` calls it in the `finally` |

---

## Explicitly **not** on this list

The following are placeholders of the localization itself, i.e. the actual work —
not legacy:

- ~~`detection/hello_world.py` and the profile `hello_world`.~~ **Removed** on
  28.09.2026: a start without a chosen job is now an error (`UNKNOWN_RECIPE`)
  instead of a silent placeholder job.
- ~~`frameId = "world"`, although no world system exists yet.~~ **Done**: the frame
  now follows the image — `world` as soon as a reference tag from the tag map is
  visible, otherwise the camera coordinate system of the source.
- ~~Poses fixed at `[0,0,0]` / `[0,0,0,1]`.~~ **Done** for `apriltag`; for
  `calibration` they remain zero, and that is correct there.
- ~~That `detect_apriltags.py` does not apply the distortion coefficients.~~
  **Done** in `tagloc.pose`: the corners are undistorted before `solvePnP`. The
  prototype `src/apriltag/detect_apriltags.py` was removed on 30.10.2026.
- **Hand-eye calibration**: calculation, file format and tool exist
  (`tagloc.handeye`, `python -m tagloc.cli.calibrate_handeye`). Open is the execution
  on the real hardware — `T_flansch_cam` is a constant
  (camera rigid on the robot) and has to be measured once. As long as no measured file
  lies under `data/handeye/cam_flange.json`, only the optical route takes effect:
  without a world tag in the image the flange camera delivers values in the camera CS.
  `auto_execute` remains `False` in any case until the calibration on the robot is
  proven.

See [`apriltag-lokalisierung.md`](../apriltag-lokalisierung.md).

---

## Order of removal — **completed on 21.09.2026**

The chain was laid out here and was worked through exactly in this way:

1. ~~**B4** (temperature display in the popup) and **B2** (relay)~~ — after that nothing
   read a temperature via the vision tree any more.
2. ~~**A2** (`CpuTemperatureResult`)~~ — with that **A1** was empty.
3. ~~**A1** (`2:VisionSystem`)~~ — since then the address space has been unambiguous, and **B6**
   (`legacy` branch) could fall.
4. ~~**A4**, **A5**, **A3**, **A6**~~ — the rest of the demo, including the namespace.
5. **C3** is already done (see section C).

Still open are **B1** (only still in the frontend), **B3**, **B7**, **B8** in the WSC repo as well as
**C2**, **C7** and section D. B1 depends on the mDNS switch: the addresses are
still constants, although the server now announces itself.

**D5 bit us on 22.09.2026.** The package move deleted `src/OPCUA/server.py`,
and the service on the Pi no longer started after a reboot — the unit points to
this path, and because it lies in no repo, it could not be checked during the restructuring
what it points to. It was fixed with a starter at the old location (**D11**),
not with two manual interventions on the Pis: an intervention that nobody versions
would be gone again at the next re-setup.

Lesson for the next restructuring: **an entry point that an unversioned deployment holds on to is not pulled out without a replacement.** As long as D5 is open, every
path under `src/` that the unit names is in effect a public interface.
