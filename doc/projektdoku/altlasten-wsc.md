# Altlasten webskillcomposition — Audit unseres Anteils

Stand: 30.10.2026. Untersucht wurde, was unsere Gruppe im Uni-Repo `webskillcomposition` auf Branch
`Ungetestet` (`5812275`) gegenüber `dev` (`d824975`) beigetragen hat — Vergleich `origin/dev..Ungetestet`.
Code, der schon in `dev` stand, wird nur gemeldet (Spalte „dev?“), nicht bewertet und nicht geändert.

Dieses Dokument löst die WSC-Teile von [`altlasten.md`](altlasten.md) (Abschnitt B) und
[`altlasten-toter-code.md`](altlasten-toter-code.md) (IDs W…) ab; deren offene Punkte sind neu geprüft
und tragen hier die alte ID in der Spalte „ex-ID“. Gegenstück für dieses Repo:
[`altlasten-zelle.md`](altlasten-zelle.md). Plan: [`arbeitsplaene/aufraeumen-und-code-handbuch.md`](arbeitsplaene/aufraeumen-und-code-handbuch.md).
Was jede Datei tut, steht im [`code-handbuch.md`](code-handbuch.md), Teil B.

**Aufgeräumt wird auf Branch `ungetestet-aufraeumen`** (webskillcomposition, lokal, ungemergt). Was davon
umgesetzt ist, steht am Ende unter „Umsetzungsstand“.

**Methode:** TypeScript-Compiler-API (Import- und Symbolgraph mit Erreichbarkeit aus `main.tsx` und
`tag-tool/main.tsx`, ohne Tests), `tsc --noEmit --noUnusedLocals --noUnusedParameters`, Python-AST fürs
Backend, Hash-Vergleich von rund 1.800 Funktionsrümpfen, Suche nach identischen Blöcken ab 6 normalisierten
Zeilen, Zuordnung der Zeilen per blame, grep nur über unsere hinzugefügten Zeilen. Rein statisch — „ungenutzt“
heißt: kein Verwender im Code gefunden, kein dynamischer Zugriff erkennbar.

**Ausgangsstand der Tests:** `tsc` fehlerfrei; `vitest run` 79 Dateien / 836 Tests grün; Backend `pytest`
81 bestanden, 1 übersprungen.

**Konfidenz:** sicher = Symbolgraph und grep ohne Verwender; wahrscheinlich = Verwender nur indirekt oder
zeitgebunden; unsicher = Absicht unklar.

## 0. Strukturbefund S1: Revert 839857c färbt den Diff
Commit 839857c (Shaming Zhang, 28.10.) macht den Merge `origin/dev -> UngetestetAR` rückgängig, **ohne dass dev aus der Historie fällt**. Deshalb ist dev formal enthalten, inhaltlich fehlen aber dev-Änderungen. Folgen im Diff:
- 36 Dateien bestehen **nur** aus Revert (rund 4000 + / 4100 - Zeilen): fast ganz `features/task/*` (taskDocument.ts, editorGraph.ts, TaskEditorContext.tsx, TaskProgramPanel.tsx ... = ältere Fassung von dennis, ProgramEdit-Workflow fehlt), gelöschte dev-Dateien (programGraphOpcua.ts, valueTypes.ts, programValues.ts, AppErrorBoundary.tsx, robotIkPose.test.ts, docs/task-editor.md), `package.json` (vite 7.3.6 -> rolldown-vite), README/docs/development.md (Python 3.12/Parcel/`npm install`/TODO-Zeilen statt dev-Stand), Backend (Emergency-Stop, Subtree-Reads fehlen).
- 23 weitere Dateien sind gemischt (applicationController.ts, store.ts, server_connection.py ...).
- Blame der ca. 51.800 geänderten Zeilen: NobbisCode 17.042, Meric-L 15.764, Shaming Zhang 9.416, John Glanz 5.380, **Revert 4.197**, dennis 17.
- In den Tabellen steht bei solchen Funden `Revert` in der Spalte "dev?": nur melden, nicht ändern.
- **Einordnung (Koordinator, 30.10.):** Der Revert ist gewollt. Die Commit-Nachricht von `839857c` sagt „Ungetestet goes on without dev: its changes (ProgramEdit workflow, waypoints relative to the robot, emergency stop, typed node writes, subtree reads, vite 7.3.6) are taken out again; dev and main stay as they are.“ Die Revert-Dateien gehören deshalb weder zu unserem Code noch zum Aufräumen. Beim späteren Zusammenführen mit `dev` sind Konflikte in `features/task`, Backend und Docs zu erwarten; die Doku-Regressionen (README, docs/development.md, docs/features.md) lösen sich dann mit dem dev-Stand.

## 1. Toter Code (T)

Spalten: ID | Was | Wo | Beleg | Konfidenz | Empfehlung | Risiko | Aufwand | dev? | ex-ID. "Beleg" nennt Befehl/Methode und Kurzergebnis (SG = Symbolgraph `reach.cjs`, FR = findReferences `analyze.cjs`).

| ID | Was | Wo | Beleg | Konf. | Empfehlung | Risiko | Aufw. | dev? | ex-ID |
|---|---|---|---|---|---|---|---|---|---|
| T1 | Datei nirgends eingebunden | cell-modules/components/ModuleCatalogButton.tsx (ganz) | Importgraph: 0 Importer; `grep -rnw ModuleCatalogButton src docs` nur Definition; Katalog wird in ViewportHeaderControls.tsx:511 direkt geöffnet | sicher | Datei löschen | gering | S | nein | W5 |
| T2 | Komponente `PiStatusList` ohne Verwender (nur `PiStatus` und `isPiConnected` werden importiert) | opcua-server/components/PiStatusList.tsx:34-41 | SG: nicht erreichbar; `grep -rn PiStatusList src` nur Definition + Kommentar | sicher | Funktion löschen, `isPiConnected` nach opcua-server/model verschieben (CellWatchPanel importiert es aus der Komponentendatei) | gering | S | nein | neu |
| T3 | Rest des alten Layer-2-Laufs (Pi plant, Frontend meldet Pose): Request-Builder, Parser, Typen, Schema-Konstanten | cell-modules/model/layer2.ts:102-147 (`LAYER2_RUN_SCHEMA`, `PoseJson`, `Layer2RunOptions`, `DEFAULT_LAYER2_OPTIONS`, `Layer2RunRequest`, `buildLayer2Request`); layer2Run.ts:11-125 (`parseLayer2Target/Status`, `layer2Active`, `layer2Report`, `LAYER2_REPORT_SCHEMA`, `LAYER2_PURPOSE_LABELS`, `Layer2Run/StepState/StepStatus/Status`, `parseJson`) | SG: alle nur aus layer2.test.ts / layer2Run.test.ts erreichbar bzw. gar nicht (`DEFAULT_LAYER2_OPTIONS`, `LAYER2_PURPOSE_LABELS`); `grep -rn StartLayer2Run src` leer. `Layer2Pose`/`Layer2Target` bleiben nötig (robotDrive.ts, sceneState.ts) | sicher | Löschen samt zugehöriger Testabschnitte; `Layer2Pose` und eine schlanke `DriveTarget`-Definition nach robotDrive.ts ziehen | gering | M | nein | W2 (Rest) |
| T4 | Zustand `layer2Target`/`setLayer2Target` wird nie gesetzt oder gelesen | viewport/model/sceneState.ts:139-140,191,238-239,252,283 | `grep -rnw layer2Target src` nur sceneState.ts; keine Zuweisung ausser `null` | sicher | entfernen (samt Import `Layer2Target`) | gering | S | nein | W3 |
| T5 | Layer2Binding: `startNodeId`, `reportNodeId`, `targetNodeId` ohne Leser; `statusNodeId` nur für Anzeige "Layer 2 läuft" in PiActivity | opcua-server/config/piServers.ts:111-126,253-258,447-452; components/PiActivity.tsx:65,86,98 | `grep -rn "layer2\." src`: Leser nur PiActivity (Status); Rest nur visionResult.test.ts. Pi-Vertrag `wsc.vision.layer2.*` bleibt dokumentiert | wahrscheinlich | Binding auf Status reduzieren oder ganz streichen, sobald klar ist, dass kein anderer Client den Lauf startet | mittel | S | nein | W4 |
| T6 | Tag-basierter Hand-Auge-Start (abgelöst durch Schachbrett): `handEyeStartJson`, `robotTagChoices`, `HandEyeTagChoice`, `HAND_PI_DEFAULT_TAG_SIZE_M`, `HAND_EYE_GOOD_SAMPLES` | cell-modules/model/handEye.ts:180,194,208,281,286 | SG: nur handEye.test.ts bzw. gar nicht; verwendet wird `handEyeBoardStartJson` | sicher | löschen + Testabschnitt; `describeHandEyeRefusal` Text "Pi kennt nur Tags" gleich mit prüfen (B8) | gering | S | nein | neu |
| T7 | `trackModuleMoves` samt Helfern (Tracking liegt in moduleTracking.ts); nur `applyModuleMoves` wird gebraucht | cell-modules/model/moveTracker.ts:20-100 (`MoveTrackerOptions/State/Step`, `DEFAULT_MOVE_TRACKER`, `floorDistance`, `yawDistance`, `differs`, `trackModuleMoves`) | SG: nur moveTracker.test.ts; FR: 0 Produktivverwender | sicher | alles ausser `applyModuleMoves` löschen, Datei danach evtl. nach sceneState/cellModules verschieben | gering | S | nein | W7 |
| T8 | "Gemessener Ablagepunkt" für Pick&Place (durch Tischmitte / `measurePlaceFromCorner` abgelöst) | cell-modules/model/pickPlace.ts:156-330 (`StoredPlaceTarget`, `storedPlaceTargetTo/FromString`, `isCorner`, `tagFrame`, `placePointInModule`, `placeTargetWorldPose`, `tagPointInBase`); `MEASURED_PLACE_TARGET`, `CALIBRATION_SHEET`, `ITEM_EDGE_6_FROM_CORNER_M` hängen nur daran | SG: Kette nur aus pickPlace.test.ts erreichbar; usePickPlaceRun.ts nutzt nur `TableCorner`, `measurePlaceFromCorner`, `placeAccuracyNote`, `centreShift`, `placeBlocker`, `tableTopCentreInBase` | sicher | Kette (ca. 110 Zeilen) + Tests löschen; `TableCorner`/`modulePointInBase` prüfen | gering | M | nein | neu |
| T9 | `describeCellChange` + `KIND_LABELS`: durch CellChangeAlert abgelöst (dort `moduleName`/`moveDetail`/`KINDS` mit gleicher Logik, siehe D12) | cell-modules/model/cellWatch.ts:229-262 | SG: nur cellWatch.test.ts | sicher | Namens-/Detailhelfer aus CellChangeAlert nach cellWatch.ts heben, `describeCellChange` löschen | gering | S | nein | neu |
| T10 | Attrappen-Treiber `DISCONNECTED_DRIVER`, `PRETEND_DRIVER` ("Trockenlauf") | cell-modules/model/robotDrive.ts:94-114 | SG: 0 Verwender, auch in Tests nicht | sicher | löschen | gering | S | nein | W6 (+neu) |
| T11 | `shiftBounds` | cell-modules/config/moduleCatalog.ts:306-312 | FR: 0 Verwender | sicher | löschen | gering | S | nein | W6 |
| T12 | Alias-Konstanten aus der Auslagerung nach tagViews.ts: `ROBOT_TAG_TILTS` (nur Test), `ROBOT_TAG_VIEW_ROLLS_DEG` (nirgends), Einzeiler `robotTagViews()` | cell-modules/model/robotTagRun.ts:40-42,60 | SG | sicher | Aliase löschen, Test auf `TAG_VIEW_TILTS`/`tagViews` umstellen | gering | S | nein | neu |
| T13 | `VISION_RECIPES` + `VisionRecipe` nur von Test benutzt | opcua-server/config/piServers.ts:9-37 | SG: nur visionResult.test.ts:255-280 | wahrscheinlich | löschen oder per UI nutzen (Pi-Vertrag steht im Zellen-Repo) | gering | S | nein | W7 |
| T14 | `snapToStep` | tag-tool/model/units.ts:11 | FR: 0 Verwender | sicher | löschen | gering | S | nein | 1.3 |
| T15 | Feld `resultNodeId` der VisionBinding wird nie gelesen ("Unused: payload arrives in event") | piServers.ts:291-292,362,402,427 | `grep -rnw resultNodeId src`: nur Test-Fixture | sicher | Feld entfernen | gering | S | nein | neu |
| T16 | Ungenutzte Importe/Variablen (tsc) | centreOnTag.ts:4 `CENTRED_DISTANCE_M`; tagPlacement.ts:5 `TagReferenceLine`; AutolocateActions.tsx:8 `autolocateUi`; TagReferencePanel.tsx:135,137 `robots`,`model`; Tests: robotHold.test.ts:11 `id`, tagReferenceRun.test.ts:9 `MeasureReferenceOutcome`; Backend application_service.py:50 `ServerStatus` | `npx tsc --noEmit -p tsconfig.json --noUnusedLocals --noUnusedParameters` (30 Meldungen, 9 in unseren Hunks); Python-AST | sicher | entfernen; danach `noUnusedLocals` in tsconfig erwägen | gering | S | nein | neu |
| T17 | Task-Editor (alte dennis-Fassung): ungenutzte `cloneTaskStep`, `cloneConstant`, `summarizeJsonValue/SkillInputs/Waypoint`, Parameter `node`; TaskProgramPanel `lastTransition`, `currentStepId`, `currentStepState`, `stepStates`, `formatBooleanLike`, `truncateDebugValue`, auskommentierter JSX-Block `TaskInfoRow` (Z.559) | task/model/editorGraph.ts:131-160,490,514,749; task/components/TaskProgramPanel.tsx:269-287,559,1160,1180 | tsc + SG | sicher | **nicht anfassen**, Revert-Code (S1) | - | - | Revert | neu |
| T18 | Leeres Paket `backend.config` (blieb nach Entfernen von pi_relay.py übrig) | backend/src/backend/config/__init__.py | `grep -rn "backend.config"`: 0; Commit 42cbcb9 | sicher | Ordner löschen | gering | S | nein | B1 (Rest) |
| T19 | Getracktes Build-Artefakt | frontend/tsconfig.tsbuildinfo | `git ls-files`: getrackt; tsconfig/package.json ohne `incremental`/`composite`, kein Verweis; nicht in .gitignore | sicher | `git rm --cached`, `*.tsbuildinfo` in frontend/.gitignore | gering | S | nein | neu |
| T20 | CSS-Klasse `.select` (+ `:focus`, `option`) ohne Verwender; zweite Definition in dev (index.css:495) ebenfalls ungenutzt | frontend/src/index.css:227-250 | `grep` aller `<select`: keine hat class `select`; `grep -rnE 'className=.*select'` leer | sicher | unseren Block löschen (dev-Block nur melden) | gering | S | Block 495 ja (melden) | neu |
| T21 | 3D-Modelle ohne Verweis: `public/models/table-cobot/table-cobot.stl`; `dark-magician` nur vom Code-Default MOD-2 (moduleCatalog.ts:379) | frontend/public/models/ | `grep -rn` über src/backend/docs: table-cobot 0 Treffer; dark-magician nur moduleCatalog.ts:379 | wahrscheinlich (table-cobot erscheint über das Vite-Plugin in der Auswahl) | table-cobot klären/löschen; dark-magician siehe B5 | gering | S | nein | neu |
| T22 | Konstante `PI_ASSUMED_TAG_SIZE_M` layer1/layer2 identisch, `WORLD_TAG_IDS` doppelt zu Katalog-Daten | moduleCatalog.ts:262-274 | siehe D13 | unsicher | bei D13 mitklären | - | - | nein | neu |
| T23 | Unnötige `export`-Schlüsselwörter | 203 Exporte (meist Typen/Konstanten) nur in der eigenen Datei benutzt; 111 Exporte nur von Tests benutzt, davon 17 ohne interne Verwendung (= T3, T6-T9, T12, T13) | FR-Analyse `analysis.json` | sicher | nur im Zug anderer Änderungen bereinigen | gering | M | nein | neu |
| T24 | dev-Code (nur melden): ungenutzte Variablen `socketStatus` (DesktopLayout.tsx:50), `setServersOpen` (ServerManager.tsx:14), `nextSampleCount` (JointAnglesPanel.tsx:339), `shortServerName` (RobotManager.tsx:202), Viewport.tsx `robotLabel`, `workspaceGenerationState`, `originPosition`, `originQuaternion`, `collidingRobotIds`; `solvePositionIk` u.a. (robotIk.ts), `applyRobotJointMapping`, `createActiveRobotJointSyncSession`, `discover_methods`/`discover_skills` (asyncua_discovery.py:998,1026) | s.o. | In origin/dev gleich oft vorhanden (`git show origin/dev:... | grep -c`), also dev-Altlast | sicher | nicht anfassen | - | - | ja | 1.3 |

## 2. Doppelungen (D)

Spalten wie T; "Empfehlung" nennt die Variante, die bleibt, und die umzustellenden Aufrufer.

| ID | Was | Wo | Beleg | Konf. | Empfehlung (bleibt / umstellen) | Risiko | Aufw. | dev? | ex-ID |
|---|---|---|---|---|---|---|---|---|---|
| D1 | **Pi-Methodenaufruf + Polling auf methodCallStatuses** fünfmal ausgeschrieben (gleicher Ablauf: nodeId prüfen, callRawMethod quiet, Schleife waited += POLL_MS, Fehlerregex BadNodeIdUnknown/BadMethodInvalid, Output ResultJson+Error, START_SINGLE_JOB_ERRORS) | opcua-server/model/piMethodCall.ts:22-61; cell-modules/model/measureBoardCall.ts:22-76; measureTagsCall.ts:18-70; tagReferenceCall.ts:364-409 (callTagReference) und :456-496 (setTagReferencePreview) | dupblocks.py: 9-10 identische normalisierte Zeilen zwischen allen vier Dateien (piMethodCall 37-43 = tagReferenceCall 474-480) | sicher | **Bleibt:** callTagReference (hat schon MethodSpec) verallgemeinert nach opcua-server/model/piMethodCall.ts; measureTags, measureBoard, planTagReferenceView, measureTagReference, setTagReferencePreview, callPiMethod werden dünne Spezifikationen. Aufrufer unverändert (gleiche Signaturen): ApproachPlanPanel, TagReferencePanel, centreOnTag, tagViews, robotTagRun, tagReferenceRun, HandEyeAutoPanel, useBoardCalibrationRun, usePauseWhileRobotMoves | mittel (Fehlertexte je Wrapper erhalten) | M | nein | neu |
| D2 | Warteschleifen: waitFor(condition, timeout) in BoardRunner (boardCalibrationRunner.ts:184-193) und HandEyeAutoPanel.tsx:188-200; Greifer-Antwort abwarten in usePickPlaceRun.ts:666-676 (awaitCall) und inline useBoardCalibrationRun.ts:309-317; sleep 4 mal lokal (HandEyeAutoPanel.tsx:74, useBoardCalibrationRun.ts:133, usePickPlaceRun.ts:78, tagReferenceCall.ts:411) plus Default-Lambda in den D1-Dateien; POLL_MS 7 mal; GRIPPER_TIMEOUT_MS=15_000 und CAPTURE_TIMEOUT_MS=60_000 je 2 mal | siehe Wo | Funktions-Hash sleep identisch (23 Tokens); grep deadline/waited: 14 Schleifen | sicher | Modul shared/async.ts mit sleep, waitUntil(cond, timeoutMs, deps), awaitMethodCall(controller, requestId, timeoutMs). Bleibt: Variante des BoardRunner (injizierbare Uhr, testbar) | gering | M | nein | neu |
| D3 | Hooks "Methode aufrufen und auf Status warten": gleiche Bausteine requestIdRef, clearTimer, call() mit Timeout, Effekt auf methodCallStatuses; COMMAND_TIMEOUT_MS = 330000 vier mal | useCellWatch.ts:29-109; useHandEyeCalibration.ts:35-128; useSaveRawImage.ts:57-119; useCameraCalibration.ts:100-386; useVisionJob.ts:79-344 (JOB_TIMEOUT_MS :70); useContinuousVisionJob.ts | clearTimer-Körper identisch in 4 Dateien (useCameraCalibration:246, useCellWatch:57, useHandEyeCalibration:65, useSaveRawImage:68); Block-Duplikate useCellWatch:41-54/75-88 = useHandEyeCalibration:49-62/89-102 | sicher | Hook usePiCommand(serverUrl, timeoutMs) mit run(nodeId, args), pending, error; Timeout einmal (PI_COMMAND_TIMEOUT_MS, an Backend-Timeout gekoppelt dokumentieren); 5 Hooks umstellen | mittel (Hooks ohne Tests, siehe L1) | L | nein | neu |
| D4 | "Pi-Knoten abonnieren und Live-Wert lesen" 8 mal von Hand (wortgleicher Kommentar "subscribeNode/unsubscribeNode get a new identity..."); privater Helfer useSubscribedNode existiert schon | cell-modules/model/useTagReferenceLive.ts:9 (Original, nicht exportiert); Kopien: useCameraHealth.ts:44-52, useMjpegStreamUrl.ts:24-32, useCameraFrame.ts:41-48, useCellWatch.ts:39-47, useHandEyeCalibration.ts:47-55, useCameraCalibration.ts, PiActivity.tsx:75-80. Dazu namespaceUris + useMemo(resolveVisionBinding) 20 mal | grep "resolveVisionBinding(" src: 20 Treffer; 21 mal byUrl[...]?.namespaceUris | sicher | useSubscribedNode nach opcua-server/model exportieren, dazu usePiBinding(serverUrl); Kopien umstellen | gering | M | nein | neu |
| D5 | Effekt "Robotermodell geladen?" (setInterval 300 ms) 4 mal | ApproachPlanPanel.tsx:111-118; TagReferencePanel.tsx:143-149; usePickPlaceRun.ts:203-209; useBoardCalibrationRun.ts:222-229 | dupblocks: 16 Zeilen ApproachPlanPanel 105-124 = TagReferencePanel 138-155 | sicher | Hook useRobotModelLoaded(controller, robot, kinematics, enabled) neben robotModelLoaded in controllerRobotDrive.ts; ebenso gemeinsamer Vorspann (useCellRobot, layer2Gate, driveReadiness, missing) der drei Panels/Runs | gering | S | nein | neu |
| D6 | **Vektor-Helfer** (add, sub/subtract/minus, scale/scaled, dot/dotVec, cross, unit/normalise, norm, distance) lokal definiert | approachPlan.ts:143-153; centreOnTag.ts:41-50; tagViews.ts:122-131; tagServo.ts:122-123; tagEdgeDistance.ts:14-16; routeProgress.ts:14; tagPlacement.ts:30-53; modelPlanes.ts:230-246; modelEdges.ts:20-31; cellModules.ts:207; worldTagCheck.ts:79 | Funktions-Hash: cross 5 Kopien (3 wortgleich), sub 5, add 3, dot 4, scale 4, subtract 3, distance 2 wortgleich | sicher | Bleibt: neues cell-modules/model/vec3.ts (Variante aus tagPlacement.ts: vollständig, normalise ohne Division durch 0); 11 Dateien importieren. three.js Vector3 bewusst nicht (Tupel-Typ Vec3 überall) | gering (reine Funktionen, Tests vorhanden) | M | nein | neu |
| D7 | Grad/Rad: const DEG = Math.PI / 180 sieben mal (CellModuleModel.tsx:22, ModulePreview.tsx:38, WorldTagMarkers.tsx:19, handEyeAutoPoses.ts:94, tagGeometry.ts:33, tagReferenceFusion.ts:37, tagViews.ts:26), dazu ca. 45 Inline-Umrechnungen in 29 Dateien, und degToRad/radToDeg in tag-tool/model/units.ts:7-8 | grep Math.PI / 180 bzw. 180 / Math.PI | sicher | DEG, degToRad, radToDeg einmal in shared/math/angles.ts; tag-tool/units.ts behält nur mm/m | gering | S | nein | neu |
| D8 | **Pose-/Quaternion-Bibliothek zweimal:** tagGeometry.ts (eigene Tupel-Mathematik, Pose.orientation) und tag-tool/model/pose.ts (three.js, Pose.quaternion, composePoses(...poses), invertPose, quaternionFromRpy, quaternionFromAxes = quaternionFromBasis). Gleiche Pose-Form als eigener Typ 5 mal: Pose (tagGeometry.ts:110), HandEyePose (handEye.ts:13), Layer2Pose (layer2Run.ts:13), PoseJson (layer2.ts:104), TagToModule (moduleCatalog.ts:31). Gier-Helfer doppelt: cellModules.ts:141-146 (yawFromQuaternion, quaternionFromYaw) vs tagServo.ts:183-190 (yawOf, yawQuat) | siehe Wo | grep "interface (HandEyePose|Layer2Pose|PoseJson|Pose)" | sicher (Typen), wahrscheinlich (zwei Bibliotheken) | Bleibt: tagGeometry.ts (30+ Verwender, getestet, JSON-tauglich). Typen zu Pose zusammenlegen. tag-tool auf tagGeometry umstellen oder mit dem Tool entfernen (D14) | mittel | M | nein | P5 |
| D9 | Kleine Format-/Parse-Helfer mehrfach: comma(value,digits) 8 mal (tagViews.ts:104, tagEdgeDistance.ts:12, tagMethodComparison.ts:34, tagReferenceFusion.ts:80, robotTagRun.ts:114, TagReferencePanel.tsx:99, ApproachPlanPanel.tsx:567, TagReferenceLiveParts.tsx:11); mm 4 mal; formatMm 2 (AutolocateLayer1Window.tsx:126, HandEyeCalibrationWindow.tsx:17); formatMetres 2 (CellModuleMarkers.tsx:15, WorldTagMarkers.tsx:98) plus formatWorldMm (format.ts); describeError 4 (boardCalibrationRunner.ts:107, catalogStore.ts:373, useBoardCalibrationRun.ts:135, usePickPlaceRun.ts:108); finiteOrNull identisch handEye.ts:116 = visionResult.ts:83; num/numberOrNull 4 (boardCentring.ts:64, cellWatch.ts:115, tagReferenceCall.ts:156, tagServo.ts:54); isVec 2 | siehe Wo | Funktions-Hash exakt gleich (23-30 Tokens) | sicher | shared/format.ts (comma, mm, metres) und shared/parse.ts (numberOrNull, isVec, finiteOrNull, errorText); cell-modules/model/format.ts (7 Zeilen) darin aufgehen lassen | gering | S | nein | neu |
| D10 | Mini-Store-Muster (Zustand + listeners + useSyncExternalStore) 3 mal wortgleich, plus eigener Store in catalogStore.ts:351-360,490-496 und cellWatchSwitch.ts; localStorage lesen/schreiben mit try/catch in 7 Dateien | autolocateUi.ts:52-95; robotOperation.ts:15-47; tagReferenceSettings.ts:108-145; localStorage: AutolocateLayer1Window.tsx:49,69; autolocateUi.ts:36,62; catalogStore.ts:332-344; handEyeBoard.ts:38; tagReferenceSettings.ts:112,126; useBoardCalibrationRun.ts:141,150; usePickPlaceRun.ts:227,253,428,821,831 | Struktur-Hash: useAutolocateUi/useRobotOperation/useTagReferenceSettings gleich; in dev-Code nirgends localStorage (grep) | sicher | shared/createStore.ts (createStore(initial, persistKey?)) | gering | M | nein | neu |
| D11 | Zahleneingabe in 3 Varianten (Commit bei Enter/Blur vs. live): tag-tool/components/NumberField.tsx:21-78, shared/ui/SecondsField.tsx:23-35 (commit struktur-gleich), ModuleCatalogModal.tsx:139-178; Field 2 mal (ModuleCatalogModal.tsx:180, CameraCalibrationModal.tsx:59); Overlay-Rahmen "fixed inset-0 z-40 ... bg-black/50" in ModuleCatalogModal.tsx:1436, RobotRunConfirm.tsx:40 (dev: ConnectOpcUa.tsx:39, RawMethodCallModal.tsx:96) | siehe Wo | Funktions-Hash | wahrscheinlich | ein shared/ui/NumberField (Modus commit/live) und shared/ui/Modal; dev-Stellen nur melden | gering | M | Overlay: ja (melden) | neu |
| D12 | Alte und neue Darstellung einer Zellenänderung: describeCellChange (cellWatch.ts:236-261) vs moduleName/moveDetail in CellChangeAlert.tsx:15-37 (gleiche Fallback-Kette, gleiche Zahlenformate) | siehe Wo | Sichtvergleich | sicher | Logik einmal in cellWatch.ts (siehe T9) | gering | S | nein | neu |
| D13 | **Zwei Quellen für den Standardkatalog:** Code-Default MODULE_CATALOG (moduleCatalog.ts:358-432: 10 Einträge = MOD-1, MOD-2, MOD-A, MOD-B, MOD-C, ROBOT-DEMO1 + 4 Welt-Tags WORLD-583..586; der Koordinator zählte 7) vs. backend/catalog/module_catalog.json (8 Einträge, Revision 35: MOD-1 "CrashBandicoot", ROBOT_FRANKA, WORLD-0..0-4, MOD-7, MOD-8). IDs, Namen, Tag-IDs und Modelle weichen ab. Verwendung des Defaults: catalogStore.ts:352,392,468 (Fallback/Reset), Default-Parameter in cellModules.ts:365,387,450, layer1Import.ts:65, findModuleByTag:445; Tests nutzen MODULE_CATALOG[0/1] (catalogStore.test.ts, catalogSync.test.ts) | JSON per Python gelesen; grep -rn "MODULE_CATALOG" src | sicher | **Bleibt:** JSON im Backend als einzige Quelle; Code-Default auf leere Liste bzw. Test-Fixture reduzieren, Platzhalter/Spielmodelle (B5) fallen weg; WORLD_TAG_IDS (moduleCatalog.ts:262) aus den Katalogeinträgen (kind world) ableiten | mittel (Reset-Funktion, Tests) | M | nein | neu |
| D14 | Stand-alone **tag-tool** (22 Dateien, 2523 Zeilen inkl. Tests) dupliziert die Katalog-Tag-Platzierung: ModuleScene (tag-tool/components/ModuleScene.tsx:81-188 vs cell-modules/components/ModulePreview.tsx:197-396), tagTexture (sceneParts.tsx:71-93 vs ModulePreview.tsx:49-74), Modell-Laden (loadModule.ts vs CellModuleModel.tsx), Section vs dev shared/ui/DisclosureSection, placement.ts/pose.ts vs tagPlacement.ts/tagGeometry.ts. Nicht verlinkt (nur per URL /tag-tool.html), nicht in README/docs; von aussen nutzt nur WorldTagMarkers.tsx:5-6 tagTexture und printedTagSizeM | grep tag-tool ausserhalb: nur vite.config.ts:92, tag-tool.html, WorldTagMarkers.tsx | wahrscheinlich | Zukunft klären (Autor Shaming Zhang, e5c183a). Bleibt es: tagTexture, tag36h11, tagFrame nach cell-modules/shared verschieben und Doppelungen beseitigen; fällt es weg: Tool, vite.config-Eintrag, tag-tool.html entfernen | hoch (Funktionsverlust) | L | nein | P5 |
| D15 | Geometrie: closestOnSegment (modelPlanes.ts:787) vs closestPointOnSegment (modelEdges.ts:33); distance (cellModules.ts:207, worldTagCheck.ts:79) vs distanceBetween (modelEdges.ts:28); angleBetween (tagViews.ts:101) vs angleBetweenDeg (worldTagCheck.ts:84); modelPlanes.ts:258-271 = 292-305 (13 Zeilen) | dupblocks | wahrscheinlich | je eine Implementierung in vec3.ts (D6) | gering | S | nein | neu |
| D16 | Messlauf-Gerüst für Roboter-Tags und Modul-Tags: moveTo, failed, measureOne fast gleich; robotTagRun.ts:366-378 = tagReferenceRun.ts:674-686 (11 Zeilen); :255-263 = :424-432; :288-297 = :480-489 | dupblocks | wahrscheinlich | gemeinsamen Schritt "Ansicht anfahren und messen" in tagViews.ts (dort liegt measureTagViews schon) | mittel | M | nein | neu |
| D17 | resolveVisionBinding: zwei fast gleiche 20-Zeilen-Objekte für "unknown" (piServers.ts:356-377 und 396-417; 12 Zeilen identisch) | piServers.ts | dupblocks 365-376 = 405-416 | sicher | einmal unknownBinding(resultReadyEventTypeNodeId, eventTypeNodeIds) | gering | S | nein | neu |
| D18 | Geschwindigkeitsgrenze 0,1 doppelt: AUTO_DRIVE_MAX_SPEED = 0.1 (robotHold.ts:9) und MAX_GOTO_SPEED = 0.1 (applicationController.ts:124, kappt per capGotoSpeed alles); Aliase PICK_PLACE_MAX_SPEED (usePickPlaceRun.ts:62), BOARD_DRIVE_MAX_SPEED (useBoardCalibrationRun.ts:48), LAYER2_DRIVE_MAX_SPEED (ApproachPlanPanel.tsx:50), SLOW_DRIVE_MAX_SPEED = /3 (usePickPlaceRun.ts:64) | siehe Wo | grep MAX_SPEED | sicher | eine Konstante, Aliase entfernen; Commit 5812275 führte die Kappung im Controller ein, die Aliase blieben | gering | S | nein | neu |
| D19 | Pi-/Roboter-Adressen und Pi-Listen an mehreren Stellen: piServers.ts:1-2, robotServers.ts:7-11, ModuleCatalogModal.tsx:1090 (Platzhalter), moduleCatalog.ts:225 (Kommentar), module_catalog.json:48, Pi-Auswahl in CameraCalibrationModal.tsx:269, ServerManager.tsx:8 (PI_URLS), PiStatusList.tsx:5 (PIS), sceneState.ts:57 (LIVESTREAM_CAMERAS) | grep | sicher | eine Liste PI_SERVERS (url, rolle, label) in piServers.ts | gering | S | nein | B1 |
| D20 | Backend: application_service.py:1120-1142 = 1189-1211 (18 Zeilen, Aktion ausführen vs halt/reset), asyncua_discovery.py:579-594 = dev method_calls.py:69-84 (15 Zeilen), server_connection.py:455-461 = 525-531, asyncua_discovery.py:1012-1017/1065-1070 | dupblocks.py | wahrscheinlich | erst nach Entscheidung S1; application_service-Block ist dennis/Revert-Code | mittel | M | Revert / ja | neu |
| D21 | dev-nahe Doppelungen (nur melden): entities/server/model/store.ts wiederholt den methodCallStatuses-Update 6 mal (Z. 710-715, 776-789/805-818, 841-849, 871-879, 913-918, 988-993; unsere Zeilen 704-723 setzen es fort); ModelErrorBoundary.tsx (unser) vs dev EnvironmentErrorBoundary.tsx; task/*: TaskProgramPanel.tsx 117-130=721-734, 321-332=866-877, 156-165=520-529, TaskEditorContext.tsx 6-Zeilen-Muster 6 mal, editorGraph.ts 518-533=996-1011, taskDocument.ts 291-297=495-501 | dupblocks | wahrscheinlich | nur melden | gering | S | store/Boundary: ja; task: Revert | neu |

## 3. Behelfe / Altlasten (B)

| ID | Was | Wo | Beleg | Konf. | Empfehlung | Risiko | Aufw. | dev? | ex-ID |
|---|---|---|---|---|---|---|---|---|---|
| B1 | **Hartcodierte Pi-Adressen** 10.10.38.104 / .109, inzwischen von 8 Dateien genutzt (usePauseWhileRobotMoves, CameraCalibrationModal, ServerManager, CellWatchPanel, AutolocateActions, PiStatusList, catalogValidation.ts:158, Layer2CameraWindow, sceneState LIVESTREAM_CAMERAS) | opcua-server/config/piServers.ts:1-2 | grep PI_1_SERVER_URL/PI_2_SERVER_URL; ourgrep IPs | sicher | Konfiguration (Env/JSON/Backend-Endpunkt oder mDNS-Auswahl) statt Konstanten; vorher D19 (eine Liste) | mittel | M | nein | B1 (offen) |
| B2 | Hartcodierte Roboteradressen als Vorschlagsliste (127.0.0.1, 10.10.38.25-28); gleiche Adresse als Platzhalter und Kommentar; im Katalog-JSON ist serverUrl Datum (korrekt) | opcua-server/config/robotServers.ts:7-11; ModuleCatalogModal.tsx:1090; moduleCatalog.ts:225; vite.config.ts:84 (Proxy 127.0.0.1:8000, Dev-Setting, ok) | ourgrep | sicher | Liste aus dem Katalog ableiten (Roboter-Einträge), Platzhalter neutral | gering | S | nein | W9 |
| B3 | "First Layer"/"Second Layer" als Label fester URLs; dieselben Wörter heissen im Workflow "Layer 1/2" bzw. "Level 1/2" (mehrdeutig); Props firstLayerConnected/secondLayerConnected neben UI-Text "Deckenkamera (Pi 1)"/"Handkamera (Pi 2)" | piServers.ts:4-7 (LAYER_LABELS, in 7 Dateien benutzt); PiStatusList.tsx:6-7; CellWatchPanel.tsx:24-25; AutolocateActions.tsx:116-140,511,672; CameraCalibrationModal.tsx:243,271 | grep | sicher | Rolle ("Decken-Pi", "Hand-Pi") aus Serverdaten bzw. einer PI_SERVERS-Liste (D19) | gering | S | nein | B3 |
| B4 | Zeitgebundener UI-Hinweis "Deckenkamera (First Layer) ist noch nicht final vermessen/fest montiert ... Second Layer ist verlässlicher"; Standardwahl PI_2 deswegen; seit eba0b34/9fe7dd1 gibt es einen Roboter-Kalibrierlauf für Kamera 1 | CameraCalibrationModal.tsx:164,169-170,277-281 | Sichtprüfung + git log | wahrscheinlich veraltet | Hinweis streichen, Standard prüfen | gering | S | nein | neu |
| B5 | **Spielmodelle im Repo** (Crash Bandicoot (c) Activision, Dark Magician (c) Konami): frontend/.gitignore hatte "nur lokal, darf nicht ins Uni-Repo"; Commit e2f03ef (John Glanz, 28.09.) entfernte die Ignore-Zeilen und committete die Dateien (LFS). Der Kommentar moduleCatalog.ts:366-367,378 behauptet weiter "kept out of git (see frontend/.gitignore)". Der Backend-Katalog nutzt crash als echtes Modul MOD-1 "CrashBandicoot" (module_catalog.json:23); dark-magician nur über den Code-Default MOD-2 | frontend/public/models/crash (652 K), dark-magician (3,2 M); moduleCatalog.ts:366-379 | git log -p frontend/.gitignore; grep | sicher | Lizenzfrage mit Betreuern klären; Modelle entfernen, MOD-1 durch neutrales Modell ersetzen, .gitignore-Zeilen wiederherstellen; History-Bereinigung nur nach Absprache | hoch (Recht/Verlust der Demo) | M | nein | neu |
| B6 | Platzhaltermodule im Code-Default ("Module 1 (placeholder)", MOD-A/B/C, ROBOT-DEMO1 "Demo1" mit EVA-URDF) | moduleCatalog.ts:358-411 | siehe D13 | sicher | mit D13 entfernen | gering | S | nein | neu |
| B7 | **Testphase-Schalter** TAG_MEASURE_ALL_TESTPHASE = true ("Vorgabe 21.10.2026": jeder Lauf misst alle Tags neu, Ergebnisse nur auf Knopfdruck). Wirkt über measureAll-Einstellung (localStorage) und ca. 25 testPhase-Zweige | tagMeasurementState.ts:27-30; tagReferenceSettings.ts:36,62; TagReferencePanel.tsx:98,178,482-483,560-632,743-762,1040-1043,1064-1090; TagReferenceWindow.tsx:29 | grep Testphase | sicher | Entscheidung nach Ende der Testphase; dann Zweige und Schalter entfernen (oder Schalter dauerhaft machen und umbenennen) | mittel | M | nein | neu |
| B8 | Kompatibilitätszweige für alte Pi-Stände ("neuen Stand aufspielen", "Pis before this", "older Pi"): legacyCameraFrameNodeId (piServers.ts:297-308,366,406,433; useCameraFrame.ts:17-37, Kommentar "drop once both run the new server"), Hinweise measureTags/measureBoard/tagReferenceCall (measureTagsCall.ts:53, measureBoardCall.ts:59, tagReferenceCall.ts:392,484), AutolocateActions.tsx:37,67 (minWorldTags null), TagReferencePanel.tsx:735, boardCentring.ts:202, handEye.ts:27-92,254 (Pi kennt nur Tags), HandEyeCalibrationWindow.tsx:26, visionResult.ts:40 | siehe Wo | ourgrep "older|aufspielen|veraltet" | sicher | Pi-Stände prüfen; danach Zweige entfernen (Bedingung: beide Pis auf aktuellem Server) | mittel | M | nein | neu |
| B9 | Rest der CPU-Temperatur-Demo: LEGACY_SCHEMA "wsc.vision.test/1" mit eigenem Parserzweig (und Test visionResult.test.ts:201); veralteter Kommentar "drives both the legacy VisionSystem smoke test and the VisionMachine server" | opcua-server/model/visionResult.ts:3,160-170; useVisionJob.ts:76 | grep wsc.vision.test | sicher | Zweig, Felder message/time und Test streichen; Kommentar berichtigen | gering | S | nein | A1/A2/B6 (Rest) |
| B10 | Migrationscode alter Browser-Kataloge: LEGACY_STORAGE_KEY, einmalige Übernahme ins Backend, Feld size statt bounds | cell-modules/model/catalogStore.ts:34,56-61,412-418 | grep legacy | wahrscheinlich | nach Ablauf der Umstellungsphase entfernen (Bedingung: keine Browser mit altem Key) | gering | S | nein | neu |
| B11 | Diagnoseausgaben: 4 console.info "[Zellüberwachung] ..." und 1 "[Kamera] ..."; weitere 9 console.warn/error (Fehlerberichte, vertretbar) | AutolocateActions.tsx:250,254,268,720; useMjpegWatchdog.ts:117; applicationController.ts:309 u.a. | ourgrep console.* (14 Treffer in unserem Anteil) | wahrscheinlich | Info-Logs in ein kleines Log-Modul mit Schalter oder entfernen | gering | S | nein | neu |
| B12 | Veraltete Kommentare/Texte: layer2.ts:24 nennt Layer2RunPanel; RobotRunConfirm.tsx:23 "Layer 2 run, the spin test" (Spin-Test existiert nicht, Komponente nur von CameraCalibrationModal.tsx:483 benutzt); robotDrive.ts:7-18 beschreibt den Pi-geführten Layer-2-Lauf; AutolocateActions.tsx:64-65 (Layer-Fenster würden hier gerendert); moduleCatalog.ts:13 "keeps it in the browser" (Quelle ist das Backend, catalogStore); controllerRobotDrive.ts:55-56 zwei Doc-Kommentare für eine Konstante | siehe Wo | grep | sicher | Kommentare berichtigen, RobotRunConfirm nach opcua-server/components verschieben | gering | S | nein | W8 |
| B13 | **AutolocateLayer1Window**: einziger Aufrufer (AutolocateActions.tsx:701) übergibt immer compact; der Nicht-compact-Zweig (ca. Zeilen 763-872, 110 Zeilen) und das Tracking-/Standardfenster dahinter sind unerreichbar; Prop compact immer true | cell-modules/components/AutolocateLayer1Window.tsx:341,376,645,763-872 | grep compact + Aufrufer | sicher (bestätigt Handbuch-Befund) | Zweig streichen, Prop entfernen, Datei danach zerlegen (U2) | mittel | M | nein | neu |
| B14 | Doku-Regressionen und TODO durch den Revert: docs/development.md:50 "TODO: Confirm whether uv sync ...", :72 "TODO: Add frontend dev server URL", Tabelle "Parcel dev server ... npm run start" (Projekt nutzt Vite), Python 3.12 statt 3.13 (pyproject: >=3.13), npm install statt npm ci, npm test entfernt; README ohne Task-Editor-Link, DOI-Badge ohne Link; docs/task-editor.md gelöscht | docs/development.md, README.md, docs/features.md | git diff origin/dev..HEAD -- docs README.md | sicher | bei Entscheidung S1 auf dev-Stand zurücksetzen | gering | S | Revert | neu |
| B15 | Hand geschriebene vitest-Typen überdecken die echten: `declare module 'vitest'` mit Teilmenge (it.each, vi, einige Matcher) wird von uns erweitert | frontend/src/test/vitest.d.ts:3-23 | Sichtprüfung; Entfernen nicht erprobt (Worktree-Regeln) | unsicher | prüfen, ob tsc ohne die Datei sauber bleibt, dann löschen | gering | S | teils ja | neu |
| B16 | Deployment-Behelfe in dev (nur melden): app.py HOST-Env mountet StaticFiles, host 0.0.0.0; Branchname "Ungetestet" | backend/src/backend/app.py:18-19,30 | Lesen | sicher | nur melden | - | - | ja | B7/B8 |
| B17 | Getrackte Laufzeitdaten: backend/catalog/module_catalog.json (Revision 35, updatedAt) wird von der App beim Speichern umgeschrieben (7 Commits); beabsichtigt (docs/development.md "Module catalog"), erzeugt aber Merge-Konflikte | backend/catalog/module_catalog.json | git log | unsicher | so lassen; Merge-Regel festlegen | gering | S | nein | P10 |
| B18 | Feature-Flags/Debug-Schalter: nur TAG_MEASURE_ALL_TESTPHASE (B7) und showDebugImage (TagReferenceWindow.tsx:34, Nutzerschalter). Keine VITE_*/process.env; Backend nur WSC_DATA_DIR (module_catalog_store.py:34, dokumentiert) | ourgrep | sicher | nichts zu tun | - | - | nein | - |
| B19 | Kein auskommentierter Code, keine TODO/FIXME/HACK im Code; einziger Treffer ist ein auskommentierter JSX-Block TaskInfoRow (Revert-Code) | ourgrep | sicher | s. T17 | - | - | Revert | - |

## 4. Richtlinien (U), nur unser Anteil

**U1 Kennzahlen** (`metrics.py`, nur Produktivcode in frontend/src, Status A/M; bei M ist die ganze Datei gezählt, daher dort überwiegend dev): 182 Dateien / 47.841 Zeilen Produktivcode, 73 Testdateien / 14.360 Zeilen.

| Kennzahl | Summe | Spitzen |
|---|---|---|
| any | 9 | robotIkModel.ts 6 (dev-nah), usePickPlaceRun.ts 1, Viewport.tsx 1, tag-tool/placement.ts 1 |
| as unknown as | 8 | CellModuleModel.tsx 2, layer2Run.ts 2 (stirbt mit T3), Viewport.tsx 2, planeWorker.ts 1, websocketClient.ts 1 |
| ts-ignore / expect-error | 0 | - |
| eslint-disable | 59 | usePickPlaceRun.ts 10, AutolocateLayer1Window.tsx 6, AutolocateActions.tsx 6, ApproachPlanPanel.tsx 4, useCameraCalibration.ts 4, useContinuousVisionJob.ts 4, useBoardCalibrationRun.ts 3 (fast alle react-hooks/exhaustive-deps) |
| console.* | 14 | siehe B11 |

**U2 Dateien über 400 Zeilen** (Produktiv, von uns neu): ModuleCatalogModal.tsx 1643, modelPlanes.ts 1210, TagReferencePanel.tsx 1116, usePickPlaceRun.ts 933, AutolocateLayer1Window.tsx 875, ApproachPlanPanel.tsx 868, pickPlace.ts 849, HandEyeAutoPanel.tsx 762, AutolocateActions.tsx 745, ModelPlanes.tsx 729, tagReferenceRun.ts 699, taskDocument.ts 685 (Revert), useBoardCalibrationRun.ts 627, boardCalibrationRunner.ts 565, piServers.ts 562, cellModules.ts 536, TagToolApp.tsx 536, CameraCalibrationModal.tsx 529, catalogStore.ts 508, tagReferenceCall.ts 497, approachPlan.ts 492, moduleCatalog.ts 455, tagViews.ts 454, ModulePreview.tsx 449, robotDrive.ts 413. Überwiegend dev/Revert, nur melden: applicationController.ts 1891, Viewport.tsx 1812, TaskProgramPanel.tsx 1282, editorGraph.ts 1070, store.ts 1012, JointAnglesPanel.tsx 788.

**U3 Funktionen/Komponenten über 80 Zeilen** (`bigfuncs.py`: 72 Treffer in unserem Anteil; die größten mit Zerlegungsvorschlag):

| Datei:Zeile | Name (Zeilen) | Vorschlag |
|---|---|---|
| cell-modules/components/TagReferencePanel.tsx:134 | TagReferencePanel (918), darin run (236) | run als Hook useTagReferenceRun nach model/ (Vorbild usePickPlaceRun/BoardRunner); gemeinsamen Vorspann (D5) auslagern; Einstellungen und Tag-Zeilen als eigene Komponenten (TagReferenceLiveParts.tsx existiert schon) |
| cell-modules/components/ApproachPlanPanel.tsx:80 | ApproachPlanPanel (788), darin drive (292) | useApproachPlan (Planung) und useApproachDrive (Fahrt) nach model/; Panel nur Darstellung |
| cell-modules/model/usePickPlaceRun.ts:195 | usePickPlaceRun (719), run (137) | Teach-, Routing- und Fahr-/Greifer-Teil trennen; Fahrablauf als Klasse nach Vorbild BoardRunner mit injizierten Abhängigkeiten (testbar, L1) |
| cell-modules/components/HandEyeAutoPanel.tsx:125 | HandEyeAutoPanel (637), run (290) | run nach model/handEyeAutoRunner.ts (Vorbild boardCalibrationRunner.ts), Panel zeigt nur Zustand |
| opcua-server/components/AutolocateActions.tsx:137 | AutolocateActions (608) | Rearm-/Zustandslogik (Z. 236-275) als useCellWatchRearm, Pause-Texte (worldTagPause*) nach cell-modules/model, Aufteilung in Level-1-, Level-2-, Pick-and-Place-Abschnitt |
| cell-modules/components/AutolocateLayer1Window.tsx:370 | AutolocateLayer1Window (505) | zuerst B13 (toter Zweig), dann Scan-Zustand als Hook und Ansicht trennen |
| cell-modules/components/ModuleCatalogModal.tsx:822, 1375, 476, 277 | ModuleEditor (441), ModuleCatalogModal (268), TagPlacementPanel (237), TagEditor (168) | je Komponente eine Datei (ModuleEditor.tsx, TagEditor.tsx, TagPlacementPanel.tsx), Eingabehelfer NumberField/Field/Vec3Fields nach shared/ui (D11) |
| cell-modules/model/useBoardCalibrationRun.ts:176 | useBoardCalibrationRun (451) | Greifer-/Hold-Teil (Z. 300-330) und Positions-Persistenz (Z. 139-170) herausziehen (D2, D10) |
| opcua-server/components/CameraCalibrationModal.tsx:162 | CameraCalibrationModal (367) | Ansichten Idle/Capturing/Ergebnis als Komponenten; Roboter-Teil liegt schon in BoardCalibrationRobot.tsx |
| cell-modules/model/tagReferenceRun.ts:367, 444 | measureTagReferences (332), measureJob (221) | Phasen als Funktionen (anfahren, zentrieren, planen, messen) mit gemeinsamem Gerüst für robotTagRun (D16) |
| tag-tool/TagToolApp.tsx:64 | TagToolApp (472) | Zustand in Hook, Panels (Quelle, Platzierung, Export) als Komponenten, falls das Tool bleibt (D14) |
| opcua-server/model/useCameraCalibration.ts:100, useVisionJob.ts:79, useContinuousVisionJob.ts:60 | 287 / 266 / 251 | schrumpfen mit usePiCommand (D3) |
| opcua-server/config/piServers.ts:354 | resolveVisionBinding (128) | Binding-Aufbau je Teil (calibration, handEye, ...) als kleine Funktionen; Datei in bindings.ts und deviceHealth.ts teilen |
| cell-modules/model/modelPlanes.ts (1210) | Algorithmus-Datei | in planes/buckets.ts (Buckets, Candidates, fitPlane, clusterPlanes), planes/rim.ts (RimLine, mergeCollinear, corners), planes/outline.ts (outlineOf, facesOf, buildPlane) |

Nur melden (dev/Revert, ebenfalls über 80 Zeilen): ViewportRobot (1030) und Viewport (324) in Viewport.tsx, JointAnglesPanel (653), ViewportHeaderControls (480), applyServerMessage (402), TaskProgramPanel (615), TaskEditorProvider (303), RobotActionsPanel (236), DesktopLayout (197; unser Hunk fügt CellWatch/PiActivity hinzu).

**U4 Effekte/Intervalle/Listener ohne Cleanup:** `effects.py` prüfte alle useEffect/useLayoutEffect in unserem Anteil auf setInterval, addEventListener, subscribe, Observer ohne zurückgegebene Aufräumfunktion: **kein echter Treffer** (2 Treffer manuell geprüft, in Ordnung: useBoardCalibrationRun.ts:422, useContinuousVisionJob.ts:240). Randfälle: catalogStore.ts:476-488 startSync startet ein Modul-Intervall ohne Stop (Singleton, beabsichtigt, aber nicht abschaltbar, erschwert Tests); PiActivity.tsx (ResetPisButton, ca. Z. 148) ruft usePiState(url) innerhalb von serverUrls.map auf (Hook in Schleife, funktioniert nur bei konstanter Listenlänge, Regeln der Hooks); 59 eslint-disable auf Abhängigkeiten (U1) verdecken mögliche veraltete Closures.

**U5 Feature-Grenzen** (`xfeat.py`): dev-Code hat **keine** Importe zwischen Features (nur über app/entities/shared). Unser Anteil: cell-modules nach opcua-server 44 Importe, opcua-server nach cell-modules 21 (**zyklisch**), cell-modules nach viewport 17 und viewport nach cell-modules 18 (**zyklisch**), viewport nach opcua-server 10, viewport nach task 3, cell-modules nach robot-control 32, robot-control nach viewport 4, task nach opcua-server 2; außerdem shared nach entities 3, cell-modules nach app 6, cell-modules nach tag-tool 2, tag-tool nach features 2 (robotModels, URDFLoader). Interna statt öffentlicher Fläche: opcua-server/components/AutolocateActions.tsx importiert 13 Dateien aus cell-modules (components und model); Viewport.tsx und ViewportHeaderControls.tsx importieren über 20 Dateien aus cell-modules/opcua-server; cell-modules/model importiert opcua-server/model/useVisionJob, useCameraCalibration, useHandEyeCalibration, useContinuousVisionJob (Hooks eines anderen Features). Empfehlung: AutolocateActions, CellWatchPanel und die Autolocate-Bezüge nach cell-modules (oder ein Feature autolocate), Pi-Hooks und piServers als gemeinsame Schicht (shared/pi oder entities/pi), je Feature eine index.ts mit der öffentlichen Fläche (wie features/opcua-server/components/index.ts), RobotRunConfirm nach shared/ui.

**U6 tag-tool gegenüber dev-Konventionen:** dev: features/NAME/(components, model, context, config), gemeinsame UI in shared/ui, Einstieg nur main.tsx. tag-tool: Geschwisterordner von features statt Feature, eigener Einstieg (main.tsx + tag-tool.html + zweiter Vite-Input), eigene components (NumberField, Section) statt shared/ui, eigene Pose- und Einheitenmathematik (D7, D8), importiert Feature-Interna (robotModels, URDFLoader) und wird selbst von cell-modules importiert (WorldTagMarkers.tsx:5-6). Bleibt es: nach features/tag-tool verschieben, gemeinsame Teile nach shared (D14); sonst entfernen.

**U7 Sprache** (`lang.py`; Kommentarzeilen mit eindeutiger Sprache, String-Literale mit Leerzeichen ab 8 Zeichen): Kommentare unser Anteil **3838 englisch / 45 deutsch**, dev 8 englisch / 0 deutsch (kaum Kommentare); 7 von 158 Dateien mischen. UI-Texte unser Anteil **414 deutsch / 76 englisch**, dev 0 deutsch / 22 englisch. Die Bedienoberfläche ist bei uns überwiegend deutsch, im dev-Code englisch (englisch z. B. ModuleCatalogModal "Module catalog", "Robot model", "Face"; deutsch AutolocateActions, TagReferencePanel, CameraCalibrationModal). Empfehlung: Kommentare englisch lassen (wie dev), eine UI-Sprache festlegen und im Modulkatalog angleichen, Texte langfristig je Feature in einer Datei sammeln; keine Massenübersetzung.

## 5. Tests (L)

**L1 model-, config- und shared-Dateien ohne eigene Testdatei** (`notest.py`): 35 Dateien / 5031 Zeilen ganz ohne Test, 9 weitere nur indirekt (Typen/Konfiguration). Mit Logik und ohne Test, nach Risiko: usePickPlaceRun.ts (933), useBoardCalibrationRun.ts (627, Kernlogik liegt aber im getesteten BoardRunner), useCameraCalibration.ts (387), useVisionJob.ts (345), useContinuousVisionJob.ts (311), sceneState.ts (291), useConnectServerWithFeedback.ts (153), useMjpegWatchdog.ts (133), useHandEyeCalibration.ts (129), useCellWatch.ts (110), autolocateUi.ts (96), useRobotHold.ts (94), measureBoardCall.ts (77; die Schwester measureTagsCall.ts wird indirekt getestet), useRobotMotionWatch.ts (65), robotOperation.ts (48), usePauseWhileRobotMoves.ts (48), planeWorker.ts (37), cellWatchSwitch.ts (16), format.ts (7), tag-tool/model/units.ts (14). Nur Typen/Konfiguration (unkritisch): entities/server/model/types.ts, task/model/types.ts, flangeTrajectory.ts, robotServers.ts. dev-Datei ohne Test (melden): viewport/model/robotIk.ts. UI: es gibt keinen einzigen Komponententest (nur .test.ts, kein .test.tsx). Empfehlung: erst die Hook-Zusammenlegungen (D3, D4, D5) mit je einem Test des neuen gemeinsamen Hooks, dann sceneState und autolocateUi.

## 6. Prüfung der bereits bekannten Handbuch-Funde

| Fund | Ergebnis | ID |
|---|---|---|
| ModuleCatalogButton.tsx nirgends eingebunden | bestätigt (0 Importer, Katalog öffnet ViewportHeaderControls.tsx:511 direkt) | T1 |
| AutolocateLayer1Window rendert nur den compact-Zweig, Tracking-Teil unerreichbar | bestätigt: einziger Aufrufer AutolocateActions.tsx:701 übergibt compact; Nicht-compact-Rückgabe Z. 763-872 unerreichbar | B13 |
| RobotRunConfirm nur in CameraCalibrationModal benutzt, Kommentar nennt Layer 2 | bestätigt (CameraCalibrationModal.tsx:483; Kommentar Z. 23 "a Layer 2 run, the spin test"; Datei liegt in cell-modules, Aufrufer in opcua-server) | B12 |
| Kopfkommentar moduleCatalog.ts sagt "Browser", Quelle ist das Backend | bestätigt (Z. 13; catalogStore holt vom Backend, localStorage nur Cache) | B12 |
| Standardkatalog weicht von backend/catalog/module_catalog.json ab | bestätigt; Code-Default hat 10 Einträge (6 Module/Roboter + 4 Welt-Tags), nicht 7; JSON 8 | D13 |
| frontend/tsconfig.tsbuildinfo getrackt | bestätigt, ohne Konfigurationsbezug | T19 |

## 7. Status der alten WSC-Punkte (altlasten.md, altlasten-toter-code.md)

| Alt | Stand jetzt |
|---|---|
| W1 AutolocateLayer2Window | erledigt (Datei existiert nicht mehr) |
| W2 Kette Layer2RunPanel, EndEffectorSpinTest, endEffectorSpin, useLayer2Run | Komponenten erledigt; RobotRunConfirm lebt (CameraCalibrationModal); Modell-Reste offen (T3) |
| W3 layer2Target / Layer2TargetMarker | Marker-Datei weg; State offen (T4) |
| W4 Layer2Binding | offen (T5) |
| W5 ModuleCatalogButton | offen (T1) |
| W6 shiftBounds, DISCONNECTED_DRIVER | offen (T10, T11) |
| W7 trackModuleMoves, VISION_RECIPES | offen (T7, T13) |
| W8 veraltete Kommentare | offen, erweitert (B12); der Kommentar AutolocateActions.tsx:64-65 ist nicht mehr auffindbar, der Import autolocateUi dort ist aber unbenutzt (T16) |
| W9 Roboteradressen | offen (B2) |
| B1 Pi-Adressen | offen, Nutzung gewachsen (B1); Backend-Teil erledigt bis auf leeres Paket (T18) |
| B3 First/Second Layer | offen (B3) |
| B7, B8 | dev bzw. Branchname, nur melden (B16) |
| D7, D8 (alte Testfehler) | nicht mehr gültig (Ausgangsstand grün) |

## 8. Nicht untersucht / offen

- Kein Laufzeittest: Aussagen "ungenutzt" stützen sich auf statische Analyse. Dynamische Zugriffe (Registrierung per String, JSON-Konfiguration) nur per grep geprüft.
- Entfernen der vitest-Typdatei (B15) und jede Löschung wurden nicht erprobt (Repo nur lesend).
- Textähnliche Doppelungen unter 6 Zeilen und semantische Doppelungen ohne gleichen Text (z. B. gleiche Fahrlogik in Panels) nur stichprobenartig; der Struktur-Hash fand 35 Gruppen, aufgenommen sind die relevanten.
- Revert-Dateien (S1) nicht inhaltlich bewertet; Funde dort nur als Hinweis.
- Python-Backend: AST-Prüfung auf ungenutzte Definitionen und Importe (1 echter Treffer, T16); ruff nicht verfügbar. Backend-Dateien ohne eigenen Test (server_connection, action_execution) nicht neu bewertet.
- Hunks in Mischdateien per git blame dem Revert oder der Gruppe zugeordnet, nicht jede Zeile einzeln.
- Spielmodelle sind im Worktree nur LFS-Zeiger (130-133 Bytes); Inhalt nicht geprüft.
- 203 nur intern genutzte Exporte (T23) nicht einzeln aufgelistet (Liste in analysis.json).

## 9. Priorisierte Umsetzungsliste (Top 15)

| Nr | Maßnahme | IDs | Nutzen | Risiko | Aufw. |
|---|---|---|---|---|---|
| 1 | Revert 839857c: geklärt — bleibt (bewusst, siehe Abschnitt 0); Revert-Dateien (features/task, Docs, Backend-Mischdateien) beim Aufräumen nicht anfassen | S1, B14, T17, D20, D21 | sehr hoch (verhindert Merge-Konflikte und Doku-Regression) | mittel | M |
| 2 | Spielmodelle (Activision/Konami) aus Repo und Katalog nehmen, .gitignore und Kommentar wiederherstellen, mit Betreuern abstimmen | B5, T21 | hoch (Rechtslage) | hoch | M |
| 3 | Kleinkram in einem Commit: tsbuildinfo untracken, leeres backend/config, ungenutzte Importe/Variablen (tsc noUnused), CSS .select, ModuleCatalogButton, PiStatusList-Komponente, snapToStep, shiftBounds, resultNodeId | T1, T2, T11, T14-T16, T18-T20 | mittel, sofort sichtbar | gering | S |
| 4 | Toten Modellrest samt Tests löschen: layer2.ts/layer2Run.ts, handEyeStartJson, trackModuleMoves, Ablagepunkt-Kette, describeCellChange, Treiber-Attrappen, Aliase, VISION_RECIPES, layer2Target, Layer2Binding | T3-T10, T12, T13, T5 | hoch (ca. 600 Zeilen weniger) | gering | M |
| 5 | AutolocateLayer1Window: unerreichbaren Nicht-compact-Zweig entfernen | B13 | mittel (ca. 110 Zeilen, Voraussetzung für Zerlegung) | gering | M |
| 6 | Pi-Methodenaufruf vereinheitlichen (callTagReference verallgemeinern) | D1 | hoch (5 Kopien, Fehlertexte an einer Stelle) | mittel | M |
| 7 | Gemeinsame Mathe-/Format-Module: vec3.ts, angles, format/parse, Pose-Typen vereinheitlichen | D6, D7, D8, D9, D15 | hoch (ca. 40 Kopien) | gering (Tests vorhanden) | M |
| 8 | Hooks zusammenführen: usePiCommand, useSubscribedNode, usePiBinding, useRobotModelLoaded, Warte-Helfer, Geschwindigkeitskonstante | D2, D3, D4, D5, D18 | hoch (6 Hooks schrumpfen deutlich) | mittel (Hooks ohne Tests, daher zuerst Test des neuen Hooks) | L |
| 9 | Standardkatalog nur aus dem Backend, Platzhalter weg | D13, B6 | mittel | mittel | M |
| 10 | Pi-/Roboter-Konfiguration: eine Liste mit Rolle, Label und Adresse, Adressen nicht im Code, Hinweis B4 weg | B1, B2, B3, B4, D19 | mittel | mittel | M |
| 11 | Legacy-/Kompatibilitätscode nach Prüfung der Pi-Stände entfernen: LEGACY_SCHEMA, legacyCameraFrameNodeId, Alt-Pi-Texte, localStorage-Migration | B8, B9, B10 | mittel | mittel (Pi-Stände prüfen) | M |
| 12 | Veraltete Kommentare berichtigen, RobotRunConfirm und AutolocateActions an den richtigen Ort | B12, U5 | mittel | gering | S |
| 13 | Feature-Grenzen: Zyklen cell-modules, opcua-server und viewport auflösen, index.ts je Feature, Pi-Schicht gemeinsam | U5 | hoch (langfristig) | mittel | L |
| 14 | Große Dateien zerlegen: ModuleCatalogModal, TagReferencePanel, ApproachPlanPanel, usePickPlaceRun, HandEyeAutoPanel, modelPlanes | U2, U3 | mittel | mittel | L |
| 15 | Zukunft des tag-tool klären (entfernen oder nach features verschieben), Testphase-Schalter nach Ende der Testphase abbauen, Tests für sceneState, autolocateUi und neue Hooks | D14, U6, B7, L1 | mittel | hoch (D14) | M |


## 10. Welttag-Kette über beide Repos

> **Nachtrag 30.10.2026, 17:30 — Stand der Pi-Tag-Map hat sich geändert.** Die Analyse unten wurde gegen `3e54cd6` („Welttags 584-586 neu vermessen“) geschrieben. Dieser Commit und die beiden folgenden (`89c8d3b`, `6ebab91`) wurden um 15:47 wieder von `apriltag` genommen; der Branch steht auf `d55df91`. Dort liegen 583–586 in `config/tagmap.json` wieder auf denselben Lagen wie im Frontend-Seed und im Backend-Katalog (583 (0,0,0), 584 (1,593; −0,052; 0), 585 (1,593; 1,618; 0), 586 (0; 1,618; 0)); die Größe ist auf dem Pi und im Seed 0,198 m, im Backend-JSON 0,2 m.
>
> Die **strukturellen Befunde gelten unverändert**: vier unabhängige Kopien, kein Client für `SetTagMap`/`TagMapJson`, Basis aus dem Katalog gegen Module aus der Pi-Welt (`baseCorrection`), verworfene Neigung, zwei Pi-Tag-Maps. Die Abweichungen von 0,5–2 m unten zeigen, was geschieht, sobald die Pi-Karte neu vermessen wird, ohne Frontend und Backend nachzuziehen — sehr wahrscheinlich die Erklärung dafür, dass die Neuvermessung die Genauigkeit Roboter→Modul verschlechtert hat. Vor einer erneuten Neuvermessung müssen die Kopien gleichgezogen werden (Empfehlung Nr. 0) oder das Frontend die Welttags vom Pi lesen (Nr. 1 und 2).

Anlass: Frage des Nutzers (30.10.2026), warum sich die Welttag-Logik im Frontend nicht flexibel anpassen lässt und warum ein Welttag-Fehler die Genauigkeit Roboter→Modul stört, obwohl er nur ein gemeinsamer Versatz sein sollte. Untersucht wurden dieses Repo und `ADP_flexible_Roboterzelle` (`3e54cd6`). Kernaussagen vom Koordinator nachgeprüft (`tagServo.ts:198-213`, `ApproachPlanPanel.tsx:584-596`, Werte in `config/tagmap.json` gegen `moduleCatalog.ts`). Umbau als eigenes Feature planen — nicht Teil des Aufräumens, weil sich Katalogschema und Abläufe ändern.

Stand: ADP `apriltag` 3e54cd6 (Worktree `apriltag-aufraeumen`), webskillcomposition `Ungetestet` 5812275.
Abkürzungen: ADP = `/home/meric/ADP/ADP_flexible_Roboterzelle/.claude/worktrees/apriltag-aufraeumen`,
WSC = `/home/meric/ADP/webskillcomposition/.claude/worktrees/ungetestet-aufraeumen`, FE = `WSC/frontend/src/features/cell-modules`.
Nur gelesen, nichts geändert. Unsicheres ist als **unsicher** markiert.

### 0. Kurzfassung

1. Es gibt **vier unabhängige Kopien** der Welttag-Daten (Pi-Datei auf zwei Pis, Frontend-Konstante, Backend-Katalog-JSON, Tag-Tool-Konstante). Die Pi-Tag-Map wurde mit 3e54cd6 neu vermessen, **Frontend-Konstante und Backend-Katalog haben noch die alten Lagen** (584-586 weichen um 0,52 m / 1,77 m / 2,06 m ab). Das Frontend ruft nie `SetTagMap` auf und liest `TagMapJson` nicht. Die dafür gebaute Pi-Schnittstelle (ADP `runner.py:862-922`) hat keinen Client.
2. Die Anfahrrechnung ist **nicht welt-unabhängig**. Zwei Stellen mischen Quellen: `baseCorrection` (FE `model/tagServo.ts:198-213`) setzt die Roboterbasis aus der **Frontend-Katalog-Lage** des Anker-Welttags, die Module stehen aber in der **Pi-Welt** (Deckenkamera-Gelenk-PnP). Der Versatz trifft dann nur die Basis, nicht Roboter und Modul gleich.
3. Die Kette `T_base_tag = F · X · C` (`tagServo.ts:100`) selbst ist welt-frei. Welt geht für den **ersten Blick** (Zielvorgabe) und für Pick & Place (`tableInBase = inv(baseInWorld)·poseOf(table)`, `pickPlace.ts:143`) ein.
4. Layer 1 (Pi) ist intern konsistent: Roboter und Modul gehen durch **dasselbe** `T_world_cam` (ein gemeinsames PnP über alle sichtbaren Welttags), nicht je "nächster Welttag". Ein reiner Welttag-Fehler kürzt sich dort heraus. Er kürzt sich **nicht** heraus, wo das Frontend Neigung verwirft ("nur Heading") oder wo Basis und Modul aus verschiedenen Ankern stammen (Frontend-Anker, Layer 2, Pick & Place nach Neuscan).

### 1. Inventar

Spalten: Ort | Was | Seite | Quelle der Werte | wer liest es.

#### 1.1 Pi-Seite (ADP)

| Ort | Was | Seite | Quelle | Leser |
|---|---|---|---|---|
| `config/tagmap.json:1-40` | **Hauptdatei**: `anchorTagId 583`, 4 Welttags, `sizeM 0.20`, `poseInWorld`. 583 = (0,0,0); 584 = (2.0412, 0.2140, -0.0352); 585 = (-0.1535, 1.8813, **0.1700**), Pitch ca. -2,5 deg; 586 = (1.9952, 2.1344, 0.0058) | Pi | Datei (je Pi eigenes Checkout, `server.py:312`) | `load_tag_map` in `detection/apriltag.py:366-370`; `SetTagMap` überschreibt (`runner.py:900-918`) |
| `config/tagmap.example.json`, `tagmap.test.example.json` | Beispiel: Welttags 0..3, `sizeM 0.1`, 2,4 x 1,8 m | Pi | Datei | nur Doku/Tests, bewusst abweichend |
| `src/tagloc/tagmap.py:126-130, 213-220` | `TagMap.anchor_tag_id` (Default 0) + Prüfung | Pi | Datei | `validate_tag_map`, `place_tags` (`:425-455`). Wirkt nicht auf die Lokalisierungsrechnung (**unsicher**: nur per grep geprüft, kein Verweis in `localize.py`/`plan.py`) |
| `src/tagloc/tagmap.py:143-150` | `size_for`: Tag-Map gewinnt, sonst Profilwert | Pi | Tag-Map / Profil | Kamerapose, Tag-Posenschätzung |
| `src/tagloc/tagmap.py:232-249` | `nearest_world_tag` (euklid. Abstand zur Weltpose) | Pi | Tag-Map | `layer2/plan.py:283`, `localize.py:311,453,531` |
| `src/vision_server/server.py:108,147` | `tag_size_m = 0.080` (Modultag-Größe je Pi) | Pi | hartcodiert | Fallback für Tags ohne Map-Eintrag |
| `src/vision_server/profiles.py:21,37,48` | `DEFAULT_TAG_MAP_PATH`, Default-`tag_size_m 0.05` | Pi | hartcodiert | Profil |
| `src/tagloc/localize.py:152-239` | `localize_camera`: `T_world_cam` aus **allen** sichtbaren Welttags; Gelenk-PnP `_solve_joint` (`:241`); `primary_tag_id` = nächster (nur Info, `:233`) | Pi | Tag-Map | `apriltag.py:1190`, Hand-Pi-Anker |
| `src/tagloc/pose.py:272-321` | `solve_camera_from_world_tags`: Eckpunkte = `T_world_tag · Ecke(sizeM)` | Pi | Tag-Map (Lage + `sizeM`) | `localize.py:248` |
| `src/tagloc/localize.py:276-342` | `locate_modules`: `pose = T_world_cam · T_cam_tag · tag_to_module` (`:302-306`); zusätzlich `poseInReferenceTag` zum nächsten Welttag (`:311-316`) | Pi | Tag-Map | Payload |
| `src/tagloc/localize.py:534-557`, `handeye.py:114-124` | `anchor_world_base = T_world_cam · inv(T_flange_cam) · inv(T_base_flange)` | Pi (Hand) | Tag-Map + Hand-Auge + Flanschpose aus Job | `apriltag.py:1196-1210`, `layer2/run.py` |
| `src/vision_server/layer2/plan.py:283-292, 195, 203-205` | Anker-Schritt am **nächsten Welttag zur groben Basis**; Fahrziel `T_base_flange = inv(T_world_base) · T_world_tag · T_tag_cam · inv(T_flange_cam)` | Pi (Hand) | Tag-Map des Hand-Pi + `robotBase` aus dem Frontend | `layer2/run.py:268-290` |
| `src/vision_server/cellwatch.py:670-722` (`CameraPoseKeeper`) | merkt Deckenkamera-Pose aus >= 2 Welttags; Schlüssel = Weltpositionen der Tag-Map | Pi | Tag-Map | Zellüberwachung |
| `runner.py:862-922`, `address_space.py:282` | OPC UA `SetTagMap(String) -> Int32`, Variable `TagMapJson` | Pi | Schnittstelle | **niemand im Frontend** |
| `tests/test_joint_camera_pose.py:27`, `tests/test_cellwatch_storedpose.py:53-60` | alte Lagen 1,593/-0,052/1,618, `sizeM 0.198` | Pi (Test) | hartcodiert | nur Tests |

#### 1.2 Frontend/Backend (WSC)

| Ort | Was | Seite | Quelle | Leser |
|---|---|---|---|---|
| `FE/config/moduleCatalog.ts:262` | `WORLD_TAG_IDS = [583,584,585,586]` | Frontend | hartcodiert | `catalogValidation.ts:96`, `ModuleCatalogModal.tsx:83,119,1466` |
| `FE/config/moduleCatalog.ts:413-430` | Default-Katalog-Einträge `WORLD-583..586`: **584=(1.593,-0.052,0), 585=(1.593,1.618,0), 586=(0,1.618,0)**, Rotation 0, **`sizeM 0.198`** | Frontend | hartcodiert (Seed, wenn Backend leer: `catalogStore.ts:352,392`) | `placeWorldTags` |
| `WSC/backend/catalog/module_catalog.json:139-319` | **gespeicherter** Katalog (Revision 35): `WORLD-0`, `WORLD-0-2/-3/-4` (Namen "Welt-Tag 1..4"), Tags 583-586, `sizeM 0.2`, **Positionen = alte Werte 1.593/-0.052/1.618**, Rotation 0 | Backend | Datei, über UI editiert | Frontend lädt ihn (`catalogStore.ts:392`) |
| `FE/model/cellModules.ts:365-382` `placeWorldTags` | macht aus Katalog `PlacedWorldTag` (`worldPosition`, `rotationDeg`, `sizeM`) | Frontend | Backend-Katalog | `worldTagCheck`, `anchorCandidates`, `baseCorrection`, Marker |
| `FE/model/worldTagCheck.ts` | vergleicht Pi-Messung der Welttags mit Katalog-Lage (Abweichung, Paarabstände) | Frontend | Katalog + Pi-Payload | nur Anzeige (`WorldTagMarkers.tsx`, `ApproachPlanPanel.tsx:169-175`) |
| `FE/model/approachPlan.ts:122-140` `anchorCandidates` | Anker-Kandidaten = gesehene Welttags, Lage **aus dem Katalog** | Frontend | Katalog | `ApproachPlanPanel.tsx:254` |
| `FE/components/ApproachPlanPanel.tsx:584-596` | `baseCorrection({position: worldTag.worldPosition, orientation: quat(rotationDeg)}, tagInBase, placedBase.pose)` | Frontend | Katalog (Welttag) + Hand-Messung | setzt Twin-Basis |
| `FE/model/tagServo.ts:198-213` | `baseInWorld = tagInWorld.position - R_yaw·tagInBase.position` (nur Heading) | Frontend | s. o. | s. o. |
| `FE/model/layer2.ts:150-185` | `StartLayer2Run`-Request: `robotBase` und Modulposen = Layer 1/Twin; **Welttag-Lagen gehen nicht mit** (Pi nimmt seine Tag-Map) | Frontend | Szene | Pi-Layer-2 |
| `FE/components/ModuleCatalogModal.tsx:117-130, 744-796, 1466, 1586` | UI zum Anlegen/Ändern von Welttag-Einträgen (Lage, Größe); Hint `:796`: "Must match config/tagmap.json on the Pi" | Frontend | Handeingabe | schreibt nur den Backend-Katalog |
| `FE/model/catalogValidation.ts:18,87-100` | `MAX_TAG_ID = 586`; Warnung, wenn Welttag-ID nicht in `WORLD_TAG_IDS` | Frontend | hartcodiert | Katalog-Validierung |
| `FE/config/moduleCatalog.ts:272-285` | `WORLD_TAG_THICKNESS_M`, `worldTagBounds(sizeM)` (Default 0,1 bei ungültiger Größe) | Frontend | hartcodiert | Marker |
| `FE/config/moduleCatalog.ts:268-275` `PI_ASSUMED_TAG_SIZE_M` | `layer1 0.08, layer2 0.08` (Spiegel von `server.py:108,147`) | Frontend | hartcodiert, DUP | Hinweis |
| `WSC/frontend/src/tag-tool/components/ExportPanel.tsx:13-14,173-178` | `WORLD_ANCHOR_TAG_ID = 0` ("anchorTagId, 0 in config/tagmap.json") | Frontend (Tag-Tool) | hartcodiert, **veraltet** (Pi-Datei hat 583) | Hinweis im Tag-Tool |
| `FE/model/pickPlace.ts:137-143, 312-322, 500, 679, 770` | `tableInBase = inv(baseInWorld) · poseOf(table)` | Frontend | Szene (Twin-Basis + Modulpose Layer 1/2) | Pick & Place |
| `FE/model/routeProgress.ts:36-59` | Layer-2-Ergebnis → Modulpose: `baseInWorld · tagInBase` | Frontend | Twin-Basis | Szene |
| `FE/model/cellModules.ts:463-480` `mergeLayer1Scan` | "precise"-Module behalten ihre Pose bei neuem Scan (<= 0,1 m Differenz) | Frontend | Szene | Szene |
| `FE/opcua-server/config/piServers.ts` | NodeIds (u. a. `StartLayer2Run`, `:448`); **kein** `SetTagMap`/`TagMapJson` | Frontend | -- | -- |

#### 1.3 Doppelungen mit Werten (Stand heute)

| Information | Pi `tagmap.json` (neu) | FE-Default `moduleCatalog.ts` | Backend-Katalog JSON | Tag-Tool |
|---|---|---|---|---|
| Größe Welttag | 0,20 | **0,198** | 0,2 | -- |
| 583 Position | (0,0,0) | (0,0,0) | (0,0,0) | -- |
| 584 | (2.0412, 0.2140, -0.0352) | (1.593, -0.052, 0) | (1.593, -0.052, 0) | -- |
| 585 | (-0.1535, 1.8813, 0.1700) | (1.593, 1.618, 0) | (1.593, 1.618, 0) | -- |
| 586 | (1.9952, 2.1344, 0.0058) | (0, 1.618, 0) | (0, 1.618, 0) | -- |
| Rotation 584-586 | kleine Neigung/Gier (585 Pitch ca. -2,5 deg) | 0 | 0 | -- |
| Anker-Tag | `anchorTagId 583` | "583 is the world origin" (Kommentar `:414`) | -- | **0** |
| moduleId | -- | `WORLD-583` | `WORLD-0`, `WORLD-0-2..4` | -- |

Differenz Pi gegen Katalog heute (Betrag): 584: 0,52 m, 585: 1,77 m, 586: 2,06 m. 583 stimmt überein. Vor 3e54cd6 stimmten Pi und Katalog in Position und Rotation überein; nur die Größe wich (Pi 0,198, Backend-JSON 0,2).

Auffällig, **unsicher**: 585 hat z = 0,17 m, 584 z = -0,035 m. Das Frontend kommentiert die Welttags als "flat on the floor" (`moduleCatalog.ts:413`). Ob das echte Höhen sind oder ein Vermessungsartefakt, ist aus dem Code nicht erkennbar.

### 2. Warum nicht flexibel im Frontend änderbar

Um einen Welttag hinzuzufügen, zu verschieben oder die Größe zu ändern, muss man **heute** anfassen:

1. `ADP/config/tagmap.json` auf **jedem** Pi, der misst (Decken-Pi und Hand-Pi haben je ein eigenes Checkout, `server.py:312`; nichts synchronisiert sie). Alternativ `SetTagMap` je Pi, aber das ruft kein Frontend-Code auf.
2. Den Backend-Katalog (`module_catalog.json`, in der App über `ModuleCatalogModal.tsx:117-130,744-796`). Der Hint `:796` sagt ausdrücklich, die Werte müssten **von Hand** mit der Pi-Datei übereinstimmen.
3. `moduleCatalog.ts:262` (`WORLD_TAG_IDS`) und `:413-430` (Seed).
4. `catalogValidation.ts:18` (`MAX_TAG_ID = 586`), falls die ID größer ist.
5. `ExportPanel.tsx:14` (Anker-ID 0, schon jetzt falsch).
6. Tests in beiden Repos (alte Lagen).

Eine **einzige Quelle der Wahrheit ist vorgesehen, aber nicht durchgesetzt**: `doc/projektdoku/arbeitsplaene/apriltag-tagmap-ueber-opcua.md` ("Backend wird zur Pflegestelle", fertig 22.09.2026) beschreibt `SetTagMap`/`TagMapJson`. Der Pi hat sie (`runner.py:862-922`, `address_space.py:282`). Die Suche nach `SetTagMap|TagMapJson` in WSC findet keinen Treffer im Code (nur den Schema-String `wsc.vision.tagmap/2` in einem Test und Kommentare zu `config/tagmap.json`); `piServers.ts` kennt beide nicht. Das Frontend hält sich also nicht daran. Der Wert aus 3e54cd6 landet nur auf dem Pi; Frontend und Backend wurden nicht nachgezogen (1.3).

### 3. Fehlerfortpflanzung

#### 3a) Bezug von Roboterbasis und Modul

**Layer 1 (Deckenkamera, Pi).** `localize_camera` bildet `T_world_cam` einmal pro Bild aus **allen** sichtbaren Welttags der Tag-Map (`localize.py:184-239`; bei >= 2 Tags und Kalibrierung ein gemeinsames PnP über alle Tag-Ecken, `:213-221`, `pose.py:272-321`). Roboter und Module laufen danach durch dieselbe Formel `T_world_cam · T_cam_tag · tag_to_module` (`localize.py:302-306`). "Nächster Welttag" wird in Layer 1 nur als **Zusatz** gemeldet (`reference_tag_id`, `poseInReferenceTag`, `:311-316`; `primary_tag_id`, `:233`). Das Frontend wertet beides nicht aus (Suche nach `referenceTag|poseInReferenceTag` in WSC: keine Verwendung außer Namensdubletten). Roboter und Modul beziehen sich in Layer 1 also auf **denselben** Kameraschätzwert. Lagefehler einzelner Welttags gehen dort nur über die Anpassung des PnP in `T_world_cam` ein, nicht je Objekt unterschiedlich.

**Layer 2 am Pi.** Anker: Welttag = `nearest_world_tag(tag_map, request.robot_base)` (`plan.py:283-292`), ein einzelner Tag der **Hand-Pi-Tag-Map**. Danach `T_world_base` aus diesem Tag (`handeye.py:114-124`); Module werden mit der geankerten Basis geplant und gemessen: `T_world_module = T_world_base · F · X · C`. Roboter→Modul ist dann nur `F·X·C`, die Welttag-Lage kürzt sich (`inv(T_world_base)·T_world_base`). Der **grobe erste Blick** nutzt aber die Layer-1-Welt der Decken-Pi aus dem Request (`layer2.ts:150-185`), und die kann sich von der Hand-Pi-Tag-Map unterscheiden (3c-3).

**Layer 2 im Frontend** (`ApproachPlanPanel`, eingebunden in `AutolocateActions.tsx:3`): Basis = Anker-Welttag-Lage **aus dem Katalog** (`ApproachPlanPanel.tsx:584-596`, `tagServo.ts:198-213`); Module = Layer-1-Welt der Decken-Pi. Das sind **zwei verschiedene Bezüge**: Roboter relativ zu einem einzelnen Welttag in der Katalog-Welt, Modul relativ zur Pi-Welt (Fit über alle Tags). Fehler der Tag-Map-Lage dieses Tags und jede Katalog-gegen-Pi-Abweichung gehen **direkt in Roboter→Modul** ein.

#### 3b) Starrkörper, Rotation, Maßstab

- **Starrkörper-Versatz** von `T_world_cam` (Layer 1): kürzt sich in `inv(T_wr)·T_wm` exakt, solange `T_world_cam` ein einziger Wert für beide ist (`E·T_cr`, `E·T_cm`, `E` hebt sich auf). Gilt Pi-intern.
- **Rotationsfehler** von `T_world_cam`: kürzt sich rechnerisch ebenfalls, **aber nicht mehr im Frontend**: `levelModulePose` (`cellModules.ts:172-184`), `robotOriginFromBase` (`layer2.ts:205-226`), `baseCorrection` (`tagServo.ts:203-212`) und `usePickPlaceRun.ts:329-337` behalten nur das **Heading** und werfen die Neigung weg ("its tilt is noise"). Eine Restneigung von `E` (z. B. aus der Lage von 585 mit z = 0,17 m und Pitch -2,5 deg) bleibt dadurch auf der Positionsseite stehen, während die Orientierung abgeschnitten wird. Hebelarm: etwa 17 mm je Grad je Meter Abstand Roboter-Modul. Das ist die Struktur der Rechnung, kein Messwert; die tatsächliche Größe ist **unsicher**.
- **Verschiedene Bezüge** (Katalog-Welttag gegen Pi-Fit): Versatz = Katalog-Lage minus Pi-Lage des Ankertags, **nicht** kompensiert (3c). Heute 0 für Anker 583, bis ca. 2 m für 586.
- **Maßstab / Taggröße:** Die Distanz T_cam_tag skaliert linear mit `assumed_size/true_size` (`tagmap.py:143-150`; Doku `apriltag-tagmap-ueber-opcua.md`). Die Welttag-Größe geht ein (i) in die Ecken des Decken-PnP (`pose.py:314-316`): Eckenversatz je Ecke 1 mm bei 0,198 gegen 0,2, vernachlässigbar; (ii) in die **Kartenvermessung selbst** (`place_tags`, `tagmap.py:425-455`): Abstände aus Ko-Beobachtungen skalieren mit der angenommenen Größe, 1 % sind 2 cm auf 2 m; (iii) in jede Einzel-Tag-Lokalisierung, vor allem im Hand-Pi-Anker (`apriltag.py:1196-1210`): Entfernung Kamera-Tag skaliert 1:1, bei 1,0 m Anker-Standoff (`approachPlan.ts: DEFAULT_ANCHOR_STANDOFF_M = 1.0`) wären das 10 mm je 1 % Größenfehler, bei 0,3 m (`plan.py: DEFAULT_STANDOFF_M`) 3 mm. Das geht über `T_world_base` direkt in die Basis ein. Die Frontend-Konstante 0,198 (`moduleCatalog.ts:428-429`) wirkt **nicht** auf Messungen, nur auf Marker/Bounds: das Frontend schickt nie eine Größe an den Pi (`measureTagsCall.ts` sendet nur `tagIds` und `frames`).

#### 3c) Mischung von Quellen

Belegt:

1. **Frontend-Anker**: Basis aus Katalog-Welttag-Lage (`ApproachPlanPanel.tsx:584-596`) gegen Module aus Pi-Layer-1 (`ApproachPlanPanel.tsx:254`, `approachPlan.ts:90-110`). Der Versatz trifft nur die Basis. Heute für Tag 583 harmlos (Lagen gleich), für 584-586 um 0,5-2 m falsch. Mindernd: `anchorCandidates` sortiert nach `seen` aus `worldTagCheck.deviations` (`ApproachPlanPanel.tsx:169-175, 254`; `approachPlan.ts:136-139`); wahrscheinlich kommt der Tag mit kleinster Abweichung zuerst, also 583. **unsicher**: die Sortierung in `worldTagCheck`/`ApproachPlanPanel.tsx:169-175` habe ich nicht im Detail verfolgt.
2. **Katalog-Orientierung 0 gegen Pi-Rotation**: die Basis-Gier kommt aus `yawOf(tagInWorld · inv(tagInBase))` mit der Katalog-Rotation (`tagServo.ts:203`) und ist damit um die Differenz Katalog-Pi-Rotation verfälscht (Betrag für Gier **unsicher**).
3. **Zwei Pi-Tag-Maps** (Decken-Pi, Hand-Pi): Der Request enthält die Layer-1-Welt der Decken-Pi, der Anker nutzt die Datei des Hand-Pi (`plan.py:283-290`). Gleichstand beider Dateien ist nirgends erzwungen. **unsicher**, ob beide Pis 3e54cd6 gezogen haben.
4. **Pick & Place**: `baseInWorld` = Twin-Ursprung (`usePickPlaceRun.ts:325-337`), `table` = Szenenmodul (Layer 1 grob oder Layer 2 präzise). Nach einem neuen Layer-1-Scan bleiben "precise"-Module erhalten (`mergeLayer1Scan`, `cellModules.ts:463-480`), die Roboterbasis kommt aber neu aus Layer 1, und die Ankerkorrektur wird verworfen (`ApproachPlanPanel.tsx:178-184`). Tisch (Anker-Welt) und Basis (Layer-1-Welt) stehen dann in verschiedenen Welten. Der Mechanismus ist im Code belegt; ob er im Betrieb auftritt, hängt vom Ablauf ab (**unsicher**).

#### 3d) Geht die Welt in den direkten Betrieb ein?

- `tagInBaseFrom = F · X · C` (`tagServo.ts:100`) und `servoStep` (`:132-178`): **welt-frei**. Das Ziel entsteht aus `poseInCamera` der Handkamera, Flansch aus URDF-FK. Welt geht nur ein, wenn der Tag nicht im Bild ist.
- **Erster Blick** (Annäherung, Standoff 0,35 m bzw. 0,6 m): `tagInBase = inv(baseInWorld)·tagWorld` (`approachPlan.ts:90-110`; Pi: `plan.py:203-208`). Auf diesem Pfad schickt der Welt-Fehler (3c-1/-3) den Roboter ins Leere; `MAX_STEP_M = 0,05` (`tagServo.ts`) begrenzt die Korrektur je Schritt auf 5 cm. **unsicher**: ob mehrere Schritte reichen, hängt vom Sichtfeld ab.
- **Pick & Place** (`pickPlace.ts:137-143, 312-322, 770`): Zielpunkte aus `inv(baseInWorld)·poseOf(table)`. Die Welt ist hier **Rechengrundlage**, nicht nur Startnäherung. Der Fehler aus 3c-4 geht ohne Handkamera-Korrektur in die Zielposition (außer die Ecke wird gemessen, `measurePlaceFromCorner`, `usePickPlaceRun.ts:515-520`).
- **Kollisionsprüfung** `cellClearance.ts:25,56,111`: Boden = `bounds.min[2]` des Tisches, also tischrelativ. Kein Welttag-Bezug gefunden.
- **Sicherheitsabstände** (Standoff, `MAX_STEP_M`): Konstanten, weltfrei.

Fazit: "Bei direkter Messung spielt Welt keine Rolle" stimmt für `F·X·C`. Der Betrieb besteht aber nicht nur daraus: Zielvorgabe und Pick & Place rechnen in Weltkoordinaten, und die Basis stammt dort teils aus einer **anderen Welt** (Katalog-Welttag) als die Module. Ein Welttag-Offset ist nur dann ein harmloser gemeinsamer Offset, wenn Basis und Modul **durch dieselbe Welt-Transformation** laufen. Genau das ist im Frontend-Anker nicht gegeben.

#### 3e) Taggröße 3e54cd6

Diff von 3e54cd6 an `config/tagmap.json`: `sizeM` von **drei** Tags (584, 585, 586) von 0.198 auf 0.20; 583 stand schon auf 0.20. Positionen und Orientierungen von 584-586 wurden komplett neu gesetzt (vorher (1.593,-0.052,0), (1.593,1.618,0), (0,1.618,0), Rotation 0; Commit-Text: "stammten aus einer aelteren, nicht mehr gueltigen Vermessung"). Neu: Mittel aus 7 Deckenbildern, Schließfehler < 5 mm / 0,01 deg.

Wirkung: Distanz T_cam_tag ∝ `size_assumed/size_true`; 0,198 statt 0,20 bedeutet, der Tag erscheint 1 % näher. Im Einzel-Tag-Pfad der Decken-Lokalisierung (`localize.py:184-193`) sind das bei 2-3 m Kameraabstand 2-3 cm Entfernungsfehler (Kamerahöhe **unsicher**, nicht im Code gelesen). Im gemeinsamen PnP wirkt die Größe nur über die Ecken (ca. 1 mm), aber die Positionen stammen aus einer Vermessung mit demselben Skalenfehler (`place_tags`). Wie stark die alte Karte verzerrt war, ist nicht nachgerechnet. Frontend und Backend wurden nicht nachgezogen (0,198 in `moduleCatalog.ts:428-429`; Backend-JSON 0,2 mit alten Lagen).

### 4. Empfehlung (nach Aufwand/Risiko)

**Ziel:** Pi-`config/tagmap.json` ist die einzige Quelle der Wahrheit (Format `wsc.vision.tagmap/2`, geschrieben über `SetTagMap` oder direkt auf dem Pi). Das Frontend zeigt nur an, was der Pi meldet.

| # | Maßnahme | Dateien | Aufwand | Risiko | Schnittstelle? |
|---|---|---|---|---|---|
| 0 | **Sofort, ohne Code**: Backend-Katalog (`module_catalog.json:139-319`) und `moduleCatalog.ts:413-430` von Hand auf `config/tagmap.json` 3e54cd6 setzen (Lage, Rotation, `sizeM 0.2`); beide Pis auf denselben Stand ziehen. Beseitigt den akuten Versatz aus 3c-1. Nur Kopie, kein Schutz gegen Wiederholung | siehe links | minimal | minimal | nein |
| 1 | **Anfahrrechnung welt-unabhängig, Teil 1**: `baseCorrection` und `anchorCandidates` mit der **vom Pi gemeldeten** Welttag-Lage (`worldTags` im Layer-1-Ergebnis) rechnen statt mit dem Katalog; ohne Pi-Wert Anker sperren | `FE/components/ApproachPlanPanel.tsx:584-596`, `FE/model/approachPlan.ts:122-140`, `FE/model/tagServo.ts:198-213` | klein | niedrig | nein (Payload enthält Welttag-Posen: `AutolocateLayer1Window.tsx:122`) |
| 2 | Katalog-Welttag-Einträge **nur noch lesen**: `worldPose`/`sizeM` aus `TagMapJson` des Decken-Pi füllen; Eingabefelder `ModuleCatalogModal.tsx:744-796` und Seed `moduleCatalog.ts:413-430` entfallen; `WORLD_TAG_IDS` (`:262`), `MAX_TAG_ID` (`catalogValidation.ts:18`), `WORLD_ANCHOR_TAG_ID` (`ExportPanel.tsx:14`) aus der Karte ableiten | `moduleCatalog.ts`, `catalogStore.ts`, `ModuleCatalogModal.tsx`, `catalogValidation.ts`, `ExportPanel.tsx`, `piServers.ts` (+Knoten `TagMapJson`) | mittel | mittel (Katalog-Schema `wsc.cellModules.catalog/1` ändert sich; Backend-Revision 35 migrieren) | Frontend liest vorhandenen Knoten; **keine** Pi-Änderung |
| 3 | Backend-Katalog: Welttag-Einträge (`WORLD-0`, `WORLD-0-2..4`) entfernen oder als abgeleitet markieren | `WSC/backend/catalog/module_catalog.json:139-319`, Backend-Validierung | klein | niedrig (Migration) | Katalog-JSON-Schema (WSC-intern) |
| 4 | Gleiche Karte auf **beiden** Pis: eine Pflegestelle ruft `SetTagMap` an Decken- und Hand-Pi mit demselben JSON und prüft danach per `TagMapJson`/`tagloc/identity.py` die Gleichheit | Backend (neu) oder Skript `tools/`; `runner.py:862-922` | mittel | mittel (`SetTagMap` liefert BUSY bei laufendem Job/Layer 2/Kalibrierung) | nutzt bestehendes OPC UA; **keine Änderung** an `SetTagMap`/`tagmap.json` |
| 5 | Pi: Tag-Map-Identität (`tag_map_identity`) in Layer-1-Ergebnis und Layer-2-Report aufnehmen; Frontend sperrt den Anker bei Abweichung Decken-/Hand-Pi | `src/vision_server/payload.py`, `layer2/run.py` | mittel | niedrig | **ja**: additives Payload-Feld (z. B. `tagMapId`); `vision-server-interface.md` anpassen |
| 6 | **Anfahrrechnung welt-unabhängig, Teil 2**: Basis und Modul stets aus **derselben** Welt-Transformation; Pick & Place nach neuem Layer-1-Scan: "precise"-Module nur mit der Basis desselben Ankers verwenden, sonst auf grob zurücksetzen (`mergeLayer1Scan`); optional liefert Layer 1 `T_robot_module` direkt | `FE/model/cellModules.ts:463-480`, `usePickPlaceRun.ts:325-337`, `ApproachPlanPanel.tsx:178-184`, `pickPlace.ts` | mittel-groß | mittel (Verhalten) | nein; optional additives Payload-Feld (`poseInRobot`) -> `vision-server-interface.md` |
| 7 | Neigung nicht einseitig verwerfen: Roboter und Modul beide gleich behandeln, bevor relativ verrechnet wird | `cellModules.ts:172-184`, `layer2.ts:205-226` | klein | mittel | nein |
| 8 | Hygiene: Tests/Beispielkarten auf neue Werte; Konstante 0,198 entfernen | `ADP/tests/test_joint_camera_pose.py:27`, `tests/test_cellwatch_storedpose.py:53-60`, `moduleCatalog.ts:428-429` | klein | niedrig | nein |

**Schnittstellenänderungen:** nur Nr. 5 (und optional 6) ändern Payload/OPC-UA-Inhalte, beide additiv. Das `tagmap.json`-Schema bleibt unverändert. Das Katalog-Schema `wsc.cellModules.catalog/1` ändert sich bei Nr. 2/3 (Welttag-Einträge entfallen oder werden abgeleitet).

### 5. Offene/unsichere Punkte

- Ob beide Pis `config/tagmap.json` nach 3e54cd6 gezogen haben (aus dem Code nicht prüfbar).
- Ob 585 wirklich auf z = 0,17 m liegt.
- Ob `anchorCandidates` im Betrieb 583 bevorzugt (hängt von der `worldTagCheck`-Rangfolge ab).
- Wie groß der Neigungs-Hebelarm (3b) im realen Betrieb ist; nicht nachgemessen.
- Genaue Wirkung der alten 0,198 auf die alte Karte (3e): skizziert, nicht verifiziert.
- Hand-Auge-Kalibrierung `X` (`handeye-genauigkeit.md`) ist welt-unabhängig und nicht Teil dieser Frage; nicht geprüft.
- Zeilennummern stammen aus Teil-Lesungen (`sed`/`grep`) und wurden nicht jede erneut gegen die Datei geprüft.

## 11. Umsetzungsstand

Branch `ungetestet-aufraeumen` (webskillcomposition, Basis `Ungetestet` `5812275`, ungemergt). Wird beim Aufräumen fortgeschrieben. Die Commits bis `20bbf7f` liegen auf `origin/ungetestet-aufraeumen`; die Commits ab `3b5652b` (Sitzung B) sind bisher nur lokal und noch nicht gepusht.

| IDs | Stand | Commit |
| --- | --- | --- |
| T1, T2 | erledigt: `ModuleCatalogButton.tsx` gelöscht; Komponente `PiStatusList` entfernt, `isPiConnected` nach `opcua-server/model/piConnection.ts` | `ebc3d05` |
| T18 | erledigt: leeres Paket `backend/src/backend/config/` entfernt | `95bf8e0` |
| T19 | erledigt: `frontend/tsconfig.tsbuildinfo` nicht mehr getrackt, `*.tsbuildinfo` in `frontend/.gitignore` | `e635210` |
| T11, T14, T15, T16, T20 | erledigt: `shiftBounds`, `snapToStep`, Feld `resultNodeId`, ungenutzte Importe und Variablen (auch `ServerStatus` im Backend), CSS-Klasse `.select` | `08f5dfd` |
| T4 | erledigt: Zustand `layer2Target`/`setLayer2Target` aus `sceneState.ts` | `cd4b407` |
| T3, B12 (Teil) | erledigt: Rest des alten Layer-2-Laufs (`layer2Run.ts` samt Test gelöscht, Request-Bau aus `layer2.ts`, `Layer2Pose` nach `robotDrive.ts`); Kopfkommentare von `layer2.ts` und `robotDrive.ts` berichtigt | `ef4532c` |
| T10, T12, T13 | erledigt: Treiber-Attrappen, Alias-Konstanten in `robotTagRun.ts`, `VISION_RECIPES` | `d322927` |
| T6 | erledigt: tag-basierter Hand-Auge-Start (`handEyeStartJson`, `HAND_EYE_GOOD_SAMPLES` u. a.) | `a33ba29` |
| T7 | erledigt: `trackModuleMoves` samt Helfern; `applyModuleMoves` bleibt | `320033a` |
| T8 | erledigt: Kette des gemessenen Ablagepunkts; `MEASURED_PLACE_TARGET`, `TableCorner` und `modulePointInBase` bleiben (Standardecke, `measurePlaceFromCorner`) | `9463ec2` |
| T9, D12 | erledigt: `describeCellChange`/`KIND_LABELS` entfernt; Namens- und Detailhelfer von `CellChangeAlert` als `cellChangeModuleName`/`cellChangeMoveDetail` nach `cellWatch.ts` (mit Tests) | `b03dec6` |
| T5 | erledigt: `Layer2Binding` auf `statusNodeId` reduziert; Pi-Vertrag `wsc.vision.layer2.*` unverändert | `20bbf7f` |
| B9 | erledigt: Zweig des Demo-Schemas `wsc.vision.test/1` in `parseVisionResult` samt Feldern `message`/`time` und Test; kein Pi sendet das Schema noch (ADP `src`, `tests`, `config` ohne Treffer) | `3b5652b` |
| B12 (Rest) | erledigt: `RobotRunConfirm.tsx` nach `opcua-server/components` (neben `CameraCalibrationModal`); Kommentare in `RobotRunConfirm`, `moduleCatalog.ts` (Katalog liegt im Backend), `controllerRobotDrive.ts` sowie Spin-Test-Reste in `FlangeTrajectoryMarker.tsx` und `robotHold.ts`. Die Stelle `AutolocateActions.tsx:64-65` gibt es nicht mehr | `d129912` |
| B13 | erledigt: unerreichbarer Nicht-compact-Zweig samt Tracking und Prop `compact` aus `AutolocateLayer1Window.tsx` (874 → 465 Zeilen); das Scan-Fenster der Zellüberwachung ist unverändert | `0bd4841` |

**Tests**

- Übernahmestand (`20bbf7f`): `tsc --noEmit` fehlerfrei; `vitest run` 78 Dateien / 812 Tests (gegenüber 79 / 836 entfallen `layer2Run.test.ts` und 24 Tests zu gelöschtem totem Code); Backend `pytest -q` 81 bestanden, 1 übersprungen.
- Nach B9, B12, B13 (`0bd4841`): `tsc` fehlerfrei; `vitest` 78 Dateien / 811 Tests (−1: Test des Demo-Schemas, B9); Backend 81 bestanden, 1 übersprungen.

**Folgefunde** (beim Umsetzen entdeckt; laut Nutzer am 30.10. nur vermerken, nicht löschen)

| Was | Wo | Beleg |
| --- | --- | --- |
| Hook für Dauerjobs ohne Verwender (seit B13) | `frontend/src/features/opcua-server/model/useContinuousVisionJob.ts` (310 Zeilen, ohne Test) | `grep -rn useContinuousVisionJob frontend/src`: nur die Datei selbst |
| Verfolgen-Logik nur noch aus Tests erreichbar (seit B13) | `cell-modules/model/moduleTracking.ts` | einziger Importer `moduleTracking.test.ts` |
| `describeLayer1Import` nur noch aus Tests erreichbar (seit B13) | `cell-modules/model/layer1Import.ts:128` | nur `layer1Import.test.ts` |
| `clearCellModules` ohne Aufrufer (seit B13) | `viewport/model/sceneState.ts:149, 241` | grep |
| Feld ohne Erzeuger, Rest des Spin-Tests | `FlangeTrajectory.centre` (`cell-modules/model/flangeTrajectory.ts:23`) und sein Zeichenzweig in `FlangeTrajectoryMarker.tsx:91-96` | kein Planer setzt `centre` |
| Unerreichbare Wege, bewusst belassen | `AutolocateLayer1Window.tsx`: Folge-Scan ohne `onProposal` (zieht Module direkt nach), Zweig `worldTagsOnly` in `importIntoViewport`, Text „Erkannte Module“ bei `following` | der einzige Aufrufer setzt `onProposal` und `headless` genau bei `followTagIds !== null` |
| Alter Browser-Schlüssel | localStorage `wsc.autolocate.layer1.trackingThresholds` wird nicht mehr gelesen oder geschrieben und kann in Browsern liegen bleiben | B13 |

**Entscheidungen**

- **D18** (Nutzer, 30.10.): statt einer festen Konstante **eine im Frontend einstellbare Geschwindigkeit** — Obergrenze für jedes `go_to` und Wert aller automatischen Läufe; das langsame Absetzen bei Pick & Place fährt ein Drittel davon. Grund: Auf `Ungetestet` setzt `a954fb0` (nach der Basis `5812275`) die Obergrenze und die Kalibrierfahrt auf 0,2, die übrigen Läufe bleiben bei 0,1 — die Annahme „überall 0,1“ trifft nicht mehr zu. Umsetzung nach D10, dessen Store sie nutzt.

**Hinweis zum späteren Merge**

`origin/Ungetestet` ist seit der Basis `5812275` weitergelaufen: `8584209` (Tag-Map-Sync, NobbisCode), `a954fb0` (Ablagehöhe, Geschwindigkeit 0,2, Roboter-Log, Höhen aus CAD), `3f4dd21` (Merge). Beim Zusammenführen sind Konflikte absehbar, u. a. in `PiStatusList.tsx` (hier entschlackt, dort ergänzt), `piServers.ts`, `visionResult.test.ts`, `cellWatch.ts`, `moduleCatalog.ts`, `AutolocateActions.tsx`, `applicationController.ts`, `useBoardCalibrationRun.ts` und `usePickPlaceRun.ts`.
