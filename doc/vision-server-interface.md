# Vision-Server — Systemaufbau und Backend-Schnittstelle

Diese Datei beschreibt den eigenständigen OPC-UA-Vision-Server (OPC 40100,
Machine Vision) und **was ein Backend implementieren muss**, um von ihm ein
Ergebnis zu bekommen. Sie ist ohne Kenntnis dieses Repos benutzbar.

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
    │       ├── StartContinuous          <- Dauerbetrieb, siehe Abschnitt 7.5
    │       ├── Abort                   <- wie Stop, ueber den Abort-Uebergang
    │       └── SimulationMode          (nicht verlinkt, siehe Abschnitt 7.5)
    ├── ResultManagement
    │   ├── Results/LatestResult         (Typ: 3:ResultType, wird pro Job überschrieben)
    │   │   └── ResultContent[0]         JSON-String des letzten Ergebnisses
    │   └── GetResultById | ReleaseResultHandle | ...   (nicht implementiert, siehe 7.3)
    ├── LatestResultJson                 ns=4;s=VisionMachine.LatestResultJson
    │                                    derselbe JSON-String, als einfacher String-Knoten
    ├── LatestCameraFrame                ns=4;s=VisionMachine.LatestCameraFrame
    │                                    Base64-JPEG des Kamera-Livestreams, siehe Abschnitt 10
    ├── CameraStreamMode                 ns=4;s=VisionMachine.CameraStreamMode
    │                                    **beschreibbar**: off | apriltag | calibration
    └── Calibration                      ns=4;s=VisionMachine.Calibration
                                         Fernkalibrierung der Kamera, siehe Abschnitt 12
```

Interner Aufbau (Python-Paket `src/vision_server/`):

| Modul | Aufgabe |
| --- | --- |
| `address_space.py` | Nodeset-Import, `VisionSystem`-Instanz, `HasNotifier` |
| `state_machine.py` | beide 40100-Zustandsautomaten |
| `events.py` | Event-Generatoren, `ResultReadyEvent` mit Payload |
| `result_management.py` | Ergebnisknoten + JSON-Spiegel |
| `job.py` | Validierung, Guard, Job-Ablauf, Fehlerpfad |
| `detection/` | Strategie `DetectionSource`; `hello_world.py`, `apriltag.py` (AprilTags), `script_runner.py` (Kalibrierprüfung) |
| `camera.py` | `SharedCamera` — ein Capture-Loop, geteilt von Erkennung und Livestream |
| `camera_stream.py` | Schreibt Kamera-Frames als Base64-JPEG in `LatestCameraFrame`, siehe Abschnitt 10 |
| `stream_overlay.py` | Markiert erkannte Tags im Livestream-Bild, siehe Abschnitt 10 |
| `calibration_session.py` | Fernkalibrierung: Aufnahmeschleife, Status-JSON, eigenes Overlay, siehe Abschnitt 12 |
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
unterscheiden sich nur in der `AprilTagProfileConfig`, die `OPCUA/server.py` je
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
- Livestream und `apriltag`-Job teilen sich **dieselbe** Kamera (`SharedCamera`
  in `camera.py`): der Job liest nur die zwischengespeicherten Frames mit,
  öffnet die Hardware nicht erneut. Während eines laufenden Jobs bleibt der
  Stream daher unverändert aktiv, es gibt kein Aussetzen.
- Auflösung, Bildrate und JPEG-Qualität stehen in `CameraStreamConfig`
  (`profiles.py`) — Standard 1280×720, 5 fps, Qualität 70.
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
- Gezeichnet wird auf einer Kopie; der geteilte Frame bleibt unverändert.
- Ein Fehler im Overlay beendet den Stream nicht — dann kommt das unmarkierte
  Bild, und der Fehler steht im Log.

Das Frontend braucht dafür nur ein Auswahlfeld, das beim Wechsel diesen Knoten
schreibt. Die Beschriftungen stehen serverseitig in
`tagloc.modes.OVERLAY_MODE_LABELS`. Ablauf und Abnahme:
[`apriltag-e2e-test.md`](apriltag-e2e-test.md) Abschnitt 4.

---

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

Angelegt wird nur, was in der `AssetConfig` des Pis steht (`src/OPCUA/server.py`,
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
[`src/OPCUA/nodesets/README.md`](../src/OPCUA/nodesets/README.md).

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

## 12. Kamerakalibrierung aus der Ferne

Ohne Kalibrierdatei öffnet die AprilTag-Quelle nicht, das Vision-System bleibt
in `Preoperational` und jeder Job wird abgelehnt. Erzeugt wurde die Datei bisher
nur an der Kommandozeile mit `tagloc.cli.calibrate` — also mit Tastatur neben
der Kamera oder über den Umweg Bilder sammeln und nachrechnen
([`apriltag-e2e-test.md`](apriltag-e2e-test.md) 3.3). Über diese Schnittstelle
kalibriert stattdessen jemand aus dem Frontend, während er das Board vor die
Kamera hält.

**Ausdrücklich kein Teil von OPC 40100** — der Standard kennt keinen
Kalibriermodus. Die Knoten liegen deshalb im eigenen Namensraum unterhalb von
`VisionMachine` und rühren die Zustandsautomaten nicht an: kalibriert werden
muss gerade dann, wenn das System **nicht** betriebsbereit ist.

### 12.1 Knoten

```
ns=<vision>;s=VisionMachine.Calibration
├── Status     String, JSON (wsc.vision.calibration-status/1), nur der Server schreibt
├── Start      (Settings: String)  -> (Error: Int32, Message: String)
├── Capture    ()                  -> (Error: Int32, Message: String)
├── Compute    ()                  -> (Error: Int32, Message: String)
├── Save       ()                  -> (Error: Int32, Message: String)
└── Cancel     ()                  -> (Error: Int32, Message: String)
```

Alle Knoten haben sprechende String-NodeIds, auch die `InputArguments`- und
`OutputArguments`-Eigenschaften der Methoden. `Error` benutzt dieselben Codes
wie `StartSingleJob` (Abschnitt 5); `Message` ist ein Satz **für den Bediener**
und sollte angezeigt werden, statt den Code zu übersetzen.

Die Knoten existieren nur, wenn der Server mit `camera_stream` konfiguriert ist
— die Kalibrierung braucht dieselbe Kamera. Fehlen sie, antwortet der Server auf
einen Aufruf mit `BadNodeIdUnknown`.

### 12.2 Ablauf

```
Frontend                                   Vision-Server
   |  subscribe(Calibration.Status)            |   (wie jeder andere Knoten)
   |------- Start(Board als JSON) ------------>|   prueft Board, startet die Auswertung
   |<------ (0, "Kalibrierung cal-0001 ...") --|   state: collecting
   |                                           |
   |   Status ~3x/s: Board sichtbar? ruhig? wie viele Aufnahmen? naechster Schritt?
   |   Der Server nimmt selbst auf, sobald das Board ruhig in einer NEUEN Ansicht steht
   |------- Capture() ------------------------>|   (optional, nimmt sofort auf)
   |------- Compute() ------------------------>|   state: computing -> review
   |<------ Status mit result (RMS, Abdeckung, Bewertung)
   |------- Save() --------------------------->|   Backup, Datei schreiben, Quelle uebernimmt
   |<------ Status mit saved, state: done      |   Preoperational -> Operational
```

`Cancel` verwirft jederzeit (außer während `Save`, das mit `BUSY` antwortet).
Das Ergebnis wird **erst mit `Save` geschrieben**: eine schlechte Kalibrierung
darf eine gute nicht unbemerkt überschreiben. Die alte Datei bleibt als
`<name>.json.bak-<Zeitstempel>` liegen, geschrieben wird über eine temporäre
Datei und `os.replace`.

### 12.3 `Start`-Einstellungen

```jsonc
{
  "board": {
    "type": "charuco",        // oder "chessboard"
    "cols": 7,                // ChArUco: Felder. Schachbrett: INNERE ECKEN (9 bei 10 Feldern)
    "rows": 5,
    "squareSizeM": 0.030,     // SI-Meter, nicht Millimeter
    "markerSizeM": 0.022,     // nur ChArUco, muss kleiner als squareSizeM sein
    "dictionary": "DICT_4X4_50"
  },
  "targetSamples": 20,        // Ziel fuer die automatische Aufnahme (min. 15)
  "autoCapture": true
}
```

Die Schlüssel sind die des `board`-Eintrags in der Kalibrierdatei
(`BoardSpec.as_dict`) — was eine Datei dokumentiert, lässt sich unverändert
wieder als Einstellung schicken. Ungültige Werte werden mit `INVALID_ARGUMENT`
und einem Klartextgrund abgelehnt, ohne dass eine Sitzung startet.

### 12.4 Status-Dokument

```jsonc
{
  "schema": "wsc.vision.calibration-status/1",
  "state": "collecting",      // idle | collecting | computing | review | saving | done
  "sessionId": "cal-0001",
  "frameId": "cam_ceiling",
  "calibrationPath": "/home/pi/.../data/calibration/cam_ceiling.json",
  "minSamples": 15, "maxSamples": 60,
  "settings": { "board": { }, "targetSamples": 20, "autoCapture": true },
  "imageSize": [1280, 720],
  "sampleCount": 7,
  "cameraOk": true,           // kommen ueberhaupt Bilder?
  "boardVisible": true, "boardStill": false, "cornerCount": 24,
  "progress": { "x": 0.8, "y": 0.4, "size": 0.3, "skew": 0.2 },  // je [0,1]
  "coverage": [0.72, 0.55],   // Bildabdeckung aller Aufnahmen
  "grid": [[2,0,1],[1,3,0],[0,0,0]],   // Aufnahmen je Bilddrittel, Zeile 0 = oben
  "hint": { "code": "move_to_region", "text": "Board in den Bildbereich oben links bewegen." },
  "lastCapture": { "index": 7, "reason": "auto", "at": "2026-09-21T12:10:03+00:00" },
  "result": null,             // nach Compute: rmsReprojectionError, coverage, quality, notes, ...
  "saved": null,              // nach Save: path, backupPath, applied, message
  "current": { "exists": true, "calibrationId": "...", "imageSize": [1280, 720] },
  "error": null,
  "updatedAt": "2026-09-21T12:10:04+00:00"
}
```

`hint.text` ist die eigentliche Bedienerführung — **anzeigen, nicht selbst
formulieren**. `hint.code` ist stabil und eignet sich für eigene Texte oder
Icons: `no_camera`, `show_board`, `hold_still`, `capturing`, `move_to_region`,
`tilt`, `vary_distance`, `move_horizontal`, `move_vertical`, `new_pose`,
`enough`.

`current` beschreibt die Datei, die **gerade gilt** — damit lässt sich vor dem
Start anzeigen, womit zuletzt kalibriert wurde, und das Board-Formular
vorbelegen.

### 12.5 Was der Server dabei selbst entscheidet

- **Welche Aufnahme zählt.** Jede Ansicht wird auf vier Zahlen reduziert
  (Position x/y, scheinbare Größe, Verkippung — `tagloc.calibration_guide`,
  nach dem Vorbild von ROS `camera_calibration`). Aufgenommen wird nur, was
  sich von allen bisherigen Ansichten ausreichend unterscheidet und dabei ruhig
  gehalten wird. Das verhindert 20 Aufnahmen derselben Pose — die häufigste
  Ursache für eine Kalibrierung mit gutem RMS und unbrauchbarer Verzeichnung.
- **Was als Nächstes zu tun ist** (`hint`): fehlender Bildbereich vor
  Verkippung vor Abstand.
- **Wie gut das Ergebnis ist** (`result.quality`): `good` bei RMS < 0,5 px
  **und** Abdeckung ≥ 70 %, `usable` bis RMS < 1,0 px und Abdeckung ≥ 60 %,
  sonst `poor`. `result.notes` nennt jeden Grund im Klartext. Dieselben
  Schwellen wie in [`apriltag-e2e-test.md`](apriltag-e2e-test.md).

### 12.6 Auflösung — der eigentliche Gewinn

Die Kalibrierung entsteht aus den Bildern der **geteilten Kamera**
(`SharedCamera`, Abschnitt 10), also mit genau der Auflösung, die auch die
Erkennung sieht. Eine mit der CLI bei anderer Auflösung erzeugte Datei lehnt
`check_resolution` beim ersten Job ab; bei anderem Seitenverhältnis lässt sie
sich nicht einmal umrechnen. Über diesen Weg kann das nicht passieren.

### 12.7 Nebenwirkungen, die ein Client kennen sollte

- Während `collecting` stellt der Server `CameraStreamMode` auf `calibration`
  und zeichnet sein eigenes Overlay (gefundene Ecken, abgedeckte Bildbereiche,
  Aufnahmezähler, grüner Rahmen bei jeder Aufnahme). Danach setzt er den Modus
  auf den vorherigen Wert zurück — außer jemand hat ihn inzwischen selbst
  geändert.
- Nach `Save` lädt die AprilTag-Quelle die neue Datei. War sie mangels
  Kalibrierung nie geöffnet, wird sie jetzt geöffnet, und das System geht von
  `Preoperational` nach `Operational`. `saved.applied` sagt, ob das geklappt
  hat, `saved.message` warum nicht.
- Damit das überhaupt möglich ist, öffnet der Server die **Kamera auch dann**,
  wenn die Erkennungsquelle nicht startet. Livestream und Kalibrierung laufen
  also auch auf einem Pi ohne Kalibrierdatei; die Quelle selbst bleibt zu.
- Es läuft höchstens **eine** Sitzung. Ein zweites `Start` wird mit
  `INVALID_STATE` abgelehnt, statt die laufende zu überschreiben — zwei
  Frontends sehen über `Status` dieselbe Sitzung.
