# Part-10-Programm vervollständigen: alle Job-Eingaben und Abbruch

**Status:** geplant — 21.09.2026
**Verantwortlich:** `Agent: Part-10-Programm vervollständigen`
**Thema:** opcua
**Branch:** `worktree-part10-wrapper-mdns` → `feature/vision-server`

## Ziel

Der **Job-Pfad** des Vision-Servers wird vollständig über das Part-10-Programm
bedienbar. Heute erreicht ein Part-10-Client nur `RecipeId` und den
Einzel-/Dauerbetrieb; vier der fünf Job-Eingaben und der Abbruch über `Abort`
fehlen.

Die **Systemebene** (Vision-System anhalten und wieder betriebsbereit machen)
wird bewusst **nicht** über Part 10 gespiegelt — Begründung unter „Abgrenzung".

## Bereits gelesen

- `doc/part10-programm-schnittstelle.md` — der bestehende Wrapper, Adressraum,
  Zustände, Events, Fehlercodes
- `doc/vision-server-interface.md` — die 40100-Schnittstelle, gegen die der
  Wrapper arbeitet; Abschnitte 5 (`StartSingleJob`, `Stop`) und 7
- `doc/vision-system.md` — Ist-Stand; kennt den Part-10-Wrapper **noch nicht**,
  muss nach Abschluss nachgezogen werden
- `betreuer/OPC_UA_Program.py`, `betreuer/testProgram.py` — die Vorlage, deren
  Struktur wir übernehmen
- Nachgesehen am laufenden Conveyor (`opc.tcp://10.10.38.41:4840/conveyor/`):
  `RunContinuous`, `MoveDistance`, `MoveUntilSensor` — je ein Programm pro
  Aktion, `Start()` ohne Argumente, Eingaben im `ParameterSet`

## Betroffene Dateien

- `src/vision_server/vision_program.py` — ParameterSet erweitern, zweites
  Programm für den Abbruch
- `src/vision_server/runner.py` — Einbau des zweiten Programms
- `tests/test_vision_program.py` — **neu**
- `doc/part10-programm-schnittstelle.md` — Schnittstelle nachziehen
- `doc/vision-system.md` — Ist-Stand um den Part-10-Zweig ergänzen

## Schnittstellen

### OPC UA

Namespace `http://launch-rm.de/vision` (aktuell ns=7, **immer über die URI
auflösen**). Alle NodeIds sind sprechende String-Ids.

#### Bestehend, bleibt unverändert

| Element | NodeId | Typ | Richtung |
| --- | --- | --- | --- |
| Programm | `ns=<vision>;s=VisionProgram` | `ProgramStateMachineType` (`ns=0;i=2391`) | — |
| Zustand | `…;s=VisionProgram` → Kind `0:CurrentState` | LocalizedText | Lesen |
| Start | `ns=<vision>;s=VisionProgram.Start` | Methode, keine Argumente | Aufruf |
| Halt | `ns=<vision>;s=VisionProgram.Halt` | Methode, keine Argumente | Aufruf |
| Reset | `ns=<vision>;s=VisionProgram.Reset` | Methode, keine Argumente | Aufruf |
| Rezept | `…;s=VisionProgram.ParameterSet.RecipeId` | String, **beschreibbar** | Schreiben |
| Dauerbetrieb | `…;s=VisionProgram.ParameterSet.Continuous` | Boolean, **beschreibbar** | Schreiben |
| JobId | `…;s=VisionProgram.ResultSet.JobId` | String | Lesen |
| Fehlercode | `…;s=VisionProgram.ResultSet.ErrorCode` | Int32 | Lesen |
| Ausführungsart | `…;s=VisionProgram.ResultSet.ExecutionMode` | String | Lesen |

#### Neu: vier Eingaben im `ParameterSet`

| Element | NodeId | Typ | Grenze | Bedeutung |
| --- | --- | --- | --- | --- |
| MeasId | `…;s=VisionProgram.ParameterSet.MeasId` | String, beschreibbar | ≤ 128 Zeichen | Messauftrag; leer erlaubt |
| PartId | `…;s=VisionProgram.ParameterSet.PartId` | String, beschreibbar | ≤ 128 Zeichen | Werkstück; leer erlaubt |
| ProductId | `…;s=VisionProgram.ParameterSet.ProductId` | String, beschreibbar | ≤ 128 Zeichen | Produkt; leer erlaubt |
| Parameters | `…;s=VisionProgram.ParameterSet.Parameters` | String**[]**, beschreibbar | ≤ 16 Einträge | Rezeptspezifische Schalter, z. B. `force-error` |

Die Grenzen stammen aus `VisionServerConfig.max_id_length` (128) und
`max_parameters` (16) und werden **nicht** im Programm dupliziert, sondern von
`build_job_request()` geprüft. Verletzung ⇒ `ErrorCode=2`
(`INVALID_ARGUMENT`), kein Zustandswechsel.

#### Neu: zweites Programm für den Abbruch

| Element | NodeId | Typ |
| --- | --- | --- |
| Programm | `ns=<vision>;s=AbortJob` | `ProgramStateMachineType` |
| Start | `ns=<vision>;s=AbortJob.Start` | Methode, keine Argumente |

`AbortJob.Start()` bricht einen laufenden Job über den **Abort**-Übergang ab
(`JobRunner.stop(abort=True)`), im Unterschied zu `VisionProgram.Halt()`, das
den **Stop**-Übergang nimmt. Das Programm kehrt sofort nach `Ready` zurück; es
ist eine Aktion, kein länger laufender Ablauf.

Grund für ein eigenes Programm statt eines Schalters im `ParameterSet`: Die
Zelle modelliert **Aktionen als Programme** (Conveyor: `MoveDistance`,
`MoveUntilSensor`) und **Eingaben als Parameter**. Ein Boolean `AbortOnHalt`
wäre ein Modus, kein Parameter — siehe „Offene Fragen".

#### Methodensignaturen

Alle Methoden beider Programme: **keine Eingangsargumente, keine
Rückgabewerte** (Part-10-Konvention, wie bei den übrigen Zellmodulen).
Rückmeldung ausschließlich über Zustandswechsel und `ProgramTransitionEvent`
(`ns=0;i=2378`, emittiert vom jeweiligen Programmknoten).

Ablehnung eines Aufrufs: **kein** Zustandswechsel, stattdessen ein Event mit
`Severity=500` und Klartext in `Message`; maschinenlesbar in
`ResultSet/ErrorCode`.

### Python

```python
# Modul: src/vision_server/vision_program.py

class VisionProgram(Program):
    """Part-10-Sicht auf den Vision-Job. Ruft denselben JobRunner wie 40100."""

    async def start(self) -> str | None:
        """Liest das komplette ParameterSet und startet den Job.

        Rueckgabe None = angenommen (Ready -> Running); ein String = abgelehnt
        (Zustand bleibt, Text reist als ProgramTransitionEvent).
        """

    async def _read_parameters(self) -> tuple[str, str, str, str, tuple[str, ...]]:
        """(meas_id, part_id, recipe_id, product_id, parameters).

        Ein nicht lesbarer Knoten faellt auf den Standardwert zurueck und
        verhindert den Start nicht -- geloggt, nicht geworfen.
        """


class AbortJobProgram(Program):
    """Bricht den laufenden Job ueber den Abort-Uebergang ab."""

    async def start(self) -> str | None:
        """`JobRunner.stop(abort=True)`; kehrt sofort nach Ready zurueck.

        Kein laufender Job ist kein Fehler -- liefert None wie ein
        erfolgreicher Abbruch (fire-and-forget, wie 40100 `Abort`).
        """


async def install_vision_program(
    server: Server,
    parent: Node,
    own_idx: int,
    jobs: JobRunner,
    *,
    known_recipes: frozenset[str] | None = None,
    mirror_nodes: dict[str, Node] | None = None,
) -> tuple[VisionProgram, AbortJobProgram]:
    """Haengt beide Programme neben das Vision-System.

    Rueckgabe aendert sich von `VisionProgram` auf ein Tupel -- einziger
    Bruch an einer internen Signatur; einziger Aufrufer ist `runner.py`.
    """
```

Unverändert bleibt `JobRunner` — das Programm ruft nur, was 40100 auch ruft:
`start_single_job(...)`, `start_continuous(...)`, `stop()`, `stop(abort=True)`,
`cancel_running()`.

### Daten / Payload

**Unverändert.** Schema `wsc.vision.detections/1` in
`ns=<vision>;s=VisionMachine.LatestResultJson`, unter `VisionProgram/ResultSet`
weiterhin nur **verlinkt**, nicht kopiert.

Die neuen Eingaben landen unverändert im Job und erscheinen dort, wo 40100 sie
heute schon ablegt — das Payload bekommt **kein** neues Feld.

### Netz / Betrieb

Unverändert: Port 4840, Endpoint `opc.tcp://<pi>:4840/raspi/server/`,
mDNS-Dienst `_opcua-tcp._tcp.local.`, ApplicationUri
`urn:plcm:camera-server:ceiling-01` bzw. `:roboter-hand-01`.

**Zusatzbefund aus dem Betrieb (21.09.2026):** Ein fehlendes `zeroconf` lässt
den Server normal starten und erzeugt nur eine Warnung — der Ausfall der
mDNS-Ankündigung ist am laufenden Server nicht erkennbar. Genau dieser Fall ist
auf dem Hand-Pi eingetreten. Ob und wo der mDNS-Zustand sichtbar gemacht wird,
steht unter „Offene Fragen"; ins Vision-Programm gehört er nicht.

### Fehlerfälle

| Fall | `ErrorCode` | Zustand danach | Rückmeldung |
| --- | --- | --- | --- |
| Vision-System nicht betriebsbereit | 1 `INVALID_STATE` | bleibt | Event, Severity 500 |
| Id zu lang (>128) oder zu viele Parameter (>16) | 2 `INVALID_ARGUMENT` | bleibt | Event, Severity 500 |
| Es läuft bereits ein Job | 3 `BUSY` | bleibt | Event, Severity 500 |
| Unbekannte `RecipeId` | 4 `UNKNOWN_RECIPE` | bleibt | Event, Severity 500 |
| Erkennung fehlgeschlagen / Timeout | 5 `DETECTION_FAILED` | zurück nach `Ready` | Zustandswechsel |
| Serverfehler | 6 `INTERNAL` | zurück nach `Ready` | Zustandswechsel |
| Abbruch über `Halt` bzw. `AbortJob` | 7 `CANCELLED` | `Halted` bzw. `Ready` | Zustandswechsel |
| `AbortJob.Start()` ohne laufenden Job | 0 `OK` | `Ready` | Zustandswechsel |
| Kamera fehlt beim Serverstart | — | Programm startet in `Halted` | — |

## Abgrenzung: die Systemebene bleibt bei 40100

Nicht Teil dieses Plans: `halt_system` und `reset_system` aus `runner.py`, die
den **Automaten des Vision-Systems** schalten (`states.halt()`,
`states.enter_operational()`).

Begründung: Ein Part-10-Programm modelliert eine **Aufgabe**, kein **Gerät**.
Für den Gerätezustand sind DI und Machinery zuständig — dieselben Nodesets, die
Conveyor und CardDispenser laden und die wir über Part 2 (AMCM) bereits im
Adressraum haben. Ein zweiter Part-10-Automat für die Systemebene ließe zwei
Automaten dieselbe Wahrheit behaupten; genau das vermeidet der Wrapper heute.

Folge für Clients: System anhalten und wieder betriebsbereit machen geht
weiterhin nur über die 40100-Methoden. Das gehört so in
`part10-programm-schnittstelle.md`.

## Vorgehen

1. `ParameterSet` um `MeasId`, `PartId`, `ProductId`, `Parameters` erweitern;
   `Parameters` als String-Array anlegen und beschreibbar setzen.
2. `VisionProgram.start()` auf `_read_parameters()` umstellen und alle fünf
   Werte an den `JobRunner` durchreichen.
3. `AbortJobProgram` ergänzen, `install_vision_program()` gibt beide zurück,
   `runner.py` hält beide in `VisionMachine`.
4. Tests in `tests/test_vision_program.py`: Parameter kommen im Job an, zu
   lange Id wird mit 2 abgelehnt, zu viele Parameter ebenso, Abort beendet
   einen laufenden Job, Abort ohne Job ist `OK`.
5. `doc/part10-programm-schnittstelle.md` nachziehen (Adressraum, Ablauf,
   Abgrenzung der Systemebene).
6. `doc/vision-system.md` um den Part-10-Zweig ergänzen — der Ist-Stand kennt
   ihn bisher nicht.

## Offene Fragen

- **`Continuous` als Parameter oder als eigenes Programm?** Der Conveyor hat
  `RunContinuous` als **eigenes Programm**. Strukturtreuer wäre also ein
  zweites Programm `DetectContinuous` statt des Booleans. Dagegen spricht, dass
  die aktuelle Form bereits dokumentiert und an den Backend-/Frontend-Agenten
  übergeben ist. Änderung nur bewusst und mit Doku-Update.
- **`AbortJob` als Programm oder Schalter?** Hier als Programm geplant. Falls
  das Team einen Schalter `ParameterSet/AbortOnHalt` bevorzugt, entfällt das
  zweite Programm.
- **mDNS-Zustand sichtbar machen?** Ein fehlendes `zeroconf` bleibt heute
  unsichtbar. Lohnt sich eine Variable, die meldet, ob angekündigt wurde — und
  wo gehört sie hin, wenn nicht ins Vision-Programm?
- **`ApplicationName` je Pi?** Beide Pis melden `Raspberry Pi OPC UA Server`.
  Der Aggregation-Server geht über die ApplicationUri, eine Oberfläche
  womöglich über den Namen. Umbenennen würde die Verbindungsdaten in
  `vision-server-interface.md` ändern.

## Abweichungen vom Plan

_(wird während der Umsetzung gefüllt)_

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- `doc/part10-programm-schnittstelle.md` und `doc/vision-system.md` auf den
  neuen Stand gebracht — der Plan ist die Planung, die Fach-MD der Stand.
