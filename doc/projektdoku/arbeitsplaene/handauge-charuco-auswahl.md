# Brettauswahl für die Hand-Auge-Rohaufnahme

**Status:** fertig — 09.11.2026 (ohne Hardwarefahrt)
**Verantwortlich:** Meric, Codex
**Thema:** apriltag
**Branch:** `handauge-schachbrett-offline` (dieses Repo und `webskillcomposition`)

## Ziel

Im bestehenden Hand-Auge-Fenster zwischen dem normalen Schachbrett und dem
ChArUco-Brett wählen. Der vorhandene Startknopf, der konfigurierte Kamerastrom,
die kalibrierte Intrinsik und das Rohdatenverzeichnis bleiben die Schnittstelle.
Die Auswahl gilt für die gesamte Aufnahme und steht in `config.json`.

## Gelesene Dokumente

- `CLAUDE.md`, `arbeitsplaene/README.md`
- `handauge-schachbrett-rohdaten-offline.md`
- `vision-server-interface.md`, Abschnitt 15

## Betroffene Dateien

- Pi: `src/tagloc/boards.py`, `src/tagloc/handeye_offline.py`,
  `src/vision_server/handeye_session.py`, `src/vision_server/detection/apriltag/board.py`
- Frontend: `HandEyeAutoPanel.tsx`, `handEye.ts`, `boardCentring.ts`,
  `measureBoardCall.ts`
- Tests und Dokumentation der Brettauswahl

## Schnittstellen

`StartHandEyeCalibration` und `MeasureBoard` akzeptieren im Feld `board`
weiterhin `{"type":"chessboard","cols":7,"rows":10,"squareSizeM":0.023}`.
Für ChArUco gilt
`{"type":"charuco","cols":11,"rows":7,"squareSizeM":0.025,"markerSizeM":0.0185,"dictionary":"DICT_5X5_100"}`.
Bei ChArUco zählen `cols` und `rows` die Felder, beim Schachbrett die Innenecken.
Der Pi verwendet `CharucoDetector.detectBoard()` und `board.matchImagePoints()`.
Online werden mindestens 25 erkannte Ecken verlangt. Offline werden die
Bildpunkte je Pose mit den jeweiligen IDs und 3D-Punkten gespeichert; die
Reprojektion verwendet diese poseabhängigen Korrespondenzen.

`data/rohbilder/calibration/<Sitzung>/config.json` speichert den gewählten
Bretttyp und seine Maße. Bilder und `robot_state.json` bleiben unter derselben
`pose_NNN`-ID. Es gibt keine zusätzliche OPC-UA-Methode oder Taste.

## Fehlerfälle und offene Prüfung

- Weniger als 25 ChArUco-Ecken, fehlende IDs oder zu großer PnP-Fehler:
  Aufnahme beziehungsweise Offline-Pose mit Grund verwerfen.
- Mit dem aktuellen Livebild und der vorgegebenen Brettdefinition wurden
  60 von 60 Innenecken erkannt. Hardwarefahrt und Genauigkeitsprüfung stehen aus.

## Umsetzung und Prüfung

Die Auswahl ist im bestehenden Kalibrierfenster eingebaut. Online-Prüfung und
`MeasureBoard` akzeptieren ChArUco, die Offline-Auswertung verarbeitet pro Pose
die tatsächlich erkannten IDs. Ein synthetisches Brett mit 60 Ecken und eine
teilweise verdeckte Aufnahme mit 30 Ecken wurden erfolgreich offline gelesen.
Die gezielten Python- und Frontend-Tests sowie der TypeScript-Typcheck liefen
erfolgreich. Die reale Aufnahmefahrt bleibt offen.
