# Code-Dokumentation: Funktionen, Schnittstellen, Verbindungen

Stand: 15.11.2026.

Diese Seite ist der Einstieg in den Code. Sie zeigt, welche Teile es gibt, wie sie über OPC UA
verbunden sind und welche Funktionen und Schnittstellen man kennen muss. Die Details stehen
in den verlinkten Fach-MDs. Jede einzelne Datei beschreibt [`code-handbuch.md`](code-handbuch.md).

- **Docstrings und Kommentare im Code sind englisch.** Format: PEP 257 und Google-Style
  (`Args:`, `Returns:`, `Raises:`).
- **Meldungen bleiben deutsch.** Log-, Fehler- und OPC-UA-Texte sind für Bediener gedacht und
  zum Teil in Tests geprüft.

---

## 1. Systembild

```mermaid
flowchart LR
    subgraph Zelle["Roboterzelle (LAN 10.10.38.0/24)"]
        P1["Decken-Pi (Layer 1)<br/>ADP-Roboter-Lokalisierung<br/>opc.tcp://10.10.38.104:4840/raspi/server/<br/>Kamera: picamera2, Frame cam_ceiling"]
        P2["Hand-Pi (Layer 2)<br/>ADP-HandInEye-Kamera-Pi<br/>opc.tcp://10.10.38.109:4840/raspi/server/<br/>Kamera: RealSense D415, Frame cam_flange"]
        LDS["Local Discovery Server (open62541)<br/>opc.tcp://10.10.38.27:4840/"]
        AGG["Aggregation-Server<br/>opc.tcp://10.10.38.27:48400/"]
        WSC["webskillcomposition-Backend<br/>(FastAPI + asyncua-Client)"]
        R["Roboter-Server (z. B. FR3)"]
    end
    FE["WSC-Frontend (Browser)"]

    P1 -- "RegisterServer alle 60 s" --> LDS
    P2 -- "RegisterServer alle 60 s" --> LDS
    AGG -- "führt die beim LDS angemeldeten Server" --> LDS
    WSC -- "OPC UA Client" --> P1
    WSC -- "OPC UA Client" --> P2
    WSC -- "OPC UA Client" --> R
    FE -- "WebSocket" --> WSC
    FE -- "HTTP MJPEG :8080<br/>/stream.mjpg, /snapshot.jpg" --> P2
    P1 -. "mDNS _opcua-tcp._tcp.local." .-> FE
    P2 -. "mDNS _opcua-tcp._tcp.local." .-> FE
```

- **Die Pis sind OPC-UA-Server.** Sie bauen selbst keine OPC-UA-Verbindung zu einem anderen
  Server auf. Ausnahme ist die Anmeldung beim Discovery-Server (`RegisterServer`).
- **Das WSC-Backend ist der Client.** Es verbindet sich direkt mit den festen URLs der Pis.
  Diese stehen in webskillcomposition unter
  `frontend/src/features/opcua-server/config/piServers.ts`
  (`PI_1_SERVER_URL` = Decken-Pi, `PI_2_SERVER_URL` = Hand-Pi).
- **Den Roboter steuert nur das Backend.** Die Pis kennen den Roboter nur über die Posen, die
  ihnen das Backend übergibt (Layer 2, Hand-Auge-Aufnahmen).
- **Diagnose-Clients** im Repo (`src/vision_server/tools/`): `handshake_client.py`,
  `calibration_client.py`, `stream_viewer.py`, `diagnose_board.py`. Sie verbinden sich mit
  `asyncua.Client` direkt mit einem Pi.

## 2. Wie der OPC-UA-Server erreichbar ist

| | Wert | Code |
| --- | --- | --- |
| Endpoint (Betrieb) | `opc.tcp://0.0.0.0:4840/raspi/server/`. Er lauscht auf allen Schnittstellen und kündigt die LAN-IPv4 an. | `vision_server/server.py` (`ENDPOINT`, `vision_config()`) |
| Start (Betrieb) | `python3 -m vision_server.server`, auf dem Pi als systemd-Dienst `opcua-server.service` | `vision_server/server.py` |
| Start (Entwicklung) | `python3 -m vision_server`, Port 4841, Pfad `/vision/machine/` | `vision_server/__main__.py` |
| Security | `NoSecurity`, ohne Anmeldung | |
| Namespace Vision | `http://launch-rm.de/vision`. Den Index immer per `get_namespace_index(uri)` auflösen. | `vision_server/config.py` |
| LDS-Anmeldung | `opc.tcp://10.10.38.27:4840/`, Erneuerung alle 60 s. Ist der LDS nicht erreichbar, gibt es nur eine Warnung, der Start scheitert nicht. | `vision_server/discovery/lds.py` |
| mDNS | Dienst `_opcua-tcp._tcp.local.`, TXT `path`, `caps`. Ohne `zeroconf` läuft der Server trotzdem. | `vision_server/discovery/mdns.py` |
| MJPEG-Livebild | HTTP-Port aus `CameraStreamHttpPort` (Standard 8080), `/stream.mjpg` und `/snapshot.jpg` | `vision_server/mjpeg_server.py` |

**Warum LDS und mDNS:** Der Aggregation-Server nimmt nur Server auf, die beim LDS angemeldet
sind. Eine mDNS-Ankündigung allein reicht nicht (gemessen am 21.09.2026). mDNS bleibt für
Clients im Subnetz.

**Umgebungsvariablen** (Auswahl, alle in `vision_server/server.py` bzw. `discovery/`):

| Variable | Wirkung |
| --- | --- |
| `OPCUA_LDS_URL` | Andere LDS-URL; `""` schaltet die Anmeldung ab |
| `OPCUA_MDNS` | `0`/`false`/`off` schaltet die mDNS-Ankündigung ab |
| `OPCUA_MDNS_NAME`, `OPCUA_APPLICATION_URI` | Name und ApplicationUri überschreiben |
| `VISION_SYSTEM_ID`, `VISION_FRAME_ID` | Identität überschreiben (sonst aus dem Hostnamen, `PI_IDENTITIES`) |
| `VISION_CAMERA_BACKEND` | `picamera2` oder `realsense` (sonst aus dem Hostnamen) |
| `VISION_DISABLE_STREAM=1` | Livebild aus |
| `VISION_SUBPIXEL_CORNER_REFINEMENT` | Subpixel-Ecken an/aus bzw. Verfahren |
| `VISION_CALIBRATION_MODEL` | Linsenmodell der Kalibrierung erzwingen |

## 3. Adressraum und Schnittstellen

Ein Server trägt einen Baum in zwei Sichten auf denselben Job:

```
Objects/
├── Machines/VisionMachine     OPC 40100 VisionSystemType  (ns=<vision>;s=VisionMachine)
└── VisionProgram              OPC UA Teil 10, Bedienoberfläche für das Frontend
```

Alle Knoten haben sprechende String-NodeIds `ns=<vision>;s=VisionMachine.<Name>`. Die
Methoden und Wertknoten sind zusätzlich unter `VisionProgram` verlinkt.

### 3.1 Methoden

| Methode | Zweck | Pi | Detail |
| --- | --- | --- | --- |
| `StartSingleJob` | Einen Job starten (`RecipeId` `apriltag`: Module lokalisieren; `calibration`: Messbereitschaft prüfen). Das Ergebnis kommt als Event und in `LatestResultJson`. | beide | [`vision-server-interface.md`](vision-server-interface.md) §4–6 |
| `Stop`, `Abort`, `StartContinuous` | Job abbrechen bzw. Dauerbetrieb | beide | §5, §7.5 |
| `StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration`, `AbortCalibration` | Interaktive Kamera-Intrinsik. Die alte Datei bleibt als `.bak` erhalten. Das Frontend nutzt sie nur noch am Decken-Pi. | beide | §12 |
| `SetTagMap` | Tag-Map der Zelle setzen | beide | §12.5a |
| `MeasureTags` | Bestimmte Tags über mehrere Bilder messen (Pose, Streuung, Blickwinkel) | Hand-Pi | §15 |
| `MeasureBoard` | Kalibrierbrett messen, z. B. zum Zentrieren | beide | §15.4 |
| `StartHandEyeCalibration`, `CaptureHandEyeSample`, `FinishHandEyeCalibration`, `AbortHandEyeCalibration` | Hand-Auge-Kalibrierung. `fullCalibration: true` rechnet Intrinsik und Hand-Auge aus derselben Rohaufnahme und sichert beide alten Dateien als `.bak`. | Hand-Pi | §15, §15.5–15.7 |
| `StartLayer2Run`, `ReportRobotPose` | Layer-2-Lauf: Der Pi plant die Ziele, das Backend fährt sie an und meldet die erreichte Flanschpose zurück | Hand-Pi | §14 |
| `MeasureTagReference`, `SetTagReferencePreview` | Tag gegen seine Bezugskanten einmessen | Hand-Pi | [`apriltag-referenz.md`](apriltag-referenz.md) |
| `StartCellWatch` | Zellüberwachung starten | Decken-Pi | §16 |
| `SaveRawImage`, `SaveUseCaseImage` | Rohbild bzw. Use-Case-Foto mit Metadaten speichern | beide | Arbeitspläne `use-case-*.md` |

### 3.2 Wertknoten (lesen bzw. abonnieren)

| Knoten | Inhalt |
| --- | --- |
| `LatestResultJson` | Letztes Job-Ergebnis als JSON (`wsc.vision.detections/1`, `vision_server/payload.py`) |
| `LatestCameraFrame`, `CameraStreamMode` (schreibbar), `CameraStreamHttpPort` | Livebild als Base64-JPEG, Overlay-Modus, Port des MJPEG-Servers |
| `CalibrationProgress`, `ActiveCalibrationInfo` | Stand der Kamerakalibrierung bzw. aktive Kalibrierung |
| `HandEyeStatus` | Stand der Hand-Auge-Sitzung und Ergebnis |
| `Layer2Target`, `Layer2Status` | Nächstes Ziel des Layer-2-Laufs bzw. Fortschritt je Schritt |
| `TagMapJson`, `TagReferencePreviewStatus`, `CellWatchStatus` | Tag-Map, Vorschau der Referenzmessung, Zellüberwachung |
| `VisionAsset/…/DeviceHealth` | Kamerazustand nach OPC 40100-2 (§11.4) |

### 3.3 Dateien auf dem Pi

| Datei | Inhalt | Schreiber |
| --- | --- | --- |
| `data/calibration/<frame>.json` | Kamera-Intrinsik (`wsc.vision.calibration/2`) | `tagloc.calibration.save_calibration` |
| `data/handeye/<frame>.json` | Hand-Auge `T_flange_cam` (`wsc.vision.handeye/1`) | `tagloc.handeye.save_hand_eye` |
| `*.json.<UTC>.bak` | Sicherungen der vorherigen Kalibrierung, nie überschrieben | `calibration_session`, `handeye_session` |
| `data/rohbilder/calibration/<UTC>_<id>/` | Rohaufnahmen einer Hand-Auge- bzw. Gesamtkalibrierung | `handeye_session` |
| `config/tagmap.json` | Tag-Map (Welt-Tags, Tag-Größen) | `SetTagMap` |

## 4. Die wichtigsten Funktionen

Posen heißen `T_a_b` bzw. `pose_a_b`: die Pose von Frame b, ausgedrückt in Frame a
(`T_base_flange` = Flansch in der Roboterbasis).

### 4.1 `tagloc`: Lokalisierung (Bibliothek, ohne OPC UA)

| Funktion / Klasse | Datei | Zweck |
| --- | --- | --- |
| `load_calibration`, `save_calibration`, `CameraCalibration` | `calibration.py` | Kamera-Intrinsik lesen und schreiben |
| `detect_board`, `calibrate_from_samples` | `boards.py` | Schachbrett/ChArUco finden, Intrinsik aus Aufnahmen rechnen |
| `TagDetector` (Protokoll), `ArucoTagDetector`, `PupilAprilTagDetector` | `detector.py` | AprilTags im Graubild finden (`detect(gray)`) |
| `estimate_tag_pose`, `estimate_tag_poses` | `pose.py` | Tag-Pose in der Kamera per PnP (IPPE) |
| `localize_camera`, `locate_modules` | `localize.py` | Kamerapose aus Welt-Tags, Modulposen aus Modul-Tags (Layer 1) |
| `localize_camera_from_anchor` | `localize.py` | Kamerapose der Handkamera aus Roboteranker und Kinematik |
| `solve_hand_eye`, `solve_hand_eye_robust`, `HandEye`, `RobotAnchor` | `handeye.py` | Hand-Auge `T_flange_cam` lösen, Roboter am Welt-Tag ankern |
| `calibrate_session` | `handeye_offline.py` | Gesamtkalibrierung aus einer Rohaufnahme: Intrinsik und Hand-Auge mit Reprojektions-Verfeinerung |
| `measure_reference_lines` | `reference_edges.py` | Tag-Lage gegen die Bezugskanten eines Moduls korrigieren |
| `load_tag_map`, `TagMap` | `tagmap.py` | Tag-Map lesen und prüfen |
| `compose`, `invert` | `geometry.py` | Posen verketten und invertieren |

### 4.2 `vision_server`: der OPC-UA-Server auf dem Pi

| Funktion / Klasse | Datei | Zweck |
| --- | --- | --- |
| `vision_config`, `main` | `server.py` | Pi-spezifische Konfiguration (Identität, Kamera, Profile), Start mit mDNS und LDS |
| `install_vision_machine` | `runner/install.py` | Baut den kompletten Adressraum und verdrahtet alle Methoden |
| `JobRunner` | `job.py` | Ein Job zur Zeit: prüfen, ausführen, Ergebnis und Fehler melden |
| `SharedCamera` | `camera.py` | Ein Aufnahme-Loop, geteilt von Erkennung, Livebild und Messungen |
| `CameraStreamPublisher`, `MjpegServer` | `camera_stream.py`, `mjpeg_server.py` | Livebild als OPC-UA-Knoten und als MJPEG über HTTP |
| `CalibrationSession` | `calibration_session.py` | Interaktive Intrinsik-Kalibrierung (Decken-Pi) |
| `HandEyeSession` | `handeye_session.py` | Hand-Auge- und Gesamtkalibrierung (Hand-Pi) |
| `Layer2Run`, `plan_run`, `plan_step` | `layer2/run.py`, `layer2/plan.py` | Layer-2-Lauf: Ziele planen, Robotermeldungen auswerten |
| `measuretag.build_result`, `measureboard.build_result` | `measuretag.py`, `measureboard.py` | Ergebnisse von `MeasureTags`/`MeasureBoard` |
| `CellWatch` | `cellwatch.py` | Zellüberwachung: bestätigte Moduländerungen melden |
| `build_result_payload`, `build_error_payload` | `payload.py` | JSON-Nutzlast der Job-Ergebnisse |
| `register` (LDS), `advertised_endpoint` | `discovery/lds.py` | Anmeldung beim Discovery-Server mit erreichbarer Adresse |

### 4.3 Weitere Pakete

| Paket | Zweck |
| --- | --- |
| `src/ua_program/` | Rudimentäre OPC-UA-Programme nach Teil 10 (Grundlage von `VisionProgram`) |
| `src/use_case_eval/` | Auswertung der Use-Case-Fotos (Ablagegenauigkeit gegen Ziel bzw. Tag) |
| `src/jobs/calibrate.py` | Job `calibration`: Messbereitschaft prüfen (Subprozess) |

## 5. Ablauf in Kürze

1. **Layer 1 (Decken-Pi):** Das Backend ruft `StartSingleJob` mit `apriltag` auf. Der Pi
   rechnet die Kamerapose aus den Welt-Tags (`localize_camera`), dann die Modulposen
   (`locate_modules`), und liefert alles als Event und in `LatestResultJson`.
2. **Layer 2 (Hand-Pi):** Das Backend fährt den Roboter. Der Hand-Pi misst je Tag
   (`MeasureTags`) bzw. plant den Lauf selbst (`StartLayer2Run` → `Layer2Target` →
   `ReportRobotPose`). Die Kamerapose kommt aus Flanschpose × Hand-Auge.
3. **Kalibrierung:**
   - Decken-Pi: `StartCalibration` … `FinishCalibration` (nur Intrinsik).
   - Hand-Pi: `StartHandEyeCalibration` mit `fullCalibration: true`. Der Roboter fährt die
     Posen ab, beim Abschluss schreibt der Pi Intrinsik und Hand-Auge, sichert die alten
     Dateien und übernimmt beide live.

## 6. Frontend-Abläufe im WebSkillComposition-Stand `ADP_Lokaliserung`

Dieser Abschnitt beschreibt ausschließlich die im Branch `ADP_Lokaliserung`
geänderten Frontend-Abläufe. Die vollständige OPC-UA-/WebSocket-Schnittstelle steht in
`doc/projektdoku/vision-server-interface.md` und im WebSkillComposition-Repository unter
`docs/data-and-interface-model.md`.

| Ablauf | Geänderte Frontend-Module | Verhalten dieses Branches |
| --- | --- | --- |
| Pi-Anbindung | WSC `frontend/src/features/opcua-server/config/piServers.ts`, `useVisionJob.ts`, `useCameraCalibration.ts`, `useHandEyeCalibration.ts` | Das Frontend ruft die beiden Vision-Pis direkt per OPC UA auf. Der Decken-Pi dient Layer 1 und Zellüberwachung; der Hand-Pi Hand-Auge, Tag-Referenzmessung und Layer 2. Namespace-Indizes werden aus Namespace-URIs aufgelöst. Der Roboterverkehr läuft über den Backend-WebSocket. |
| Modulkatalog und Tag-Map | WSC `backend/src/backend/api/module_catalog.py`, `services/module_catalog_store.py`, `frontend/src/features/cell-modules/model/catalogStore.ts`, `frontend/src/features/opcua-server/model/useTagMapSync.ts` | Der Katalog ist die Quelle für Module, Tag-Rollen, Modell-/Roboterzuordnung und Welttag-Posen. `GET/PUT /api/module-catalog` nutzt Revisionen; `409` liefert den aktuellen Stand. Welt-Tags werden je Pi über `SetTagMap` synchronisiert. Details stehen in `docs/vision-workflows.md` im WebSkillComposition-Repo. |
| Layer 1 und Lokalisierung | WSC `AutolocateLayer1Window.tsx`, `planLayer1Import.ts`, `layer1Import.ts`, `worldTagCheck.ts` | Der Decken-Pi liefert Erkennungen und Posen. Vor dem Import werden Welttag-Abweichungen gegen den Katalog geprüft; der Import ordnet erkannte Tags Modulen zu und aktualisiert Szene, Kamera- und Welttag-Beobachtungen. |
| Tag einmessen | WSC `TagReferencePanel.tsx`, `tagReferenceRun.ts`, `tagReferenceCall.ts`, `useTagReferenceLive.ts` | Die Oberfläche plant Referenzansichten, ruft `MeasureTagReference` am Hand-Pi auf, zeigt Vorschau und Messunsicherheit und übernimmt bestätigte Korrekturen in den Katalog. |
| Tag-Geometrie bearbeiten | WSC `frontend/src/tag-tool/TagToolApp.tsx`, `model/exportTagPose.ts` | Das separate Tag-Tool platziert Tags in einem URDF-Modell und exportiert Katalog-/Pi-JSON sowie einen festen URDF-Link. Es ist ein Editor, keine Kameramessung. |
| Layer 2 | `frontend/src/features/cell-modules/model/approachPlan.ts`, `ApproachPlanPanel.tsx` | Ein geeigneter Sichtpunkt je Modul; Tag-Messung aus dem geplanten Abstand (Standard 0,35 m); nicht anfahrbare oder nicht messbare Tags werden übersprungen und der Lauf fährt fort. Abbruch, nicht lesbare Gelenke oder unsichere Rückfahrt beenden den Lauf. |
| Handkamera-Gesamtkalibrierung | `frontend/src/features/cell-modules/model/handEye.ts`, `handEyeBoard.ts`, `handEyeOrder.ts`, `handEyeAutoPoses.ts`, `HandEyeAutoPanel.tsx` | Ein vom Bediener gestarteter Kalibrierablauf übermittelt Brettgeometrie und fordert `fullCalibration` an. Die berechneten Posegruppen berücksichtigen die Bildrandabdeckung bei 1920×1080. Pi-seitige Berechnung, Sicherungen und OPC-UA-Status sind in `vision-server-interface.md` beschrieben. |
| Kamerazustand | `frontend/src/features/opcua-server/model/useCameraHealth.ts`, `PiStatusList.tsx` | Der Status aus `DeviceHealth` (OPC 40100-2) erscheint getrennt von der Pi-Verbindung. Fehlt Knoten oder Wert, zeigt die Oberfläche „Zustand unbekannt“. |
| Use-Case-Foto und Modulpose | `frontend/src/features/cell-modules/model/useCaseRecord.ts`, `useCaseMeasurement.ts`, `pickPlace.ts` | Der ausgewählte Messbezug kann die Mitte des Tisch-Tags statt der Tischmitte verwenden; die gemessene Tag-Gier wird beim verfügbaren Katalog-Tag in die Modulpose übernommen. |

Weitere fachliche Festlegungen dieses Branches stehen in `arbeitsplaene/README.md`
und den dort referenzierten Arbeitsplänen, insbesondere zu Layer 2, Posegruppen und
Hand-Auge-Kalibrierung. Die konsolidierten Layer-2-Regeln liegen jetzt in
[`layer2-regeln.md`](layer2-regeln.md). Unveränderte WebSocket-Commands und OPC-UA-
Grundlagen werden hier nicht erneut beschrieben.
