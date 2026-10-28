# Hand-Auge-Kalibrierung: fester Versatz beim Anfahren beseitigen

**Status:** in Arbeit — 28.10.2026
**Verantwortlich:** Meric, `Agent: Hand-Auge-Genauigkeit`
**Thema:** apriltag
**Branch:** `apriltag` (Pi), `handeye-genauigkeit` in webskillcomposition (Frontend)

## Ziel

Beim Anfahren von Tags und Zielen trifft der FR3 systematisch daneben: ein fester Versatz, der bei jedem Anfahren gleich ist (Größe noch nicht gemessen; Ziel ≤ 1 mm). Die Anfahrrechnung `T_base_tag = F · X · C` (`tagServo.ts:100`) nutzt Flanschpose `F` aus der URDF-FK, Hand-Auge `X = T_flange_cam` vom Hand-Pi und Tag-Messung `C` aus `MeasureTags`. Ein fester Versatz kommt aus Fehlern, die alle Ansichten gleich verschieben. Die heutige Gütezahl (Streuung der Brettlage) zeigt sie deshalb nicht.

Hinterher soll die Kalibrierung den Versatz sichtbar machen und ihn klein halten: `X` und der Brettmaßstab werden gemeinsam gegen die Pixelmessung verfeinert, der Tag-Pfad misst genauer, und die Kalibrierung meldet einen echten Vorhersagefehler statt nur einer Konsistenzzahl. Vorher ging das nicht, weil die Gütezahl einen festen Versatz nicht erkennt.

Rahmen (Nutzer, 28.10.2026): Pi läuft auf `apriltag`. Brett = 7×8 Felder, also **6×7 Innenecken**, 23 mm. Keine neue Hardware, Belichtung bleibt, reine Software-Optimierung.

## Bereits gelesen

Ausgewertet für diesen Plan (CLAUDE.md, Abschnitt „Bevor du anfängst“):

- `CLAUDE.md` (Repo-Wurzel): Regeln, Arbeitsplan-Pflicht, Schnittstellenteil
- `.claude/skills/projektdoku/SKILL.md`: Ablauf Lesen, Plan, Umsetzung, Commit
- `doc/projektdoku/arbeitsplaene/_vorlage.md`: Format des Plans
- `doc/projektdoku/arbeitsplaene/README.md`: Doppelarbeits-Check; Index mit Plänen zu Hand-Auge und Brett
- `doc/projektdoku/arbeitsplaene/handeye-schachbrett-automatik.md`: Stilbeispiel, Board-Frame; nennt 5×6 Innenecken, das Brett hat 6×7 (siehe Offene Fragen)
- `doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`: Stilbeispiel, `MeasureBoard`, `reprojectionErrorPx`
- Freigegebener Plan `/home/meric/.claude/plans/glittery-riding-hartmanis.md`: Befunde, Umsetzung A/B, Verifikation

Noch nicht ausgewertet (vor der Umsetzung nachzulesen): `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`, `doc/projektdoku/vision-server-interface.md` (Abschnitt 15), `doc/projektdoku/apriltag-lokalisierung.md`, `doc/projektdoku/altlasten.md`.

## Betroffene Dateien

Diese Liste reserviert die Dateien. Parallele Änderungen daran vorher absprechen.

Hand-Pi (Repo ADP_flexible_Roboterzelle, Branch `apriltag`):

- `src/vision_server/handeye_session.py`: Eckpunkte, Intrinsik und Gelenke je Aufnahme speichern; `intrinsicsSource`; Sitzungsdatensatz; `finish`-Prüfung auf Drehachsen
- `src/tagloc/handeye.py`: neue Funktion `refine_hand_eye` (Levenberg-Marquardt in numpy, Huber, LOO)
- `src/tagloc/cli/calibrate_handeye.py`: `--session <dir>`, Offline-Nachrechnung
- `src/tagloc/pose.py`: IPPE_SQUARE-Lösungen mit `cv2.solvePnPRefineLM` verfeinern (Tag-Pfad)
- `src/tagloc/detector.py`: Subpixel-Ecken für `cam_flange` standardmäßig an (Wahl per Messung im Test)
- `data/handeye/sessions/<UTC-Zeit>/`: Sitzungsdatensätze (JSON + ein Bild je Aufnahme)
- `tests/test_handeye_refine.py` (neu), `tests/test_tag_pipeline.py`, `tests/test_handeye_session.py`

Frontend (Repo webskillcomposition, Branch `handeye-genauigkeit` von `Ungetestet`):

- `robotDrive.ts`: Ankunft nach 3 ruhigen Polls, Gelenke nach der Aufnahme nachlesen
- `HandEyeAutoPanel.tsx`: Stillstand-Logik, Prüfung „Brettursprung anfahren“
- `handEye.ts`: Parser für die neuen Felder
- `HandEyeCalibrationWindow.tsx`: Anzeige der Gütezahlen und Warnhinweise

Doku (nur im Umsetzungsschritt C, nicht in diesem Plan-Commit):

- `doc/projektdoku/arbeitsplaene/handeye-schachbrett-automatik.md`: Brett korrigieren (6×7 Innenecken)
- `doc/projektdoku/vision-server-interface.md`: Abschnitt 15, neue Status- und Ergebnisfelder

## Schnittstellen

Stand nach Umsetzung: Pi `c494f71` (Branch `apriltag`), Frontend `6e19c66` und `f5c81ba` (webskillcomposition, Branch `handeye-genauigkeit`). Der frühere Entwurf ist ersetzt; alles hier wurde gegen den Code geprüft.

### OPC UA

Keine neuen Knoten oder Methoden. Geändert ist nur das Verhalten von `FinishHandEyeCalibration` und der Inhalt von `HandEyeStatus`. `StartHandEyeCalibration`, `CaptureHandEyeSample`, `AbortHandEyeCalibration` und die Sperren bleiben wie in `handeye-ueber-opcua.md` und Abschnitt 15 der `vision-server-interface.md`.

- `FinishHandEyeCalibration() -> (Error: Int32)`:
  - `INVALID_STATE`, wenn mindestens 3 Aufnahmen angenommen sind, deren Flanschposen um weniger als 3 Drehachsen gehen (`REQUIRED_AXES = 3`; eine Achse zählt ab 5°, `rotation_diversity`). Die Sitzung bleibt im Zustand `capturing` offen, im Status steht der Grund, der Datensatz wird mit `outcome: "refused"` geschrieben.
  - `INVALID_STATE` auch bei weniger als 3 verwertbaren Posen (der Solver wirft `ValueError`). Dann bleibt die Sitzung offen, der Datensatz hat `outcome: "failed"`.
  - `INTERNAL`, wenn die Kalibrierung berechnet, aber nicht gespeichert werden konnte. Die Sitzung geht dann auf `failed`.
  - `INVALID_STATE` auch, wenn die Sitzung nicht im Zustand `capturing` ist oder gerade gemessen wird.
  - Bei Erfolg: `OK`, Zustand `finished`, Datensatz mit `outcome: "finished"`.

### Python

**`src/tagloc/handeye.py`** (Verfeinerung gegen die Brettecken, nur Brett)

```python
refine_hand_eye(
    poses_base_flange: Sequence[Pose],      # T_base_flansch je Aufnahme
    corners_px: Sequence[np.ndarray],       # je Aufnahme (N, 2), entzerrt (P = K)
    object_points: np.ndarray,              # (N, 3), Brett-KS, Nennmaß
    camera_matrix: np.ndarray,              # (3, 3)
    *,
    initial_flange_cam: Pose,               # Start X = T_flansch_cam (Park-Martin)
    initial_base_board: Pose,               # Start T_base_brett (estimate_base_board)
    initial_scale: float = 1.0,
    estimate_scale: bool = True,            # Brettmaßstab mitschätzen
    huber_delta_px: float = 1.0,
    max_iterations: int = 60,
) -> HandEyeRefinement
```

- `HandEyeRefinement` (frozen dataclass): `pose_flange_cam` (X), `pose_base_board` (Brett-Ursprung = erste Innenecke, +z ins Brett), `board_scale` (1,0 = Feldgröße wie angegeben; bei `estimate_scale=False` der Startwert), `reprojection_rms_px` (RMS des 2D-Abstands über alle Ecken), `iterations`, `converged`, `sample_rms_px` (je Aufnahme RMS in px).
- Levenberg-Marquardt mit numerischer Jacobi-Matrix in numpy, Huber-Gewichtung (IRLS). Unbekannt sind X (6), T_base_brett (6) und optional s (1).
- `ValueError`: weniger als 3 Aufnahmen; Posen- und Eckensätze verschieden lang; Eckenzahl passt nicht zu `object_points`.

```python
refine_hand_eye_robust(
    poses_base_flange: Sequence[Pose],
    corners_px: Sequence[np.ndarray],
    poses_cam_board: Sequence[Pose],        # T_cam_brett je Aufnahme
    object_points: np.ndarray,
    camera_matrix: np.ndarray,
    *,
    estimate_scale: bool = True,
    reject: bool = True,
    huber_delta_px: float = 1.0,
) -> tuple[HandEyeRefinement, tuple[int, ...], HandEyeSolution]
```

- Liefert `(Verfeinerung der verbliebenen Aufnahmen, Indizes der verworfenen Eingabe-Aufnahmen in der Reihenfolge des Verwerfens, Park-Martin-Start als HandEyeSolution)`.
- Ausreißer-Regel: Nach jeder Verfeinerung wird der RMS-Pixelfehler je Aufnahme geprüft. Schwelle: `max(1,0 px, Median + 3 · 1,4826 · MAD)`. Liegt die schlechteste darüber, fliegt sie raus, und es wird mit Warmstart neu verfeinert.
- Grenzen: höchstens 25 % der Aufnahmen (`int(n · 0,25)`); es bleiben immer mindestens 4; die verbleibenden Posen müssen noch um mindestens 3 Achsen gehen. Wird eine Grenze erreicht, endet die Schleife ohne weiteres Verwerfen.
- `ValueError` wie bei `solve_hand_eye` (siehe unten) und wie bei `refine_hand_eye`.

```python
leave_one_out_errors(
    poses_base_flange, corners_px, poses_cam_board, object_points, camera_matrix,
    full: HandEyeRefinement,                # Gesamtlösung als Warmstart
    *,
    estimate_scale: bool = True,
    huber_delta_px: float = 1.0,
) -> LeaveOneOutResult
```

- Je Aufnahme `i`: ohne `i` verfeinern, die Ecken von `i` vorhersagen.
- `LeaveOneOutResult`: `error_px` (je Aufnahme, RMS in px), `error_m` (je Aufnahme, mittlerer 3D-Abstand im Kamera-KS in m; Brettpunkte mit `s` skaliert, damit der Massstabsfehler nicht als Vorhersagefehler zählt), `rms_px`, `rms_m` (RMS über alle Aufnahmen).
- `ValueError`: weniger als 4 Aufnahmen.

Hilfsfunktionen in `tagloc.handeye`: `rotation_diversity(poses) -> (größte Drehung in Grad, Anzahl Achsen)`, `estimate_base_board(...)`, `reconstruction_spread(...)`. Konstanten: `MIN_AXES = 3`, `AXIS_THRESHOLD_DEG = 5.0`, `MIN_SAMPLES = 3`, `HUBER_DELTA_PX = 1.0`.

`solve_hand_eye` und `solve_hand_eye_robust` (Park-Martin) bleiben unverändert. `solve_hand_eye_robust` wirft `ValueError` bei zu wenigen oder zu ähnlichen Posen.

**`src/vision_server/handeye_session.py`**

- `HandEyeSession.finish() -> VisionErrorCode`: Ablauf und Codes siehe OPC UA oben. Mit Brett wird `_refine_board` verwendet (`refine_hand_eye_robust`, Leave-one-out über die verbliebenen Aufnahmen, falls mehr als 3 bleiben). Ohne Brett, oder wenn die Verfeinerung nicht möglich ist, bleibt es bei Park-Martin.
- Sitzungsports `HandEyePorts` (Auszug): `measure_board: Callable[[BoardSpec], Awaitable[tuple]] | None`, `intrinsics_source: Callable[[], str | None] | None`, `sessions_dir: Path | None` (Standard: `sessions/` neben `hand_eye_path`).

**`src/vision_server/detection/apriltag.py`, `measure_board(spec) -> (Pose | None, Grund, Info)`**

- Mittelt über `samples_per_job` frische Bilder; für `cam_flange` sind das 5 (`server.py`, Preset).
- `Info` enthält immer `reprojectionErrorPx`. Bei Erfolg zusätzlich:
  - `corners`: Ecken `(N, 2)` im verzerrten Bild, Mittel über die verwendeten Bilder,
  - `calibration`: die verwendete `CameraCalibration`,
  - `intrinsicsSource`: `"calibration"`, `"realsense_factory"`, `"placeholder"` oder `null`,
  - `image`: das letzte verwendete Bild (für `sample_NNN.png`).
- Ohne `corners`/`calibration` gibt es keine Verfeinerung, nur Park-Martin.

**`src/tagloc/cli/calibrate_handeye.py`**

```
python -m tagloc.cli.calibrate_handeye --session <ordner|session.json> [--out <datei>] [--no-scale]
python -m tagloc.cli.calibrate_handeye --samples <liste.json> --calibration <cam.json> --out <datei>   # alter Weg, ohne Verfeinerung
```

- `--session`: liest `session.json` (Schema `wsc.vision.handeye.session/1`), rechnet Park-Martin, robust, dann die Verfeinerung samt Leave-one-out neu. Ohne `--out` nur Ausgabe; mit `--out` wird die Kalibrierdatei geschrieben.
- `--no-scale`: Brettmaßstab nicht mitschätzen.
- Fehlen Ecken oder Intrinsik im Datensatz (z. B. Tag-Sitzung), rechnet die CLI nur Park-Martin.
- Ausgabe enthält eine Warnung bei `|boardScale − 1| > 0,3 %`.

**`src/tagloc/pose.py`, `_solve_tag_pose`**

- Beide IPPE_SQUARE-Lösungen werden mit `cv2.solvePnPRefineLM` verfeinert. Gewonnen hat die mit der kleineren Reprojektion.
- `ambiguity` (Verhältnis zur zweiten Lösung) wird weiter aus den **rohen** IPPE-Fehlern berechnet, damit `AMBIGUITY_THRESHOLD` (0,6) nach der Verfeinerung nicht falsch auslöst.

**Umgebungsvariable `VISION_SUBPIXEL_CORNER_REFINEMENT`** (`src/vision_server/server.py`, `subpixel_corner_settings`)

| Wert | Wirkung |
| --- | --- |
| `0`, `none`, `false` | Subpixel-Ecken aus |
| `1`, `true` | an, mit dem Verfahren des Presets |
| `apriltag` | an, Verfahren `apriltag` |
| `subpix` | an, Verfahren `subpix` |
| leer | Preset: `cam_flange` an mit `subpix`, übrige Presets aus |
| anderes | `ValueError` beim Start |

### Daten / Payload

**`HandEyeStatus`**, Schema `wsc.vision.handeye.status/1`. Neue Felder sind mit (neu) markiert, sonst unverändert.

- Top-level:
  - `intrinsicsSource` (neu): `"calibration"`, `"realsense_factory"`, `"placeholder"` oder `null` (unbekannt).
  - `samples[i]` (neu): `looErrorPx`, `looErrorM`. Nur nach `Finish` und nur für nicht verworfene Aufnahmen, sonst `null`.
- `result` (nur nach erfolgreichem `Finish`, sonst `null`):
  - bisherig: `poseFlangeCam`, `rmsPositionM`, `rmsRotationDeg`, `maxPositionM`, `maxRotationDeg`, `sampleCount`, `rejectedCount`, `rejectedSamples`, `knownTagCheckM`, `knownTagCheckDeg`, `path`, `backupPath`.
  - neu `refinedReprojectionRmsPx`: RMS der gemeinsamen Verfeinerung in px. `null` ohne Brett oder ohne Verfeinerung.
  - neu `boardScale`: Brettmaßstab gegen Nennmaß (1,0 = richtig). `null` ohne Verfeinerung.
  - neu `looErrorRmsPx`, `looErrorRmsM`: RMS des Leave-one-out-Vorhersagefehlers, px bzw. m (Kamera-KS). `null`, wenn weniger als 4 Aufnahmen verbleiben.
  - neu `refinementConverged`: `true` / `false` / `null`.
  - neu `targetInBase`: `{position: [x, y, z], orientation: [x, y, z, w]}` in m, Basis-KS. Mit Brett der Brettursprung (erste Innenecke, +z ins Brett) aus der Verfeinerung, sonst das Mittel von `F · X · C`. Im Tag-Modus das Tag. Also **nicht** `null` ohne Verfeinerung.
  - neu `warnings`: Liste deutscher Texte (Pi-Wortlaut mit ASCII-Ersatz wie „--“). Möglich sind:
    - Intrinsik `realsense_factory`: „Intrinsik: Werkswerte der Kamera, keine eigene Kalibrierung -- Massstab und Verzeichnung koennen abweichen; Kamera neu kalibrieren.“
    - Intrinsik `placeholder`: „Intrinsik: Platzhalter, die Posen sind nicht massshaltig.“
    - keine Verfeinerung möglich: „Keine gemeinsame Verfeinerung gegen die Bildpunkte moeglich -- Ergebnis nur aus Park-Martin.“
    - `|boardScale − 1| > 0,3 %`: „Brettmassstab weicht X,X % vom Nennmass ab -- Brett nachmessen / Intrinsik pruefen.“
    - Die Liste ist leer, wenn nichts zu melden ist.
  - neu `sessionPath`: Ordner des Datensatzes, oder `null`, wenn er nicht geschrieben werden konnte.
- `current` (neu, zusätzlich zu den bisherigen Feldern): `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`, `intrinsicsSource`. Jeweils aus `cam_flange.json`, `null` wenn nicht gespeichert.

**`data/handeye/cam_flange.json`** (Schema `wsc.vision.handeye/1`): bisherige Schlüssel unverändert. Zusätzlich, nur wenn vorhanden: `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`, `intrinsicsSource`. Vor dem Überschreiben wird die alte Datei als `cam_flange.json.<UTC>.bak` gesichert.

**Sitzungsdatensatz** `data/handeye/sessions/<UTC-Start>/` (`<UTC-Start>` = `YYYYMMDDTHHMMSSZ`, Zeit des `Start`):

- `session.json`, Schema `wsc.vision.handeye.session/1`:
  - `outcome`: `"finished"`, `"refused"` (zu wenige Drehachsen) oder `"failed"` (Solver-Fehler, Speicherfehler).
  - `createdAt`, `frameId`, `tagId`, `tagInBase`, `board` (`type`, `cols`, `rows`, `squareSizeM`), `intrinsicsSource`.
  - `intrinsics`: `cameraMatrix`, `distortionCoefficients`, `distortionModel`, `imageSize`, `calibrationId`, `frameId`; `null` ohne Kalibrierung.
  - `samples[]`: `index`, `accepted`, `detail`, `flangeInBase`, `camToTarget`, `reprojectionErrorPx`, `cornersPx` (N × 2 im verzerrten Bild), `image` (Dateiname oder `null`).
  - `result`: wie im Status.
- `sample_NNN.png` je Aufnahme mit Bild (Nummer = `index`, dreistellig).
- Ein zweiter Versuch derselben Sitzung überschreibt den Ordner.

**Pose-Format** überall: `{"position": [x, y, z], "orientation": [x, y, z, w]}`, Meter und Quaternion `xyzw`.

### Netz / Betrieb

Keine neuen Ports, Services oder mDNS-Namen. Die Datensätze liegen auf dem Hand-Pi unter `data/handeye/sessions/`, `data/` ist nicht versioniert. Der Offline-Lauf (`--session`) braucht nur das Repo und numpy/OpenCV, kein Netz.

### Frontend (webskillcomposition, Branch `handeye-genauigkeit`)

Nur zur Orientierung, nicht Teil des Pi-Vertrags:

- **Ankunft** (`robotDrive.ts`): erst nach 3 ruhigen Lesungen in Folge (`ARRIVAL_STILL_READS = 3`, Abstand 250 ms). Vorher reichte eine.
- **Stillstand bei der Aufnahme** (`HandEyeAutoPanel.tsx`, `jointSettling.ts`): 5 Gelenk-Lesungen mit 100 ms Abstand. Streuung je Gelenk höchstens `JOINT_SETTLE_TOLERANCE_RAD = 0,0002` rad, sonst Wiederholung bis zu 3 Mal (800 ms Pause). Danach wird die Pose übersprungen; der Hinweis „Nicht ruhig genug“ nennt sie.
- **Nachlesen**: nach `CaptureHandEyeSample` werden die Gelenke erneut gelesen. Hat sich ein Gelenk um mehr als 0,0002 rad bewegt, wird die Aufnahme **einmal** wiederholt. Die verdächtige Aufnahme bleibt auf dem Pi (der Pi hat keinen Rückzug), der Hinweis nennt ihre Nummer.
- **Brettursprung anfahren** (`HandEyeAutoPanel.tsx`, Logik in `handEyeBoardOrigin.ts`): nach der Kalibrierung zweistufig, zuerst 60 mm, dann 10 mm vor die erste Innenecke entlang der Brett-Normalen (+z ins Brett, Werkzeug zeigt ins Brett). Werkzeugspitze = Flansch + 0,1034 m (URDF, sonst Standardwert). Der Bediener misst den Abstand der Spitze zur Ecke.
- **Anzeige** (`HandEyeCalibrationWindow.tsx`): Vorhersagefehler (`looErrorRmsM`), Brettmaßstab (gelb ab 0,3 %), Hinweis zur Intrinsik (`realsense_factory`, `placeholder` und unbekannte Quellen; `calibration` gilt als gut), `result.warnings`.
- **Brettgröße** (`DEFAULT_BOARD`): 6 × 7 Innenecken, 23 mm.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| `Finish`, mindestens 3 Aufnahmen, aber weniger als 3 Drehachsen | `INVALID_STATE`, Sitzung bleibt offen, Datensatz `refused`, Grund im Status |
| `Finish`, weniger als 3 verwertbare Posen | `INVALID_STATE`, Sitzung bleibt offen, Datensatz `failed` |
| `Finish`, Speichern schlägt fehl | `INTERNAL`, Zustand `failed`, Datensatz `failed` |
| Intrinsik `realsense_factory` oder `placeholder` | `intrinsicsSource` gesetzt, Warnung in `result.warnings` |
| `|boardScale − 1| > 0,3 %` | Warnung „Brettmassstab weicht …“ |
| Ausreißer-Aufnahme (Pixelfehler über Schwelle) | verworfen, Index in `result.rejectedSamples`, Verfeinerung ohne sie neu gerechnet |
| Keine Ecken oder Intrinsik in der Aufnahme | Park-Martin, Warnung „Keine gemeinsame Verfeinerung …“ |
| Verfeinerung wirft unerwartet | Park-Martin, Fehler ins Log (Kalibrierung scheitert nicht) |
| Brett nicht vollständig im Bild | Aufnahme verworfen (`DETECTION_FAILED`), Sitzung läuft weiter |
| Gelenke nach Aufnahme bewegt | Aufnahme einmal wiederholt (Frontend); die erste bleibt auf dem Pi |
| `refine_hand_eye` mit weniger als 3 Aufnahmen | `ValueError` |
| `leave_one_out_errors` mit weniger als 4 Aufnahmen | `ValueError` (im Server: kein LOO, Feld `null`) |
| `VISION_SUBPIXEL_CORNER_REFINEMENT` unbekannt | `ValueError` beim Start |

## Vorgehen

Umsetzung in zwei Teilen, parallel bearbeitet, je von einem Umsetzungsagenten. Tests schreibt und prüft der Hauptagent.

**A — Hand-Pi (Branch `apriltag`):**

1. Eckpunkte je Aufnahme speichern (`HandEyeSession.capture`): Brettecken als Mittel über 5 frische Frames, verwendete Intrinsik. Sitzung als Datensatz nach `data/handeye/sessions/<UTC-Zeit>/`.
2. Gemeinsame Verfeinerung `refine_hand_eye` in `src/tagloc/handeye.py`: Start = Ergebnis von `solve_hand_eye_robust`; Levenberg-Marquardt in numpy (kein scipy auf dem Pi) über `X` (6), `T_base_board` (6) und optional Maßstab `s` (1). Residuen: Reprojektion aller Brettecken aller Aufnahmen in Pixeln, ohne Verzerrung auf entzerrten Punkten wie in `boards.py`. Huber-Gewichtung. Wiederverwendet werden `compose`, `invert`, `rvec_from_rotation`, `average_poses` aus `src/tagloc/geometry.py`.
3. Leave-one-out-Vorhersagefehler: `X` ohne Aufnahme `i` verfeinern, Ecken in `i` vorhersagen → `looErrorPx` und `looErrorM` je Aufnahme, RMS im Ergebnis.
4. Status und Prüfungen: `intrinsicsSource` im Status; Hinweis bei `realsense_factory`; Hinweis bei `|boardScale − 1| > 0,3 %`; `finish` verweigert bei weniger als 3 Drehachsen.
5. Tag-Pfad: beide IPPE_SQUARE-Lösungen in `src/tagloc/pose.py` mit `cv2.solvePnPRefineLM` verfeinern, die kleinere Reprojektion gewinnt (Muster aus `boards.py`, `estimate_board_pose`). Für `cam_flange` Subpixel-Ecken standardmäßig an (`detector.py`).

**B — Frontend (webskillcomposition, Branch `handeye-genauigkeit`):**

1. Stillstand: Ankunft nach 3 ruhigen Polls (`robotDrive.ts`, `HandEyeAutoPanel.tsx`). `settledJoints` verwirft Posen bei Streuung über 0,0002 rad. Nach `CaptureHandEyeSample` Gelenke nachlesen; bei Abweichung Aufnahme verwerfen und wiederholen.
2. Anzeige: `looErrorMm`-Ersatz `looErrorRmsM`, `refinedReprojectionRmsPx`, `boardScale`, `intrinsicsSource` mit Warnhinweisen (`handEye.ts`, `HandEyeCalibrationWindow.tsx`).
3. Prüfung „Brettursprung anfahren“ in `HandEyeAutoPanel`: nach `Finish` 10 mm über den `targetInBase`-Ursprung (Brett-Normale) fahren, über bestehendes `driver.moveFlangeTo` / `planMove`. Der Bediener misst den Versatz. Damit lässt sich der Hand-Auge-Fehler vom Tag-Messfehler trennen.

**Befunde, die den Plan begründen:**

1. Translation aus fixer Rotation (`solve_hand_eye`): Park-Martin für R, danach ungewichtetes `lstsq` für t. Ein Rotationsfehler von 0,1° ergibt etwa 0,5–1 mm Translationsfehler. Es gibt keine gemeinsame Verfeinerung gegen die Pixelmessung.
2. Maßstab nicht geprüft: Feldgröße des Bretts (23 mm nominal) und Intrinsik (noch vom alten Brett 7×9/22 mm; stiller Rückfall auf Werks-Intrinsik möglich) skalieren alle `t_C` gleich und erzeugen einen Bias in `t_X`. Mit bekannten Roboter-Translationen ist der Maßstab beobachtbar und kann mitgeschätzt werden.
3. Tag-Pfad unverfeinert: `CORNER_REFINE_NONE` (`profiles.py`, `subpixel_corner_refinement=False`), IPPE_SQUARE ohne `solvePnPRefineLM`, erste Lösung (`src/tagloc/pose.py`). Der Brett-Pfad macht es bereits richtig.
4. Gütezahl ist nicht Genauigkeit: Konsistenz statt Vorhersagefehler; kein Wert, der einen Versatz zeigt; `intrinsicsSource` fehlt im Status.
5. Kleinere Punkte: Stillstand = 1 ruhiger Poll (`robotDrive.ts:343`); Gelenke nach der Aufnahme nicht nachgelesen; `Finish` erzwingt keine 3 Drehachsen; `handeye-schachbrett-automatik.md` nennt falsches Brett (5×6 Innenecken).

## Verifikation

**Pi:**

- `tests/test_handeye_refine.py` (neu): synthetische Ecken mit 0,3 px Rauschen → verfeinertes `X` genauer als Park-Martin. Eingebauter Maßstabsfehler von 1 % → `boardScale` ≈ 1,01, und der Translationsbias von `X` verschwindet. Ausreißer-Aufnahme → Huber hält. LOO-Fehler plausibel.
- `tests/test_tag_pipeline.py`: gerenderter Tag mit LM genauer als ohne.
- `tests/test_handeye_session.py`: weniger als 3 Achsen wird verweigert; `intrinsicsSource` im Status.
- Ausführung: `PYTHONPATH=src python -m unittest discover -s tests -t .`
- Lauf 28.10.2026 mit der Projekt-venv (`.venv/bin/python`): 865 Tests OK. Frontend-Tests in diesem Lauf nicht geprüft.

**Frontend:**

- vitest für Stillstand, Nachlesen der Gelenke und den Status-Parser.
- `npm test` und `npx tsc --noEmit`.

**Am Aufbau (Nutzer):**

- Kalibrierfahrt → `looErrorRmsM` und `boardScale` ablesen.
- „Brettursprung anfahren“ und Tag-Anfahren vorher und nachher messen. Ziel: Versatz ≤ 1 mm.

## Ergebnisse (synthetisch, 28.10.2026)

Simulation mit 640 × 480 Pixeln, fx = 466, 15 Aufnahmen, 30 Zufallsseeds. Die Zahlen sind Mediane und 90-%-Perzentile (p90) über die Seeds, nicht am Aufbau gemessen. Ein Auswertungsskript liegt nicht im Repo; `tools/make_synthetic_scene.py` erzeugt nur die Szenen.

- **Translation `t_X` (Hand-Auge), Park-Martin gegen verfeinert:**
  - Park-Martin: Median 1,55 mm, p90 2,3 mm.
  - Verfeinert bei 0,3 px Rauschen: Median 0,20 mm, p90 0,6 mm.
- **Brettmaßstab 1 % zu groß:**
  - Park-Martin: 4,8 mm Versatz in `t_X`.
  - Verfeinert mit Maßstabsschätzung: 0,20 mm, Maßstab auf 0,06 % genau.
- **Brett korrekt, Maßstabsschätzung eingeschaltet:** kostet etwas Genauigkeit (p90 0,25 mm → 0,6 mm). Deshalb ist `--no-scale` vorhanden, wenn das Brett nachweislich stimmt.
- **Tag-Ecken gerendert** (Median der Reprojektion): `none` 0,83 px, `apriltag` 0,70 px, `subpix` 0,35 px.
- **Tag-Pose mit `solvePnPRefineLM`** (frontal): Flip-Anteil 9,8 % → 0 %, Rotationsfehler 2,3° → 1,3° (Reprojektion 0,3 px).
- **Warum die Ausreißerprüfung nach der Verfeinerung läuft:** Die alte Prüfung auf der Park-Martin-Streuung verwarf bei einem Maßstabsfehler gute Aufnahmen, weil der Maßstab die Streuung aufbläht.

Die Zahlen sagen nichts über den festen Versatz am echten FR3 aus; das klärt nur die Messung am Aufbau (siehe Offene Fragen).

## Offene Fragen

- **Messung am Aufbau steht aus.** Ob der feste Versatz unter 1 mm liegt, ist noch nicht gemessen. Ablauf: Kalibrierfahrt; `boardScale` ablesen (Abweichung über 0,3 %: Brett nachmessen oder Intrinsik neu kalibrieren); `looErrorRmsM` ablesen; „Brettursprung anfahren“ und Tag-Anfahren vorher und nachher messen.
- **Pi-Stand:** Der Pi braucht den Stand `c494f71` auf `apriltag`. Ohne ihn fehlen die neuen Status-Felder, und das Frontend zeigt keine Vorhersagefehler.
- **Status bleibt `in Arbeit`**, bis die Messung am Aufbau vorliegt.
- Brettgröße: Nachträge stehen in `handeye-schachbrett-automatik.md` (Ende) und in `vision-server-interface.md`, Abschnitt 15. Die älteren Angaben dort (6×7 **Felder**, Beispiel `cols 5 / rows 6`) bleiben unverändert stehen.

## Abweichungen vom Plan

- **Python-API:** `refine_hand_eye` nimmt Posen, Eckensätze, Brettpunkte und K direkt entgegen und liefert `HandEyeRefinement`, nicht `HandEyeSample`/`RefineResult`. Die Ausreißerprüfung sitzt in `refine_hand_eye_robust` (Pixelfehler je Aufnahme, siehe Schnittstellen).
- **`HandEyeSession.finish()`** gibt `VisionErrorCode` zurück, nicht ein `HandEyeResult`. Das Ergebnis steht im Status unter `result`.
- **Drehachsen-Prüfung** greift nur, wenn mindestens 3 Aufnahmen angenommen sind. Darunter meldet der Solver die Lage.
- **Subpixel-Ecken** stehen nicht als Standard in `detector.py`, sondern im Preset `cam_flange` (`server.py`) und sind per `VISION_SUBPIXEL_CORNER_REFINEMENT` übersteuerbar.
- **Sitzungsdatensatz** kennt zusätzlich `outcome: "refused"` und `"failed"`. Bei `refused` und `failed` bleibt die Sitzung offen.
- **Frontend:** Der Stillstand prüft 5 Lesungen (vorher nicht festgelegt), die Wiederholung ist 3-mal. Die Brettursprung-Prüfung fährt zweistufig, 60 mm und dann 10 mm vor die Ecke, statt nur 10 mm. `looErrorMm` heißt `looErrorRmsM` (Meter).
- **Schnittstellenkommentar im Frontend:** `handEye.ts` nennt für `intrinsicsSource` den Wert `"file"`. Der Pi liefert `calibration`. Die Logik (`f5c81ba`) ist richtig, nur der Kommentar ist veraltet.
- **Tests:** Die Pi-Tests liegen in `tests/test_handeye_refine.py`, `tests/test_handeye_session_refine.py` und `tests/test_subpixel_settings.py`. `tests/test_tag_pipeline.py` und `tests/test_handeye_session.py` bestehen weiter. Frontend: `jointSettling.test.ts`, `handEyeBoardOrigin.test.ts`, `HandEyeCalibrationWindow.test.ts`, `handEye.test.ts`.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/projektdoku/` übernommen (nicht nur hier im Plan stehen lassen).
