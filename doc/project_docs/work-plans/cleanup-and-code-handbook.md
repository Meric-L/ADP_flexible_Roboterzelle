# Cleaning up both repos and code handbook

**Status:** in progress — 30.10.2026
**Owner:** Meric, `Agent: cleanup and code handbook`
**Topic:** other
**Branch:** docs on `apriltag`; code on `apriltag-aufraeumen` (this repo) and `ADP_Lokaliserung` (webskillcomposition) — both local, unmerged

## Goal

Over weeks of parallel work, dead code, duplicates (e.g. several calibration scripts), workarounds and unclean spots have accumulated in both repos. The existing lists ([`../legacy.md`](legacy.md), [`../legacy-dead-code.md`](legacy-dead-code.md))
date from 21.09./21.10. and, for WSC, still refer to branch `tag-einmessen`.

Afterwards there will be:

1. two current audits — [`../legacy-cell.md`](legacy-cell.md) (this repo, `apriltag`) and
   [`../legacy-wsc.md`](legacy-wsc.md) (webskillcomposition, only our share `origin/dev..Ungetestet`);
2. a code handbook [`../README.md`](../code/README.md), in which one can look up for every file of our
   code what it is for and where the details are;
3. the findings worked off on separate branches, which are merged only on instruction.

## Already read

- `CLAUDE.md`: rules, work-plan obligation, German as the language, no deleting/renaming of topic docs
- `doc/project_docs/work-plans/README.md` and `_template.md`: index, format
- `doc/project_docs/work-plans/legacy.md`, `doc/project_docs/work-plans/legacy-dead-code.md`: previous findings (will be carried over and re-checked)
- Branch `apriltag-refactoring` (plan `refactoring-apriltag.md` there): as of 23.09., 149 commits behind `apriltag`, not merged — only a source of ideas

## Affected files

Docs (branch `apriltag`):

- `doc/project_docs/work-plans/legacy-cell.md`, `doc/project_docs/work-plans/legacy-wsc.md`, `doc/project_docs/code/README.md` (new)
- `doc/project_docs/work-plans/legacy.md`, `doc/project_docs/work-plans/legacy-dead-code.md` (only header note "superseded by …")
- `doc/project_docs/work-plans/README.md` (index line, topic-doc table), `README.md` (docs table)

Code: the places named with an ID in the audits. Every change carries the finding ID in the
commit message, so that each one can be traced individually and reverted.

## Interfaces

The cleanup changes **no** documented interface: OPC UA NodeIds, methods and their arguments, payload fields, file formats (`tagmap.json`, calibration JSON) and the CLI commands `python -m tagloc.cli.*` remain as stated in the topic docs. Where a finding could only be resolved with an interface change, it stays open in the audit and is coordinated with the team.

Python import paths within `src/` may change when dead code is removed or duplicates are merged. The tests (`PYTHONPATH=src python -m unittest discover -s tests -t .`)
must then remain green without modification.

## Approach

1. Audit of both repos (dead code, duplicates, workarounds, guidelines, test gaps), findings with evidence and ID.
2. Write and push the audits and code handbook on `apriltag`.
3. Cleanup on `apriltag-aufraeumen` or `ADP_Lokaliserung`, by risk: dead code →
   duplicates → guidelines → workarounds (only if the removal condition is met). Touch files on which a lot is currently being worked on
   on `apriltag` only carefully (conflict risk at the later merge).
4. After each block, all tests; mark completed findings in the audit with branch and commit.


## Split into two sessions (from 30.10.2026)

From here on two Claude sessions work in parallel. Each has **its own repo, its own branch, its own audit and its own handbook part**. This way they never touch the same file.

| | Session A — cell | Session B — webskill |
| --- | --- | --- |
| Worktree | `/home/meric/ADP/ADP_flexible_Roboterzelle/.claude/worktrees/apriltag-aufraeumen` | `/home/meric/ADP/webskillcomposition/.claude/worktrees/ungetestet-aufraeumen` |
| Branch | `apriltag-aufraeumen` (base `apriltag`; merged into `main` on 17.11.2026) | `ADP_Lokaliserung` (base `Ungetestet` `5812275`; local, unmerged) |
| Findings | [`../legacy-cell.md`](legacy-cell.md) | [`../legacy-wsc.md`](legacy-wsc.md) |
| Handbook | `README.md`, only **part A** | `README.md`, only **part B** |
| Record status in | `legacy-cell.md`, section 12 "Implementation status" | `legacy-wsc.md`, section 11 "Implementation status" |
| Tests | `PYTHONPATH=src /home/meric/ADP/ADP_flexible_Roboterzelle/.venv/bin/python -m unittest discover -s tests -t .` (starting state 877 OK) | in `frontend/`: `npx tsc --noEmit`, `npx vitest run` (79 files / 836 tests); in `backend/`: `/home/meric/ADP/webskillcomposition/backend/.venv/bin/python -m pytest -q` (81 + 1 skipped) |

**Shared rules**

- Start Claude Code **in its own worktree**, not in `/home/meric/ADP` — otherwise the hook
  `doc_rules.py` blocks every Write/Edit (finding B14 in `legacy-cell.md`).
- One commit per finding ID or small group, ID at the start of the message (`aufraeumen(T1): …`). After each block
  all tests. Do not push code branches, merge nothing, no force-push.
- Do not change any documented interface (OPC UA nodes/methods, payloads `wsc.vision.*`, `tagmap.json`,
  CLI options). What can only be done with an interface change stays open.
- Before every deletion, repeat the usage search yourself; evidence in the audit may be out of date.
- Findings under "Entscheidung nötig" (decision needed) are **not** implemented, but are referred back to the user.

### Session A — cell (`apriltag-aufraeumen`)

**Do not touch:**

- hot files: `src/vision_server/runner.py`, `detection/apriltag.py`, `server.py`, `stream_overlay.py`,
  `cellwatch.py`, `config.py`, `src/tagloc/overlay.py`, `src/tagloc/boards.py` (exception: one-line
  comment/docstring correction);
- reserved by the plan [`hand-eye-accuracy.md`](hand-eye-accuracy.md): `src/vision_server/handeye_session.py`,
  `src/tagloc/handeye.py`, `src/tagloc/cli/calibrate_handeye.py`, `src/tagloc/pose.py`, `src/tagloc/detector.py`;
- do not merge `apriltag-refactoring`. Individual commits via cherry-pick (`-x`) only if they apply without conflict
  and touch none of the files above; otherwise leave the finding open.

**Addendum 30.10.2026 (user decision):** `runner.py` and `detection/apriltag.py` are after all being split —
based on `usecase-foto` (more recent version, merged into `apriltag-aufraeumen`, `2e6360e`). The
hand-eye files are released (the state from `handeye-genauigkeit` is already on `apriltag`), subject to a
compatibility check: hand-eye payload `wsc.vision.handeye.status/1`, `session.json` and the OPC UA methods
must be identical before and after, measured against what the frontend reads. Order, so that the tests do not overlap:
first `detection/apriltag.py`, then `runner.py`, then hand-eye (D4, `finish()`, translation).
The remaining named hot files stay locked.

**Order:**

1. **T1** Delete prototype `src/apriltag/`, carry references in docstrings and docs along too.
2. **Text and hygiene:** T11, B2 (`.gitignore` exceptions), B3, B5 (README structure), B6 (`betreuer/` references), B7 (`cli/calibrate.py`: key "S", `--capture-to` headless), B10 (`diagnose_board` 7x10), B14 (make the hook robust).
3. **Dead code:** T2, T3 (cherry-pick candidate `7e67251`), T4, T5, T9 (only `profiles.py`), T10 (excluding hot/reserved files), T14 (only `cancel_running`).
4. **D11/B9** align hostnames in `src/jobs/calibrate.py` with the real Pi names, without changing `server.py`; plus test **L3**.
5. **Duplicates:** D12 (`tests/_support.py`, candidates `1c938f6`, `9a7942f`), D9, D13, D15 and D10 only in non-hot files, D16.
6. **Tests:** L1 + L6 (`VisionProgram`, `ua_program`; template `test_ua_methoden.py` from `dfcfa4d`), L7, L12.
7. **U4, U6**; last **U2** (translate English docstrings) only in non-hot, non-reserved files.

**Decision needed:** T6/T7, T8, T12/T13, B1 (version `data/`?), B11, B13, D4 and all decompositions from section 7
(only after merge of `usecase-foto`; D4 after `handeye-genauigkeit`).

### Session B — webskill (`ADP_Lokaliserung`)

**Preliminary (handover 30.10.2026):** block 1 is partly implemented — 12 commits on `ADP_Lokaliserung` (T1–T16, T18–T20, parts of B12); full tests have **not** yet been run on it. Open and to be taken over by session B: **B13** is half-edited in `frontend/src/features/cell-modules/components/AutolocateLayer1Window.tsx` and not committed (look at the diff, finish it or discard it), then **B9** and the rest of **B12** (incl. `RobotRunConfirm` to `opcua-server/components`). Only then all tests, then order item 1.

**Environment:** `frontend/node_modules` and the contents of `frontend/public/urdf/` are symlinks from the main checkout (submodule, `info/exclude`) — never commit them, always add files individually. 3D models are only LFS pointers. `origin` (git.rwth-aachen.de) needs an HTTPS login; without it `origin/dev` stays at `d824975`. Comments in the code are in English (like `dev`).

**Do not touch:** files and lines from the revert `839857c` (column "dev?" = revert: `features/task/*`, docs, mixed backend files), dev code, B5 (game models, licence question), B7 (test-phase switch), B8 (old-Pi branches), B10, D13/B6 (catalogue default), D14/U6 (tag tool), the world-tag chain (section 10 — its own feature with its own work plan).

**Order:**

1. **Shared helpers:** D6 + D15 (`vec3.ts`), D7 (angle), D9 (format/parse), D17, D18 (one speed constant), D10 (`createStore`).
2. **Pi calls:** D1 (one `callPiMethod` with `MethodSpec`), D4 (`useSubscribedNode`/`usePiBinding`), D5 (`useRobotModelLoaded`), D2 (wait helper), then D3 (`usePiCommand`) — **first** a test for the new hook (L1).
3. **Configuration:** D19 + B1–B4 (one `PI_SERVERS` list with role, label, address); U4 (hook in a loop in `PiActivity.tsx`).
4. **Structure:** U5 (resolve cycles between features, `index.ts` per feature), then U3 decompositions (`ModuleCatalogModal`, `TagReferencePanel`, `ApproachPlanPanel`, `usePickPlaceRun`, `HandEyeAutoPanel`, `modelPlanes`), one file per commit.

**Docs of session B** are in the cell repo on `apriltag`: create a separate temporary worktree from the current
`origin/apriltag` (do not use the one from session A), change only `legacy-wsc.md` (section 11) and part B
of `README.md`, commit, rebase onto `origin/apriltag`, fast-forward push (docs push to `apriltag` is approved), remove the worktree.


## Open tasks on the Pis (as of 30.10.2026)

Not done without access to the Pis. Keep to the order; before each restart check that no job and no calibration is running (a cell monitoring may end, the frontend restarts it). Back up beforehand:
`config/tagmap.json`, local changes (`diff > …patch`), `override.conf`.

**Ceiling Pi `ADP-Roboter-Lokalisierung`** (repo `/home/adp-roboter/Desktop/ADP_Lokalisierung/ADP_flexible_Roboterzelle`)

1. Check whether the unit switched on 30.10. is running: `ExecStart=<Repo>/.venv/bin/python -m vision_server.server`,
   `Environment=PYTHONPATH=<Repo>/src`; backup `/etc/systemd/system/opcua-server.service.bak-20261030`.
   Status active, journal without traceback, ports 4840 (OPC UA) and 8080 (MJPEG).
2. Set the repo to `origin/apriltag`. The Pi was at `6ebab91` (three commits that were reverted on the remote)
   with local changes to `config/tagmap.json` and `doc/project_docs/topics/apriltag-reference.md`: back up,
   `fetch`, check that `src tests tools` do not differ from `origin/apriltag`, then hard reset to
   `origin/apriltag`.
3. **Decide beforehand: world tag size 0.2 or 0.198.** Locally on the Pi it was 0.2, on `origin/apriltag` 0.198.
   If 0.2 applies, first change and push `config/tagmap.json` on `apriltag`, then step 2.
4. Fix the typo in the drop-in `/etc/systemd/system/opcua-server.service.d/override.conf`:
   `VISION_APRILTAG_QUAD_DECICMATE` → `VISION_APRILTAG_QUAD_DECIMATE` (value 1.5). It has never had an effect so far; afterwards detection becomes faster, small/distant tags are more likely to be missed — observe after the restart.
5. Remove `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` from the drop-in (emergency exit, not needed in operation as long as
   `data/calibration/cam_ceiling.json` exists).
6. Clean up OpenCV: installed are `opencv` 4.10.0 **and** `opencv-contrib-python` 5.0.0.93 (package conflict).
   Keep only `opencv-contrib-python`; afterwards check `import cv2; cv2.aruco` and a test job.
7. `daemon-reload`, one restart, check as in 1.

**Hand Pi `ADP-HandInEye-Kamera-Pi`**

1. Inventory as for the ceiling Pi (prompt in the chat of 30.10.: unit, drop-ins, Python/venv, OpenCV and pupil version, `VISION_*` variables, branch/commit/`status`, `config/tagmap.json` with sha256 and world tags, files under `data/calibration/` and `data/handeye/`).
2. Switch the unit to `-m vision_server.server` as on the ceiling Pi (backup, `daemon-reload`, restart, check).
3. Repo on `origin/apriltag`; same tag map as on the ceiling Pi (compare sha256).
4. Check for the same typo and the same points 5–6.

**Afterwards in the repo (session A):**

- When **both** units have been switched: version the unit template `deploy/opcua-server.service`, delete `src/OPCUA/`, adjust references in `vision-system.md` and in the handbook (B13, formerly D5, formerly D11).
- When both Pis have OpenCV ≥ 4.7: delete fallback branches for OpenCV < 4.7 (T12). Decision on `pupil-apriltags` (T13): installed on the ceiling Pi, unused in operation (backend `aruco`).
- Enter the findings in `legacy-cell.md` and in the world-tag section of `legacy-wsc.md`.

## Open questions

- Unclear tools (e.g. `src/vision_server/tools/pi_load_profiler.py`) are not deleted,
  but are recorded in the audit as a question for the team.

## Deviations from the plan

To be filled in during implementation.

## After completion

- Status set to `done`, index updated.
- Merge of `apriltag-aufraeumen` into `apriltag` and `ADP_Lokaliserung` into `Ungetestet` only on instruction.
