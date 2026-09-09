# Vision-System — Stand & nächste Schritte (für Agenten)

Kurzorientierung, damit ein Agent ohne vorherigen Kontext weiß, wo das
Vision-Server-Vorhaben gerade steht. Ausführlicher Ist-Stand:
[`vision-system.md`](vision-system.md). Backend-Schnittstelle:
[`vision-server-interface.md`](vision-server-interface.md). Vollständiger Plan:
[`vision-system-integration.md`](vision-system-integration.md) (Teil 9 = Phasenplan,
Teil 4 = Soll-Architektur).

## Aktueller Stand

- [x] **Phase 1 (Spike)**: `Opc.Ua.MachineVision.NodeSet2.xml` ist vendoriert,
  lässt sich per `import_xml()` laden und instanziieren. Die bekannten
  `asyncua`-Importrisiken (Issue #651) sind ausgeschlossen.
- [x] **Phase 2**: eigenes Paket `src/vision_server/` mit eigenem Endpoint
  (`opc.tcp://0.0.0.0:4841/vision/machine/`), eigener ApplicationURI
  (`urn:launch-rm:vision:machine`) und eigenem Namespace
  (`http://launch-rm.de/vision`). `VisionSystem`-Instanz mit `HasNotifier`
  vom Server-Objekt. Modulaufteilung nach Teil 4.1, aber im `src/`-Layout
  dieses Repos statt als eigenes uv-Projekt.
- [x] **Phase 3 (Server 1, mit Platzhalter-Erkennung)**: Zustandsautomaten,
  `StartSingleJob` mit Guard und Validierung, ResultManagement-Ablage,
  vier Events, JSON-Payload im Schema aus Teil 4.3, Fehlerpfad über den
  `Error`-Zustand. Erkennung ist eine `DetectionSource`-Strategie; aktiv ist
  `hello_world.py`.
- [x] Der Raspi-Server läuft auf dem Pi als systemd-Service
  `opcua-server.service` (`Restart=always`, `RestartSec=5`, nicht in diesem
  Repo versioniert). Für den Vision-Server ist `vision-server.service` analog
  vorgesehen. **Vorsicht beim manuellem Testen**: ein Vordergrundstart
  kollidiert mit dem laufenden Service (Port belegt) — `systemctl restart`
  benutzen oder einen freien Port wählen.
- [~] Der alte Hello-World-Smoke-Test steckt **noch** in
  `src/OPCUA/server.py` (Port 4840). Er bleibt absichtlich bis zum
  gemeinsamen Test mit dem Teamkollegen erhalten und wird danach auf einem
  eigenen Branch entfernt (siehe „Nächste Schritte" 2).

### Verifiziert (lokal, asyncua 2.0.1, Server + Client in zwei Prozessen)

Happy Path (Payload, Event-Reihenfolge, Korrelation über `jobId`,
`ResultState`/`IsPartial`/`IsSimulated`/`CreationTime` im Event, beide
Ergebnisknoten) · `BUSY` bei zwei gleichzeitigen Aufrufen ·
`INVALID_ARGUMENT` bei überlanger `MeasId` · `UNKNOWN_RECIPE` bei unbekanntem
Rezept · Fehlerpfad `--parameter force-error` mit `resultState=5`, Durchlauf
über den `Error`-Zustand und funktionierender Wiederaufnahme.

## Verifizierte Fakten, die den Docs widersprachen

Diese Punkte sind gegen das Nodeset und den asyncua-Quellcode geprüft; die
alten Aussagen in Teil 2/4.2/4.4 des Plans sind falsch:

1. **`ResultReadyEventType` (i=1024) hat kein `Result: ResultDataType`**,
   sondern 15 flache Felder. Ohne `load_data_type_definitions()` dekodierbar
   sind nur `ResultContent` (`BaseDataType`, ValueRank 1), `CreationTime`,
   `IsPartial`, `IsSimulated`, `ResultState`. ⇒ Das Payload reist **im Event**
   in `ResultContent[0]`; ein zweiter Read ist unnötig.
2. **Die `ResultManagement`-Methoden sind vom asyncua-Client unbenutzbar** —
   nicht wegen der Rückgabe, sondern weil schon die **Eingabe**
   (`ResultIdDataType`, `JobIdDataType`) ein ExtensionObject ist (Issue #1693).
   Kein Handle ⇒ der Handle-Leak aus Teil 6.4 ist gegenstandslos.
3. **Es gibt keinen Übergang `Halted → Operational`.** Die früher
   dokumentierte Folge `Preoperational → Halted → Operational` ist nicht
   konform. Korrekt: `Preoperational → Operational`
   (`PreoperationalToOperationalAuto`), innen `Initialized → Ready`.
4. **Der `Error`-State ist nicht `i=5030`.** Die States der äußeren
   `VisionStateMachine` sind Mandatory-Kinder der Instanz
   (`get_child(f"{mv}:Error")`); `i=5030` ist der **Typ**knoten und würde
   instanzübergreifend wirken. Nur die States der `AutomaticModeStateMachine`
   (i=5056–5059) kommen per fester NodeId.
5. **`LastTransition` existiert auf der Instanz nicht.** asyncuas
   `change_state(..., transition=...)` schreibt dorthin und würde scheitern ⇒
   Zustandswechsel ohne `Transition`-Objekt, Übergangsname in der
   Event-Nachricht.
6. **`ResultContent` kommt mit einer Null-NodeId als DataType.** Deshalb
   scheitert *jeder* Write mit `BadTypeMismatch` — auch der in Teil 4.2
   (Stolperstein 4) empfohlene `[ua.Variant(...)]`-Write. Erst der
   DataType-Override auf `String` macht den **Array**-Write möglich, womit die
   `ResultContent[0]`-Semantik erhalten bleibt.
7. **Event-Felder brauchen fertige `ua.Variant`-Objekte.** asyncua leitet den
   Feldtyp aus dem Nodeset ab und erhält für diese Felder VariantType `Null`;
   ein roher Python-Wert kommt beim Client als `None` an.
8. **`@uamethod`-Handler müssen `async` sein**, wenn sie einen Task starten:
   synchrone Handler laufen in einem ThreadPoolExecutor ohne Event-Loop.
9. **Methodenrückgaben müssen ein `tuple` sein.** Eine `list` wird von
   asyncua als *ein* Variant verpackt, der Client bekommt dann verschachtelte
   Variants. Betrifft auch `src/OPCUA/server.py:195`.
10. **Kein Event-Bubbling über `HasNotifier`** (bereits bekannt): asyncua
    matcht `emitting_node` exakt, ein Abo auf `i=2253` empfängt nichts. Alle
    Generatoren — auch der der State Machine — müssen aus dem
    `VisionSystem`-Knoten emittieren.

## Nächste Schritte

1. **Gemeinsamer Test mit dem Teamkollegen (Backend)** — Schnittstelle einmal
   durchspielen, Grundlage ist
   [`vision-server-interface.md`](vision-server-interface.md). Danach
   `vision-server.service` auf dem Pi einrichten.
2. **Cleanup-Branch** — den Phase-3-Smoke-Test aus `src/OPCUA/server.py`
   entfernen (Zeilen des `# --- Phase 3`-Blocks, die State-Machine-Helfer und
   die dann unbenutzten Imports). **Bleiben** müssen `RaspiDevice`, der
   Nodeset-Import, die `VisionSystem`-Instanz, `HasNotifier`,
   `ResultManagement`/`Results`, `CpuTemperatureResult` und die
   Polling-Schleife — `CpuTemperatureResult` hängt unter `VisionSystem` und
   wird vom Temperatur-Interface genutzt. NodeId-sicher: die betroffenen Knoten
   entstehen *vor* dem gelöschten Block, `print_setpoint.py` (`ns=2;i=4`)
   bleibt gültig. Danach das Nodeset-XML nach `src/vision_server/nodesets/`
   verschieben und die zwei Pfadkonstanten anpassen.
3. **Echtes Ergebnis-Payload** — neue `DetectionSource` in
   `src/vision_server/detection/` plus Registry-Eintrag. Das Schema
   (`wsc.vision.detections/1`) bleibt unverändert, es füllen sich nur
   `detections`. Voraussetzung ist die Klärung von Koordinatensystem und
   Kalibrierung (siehe unten).
4. **Danach Server 2 (3D)** — zweite Instanz auf Port 4842 mit eigener
   ApplicationURI und eigener `visionSystemId`, nach Teil 4.5/4.6.

> Phase 0 und Phasen 4–12 aus Teil 9 betreffen das **WebSkillComposition-
> Backend/Frontend** (separates Repo, existiert hier **nicht**). Für dieses
> Repo sind nur Phasen 1–3 relevant.

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
  Diese bestimmen, was `frameId`/`position`/`orientation` im Ergebnis-Payload
  konkret bedeuten müssen.
- **Strukturtypisierte Event-Felder** befüllen, sobald asyncua-Issue #1693
  gefixt ist. Das JSON-Payload bleibt auch dann die maßgebliche Quelle.
