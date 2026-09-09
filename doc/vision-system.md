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
└── VisionSystem                      (Typ: 1:VisionSystemType aus dem MV-Nodeset)
    └── ResultManagement
        └── Results
            └── CpuTemperatureResult  (Typ: 1:ResultType)
                └── ResultContent     Double   ← Platzhalter für ein echtes Ergebnis
```

`VisionSystem` wird per `instantiate()` unter `ResultManagement/Results` als
Instanz von `ResultType` angelegt; `ResultContent` bekommt explizit den DataType
`Double` gesetzt, weil das Attribut im Nodeset als `BaseDataType` deklariert ist.

## Laufzeitverhalten

- Endpoint: `opc.tcp://0.0.0.0:4840/raspi/server/`
- `SecurityPolicy: NoSecurity` — keine Authentifizierung/Verschlüsselung.
- Alle 1 s: `Counter` hochzählen, `CpuTemperature` und `ResultContent` mit dem
  aktuellen CPU-Temperaturwert (`/sys/class/thermal/thermal_zone0/temp`) füllen.
- `ResultContent` trägt aktuell **die CPU-Temperatur, keine echte Vision-Erkennung**
  — es demonstriert nur, dass der Ergebnis-Knoten beschreibbar und über OPC UA
  lesbar ist.

## Starten

```bash
pip install -r requirements.txt
python src/OPCUA/server.py
```

Der Server startet zusätzlich **automatisch beim Booten des Raspberry Pi**
(außerhalb dieses Repos konfiguriert, z. B. systemd/cron auf dem Gerät selbst —
nicht als Datei im Repo versioniert).

## Bezug zum Gesamtplan

Das entspricht **Phase 1** ("Spike") aus Teil 9 von
[`vision-system-integration.md`](vision-system-integration.md#teil-9--reihenfolge):
Nodeset vendoren, `init()` → `import_xml()` → ein Machine-Vision-Typ instanziieren
— und damit die beiden dort genannten `asyncua`-Risiken (Issues #651/#1693)
praktisch ausschließen. Details siehe
[`vision-system-next-steps.md`](vision-system-next-steps.md).

## Bekannte Einschränkungen

- Kein `VisionStateMachine` / `AutomaticModeStateMachine` — die Zustände aus
  OPC 40100 (`Preoperational`, `Ready`, `SingleExecution`, …) existieren nicht.
- Keine `StartSingleJob`-Methode, kein Job-Ablauf.
- Keine Events (`JobStartedEvent`, `ResultReadyEvent`, …) — Ergebnisse werden nur
  per Polling über die Variable `ResultContent` sichtbar, nicht über Events.
- `ResultContent` ist ein rohes `Double`, nicht das JSON-Payload-Schema
  (`moduleId`, `position`, `orientation`, `frameId`, …) aus Teil 4.3 des Plans.
- Nur eine Instanz — die im Plan vorgesehene Trennung in 2D-/3D-Server (Server 1/2)
  existiert noch nicht.
