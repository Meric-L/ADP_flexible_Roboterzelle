# Vision-System — Ist-Stand

Diese Datei beschreibt, was im Repo **tatsächlich implementiert** ist. Für die
Backend-Schnittstelle siehe [`vision-server-interface.md`](vision-server-interface.md),
für den vollständigen Zielplan
[`vision-system-integration.md`](vision-system-integration.md).

## Zweck

**Ein** OPC-UA-Server auf dem Raspberry Pi (`src/vision_server/server.py`, Port 4840,
Endpoint `opc.tcp://<pi>:4840/raspi/server/`) mit **zwei Sichten auf denselben
Job**:

| Knoten | Inhalt |
| --- | --- |
| `Machines/VisionMachine` | **OPC 40100 (Machine Vision)** mit vollem Job-Ablauf, implementiert im Paket `src/vision_server/` |
| `VisionProgram` | **OPC UA Teil 10**, generische Bedienoberfläche auf demselben `JobRunner` — der Einstieg für das Frontend |

Der Endpoint-Pfad heißt weiterhin `/raspi/server/`, obwohl das namensgebende
`RaspiDevice` entfernt ist: er steht in mDNS-Ankündigung, LDS-Registrierung und
jeder Client-Konfiguration.

Das Vision-System ist die Umsetzung von Phase 2 und 3 aus Teil 9 des Plans.
Neben dem `hello-world`-Platzhalterrezept (nur zu Testzwecken, `moduleId`
immer `HELLO-WORLD`) gibt es inzwischen eine echte Erkennung: das Rezept
`apriltag` liefert reale AprilTag-Posen samt Welttag-Referenz und
Hand-Auge-Ankerung (siehe [`apriltag-referenz.md`](apriltag-referenz.md)),
und `calibration` ist eine echte Bereitschaftsprüfung der Kamerakalibrierung.
Details zu allen drei Rezepten und zur Part-2-Anlagensicht (AMCM) stehen in
[`vision-server-interface.md`](vision-server-interface.md).

Das Vision-Paket ist als **Einbau** gebaut (`install_vision_machine(server, config)`)
und hängt seine Knoten in einen bestehenden Server. Ein zweiter Serverprozess wäre
Verschwendung: er würde den 40100-Adressraum ein zweites Mal laden (gemessen
~110 MB RSS pro Prozess) und das Backend zu zwei Sessions zwingen. Für Entwicklung
und isolierte Tests lässt sich das Paket zusätzlich standalone starten
(`python -m vision_server`), das ist aber nicht der Produktionsweg.

## Dateien

| Datei | Inhalt |
| --- | --- |
| [`src/vision_server/`](../../src/vision_server/) | Vision-Server (Paket, siehe Modultabelle unten) |
| [`src/vision_server/tools/hello_world_client.py`](../../src/vision_server/tools/hello_world_client.py) | Testclient: Referenzimplementierung des Handshakes |
| [`src/vision_server/server.py`](../../src/vision_server/server.py) | Server der Zelle: Identität des Pi, mDNS/LDS, Einbau des Vision-Systems |
| [`src/vision_server/nodesets/Opc.Ua.MachineVision.NodeSet2.xml`](../../src/vision_server/nodesets/Opc.Ua.MachineVision.NodeSet2.xml) | vendorierter offizieller OPC 40100-Nodeset; von **beiden** Servern geladen |
| [`requirements.txt`](../../requirements.txt) | u. a. `asyncua` |

## Aufbau des Vision-Servers

| Modul | Aufgabe |
| --- | --- |
| `__main__.py` | argparse, Konfiguration, Start |
| `server.py` | Server der Zelle: Identität des Pi, mDNS/LDS, Einbau von Vision-System + Part-10-Programm |
| `config.py` | `VisionServerConfig` (frozen dataclass), Rezept-Profile |
| `nodeset_ids.py` | NodeId-Konstanten der Nodesets |
| `address_space.py` | Nodeset-Import (MachineVision, DI, Machinery, AMCM), `VisionMachine`-Instanz, `HasNotifier` |
| `asset_model.py` | Part-2-Anlagensicht (AMCM), `VisionAsset` samt Zustandsblock (`DeviceHealth`, siehe `camera_health.py`) |
| `state_machine.py` | Bindung beider 40100-Zustandsautomaten |
| `events.py` | Event-Generatoren, `ResultReadyEvent` mit Payload |
| `result_management.py` | Ergebnisknoten + JSON-Spiegelknoten |
| `payload.py` | JSON-Schema `wsc.vision.detections/1` |
| `errors.py` | `VisionJobError`, Fehlercodes für den `Error`-Ausgang |
| `job.py` | Validierung, State-Guard, Job-Ablauf, Fehlerpfad |
| `profiles.py` | `AprilTagProfileConfig` u. a. — Kamera, Hand-Auge-Pfad, Tag-Map je Pi |
| `runner.py` | `install_vision_machine()` (Einbau) und `run()` (standalone) |
| `vision_program.py` | Part-10-Programmfassade `VisionProgram`, siehe [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) |
| `camera.py` | `SharedCamera` — ein Capture-Loop, geteilt von Erkennung und Livestream |
| `camera_stream.py` | schreibt Kamera-Frames als Base64-JPEG in `LatestCameraFrame` |
| `camera_health.py` | Kamerazustand als `DeviceHealth` (OPC 40100-2) |
| `stream_overlay.py` | markiert erkannte Tags im Livestream-Bild |
| `calibration_session.py` | `CalibrationSession` — interaktive Kamerakalibrierung über OPC UA |
| `discovery/mdns.py` | mDNS-Ankündigung |
| `discovery/lds.py` | LDS-Registrierung beim Aggregation-Server |
| `detection/` | Strategie `DetectionSource`: `hello_world.py` (Platzhalter), `apriltag.py` (echte AprilTag-Erkennung), `script_runner.py` (Kalibrierprüfung, Rezept `calibration`) |

Echte Bilderkennung anschließen = eine neue Datei in `detection/` plus ein
Registry-Eintrag; der Server-Kern kennt keine Bildverarbeitung. Details zu
allen Modulen und der vollständigen Schnittstelle stehen in
[`vision-server-interface.md`](vision-server-interface.md) Abschnitt 1.

## Adressraum (Port 4840)

```
Objects/
├── Machines/                            ns=<machinery>;i=1001  (Standardordner Machinery)
│   └── VisionMachine                    ns=<vision>;s=VisionMachine  (Typ: <mv>:VisionSystemType)
│       ├── VisionStateMachine           Preoperational | Halted | Error | Operational
│       │   └── AutomaticModeStateMachine  Initialized | Ready | SingleExecution | ContinuousExecution
│       │       └── StartSingleJob       verlinkt, siehe vision-server-interface.md 7.5
│       ├── ResultManagement
│       │   └── Results/LatestResult     (Typ: <mv>:ResultType, wird pro Job überschrieben)
│       │       └── ResultContent[0]     String  <- JSON-Payload
│       └── LatestResultJson             String  <- dasselbe JSON, einfacher Knoten
└── VisionProgram                        ns=<vision>;s=VisionProgram  (Typ: ProgramStateMachineType)
    ├── ParameterSet/                    RecipeId, Continuous  (beschreibbar)
    └── ResultSet/                       JobId, ErrorCode, ExecutionMode
                                         + Verweise auf LatestResultJson u. a.
```

Gekürzt dargestellt — die vollständige, aktuelle Baumdarstellung inklusive
Part-2-Anlagensicht (`VisionAsset` unter AMCM), der vier Kalibriermethoden
(`StartCalibration` u. a.), `LatestCameraFrame` und `CameraStreamMode` steht in
[`vision-server-interface.md`](vision-server-interface.md) Abschnitt 1.

`VisionMachine` liegt im Machinery-Standardordner `Machines` statt direkt unter
`Objects`. Grund ist nicht Technik, sondern Lesbarkeit: wer den Server browst,
soll **einen** Einstieg sehen. Für Clients ändert das nichts — die String-NodeId
hängt nicht an der Browse-Position. Ohne geladenes Machinery-Nodeset (also ohne
`config.assets`) fällt der Aufbau auf `Objects` zurück.

Der frühere raspi-Namespace ist mit `RaspiDevice` entfallen, gleichzeitig kamen
mit Part 2 (AMCM) drei weitere Nodesets dazu (DI, Machinery, AMCM). Die
Namespace-Indizes haben sich dadurch mehrfach verschoben — aktuell (Import-
Reihenfolge MachineVision → DI → Machinery → AMCM → eigener Vision-Namespace):
ns=2 MachineVision, ns=4 Machinery, ns=6 der eigene Vision-Namespace, siehe
[`vision-server-interface.md`](vision-server-interface.md) Abschnitt 1. Wer sie
über `get_namespace_index(uri)` auflöst, merkt von Verschiebungen nichts.

Die `VisionMachine`-Instanz bekommt eine **String-NodeId** (`ns=<vision>;s=VisionMachine`),
wodurch `instantiate()` auch alle Kinder mit sprechenden, stabilen NodeIds anlegt
(z. B. `…;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob`).
Das erspart dem Backend jede Discovery.

Die States der äußeren `VisionStateMachine` sind Mandatory-Kinder der Instanz und
werden per BrowseName geholt. Die States der `AutomaticModeStateMachine`
(`Initialized`/`Ready`/`SingleExecution`/`ContinuousExecution`) sind **keine**
Instanzkinder — sie existieren nur als feste Knoten am Typ
`VisionAutomaticModeStateMachineType` (i=5056–5059) und werden per NodeId geholt.

## Laufzeitverhalten

- Endpoint `opc.tcp://<LAN-IPv4>:4840/raspi/server/`, gelauscht wird auf
  `0.0.0.0:4840` (`Server.socket_address`). Die Trennung ist nötig, weil der
  Endpoint als DiscoveryUrl beim Discovery-Server landet und `0.0.0.0` dort
  wertlos wäre; über `127.0.0.1` bleibt der Server trotzdem erreichbar. Ist
  keine LAN-IPv4 zu ermitteln, bleibt es bei `opc.tcp://0.0.0.0:4840/…` und der
  Server läuft ohne LDS-Anmeldung. `SecurityPolicy: NoSecurity`.
- Beim Start zwei Bekanntmachungen nebeneinander, beide werden beim geordneten
  Beenden zurückgezogen:
  - **mDNS** (`src/vision_server/discovery/mdns.py`): `_opcua-tcp._tcp.local.`, Instanzname ist die
    Vision-Identität. Für Clients im Subnetz. **Seit 23.09.2026
    standardmäßig aus**; einschalten mit `OPCUA_MDNS=1`.
  - **LDS-Anmeldung** (`src/vision_server/discovery/lds.py` über `Server.register_to_discovery()`):
    **seit 23.09.2026 standardmäßig aus** — der Server erscheint dann nicht im
    Aggregation-Server. Einschalten mit
    `OPCUA_LDS_URL=opc.tcp://10.10.38.27:4840/`, dann alle 60 s erneuert.
    **Nur darüber** nimmt der Aggregation-Server der Zelle das Modul auf —
    mDNS allein genügt ihm nicht, siehe [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) §2.
  Scheitert eine der beiden, läuft der Server weiter und loggt eine Warnung.
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

# Server der Zelle (Vision-System + Part-10-Programm + mDNS/LDS)
PYTHONPATH=src python3 -m vision_server.server

# Handshake einmal durchspielen
PYTHONPATH=src python3 src/vision_server/tools/hello_world_client.py \
    --url opc.tcp://127.0.0.1:4840/raspi/server/

# Nur das Vision-System, isoliert (Entwicklung)
PYTHONPATH=src python3 -m vision_server --port 4841 --log-level INFO
```

Zwei Umschalter verändern den Adressraum bzw. das Verhalten:

| Variable/Option | Wirkung |
| --- | --- |
| `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` | AprilTag-Erkennung läuft auch ohne echte Kamerakalibrierung (Posen dann nicht maßstabsgetreu) — bewusster, temporärer Bypass zum Testen von Erkennung/Overlay/Job-Pfad vor der echten Kalibrierung, nie im Standard aktiv, siehe [`apriltag-referenz.md`](apriltag-referenz.md) |
| `VisionServerConfig.assets` (`config.assets`) | `None` = Part 2 (AMCM) nicht geladen, spart ~13 MB RSS/~1,6 s Startzeit; gesetzt lädt der Server zusätzlich DI/Machinery/AMCM-Nodesets und legt `VisionAsset` an |

### Zwei Wege auf denselben Server

| Weg | Wofür |
| --- | --- |
| `python3 -m vision_server.server` | Entwicklung und Handarbeit. Braucht `PYTHONPATH=src` oder `WorkingDirectory=…/src`. |
| `python3 src/OPCUA/server.py` | **Die systemd-Unit auf den Pis.** Ein 15-Zeilen-Starter ohne Logik, der `src/` auf den Pfad legt und `vision_server.server.main()` aufruft. |

`src/OPCUA/server.py` existiert **nur** deshalb: die Unit zeigt seit jeher auf
diesen Pfad, sie ist in keinem Repo versioniert (Altlast D5), und sie auf zwei
Pis von Hand nachzuziehen wäre eine Fehlerquelle bei jedem Neuaufsetzen. Sie
kann wegfallen, sobald die Unit versioniert ist und mit ausgerollt wird.

Beim Umzug ins Paket war diese Datei kurzzeitig gelöscht — der Dienst startete
nach einem Reboot nicht mehr. Der Starter ist die Lehre daraus: **an einem
Einstiegspunkt, den ein nicht versioniertes Deployment festhält, wird nicht
ohne Ersatz gezogen.**

Auf dem Pi läuft der Server produktiv als systemd-Service `opcua-server.service`
(`Restart=always`, `RestartSec=5`, `/etc/systemd/system/opcua-server.service` —
nicht in diesem Repo versioniert). **Wichtig beim Testen auf dem Pi:** Kein
Vordergrundstart, das kollidiert mit dem Service (Port 4840 belegt) —
stattdessen `systemctl restart opcua-server.service` und mit einem separaten
Client dagegen testen.

**Kamera-Watchdog** (`camera.py`): `capture_array()` kann ohne Fehlermeldung
ewig haengen, wenn libcamera keinen Frame mehr liefert (Pi 1, 2026-09-22: der
Livestream zeigte ein eingefrorenes Bild, Jobs scheiterten mit „Kein Kamerabild
innerhalb von 5.0 s“). Jede Aufnahme hat deshalb `frame_timeout_s` (3 s); bei
einem Haenger wird die Kamera neu geoeffnet, nach `max_reopen_attempts` (2)
erfolglosen Neu-Oeffnungen beendet sich der Prozess hart und systemd startet
ihn neu.

**Der Watchdog überwacht ausschließlich die Aufnahme, nicht den Livestream.**
Nach außen meldet er sich über OPC 40100-2: `DeviceHealth` sagt, wie es der
Kamera geht, und jeder Zustandswechsel feuert zusätzlich den passenden
DI-Alarm (`camera_health.py`). Der Livestream ist reiner Publisher — er
veröffentlicht auch ein veraltetes Bild weiter. Bei hängender Kamera steht
das Livebild deshalb still; **dass** es steht, sagt die Ampel im Frontend und
nicht das fehlende Bild. Vollständig in
[`vision-server-interface.md`](vision-server-interface.md) Abschnitt 11.4.

Im Journal danach suchen mit
`journalctl -u opcua-server.service | grep -E "haengt|neu geoeffnet|Kamerazustand|beende den Prozess"`.
Häufen sich die Meldungen, ist die Ursache meist Hardware (Flachbandkabel).

## Verifizierter Stand

Lokal gegen asyncua 2.0.1 Ende-zu-Ende durchgelaufen, im
Produktionszuschnitt (`src/vision_server/server.py` auf 4840) und mit separatem Client:
Happy Path inkl. Payload und Event-Reihenfolge, `BUSY` bei zwei gleichzeitigen
Aufrufen, `INVALID_ARGUMENT` bei überlanger `MeasId`, `UNKNOWN_RECIPE` bei
unbekanntem Rezept, Fehlerpfad über den `Error`-Zustand mit anschließender
Wiederaufnahme.

Nach dem Abbau der CPU-Temperatur-Demo (Altlasten A1–A6) zusätzlich geprüft:
Der Adressraum enthält genau **eine** `VisionSystemType`-Instanz, sie hängt unter
`Objects/Machines`, und der Namensraum `http://launch-rm.de/raspi` ist fort.
Festgehalten in `tests/test_part10_fassade.py`.

## Bekannte Einschränkungen

- Nur das Rezept `hello-world` ist ein Platzhalter: `moduleId` ist `HELLO-WORLD`,
  die Pose immer Null. Das Rezept `apriltag` liefert echte Posen, `calibration`
  eine echte Bereitschaftsprüfung.
- `frameId` folgt der Erkennungsquelle: `world`, sobald ein Referenz-Tag aus der
  Tag-Map sichtbar ist, sonst das Kamera-KS. Die Hand-Auge-Kalibrierung für
  Layer 2 ist implementiert (`AprilTagProfileConfig.hand_eye_path`, verkettet
  in `detection/apriltag.py`) — offen ist nur noch die reale Vermessung der
  Hand-Auge-Datei je Zelle, siehe [`vision-server-interface.md`](vision-server-interface.md)
  Abschnitt 9.
- Verlinkt sind `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`
  und `Reset`. `SimulationMode` und `SelectModeAutomatic` existieren im
  Adressraum, tun aber bewusst nichts — Begründung je Methode in
  [`vision-server-interface.md`](vision-server-interface.md) Abschnitt 7.5.
- Die `ResultManagement`-Methoden (`GetResultById` & Co.) sind nicht
  implementiert und vom asyncua-Client aus auch nicht aufrufbar — sie erwarten
  Struktur-ExtensionObjects als Eingabe (asyncua-Issue #1693).
- Die strukturtypisierten Event-Felder (`ResultId`, `JobId`, …) werden leer
  gesendet; ihre Werte stehen im JSON-Payload.
- Events erreichen nur ein Abo direkt auf `VisionMachine`. Die
  `HasNotifier`-Referenz ist gesetzt, bewirkt bei asyncua serverseitig aber kein
  Event-Bubbling.
- Nur eine Vision-Instanz — ein zweites Profil (3D) existiert noch nicht.
- Events erreichen **nur** ein Abo auf dem emittierenden Knoten. Ein reiner
  Part-10-Client, der nur `VisionProgram` abonniert, sieht die 40100-Events
  deshalb nicht; er holt sein Ergebnis über die Wertänderung von
  `VisionProgram/ResultSet/LatestResultJson`. Am 21.09.2026 gegen asyncua 2.0.1
  nachgemessen — auch eine `HasEventSource`-Referenz ändert daran nichts.
- Die Deckenkamera (Pi 1) nimmt mit voller Sensorauflösung 4056×3040 auf;
  Livestream und Overlay laufen auf einem zweiten, vom ISP skalierten Strom
  (960×720), siehe [`vision-server-interface.md`](vision-server-interface.md)
  Abschnitt 10.3. Eine Kalibrierung für 2028×1520 wird bis zur Neukalibrierung
  hochgerechnet (`allow_resolution_mismatch` im Preset `cam_ceiling`) — nach
  der Neukalibrierung bei 4056×3040 den Schalter wieder entfernen.
