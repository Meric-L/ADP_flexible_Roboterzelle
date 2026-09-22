# Vision-Server — Systemaufbau und Backend-Schnittstelle

Diese Datei beschreibt den eigenständigen OPC-UA-Vision-Server (OPC 40100,
Machine Vision) und **was ein Backend implementieren muss**, um von ihm ein
Ergebnis zu bekommen. Sie ist ohne Kenntnis dieses Repos benutzbar.

> **Ergänzend, seit 2026-09-21:** Derselbe Job lässt sich zusätzlich über ein
> generisches **OPC-UA-Part-10-Programm** starten — dieselbe Bedienform, die
> Conveyor und CardDispenser in dieser Zelle benutzen. Der Server kündigt sich
> außerdem per mDNS an und wird vom Aggregation-Server der Zelle von selbst
> gefunden. Beides ist additiv, alles unten Beschriebene bleibt unverändert
> gültig. Siehe
> [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).
>
> **Achtung:** Der Vision-Namespace liegt inzwischen auf **ns=6**. Er wanderte
> erst von ns=4 auf ns=7 (die Part-2-Nodesets kamen dazwischen) und dann auf
> ns=6, weil mit `RaspiDevice` auch der Namensraum `http://launch-rm.de/raspi`
> entfallen ist. Genau deshalb gilt ausnahmslos: **Namespace-Indizes immer über
> die URI auflösen, nie hartcodieren.**

Stand: `hello-world` ist weiterhin ein **Platzhalter** ohne Bildverarbeitung.
`apriltag` ist die echte Erkennung — sie steuert die Pi-Kamera an, lokalisiert
mit AprilTags bestückte Module und liefert echte Posen; `calibration` prüft die
Messbereitschaft dieser Zelle. Der komplette Job-Ablauf (Zustandsautomaten,
Events, Ergebnisablage, Fehlerpfad) ist für alle drei identisch, und das
Payload-Format `wsc.vision.detections/1` ist **unverändert** geblieben.

> Die frühere QR-Code-Erkennung (`image-recognition`) ist entfallen. Sie war der
> Machbarkeitsnachweis für die Kamerastrecke; was von ihr bleibt, ist die
> `SharedCamera` darunter. Siehe [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md)
> Abschnitt 6.

## 1. Systemaufbau

**Ein** OPC-UA-Server auf dem Raspberry Pi (systemd-Service `opcua-server.service`,
Port 4840). Er trägt **einen** fachlichen Baum: das Vision-System, in zwei
Sichten auf denselben Job.

```
Raspberry Pi — opc.tcp://<pi>:4840/raspi/server/
├── Machines/          Standardordner aus OPC UA Machinery
│   └── VisionMachine  OPC 40100 VisionSystemType        <- dieses Dokument
└── VisionProgram      OPC UA Teil 10, Bedienoberfläche  <- part10-programm-schnittstelle.md
```

Beide Sichten bedienen **denselben** `JobRunner`; es gibt keinen zweiten
Job-Zustand. Wer nur Teil 10 sprechen will, kommt ohne eine einzige
40100-NodeId aus — siehe
[`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md).

> **Der Pfad `/raspi/server/` bleibt**, obwohl das Raspi-Interface weg ist. Er
> steht in der mDNS-Ankündigung, in der LDS-Registrierung und in jeder
> Client-Konfiguration; ihn umzubenennen bräche jede vorhandene Verbindung,
> ohne etwas zu verbessern.

Entfallen sind (Altlasten A1–A6): `RaspiDevice` mit CPU-Temperatur, Counter und
Sollwert, die leere Zweitinstanz `2:VisionSystem`, `CpuTemperatureResult` und
der Namensraum `http://launch-rm.de/raspi`.

Adressraum des Vision-Systems:

```
Objects/
└── Machines/                                ns=<machinery>;i=1001  (Standardordner)
    └── VisionMachine                        ns=<vision>;s=VisionMachine   (Typ: <mv>:VisionSystemType)
        ├── VisionStateMachine               Preoperational | Halted | Error | Operational
        │   └── AutomaticModeStateMachine    Initialized | Ready | SingleExecution | ContinuousExecution
        │       ├── StartSingleJob           <- Job starten
        │       ├── Stop                     <- laufenden Job abbrechen, siehe Abschnitt 5
        │       ├── StartContinuous          <- Dauerbetrieb, siehe Abschnitt 7.5
        │       ├── Abort                    <- wie Stop, ueber den Abort-Uebergang
        │       └── SimulationMode           (nicht verlinkt, siehe Abschnitt 7.5)
        ├── ResultManagement
        │   ├── Results/LatestResult         (Typ: <mv>:ResultType, wird pro Job überschrieben)
        │   │   └── ResultContent[0]         JSON-String des letzten Ergebnisses
        │   └── GetResultById | ReleaseResultHandle | ...   (nicht implementiert, siehe 7.3)
        ├── VisionAsset                      OPC 40100-2, siehe Abschnitt 11
        ├── StartCalibration                 ns=<vision>;s=VisionMachine.StartCalibration
        ├── CaptureCalibrationSample         ns=<vision>;s=VisionMachine.CaptureCalibrationSample
        ├── FinishCalibration                ns=<vision>;s=VisionMachine.FinishCalibration
        ├── AbortCalibration                 ns=<vision>;s=VisionMachine.AbortCalibration
        ├── CalibrationProgress              ns=<vision>;s=VisionMachine.CalibrationProgress
        ├── ActiveCalibrationInfo            ns=<vision>;s=VisionMachine.ActiveCalibrationInfo
        ├── LatestResultJson                 ns=<vision>;s=VisionMachine.LatestResultJson
        │                                    derselbe JSON-String, als einfacher String-Knoten
        ├── LatestCameraFrame                ns=<vision>;s=VisionMachine.LatestCameraFrame
        │                                    Base64-JPEG des Kamera-Livestreams, siehe Abschnitt 10
        ├── CameraStreamMode                 ns=<vision>;s=VisionMachine.CameraStreamMode
        │                                    **beschreibbar**: off | apriltag | calibration
        └── CameraStreamHttpPort             ns=<vision>;s=VisionMachine.CameraStreamHttpPort
                                             Int32: Port des MJPEG-Streams, 0 = keiner (Abschnitt 10.2)
```

Die vier Kalibriermethoden und die fünf Wertknoten sind **zusätzlich** unter
`VisionProgram` erreichbar — als Referenzen, nicht als Kopien. Es bleibt je ein
Knoten mit einem Wert bzw. einer Implementierung.

Interner Aufbau (Python-Paket `src/vision_server/`):

| Modul | Aufgabe |
| --- | --- |
| `address_space.py` | Nodeset-Import, `VisionMachine`-Instanz unter `Machines`, `HasNotifier` |
| `state_machine.py` | beide 40100-Zustandsautomaten |
| `events.py` | Event-Generatoren, `ResultReadyEvent` mit Payload |
| `result_management.py` | Ergebnisknoten + JSON-Spiegel |
| `job.py` | Validierung, Guard, Job-Ablauf, Fehlerpfad |
| `detection/` | Strategie `DetectionSource`; `hello_world.py`, `apriltag.py` (AprilTags), `script_runner.py` (Kalibrierprüfung) |
| `camera.py` | `SharedCamera` — ein Capture-Loop, geteilt von Erkennung und Livestream |
| `camera_stream.py` | Markiert und kodiert jeden Frame einmal; schreibt ihn als Base64-JPEG in `LatestCameraFrame` und reicht ihn an den MJPEG-Server, siehe Abschnitt 10 |
| `mjpeg_server.py` | Livestream als MJPEG über HTTP (`/stream.mjpg`, `/snapshot.jpg`), siehe Abschnitt 10.2 |
| `stream_overlay.py` | Markiert erkannte Tags im Livestream-Bild, siehe Abschnitt 10 |
| `tagloc/` (eigenes Paket) | Die Lokalisierung selbst: Kalibrierung, Erkennung, Posen, Tag-Map. Siehe [`apriltag-referenz.md`](apriltag-referenz.md) |
| `payload.py` | JSON-Schema `wsc.vision.detections/1` |

Echte Erkennung anschließen = **eine neue Datei in `detection/` plus ein
Registry-Eintrag**. Der Server-Kern und diese Schnittstelle bleiben unberührt.

## 2. Verbindungsdaten

| | Wert |
| --- | --- |
| Endpoint | `opc.tcp://<pi>:4840/raspi/server/` |
| Security | `NoSecurity` (keine Authentifizierung/Verschlüsselung) |
| ServerName | `Raspberry Pi OPC UA Server` |
| Namespace Vision | `http://launch-rm.de/vision` (aktuell **ns=6**, siehe Hinweis oben) |
| Namespace 40100 | `http://opcfoundation.org/UA/MachineVision` (aktuell ns=2) |
| Namespace Machinery | `http://opcfoundation.org/UA/Machinery/` (aktuell ns=4, trägt `Machines`) |

**Namespace-Indizes nie hardcoden** — immer
`await client.get_namespace_index("<uri>")`. Die Indizes verschieben sich, sobald
im Adressraum etwas dazukommt.

Wichtige NodeIds (sprechende String-Ids, stabil über Neustarts und Änderungen):

| Knoten | NodeId |
| --- | --- |
| Vision-System | `ns=<vision>;s=VisionMachine` |
| StartSingleJob | `ns=<vision>;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob` |
| Zustand außen | `ns=<vision>;s=VisionMachine.VisionStateMachine.CurrentState` |
| Zustand innen | `ns=<vision>;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.CurrentState` |
| Letztes Ergebnis (JSON) | `ns=<vision>;s=VisionMachine.LatestResultJson` |
| Kamera-Livestream (Base64-JPEG) | `ns=<vision>;s=VisionMachine.LatestCameraFrame` — nur vorhanden, wenn `camera_stream` konfiguriert ist (siehe Abschnitt 10) |
| Port des MJPEG-Streams | `ns=<vision>;s=VisionMachine.CameraStreamHttpPort` — Int32, 0 = kein HTTP-Stream (siehe Abschnitt 10.2) |

## 3. Was das Backend können muss

Vier Fähigkeiten, mehr nicht:

| # | Fähigkeit | Konkret |
| --- | --- | --- |
| B1 | Namespace-Index zur URI auflösen | `get_namespace_index(uri)` für beide URIs aus Abschnitt 2 |
| B2 | **Event-Subscription mit Event-Typ-Liste** | `subscription.subscribe_events(vision_node, [<Event-Typ-Knoten>])` — die Typliste ist **zwingend**, siehe 7.1 |
| B3 | Methodenaufruf mit 5 Eingaben / 2 Ausgaben | `automatic_node.call_method(start_node, meas, part, recipe, product, parameters)` |
| B4 | Event-Felder lesen und JSON parsen | `event.ResultContent[0]` → `json.loads(...)` |

Nicht benötigt: `load_data_type_definitions()`, Structured DataTypes,
Ergebnis-Handles, Polling, Discovery.

Ein Vision-Server liefert **keine Roboterliste** — eine Robot-Discovery im
Backend muss für diesen Endpoint leer bleiben dürfen, ohne die Verbindung als
fehlerhaft zu behandeln.

## 4. Handshake: ein Job

**Reihenfolge ist bindend — erst abonnieren, dann aufrufen.** Ein Job ist nach
~250 ms fertig; es gibt kein Event-Replay.

```
Backend                                     Vision-Server
   |  1. connect + Namespace-Indizes            |
   |  2. subscribe_events(VisionMachine, [...]) |
   |------------- StartSingleJob -------------->|  Guard + Validierung (synchron)
   |<------ (JobId: String, Error: Int32) ------|  Error != 0  => Ende, keine Events
   |                                            |
   |<---- StateChangedEvent Ready->SingleExecution
   |<---- JobStartedEvent            Message = JobId
   |<---- AcquisitionDoneEvent       Message = JobId
   |<---- ResultReadyEvent           ResultContent[0] = JSON  <-- das Ergebnis
   |<---- StateChangedEvent SingleExecution->Ready
   |<---- ReadyEvent                 Message = JobId
```

Zuordnung Ergebnis → Job über `payload["jobId"]`, **nicht** über die
Event-Felder `JobId`/`ResultId` (siehe 7.2).

Event-Typen, die abonniert werden sollten (NodeIds im 40100-Namespace):

| NodeId | Typ | Bedeutung |
| --- | --- | --- |
| `ns=<mv>;i=1024` | `ResultReadyEventType` | **trägt das Ergebnis-Payload** |
| `ns=<mv>;i=1013` | `JobStartedEventType` | Job angenommen und gestartet |
| `ns=<mv>;i=1025` | `AcquisitionDoneEventType` | Aufnahme fertig (fehlt im Fehlerfall) |
| `ns=<mv>;i=1023` | `ReadyEventType` | Job abgeschlossen, wieder aufnahmebereit |
| `ns=<mv>;i=1018` | `StateChangedEventType` | Zustandswechsel, mit `FromState`/`ToState` |

Dekodierbare Felder des `ResultReadyEvent`: `ResultContent` (String-Array),
`ResultState` (Int32), `IsPartial`, `IsSimulated`, `CreationTime`, `Message`.

## 5. `StartSingleJob`

Knoten: `AutomaticModeStateMachine/StartSingleJob`. Der Aufruf ist
**nicht blockierend** — er quittiert die Annahme, das Ergebnis kommt per Event.

| Argument | Nodeset-Typ | **Tatsächlich erwartet** |
| --- | --- | --- |
| `MeasId` | `MeasIdDataType` | String (leer erlaubt) |
| `PartId` | `PartIdDataType` | String (leer erlaubt) |
| `RecipeId` | `RecipeIdExternalDataType` | String; **waehlt den Job**, siehe unten |
| `ProductId` | `ProductIdDataType` | String (leer erlaubt) |
| `Parameters` | `BaseDataType[]` | String-Array, max. 16 Einträge |

| Ausgabe | Nodeset-Typ | **Tatsächlich geliefert** |
| --- | --- | --- |
| `JobId` | `JobIdDataType` | String, z. B. `job-000001`; leer bei Ablehnung |
| `Error` | `Int32` | Fehlercode, siehe unten |

### Verfügbare Jobs (`RecipeId`)

Jede `RecipeId` waehlt ein Erkennungsprofil (`detection/`). Alle laufen ueber
denselben `StartSingleJob`-Aufruf und denselben Event-/Payload-Ablauf aus
Abschnitt 4 und 6.

| `RecipeId` | Job | Implementierung | Ergebnis |
| --- | --- | --- | --- |
| `""` oder `"hello-world"` | Platzhalter ohne Bildverarbeitung | in-process, `detection/hello_world.py` | `attributes.message` = `"Hello World"` |
| `"calibration"` | Messbereitschaft dieser Zelle pruefen | Subprozess, `src/jobs/calibrate.py` | `attributes.message` = Kalibrier-Id, Aufloesung, RMS, Aufnahmezahl, Alter, Tag-Map |
| `"apriltag"` | **Module lokalisieren** | in-process, `detection/apriltag.py`, liest von der geteilten Kamera (Abschnitt 10) | eine Detektion je erkanntem Modul-Tag mit echter Pose |

`apriltag` nimmt `samples_per_job` Bilder auf (Decke 3, Flansch 5), mittelt die
Posen und verwirft Tags oberhalb von `max_reproj_error_px`. Daraus ergibt sich
`job_timeout` (Abschnitt 9) von 20 s. Die Kamera wird dafuer **nicht** extra
geoeffnet — sie laeuft bereits fuer den Livestream (Abschnitt 10) und wird nur
mitgelesen.

`attributes` einer AprilTag-Detektion: `tagId`, `reprojErrorPx`, `ambiguous`,
`sampleCount`, `frameTimestamp`, `recipeId`; fehlen erwartete Module aus der
Tag-Map, zusaetzlich `missingModules`. Ist kein Tag im Bild, liefert der Job
**kein** leeres Erfolgsergebnis, sondern `Error=5` (`DETECTION_FAILED`).

`frameId` haengt am Bild: sieht die Kamera einen Referenz-Tag aus der Tag-Map
(Welt-Board, Robotertisch), liefert die Quelle Posen im Welt-KS; sonst im
Kamera-KS. **Deshalb `frameId` nie annehmen, sondern lesen** — zusammen mit
`frameConvention`, die sagt, wohin +Z zeigt.

Layer 1 und Layer 2 benutzen **dasselbe** Rezept und dasselbe Profil. Sie
unterscheiden sich nur in der `AprilTagProfileConfig`, die `server.py` je
Pi setzt.

Eine unbekannte `RecipeId` wird sofort mit `Error=4` (`UNKNOWN_RECIPE`)
abgelehnt; die Fehlermeldung listet die bekannten Rezepte.

Die gewaehlte Quelle bestimmt `frameId`, `frameConvention`, `configurationId`
und `IsSimulated` des Ergebnisses — `hello-world` bleibt damit dauerhaft als
kamerafreier Smoke-Test brauchbar, auch wenn daneben eine echte Erkennung
laeuft. Die `RecipeId` steht nach dem Job in `InternalRecipeId` am
Ergebnisknoten und in `attributes.recipeId`.

**Bewusste Abweichung:** Das Nodeset deklariert die Ids als Strukturen. Ein
asyncua-Client kann solche ExtensionObjects ohne
`load_data_type_definitions()` weder bauen noch lesen (asyncua-Issue #1693),
deshalb werden hier Strings ausgetauscht. Ein UA-Browser wie UaExpert zeigt
dementsprechend einen String, wo der Typ eine Struktur erwartet — das ist
erwartet, kein Fehler.

### `Stop`

Knoten: `AutomaticModeStateMachine/Stop`. Bricht einen laufenden Job **wirklich**
ab (nicht nur formal) — Subprozess wird beendet (`terminate()`, nach 2 s
`kill()`, siehe Abschnitt 9), der Automat geht zurueck nach `Ready`, und ein
`ResultReadyEvent` mit `resultState=7` (`CANCELLED`) wird gefeuert. Der Aufruf
blockiert, bis das abgeschlossen ist — ein `StartSingleJob` direkt danach
trifft nie auf einen noch aufraeumenden Job.

| Argument | Nodeset-Typ | **Tatsächlich erwartet** |
| --- | --- | --- |
| `Cause` | `Int32` | wird nicht ausgewertet |
| `CauseDescription` | `String` | wird nicht ausgewertet |

| Ausgabe | Nodeset-Typ | **Tatsächlich geliefert** |
| --- | --- | --- |
| `Error` | `Int32` | `0`, wenn abgebrochen oder gar nichts lief; `!= 0`, falls der Abbruch selbst scheiterte |

Fire-and-forget-tauglich: Ein `Stop` ohne laufenden Job (z. B. weil der Job
schon fertig war) liefert genauso `Error=0` wie ein erfolgreicher Abbruch —
das Frontend muss den Zustand vorher nicht kennen. Nur wenn der Job sich
nicht innerhalb von `stop_timeout` (5 s, `config.py`) abbrechen liess, kommt
`Error=6` (`INTERNAL`) zurueck.

### Fehlercodes (`Error`)

Das Nodeset definiert für `Error` keinen Enum; diese Codes sind
projektspezifisch und müssen im Backend gespiegelt werden.

| Code | Name | Bedeutung | Folgen Events? |
| --- | --- | --- | --- |
| 0 | `OK` | Job angenommen | ja |
| 1 | `INVALID_STATE` | Automat nicht in `Operational`/`Ready` | nein |
| 2 | `INVALID_ARGUMENT` | Id zu lang, falscher Typ, zu viele Parameter | nein |
| 3 | `BUSY` | es läuft bereits ein Job | nein |
| 4 | `UNKNOWN_RECIPE` | `RecipeId` nicht bekannt | nein |
| 5 | `DETECTION_FAILED` | Erkennung fehlgeschlagen | ja, mit `resultState=5` |
| 6 | `INTERNAL` | unerwarteter Serverfehler | ja, mit `resultState=6` |
| 7 | `CANCELLED` | Job durch `Stop` abgebrochen | ja, mit `resultState=7` |

Faustregel: **1–4 werden sofort im Rückgabewert abgelehnt** (der Automat bleibt
`Ready`, es folgt kein Event). **5–7 passieren während der Ausführung** und
kommen als `ResultReadyEvent` mit `resultState != 0` — ein eventgetriebener
Client erfährt einen Erkennungsfehler (oder Abbruch) also nie erst per Timeout.

Nebenläufigkeit: Es läuft immer nur **ein** Job. Ein zweiter Aufruf während
eines laufenden Jobs wird deterministisch mit `BUSY` abgelehnt, statt Zustände
zu überschreiben.

## 6. Ergebnis-Payload

JSON-String in `ResultContent[0]` (Schema `wsc.vision.detections/1`) — identisch
im Event, am Ergebnisknoten und in `LatestResultJson`.

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-hello-01",
  "resultId": "res-job-000001",
  "jobId": "job-000001",              // Korrelation zum StartSingleJob-Rückgabewert
  "creationTime": "2026-09-09T19:16:59.612+00:00",
  "resultState": 0,                   // 0 = ok, sonst Fehlercode aus Abschnitt 5
  "frameId": "world",                 // Bezugsrahmen der Posen, siehe unten
  "lengthUnit": "m",                  // immer SI-Meter
  "angleUnit": "rad",
  "rotation": "quaternion_xyzw",      // three.js-Reihenfolge
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

Fehlerfall — gleiches Schema, zusätzlich `errorCode`/`errorText`, `detections`
leer:

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "jobId": "job-000004",
  "resultState": 5,
  "errorCode": 5,
  "errorText": "Erkennung durch Parameter 'force-error' fehlgeschlagen",
  "detections": []
}
```

**Zwei additive Schlüssel** (nur bei nicht-leerem Wert, das Schema bleibt
`wsc.vision.detections/1`):

| Schlüssel | Bedeutung |
| --- | --- |
| `frameConvention` | Achsenkonvention hinter `frameId`, z. B. `"z_forward_x_right_y_down"` = OpenCV-Optikrahmen: +X rechts im Bild, +Y nach unten, +Z entlang der optischen Achse nach vorn. Pose ist `T_cam_tag`, Tiefe positiv. **Nicht** die Robotik-Konvention (REP-103) und **kein** Weltsystem. |
| `configurationId` | Identität der wirksamen Konfiguration, z. B. Kalibrierdatei plus Änderungszeit — beantwortet bei falschen Posen die Frage, welche Kalibrierung das war. |

`frameId` kommt jetzt **von der Erkennungsquelle**, nicht mehr fest aus dem
Modul: eine Instanz kann gleichzeitig eine Platzhalterquelle im Weltrahmen und
eine Kameraquelle im Optikrahmen bedienen. Ohne `frameConvention` ist eine
kamerarelative Pose nicht interpretierbar — ein Rahmenname allein sagt nicht,
wohin +Z zeigt.

Regeln für den Parser: `schema` prüfen und unbekannte Versionen verwerfen statt
zu crashen; `resultState != 0` als Fehler behandeln; `position` in Metern und
`orientation` als Quaternion `xyzw` interpretieren; `frameId` niemals annehmen,
sondern lesen.

Der Platzhalter liefert bewusst schon die endgültige Struktur: Wenn echte
Erkennung dazukommt, ändern sich nur die Werte in `detections`, nicht das
Schema — der Backend-Parser muss dann nicht angefasst werden.

## 7. Stolpersteine (alle verifiziert)

### 7.1 Ohne Event-Typ-Liste kommt kein Payload

`subscribe_events(node)` ohne zweites Argument baut die Select-Clauses nur aus
`BaseEventType`. Die Events treffen dann ein, aber `ResultContent` ist `None` —
sieht aus wie "der Server sendet nichts". Immer die Typliste übergeben:

```python
sub = await client.create_subscription(100, handler)
await sub.subscribe_events(
    vision_node,
    [client.get_node(ua.NodeId(i, mv_idx)) for i in (1024, 1013, 1025, 1023, 1018)],
)
```

Die Select-Clauses schränken außerdem **nicht** ein, *welche* Events geliefert
werden: Es kommt alles an, was `VisionMachine` emittiert. Clientseitig auf
`event.EventType` filtern.

### 7.2 Abo muss auf `VisionMachine` sitzen, nicht auf dem Server-Objekt

Der Server setzt spec-konform eine `HasNotifier`-Referenz vom Server-Objekt auf
`VisionMachine`, aber **asyncua bubbelt Events serverseitig nicht**: Die
Auslieferung matcht strikt den exakten `emitting_node`. Ein Abo auf
`i=2253`/`client.nodes.server` empfängt daher **nichts**.

Ebenfalls davon betroffen: die Structure-Felder `ResultId`, `JobId`,
`InternalRecipeId`, `InternalConfigurationId` des `ResultReadyEvent` werden
absichtlich leer gesendet, weil ein Client sie nicht dekodieren könnte. Ihre
Werte stehen im JSON-Payload. `Message` trägt zusätzlich die `ResultId` als
Klartext.

### 7.3 `ResultManagement`-Methoden sind nicht benutzbar

`GetResultById`, `GetResultComponentsById` und `GetResultListFiltered` existieren
im Adressraum (aus dem Nodeset), sind aber **nicht implementiert** und vom
Client aus ohnehin nicht aufrufbar: Sie erwarten `ResultIdDataType` bzw.
`JobIdDataType` als **Eingabe**-ExtensionObject. Deshalb gibt es auch keine
Ergebnis-Handles und folglich kein `ReleaseResultHandle`-Leck zu verhindern.

Das Ergebnis kommt stattdessen im Event (primär). Fallback, falls ein Client
Array-Felder in Events nicht verarbeitet: auf das `ResultReadyEvent` hin **ein**
`read_value()` auf `LatestResultJson` — weiterhin eventgetrieben, kein Polling.

### 7.4 Es gibt genau eine 40100-Instanz — die Doppelung ist entfernt

**Erledigt.** Früher lag neben `VisionMachine` eine ältere, leere
`VisionSystemType`-Instanz `ns=2;s=…VisionSystem`, die nur `CpuTemperatureResult`
trug. Wer Instanzen per Typ einsammelte, fand zwei Systeme; ein Aufruf auf die
nicht verlinkte `StartSingleJob` der Altlast (`ns=2;i=150`) wurde mit
`BadNothingToDo` beantwortet. Das hat mindestens einmal echte Zeit gekostet.

Beides ist weg: die Altlast-Instanz und mit ihr `RaspiDevice` und der Namensraum
`http://launch-rm.de/raspi`. Eine Typsuche findet jetzt genau ein System.
Verifiziert durch `tests/test_part10_fassade.py`, das den Adressraum durchläuft
und auf **einer** Instanz besteht.

Die Empfehlung bleibt trotzdem: die feste NodeId `ns=<vision>;s=VisionMachine`
verwenden. Sie ist über Neustarts stabil und kostet keine Browse-Runde.

### 7.5 Welche Methoden verlinkt sind — und welche nicht

Das Nodeset bringt **alle** Methoden der Spec als Teil der Typdefinition mit;
sie stehen im Adressraum, sobald die `VisionSystemType`-Instanz existiert. Ob
sie etwas tun, entscheidet `server.link_method` in `runner.py`. Eine nicht
verlinkte Methode antwortet `BadNothingToDo` auf OPC-UA-Statusebene — **nicht**
`Error != 0` im Output.

**Verlinkt:**

| Methode | Wirkung |
| --- | --- |
| `StartSingleJob` | ein Durchlauf, Ergebnis, zurück nach `Ready` |
| `StartContinuous` | läuft bis `Stop` oder `Abort`; je Durchlauf ein eigenes Ergebnis unter `<jobId>-0001`, `-0002`, … Ein fehlgeschlagener Durchlauf beendet den Dauerbetrieb — sonst erzeugte dieselbe Störung im Sekundentakt dieselbe Meldung. Pause dazwischen: `continuous_interval_s` (Standard 1 s) |
| `Stop` | bricht den laufenden Job ab, Ergebnis mit `resultState=7` (`CANCELLED`) |
| `Abort` | wie `Stop`, aber über den Abort-Übergang. Für uns ist das der einzige Unterschied: es gibt keinen Zwischenstand, den ein Abbruch verwerfen könnte |
| `Halt` | beendet den laufenden Job und fährt nach `Halted`. Danach nimmt der Server keine Jobs mehr an |
| `Reset` | zurück nach `Operational`, über `Preoperational` — das Nodeset kennt keinen Übergang `Halted -> Operational` |

**Nicht verlinkt, mit Grund:**

| Methode | Warum nicht |
| --- | --- |
| `SimulationMode` | bräuchte je Profil eine simulierte Datenquelle. Das Profil `hello_world` ist bereits genau das und ohne Kamera aufrufbar |
| `SelectModeAutomatic` | es gibt nur eine Betriebsart. Eine Methode, die immer `OK` zurückgibt und nichts umschaltet, wäre irreführender als eine erkennbar nicht implementierte |
| `ConfirmAll`, `Sync` der StepModels | gehören zum Schrittketten-Modell, das wir nicht benutzen |
| **`ConfigurationManagement`** vollständig | bräuchte ein Konfigurations-Datenmodell, das die Zelle nicht hat. `configurationId` im Ergebnis benennt die wirksame Kalibrierung |
| **`RecipeManagement`** vollständig | unsere `RecipeId` ist ein Routing-Schlüssel auf ein Erkennungsprofil, kein verwaltetes Rezeptobjekt |
| `GetResultById`, `ReleaseResultHandle`, `GetResultListFiltered` | das Ergebnis kommt im Event und steht am Knoten; eine Handle-Verwaltung wäre Aufwand ohne Abnehmer (siehe 7.3) |

Diese Lücken sind Entscheidungen, keine Versäumnisse.

## 8. Ablauf einmal durchspielen

```bash
# Auf dem Pi laeuft der Server als Service; nach einem Code-Update:
sudo systemctl restart opcua-server.service

# Referenzimplementierung des Handshakes (von beliebigem Rechner):
PYTHONPATH=src python3 src/vision_server/tools/hello_world_client.py \
    --url opc.tcp://<pi>:4840/raspi/server/
```

Der Client gibt `StartSingleJob -> JobId=... Error=0`, das Payload und die
Event-Reihenfolge aus; Exit-Code `0` heißt: Handshake vollständig durchlaufen.
Er ist die kürzeste Vorlage für die Backend-Anbindung.

Vorführbare Sonderfälle:

| Aufruf | Erwartung |
| --- | --- |
| zwei Aufrufe gleichzeitig | einer `Error=0`, einer `Error=3` (BUSY) |
| `--meas-id` mit >128 Zeichen | `Error=2`, keine Events |
| `--recipe-id does-not-exist` | `Error=4`, keine Events |
| `--parameter force-error` | `Error=0`, dann `ResultReadyEvent` mit `resultState=5`, Automat läuft über `Error` zurück nach `Operational` und ist wieder aufnahmebereit |

## 9. Offen / nächste Schritte

- **Kalibrierung**: `calibration` ist jetzt eine **Bereitschaftspruefung** und
  meldet Kalibrier-Id, Aufloesung, RMS, Aufnahmezahl, Alter und Tag-Map. Die
  Kalibrierung selbst bleibt bedienergefuehrt (`python -m tagloc.cli.calibrate`)
  — sie braucht jemanden, der ein Board durchs Bildfeld fuehrt, und laesst sich
  deshalb nicht sinnvoll aus der Ferne ausloesen.
- **Job-Timeout**: eine Erkennung, die laenger als `job_timeout` (20 s) braucht,
  wird abgebrochen und als `DETECTION_FAILED` gemeldet; der Automat kehrt nach
  `Ready` zurueck. Ein blockierter Worker-Thread laesst den *naechsten* Job
  desselben Profils allerdings ebenfalls in den Timeout laufen — deshalb hat die
  AprilTag-Quelle mit `capture_timeout_s` ein eigenes, kuerzeres Aufnahme-Timeout.
- **Koordinatensystem**: Posen sind jetzt echt. `frameId` ist `world`, sobald ein
  Referenz-Tag aus der Tag-Map im Bild ist, sonst das Kamera-KS der Quelle.
  **Offen bleibt die Hand-Auge-Kalibrierung** fuer Layer 2: die Kette Kamera →
  Roboterbasis ist nicht eingemessen, ein automatisches Anfahren erkannter Posen
  darf bis dahin nicht scharf geschaltet werden. Layer 2 liefert deshalb im
  Kamera-KS mit gesetzter `frameConvention`, und das Backend verkettet.
- **Zweite Kamera / 3D-Profil**: würde als zweite `VisionSystemType`-Instanz im
  selben Server hängen (eigener Instanzname und eigene `visionSystemId`),
  dieselbe Schnittstelle. Ein zweiter Serverprozess ist nicht vorgesehen —
  er würde denselben 40100-Adressraum ein zweites Mal laden (~110 MB).
- **Structure-Felder der Events** befüllen, sobald asyncua-Issue #1693 gefixt
  ist. Das Payload bleibt auch dann die maßgebliche Quelle.

## 10. Kamera-Livestream

Transportweg laut Absprache mit dem Backend: **kein neuer Methodenaufruf,
kein Lifecycle**. Sobald der Server läuft und `camera_stream` konfiguriert
ist, schreibt er kontinuierlich (Standard: 5 Bilder/s) den jeweils neuesten
Kamera-Frame als Base64-kodiertes JPEG in einen einfachen String-Knoten:

```
ns=<vision>;s=VisionMachine.LatestCameraFrame
```

Das Backend abonniert diesen Knoten wie jeden anderen Wert — dieselbe
Infrastruktur wie für `LatestResultJson`, kein neues Protokoll. Der Knotenwert
ist **kein** JSON, sondern der rohe Base64-String des JPEG; ein Client
dekodiert `atob(value)` bzw. `base64.b64decode(value)` und bekommt direkt die
JPEG-Bytes.

Verhalten:

- Der Knoten existiert **nur**, wenn der Server mit `camera_stream`
  konfiguriert wurde (auf dem Pi über `server.py` der Fall, beim
  lokalen `python -m vision_server` standardmäßig **nicht** — dort fehlt
  i. d. R. die Kamera).
- Läuft die Kamera nicht (Fehler beim Öffnen), existiert der Knoten zwar,
  bleibt aber leer (`""`) — kein Fehlerzustand des Automaten, rein
  Stream-lokal.
- Livestream und `apriltag`-Job teilen sich **dieselbe** Kamera (`SharedCamera`
  in `camera.py`): der Job liest nur die zwischengespeicherten Frames mit,
  öffnet die Hardware nicht erneut. Während eines laufenden Jobs bleibt der
  Stream daher unverändert aktiv, es gibt kein Aussetzen.
- Auflösung, Bildrate und JPEG-Qualität stehen in `CameraStreamConfig`
  (`profiles.py`) — Standard 1280×720, Aufnahme 15 fps, Knoten 5 fps,
  HTTP-Stream 15 fps, Qualität 70.
- Der Knoten ist seit dem MJPEG-Stream (10.2) nur noch der **Rückfallweg**.
  Base64 über OPC UA, Backend, WebSocket und React-Store taugt für ein
  Kontrollbild, nicht für Video.
- **Für den Stream herunterskaliert, falls breiter als `max_stream_width`**
  (Standard 960 px) — Erkennung und Kalibrierung sehen weiterhin den vollen
  Kamera-Frame, nur die veröffentlichte Kopie wird kleiner. Ohne das kostete
  z. B. `cam_ceiling` (2028×1520, siehe unten) pro Tick ein JPEG-Encode eines
  ~3-MP-Bildes und die Framerate brach spürbar ein.
- **Kamera-Backend ist pro Pi verschieden**, `CameraStreamConfig.backend`
  (`"picamera2"` | `"realsense"` | `"opencv"`) macht das explizit:
  `server.py` wählt es über `PI_CAMERA_BACKENDS`
  (Hostname → Backend, Fallback `"picamera2"`, override per Env-Var
  `VISION_CAMERA_BACKEND`). Aktuell: `ADP-Roboter-Lokalisierung` → Picamera2
  (Deckenkamera), `ADP-HandInEye-Kamera-Pi` → RealSense. Für die Erkennung und
  den Livestream ist das Backend unsichtbar — beide lesen nur `CameraFrame`
  (BGR-Array) von `SharedCamera.latest_frame`.
- **RealSense hat eigene, bewusst konservative Defaults** (`realsense_resolution`
  640×480, `realsense_fps` 15, unabhängig von `resolution`/`stream_fps` der
  anderen Backends): die auf dem Pi nötige RSUSB/libuvc-Anbindung (der
  Kernel bringt keinen brauchbaren UVC-Treiber für RealSense mit) limitiert
  die Bandbreite, `1280×720@30` scheiterte dort mit
  `RuntimeError: Couldn't resolve requests`. Scheitert `pipeline.start()`,
  loggt `_open_realsense` zusätzlich die tatsächlich unterstützten
  Farb-Profile der angeschlossenen Kamera — damit lässt sich der Wert bei
  Bedarf gezielt hochsetzen, statt zu raten.
- Welche Quelle den Stream speist, entscheidet sich über die Eigenschaft: der
  Server nimmt die erste geöffnete Quelle, die eine `SharedCamera` hält. Früher
  stand hier der feste Profilname `image_recognition`; ein Umbenennen hätte den
  Stream still sterben lassen.

### 10.1 Overlay — was im Bild markiert wird

Der Stream ist ein **Debugwerkzeug**: er soll nicht nur zeigen, dass ein Bild
ankommt, sondern was die Erkennung darin sieht. Ein zweiter, **beschreibbarer**
Knoten wählt den Modus:

```
ns=<vision>;s=VisionMachine.CameraStreamMode     Datentyp String, schreibbar
```

| Wert | Anzeige im Frontend | Was markiert wird |
| --- | --- | --- |
| `off` | „Rohbild" | nichts — das unveränderte Kamerabild |
| `apriltag` (Standard) | „AprilTags markieren" | Umriss jedes erkannten Tags, **Achsenkreuz im Tag** (X rot, Y grün, Z blau), ID, Modulname aus der Tag-Map, Distanz, Reprojektionsfehler; mehrdeutige Posen orange statt grün |
| `calibration` | „Kalibrierboard markieren" | die gefundenen Board-Ecken und die Bildabdeckung |

Verhalten:

- Ein **ungültiger oder leerer Wert hält den Stream nicht an** — er fällt auf
  `apriltag` zurück. Das Bild ist wichtiger als die Markierung.
- Das Overlay rechnet höchstens alle `overlay_interval_s` (Standard 0,5 s) neu
  und zeichnet dazwischen das letzte Ergebnis weiter. Bei fest montierter Kamera
  sieht man davon nichts, die CPU des Pi schon.
- Overlay und Job benutzen **dieselbe geladene Kalibrierung und Tag-Map**. Sieht
  man im Stream etwas anderes als im Jobergebnis, liegt es folglich nicht an
  zwei verschiedenen Konfigurationen.
- Im `calibration`-Modus sucht das Overlay mit **derselben Board-Geometrie**
  wie `CaptureCalibrationSample` (Abschnitt 12.2, aus `AprilTagProfileConfig`)
  — nicht aus der geladenen Kalibrierdatei. Bei einer Platzhalter-Kalibrierung
  (Abschnitt 9, `allow_placeholder_calibration`) gibt es dort kein Board-Feld;
  eine andere Geometrie anzunehmen hätte die Ecken-Erkennung im Stream leer
  laufen lassen, obwohl das eigentliche Erfassen die Ecken findet.
- Ebenfalls nur im `calibration`-Modus: die Ecken-Suche läuft auf einer auf
  `max_stream_width` herunterskalierten Kopie, nicht auf dem vollen
  Kamera-Frame — bei `cam_ceiling` (2028×1520) kostete die volle Auflösung
  auf dem Pi spürbar Zeit und drückte die Framerate.
- Gezeichnet wird auf einer Kopie; der geteilte Frame bleibt unverändert.
- Ein Fehler im Overlay beendet den Stream nicht — dann kommt das unmarkierte
  Bild, und der Fehler steht im Log.

Das Frontend braucht dafür nur ein Auswahlfeld, das beim Wechsel diesen Knoten
schreibt. Die Beschriftungen stehen serverseitig in
`tagloc.modes.OVERLAY_MODE_LABELS`. Ablauf und Abnahme:
[`apriltag-e2e-test.md`](apriltag-e2e-test.md) Abschnitt 4.

---

### 10.2 MJPEG über HTTP — das flüssige Live-Bild

Zusätzlich zum Knoten liefert der Pi den Stream direkt per HTTP aus, als
`multipart/x-mixed-replace` (MJPEG). Ein Browser spielt das ohne Bibliothek in
einem `<img>` ab; Backend und OPC UA sind nicht beteiligt.

```
http://<pi>:<port>/stream.mjpg     endloser Stream, ~15 fps
http://<pi>:<port>/snapshot.jpg    das neueste Einzelbild (503, solange keins da ist)
```

- **Port**: steht im Knoten `CameraStreamHttpPort` (Standard 8080). Er wird
  erst gesetzt, wenn der Server wirklich lauscht; bis dahin und bei belegtem
  Port steht dort 0. Die Adresse `<pi>` nimmt der Client aus seiner
  OPC-UA-Endpoint-URL — der Pi kennt seine von außen erreichbare Adresse nicht
  verlässlich.
- **Dieselben Bilder** wie im Knoten, dasselbe Overlay (`CameraStreamMode`
  gilt für beide). Jeder Frame wird genau einmal markiert und kodiert.
- **Rate**: ohne Zuschauer tickt der Publisher nur mit `stream_fps`; ab einer
  offenen HTTP-Verbindung mit `http_fps`. Höchstens 4 gleichzeitige Zuschauer,
  weitere bekommen 503.
- **Kamera hängt** (Watchdog, siehe `vision-system.md`): es kommen keine neuen
  Bilder, die Verbindung bleibt offen, der Browser zeigt das letzte Bild.
- **Rückfall**: Ist der Port 0 oder die URL nicht erreichbar (Firewall, altes
  Pi-Build), nutzt das Frontend weiter `LatestCameraFrame`.

### 10.3 Zwei Bildströme an der Deckenkamera

Die Deckenkamera (Pi 1, HQ-Kamera IMX477) nimmt mit der vollen
Sensorauflösung **4056×3040** auf. Der Livestream rechnet darauf nicht: die
Pi-Kamera liefert aus demselben Frame einen zweiten, vom ISP skalierten Strom
(`lores`, 960×720), praktisch ohne CPU-Last.

| Wer | Bild | Auflösung |
| --- | --- | --- |
| AprilTag-Job, Kalibrier-Session | `CameraFrame.image` | 4056×3040 |
| Livestream (MJPEG und Knoten), Overlay „AprilTags markieren“ und „Kalibrierboard markieren“ | `CameraFrame.preview` | 960×720 |

Beide Bilder kommen aus **einem** Request, stammen also immer aus derselben
Aufnahme. Das Overlay rechnet die Kalibrierung auf 960×720 um
(`scale_to_resolution`); das Seitenverhältnis weicht um 0,07 % ab, unter der
Schranke von 0,1 %.

Folge: Das Overlay sucht Tags im kleinen Bild. Ein sehr kleiner, weit
entfernter Tag kann dort fehlen, den der Job im vollen Bild trotzdem findet —
nie umgekehrt. Das Jobergebnis ist maßgeblich, das Overlay ist die Vorschau.

Einstellungen (`CameraStreamConfig`, Preset in `server.py`,
`PI_CAMERA_STREAM_PRESETS`): `preview_resolution` (960×720), `capture_fps` 10
(Obergrenze des IMX477 bei voller Auflösung), `buffer_count` 2 (sechs Puffer à
37 MB passen nicht in den CMA-Speicher). Ohne `preview_resolution` — Hand-Pi
mit RealSense, OpenCV — verkleinert der Stream wie bisher selbst.

Hauptstrom im Format `RGB888`: Picamera2 legt es als B,G,R ab, also direkt
OpenCV-BGR — die frühere Farbumrechnung auf jedem Frame entfällt.

## 11. OPC 40100-2: Anlagensicht

Part 1 beantwortet, **wie man das System bedient**. Part 2 — *Asset Management
and Condition Monitoring*, veröffentlicht 17.05.2024 — beantwortet, **woraus es
besteht**: Recheneinheit, Bildsensor, Objektiv, jeweils mit Identifikation. Für
Service und Instandhaltung, nicht für den Betrieb.

```
ns=<vision>;s=VisionMachine.VisionAsset
├── Identification        Manufacturer, Model, SerialNumber, SoftwareRevision
├── ComputingDevices/ComputingDevice
├── ImageSensors/ImageSensor
└── Lenses/Lens
```

Angelegt wird nur, was in der `AssetConfig` des Pis steht (`src/vision_server/server.py`,
`PI_ASSET_PRESETS`). Ein leeres Modellfeld heißt „nicht bekannt" und erzeugt
**keinen** Eintrag — ein erfundenes Modell wäre in einer Instandhaltungssicht
schlimmer als eine Lücke.

### 11.1 Was das kostet

Part 2 bringt **DI 1.04.0** und **Machinery 1.03.0** mit; der Server lädt also
vier Nodesets statt einem. Gemessen (Desktop, asyncua 2.0.1):

| | RSS | Startzeit |
| --- | --- | --- |
| nur Part 1 | 108,5 MB | 0,65 s |
| mit Part 2 | 121,3 MB | +1,6 s |
| Anlagensicht instanziiert | +2,9 MB | |

Rund **16 MB und knapp zwei Sekunden**. Ohne `assets` in der
`VisionServerConfig` wird nichts davon geladen. Nachmessen:
`PYTHONPATH=src python3 tools/measure_nodeset_import.py`.

### 11.2 Zwei Fallen

**Die Nodeset-Versionen sind gepinnt.** Das neueste DI (1.05.0) lässt sich mit
asyncua 2.0.1 **nicht** importieren — es fordert UA-Basis 1.05.04 und scheitert
mit `BadParentNodeIdInvalid`. Das neueste Machinery zöge zusätzlich `IA` herein.
Gewählt sind genau die Versionen, die AMCM als `RequiredModel` nennt. Details in
[`src/vision_server/nodesets/README.md`](../src/vision_server/nodesets/README.md).

**Der Namensraumindex verschiebt sich.** Mit Part 2 liegt
`http://launch-rm.de/vision` nicht mehr auf Index 3, sondern auf 6. Clients
müssen ihn zur Laufzeit über `get_namespace_index` auflösen. Wer einen Index
hart einträgt, bemerkt es erst, wenn jemand ein Nodeset ergänzt.

### 11.3 Warum asyncua Platzhalter anlegt

`<VisionItem>` & Co. tragen die Modelling Rule `MandatoryPlaceholder`. asyncua
instanziiert sie deshalb als echte Knoten, obwohl sie Vorlagen des Typs sind.
Sie nachträglich zu löschen kostete **9 s für 26 Knoten** — das rekursive
Löschen ist dort pathologisch langsam. `asset_model.py` legt deshalb nur die
Ordner an, die es füllt: rund 50 Knoten statt 700.

---

## 12. Interaktive Kalibrierung (frontend-gesteuert)

Bisher lief Kalibrierung ausschließlich über das eigenständige CLI-Tool
(`tagloc.cli.calibrate` per SSH) — es öffnet die Kamera exklusiv, der Server
muss dafür gestoppt sein. Diese vier Methoden plus ein Knoten erlauben
dasselbe **bei laufendem Server**, aus einem Settings-Menü heraus: Session
starten, Board vor die Kamera halten, Aufnahme auslösen, Fortschritt live
sehen. Ein explizites `FinishCalibration` ist dabei **optional** — sobald die
Abdeckung reicht, schließt sich die Session von selbst ab (Abschnitt 12.5),
das Frontend muss also nur `CalibrationProgress` beobachten und das Ergebnis
anzeigen, sobald es dort auftaucht.

Wie beim Livestream gilt: die Session liest nur aus der bereits laufenden
`SharedCamera` mit (dieselbe, die `apriltag`-Job und Livestream nutzen) —
kein zweiter, exklusiver Kamera-Zugriff, kein Stoppen des Servers nötig.

**Eine frisch gespeicherte Kalibrierung wirkt sofort, ohne Server-Neustart.**
Direkt nach dem Speichern übernehmen die laufende Erkennung (`apriltag`-Job)
und das Stream-Overlay die neuen Werte — der nächste Job nach einer
erfolgreichen Kalibrierung rechnet bereits damit. (Bis 2026-09-22 stimmte
das nicht: die Datei lag zwar auf der Platte, die laufende Erkennung merkte
das aber erst nach einem manuellen `systemctl restart`. Falls ihr das noch
irgendwo dokumentiert oder umgangen habt, ist das jetzt nicht mehr nötig.)

**Board-Geometrie ist serverseitig fest konfiguriert** (`AprilTagProfileConfig`
in `profiles.py`, pro Pi in `PI_APRILTAG_PRESETS` in `src/vision_server/server.py`) —
das Frontend sendet und kennt keine Board-Parameter.

### 12.1 `StartCalibration`

Setzt gesammelte Samples zurück und beginnt eine neue Session. Aufnahmen
kommen danach ausschließlich über `CaptureCalibrationSample` (Abschnitt
12.2) — kein automatisches Erfassen.

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` (`OK`), `1` (`INVALID_STATE`, Automat nicht `Ready`), `3` (`BUSY`, es läuft bereits ein Job oder eine Session) |

### 12.2 `CaptureCalibrationSample`

Versucht **eine** Aufnahme vom aktuellen Kamerabild. Manuell ausgelöst —
kein Zeitintervall, kein Bewegungsabgleich: der Operator sieht das Live-Bild
(Livestream oder `stream_viewer.py`, Abschnitt 12.5) und entscheidet selbst,
wann eine Pose gut ist, bevor er auslöst.

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` (`OK`, Board gefunden und übernommen), `1` (`INVALID_STATE`, keine Session aktiv), `5` (`DETECTION_FAILED`, Board in diesem Frame nicht gefunden — die Session läuft weiter, einfach erneut auslösen) |

### 12.3 `FinishCalibration`

Beendet die Session manuell, rechnet aus den gesammelten Samples und
speichert `data/calibration/<frame_id>.json` — derselbe Rechenkern wie im
CLI-Tool (`tagloc.boards.calibrate_from_samples`) und wie der automatische
Abschluss (Abschnitt 12.5). Gedacht für den Fall, dass der Operator **vor**
Erreichen der Abdeckungs-Schwelle abbrechen und trotzdem das bisherige
Ergebnis haben will — im Normalfall (Schwelle erreicht) ist die Session zu
diesem Zeitpunkt schon automatisch beendet, ein weiterer Aufruf liefert dann
nur noch `INVALID_STATE`.

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Summary` | `String` (JSON) | z. B. `{"rms":0.2945,"samples":21,"coverageX":0.96,"coverageY":0.95,"path":"data/calibration/cam_flange.json"}`, ggf. mit `warning` (siehe 12.5). Bei Fehlschlag `{"message": "...", "samples": N}` |
| `Error` | `Int32` | `0` (`OK`, gespeichert), `1` (`INVALID_STATE`, keine Session aktiv — auch wenn sie sich gerade automatisch beendet hat), `5` (`DETECTION_FAILED`, weniger als 3 Samples) |

### 12.4 `AbortCalibration`

Beendet die Session, **ohne** zu speichern — für den Fall, dass sich der
Operator vertan hat oder neu anfangen will.

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` (`OK`), `1` (`INVALID_STATE`, keine Session aktiv) |

### 12.5 `CalibrationProgress` (nur lesen)

```
ns=<vision>;s=VisionMachine.CalibrationProgress     Datentyp String (JSON)
```

Wird bei jedem Publish-Tick des Livestreams mitgeschrieben (kein eigener
Task) — läuft also auch mit, wenn `CameraStreamMode` gerade auf `off` steht.

| Feld | Bedeutung |
| --- | --- |
| `running` | `bool` — Session aktiv? |
| `samples` | Anzahl bisher erfasster Aufnahmen |
| `minSamples` | Mindestanzahl für ein erfolgreiches `FinishCalibration` (Config, Standard 15) |
| `coverageX`, `coverageY` | kumulierte Bildabdeckung der Board-Ecken über alle Samples, 0–1 |
| `result` | **nur vorhanden, sobald die Session beendet ist** (automatisch oder per `FinishCalibration`/`AbortCalibration`, siehe unten) |

Im Ruhezustand (keine Session je gestartet, oder nach `Abort` ohne
automatischen Abschluss): `{"running": false}`, ohne `result`. Mitverfolgen
lässt sich das auch visuell über den Livestream
(`CameraStreamMode="calibration"`, Abschnitt 10.1) — im Bild erscheinen dann
zusätzlich zur aktuellen Board-Erkennung die kumulierte Abdeckung und
`Aufnahmen X/minSamples`.

**Automatischer Abschluss:** Erreichen `coverageX` **und** `coverageY`
`calibration_coverage_threshold` (Config, Standard `0.7`, entspricht dem
Abbruchkriterium aus dem Testplan) und liegen genug Aufnahmen vor, rechnet
und speichert die Session **von selbst** — ausgelöst vom nächsten
`CaptureCalibrationSample`-Aufruf, der die Schwelle überschreitet. Kein
Aufruf von `FinishCalibration` nötig. Das Frontend erkennt das daran, dass
`running` auf `false` springt und `result` erscheint:

```jsonc
{
  "running": false, "samples": 18, "minSamples": 15,
  "coverageX": 0.84, "coverageY": 0.9,
  "result": {
    "error": 0, "rms": 2.069, "samples": 18,
    "coverageX": 0.84, "coverageY": 0.9,
    "path": "data/calibration/cam_ceiling.json",
    "warning": "RMS 2.069 px ueber dem Zielwert 0.5 px -- ..."
  }
}
```

`result.error` ist derselbe `Error`-Code wie bei `FinishCalibration`
(0 = `OK`). **Abdeckung allein sagt nichts über die tatsächliche
Genauigkeit** — ein Board, das nie gekippt wurde, füllt zwar den
Bildbereich, lässt die Brennweite aber unbestimmt (Testplan Abschnitt 3.3).
`result.warning` erscheint deshalb zusätzlich, wenn der RMS-Reprojektionsfehler
über 0,5 px liegt; sie verhindert das Speichern **nicht** — die Datei ist
trotzdem geschrieben, nur mit dem Hinweis, dass sie ungenauer als empfohlen
ist. Das Frontend sollte diese Warnung sichtbar anzeigen, nicht nur loggen.
`calibration_coverage_threshold: null` in der Config schaltet den
automatischen Abschluss ganz ab (nur noch manuelles `FinishCalibration`, wie
es die CLI-Tools weiter unterstützen).

Für Pis mit angeschlossenem Monitor gibt es dafür drei Kommandozeilen-Tools
unter `src/vision_server/tools/` (fürs Frontend-Team als Referenz, nicht
Teil des Frontends): `calibration_client.py` startet/beendet eine Session und
loggt `CalibrationProgress`; `stream_viewer.py` zeigt den Livestream in einem
lokalen Fenster und löst mit der Leertaste `CaptureCalibrationSample` aus —
zusammen der Handshake, den ein Frontend nachbilden muss. `diagnose_board.py`
ist reine Fehlersuche: holt ein unmarkiertes Rohbild vom laufenden Server und
probiert mehrere plausible `cols`/`rows`-Kombinationen gegen `detect_board`
durch, falls das Board im Stream zwar sichtbar, aber nicht erkannt wird —
z. B. weil die in `PI_APRILTAG_PRESETS` angenommene Geometrie nicht zum
tatsächlich aufgehängten Board passt.

### 12.6 `ActiveCalibrationInfo` (nur lesen)

```
ns=<vision>;s=VisionMachine.ActiveCalibrationInfo     Datentyp String (JSON)
```

Anders als `CalibrationProgress` (Fortschritt *einer laufenden Session*,
verschwindet mit dem nächsten `StartCalibration`) beschreibt dieser Knoten,
**was gerade tatsächlich für Posen benutzt wird** — er bleibt stehen, egal
was mit einer Session passiert, und ist die richtige Quelle für eine
dauerhafte "Zuletzt kalibriert am ..."-Anzeige im Frontend. Geschrieben beim
Start (was `open()` geladen hat) und nach jeder erfolgreichen interaktiven
Kalibrierung (derselbe Moment, in dem sie auch live übernommen wird, siehe
oben).

```jsonc
{
  "placeholder": false,
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T10:00:00+00:00",
  "createdAt": "2026-09-22T10:00:00+00:00",
  "rms": 0.2945,
  "samples": 21,
  "board": {"type": "chessboard", "cols": 7, "rows": 9, "squareSizeM": 0.022, ...},
  "path": "data/calibration/cam_flange.json"
}
```

| Feld | Bedeutung |
| --- | --- |
| `placeholder` | `true`, wenn noch nie echt kalibriert wurde (`allow_placeholder_calibration`, Abschnitt 9) — dann sind `rms`/`createdAt` `null`, Posen sind nicht maßhaltig |
| `calibrationId`, `createdAt` | aus der Kalibrierdatei, stabil über Neustarts |
| `rms`, `samples`, `board` | wie im `result`-Objekt aus `CalibrationProgress`/`FinishCalibration` |
| `path` | Pfad der Datei auf dem Pi (Diagnose, keine Backend-Bedeutung) |

### 12.7 Sperren

`StartCalibration` lehnt ab (`BUSY`), solange ein Job läuft. Umgekehrt lehnen
`StartSingleJob`/`StartContinuous` ab (`BUSY`), solange eine Kalibrier-Session
läuft — beide teilen sich Kamera und Detektor, gleichzeitig ergibt keinen
Sinn. Es gibt dafür **keinen eigenen State-Machine-Zustand**: das Nodeset
kennt keinen passenden Zustand für „Kalibrierung läuft", der Automat bleibt in
`Ready`, die Sperre läuft rein über die beiden Busy-Flags — dieselbe
`BUSY`-Semantik wie zwischen zwei Jobs (Abschnitt 5, Fehlercodes).

### 12.8 Stand

Für Layer 2 (Hand-Pi, `ADP-HandInEye-Kamera-Pi`, RealSense) real verifiziert
(`chessboard`, 7×9, 22 mm, RMS 0,2945 px bei 21 Aufnahmen über das CLI-Tool).

Layer 1 (Deckenkamera, `ADP-Roboter-Lokalisierung`, Picamera2) ist über den
interaktiven Weg (`CaptureCalibrationSample` + Stream-Viewer) einmal
durchgespielt worden — Board-Geometrie (7×9, 22 mm) über `diagnose_board.py`
bestätigt, `data/calibration/cam_ceiling.json` existiert. RMS lag beim ersten
Versuch bei 2,069 px (Abdeckung 84 %/90 %) — deutlich über dem Zielwert, da
die Kamera noch nicht fest montiert ist und das Board zu wenig gekippt
wurde. Sobald die Kamera fest hängt, muss neu kalibriert werden, jetzt mit
Fokus auf Neigung/Distanz-Variation statt nur Bildabdeckung (siehe die
`warning` in Abschnitt 12.5). Der Code selbst ist pi-unabhängig.
