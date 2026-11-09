# Hand-Auge-Kalibrierung mit Schachbrett-Rohdaten und Offline-Vergleich

**Status:** fertig — 09.11.2026 (ohne Hardwarefahrt)
**Verantwortlich:** Meric, Codex
**Thema:** apriltag
**Branch:** `handauge-schachbrett-offline` (dieses Repo und `webskillcomposition`)

## Ziel

Die bestehende automatische Hand-Auge-Kalibrierung wird umgebaut: Das Frontend
fährt bis zu 60 sichere, geometrisch unterschiedliche Posen an. Der Hand-Pi
speichert pro Pose das Rohbild des bereits konfigurierten Kamerastroms und den gemessenen
Roboterzustand. Die Berechnung läuft danach beliebig oft offline. Der bestehende
Button „Kalibrierung starten“ startet die neue Aufnahmefahrt; es gibt keinen
zusätzlichen Start-Button.

Das Schachbrett hat 8 × 11 Felder, also 7 × 10 innere Ecken, bei 0,023 m
Feldgröße. Die Kameraintrinsik bleibt fest. 0,5 mm sind ein Entwicklungsziel,
keine aus Reprojektion allein nachweisbare Genauigkeit.

## Bereits gelesen

- `CLAUDE.md`, `arbeitsplaene/README.md`
- `handeye-schachbrett-automatik.md`, `handeye-ueber-opcua.md`, `handeye-genauigkeit.md`
- `src/tagloc/boards.py`, `src/tagloc/handeye.py`, `src/vision_server/handeye_session.py`
- `webskillcomposition/frontend/src/features/cell-modules/model/handEyeAutoPoses.ts`

## Betroffene Dateien

- Pi: `src/vision_server/detection/apriltag/board.py`,
  `handeye_session.py`, `runner/hand_eye.py`
- Offline: `src/tagloc/handeye_offline.py`,
  `src/tagloc/cli/calibrate_handeye_offline.py`, `requirements.txt`
- Frontend: `HandEyeAutoPanel.tsx`, `handEyeAutoPoses.ts`, `handEye.ts`
- Tests: Rohdaten- und Solver-Tests; Frontend-Posen-Tests

## Schnittstellen

### OPC UA

Die bestehenden Methoden und NodeIds bleiben. `StartHandEyeCalibration`
akzeptiert bei `board` zusätzlich `captureOnly: true`. In diesem Modus
schließt `FinishHandEyeCalibration` nur den Datensatz; es berechnet und
aktiviert keine Transformation. `AbortHandEyeCalibration` erhält bereits
geschriebene Rohbilder. Der `HandEyeStatus` enthält `sessionPath`.

`CaptureHandEyeSample` erhält im Aufnahmemodus zusätzlich `poseId` im Format
`pose_NNN`, `poseGroup` (`A`–`E`) und `jointAnglesRad` (sieben Winkel). Die
Flanschpose `flangeInBase` stammt aus FK über diese gemessenen Gelenkwinkel.

### Daten

Unter `data/rohbilder/calibration/<UTC>_<ID>/` liegen `config.json`,
`poses/pose_NNN/camera.png`, `poses/pose_NNN/robot_state.json` und `results/`.
`config.json` enthält Brett, Koordinatenkonventionen, Intrinsik und Bildstrom.
`robot_state.json` enthält exakt dieselbe `poseId` wie der Ordner, die
4×4-Matrix `T_base_flange`, Gelenkwinkel, Zeitstempel, Gruppe,
Aufnahmestatus und Online-Reprojektionsfehler. Bilder werden sofort nach jeder
Aufnahme geschrieben. Offline-Ergebnisse stehen nur unter `results/`.

### Python und Betrieb

`tagloc.handeye_offline.load_observations(folder, pnp, max_pnp_rms_px)` liest
alle Originalbilder erneut und cacht Ecken sowie PnP-Diagnosen.
`solve_subset(...)` berechnet Park, Tsai und Daniilidis und verfeinert
`T_flange_camera` und `T_base_board` mit SciPy gegen die Bildpunkte.
`run_experiments(...)` speichert mehrere verteilte Teilmengen und Gruppen.
Unter `results/` liegen Detektionscache, Experiment-JSON, ein HTML-Vergleich
und bei konvergierter Gesamtlösung `candidate_handeye_<pnp>.json` im
bestehenden Hand-Auge-Dateiformat. Diese Kandidatendatei wird nicht aktiviert.

Aufruf: `python -m tagloc.cli.calibrate_handeye_offline <session>`; mit
`--pnp ippe` wird das alternative planare PnP-Verfahren untersucht.
Ergebnisse werden nicht automatisch aktiv. Keine neuen Ports oder Dienste.

## Fehlerfälle und Grenzen

- Fehlendes Rohbild, falsche Bildgröße oder unvollständiges 7×10-Raster:
  Aufnahme mit Grund ausschließen.
- Doppelte oder ungültige Pose-ID und fehlende Gelenkwinkel: Aufnahme ablehnen.
- Bei Schreibfehler `INTERNAL`; bis dahin geschriebene Daten bleiben erhalten.
- Keine echte Roboterfahrt ohne die bestehenden Sicherheits- und Bewegungsfreigaben.
- Eine unabhängige Genauigkeitsvalidierung ist nicht Teil dieses Schritts.

## Offene Prüfung an Hardware

Konfigurierten RealSense-Strom und zugehörige Intrinsikdatei gegen die
tatsächliche Auflösung prüfen; die Software kann diese Prüfung ohne Gerät
nicht durchführen.

## Stand 09.11.2026: Vergleich mit `cv2.calibrateHandEye` (OpenCV 4.14, offline)

Beide Sitzungen (`20261109T125050Z_a5c964f0` ChArUco, `20261109T125825Z_a580eaba`
Schachbrett) wurden in einer separaten Umgebung (`tools/handeye_offline_cv4/`,
OpenCV 4.14, Pis bleiben auf OpenCV 5) erneut ausgewertet. Bericht:
`docs/auswertung/calibration_cv4_vergleich.html`, Rohzahlen unter
`docs/auswertung/handeye_cv4/`.

- cv2-Park ist identisch mit `park_numpy`; Tsai/Daniilidis weichen ≤ 0,45 mm ab.
  Das fehlende `calibrateHandEye` unter OpenCV 5 hat die Ergebnisse nicht verfälscht.
- Ergebnis bleibt die Reprojektions-Verfeinerung (aus jedem Start dieselbe Lösung);
  geschlossene Verfahren nur als Startwert, ihre Translation ist bei kleinen
  Drehwinkeln schlecht bestimmt (6,7 bzw. 8,3 mm daneben).
- Der Fehler je Pose ist ein starrer Versatz und wächst mit der Aufnahmezeit;
  ein Modell mit linear wandernder Brettlage erklärt in Sitzung 1 einen großen Teil
  (Drift 4,1 mm), in Sitzung 2 wenig (2,0 mm). Ursache nicht belegt.
- 0,5 mm nicht erreichbar mit diesen Daten. Nächster Schritt (braucht Roboterfahrt,
  erst nach Freigabe): Aufnahme mit warmgefahrenem Roboter, wiederholter
  Referenzpose und gemischter Gruppenreihenfolge.
