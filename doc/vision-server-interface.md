# Vision-Server — Systemaufbau und Backend-Schnittstelle

Diese Datei beschreibt den eigenständigen OPC-UA-Vision-Server (OPC 40100,
Machine Vision) und **was ein Backend implementieren muss**, um von ihm ein
Ergebnis zu bekommen. Sie ist ohne Kenntnis dieses Repos benutzbar.

Stand: Erkennungsstufe ist ein **Hello-World-Platzhalter** — der komplette
Job-Ablauf (Zustandsautomaten, Events, Ergebnisablage, Fehlerpfad) ist echt,
nur das erkannte "Modul" ist erfunden. Der Umstieg auf echte Bilderkennung
ändert das Payload-Format **nicht**.

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
    │       ├── StartSingleJob           <- die einzige verlinkte Methode
    │       └── StartContinuous | Stop | Abort | SimulationMode   (nicht implementiert)
    ├── ResultManagement
    │   ├── Results/LatestResult         (Typ: 3:ResultType, wird pro Job überschrieben)
    │   │   └── ResultContent[0]         JSON-String des letzten Ergebnisses
    │   └── GetResultById | ReleaseResultHandle | ...   (nicht implementiert, siehe 7.3)
    └── LatestResultJson                 ns=4;s=VisionMachine.LatestResultJson
                                         derselbe JSON-String, als einfacher String-Knoten
```

Interner Aufbau (Python-Paket `src/vision_server/`):

| Modul | Aufgabe |
| --- | --- |
| `address_space.py` | Nodeset-Import, `VisionSystem`-Instanz, `HasNotifier` |
| `state_machine.py` | beide 40100-Zustandsautomaten |
| `events.py` | Event-Generatoren, `ResultReadyEvent` mit Payload |
| `result_management.py` | Ergebnisknoten + JSON-Spiegel |
| `job.py` | Validierung, Guard, Job-Ablauf, Fehlerpfad |
| `detection/` | Strategie `DetectionSource`; aktuell `hello_world.py` |
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
| `RecipeId` | `RecipeIdExternalDataType` | String, siehe Job-Tabelle unten |
| `ProductId` | `ProductIdDataType` | String (leer erlaubt) |
| `Parameters` | `BaseDataType[]` | String-Array, max. 16 Einträge |

| Ausgabe | Nodeset-Typ | **Tatsächlich geliefert** |
| --- | --- | --- |
| `JobId` | `JobIdDataType` | String, z. B. `job-000001`; leer bei Ablehnung |
| `Error` | `Int32` | Fehlercode, siehe unten |

### Verfügbare Jobs (`RecipeId`)

Jede `RecipeId` waehlt ein eigenes Script auf dem Pi aus. Alle drei laufen
ueber denselben `StartSingleJob`-Aufruf und denselben Event-/Payload-Ablauf
aus Abschnitt 4 und 6 — nur `attributes.message` und `moduleId` im Ergebnis
unterscheiden sich. Die Logik hinter den Scripts ist aktuell noch ein
Platzhalter (siehe Abschnitt 9); die Schnittstelle aendert sich beim Umstieg
auf echte Logik nicht.

| `RecipeId` | Job | Ausgefuehrtes Script | `attributes.message` im Ergebnis |
| --- | --- | --- | --- |
| `""` oder `"hello-world"` | Platzhalter ohne Bildverarbeitung | — (in-process, kein Subprozess) | `"Hello World"` |
| `"calibration"` | Kalibrierung | `src/vision_server/scripts/calibrate.py` | `"Calibrieren"` |
| `"image-recognition"` | Bilderkennung | `src/vision_server/scripts/take_image.py` | `"Taking Image"` |

Eine unbekannte `RecipeId` wird sofort mit `Error=4` (`UNKNOWN_RECIPE`)
abgelehnt, siehe Fehlercode-Tabelle unten.

**Bewusste Abweichung:** Das Nodeset deklariert die Ids als Strukturen. Ein
asyncua-Client kann solche ExtensionObjects ohne
`load_data_type_definitions()` weder bauen noch lesen (asyncua-Issue #1693),
deshalb werden hier Strings ausgetauscht. Ein UA-Browser wie UaExpert zeigt
dementsprechend einen String, wo der Typ eine Struktur erwartet — das ist
erwartet, kein Fehler.

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

Faustregel: **1–4 werden sofort im Rückgabewert abgelehnt** (der Automat bleibt
`Ready`, es folgt kein Event). **5–6 passieren während der Ausführung** und
kommen als `ResultReadyEvent` mit `resultState != 0` — ein eventgetriebener
Client erfährt einen Erkennungsfehler also nie erst per Timeout.

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
  "frameId": "world",                 // Bezugsrahmen der Posen
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

Verlinkt ist nur `StartSingleJob`. `StartContinuous`, `Stop`, `Abort`,
`SimulationMode`, `Reset`, `Halt`, `SelectModeAutomatic` und die `Sync`-Methoden
der StepModels sind im Adressraum sichtbar, haben aber keine Implementierung —
ein Aufruf tut nichts. Ein Backend darf sie nicht für den Ablauf voraussetzen.

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

- **Echte Kalibrierung/Erkennung**: `calibration` und `image-recognition`
  fuehren bereits eigene Scripts aus (`src/vision_server/scripts/`), deren
  Inhalt aber noch Platzhalter ist. Payload-Schema bleibt beim Nachruesten
  der echten Logik unveraendert. Bis dahin ist `moduleId` erfunden und die
  Pose immer Null.
- **Koordinatensystem**: `frameId` ist derzeit fest `"world"`, ohne
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
