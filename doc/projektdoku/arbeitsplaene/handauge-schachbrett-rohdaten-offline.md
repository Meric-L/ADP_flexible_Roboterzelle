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

## Nachtrag 11.11.2026: Gesamtkalibrierung auf dem Pi (`fullCalibration`)

1. **Gesamtkalibrierung:** `StartHandEyeCalibration` mit `fullCalibration: true` nimmt Rohbilder auf wie `captureOnly`. `FinishHandEyeCalibration` rechnet auf dem Pi die Intrinsik (`cv2.calibrateCamera`, volles Modell) und danach das Hand-Auge (Park, dann gemeinsame Reprojektions-Verfeinerung mit scipy). Beide Ergebnisse werden mit `.bak`-Sicherung zusammen nach `data/calibration/cam_flange.json` und `data/handeye/cam_flange.json` geschrieben und live übernommen. Scheitert das Schreiben, werden beide wiederhergestellt. Details: [vision-server-interface.md](../vision-server-interface.md), Abschnitt 15.6.
2. **Brettmaße frei:** Schachbrett und ChArUco haben frei eintragbare Felder, Maße und Dictionary (Abschnitt 15.7 der Schnittstellendoku). Das Labor-Brett wird mit den nachgemessenen Werten (Feld 24,982 mm, Marker 18,487 mm) ohne Skalierungsfaktor verwendet. Das Frontend (`webskillcomposition`, Branch `ungetestet-aufraeumen`) merkt sich die Angaben im Browser.
3. **Status:** `HandEyeStatus` hat das Feld `fullCalibration`. Bei `fullCalibration` steht in `result` `kind: "intrinsicsAndHandEye"` mit Intrinsik, Hand-Auge, `excluded` und `backups`.
4. **Abhängigkeiten:** `requirements.txt` nennt `opencv-contrib-python>=4.8.0` ohne Obergrenze `<5`, weil die Pis mit OpenCV 5 laufen. `scipy>=1.11` wird auf dem Pi gebraucht; fehlt es, lautet die Meldung „scipy fehlt auf dem Pi (pip install scipy)“.

Hinweis: Die Offline-Skripte unter `tools/handeye_offline_cv4/` sind auf diesem Branch nicht übernommen. Sie liegen auf `handauge-schachbrett-offline`.

Bewusst nicht verwendet werden die Kinematik-Korrektur der Flanschpose und die Werks-Intrinsik. Die Flanschpose ist die nominelle FK aus den gemessenen Gelenkwinkeln. Die Kinematik-Korrektur hat die 1080p-Sitzung verschlechtert: mit Korrektur 4,78 px statt 3,63 px ohne.
