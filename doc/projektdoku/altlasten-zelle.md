# Legacy items, cell: audit of this repo (`apriltag`)

As of: 30.10.2026, branch `apriltag`, commit `d55df91`. Line numbers refer to this state.

This document replaces the cell parts of [`altlasten.md`](altlasten.md) and
[`altlasten-toter-code.md`](altlasten-toter-code.md); their open items have been re-checked and
carry the old ID here (column „ex-ID“). Counterpart for webskillcomposition:
[`altlasten-wsc.md`](altlasten-wsc.md) — it also contains the **world-tag chain across both repos**
(section 10). What each file does: [`code-dokumentation.md`](code-dokumentation.md), part A. Plan:
[`arbeitsplaene/aufraeumen-und-code-handbuch.md`](arbeitsplaene/aufraeumen-und-code-handbuch.md).

**Cleanup is done on branch `apriltag-aufraeumen`** (merged into `main` on 17.11.2026). What has been implemented is
listed at the end under „Umsetzungsstand“ (implementation status).

**Not touched during the cleanup:**

- hot files (column „heiß?“ (hot?)) — heavily in progress on `apriltag` or touched by the unmerged branch `usecase-foto`;
- the files reserved by the plan [`handeye-genauigkeit.md`](arbeitsplaene/handeye-genauigkeit.md) (status „in Arbeit“ (in progress)):
  `src/vision_server/handeye_session.py`, `src/tagloc/handeye.py`, `src/tagloc/cli/calibrate_handeye.py`,
  `src/tagloc/pose.py`, `src/tagloc/detector.py` — affects D4 and the decomposition of `finish()`;
- documented interfaces (OPC UA, payload, `tagmap.json`, CLI options).

## 0. Method and limits

**Scripts** (all under `/home/meric/.claude/jobs/ee1c32b3/tmp/agent-adp-tot/`, repo read-only):

| Script | Purpose |
|---|---|
| `imports.py` | import graph (src/tests/tools): who imports which module |
| `defs.py`, `cand.py`, `cand2.py` | 1821 definitions (function, class, method, constant, field); references separated into production, tests, strings, docstrings, comments, `__all__` |
| `reach.py`, `reach2.py` | name-based reachability from the module roots of production (`self.x` calls limited to the class family); finds chains of dead functions |
| `fields.py`, `params.py`, `cli_opts.py` | never-read fields, never-set parameters, never-read argparse options |
| `unused_imports.py`, `redef.py`, `stale_refs.py` | unused imports, multiple definitions, dead references in docstrings/comments |
| `dups_funcs.py`, `dups_near.py`, `dups_text.py`, `test_helpers.py` | normalized AST hashes (>= 5 lines), similarity (4-gram Jaccard >= 0.70), text blocks >= 4/6 normalized lines, test helpers with the same name |
| `uchecks.py`, `lang.py` | functions > 80 lines, `print`, `except`, `__all__`, language of docstrings/comments |
| `runcov.py`, `covreport.py`, `uncovered_*.py` | **measured line coverage**: complete suite (877 tests, green, 247 s) in a copy of the repo (`git archive`) with `sys.monitoring` (Python 3.12, nothing installed) |
| `refbranch.py` | findings on branch `apriltag-refactoring` |

Search commands: `git grep -nw <name> -- src tests tools doc config README.md '*.sh' '*.bat'` (word search),
plus targeted `git grep -n -E`.

**Limits:**
- Name-based: name collisions make code look "alive" (under-reported), never "dead". Dynamic access was
  checked (`getattr`, strings, asyncua callbacks). Three false alarms were ruled out this way:
  `SharedCamera.factory_calibration` (`getattr` in `detection/apriltag.py:451`),
  `CameraStreamConfig.overlay_interval_s/overlay_detect_interval_s` (`getattr` in `runner.py:137,140`),
  `EventCollector.event_notification` (callback from asyncua).
- The coverage only captures the main process; subprocesses (scripts of `ScriptDetectionSource`) are
  missing. Line coverage, not branch coverage.
- Not verifiable from the repo: hardware, systemd units (not versioned, legacy D5), OpenCV version on the
  Pis, use by the WSC frontend.
- The language heuristic (stop words) is rough; only orders of magnitude.

**Most important context before all tables:**
- **Branch `apriltag-refactoring`** (plan `refactoring-apriltag.md`, status „fertig“ (done), not integrated)
  contains 40 commits and already resolves many findings below. Reference each as "refactoring `<hash>`".
  It touches almost all hot files (`runner.py`, `detection/apriltag.py`, `stream_overlay.py`, `job.py`, ...).
  **The cleanup branch should not change the same places in parallel**, but build on it or only take
  findings that are missing there (column „refactoring“).
- **Branch `usecase-foto`** (2 commits, additions only) changes `runner.py` (+183), `detection/apriltag.py`
  (+5), `config.py` (+3).
- Column **hot?** = file is heavily edited on `apriltag` or touched by `usecase-foto`: `runner.py`,
  `detection/apriltag.py`, `server.py`, `stream_overlay.py`, `cellwatch.py`, `tagloc/overlay.py`,
  `tagloc/boards.py`, `config.py`.

---

## 1. Dead code (T)

Interface: OPC UA / payload / CLI / file = file format / no.

| ID | What | Where | Evidence | Conf. | Recommendation | Risk | Effort | Interface | ex-ID | hot? | refactoring |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 | **Prototype package** `src/apriltag/` (`calibrate_camera.py` 291 lines, `detect_apriltags.py` 107 lines, `MANUAL_TEST.md`) | `src/apriltag/` | `imports.py`: imported by no one; `git grep -n "src/apriltag"`: only docstrings (`tagloc/boards.py:3,277`, `calibration.py:8`, `pose.py:3`), `MANUAL_TEST.md` (marks itself „abgelöst“ (superseded)), docs (`apriltag-lokalisierung.md:173,460,676-685`, `apriltag-referenz.md:435`). Coverage 0 %. Writes/reads `calibration.yaml` (`FileStorage`), which is read nowhere else and not tracked. The deletion condition of the docs („erst nach einmaligem Lauf der neuen Kette an der echten Pi-Kamera“ (only after one run of the new chain on the real Pi camera), `apriltag-lokalisierung.md:683`) is met: `vision-server-interface.md:1333-1338` (hand Pi really calibrated with `tagloc.cli.calibrate`, RMS 0.2945 px, 21 captures) | certain | delete folder; carry references along (3 docstrings, 2 doc places, `altlasten.md:116-118`); then check whether `pupil-apriltags` is still needed (T13) | low | S | no | ex T1, P2 | no | stays there |
| T2 | `poses_to_json` | `tagloc/cli/_common.py:229` | `git grep -nw poses_to_json`: only definition. Docstring names `transform.py`, which does not use it | certain | delete (or switch `transform.py` to it) | minimal | S | no | ex T4 | no | used there by `transform` (b679606), finding obsolete |
| T3 | `SharedCameraSource`, `iter_frames`, `FrameSource` (Protocol, only in `__all__`), `ImageFolderSource.current_path` | `tagloc/frames.py:20,52,174,222` | `git grep -nw`: only definition and `__all__` (`frames.py:254-261`); no docs | certain | delete | low | S | no | ex T2, T3 | no | removed (7e67251) |
| T4 | `build_detection_source` (singular) | `vision_server/detection/__init__.py:51` | only definition and `__all__:70`; used is `build_detection_sources` | certain | delete | low | S | no | ex T5 | no | removed |
| T5 | `Program.get_current_state` | `ua_program/program.py:162` | only definition (supervisor's template; template no longer in the repo, see B6) | certain | delete | low | S | no | ex T6 | no | still there (program.py:169) |
| T6 | `world_tag_for_robot`, `robot_locations`, `_rank`, `merge_locations` (chain, used only by tests) | `tagloc/localize.py:474,483,508,513` | `reach2.py`: not reachable from production; hits only `tests/test_welttags.py:29-30,370,381,414,422`. Docs describe them as API (`apriltag-lokalisierung.md:155,163`, `apriltag-referenz.md:490-500`); Layer 2 control now lies with the WSC frontend | certain (production dead) | **decision needed**: delete along with test and docs, or deliberately keep as library and mark it as such in the docs | medium (tests, docs) | M | no | ex T7, P4 | no | bundled, not deleted (4e27abf); `robot_locations` deliberately kept |
| T7 | `estimate_tag_pose` (singular), `CameraCalibration.camera_params` | `tagloc/pose.py:151`, `calibration.py:66` | production uses `estimate_tag_poses` or computes itself; hits only in tests and docs | probable | keep as test/doc API or delete along with tests; decide after T6 | low | S | no | new | no | – |
| T8 | **Tools without entry point**: `vision_server/tools/list_realsense_profiles.py` (59 lines), `pi_load_profiler.py` (465 lines) | `src/vision_server/tools/` | `git grep -nw`: no reference in code, tests, docs (except the old audit). `list_realsense_profiles` duplicates the profile loop from `camera._list_realsense_color_profiles` (D9). Coverage 0 % | probable | `list_realsense_profiles`: delete or switch to shared helper; `pi_load_profiler`: ask the user (hand tool for Pi load), otherwise delete | low | S | no | ex T8 | no | no longer listed in `tools/` on the branch |
| T9 | **Dead config fields**: `AprilTagProfileConfig.use_picamera`, `.camera_index`, `.warmup_s`; `VisionServerConfig.configuration_id` | `profiles.py:33,34,43`; `config.py:44` | `use_picamera`: no hit except definition. `camera_index`/`warmup_s`: only readers `camera.py:437,515,651` on the `CameraStreamConfig`, no read access on an AprilTag config. `configuration_id`: only default `"hello-world-config"` (rest of the removed profile); the source builds its own value (`detection/apriltag.py:308`) | certain (`use_picamera`, `configuration_id`), probable (rest) | delete fields; check kwargs in tests | low | S | no | new (to C4) | `config.py` yes (usecase-foto) | `config.py` -1 line (probably `configuration_id`) |
| T10 | **Unused imports** (src): `timezone`; `DI_DEVICE_HEALTH_ALARMS`; `time`; `invert`, `rvec_from_rotation` | `tagloc/overlay.py:22`; `asset_model.py:29`; `calibration_session.py:42`; `handeye_session.py:48,51` | `unused_imports.py`, each confirmed with `grep -nw` (`DI_DEVICE_HEALTH_ALARMS` only import + definition `nodeset_ids.py:57`, so the constant is dead too). Tests: `test_calibration_session.py:33`, `test_cellwatch_posestore.py:8,12,19`, `test_reference_edges.py:37`, `test_tagreference_liveview.py:9,22` | certain | remove, take constant `nodeset_ids.py:57` along | minimal | S | no | new | `overlay.py` yes | partly (e63904f, `nodeset_ids.py` -4 lines) |
| T11 | **References to things not (or no longer) present** | `jobs/calibrate.py:22` („`OPCUA/server.py`“); `discovery/mdns.py:3`, `ua_program/program.py:3` (`betreuer/...`: folder removed since 69bf237 on 23.09., template never tracked); `camera_health.py:133`, `tools/calibration_client.py:23` (`doc/vision-server-interface.md`, correct `doc/projektdoku/...`); `server.py:116` (`tools/diagnose_board.py`, correct `src/vision_server/tools/`); `requirements.txt:8,13` | `stale_refs.py` + `git ls-files`/`git log -- betreuer` | certain | correct the texts (pure comments) | none | S | no | ex T9 | `server.py` yes, others no | `jobs/calibrate.py` rewritten there |
| T12 | **OpenCV < 4.7 fallbacks** (`CharucoBoard_create`, `Dictionary_get`, `interpolateCornersCharuco`, `drawMarker`, older `DetectorParameters`) | `boards.py:81-87,108-114`; `detector.py:73,189-193,237`; `pose.py:126`; `tools/make_*` | `requirements.txt` requires `opencv-contrib-python>=4.8.0`; `# pragma: no cover`; coverage: never executed | **uncertain** (Pi OpenCV not verifiable; legacy D2/D3 says „4.x auf dem Pi“ (4.x on the Pi)) | first determine the Pi OpenCV version; if >= 4.7, delete the branches | medium | S | no | to D2/D3 | `boards.py` yes | – |
| T13 | **pupil backend** `PupilAprilTagDetector` including `--backend pupil/auto` and dependency `pupil-apriltags` | `tagloc/detector.py:251,287-316`; `cli/_common.py:86`; `profiles.py:49`; `requirements.txt:6` | production sets only `"aruco"`; `git grep -i pupil tests` empty; class never executed | probable | remove backend and dependency together, **or** add a test with a dummy | medium (OpenCV without tag family, legacy D3) | M | CLI (`--backend`) | ex P1 | no | – |
| T14 | **Never-set options** (selection) `events.fire_result_ready(is_partial)` `events.py:59`; `VisionStateMachines.recover(halt=True)` `state_machine.py:167`; `JobRunner.cancel_running(timeout)` `job.py:186`; `mdns.announce(address, caps)` `mdns.py:89`; `draw_tag_overlay(show_distance)` `overlay.py:192`; `leave_one_out_errors(huber_delta_px)` `handeye.py:695`; `PupilAprilTagDetector(nthreads)` | `params.py`: set at no call site (production and tests). `is_partial` = legacy C2; `recover(halt)` maps a spec transition | probable | simplify only the two without spec reference (`cancel_running`, `show_distance`); leave the rest as documented defaults | low | S | OPC UA only for `is_partial` | ex C2 | `overlay.py` yes | – |
| T15 | **Artifacts**: `board.svg` (ChArUco 7x5, 41 KB, root); `hardware/Hardware_Architektur_v2(2).drawio.png`; two `data/rohbilder/cam_ceiling_20261030-*.png` (`141931`, `141939`) | root, `hardware/`, `data/rohbilder/` | `board.svg`: only example output of `make_tag_sheet.py`; the Pis use chessboard 7x10 („kein ChArUco“ (no ChArUco), `server.py:140`). PNG: referenced by no file. Raw images from 30.10.: no hit in code/docs/tests (`git grep cam_ceiling_2026`); tests need only `...20261019-114704/142133` (`tests/test_detector.py:29,34`) | probable | remove `board.svg` (regenerable); keep raw images only if they are a measurement series (see B1) | low | S | no | ex P9 | no | – |

**Checked and deliberately NOT reported as dead:**

| What | Reason |
|---|---|
| `src/OPCUA/server.py` (ex D11) | entry point of the non-versioned systemd unit (legacy D5 open); nobody imports it, which is intentional |
| `vision_server/__main__.py` (`python -m vision_server`) | second server start (port 4841) for development, documented (`vision-system.md:37,178`, `vision-system-next-steps.md:25`); `build_config` untested |
| `src/jobs/calibrate.py` | check job of recipe `calibration` (OPC UA), started by `ScriptDetectionSource` via `JOBS_DIR` (`detection/__init__.py:12,20`); see section 3 |
| `tools/make_synthetic_scene.py` (`render_reference_scene`, `_polygon_basis`, ...) | imported by tests (`from tools.make_synthetic_scene import ...`), test oracle |
| `vision_server/tools/{calibration_client,handshake_client,stream_viewer,diagnose_board,measure_framerate}.py` | named in docs as reference clients/hand tools (`vision-server-interface.md:520,1266-1270`, `frontend-integration-kalibrierung.md:239-247`, `deckenkamera-volle-aufloesung.md:126`) |
| `DeviceHealth.MAINTENANCE_REQUIRED`, `VisionAssetNodes.computing_device` | read only by tests, but part of the OPC-40100-2 model |
| `tagloc.cli.*` (5 CLIs) | named as start commands in 8+ doc files; no tests (section 4) |
| `data/LV1/` (40 JPG, 79 MB) | no code reads them, not re-obtainable; named in docs as false-alarm reference (`apriltag-referenz.md:1041`) |

**Carry-over from the legacy lists (cell):**

| Old ID | State today |
|---|---|
| T1–T7, T9, P1, P2, P4, P9 | see T1–T15 (all still present; T3/T4 removed only on refactoring) |
| T8 | `list_realsense_profiles` and `pi_load_profiler` still without reference; `measure_framerate.py` is named in `deckenkamera-volle-aufloesung.md:126`, so it is not dead |
| P3 (three ways to the camera pose) | still all used; not examined further |
| P7 (`data/LV1`), P8 (raw images 19.10.) | LV1: keep; raw images 19.10.: in use |
| C2 (`IsPartial` fixed) | still so: `result_management.py:31,115`, `events.py:59,76` |
| C7 (LDS URL in code) | still so: `discovery/lds.py:63-68` (`CELL_LDS_URL`, `DEFAULT_LDS_URL`: two names for the same value); overridable via `OPCUA_LDS_URL` |
| D1 (`.gitignore`) | still so, see B2 |
| D4 (`setup.sh/.bat` not a project setup) | still so: only ShareLaTeX remote and aliases |
| D5 (systemd unit not versioned) | open; keeps `src/OPCUA/server.py` alive |
| D2/D3 (OpenCV 4.x on the Pi unverified) | open, see T12 |
| D11 (`src/OPCUA/server.py`) | keep (see above) |

---

## 2. Duplications (D)

| ID | What | Where | Evidence | Conf. | Which variant stays / change | Risk | Effort | Interface | ex-ID | hot? | refactoring |
|---|---|---|---|---|---|---|---|---|---|---|---|
| D1 | **`BoardSpec` built from the camera config, 3 copies** | `calibration_session.py:240`, `detection/apriltag.py:930` (`default_board_spec`), `stream_overlay.py:253` (`_board_spec`) | `dups_text.py`: 6–7 identical lines; `stream_overlay.py:245` itself says „dieselbe Quelle wie `CalibrationSession._spec()`“ (same source as `CalibrationSession._spec()`) | certain | one function `board_spec_from_config(config)` (lazy import of `tagloc.boards`, so `profiles.py` stays free); switch the three callers | low | S | no | new | yes (apriltag, stream_overlay) | done 743e473 |
| D2 | **NaN/Inf -> `None` helper in 5 variants** | `handeye_session.py:173`, `measureboard.py:89`, `measuretag.py:240` (all three byte-identical, `dups_funcs.py`); `detection/apriltag.py:107` (rounds to 4 digits); `tagloc/handeye.py:854` (rounds to 6 digits); inline 4x in `handeye_session.py:702-705`; `observations.py:82` | see "Where" | certain | one helper `finite_or_none(value, digits=None)` in a stdlib module; rounding as parameter, because it touches the file format (`handeye.py` writes 6 digits) | low, mind rounding | S | file, payload | new | `apriltag.py` yes | done 5bce4f4 (`tagloc/jsonio.py`) |
| D3 | **Read JSON request and write result**: prelude `json.loads` -> „Kein JSON“ (No JSON) -> schema check 5x; `_is_int/_is_number/_numbers` 3x; `_iso` 3x; `dumps(..., allow_nan=False, default=float)` 5x (+`payload._dumps`); private cross-imports `_load/_pose` (`handeye_session.py:69`), `_parse_board` (`runner.py:1247`) | `cellwatch.py:557`, `layer2/plan.py:380-396`, `measureboard.py:70,85,202`, `measuretag.py:66,98,247,251`, `tagreference.py:72-76,101,325,425` | `git grep "Kein JSON"` (5 hits); `measureboard.py` already imports parts from `measuretag.py`, but copies `_iso/_finite/dumps` | certain | shared module `vision_server/jsonio.py` (parser prelude, `dumps`, `iso`); move `_parse_board` to `measureboard` | low (error texts are bound by tests) | M | payload (error texts) | new | `runner.py` yes, others no | „Kein JSON“ there 0 hits, done (3874bda, fdc170f) |
| D4 | **Hand-eye refinement twice**: pipeline undistort -> `refine_hand_eye_robust` -> outliers -> leave-one-out -> `reconstruction_spread` once in the server (`_refine_board`), once in the CLI (`solve_session`); `SESSION_SCHEMA` twice (`calibrate_handeye.py:75`, `handeye_session.py:78`); writer and reader of `session.json` without shared definition | `handeye_session.py:193-243`; `tagloc/cli/calibrate_handeye.py:143-266` (124 lines) | reading both functions; CLI docstring: „rechnet dieselbe Kalibrierung neu“ (recalculates the same calibration) | certain | pipeline as one function in `tagloc/handeye.py`, server and CLI call it; schema constant moves there; CLI only prints | medium (calculation path, CLI without test) | M | file (`session.json`) | new | no | CLI there split and tested (`test_cli_calibrate_handeye.py`) |
| D5 | **Read and check pose from `{position, orientation}`**: 3 variants with null-quaternion test (`< 1e-9`) | `layer2/plan.py:369-376`, `tagreference.py:111-120`, `detection/apriltag.py:75-107` (+ `geometry.pose_from_dict`, unchecked) | `git grep 1e-9` | probable | **not to unify blindly**: refactoring deliberately rejected this (9f56bb2: error behavior differs); at most one checked variant for the two JSON cases | medium | S | payload | new | `apriltag.py` yes | deliberately not done |
| D6 | **OPC UA method wrapper three times**: `refusal()` + guards (parse -> Operational -> busy) + `add_method([String],[String,Int32])` | `runner.py:1233-1286` (MeasureBoard), `1300-1366` (MeasureTags), `1370-1547` (TagReference) | `dups_text.py` (6-line blocks 1223/1234/1301/1371/1712) | certain | helper `json_method(...)` in `ua_nodes.py`; decomposition see section 7 | medium (many OPC UA tests) | M | OPC UA (keep behavior the same) | new | **yes** | `ua_nodes.add_named_variable` (28c0365), method part not there |
| D7 | `start_single_job` / `start_continuous` (admission, 95 % identical), `_run` / `_run_continuous` (75 %) | `job.py:230-285,287,359`; `runner.py:487-549` (wrappers on top) | `dups_near.py` | certain | shared admission step; wrappers in `runner.py` likewise | low (well tested: `test_job_runner.py`) | M | OPC UA | new | `job.py` no, `runner.py` yes | done (5d1714b) |
| D8 | **OPC UA test clients**: arguments `--url/--namespace/--vision-system`, `Client(...)`, `get_namespace_index`, `get_node(NodeId(...))`, `main()` body | `tools/calibration_client.py:112-129`, `handshake_client.py:116-135`, `stream_viewer.py:90-108`, `diagnose_board.py:41-111` | `dups_near.py` 0.79; `dups_text.py` | certain | **low priority**: `calibration_client.py:23` deliberately wants to stay a „reiner OPC-UA-Client ohne `vision_server`-Import“ (pure OPC UA client without `vision_server` import) (reference to copy from). At most a shared argument group | low | S | no | new | no | – |
| D9 | **RealSense profile query** | `camera.py:113-138` and `tools/list_realsense_profiles.py:27-37` | `dups_text.py` (5 lines); same loop `query_sensors -> get_stream_profiles -> color` | certain | helper in `camera.py`, tool calls it; or delete tool (T8) | low | S | no | new | `camera.py` no | – |
| D10 | **Constants in several places** (table below) | scattered | `git grep` | certain | define centrally | low | M | CLI defaults, file format | new | partly | `DEFAULT_TAG_FAMILY` (96a7224) |
| D11 | **Frame/Pi identity duplicated, with deviation**: `jobs/calibrate.py:26` knows the hostnames `pi-decke`/`pi-hand`, `server.py:73-74` (`PI_IDENTITIES`) the real `ADP-Roboter-Lokalisierung`/`ADP-HandInEye-Kamera-Pi`; paths `data/calibration/<frame>.json`, `config/tagmap.json` are built again in `jobs/calibrate.py:34-35` and `server.py:311-312` | `jobs/calibrate.py:21-35`; `server.py:70-84,300-312` | word search for `pi-decke` and `ADP-Roboter`; `git log -S` shows: `server.py` was switched on 21.09., `jobs/calibrate.py` was not. Consequence: frame „world“, recipe `calibration` reports „KEINE KALIBRIERUNG unter .../world.json“ (NO CALIBRATION under .../world.json), unless `VISION_FRAME_ID` is set in the (unversioned) unit (**effect uncertain**) | certain (drift), uncertain (effect) | identity in a stdlib module (without asyncua), both use it; or the server passes frame and paths as job parameters | low | S | OPC UA (recipe `calibration`, text output) | new | `server.py` yes | fixed (f856b9b: frame and paths from the server) |
| D12 | **Test helpers multiple times**: `pose()` in 6 test files (3 byte-identical: `test_handeye/tag_map/welttags`; 2 same in content), `FakeCamera` 6 variants, `FakeNode` 4, `FakeServer` 3, `build()` (`test_asset_model.py:36` = `test_part10_fassade.py:31`), classes `ResultTest/SourceTest/OpcUaTest/RequestTest` in `test_measuretag/measureboard/tagreference` (11-line block `test_measuretag.py:293-306` = `test_tagreference.py:844-857`) | `tests/` | `test_helpers.py`, `dups_funcs.py tests` | certain | `tests/_support.py` with `pose()`, `FakeNode`, `build()`; do not force the `FakeCamera` variants (different purposes) | low | M | no | new | no | done (1c938f6, 9a7942f) |
| D13 | **Marker/board generation**: `tools/make_tag_sheet.py:39-85` (`_dictionary`, `render_marker`, `render_charuco`) repeats `detector.predefined_dictionary` (`dups_near` 0.88) and `boards.build_board` including version switches; `generateImageMarker/drawMarker` also in `make_synthetic_scene.py:287` | `tools/` | reading | probable | tool imports from `tagloc`. **Exception**: `make_synthetic_scene.chessboard_object_points` (:595) stays independent, it is the oracle for `boards.py` (docstring) | low | S | no | new | `boards.py` no | done (29c0e6e); renderer moved to `tagloc/synthetic.py` (e147473): question whether a test oracle belongs in the library |
| D14 | **Cancel task and wait** (`cancel()`, then `await` with `suppress(CancelledError)`) 5x; `calibration_session._pool/_save_pool` identical except for names | `camera.py:680`, `camera_health.py:233`, `camera_stream.py:364`, `cellwatch.py:1162`, `runner.py:444`; `calibration_session.py:250-271` | `dups_funcs.py` (`stop` = `_halt`) | certain | helper `cancel_and_wait`, `SerialExecutor` | low | S | no | new | `cellwatch`, `runner` yes | done (0fd39f0, 9ea0b9a) |
| D15 | **Overlay** checked: **no real duplication** between `tagloc/overlay.py`, `reference_overlay.py`, `stream_overlay.py`. Only: `reference_overlay.py:21` imports private `_scale_for`; `stream_overlay.py:294,329,364,603` imports `_resize_for_stream` from `camera_stream` four times inline; `_FONT_SCALE` twice with different values (0.5 / 0.55) | see "Where" | reading | certain | make `_scale_for` and `_resize_for_stream` public | low | S | no | new | yes | – |
| D16 | **Trivia**: timestamp `%Y%m%d-%H%M%S` in `raw_image.py:21` and `tagreference_log.py:40`; UTC `Z` timestamps in `handeye.py:827` and `handeye_session.py:273` | see "Where" | `git grep strftime` | certain | optional, with D3 | low | S | file format (keep format) | new | no | – |

Two detector backends (ex P1): see T13.

**D10: constants present multiple times**

| Constant | Places |
|---|---|
| Tag family `"tag36h11"` | 13x: `cli/_common.py:84`, `cli/calibrate_handeye.py:103`, `detector.py:76,159,259,281`, `tagmap.py:131,343`, `profiles.py:41`, `make_synthetic_scene.py:316,458,751,867` (refactoring: `DEFAULT_TAG_FAMILY`, 96a7224) |
| Standard tag size `0.05` | 6x: `cli/_common.py:92`, `cli/calibrate_handeye.py:140`, `overlay.py:198`, `pose.py:169`, `tagmap.py:503`, `profiles.py:42`; the Pis override with `0.080` (`server.py:90,139`) |
| Board standard 9x6, 30 mm, marker 22 mm | 5x: `boards.py:34-39`, `cli/calibrate.py:39-43`, `profiles.py:112-117`, prototype, `board.svg` (ChArUco 7x5). **The Pis use 7x10, 23 mm** (`server.py:127-129,148-150`). The docs still name ChArUco 7x5 (`apriltag-e2e-test.md:148,232`, `apriltag-referenz.md:702`) or 6x7 (`vision-server-interface.md:1330`) |
| Minimum captures `15` / `3` | `15`: `cli/calibrate.py:32`, `profiles.py:120` (+ prototype); `3` as literal: `cli/calibrate.py:77,152`, `boards.py:433,452`; constant `calibration_session.py:64`; `handeye.py:57` (`MIN_SAMPLES = 3`, different meaning, same name as CLI `MIN_SAMPLES = 15`) |
| `REPO_ROOT` | 4x: `jobs/calibrate.py:17`, `profiles.py:16` (`_REPO_ROOT`, imported by `config.py:6` and `runner.py:28`), `server.py:219`, `tools/measure_nodeset_import.py:20` |
| Paths `data/calibration/<frame>.json`, `config/tagmap.json` | `jobs/calibrate.py:34-35`, `server.py:311-312`, `profiles.py:19-21` (default `camera.json`) |
| Endpoint path `/raspi/server/`, `127.0.0.1:4840` | `server.py:58` (source), 4 clients (`--url` default), tests |
| Namespace URI `http://launch-rm.de/vision` | `config.py:41`, 4 clients, 8 test files |
| Resolutions `(2028,1520)`, `(640,480)` | `frames.py:112,148,205,207`, `profiles.py:35,213`, `server.py:145` |
| `"cam_ceiling"`/`"cam_flange"` | 14x in `server.py`, 14 further places in 10 files |
| Schema strings `"wsc.vision.*"` | 14 files; `SESSION_SCHEMA` twice (D4) |

---

## 3. Calibration

The user found „3 Kalibrier-Skripte“ (3 calibration scripts): most likely `src/apriltag/calibrate_camera.py`,
`src/jobs/calibrate.py` and `src/tagloc/cli/calibrate.py`. Including hand-eye, there are five programs
with „calibrate“ in the name.

| File | Purpose | Entry point (who calls how) | Status | Doc references (excluding legacy lists) | Tests (coverage) | Recommendation |
|---|---|---|---|---|---|---|
| `src/apriltag/calibrate_camera.py` | Prototype: interactive calibration with `cv2.VideoCapture`, writes OpenCV YAML | by hand `python src/apriltag/calibrate_camera.py`; nobody imports it | **superseded** (`MANUAL_TEST.md:3-9` says so itself) | 1 file (`apriltag-lokalisierung.md:460,676`) | none (0 %) | **delete** (T1) |
| `src/apriltag/detect_apriltags.py` | Prototype detection, without undistortion | by hand | superseded | `apriltag-referenz.md:435` | none (0 %) | delete (T1) |
| `src/jobs/calibrate.py` | **not a calibration**, but a readiness check: calibration present? resolution, model, RMS, age, tag map | OPC UA recipe `calibration` -> `detection/__init__.py:20-21` -> `ScriptDetectionSource` starts `sys.executable jobs/calibrate.py` as a subprocess; stdout becomes `attributes.message` | **active, check job** (interface: `vision-server-interface.md:242`) | 3 files | none (0 %; `test_script_runner` only starts throwaway scripts) | **keep, correct**: hostnames outdated (D11); hint text names `--board charuco` (`:41`), while the Pis use chessboard 7x10; name misleading (renaming to `calibration_check.py` possible, only the `JOBS_DIR` entry changes); add test |
| `src/tagloc/cli/calibrate.py` | camera calibration on PC/Pi, interactive (window) or from an image folder | `python -m tagloc.cli.calibrate`; **stop the server beforehand** (it opens the camera itself) | **active as a tool** (offline, fallback path; really used on the hand Pi, `vision-server-interface.md:1333`) | 5 files | none (0 %); calculation core `boards.calibrate_from_samples` is tested | **keep**, fix defects: header names the key `S = save` (`:3`), which does not exist (keys: space bar, C calculates and saves, Q); `--capture-to` only works in window mode (`:110-149`); default geometry 9x6/30 mm does not fit the Pis; docs still name ChArUco; literal `3`; split (refactoring 185b278) |
| `src/tagloc/cli/calibrate_handeye.py` | hand-eye `T_flansch_cam`: (a) `--samples` tag capture list, (b) `--session` offline recalculation of the hand Pi session | `python -m tagloc.cli.calibrate_handeye` | **tool**; (a) is the **older way** (`handeye-genauigkeit.md:151` „alter Weg“ (old way)); for `wsc.vision.handeye.samples/1` there is no writer in the repo; (b) current | 7 files | none (0 %); calculation core `tagloc/handeye.py` 97 % | (b) keep and merge with the server path (D4); (a) after consultation delete or mark as „Legacy“; add test |
| `src/vision_server/calibration_session.py` | camera calibration **in the running server** via OPC UA (`StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration`, `CalibrationProgress`); reads from the shared `SharedCamera` | `runner.py` builds the instance; frontend calls the methods | **active, production path** | 6 files | `test_calibration_session.py` (93 %) | keep; `calibration_min_samples` is only displayed as `minSamples` (`:371`), the calculation threshold is the constant 3 (`:64`): clarify name/docs (B11) |
| `src/vision_server/tools/calibration_client.py` | test client/reference: start, progress, finish via OPC UA | by hand | **tool (reference)**, not superseded | 2 files | none (0 %) | keep; correct the outdated path in `:23` |
| `vision_server/tools/stream_viewer.py`, `diagnose_board.py` | capture with the space bar; try out board geometry | by hand | `stream_viewer` active; `diagnose_board` **partly superseded** by `MeasureBoard(diagnose=true)` (`measureboard.py:43`, `boards.diagnose_board`, `vision-server-interface.md:1723`); `DEFAULT_COMBOS` (`:30`) do not know the board 7x10 (since 28.10.) | 2 files each | none | `diagnose_board`: update the combinations or delete, if the `MeasureBoard` diagnosis is sufficient |
| `src/tagloc/calibration.py`, `boards.py`, `lens.py` | data format (JSON), board detection and calculation core, lens models | used by all of the above | **active, shared core** | 2 files (`calibration`) | 100 % / 73 % / 95 % | keep; ChArUco path (`boards.py:66-87,104-117,439-445`) untested and unused on the Pis |
| `vision_server/handeye_session.py`, `tagloc/handeye.py` | hand-eye per OPC UA (session, refinement, anchoring) and calculation core | `runner.py`; frontend | **active, production path** | 4 files | 93 % / 97 % | keep; resolve the duplicate pipeline in the CLI (D4); `finish()` has 192 lines |

**Verdict:**
- **Really duplicate and superseded:** the prototype `src/apriltag/` (T1). It is replaced by `tagloc` and can go.
- **Not a duplicate, but a complement:** `tagloc.cli.calibrate` (tool, exclusive camera, also image folder) and `CalibrationSession` (in the server, frontend-driven) compute with the same core (`boards.calibrate_from_samples`, `lens.calibrate`). Only the side items are duplicated: board default values (D10), minimum counts, `BoardSpec` construction (D1).
- **`src/jobs/calibrate.py` is not a calibration**, but a check job in recipe `calibration`: keep, fix the hostname error (D11), consider renaming.
- **Hand-eye:** two calculation paths for the same pipeline (D4) and an older input path (`--samples`) without a writer in the repo.
- **Actually to delete:** only `src/apriltag/` (3 files). Everything else is active or a tool, with the corrections named.

---

## 4. Test gaps (L)

Measured: full run 877 tests, statement coverage per module (`covreport.json`). „Kein Test importiert es“
(No test imports it) comes from `imports.py`; coverage can still be > 0 (indirectly via `runner`).

| ID | Gap | Evidence | Productive? | Recommendation | Effort | hot? |
|---|---|---|---|---|---|---|
| L1 | **`vision_program.py` (Part 10: `VisionProgram.start/halt/reset/_on_job_finished/_finish/_read_parameter/_write_results`)**: 50 % coverage, no test imports it; `test_part10_fassade.py` only checks the address space | `covreport`: named methods 0 hits | **yes**, entry point of the generic operator interface | OPC UA test with `Start` via `VisionProgram` (refactoring has `test_ua_methoden.py`, 564 lines, as template) | M | no |
| L2 | **`tagloc/cli/*`** (5 CLIs, `_common` 27 %) completely without test | 0 % | no (tool), but start commands in 8+ docs; `calibrate_handeye --session` contains the refinement pipeline (D4) | smoke tests with synthetic scenes (`tools/make_synthetic_scene`) | M | no |
| L3 | `src/jobs/calibrate.py` (0 %) | contains the hostname error (D11) | **yes** (OPC UA recipe) | test with a temp folder | S | no |
| L4 | **Hardware opening in `camera.py`**: `_open_picamera2` (15 call sites), `_open_realsense` (12), `_open_opencv`, `_log_metadata`, `factory_calibration` (RealSense intrinsics of the hand Pi), `_list_realsense_color_profiles`; module 73 % | `uncovered_funcs.py` | **yes** | mocks for `picamera2`/`pyrealsense2` (imports are lazy) | M | no |
| L5 | **Start wiring**: `server.main()` (21 call sites), `application_uri`, `runner.run()`, `_start_mjpeg_server`, `_write_calibration_info`; `vision_server/__main__.py` (0 %) | 0 hits | **yes** | smoke test `main` with camera/LDS switched off | M | yes |
| L6 | **`ua_program/program.py`** 62 %: `_start/_suspend/_resume/_halt/_reset_method`, `_transition`, `running_to_*` without hits; no test imports it | `uncovered_funcs.py` | yes (base of `VisionProgram`) | together with L1 | M | no |
| L7 | `tagloc/frames.py` 42 %: `ImageFolderSource`, `VideoCaptureSource`, `PiCameraSource`, `RealSenseSource`, `SingleImageSource` | 0 hits in `__init__/read` | no (CLI) | image folder test with temp images; rest mocks | S | no |
| L8 | **ChArUco path** (`boards.build_board`, `detect_board` Charuco, `calibrate_from_samples` Charuco), `compute_coverage`, parts of `diagnose_board`; `PupilAprilTagDetector` | `boards.py:68-85,104-117,284-295,439-445`, `detector.py:260-277` | no (Pis: chessboard) | decide together with T12/T13 (test or delete) | S | `boards.py` yes |
| L9 | `discovery/mdns.py` 38 % (`detect_lan_ipv4`, `announce` core), `detection/script_runner.py` 77 % (error paths) | `uncovered_lines.py` | yes (network registration) | mock for `zeroconf`/socket | S | no |
| L10 | `state_machine.py` 68 % (`enter_operational`, `recover`), `runner.py` 64 % overall (start/stop glue), `CellWatch._loop`, `stream_overlay._status_for_current_preview` | `uncovered_funcs.py` | yes | targeted via L5 | M | yes |
| L11 | `tagloc/overlay.py`: `Window`, `has_display`, `summarise` (CLI display) | 0 hits | no | low priority | S | yes |
| L12 | all `vision_server/tools/*` (7), `tools/make_tag_sheet.py`, `tools/measure_nodeset_import.py`, `src/OPCUA/server.py` | 0 % | no | at most an import smoke test; for `src/OPCUA/server.py` a test „importiert `vision_server.server.main`“ (imports `vision_server.server.main`) (secures the unit entry point) | S | no |

Well covered (> 93 %): `tagloc/{calibration,geometry,handeye,localize,tagmap,reference_edges,reference_overlay,lens,observations,identity,modes}`, `vision_server/{calibration_session,handeye_session,cellwatch,tagreference*,layer2/*,measuretag,measureboard,payload,profiles,config,events,errors,result_management,discovery/lds}`.

---

## 5. Guidelines (U)

| ID | Finding | Evidence | Recommendation | Effort | hot? |
|---|---|---|---|---|---|
| U1 | **Functions > 80 lines: 26 of them**: `runner.install_vision_machine` 625 lines (:458), `reference_edges._measure_one_impl` 224 (:545), `address_space.attach_vision_system` 203 (:185), `handeye_session.finish` 192 (:416), `runner._install_tag_reference` 178 (:1370), `reference_edges.plan_reference_view` 147 (:1047), `make_synthetic_scene.render_reference_scene` 138 (:450), `apriltag.acquire_and_detect` 131 (:1227), `calibrate_handeye.solve_session` 124 (:143), `apriltag.open` 122 (:326), `handeye.refine_hand_eye` 119 (:457), `cli/detect.main` 119 (:74), `runner._install_cell_watch` 113 (:1607), `vision_program.install_vision_program` 112 (:200), `apriltag.measure_tag_reference` 109 (:549), `measureboard.build_result` 104 (:96), `tagreference.parse_request` 102 (:90), `asset_model.attach_asset_model` 100 (:291), `pose.solve_camera_from_world_tags` 94 (:272), `pi_load_profiler.run` 89 (:350), `localize.localize_camera` 87 (:152), `mdns.announce` 85 (:89), `stream_overlay.annotate` 84 (:266), `reference_edges.measure_reference_lines` 81 (:771), `fit_in_plane_correction` 81 (:920) | `uchecks.py`; refactoring split `apriltag.open/acquire_and_detect` (0dceb9c), `solve_hand_eye` (b115823), `calibrate.main`, `detect.main` (185b278) | section 7; non-hot files first | M–L | about half yes |
| U2 | **English instead of German docstrings/comments** (CLAUDE.md: German). Stop-word count: EN 3 897 against DE 11 558, i.e. roughly a quarter English. **Predominantly English (18 files):** `tagloc/localize.py` (EN 474/DE 135), `tools/make_synthetic_scene.py` (332/24), `tagloc/tagmap.py` (330/80), `tests/test_tag_pipeline.py` (159/29), `tagloc/geometry.py` (121/6), `tests/test_welttags.py` (113/75), `tagloc/frames.py` (72/23), `tagloc/observations.py` (64/9), `tests/test_layering.py`, `tagloc/cli/_common.py`, `tests/test_tag_map.py`, `tests/test_tag_geometry.py`, `tools/measure_nodeset_import.py`, `src/jobs/calibrate.py` (31/0), `tagloc/cli/detect.py`, `tagloc/modes.py`, `tools/make_tag_sheet.py`, `tagloc/identity.py`. **Mixed:** `detection/apriltag.py` (227/523), `tagloc/overlay.py` (138/126), `asset_model.py`, `detector.py`, `pose.py`, `calibration.py`, `tests/test_apriltag_source.py`, `tests/test_camera_owner.py` | `lang.py` (heuristic) | translate into German, file by file, **last**, and only in files that are currently not hot; pure comment work | L | `apriltag.py`, `overlay.py` yes |
| U3 | **`print` in library code: none.** All `print` calls are in CLIs, tools and `src/jobs/calibrate.py` | `uchecks.py` | nothing to do | – | – |
| U4 | **Bare `except:`: none.** `except Exception` without log and without `raise` (10): `jobs/calibrate.py:49` (error is reported, ok), `pose.py:256` (deliberate), `ua_program/program.py:210`, `asset_model.py:124`, `camera.py:137` (diagnostic, documented), `handeye_session.py:638,697` (`_intrinsics_source`, `_current`: errors silent), `tools/measure_framerate.py:89`, `vision_program.py:110`, `tools/measure_nodeset_import.py:70` | `uchecks.py` | only `handeye_session.py:638,697`, `asset_model.py:124`, `program.py:210` get `_log.debug/warning`; the rest is justified | S | no |
| U5 | **Magic constants present multiple times** | table D10 | define centrally (with D10) | M | partly |
| U6 | **`__all__` does not match (4 modules):** public but not listed: `detector.apply_detector_parameters`, `frames.RealSenseSource`, `overlay.format_calibration_stamp`/`draw_calibration_caption`, `reference_edges.tag_outline_px`. No entry points to something missing. Dead entries that disappear with T3/T4/T6: `frames.SharedCameraSource/iter_frames/FrameSource`, `detection.build_detection_source`, `localize.merge_locations/world_tag_for_robot` | `uchecks.py` | complete `__all__` or clean up with the deletions | S | `overlay.py` yes |

---

## 6. Aids and repo hygiene (B)

| ID | Finding | Evidence | Recommendation | Risk | Effort | Interface | ex-ID | hot? |
|---|---|---|---|---|---|---|---|---|
| B1 | **`data/` is tracked, although the README says „nicht versioniert“ (not versioned)**: 49 image files (`data/LV1` 40 JPG, 79 MB; `data/rohbilder` 4 PNG, 65 MB) plus 2 `.gitkeep`. `.gitignore` contains `data/*`, `!data/rohbilder/` (the exception explicitly allows tracking) and `*.jpg` (LV1 only via force-add). Of the 4 raw images, tests need only 2 (`test_detector.py:29,34`); 2 from 30.10. are unreferenced (T15) | `git ls-files data`; `du -sh`; `README.md:30` | Decide: correct the README **or** keep only the two test images and remove the rest from Git (otherwise the repo grows by about 20 MB per measurement series); keep LV1 as false-alarm reference, supplement the README | low | S | no | ex P7, D1 | no |
| B2 | **`.gitignore` ignores `*.txt`, `*.sh`, `*.bat`, `concept/`, `hardware/`**; still tracked are `requirements.txt`, `setup.sh`, `setup.bat`, `concept/*`, `hardware/*`. New files of these types stay invisible to `git add` | `git ls-files` (`.txt/.sh/.bat`) | add exceptions `!requirements.txt`, `!/setup.*`, `!concept/` or remove entries; otherwise a future deploy script gets lost | low | S | no | ex D1 | no |
| B3 | **`requirements.txt` refers to `src/ua_mdns.py`** (does not exist; mDNS is in `vision_server/discovery/mdns.py`) and to `doc/altlasten.md`, `doc/apriltag-e2e-test.md` (correct: `doc/projektdoku/`); `pupil-apriltags` only for T13 | `requirements.txt:8,13` | correct the comments | none | S | no | new | no |
| B4 | **CLAUDE.md calls the tests „Pytest-Tests“** (`:100`); what actually runs is `unittest discover` (README, legacy items; all tests are `unittest.TestCase`) | `CLAUDE.md:100`; word search for pytest in `tests` and `src` empty | name them „Unittest-Tests“ and the command (CLAUDE.md only after consultation) | none | S | no | new | no |
| B5 | **README.md lists `src/vision_server` twice** (`:23-24`) and does not name `src/jobs`, `src/ua_program`, `src/OPCUA`, `src/apriltag` | `README.md:21-29` | rewrite the structure (after T1) | none | S | no | new | no |
| B6 | **`betreuer/` is missing**, but presupposed by `CLAUDE.md` (table, rules), `ua_program/program.py:3`, `discovery/mdns.py:3` and `altlasten.md` D9 (folder removed in 69bf237 on 23.09., `OPC_UA_Program.py` never tracked) | `git ls-files` for betreuer empty; `git log -- betreuer` | clean up the references or restore the folder | none | S | no | new (to D9) | no |
| B7 | **`tagloc/cli/calibrate.py:3` names the key `S = save`**, which does not exist; **`--capture-to` only works in window mode** (`:110,147-149`; the headless branch returns earlier with `continue`), but the docs `apriltag-e2e-test.md:244-251` promise `--no-gui --capture-to` | reading `cli/calibrate.py:113-153` | correct the docstring; also serve `--capture-to` in the headless path (only sensible with a camera source) | low | S | CLI | new | no |
| B8 | **`VisionServerConfig.configuration_id`** is read nowhere | see T9 | delete | low | S | no | new | yes (`config.py`) |
| B9 | **`src/jobs/calibrate.py` maps hostnames differently than `server.py`** | see D11 | fix | low | S | OPC UA | new | `server.py` yes |
| B10 | **`diagnose_board.py` `DEFAULT_COMBOS`** without the current 7x10 board (docstring names 7x9/22 mm) | `tools/diagnose_board.py:28-33`; board change `c615b36` (28.10.) | update or delete the tool (section 3) | low | S | no | new | no |
| B11 | **`calibration_min_samples` is only displayed** (`minSamples` in `CalibrationProgress`); the real threshold is the fixed constant 3 | `calibration_session.py:64,371,400`; `profiles.py:119-120` | document/rename the field as „empfohlene Aufnahmen“ (recommended captures), or really couple the threshold to it (behavior changes) | low (node `CalibrationProgress`) | S | payload | new | no |
| B12 | **Docs name outdated values**: `vision-server-interface.md:1330` board 6x7, `apriltag-e2e-test.md`/`apriltag-referenz.md` ChArUco 7x5; code: chessboard 7x10, 23 mm (since 28.10.). `frontend-integration-kalibrierung.md` names `ns=7`, the legacy items name ns=6 | docs vs. `server.py:127-150` | update the docs (after consultation; CLAUDE.md forbids silent rewriting of interfaces) | none | S | no | new | no |
| B13 | `setup.sh` only sets up ShareLaTeX (ex D4); systemd unit not versioned (ex D5). A unit file in the repo would make `src/OPCUA/server.py` superfluous (ex D11) | `setup.sh`, `src/OPCUA/server.py` | later, with consultation for the Pis | medium (Pi operation) | M | operation | ex D4, D5, D11 | no |

---

## 7. Decomposition proposals (proposals only; **only after merge of `usecase-foto` and reconciliation with `apriltag-refactoring`**)

All of the following files are hot. No change on the cleanup branch until it is clear what
`apriltag-refactoring` has already decomposed.

| File | Finding | Proposal | Interface risk |
|---|---|---|---|
| `runner.py` (1741 lines) | `install_vision_machine` 625 lines in one function (:458-1085); `_install_*` already split off per feature, but `_install_tag_reference` 178 and `_install_cell_watch` 113 lines; triple method wrapper (D6); `usecase-foto` adds +183 lines | split into steps (server nodes, job methods, calibration, one per additional feature); wrapper helper `json_method` in `ua_nodes.py`; feature installers into a package | OPC UA names and order unchanged; run the `test_*_opcua` tests |
| `detection/apriltag.py` (1357 lines) | one class with job, measuring of tags/board/reference edges, cell monitoring; `acquire_and_detect` 131, `open` 122, `measure_tag_reference` 109 lines | separate modules/mixins: `measure_tags`, `measure_board`, `reference`; move JSON helpers (`_finite_or_none`) out (D2) | payload fields unchanged |
| `server.py` (556 lines) | about 200 lines of presets (`PI_APRILTAG_PRESETS`, `PI_CAMERA_STREAM_PRESETS`, identities) next to the start logic | presets and identity in `vision_server/pi_presets.py` (stdlib); `server.py` keeps `main()`; also solves D11 | environment variables, `ENDPOINT` unchanged |
| `stream_overlay.py` (643 lines) | `annotate` 84 lines; reference preview (`_tag_reference_view`, `_evaluate_reference`, ...) as its own block | move reference preview into its own class; make `_resize_for_stream` public (D15) | none |
| `cellwatch.py` (1303 lines) | tracker, pose store, options, JSON, flow in one file | package `cellwatch/{options,tracker,posestore,session}.py` | OPC UA (`StartCellWatch`, payload) unchanged |
| `tagloc/overlay.py`, `boards.py` | `overlay.py`: `Window`/`summarise` only for CLI (L11); `boards.py` mixes detection, pose and diagnosis | `boards.py`: ChArUco part and `diagnose_board` separated, if ChArUco stays (T12/L8) | low |
| not hot, but long | `address_space.attach_vision_system` 203 lines, `reference_edges._measure_one_impl` 224 lines, `handeye_session.finish` 192 lines | split per step (`finish`: check, calculate, save, build result) | low |

---

## 8. Ideas from `apriltag-refactoring` (40 commits, not integrated)

Command `git log --oneline apriltag..apriltag-refactoring`; checked per topic whether the problem still
exists on `apriltag` (`refbranch.py`, word search on both states):

| Commit | Topic | Problem still exists on apriltag? | Reference |
|---|---|---|---|
| 3874bda, fdc170f, 5bce4f4 | `tagloc/jsonio.py`: JSON read/write, `finite_or_none` | yes | D2, D3 |
| 743e473 | `board_spec_from_config` | yes | D1 |
| 0fd39f0, 9ea0b9a, 7d795d3 | `cancel_and_wait`, `SerialExecutor` | yes | D14 |
| 96a7224 | `DEFAULT_TAG_FAMILY` | yes | D10 |
| f856b9b | `jobs/calibrate.py` gets frame/paths from the server | yes | D11, B9 |
| 5d1714b | bundle JobRunner admission | yes | D7 |
| 7e67251 | dead parts from `frames.py` | yes | T3 |
| e63904f | dead code in the vision server removed (including `build_detection_source`) | yes | T4, T10 |
| 29c0e6e, d2a0924, e147473 | board/marker generation only in `boards/detector`; renderer moved to `tagloc/synthetic.py` | yes | D13 (question: does the test oracle belong in the library?) |
| 185b278, 0dceb9c, b115823 | `calibrate.main`, `detect.main`, `open`, `acquire_and_detect`, `solve_hand_eye` decomposed | yes | U1 |
| 1c938f6, 9a7942f | shared test helpers `tests/_support.py` | yes | D12 |
| 2ea0e2b | `ua_program` table-driven | yes | L6 |
| dfcfa4d | OPC UA methods characterized (`test_ua_methoden.py`) | yes | L1 |
| 7e10777, 6b740f1 | CLI aborts with a hint; loading helpers in `_common` | yes | L2 |
| 9f56bb2 | quaternion checks **deliberately not** unified | – | D5 |
| 28c0365 | additive nodes via `ua_nodes.add_named_variable` | yes | D6 |
| 4aca679, efb6b5f, 4e27abf, 742a386, 39df60d, 3c77acf | smaller bundlings in `geometry`, `localize`, `calibration`, `tagmap` | not checked individually | – |

**Consequence:** Most of the duplication findings (D1–D3, D7, D11–D14) are already solved there.
**Procedure (coordinator, 30.10.):** `apriltag-refactoring` is not integrated as a whole (integration only
on request). On `apriltag-aufraeumen`, individual commits are taken over via cherry-pick, if they apply
without conflict and do not touch hot or reserved files; otherwise the finding stays open. Independently
address: T1, T5, the T6/T7 decision, T8, T9, T10 (rest), T11, T13–T15, D4, D6 (rest), D8–D10, D15, as well
as sections 4–6.

---

## 9. Prioritized implementation list (top 15)

| # | Measure | Benefit | Risk | Effort | Note |
|---|---|---|---|---|---|
| 1 | delete `src/apriltag/`, carry references along (T1) | 400 lines, three docstring/doc legacy items, a clear answer to „welches Kalibrierskript“ (which calibration script) | low | S | deletion condition in the docs is met; independent of refactoring |
| 2 | fix the hostname drift in `jobs/calibrate.py` (D11/B9) | real error path in OPC UA recipe `calibration` | low | S | already solved on refactoring (f856b9b), then drops out |
| 3 | correct comment and path errors (T11, B3–B7, B10) | misleading hints (`S = save`, `betreuer/`, `ua_mdns.py`, old doc paths) | none | S | pure text |
| 4 | clarify `.gitignore` and `data/` tracking (B1, B2, T15) | repo size (144 MB of images), invisible new files | low | S | consultation needed |
| 5 | dead config fields and unused imports (T9, T10) | less confusion (`configuration_id`, `use_picamera`) | low | S | `config.py` hot: after merge of usecase-foto |
| 6 | delete dead functions (T2–T5) | about 60 lines | low | S | partly done on refactoring |
| 7 | decision on `world_tag_for_robot`/`merge_locations`/`estimate_tag_pose` (T6, T7) | honest API, doc consistency | medium | M | technical follow-up question |
| 8 | test for the Part 10 entry `VisionProgram` and `ua_program` (L1, L6) | largest gap in the production path | low | M | refactoring `test_ua_methoden.py` as template |
| 9 | implement the hand-eye pipeline only once (D4) with a CLI test (L2) | one calculation path fewer | medium | M | file format `session.json` stays |
| 10 | test for `jobs/calibrate.py` and start wiring `server.main` (L3, L5) | secures the entry points (also `src/OPCUA/server.py`) | low | M | |
| 11 | JSON helper module (D2, D3, D16) | about 80 duplicated lines, one NaN guard | low to medium | M | solved on refactoring, take over there |
| 12 | centralize board/calibration defaults and constants (D10, B11) | drift between Pi preset and CLI/docs (7x10 vs. 9x6) | low | M | changing CLI defaults is a CLI interface change |
| 13 | decide the `pupil` backend and OpenCV <4.7 branches (T12, T13, L8) | less untested code, one dependency fewer | medium | M | determine the Pi OpenCV version first |
| 14 | `tests/_support.py` (D12); decide `diagnose_board`/`list_realsense_profiles`/`pi_load_profiler` (T8) | fewer copied test helpers, less tool ballast | low | M | |
| 15 | decomposition of `runner.py`, `detection/apriltag.py`, `server.py` presets (section 7) and translating English comments into German (U2) | readability, guideline | medium to high (conflicts) | L | **only after merge of usecase-foto and refactoring** |

---

## 10. Not examined / open

- Runtime behavior on the Pis (OpenCV version, hostnames, `VISION_FRAME_ID` in the systemd unit): the effect of D11 and T12 remains **uncertain**.
- WSC repo and use of the OPC UA methods by the frontend: whether `StartCalibration` & Co. are used productively is only documented here.
- Branch and subprocess coverage (`ScriptDetectionSource`) not measured; coverage is statement coverage in the main process.
- Smaller refactoring commits (bundlings in `geometry/localize/tagmap`) not checked individually.
- The language distribution (U2) is a heuristic; individual docstrings may be misclassified.
- Doc consistency checked only at the calibration points, not for all 30 work plans.
- Nodeset XML, `doc/*.tex`, `.claude/hooks` not examined.
- Nothing in the repo was changed; coverage ran in a copy under `.../agent-adp-tot/repo-copy`.

## 11. Coordinator addendum

| ID | Finding | Evidence | Recommendation | Risk | Effort |
|---|---|---|---|---|---|
| B14 | **Repo hook blocks Write/Edit when Claude Code is not started in the repo root.** `.claude/settings.json` calls `python3 "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/doku_regeln.py"`; if a session is started one level higher (e.g. `/home/meric/ADP`), the file is missing there, the hook ends with an error, and every write access is refused — although the hook, according to `CLAUDE.md`, is only supposed to remind | session of 30.10.2026; `.claude/settings.json` | change the call so that it ends silently when the file is missing (fallback: determine the repo root via the script directory) | none | S |
| B15 | **World tags: two Pis, two tag maps, nothing enforces equality**; frontend and backend keep their own, by now outdated, copies | see [`altlasten-wsc.md`](altlasten-wsc.md), section 10 | one maintenance point via `SetTagMap`/`TagMapJson`; plan as its own feature | medium | M |

## 12. Implementation status

Branch `apriltag-aufraeumen`. Updated during the cleanup.

| IDs | State | Commit |
| --- | --- | --- |
| T1 | implemented | 863bd89 |
| T11, B3, B6 | implemented, except `server.py:116` (hot file) and `CLAUDE.md` (`betreuer/` references, deliberately unchanged) | 2b24719 |
| B2 | implemented (`!requirements.txt`, `!/setup.sh`, `!/setup.bat`); `concept/` and `hardware/` remain ignored | b08502a |
| B5 | implemented | 53ab6ce |
| B7 | implemented (docstring, `--capture-to` headless with camera source, test) | e200d89 |
| B10 | implemented | c60e37d |
| B14 | implemented | 119d839 |
| T3 | implemented by cherry-pick of 7e67251 | aaa4c4e |
| T2, T4, T5 | implemented | 944b485 |
| T9 | partly: fields removed from `profiles.py`; `VisionServerConfig.configuration_id` (`config.py`, hot) remains | b0cff8a |
| T10 | partly: `asset_model`, `calibration_session`, `nodeset_ids`, tests; open: `tagloc/overlay.py` (hot), `handeye_session.py` (reserved) | ec72d8d |
| T14 | partly: only `cancel_running`; `show_distance` (`overlay.py`, hot) remains | e421cc6 |
| D11, B9, L3 | implemented, by hand instead of cherry-pick (`f856b9b` produced conflicts in `jobs/calibrate.py` and `detection/__init__.py`): `jobs/calibrate.py` takes `VISION_FRAME_ID`, otherwise the table `FRAME_BY_HOST` with the real Pi hostnames. The table mirrors `server.PI_IDENTITIES`; importing `vision_server.server` is not possible, as it loads asyncua. One test compares both tables. The hint text names the chessboard 7x10 instead of `--board charuco`. New: `tests/test_jobs_calibrate.py`. Passing frame and paths from the server (as in `f856b9b`) remains open, because that would require changing `detection/__init__.py` and `script_runner.py` | 8f4ef13 |
| D12 | implemented, rebuilt instead of cherry-pick (`1c938f6` and `9a7942f` produced conflicts): `tests/_support.py` with `pose()`, `RecordingNode` (replaces `FakeNode` in `test_camera_health`, `test_camera_stream`, `test_mjpeg_server`), `run_briefly`, `AddressSpaceCache` (instead of `build()` in `test_asset_model`, `test_part10_fassade`). `test_cellwatch.FakeNode` (null dummy) and the `FakeCamera` variants remain separate | 4e3af2c |
| D9, D13 | implemented: `camera.realsense_color_profiles` is the one profile loop (error message and `tools/list_realsense_profiles.py`), with test. `tools/make_tag_sheet.py` uses `tagloc.detector.predefined_dictionary` and `tagloc.boards.build_board`; SVG byte-identical before and after. `make_synthetic_scene.chessboard_object_points` remains as oracle | 0c56a6d |
| D16 | partly: `raw_image.FILE_STAMP_FORMAT` for `raw_image` and `tagreference_log`; the UTC stamps in `handeye.py` and `handeye_session.py` remain (reserved) | bde30f3 |
| D10 | partly: `tagloc.tagmap.DEFAULT_TAG_FAMILY` in `tagmap.py`, `cli/_common.py`, `tools/make_synthetic_scene.py`; `profiles.py` keeps the literal (must remain importable without numpy), a test compares both values. Open: `detector.py`, `cli/calibrate_handeye.py` (reserved), as well as tag size, board standard, minimum captures, resolutions, frame names | 7a97c49 |
| D15 | not implemented: `_scale_for` is in `tagloc/overlay.py`, `_resize_for_stream` is imported in `stream_overlay.py` (both hot) | – |
| L1, L6 | implemented: `tests/test_vision_program.py` (17 tests) with a dummy source instead of `hello_world` (no longer exists) and a `DemoProgram` for suspend, resume, rejected function, lock and `running_to_*`; template `test_ua_methoden.py` from `dfcfa4d`, without the 40100 page and the calibration methods | 3fb10f8 |
| L7 | implemented: temp-image tests for `ImageFolderSource`, `SingleImageSource`, `open_source`, `to_gray`, `image_size`; camera sources remain without test (hardware) | 27daa0f |
| L12 | implemented: `tests/test_tool_imports.py` (import and `main` of all `vision_server/tools/*`, SVG test for `make_tag_sheet`, nodeset paths of `measure_nodeset_import`, `src/OPCUA/server.py` loads `vision_server.server.main`) | 562db88 |
| U4 | partly: `vision_server/asset_model.py` (`_ensure_folder`, line 123 instead of 124 in the finding) with `_log.debug` and reason, `ua_program/program.py` (under `src/ua_program/`, not under `vision_server/`; line 208) with `logger.warning(..., exc_info=True)`, behavior unchanged. Open: `handeye_session.py:638,697` (reserved). Deliberately unchanged, because justified: `jobs/calibrate.py` (error is reported), `pose.py:256` (reserved, deliberate), `camera.py` (diagnostic, documented), `tools/measure_framerate.py`, `vision_program.py:110`, `tools/measure_nodeset_import.py` | d8c5316 |
| U6 | partly: `reference_edges.tag_outline_px` added to `__all__`; `frames.RealSenseSource` was already in it; `detection.DETECTION_SOURCES` exists (the hit in the finding was an annotation, not a dead entry). Open: `detector.apply_detector_parameters` (reserved) and `overlay.format_calibration_stamp`/`draw_calibration_caption` (hot). The dead entries `frames.SharedCameraSource/iter_frames`, `build_detection_source`, `localize.merge_locations/world_tag_for_robot` disappear only with T6/T7 (decision needed); `localize.__all__` still lists `merge_locations` and `world_tag_for_robot`, because both functions still exist | 087a207 |
| U2 | partly: docstrings and comments translated into German, text only (AST without docstrings identical before and after each file; argparse `help` and outputs untouched; umlauts written as ae/oe/ue as usual in the module). `tagloc/localize.py` 4ede203, `tagloc/tagmap.py` 6bc8ac7, `tagloc/geometry.py` 6e26c15, `tagloc/frames.py` ecf9112, `tagloc/observations.py` 7b2bdc3, `tagloc/cli/_common.py` e437ab3, `tagloc/cli/detect.py` 274a456, `tagloc/modes.py` 9ef4538, `tagloc/identity.py` f4420fe, `jobs/calibrate.py` 58e0d22, `tools/make_tag_sheet.py` abe8608, `tools/measure_nodeset_import.py` dcff2bb, `tools/make_synthetic_scene.py` 16ffbfb, tests (`test_tag_pipeline`, `test_welttags`, `test_layering`, `test_tag_map`, `test_tag_geometry`) 1c5a6a5. Open: mixed and locked files (`detection/apriltag.py`, `overlay.py`, `detector.py`, `pose.py`, `calibration.py`, `asset_model.py`, `tests/test_apriltag_source.py`, `tests/test_camera_owner.py`) | per file, see status |
| D2 | partly: `tagloc/jsonio.py` with `finite_or_none(value, digits=None)` (stdlib, rounding per caller), `detection/apriltag` uses it (payload 4 digits, `PAYLOAD_DIGITS`); new `tests/test_jsonio.py`, `jsonio` in `test_layering`. Open: `measuretag._finite`, `measureboard._finite` (byte-identical, not locked, just not yet switched), `handeye_session._finite_or_none` and `tagloc/handeye._finite` (6 digits; hand-eye files) | 5ca249c |
| Decomposition `detection/apriltag.py` (section 7), U1 for `acquire_and_detect`, `open`, `measure_tag_reference` | implemented: package `detection/apriltag/` with `__init__.py` (class, state), `resources.py`, `capture.py`, `job.py`, `tags.py`, `board.py`, `reference.py`, `cell.py`, `snapshot.py`; mixins, one class, import path, logger name, payload fields and order unchanged. Longest method is now 62 lines (previously 131/122/109). Checked with a reference run over job, MeasureTags, MeasureBoard, reference edges, `observe_cell`, `open` paths and log messages on synthetic scenes: byte-identical before and after. Directory overview in `code-dokumentation.md`, part A2 | 22bf9ea (package), 6e187f7 (modules) |
| U2 | partly, addendum: `detection/apriltag/*` (docstrings and comments in German; log and payload texts unchanged) and `tests/test_apriltag_source.py`. Open: `overlay.py`, `detector.py`, `pose.py`, `calibration.py`, `asset_model.py`, `tests/test_camera_owner.py` | 6e187f7, f6326ae (test) |
| Decomposition `runner.py` (section 7), U1 for `install_vision_machine` (625 lines), `_install_tag_reference` (178), `_install_cell_watch` (113), D6, D7 (start wrapper), D14 (where not locked) | implemented: package `runner/` (previously 1924 lines in one file, now 21 files with roughly 2500 lines together, largest `calibration.py` with 337): `install.py` (`install_vision_machine` as a sequence of steps, 60 lines), `wiring.py` (`Wiring`: session, annotator, layer-2 run, hand-eye session, instead of late-bound local variables), `jobs.py`, `calibration.py`, `tag_map.py`, `part10.py`, `sources.py`, `livestream.py`, `health.py`, `machine.py`, `standalone.py`, and one installer per feature (`layer2`, `hand_eye`, `measure_board`, `measure_tags`, `tag_reference`, `save_raw_image`, `save_use_case_image`, `cell_watch`). **D6:** `uamethods.json_method` is the shared frame of the five JSON methods (read request, operational state, lock, work; error texts and codes stay with the callers), `add_vision_method` registers all methods under a fixed NodeId. **D14:** `aio.cancel_and_wait` in `camera.py`, `camera_health.py`, `camera_stream.py` and `runner`; `cellwatch.py` remains locked, the calibration pools (`SerialExecutor`) remain open. Longest function now 80 lines including docstring (previously 625). Import path `vision_server.runner`, logger name and all names previously defined there unchanged; two test patches point to the module that reads the size (`runner.install.build_detection_sources`, `runner.save_use_case_image.USE_CASE_FRESH_FRAME_TIMEOUT_S`). Checked with a reference run over six build variants (hand Pi, ceiling, without AprilTag, without part 2/stream, source not ready, without storage) with a dummy camera: complete address space from `Objects` (NodeId, BrowseName, types, references, values, method arguments), per method calls with invalid JSON and empty argument (Operational, after expiry, after halt), flows with valid requests, and all log messages: byte-identical before and after. Directory overview in `code-dokumentation.md`, part A2 | d005af9 (package), 3b1e925 and cd407ad (moves), f830ac7 (`install_vision_machine`), cd3bab3 (D6), ad33ed8 (D14) |
| U2 | partly, addendum: `runner/*` (where English: `livestream`, `machine`, `sources`), `tests/test_camera_owner.py`, references to `runner.py` in comments. Open: `overlay.py`, `detector.py`, `pose.py`, `calibration.py`, `asset_model.py` | 54faf75 |
| D4 | implemented: `tagloc.handeye.refine_board_session` (undistortion, `refine_hand_eye_robust`, leave-one-out, `reconstruction_spread`) is the **one** refinement pipeline; `handeye_session._refine_board` and `calibrate_handeye.solve_session` call it (`BoardRefinement` as result, `RefinementError` with `stage` for calculation errors, `ValueError` passes through). `SESSION_SCHEMA` now only in `tagloc/handeye.py`, likewise writer (`session_payload`, `session_json`, `SessionSample`) and reader (`load_session`, `SessionDataset`) of `session.json`; `BOARD_SCALE_WARNING` replaces the literal 0.003 in the CLI and the second constant in the server. The CLI now only calculates and prints (`_print_refinement`). New `tests/test_handeye_dataset.py` (CLI recalculates the Pi's session and writes the same file; reader against writer; error stages). Only behavior change: if the refinement in `--session` crashes midway, the lines already printed before the traceback are missing (exception type and text remain) | 516de8d |
| U1 | partly, hand-eye addendum: `HandEyeSession.finish` 192 to 68 lines (`_axes_refusal`, `_solve`, `_annotate_samples`, `_save`, `_result_payload`, `_result_warnings`, `_summary_message`), `refine_hand_eye` 119 to 93 (`_RefineProblem`), `solve_camera_from_world_tags` 94 to 69 (`_world_correspondences`, `_pnp_starts`), `solve_session` 124 to 65 (`_print_refinement`). Order of status change, publishing and persisting in `finish` unchanged | de0e664, e1e0167, d19d131 (`solve_session` with D4) |
| D2 | remainder implemented: `handeye_session._finite_or_none`, `tagloc/handeye._finite` (now `finite_or_none(x, FILE_DIGITS)`, 6 digits), `measuretag._finite`, `measureboard._finite` and the four inline checks in `_current()` are removed in favor of `tagloc.jsonio.finite_or_none`. Only `observations.py:82` remains open | 9a6e5b6 |
| D16 | implemented: `tagloc.jsonio.utc_now_z()` (`createdAt`) and `utc_stamp()` (file and folder name `%Y%m%dT%H%M%SZ`) in `handeye.py` and `handeye_session.py`; formats unchanged | 7bc86ce |
| D10 | remainder implemented: `DEFAULT_TAG_FAMILY` in `detector.py` (four signatures) and in the `--family` default of `calibrate_handeye`. Open as above: tag size, board standard, minimum captures, resolutions, frame names | 1bb7c93 |
| T10, U4 | remainder implemented: `invert`, `rvec_from_rotation` removed from `handeye_session.py`; `_intrinsics_source` and `_current` log the reason with `_log.debug` (called on every status, return values unchanged) | 3c12055 |
| U6 | remainder implemented: `detector.apply_detector_parameters` in `__all__`. Open remain `overlay.format_calibration_stamp`/`draw_calibration_caption` (hot) and the dead entries from T6/T7 | 1bb7c93 |
| U2 | addendum: `pose.py`, `detector.py`, `calibration.py`, `vision_server/asset_model.py` and the remaining English docstrings in `tagloc/handeye.py` and `calibrate_handeye.py` (text only; AST without docstrings identical before and after each file; log and CLI texts unchanged). Open: only `tagloc/overlay.py` (hot) | d59291d, 4ff70bb, 0b5d05e |
| B13 (ex D4, D5, D11) | implemented for D5/D11: `deploy/opcua-server.service` (template with `@USER@`, `@REPO@`) and `deploy/README.md` (values per Pi, drop-in of the ceiling Pi, installation, checking, way back); `src/OPCUA/` deleted, the test for the starter now checks the template (`-m vision_server.server`, `PYTHONPATH=…/src`, `main` importable). Both Pis have started the unit with `-m vision_server.server` since 02.11.2026. `Description`, `After`, `Wants`, `WantedBy` of the template are not recorded on the Pis, only set as common defaults. `setup.sh` (ex D4) remains open | 642e400 |
| T12 | implemented (both Pis and the development machine: OpenCV 5.0): `Dictionary_get`, `DetectorParameters_create`, `CharucoBoard_create`, `interpolateCornersCharuco`, old `detectMarkers` path, `drawMarker`, `board.draw` and the `hasattr` switches (`findChessboardCornersSB`, `solvePnPGeneric`, `solvePnPRefineLM`, `CharucoDetector`, `ArucoDetector`) in `detector.py`, `boards.py`, `pose.py`, `tools/make_tag_sheet.py`, `tools/make_synthetic_scene.py`; docstrings assume OpenCV >= 4.8. Reference runs (AprilTag decomposition, hand-eye) byte-identical before and after | b693e9f |

Test run after this block: 922 tests, OK (starting state 882); after U2, U4, U6 unchanged at 922, OK. After D2 and the decomposition of `detection/apriltag` 972 tests, OK (new: `test_jsonio`). After the decomposition of `runner` 989 tests, OK (new: `test_runner_uamethods`, `test_aio`).

Test run after the rework of hand-eye calibration (D4, U1, D2, D16, D10, T10, U4, U6, U2): 997 tests, OK (new: `test_handeye_dataset`, `UtcStampTest` in `test_jsonio`). Compatibility with a reference run over sessions (tag and board, rejected captures, abort, failure cases, saving with backup), `session.json`, result file, `calibrate_handeye --session` and `--samples`, numbers of `refine_hand_eye(_robust)` and leave-one-out, MeasureBoard/MeasureTags and poses before and after: byte-identical (exception: the lines mentioned above before a CLI crash); every field of the frontend contract (`handEye.ts`) appears in the status payloads.

Test run after B13 and T12: 998 tests, OK (new: one test for the systemd template; `SystemdEinstiegTest` now has two instead of one).
