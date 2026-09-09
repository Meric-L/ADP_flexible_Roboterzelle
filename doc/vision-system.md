# Vision-System — Ist-Stand

Diese Datei beschreibt, was im Repo **tatsächlich implementiert** ist. Für die
Backend-Schnittstelle siehe [`vision-server-interface.md`](vision-server-interface.md),
für den vollständigen Zielplan
[`vision-system-integration.md`](vision-system-integration.md).

## Zweck

Auf dem Raspberry Pi laufen **zwei getrennte OPC-UA-Server**:

| Server | Port | Inhalt |
| --- | --- | --- |
| Raspi-Server (`src/OPCUA/server.py`) | 4840 | eigenes `RaspiDevice`-Interface (CPU-Temperatur, Zähler, Sollwert); enthält zusätzlich noch den alten Vision-Smoke-Test |
| **Vision-Server (`src/vision_server/`)** | 4841 | eigenständige **OPC 40100 (Machine Vision)**-Implementierung mit vollem Job-Ablauf |

Der Vision-Server ist die Umsetzung von Phase 2 und 3 aus Teil 9 des Plans. Seine
Erkennungsstufe ist bewusst noch ein **Hello-World-Platzhalter**: Zustandsautomaten,
Events, Ergebnisablage, Validierung und Fehlerpfad sind echt, nur das erkannte
"Modul" ist erfunden.

## Dateien

| Datei | Inhalt |
| --- | --- |
| [`src/vision_server/`](../src/vision_server/) | Vision-Server (Paket, siehe Modultabelle unten) |
| [`src/vision_server/tools/hello_world_client.py`](../src/vision_server/tools/hello_world_client.py) | Testclient: Referenzimplementierung des Handshakes |
| [`src/OPCUA/server.py`](../src/OPCUA/server.py) | Raspi-Server (Temperatur-Interface + alter Smoke-Test) |
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
| `runner.py` | Composition Root, `@uamethod`-Wrapper |
| `detection/` | Strategie `DetectionSource`; aktuell nur `hello_world.py` |

Echte Bilderkennung anschließen = eine neue Datei in `detection/` plus ein
Registry-Eintrag; der Server-Kern kennt keine Bildverarbeitung.

## Adressraum (Vision-Server, Port 4841)

```
Objects/
└── VisionMachine                        ns=3;s=VisionMachine  (Typ: 2:VisionSystemType)
    ├── VisionStateMachine               Preoperational | Halted | Error | Operational
    │   └── AutomaticModeStateMachine    Initialized | Ready | SingleExecution | ContinuousExecution
    │       └── StartSingleJob           verlinkt (einzige implementierte Methode)
    ├── ResultManagement
    │   └── Results/LatestResult         (Typ: 2:ResultType, wird pro Job überschrieben)
    │       └── ResultContent[0]         String  <- JSON-Payload
    └── LatestResultJson                 String  <- dasselbe JSON, einfacher Knoten
```

Die `VisionSystem`-Instanz bekommt eine **String-NodeId** (`ns=3;s=VisionMachine`),
wodurch `instantiate()` auch alle Kinder mit sprechenden, stabilen NodeIds anlegt
(z. B. `…;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob`).
Das erspart dem Backend jede Discovery.

Die States der äußeren `VisionStateMachine` sind Mandatory-Kinder der Instanz und
werden per BrowseName geholt. Die States der `AutomaticModeStateMachine`
(`Initialized`/`Ready`/`SingleExecution`/`ContinuousExecution`) sind **keine**
Instanzkinder — sie existieren nur als feste Knoten am Typ
`VisionAutomaticModeStateMachineType` (i=5056–5059) und werden per NodeId geholt.

## Laufzeitverhalten

- Endpoint `opc.tcp://0.0.0.0:4841/vision/machine/`, ApplicationURI
  `urn:launch-rm:vision:machine`, `SecurityPolicy: NoSecurity`.
- Beim Start: `Preoperational → Operational`, innen `Initialized → Ready`.
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

# Vision-Server
PYTHONPATH=src python3 -m vision_server --port 4841 --log-level INFO

# Handshake einmal durchspielen
PYTHONPATH=src python3 src/vision_server/tools/hello_world_client.py \
    --url opc.tcp://127.0.0.1:4841/vision/machine/
```

Der Raspi-Server läuft auf dem Pi produktiv als systemd-Service
`opcua-server.service` (`Restart=always`, `RestartSec=5`,
`/etc/systemd/system/opcua-server.service` — nicht in diesem Repo versioniert).
Für den Vision-Server ist ein analoger Service `vision-server.service`
vorgesehen:

```ini
[Service]
WorkingDirectory=/home/pi/ADP_flexible_Roboterzelle
Environment=PYTHONPATH=/home/pi/ADP_flexible_Roboterzelle/src
ExecStart=/usr/bin/python3 -m vision_server --port 4841
Restart=always
RestartSec=5
```

**Wichtig beim Testen auf dem Pi:** Ein Vordergrundstart kollidiert mit einem
laufenden Service (Port belegt) — stattdessen `systemctl restart <service>`
verwenden und mit einem separaten Client dagegen testen, oder den
Vordergrundlauf auf einen freien Port legen (`--port 4842`).

## Verifizierter Stand

Lokal gegen asyncua 2.0.1 Ende-zu-Ende durchgelaufen (Server + Client in zwei
Prozessen): Happy Path inkl. Payload und Event-Reihenfolge, `BUSY` bei zwei
gleichzeitigen Aufrufen, `INVALID_ARGUMENT` bei überlanger `MeasId`,
`UNKNOWN_RECIPE` bei unbekanntem Rezept, Fehlerpfad über den `Error`-Zustand mit
anschließender Wiederaufnahme.

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
- Nur eine Instanz — der 3D-Server (Server 2, Port 4842) existiert noch nicht.
- Der alte Smoke-Test im Raspi-Server ist noch vorhanden (Doppelung), wird nach
  dem gemeinsamen Test auf einem eigenen Branch entfernt.
