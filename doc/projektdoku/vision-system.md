# Vision-System — Ist-Stand

Diese Datei beschreibt, was im Repo **tatsächlich implementiert** ist. Backend-Schnittstelle:
[`vision-server-interface.md`](vision-server-interface.md). Zielbild und WSC-Anbindung:
[`vision-system-integration.md`](vision-system-integration.md).

## Zweck

**Ein** OPC-UA-Server auf dem Raspberry Pi (`src/vision_server/server.py`, Port 4840,
Endpoint `opc.tcp://<pi>:4840/raspi/server/`) mit **zwei Sichten auf denselben Job**:

| Knoten | Inhalt |
| --- | --- |
| `Machines/VisionMachine` | **OPC 40100 (Machine Vision)** mit vollem Job-Ablauf, Paket `src/vision_server/` |
| `VisionProgram` | **OPC UA Teil 10**, generische Bedienoberfläche auf demselben `JobRunner`, Einstieg für das Frontend |

Der Endpoint-Pfad `/raspi/server/` bleibt, obwohl das namensgebende `RaspiDevice` entfernt ist:
er steht in mDNS, LDS und jeder Client-Konfiguration.

Erkennung: Rezept `apriltag` liefert AprilTag-Posen samt Welttag-Referenz und Hand-Auge-Ankerung
([`apriltag-referenz.md`](apriltag-referenz.md)); `calibration` ist eine Bereitschaftsprüfung der
Kamerakalibrierung. Ein Start ohne `RecipeId` ist ein Fehler (`UNKNOWN_RECIPE`), es gibt keinen Standardjob.

Das Vision-Paket ist ein **Einbau** (`install_vision_machine(server, config)`) in den bestehenden Server.
Ein zweiter Serverprozess ist nicht vorgesehen: er lüde den 40100-Adressraum doppelt (~110 MB RSS je Prozess)
und zwänge das Backend zu zwei Sessions. `python -m vision_server` ist nur für Entwicklung und isolierte Tests.

## Dateien

Testclient (Referenz für den Handshake): `src/vision_server/tools/handshake_client.py`.
Nodeset `src/vision_server/nodesets/Opc.Ua.MachineVision.NodeSet2.xml` (vendored, von beiden Servern geladen).
Abhängigkeiten: [`requirements.txt`](../../requirements.txt). Deployment (systemd): [`deploy/`](../../deploy/README.md).

## Aufbau des Vision-Servers

| Modul | Aufgabe |
| --- | --- |
| `__main__.py` | argparse, Konfiguration, Start |
| `server.py` | Server der Zelle: Identität des Pi, mDNS/LDS, Einbau von Vision-System und Part-10-Programm |
| `config.py` | `VisionServerConfig` (frozen dataclass), Rezept-Profile |
| `nodeset_ids.py` | NodeId-Konstanten der Nodesets |
| `address_space.py` | Nodeset-Import (MachineVision, DI, Machinery, AMCM), `VisionMachine`-Instanz, `HasNotifier` |
| `asset_model.py` | Part-2-Anlagensicht (AMCM), `VisionAsset` mit `DeviceHealth` (`camera_health.py`) |
| `state_machine.py` | Bindung beider 40100-Zustandsautomaten |
| `events.py` | Event-Generatoren, `ResultReadyEvent` mit Payload |
| `result_management.py` | Ergebnisknoten und JSON-Spiegelknoten |
| `payload.py` | JSON-Schema `wsc.vision.detections/1` |
| `errors.py` | `VisionJobError`, Fehlercodes für den `Error`-Ausgang |
| `job.py` | Validierung, State-Guard, Job-Ablauf, Fehlerpfad |
| `profiles.py` | `AprilTagProfileConfig` (Kamera, Hand-Auge-Pfad, Tag-Map je Pi) |
| `errors.py` | `VisionJobError`, Fehlercodes für den `Error`-Ausgang |
| `runner/` | `install.py` mit `install_vision_machine()` (Einbau), `standalone.py` mit `run()` (eigenständiger Start); Details [`code/zelle-vision-server.md`](code/zelle-vision-server.md) |
| `vision_program.py` | Part-10-Programmfassade `VisionProgram` auf `src/ua_program/program.py`, siehe [`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) |
| `camera.py` | `SharedCamera`: ein Capture-Loop, geteilt von Erkennung und Livestream |
| `camera_stream.py` | schreibt Kamera-Frames als Base64-JPEG in `LatestCameraFrame` |
| `stream_overlay.py` | markiert erkannte Tags im Livestream-Bild |
| `calibration_session.py` | `CalibrationSession`: interaktive Kamerakalibrierung über OPC UA |
| `discovery/mdns.py`, `discovery/lds.py` | mDNS-Ankündigung, LDS-Registrierung beim Aggregation-Server |
| `detection/` | Strategie `DetectionSource`: `apriltag/` (echte Erkennung), `script_runner.py` (Rezept `calibration`) |

Eine neue Erkennungsquelle ist eine Datei unter `detection/` plus Registry-Eintrag (Lazy-Import),
eine Zeile in `DEFAULT_RECIPE_PROFILES` und ggf. ein Profil je Pi. Der Server-Kern (`job.py`,
`payload.py`, `events.py`, `result_management.py`, `state_machine.py`, `address_space.py`) bleibt unverändert.

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
    └── ResultSet/                       JobId, ErrorCode, ExecutionMode + Verweise auf LatestResultJson
```

Gekürzt. Vollständig (Kalibriermethoden, `LatestCameraFrame`, `CameraStreamMode`): [`vision-server-interface.md`](vision-server-interface.md) Abschnitt 1 und 13.1.

- Namespaces: `ns=2` MachineVision, `ns=4` Machinery, `ns=6` eigener Namespace `http://launch-rm.de/vision`.
  Index immer über `get_namespace_index(uri)` auflösen. Der Namespace `http://launch-rm.de/raspi` ist fort (mit `RaspiDevice`).
- Die Kalibriermethoden `StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration` und `AbortCalibration`
  hängen am Vision-System; Ablauf und Signaturen: [`vision-server-interface.md`](vision-server-interface.md) Abschnitt 12.
- Die Instanz hat die String-NodeId `ns=<vision>;s=VisionMachine`. Damit legt `instantiate()` die Kinder mit stabilen
  NodeIds an (z. B. `…;s=VisionMachine.VisionStateMachine.AutomaticModeStateMachine.StartSingleJob`); Discovery ist nicht nötig.
- Die States der äußeren `VisionStateMachine` sind Mandatory-Kinder der Instanz. Die States der
  `AutomaticModeStateMachine` hängen am Typ `VisionAutomaticModeStateMachineType` (i=5056–5059) und werden per NodeId geholt.
- Genau **eine** `VisionSystemType`-Instanz, unter `Objects/Machines`. Ohne geladenes Machinery-Nodeset (ohne `config.assets`) liegt sie unter `Objects`.

## Laufzeitverhalten

- Endpoint `opc.tcp://<LAN-IPv4>:4840/raspi/server/`; gelauscht wird auf `0.0.0.0:4840` (`Server.socket_address`).
  Die Trennung ist nötig, weil der Endpoint als DiscoveryUrl beim Discovery-Server landet und `0.0.0.0` dort wertlos wäre.
  Ist keine LAN-IPv4 zu ermitteln, bleibt es bei `opc.tcp://0.0.0.0:4840/…` und der Server läuft ohne LDS-Anmeldung.
  `SecurityPolicy: NoSecurity`.
- Zwei Bekanntmachungen beim Start, beide werden beim geordneten Beenden zurückgezogen. Scheitert eine, läuft der Server weiter.
  - **mDNS** (`_opcua-tcp._tcp.local.`, Instanzname = Vision-Identität): standardmäßig an, abschalten mit `OPCUA_MDNS=0`.
  - **LDS** (`Server.register_to_discovery()`): standardmäßig an, meldet sich bei `opc.tcp://10.10.38.27:4840/` an und
    erneuert alle 60 s. Abschalten mit `OPCUA_LDS_URL=""`, umbiegen mit einer anderen URL. **Nur darüber** nimmt der
    Aggregation-Server das Modul auf ([`part10-programm-schnittstelle.md`](part10-programm-schnittstelle.md) §2).
- Start des Vision-Systems: `Preoperational → Operational`, innen `Initialized → Ready`.
- `StartSingleJob` prüft **synchron** Zustand und Eingaben und quittiert mit `(JobId, Error)`. Der Job läuft asynchron.
- Jobfolge `Ready → SingleExecution → Ready`. Events `StateChangedEvent`, `JobStartedEvent`, `AcquisitionDoneEvent`,
  `ResultReadyEvent`, `ReadyEvent`, alle vom `VisionMachine`-Knoten.
- Das Ergebnis-JSON reist **im `ResultReadyEvent`** (`ResultContent[0]`) und steht zusätzlich in den Ergebnisknoten. Kein Polling.
- Nur **ein** Job gleichzeitig; ein zweiter Aufruf wird mit `BUSY` abgelehnt.
- Fehler in der Erkennung: Rückkehr über `Error` nach `Operational`, gemeldet als `ResultReadyEvent` mit `resultState != 0`.
- Job-Timeout 20 s (`config.job_timeout`). Bei Überschreitung `DETECTION_FAILED`, der Automat kehrt nach `Ready` zurück.
  Die AprilTag-Quelle hat zusätzlich `capture_timeout_s`, damit ein hängender Worker nicht den nächsten Job blockiert.
- Erkennungsquellen öffnen und schließen über `open()`/`close()`. Eine Quelle, die nicht öffnet, hält den Automaten in
  `Preoperational`, statt jeden Job mit `DETECTION_FAILED` zu beantworten.
- OpenCV-Arbeit gehört in `run_blocking()` der Basisklasse, sonst friert der gemeinsame Event-Loop ein.

## Starten

```bash
pip install -r requirements.txt

# Server der Zelle (Vision-System + Part-10-Programm + mDNS/LDS)
PYTHONPATH=src python3 -m vision_server.server

# Handshake einmal durchspielen
PYTHONPATH=src python3 src/vision_server/tools/handshake_client.py \
    --url opc.tcp://127.0.0.1:4840/raspi/server/ --recipe-id calibration

# Nur das Vision-System, isoliert (Entwicklung)
PYTHONPATH=src python3 -m vision_server --port 4841 --log-level INFO
```

| Variable/Option | Wirkung |
| --- | --- |
| `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` | AprilTag-Erkennung auch ohne echte Kamerakalibrierung; Posen nicht maßstabsgetreu. Nur zum Testen, nie im Standard ([`apriltag-referenz.md`](apriltag-referenz.md)) |
| `VisionServerConfig.assets` | `None` = Part 2 (AMCM) nicht geladen (spart ~13 MB RSS, ~1,6 s Start). Gesetzt: DI/Machinery/AMCM-Nodesets und `VisionAsset` |

Auf dem Pi läuft der Server als systemd-Service `opcua-server.service` (`Restart=always`, `RestartSec=5`,
`/etc/systemd/system/opcua-server.service`, Vorlage in `deploy/`). Die Unit startet `<Repo>/.venv/bin/python -m vision_server.server` mit `PYTHONPATH=<Repo>/src`.
Entwicklung und Handarbeit: `python3 -m vision_server.server` mit `PYTHONPATH=src` oder `WorkingDirectory=…/src`. **Beim Testen auf dem Pi** kein Vordergrundstart (Port 4840 ist belegt):
`systemctl restart opcua-server.service` und ein separater Client.

### Kamera-Watchdog

`capture_array()` kann ohne Fehler hängen. Jede Aufnahme hat deshalb `frame_timeout_s` (3 s); bei einem Hänger wird die Kamera
neu geöffnet. Nach `max_reopen_attempts` (2) beendet sich der Prozess hart und systemd startet ihn neu (`camera.py`).
Der Watchdog überwacht **nur die Aufnahme**. Er meldet sich über `DeviceHealth` (OPC 40100-2) und DI-Alarme (`camera_health.py`);
der Livestream zeigt auch ein veraltetes Bild weiter. Details: [`vision-server-interface.md`](vision-server-interface.md) Abschnitt 11.4.

```bash
journalctl -u opcua-server.service | grep -E "haengt|neu geoeffnet|Kamerazustand|beende den Prozess"
```

Häufen sich die Meldungen, ist meist die Hardware (Flachbandkabel) die Ursache.

## Getestete Fälle

Ende-zu-Ende gegen asyncua 2.0.1, im Produktionszuschnitt auf Port 4840, mit separatem Client:

- Happy Path: Payload, Event-Reihenfolge, Korrelation über `jobId`, `ResultState`/`IsPartial`/`IsSimulated`/`CreationTime`
  im Event, beide Ergebnisknoten.
- `BUSY` bei zwei gleichzeitigen Aufrufen; `INVALID_ARGUMENT` bei überlanger `MeasId`; `UNKNOWN_RECIPE` bei unbekanntem Rezept.
- Fehlerpfad `--parameter force-error`: `resultState=5`, Durchlauf über `Error`, danach wieder aufnahmebereit.
- Genau eine `VisionSystemType`-Instanz, kein `raspi`-Namespace: `tests/test_part10_fassade.py`.
- Testsuite: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

## asyncua-Fallstricke im Server-Code

Gegen Nodeset und asyncua-Quellcode geprüft. Gelten für jede Änderung am Server. Die Event-Zustellung
(nur am emittierenden Knoten) ist in [`vision-server-interface.md`](vision-server-interface.md) §7 beschrieben.

- `ResultReadyEventType` (i=1024) hat keine `Result`-Struktur, sondern flache Felder. Das Payload reist in `ResultContent[0]`.
- `ResultContent` hat eine Null-NodeId als DataType; jeder Write scheitert mit `BadTypeMismatch`. Lösung: DataType auf `String`
  überschreiben, dann Array-Write `ua.Variant([payload], ua.VariantType.String)`.
- Event-Felder brauchen fertige `ua.Variant(wert, typ)`-Objekte; ein roher Python-Wert kommt beim Client als `None` an.
- `@uamethod`-Handler, die einen Task starten, müssen `async` sein (synchrone laufen ohne Event-Loop). Die Prüflogik bleibt synchron.
- Methodenrückgaben als **Tupel**; eine `list` wird als *ein* Variant verpackt.
- `change_state()` ohne `Transition`-Objekt: der `LastTransition`-Knoten existiert auf der Instanz nicht.
- Übergänge: kein `Halted → Operational`. Korrekt `Preoperational → Operational` (`PreoperationalToOperationalAuto`), innen `Initialized → Ready`.
  Der `Error`-Zustand ist Mandatory-Kind der Instanz, nicht `i=5030` (der Typknoten).

## Bekannte Einschränkungen

- Nur eine Vision-Instanz; das 3D-Profil fehlt ([`vision-system-next-steps.md`](vision-system-next-steps.md)).
- Verlinkt sind `StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`, `Reset`. `SimulationMode` und
  `SelectModeAutomatic` existieren, haben bewusst keine Wirkung ([`vision-server-interface.md`](vision-server-interface.md) 7.5).
- `ResultManagement`-Methoden (`GetResultById` & Co.) sind nicht implementiert und vom asyncua-Client aus nicht aufrufbar
  (asyncua-Issue #1693). Ergebnisse kommen über `ResultReadyEvent` und `LatestResultJson`.
- Strukturtypisierte Event-Felder (`ResultId`, `JobId`, …) werden leer gesendet; die Werte stehen im JSON.
- Die `HasNotifier`-Referenz des Server-Objekts auf `VisionMachine` ist gesetzt, bewirkt bei asyncua aber kein
  Event-Bubbling. Auch eine `HasEventSource`-Referenz ändert daran nichts (gemessen gegen asyncua 2.0.1).
- Ein reiner Part-10-Client, der nur `VisionProgram` abonniert, sieht die 40100-Events nicht; er holt das Ergebnis über
  die Wertänderung von `VisionProgram/ResultSet/LatestResultJson`.
- Deckenkamera: Aufnahme mit 4056×3040; Livestream und Overlay laufen auf einem zweiten Strom (960×720), siehe
  [`vision-server-interface.md`](vision-server-interface.md) Abschnitt 10.3.

## Kamera-Controls (Picamera2)

Libcamera-Controls der Deckenkamera setzt man ohne Codeänderung: Feld `CameraStreamConfig.camera_controls`,
Preset `PI_CAMERA_STREAM_PRESETS["cam_ceiling"]["camera_controls"]` (vorerst leer) und Umgebungsvariable
`VISION_CAMERA_CONTROLS` (JSON-Objekt; je Schlüssel gewinnt die Variable, `null` entfernt einen Preset-Eintrag):

```
VISION_CAMERA_CONTROLS='{"ExposureValue": -0.5, "Sharpness": 1.5, "NoiseReductionMode": "Fast"}'
```

- Die Controls gehen als `controls=` in die Video-Konfiguration und werden mit den Picamera2-Standards
  (`FrameDurationLimits`, `NoiseReductionMode`) **gemischt**, nicht ersetzt.
- Typen: `ExposureTime` in µs (int), `AnalogueGain`, `ExposureValue`, `Sharpness`, `Contrast`, `Brightness`, `Saturation` (float), `AeEnable` (bool).
  Enum-Controls (`NoiseReductionMode`, `AeMeteringMode`, `AeExposureMode`, `AeConstraintMode`, `AwbMode`) als Name, z. B. `"HighQuality"`, `"Off"`, `"Fast"`.
- Unbekannter Name (`Picamera2.camera_controls`) oder Enum-Name wirft beim Öffnen `ValueError`; die Meldung listet die gültigen Namen.
  RealSense und OpenCV ignorieren das Feld (einmalige Warnung je Öffnen).
- Nach dem Warmup loggt der Server `ExposureTime`, `AnalogueGain`, `DigitalGain`, `Lux`, `ColourTemperature`, `FocusFoM`
  (`journalctl … | grep "Kamera-Metadaten"`).
- Hardware: M12-Objektiv mit fester Blende; einstellbar nur der Fokus von Hand und die ISP-/Belichtungs-Controls.
- Setzen auf dem Pi: `sudo systemctl edit opcua-server.service` mit `Environment='VISION_CAMERA_CONTROLS=...'`, dann `systemctl restart`.
  Arbeitsplan: [`arbeitsplaene/kamera-controls-und-detektor-parameter.md`](arbeitsplaene/kamera-controls-und-detektor-parameter.md).
