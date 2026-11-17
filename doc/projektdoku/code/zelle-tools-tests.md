# Zelle: tools, deployment, config, data, tests

Detailed reference for the non-library parts of the ADP repository (branch `main`, as of 2026-11-17). Overview: [code-dokumentation.md](../code-dokumentation.md).


Scope: `tools/`, `deploy/`, `config/`, `data/`, `tests/` on branch `main`. Paths are relative to the repository root.

### `tools/make_tag_sheet.py`
- Purpose: prints AprilTag markers or a ChArUco board as an SVG with millimetre-exact size (a PNG would depend on printer DPI).
- Entry points: `main(argv)`, `build_parser()`, `render_marker`, `render_charuco`, `build_svg`, `layout_tags`.
- Interface: required `--out <file.svg>`; `--ids`, `--size-mm`, `--family` for tags; `--charuco` with `--cols`, `--rows`, `--square-mm`, `--marker-mm`, `--board-dictionary` for boards.
- Details: `doc/projektdoku/apriltag-referenz.md`. Smoke-tested in `tests/test_tool_imports.py`.

### `tools/make_synthetic_scene.py`
- Purpose: renders synthetic camera images with known ground truth (tag or chessboard poses are given, projected with the real intrinsics), so the detector-to-pose chain can be checked against reference values without a camera.
- Entry points: `main(argv)`, `build_parser()`, `write_tag_scene`, `write_chessboard_scene`, `render_tags`, `render_reference_scene`, `synthetic_calibration`, `chessboard_object_points`.
- Interface: `--out <dir>` (required), `--count`, `--width`, `--height`, `--seed`, `--family`, `--cols`, `--rows`, `--square-size-m`. Writes the images and a calibration file.
- Used by `tests/test_tag_pipeline.py` and `tests/test_reference_edges.py`.

### `tools/make_use_case_target.py`
- Purpose: prints the use-case target sheet (page 1: angle scale, crosshair, four ArUco markers DICT_4X4_50 IDs 0–3) and the dice-sticker sheet (page 2), A4 at 1:1, as SVG; optional PDF.
- Entry points: `build_target_svg`, `build_cube_svg`, `marker_path`, `svg_to_pdf`, `main`.
- Interface: `--out` (default `data/use_case_target`), `--stickers` (default 12), `--pdf` (needs `inkscape` or `rsvg-convert`).
- Referenced by `src/use_case_eval/target.py` and `doc/projektdoku/arbeitsplaene/use-case-messung.md`.

### `tools/measure_nodeset_import.py`
- Purpose: measures the RSS and time cost of importing the OPC UA companion-spec nodesets (asyncua), as a basis for the Raspberry Pi memory decision.
- Entry points: `rss_mb`, `main`; `IMPORT_ORDER` lists the nodeset files (checked in `tests/test_tool_imports.py`).
- Details: md`.

### `tools/use_case_auswertung.py`
- Purpose: evaluates one use-case trial folder: image measurement and key figures (ISO 9283 style); writes `auswertung.csv` and `auswertung.json` into that folder.
- Entry points: `main(argv)`, `build_parser()`, `evaluate`, `calibrate_sticker`.
- Interface: positional `reihenordner` (e.g. `data/use-case/reihe_001`); options `--neu-messen`, `--bilder`, `--aufkleber-kalibrieren VOR NACH`, `--intrinsik` (see `--help`).
- Uses `use_case_eval` (package `src/use_case_eval/`) and `tagloc.calibration`. Tested via `tests/test_use_case_eval.py`. Details: `doc/projektdoku/arbeitsplaene/use-case-messung.md`.

### `tools/handeye_offline_cv4/`
- Purpose: offline analysis of stored hand-eye sessions with OpenCV 4.x (`cv2.calibrateHandEye`), separate from the Pis, which stay on OpenCV 5.
- Dependencies: `tools/handeye_offline_cv4/requirements.txt` (opencv-contrib-python-headless 4.14.0.94, numpy 2.5.4, scipy 1.18.1). Scripts import `tagloc` and need `PYTHONPATH=src`.
- Inputs: session folders under `data/rohbilder/calibration/<session>/`. Outputs: JSON/CSV/HTML under `doc/auswertung/handeye_cv4/`, `doc/auswertung/*.html`, and, for the activation script, the active calibration files under `data/`.
- Results and interpretation: `doc/auswertung/*.md`, `doc/projektdoku/arbeitsplaene/handauge-schachbrett-rohdaten-offline.md`.

### `tools/handeye_offline_cv4/analyse.py`
- Purpose: reads sessions (read-only, works on a temporary copy) and computes hand-eye results, residuals and a comparison against the OpenCV closed-form methods.
- Entry points: `analyse_session`, `load_states`, `closed_forms`, `refine`, `write_csv`; CLI `main`.
- Interface: `--out` (default `doc/auswertung/handeye_cv4`), repeatable `--session`. Writes `<session>.json`, `<session>_posen.csv`, `vergleich.json`.

### `tools/handeye_offline_cv4/drift.py`
- Purpose: drift test. Fits a rigid hand-eye model against models where the board or mount drifts linearly over time.
- Entry points: `fit`, `analyse`, `main`.
- Interface: `--out` (default `doc/auswertung/handeye_cv4`), repeatable `--session`. Writes `drift.json`.

### `tools/handeye_offline_cv4/drift_report.py`
- Purpose: builds the drift HTML report from the offline outputs. Standard library only.
- Entry points: `build()` (run by the `__main__` block); helpers `load`, `table_html`, `details`, `chart4`–`chart6`. Provides `CSS`, `de`, `details`, `table_html` to `warm_report.py`.
- Interface: no arguments; reads `doc/auswertung/handeye_cv4/`, writes `doc/auswertung/handeye_drift.html`.

### `tools/handeye_offline_cv4/report.py`
- Purpose: builds the OpenCV comparison report. Standard library only; reads only `doc/auswertung/handeye_cv4/`.
- Entry points: `load`, `method_section`, `reproj_section`, `influence_section`, `repro_section`; module-level run writes `OUT`.
- Interface: no arguments; writes `doc/auswertung/calibration_cv4_vergleich.html` (self-contained, inline).

### `tools/handeye_offline_cv4/warm_report.py`
- Purpose: compares cold and warmed-up hand-eye sessions of 09.11.2026 with matplotlib plots embedded as inline SVG.
- Entry points: `main()` (parses `--out`, default `DEFAULT_OUT`), `build(out_path)`, `compute`, `plot_final`, `plot_sweep`, `plot_time`, `plot_driftmm`.
- Writes `doc/auswertung/handeye_warm_vergleich.html` and a JSON companion. Reads `analyse.py`/`drift.py` outputs only.

### `tools/handeye_offline_cv4/laeufe_vergleich.py`
- Purpose: compares several 1080p calibration runs (intrinsics and hand-eye per run) and shows their spread.
- Entry points: module-level script; `load`, `intrinsics`, `pnp`, `handeye`, `angles`.
- Interface: session folders as `sys.argv[1:]` (read at import time). No `__main__` guard. Details: `doc/auswertung/handauge_laeufe_vergleich_20261110.md`.

### `tools/handeye_offline_cv4/intrinsik_offline.py`
- Purpose: offline intrinsics from ChArUco images of the hand camera (11x7, DICT_5X5_100); compares three distortion models with leave-one-out, against the factory intrinsics. Stores and activates nothing.
- Entry points: module-level script; helper `evalK`.
- Interface: positional `muster` (default glob `data/calibration/cam_flange_pending/kalib_*.jpg`), `--nur-gruppe`. Reads `data/calibration/cam_flange.json`.
- Details: `doc/auswertung/intrinsik_20261110.md`.

### `tools/handeye_offline_cv4/intrinsik_1080.py`
- Purpose: offline intrinsics at 1920x1080 from a hand-eye raw recording (ChArUco, measured board scale).
- Entry points: module-level script; helper `evalK`. No `__main__` guard.
- Details: `doc/auswertung/handauge_1080_20261110.md`.

### `tools/handeye_offline_cv4/kalibrierung_aktivieren.py`
- Purpose: writes the offline calibration of one session as the active files: intrinsics to `data/calibration/cam_flange.json`, hand-eye to `data/handeye/cam_flange.json`, using the `tagloc` save functions.
- Entry points: module-level script (argparse at top). Options: `--aufloesung 1920x1080|640x480` (required), `--sitzung` (default `data/rohbilder/calibration/20261110T104151Z_77e0dd17`), `--quelle`.
- Overwrites both files directly; there is no backup step in the script. Usage in `doc/auswertung/handauge_1080_20261110.md`.

### `tools/handeye_offline_cv4/kinematik/`
- Purpose: experiments on the Franka kinematic model (joint offsets and link lengths) and their effect on hand-eye results. Writes candidate files into the session `results/` folder.
- `kin.py`: helper module (nominal forward kinematics, `load`, `resid`, `rms`); imported by `ba.py`, `ba2.py`, `intr.py`.
- `ba.py`: bundle adjustment, over-parameterised, "only as an indication". `ba2.py`: identifiable kinematic model, leave-one-session-out. Both have a `__main__` guard. Result: `doc/auswertung/handeye_cv4/kinematik_ergebnis.md`.
- `intr.py`: experiment with several intrinsics (factory values of the RealSense D415 as `WERK`); `run()` is also imported by `intr_neu.py` and `handeye_1080.py`.
- `intr_neu.py`: runs the four sessions of 09.11.2026 with the offline intrinsics of 10.11.2026; no `__main__` guard.
- `handeye_1080.py`: rigid and kinematics-corrected hand-eye for the 1080p session. Positional args: session folder, input JSON, optional model name. Writes `results/candidate_handeye_offline.json`. No `__main__` guard. Line 20 contains a dead conditional (`... if False else ...`).

### `deploy/opcua-server.service`
- Purpose: systemd unit template for the vision server on the Raspberry Pis. Placeholders `@USER@` and `@REPO@` are replaced before install.
- Key settings: `ExecStart=@REPO@/.venv/bin/python -m vision_server.server`, `Environment=PYTHONPATH=@REPO@/src`, `Restart=always`, `RestartSec=5`, `NoNewPrivileges`, `PrivateTmp`.
- Per-Pi `VISION_*` variables belong in a drop-in `opcua-server.service.d/override.conf`, not in this template. Install and rollback steps: `deploy/README.md`. Ports 4840 (OPC UA) and 8080 (MJPEG) per that README.
- The test `SystemdEinstiegTest` in `tests/test_tool_imports.py` checks the `ExecStart` form and the `PYTHONPATH` line.

### `deploy/README.md`
- Purpose: install and rollback guide for the unit; table of per-Pi values (user, repo path, drop-in).
- Lists which unit entries were not verified on the Pis (`Description`, `After`, `Wants`, `WantedBy`). Commands to compare with `systemctl cat`.

### `config/tagmap.json`
- Purpose: the live tag map of the cell, read by the server (`src/vision_server/server.py`, `src/vision_server/profiles.py`) and by `src/jobs/calibrate.py` from `REPO_ROOT/config/tagmap.json`.
- Schema (described, values not reproduced): top-level `schema` (`wsc.vision.tagmap/2`), `frameId`, `anchorTagId`, `tagFamily`, `tags` (list). Each tag has `tagId`, `role` (`world`, `robot` or `module`), `sizeM`, `poseInWorld` (`{position [x,y,z] m, orientation [x,y,z,w]}` or `null` when the tag is measured), and for robot/module tags `moduleId`, `instanceId`, `tagToModule`.
- Parser: `src/tagloc/tagmap.py` (`load_tag_map`, `tag_map_from_json`, `save_tag_map`, `validate_tag_map`); role names of schema 1 are mapped on load. Background: `doc/projektdoku/arbeitsplaene/apriltag-welttag-konzept.md`.

### `config/tagmap.example.json` and `config/tagmap.test.example.json`
- Purpose: templates with the same schema. The example has world, robot and module tags; the test example has one world tag and one module tag (for the small setup test).
- Not loaded by any code or test in the repo; referenced in docs only (`doc/projektdoku/code-dokumentation.md`). See Flags.

### `data/`
- Purpose: machine-specific calibration, captures and results. Layout below; generated folders are created at runtime by the server or the tools.
- Versioned data: `data/use-case/` (trial series; `doc/projektdoku/arbeitsplaene/use-case-messung.md` says `data/` is versioned from 02.11.2026 on and should be committed deliberately), `data/rohbilder/` raw captures (`.gitignore` exception), `data/calibration/cam_flange.json`, `data/handeye/cam_flange.json` (exceptions in `.gitignore`), `data/Linealtest/` (ruler test, `messung_NNN/`), `data/use_case/` (older trial series). On `main` (since 17.11.2026) older measurement and calibration images (`data/LV1/`, `data/calibration/cam_ceiling_captures/`, older raw captures, part of the PNGs in `data/use_case/reihe_001/`) are no longer versioned; they remain in the git history and on branch `apriltag-aufraeumen`.
- Git rules: `.gitignore` excludes `data/calibration/**` and `data/handeye/**` except the two `cam_flange.json`, excludes `data/tagreference/`, and excludes `*.jpg` everywhere. It has no rule for `data/use-case/` or the other `data/` paths, and `!data/rohbilder/` re-includes raw images. Files already tracked stay tracked.

#### Layout
| Path | Content | Created by |
| --- | --- | --- |
| `data/calibration/<frame_id>.json` | camera calibration per frame (`cam_flange`, `cam_ceiling`) | calibration job / `tagloc.cli.calibrate`; `src/vision_server/server.py` (path per frame) |
| `data/calibration/<frame_id>_captures/` | calibration captures (not versioned on `main`) | server, when capture is enabled |
| `data/calibration/cellwatch_camera_pose.json` | stored camera pose for cell watch (`CameraPoseKeeper`) | `src/vision_server/runner/cell_watch.py` (runtime) |
| `data/calibration/<frame_id>_pending/` | working folder of the calibration session (not present in the repo) | `src/vision_server/calibration_session.py`, emptied on each start |
| `data/handeye/<frame_id>.json` | hand-eye result | `tagloc.handeye`, hand-eye session |
| `data/handeye/sessions/<Zeit>/` | hand-eye sessions from the hand Pi (`session.json`) | not present in the repo; written at runtime |
| `data/rohbilder/` | raw camera images (`cam_ceiling_<date>-<time>.png`), `.gitkeep` | `SaveRawImage` method |
| `data/rohbilder/calibration/<session>/` | hand-eye sessions: `poses/`, `results/` | `src/vision_server/handeye_session.py`, `runner/hand_eye.py`; read by the `tools/handeye_offline_cv4/` scripts |
| `data/use-case/reihe_NNN/` | trials `versuch_NNN.json` and `versuch_NNN_messung.png` | `SaveUseCaseImage` (`config.use_case_dir`); `tools/use_case_auswertung.py` adds `auswertung.csv/json` |
| `data/tagreference/` | tag-reference debug data | server, `config.tag_reference_debug_dir`; gitignored |
| `data/use_case_target/` | default output of `tools/make_use_case_target.py` | tool; not present in the repo |
| `data/Linealtest/messung_NNN/` | ruler test: `bild_NN.png` and `messung.json` | not written by current code |
| `data/use_case/reihe_NNN/` | older trial series (`versuch_NNN.json`, PNGs); superseded by `data/use-case/` | not written by current code (`config.use_case_dir` is `data/use-case`) |

- `data/.gitkeep` and `data/rohbilder/.gitkeep` are placeholders.
- Pi-level details, including the `data/` description in the code handbook: `doc/projektdoku/code-dokumentation.md` (section on `data/`).

### `tests/`
- Layout: 67 test modules `tests/test_*.py` (`unittest.TestCase` classes), `tests/_support.py` (shared helpers: `RecordingNode`, nodeset check `HAS_NODESETS`; not collected by discovery), `tests/__init__.py` (empty).
- Run all: from the repository root, `PYTHONPATH=src python3 -m unittest discover -s tests -t .` (`README.md`). One module: `PYTHONPATH=src python3 -m unittest tests.test_tag_pipeline -v`. Dependencies in `requirements.txt`.
- Tests skip, rather than fail, when optional packages (`cv2`, `asyncua`) or the OPC UA nodesets are missing (`_support.py`, `test_tool_imports.py`).

Coverage by area (from each module's docstring; module names only):
- `tagloc` geometry and tag map: `test_tag_geometry`, `test_tag_map`, `test_tag_pipeline` (end-to-end on a synthetic image), `test_welttags`, `test_joint_camera_pose`, `test_pose_filter`, `test_lens`, `test_layering` (dependency direction rule), `test_jsonio`, `test_overlay_scaling`.
- Calibration and hand-eye: `test_calibration_io`, `test_calibration_session`, `test_calibration_info`, `test_calibrate_cli`, `test_boards_pose`, `test_measureboard`, `test_measuretag`, `test_handeye*` (nine modules: `test_handeye`, `test_handeye_cv4_drift`, `test_handeye_dataset`, `test_handeye_full_calibration`, `test_handeye_opcua`, `test_handeye_raw_offline`, `test_handeye_refine`, `test_handeye_robust`, `test_handeye_session`, `test_handeye_session_refine`).
- Tag reference and detection: `test_tagreference`, `test_tagreference_liveview`, `test_reference_edges`, `test_detector`, `test_frame_sources`, `test_apriltag_source`, `test_detection_source`, `test_script_runner`, `test_payload`.
- Overlays and stream: `test_stream_overlay`, `test_stream_calibration_overlay`, `test_camera_stream`, `test_mjpeg_server`.
- Server (`src/vision_server`): `test_aio`, `test_asset_model`, `test_camera`, `test_camera_health`, `test_camera_owner`, `test_shared_camera`, `test_cellwatch*` (3 modules), `test_discovery_lds`, `test_discovery_mdns`, `test_job_runner`, `test_recipe_routing`, `test_raw_image`, `test_part10_fassade`, `test_runner_uamethods`, `test_server_config`, `test_subpixel_settings`, `test_vision_program`, `test_layer2_plan`, `test_layer2_run`, `test_layer2_opcua`, `test_use_case_image`, `test_use_case_eval`, `test_use_case_tag`.
- Jobs (`src/jobs`): `test_jobs_calibrate`.
- Tools and entry points: `test_tool_imports` (import and `main` smoke tests for tools and `deploy/opcua-server.service`), `test_use_case_eval` (loads `tools/use_case_auswertung.py`).

