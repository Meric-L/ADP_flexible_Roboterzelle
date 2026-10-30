# Altlasten Zelle — Audit dieses Repos (`apriltag`)

Stand: 30.10.2026, Branch `apriltag`, Commit `d55df91`. Zeilennummern beziehen sich darauf.

Dieses Dokument löst die Zellen-Teile von [`altlasten.md`](altlasten.md) und
[`altlasten-toter-code.md`](altlasten-toter-code.md) ab; deren offene Punkte sind neu geprüft und tragen
hier die alte ID (Spalte „ex-ID“). Gegenstück für webskillcomposition: [`altlasten-wsc.md`](altlasten-wsc.md) —
dort steht auch die **Welttag-Kette über beide Repos** (Abschnitt 10). Was jede Datei tut:
[`code-handbuch.md`](code-handbuch.md), Teil A. Plan: [`arbeitsplaene/aufraeumen-und-code-handbuch.md`](arbeitsplaene/aufraeumen-und-code-handbuch.md).

**Aufgeräumt wird auf Branch `apriltag-aufraeumen`** (lokal, ungemergt). Was davon umgesetzt ist, steht am
Ende unter „Umsetzungsstand“.

**Nicht angefasst werden beim Aufräumen:**

- heiße Dateien (Spalte „heiß?“) — auf `apriltag` stark in Arbeit oder vom ungemergten Branch `usecase-foto` berührt;
- die vom Plan [`handeye-genauigkeit.md`](arbeitsplaene/handeye-genauigkeit.md) (Status „in Arbeit“) reservierten Dateien:
  `src/vision_server/handeye_session.py`, `src/tagloc/handeye.py`, `src/tagloc/cli/calibrate_handeye.py`,
  `src/tagloc/pose.py`, `src/tagloc/detector.py` — betrifft D4 und die Zerlegung von `finish()`;
- dokumentierte Schnittstellen (OPC UA, Payload, `tagmap.json`, CLI-Optionen).

## 0. Methode und Grenzen

**Skripte** (alle unter `/home/meric/.claude/jobs/ee1c32b3/tmp/agent-adp-tot/`, Repo nur gelesen):

| Skript | Zweck |
|---|---|
| `imports.py` | Import-Graph (src/tests/tools): wer importiert welches Modul |
| `defs.py`, `cand.py`, `cand2.py` | 1821 Definitionen (Funktion, Klasse, Methode, Konstante, Feld); Referenzen getrennt nach Produktion, Tests, Strings, Docstrings, Kommentaren, `__all__` |
| `reach.py`, `reach2.py` | namensbasierte Erreichbarkeit ab Modulwurzeln der Produktion (`self.x`-Aufrufe auf Klassenfamilie begrenzt); findet Ketten toter Funktionen |
| `fields.py`, `params.py`, `cli_opts.py` | nie gelesene Felder, nie gesetzte Parameter, nie gelesene argparse-Optionen |
| `unused_imports.py`, `redef.py`, `stale_refs.py` | unbenutzte Imports, Mehrfachdefinitionen, tote Verweise in Docstrings/Kommentaren |
| `dups_funcs.py`, `dups_near.py`, `dups_text.py`, `test_helpers.py` | normalisierte AST-Hashes (>= 5 Zeilen), Ähnlichkeit (4-Gramm-Jaccard >= 0,70), Textblöcke >= 4/6 normalisierte Zeilen, Test-Helfer mit gleichem Namen |
| `uchecks.py`, `lang.py` | Funktionen > 80 Zeilen, `print`, `except`, `__all__`, Sprache der Docstrings/Kommentare |
| `runcov.py`, `covreport.py`, `uncovered_*.py` | **gemessene Zeilenabdeckung**: komplette Suite (877 Tests, grün, 247 s) in einer Kopie des Repos (`git archive`) mit `sys.monitoring` (Python 3.12, nichts installiert) |
| `refbranch.py` | Fundstand auf Branch `apriltag-refactoring` |

Suchbefehle: `git grep -nw <Name> -- src tests tools doc config README.md '*.sh' '*.bat'` (Wortsuche), dazu gezielte `git grep -n -E`.

**Grenzen:**
- Namensbasiert: Namenskollisionen machen Code „lebendig“ (zu wenig gemeldet), nie „tot“. Dynamischer Zugriff wurde geprüft (`getattr`, Strings, asyncua-Callbacks). Drei Fehlalarme dadurch ausgeschlossen: `SharedCamera.factory_calibration` (`getattr` in `detection/apriltag.py:451`), `CameraStreamConfig.overlay_interval_s/overlay_detect_interval_s` (`getattr` in `runner.py:137,140`), `EventCollector.event_notification` (Callback von asyncua).
- Die Coverage erfasst nur den Hauptprozess; Unterprozesse (Skripte von `ScriptDetectionSource`) fehlen. Zeilen-, nicht Zweigabdeckung.
- Nicht belegbar aus dem Repo: Hardware, systemd-Units (nicht versioniert, Altlast D5), OpenCV-Version auf den Pis, Nutzung durch das WSC-Frontend.
- Sprachheuristik (Stoppwörter) ist grob; nur Größenordnungen.

**Wichtigster Kontext vor allen Tabellen:**
- **Branch `apriltag-refactoring`** (Plan `refactoring-apriltag.md`, Status „fertig“, nicht integriert) enthält 40 Commits und löst viele Funde unten bereits. Verweis jeweils als „refactoring `<hash>`“. Er berührt fast alle heißen Dateien (`runner.py`, `detection/apriltag.py`, `stream_overlay.py`, `job.py`, ...). **Der Aufräum-Branch sollte nicht parallel dieselben Stellen ändern**, sondern darauf aufbauen oder nur Funde nehmen, die dort fehlen (Spalte „refactoring“).
- **Branch `usecase-foto`** (2 Commits, nur Ergänzungen) ändert `runner.py` (+183), `detection/apriltag.py` (+5), `config.py` (+3).
- Spalte **heiß?** = Datei wird auf `apriltag` stark bearbeitet oder von `usecase-foto` berührt: `runner.py`, `detection/apriltag.py`, `server.py`, `stream_overlay.py`, `cellwatch.py`, `tagloc/overlay.py`, `tagloc/boards.py`, `config.py`.

---

## 1. Toter Code (T)

Schnittstelle: OPC UA / Payload / CLI / Datei = Dateiformat / nein.

| ID | Was | Wo | Beleg | Konf. | Empfehlung | Risiko | Aufw. | Schnittst. | ex-ID | heiß? | refactoring |
|---|---|---|---|---|---|---|---|---|---|---|---|
| T1 | **Prototyp-Paket** `src/apriltag/` (`calibrate_camera.py` 291 Z., `detect_apriltags.py` 107 Z., `MANUAL_TEST.md`) | `src/apriltag/` | `imports.py`: von niemandem importiert; `git grep -n "src/apriltag"`: nur Docstrings (`tagloc/boards.py:3,277`, `calibration.py:8`, `pose.py:3`), `MANUAL_TEST.md` (markiert sich selbst „abgelöst“), Doku (`apriltag-lokalisierung.md:173,460,676-685`, `apriltag-referenz.md:435`). Coverage 0 %. Schreibt/liest `calibration.yaml` (`FileStorage`), das nirgends sonst gelesen wird und nicht getrackt ist. Löschbedingung der Doku („erst nach einmaligem Lauf der neuen Kette an der echten Pi-Kamera“, `apriltag-lokalisierung.md:683`) ist erfüllt: `vision-server-interface.md:1333-1338` (Hand-Pi real kalibriert mit `tagloc.cli.calibrate`, RMS 0,2945 px, 21 Aufnahmen) | sicher | Ordner löschen; Verweise mitziehen (3 Docstrings, 2 Doku-Stellen, `altlasten.md:116-118`); danach prüfen, ob `pupil-apriltags` noch gebraucht wird (T13) | niedrig | S | nein | ex T1, P2 | nein | bleibt dort stehen |
| T2 | `poses_to_json` | `tagloc/cli/_common.py:229` | `git grep -nw poses_to_json`: nur Definition. Docstring nennt `transform.py`, das es nicht benutzt | sicher | löschen (oder `transform.py` darauf umstellen) | minimal | S | nein | ex T4 | nein | dort von `transform` benutzt (b679606), Fund entfällt |
| T3 | `SharedCameraSource`, `iter_frames`, `FrameSource` (Protocol, nur `__all__`), `ImageFolderSource.current_path` | `tagloc/frames.py:20,52,174,222` | `git grep -nw`: nur Definition und `__all__` (`frames.py:254-261`); keine Doku | sicher | löschen | niedrig | S | nein | ex T2, T3 | nein | entfernt (7e67251) |
| T4 | `build_detection_source` (Einzahl) | `vision_server/detection/__init__.py:51` | nur Definition und `__all__:70`; benutzt wird `build_detection_sources` | sicher | löschen | niedrig | S | nein | ex T5 | nein | entfernt |
| T5 | `Program.get_current_state` | `ua_program/program.py:162` | nur Definition (Betreuer-Vorlage; Vorlage liegt nicht mehr im Repo, siehe B6) | sicher | löschen | niedrig | S | nein | ex T6 | nein | noch da (program.py:169) |
| T6 | `world_tag_for_robot`, `robot_locations`, `_rank`, `merge_locations` (Kette, nur von Tests benutzt) | `tagloc/localize.py:474,483,508,513` | `reach2.py`: aus Produktion nicht erreichbar; Treffer nur `tests/test_welttags.py:29-30,370,381,414,422`. Doku beschreibt sie als API (`apriltag-lokalisierung.md:155,163`, `apriltag-referenz.md:490-500`); Layer-2-Steuerung liegt inzwischen beim WSC-Frontend | sicher (Produktion tot) | **Entscheidung nötig**: löschen samt Test und Doku, oder bewusst als Bibliothek behalten und in der Doku so kennzeichnen | mittel (Tests, Doku) | M | nein | ex T7, P4 | nein | gebündelt, nicht gelöscht (4e27abf); `robot_locations` bewusst behalten |
| T7 | `estimate_tag_pose` (Einzahl), `CameraCalibration.camera_params` | `tagloc/pose.py:151`, `calibration.py:66` | Produktion nutzt `estimate_tag_poses` bzw. rechnet selbst; Treffer nur in Tests und Doku | wahrscheinlich | als Test-/Doku-API behalten oder samt Tests löschen; nach T6 entscheiden | niedrig | S | nein | neu | nein | – |
| T8 | **Werkzeuge ohne Einstieg**: `vision_server/tools/list_realsense_profiles.py` (59 Z.), `pi_load_profiler.py` (465 Z.) | `src/vision_server/tools/` | `git grep -nw`: keine Referenz in Code, Tests, Doku (ausser dem alten Audit). `list_realsense_profiles` dupliziert die Profilschleife aus `camera._list_realsense_color_profiles` (D9). Coverage 0 % | wahrscheinlich | `list_realsense_profiles`: löschen oder auf gemeinsamen Helfer umstellen; `pi_load_profiler`: Nutzer fragen (Handwerkzeug für Pi-Last), sonst löschen | niedrig | S | nein | ex T8 | nein | auf dem Branch nicht mehr in `tools/` aufgeführt |
| T9 | **Tote Konfigurationsfelder**: `AprilTagProfileConfig.use_picamera`, `.camera_index`, `.warmup_s`; `VisionServerConfig.configuration_id` | `profiles.py:33,34,43`; `config.py:44` | `use_picamera`: kein Treffer ausser Definition. `camera_index`/`warmup_s`: Leser nur `camera.py:437,515,651` auf der `CameraStreamConfig`, kein Lesezugriff auf einer AprilTag-Config. `configuration_id`: nur Default `"hello-world-config"` (Rest des entfernten Profils), die Quelle baut ihren Wert selbst (`detection/apriltag.py:308`) | sicher (`use_picamera`, `configuration_id`), wahrscheinlich (Rest) | Felder löschen; Kwargs in Tests prüfen | niedrig | S | nein | neu (zu C4) | `config.py` ja (usecase-foto) | `config.py` -1 Zeile (vermutlich `configuration_id`) |
| T10 | **Unbenutzte Imports** (src): `timezone`; `DI_DEVICE_HEALTH_ALARMS`; `time`; `invert`, `rvec_from_rotation` | `tagloc/overlay.py:22`; `asset_model.py:29`; `calibration_session.py:42`; `handeye_session.py:48,51` | `unused_imports.py`, je `grep -nw` bestätigt (`DI_DEVICE_HEALTH_ALARMS` nur Import + Definition `nodeset_ids.py:57`, damit auch die Konstante tot). Tests: `test_calibration_session.py:33`, `test_cellwatch_posestore.py:8,12,19`, `test_reference_edges.py:37`, `test_tagreference_liveview.py:9,22` | sicher | entfernen, Konstante `nodeset_ids.py:57` mitnehmen | minimal | S | nein | neu | `overlay.py` ja | teilweise (e63904f, `nodeset_ids.py` -4 Zeilen) |
| T11 | **Verweise auf nicht (mehr) Vorhandenes** | `jobs/calibrate.py:22` („`OPCUA/server.py`“); `discovery/mdns.py:3`, `ua_program/program.py:3` (`betreuer/...`: Ordner seit 69bf237 vom 23.09. entfernt, Vorlage nie getrackt); `camera_health.py:133`, `tools/calibration_client.py:23` (`doc/vision-server-interface.md`, richtig `doc/projektdoku/...`); `server.py:116` (`tools/diagnose_board.py`, richtig `src/vision_server/tools/`); `requirements.txt:8,13` | `stale_refs.py` + `git ls-files`/`git log -- betreuer` | sicher | Texte berichtigen (reine Kommentare) | keines | S | nein | ex T9 | `server.py` ja, übrige nein | `jobs/calibrate.py` dort neu geschrieben |
| T12 | **OpenCV < 4.7-Rückfälle** (`CharucoBoard_create`, `Dictionary_get`, `interpolateCornersCharuco`, `drawMarker`, ältere `DetectorParameters`) | `boards.py:81-87,108-114`; `detector.py:73,189-193,237`; `pose.py:126`; `tools/make_*` | `requirements.txt` verlangt `opencv-contrib-python>=4.8.0`; `# pragma: no cover`; Coverage: nie ausgeführt | **unsicher** (Pi-OpenCV nicht belegbar; Altlast D2/D3 nennt „4.x auf dem Pi“) | erst Pi-OpenCV-Version feststellen; wenn >= 4.7, Zweige löschen | mittel | S | nein | zu D2/D3 | `boards.py` ja | – |
| T13 | **pupil-Backend** `PupilAprilTagDetector` samt `--backend pupil/auto` und Abhängigkeit `pupil-apriltags` | `tagloc/detector.py:251,287-316`; `cli/_common.py:86`; `profiles.py:49`; `requirements.txt:6` | Produktion setzt nur `"aruco"`; `git grep -i pupil tests` leer; Klasse nie ausgeführt | wahrscheinlich | Backend + Abhängigkeit zusammen streichen **oder** Test mit Attrappe ergänzen | mittel (OpenCV ohne Tag-Familie, Altlast D3) | M | CLI (`--backend`) | ex P1 | nein | – |
| T14 | **Nie gesetzte Optionen** (Auswahl) | `events.fire_result_ready(is_partial)` `events.py:59`; `VisionStateMachines.recover(halt=True)` `state_machine.py:167`; `JobRunner.cancel_running(timeout)` `job.py:186`; `mdns.announce(address, caps)` `mdns.py:89`; `draw_tag_overlay(show_distance)` `overlay.py:192`; `leave_one_out_errors(huber_delta_px)` `handeye.py:695`; `PupilAprilTagDetector(nthreads)` | `params.py`: an keiner Aufrufstelle (Produktion und Tests) gesetzt. `is_partial` = Altlast C2; `recover(halt)` bildet einen Spec-Übergang ab | wahrscheinlich | nur die zwei ohne Spec-Bezug vereinfachen (`cancel_running`, `show_distance`); Rest als dokumentierte Voreinstellung lassen | niedrig | S | OPC UA nur bei `is_partial` | ex C2 | `overlay.py` ja | – |
| T15 | **Artefakte**: `board.svg` (ChArUco 7x5, 41 KB, Wurzel); `hardware/Hardware_Architektur_v2(2).drawio.png`; zwei `data/rohbilder/cam_ceiling_20261030-*.png` (`141931`, `141939`) | Wurzel, `hardware/`, `data/rohbilder/` | `board.svg`: nur Beispiel-Ausgabe von `make_tag_sheet.py`; die Pis nutzen Schachbrett 7x10 („kein ChArUco“, `server.py:140`). PNG: von keiner Datei referenziert. Rohbilder vom 30.10.: kein Treffer in Code/Doku/Tests (`git grep cam_ceiling_2026`); Tests brauchen nur `...20261019-114704/142133` (`tests/test_detector.py:29,34`) | wahrscheinlich | `board.svg` entfernen (neu erzeugbar); Rohbilder nur behalten, wenn Messreihe (siehe B1) | niedrig | S | nein | ex P9 | nein | – |

**Geprüft und bewusst NICHT als tot gemeldet:**

| Was | Grund |
|---|---|
| `src/OPCUA/server.py` (ex D11) | Einstieg der nicht versionierten systemd-Unit (Altlast D5 offen); niemand importiert ihn, das ist Absicht |
| `vision_server/__main__.py` (`python -m vision_server`) | zweiter Serverstart (Port 4841) für Entwicklung, dokumentiert (`vision-system.md:37,178`, `vision-system-next-steps.md:25`); `build_config` ungetestet |
| `src/jobs/calibrate.py` | Prüf-Job des Rezepts `calibration` (OPC UA), gestartet von `ScriptDetectionSource` über `JOBS_DIR` (`detection/__init__.py:12,20`); siehe Abschnitt 3 |
| `tools/make_synthetic_scene.py` (`render_reference_scene`, `_polygon_basis`, ...) | von Tests importiert (`from tools.make_synthetic_scene import ...`), Test-Orakel |
| `vision_server/tools/{calibration_client,handshake_client,stream_viewer,diagnose_board,measure_framerate}.py` | in Doku als Referenzclients/Handwerkzeug genannt (`vision-server-interface.md:520,1266-1270`, `frontend-integration-kalibrierung.md:239-247`, `deckenkamera-volle-aufloesung.md:126`) |
| `DeviceHealth.MAINTENANCE_REQUIRED`, `VisionAssetNodes.computing_device` | nur von Tests gelesen, aber Teil des OPC-40100-2-Modells |
| `tagloc.cli.*` (5 CLIs) | in 8+ Doku-Dateien als Startbefehle genannt; keine Tests (Abschnitt 4) |
| `data/LV1/` (40 JPG, 79 MB) | kein Code liest sie, nicht neu beschaffbar; als Fehlalarm-Referenz in Doku genannt (`apriltag-referenz.md:1041`) |

**Übernahme aus den Altlastenlisten (Zelle):**

| Alt-ID | Stand heute |
|---|---|
| T1–T7, T9, P1, P2, P4, P9 | siehe T1–T15 (alle noch vorhanden; T3/T4 nur auf refactoring entfernt) |
| T8 | `list_realsense_profiles` und `pi_load_profiler` noch ohne Referenz; `measure_framerate.py` ist in `deckenkamera-volle-aufloesung.md:126` genannt, also nicht tot |
| P3 (drei Wege zur Kamerapose) | weiterhin alles benutzt; nicht vertieft |
| P7 (`data/LV1`), P8 (Rohbilder 19.10.) | LV1: behalten; Rohbilder 19.10.: in Gebrauch |
| C2 (`IsPartial` fest) | noch so: `result_management.py:31,115`, `events.py:59,76` |
| C7 (LDS-URL im Code) | noch so: `discovery/lds.py:63-68` (`CELL_LDS_URL`, `DEFAULT_LDS_URL` zwei Namen für denselben Wert); per `OPCUA_LDS_URL` überschreibbar |
| D1 (`.gitignore`) | noch so, siehe B2 |
| D4 (`setup.sh/.bat` kein Projekt-Setup) | noch so: nur ShareLaTeX-Remote und Aliase |
| D5 (systemd-Unit nicht versioniert) | offen; hält `src/OPCUA/server.py` am Leben |
| D2/D3 (OpenCV 4.x auf dem Pi ungeprüft) | offen, siehe T12 |
| D11 (`src/OPCUA/server.py`) | behalten (s. o.) |

---

## 2. Doppelungen (D)

| ID | Was | Wo | Beleg | Konf. | Welche Variante bleibt / Umstellung | Risiko | Aufw. | Schnittst. | ex-ID | heiß? | refactoring |
|---|---|---|---|---|---|---|---|---|---|---|---|
| D1 | **`BoardSpec` aus der Kamera-Config gebaut, 3 Kopien** | `calibration_session.py:240`, `detection/apriltag.py:930` (`default_board_spec`), `stream_overlay.py:253` (`_board_spec`) | `dups_text.py`: 6–7 identische Zeilen; `stream_overlay.py:245` sagt selbst „dieselbe Quelle wie `CalibrationSession._spec()`“ | sicher | eine Funktion `board_spec_from_config(config)` (Import von `tagloc.boards` lazy, damit `profiles.py` frei bleibt); drei Aufrufer umstellen | niedrig | S | nein | neu | ja (apriltag, stream_overlay) | umgesetzt 743e473 |
| D2 | **NaN/Inf -> `None`-Helfer in 5 Varianten** | `handeye_session.py:173`, `measureboard.py:89`, `measuretag.py:240` (alle drei byte-gleich, `dups_funcs.py`); `detection/apriltag.py:107` (rundet 4 Stellen); `tagloc/handeye.py:854` (rundet 6 Stellen); inline 4x in `handeye_session.py:702-705`; `observations.py:82` | s. „Wo“ | sicher | ein Helfer `finite_or_none(value, digits=None)` in einem stdlib-Modul; Rundung als Parameter, weil sie das Dateiformat berührt (`handeye.py` schreibt 6 Stellen) | niedrig, Rundung beachten | S | Datei, Payload | neu | `apriltag.py` ja | umgesetzt 5bce4f4 (`tagloc/jsonio.py`) |
| D3 | **JSON-Anfrage lesen und Ergebnis schreiben**: Vorspann `json.loads` -> „Kein JSON“ -> Schemaprüfung 5x; `_is_int/_is_number/_numbers` 3x; `_iso` 3x; `dumps(..., allow_nan=False, default=float)` 5x (+`payload._dumps`); private Querimporte `_load/_pose` (`handeye_session.py:69`), `_parse_board` (`runner.py:1247`) | `cellwatch.py:557`, `layer2/plan.py:380-396`, `measureboard.py:70,85,202`, `measuretag.py:66,98,247,251`, `tagreference.py:72-76,101,325,425` | `git grep "Kein JSON"` (5 Treffer); `measureboard.py` importiert schon Teile aus `measuretag.py`, kopiert aber `_iso/_finite/dumps` | sicher | gemeinsames Modul `vision_server/jsonio.py` (Parser-Vorspann, `dumps`, `iso`); `_parse_board` nach `measureboard` verschieben | niedrig (Fehlertexte sind durch Tests gebunden) | M | Payload (Fehlertexte) | neu | `runner.py` ja, übrige nein | „Kein JSON“ dort 0 Treffer, umgesetzt (3874bda, fdc170f) |
| D4 | **Hand-Auge-Verfeinerung zweimal**: Pipeline Undistort -> `refine_hand_eye_robust` -> Ausreisser -> Leave-one-out -> `reconstruction_spread` einmal im Server (`_refine_board`), einmal im CLI (`solve_session`); `SESSION_SCHEMA` zweimal (`calibrate_handeye.py:75`, `handeye_session.py:78`); Schreiber und Leser der `session.json` ohne gemeinsame Definition | `handeye_session.py:193-243`; `tagloc/cli/calibrate_handeye.py:143-266` (124 Z.) | Lesen beider Funktionen; CLI-Docstring: „rechnet dieselbe Kalibrierung neu“ | sicher | Pipeline als eine Funktion nach `tagloc/handeye.py`, Server und CLI rufen sie; Schema-Konstante dorthin; CLI druckt nur | mittel (Rechenweg, CLI ohne Test) | M | Datei (`session.json`) | neu | nein | CLI dort zerlegt und getestet (`test_cli_calibrate_handeye.py`) |
| D5 | **Pose aus `{position, orientation}` lesen und prüfen**: 3 Varianten mit Nullquaternion-Test (`< 1e-9`) | `layer2/plan.py:369-376`, `tagreference.py:111-120`, `detection/apriltag.py:75-107` (+ `geometry.pose_from_dict` ungeprüft) | `git grep 1e-9` | wahrscheinlich | **nicht blind vereinheitlichen**: refactoring hat das bewusst abgelehnt (9f56bb2: Fehlerverhalten unterscheidet sich); höchstens eine geprüfte Variante für die zwei JSON-Fälle | mittel | S | Payload | neu | `apriltag.py` ja | bewusst nicht umgesetzt |
| D6 | **OPC-UA-Methoden-Wrapper dreifach**: `refusal()` + Guards (parse -> Operational -> busy) + `add_method([String],[String,Int32])` | `runner.py:1233-1286` (MeasureBoard), `1300-1366` (MeasureTags), `1370-1547` (TagReference) | `dups_text.py` (6-Zeilen-Blöcke 1223/1234/1301/1371/1712) | sicher | Hilfsfunktion `json_method(...)` in `ua_nodes.py`; Zerlegung siehe Abschnitt 7 | mittel (viele OPC-UA-Tests) | M | OPC UA (Verhalten gleich lassen) | neu | **ja** | `ua_nodes.add_named_variable` (28c0365), Methoden-Teil dort nicht |
| D7 | `start_single_job` / `start_continuous` (Zulassung, 95 % gleich), `_run` / `_run_continuous` (75 %) | `job.py:230-285,287,359`; `runner.py:487-549` (Wrapper dazu) | `dups_near.py` | sicher | gemeinsamer Zulassungsschritt; Wrapper in `runner.py` ebenso | niedrig (gut getestet: `test_job_runner.py`) | M | OPC UA | neu | `job.py` nein, `runner.py` ja | umgesetzt (5d1714b) |
| D8 | **OPC-UA-Testclients**: Argumente `--url/--namespace/--vision-system`, `Client(...)`, `get_namespace_index`, `get_node(NodeId(...))`, `main()`-Rumpf | `tools/calibration_client.py:112-129`, `handshake_client.py:116-135`, `stream_viewer.py:90-108`, `diagnose_board.py:41-111` | `dups_near.py` 0,79; `dups_text.py` | sicher | **niedrige Priorität**: `calibration_client.py:23` will bewusst „reiner OPC-UA-Client ohne `vision_server`-Import“ bleiben (Referenz zum Abschreiben). Höchstens gemeinsame Argumentgruppe | niedrig | S | nein | neu | nein | – |
| D9 | **RealSense-Profilabfrage** | `camera.py:113-138` und `tools/list_realsense_profiles.py:27-37` | `dups_text.py` (5 Zeilen); gleiche Schleife `query_sensors -> get_stream_profiles -> color` | sicher | Helfer in `camera.py`, Tool ruft ihn; oder Tool löschen (T8) | niedrig | S | nein | neu | `camera.py` nein | – |
| D10 | **Konstanten an mehreren Stellen** (Tabelle unten) | verstreut | `git grep` | sicher | zentral definieren | niedrig | M | CLI-Defaults, Dateiformat | neu | teils | `DEFAULT_TAG_FAMILY` (96a7224) |
| D11 | **Frame-/Pi-Identität doppelt, mit Abweichung**: `jobs/calibrate.py:26` kennt Hostnamen `pi-decke`/`pi-hand`, `server.py:73-74` (`PI_IDENTITIES`) die echten `ADP-Roboter-Lokalisierung`/`ADP-HandInEye-Kamera-Pi`; Pfade `data/calibration/<frame>.json`, `config/tagmap.json` in `jobs/calibrate.py:34-35` und `server.py:311-312` nochmals gebaut | `jobs/calibrate.py:21-35`; `server.py:70-84,300-312` | Wortsuche nach `pi-decke` und `ADP-Roboter`; `git log -S` zeigt: `server.py` wurde am 21.09. umgestellt, `jobs/calibrate.py` nicht. Folge: Rahmen „world“, Rezept `calibration` meldet „KEINE KALIBRIERUNG unter .../world.json“, ausser `VISION_FRAME_ID` steht in der (unversionierten) Unit (**Wirkung unsicher**) | sicher (Drift), unsicher (Wirkung) | Identität in ein stdlib-Modul (ohne asyncua), beide nutzen; oder Server übergibt Rahmen und Pfade als Job-Parameter | niedrig | S | OPC UA (Rezept `calibration`, Textausgabe) | neu | `server.py` ja | behoben (f856b9b: Rahmen und Pfade vom Server) |
| D12 | **Test-Helfer mehrfach**: `pose()` in 6 Testdateien (3 byte-gleich: `test_handeye/tag_map/welttags`; 2 inhaltlich gleich), `FakeCamera` 6 Varianten, `FakeNode` 4, `FakeServer` 3, `build()` (`test_asset_model.py:36` = `test_part10_fassade.py:31`), Klassen `ResultTest/SourceTest/OpcUaTest/RequestTest` in `test_measuretag/measureboard/tagreference` (11-Zeilen-Block `test_measuretag.py:293-306` = `test_tagreference.py:844-857`) | `tests/` | `test_helpers.py`, `dups_funcs.py tests` | sicher | `tests/_support.py` mit `pose()`, `FakeNode`, `build()`; die `FakeCamera`-Varianten nicht erzwingen (verschiedene Zwecke) | niedrig | M | nein | neu | nein | umgesetzt (1c938f6, 9a7942f) |
| D13 | **Marker-/Board-Erzeugung**: `tools/make_tag_sheet.py:39-85` (`_dictionary`, `render_marker`, `render_charuco`) wiederholt `detector.predefined_dictionary` (`dups_near` 0,88) und `boards.build_board` samt Versionsweichen; `generateImageMarker/drawMarker` auch in `make_synthetic_scene.py:287` | `tools/` | Lesen | wahrscheinlich | Tool importiert aus `tagloc`. **Ausnahme**: `make_synthetic_scene.chessboard_object_points` (:595) bleibt unabhängig, es ist das Orakel für `boards.py` (Docstring) | niedrig | S | nein | neu | `boards.py` nein | umgesetzt (29c0e6e); Renderer nach `tagloc/synthetic.py` verlegt (e147473): Frage, ob ein Test-Orakel in die Bibliothek gehört |
| D14 | **Task abbrechen und abwarten** (`cancel()`, dann `await` mit `suppress(CancelledError)`) 5x; `calibration_session._pool/_save_pool` identisch bis auf Namen | `camera.py:680`, `camera_health.py:233`, `camera_stream.py:364`, `cellwatch.py:1162`, `runner.py:444`; `calibration_session.py:250-271` | `dups_funcs.py` (`stop` = `_halt`) | sicher | Helfer `cancel_and_wait`, `SerialExecutor` | niedrig | S | nein | neu | `cellwatch`, `runner` ja | umgesetzt (0fd39f0, 9ea0b9a) |
| D15 | **Overlay** geprüft: **keine echte Doppelung** zwischen `tagloc/overlay.py`, `reference_overlay.py`, `stream_overlay.py`. Nur: `reference_overlay.py:21` importiert privates `_scale_for`; `stream_overlay.py:294,329,364,603` importiert `_resize_for_stream` aus `camera_stream` viermal inline; `_FONT_SCALE` zweimal mit verschiedenen Werten (0,5 / 0,55) | s. „Wo“ | Lesen | sicher | `_scale_for` und `_resize_for_stream` öffentlich machen | niedrig | S | nein | neu | ja | – |
| D16 | **Kleinkram**: Zeitstempel `%Y%m%d-%H%M%S` in `raw_image.py:21` und `tagreference_log.py:40`; UTC-`Z`-Zeitstempel in `handeye.py:827` und `handeye_session.py:273` | s. „Wo“ | `git grep strftime` | sicher | optional, mit D3 | niedrig | S | Dateiformat (Format erhalten) | neu | nein | – |

Zwei Detektor-Backends (ex P1) siehe T13.

**D10: mehrfach vorhandene Konstanten**

| Konstante | Stellen |
|---|---|
| Tag-Familie `"tag36h11"` | 13x: `cli/_common.py:84`, `cli/calibrate_handeye.py:103`, `detector.py:76,159,259,281`, `tagmap.py:131,343`, `profiles.py:41`, `make_synthetic_scene.py:316,458,751,867` (refactoring: `DEFAULT_TAG_FAMILY`, 96a7224) |
| Standard-Taggröße `0.05` | 6x: `cli/_common.py:92`, `cli/calibrate_handeye.py:140`, `overlay.py:198`, `pose.py:169`, `tagmap.py:503`, `profiles.py:42`; die Pis überschreiben mit `0.080` (`server.py:90,139`) |
| Brett-Standard 9x6, 30 mm, Marker 22 mm | 5x: `boards.py:34-39`, `cli/calibrate.py:39-43`, `profiles.py:112-117`, Prototyp, `board.svg` (ChArUco 7x5). **Die Pis nutzen 7x10, 23 mm** (`server.py:127-129,148-150`). Doku nennt noch ChArUco 7x5 (`apriltag-e2e-test.md:148,232`, `apriltag-referenz.md:702`) bzw. 6x7 (`vision-server-interface.md:1330`) |
| Mindestaufnahmen `15` / `3` | `15`: `cli/calibrate.py:32`, `profiles.py:120` (+ Prototyp); `3` als Literal: `cli/calibrate.py:77,152`, `boards.py:433,452`; Konstante `calibration_session.py:64`; `handeye.py:57` (`MIN_SAMPLES = 3`, anderer Sinn, gleicher Name wie CLI-`MIN_SAMPLES = 15`) |
| `REPO_ROOT` | 4x: `jobs/calibrate.py:17`, `profiles.py:16` (`_REPO_ROOT`, von `config.py:6` und `runner.py:28` importiert), `server.py:219`, `tools/measure_nodeset_import.py:20` |
| Pfade `data/calibration/<frame>.json`, `config/tagmap.json` | `jobs/calibrate.py:34-35`, `server.py:311-312`, `profiles.py:19-21` (Default `camera.json`) |
| Endpoint-Pfad `/raspi/server/`, `127.0.0.1:4840` | `server.py:58` (Quelle), 4 Clients (`--url`-Default), Tests |
| Namespace-URI `http://launch-rm.de/vision` | `config.py:41`, 4 Clients, 8 Testdateien |
| Auflösungen `(2028,1520)`, `(640,480)` | `frames.py:112,148,205,207`, `profiles.py:35,213`, `server.py:145` |
| `"cam_ceiling"`/`"cam_flange"` | 14x in `server.py`, 14 weitere Stellen in 10 Dateien |
| Schema-Strings `"wsc.vision.*"` | 14 Dateien; `SESSION_SCHEMA` zweimal (D4) |

---

## 3. Kalibrierung

Der Nutzer fand „3 Kalibrier-Skripte“: sehr wahrscheinlich `src/apriltag/calibrate_camera.py`, `src/jobs/calibrate.py` und `src/tagloc/cli/calibrate.py`. Mit Hand-Auge sind es fünf Programme mit „calibrate“ im Namen.

| Datei | Zweck | Einstieg (wer ruft wie) | Status | Doku-Verweise (ohne Altlastenlisten) | Tests (Coverage) | Empfehlung |
|---|---|---|---|---|---|---|
| `src/apriltag/calibrate_camera.py` | Prototyp: interaktive Kalibrierung mit `cv2.VideoCapture`, schreibt OpenCV-YAML | von Hand `python src/apriltag/calibrate_camera.py`; niemand importiert es | **abgelöst** (`MANUAL_TEST.md:3-9` sagt es selbst) | 1 Datei (`apriltag-lokalisierung.md:460,676`) | keine (0 %) | **löschen** (T1) |
| `src/apriltag/detect_apriltags.py` | Prototyp-Erkennung, ohne Entzerrung | von Hand | abgelöst | `apriltag-referenz.md:435` | keine (0 %) | löschen (T1) |
| `src/jobs/calibrate.py` | **keine Kalibrierung**, sondern Bereitschaftsprüfung: Kalibrierung vorhanden? Auflösung, Modell, RMS, Alter, Tag-Map | OPC-UA-Rezept `calibration` -> `detection/__init__.py:20-21` -> `ScriptDetectionSource` startet `sys.executable jobs/calibrate.py` als Subprozess; stdout wird `attributes.message` | **aktiv, Prüf-Job** (Schnittstelle: `vision-server-interface.md:242`) | 3 Dateien | keine (0 %; `test_script_runner` startet nur Wegwerfskripte) | **behalten, korrigieren**: Hostnamen veraltet (D11); Hinweistext nennt `--board charuco` (`:41`), die Pis nutzen Schachbrett 7x10; Name irreführend (Umbenennung in `calibration_check.py` möglich, nur `JOBS_DIR`-Eintrag ändern); Test ergänzen |
| `src/tagloc/cli/calibrate.py` | Kamerakalibrierung am PC/Pi, interaktiv (Fenster) oder aus Bildordner | `python -m tagloc.cli.calibrate`; **Server vorher stoppen** (öffnet Kamera selbst) | **aktiv als Werkzeug** (offline, Notweg; am Hand-Pi real benutzt, `vision-server-interface.md:1333`) | 5 Dateien | keine (0 %); Rechenkern `boards.calibrate_from_samples` ist getestet | **behalten**, Mängel beheben: Kopfzeile nennt Taste `S = save` (`:3`), gibt es nicht (Tasten: Leertaste, C rechnet+speichert, Q); `--capture-to` wirkt nur im Fenstermodus (`:110-149`); Standardgeometrie 9x6/30 mm passt nicht zu den Pis; Doku nennt noch ChArUco; Literal `3`; zerlegen (refactoring 185b278) |
| `src/tagloc/cli/calibrate_handeye.py` | Hand-Auge `T_flansch_cam`: (a) `--samples` Tag-Aufnahmeliste, (b) `--session` Sitzung des Hand-Pi offline neu rechnen | `python -m tagloc.cli.calibrate_handeye` | **Werkzeug**; (a) ist der **ältere Weg** (`handeye-genauigkeit.md:151` „alter Weg“), für `wsc.vision.handeye.samples/1` gibt es im Repo keinen Schreiber; (b) aktuell | 7 Dateien | keine (0 %); Rechenkern `tagloc/handeye.py` 97 % | (b) behalten und mit dem Server-Pfad zusammenführen (D4); (a) nach Rückfrage streichen oder als „Legacy“ kennzeichnen; Test ergänzen |
| `src/vision_server/calibration_session.py` | Kamerakalibrierung **im laufenden Server** über OPC UA (`StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration`, `CalibrationProgress`); liest aus der geteilten `SharedCamera` | `runner.py` baut die Instanz, Frontend ruft Methoden | **aktiv, Produktionspfad** | 6 Dateien | `test_calibration_session.py` (93 %) | behalten; `calibration_min_samples` wird nur als `minSamples` angezeigt (`:371`), die Rechenschwelle ist die Konstante 3 (`:64`): Name/Doku klären (B11) |
| `src/vision_server/tools/calibration_client.py` | Testclient/Referenz: Start, Fortschritt, Finish per OPC UA | von Hand | **Werkzeug (Referenz)**, nicht abgelöst | 2 Dateien | keine (0 %) | behalten; veralteten Pfad in `:23` berichtigen |
| `vision_server/tools/stream_viewer.py`, `diagnose_board.py` | Aufnahme per Leertaste; Brettgeometrie durchprobieren | von Hand | `stream_viewer` aktiv; `diagnose_board` **teilweise abgelöst** durch `MeasureBoard(diagnose=true)` (`measureboard.py:43`, `boards.diagnose_board`, `vision-server-interface.md:1723`); `DEFAULT_COMBOS` (`:30`) kennen das Brett 7x10 (seit 28.10.) nicht | je 2 Dateien | keine | `diagnose_board`: Kombinationen aktualisieren oder streichen, wenn die `MeasureBoard`-Diagnose reicht |
| `src/tagloc/calibration.py`, `boards.py`, `lens.py` | Datenformat (JSON), Brett-Erkennung und Rechenkern, Linsenmodelle | von allen obigen | **aktiv, gemeinsamer Kern** | 2 Dateien (`calibration`) | 100 % / 73 % / 95 % | behalten; ChArUco-Pfad (`boards.py:66-87,104-117,439-445`) ungetestet und auf den Pis unbenutzt |
| `vision_server/handeye_session.py`, `tagloc/handeye.py` | Hand-Auge per OPC UA (Sitzung, Verfeinerung, Ankern) und Rechenkern | `runner.py`; Frontend | **aktiv, Produktionspfad** | 4 Dateien | 93 % / 97 % | behalten; Duplikat der Pipeline im CLI auflösen (D4); `finish()` hat 192 Zeilen |

**Urteil:**
- **Wirklich doppelt und überholt:** der Prototyp `src/apriltag/` (T1). Er ist durch `tagloc` ersetzt und kann weg.
- **Kein Doppel, sondern Ergänzung:** `tagloc.cli.calibrate` (Werkzeug, exklusive Kamera, auch Bildordner) und `CalibrationSession` (im Server, Frontend-gesteuert) rechnen mit demselben Kern (`boards.calibrate_from_samples`, `lens.calibrate`). Doppelt sind nur Beiwerk: Board-Standardwerte (D10), Mindestanzahlen, `BoardSpec`-Bau (D1).
- **`src/jobs/calibrate.py` ist keine Kalibrierung**, sondern ein Prüf-Job im Rezept `calibration`: behalten, Hostnamen-Fehler beheben (D11), Umbenennen erwägen.
- **Hand-Auge:** zwei Rechenwege für dieselbe Pipeline (D4) und ein älterer Eingabeweg (`--samples`) ohne Schreiber im Repo.
- **Tatsächlich zu löschen:** nur `src/apriltag/` (3 Dateien). Alles andere ist aktiv oder Werkzeug, mit den genannten Korrekturen.

---

## 4. Testlücken (L)

Gemessen: Gesamtlauf 877 Tests, Anweisungsabdeckung je Modul (`covreport.json`). „Kein Test importiert es“ stammt aus `imports.py`; die Abdeckung kann trotzdem > 0 sein (indirekt über `runner`).

| ID | Lücke | Beleg | Produktiv? | Empfehlung | Aufw. | heiß? |
|---|---|---|---|---|---|---|
| L1 | **`vision_program.py` (Part 10: `VisionProgram.start/halt/reset/_on_job_finished/_finish/_read_parameter/_write_results`)**: 50 % Abdeckung, kein Test importiert es; `test_part10_fassade.py` prüft nur den Adressraum | `covreport`: genannte Methoden 0 Treffer | **ja**, Einstieg der generischen Bedienoberfläche | OPC-UA-Test mit `Start` über `VisionProgram` (refactoring hat `test_ua_methoden.py`, 564 Z., als Vorlage) | M | nein |
| L2 | **`tagloc/cli/*`** (5 CLIs, `_common` 27 %) komplett ohne Test | 0 % | nein (Werkzeug), aber Startbefehle in 8+ Dokus; `calibrate_handeye --session` enthält die Verfeinerungs-Pipeline (D4) | Rauchtests mit synthetischen Szenen (`tools/make_synthetic_scene`) | M | nein |
| L3 | `src/jobs/calibrate.py` (0 %) | enthält den Hostnamen-Fehler (D11) | **ja** (OPC-UA-Rezept) | Test mit Tempordner | S | nein |
| L4 | **Hardware-Öffnen in `camera.py`**: `_open_picamera2` (15 Anw.), `_open_realsense` (12), `_open_opencv`, `_log_metadata`, `factory_calibration` (RealSense-Intrinsik des Hand-Pi), `_list_realsense_color_profiles`; Modul 73 % | `uncovered_funcs.py` | **ja** | Attrappen für `picamera2`/`pyrealsense2` (Importe sind lazy) | M | nein |
| L5 | **Start-Verdrahtung**: `server.main()` (21 Anw.), `application_uri`, `runner.run()`, `_start_mjpeg_server`, `_write_calibration_info`; `vision_server/__main__.py` (0 %) | 0 Treffer | **ja** | Rauchtest `main` mit abgeschalteter Kamera/LDS | M | ja |
| L6 | **`ua_program/program.py`** 62 %: `_start/_suspend/_resume/_halt/_reset_method`, `_transition`, `running_to_*` ohne Treffer; kein Test importiert es | `uncovered_funcs.py` | ja (Basis von `VisionProgram`) | zusammen mit L1 | M | nein |
| L7 | `tagloc/frames.py` 42 %: `ImageFolderSource`, `VideoCaptureSource`, `PiCameraSource`, `RealSenseSource`, `SingleImageSource` | 0 Treffer in `__init__/read` | nein (CLI) | Bildordner-Test mit Tempbildern; Rest Attrappen | S | nein |
| L8 | **ChArUco-Pfad** (`boards.build_board`, `detect_board` Charuco, `calibrate_from_samples` Charuco), `compute_coverage`, Teile von `diagnose_board`; `PupilAprilTagDetector` | `boards.py:68-85,104-117,284-295,439-445`, `detector.py:260-277` | nein (Pis: Schachbrett) | zusammen mit T12/T13 entscheiden (testen oder streichen) | S | `boards.py` ja |
| L9 | `discovery/mdns.py` 38 % (`detect_lan_ipv4`, `announce`-Kern), `detection/script_runner.py` 77 % (Fehlerpfade) | `uncovered_lines.py` | ja (Anmeldung im Netz) | Attrappe für `zeroconf`/Socket | S | nein |
| L10 | `state_machine.py` 68 % (`enter_operational`, `recover`), `runner.py` 64 % insgesamt (Start-/Stop-Glue), `CellWatch._loop`, `stream_overlay._status_for_current_preview` | `uncovered_funcs.py` | ja | gezielt über L5 | M | ja |
| L11 | `tagloc/overlay.py`: `Window`, `has_display`, `summarise` (CLI-Anzeige) | 0 Treffer | nein | niedrig priorisieren | S | ja |
| L12 | alle `vision_server/tools/*` (7), `tools/make_tag_sheet.py`, `tools/measure_nodeset_import.py`, `src/OPCUA/server.py` | 0 % | nein | höchstens Import-Rauchtest; für `src/OPCUA/server.py` ein Test „importiert `vision_server.server.main`“ (sichert den Unit-Einstieg) | S | nein |

Gut abgedeckt (> 93 %): `tagloc/{calibration,geometry,handeye,localize,tagmap,reference_edges,reference_overlay,lens,observations,identity,modes}`, `vision_server/{calibration_session,handeye_session,cellwatch,tagreference*,layer2/*,measuretag,measureboard,payload,profiles,config,events,errors,result_management,discovery/lds}`.

---

## 5. Richtlinien (U)

| ID | Befund | Beleg | Empfehlung | Aufw. | heiß? |
|---|---|---|---|---|---|
| U1 | **Funktionen > 80 Zeilen: 26 Stück**: `runner.install_vision_machine` 625 (:458), `reference_edges._measure_one_impl` 224 (:545), `address_space.attach_vision_system` 203 (:185), `handeye_session.finish` 192 (:416), `runner._install_tag_reference` 178 (:1370), `reference_edges.plan_reference_view` 147 (:1047), `make_synthetic_scene.render_reference_scene` 138 (:450), `apriltag.acquire_and_detect` 131 (:1227), `calibrate_handeye.solve_session` 124 (:143), `apriltag.open` 122 (:326), `handeye.refine_hand_eye` 119 (:457), `cli/detect.main` 119 (:74), `runner._install_cell_watch` 113 (:1607), `vision_program.install_vision_program` 112 (:200), `apriltag.measure_tag_reference` 109 (:549), `measureboard.build_result` 104 (:96), `tagreference.parse_request` 102 (:90), `asset_model.attach_asset_model` 100 (:291), `pose.solve_camera_from_world_tags` 94 (:272), `pi_load_profiler.run` 89 (:350), `localize.localize_camera` 87 (:152), `mdns.announce` 85 (:89), `stream_overlay.annotate` 84 (:266), `reference_edges.measure_reference_lines` 81 (:771), `fit_in_plane_correction` 81 (:920) | `uchecks.py`; refactoring zerlegt `apriltag.open/acquire_and_detect` (0dceb9c), `solve_hand_eye` (b115823), `calibrate.main`, `detect.main` (185b278) | Abschnitt 7; nicht heiße Dateien zuerst | M–L | etwa die Hälfte ja |
| U2 | **Englische statt deutscher Docstrings/Kommentare** (CLAUDE.md: Deutsch). Stoppwortzählung: EN 3 897 gegen DE 11 558, also etwa ein Viertel Englisch. **Überwiegend englisch (18 Dateien):** `tagloc/localize.py` (EN 474/DE 135), `tools/make_synthetic_scene.py` (332/24), `tagloc/tagmap.py` (330/80), `tests/test_tag_pipeline.py` (159/29), `tagloc/geometry.py` (121/6), `tests/test_welttags.py` (113/75), `tagloc/frames.py` (72/23), `tagloc/observations.py` (64/9), `tests/test_layering.py`, `tagloc/cli/_common.py`, `tests/test_tag_map.py`, `tests/test_tag_geometry.py`, `tools/measure_nodeset_import.py`, `src/jobs/calibrate.py` (31/0), `tagloc/cli/detect.py`, `tagloc/modes.py`, `tools/make_tag_sheet.py`, `tagloc/identity.py`. **Gemischt:** `detection/apriltag.py` (227/523), `tagloc/overlay.py` (138/126), `asset_model.py`, `detector.py`, `pose.py`, `calibration.py`, `tests/test_apriltag_source.py`, `tests/test_camera_owner.py` | `lang.py` (Heuristik) | Übersetzen, Datei für Datei, **zuletzt** und nur in gerade nicht heißen Dateien; reines Kommentarwerk | L | `apriltag.py`, `overlay.py` ja |
| U3 | **`print` in Bibliothekscode: keiner.** Alle `print` stehen in CLIs, Werkzeugen und `src/jobs/calibrate.py` | `uchecks.py` | nichts zu tun | – | – |
| U4 | **Nackte `except:`: keine.** `except Exception` ohne Log und ohne `raise` (10): `jobs/calibrate.py:49` (Fehler wird gemeldet, ok), `pose.py:256` (bewusst), `ua_program/program.py:210`, `asset_model.py:124`, `camera.py:137` (diagnostisch, dokumentiert), `handeye_session.py:638,697` (`_intrinsics_source`, `_current`: Fehler stumm), `tools/measure_framerate.py:89`, `vision_program.py:110`, `tools/measure_nodeset_import.py:70` | `uchecks.py` | nur `handeye_session.py:638,697`, `asset_model.py:124`, `program.py:210` mit `_log.debug/warning` versehen; Rest ist begründet | S | nein |
| U5 | **Mehrfach vorhandene magische Konstanten** | Tabelle D10 | zentral definieren (mit D10) | M | teils |
| U6 | **`__all__` passt nicht (4 Module):** öffentlich, aber nicht aufgeführt: `detector.apply_detector_parameters`, `frames.RealSenseSource`, `overlay.format_calibration_stamp`/`draw_calibration_caption`, `reference_edges.tag_outline_px`. Kein Eintrag verweist auf Fehlendes. Tote Einträge, die mit T3/T4/T6 verschwinden: `frames.SharedCameraSource/iter_frames/FrameSource`, `detection.build_detection_source`, `localize.merge_locations/world_tag_for_robot` | `uchecks.py` | `__all__` ergänzen bzw. mit den Löschungen bereinigen | S | `overlay.py` ja |

---

## 6. Behelfe und Repo-Hygiene (B)

| ID | Befund | Beleg | Empfehlung | Risiko | Aufw. | Schnittst. | ex-ID | heiß? |
|---|---|---|---|---|---|---|---|---|
| B1 | **`data/` ist getrackt, obwohl README „nicht versioniert“ sagt**: 49 Bilddateien (`data/LV1` 40 JPG, 79 MB; `data/rohbilder` 4 PNG, 65 MB) plus 2 `.gitkeep`. `.gitignore` enthält `data/*`, `!data/rohbilder/` (Ausnahme erlaubt das Tracken ausdrücklich) und `*.jpg` (LV1 nur per Force-Add). Von 4 Rohbildern brauchen Tests nur 2 (`test_detector.py:29,34`), 2 vom 30.10. sind unreferenziert (T15) | `git ls-files data`; `du -sh`; `README.md:30` | Entscheiden: README korrigieren **oder** nur die zwei Testbilder behalten und den Rest aus Git nehmen (Repo wächst sonst um ca. 20 MB je Messreihe); LV1 als Fehlalarm-Referenz behalten, README ergänzen | niedrig | S | nein | ex P7, D1 | nein |
| B2 | **`.gitignore` ignoriert `*.txt`, `*.sh`, `*.bat`, `concept/`, `hardware/`**; getrackt sind trotzdem `requirements.txt`, `setup.sh`, `setup.bat`, `concept/*`, `hardware/*`. Neue Dateien dieser Typen bleiben für `git add` unsichtbar | `git ls-files` (`.txt/.sh/.bat`) | Ausnahmen `!requirements.txt`, `!/setup.*`, `!concept/` ergänzen oder Einträge streichen; sonst geht ein künftiges Deploy-Skript verloren | niedrig | S | nein | ex D1 | nein |
| B3 | **`requirements.txt` verweist auf `src/ua_mdns.py`** (existiert nicht; mDNS liegt in `vision_server/discovery/mdns.py`) und auf `doc/altlasten.md`, `doc/apriltag-e2e-test.md` (richtig `doc/projektdoku/`); `pupil-apriltags` nur für T13 | `requirements.txt:8,13` | Kommentare berichtigen | keines | S | nein | neu | nein |
| B4 | **CLAUDE.md nennt die Tests „Pytest-Tests“** (`:100`); gelaufen wird `unittest discover` (README, Altlasten; alle Tests sind `unittest.TestCase`) | `CLAUDE.md:100`; Wortsuche nach pytest in `tests` und `src` leer | „Unittest-Tests“ und den Befehl nennen (CLAUDE.md nur mit Absprache) | keines | S | nein | neu | nein |
| B5 | **README.md listet `src/vision_server` doppelt** (`:23-24`) und nennt `src/jobs`, `src/ua_program`, `src/OPCUA`, `src/apriltag` nicht | `README.md:21-29` | Struktur neu schreiben (nach T1) | keines | S | nein | neu | nein |
| B6 | **`betreuer/` fehlt**, wird aber von `CLAUDE.md` (Tabelle, Regeln), `ua_program/program.py:3`, `discovery/mdns.py:3` und `altlasten.md` D9 vorausgesetzt (Ordner in 69bf237 am 23.09. entfernt, `OPC_UA_Program.py` nie getrackt) | `git ls-files` nach betreuer leer; `git log -- betreuer` | Verweise bereinigen oder Ordner wiederherstellen | keines | S | nein | neu (zu D9) | nein |
| B7 | **`tagloc/cli/calibrate.py:3` nennt die Taste `S = save`**, die es nicht gibt; **`--capture-to` wirkt nur im Fenstermodus** (`:110,147-149`; der Headless-Zweig kehrt vorher mit `continue` zurück), die Doku `apriltag-e2e-test.md:244-251` verspricht aber `--no-gui --capture-to` | Lesen `cli/calibrate.py:113-153` | Docstring berichtigen; `--capture-to` auch im Headless-Pfad bedienen (nur bei Kamera-Quelle sinnvoll) | niedrig | S | CLI | neu | nein |
| B8 | **`VisionServerConfig.configuration_id`** wird nirgends gelesen | siehe T9 | löschen | niedrig | S | nein | neu | ja (`config.py`) |
| B9 | **`src/jobs/calibrate.py` ordnet Hostnamen anders zu als `server.py`** | siehe D11 | beheben | niedrig | S | OPC UA | neu | `server.py` ja |
| B10 | **`diagnose_board.py` `DEFAULT_COMBOS`** ohne das aktuelle 7x10-Brett (Docstring nennt 7x9/22 mm) | `tools/diagnose_board.py:28-33`; Brettwechsel `c615b36` (28.10.) | aktualisieren oder Werkzeug streichen (Abschnitt 3) | niedrig | S | nein | neu | nein |
| B11 | **`calibration_min_samples` wird nur angezeigt** (`minSamples` in `CalibrationProgress`), die echte Schwelle ist die feste Konstante 3 | `calibration_session.py:64,371,400`; `profiles.py:119-120` | Feld als „empfohlene Aufnahmen“ dokumentieren/umbenennen, oder die Schwelle wirklich daran koppeln (Verhalten ändert sich) | niedrig (Knoten `CalibrationProgress`) | S | Payload | neu | nein |
| B12 | Doku nennt veraltete Werte: `vision-server-interface.md:1330` Brett 6x7, `apriltag-e2e-test.md`/`apriltag-referenz.md` ChArUco 7x5; Code: Schachbrett 7x10, 23 mm (seit 28.10.). `frontend-integration-kalibrierung.md` nennt `ns=7`, Altlasten nennen ns=6 | Doku vs. `server.py:127-150` | Doku nachziehen (mit Absprache, CLAUDE.md verbietet stilles Umschreiben von Schnittstellen) | keines | S | nein | neu | nein |
| B13 | `setup.sh` richtet nur ShareLaTeX ein (ex D4); systemd-Unit nicht versioniert (ex D5). Eine Unit-Datei im Repo würde `src/OPCUA/server.py` überflüssig machen (ex D11) | `setup.sh`, `src/OPCUA/server.py` | später, mit Absprache für die Pis | mittel (Pi-Betrieb) | M | Betrieb | ex D4, D5, D11 | nein |

---

## 7. Zerlegungsvorschläge (nur Vorschläge; **erst nach Merge von `usecase-foto` und Abgleich mit `apriltag-refactoring`**)

Alle folgenden Dateien sind heiß. Keine Änderung auf dem Aufräum-Branch, bis klar ist, was `apriltag-refactoring` schon zerlegt hat.

| Datei | Befund | Vorschlag | Schnittstellen-Risiko |
|---|---|---|---|
| `runner.py` (1741 Z.) | `install_vision_machine` 625 Zeilen in einer Funktion (:458-1085); `_install_*` je Feature schon abgesetzt, aber `_install_tag_reference` 178 und `_install_cell_watch` 113 Z.; dreifacher Methoden-Wrapper (D6); `usecase-foto` fügt +183 Zeilen ein | in Schritte teilen (Server-Knoten, Job-Methoden, Kalibrierung, je Zusatzfeature); Wrapper-Hilfe `json_method` in `ua_nodes.py`; Feature-Installer in ein Paket | OPC-UA-Namen und Reihenfolge unverändert; `test_*_opcua`-Tests laufen lassen |
| `detection/apriltag.py` (1357 Z.) | eine Klasse mit Job, Messen von Tags/Brett/Bezugskanten, Zellüberwachung; `acquire_and_detect` 131, `open` 122, `measure_tag_reference` 109 Z. | getrennte Module/Mixins: `measure_tags`, `measure_board`, `reference`; JSON-Hilfen (`_finite_or_none`) auslagern (D2) | Payload-Felder unverändert |
| `server.py` (556 Z.) | rund 200 Zeilen Presets (`PI_APRILTAG_PRESETS`, `PI_CAMERA_STREAM_PRESETS`, Identitäten) neben der Start-Logik | Presets und Identität in `vision_server/pi_presets.py` (stdlib); `server.py` behält `main()`; löst auch D11 | Umgebungsvariablen, `ENDPOINT` unverändert |
| `stream_overlay.py` (643 Z.) | `annotate` 84 Z.; Referenzvorschau (`_tag_reference_view`, `_evaluate_reference`, ...) als eigener Block | Referenzvorschau in eigene Klasse auslagern; `_resize_for_stream` öffentlich (D15) | keine |
| `cellwatch.py` (1303 Z.) | Tracker, Pose-Speicher, Optionen, JSON, Ablauf in einer Datei | Paket `cellwatch/{options,tracker,posestore,session}.py` | OPC UA (`StartCellWatch`, Payload) unverändert |
| `tagloc/overlay.py`, `boards.py` | `overlay.py`: `Window`/`summarise` nur für CLI (L11); `boards.py` mischt Erkennung, Pose, Diagnose | `boards.py`: ChArUco-Teil und `diagnose_board` getrennt, falls ChArUco bleibt (T12/L8) | gering |
| nicht heiß, trotzdem lang | `address_space.attach_vision_system` 203 Z., `reference_edges._measure_one_impl` 224 Z., `handeye_session.finish` 192 Z. | je Schritt aufteilen (`finish`: Prüfen, Rechnen, Speichern, Ergebnis bauen) | gering |

---

## 8. Ideen aus `apriltag-refactoring` (40 Commits, nicht integriert)

Aufruf `git log --oneline apriltag..apriltag-refactoring`; je Thema geprüft, ob das Problem auf `apriltag` noch besteht (`refbranch.py`, Wortsuche auf beiden Ständen):

| Commit | Thema | Problem besteht auf apriltag? | Bezug |
|---|---|---|---|
| 3874bda, fdc170f, 5bce4f4 | `tagloc/jsonio.py`: JSON lesen/schreiben, `finite_or_none` | ja | D2, D3 |
| 743e473 | `board_spec_from_config` | ja | D1 |
| 0fd39f0, 9ea0b9a, 7d795d3 | `cancel_and_wait`, `SerialExecutor` | ja | D14 |
| 96a7224 | `DEFAULT_TAG_FAMILY` | ja | D10 |
| f856b9b | `jobs/calibrate.py` bekommt Rahmen/Pfade vom Server | ja | D11, B9 |
| 5d1714b | JobRunner-Zulassung bündeln | ja | D7 |
| 7e67251 | tote Teile aus `frames.py` | ja | T3 |
| e63904f | toten Code im Vision-Server entfernt (u. a. `build_detection_source`) | ja | T4, T10 |
| 29c0e6e, d2a0924, e147473 | Board-/Marker-Erzeugung nur in `boards/detector`; Renderer nach `tagloc/synthetic.py` | ja | D13 (Frage: gehört das Test-Orakel in die Bibliothek?) |
| 185b278, 0dceb9c, b115823 | `calibrate.main`, `detect.main`, `open`, `acquire_and_detect`, `solve_hand_eye` zerlegt | ja | U1 |
| 1c938f6, 9a7942f | gemeinsame Test-Helfer `tests/_support.py` | ja | D12 |
| 2ea0e2b | `ua_program` tabellengesteuert | ja | L6 |
| dfcfa4d | OPC-UA-Methoden charakterisiert (`test_ua_methoden.py`) | ja | L1 |
| 7e10777, 6b740f1 | CLI bricht mit Hinweis ab; Lade-Helfer in `_common` | ja | L2 |
| 9f56bb2 | Quaternion-Prüfungen **bewusst nicht** vereinheitlicht | – | D5 |
| 28c0365 | additive Knoten über `ua_nodes.add_named_variable` | ja | D6 |
| 4aca679, efb6b5f, 4e27abf, 742a386, 39df60d, 3c77acf | kleinere Bündelungen in `geometry`, `localize`, `calibration`, `tagmap` | nicht einzeln geprüft | – |

**Konsequenz:** Der größte Teil der Doppelungsfunde (D1–D3, D7, D11–D14) ist dort schon gelöst. **Vorgehen (Koordinator, 30.10.):** `apriltag-refactoring` wird nicht als Ganzes integriert (Integration nur auf Ansage). Auf `apriltag-aufraeumen` werden einzelne Commits per Cherry-pick übernommen, wenn sie ohne Konflikt passen und keine heißen oder reservierten Dateien berühren; sonst bleibt der Fund offen. Unabhängig davon angehen: T1, T5, T6/T7-Entscheidung, T8, T9, T10 (Rest), T11, T13–T15, D4, D6 (Rest), D8–D10, D15, sowie Abschnitte 4–6.

---

## 9. Priorisierte Umsetzungsliste (Top 15)

| # | Maßnahme | Nutzen | Risiko | Aufw. | Hinweis |
|---|---|---|---|---|---|
| 1 | `src/apriltag/` löschen, Verweise mitziehen (T1) | 400 Zeilen, drei Docstring-/Doku-Altlasten, klare Antwort auf „welches Kalibrierskript“ | niedrig | S | Löschbedingung der Doku ist erfüllt; unabhängig von refactoring |
| 2 | Hostnamen-Drift in `jobs/calibrate.py` beheben (D11/B9) | echter Fehlerpfad im OPC-UA-Rezept `calibration` | niedrig | S | auf refactoring (f856b9b) schon gelöst, dann entfällt |
| 3 | Kommentar- und Pfadfehler berichtigen (T11, B3–B7, B10) | irreführende Hinweise (`S = save`, `betreuer/`, `ua_mdns.py`, alte Doku-Pfade) | keines | S | reiner Text |
| 4 | `.gitignore` und `data/`-Tracking klären (B1, B2, T15) | Repo-Größe (144 MB Bilder), unsichtbare neue Dateien | niedrig | S | Absprache nötig |
| 5 | Tote Konfigurationsfelder und unbenutzte Imports (T9, T10) | weniger Verwirrung (`configuration_id`, `use_picamera`) | niedrig | S | `config.py` heiß: nach Merge von usecase-foto |
| 6 | Tote Funktionen löschen (T2–T5) | ca. 60 Zeilen | niedrig | S | teils auf refactoring erledigt |
| 7 | Entscheidung zu `world_tag_for_robot`/`merge_locations`/`estimate_tag_pose` (T6, T7) | ehrliche API, Doku-Konsistenz | mittel | M | fachliche Rückfrage |
| 8 | Test für Part-10-Einstieg `VisionProgram` und `ua_program` (L1, L6) | größte Lücke im Produktionspfad | niedrig | M | refactoring-`test_ua_methoden.py` als Vorlage |
| 9 | Hand-Auge-Pipeline nur einmal implementieren (D4) mit CLI-Test (L2) | ein Rechenweg weniger | mittel | M | Dateiformat `session.json` bleibt |
| 10 | Test für `jobs/calibrate.py` und Startverdrahtung `server.main` (L3, L5) | sichert die Einstiege (auch `src/OPCUA/server.py`) | niedrig | M | |
| 11 | JSON-Hilfsmodul (D2, D3, D16) | ca. 80 duplizierte Zeilen, ein NaN-Wächter | niedrig bis mittel | M | auf refactoring gelöst, dort übernehmen |
| 12 | Brett-/Kalibrier-Standardwerte und Konstanten zentralisieren (D10, B11) | Drift Pi-Preset gegen CLI/Doku (7x10 gegen 9x6) | niedrig | M | CLI-Defaults ändern ist eine CLI-Schnittstelle |
| 13 | `pupil`-Backend und OpenCV-<4.7-Zweige entscheiden (T12, T13, L8) | weniger ungetestet Code, eine Abhängigkeit weniger | mittel | M | Pi-OpenCV-Version erst feststellen |
| 14 | `tests/_support.py` (D12); `diagnose_board`/`list_realsense_profiles`/`pi_load_profiler` entscheiden (T8) | weniger kopierte Test-Helfer, weniger Werkzeugballast | niedrig | M | |
| 15 | Zerlegung `runner.py`, `detection/apriltag.py`, `server.py`-Presets (Abschnitt 7) und Übersetzen englischer Kommentare (U2) | Lesbarkeit, Richtlinie | mittel bis hoch (Konflikte) | L | **erst nach Merge von usecase-foto und refactoring** |

---

## 10. Nicht untersucht / offen

- Laufzeitverhalten auf den Pis (OpenCV-Version, Hostnamen, `VISION_FRAME_ID` in der systemd-Unit): Auswirkung von D11 und T12 bleibt **unsicher**.
- WSC-Repo und Nutzung der OPC-UA-Methoden durch das Frontend: ob `StartCalibration` & Co. produktiv verwendet werden, ist hier nur aus der Doku belegt.
- Zweig- und Unterprozess-Abdeckung (`ScriptDetectionSource`) nicht gemessen; Abdeckung ist Anweisungsabdeckung im Hauptprozess.
- Kleinere refactoring-Commits (Bündelungen in `geometry/localize/tagmap`) nicht einzeln geprüft.
- Die Sprachverteilung (U2) ist eine Heuristik; einzelne Docstrings können falsch eingestuft sein.
- Doku-Konsistenz nur an den Kalibrierstellen geprüft, nicht für alle 30 Arbeitspläne.
- Nodeset-XML, `doc/*.tex`, `.claude/hooks` nicht untersucht.
- Im Repo wurde nichts geändert; die Coverage lief in einer Kopie unter `.../agent-adp-tot/repo-copy`.

## 11. Nachtrag Koordinator

| ID | Befund | Beleg | Empfehlung | Risiko | Aufw. |
|---|---|---|---|---|---|
| B14 | **Repo-Hook blockiert Write/Edit, wenn Claude Code nicht in der Repo-Wurzel gestartet wird.** `.claude/settings.json` ruft `python3 "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/doku_regeln.py"`; startet man eine Sitzung eine Ebene höher (z. B. `/home/meric/ADP`), fehlt die Datei dort, der Hook endet mit Fehler und jeder Schreibzugriff wird abgelehnt — obwohl der Hook laut `CLAUDE.md` nur erinnern soll | Sitzung vom 30.10.2026; `.claude/settings.json` | Aufruf so ändern, dass er bei fehlender Datei still endet (Rückfall: Repo-Wurzel über das Skriptverzeichnis bestimmen) | keines | S |
| B15 | **Welttags: zwei Pis, zwei Tag-Maps, nichts erzwingt Gleichstand**; Frontend und Backend halten eigene, inzwischen veraltete Kopien | siehe [`altlasten-wsc.md`](altlasten-wsc.md), Abschnitt 10 | eine Pflegestelle über `SetTagMap`/`TagMapJson`; als eigenes Feature planen | mittel | M |

## 12. Umsetzungsstand

Branch `apriltag-aufraeumen`. Wird beim Aufräumen fortgeschrieben.

| IDs | Stand | Commit |
| --- | --- | --- |
| T1 | umgesetzt | 863bd89 |
| T11, B3, B6 | umgesetzt, bis auf `server.py:116` (heiße Datei) und `CLAUDE.md` (`betreuer/`-Verweise, bewusst unverändert) | 2b24719 |
| B2 | umgesetzt (`!requirements.txt`, `!/setup.sh`, `!/setup.bat`); `concept/` und `hardware/` bleiben ignoriert | b08502a |
| B5 | umgesetzt | 53ab6ce |
| B7 | umgesetzt (Docstring, `--capture-to` headless bei Kamera-Quelle, Test) | e200d89 |
| B10 | umgesetzt | c60e37d |
| B14 | umgesetzt | 119d839 |
| T3 | umgesetzt per Cherry-pick von 7e67251 | aaa4c4e |
| T2, T4, T5 | umgesetzt | 944b485 |
| T9 | teilweise: Felder in `profiles.py` entfernt; `VisionServerConfig.configuration_id` (`config.py`, heiß) bleibt | b0cff8a |
| T10 | teilweise: `asset_model`, `calibration_session`, `nodeset_ids`, Tests; offen `tagloc/overlay.py` (heiß), `handeye_session.py` (reserviert) | ec72d8d |
| T14 | teilweise: nur `cancel_running`; `show_distance` (`overlay.py`, heiß) bleibt | e421cc6 |
| D11, B9, L3 | umgesetzt, selbst statt Cherry-pick (`f856b9b` ergab Konflikte in `jobs/calibrate.py` und `detection/__init__.py`): `jobs/calibrate.py` nimmt `VISION_FRAME_ID`, sonst die Tabelle `FRAME_BY_HOST` mit den echten Pi-Hostnamen. Die Tabelle spiegelt `server.PI_IDENTITIES`; `vision_server.server` importieren geht nicht, es lädt asyncua. Ein Test vergleicht beide Tabellen. Hinweistext nennt das Schachbrett 7x10 statt `--board charuco`. Neu: `tests/test_jobs_calibrate.py`. Rahmen und Pfade vom Server übergeben (wie in `f856b9b`) bleibt offen, weil dafür `detection/__init__.py` und `script_runner.py` geändert würden | 8f4ef13 |
| D12 | umgesetzt, nachgebaut statt Cherry-pick (`1c938f6` und `9a7942f` ergaben Konflikte): `tests/_support.py` mit `pose()`, `RecordingNode` (ersetzt `FakeNode` in `test_camera_health`, `test_camera_stream`, `test_mjpeg_server`), `run_briefly`, `AddressSpaceCache` (statt `build()` in `test_asset_model`, `test_part10_fassade`). `test_cellwatch.FakeNode` (Null-Attrappe) und die `FakeCamera`-Varianten bleiben getrennt | 4e3af2c |
| D9, D13 | umgesetzt: `camera.realsense_color_profiles` ist die eine Profilschleife (Fehlermeldung und `tools/list_realsense_profiles.py`), mit Test. `tools/make_tag_sheet.py` nutzt `tagloc.detector.predefined_dictionary` und `tagloc.boards.build_board`; SVG vorher und nachher byteweise gleich. `make_synthetic_scene.chessboard_object_points` bleibt als Orakel | 0c56a6d |
| D16 | teilweise: `raw_image.FILE_STAMP_FORMAT` für `raw_image` und `tagreference_log`; die UTC-Stempel in `handeye.py` und `handeye_session.py` bleiben (reserviert) | bde30f3 |
| D10 | teilweise: `tagloc.tagmap.DEFAULT_TAG_FAMILY` in `tagmap.py`, `cli/_common.py`, `tools/make_synthetic_scene.py`; `profiles.py` behält das Literal (muss ohne numpy importierbar bleiben), ein Test vergleicht beide Werte. Offen: `detector.py`, `cli/calibrate_handeye.py` (reserviert) sowie Tag-Größe, Brett-Standard, Mindestaufnahmen, Auflösungen, Rahmen-Namen | 7a97c49 |
| D15 | nicht umgesetzt: `_scale_for` liegt in `tagloc/overlay.py`, `_resize_for_stream` wird in `stream_overlay.py` importiert (beide heiß) | – |
| L1, L6 | umgesetzt: `tests/test_vision_program.py` (17 Tests) mit Attrappen-Quelle statt `hello_world` (gibt es nicht mehr) und einem `DemoProgram` für Suspend, Resume, abgelehnte Funktion, Lock und `running_to_*`; Vorlage `test_ua_methoden.py` aus `dfcfa4d`, ohne die 40100-Seite und die Kalibriermethoden | 3fb10f8 |
| L7 | umgesetzt: Tempbild-Tests für `ImageFolderSource`, `SingleImageSource`, `open_source`, `to_gray`, `image_size`; Kamera-Quellen bleiben ohne Test (Hardware) | 27daa0f |
| L12 | umgesetzt: `tests/test_tool_imports.py` (Import und `main` aller `vision_server/tools/*`, SVG-Test für `make_tag_sheet`, Nodeset-Pfade von `measure_nodeset_import`, `src/OPCUA/server.py` lädt `vision_server.server.main`) | 562db88 |
| U4 | teilweise: `vision_server/asset_model.py` (`_ensure_folder`, Zeile 123 statt 124 im Fund) mit `_log.debug` und Grund, `ua_program/program.py` (liegt unter `src/ua_program/`, nicht unter `vision_server/`; Zeile 208) mit `logger.warning(..., exc_info=True)`, Verhalten unverändert. Offen: `handeye_session.py:638,697` (reserviert). Bewusst unverändert, weil begründet: `jobs/calibrate.py` (Fehler wird gemeldet), `pose.py:256` (reserviert, bewusst), `camera.py` (diagnostisch, dokumentiert), `tools/measure_framerate.py`, `vision_program.py:110`, `tools/measure_nodeset_import.py` | d8c5316 |
| U6 | teilweise: `reference_edges.tag_outline_px` in `__all__` aufgenommen; `frames.RealSenseSource` stand schon darin, `detection.DETECTION_SOURCES` ist vorhanden (der Treffer im Fund war eine Annotation, kein toter Eintrag). Offen: `detector.apply_detector_parameters` (reserviert) und `overlay.format_calibration_stamp`/`draw_calibration_caption` (heiß). Die toten Einträge `frames.SharedCameraSource/iter_frames`, `build_detection_source`, `localize.merge_locations/world_tag_for_robot` verschwinden erst mit T6/T7 (Entscheidung nötig); `localize.__all__` führt `merge_locations` und `world_tag_for_robot` weiter, denn beide Funktionen existieren noch | 087a207 |
| U2 | teilweise: Docstrings und Kommentare ins Deutsche übersetzt, nur Text (AST ohne Docstrings vor und nach jeder Datei gleich, argparse-`help` und Ausgaben unberührt, Umlaute wie im Modul üblich als ae/oe/ue). `tagloc/localize.py` 4ede203, `tagloc/tagmap.py` 6bc8ac7, `tagloc/geometry.py` 6e26c15, `tagloc/frames.py` ecf9112, `tagloc/observations.py` 7b2bdc3, `tagloc/cli/_common.py` e437ab3, `tagloc/cli/detect.py` 274a456, `tagloc/modes.py` 9ef4538, `tagloc/identity.py` f4420fe, `jobs/calibrate.py` 58e0d22, `tools/make_tag_sheet.py` abe8608, `tools/measure_nodeset_import.py` dcff2bb, `tools/make_synthetic_scene.py` 16ffbfb, Tests (`test_tag_pipeline`, `test_welttags`, `test_layering`, `test_tag_map`, `test_tag_geometry`) 1c5a6a5. Offen: gemischte und gesperrte Dateien (`detection/apriltag.py`, `overlay.py`, `detector.py`, `pose.py`, `calibration.py`, `asset_model.py`, `tests/test_apriltag_source.py`, `tests/test_camera_owner.py`) | je Datei, siehe Stand |
| D2 | teilweise: `tagloc/jsonio.py` mit `finite_or_none(value, digits=None)` (stdlib, Rundung je Aufrufer), `detection/apriltag` nutzt es (Payload 4 Stellen, `PAYLOAD_DIGITS`); neu `tests/test_jsonio.py`, `jsonio` in `test_layering`. Offen: `measuretag._finite`, `measureboard._finite` (byte-gleich, nicht gesperrt, nur noch nicht umgestellt), `handeye_session._finite_or_none` und `tagloc/handeye._finite` (6 Stellen; Hand-Auge-Dateien) | 5ca249c |
| Zerlegung `detection/apriltag.py` (Abschnitt 7), U1 für `acquire_and_detect`, `open`, `measure_tag_reference` | umgesetzt: Paket `detection/apriltag/` mit `__init__.py` (Klasse, Zustand), `resources.py`, `capture.py`, `job.py`, `tags.py`, `board.py`, `reference.py`, `cell.py`, `snapshot.py`; Mixins, eine Klasse, Importpfad, Logger-Name, Payload-Felder und Reihenfolge unverändert. Längste Methode jetzt 62 Zeilen (vorher 131/122/109). Geprüft mit einem Referenzlauf über Job, MeasureTags, MeasureBoard, Bezugskanten, `observe_cell`, `open`-Pfade und Log-Meldungen an synthetischen Szenen: vorher und nachher byte-gleich. Verzeichnis-Übersicht in `code-handbuch.md`, Teil A2 | 22bf9ea (Paket), 6e187f7 (Module) |
| U2 | teilweise, Nachtrag: `detection/apriltag/*` (Docstrings und Kommentare deutsch; Log- und Payload-Texte unverändert) und `tests/test_apriltag_source.py`. Offen: `overlay.py`, `detector.py`, `pose.py`, `calibration.py`, `asset_model.py`, `tests/test_camera_owner.py` | 6e187f7, f6326ae (Test) |

Testlauf nach diesem Block: 922 Tests, OK (Ausgangsstand 882); nach U2, U4, U6 unverändert 922, OK. Nach D2 und der Zerlegung von `detection/apriltag` 972 Tests, OK (neu: `test_jsonio`).
