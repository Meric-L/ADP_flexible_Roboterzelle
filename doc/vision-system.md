# Vision-System — Ist-Stand

Diese Datei beschreibt, was im Repo **tatsächlich implementiert** ist. Für die
Backend-Schnittstelle siehe [`vision-server-interface.md`](vision-server-interface.md),
für den vollständigen Zielplan
[`vision-system-integration.md`](vision-system-integration.md).

## Zweck

**Ein** OPC-UA-Server auf dem Raspberry Pi (`src/OPCUA/server.py`, Port 4840,
Endpoint `opc.tcp://<pi>:4840/raspi/server/`) mit zwei unabhängigen Bäumen:

| Baum | Inhalt |
| --- | --- |
| `RaspiDevice` | gewachsenes eigenes Interface: CPU-Temperatur, Zähler, Sollwert |
| `VisionMachine` | **OPC 40100 (Machine Vision)** mit vollem Job-Ablauf, implementiert im Paket `src/vision_server/` |

Das Vision-System ist die Umsetzung von Phase 2 und 3 aus Teil 9 des Plans. Seine
Erkennungsstufe ist bewusst noch ein **Hello-World-Platzhalter**: Zustandsautomaten,
Events, Ergebnisablage, Validierung und Fehlerpfad sind echt, nur das erkannte
"Modul" ist erfunden.

Das Vision-Paket ist als **Einbau** gebaut (`install_vision_machine(server, config)`)
und hängt seine Knoten in einen bestehenden Server. Ein zweiter Serverprozess wäre
Verschwendung: er würde den 40100-Adressraum ein zweites Mal laden (gemessen
~110 MB RSS pro Prozess) und das Backend zu zwei Sessions zwingen. Für Entwicklung
und isolierte Tests lässt sich das Paket zusätzlich standalone starten
(`python -m vision_server`), das ist aber nicht der Produktionsweg.

## Dateien

| Datei | Inhalt |
| --- | --- |
| [`src/vision_server/`](../src/vision_server/) | Vision-Server (Paket, siehe Modultabelle unten) |
| [`src/vision_server/tools/hello_world_client.py`](../src/vision_server/tools/hello_world_client.py) | Testclient: Referenzimplementierung des Handshakes |
| [`src/OPCUA/server.py`](../src/OPCUA/server.py) | Server der Zelle: Raspi-Interface + Einbau des Vision-Systems |
| [`src/OPCUA/Opc.Ua.MachineVision.NodeSet2.xml`](../src/OPCUA/Opc.Ua.MachineVision.NodeSet2.xml) | vendorierter offizieller OPC 40100-Nodeset; von **beiden** Servern geladen |
| [`requirements.txt`](../requirements.txt) | u. a. `asyncua` |

## Aufbau des Vision-Servers

| Modul | Aufgabe |
| --- | --- |
| `__main__.py` | argparse, Konfiguration, Start |
| `config.py` | `VisionServerConfig` (frozen dataclass) |
| `nodeset_ids.py` | NodeId-Konstanten des Nodesets |
| `address_space.py` | Nodeset-Import, `VisionSystem`-Instanz, `HasNotifier` |
| `state_machine.py` | Bindung beider 40100-Zustandsautomaten |
| `events.py` | Event-Generatoren, `ResultReadyEvent` mit Payload |
| `result_management.py` | Ergebnisknoten + JSON-Spiegelknoten |
| `payload.py` | JSON-Schema `wsc.vision.detections/1` |
| `errors.py` | Fehlercodes für den `Error`-Ausgang |
| `job.py` | Validierung, State-Guard, Job-Ablauf, Fehlerpfad |
| `runner.py` | `install_vision_machine()` (Einbau) und `run()` (standalone) |
| `detection/` | Strategie `DetectionSource`; aktuell nur `hello_world.py` |

Echte Bilderkennung anschließen = eine neue Datei in `detection/` plus ein
Registry-Eintrag; der Server-Kern kennt keine Bildverarbeitung.

## Adressraum (Port 4840)

```
Objects/
├── RaspiDevice                          ns=2  (unverändert; Setpoint bleibt ns=2;i=4)
│   ├── CpuTemperature   Double
│   ├── Counter          Int64
│   └── Setpoint         Double  (writable)
├── VisionSystem                         ns=2  Altlast: leere 40100-Instanz,
│   └── ResultManagement/Results/CpuTemperatureResult   trägt nur die CPU-Temperatur
└── VisionMachine                        ns=4;s=VisionMachine  (Typ: 3:VisionSystemType)
    ├── VisionStateMachine               Preoperational | Halted | Error | Operational
    │   └── AutomaticModeStateMachine    Initialized | Ready | SingleExecution | ContinuousExecution
    │       └── StartSingleJob           verlinkt (einzige implementierte Methode)
    ├── ResultManagement
    │   └── Results/LatestResult         (Typ: 3:ResultType, wird pro Job überschrieben)
    │       └── ResultContent[0]         String  <- JSON-Payload
    └── LatestResultJson                 String  <- dasselbe JSON, einfacher Knoten
```

Die Reihenfolge im Aufbau ist bindend: erst der raspi-Namespace und die
`RaspiDevice`-Knoten, dann der Nodeset-Import, dann der Vision-Namespace. Nur so
bleiben die vorhandenen NodeIds (`ns=2;i=4` für den Sollwert) gültig.

Die `VisionMachine`-Instanz bekommt eine **String-NodeId** (`ns=4;s=VisionMachine`),
wodurch `instantiate()` auch alle Kinder mit sprechenden, stabilen NodeIds anlegt
(z. B. `…;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob`).
Das erspart dem Backend jede Discovery.

Die States der äußeren `VisionStateMachine` sind Mandatory-Kinder der Instanz und
werden per BrowseName geholt. Die States der `AutomaticModeStateMachine`
(`Initialized`/`Ready`/`SingleExecution`/`ContinuousExecution`) sind **keine**
Instanzkinder — sie existieren nur als feste Knoten am Typ
`VisionAutomaticModeStateMachineType` (i=5056–5059) und werden per NodeId geholt.

## Laufzeitverhalten

- Endpoint `opc.tcp://0.0.0.0:4840/raspi/server/`, `SecurityPolicy: NoSecurity`.
- Alle 1 s: `Counter` hochzählen, `CpuTemperature` und
  `CpuTemperatureResult/ResultContent` mit der CPU-Temperatur füllen
  (unverändertes Verhalten des Raspi-Interfaces).
- Beim Start des Vision-Systems: `Preoperational → Operational`, innen `Initialized → Ready`.
- `StartSingleJob` prüft **synchron** Zustand und Eingaben und quittiert mit
  `(JobId, Error)`; der Job selbst läuft asynchron und meldet sich per Events.
- Zustandsfolge eines Jobs: `Ready → SingleExecution → Ready`, dazu die Events
  `StateChangedEvent`, `JobStartedEvent`, `AcquisitionDoneEvent`,
  `ResultReadyEvent`, `ReadyEvent` — alle emittiert vom `VisionMachine`-Knoten.
- Das Ergebnis-JSON reist **im `ResultReadyEvent`** (`ResultContent[0]`) mit; es
  wird zusätzlich in die Ergebnisknoten geschrieben. Kein Polling nötig.
- Nur **ein** Job gleichzeitig; ein zweiter Aufruf wird mit `BUSY` abgelehnt.
- Fehler in der Erkennung führen über `Error` und zurück nach `Operational`,
  und werden als `ResultReadyEvent` mit `resultState != 0` gemeldet.

## Starten

```bash
pip install -r requirements.txt

# Server der Zelle (Raspi-Interface + Vision-System)
python3 src/OPCUA/server.py

# Handshake einmal durchspielen
PYTHONPATH=src python3 src/vision_server/tools/hello_world_client.py \
    --url opc.tcp://127.0.0.1:4840/raspi/server/

# Nur das Vision-System, isoliert (Entwicklung)
PYTHONPATH=src python3 -m vision_server --port 4841 --log-level INFO
```

`server.py` legt `src/` selbst auf den `sys.path`, damit der Startbefehl des
Services unverändert bleiben kann.

Auf dem Pi läuft der Server produktiv als systemd-Service `opcua-server.service`
(`Restart=always`, `RestartSec=5`, `/etc/systemd/system/opcua-server.service` —
nicht in diesem Repo versioniert). **Wichtig beim Testen auf dem Pi:** Kein
Vordergrundstart, das kollidiert mit dem Service (Port 4840 belegt) —
stattdessen `systemctl restart opcua-server.service` und mit einem separaten
Client dagegen testen.

## Verifizierter Stand

Lokal gegen asyncua 2.0.1 Ende-zu-Ende durchgelaufen, im
Produktionszuschnitt (`src/OPCUA/server.py` auf 4840) und mit separatem Client:
Happy Path inkl. Payload und Event-Reihenfolge, `BUSY` bei zwei gleichzeitigen
Aufrufen, `INVALID_ARGUMENT` bei überlanger `MeasId`, `UNKNOWN_RECIPE` bei
unbekanntem Rezept, Fehlerpfad über den `Error`-Zustand mit anschließender
Wiederaufnahme.

Rückwärtskompatibilität geprüft: `ns=2;i=4` ist weiterhin der schreibbare
Sollwert, `RaspiDevice/CpuTemperature` und `Counter` laufen, und
`VisionSystem/ResultManagement/Results/CpuTemperatureResult` liefert unverändert
einen Double.

## Bekannte Einschränkungen

- Die Erkennung ist ein Platzhalter: `moduleId` ist `HELLO-WORLD`, die Pose immer
  Null. Das Payload-Schema ist aber schon das endgültige.
- `frameId` ist fest `"world"`; ohne Hand-Auge-Kalibrierung haben die Posen keine
  reale Bedeutung.
- Verlinkt ist nur `StartSingleJob`. `StartContinuous`/`Stop`/`Abort`/
  `SimulationMode`/`Reset`/`Halt` existieren im Adressraum, tun aber nichts.
- Die `ResultManagement`-Methoden (`GetResultById` & Co.) sind nicht
  implementiert und vom asyncua-Client aus auch nicht aufrufbar — sie erwarten
  Struktur-ExtensionObjects als Eingabe (asyncua-Issue #1693).
- Die strukturtypisierten Event-Felder (`ResultId`, `JobId`, …) werden leer
  gesendet; ihre Werte stehen im JSON-Payload.
- Events erreichen nur ein Abo direkt auf `VisionMachine`. Die
  `HasNotifier`-Referenz ist gesetzt, bewirkt bei asyncua serverseitig aber kein
  Event-Bubbling.
- Nur eine Vision-Instanz — ein zweites Profil (3D) existiert noch nicht.
- Im Adressraum liegt neben `VisionMachine` noch die alte, leere
  `VisionSystemType`-Instanz `2:VisionSystem`, die nur `CpuTemperatureResult`
  trägt. Eine typbasierte Suche nach Vision-Systemen würde deshalb zwei
  Instanzen finden — Clients müssen `ns=4;s=VisionMachine` fest verwenden. Die
  Altlast verschwindet, sobald das Temperatur-Interface auf
  `RaspiDevice/CpuTemperature` umgestellt ist.
