# Altlasten A1–A6/B2/B4/B6 abbauen und VisionProgram zur alleinigen Fassade machen

**Status:** fertig — 22.09.2026
**Verantwortlich:** `Agent: RaspiDevice/VisionSystem entfernen, Part-10-Fassade`
**Thema:** opcua
**Branch:** worktree-altlasten-abbau (aus `feature/vision-server`)

## Ziel

Der Adressraum enthält hinterher **genau ein** Vision-System und **einen**
Einstiegspunkt für das Frontend. `RaspiDevice` und die leere Altlast-Instanz
`2:VisionSystem` sind in beiden Repos entfernt, `VisionMachine` liegt
standardkonform unter `Objects/Machines` statt neben `VisionProgram`, und ein
Client, der nur OPC UA Teil 10 spricht, kommt ohne einen einzigen Zugriff auf
`VisionMachine` aus.

Auslöser: Der Betreuer war durch vier gleichrangige Objekte unter `Objects`
verwirrt und möchte ausschließlich auf dem Part-10-Teil arbeiten.

## Bereits gelesen

- `doc/altlasten.md` — A1–A6 (CPU-Temperatur-Demo), B2/B4/B6 (WSC), Abbaukette
- `doc/vision-system.md` — Adressraum, Reihenfolge des Aufbaus
- `doc/vision-server-interface.md` — 40100-Schnittstelle, §7.4
- `doc/part10-programm-schnittstelle.md` — Part-10-Adressraum, Namespace-Tabelle
- `doc/arbeitsplaene/README.md` — kein konkurrierender Plan zum Thema `opcua`

## Betroffene Dateien

**Zelle** (dieses Repo)

- `src/OPCUA/server.py` → `src/vision_server/cell_server.py` (verschoben)
- `src/OPCUA/print_setpoint.py` (gelöscht)
- `src/ua_lds.py` → `src/vision_server/discovery/lds.py` (verschoben)
- `src/ua_mdns.py` → `src/vision_server/discovery/mdns.py` (verschoben)
- `src/OPCUA/*.xml`, `src/OPCUA/nodesets/` → `src/vision_server/nodesets/`
- `src/vision_server/address_space.py`
- `src/vision_server/asset_model.py`
- `src/vision_server/runner.py`
- `src/vision_server/vision_program.py`
- `tests/test_part10_fassade.py` (neu)
- `doc/altlasten.md`, `doc/vision-system.md`, `doc/vision-server-interface.md`,
  `doc/part10-programm-schnittstelle.md`

**WSC** (`webskillcomposition`, Branch `Ungetestet`)

- `backend/src/backend/config/pi_relay.py`
- `backend/src/backend/runtime/application_service.py`
- `backend/src/backend/runtime/runtime_registry.py`
- `frontend/src/features/opcua-server/config/piServers.ts`
- `frontend/src/features/opcua-server/model/visionResult.test.ts`
- `frontend/src/features/autolocate/components/AutolocateModulesModal.tsx`

## Schnittstellen

### Was wegfällt

| Weggefallen | Bisherige NodeId | Ersatz |
| --- | --- | --- |
| `RaspiDevice` samt `CpuTemperature`, `Counter`, `Setpoint` | `ns=2;i=1` … `i=4` | keiner — Demo ohne fachlichen Zweck |
| `2:VisionSystem` (leere 40100-Instanz) | `ns=2;i=5` | `VisionMachine` war immer schon das echte System |
| `CpuTemperatureResult` | `ns=2;i=…` unter `Results` | keiner |
| Namespace `http://launch-rm.de/raspi` | `ns=2` | entfällt vollständig |

**Folge für alle Clients:** Die Namespace-Indizes rücken um **eins nach unten**.

```
vorher                                    nachher
ns=2  http://launch-rm.de/raspi           (entfällt)
ns=3  …/UA/MachineVision                  ns=2  …/UA/MachineVision
ns=4  …/UA/DI/                            ns=3  …/UA/DI/
ns=5  …/UA/Machinery/                     ns=4  …/UA/Machinery/
ns=6  …/UA/MachineVision/AMCM/            ns=5  …/UA/MachineVision/AMCM/
ns=7  http://launch-rm.de/vision          ns=6  http://launch-rm.de/vision
```

Wer `get_namespace_index(uri)` benutzt, merkt nichts. Wer einen Index fest
verdrahtet hat, greift ins Leere — betrifft im WSC nur die Rückfall-Konstante
`DEFAULT_MACHINE_VISION_NAMESPACE_INDEX` (3 → 2).

### Adressraum nachher

```
Objects/
├── Machines/                     (OPC UA Machinery, Standard-Ordner)
│   └── VisionMachine             ns=<vision>;s=VisionMachine   OPC 40100
└── VisionProgram                 ns=<vision>;s=VisionProgram   OPC UA Teil 10
    ├── CurrentState / Start / Halt / Reset / Suspend / Resume
    ├── ParameterSet/  RecipeId, Continuous          (beschreibbar)
    ├── ResultSet/     JobId, ErrorCode, ExecutionMode
    │                  LatestResultJson, LatestCameraFrame,
    │                  CameraStreamMode, CalibrationProgress   (Verweise)
    ├── StartCalibration                             (Verweis, s. u.)
    ├── FinishCalibration                            (Verweis)
    └── AbortCalibration                             (Verweis)
```

`VisionMachine` bleibt vollständig erhalten und per fester String-NodeId
erreichbar. Sie hängt nur nicht mehr direkt unter `Objects`.

### Neu unter VisionProgram

Die drei Kalibriermethoden werden **nicht zweitregistriert**, sondern per
`HasComponent`-Referenz zusätzlich unter `VisionProgram` sichtbar gemacht —
dasselbe Muster wie die bereits vorhandenen `Organizes`-Verweise im
`ResultSet`. Es bleibt genau ein Methodenknoten mit genau einer Implementierung.

| Methode | NodeId | Eingang | Ausgang |
| --- | --- | --- | --- |
| `StartCalibration` | `ns=<vision>;s=VisionMachine.StartCalibration` | — | `Error: Int32` |
| `FinishCalibration` | `ns=<vision>;s=VisionMachine.FinishCalibration` | — | `Summary: String`, `Error: Int32` |
| `AbortCalibration` | `ns=<vision>;s=VisionMachine.AbortCalibration` | — | `Error: Int32` |

Aufruf geht über **beide** Objekte, belegt am 21.09.2026 gegen asyncua 2.0.1:

```python
await program_node.call_method(method_node)   # objectId = VisionProgram
await machine_node.call_method(method_node)   # objectId = VisionMachine
```

Nebenbei bekommen die drei Methoden dabei stabile String-NodeIds; bisher
vergab asyncua ihnen laufende Nummern, die sich bei jedem neuen Knoten
verschieben.

### Ergebnisse für einen reinen Part-10-Client

**Gemessen am 21.09.2026 (asyncua 2.0.1): Events propagieren nicht über die
`HasNotifier`/`HasEventSource`-Hierarchie.** Ein Abo auf `VisionProgram`
bekommt die 40100-Events von `VisionMachine` also *nicht* zu sehen, auch
nicht mit einer `HasEventSource`-Referenz dazwischen. Nachgestellt mit zwei
Objekten, zwei Abos und zwei Generatoren: jedes Event erreicht ausschließlich
das Abo auf dem emittierenden Knoten.

Die 40100-Events zusätzlich auf `VisionProgram` zu emittieren wäre echte
Doppelung — zwei Events pro Ursache. Der Weg für einen Part-10-Client ist
deshalb:

1. `ProgramTransitionEvent` (`ns=0;i=2378`) auf `VisionProgram` abonnieren —
   sagt, **dass** ein Job fertig ist (`Running -> Ready`).
2. Wertänderung von `VisionProgram/ResultSet/LatestResultJson` abonnieren —
   liefert **was** erkannt wurde, Schema `wsc.vision.detections/1`.
3. `ResultSet/ErrorCode` lesen, falls der Übergang einen Fehler meldet.

Damit braucht ein Part-10-Client keine einzige 40100-NodeId. Die 40100-Events
bleiben für 40100-Clients unverändert auf `VisionMachine`.

### Python

```python
# Modul: src/vision_server/address_space.py
async def _vision_parent(server: Server) -> Node:
    """Objects/Machines, falls das Machinery-Nodeset geladen ist, sonst Objects.

    Wirft nicht: ohne Part 2 gibt es keinen Machines-Ordner, und das
    Vision-System muss trotzdem entstehen.
    """

# Modul: src/vision_server/vision_program.py
async def install_vision_program(
    server, parent, own_idx, jobs, *,
    known_recipes: frozenset[str] | None = None,
    mirror_nodes: dict[str, Node] | None = None,
    mirror_methods: dict[str, Node] | None = None,   # neu
) -> VisionProgram:
    """`mirror_methods` werden per HasComponent zusaetzlich unter dem
    Programm sichtbar -- Referenzen, keine zweite Registrierung."""
```

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Machinery-Nodeset nicht geladen (`config.assets is None`) | `VisionMachine` hängt wie bisher direkt unter `Objects`; geloggt als Info, kein Fehler |
| `config.apriltag is None` | keine Kalibriermethoden, also auch keine Verweise — `mirror_methods` bleibt leer |
| Client verdrahtet `ns=3` für MachineVision fest | greift ins Leere; einzige bekannte Stelle ist die WSC-Rückfallkonstante, wird mitgeändert |

## Vorgehen

1. Zelle: `server.py` entkernen, `print_setpoint.py` löschen.
2. Zelle: `address_space.py` — Elternknoten `Objects/Machines`.
3. Zelle: `asset_model.py` — hartcodiertes `"VisionMachine."` auf
   `config.vision_system_name` umstellen.
4. Zelle: `vision_program.py` + `runner.py` — `mirror_methods`.
5. Zelle: Test `tests/test_part10_fassade.py` — Aufruf über beide Objekte,
   Elternknoten, keine zweite `VisionSystemType`-Instanz.
6. Zelle: Doku nachziehen.
7. WSC: Relay, Temperaturanzeige, `legacy`-Zweig entfernen.

## Offene Fragen

- Der Umzug unter `Machines` hängt daran, dass `config.assets` gesetzt ist.
  Auf beiden Pis ist das der Fall. Für eine Entwicklungsinstanz ohne Part 2
  bleibt der Fallback.

## Abweichungen vom Plan

**1. Events lassen sich nicht spiegeln — gemessen, nicht angenommen.**
Der Plan ging davon aus, dass eine `HasEventSource`-Referenz genügt, damit ein
Abo auf `VisionProgram` auch die 40100-Events sieht. Das ist falsch. Am
21.09.2026 gegen asyncua 2.0.1 mit zwei Objekten, zwei Abos und zwei
Generatoren nachgestellt: jedes Event erreicht **ausschließlich** das Abo auf
dem emittierenden Knoten, mit und ohne Referenz. Kontrollfall lief, der
Testaufbau war also nicht schuld.

Folge: Die 40100-Events bleiben auf `VisionMachine`. Ein reiner Part-10-Client
holt sein Ergebnis über die Wertänderung von
`VisionProgram/ResultSet/LatestResultJson` statt über ein Event. Sie zusätzlich
auf `VisionProgram` zu emittieren wäre echte Doppelung gewesen — zwei Events
pro Ursache — und genau das sollte dieser Umbau ja beenden.

**2. Methodenspiegelung geht per Referenz.** Ebenfalls vorab gemessen: eine
`HasComponent`-Referenz reicht, der Aufruf gelingt mit beiden Objekten als
`objectId`, ohne zweite Registrierung.

**3. Zusätzlich gemacht, weil es unmittelbar anschloss:**

* `origin/feature/vision-server` (20617c7) eingezogen — die Umstellung auf
  `Server.register_to_discovery()` und vier weitere Commits. Ein Konflikt in
  `server.py`, beide Male Kommentare bzw. die gelöschte Demo gegen ihren
  Wegfall.
* **Paketumzug:** `src/OPCUA/` ist aufgelöst. Nach dem Altlasten-Abbau enthielt
  `server.py` nur noch Pi-Konfiguration, mDNS und LDS — das gehört neben das
  Paket, das es benutzt. Erledigt damit Altlast **C5** (Nodesets) mit.

**4. B4 und B5 waren schon weg.** Der Plan sah vor, die Temperaturanzeige und
`MOCK_MODULES` aus dem Autolocate-Popup zu entfernen. Beide waren am 22.09.2026
nicht mehr vorhanden — das Popup hatte sich zwischenzeitlich weiterentwickelt.

**5. Nicht gemacht:** `legacyCameraFrameNodeId` im Frontend bleibt. Der Name
täuscht — gemeint ist der numerische Kamera-Knoten des *älteren Pi-Builds*,
nicht die entfernte `2:VisionSystem`. Solange die beiden Pis zeitversetzt
umgestellt werden (und das stehen sie gerade, siehe D5), ist der Rückfall
nützlich.

**6. Betriebsfolge, die der Plan nicht hatte:** Die systemd-Unit auf beiden Pis
startet `src/OPCUA/server.py`. Diesen Pfad gibt es nicht mehr; die Unit ist
nirgends versioniert (Altlast D5) und muss von Hand auf
`python3 -m vision_server.cell_server` mit `WorkingDirectory=<repo>/src`
umgestellt werden.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- [x] `doc/altlasten.md`: A1–A6, B2, B4, B5, B6, C5 gestrichen; B1 auf den
      Frontend-Rest eingekürzt; D5 als akut markiert.
- [x] Fachdoku nachgezogen: `vision-system.md`, `vision-server-interface.md`,
      `part10-programm-schnittstelle.md`, `vision-system-next-steps.md`,
      `apriltag-e2e-test.md`, `apriltag-referenz.md`, `praesentation.md`,
      `README.md`.

### Geprüft

| Was | Ergebnis |
| --- | --- |
| Zelle: `PYTHONPATH=src python3 -m unittest discover -s tests -t .` | 249 Tests, grün |
| Zelle: Server unter neuem Einstiegspunkt gestartet | Nodesets aus dem Paket, `ns=6;s=VisionMachine` unter `Machines`, `VisionProgram` daneben, mDNS aktiv |
| WSC Frontend: `npx vitest run src/features/opcua-server` | 16 Tests, grün |
| WSC Frontend: `npx tsc --noEmit` | ohne Befund |
| WSC Frontend: volle Suite | 121 grün, 1 rot — `entities/robot/model/store.test.ts:188`, vorbestehend (Altlast D8), keine Robot-Datei angefasst |
| WSC Backend | `pytest` ist im venv nicht installiert (Altlast D7: die Suite lief ohnehin nicht durch). Stattdessen Importe von `application_service` und `runtime_registry` geprüft — ziehen sauber |

**Offen:** Die systemd-Units auf beiden Pis. Bis sie umgestellt sind, startet
der Dienst dort nach einem Pull nicht mehr.
