# OPC UA

Der OPC-UA-Server auf dem Raspberry Pi, die OPC-40100-Struktur (Machine Vision)
und die Anbindung an WebSkillComposition. Beginnt dort, wo
[`vision.md`](vision.md) endet: eine fertige Pose wird als Ergebnis-Payload
ausgeliefert.

## Kernfakten

- Der Server läuft auf dem Raspberry Pi als **systemd-Service
  `opcua-server.service`** (`Restart=always`, `RestartSec=5`, unter
  `/etc/systemd/system/`). Die Service-Datei ist **nicht in diesem Repo
  versioniert**.
- **Stolperfalle beim Testen:** Der Service belegt Port **4840**. Ein
  einfaches `python src/OPCUA/server.py` im Vordergrund kollidiert damit. Zum
  Testen `systemctl restart opcua-server.service` und mit einem separaten
  Client dagegen testen.
- [`Opc.Ua.MachineVision.NodeSet2.xml`](../src/OPCUA/Opc.Ua.MachineVision.NodeSet2.xml)
  ist vendoriert (OPC 40100, Core UA 1.04, keine weiteren Abhängigkeiten) und
  lässt sich per `import_xml()` laden, ohne den bekannten `asyncua`-Import-Bug
  (Issue #651) auszulösen. Machbarkeit damit nachgewiesen.
- Adressraum: `RaspiDevice` (eigener Namespace, CPU-Temperatur, Zähler,
  schreibbarer Setpoint) und `VisionSystem` vom Typ `VisionSystemType` mit
  `VisionStateMachine`, `AutomaticModeStateMachine` und
  `ResultManagement/Results`.
- **Wichtiger Fund:** `HasNotifier` allein genügt bei `asyncua` **nicht** —
  ein einzelnes `subscribeEvent(serverUrl, 'i=2253')` fängt die Vision-Events
  nicht mit auf. Details in
  [`doc/vision-system-next-steps.md`](../doc/vision-system-next-steps.md),
  widerlegt Teil 4.2 des Integrationsplans.
- Der Ergebnis-Payload ist derzeit ein **JSON-String in `ResultContent[0]`**,
  Schema `wsc.vision.test/1`, Inhalt noch „Hello World" — **kein echtes
  Detection-Payload** (Modul-ID + 6D-Pose) implementiert.
- `CpuTemperatureResult` samt Polling-Loop **nicht anfassen** — wird von einem
  Teammitglied genutzt.
- Zielbild: zwei schlanke Vision-Server, das **Backend orchestriert** als
  OPC-UA-Client beider Welten (Robotics 40010 + Machine Vision 40100), die
  Robotersteuerung selbst wird nicht verändert. Leitplanke: null
  Verhaltensänderung an bestehenden Codepfaden, alles Neue in neuen Dateien.

## Stand der Phasen

Kurzfassung — verbindlich ist
[`doc/vision-system-next-steps.md`](../doc/vision-system-next-steps.md).

- **Phase 1 (Spike)** — erledigt: Nodeset lädt, Instanzen anlegbar.
- **Phase 2** — teilweise: `VisionSystem` ist im Adressraum sichtbar, läuft aber
  noch im bestehenden `raspi`-Server statt als eigenes `vision-server/`-Package
  mit eigenem Endpoint und App-URI.
- **Phase 3** — teilweise: `StartSingleJob` durchläuft die volle Zustandsfolge,
  die drei Events feuern, End-to-end gegen den laufenden Service verifiziert.
  Fehlt: echtes Detection-Payload.

## Tiefendokumente

| Dokument | Inhalt | Wann lesen |
| --- | --- | --- |
| [`doc/vision-system-next-steps.md`](../doc/vision-system-next-steps.md) | Kurzorientierung Stand & nächste Schritte, explizit für Agenten geschrieben | **Zuerst.** Reicht für die meisten Aufgaben. |
| [`doc/vision-system.md`](../doc/vision-system.md) | Ist-Stand des implementierten Servers, Adressraum-Baum, bekannte Einschränkungen | Bei Arbeit am bestehenden Server |
| [`doc/vision-system-integration.md`](../doc/vision-system-integration.md) | Vollständiger Zielplan, 1000+ Zeilen: Bestandsanalyse, Soll-Architektur, Payload-Schema, Job-Ablauf, Signalweg, Phasenplan | Nur bei Bedarf, gezielt einen Teil. Teil 4 = Soll-Architektur, Teil 9 = Phasenplan. |
| [`src/OPCUA/server.py`](../src/OPCUA/server.py) | Server-Implementierung | — |
| [`src/OPCUA/print_setpoint.py`](../src/OPCUA/print_setpoint.py) | Client-Hilfsskript | — |

## Offene Fragen

- Wie aus der Pose eine Roboterbewegung wird (Teil 6.3 des Integrationsplans,
  dort als B4 geführt) — ungeklärt.
- Hand-Auge-Transformation (Teil 6.2) — Verfahren noch nicht festgelegt.
- Aufteilung Server 1 / Server 2 (Teil 4.5) — Konzept steht, Umsetzung offen.
- Schema des echten Detection-Payloads (Teil 4.3) — noch nicht implementiert.

## Log

<!-- Neue Einträge unten anhängen. Format siehe .claude/skills/projektwissen/SKILL.md -->

### 2026-09-13 — John — Gemeinsame Wissensstruktur eingeführt
**Status:** erledigt
**Betrifft:** aufbau, vision, opcua

Siehe [`aufbau.md`](aufbau.md) für den vollständigen Eintrag. Kurz: Erkenntnisse
aus einzelnen Chats gehören ab jetzt in das Log der passenden Bereichsdatei.
