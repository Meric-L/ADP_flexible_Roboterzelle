# Vision-System — Stand & nächste Schritte (für Agenten)

Kurzorientierung, damit ein Agent ohne vorherigen Kontext weiß, wo das
Vision-Server-Vorhaben gerade steht. Ausführlicher Ist-Stand:
[`vision-system.md`](vision-system.md). Vollständiger Plan:
[`vision-system-integration.md`](vision-system-integration.md) (Teil 9 = Phasenplan,
Teil 4 = Soll-Architektur).

## Aktueller Stand

- [x] **Phase 1 (Spike)** erledigt: `Opc.Ua.MachineVision.NodeSet2.xml` ist
  vendoriert und lässt sich per `import_xml()` laden; eine Instanz von
  `VisionSystemType` bzw. `ResultType` lässt sich anlegen und beschreiben.
  Die bekannten `asyncua`-Importrisiken (Issue #651) sind damit praktisch
  ausgeschlossen.
- [x] Server läuft auf dem Raspberry Pi und startet automatisch beim Booten
  — **nicht** nur "beim Booten", sondern als systemd-Service
  `opcua-server.service` (`Restart=always`, `RestartSec=5`,
  `/etc/systemd/system/opcua-server.service`, nicht in diesem Repo
  versioniert). D.h. jeder Absturz von `server.py` wird automatisch nach
  5s neu gestartet — praktisch für Robustheit, aber **Vorsicht bei
  manuellem Testen**: ein einfaches `python src/OPCUA/server.py` im
  Vordergrund kollidiert mit dem laufenden Service (Port 4840 belegt);
  zum Testen lieber `systemctl restart opcua-server.service` verwenden
  und gegen den laufenden Service mit einem separaten Client testen.
- [~] **Phase 2** teilweise: Die `VisionSystem`-Instanz ist im Adressraum
  sichtbar und hat die `HasNotifier`-Referenz vom Server-Objekt
  (Teil 4.2). Sie läuft aber noch **im bestehenden `raspi`-Server**
  statt als eigenes `vision-server/`-Package mit eigenem Endpoint/App-URI
  (Teil 4.1/4.6).
- [~] **Phase 3** teilweise (Smoke-Test): `StartSingleJob` ist verlinkt und
  durchläuft die volle Zustandsfolge `Preoperational → Halted →
  Operational/Initialized → Ready → SingleExecution → Ready`; die drei
  Events (`JobStartedEvent`, `AcquisitionDoneEvent`, `ResultReadyEvent`)
  feuern; `ResultContent` einer neuen, separaten `HelloWorldResult`-Instanz
  enthält einen JSON-String (`{"schema": "wsc.vision.test/1", "jobId",
  "creationTime", "message": "Hello World", "time"}`) — noch **kein**
  echtes Detection-Payload (Modul-ID + 6D-Pose, Teil 4.3). Die bestehende
  `CpuTemperatureResult`-Instanz + Polling-Loop bleiben unverändert (wird
  von einem Teammitglied genutzt). End-to-end gegen den laufenden
  Produktions-Service verifiziert (siehe unten).

  **Wichtiger Fund, der Teil 4.2 widerlegt:** Die Aussage "ein einziger
  `subscribeEvent(serverUrl, 'i=2253')` reicht dank `HasNotifier`" gilt
  **nicht** für die hier verwendete `asyncua`-Bibliothek. Serverseitig
  matcht `asyncua` Events beim Ausliefern strikt nach exaktem
  `emitting_node` (`monitored_item_service.py: trigger_event` prüft
  `event.emitting_node in self._monitored_events`) — es gibt **keine**
  Traversierung der `HasNotifier`-Hierarchie. Ein Client muss deshalb
  direkt auf `VisionSystem` abonnieren (`subscribe_events(vision_system,
  ...)`), nicht auf `client.nodes.server`. Verifiziert per Testclient:
  Abo auf `nodes.server` empfängt nichts, Abo auf `VisionSystem` empfängt
  alle drei Events zuverlässig. Relevant für die spätere
  Frontend-Anbindung (Teil 5.1/5.2).

## Nächste Schritte (aus Teil 9 des Plans, Phasen 2–3)

- [x] **HasNotifier setzen** — `VisionSystem` per
  `server.nodes.server.add_reference(vision_system, ua.ObjectIds.HasNotifier, forward=True)`
  und `set_event_notifier([...])` sichtbar gemacht (Teil 4.2), siehe
  `src/OPCUA/server.py`.

- [x] **State Machine + `StartSingleJob` + Events (Smoke-Test)** — siehe oben.
  Details: `VisionStateMachine`/`AutomaticModeStateMachine` werden **nicht**
  per `FiniteStateMachine.install()` neu angelegt (das würde einen
  doppelten Baum erzeugen), sondern an die von der Nodeset-Instanziierung
  bereits vorhandenen Knoten gebunden (`_state_machine_node` manuell setzen,
  dann `init()`). Die States der `AutomaticModeStateMachine`
  (`Initialized`/`Ready`/`SingleExecution`/`ContinuousExecution`) sind
  **nicht** als Kinder der Instanz vorhanden (kein `HasModellingRule` auf
  diesen Knoten im Nodeset), sondern nur als feste Knoten am Typ
  `VisionAutomaticModeStateMachineType` (ns=1;i=5056/5057/5058/5059) —
  müssen per fester NodeId (mit `mv_idx`) geholt werden, nicht per
  `get_child()` auf der Instanz. Siehe `src/OPCUA/server.py`.

1. **Package-Split** — eigenes `vision-server/`-Package mit eigenem
   Endpoint/App-URI statt im bestehenden `raspi`-Server (Teil 4.1/4.6). Wird
   auf einem eigenen Branch bearbeitet (`feature/vision-server-package-split`).
2. **Echtes Ergebnis-Payload** — volles JSON-Schema aus Teil 4.3
   (`moduleId`, `instanceId`, `position`, `orientation` als Quaternion `xyzw`,
   `frameId`, `lengthUnit`/`angleUnit` etc.) statt des Hello-World-Platzhalters,
   sobald echte Bilderkennung angeschlossen wird. **Abweichung vom
   Stolperstein-4-Rezept:** `ResultContent.write_value([ua.Variant(json, ...)])`
   (Liste von Variants) schlägt mit `BadTypeMismatch` fehl. Stattdessen wie
   beim bestehenden `CpuTemperatureResult`-Muster einen skalaren Wert
   schreiben: `ResultContent.write_value(json_string, ua.VariantType.String)`
   (nach `write_attribute(DataType, ...)`-Override auf `String`).
3. **Danach erst Server 2 (3D)** — zweite Instanz/Profil nach Teil 4.5/4.6,
   sobald Server 1 (2D) den Job-Ablauf stabil durchläuft.

> Phase 0 und Phasen 4–12 aus Teil 9 betreffen das **WebSkillComposition-
> Backend/Frontend** (ein separates Repo mit `backend/`, `frontend/` — existiert
> **nicht** in diesem Repo). Für dieses Repo relevant sind nur Phasen 1–3
> (Aufbau des Vision-Servers selbst).

## Offene Entscheidungen (noch nicht getroffen)

- **Dispatch-Modus** (Teil 6.3): wie eine erkannte Pose zu einer Roboterbewegung
  wird (`cartesian` via `LinMoveTcp` vs. `frontendIk`) — abhängig davon, ob der
  reale Robotics-Server `LinMoveTcp`/`RotMoveTcp` exponiert. Betrifft die
  Backend-Seite, nicht diesen Server.
- **Hand-Auge-Kalibrierung** — noch nicht spezifiziert, `auto_execute` bleibt
  bis dahin `False`.
- **Konzeptionelle Lücken aus der Lokalisierung selbst** — Koordinatensystem-
  Kette Welt-Tag → Kamera → Roboter, Format der Layer-1→Layer-2-Übergabe,
  Repositionierungsstrategie: siehe [`concept/offene_punkte.md`](../concept/offene_punkte.md).
  Diese bestimmen letztlich, was `frameId`/`position`/`orientation` im
  Ergebnis-Payload (Schritt 4 oben) konkret bedeuten müssen.
