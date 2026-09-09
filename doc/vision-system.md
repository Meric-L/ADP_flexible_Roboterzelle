# Vision-System — Ist-Stand (Raspberry Pi OPC-UA-Server)

Diese Datei beschreibt, was im Repo **tatsächlich implementiert** ist. Für den
vollständigen Zielplan (Integration in WebSkillComposition, JSON-Payload-Schema,
Job-Ablauf etc.) siehe [`vision-system-integration.md`](vision-system-integration.md).

## Zweck

Ein einzelner OPC-UA-Server auf dem Raspberry Pi, der zwei Dinge tut:

1. Ein einfaches eigenes Interface (`RaspiDevice`) mit CPU-Temperatur, Zähler,
   Sollwert — Ausgangspunkt des Servers, unabhängig vom Vision-Teil.
2. Eine **OPC 40100 (Machine Vision)**-Adressraum-Struktur, die als Machbarkeits-
   Nachweis dient: Der offizielle Machine-Vision-Nodeset lässt sich importieren
   und instanziieren, ohne den bekannten `asyncua`-Import-Bug auszulösen
   (siehe [Bekannte Einschränkungen](#bekannte-einschränkungen)).

## Dateien

| Datei | Inhalt |
| --- | --- |
| [`src/OPCUA/server.py`](../src/OPCUA/server.py) | Server-Implementierung (Setup + Endlosschleife) |
| [`src/OPCUA/Opc.Ua.MachineVision.NodeSet2.xml`](../src/OPCUA/Opc.Ua.MachineVision.NodeSet2.xml) | Vendorierter offizieller OPC 40100-Nodeset (Core UA 1.04, keine weiteren Abhängigkeiten) |
| [`requirements.txt`](../requirements.txt) | u. a. `asyncua` |

## Adressraum

```
Objects/
├── RaspiDevice                       (eigener ns "http://launch-rm.de/raspi")
│   ├── CpuTemperature   Double
│   ├── Counter          Int64
│   └── Setpoint         Double       (writable)
└── VisionSystem                      (Typ: 1:VisionSystemType aus dem MV-Nodeset,
    │                                   HasNotifier-Referenz vom Server-Objekt)
    ├── VisionStateMachine            (States: Preoperational/Halted/Operational/Error)
    │   └── AutomaticModeStateMachine (States: Initialized/Ready/SingleExecution/…,
    │                                   Methode StartSingleJob verlinkt)
    └── ResultManagement
        └── Results
            ├── CpuTemperatureResult  (Typ: 1:ResultType)
            │   └── ResultContent     Double   ← Platzhalter, per 1s-Polling befüllt
            └── HelloWorldResult      (Typ: 1:ResultType)
                └── ResultContent     String   ← JSON, nur bei StartSingleJob-Aufruf befüllt
```

`VisionSystem` wird per `instantiate()` unter `ResultManagement/Results` als
Instanz von `ResultType` angelegt; `ResultContent` bekommt explizit den DataType
`Double` bzw. `String` gesetzt, weil das Attribut im Nodeset als `BaseDataType`
deklariert ist. `HelloWorldResult` ist eine zusätzliche, separate Instanz für den
`StartSingleJob`-Smoke-Test (Details: [`vision-system-next-steps.md`](vision-system-next-steps.md)) —
`CpuTemperatureResult` bleibt unverändert.

## Laufzeitverhalten

- Endpoint: `opc.tcp://0.0.0.0:4840/raspi/server/`
- `SecurityPolicy: NoSecurity` — keine Authentifizierung/Verschlüsselung.
- Alle 1 s: `Counter` hochzählen, `CpuTemperature` und `ResultContent` mit dem
  aktuellen CPU-Temperaturwert (`/sys/class/thermal/thermal_zone0/temp`) füllen.
- `CpuTemperatureResult/ResultContent` trägt weiterhin **die CPU-Temperatur,
  keine echte Vision-Erkennung** — Platzhalter, per Polling befüllt.
- `StartSingleJob` (auf `AutomaticModeStateMachine`) löst zusätzlich, event-
  getrieben, einen Job aus, der `HelloWorldResult/ResultContent` mit einem
  JSON-String (`"Hello World"` + Uhrzeit) befüllt und dabei die volle
  Zustandsfolge durchläuft und drei Events feuert (Details:
  [`vision-system-next-steps.md`](vision-system-next-steps.md)).

## Starten

```bash
pip install -r requirements.txt
python src/OPCUA/server.py
```

Auf dem Raspberry Pi läuft der Server **produktiv als systemd-Service**
`opcua-server.service` (`Restart=always`, `RestartSec=5`,
`/etc/systemd/system/opcua-server.service` — nicht in diesem Repo versioniert).
**Wichtig beim Testen auf dem Pi:** Nicht einfach `python src/OPCUA/server.py`
im Vordergrund starten (Port 4840 kollidiert mit dem laufenden Service) —
stattdessen `systemctl restart opcua-server.service` verwenden, um den
laufenden Service mit dem aktuellen Code-Stand neu zu starten, und mit einem
separaten Client dagegen testen.

## Bezug zum Gesamtplan

Phase 1 ("Spike") aus Teil 9 von
[`vision-system-integration.md`](vision-system-integration.md#teil-9--reihenfolge)
ist erledigt (Nodeset vendoren, `init()` → `import_xml()` → Typ instanziieren).
Phase 2 (Adressraum + `HasNotifier`) und ein Teil von Phase 3 (State Machine,
`StartSingleJob`, Events, Smoke-Test-Payload) sind ebenfalls umgesetzt — aber
weiterhin im bestehenden `raspi`-Server statt im geplanten eigenen
`vision-server/`-Package, und mit einem simulierten statt einem echten
Ergebnis-Payload. Details und offene Punkte siehe
[`vision-system-next-steps.md`](vision-system-next-steps.md).

## Bekannte Einschränkungen

- `StartSingleJob` erzeugt aktuell nur ein simuliertes "Hello World"-Ergebnis,
  keine echte Bilderkennung.
- `ResultContent` von `HelloWorldResult` ist ein einfacher JSON-String
  (`message`, `time`), nicht das volle Detection-Payload-Schema (`moduleId`,
  `position`, `orientation`, `frameId`, …) aus Teil 4.3 des Plans.
- Nicht alle State-Machine-Methoden sind verlinkt — nur `StartSingleJob`.
  `Reset`/`Halt`/`Abort`/`SimulationMode`/`StartContinuous`/etc. existieren im
  Adressraum, haben aber keine Python-Implementierung.
- Events feuern nur bei Abonnement direkt auf `VisionSystem`, nicht bei
  Abonnement auf das generische Server-Objekt — die `HasNotifier`-Referenz
  bewirkt bei `asyncua` server-seitig kein Event-Bubbling (siehe
  [`vision-system-next-steps.md`](vision-system-next-steps.md)).
- Nur eine Instanz — die im Plan vorgesehene Trennung in 2D-/3D-Server (Server 1/2)
  existiert noch nicht.
