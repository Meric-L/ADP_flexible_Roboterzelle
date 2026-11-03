# Layer-2-Lauf auf dem Hand-Pi: ankern, planen, messen

**Status:** fertig — 29.09.2026 (Fahrbefehl am Roboter offen, siehe Offene Fragen)
**Verantwortlich:** Meric, `Agent: Layer-2-Lauf auf dem Hand-Pi`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Der Hand-Pi führt die Feinmessung (Layer 2) selbst:
1. Er plant, wohin der Flansch muss, um den nächsten Welttag zu sehen.
2. Er ankert sich dort (`apriltag-hand-auge-ankern.md`).
3. Er plant danach jedes Modul aus der **gemessenen** statt der groben Roboterbasis.
4. Er misst jedes Modul.

Frontend und Backend reichen nur noch Posen weiter:
- vom Frontend an den Pi: grobe Posen aus Layer 1 und die Tag-Lagen aus dem Modulkatalog;
- vom Pi an den Roboter: das nächste Fahrziel;
- vom Roboter an den Pi: die erreichte Pose.

Alles läuft über OPC UA.

Vorher lag die Planung im Frontend (`webskillcomposition`, `cell-modules/model/layer2.ts`). Ihr fehlte der Anker-Schritt, und sie plante immer mit der groben Basis aus Layer 1.

## Bereits gelesen

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md` (kein Plan zu einem Layer-2-Lauf; `deckenkamera-volle-aufloesung.md` in Arbeit, anderes Thema)
- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md` (Anker-Rechnung, Roboterpose als Job-Parameter)
- `doc/projektdoku/arbeitsplaene/apriltag-tagmap-ueber-opcua.md` (Muster SetTagMap/TagMapJson)
- `doc/projektdoku/vision-server-interface.md` (Methoden, Fehlercodes, Sperren 12.7)
- `doc/projektdoku/apriltag-lokalisierung.md` Abschnitt zur Repositionierung: „nicht Teil dieser Bibliothek … gehört in die Ablaufsteuerung“. Deshalb liegt der Lauf in `vision_server/`, nicht in `tagloc/`.

## Betroffene Dateien

- `src/vision_server/layer2/__init__.py`, `plan.py`, `run.py` (neu)
- `src/vision_server/address_space.py`: Knoten `Layer2Target` und `Layer2Status`
- `src/vision_server/runner.py`: Methoden, Verdrahtung, Sperren, Stop
- `tests/test_layer2_plan.py`, `tests/test_layer2_run.py` (neu)
- `doc/projektdoku/vision-server-interface.md`: neuer Abschnitt
- `doc/projektdoku/arbeitsplaene/README.md`: Indexzeile

## Schnittstellen

### OPC UA

Alle Elemente liegen unter `VisionMachine` im Namensraum `http://launch-rm.de/vision`. Es gibt sie **nur auf dem Hand-Pi**, also wenn `AprilTagProfileConfig.hand_eye_path` gesetzt ist. Sie werden zusätzlich unter `VisionProgram` verlinkt, wie die Kalibriermethoden.

| Element | NodeId | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| StartLayer2Run | `ns=<vision>;s=VisionMachine.StartLayer2Run` | Methode | Aufruf | startet einen Lauf |
| ReportRobotPose | `ns=<vision>;s=VisionMachine.ReportRobotPose` | Methode | Aufruf | meldet die erreichte Flanschpose, startet die Messung |
| Layer2Target | `ns=<vision>;s=VisionMachine.Layer2Target` | String (JSON) | Lesen/Abo | nächstes Fahrziel; `""`, wenn der Lauf gerade keins hat |
| Layer2Status | `ns=<vision>;s=VisionMachine.Layer2Status` | String (JSON) | Lesen/Abo | Zustand des Laufs samt Schritten und Ergebnissen |
| Stop | vorhanden | Methode | Aufruf | bricht zusätzlich einen laufenden Layer-2-Lauf ab |

**Methodensignaturen:**
- `StartLayer2Run(RunJson: String) -> (Error: Int32)`
- `ReportRobotPose(ReportJson: String) -> (Error: Int32)`

**Fehlercodes** (`errors.py`):

| Code | Wann |
| --- | --- |
| `OK` | angenommen |
| `INVALID_ARGUMENT` | JSON ungültig oder Schema falsch |
| `INVALID_STATE` | Vision-System nicht `Ready`; keine Hand-Auge-Datei geladen; `ReportRobotPose` ohne wartendes Ziel oder mit falschem `stepIndex` |
| `BUSY` | ein Job, eine Kalibrierung oder schon ein Lauf aktiv (StartLayer2Run); ein Job läuft (ReportRobotPose) |

**Sperren während eines Laufs:** `StartCalibration` und `SetTagMap` antworten `BUSY`, weil `SetTagMap` den Anker verwirft. `StartSingleJob` bleibt erlaubt, zum Debuggen.

### Daten

**`RunJson`**, Schema `wsc.vision.layer2.run/1`. Posen sind überall `{"position": [x,y,z], "orientation": [qx,qy,qz,qw]}`, in Metern, Quaternion xyzw.

```json
{
  "schema": "wsc.vision.layer2.run/1",
  "robotBase": { "position": [..], "orientation": [..] },
  "modules": [
    {
      "moduleId": "MOD-A",
      "name": "Modul A",
      "pose": { "position": [..], "orientation": [..] },
      "tags": [ { "tagId": 7, "poseInModule": { "position": [..], "orientation": [..] } } ]
    }
  ],
  "options": { "standoffM": 0.3, "reachM": 0.85 }
}
```

- `robotBase`: `T_world_base` aus Layer 1, grob.
- `modules[].pose`: `T_world_module` aus Layer 1, grob. Der Roboter selbst gehört nicht in die Liste.
- `modules[].tags[].poseInModule`: `T_module_tag` aus dem Modulkatalog. Der Tag-Frame ist der der Bibliothek: +X rechts, +Y zur Oberkante des Drucks, +Z aus dem Tag heraus.
- `options` ist optional. Die Defaults sind 0,3 m Abstand und 0,85 m Reichweite. Die Reichweite ist nur ein Hinweis.

**`ReportJson`**, Schema `wsc.vision.layer2.report/1`:

```json
{ "schema": "wsc.vision.layer2.report/1", "stepIndex": 0, "reached": true,
  "flangeInBase": { "position": [..], "orientation": [..] } }
{ "schema": "wsc.vision.layer2.report/1", "stepIndex": 0, "reached": false,
  "reason": "außer Reichweite" }
```

`flangeInBase` ist die Pose, die der Roboter **meldet**, nicht das Ziel. Nur mit ihr darf geankert werden (`apriltag-hand-auge-ankern.md`).

**`Layer2Target`**, Schema `wsc.vision.layer2.target/1`:

```json
{ "schema": "wsc.vision.layer2.target/1", "runId": "l2-000001", "stepIndex": 0,
  "purpose": "anchor", "moduleId": "", "name": "Welttag 0", "tagId": 0,
  "flangeInBase": {..}, "cameraInWorld": {..}, "tagInWorld": {..},
  "reachM": 0.62, "reachable": true }
```

- `purpose` ist `"anchor"` oder `"measure"`.
- `flangeInBase` ist das Fahrziel für den Roboter.
- `cameraInWorld` und `tagInWorld` dienen der Anzeige.

**`Layer2Status`**, Schema `wsc.vision.layer2.status/1`:

```json
{ "schema": "wsc.vision.layer2.status/1", "runId": "l2-000001",
  "state": "waitingForRobot", "message": "", "stepIndex": 0,
  "anchor": { "worldTagId": 0, "baseInWorld": {..} },
  "steps": [ { "stepIndex": 0, "purpose": "anchor", "moduleId": "", "name": "Welttag 0",
               "tagId": 0, "reachM": 0.62, "reachable": true,
               "status": "done", "detail": "", "result": null } ] }
```

- `state` ist einer von: `idle`, `waitingForRobot`, `measuring`, `finished`, `aborted`.
- `steps[].status` ist einer von: `pending`, `target`, `measuring`, `done`, `failed`, `skipped`.
- `anchor` ist `null`, bis dieser Lauf geankert hat.
- `steps[].result` gibt es nur bei Mess-Schritten: `{"frameId", "detections"}` aus dem Payload des Jobs (`wsc.vision.detections/1`).

### Python

```python
# Modul: src/vision_server/layer2/plan.py   (numpy, tagloc; kein asyncua)
@dataclass(frozen=True)
class RunRequest: robot_base: Pose; modules: tuple[ModuleRequest, ...]; standoff_m: float; reach_m: float
def parse_run_request(text: str) -> RunRequest          # ValueError mit Klartext
def parse_report(text: str) -> RobotReport              # ValueError
def view_target(T_world_tag, T_world_base, T_flange_cam, standoff_m) -> ViewTarget
def plan_run(request, tag_map, pose_world_base, T_flange_cam) -> list[PlannedStep]

# Modul: src/vision_server/layer2/run.py   (asyncio, kein asyncua)
class Layer2Run:
    active: bool
    def start(self, request_json: str) -> VisionErrorCode
    def report(self, report_json: str) -> VisionErrorCode
    def on_job_finished(self, job_id: str, code: VisionErrorCode) -> None
    async def abort(self, reason: str) -> None
```

### Rechnung

Welttag und Modultag gehen durch **dieselbe** Funktion `view_target`:

```
T_tag_cam      = Drehung 180° um x, 0,3 m entlang +z des Tags  (Kamera schaut auf den Tag)
T_world_cam    = T_world_tag · T_tag_cam
T_world_flange = T_world_cam · inv(T_flansch_cam)
T_base_flange  = inv(T_world_base) · T_world_flange             <- Fahrziel
```

Die beiden Tag-Arten unterscheiden sich nur darin, **woher** `T_world_tag` kommt:
- Welttag: aus der Tag-Map (`nearest_world_tag`, der der Basis nächste).
- Modultag: `T_world_module · T_module_tag` des Tags, der am meisten zur Basis zeigt.

Außerdem wird `T_world_base` gewechselt:
- vor dem Ankern: die grobe Basis aus Layer 1;
- danach: die gemessene aus `RobotAnchor.pose_world_base`.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Welttag nicht gesehen, keine Hand-Auge-Datei, Roboterpose fehlt | Anker-Schritt `failed`, Lauf `aborted`, Rest `skipped` |
| Roboter meldet `reached: false` | Schritt `failed`; beim Anker Abbruch, sonst weiter |
| Modul-Tag nicht gesehen oder Pose nicht im Welt-KS | Schritt `skipped` mit Grund, weiter |
| Job-Fehler (Timeout, Kamera) | Schritt `failed`, weiter; beim Anker Abbruch |
| Modul außer Reichweite | Schritt `skipped` ohne Fahrt |
| `Stop` | Lauf `aborted`, `Layer2Target = ""`, laufender Job wird abgebrochen |

Hinweis zum Anker-Schritt: `locate_modules` überspringt Welttags. Ein Bild nur mit dem Welttag liefert deshalb `DETECTION_FAILED`, obwohl dabei geankert wurde. Der Schritt gilt als erfolgreich, wenn der Job einen **neuen** `RobotAnchor` hinterlassen hat, egal mit welchem Code.

## Vorgehen

1. `plan.py` mit Tests: Parser, `view_target`, Tag-Wahl, Reihenfolge, Neuplanung.
2. `run.py` mit Tests gegen Fakes: Ablauf, Anker, Abbruch, Report-Prüfung.
3. Knoten und Methoden in `address_space.py`/`runner.py`, Sperren, Stop.
4. Fach-MD `vision-server-interface.md`: neuer Abschnitt.

## Offene Fragen

- **Fahrbefehl am Roboter:** Die Roboter-Server bieten heute nur `go_to` mit Gelenkwinkeln. Ein kartesischer Befehl (Flanschpose im Basis-KS) und das Auslesen der echten Flanschpose fehlen. Bis dahin füllt das Frontend den Report im Trockenlauf mit dem Ziel selbst. Das ist nur zum Ausprobieren gedacht.
- `T_flansch_cam` ist an der echten Hardware noch nicht gemessen (siehe `apriltag-hand-auge-ankern.md`). Ohne die Datei lehnt `StartLayer2Run` mit `INVALID_STATE` ab.

## Abweichungen vom Plan

- `Layer2Status` hat zusätzlich `stepIndex` (aktueller Schritt, `-1` ohne) und `anchor.spreadM`.
- Die Methoden hängen nicht an `jobs.busy` allein: `StartLayer2Run` prüft auch die Kalibrier-Session (`calibration_session.busy`).
- **Stop über die Part-10-Fassade** (`VisionProgram`) bricht nur den Job ab, nicht den Lauf. Der Lauf wertet den abgebrochenen Job dann als `failed` (`CANCELLED`); beim Anker-Schritt bricht er ab. Den ganzen Lauf beendet `AutomaticModeStateMachine/Stop`.
- Der OPC-UA-Test (`tests/test_layer2_opcua.py`) fährt `runner._install_layer2_run` gegen einen echten asyncua-Server mit Fake-JobRunner und Fake-Quelle. Über die ganze Maschine mit Kamera ist der Lauf nicht getestet.
- **Frontend** (`webskillcomposition`, Branch `Ungetestet`): Das Fenster „Autolocate Layer 2“ hat „Tags erkennen (Debug)“ und den Bereich „Feinmessung: Ankern und Module“. Die alte Planung im Browser ist entfernt.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index aktualisiert.
- [x] Fachwissen in `vision-server-interface.md` Abschnitt 14 übernommen.
- [x] Testlauf: 522 Tests, OK. Neu: `test_layer2_plan.py` (11), `test_layer2_run.py` (8), `test_layer2_opcua.py` (1).
