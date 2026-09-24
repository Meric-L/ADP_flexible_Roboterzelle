# MJPEG-Livestream, Kamera-Watchdog und Welt-Tag-Map auf feature/vision-server

**Status:** fertig — 22.09.2026
**Verantwortlich:** John Glanz, `Agent: Übernahme von Branch John`
**Thema:** vision
**Branch:** feature/vision-server (übernommen von `John`)

## Ziel

Die Pis laufen auf `feature/vision-server`, drei Dinge lagen aber nur auf dem
Branch `John`: ein flüssiger Livestream direkt vom Pi in den Browser (statt
Base64 über OPC UA), ein Watchdog gegen die hängende Kamera auf Pi 1 und die
Tag-Map mit dem Welt-Tag. Danach reicht auf beiden Pis ein
`git pull` auf `feature/vision-server`.

## Bereits gelesen

- `CLAUDE.md`, `doc/arbeitsplaene/README.md` (kein laufender Plan zum Thema)
- `doc/vision-server-interface.md` (Abschnitt 1, 10, 12)
- `doc/vision-system.md`
- `doc/altlasten.md`

## Betroffene Dateien

- `src/vision_server/camera.py`, `camera_stream.py`, `mjpeg_server.py` (neu),
  `profiles.py`, `address_space.py`, `runner.py`
- `tests/test_shared_camera.py` (neu), `tests/test_mjpeg_server.py` (neu),
  `tests/test_camera_stream.py`
- `config/tagmap.json` (neu)
- `doc/vision-server-interface.md` (Adressraum, Abschnitt 10, 10.2), `doc/vision-system.md`

## Schnittstellen

### OPC UA

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| Port des MJPEG-Streams | `ns=<vision>;s=VisionMachine.CameraStreamHttpPort` | Int32 | Lesen/Abo | Port auf dem Pi; `0`, solange kein HTTP-Server lauscht (aus, Port belegt, Server startet noch). Fehlt der Knoten, ist der Pi älter als diese Änderung |

Unverändert: `LatestCameraFrame` bleibt der Rückfallweg (höchstens
`stream_fps`), `CameraStreamMode` wählt weiter das Overlay — für beide Wege
gleich.

### Netz / Betrieb

| | Wert |
| --- | --- |
| Stream | `GET http://<pi>:<port>/stream.mjpg` — `multipart/x-mixed-replace; boundary=frame`, spielt in jedem `<img>` |
| Einzelbild | `GET http://<pi>:<port>/snapshot.jpg` — neuestes JPEG, `503`, solange keins da ist |
| Port | `CameraStreamConfig.http_port`, Standard `8080`; `0` schaltet ab |
| Rate | `http_fps` 15 mit mindestens einem Zuschauer, sonst `stream_fps` 5; Aufnahme `capture_fps` 15 |
| Abhängigkeiten | keine neuen (nur `asyncio.start_server`) |

Das Frontend baut die URL aus der Adresse, unter der es den OPC-UA-Server
erreicht, und dem Port aus dem Knoten. Port 8080 muss vom Browser-Rechner
aus erreichbar sein.

### Konfiguration (`CameraStreamConfig`, Watchdog)

| Feld | Standard | Bedeutung |
| --- | --- | --- |
| `frame_timeout_s` | 3.0 | so lange darf eine einzelne Aufnahme dauern, danach gilt die Kamera als hängend |
| `max_capture_failures` | 3 | Fehler in Folge bis zum Neu-Öffnen; ein Hänger zählt sofort voll |
| `max_reopen_attempts` | 2 | Neu-Öffnungen ohne ein Bild dazwischen, danach beendet sich der Prozess hart und systemd startet neu |
| `stale_frame_s` | 2.0 | älter ist ein Frame kein Live-Bild mehr: der Knoten wird einmal geleert, MJPEG-Zuschauer bekommen nichts Altes |
| `overlay_timeout_s` | 2.0 | hängt das Overlay länger, geht das Rohbild raus |

Gilt für alle Kamera-Backends gleich (Picamera2, RealSense, OpenCV), weil der
Watchdog im gemeinsamen Capture-Loop sitzt.

### Daten

`config/tagmap.json`: nur Tag 0 als Welt-Tag (`role: world`, `sizeM` 0,1,
Ursprung des Welt-KS). Modul-Tags stehen **bewusst nicht** darin — der Pi
meldet sie als `TAG-<id>` mit Weltpose, die Zuordnung Tag → Modul samt
Versatz macht das Frontend (WSC `features/cell-modules/config/moduleCatalog.ts`).

### Fehlerfälle

- Port belegt: Server startet ohne HTTP-Stream, Knoten bleibt `0`, Frontend
  fällt auf `LatestCameraFrame` zurück.
- Kamera hängt: Neu-Öffnen, notfalls Prozessneustart durch systemd; während
  des Hängers kein veraltetes Bild auf keinem der beiden Wege.
- Zu viele Zuschauer: weitere Verbindungen bekommen `503` (Limit in
  `mjpeg_server.py`); unbekannter Pfad `404`.

## Abweichungen vom Plan

- Die Fernkalibrierung von `John` (`VisionMachine.Calibration.*`) ist **nicht**
  übernommen — auf diesem Branch gilt die eigene Kalibrierung
  (`StartCalibration` / `CaptureCalibrationSample` / `FinishCalibration` /
  `AbortCalibration`, `CalibrationProgress`, Abschnitt 12 der
  Schnittstellendoku). Offen: der Kalibrierdialog im WSC-Frontend
  (`Ungetestet`) spricht noch die `John`-Schnittstelle an und muss auf diese
  umgestellt werden.
- `SharedCamera.is_open` kam mit, weil die Watchdog-Tests es brauchen.
- `max_stream_width` (dieser Branch) und die MJPEG-Kodierung (`John`)
  zusammengeführt: verkleinert wird einmal beim Kodieren, beide Wege bekommen
  dasselbe Bild. Der Kalibrier-Fortschritt wird im Knotentakt geschrieben.

## Nach Abschluss

- [x] Status `fertig`, Schnittstellen wie umgesetzt
- [x] Zeile im Index
- [x] Fachwissen in `doc/vision-server-interface.md` (Abschnitt 10.2) und
  `doc/vision-system.md`
