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
`doc/auswertung/calibration_cv4_vergleich.html`, Rohzahlen unter
`doc/auswertung/handeye_cv4/`.

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

## Nachtrag 09.11.2026 (2): Sitzungen nach Aufwärmen und Kinematik als Fehlerquelle

Die beiden offenen Punkte aus dem Stand oben (warmgefahrener Roboter, gemischte Gruppenreihenfolge) sind in neuen Sitzungen umgesetzt.

### Neue Sitzungen

- `20261109T142717Z_a7d466a1` (S3, Schachbrett 7×10): 53 von 55 Posen gültig, nach 15 min Aufwärmen. Gruppen reihum gemischt, Gruppenwechsel 1,00 (kalt S1: 0,32).
- `20261109T143834Z_6852cbc7` (S4, ChArUco 11×7): 47 von 47 Posen gültig, nach 15 min Aufwärmen. Gruppen reihum gemischt, Gruppenwechsel 0,98 (kalt S2: 0,28).
- Alle vier Sitzungen mit `analyse.py` und `drift.py --session` offline nachgerechnet.
- Berichte: `doc/auswertung/handeye_warm_vergleich.html` (Generator `tools/handeye_offline_cv4/warm_report.py`), Einzelberichte `calibration_schachbrett_2.html` und `calibration_charuco_2.html`.

### Endlösungen kalt gegen warm

Translation T_flange_camera in mm (x / y / z):

- S1 kalt: −47,43 / −33,82 / 57,08; RMS 2,01 px
- S2 kalt: −47,07 / −35,21 / 57,10; RMS 1,58 px
- S3 warm: −46,79 / −35,45 / 57,28; RMS 1,89 px
- S4 warm: −47,01 / −34,93 / 56,89; RMS 1,54 px

Befunde:

- Spannweite über alle vier Sitzungen: 1,76 mm (Ausreißer S1, größtes Paar S1–S3). Paar S3–S4 (beide warm): 0,69 mm. Paar S1–S2 (beide kalt): 1,43 mm.
- Lineare Brettdrift erklärt warm nur noch 5 bis 7 % (S3 7,4 %, S4 5,2 %); kalt in S1 27 %.
- Die zweite Hälfte bleibt schlechter: RMS-Verhältnis zweite zu erster Hälfte S3 1,84, S4 2,10 (kalt S1 1,72, S2 1,37).
- In S4 bleibt nach Herausrechnen von Kippung und Abstand eine Teilkorrelation RMS–Zeit von 0,53.
- Aufwärmen allein bringt die 0,5 mm nicht. Das Ziel 0,5 mm ist weiterhin nicht belegt.

### Kinematik als Fehlerquelle (Experiment, `tools/handeye_offline_cv4/kinematik/`)

- `T_base_flange` ist exakt die nominelle Franka-Vorwärtskinematik der gemessenen Gelenkwinkel. Der feste Rest ist Rz −45° und z −0,2 mm. Die Soll/Ist-Abweichung der Gelenke aus der Wiederholgenauigkeitsmessung spielt daher keine Rolle, weil die gemessenen Winkel verwendet werden.
- Die nominellen Parameter weichen aber vom realen Roboter ab. Ein gemeinsamer Bündelausgleich mit trennbaren Kinematikparametern (Nullpunkte Gelenk 2 bis 6, Längen d3, d5, a4, a5, a7) senkt die Reprojektion in jeder zurückgehaltenen Sitzung (Leave-one-session-out):
  - S1: 2,01 → 1,28 px
  - S2: 1,58 → 1,07 px
  - S3: 1,89 → 1,37 px
  - S4: 1,54 → 1,06 px
- Geschätzte Werte im gemeinsamen Fit: Nullpunkte 0,0 / −1,8 / −4,2 / 2,0 / 4,3 mrad; Längen 0,7 / 1,6 / 1,1 / −2,8 / −3,5 mm.
- Spannweite der Hand-Auge-Translation über die Sitzungen: 1,76 mm → 1,34 mm.
- Ein überparametrisiertes Modell (alle 7 Nullpunkte und Längen) ist nicht von Hand-Auge und Brettlage trennbar und liefert unsinnige Translationen (t_X bis 122,7 mm). Es dient nur als Indiz.

### Bewertung

- Gemeinsamer Fit über alle vier Sitzungen: RMS 1,79 → 1,16 px. Die Kinematik erklärt damit rund 60 % der Fehlervarianz (1 − (1,16/1,79)² ≈ 0,58).
- Der Rest liegt bei gut 1 px, gegenüber einem PnP-Niveau von 0,25 px.

### Nächste Schritte (offen)

- Kinematik mitkalibrieren: Modell ausbauen, ggf. Durchbiegung unter Schwerkraft für Gelenk 2 und 4 berücksichtigen, Intrinsik mitschätzen.
- Alternativ lokale Kalibrierung im Arbeits-Orientierungsbereich. Die flachen Gruppen A und E schließen bereits mit 0,45 bis 0,55 px.
- Genauigkeit unabhängig messen: AprilTag aus verschiedenen Posen anfahren und die Streuung von `tagInBase` auswerten (AprilTag-Modus der Wiederholgenauigkeit).
- Für neue Aufnahmen echte Zufallsreihenfolge verwenden, damit die Kippung nicht mit der Zeit korreliert.

## Nachtrag 10.11.2026: Posegruppe F (Bildrand) für die Offline-Intrinsik

### Anlass

- Die Intrinsik der Hand-Kamera wird offline neu bestimmt (`doc/auswertung/intrinsik_20261110.md`).
- Dafür braucht es Ansichten mit dem Brett in den Bildecken und an den Bildrändern sowie starke Kippung. Die Kippung bestimmt die Brennweite.
- Bisher zielte jede Ansicht auf die Brettmitte. Die Hand-Auge-Rohaufnahme liefert damit in einem automatischen Lauf verlustfreie Bilder für Intrinsik und Hand-Auge.

### Schnittstellenänderung

Gilt ab sofort und ersetzt die Angaben zu `poseGroup` und zur Posenzahl oben.

- `CaptureHandEyeSample.poseGroup` ist `A`–`F`.
- Pi (`handeye_session.py`) und Offline-Leser (`tagloc.handeye_offline.load_observations`) nutzen die gemeinsame Konstante `tagloc.handeye_offline.POSE_GROUPS = ("A","B","C","D","E","F")`.
- Andere Werte: `INVALID_ARGUMENT` (Pi) bzw. Ausschluss der Aufnahme mit Grund „Posegruppe fehlt“ (Offline).

### Frontend (webskillcomposition)

Dateien: `handEyeAutoPoses.ts`, `HandEyeAutoPanel.tsx`.

- Gruppe F hat 16 Kandidaten.
- 8 Bildpositionen: 4 Ecken, 4 Kantenmitten. Die Brettmitte liegt bei ±0,30·Bildbreite bzw. ±0,28·Bildhöhe vom Bildzentrum.
- Je Bildposition zwei Ansichten: eine mit 30°, eine mit 45° Kippung. Eine kippt in Versatzrichtung, die andere senkrecht dazu (welche Kippung welche Richtung bekommt, wechselt von Position zu Position). 8 × 2 = 16.
- Nur beim ChArUco-Brett, da das Schachbrett vollständig im Bild sein muss.
- Mit ChArUco bis zu 76 Posen (Quote F 16), mit Schachbrett weiterhin bis zu 60.
- Gemischte Reihenfolge schließt F ein.
- Hilfsfunktion `projectBoardCentrePx` und Konstante `GUESS_FOCAL_PX = 605` (Schätzung, nur zum Zielen).

### Offline-Intrinsik

- Aufruf: `python tools/handeye_offline_cv4/intrinsik_offline.py <Sitzungsordner> [--nur-gruppe F]`. Das Skript liest `poses/*/camera.png` direkt.

### Erster Befund ohne Gruppe F

- Sitzung `20261109T143834Z_6852cbc7` (47 PNG, Kippung 1–39°) ergibt allein: fx 603,3, fy 603,5, cx 332,0, cy 244,2; k1 0,092, k2 −0,125; RMS 0,11 px.
- Das deckt sich mit den 18 Einzelaufnahmen vom 10.11. (fx 602,9, fy 603,3, cx 331,8, cy 244,2).
- Der Werksangabe fx 608,8 liegt die Messung damit etwa 1 % daneben. Gruppe F soll das mit stärkerer Kippung bestätigen.

### Commits

- ADP `6327a98` (Pi und Offline).
- Frontend `6c59c40` auf Branch `handauge-schachbrett-offline` in `webskillcomposition` (Remote archiviert, Commit liegt lokal).
