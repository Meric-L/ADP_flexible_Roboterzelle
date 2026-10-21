# Altlasten — toter Code, ungetesteter Code, AprilTag-Doppelungen

Stand: 2026-10-21. Ergänzung zu [`altlasten.md`](altlasten.md) (Demo-Aufbauten,
Platzhalter, Deployment-Behelfe). Hier geht es um **Code**: was nirgends mehr
aufgerufen wird, was kein Test anfasst und wo es beim AprilTag-Thema zwei Wege für
dasselbe gibt.

Untersucht wurden beide Repos:

- **Zelle** = `ADP_flexible_Roboterzelle` (dieses Repo)
- **WSC** = `webskillcomposition` (Uni-Repo, Branch `tag-einmessen`)

Methode: statische Suche (AST-Skript und `git grep -w`) über Quellen, Tests, Tools und
Doku; `vulture` stand nicht zur Verfügung. Nichts wurde gelöscht oder geändert.
„Sicher" heißt: die Suche fand außer der Definition (und `__all__`) keinen Treffer und
kein dynamischer Zugriff ist erkennbar. Vor jedem Löschen die genannte Suche erneut
laufen lassen — andere Sitzungen schreiben parallel.

**Testlauf:**

- Zelle, `.venv`: `Ran 763 tests … OK` (121 s). Ohne `.venv` (System-Python ohne
  cv2/asyncua) schlagen 30 Tests an der Umgebung fehl — kein Codefehler.
- WSC Backend: 65 gesammelt, 60 bestanden, 1 übersprungen
  (`tests/test_websocket_router.py:85`, braucht `WSC2_DEMO_OPCUA_URL`).
- WSC Frontend: `npx vitest run` 57 Dateien, 560 Tests, alle grün.

**Korrektur an `altlasten.md`:** D7 (`test_asyncua_discovery.py` bricht beim Collect ab)
und D8 (roter Test in `store.test.ts`) gelten nicht mehr — beide laufen grün. Beim
nächsten Pflegen dort streichen; hier wurde das bestehende Dokument bewusst nicht
angefasst.

---

## 1. Toter Code

### 1.1 Zelle

| # | Was | Wo | Beleg | Konfidenz | Risiko beim Löschen |
|---|---|---|---|---|---|
| T1 | **Prototyp `src/apriltag/`** (`calibrate_camera.py`, `detect_apriltags.py`, `MANUAL_TEST.md`) | `src/apriltag/` | Kein Import, kein Test. Abgelöst durch `tagloc.cli.calibrate` / `tagloc.cli.detect` (steht in `MANUAL_TEST.md` selbst). Greift auf `src/apriltag/calibration.yaml` zu, die nicht getrackt ist. Wendet keine Verzerrung an | sicher | niedrig. Verweise in `apriltag-lokalisierung.md:460`, `apriltag-referenz.md:435`, `altlasten.md:116-118` und die Herkunfts-Docstrings in `tagloc/pose.py:3`, `boards.py:3,160`, `calibration.py:8` mitziehen |
| T2 | `SharedCameraSource` und `iter_frames` | `src/tagloc/frames.py:174`, `:222` | Nur Definition und `__all__` (`:257`, `:261`) | sicher | niedrig; erst in `apriltag-referenz.md` gegenlesen, ob als CLI-Baustein beschrieben |
| T3 | `ImageFolderSource.current_path` | `src/tagloc/frames.py:52` | Nur Definition | sicher | minimal |
| T4 | `poses_to_json` | `src/tagloc/cli/_common.py:229` | Nur Definition; Docstring nennt `cli/transform.py`, das es nicht benutzt | sicher | minimal |
| T5 | `build_detection_source` (Einzahl) | `src/vision_server/detection/__init__.py:51` | Nur Definition und `__all__` (`:70`); benutzt wird `build_detection_sources` | sicher | niedrig |
| T6 | `Program.get_current_state` | `src/ua_program/program.py:162` | Nur Definition | sicher | niedrig; evtl. aus der Betreuer-Vorlage übernommen, dann dort vergleichen |
| T7 | `world_tag_for_robot` | `src/tagloc/localize.py:513` | In `src/` nur Definition und `__all__` (`:641`); einziger Aufrufer `tests/test_welttags.py:370,381` | sicher (Produktion nie aufgerufen) | mittel — der Test fiele mit weg; evtl. für einen Layer-2-Ersatz gedacht |
| T8 | `list_realsense_profiles.py`, `pi_load_profiler.py` (465 Zeilen) | `src/vision_server/tools/` | Keine Referenz in Code, Tests oder Doku | wahrscheinlich | niedrig bis mittel — könnten lokale Handwerkzeuge sein; erst fragen |
| T9 | Altverweis im Docstring: „OPCUA/server.py" | `src/jobs/calibrate.py:22` | Datei lebt (wird von `ScriptDetectionSource` gestartet), nur der Verweis ist veraltet | sicher | keines (Kosmetik) |

Kein auskommentierter Code, keine `TODO`/`FIXME`/`HACK`-Marker, keine Kompatibilitäts-
Aliasse gefunden. **Nicht untersucht:** nie gesetzte Flags und Parameter.

Bereits in `altlasten.md`: D11 (`src/OPCUA/server.py`), C2 (`IsPartial`), C7 (LDS-URL),
D4 (`setup.sh`/`.bat`).

### 1.2 WSC — Code der Gruppe

| # | Was | Wo | Beleg | Konfidenz | Risiko |
|---|---|---|---|---|---|
| W1 | **`AutolocateLayer2Window`** | `frontend/src/features/cell-modules/components/AutolocateLayer2Window.tsx:80` | Kein Import, auch nicht lazy. Abgelöst durch `AutolocateActions.tsx` (Commit `2b56b4f`, „Layer 2" mit `ApproachPlanPanel compact`) | sicher | gering |
| W2 | **Kette, die nur an W1 hängt:** `Layer2RunPanel.tsx`, `RobotRunConfirm.tsx`, `EndEffectorSpinTest.tsx`, `model/endEffectorSpin.ts` (+ `.test.ts`), `opcua-server/model/useLayer2Run.ts` | `cell-modules/…`, `opcua-server/model/` | Je Name nur vom Vorgänger in der Kette importiert. Der Test `endEffectorSpin.test.ts` hält toten Code grün | sicher | gering; `layer2Gate`, `layer2Run`, `robotDrive`, `controllerRobotDrive` bleiben (von `ApproachPlanPanel`, `TagReferencePanel`, `Viewport` gebraucht) |
| W3 | `sceneState.layer2Target` / `setLayer2Target`, `Layer2TargetMarker` | `viewport/model/sceneState.ts:112-113,158,203-212`, `Viewport.tsx:1775`, `Layer2TargetMarker.tsx` | Gesetzt wird es nur in `Layer2RunPanel.tsx:203,207` (tot). Der Marker rendert deshalb immer nichts | sicher | mittel, hängt an W2 |
| W4 | Layer-2-Bindung in `piServers.ts` (`Layer2Binding`, `StartLayer2Run`, `Layer2Target`) | `opcua-server/config/piServers.ts:115-122,219,284,402-405` | `startNodeId`/`targetNodeId` haben ohne `useLayer2Run` keinen Leser; `statusNodeId` wird noch in `PiActivity.tsx:64` gelesen | wahrscheinlich | mittel — der Pi-Vertrag `wsc.vision.layer2.*` ist dokumentiert |
| W5 | `ModuleCatalogButton.tsx` | `cell-modules/components/` | Nur Definition; der Katalog wird in `ViewportHeaderControls.tsx:11,511` direkt geöffnet | sicher | gering |
| W6 | Ungenutzte Exporte: `shiftBounds` (`config/moduleCatalog.ts`), `DISCONNECTED_DRIVER` (`model/robotDrive.ts`) | siehe Spalte | Nur Definition | sicher | gering |
| W7 | `trackModuleMoves` (`model/moveTracker.ts`), `VISION_RECIPES` (`piServers.ts:26`) | siehe Spalte | Nur vom Test benutzt (`visionResult.test.ts:240-265` für `VISION_RECIPES`) | wahrscheinlich | gering |
| W8 | **Veraltete Kommentare** | `AutolocateActions.tsx:64-65` (behauptet, Layer-1/2-Fenster würden hier gerendert und über `autolocateUi` geöffnet; `autolocateUi` kennt nur noch `timing` und `tagReferenceOpen`), `cell-modules/model/layer2.ts:24` (verweist auf `Layer2RunPanel`) | — | sicher | keines |
| W9 | **Hartcodierte Roboteradressen** `10.10.38.25`–`.28` plus `127.0.0.1` | `opcua-server/config/robotServers.ts` | Ergänzung zu B1 in `altlasten.md`, dort nicht erwähnt | sicher | — |

Keine `TODO`/`FIXME`-Marker, kein auskommentierter Code, keine toten Feature-Flags
(einzige Umgebungsvariable: `VITE_WSC2_WS_URL`). Aus dem Backend ist nichts der Gruppe
ungenutzt.

### 1.3 WSC — Uni-Code (nur Hinweis, nicht von uns)

Geschichte nicht geprüft; vor einer Meldung an die Betreuer nachsehen.

- Backend: `runtime/robot_session.py` `require_method_node_id`;
  `runtime/server_session.py` `replace_robots`; `services/runtime_registry.py`
  `surface_jobs` (wahrscheinlich Property); `opcua/asyncua_discovery.py`
  `discover_methods`, `discover_skills`.
- Frontend: `viewport/model/robotIk.ts` `solvePositionIk`; `task/model/editorGraph.ts`
  `describeTaskEditorNode`, `createVariableNodeData`;
  `robot-control/model/robotJointSync.ts` `applyRobotJointMapping`,
  `createActiveRobotJointSyncSession` (nur im Test); `tag-tool/model/units.ts`
  `snapToStep`.

---

## 2. Ungetestet

### 2.1 Zelle

Dünn belegt sind vor allem Einstiegspunkte und hardwarenahe Quellen; die Kernrechnung
(`pose`, `localize`, `calibration`, `tagmap`, `handeye`, `cellwatch`, `layer2_*`) ist
abgedeckt.

**Module ohne Treffer in `tests/`:**

- `src/jobs/calibrate.py`
- `src/tagloc/cli/{build_tagmap,calibrate,calibrate_handeye,transform}.py` (`detect`
  nur indirekt) — das sind die in der Doku genannten Startbefehle
- `src/vision_server/state_machine.py`, `vision_program.py` (nur indirekt über
  `test_part10_fassade` / `test_layering` möglich)
- `tools/{calibration_client,diagnose_board,handshake_client,stream_viewer}.py`,
  `src/vision_server/tools/{list_realsense_profiles,measure_framerate,pi_load_profiler}.py`

**Nur ein Testbezug (dünn):** `tagloc/cli/_common.py`, `ua_program/program.py`,
`detection/script_runner.py`, `discovery/lds.py`, `events.py`, `mjpeg_server.py`,
`raw_image.py`, `result_management.py`, `tagreference.py`.

**Große öffentliche Symbole ohne direkten Test:**

| Symbol | Wo | Hinweis |
|---|---|---|
| `Program`, `ProgramStateMachine` | `ua_program/program.py:39`, `:307` (265 + 159 Zeilen) | evtl. indirekt über die Fassade, nicht belegt |
| `VisionStateMachines` | `vision_server/state_machine.py:47` | nur über Job-/Runner-Tests |
| `solve_camera_from_world_tags` | `tagloc/pose.py:242` | läuft nur über `localize` |
| `calibrate` | `tagloc/lens.py:162` | |
| `merge_samples` | `tagloc/localize.py:364` | indirekt über `apriltag.py:1046` |
| **`PupilAprilTagDetector`**, `apply_detector_parameters` | `tagloc/detector.py:231`, `:85` | das pupil-Backend und der `auto`-Fallback sind **vollständig ungetestet** |
| `ImageFolderSource`, `PiCameraSource`, `RealSenseSource` | `tagloc/frames.py` | `test_frame_sources.py` hat 35 Zeilen |
| `parse_detector_params`, `load_*_or_exit`, `build_parser`, `add_detection_arguments` | `tagloc/cli/` | CLIs ohne Test |
| `build_config`, `coerce_id`, `reference_warnings`, `asset_config`, `vision_camera_backend`, `mdns_instance_name`, `sanitize_instance_name`, `save_with_backup`, `parse_sample` | `vision_server/` (`__main__.py:15`, `job.py:48`, `tagreference.py:268`, `server.py:326/349/355`, `discovery/mdns.py:52`, `handeye_session.py:140/436`) | meist nur über umgebende Tests |

**Querverweis — „an echter Hardware nicht getestet"** (laut Arbeitsplänen, also nicht
Testlücke, sondern fehlende Messung): `handeye-schachbrett-automatik`,
`handeye-ueber-opcua`, `layer2-annaeherung`, `layer2-measuretags`,
`layer2-schraege-ansicht`, `layer2-lauf-hand-pi` (Fahrbefehl offen),
`zellen-ueberwachung` (`cellwatch.py`), `tag-einmessen-bezugskanten`
(`reference_edges.py`, `tagreference.py`, `measure_tag_reference` in `apriltag.py:526`),
`kalibrierung-linsenmodell-je-kamera` (Neukalibrierung LV1 steht aus),
`livestream-tags-volle-aufloesung` und `stream-overlay-schlank` (nicht am Pi
gemessen). OpenCV 4.x auf dem Pi ist ebenfalls nie ausgeführt (`altlasten.md` D2).

### 2.2 WSC

**Backend** (`tests/` deckt `module_catalog_api`, `method_calls`, `messages`,
`runtime_registry`, `application_service`, `surface_websocket`, `websocket_router`,
`robot_models`, `asyncua_discovery` ab). Ohne eigenen Test: `opcua/server_connection.py`
(624 Zeilen), `opcua/discovery.py`, `runtime/action_execution.py` (332 Zeilen),
`geometry/*` — wahrscheinlich nur indirekt über `application_service`, nicht belegt.

**Frontend:** es gibt **keinen einzigen Komponententest** (nur `*.test.ts`, kein
`*.test.tsx`). Ohne eigenen Test:

- **Alle Hooks in `opcua-server/model/`:** `useVisionJob` (344 Zeilen),
  `useCameraCalibration` (386), `useCellWatch`, `useHandEyeCalibration`,
  `useCameraHealth`, `useCameraFrame`, `useMjpegStreamUrl`,
  `useConnectServerWithFeedback`.
- `opcua-server/config/piServers.ts` (501 Zeilen, nur indirekt über
  `visionResult.test.ts`), `OpcuaServerContext.tsx`, `PiActivityContext.tsx`.
- `cell-modules/model`: `controllerRobotDrive.ts`, `measureTagsCall.ts`,
  `useCellRobot.ts`, `useRobotHold.ts`, `flangeTrajectory.ts`, `format.ts`,
  `autolocateUi.ts`, `planeWorker.ts`; `config/moduleCatalog.ts` (454 Zeilen).
- `viewport/model`: `sceneState.ts`, `workspaceSurfaceGeneration.ts` (776 Zeilen),
  `robotIk*.ts`.

**Nach Funktion:**

| Funktion | Rechnung/Modell getestet | Ungetestet |
|---|---|---|
| Modulkatalog | `catalogStore`, `catalogSync`, `catalogValidation`, `cellModules`, `modelPlanes`, `modelEdges`, `modelFit` | `ModuleCatalogModal` |
| Tag-Platzierung / Tag-Einmessen | `tagPlacement`, `tagGeometry`, `tagReferenceCall`, `tagReferenceRun`, `tagMeasurementState`, `tagServo`, `tagSightings`, `centreOnTag`, `robotTagRun`, `worldTagCheck`, `routeProgress`, `approachPlan` | `TagReferencePanel`, `TagReferenceWindow`, `ApproachPlanPanel`, `measureTagsCall.ts` |
| Hand-Kamera-Kalibrierung | `handEye`, `handEyeAutoPoses` | `useHandEyeCalibration`, `HandEyeCalibrationWindow`, `HandEyeAutoPanel` |
| Zellen-Überwachung | `cellWatch`, `cellClearance` | `useCellWatch`, `CellChangeAlert`, `AutolocateActions` |
| Autolocate | `layer1Import` | `AutolocateLayer1Window`, `AutolocateActions`, `useVisionJob` |

Einschränkung: nicht geprüft, ob einzelne Dateien in fremden Tests mitgeprüft werden
(z. B. `layer2Gate.test.ts`).

---

## 3. AprilTag: Doppelungen und Lücken

### 3.1 Toter Code mit AprilTag-Bezug (Übersicht)

Zusammenzug aus Abschnitt 1 — alles, was zur Erkennung, Pose, Tag-Map oder zur
Tag-Anfahrt gehört und nirgends in der Produktion läuft. Details und Belege dort.

| Repo | Was | Ref. | Konfidenz |
|---|---|---|---|
| Zelle | Prototyp-Erkennung und -Kalibrierung `src/apriltag/` (inkl. `MANUAL_TEST.md`) | T1 | sicher |
| Zelle | `SharedCameraSource`, `iter_frames`, `ImageFolderSource.current_path` (Bildquellen der Tag-CLIs) | T2, T3 | sicher |
| Zelle | `poses_to_json` (Ausgabe der Tag-Posen im CLI) | T4 | sicher |
| Zelle | `build_detection_source` (Einzahl, Erkennungsquelle) | T5 | sicher |
| Zelle | `world_tag_for_robot` (Welttag-Wahl für einen Roboter; nur im Test) | T7 | sicher |
| Zelle | `PupilAprilTagDetector` samt `--backend pupil` und `auto`-Fallback: nie produktiv gesetzt, nie getestet | P1 | wahrscheinlich |
| WSC | `AutolocateLayer2Window` und die Kette `Layer2RunPanel`, `RobotRunConfirm`, `EndEffectorSpinTest`, `endEffectorSpin`, `useLayer2Run` (Tag-Anfahrt, abgelöst durch `AutolocateActions`) | W1, W2 | sicher |
| WSC | `layer2Target`, `setLayer2Target`, `Layer2TargetMarker` (Zielmarker rendert nie) | W3 | sicher |
| WSC | `startNodeId`/`targetNodeId` in `Layer2Binding` (`piServers.ts`) | W4 | wahrscheinlich |
| WSC | `tag-tool/model/units.ts` `snapToStep` (nur Definition); das übrige Tag-Tool ist durch die Katalog-Platzierung doppelt, aber teilweise noch genutzt | P5, 1.3 | sicher / Zukunft offen |

Alles andere in Zelle und WSC mit Tag-Bezug (`tagloc/*`, `tagreference`, `cellwatch`,
`tagPlacement`, `tagGeometry`, Tag-Einmessen) wird benutzt; für die Rechnung gibt es
Tests, nur die Oberfläche und die Hooks sind es nicht (Abschnitt 2).

### 3.2 Doppelungen und Lücken

| # | Befund | Wo | Einschätzung |
|---|---|---|---|
| P1 | **Zwei Detektor-Backends.** `ArucoTagDetector` (Standard) und `PupilAprilTagDetector` (Fallback bei `backend="auto"`, wählbar mit `--backend pupil`) | Zelle: `tagloc/detector.py:231,287,293`, `cli/_common.py:86`, `profiles.py:49` | Produktiv wird nur `"aruco"` gesetzt; das pupil-Backend hat keinen Test. Wenn `src/apriltag/` (T1) fällt, bleibt `pupil-apriltags` allein für diesen ungetesteten Fallback als Abhängigkeit. Risiko mittel: der Fallback deckt OpenCV ohne die Tag-Familie ab (`altlasten.md` D3) |
| P2 | **Zwei Pose-Pfade.** Prototyp `src/apriltag/detect_apriltags.py` (ohne Entzerrung) neben `tagloc/pose.py` (entzerrt) | Zelle | Produktiv nur `pose.py`; Prototyp siehe T1 |
| P3 | **Drei Wege zur Kamerapose.** Welttags (`solve_camera_from_world_tags`), Referenz-Tags (`camera_pose_from_reference_tags`, `localize.py:263`), Anker (`anchor_from_localization`, `localize_camera_from_anchor`, `localize.py:534,559`) mit gemeinsamem `_solve_joint` (`localize.py:241`) | Zelle | Kein toter Code, alles wird benutzt. Nur Komplexitätshinweis, nicht vertieft |
| P4 | `world_tag_for_robot` nur in Tests | `localize.py:513` | siehe T7 |
| P5 | **Zwei Wege, einen Tag zu platzieren.** Eigenständiges `frontend/src/tag-tool/` (`tag-tool.html`, Vite-Eintrag `vite.config.ts:92`, `placement.ts`, `pose.ts`, `exportTagPose.ts`; erzeugt eine JSON-Datei) und im Modulkatalog `cell-modules/model/tagPlacement.ts` + `tagGeometry.ts` (Ecke plus Abstände, Kanten; speichert im Katalog). Doppelte Hilfen: `quaternionFromAxes` (`tag-tool/model/pose.ts`) vs. `quaternionFromBasis` (`tagGeometry.ts`) | WSC | Das Tag-Tool ist nicht ganz tot: `WorldTagMarkers.tsx:5-6` nutzt sein `tag36h11`-Muster und `printedTagSizeM`. In `docs/development.md` und `README.md` kommt es nicht vor. Autor laut Commit `e5c183a` ist jemand anderes — **Zukunft klären, bevor jemand löscht**. Risiko hoch |
| P6 | Tote Teile mit Tag-Bezug: `Layer2TargetMarker`, `setLayer2Target` | WSC | siehe W3 |
| P7 | **`data/LV1/`** (Train 29, Val 11 Dateien `kalib_*.jpg`), getrackt | Zelle | Kein Code liest sie; nur Doku (`apriltag-referenz.md:1041`, `vision-server-interface.md:1418`: tag-freie Schachbrettbilder als Fehlalarm-Referenz). Nicht neu beschaffbar → **behalten**, aber im Test oder der Doku als Quelle verankern |
| P8 | `data/rohbilder/cam_ceiling_20261019-*.png` | Zelle | In Gebrauch (`tests/test_detector.py:29,34`, Zielordner in `config.py:79`) — kein Fund |
| P9 | `hardware/Hardware_Architektur_v2(2).drawio.png` | Zelle | Von keiner Datei referenziert; vermutlich Doku-Asset. Unsicher |
| P10 | Datei `backend/catalog/module_catalog.json` | WSC | Alle Felder haben einen Leser. Offen nur `placement.normal` (2 Einträge, nicht einzeln belegt). Die Datei hat fremde uncommittete Änderungen, nicht bewertet |
| P11 | Backend kennt **keinen** AprilTag-Code | WSC `backend/` | Anbindung läuft über generische Methodenaufrufe (`opcua/method_calls.py`); alles Tag-Wissen liegt im Frontend |

---

## 4. Empfohlene Reihenfolge

1. **Sofort, ohne Risiko:** D7/D8 in `altlasten.md` streichen; veraltete Kommentare
   W8 und T9 berichtigen.
2. **WSC-Layer-2-Kette löschen** (W1–W3, danach W4): die einzige größere Altlast
   der Gruppe. Erst Abschnitt `Layer2Binding` gegen `layer2-annaeherung.md` prüfen,
   weil der Pi-Vertrag dokumentiert bleibt.
3. **Zelle: `src/apriltag/` entfernen (T1)** samt Doku-Verweisen, dann T2–T6.
   `pupil-apriltags` danach bewusst entscheiden (P1): entweder Fallback testen oder
   Backend und Abhängigkeit zusammen streichen.
4. **Klären statt löschen:** Zukunft des Tag-Tools (P5), `pi_load_profiler.py` und
   `list_realsense_profiles.py` (T8), `world_tag_for_robot` (T7).
5. **Testlücken mit dem besten Verhältnis aus Nutzen und Aufwand:** pupil-Backend
   (oder dessen Streichung), `tagloc/cli/*` als Smoke-Tests (laufen sie überhaupt?),
   `useVisionJob` und `useCellWatch` im Frontend, `PiCameraSource`/`RealSenseSource`
   mit Attrappen.
