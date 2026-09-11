# Vision-Server — Systemaufbau und Backend-Schnittstelle

Diese Datei beschreibt den eigenständigen OPC-UA-Vision-Server (OPC 40100,
Machine Vision) und **was ein Backend implementieren muss**, um von ihm ein
Ergebnis zu bekommen. Sie ist ohne Kenntnis dieses Repos benutzbar.

Stand: `hello-world` ist weiterhin ein **Platzhalter** ohne Bildverarbeitung,
`calibration` ebenso. `image-recognition` steuert echt die Pi-Kamera an und
sucht per QR-Code — der komplette Job-Ablauf (Zustandsautomaten, Events,
Ergebnisablage, Fehlerpfad) ist für alle drei identisch. Der Umstieg auf
echte Bilderkennung ändert das Payload-Format **nicht**.

## 1. Systemaufbau

**Ein** OPC-UA-Server auf dem Raspberry Pi (systemd-Service `opcua-server.service`,
Port 4840). Er trägt zwei unabhängige Bäume: das gewachsene Raspi-Interface und
das Vision-System.

```
Raspberry Pi — opc.tcp://<pi>:4840/raspi/server/
├── RaspiDevice        CPU-Temperatur, Counter, Setpoint (Temperatur-Interface)
├── VisionMachine      OPC 40100 VisionSystemType        <- dieses Dokument
└── VisionSystem       Altlast: leere 40100-Instanz, trägt nur
                       CpuTemperatureResult (siehe 7.5) — NICHT verwenden
```

Adressraum des Vision-Systems:

```
Objects/
└── VisionMachine                        ns=4;s=VisionMachine   (Typ: 3:VisionSystemType)
    ├── VisionStateMachine               Preoperational | Halted | Error | Operational
    │   └── AutomaticModeStateMachine    Initialized | Ready | SingleExecution | ContinuousExecution
    │       ├── StartSingleJob           <- Job starten
    │       ├── Stop                     <- laufenden Job abbrechen, siehe Abschnitt 5
    │       └── StartContinuous | Abort | SimulationMode   (nicht implementiert)
    ├── ResultManagement
    │   ├── Results/LatestResult         (Typ: 3:ResultType, wird pro Job überschrieben)
    │   │   └── ResultContent[0]         JSON-String des letzten Ergebnisses
    │   └── GetResultById | ReleaseResultHandle | ...   (nicht implementiert, siehe 7.3)
    ├── LatestResultJson                 ns=4;s=VisionMachine.LatestResultJson
    │                                    derselbe JSON-String, als einfacher String-Knoten
    └── LatestCameraFrame                ns=4;s=VisionMachine.LatestCameraFrame
                                         Base64-JPEG des Kamera-Livestreams, siehe Abschnitt 10
```

Interner Aufbau (Python-Paket `src/vision_server/`):

| Modul | Aufgabe |
| --- | --- |
| `address_space.py` | Nodeset-Import, `VisionSystem`-Instanz, `HasNotifier` |
| `state_machine.py` | beide 40100-Zustandsautomaten |
| `events.py` | Event-Generatoren, `ResultReadyEvent` mit Payload |
| `result_management.py` | Ergebnisknoten + JSON-Spiegel |
| `job.py` | Validierung, Guard, Job-Ablauf, Fehlerpfad |
| `detection/` | Strategie `DetectionSource`; `hello_world.py`, `image_recognition.py` (QR), `script_runner.py` (Kalibrierung) |
| `camera.py` | `SharedCamera` — ein Capture-Loop, geteilt von QR-Erkennung und Livestream |
| `camera_stream.py` | Schreibt Kamera-Frames als Base64-JPEG in `LatestCameraFrame`, siehe Abschnitt 10 |
| `payload.py` | JSON-Schema `wsc.vision.detections/1` |

Echte Erkennung anschließen = **eine neue Datei in `detection/` plus ein
Registry-Eintrag**. Der Server-Kern und diese Schnittstelle bleiben unberührt.

## 2. Verbindungsdaten

| | Wert |
| --- | --- |
| Endpoint | `opc.tcp://<pi>:4840/raspi/server/` |
| Security | `NoSecurity` (keine Authentifizierung/Verschlüsselung) |
| ServerName | `Raspberry Pi OPC UA Server` |
| Namespace Vision | `http://launch-rm.de/vision` (aktuell ns=4) |
| Namespace 40100 | `http://opcfoundation.org/UA/MachineVision` (aktuell ns=3) |
| Namespace Raspi | `http://launch-rm.de/raspi` (ns=2, nicht Vision-relevant) |

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

Jede `RecipeId` waehlt ein Erkennungsprofil (`detection/`). Alle drei laufen
ueber denselben `StartSingleJob`-Aufruf und denselben Event-/Payload-Ablauf
aus Abschnitt 4 und 6 — nur `attributes.message` und `moduleId` im Ergebnis
unterscheiden sich. `calibration` ist weiterhin ein Platzhalter (Subprozess-
Script, siehe Abschnitt 9); `image-recognition` steuert echt die Pi-Kamera an.

| `RecipeId` | Job | Implementierung | `attributes.message` im Ergebnis |
| --- | --- | --- | --- |
| `""` oder `"hello-world"` | Platzhalter ohne Bildverarbeitung | in-process, `detection/hello_world.py` | `"Hello World"` |
| `"calibration"` | Kalibrierung (Platzhalter) | Subprozess, `src/jobs/calibrate.py` | `"Calibrieren"` |
| `"image-recognition"` | QR-Code-Erkennung | in-process, `detection/image_recognition.py`, liest von der geteilten Kamera (Abschnitt 10) | dekodierter QR-Text, sonst `"Kein QR Code gefunden"` |

`image-recognition` sucht bis zu 30 s (`qr_scan_duration_s` in
`CameraStreamConfig`) und bricht ab, sobald ein QR-Code dekodiert werden
konnte. Deshalb liegt `job_timeout` (Abschnitt 9) bei 40 s statt 10 s. Die
Kamera wird dafuer **nicht** extra geoeffnet — sie laeuft bereits fuer den
Livestream (Abschnitt 10) und wird nur mitgelesen.

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

### 7.4 Es gibt zwei 40100-Instanzen im Adressraum — nur eine ist echt

Neben `VisionMachine` existiert eine ältere, leere `VisionSystemType`-Instanz
`ns=2;s=…VisionSystem` (BrowseName `2:VisionSystem`). Sie ist eine Altlast und
dient nur noch als Container für `CpuTemperatureResult`, das die CPU-Temperatur
des Pi trägt — **keine** Erkennung, keine Methoden, keine Events.

Konsequenz: **Nicht per Typ suchen.** Wer Instanzen von `VisionSystemType`
einsammelt, findet zwei Systeme, davon eines mit einem Temperaturwert im
`ResultContent`. Immer die feste NodeId `ns=<vision>;s=VisionMachine` verwenden
(deshalb hat sie eine sprechende String-Id). Die Altlast wird entfernt, sobald
das Temperatur-Interface auf `RaspiDevice/CpuTemperature` umgestellt ist.

### 7.5 Nicht implementierte Methoden

Verlinkt sind `StartSingleJob` und `Stop` (Abschnitt 5). `StartContinuous`,
`Abort`, `SimulationMode`, `Reset`, `Halt`, `SelectModeAutomatic` und die
`Sync`-Methoden der StepModels sind im Adressraum sichtbar, haben aber keine
Implementierung — ein Aufruf liefert `BadNothingToDo` auf OPC-UA-Statusebene
(asyncua-Default fuer eine unverlinkte Methode), nicht etwa `Error != 0` im
Output. Ein Backend darf sie nicht für den Ablauf voraussetzen.

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

- **Echte Kalibrierung**: `calibration` fuehrt weiterhin nur einen Platzhalter
  aus (`src/jobs/calibrate.py`). `image-recognition`
  (`detection/image_recognition.py`) steuert echt die Pi-Kamera per Picamera2
  an und sucht per OpenCV nach einem QR-Code. Payload-Schema bleibt beim
  Nachruesten der echten Kalibrierungslogik unveraendert. Bis dahin ist
  `moduleId` erfunden und die Pose immer Null.
- **Job-Timeout**: eine Erkennung, die laenger als `job_timeout` (40 s, wegen
  des bis zu 30 s laufenden QR-Scans) braucht, wird abgebrochen und als
  `DETECTION_FAILED` gemeldet; der Automat kehrt nach `Ready` zurueck. Ein
  blockierter Worker-Thread laesst den *naechsten* Job desselben Profils
  allerdings ebenfalls in den Timeout laufen.
- **Koordinatensystem**: `frameId` haengt an der Quelle; ohne
  Hand-Auge-Kalibrierung. Was `position`/`orientation` real bedeuten, hängt an
  der noch offenen Kalibrierung — ein automatisches Anfahren erkannter Posen
  darf bis dahin nicht scharf geschaltet werden.
- **Zweite Kamera / 3D-Profil**: würde als zweite `VisionSystemType`-Instanz im
  selben Server hängen (eigener Instanzname und eigene `visionSystemId`),
  dieselbe Schnittstelle. Ein zweiter Serverprozess ist nicht vorgesehen —
  er würde denselben 40100-Adressraum ein zweites Mal laden (~110 MB).
- **Altlast-Instanz `2:VisionSystem` entfernen** (siehe 7.4).
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
  konfiguriert wurde (auf dem Pi über `OPCUA/server.py` der Fall, beim
  lokalen `python -m vision_server` standardmäßig **nicht** — dort fehlt
  i. d. R. die Kamera).
- Läuft die Kamera nicht (Fehler beim Öffnen), existiert der Knoten zwar,
  bleibt aber leer (`""`) — kein Fehlerzustand des Automaten, rein
  Stream-lokal.
- Livestream und `image-recognition`-Job teilen sich **dieselbe** Kamera
  (`SharedCamera` in `camera.py`): ein QR-Job liest nur die zwischengespeicherten
  Frames mit, öffnet die Hardware nicht erneut. Während eines laufenden
  QR-Jobs bleibt der Stream daher unverändert aktiv, es gibt kein Aussetzen.
- Auflösung, Bildrate und JPEG-Qualität stehen in `CameraStreamConfig`
  (`profiles.py`) — Standard 1280×720, 5 fps, Qualität 70.
