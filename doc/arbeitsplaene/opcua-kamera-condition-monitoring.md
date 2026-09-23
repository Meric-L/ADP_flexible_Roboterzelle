# Kamera-Zustandsüberwachung nach OPC 40100-2 (Condition Monitoring)

**Status:** fertig — 22.09.2026
**Verantwortlich:** `Agent: Kamerazustand als DeviceHealth (OPC 40100-2)`
**Thema:** opcua
**Branch:** feature/vision-server

## Ziel

Ein Client sieht über OPC UA, ob die Kamera arbeitet. Der Kamera-Watchdog
(`camera.py`, übernommen von Branch John in `c5207f1`, eigener Plan
[`livestream-mjpeg-und-watchdog.md`](livestream-mjpeg-und-watchdog.md))
erkennt hängende und ausbleibende Aufnahmen bereits — meldet das aber
ausschließlich ins Log. Dieser Plan macht daraus einen Wert in der
Anlagensicht nach OPC 40100-2, den ein generischer Client ohne Kenntnis
dieses Repos lesen kann.

Vorher blieb eine eingefrorene Kamera nach außen unsichtbar: der Livestream
sendete denselben alten Frame weiter, der Zustandsautomat blieb auf `Ready`,
und erst ein laufender Job scheiterte mit `DETECTION_FAILED`. Seit dem
Watchdog wird der Hänger erkannt und behoben — sichtbar war er über OPC UA
trotzdem nicht.

> **Doppelarbeit, offen dokumentiert.** Dieser Lauf hatte den Watchdog
> zunächst selbst von Branch John portiert, weil er bei Beginn noch nicht
> auf `feature/vision-server` lag. Parallel tat das ein zweiter Lauf. Die
> hier eingezogene Fassung ist **seine** (`c5207f1`); der eigene Port wurde
> verworfen. Übrig bleibt die Part-2-Schicht, die es sonst nirgends gibt.

## Bereits gelesen

- `doc/vision-server-interface.md` — Abschnitt 11 (OPC 40100-2: Anlagensicht),
  Abschnitt 10 (Kamera-Livestream)
- `doc/vision-system.md` — Modultabelle, Adressraum, Laufzeitverhalten, Starten
- `doc/altlasten.md` — A3 (`RaspiDevice/Counter`, entfallenes Lebenszeichen),
  D5 (systemd-Unit nicht versioniert)
- `doc/vision-system-next-steps.md` — Stand und verifizierte Fakten
- `doc/arbeitsplaene/README.md` — Doppelarbeits-Check: kein Plan auf `in Arbeit`,
  das Thema ist frei
- `src/vision_server/nodesets/README.md` — gepinnte Nodeset-Versionen

## Betroffene Dateien

- `src/vision_server/camera.py`
- `src/vision_server/camera_health.py` (neu)
- `src/vision_server/asset_model.py`
- `src/vision_server/nodeset_ids.py`
- `src/vision_server/profiles.py`
- `src/vision_server/runner.py`
- `tools/measure_nodeset_import.py`
- `tests/test_shared_camera.py` (neu), `tests/test_camera_health.py` (neu)
- `tests/test_asset_model.py`, `tests/test_camera_stream.py`, `tests/test_camera_owner.py`
- `doc/vision-server-interface.md`, `doc/vision-system.md`, `doc/altlasten.md`

## Schnittstellen

### OPC UA

Beide Knoten liegen im Vision-Namensraum (`http://launch-rm.de/vision`) und
tragen feste String-NodeIds. Der Bildsensor-Knoten existiert nur, wenn
`AssetConfig.image_sensor_model` gesetzt ist; der Wurzelknoten immer, sobald
Part 2 geladen ist.

| Element | NodeId | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `DeviceHealth` (Anlage) | `ns=<vision>;s=VisionMachine.VisionAsset.Health.DeviceHealth` | `Int32` (`DeviceHealthEnumeration`) | Lesen / Abo | Zustand des Vision-Systems |
| `DeviceHealth` (Kamera) | `ns=<vision>;s=VisionMachine.VisionAsset.ImageSensor.Health.DeviceHealth` | `Int32` (`DeviceHealthEnumeration`) | Lesen / Abo | Zustand der Kamera |

BrowsePaths ab `VisionMachine`: `<amcm>:VisionAsset` → `<amcm>:Health` →
`<di>:DeviceHealth` bzw. `<amcm>:VisionAsset` → `<amcm>:ImageSensors` →
`<amcm>:ImageSensor` → `<amcm>:Health` → `<di>:DeviceHealth`.
`<amcm>` = `http://opcfoundation.org/UA/MachineVision/AMCM/`,
`<di>` = `http://opcfoundation.org/UA/DI/`. Indizes zur Laufzeit über
`get_namespace_index` auflösen, nie hartkodieren.

`Health` hängt per **`HasAddIn`** an seinem Besitzer (nicht `HasComponent` —
asyncua setzt das beim Instanziieren falsch und es wird nachträglich getauscht).
Typdefinition: `<amcm>:VisionHealthInfoType`.

Werte (DI `DeviceHealthEnumeration`, NAMUR NE 107):

| Wert | Name | Wann |
| --- | --- | --- |
| 0 | `NORMAL` | Frisches Kamerabild |
| 1 | `FAILURE` | Kamera nie geöffnet, oder endgültig aufgegeben (Prozess endet gleich) |
| 2 | `CHECK_FUNCTION` | Warmup, oder die Kamera wird gerade neu geöffnet |
| 3 | `OFF_SPEC` | Bild älter als `stale_frame_s` — Hänger erkannt, noch nicht eskaliert |
| 4 | `MAINTENANCE_REQUIRED` | wird nie geschrieben (kein Verschleißzähler vorhanden) |

Geschrieben wird **nur bei Zustandswechsel**. Ein Abo sieht also echte
Übergänge; im Normalbetrieb ist der Knoten nach dem ersten Frame still.

Nicht instanziiert und damit auch nicht abonnierbar: `DeviceHealthAlarms`,
`Health/State` (SEMI E10), `Health/Temperature`, `Health/RemainingLifeTime`,
`Maintenance`.

### Python

```python
# Modul: src/vision_server/camera.py
@dataclass(frozen=True)
class CameraStatus:
    """Momentaufnahme dessen, was der Watchdog ohnehin schon weiss."""
    running: bool
    has_handle: bool
    last_outcome: str            # "none" | "ok" | "error" | "hung"
    consecutive_failures: int
    reopen_attempts: int
    gave_up: bool
    frame_age_s: float | None

class SharedCamera:
    @property
    def is_open(self) -> bool: ...
    def status(self, now: float) -> CameraStatus:
        """Zustand zum Zeitpunkt `now` (derselbe `loop.time()`-Zeitstrahl)."""
    def set_give_up_handler(
        self, handler: Callable[[], Awaitable[None] | None]
    ) -> None:
        """Ersetzt, was beim endgueltigen Aufgeben passiert."""

# Modul: src/vision_server/camera_health.py
def device_health(status: CameraStatus, *, stale_frame_s: float) -> DeviceHealth:
    """Bildet den Kamerazustand auf DI's DeviceHealthEnumeration ab. Rein."""

async def write_device_health(nodes: Sequence[Node], value: DeviceHealth) -> None:
    """Schreibt denselben Wert in alle Zustandsknoten. Wirft nie."""

class CameraHealthPublisher:
    """Spiegelt den Zustand einer `SharedCamera` nach `Health/DeviceHealth`."""
    def __init__(self, camera: SharedCamera, nodes: Sequence[Node],
                 config: CameraStreamConfig) -> None: ...
    @property
    def health(self) -> DeviceHealth: ...
    def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish_now(self) -> None: ...
    def give_up_handler(
        self, then: Callable[[], None]
    ) -> Callable[[], Awaitable[None]]:
        """Erst FAILURE schreiben (mit Zeitlimit), dann `then` -- z. B. os._exit."""

# Modul: src/vision_server/asset_model.py
async def _add_health(server, owner, amcm_idx, di_idx, own_idx) -> Node | None:
    """Haengt `Health` an eine Komponente, gibt deren `DeviceHealth` zurueck.
    Wirft nie -- die Anlagensicht darf den Job-Pfad nicht gefaehrden."""
```

`VisionAssetNodes` bekommt zusätzlich `device_health: tuple[Node, ...] = ()`.

### Daten / Payload

Keine. `DeviceHealth` ist ein skalarer `Int32`, kein JSON — anders als
`LatestResultJson` oder `CalibrationProgress`.

### Netz / Betrieb

Keine neuen Ports oder Dienste. Neue Felder in `CameraStreamConfig`
(`profiles.py`), Vorgabewerte von Branch `John`:

| Feld | Vorgabe | Bedeutung |
| --- | --- | --- |
| `frame_timeout_s` | 3.0 | So lange darf eine einzelne Aufnahme dauern |
| `max_capture_failures` | 3 | Fehler in Folge bis zum Neu-Öffnen |
| `max_reopen_attempts` | 2 | Neu-Öffnungen ohne Bild bis zum Prozessabbruch |
| `stale_frame_s` | 2.0 | Ab diesem Alter gilt ein Frame als veraltet |
| `overlay_timeout_s` | 2.0 | Zeitlimit für einen Overlay-Lauf |
| `health_interval_s` | 0.5 | Kadenz der Zustandsabfrage für `DeviceHealth` |

**Wichtig für den Betrieb:** Gibt der Watchdog endgültig auf, beendet sich der
Prozess mit `os._exit(1)` und verlässt sich darauf, dass systemd ihn neu
startet (`opcua-server.service`, `Restart=always`, `RestartSec=5`). Beim
Vordergrundstart in der Entwicklung gibt es dieses Netz nicht — dort ist der
Prozess dann einfach weg. Das ist gewollt, aber man muss es wissen.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Part 2 nicht geladen (`assets is None`) | Keine Health-Knoten, kein Publisher-Task. Der Watchdog läuft trotzdem |
| Zustandsknoten nicht anlegbar | `_add_health` loggt und gibt `None`; die Anlagensicht bleibt sonst vollständig |
| Kein `image_sensor_model` konfiguriert | Nur der Wurzelknoten trägt `Health` |
| Health-Knoten da, aber keine Kamera offen | Einmal `FAILURE`, kein Task |
| Schreibfehler am Knoten | Wird gefangen und geloggt, der Task läuft weiter |
| Kamera hängt | `OFF_SPEC` → Neu-Öffnen (`CHECK_FUNCTION`) → bei Erfolg `NORMAL` |
| Kamera hoffnungslos | `FAILURE`, dann Prozessabbruch und systemd-Neustart |
| Herunterfahren | Publisher wird **vor** Stream und Quellen gestoppt, damit das geordnete Schließen nicht als Hänger durchschlägt |

## Vorgehen

1. Diesen Plan anlegen, Zeile in `README.md`.
2. `profiles.py`: sechs Felder.
3. `camera.py`: `is_open` und idempotentes `open()` (aus `3a33640`), dann der
   Watchdog aus `18150a3`. Backend-Dispatch bleibt unberührt.
4. `camera.py`: Zähler auf Instanzebene, `CameraStatus`, `status()`,
   `set_give_up_handler`, awaitbarer `on_give_up`.
5. `camera_stream.py`: Stale-Erkennung und Overlay-Zeitlimit. `max_stream_width`
   und der `CalibrationProgress`-Block bleiben erhalten.
6. `nodeset_ids.py`: Konstanten und `DeviceHealth`-Enum.
7. `asset_model.py`: `_add_health` für Wurzel und Bildsensor.
8. `camera_health.py` samt Tests.
9. `runner.py`: `_start_camera_health`, Feld in `VisionMachine`, `aclose()`.
10. `tools/measure_nodeset_import.py` reparieren, Kosten messen.
11. Fachdoku nachziehen, Status hier auf `fertig`.

## Offene Fragen

- **Aggregation an der Wurzel.** Heute tragen Wurzel- und Sensorknoten denselben
  Wert: es gibt genau eine Zustandsquelle. Kommt eine zweite Komponente mit
  eigenem Zustand dazu, braucht die Wurzel eine echte Regel — und dafür eine
  Rangfolge über die NE-107-Zustände, die DI nicht definiert. Bewusst nicht
  vorweggenommen.
- **Schwelle für `OFF_SPEC` statt `FAILURE`.** Ein veraltetes Bild ist streng
  genommen ein ungültiges Ausgangssignal. Gewählt wurde `OFF_SPEC`, weil Hänger
  sich nachweislich selbst heilen und ein HMI sonst nicht zwischen „hakt
  gerade" und „tot" unterscheiden könnte. Änderbar in genau einer Tabellenzeile
  in `camera_health.device_health()`.
- **Altlast D5** (systemd-Unit nirgends versioniert) wiegt jetzt schwerer: der
  harte Prozessabbruch verlässt sich auf `Restart=always` in einer Datei, die
  niemand im Repo nachlesen kann.
- **`Maintenance`** (`VisionMaintenanceInfoType`) wäre der nächste Teil von
  Part 2. Ohne Wartungsintervalle und Kalibrierhistorie gäbe es aber nichts
  hineinzuschreiben.

## Abweichungen vom Plan

- **`health_interval_s` ist 0,5 s statt der geplanten 1,0 s.** Der Wert war
  geraten; beim Nachrechnen zeigte sich, dass das `OFF_SPEC`-Fenster nur
  `frame_timeout_s − stale_frame_s` = 1,0 s breit ist. Bei sekündlicher
  Abfrage wäre es regelmäßig verfehlt worden. Ein Test
  (`DefaultsAreConsistentTest`) hält jetzt fest, dass die Vorgabewerte
  zueinander passen.
- **`camera.exit_process()` ist öffentlich** (bei John `_exit_process`).
  `runner.py` reicht die Funktion als Abschluss des `give_up_handler`
  weiter — ein modulübergreifender Import eines privaten Namens wäre
  schlechter.
- **`is_open` und das idempotente `open()` stammen aus Commit `3a33640`**,
  nicht aus `18150a3` wie zunächst angenommen. Ohne sie läuft `_reopen()`
  ins Leere; sie sind deshalb mitportiert.
- **Der Referenztausch braucht echte `ua.NodeId`-Objekte.** Mit der blanken
  Zahl (`ua.ObjectIds.HasAddIn` = 17604) wirft asyncuas `_to_nodeid` einen
  `ValueError`, weil es daraus einen `TwoByteNodeId` baut. Im ersten Anlauf
  lief das still in den `except`-Zweig, und `Health` hing weiter per
  `HasComponent`. Gefunden nur, weil der Adressraum von Hand geprüft wurde.
- **`tools/measure_nodeset_import.py` war kaputt** (zeigte noch auf
  `src/OPCUA/nodesets`) und ist mitrepariert — ohne das Werkzeug gäbe es
  keine gemessenen Zahlen für die Doku.
- **Der Kalibrier-Fortschritt wandert aus dem Frame-Zweig heraus.** Bei
  hängender Kamera will man am Fortschritt gerade sehen, dass die Session
  nicht weiterkommt.

### Nachtrag 23.09.2026 — der Meldeweg war nur zur Haelfte gebaut

Die erste Fassung bediente von DIs `IDeviceHealthType` nur die
Zustandsvariable. Die Norm sieht zwei Mitglieder vor; der zweite,
`DeviceHealthAlarms`, fehlte. Parallel bewertete der Livestream das Bildalter
ein zweites Mal und leerte seinen Knoten — eine zweite Wahrheit ueber "lebt
die Kamera", an der Companion Spec vorbei.

Nachgezogen:

- `DeviceHealthAlarms` mit `FailureAlarm`, `CheckFunctionAlarm` und
  `OffSpecAlarm`, gefeuert bei jedem Zustandswechsel, hoechstens einer aktiv.
  Emittiert von `VisionMachine` (dort abonnieren Clients ohnehin),
  `SourceNode` nennt die Komponente. Severity 900/500/300.
- `MaintenanceRequiredAlarmType` bleibt weg — ohne Verschleisszaehler koennte
  er nie feuern.
- Die Alarme liegen nur an der Wurzel: je Instanz 36 Knoten (gemessen), und
  Wurzel wie Bildsensor tragen denselben Wert.
- Livestream-Staleness (`_mark_stale`) ersatzlos entfernt. `stale_frame_s` ist
  damit eindeutig die OFF_SPEC-Schwelle der Aufnahme.
- Frontend (WSC-Repo): Ampel im Kamera-Panel aus `DeviceHealth`, weil das
  stehende Bild die Aussage nicht mehr traegt.

Gemessen: Anlagensicht 19 -> 23 (nur Variable) -> 132 Knoten (mit Alarmen),
Aufbau 0,151 -> 0,179 -> 0,327 s. Ende-zu-Ende gegen einen echten Client:
sieben Alarm-Events ueber ein echtes Abo.

**Bekannte Grenze:** `ConditionRefresh` ist nicht bedient — wer sich nach
einem Alarm verbindet, bekommt ihn nicht nachgeliefert und muss
`DeviceHealth` lesen. Steht so in 11.4.

### Gemessen

Desktop, asyncua 2.0.1, zwei Läufe je Variante:

| | Knoten der Anlagensicht | Aufbau |
| --- | --- | --- |
| ohne Zustandsblock | 19 | 0,150 s / 0,169 s |
| mit Zustandsblock | 23 | 0,179 s / 0,186 s |

Vier Knoten, rund 20 ms; die RSS-Differenz lag unter der Messauflösung.
Ende-zu-Ende gegen echte Knoten geprüft:
`CHECK_FUNCTION → NORMAL → OFF_SPEC → FAILURE`.

**Noch offen:** die Abnahme am Gerät. Der Weg über einen echten Hänger
(Flachbandkabel ziehen, Zustandswechsel und Zeiten in UaExpert beobachten)
steht aus — bis dahin ist bewiesen, dass die Zustandsmaschine rechnet, nicht
dass sie den realen Hänger sieht.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen nach `doc/vision-server-interface.md` (Abschnitt 11.4)
  und `doc/vision-system.md` übernommen.
