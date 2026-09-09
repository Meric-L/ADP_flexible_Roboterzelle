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
- [x] Server läuft auf dem Raspberry Pi und startet automatisch beim Booten.
- [~] **Phase 2** teilweise: Die `VisionSystem`-Instanz ist im Adressraum
  sichtbar und hat seit Kurzem die `HasNotifier`-Referenz vom Server-Objekt
  (Teil 4.2, wichtig für späteres Event-Abonnement mit einem einzigen
  `subscribeEvent`). Sie läuft aber noch **im bestehenden `raspi`-Server**
  statt als eigenes `vision-server/`-Package mit eigenem Endpoint/App-URI
  (Teil 4.1/4.6).
- [ ] **Phase 3** nicht begonnen: keine State-Machine, keine `StartSingleJob`,
  keine Events, kein JSON-Ergebnis-Payload. `ResultContent` trägt aktuell nur
  die CPU-Temperatur als Platzhalter, keine echte Erkennung (Modul-ID + 6D-Pose).

## Nächste Schritte (aus Teil 9 des Plans, Phasen 2–3)

- [x] **HasNotifier setzen** — `VisionSystem` per
  `server.nodes.server.add_reference(vision_system, ua.ObjectIds.HasNotifier, forward=True)`
  und `set_event_notifier([...])` sichtbar gemacht (Teil 4.2), siehe
  `src/OPCUA/server.py`.

1. **Package-Split** — eigenes `vision-server/`-Package mit eigenem
   Endpoint/App-URI statt im bestehenden `raspi`-Server (Teil 4.1/4.6). Wird
   auf einem eigenen Branch bearbeitet.
2. **State Machine aufbauen** — `VisionStateMachine` +
   `AutomaticModeStateMachine` über `asyncua.common.statemachine.FiniteStateMachine`;
   Zustandsfolge `Preoperational → Halted → Operational/Initialized → Ready →
   SingleExecution → Ready` (Teil 4.4).
3. **`StartSingleJob` implementieren** — Methode + Job-Coroutine, die (zunächst
   simuliert) eine Detection erzeugt statt der CPU-Temperatur.
4. **Ergebnis-Payload umstellen** — JSON-String-Schema aus Teil 4.3
   (`moduleId`, `instanceId`, `position`, `orientation` als Quaternion `xyzw`,
   `frameId`, `lengthUnit`/`angleUnit` etc.) statt rohem `Double`. Dabei
   `ResultContent` als `BaseDataType[]` mit explizitem
   `ua.Variant(json_string, ua.VariantType.String)` befüllen (Stolperstein 4
   in Teil 4.2).
5. **Events tatsächlich feuern** — `JobStartedEvent`, `AcquisitionDoneEvent`,
   `ResultReadyEvent` bei jedem Job-Durchlauf.
6. **Danach erst Server 2 (3D)** — zweite Instanz/Profil nach Teil 4.5/4.6,
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
