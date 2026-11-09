# Roboter-Wiederholgenauigkeit (ISO 9283) über die Kamerapose

**Status:** fertig — 09.11.2026 (an echter Hardware nicht getestet)
**Verantwortlich:** Meric, `Agent: Roboter-Wiederholgenauigkeit`
**Thema:** vision
**Branch:** `roboter-wiederholgenauigkeit` (dieses Repo und `webskillcomposition`, beide abgezweigt von `handauge-schachbrett-offline`)

## Ziel

Die Pose-Wiederholgenauigkeit des Roboters nach ISO 9283 (RP = l̄ + 3·S_l) wird
über die Kamerapose gemessen. Die Eye-in-Hand-Kamera sieht ein fest liegendes
ChArUco-Board (38 Marker, ein bis zwei leicht beschädigt). Der Roboter fährt eine
eingelernte Pose P immer wieder an, der Hand-Pi nimmt je Anfahrt ein Bild und eine
JSON-Aufzeichnung auf. Auswertung offline mit `repeatability_eval`.

Bewusst ohne Kamerakalibrierung und ohne Hand-Auge-Kalibrierung: aus den Ecken des
Boards wird nur die Homographie Bild → Board-Ebene berechnet, und daraus Mittelpunkt,
Drehung und Maßstab. Das reicht für eine Wiederholgenauigkeit, weil nur Streuung
zählt, nicht der absolute Fehler.

Grenzen:
- X und Y sind gut bestimmt.
- Z erscheint nur als relative Maßstabsänderung, nicht als Abstand in mm.
- Kippungen werden nicht getrennt; sie erscheinen als seitlicher Versatz.
- Gemessen wird die Kamerapose, nicht der TCP. Der Roboter meldet seine Flanschpose
  zum Vergleich mit (`actual.flangeInBase`).

## Bereits gelesen

- `CLAUDE.md`, `arbeitsplaene/README.md`, `arbeitsplaene/_vorlage.md`
- `vision-server-interface.md`, Abschnitte 18a (`SaveUseCaseImage`) und 19 als Muster für Methodenbeschreibung
- Code: `src/vision_server/repeatability_image.py`, `src/vision_server/runner/save_repeatability_image.py`,
  `src/vision_server/config.py` (`repeatability_dir`), `src/repeatability_eval/*.py`,
  `tests/test_repeatability.py`
- Frontend (nur lesen): `repeatability.ts`, `repeatabilityRecord.ts`, `useRepeatabilityRun.ts`,
  `components/RepeatabilityPanel.tsx`

## Betroffene Dateien

- Pi: `src/vision_server/repeatability_image.py`, `src/vision_server/runner/save_repeatability_image.py`,
  `src/vision_server/runner/install.py` (Anlage der Methode), `src/vision_server/config.py`
- Auswertung: `src/repeatability_eval/__init__.py`, `__main__.py`, `charuco.py`, `live.py`,
  `metrics.py`, `session.py`
- Tests: `tests/test_repeatability.py`
- Frontend (`webskillcomposition`): `frontend/src/features/cell-modules/model/repeatability.ts`,
  `repeatabilityRecord.ts`, `useRepeatabilityRun.ts`, `repeatability.test.ts`,
  `components/RepeatabilityPanel.tsx`; in `features/opcua-server/`: `model/repeatabilityMeasurement.ts`
  (+ `.test.ts`), Antwort-Parser in `model/useSaveRawImage.ts`, NodeId in `config/piServers.ts`,
  Einhängen in `components/AutolocateActions.tsx`

## Schnittstellen

### OPC UA

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SaveRepeatabilityImage` | `ns=<eigener Namespace>;s=<VisionSystemName>.SaveRepeatabilityImage` | Methode | Aufruf | Speichert das aktuelle Kamerabild einer Phase als PNG plus JSON und liefert die Live-Messung |

Die Methode wird nur angelegt, wenn eine Kamera vorhanden ist und
`repeatability_dir` nicht `None` ist (`install.py`, `_install_image_saves`).

**Eingangsargumente** (alle `String`):

| Name | Format |
| --- | --- |
| `Session` | `^[0-9A-Za-z_-]{1,64}$` |
| `Phase` | `warmlauf` \| `rauschen` \| `zyklen_auto` \| `zyklen_hand` |
| `MetadataJson` | JSON-Text des Clients, Schema `wsc.repeatability.record/1` (siehe Daten). Leer erlaubt; das Board steht unter `board`. |

**Ausgangsargumente:**

| Name | Typ | Bedeutung |
| --- | --- | --- |
| `FileName` | `String` | Pfad des PNG, relativ zum Repo; bei Fehlercode leer |
| `ResultJson` | `String` | JSON der Live-Messung (siehe unten); bei Fehlercode leer |
| `Error` | `Int32` | `VisionErrorCode` |

**Fehlercodes** (`VisionErrorCode`):

| Wert | Name | Auslöser |
| --- | --- | --- |
| 0 | `OK` | gespeichert |
| 1 | `INVALID_STATE` | kein frisches Kamerabild |
| 2 | `INVALID_ARGUMENT` | Session oder Phase ungültig; beim Speichern `ValueError` |
| 3 | `BUSY` | ein Job läuft |
| 6 | `INTERNAL` | PNG-Kodierung oder Schreibfehler (`OSError`) |

Die Codes 4, 5 und 7 liefert diese Methode nicht.

**Ablauf im Pi:** `BUSY`-Prüfung, Argumentprüfung, frisches Kamerabild holen,
Pi-Metadaten bauen, Live-Messung (`measure_for_save`), speichern. Ein Fehler in der
Live-Messung lässt das Speichern nie scheitern. Die Kamera-Controls (Belichtung, Gain)
werden bewusst nicht mitgespeichert, weil sie über den Frame nicht erreichbar sind,
ohne eine weitere Aufnahme auszulösen.

**`ResultJson` bei `OK`** (immer ein JSON-Objekt):

- Erfolg: `{"ok": true, "centerOnBoardMm": [x, y], "rotationDeg": …, "scalePxPerMm": …, "homographyRmsPx": …, "nCorners": …, "nMarkers": …}`
- Kein Erfolg: `{"ok": false, "grund": "…"}`, z. B. kein Board in den Metadaten, zu wenige Ecken, nicht berechenbare Homographie

### Python

```python
# Modul: src/vision_server/repeatability_image.py
SCHEMA = "adp.repeatability.image/1"
PHASES: dict[str, tuple[str, str]]  # phase -> (Ordnername, Dateipraefix)

def save_repeatability_image(
    image: Any, base_dir: Path, session: str, phase: str, client_meta: str,
    pi_meta: dict, measurement: dict | None, now: datetime | None = None,
) -> tuple[Path, int]:
    """Schreibt das Bild verlustfrei als PNG samt JSON; liefert (PNG-Pfad, Nummer).
    Wirft ValueError (Session/Phase), OSError (Schreibfehler). Nie ueberschreibend."""

def next_image_number(directory: Path, prefix: str) -> int:
    """Hoechste vorhandene Nummer plus 1."""
```

```python
# Modul: src/repeatability_eval/live.py
def measure_for_save(image: Any, client_meta: Any) -> dict:
    """Homographie-Kennwerte des Bildes. Wirft nie; bei Problemen {"ok": False, "grund": ...}."""
```

```python
# Modul: src/repeatability_eval/charuco.py
@dataclass(frozen=True)
class CharucoSpec:  # cols, rows (Anzahl FELDER), square_size_m, marker_size_m, dictionary, legacy_pattern
    def as_dict(self) -> dict: ...

def spec_from_dict(d: Any) -> CharucoSpec:
    """Client-Format -> Spec. ValueError bei fehlenden Feldern, Typfehlern, 0 < marker < square verletzt."""

def build_board(spec: CharucoSpec) -> Any:  # cv2.aruco.CharucoBoard
def detect_corners(gray, spec, board=None) -> tuple[np.ndarray, np.ndarray]:
    """(ids (N,) int, corners (N, 2) float64); leer, wenn nichts gefunden."""
def object_points_mm(ids, spec) -> np.ndarray:  # (N, 2) Boardkoordinaten in mm
```

```python
# Modul: src/repeatability_eval/metrics.py
def homography_metrics(ids, corners, spec, image_size: tuple[int, int]) -> dict:
    """Liefert centerOnBoardMm [x, y], rotationDeg, scalePxPerMm, homographyRmsPx, nCorners.
    ValueError bei weniger als 6 Ecken, nicht berechenbarer oder entarteter Homographie."""

def repeatability(points: np.ndarray) -> dict:
    """ISO 9283: rp = lMean + 3 * sL. Liefert centroid, lMean, sL, rp, stdPerAxis, n.
    ValueError bei weniger als 2 Messungen."""

def corner_noise(samples) -> dict[int, float]   # radiale Streuung je Ecken-ID (nur IDs mit >= 2 Bildern)
def noisy_ids(noise: dict[int, float], factor: float = 3.0) -> set[int]   # Streuung > factor x Median
def mean_corner_displacement(ids, corners, ref) -> float                 # NaN ohne gemeinsame IDs
```

```python
# Modul: src/repeatability_eval/session.py
def evaluate_session(
    folder: Path, *, exclude_ids: set[int] | None = None, write: bool = True
) -> dict:
    """Wertet eine Session aus (Schema adp.repeatability.result/1, siehe unten).
    Wirft ValueError bei keinen Aufnahmen oder fehlender Board-Angabe (client.board).
    Bei write=True zusaetzlich auswertung/ergebnis.json, messwerte.csv und Plots."""
```

CLI: `PYTHONPATH=src python -m repeatability_eval <session_ordner> [--exclude-ids 3,17] [--no-write]`.
Ausgabe auf der Konsole, Rückgabe 1 bei `ValueError` (Meldung auf stderr).

### Daten / Payload

**Ablage** (Konfiguration `repeatability_dir`, Standard `data/rohbilder/roboterwiderholgenauigkeit`):

```
data/rohbilder/roboterwiderholgenauigkeit/<Session>/
  01_warmlauf/     w_NNN.png + w_NNN.json
  02_rauschen/     r_NNN.png + r_NNN.json
  03_zyklen_auto/  a_NNN.png + a_NNN.json
  04_zyklen_hand/  h_NNN.png + h_NNN.json
  auswertung/      ergebnis.json, messwerte.csv, *.png (nach der Auswertung)
```

Bilder werden nie überschrieben; die Nummer ist die nächste freie.

**Bild-JSON** (`adp.repeatability.image/1`), neben jedem PNG:

```json
{
  "schema": "adp.repeatability.image/1",
  "session": "string",
  "phase": "warmlauf | rauschen | zyklen_auto | zyklen_hand",
  "index": "int, laufende Nummer in der Phase",
  "bild": "Dateiname des PNG",
  "gespeichert": "ISO-Zeit, lokal, Sekunden",
  "pi": "Pi-Metadaten: frame_timestamp, frame_age_s, image_shape, frame_id, vision_system_name, vision_system_id, hostname (nur wenn lesbar)",
  "client": "geparstes MetadataJson (Objekt)",
  "client_raw": "Text, nur wenn MetadataJson kein JSON-Objekt ist (dann fehlt client)",
  "messung": "Live-Messung (measure_for_save)"
}
```

**Client-Record** (`wsc.repeatability.record/1`, gesendet als `MetadataJson`). Die Feldnamen
sind fest; der Pi liest `board` für die Live-Messung:

```json
{
  "schema": "wsc.repeatability.record/1",
  "session": "string",
  "phase": "warmlauf | rauschen | zyklen_auto | zyklen_hand",
  "cycle": "int oder null; bei Warmlauf/Rauschen laufende Nummer",
  "burstIndex": "int ab 1",
  "burstCount": "int",
  "clientTime": "ISO-Zeit",
  "robot": "{robotId, name, model} oder null",
  "speed": {"fraction": "Anteil", "maxSpeed": "Roboter-Maximum", "value": "fraction x maxSpeed"},
  "settleS": "Beruhigungszeit s",
  "board": {"type": "charuco", "cols", "rows", "squareSizeM", "markerSizeM", "dictionary", "legacyPattern"},
  "taught": {"P": {"joints": [rad je Gelenk], "flangeInBase": Pose}, "V": Pose-Objekt oder null},
  "start": "{joints, flangeInBase} oder null (Stellung vor der Fahrt nach P)",
  "target": {"joints": [rad je Gelenk]},
  "actual": {
    "joints": "Mittel der Lesungen vor der Aufnahme",
    "flangeInBase": "Pose, Position in m, Basis",
    "settled": "bool, Lesungen ruhig genug zum Mitteln",
    "movedDuringCapture": "bool, erste und letzte Lesung vor der Aufnahme weichen ab",
    "jointSpreadRad": "float"
  },
  "warmupElapsedS": "float oder null",
  "settings": "vollständige Einstellungen des Laufs"
}
```

`Pose` = `{"position": [x, y, z] in m, "orientation": [x, y, z, w]}`.

**`ResultJson`/`messung`-Felder**: siehe Abschnitt OPC UA.

**`auswertung/ergebnis.json`** (`adp.repeatability.result/1`), wichtigste Schlüssel:

| Schlüssel | Bedeutung |
| --- | --- |
| `board` | Board-Spezifikation der Session |
| `basisIds` / `basisRegel` | Basis-Eckenmenge und ihre Regel |
| `ausgeschlosseneIds` / `streuendeIds` | per CLI ausgeschlossene bzw. durch Streuung > 3x Median verworfene Ecken |
| `ausgeschlosseneBilder` | je Bild `{phase, index, datei, grund}` |
| `serien.<phase>` | je Serie: `nAufnahmen`, `rpUm`, `lMeanUm`, `sLUm`, `stdPerAxisUm`, `rotationStdArcsec`, `scaleRelStdPpm`, `robotReported` (RP der gemeldeten Flanschpose, mm), `noiseRatio`, `valid`; bei weniger als 2 Aufnahmen `grund` und `rpUm: null` |
| `warmlauf` | Driftliste: `tS`, `centerMm`, `dToLastUm`, `dToNoiseMeanUm` |
| `nBilder` / `nVerwendet` | Anzahl gesamt und verwendet |
| `plots` | Dateinamen der Plots oder `matplotlib fehlt` |

**`auswertung/messwerte.csv`**: Spalten `phase, index, cycle, burst, nCorners, centerX_mm,
centerY_mm, rotationDeg, scalePxPerMm, homographyRmsPx, meanDisplacementPx, used, grund`.

**Plots** (nur mit matplotlib): `streu_<phase>.png`, `verlauf_<phase>.png`, `warmlauf_drift.png`.

### Netz / Betrieb

- Keine neuen Ports, Dienste oder systemd-Units.
- Konfiguration: `VisionServerConfig.repeatability_dir` (`None` = Methode nicht anlegen).
- Frontend-Einstellungen im localStorage: `wsc.repeatability.settings/1`, eingelernte Posen
  `wsc.repeatability.poseP` und `wsc.repeatability.poseV`.
- Auswertung läuft auf einem beliebigen Rechner mit numpy und OpenCV; matplotlib ist optional.

### Fehlerfälle

- Pi: `BUSY` bei laufendem Job, `INVALID_ARGUMENT` bei ungültiger Session/Phase,
  `INVALID_STATE` ohne frisches Bild, `INTERNAL` bei Schreib- oder Kodierfehler. Eine
  Live-Messung mit Problem liefert `ok: false` mit Grund, speichert aber das Bild.
- Frontend: Stopp-Knopf hält `go_to` an (`haltRobotAction`). Fehlende Vorpose V bricht den
  Auto-Lauf mit „Vorpose V fehlt.“ ab. Mehrere abgelehnte Aufnahmen in Folge beenden den Lauf.
- Auswertung: keine Aufnahmen oder keine Board-Angabe: `ValueError`. Bild nicht lesbar oder
  mit zu wenigen Basis-Ecken: wird ausgeschlossen, mit Grund in `ausgeschlosseneBilder`.
  Ohne Rauschserie gilt als Basis die Menge der Ecken, die in mindestens 90 % aller Bilder
  erkannt wurde; `noiseRatio` ist dann `null` und `valid` ist `false`.

## Vorgehen

### Messablauf (Frontend-Panel „Wiederholgenauigkeit“, unter dem Pick&Place-Panel beim Hand-Pi)

1. P und Vorpose V einlernen (Gelenkwinkel, localStorage). Board-Maße im Panel einstellen.
2. Warmlauf (ISO 9283): P und V wechselnd mit der Testgeschwindigkeit anfahren, Standard 30 min.
   Alle 5 Zyklen ein Bild am Ziel P. Ergebnis ist eine Driftkurve.
3. Rauschserie: 30 Bilder ohne Bewegung, 1 s Abstand. Liefert Messunsicherheit und Referenz.
4. Automatische Zyklen (Standard 30): immer aus V nach P angefahren, das ist die ISO-RP.
5. Hand-Zyklen (optional): der Bediener fährt den Roboter weg, Knopf „Zurück zu P + Aufnahme“.
   Dabei geht das Umkehrspiel ein; die Serie wird getrennt ausgewertet.
6. Je Zyklus: 2 s Beruhigung, dann ein Burst von 3 Bildern. Gelenke werden vor und nach der
   Aufnahme gelesen.
7. Geschwindigkeit: Anteil (Standard 50 %) der eingestellten Maximalgeschwindigkeit.
8. Auswertung: `PYTHONPATH=src python -m repeatability_eval <session_ordner>`.

### Auswertungsregeln (`repeatability_eval/session.py`)

- Basis-Eckenmenge: alle Ecken, die in allen Rauschbildern (mit Erkennung) erkannt wurden, minus Ecken mit
  Streuung > 3x Median, minus `--exclude-ids`.
- Ein Bild wird nur verwendet, wenn mindestens 90 % der Basis-Ecken erkannt wurden und
  mindestens 6 Ecken vorliegen.
- Burst-Mittelung je Zyklus: ein neuer Zyklus beginnt bei `burstIndex` 1 oder bei neuer
  `cycle`-Nummer. Ein zweiter Lauf in derselben Session wird dadurch nicht mit dem ersten
  gemittelt.
- Gültigkeit: `noiseRatio = RP_Rauschen / RP_Zyklen`, gültig bei `<= 0,2`.
- Zum Vergleich wird die robotergemeldete RP aus `actual.flangeInBase` berechnet.
- Beschädigte Tags: fallen zu viele Ecken weg oder bleibt das Rauschen zu hoch, wird das
  Board neu gedruckt.

## Offene Fragen

- Standard im Panel ist das Labor-ChArUco-Brett (auch Hand-Auge-Brett, `handauge-charuco-auswahl.md`):
  11 x 7 Felder, 25 mm Feld, 18,5 mm Marker, `DICT_5X5_100`, 38 Marker, 60 innere Ecken. Bei 7 Zeilen
  (ungerade) spielt `legacyPattern` keine Rolle. Ein anderes Brett im Panel einstellen.
- Kamera-Belichtung und Gain werden nicht mitgespeichert. Vor der Messung manuell fixieren.
- `movedDuringCapture` im Record stammt nur aus den Lesungen vor der Aufnahme. Die Nachlesung
  erscheint nur im Frontend.
- Kippungen werden nicht getrennt; Z nur als relative Maßstabsänderung.
- Ob die Wiederholgenauigkeit an echter Hardware die Erwartung erfüllt, ist ungeprüft.

## Abweichungen vom Plan

- Tests: `tests/test_repeatability.py` enthält 22 Tests (unittest, synthetische, leicht weichgezeichnete
  ChArUco-Bilder: Subpixel-Verschiebung auf 0,01 mm, beschädigter Tag, Session-Auswertung). Frontend:
  `repeatability.test.ts` (Board- und Einstellungsprüfung, Warmlauf-Planung, Drift, Formatierung,
  Settings-Parser, Record) und `repeatabilityMeasurement.test.ts` (ResultJson- und Ausgabe-Parser,
  Fehlertexte). Der Lauf auf dem Roboter ist nicht automatisch getestet.
- Frontend: zusätzliche Einstellung `handCycles` (Ziel der Hand-Zyklen). `cycle`/`burstIndex` zählen ab 1;
  Warmlauf und Rauschen haben `burstCount` 1.
