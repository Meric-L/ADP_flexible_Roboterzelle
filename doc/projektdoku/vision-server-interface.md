# Vision-Server — Systemaufbau und Backend-Schnittstelle

Diese Datei beschreibt den eigenständigen OPC-UA-Vision-Server (OPC 40100,
Machine Vision) und **was ein Backend implementieren muss**, um von ihm ein
Ergebnis zu bekommen. Sie ist ohne Kenntnis dieses Repos benutzbar.

**Nur die Adressen und Signaturen gesucht?** Abschnitt 13 fasst die komplette
Schnittstelle in Tabellen zusammen.

> **Zusätzlich** gibt es ein generisches **OPC-UA-Part-10-Programm** (dieselbe
> Bedienform wie Conveyor und CardDispenser) und eine mDNS-Ankündigung. Beides ist
> additiv. Siehe [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).
>
> **Namespace-Indizes nie hartcodieren.** Der Vision-Namespace liegt aktuell auf
> **ns=6** und verschiebt sich, sobald Nodesets dazukommen. Immer über die URI
> auflösen.

Der Platzhalter-Rezept `hello-world` existiert nicht mehr; ein Start ohne `RecipeId`
wird mit `Error=4` (`UNKNOWN_RECIPE`) abgelehnt. Echte Jobs: `apriltag` (Module
lokalisieren) und `calibration` (Messbereitschaft). Der Job-Ablauf ist für beide gleich,
das Payload-Format `wsc.vision.detections/1` ist unverändert.

## 1. Systemaufbau

Ein OPC-UA-Server auf dem Raspberry Pi (systemd-Service `opcua-server.service`, Port 4840)
mit **einem** fachlichen Baum. Beide Sichten bedienen denselben `JobRunner`.

```
Raspberry Pi — opc.tcp://<pi>:4840/raspi/server/
├── Machines/          Standardordner aus OPC UA Machinery
│   └── VisionMachine  OPC 40100 VisionSystemType        <- dieses Dokument
└── VisionProgram      OPC UA Teil 10, Bedienoberfläche  <- part10-programm-schnittstelle.md
```

Der Pfad `/raspi/server/` bleibt, obwohl das Raspi-Interface entfallen ist; er steht in
mDNS, LDS und jeder Client-Konfiguration. Nicht (mehr) vorhanden sind `RaspiDevice`,
eine zweite leere Instanz `2:VisionSystem`, `CpuTemperatureResult` und der Namensraum
`http://launch-rm.de/raspi`.

Adressraum des Vision-Systems:

```
Objects/
└── Machines/                                ns=<machinery>;i=1001  (Standardordner)
    └── VisionMachine                        ns=<vision>;s=VisionMachine   (Typ: <mv>:VisionSystemType)
        ├── VisionStateMachine               Preoperational | Halted | Error | Operational
        │   └── AutomaticModeStateMachine    Initialized | Ready | SingleExecution | ContinuousExecution
        │       ├── StartSingleJob           <- Job starten
        │       ├── Stop                     <- laufenden Job abbrechen (Abschnitt 5)
        │       ├── StartContinuous          <- Dauerbetrieb (Abschnitt 7.5)
        │       ├── Abort                    <- wie Stop, ueber den Abort-Uebergang
        │       └── SimulationMode           (nicht verlinkt)
        ├── ResultManagement
        │   ├── Results/LatestResult         (Typ: <mv>:ResultType, pro Job überschrieben)
        │   │   └── ResultContent[0]         JSON-String des letzten Ergebnisses
        │   └── GetResultById | ReleaseResultHandle | ...   (nicht implementiert, 7.3)
        ├── VisionAsset                      OPC 40100-2 (Abschnitt 11)
        ├── StartCalibration                 ns=<vision>;s=VisionMachine.StartCalibration
        ├── CaptureCalibrationSample         ns=<vision>;s=VisionMachine.CaptureCalibrationSample
        ├── FinishCalibration                ns=<vision>;s=VisionMachine.FinishCalibration
        ├── AbortCalibration                 ns=<vision>;s=VisionMachine.AbortCalibration
        ├── CalibrationProgress              ns=<vision>;s=VisionMachine.CalibrationProgress
        ├── SetTagMap                        ns=<vision>;s=VisionMachine.SetTagMap
        ├── TagMapJson                       ns=<vision>;s=VisionMachine.TagMapJson
        ├── ActiveCalibrationInfo            ns=<vision>;s=VisionMachine.ActiveCalibrationInfo
        ├── LatestResultJson                 ns=<vision>;s=VisionMachine.LatestResultJson
        ├── LatestCameraFrame                ns=<vision>;s=VisionMachine.LatestCameraFrame
        ├── CameraStreamMode                 ns=<vision>;s=VisionMachine.CameraStreamMode   (beschreibbar)
        └── CameraStreamHttpPort             ns=<vision>;s=VisionMachine.CameraStreamHttpPort (Int32)
```

Die Kalibriermethoden und Wertknoten sind zusätzlich unter `VisionProgram` verlinkt
(Referenzen, keine Kopien).

Python-Paket `src/vision_server/` (Auswahl): `__main__.py` (Start:
`python3 -m vision_server.server`), `config.py` (`VisionServerConfig`, Env-Variablen),
`job.py` / `state_machine.py` / `events.py` / `result_management.py` (Job, Automaten,
Events, Ergebnis), `errors.py` (Fehlercodes), `detection/` (Strategie `DetectionSource`;
Neue Erkennung = **eine Datei in `detection/` plus Registry-Eintrag**), `camera.py`
(`SharedCamera`, ein Capture-Loop für Erkennung und Livestream), `camera_stream.py`
und `mjpeg_server.py` (Abschnitt 10), `calibration_session.py` (Abschnitt 12),
`asset_model.py` (Abschnitt 11), `profiles.py` (`AprilTagProfileConfig`,
`CameraStreamConfig`), `payload.py` (Schema), `tagloc/` (Lokalisierung, siehe
[`apriltag-referenz.md`](apriltag-referenz.md)), `discovery/` (mDNS, LDS, siehe
[`vision-system.md`](vision-system.md)).

## 2. Verbindungsdaten

| | Wert |
| --- | --- |
| Endpoint | `opc.tcp://<pi>:4840/raspi/server/` |
| Security | `NoSecurity` |
| ServerName | `Raspberry Pi OPC UA Server` |
| Namespace Vision | `http://launch-rm.de/vision` (aktuell **ns=6**) |
| Namespace 40100 | `http://opcfoundation.org/UA/MachineVision` (aktuell ns=2) |
| Namespace Machinery | `http://opcfoundation.org/UA/Machinery/` (aktuell ns=4, trägt `Machines`) |

Auflösen immer per `await client.get_namespace_index("<uri>")`.

Wichtige NodeIds (String-Ids, stabil über Neustarts):

| Knoten | NodeId |
| --- | --- |
| Vision-System | `ns=<vision>;s=VisionMachine` |
| StartSingleJob | `ns=<vision>;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob` |
| Zustand außen | `ns=<vision>;s=VisionMachine.VisionStateMachine.CurrentState` |
| Zustand innen | `ns=<vision>;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.CurrentState` |
| Letztes Ergebnis (JSON) | `ns=<vision>;s=VisionMachine.LatestResultJson` |
| Livestream (Base64-JPEG) | `ns=<vision>;s=VisionMachine.LatestCameraFrame` (nur mit `camera_stream`) |
| MJPEG-Port | `ns=<vision>;s=VisionMachine.CameraStreamHttpPort` (Int32, 0 = kein HTTP-Stream) |

## 3. Was das Backend können muss

| # | Fähigkeit | Konkret |
| --- | --- | --- |
| B1 | Namespace-Index auflösen | `get_namespace_index(uri)` für beide URIs aus Abschnitt 2 |
| B2 | Event-Subscription mit Typliste | `subscription.subscribe_events(vision_node, [<Event-Typ-Knoten>])`; die Typliste ist **zwingend** (7.1) |
| B3 | Methodenaufruf 5 Eingaben / 2 Ausgaben | `automatic_node.call_method(start_node, meas, part, recipe, product, parameters)` |
| B4 | Event-Felder lesen | `event.ResultContent[0]` → `json.loads(...)` |

Nicht benötigt: `load_data_type_definitions()`, Structured DataTypes, Ergebnis-Handles,
Polling, Discovery. Eine leere Robot-Discovery ist für diesen Endpoint korrekt.

## 4. Handshake: ein Job

**Erst abonnieren, dann aufrufen.** Ein Job ist nach ~250 ms fertig; es gibt kein
Event-Replay.

```
Backend                                     Vision-Server
   |  1. connect + Namespace-Indizes            |
   |  2. subscribe_events(VisionMachine, [...]) |
   |------------- StartSingleJob -------------->|  Guard + Validierung (synchron)
   |<------ (JobId: String, Error: Int32) ------|  Error != 0  => Ende, keine Events
   |<---- StateChangedEvent Ready->SingleExecution
   |<---- JobStartedEvent            Message = JobId
   |<---- AcquisitionDoneEvent       Message = JobId
   |<---- ResultReadyEvent           ResultContent[0] = JSON  <-- das Ergebnis
   |<---- StateChangedEvent SingleExecution->Ready
   |<---- ReadyEvent                 Message = JobId
```

Ergebnis → Job über `payload["jobId"]`, **nicht** über die Event-Felder `JobId`/`ResultId`
(7.2).

| NodeId (MachineVision-NS) | Typ | Bedeutung |
| --- | --- | --- |
| `ns=<mv>;i=1024` | `ResultReadyEventType` | **trägt das Ergebnis-Payload** |
| `ns=<mv>;i=1013` | `JobStartedEventType` | Job angenommen |
| `ns=<mv>;i=1025` | `AcquisitionDoneEventType` | Aufnahme fertig (fehlt im Fehlerfall) |
| `ns=<mv>;i=1023` | `ReadyEventType` | wieder aufnahmebereit |
| `ns=<mv>;i=1018` | `StateChangedEventType` | Zustandswechsel, `FromState`/`ToState` |

Felder des `ResultReadyEvent`: `ResultContent` (String-Array), `ResultState` (Int32),
`IsPartial`, `IsSimulated`, `CreationTime`, `Message`.

## 5. `StartSingleJob`

Knoten: `AutomaticModeStateMachine/StartSingleJob`. Nicht blockierend; das Ergebnis
kommt per Event.

| Argument | Nodeset-Typ | **Tatsächlich erwartet** |
| --- | --- | --- |
| `MeasId` | `MeasIdDataType` | String (leer erlaubt) |
| `PartId` | `PartIdDataType` | String (leer erlaubt) |
| `RecipeId` | `RecipeIdExternalDataType` | String; **wählt den Job** |
| `ProductId` | `ProductIdDataType` | String (leer erlaubt) |
| `Parameters` | `BaseDataType[]` | String-Array, max. 16 Einträge |

| Ausgabe | Nodeset-Typ | **Tatsächlich geliefert** |
| --- | --- | --- |
| `JobId` | `JobIdDataType` | String, z. B. `job-000001`; leer bei Ablehnung |
| `Error` | `Int32` | Fehlercode (unten) |

### Verfügbare Jobs (`RecipeId`)

| `RecipeId` | Job | Implementierung | Ergebnis |
| --- | --- | --- | --- |
| `"calibration"` | Messbereitschaft prüfen | Subprozess, `src/jobs/calibrate.py` | `attributes.message` = Kalibrier-Id, Auflösung, RMS, Aufnahmezahl, Alter, Tag-Map |
| `"apriltag"` | **Module lokalisieren** | in-process, `detection/apriltag.py`, geteilte Kamera (Abschnitt 10) | eine Detektion je erkanntem Modul-Tag mit Pose |

`apriltag` nimmt `samples_per_job` Bilder auf (Decke 3, Flansch 5), mittelt die Posen
und verwirft Tags oberhalb `max_reproj_error_px`. Daraus folgt `job_timeout` = 20 s
(Abschnitt 9). Die Kamera wird nicht extra geöffnet.

`attributes` einer AprilTag-Detektion: `tagId`, `reprojErrorPx`, `ambiguous`,
`sampleCount`, `frameTimestamp`, `recipeId`; zusätzlich `missingModules`, wenn erwartete
Module aus der Tag-Map fehlen. Kein Tag im Bild → **`Error=5`** (`DETECTION_FAILED`),
kein leeres Erfolgsergebnis.

Die Kamera-Quelle bestimmt `frameId`, `frameConvention`, `configurationId` und
`IsSimulated`. **`frameId` nie annehmen, sondern lesen**: Sind Welttags im Bild, liefert
die Quelle Posen im Welt-KS, sonst im Kamera-KS. `frameConvention` sagt, wohin +Z zeigt.
Die `RecipeId` steht nach dem Job in `InternalRecipeId` und `attributes.recipeId`.

Jede Detektion trägt `source` (`ceiling`/`flange`), `role` (`module`/`robot`),
`referenceTagId` (nächster Welttag), `referencePosition`/`referenceOrientation`
(`T_worldtag_module`), `referenceDistanceM`, `worldTagIds`, `cameraSpreadM`,
`cameraSpreadDeg` (Güte der Kameralokalisierung).

**Kamerapose (`cameraPoseSolver`):** Mit zwei oder mehr Welttags löst der Pi
`T_world_cam` aus **einem gemeinsamen PnP über alle Ecken aller Welttags** (`joint`).
Mittelung von Einzel-Tag-Posen (`average`) ist verworfen: aus 2–3 m Höhe ist die Neigung
so schlecht bestimmt (Simulation: gemeinsam ~4,5 mm / 0,09°, Mittel ~37–41 mm / 0,8°).
Ein einzelner Welttag: `single`. `cameraReprojectionErrorPx` = RMS über alle Ecken.
`cameraSpreadM`/`Deg` = Abweichung der Einzel-Tag-Schätzungen von der gemeinsamen Pose,
nur Diagnose. In `worldTags` trägt jeder Tag `jointReprojectionErrorPx`; ein nicht
zur Karte passender Welttag hat dort den größten Wert.

`cameraPoseOrigin`: `world_tags` (optisch gemessen) oder `robot_pose` (aus Anker und
Kinematik fortgeschrieben; weniger genau). Dazu `anchorWorldTagId` und, sobald wieder ein
Welttag im Bild war, `anchorDriftM`/`anchorDriftDeg`.

**Roboterpose:** Der Aufrufer übergibt `T_base_flansch` als Job-Parameter: sieben
Doubles `(x, y, z, qx, qy, qz, qw)`, Meter, Quaternion xyzw. Fehlt sie, bleibt es beim
optischen Weg. Der Hand-Pi fragt den Roboter-Server **nicht** selbst ab.

Layer 1 und Layer 2 nutzen dasselbe Rezept; sie unterscheiden sich nur in der
`AprilTagProfileConfig` (je Pi in `server.py`).

Unbekannte oder leere `RecipeId` → `Error=4` sofort; die Fehlermeldung listet die
bekannten Rezepte. Ein Standardjob existiert nicht.

**Bewusste Abweichung:** Das Nodeset deklariert die Ids als Strukturen. asyncua kann
solche ExtensionObjects ohne `load_data_type_definitions()` nicht bauen oder lesen
(asyncua-Issue #1693), deshalb werden Strings ausgetauscht. UaExpert zeigt an diesen
Stellen einen String, wo der Typ eine Struktur erwartet; das ist erwartet.

### `Stop`

Knoten: `AutomaticModeStateMachine/Stop`. Bricht den Job **wirklich** ab: Subprozess
`terminate()`, nach 2 s `kill()` (Abschnitt 9). Automat zurück nach `Ready`, ein
`ResultReadyEvent` mit `resultState=7` (`CANCELLED`) wird gefeuert. Der Aufruf
blockiert, bis das erledigt ist; ein `StartSingleJob` danach trifft nie auf einen
aufräumenden Job.

| Argument | Typ | Hinweis |
| --- | --- | --- |
| `Cause` | `Int32` | wird nicht ausgewertet |
| `CauseDescription` | `String` | wird nicht ausgewertet |

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` bei Abbruch oder wenn nichts lief; `!= 0`, wenn der Abbruch scheiterte |

`Stop` ohne laufenden Job liefert `Error=0` (fire-and-forget). Nur wenn der Job sich nicht
innerhalb `stop_timeout` (5 s, `config.py`) abbrechen lässt: `Error=6` (`INTERNAL`).

### Fehlercodes (`Error`)

Das Nodeset definiert keinen Enum; diese Codes sind projektspezifisch und im Backend
zu spiegeln.

| Code | Name | Bedeutung | Folgen Events? |
| --- | --- | --- | --- |
| 0 | `OK` | Job angenommen | ja |
| 1 | `INVALID_STATE` | Automat nicht in `Operational`/`Ready` | nein |
| 2 | `INVALID_ARGUMENT` | Id zu lang, falscher Typ, zu viele Parameter | nein |
| 3 | `BUSY` | Job, Kalibrier-Session oder Auswertung läuft | nein |
| 4 | `UNKNOWN_RECIPE` | `RecipeId` nicht bekannt | nein |
| 5 | `DETECTION_FAILED` | Erkennung fehlgeschlagen | ja, `resultState=5` |
| 6 | `INTERNAL` | unerwarteter Serverfehler | ja, `resultState=6` |
| 7 | `CANCELLED` | durch `Stop` abgebrochen | ja, `resultState=7` |

**1–4** werden sofort im Rückgabewert abgelehnt (Automat bleibt `Ready`, kein Event).
**5–7** passieren während der Ausführung und kommen als `ResultReadyEvent` mit
`resultState != 0`. Es läuft immer nur **ein** Job; ein zweiter Aufruf bekommt `BUSY`.

## 6. Ergebnis-Payload

JSON in `ResultContent[0]` (Schema `wsc.vision.detections/1`), identisch im Event, am
Ergebnisknoten und in `LatestResultJson`.

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-hello-01",
  "resultId": "res-job-000001",
  "jobId": "job-000001",              // Korrelation zum StartSingleJob-Rückgabewert
  "creationTime": "2026-09-09T19:16:59.612+00:00",
  "resultState": 0,                   // 0 = ok, sonst Fehlercode aus Abschnitt 5
  "frameId": "world",                 // Bezugsrahmen der Posen
  "lengthUnit": "m",                  // immer SI-Meter
  "angleUnit": "rad",
  "rotation": "quaternion_xyzw",
  "detections": [
    {
      "moduleId": "HELLO-WORLD",      // später: Key in die CAD-Registry
      "instanceId": "det-1",
      "confidence": 1.0,
      "position": [0.0, 0.0, 0.0],
      "orientation": [0.0, 0.0, 0.0, 1.0],
      "boundingBox": null,
      "attributes": { "message": "Hello World" }
    }
  ]
}
```

Fehlerfall: gleiches Schema, zusätzlich `errorCode`/`errorText`, `detections: []`:

```jsonc
{ "schema": "wsc.vision.detections/1", "jobId": "job-000004", "resultState": 5,
  "errorCode": 5, "errorText": "Erkennung durch Parameter 'force-error' fehlgeschlagen",
  "detections": [] }
```

**Additive Schlüssel** (nur bei nicht-leerem Wert, Schema bleibt):

| Schlüssel | Bedeutung |
| --- | --- |
| `frameConvention` | Achsenkonvention hinter `frameId`, z. B. `"z_forward_x_right_y_down"` = OpenCV-Optikrahmen (+X rechts, +Y unten, +Z optische Achse nach vorn). Pose `T_cam_tag`, Tiefe positiv. **Nicht** REP-103, **kein** Weltsystem. |
| `configurationId` | Identität der wirksamen Konfiguration (Kalibrierdatei plus Änderungszeit) |
| `worldTags` | `[{tagId, position, orientation, reprojectionErrorPx, ambiguous, sampleCount, jointReprojectionErrorPx?}]`: die in diesem Job gesehenen Welttags. Pose = **beobachtete** `T_world_cam · T_cam_tag`, gemittelt, im Welt-KS der Tag-Map. Abweichung von `poseInWorld` der Karte = „eingemessen gegen gesehen“. Welttags stehen **nicht** in `detections`. Auch im Fehler-Payload (Bild nur mit Welttags → `DETECTION_FAILED`). |
| `cameraLocalization` | `{solver, reprojectionErrorPx, spreadM, spreadDeg, worldTagIds}` des letzten Bildes; `solver` = `joint`, `single`, `average` oder `anchor` (dann `reprojectionErrorPx: null`). Bei `joint` zusätzlich `poseInWorld` (`T_world_cam`) und `tagErrorsPx`. Auch im Fehler-Payload; fehlt ohne Kamerapose. |

Parser-Regeln: `schema` prüfen, unbekannte Versionen verwerfen; `resultState != 0` ist
ein Fehler; `position` in Metern; `orientation` xyzw; `frameId` niemals annehmen.

## 7. Stolpersteine

**7.1 Ohne Event-Typ-Liste kein Payload.** `subscribe_events(node)` ohne zweites
Argument liefert Events, aber `ResultContent` ist `None`. Immer die Liste übergeben:

```python
sub = await client.create_subscription(100, handler)
await sub.subscribe_events(
    vision_node,
    [client.get_node(ua.NodeId(i, mv_idx)) for i in (1024, 1013, 1025, 1023, 1018)],
)
```

Die Liste filtert nicht: es kommt alles an, was `VisionMachine` emittiert. Clientseitig
auf `event.EventType` filtern.

**7.2 Abo auf `VisionMachine`, nicht auf dem Server-Objekt.** asyncua bubbelt Events nicht;
die Auslieferung matcht den exakten `emitting_node`. Ein Abo auf `i=2253` /
`client.nodes.server` empfängt nichts. Die Structure-Felder `ResultId`, `JobId`,
`InternalRecipeId`, `InternalConfigurationId` des Events sind absichtlich leer; Werte im
JSON. `Message` trägt die `ResultId` als Klartext.

**7.3 `ResultManagement`-Methoden nicht benutzbar.** `GetResultById`,
`GetResultComponentsById`, `GetResultListFiltered` sind im Nodeset, aber nicht
implementiert. Sie erwarten ExtensionObjects als Eingabe und sind vom Client nicht
aufrufbar. Keine Handles, kein `ReleaseResultHandle`. Fallback, falls Array-Felder in
Events nicht ankommen: auf das `ResultReadyEvent` hin ein `read_value()` auf
`LatestResultJson` (eventgetrieben, kein Polling).

**7.4 Genau eine 40100-Instanz.** Verifiziert durch `tests/test_part10_fassade.py`. Die
feste NodeId `ns=<vision>;s=VisionMachine` verwenden, nicht per Typsuche.

**7.5 Verlinkte Methoden.** Das Nodeset bringt alle Methoden der Spec mit; `server.link_method`
in `runner/jobs.py` entscheidet, welche wirken. Eine nicht verlinkte Methode antwortet
mit `BadNothingToDo` auf Statusebene, nicht mit `Error != 0`.

| Verlinkt | Wirkung |
| --- | --- |
| `StartSingleJob` | ein Durchlauf, Ergebnis, zurück nach `Ready` |
| `StartContinuous` | läuft bis `Stop`/`Abort`; je Durchlauf Ergebnis `<jobId>-0001`, `-0002`, …; ein fehlgeschlagener Durchlauf beendet den Dauerbetrieb; Pause `continuous_interval_s` (1 s) |
| `Stop` | Abbruch, Ergebnis `resultState=7` |
| `Abort` | wie `Stop`, über den Abort-Übergang (keine Zwischenstände) |
| `Halt` | beendet den Job, fährt nach `Halted`; danach keine Jobs mehr |
| `Reset` | zurück nach `Operational` über `Preoperational` (kein `Halted -> Operational` im Nodeset) |

| Nicht verlinkt | Grund |
| --- | --- |
| `SimulationMode` | bräuchte eine simulierte Datenquelle je Profil |
| `SelectModeAutomatic` | nur eine Betriebsart; eine Methode ohne Wirkung wäre irreführend |
| `ConfirmAll`, `Sync` (StepModels) | Schrittketten-Modell, nicht benutzt |
| `ConfigurationManagement`, `RecipeManagement` | kein Konfigurations-Datenmodell; `RecipeId` ist nur ein Routing-Schlüssel |
| `GetResultById`, `ReleaseResultHandle`, `GetResultListFiltered` | Ergebnis kommt im Event und steht am Knoten |

Diese Lücken sind Entscheidungen.

## 8. Ablauf einmal durchspielen

```bash
sudo systemctl restart opcua-server.service      # auf dem Pi, nach Code-Update

# Referenz-Handshake (beliebiger Rechner):
PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py \
    --url opc.tcp://<pi>:4840/raspi/server/ --recipe-id calibration
```

Exit-Code `0` = Handshake vollständig. Der Client ist die kürzeste Vorlage für ein Backend.

| Aufruf | Erwartung |
| --- | --- |
| zwei Aufrufe gleichzeitig | einer `Error=0`, einer `Error=3` (BUSY) |
| `--meas-id` mit >128 Zeichen | `Error=2`, keine Events |
| `--recipe-id does-not-exist` | `Error=4`, keine Events |
| `--parameter force-error` | `Error=0`, dann `ResultReadyEvent` mit `resultState=5`; Automat über `Error` zurück nach `Operational` |

## 9. Offen

- **Kalibrierung:** `calibration` ist eine Bereitschaftsprüfung. Die Kamerakalibrierung
  bleibt bedienergeführt (`python -m tagloc.cli.calibrate`), sie braucht jemanden am Board
  und lässt sich nicht aus der Ferne auslösen.
- **Job-Timeout:** Dauert eine Erkennung länger als `job_timeout` (20 s), kommt
  `DETECTION_FAILED`, der Automat geht nach `Ready`. Ein hängender Worker-Thread lässt
  den *nächsten* Job desselben Profils ebenfalls in den Timeout laufen; deshalb hat die
  AprilTag-Quelle `capture_timeout_s` als kürzeres Aufnahme-Timeout.
- **Hand-Auge-Verifikation:** Die Kette Kamera → Flansch wird aus
  `AprilTagProfileConfig.hand_eye_path` geladen und mit der Roboterpose zu `T_world_cam`
  verkettet (Plan [`arbeitsplaene/apriltag-hand-auge-ankern.md`](arbeitsplaene/apriltag-hand-auge-ankern.md)).
  Offen ist die reale Vermessung je Zelle, nicht die Implementierung.
- **Zweite Kamera / 3D-Profil:** würde als zweite `VisionSystemType`-Instanz im selben
  Server hängen. Ein zweiter Serverprozess ist nicht vorgesehen (~110 MB zusätzlich).
- **Structure-Felder der Events** befüllen, sobald asyncua #1693 gefixt ist. Das Payload
  bleibt die maßgebliche Quelle.

## 10. Kamera-Livestream

Kein Methodenaufruf, kein Lifecycle. Mit `camera_stream` schreibt der Server
kontinuierlich (Standard 5 Bilder/s) den neuesten Frame als **Base64-JPEG-String** in
`ns=<vision>;s=VisionMachine.LatestCameraFrame`. Der Wert ist **kein JSON**;
`base64.b64decode(value)` liefert die JPEG-Bytes. Ein Abo funktioniert wie bei
`LatestResultJson`.

- Der Knoten existiert nur mit `camera_stream` (auf dem Pi über `server.py`; lokal über
  `python -m vision_server` standardmäßig nicht). Läuft die Kamera nicht, bleibt der
  Wert leer (`""`); das ist kein Automatenzustand.
- Livestream und `apriltag`-Job teilen **dieselbe** Kamera (`SharedCamera`); der Job liest
  nur mit. Kein Aussetzen während eines Jobs.
- Parameter `CameraStreamConfig` (`profiles.py`): 1280×720, Aufnahme 15 fps, Knoten 5 fps,
  HTTP 15 fps, JPEG-Qualität 70.
- Veröffentlicht wird auf `max_stream_width` (Standard 960 px) verkleinert. Erkennung und
  Kalibrierung sehen weiter den vollen Frame.
- **Kamera-Backend pro Pi:** `CameraStreamConfig.backend` = `"picamera2"` | `"realsense"` |
  `"opencv"`. `server.py` wählt über `PI_CAMERA_BACKENDS` (Hostname → Backend, Fallback
  `picamera2`; Env `VISION_CAMERA_BACKEND` überschreibt). Aktuell: `ADP-Roboter-Lokalisierung`
  → Picamera2 (Decke), `ADP-HandInEye-Kamera-Pi` → RealSense. Erkennung und Stream lesen
  nur `CameraFrame` (BGR) von `SharedCamera.latest_frame`.
- **RealSense:** eigene Defaults `realsense_resolution` 640×480, `realsense_fps` 15
  (unabhängig von den anderen Backends). Die RSUSB/libuvc-Anbindung auf dem Pi begrenzt
  die Bandbreite; `1280×720@30` scheitert mit `RuntimeError: Couldn't resolve requests`.
  Scheitert `pipeline.start()`, loggt `_open_realsense` die unterstützten Profile.
- Die Quelle des Streams ist die erste geöffnete Quelle, die eine `SharedCamera` hält.

### 10.1 Overlay: was im Bild markiert wird

Ein zweiter, **beschreibbarer** Knoten wählt den Modus:

```
ns=<vision>;s=VisionMachine.CameraStreamMode     Datentyp String, schreibbar
```

| Wert | Anzeige im Frontend | Was markiert wird |
| --- | --- | --- |
| `off` | „Rohbild" | nichts |
| `apriltag` (Standard) | „AprilTags markieren" | je erkanntem Tag grüner Kasten und `#<id>` (`tagloc.overlay.draw_tag_boxes`); keine Statusleiste, kein Achsenkreuz. Mehrdeutigkeit steht in den Job-Ergebnissen (`ambiguous`, `ambiguityRatio`, `confidence`). Ausführliches Overlay: `python -m tagloc.cli.detect --overlay` |
| `calibration` | „Kalibrierboard markieren" | gefundene Board-Ecken und Bildabdeckung |
| `undistorted` | „Entzerrt" | Bild entzerrt mit der aktiven Kalibrierung (`alpha`/`balance` = 1). Ohne echte Kalibrierung: Rohbild mit Hinweis „Keine Kalibrierung -- Rohbild“ |
| `tagreference` | „Tag einmessen“ | nur mit aktiver Vorschau (`SetTagReferencePreview`, 19.1); sonst Tag-Rahmen mit Titel „keine Einmess-Vorschau“ |

- Ungültiger oder leerer Wert → Fallback auf `apriltag`; der Stream hält nicht an.
- Overlay und Job nutzen dieselbe Kalibrierung und Tag-Map.
- Im `calibration`-Modus gilt die Board-Geometrie aus `AprilTagProfileConfig` (wie
  `CaptureCalibrationSample`), nicht die aus der Kalibrierdatei.
- `undistorted` verkleinert zuerst, dann `cv2.remap` mit Maps aus dem Cache
  (`tagloc.lens.undistortion_maps`); `FinishCalibration` leert den Cache. Nur Anzeige:
  Jobs rechnen mit dem Rohbild.
- Gezeichnet wird auf einer Kopie. Ein Fehler im Overlay beendet den Stream nicht; es
  kommt das unmarkierte Bild, der Fehler steht im Log.
- Overlay-Rechenintervall: `overlay_interval_s` (0,5 s). Für `apriltag` siehe 10.4.

Das Frontend braucht nur ein Auswahlfeld, das den Knoten schreibt. Beschriftungen:
`tagloc.modes.OVERLAY_MODE_LABELS`. Abnahme: [`apriltag-e2e-test.md`](apriltag-e2e-test.md) Abschnitt 4.

### 10.2 MJPEG über HTTP

```
http://<pi>:<port>/stream.mjpg     endloser Stream (multipart/x-mixed-replace), ~15 fps
http://<pi>:<port>/snapshot.jpg    neuestes Einzelbild (503, solange keins da ist)
```

- Port im Knoten `CameraStreamHttpPort` (Standard 8080). Er ist 0, bis der Server wirklich
  lauscht, und bei belegtem Port. `<pi>` nimmt der Client aus seiner Endpoint-URL.
- Dieselben Bilder und dasselbe Overlay wie der Knoten; jeder Frame wird einmal markiert.
- Rate: `stream_fps` ohne Zuschauer, `http_fps` mit offener Verbindung. Höchstens 4
  Zuschauer, weitere bekommen 503.
- Kamera hängt (Watchdog, [`vision-system.md`](vision-system.md)): Verbindung bleibt offen,
  letztes Bild steht.
- Fällt der Port auf 0 oder ist die URL nicht erreichbar, nutzt das Frontend
  `LatestCameraFrame`.

### 10.3 Zwei Bildströme an der Deckenkamera

Die Deckenkamera (Pi 1, IMX477) nimmt mit **4056×3040** auf; der ISP liefert zusätzlich
`lores` 960×720.

| Wer | Bild | Auflösung |
| --- | --- | --- |
| AprilTag-Job, Kalibrier-Session | `CameraFrame.image` | 4056×3040 |
| Overlay „AprilTags markieren“ (MJPEG und Knoten) | `CameraFrame.image` | 4056×3040, erst nach Erkennung auf `max_stream_width` verkleinert |
| „Rohbild“ (`off`) | `CameraFrame.image` | 4056×3040, auf `max_stream_width` verkleinert |
| Overlay „Kalibrierboard markieren“ (`calibration`) | `CameraFrame.preview` | 960×720 |

Stream und Job werten damit dieselben Bilder aus. Die Framerate ist niedriger, bewusst in
Kauf genommen. Overlay auf `preview` rechnet die Kalibrierung per `scale_to_resolution` um
(Seitenverhältnisfehler 0,07 %, Schranke 0,1 %).

`CameraStreamConfig`-Presets (`server.py`, `PI_CAMERA_STREAM_PRESETS`): `preview_resolution`
960×720; `capture_fps` 10 (Obergrenze IMX477 bei voller Auflösung); `buffer_count` 2 (CMA-
Speicher); `overlay_timeout_s` **8,0** (Standard 2,0; Erkennung auf 12 MP braucht länger, sonst
fällt das Overlay auf das Rohbild zurück). Pi 2 behält 2,0. Ohne `preview_resolution` (Hand-Pi)
verkleinert der Stream selbst; Job und Stream haben dort 640×480.

Hauptstrom `RGB888`: Picamera2 liefert B,G,R, also direkt OpenCV-BGR.

### 10.4 „AprilTags markieren“ in voller Auflösung

Gestreamt wird das kleine Bild, die markierten Tags stammen aber aus der Erkennung auf dem
**vollen** Frame. Auf dem kleinen Bild fehlten Tags, die der Job fand. Die Erkennung läuft
nicht im Stream-Takt (12 MP alle 0,5 s belegen einen Core des Pi und lassen Jobs in den
Timeout laufen). Gezeichnet wird das neueste Ergebnis, das ohnehin entsteht:

| Quelle | Wann | Zusätzliche CPU |
| --- | --- | --- |
| Job | jedes Bild eines AprilTag-Jobs | keine |
| Zellen-Überwachung | alle 2 s, solange scharf | keine |
| Messung | `MeasureTag`/`MeasureTags`, Hand-Auge-Aufnahmen | keine |
| Overlay selbst | nur, wenn das neueste Ergebnis älter als `overlay_detect_interval_s` (5 s) ist | am Pi grob 1 s je Lauf |

Die Overlay-Erkennung läuft im Hintergrund, höchstens eine gleichzeitig, nie während eines
Jobs oder einer Kamera-Kalibrierung (dasselbe Signal, mit dem die Zellen-Überwachung
pausiert). Sie nutzt die Stream-Detektor-Parameter des Jobs ohne Eckenverfeinerung
(bei `cam_ceiling` ist im Job `subpix` an). `overlay_detect_interval_s = 0`: das Overlay
erkennt nie selbst.

Pixel-Ecken werden von der Ergebnisauflösung auf die Stream-Größe umgerechnet. Auflösung,
Quelle und Alter des Ergebnisses stehen nicht im Bild; Markierungen können bei bewegten
Modulen einige Sekunden hinterherlaufen.

Python: `AprilTagDetectionSource.latest_tags` (`TagSnapshot`: `tag_poses`, `image_size`,
`origin` = `job`/`cellwatch`/`measure`/`overlay`, `taken_at`),
`AprilTagStreamAnnotator(full_resolution_tags=..., detect_interval_s=...)`,
`AprilTagStreamAnnotator.set_busy(...)`.

## 11. OPC 40100-2: Anlagensicht

Part 2 beantwortet, **woraus das System besteht**; zusätzlich Zustand (11.4). Für Service,
nicht für den Betrieb.

```
ns=<vision>;s=VisionMachine.VisionAsset
├── Identification        Manufacturer, Model, SerialNumber, SoftwareRevision
├── ComputingDevices/ComputingDevice
├── ImageSensors/ImageSensor
└── Lenses/Lens
```

Angelegt wird nur, was in `PI_ASSET_PRESETS` (`server.py`, `AssetConfig`) steht. Leeres
Modellfeld = „nicht bekannt“ = **kein** Eintrag.

Gemessen (Desktop, asyncua 2.0.1): RSS +12,8 MB, Start +1,6 s, Anlagensicht +2,9 MB. Ohne
`assets` in der `VisionServerConfig` wird nichts davon geladen. Nachmessen:
`PYTHONPATH=src python3 tools/measure_nodeset_import.py`.

**Pins:** DI 1.04.0 und Machinery 1.03.0 (die von AMCM genannten `RequiredModel`-Versionen).
DI 1.05.0 scheitert mit asyncua 2.0.1 (`BadParentNodeIdInvalid`, verlangt UA 1.05.04);
neueres Machinery zieht `IA` herein. Details:
[`src/vision_server/nodesets/README.md`](../../src/vision_server/nodesets/README.md).

**Namespace-Index:** Mit Part 2 liegt `http://launch-rm.de/vision` auf 6 (vorher 3). Zur
Laufzeit auflösen.

**Platzhalter:** `<VisionItem>` & Co. tragen `MandatoryPlaceholder` und werden als Knoten
angelegt. `asset_model.py` legt deshalb nur die gefüllten Ordner an (~50 statt 700 Knoten).

### 11.4 Zustand: `DeviceHealth`

Die zweite Hälfte von Part 2 (Condition Monitoring): *läuft die Kamera noch, oder ist sie
eingefroren?* Ein hängendes `capture_array()` ist ohne diesen Knoten unsichtbar.

```
ns=<vision>;s=VisionMachine.VisionAsset
├── <amcm>:Health                                      (HasAddIn)
│   └── <di>:DeviceHealth   Int32
│       ns=<vision>;s=VisionMachine.VisionAsset.Health.DeviceHealth
├── <di>:Identification
├── <amcm>:ComputingDevices/ComputingDevice
├── <amcm>:ImageSensors/ImageSensor
│   └── <amcm>:Health
│       └── <di>:DeviceHealth   Int32
│           ns=<vision>;s=VisionMachine.VisionAsset.ImageSensor.Health.DeviceHealth
└── <amcm>:Lenses/Lens
```

Beide Knoten tragen **denselben** Wert (eine Zustandsquelle: die Kamera). Die Wurzel existiert
auch ohne `image_sensor_model`. Für Frontends: **die Wurzeladresse abonnieren**. Eine echte
Rangfolge für mehrere Komponenten braucht NE-107-Regeln, die DI nicht definiert.

`DeviceHealth` = DI `DeviceHealthEnumeration` nach NAMUR NE 107, `Int32`:

| Wert | Name | Wann | HMI |
| --- | --- | --- | --- |
| 0 | `NORMAL` | frisches Kamerabild | grün |
| 1 | `FAILURE` | Kamera nie geöffnet oder aufgegeben; Prozess beendet sich danach | rot |
| 2 | `CHECK_FUNCTION` | Warmup oder Neu-Öffnen; **Startwert** | gelb |
| 3 | `OFF_SPEC` | Bild älter als `stale_frame_s`; Hänger erkannt, noch nicht eskaliert | gelb |
| 4 | `MAINTENANCE_REQUIRED` | **wird nie geschrieben** (kein Verschleißzähler) | — |

Ablauf eines Hängers (Vorgabewerte `CameraStreamConfig`):

```
NORMAL ──(Bild älter als stale_frame_s = 2 s)──> OFF_SPEC
       ──(Watchdog nach frame_timeout_s = 3 s, öffnet neu)──> CHECK_FUNCTION
       ──(Kamera liefert wieder)──> NORMAL
       ──(2 Neu-Öffnungen ohne Bild)──> FAILURE, dann Prozessabbruch
```

`OFF_SPEC` dauert `frame_timeout_s − stale_frame_s` (1,0 s); deshalb Abfrage alle
`health_interval_s` = 0,5 s. Ist `stale_frame_s ≥ frame_timeout_s`, entfällt `OFF_SPEC`
(Test: `tests/test_camera_health.py`). Logik: `camera_health.device_health()`.

Client-Regeln:
- Geschrieben wird **nur bei Zustandswechsel**; im Normalbetrieb bleibt der Knoten still.
  Pollen liefert jederzeit den gültigen Wert.
- `FAILURE` kann das letzte Lebenszeichen sein: danach beendet sich der Prozess (systemd
  startet ihn neu). Verbindungsabriss nach `FAILURE` einplanen.
- Drei Namespace-Indizes an einem Pfad: `DeviceHealth` (DI), `Health` (AMCM), NodeIds (Vision).

#### Alarme

`IDeviceHealthType` hat genau zwei Mitglieder: die Variable und einen Alarm-Ordner.

```
VisionAsset/Health
├── <di>:DeviceHealth                Int32
└── <di>:DeviceHealthAlarms
    ├── <di>:FailureAlarm            FailureAlarmType
    ├── <di>:CheckFunctionAlarm      CheckFunctionAlarmType
    └── <di>:OffSpecAlarm            OffSpecAlarmType
```

| Alarm | Severity | Wann |
| --- | --- | --- |
| `FailureAlarm` | 900 | `FAILURE` |
| `OffSpecAlarm` | 500 | `OFF_SPEC` |
| `CheckFunctionAlarm` | 300 | `CHECK_FUNCTION` |

- Höchstens einer aktiv. Beim Wechsel wird der alte mit `ActiveState = Inactive`,
  `Retain = false` gelöscht, dann feuert der neue mit `Active`/`true`.
- `NORMAL` feuert keinen Alarm, löscht nur den vorherigen.
- Emittiert von **`VisionMachine`** (derselbe Knoten, den der Client ohnehin abonniert).
  `SourceNode` = Bildsensor, falls sein Modell bekannt ist, sonst Anlagenwurzel.
- `Message` z. B. *„Kamera liefert kein Bild (hung)“*.
- Alarmobjekte nur an der Wurzel (je Instanz 36 Knoten, `AlarmConditionType`-Baum).

> **Grenze:** `ConditionRefresh` ist **nicht** bedient. Wer sich nach einem Alarm verbindet,
> bekommt ihn nicht. Verlässlich für „wie ist der Zustand jetzt“ ist `DeviceHealth`; die
> Alarme sind der Ereignisweg für „was hat sich geändert“.

Nicht angelegt: `MaintenanceRequiredAlarmType` (ohne Zähler könnte er nie feuern), `State`
(SEMI E10, braucht strukturierte DataTypes wegen asyncua #1693), `Temperature`,
`RemainingLifeTime`. `Maintenance` bleibt leer.

**Livestream:** Bei hängender Kamera **steht das Bild still**, es verschwindet nicht.
`/snapshot.jpg` antwortet mit dem letzten Bild. Die Ampel im Frontend kommt aus `DeviceHealth`.

## 12. Interaktive Kalibrierung (frontend-gesteuert)

Vier Methoden und ein Fortschrittsknoten erlauben Kamerakalibrierung **bei laufendem Server**
aus dem Frontend. Die Session liest die laufende `SharedCamera` mit; kein exklusiver Zugriff.

- **Aufnahme und Auswertung sind getrennt.** `CaptureCalibrationSample` merkt nur den Frame,
  ohne Ecken-Erkennung (sonst Timeout der OPC-UA-Anfrage). `FinishCalibration` startet die
  Auswertung (Ecken je Aufnahme + `cv2.calibrateCamera`) als **Hintergrundaufgabe** und kehrt
  sofort zurück. Ergebnis: `CalibrationProgress`.
- Während der Auswertung **pausiert der Livestream**; danach läuft er wieder.
- Der Operator sieht beim Positionieren den Livestream (`CameraStreamMode="calibration"`), der
  Board-Ecken auf dem Vorschaubild zeigt. Welche Aufnahme übernommen wird, zeigt er nicht.
- **Gespeicherte Kalibrierung wirkt sofort**, ohne Neustart: Erkennung und Overlay nehmen sie
  direkt.
- **Board-Geometrie ist serverseitig fest** (`AprilTagProfileConfig`, `PI_APRILTAG_PRESETS`).
  Das Frontend sendet keine Board-Parameter.

### 12.1 `StartCalibration`

Setzt Aufnahmen zurück, startet die Session. Aufnahmen nur über `CaptureCalibrationSample`.

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` `OK`; `1` `INVALID_STATE` (nicht `Ready`); `3` `BUSY` (Job, Session oder Auswertung läuft) |

### 12.2 `CaptureCalibrationSample`

Merkt sich **einen** Frame. Manuell, ohne Intervall und ohne Erkennung in diesem Aufruf.

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` `OK` (übernommen); `1` `INVALID_STATE` (keine Session oder kein Frame) |

### 12.3 `FinishCalibration`

Beendet die Aufnahme, startet die Auswertung im Hintergrund.

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Summary` | `String` (JSON) | Angenommen: `{"message": "Auswertung gestartet", "samples": N}`. Sofort abgelehnt: `{"message": "...", "samples": N, "model": ...}` |
| `Error` | `Int32` | `0` `OK` (Auswertung läuft); `1` `INVALID_STATE` (keine Session); `5` `DETECTION_FAILED` (weniger als 3 Aufnahmen, nichts gestartet) |

`Error=OK` heißt nur „läuft“, nicht „fertig und gut“. Das Ergebnis steht in
`CalibrationProgress["result"]`; ein Frontend, das nur den Rückgabewert zeigt, meldet
falschen Erfolg.

### 12.4 `AbortCalibration`

Verwirft die Session ohne Speichern. Nur in der Aufnahmephase; während einer laufenden
Auswertung `INVALID_STATE` (sie läuft zu Ende).

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` `OK`; `1` `INVALID_STATE` (keine Session aktiv) |

### 12.5a `SetTagMap`

```
ns=<vision>;s=<VisionSystemName>.SetTagMap

SetTagMap(TagMapJson : String) -> (Error : Int32)
```

Das Argument ist exakt der Inhalt von `config/tagmap.json` (Schema `wsc.vision.tagmap/2`).
Das Backend pflegt die Karte, kein Dateizugriff auf dem Pi nötig.

| `Error` | Wann |
| --- | --- |
| `OK` (0) | übernommen, Datei geschrieben, sofort aktiv |
| `INVALID_ARGUMENT` (2) | kein gültiges JSON, unbekanntes Schema oder Rolle |
| `BUSY` (3) | Job oder Kalibrier-Session läuft |
| `INTERNAL` (6) | Datei nicht schreibbar; Karte **trotzdem aktiv**, nach Neustart wieder die alte |

Abgelehnte Karte: die laufende bleibt. Beanstandungen von `validate_tag_map` (z. B. nur ein
Welttag statt vier) sind kein Fehler, werden geloggt, die Karte gilt. Der Hand-Anker wird
verworfen; der nächste Welttag-Blick ankert neu. Geschrieben nach `config/tagmap.json`.

### 12.5b `TagMapJson` (nur lesen)

```
ns=<vision>;s=VisionMachine.TagMapJson              Datentyp String (JSON)
```

Die geladene Karte, gleiches Format. Bewusst nicht schreibbar (Prüfung vor Gültigkeit).

### 12.5 `CalibrationProgress` (nur lesen)

```
ns=<vision>;s=VisionMachine.CalibrationProgress     Datentyp String (JSON)
```

Wird bei jedem Publish-Tick mitgeschrieben, auch bei `CameraStreamMode=off` und während der
Livestream pausiert ist.

| Feld | Bedeutung |
| --- | --- |
| `running` | Aufnahmephase aktiv; wird bei `FinishCalibration` schon `false`, **bevor** das Ergebnis steht |
| `processing` | Hintergrund-Auswertung läuft |
| `samples` | aufgenommene Bilder (**nicht** geprüft, ob das Board darauf ist) |
| `minSamples` | Mindestanzahl für eine Auswertung (Config, Standard 15) |
| `model` | `"pinhole"` oder `"fisheye"`, schon während der Aufnahme (12.10); fehlt im Ruhezustand |
| `result` | nur nach Abschluss der Auswertung |

Ruhezustand: `{"running": false, "processing": false}`, ohne `result`.

```jsonc
// waehrend der Aufnahme
{"running": true, "processing": false, "samples": 12, "minSamples": 15}
// Auswertung laeuft
{"running": false, "processing": true, "samples": 18, "minSamples": 15}
// fertig
{
  "running": false, "processing": false, "samples": 18, "minSamples": 15,
  "result": {
    "error": 0, "rms": 2.069, "model": "fisheye", "samples": 16,
    "coverageX": 0.84, "coverageY": 0.9,
    "path": "data/calibration/cam_ceiling.json",
    "backup": "data/calibration/cam_ceiling.json.20261115T093000Z.bak",
    "warning": "RMS 2.069 px ueber dem Zielwert 0.5 px -- ..."
  }
}
```

- Die alte Datei wird vor dem Überschreiben als `<name>.<UTC>.bak` gesichert (eigener Name je
  Sicherung, nie überschrieben). `result.backup` fehlt, wenn es keine alte Datei gab.
- `samples` = aufgenommen (18); `result.samples` = Bilder, auf denen das Board **gefunden**
  wurde (16). Nur Letzteres fließt ein.
- `result.error` = `Error`-Code wie bei `FinishCalibration`. Scheitern: `error != 0`, `message`
  statt `rms`/`coverageX`/`coverageY`. Ursachen: zu wenige Board-Treffer (`result.samples < 3`)
  oder numerisches Scheitern von `cv2.calibrateCamera`. Gute Abdeckung allein garantiert keine
  Genauigkeit (ein Board, das nie gekippt wurde, lässt die Brennweite offen).
- `result.warning` bei RMS > 0,5 px. Die Datei wird trotzdem gespeichert; das Frontend soll die
  Warnung **sichtbar** zeigen.

Tools unter `src/vision_server/tools/` (Referenz, nicht Teil des Frontends):
`calibration_client.py` (Session, Progress, wartet auf `result`); `stream_viewer.py` (Livestream,
Leertaste = `CaptureCalibrationSample`); `diagnose_board.py` (unmarkiertes Rohbild, probiert
`cols`/`rows`-Kombinationen, wenn das Board nicht erkannt wird).

### 12.6 `ActiveCalibrationInfo` (nur lesen)

```
ns=<vision>;s=VisionMachine.ActiveCalibrationInfo     Datentyp String (JSON)
```

Beschreibt die **tatsächlich benutzte** Kalibrierung und bleibt auch ohne Session stehen.
Quelle für „Zuletzt kalibriert am ...“. Geschrieben beim Start und nach jeder erfolgreichen
Kalibrierung.

```jsonc
{
  "placeholder": false,
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T10:00:00+00:00",
  "createdAt": "2026-09-22T10:00:00+00:00",
  "rms": 0.2945,
  "samples": 21,
  "board": {"type": "chessboard", "cols": 7, "rows": 9, "squareSizeM": 0.022, ...},
  "model": "pinhole",
  "path": "data/calibration/cam_flange.json"
}
```

`placeholder: true` = nie echt kalibriert (`allow_placeholder_calibration`, Abschnitt 9);
dann sind `rms`/`createdAt` `null`, Posen nicht maßhaltig. `path` ist nur Diagnose.

### 12.7 Sperren

`StartCalibration` → `BUSY`, solange ein Job läuft **oder** eine Session/Auswertung (Aufnahme
und Hintergrund zählen). Umgekehrt `StartSingleJob`/`StartContinuous` → `BUSY` während einer
Session. Kein eigener Automatenzustand: der Automat bleibt in `Ready`, die Sperre läuft über
die Busy-Flags.

### 12.8 Stand der Kalibrierung

- **Brett (beide Pis):** Schachbrett **6×7 Innenecken, 23 mm** (`cols=6`, `rows=7` in
  `server.py`). `cols`/`rows` sind Innenecken; das Brett hat 7×8 Felder. Mit der alten
  Einstellung 7×9 wird dieses Brett nicht gefunden, das Overlay zeigt „Board gefunden“ nicht.
  Gespeicherte Dateien bleiben gültig (Brett steht im Feld `board`). `cols + rows` = 13 ist
  ungerade und damit für die Hand-Auge-Schachbrettmessung geeignet (15.1).
- **Offen, Hand-Pi** (`ADP-HandInEye-Kamera-Pi`, RealSense): Verifiziert mit dem alten Brett
  (7×9, 22 mm, RMS 0,2945 px, 21 Aufnahmen). Mit dem neuen Brett neu kalibrieren.
- **Offen, Decke** (`ADP-Roboter-Lokalisierung`, Picamera2): `data/calibration/cam_ceiling.json`
  existiert, erster Versuch RMS 2,069 px (Abdeckung 84 %/90 %), zu hoch, da die Kamera nicht
  fest montiert und das Board zu wenig gekippt war. **Nach fester Montage neu kalibrieren**,
  mit Neigung und Distanz-Variation (siehe `warning`).

Der Code ist pi-unabhängig.

### 12.9 Debug: Aufnahmen speichern

Diagnose, kein Teil der Schnittstelle. **Standard aus**; `VISION_SAVE_CALIBRATION_CAPTURES=1`
schaltet ein (Unit oder Shell vor dem Start), danach wieder aus.

- Archiv: `data/calibration/<frame_id>_captures/<UTC-Stempel>/` (`YYYYmmddTHHMMSSZ`, bei
  Kollision `_2`, `_3`). PNG, verlustfrei (`kalib_NNN.png`), Zähler startet bei jedem
  `StartCalibration` bei 1. Jede Sitzung hat eine `session.json` (Schema
  `adp.calibration.captures/1`: Brett, Modell, aktive Kalibrierdatei). Vorherige Sitzungen
  werden nicht überschrieben.
- Arbeitsordner `data/calibration/<frame_id>_pending/`: JPEG, damit die Online-Auswertung schnell
  bleibt.
- `data/` ist versioniert: nur bewusst committen. Kein automatisches Aufräumen; bei knappem
  Speicher auf dem Pi von Hand leeren.
- Vorgehen: [`arbeitsplaene/kalibrier-aufnahmen-roh.md`](arbeitsplaene/kalibrier-aufnahmen-roh.md).

### 12.10 Linsenmodell je Kamera

| Layer | Kamera | Auflösung | Modell | Koeffizienten |
| --- | --- | --- | --- | --- |
| LV1 | `cam_ceiling` (Pi 4, HQ) | 4056 × 3040 | `fisheye` (OpenCV Kannala-Brandt) | 4: k1, k2, k3, k4 |
| LV2 | `cam_flange` (Pi 5) | 640 × 480 | `pinhole` (Brown-Conrady) | 5: k1, k2, p1, p2, k3 |

(Auswahl nach Bildtests: [`arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md`](arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md).)

- **Neu kalibrieren:** `AprilTagProfileConfig.calibration_model` (Preset in `server.py`);
  Override `VISION_CALIBRATION_MODEL=pinhole|fisheye`; CLI `--model`.
- **Posen, Overlay, Skalierung** richten sich nach dem Modell **in der geladenen Datei**.
- **Datei:** Schema `wsc.vision.calibration/2` mit `"distortionModel": "pinhole" | "fisheye"`.
  Schema `/1` wird als `pinhole` gelesen. Fisheye mit anderer Koeffizientenzahl als 4 oder
  unbekanntes Modell wird beim Laden abgelehnt.
- **OPC UA** (nur zusätzliche Felder): `CalibrationProgress.model` (Session), `result.model`
  (in jedem `result`, auch bei Fehlschlag), `FinishCalibration` `Summary.model` (bei sofortiger
  Ablehnung), `ActiveCalibrationInfo.model` (immer). Ohne Session: `ActiveCalibrationInfo.model`.
- **Technik:** modellabhängige OpenCV-Aufrufe in `tagloc/lens.py` (`undistort_points`,
  `project_points`, `calibrate`). Fisheye: `cv2.fisheye.calibrate` mit
  `RECOMPUTE_EXTRINSIC | CHECK_COND | FIX_SKEW`; bei `CHECK_COND` einmal ohne Prüfung neu
  (Log-Warnung). Flag-Konstanten zur Laufzeit nachschlagen: OpenCV 4 unter `cv2.fisheye.*`,
  OpenCV 5 unter `cv2.*` mit **anderen Zahlenwerten**.

---

## 13. Schnittstelle auf einen Blick

Vollständiger Vertrag. Wer dagegen entwickelt, braucht weder Code noch dieses Repo.
Ergänzungen sind Schnittstellenänderungen: erst Arbeitsplan unter
`doc/projektdoku/arbeitsplaene/` und Fach-MD anpassen, dann den Code (siehe `CLAUDE.md`).

### 13.1 Adressen

`<vision>` = Index von `http://launch-rm.de/vision`, `<mv>` = `http://opcfoundation.org/UA/MachineVision`,
`<amcm>`, `<di>` = Part-2-Namensräume. **Zur Laufzeit über die URI auflösen** (11.2).

| Zweck | NodeId | Zugriff |
| --- | --- | --- |
| Vision-System (Wurzel, Event-Quelle) | `ns=<vision>;s=VisionMachine` | Abo |
| Job starten | `…s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob` | Aufruf |
| Job abbrechen | `…AutomaticModeStateMachine.Stop` | Aufruf |
| Dauerbetrieb | `…AutomaticModeStateMachine.StartContinuous` / `.Abort` | Aufruf |
| Anhalten / entstören | `…VisionStateMachine.Halt` / `.Reset` | Aufruf |
| Letztes Ergebnis (JSON) | `ns=<vision>;s=VisionMachine.LatestResultJson` | Lesen / Abo |
| Letztes Ergebnis (40100) | `…ResultManagement/Results/LatestResult` → `ResultContent[0]` | Lesen |
| Livestream | `ns=<vision>;s=VisionMachine.LatestCameraFrame` | Abo |
| Overlay-Modus | `ns=<vision>;s=VisionMachine.CameraStreamMode` | **Schreiben** |
| Kalibrier-Fortschritt | `ns=<vision>;s=VisionMachine.CalibrationProgress` | Abo |
| Kalibrierung steuern | `…VisionMachine.{StartCalibration,CaptureCalibrationSample,FinishCalibration,AbortCalibration}` | Aufruf |
| **Zustand der Anlage** | `ns=<vision>;s=VisionMachine.VisionAsset.Health.DeviceHealth` | Lesen / Abo |
| Zustandsalarme | `…VisionAsset.Health.DeviceHealthAlarms.{FailureAlarm,CheckFunctionAlarm,OffSpecAlarm}` | Event-Abo auf `VisionMachine` |
| **Zustand der Kamera** | `ns=<vision>;s=VisionMachine.VisionAsset.ImageSensor.Health.DeviceHealth` | Lesen / Abo |
| Part-10-Programm | `ns=<vision>;s=VisionProgram` | 13.5 |

Knoten mit Vorbehalt: `LatestCameraFrame`, `CameraStreamMode` nur mit `camera_stream`;
`CalibrationProgress` und Kalibriermethoden nur mit `apriltag`; `VisionAsset`-Knoten nur mit
`assets`. Abo auf fehlenden Knoten liefert keinen Wert.

### 13.2 `StartSingleJob`

Eingang `(MeasId, PartId, RecipeId, ProductId, Parameters)`: Strings, `Parameters` max. 16.
Ausgang `(JobId: String, Error: Int32)`. Nicht blockierend. `RecipeId`: `calibration` oder
`apriltag`; leer/unbekannt → `Error=4`. Fehlercodes: Tabelle in Abschnitt 5.

### 13.3 Events

Alle von `VisionMachine`, MachineVision-Namensraum. Ohne Typliste kein Payload (7.1); Abo auf
`VisionMachine` (7.2).

| Event | Wann |
| --- | --- |
| `JobStartedEvent` | Job angenommen |
| `StateChangedEvent` | jeder Zustandswechsel beider Automaten |
| `AcquisitionDoneEvent` | Aufnahme fertig |
| `ResultReadyEvent` | Ergebnis da, **JSON in `ResultContent[0]`** |
| `ReadyEvent` | wieder aufnahmebereit |

Zustandsalarme (OPC 40100-2, DI-Namensraum, von `VisionMachine`): `FailureAlarmType` (900,
kein Bild), `OffSpecAlarmType` (500, veraltet), `CheckFunctionAlarmType` (300, Neustart). Höchstens
einer aktiv; `NORMAL` löscht den vorherigen. Details 11.4.

Korrelation über `jobId`; kein Polling nötig.

### 13.4 Nutzlast

`ResultReadyEvent` und `LatestResultJson`: gleiches JSON, Schema `wsc.vision.detections/1`,
Felder in Abschnitt 6.

### 13.5 Part-10-Programm

| Knoten | Richtung | Inhalt |
| --- | --- | --- |
| `VisionProgram/ParameterSet/RecipeId` | schreiben | wie oben |
| `VisionProgram/ParameterSet/Continuous` | schreiben | `Boolean`: Dauerbetrieb |
| `VisionProgram/ResultSet/JobId` | lesen | laufende bzw. letzte Job-Id |
| `VisionProgram/ResultSet/ErrorCode` | lesen | Fehlercode aus Abschnitt 5 |
| `VisionProgram/ResultSet/ExecutionMode` | lesen | Ausführungsart |

`ResultSet` spiegelt außerdem `LatestResultJson`, `LatestCameraFrame`, `CameraStreamMode`,
`CalibrationProgress`. Details: [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).

### 13.6 Betrieb

| | |
| --- | --- |
| Endpoint | `opc.tcp://<LAN-IPv4>:4840/raspi/server/`, `NoSecurity` |
| systemd | `opcua-server.service`, `Restart=always`, `RestartSec=5` |
| Auffindbar | mDNS `_opcua-tcp._tcp.local.` **und** LDS-Anmeldung beim Aggregation-Server (für den Zellbetrieb zählt LDS) |
| Zwei Instanzen | Decken-Pi (`cam_ceiling`, Picamera2) und Hand-Pi (`cam_flange`, RealSense): gleiche Schnittstelle, eigene `visionSystemId` und `frameId` |

### 13.7 Was ein Client mindestens können muss

1. Verbinden, Namensraumindizes über URIs auflösen.
2. Auf `VisionMachine` abonnieren, **mit** Event-Typliste.
3. `StartSingleJob` aufrufen, `Error` auswerten, `JobId` merken.
4. `ResultReadyEvent` → `ResultContent[0]` als JSON, Zuordnung über `jobId`.
5. Verbindungsabriss überstehen und neu verbinden (absichtlicher Neustart bei hängender Kamera, 11.4).
6. **Kamerazustand aus `DeviceHealth` lesen, nicht aus dem Bild.** Ampel: Zustandsknoten;
   Übergänge: zusätzlich die Alarme.

Punkt 5 ist im Backend noch offen (Risiko R9 in [`vision-system-integration.md`](vision-system-integration.md)).
Punkt 6 ist im Frontend umgesetzt (Ampel im Kamera-Panel).

## 14. Layer-2-Lauf (nur Hand-Pi)

Der Hand-Pi führt die Feinmessung selbst: (1) an den Welttag fahren und ankern, (2) jedes Modul
mit der **gemessenen** Basis neu planen, (3) jedes Modul messen. Frontend und Backend reichen
nur Posen weiter. Code: `src/vision_server/layer2/`; Formate:
[`arbeitsplaene/layer2-lauf-hand-pi.md`](arbeitsplaene/layer2-lauf-hand-pi.md).

Nur mit `AprilTagProfileConfig.hand_eye_path` (Pi mit `frame_id = cam_flange`). Unter `VisionMachine`
und `VisionProgram` verlinkt:

| Element | NodeId | Signatur / Typ |
| --- | --- | --- |
| `StartLayer2Run` | `ns=<vision>;s=VisionMachine.StartLayer2Run` | `(RunJson: String) -> (Error: Int32)` |
| `ReportRobotPose` | `ns=<vision>;s=VisionMachine.ReportRobotPose` | `(ReportJson: String) -> (Error: Int32)` |
| `Layer2Target` | `ns=<vision>;s=VisionMachine.Layer2Target` | String, `wsc.vision.layer2.target/1`, `""` ohne Ziel |
| `Layer2Status` | `ns=<vision>;s=VisionMachine.Layer2Status` | String, `wsc.vision.layer2.status/1` |

**Ablauf:**
1. `StartLayer2Run` mit Roboterbasis und Modulen aus Layer 1 (Tag-Lagen aus dem Katalog, `wsc.vision.layer2.run/1`).
2. `Layer2Target` abonnieren. Bei Ziel: Roboter mit dem Flansch auf `flangeInBase` fahren.
3. `ReportRobotPose` mit der **gemeldeten** Flanschpose. Bei gescheiterter Fahrt `reached: false`;
   der Pi startet dann einen `apriltag`-Job mit dieser Pose (gleiche Events wie `StartSingleJob`).
4. Nächstes Ziel in `Layer2Target`; `Layer2Status` trägt je Schritt `status`, `detail`, bei Modulen
   `result.frameId`, `result.detections`.
5. `state` am Ende `finished` oder `aborted`. `Stop` bricht jederzeit ab.

**Welttag vs. Modul:** Die Kamera steht 0,3 m vor dem Tag. Flansch = `inv(T_flansch_cam)`,
Fahrziel = `inv(T_world_base)`. Welttag: Pose aus der Tag-Map (der Basis nächste). Modul:
Modulpose aus Layer 1 mal Tag-Lage (der Tag, der am meisten zur Basis zeigt).

**Flansch-Frame:** `flangeInBase` = Werkzeugflansch ohne Greifer; darauf wird `T_flansch_cam`
kalibriert. Links: EVA/Demo1 `model_6`, UR5e `flange`, FR3 `eef_attach_point`.

**Annäherung** ([`arbeitsplaene/layer2-annaeherung.md`](arbeitsplaene/layer2-annaeherung.md)): vor
jedem Anker- und Mess-Schritt ein Schritt `purpose: "approach"`, Kamera 0,6 m vor dem Tag
(`options.approachStandoffM`, `0` = aus). Geankert und gemessen wird im nahen Bild.

**Anker:** `locate_modules` überspringt Welttags; der Anker-Job endet oft mit `DETECTION_FAILED`.
Er gilt als geglückt, wenn danach ein **neuer** `RobotAnchor` in der Quelle steht. Ohne Anker
bricht der Lauf ab.

| Code | Wann |
| --- | --- |
| `INVALID_ARGUMENT` | JSON ungültig |
| `INVALID_STATE` | keine Hand-Auge-Datei; kein vermessener Welttag; `ReportRobotPose` ohne wartendes Ziel oder falscher `stepIndex` |
| `BUSY` | Job, Kalibrierung oder anderer Lauf aktiv |

**Sperren:** Während eines Laufs `StartCalibration`, `SetTagMap` → `BUSY` (neue Tag-Map verwirft
den Anker). `StartSingleJob` bleibt zum Debuggen erlaubt.

**Offen:** Die Roboter-Server haben keinen kartesischen Fahrbefehl. Bis dahin nur Trockenlauf
(Client meldet das Ziel selbst als erreicht); der Pi ankert dann an einer nicht gefahrenen Pose.

### 14.1 Schräger Blick

Liegt der frontale Blick außer Reichweite, kippt der Planer die Kamera um die Tag-Mitte zur Basis,
in Stufen bis `options.maxTiltDeg` (Standard 60°, `0` = immer frontal). `Layer2Target.tiltDeg`
zeigt den Winkel. Details: [`arbeitsplaene/layer2-schraege-ansicht.md`](arbeitsplaene/layer2-schraege-ansicht.md).

## 15. Hand-Auge-Kalibrierung über OPC UA (nur Hand-Pi)

Bestimmt `T_flansch_cam` aus dem Frontend, nach dem Muster aus Abschnitt 12. Formate:
[`arbeitsplaene/handeye-ueber-opcua.md`](arbeitsplaene/handeye-ueber-opcua.md). Nur mit `hand_eye_path`;
unter `VisionMachine` und `VisionProgram`.

| Element | Signatur / Typ |
| --- | --- |
| `StartHandEyeCalibration` | `(OptionsJson: String) -> (Error: Int32)`, `wsc.vision.handeye.start/1` |
| `CaptureHandEyeSample` | `(SampleJson: String) -> (Error: Int32)`, `wsc.vision.handeye.sample/1` |
| `FinishHandEyeCalibration` | `() -> (Error: Int32)` |
| `AbortHandEyeCalibration` | `() -> (Error: Int32)` |
| `HandEyeStatus` | String, `wsc.vision.handeye.status/1` |

**Ablauf:** (1) Tag wählen, der liegen bleibt. (2) Roboter in verschiedene Posen fahren, je Pose
`CaptureHandEyeSample` mit der **gemeldeten** Flanschpose. (3) `FinishHandEyeCalibration`: Park–Martin
(Lage des Tags nicht nötig), schreibt `data/handeye/cam_flange.json`, alte Datei als Sicherung daneben,
sofort aktiv; ein Anker wird verworfen.

Ist `tagInBase` aus dem Katalog bekannt, prüft der Pi nur dagegen (`knownTagCheckM`/`Deg`).

**Sperren:** Während einer Sitzung `StartLayer2Run`, `StartCalibration`, `SetTagMap` → `BUSY`.

### 15.1 Schachbrett statt Tag

Ein Kalibrier-Schachbrett als festes Ziel. Labor-Brett: 7×8 Felder = **6×7 Innenecken**, 23 mm
(Maße frei eintragbar, 15.7). Beispiel:

```json
{ "schema": "wsc.vision.handeye.start/1",
  "board": { "type": "chessboard", "cols": 5, "rows": 6, "squareSizeM": 0.023 } }
```

- `cols`/`rows` = Innenecken. `cols + rows` muss **ungerade** sein, sonst `INVALID_ARGUMENT`
  (nur dann ist die 180°-Drehung eindeutig).
- Board-Frame: Ursprung in der ersten Innenecke mit schwarzem Feld, +z ins Brett
  (`tagloc.boards.canonical_chessboard_corners`).
- Jede Aufnahme braucht das **ganze** Brett; sonst `DETECTION_FAILED`, Sitzung läuft weiter.
- Status: `tagId = -1`, `target = {"type": "chessboard", ...}`, `samples[].camToTag` = `T_cam_board`.

### 15.2 Gütezahlen und Ausreißer

- `result.rmsPositionM`/`rmsRotationDeg` = echtes RMS; `maxPositionM`/`maxRotationDeg` = Maximum
  der Abweichung vom Mittel.
- Nach `Finish` hat jede Aufnahme `samples[i].residualM`, `residualDeg`, `rejected`. Abweichung
  über Median + 3 × MAD (mindestens 2 mm / 0,3°) → verworfen und neu gelöst (höchstens ein Viertel,
  nie unter 4). `result.rejectedCount`, `result.rejectedSamples`; `sampleCount` zählt nur verwendete.
- Datei und `current` tragen `maxPositionM`/`maxRotationDeg`.

### 15.3 Gespeicherte Kalibrierung im Status

`HandEyeStatus.current` = `{poseFlangeCam, calibrationId, rmsPositionM, rmsRotationDeg, sampleCount}`
aus `data/handeye/cam_flange.json`, ohne Datei `null`. Auch nach Neustart und ohne Sitzung verfügbar
(Frontend zeigt damit die Handkamera am Flansch des Roboter-Zwillings).

### 15.4 `MeasureBoard`: Brett zentrieren

Bringt das Brett vor `CaptureHandEyeSample` in die Bildmitte. Der Pi misst nur, ohne Fahrbefehle.
Details: [`arbeitsplaene/hand-auge-brett-zentrieren.md`](arbeitsplaene/hand-auge-brett-zentrieren.md).

| Element | Signatur / Typ |
| --- | --- |
| `MeasureBoard` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, `wsc.vision.measureboard.request/1` → `…result/1` |

- **Anfrage:** `{"board": {"type","cols","rows","squareSizeM"}?, "frames": 1..10 (3)?}`; ohne `board`
  das Brett der Konfiguration. Nur Bilder **nach** dem Aufruf (gilt auch für `MeasureTags`, `CaptureHandEyeSample`).
- **Error:** `OK` (auch bei `found: false`), `DETECTION_FAILED` (`error: "NO_FRAME"`),
  `INVALID_ARGUMENT`, `INVALID_STATE` (nicht `Operational`), `BUSY` (Job oder Kamera-Kalibrierung).
  Läuft auch während einer Hand-Auge-Sitzung.
- **Ergebnis-JSON:** `found`, `complete`, `frameCount`, `framesRequested`, `framesUsed`, `imageSize`,
  `capturedAt`/`capturedUntil`, `frameId`, `frameConvention`, `intrinsicsSource`, `calibrationId`,
  `board`, `error`, `message`.
  - Ganzes Brett (`complete: true`): `poseInCamera {position, orientation}`, `poseOk`,
    `reprojectionErrorPx`, `distanceM`, `viewAngleDeg`, `cornersPx`, `centerPx`, `offsetPx`, `nearEdge`.
  - Teilweise (`found: true`, `complete: false`): `partial {gridCols, gridRows, cornerCount, bboxPx}`,
    `centerPx`, `offsetPx`, `nearEdge`. Keine Pose (Ursprung eines Teilrasters ist beliebig).
  - Nichts (`found: false`): kein Fehler.
  - `offsetPx` = Bildmitte → Eckenschwerpunkt (x rechts, y unten); dorthin fährt die Kamera.
- **Schwarzer Träger:** Helle Felder ohne helle Ruhezone werden am invertierten Graubild gefunden
  (zuerst Original, dann invertiert). Ursprung-Feldfarbe bleibt am Original.
- **Rechenzeit:** Suche auf 640 px, dann Nachschärfen im Vollbild (< 0,3 px). Teilraster nur im letzten Bild.
- **`"diagnose": true`:** zusätzlich `diagnosis` mit `largestGrid {cols, rows}`, `sbExact`,
  `classicExact`, `sbSwapped`, `tried`, `imageSize`, `grayMin/Max/Mean`, `saturatedFraction`. Nur zum Analysieren.
- **`HandEyeStatus.samples[i].reprojectionErrorPx`**: mittlerer Fehler dieser Aufnahme, auch bei
  verworfenen; `null` ohne Fund.

### 15.4a Verfeinerung, Vorhersagefehler, Status

- **Verfeinerung:** Mit Brett wird `T_flansch_cam` mit Brettlage und Brettmaßstab gegen die Eckpixel
  verfeinert (`refine_hand_eye_robust`, Start Park–Martin). Ausreißer nach Pixelfehler je Aufnahme
  verworfen. Ohne Brett: Park–Martin, Hinweis in `result.warnings`.
- **`FinishHandEyeCalibration`:**
  - `INVALID_STATE`, wenn ≥ 3 angenommene Aufnahmen und < 3 Drehachsen (ab 5°). Sitzung bleibt offen,
    `outcome: "refused"`.
  - `INVALID_STATE`, wenn < 3 verwertbare Posen. Sitzung offen, `outcome: "failed"`.
  - `INTERNAL` und Zustand `failed`, wenn berechnet, aber nicht speicherbar.
  - Sonst `OK`, Zustand `finished`.
- **`HandEyeStatus`** (Schema `wsc.vision.handeye.status/1`):
  - Top-level `intrinsicsSource`: `"calibration"`, `"realsense_factory"`, `"placeholder"`, `null`.
  - `samples[i]`: `looErrorPx`, `looErrorM` (Vorhersagefehler ohne diese Aufnahme; nur nach `Finish`
    und nicht für verworfene; sonst `null`).
  - `result`: `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`,
    `refinementConverged`, `targetInBase`, `warnings`, `sessionPath`. Verfeinerte Werte ohne
    Verfeinerung `null`. `targetInBase` immer gesetzt: `{"position": [x,y,z], "orientation": [x,y,z,w]}`,
    Meter, Basis-KS (mit Brett: Brettursprung; sonst Mittel über `F · X · C`).
  - `warnings`: deutsche Texte, u. a. Werks-/Platzhalter-Intrinsik, fehlende Verfeinerung,
    `|boardScale − 1| > 0,3 %` („Brett nachmessen / Intrinsik pruefen“).
  - `current`: zusätzlich `refinedReprojectionRmsPx`, `boardScale`, `looErrorRmsPx`, `looErrorRmsM`,
    `intrinsicsSource`.
- **`data/handeye/cam_flange.json`:** dieselben Zusatzschlüssel, wenn vorhanden.
- **Sitzungsdatensatz:** `data/handeye/sessions/<UTC-Start>/session.json` (Schema
  `wsc.vision.handeye.session/1`, `outcome` finished | refused | failed) plus `sample_NNN.png`.
  Offline neu rechnen:
  `python -m tagloc.cli.calibrate_handeye --session data/handeye/sessions/<UTC-Start> [--out data/handeye/cam_flange.json] [--no-scale]`
- **Tag-Pfad:** Beide IPPE-Lösungen mit `solvePnPRefineLM` verfeinert; kleinere Reprojektion gewinnt.
  Mehrdeutigkeit aus den rohen IPPE-Fehlern.
- **Subpixel-Ecken** (Presets `cam_flange`, `cam_ceiling`, Verfahren `subpix`):
  `VISION_SUBPIXEL_CORNER_REFINEMENT`: `0`/`none`/`false` aus; `1`/`true` an; `apriltag` oder `subpix`
  an mit diesem Verfahren; anderer Wert → Start bricht mit `ValueError` ab.

### 15.5 Rohbild-Aufnahme und Offline-Kalibrierung

Schachbrett-Startknopf im Frontend: `captureOnly: true` in `StartHandEyeCalibration`. Der Pi nimmt
Bilder mit dem **konfigurierten Kamerastream** (kalibrierte Intrinsik) auf und rechnet nichts.
`CaptureHandEyeSample` erhält zusätzlich `poseId: "pose_NNN"`, `poseGroup` (`A`–`E`) und
`jointAnglesRad` (sieben Winkel); `flangeInBase` = FK der gemessenen Gelenkwinkel.

Je Aufnahme sofort unter `data/rohbilder/calibration/<UTC>_<ID>/poses/pose_NNN/`:
`camera.png` (unverändert), `robot_state.json` (Pose-ID, 4×4 `T_base_flange`, Gelenkwinkel,
Bildzeitstempel). `config.json`: Brett, Konventionen, Intrinsik.

- `HandEyeStatus.sessionPath` nennt den Ordner. `Finish` schließt nur die Aufnahme; keine
  Berechnung, keine Aktivierung. `Abort` behält die Bilder.
- Bewegen sich Gelenke während einer Aufnahme, markiert `CaptureHandEyeSample` mit
  `invalidatePoseId` und `reason` die Pose als ungültig (`quality.json`; Rohbild und
  `robot_state.json` bleiben). Die Offline-Auswertung schließt sie aus.
- Offline: `python -m tagloc.cli.calibrate_handeye_offline <sitzungsordner> [--pnp iterative|ippe]`.
  Ergebnisse nur in `results/` (Detektionscache, Experiment-JSON, HTML-Vergleich,
  `candidate_handeye_<pnp>.json`). Kandidat: kompatibel mit dem Hand-Auge-Format, **nicht** automatisch aktiv.
- Tag- und Brett-Aufrufe ohne `captureOnly` behalten ihre Semantik.

**Brettangaben** (frei eintragbar, 15.7), Beispiele: Schachbrett
`{"type":"chessboard","cols":7,"rows":10,"squareSizeM":0.023}` (Innenecken);
ChArUco `{"type":"charuco","cols":11,"rows":7,"squareSizeM":0.025,"markerSizeM":0.0185,"dictionary":"DICT_5X5_100"}`
(Felder). Gilt auch für `MeasureBoard`; wird unverändert in `config.json` gespeichert.

ChArUco braucht mindestens 25 zugeordnete Innenecken je Aufnahme; IDs und Objektpunkte werden je
Pose gespeichert und für PnP und Reprojektions-Optimierung verwendet. Intrinsik und Bildstrom bleiben
die konfigurierten Werte. Keine neue OPC-UA-Methode.

### 15.6 Gesamtkalibrierung auf dem Pi: `fullCalibration`

Intrinsik und Hand-Auge werden aus denselben Rohbildern auf dem Pi gerechnet und gemeinsam übernommen.

- **Start:** `StartHandEyeCalibration` mit `"fullCalibration": true` in den Optionen plus `board`.
  Ohne Brett ungültig. Aufnahme wie bei `captureOnly` (Gruppen A–F). `captureOnly` und
  `fullCalibration` zusammen sind ungültig.
- **Rechnung in `FinishHandEyeCalibration`** (ohne Werks-Intrinsik, ohne Startwert):
  - (a) Intrinsik: `cv2.calibrateCamera` über Bilder mit ≥ 12 ChArUco-Ecken bzw. vollständigem
    Schachbrett; Modell k1, k2, p1, p2, k3; ≥ 10 Bilder.
  - (b) Hand-Auge: PnP je Pose (≥ 25 ChArUco-Ecken bzw. volles Brett, PnP-RMS ≤ 1,5 px); Start Park
    (bzw. `park_numpy` unter OpenCV 5); dann gemeinsame Verfeinerung Hand-Auge + Brettlage mit scipy
    `least_squares` (`soft_l1`); ≥ 10 Posen.
  - Flanschpose = nominelle FK aus gemessenen Gelenkwinkeln; keine Kinematik-Korrektur.
  - Zusätzlich `<sitzung>/results/intrinsics_pi.json`, `<sitzung>/results/handeye_pi.json`.
- **Zu wenig:** `INVALID_STATE`, „Nicht berechnet: …“; Sitzung bleibt offen. Fehlt scipy:
  „scipy fehlt auf dem Pi (pip install scipy)“.
- **Schreiben:** `data/calibration/cam_flange.json` und `data/handeye/cam_flange.json`, je mit
  `.bak` der alten Datei, gleiche `calibrationId` `cam_flange@<sitzung>-pi`, beide live. Scheitert
  das Schreiben, werden beide aus der Sicherung wiederhergestellt.
- `config.json` der Sitzung: `intrinsics: null` bis zum Abschluss.
- **Status:** `HandEyeStatus.fullCalibration` (bool). `result`:

  ```json
  {"kind": "intrinsicsAndHandEye", "sessionPath": "...",
   "intrinsics": {"imageSize", "fx", "fy", "cx", "cy", "distortion", "rmsPx", "sampleCount", "calibrationId"},
   "handEye": {"translationMm", "rotationDeg", "reprojectionRmsPx", "boardSpreadRmsMm", "boardSpreadRmsDeg", "sampleCount", "initialMethod"},
   "excluded": [{"poseId", "reason"}],
   "backups": {"intrinsics", "handEye"}}
  ```

### 15.7 Brettmaße frei eintragbar

Gilt für `board` in `StartHandEyeCalibration` und `MeasureBoard`.

- **Schachbrett:** `cols`/`rows` Innenecken, ≥ 3; `cols + rows` ungerade; `squareSizeM` 0,005–0,100 m.
- **ChArUco:** `cols`/`rows` Felder, ≥ 3; `squareSizeM` 0,005–0,100 m; `markerSizeM` > 0 und < `squareSizeM`;
  `dictionary` aus `DICT_4X4`, `DICT_5X5`, `DICT_6X6`, `DICT_7X7` mit Endung `_50`, `_100`, `_250` oder
  `_1000`; `floor(cols·rows/2)` darf die Dictionary-Größe nicht überschreiten.
- Maße gehen **ohne Skalierungsfaktor** ein. Labor-Brett nachgemessen: Feld 24,982 mm, Marker 18,487 mm.
- Frontend (`webskillcomposition`, Branch `ADP_Lokaliserung`): Feldmaß, Markermaß, Dictionary eintragbar;
  Browser merkt sich die Werte.

## 16. Zellen-Überwachung (nur Deckenkamera)

Meldet **bestätigte** Modul-Änderungen (verschoben, neu, entfernt). Formate und Defaults:
[`arbeitsplaene/zellen-ueberwachung.md`](arbeitsplaene/zellen-ueberwachung.md). Nur mit AprilTag und ohne
`hand_eye_path`; unter `VisionMachine` und `VisionProgram`.

| Element | Signatur / Typ |
| --- | --- |
| `StartCellWatch` | `(OptionsJson: String) -> (Error: Int32)`, `wsc.vision.cellwatch.start/1` |
| `StopCellWatch` | `() -> (Error: Int32)` |
| `PauseCellWatch` | `(ReasonText: String, MaxPauseS: Double) -> (Error: Int32)` |
| `ResumeCellWatch` | `() -> (Error: Int32)` |
| `CellWatchStatus` | String, `wsc.vision.cellwatch.status/1` |

- **Referenz:** Ergebnis eines Layer-1-Jobs (`referenceJobId`, einer der letzten 20) oder `null`
  (aktuelle stabile Beobachtung, „Ignorieren“).
- **Gültige Bilder:** nur mit gemeinsam gelöster Pose (`joint`, ≥ 2 Welttags). Andere: „keine Aussage“,
  nicht „Tag fehlt“.
- **Auswertung:** Median je Tag über ein Zeitfenster; Bestätigungszeit und Hysterese.
- **Ressourcen:** geteilte Kamera, eigener Detektor. Während Job oder Kalibrierung: `paused`.
- **`visibleTagIds`** (additiv, int-Array): alle erkannten Tag-IDs des letzten Bildes, auch bei
  `paused`; `null` vor dem ersten Bild und bei `off`.
- **Ein Welt-Tag genügt (`minWorldTags`, Standard 1):** Die Kamera ist fest, ihre Pose wird aus Bildern
  mit ≥ 2 Welt-Tags (gemeinsames PnP, ~6 mm) geschätzt, gemerkt (Mittel der letzten 20) und bei genau
  einem sichtbaren Welt-Tag benutzt. Aus einem Tag allein wäre die Pose aus 2,5 m um Zentimeter ungenau
  (Median 40 mm). Mit gemerkter Pose: Modulfehler median 12 mm, max. 46 mm.
  - Der eine Welt-Tag wird auf Konsistenz geprüft: Ecken > `storedPoseMarginPx` (2 px) über dem
    gespeicherten Wert → Kamera vermutlich bewegt, Bild zählt nicht. Nach 5 solchen Bildern in Folge wird
    die Pose verworfen (wieder zwei Welt-Tags nötig). Erkennbar: Verschiebung ab ~7 mm, Drehung ab 0,1°.
  - `minWorldTags: 2` = altes Verhalten (nur mit zwei Welt-Tags gleichzeitig).
  - Neue Tag-Map (`SetTagMap`) verwirft die Pose.
- **Persistenz:** `cellwatch_camera_pose.json` in `data/calibration/` (mit Tag-Map und Grundlinie je
  Welt-Tag); gespeichert ab 5 Posen, danach höchstens alle 10 min; beim Start wiederhergestellt.
  - **Referenz-Scan:** `StartCellWatch` mit `referenceJobId` übernimmt `cameraLocalization.poseInWorld`
    (solver `joint`), wenn keine Pose gemerkt ist oder sie > 15 mm / 0,5° abweicht.
  - Wiederhergestellte/übernommene Pose wird ebenso geprüft; passt der Welt-Tag nicht, fällt sie samt Datei weg.
    Fehlende Datei ist kein Fehler (Pause bis zwei Welt-Tags).
  - **Status:** `cameraPose: {source, poseCount, savedAt}`; `source` = `measured` | `file` | `scan` | `null`.
- **Pausengründe** in `message`: „kein Welt-Tag im Bild“, „Kamerapose noch nicht bekannt – erst müssen 2
  Welt-Tags gleichzeitig zu sehen sein“, „Welt-Tag N passt nicht zur gespeicherten Kamerapose … Kamera
  bewegt?“, „… Pose verworfen“.
- **Flache Module** (Hochachse ≤ 30° zur Welt-Z): Gierwinkel zählt auch bei mehrdeutiger Pose. Senkrechte:
  mehrdeutig heißt nur Position.
- **`pending`** (additiv): `[{tagId, moduleId, kind, since, distanceM, rotationDeg, confirmInS}]`, noch
  unbestätigt. `since`: bei `moved` Beginn der Abweichung, bei `disappeared` letzte Sichtung, bei `appeared`
  erste Sichtung. `confirmInS` bei `disappeared` geschätzt. Nach Bestätigung wandert der Eintrag nach `changes`.
- **Pause von außen:** Das Frontend pausiert, solange der Roboter fährt (Arm und Greifer verdecken Tags).
  `PauseCellWatch("Roboter fährt", 0)` setzt `state: "paused"`, `message = ReasonText` (leer: „Ueberwachung
  pausiert“). Kein Bild wird ausgewertet, nichts in den Verlauf geschrieben. `MaxPauseS <= 0` = 600 s,
  > 3600 wird auf 3600 gekürzt. Nach Ablauf läuft die Überwachung von selbst weiter.
  `ResumeCellWatch` beendet die Pause; danach beginnen alle Timer neu, alle Tags gelten als eben gesehen,
  `pending` ist verworfen, `changes` bleiben. Beide idempotent (zweites Pause ersetzt Grund und Frist;
  Resume ohne Pause = `OK`). Ist die Überwachung nicht aktiv (`off`, `failed`): `INVALID_STATE`;
  `NaN`/`inf` als `MaxPauseS`: `INVALID_ARGUMENT`. Neu scharf schalten hebt die Pause nicht auf;
  `StopCellWatch` schon. Status: `manualPause: {reason, resumesAt}` oder `null`. Job-/Kalibrier-Pause bleibt unberührt.
- **Neu scharf schalten:** `StartCellWatch` übernimmt den Bildverlauf und die Erkennungsraten. Mit gleicher
  `referenceJobId` bleiben Kandidaten, Meldungen und `referenceId` erhalten.
- **`tagVisibility`** (additiv): je Tag `{tagId, seenRatio, framesChecked, lastSeenAt, unreliable}`.
  `seenRatio` = Anteil gültiger Bilder der letzten `missingPersistS` Sekunden mit Sichtung (stabile Antwort auf
  „ist der Tag im Bild?“). `unreliable` bei < 50 %; `message`: „unsicher erkannt: Tag 2 (20 %) …“.
- **Warum „fehlt“, obwohl der Scan ihn findet:** Der Scan wertet 3–5 Bilder aus und meldet bei einem Treffer;
  die Überwachung prüft einzelne Bilder. Grenzfälle (klein, weit, schräg, Rand, Blendung) fallen dort oft aus.
- **Timer `disappeared`:** Anzahl gültiger Bilder ohne den Tag `n` mit `(1 − Rate)^n < 10⁻⁵`, mindestens 3,
  höchstens 60. Rate = untere Grenze des 95-%-Konfidenzintervalls über den Verlauf; ohne Verlauf 0 (dann
  Obergrenze). Jede Sichtung setzt zurück. Zeit in `paused` zählt, aber nicht als „fehlt“.

## 17. MeasureTags: Tags in Kamerakoordinaten (nur Hand-Pi)

Die Steuerung plant und fährt, der Hand-Pi misst. Steht der Roboter, ruft die Steuerung `MeasureTags`
auf. Formate: [`arbeitsplaene/layer2-measuretags.md`](arbeitsplaene/layer2-measuretags.md).

| Element | Signatur / Typ |
| --- | --- |
| `MeasureTags` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, `wsc.vision.measuretag.request/1` → `…result/1` |
| `LastTagMeasurement` | String, letztes Ergebnis, nur Anzeige |

- Misst `T_cam_tag` (OpenCV-Kamera-KS), ohne Welt-KS und CAD-Versatz. Nur Bilder **nach** dem Aufruf,
  Mittel über standardmäßig 3.
- `ambiguous`: Orientierung mehrdeutig → auf Position regeln, nicht auf Drehung.
- `nearEdge`: je Tag, welchen Bildrändern er nahe kommt (innerhalb 5 %); für gezieltes Nachschwenken.
  Fahrbefehle schickt der Pi nicht.
- `intrinsicsSource`: `realsense_factory` ohne Kalibrierdatei; gilt dann auch für Jobs und Livestream.

## 18. SaveRawImage: Kamera-Vollbild als PNG

Speichert das aktuelle Vollbild verlustfrei als Rohmaterial. Plan:
[`arbeitsplaene/rohbild-speichern.md`](arbeitsplaene/rohbild-speichern.md).

| Element | Signatur / Typ |
| --- | --- |
| `SaveRawImage` | `() -> (FileName: String, Error: Int32)`, am Vision-System und unter `VisionProgram` |

- Ablage `data/rohbilder/<frame_id>_<YYYYmmdd-HHMMSS>.png`; bei Namenskollision `_2`, `_3`, …; nie überschrieben.
  `frame_id` aus dem AprilTag-Profil, sonst Name des Vision-Systems.
- `FileName`: repo-relativer Pfad mit `/`; leer bei Fehler.
- `Error`: `0` gespeichert, `1` noch kein Bild, `3` Job läuft, `6` Schreiben fehlgeschlagen.
- `VisionServerConfig.raw_image_dir`; `None` = Methode nicht angelegt. `data/` ist versioniert: große Bilder bewusst committen.

## 18a. SaveUseCaseImage: Use-Case-Foto je Versuch

Nach dem Ablegen eines Würfels in Use Case 1 fährt der Roboter die Handkamera (Hand-Pi, PI_2) in die
Fotopose: 20 cm senkrecht über der Soll-Mitte der Ablage (aus `poseFlangeCam`). Die Methode speichert Bild
und Metadaten je Versuch und misst die Ablage (Δx, Δy, Δθ gegen die Zielscheibe). Ablauf:
[`arbeitsplaene/use-case-foto.md`](arbeitsplaene/use-case-foto.md); Zielscheibe und Kennzahlen:
[`arbeitsplaene/use-case-messung.md`](arbeitsplaene/use-case-messung.md).

| Element | Signatur / Typ |
| --- | --- |
| `SaveUseCaseImage` | `(Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error: Int32)`, am Vision-System (`ns=<own_idx>;s=<vision_system_name>.SaveUseCaseImage`) und unter `VisionProgram` |

- **Ablage:** `data/use-case/reihe_NNN/versuch_NNN.png` und `versuch_NNN.json` (dreistellig). Versuchsnummer =
  höchste vorhandene + 1, Lücken bleiben. Exklusiv angelegt, nie überschrieben. Mit Messung zusätzlich
  `versuch_NNN_messung.png` (Marker, Toleranzkreise, Werte).
- **`Series`** ≥ 1, sonst `Error=2`.
- **`MetadataJson`:** Schema `wsc.usecase.trial/1` (Soll-Pose, Tisch, Route, Ist-Gelenke …). Kein gültiges
  JSON-Objekt → Text unverändert als `client_raw`. Inhalt wird nicht geprüft.
- **Bildwahl:** Bild, das ≥ 0,3 s nach dem Aufruf entstanden ist; Wartezeit höchstens 5 s. Während eines
  Jobs sofort `BUSY`.
- **`ResultJson`:** `TrialMeasurement.to_dict()` (Felder: Arbeitsplan `use-case-messung.md`); bei Fehler-Codes
  `""`. Messung läuft vor dem Speichern in einem Thread und landet in `messung`. **Ein Messfehler verhindert
  das Speichern nie:** Foto liegt, `{"ok": false, "grund": "..."}`. Ohne Kalibrierung: `Keine Kamerakalibrierung verfuegbar`.
- **Aufkleber-Versatz:** `data/use-case/aufkleber_versatz.json` (`{"x_mm", "y_mm"}`, Markermitte relativ zur
  Würfelmitte) wird abgezogen; fehlt die Datei, gilt `(0, 0)`. Bestimmung:
  `tools/use_case_auswertung.py <reihe> --aufkleber-kalibrieren N1 N2`.
- **`Error`:** `0` gespeichert; `1` INVALID_STATE (kein frisches Bild in 5 s); `2` INVALID_ARGUMENT (Reihe < 1);
  `3` BUSY; `6` INTERNAL (Speicherfehler). Codes 4, 5, 7 kommen nicht vor. Bei Fehler sind `FileName` und `ResultJson` leer.
- **JSON-Datei** `adp.usecase.trial/1`: `reihe`, `versuch`, `bild`, `gespeichert` (ISO 8601, lokal),
  `pi` (`frame_timestamp`, `frame_age_s`, `image_shape`, `frame_id`, `vision_system_name`, `vision_system_id`,
  `hostname`, `endpoint`, `camera_stream`, `apriltag`, `calibration`, `hand_eye`; nicht ermittelbare Felder fehlen),
  `messung` (immer bei neuen Versuchen), `client` (geparst) oder `client_raw`.
- **`pi.intrinsics`** (nur mit Kalibrierung): `source` (`calibration` | `realsense_factory` | `placeholder`),
  `calibrationId`, `imageSize` `[w, h]`, `cameraMatrix` (3x3), `distortionCoefficients`, `distortionModel`
  (`pinhole` | `fisheye`). Gleiche Herkunft wie `intrinsicsSource` der Hand-Auge-Sitzung.
- **Konfiguration:** `VisionServerConfig.use_case_dir`, Default `<Repo>/data/use-case`; `None` = nicht angelegt.
  Entsteht auf jedem Pi mit Kamera; aufgerufen wird sie vom Hand-Pi (PI_2).

## 19. Tag-Position einmessen: MeasureTagReference / PlanTagReferenceView (nur Hand-Pi)

Die Hand-Kamera misst die Lage eines Modul-Tags zu zwei Bezugskanten im selben Bild (ersetzt den Zollstock).
Die Steuerung fragt mit `PlanTagReferenceView`, wohin die Kamera soll (rechnet nur), fährt dorthin und ruft
`MeasureTagReference`. Ablauf: [`arbeitsplaene/tag-einmessen-bezugskanten.md`](arbeitsplaene/tag-einmessen-bezugskanten.md);
Verfahren: [`apriltag-referenz.md`](apriltag-referenz.md) Abschnitt 14.

| Element | NodeId | Signatur / Typ |
| --- | --- | --- |
| `MeasureTagReference` | `ns=<vision>;s=VisionMachine.MeasureTagReference` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, misst |
| `PlanTagReferenceView` | `ns=<vision>;s=VisionMachine.PlanTagReferenceView` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, rechnet nur |
| `LastTagReference` | `ns=<vision>;s=VisionMachine.LastTagReference` | String (JSON), nur Anzeige |

Unter `VisionMachine` und `VisionProgram`; nur Hand-Pi (`hand_eye_path`).

| Error | `MeasureTagReference` | `PlanTagReferenceView` |
| --- | --- | --- |
| 0 `OK` | gemessen; bei fehlenden Kanten `dofs` reduziert oder `none` | geplant, auch mit `fits: false` |
| 5 `DETECTION_FAILED` | `NO_FRAME`, `TAG_NOT_SEEN`; auch `ValueError` der Messung (z. B. Bildgröße passt nicht zur Kalibrierung) | entfällt |
| 2 `INVALID_ARGUMENT` | Anfrage ungültig | wie links |
| 1 `INVALID_STATE` | nicht `Operational` | wie links |
| 3 `BUSY` | Job, Kalibrierung, Hand-Auge-Sitzung, `MeasureTags` oder andere Einmessung | kein `BUSY` (kein Bild) |

- Ablehnung: `"<CODE>: Text"` im JSON; Mess-Fehler (`NO_FRAME`, `TAG_NOT_SEEN`): nackter Code. `ResultJson` immer gültiges JSON.
- `LastTagReference` nach jeder Messung, auch bei `DETECTION_FAILED`; nicht bei Ablehnung.
- **BUSY:** `MeasureTagReference` ↔ `MeasureTags` gegenseitig; `PlanTagReferenceView` nie gesperrt.

**Anfrage** `wsc.vision.tagreference.request/1` (beide Methoden):

```json
{ "schema": "wsc.vision.tagreference.request/1",
  "tagId": 7, "tagSizeM": 0.08,
  "moduleTagGuess": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "lines": [ { "id": "A", "start": [x,y,z], "end": [x,y,z] },
             { "id": "B", "start": [x,y,z], "end": [x,y,z] } ],
  "searchBandM": 0.015, "frames": 3,
  "view": { "lineExtentM": 0.10, "tiltDeg": 10 } }
```

- `moduleTagGuess`: `T_module_tag` aus dem Katalog (Startwert). `lines`: 1–4 Kanten im Modul-KS, je ≥ 2 cm, `id` eindeutig.
- Optional: `tagSizeM` (Vorrang Anfrage > Tag-Map > `config.tag_size_m`); `searchBandM` 0,015 (0,003–0,05 m);
  `frames` 3 (1–10); `view.lineExtentM` 0,10 ((0, 1] m); `view.tiltDeg` **10** (|tiltDeg| < 80).
- Nullquaternion ungültig; Orientierung wird normiert; unbekannte Felder ignoriert.

**Ergebnis** `wsc.vision.tagreference.result/1` (`MeasureTagReference`):
- `capturedAt`, `capturedUntil`, `imageSize`, `intrinsicsSource`, `framesUsed` (Bilder mit Tag), `debugDir` (19.1).
- `tag`: `poseInCamera`, `reprojectionErrorPx`, `viewAngleDeg`, `ambiguous`; `null` im Fehlerfall.
- `lines[]`: `id`, `found`, `offsetMm`, `angleDeg`, `supportPx`, `residualPx`, `contrast`, `candidates`,
  `ambiguous`, `reason` (`NOT_VISIBLE` | `NO_EDGE` | `PERPENDICULAR_TO_TAG`, nur bei nicht gefundenen).
- `correction`: `dofs` (`full` | `partial` | `none`), `deltaMm`, `deltaRotDeg`, `moduleTag`, `position`,
  `rotationDeg` (Katalogformat), `spreadMm`, `spreadDeg`, `residualMm`; `null` im Fehlerfall.
- `warnings[]`, `error` (`null` bei Erfolg).
- Mehrdeutige Tag-Pose: Orientierung bleibt der Startwert; nur der Ursprung wandert; Warnung.

**Planung** `wsc.vision.tagreference.view/1` (`PlanTagReferenceView`):

```json
{ "schema": "wsc.vision.tagreference.view/1",
  "camInTag": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "standoffM": 0.38, "fits": true, "uncovered": [], "error": null }
```

- `camInTag`: Kamerapose im Tag-KS (OpenCV, +Z aus der Linse; Blick entlang −Tag-z).
- `fits: false`: Tag immer im Bild; Kanten nacheinander nach Abstand zugenommen, solange alles bei
  `max_standoff` passt. `uncovered` nennt den Rest (ggf. `"tag"`).
- Ablehnung: `camInTag: null`, `fits: false`, `uncovered: []`, `error: "<CODE>: Text"`.

Die Anfrage ändert weder Tag-Map noch Welt-Tags. Modul-Tags und Pose stehen im Modulkatalog des Frontends.
Das Flanschziel rechnet die Steuerung selbst.

### 19.1 Live-Ansicht und Mess-Protokoll

| Element | NodeId | Signatur / Typ |
| --- | --- | --- |
| `SetTagReferencePreview` | `ns=<vision>;s=VisionMachine.SetTagReferencePreview` | `(RequestJson: String) -> (Error: Int32)` |
| `TagReferencePreviewStatus` | `ns=<vision>;s=VisionMachine.TagReferencePreviewStatus` | String (JSON), Abo, höchstens 3 Hz |
| `GET /tagreference.jpg` | MJPEG-Port (`http://<host>:<port>/tagreference.jpg`) | JPEG, `Cache-Control: no-store`, CORS `*` |

- **`SetTagReferencePreview`:** `RequestJson` = `wsc.vision.tagreference.request/1` (`frames`, `view` ignoriert).
  Ein Tag an, `""` aus. `OK`; `INVALID_ARGUMENT` (ungültiges JSON, fehlender Tag, ungültiges `overlay`);
  `INVALID_STATE` (nicht `Operational` oder kein Livestream-Overlay). Nie `BUSY`. Läuft nach 120 s ohne
  Erneuerung ab. Prüfung auch außerhalb des Modus `tagreference`.
- **`MeasureTagReference`** setzt die Vorschau auf ihre eigene Anfrage. Signatur unverändert.
- **Status** (Schema `wsc.vision.tagreference.preview/1`): `{"schema", "active", "tagId", "tagSeen", "updatedAt",
  "overlay", "lines": [{"id", "found", "offsetMm", "angleDeg", "ambiguous", "reason", "supportPx", "contrast"}]}`.
  Ohne Vorschau: `active: false`, `tagId: null`, `lines: []`.
- **Overlay-Ebenen** (`overlay` in der Anfrage: `guess`, `band`, `candidates`, `rejected`, `chosen`, `mask`,
  `labels`, `tagBox`): fehlend = `true`; unbekannt wird ignoriert; kein Bool → `INVALID_ARGUMENT`. Status zeigt die wirksame Belegung.
- **`saveDebug`** in `MeasureTagReference` (Standard `true`): `false` = kein Protokoll, `debugDir: null`.
- **Mess-Protokoll:** `data/tagreference/<YYYYmmdd-HHMMSS>_tag<id>/` (Config `tag_reference_debug_dir`, `None` = aus)
  mit `request.json`, `result.json`, `frame<k>.png` (verlustfrei), `overlay.jpg`. Höchstens 50 Ordner, nach mtime
  aufgeräumt; der neue bleibt. `ResultJson.debugDir` = repo-relativ oder `null`. Schreibfehler: nur Log-Warnung.
- **`GET /tagreference.jpg`:** `overlay.jpg` des neuesten Messordners; 404 ohne Messung.
- **Stream-Modus `tagreference`:** höchstens 3 Hz; letztes Debug-Bild bis 1 s gehalten; Messung hält ihr Bild 5 s.
  Tag nicht im Bild: Titel „Tag 7 nicht im Bild“.

**Frontend** (`webskillcomposition`): `setTagReferencePreview` in `tagReferenceCall.ts` (Timeout 10 s); Erneuerung
alle 60 s (`PREVIEW_RENEW_MS`). Schalter im Fenster „Tags einmessen“, gespeichert im localStorage unter
`wsc.tagReference.settings`. Details: [`arbeitsplaene/tag-einmessen-liveview.md`](arbeitsplaene/tag-einmessen-liveview.md) Abschnitt C.
