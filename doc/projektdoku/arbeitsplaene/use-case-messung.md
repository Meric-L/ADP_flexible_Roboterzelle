# Use-Case-Messung: Ablage des Würfels aus dem Handkamera-Bild (Δx, Δy, Δθ)

**Status:** in Arbeit — 29.10.2026
**Verantwortlich:** Meric, `Agent: Use-Case-Messung`
**Thema:** vision
**Branch:** `usecase-foto`

## Ziel

Nach jeder Ablage des Würfels in Use Case 1 wird die Lage des Würfels aus dem
Foto der Handkamera bestimmt: Abweichung in x und y (mm) und Drehung Δθ (°)
gegenüber der Mitte einer gedruckten Zielscheibe. Die Messung läuft live nach
dem Foto (in `SaveUseCaseImage`) und offline für ganze Reihen
(`tools/use_case_auswertung.py`). Daraus entstehen Genauigkeit und
Wiederholgenauigkeit der Ablage nach ISO 9283 (angelehnt). Vorher wurde die
Ablage nur mit Auge beurteilt, Zahlen gab es keine.

Die Zielscheibe behält ihre Toleranzkreise, damit die Ablage auch ohne Messung
sichtbar bewertet werden kann.

## Bereits gelesen

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/README.md`, `_vorlage.md`, `use-case-foto.md`
- `doc/projektdoku/vision-server-interface.md` (Abschnitt 18a SaveUseCaseImage)
- Code: `src/use_case_eval/` (`target.py`, `measure.py`, `live.py`, `synthetic.py`),
  `tools/make_use_case_target.py`, `tools/use_case_auswertung.py`,
  `src/vision_server/use_case_image.py`, `_install_save_use_case_image` und
  `_build_use_case_pi_meta` in `src/vision_server/runner.py`

## Stand

- Code und Tests liegen auf Branch `usecase-foto` (nicht gepusht). Die Tests
  `tests.test_use_case_eval` und `tests.test_use_case_image` sowie die gesamte
  Suite laufen grün.
- Frontend (Fotopose, Anzeige der Messung) ist umgesetzt: webskillcomposition,
  Branch `usecase-foto`, siehe Abschnitt „Kamerapose“.
- Am realen Aufbau ist noch nichts gemessen. Die Genauigkeitswerte unten stammen
  aus synthetischen Bildern.

## Betroffene Dateien

- `src/use_case_eval/target.py` (neu): Geometrie der Zielscheibe, einzige Quelle
- `src/use_case_eval/measure.py` (neu): Bildmessung, Kennzahlen, Aufkleber-Versatz
- `src/use_case_eval/live.py` (neu): Anbindung an `SaveUseCaseImage`
- `src/use_case_eval/synthetic.py` (neu): synthetische Testbilder
- `src/use_case_eval/__init__.py` (neu)
- `tools/make_use_case_target.py` (neu): erzeugt die Zielscheibe
- `tools/use_case_auswertung.py` (neu): Offline-Auswertung einer Reihe
- `src/vision_server/runner.py`: `_install_save_use_case_image` misst vor dem Speichern
- `src/vision_server/use_case_image.py`: Feld `messung`, Kontrollbild
- `tests/test_use_case_eval.py` (neu), `tests/test_use_case_image.py`
- `data/use_case_target/` (generiert), `data/use_case/` (Versuche, nicht im Git)
- `doc/projektdoku/vision-server-interface.md` (Abschnitt 18a)

## Zielscheibe v2

Die Geometrie steht in `src/use_case_eval/target.py`. Alle Angaben in mm,
Blattkoordinaten: Ursprung in der Scheibenmitte, x zeigt nach 0°, y nach 90°
(im Ausdruck nach oben), z nach oben aus dem Blatt.

| Element | Angabe |
| --- | --- |
| Skalenring | Radius 80 bis 92 mm |
| Toleranzkreise | Ø 30,3 (grün), 34,3 (gelb), 38,3 (rot) mm |
| Würfel | 20 mm, gestricheltes Quadrat in der Mitte |
| Blattmarker | ArUco `DICT_4X4_50`, IDs 0 bis 3, je 20 mm, Mittelpunkte bei (−45, +45), (+45, +45), (+45, −45), (−45, −45) mm, Kanten parallel zu den Blattachsen |
| Würfelaufkleber | ArUco `DICT_4X4_50`, ID 10, 18 mm, mit 20-mm-Schnittrahmen |

- **Warum ArUco und nicht AprilTag 36h11:** Die Zellen-Pipeline (Layer 1/2,
  Hand-Auge, Tag-Map) erkennt `tag36h11`. Mit ArUco sieht sie die Zielscheibe
  nicht, und umgekehrt.
- Um jeden Blattmarker liegt eine weiße Ruhezone von einer Markerzelle. Winkelskala,
  Strahlen und Teilstriche sind dort ausgespart.
- Die Zielscheibe hat eine Legende und einen 50-mm-Maßstab.
- **Zonen:** Die äußerste Würfelecke wird gegen die Radien 15,15 / 17,15 / 19,15 mm
  geprüft. Liegt sie innerhalb 15,15 mm, ist die Zone `gruen`; innerhalb 17,15 mm
  `gelb`; innerhalb 19,15 mm `rot`; sonst `ausserhalb`. Das entspricht dem, was
  man auf dem Blatt sieht.

### Erzeugen

```bash
PYTHONPATH=src python3 tools/make_use_case_target.py --pdf
```

Ausgabe nach `data/use_case_target/`: `zielscheibe.svg`, `wuerfelmarker.svg`
und mit `--pdf` die PDFs (braucht `inkscape` oder `rsvg-convert`). Die SVG
tragen echte Millimeter. Optional `--stickers N` für die Anzahl der
Aufkleber auf Seite 2 (Standard 12), `--out DIR` für einen anderen Ordner.

### Drucken

- Ausdruck in **100 %**: Im Druckdialog „Tatsächliche Größe“ bzw. Skalierung
  100 % wählen, keine Anpassung an die Seite.
- Den 50-mm-Maßstab auf dem Ausdruck mit dem Lineal nachmessen. Weicht er ab,
  wird nicht gemessen, sondern neu gedruckt.

## Aufbau

- **Blatt:** Die Blattmitte liegt auf dem Soll-Ablagepunkt der App. Die
  0°-Achse liegt parallel zur Tisch-x-Kante.
- Die Messung ist relativ zum Blatt. Ein Versatz des Blatts gegenüber der
  App-Ablage verschiebt alle Messwerte gleich. Das geht in die **Genauigkeit**
  (AP) ein, nicht in die Wiederholgenauigkeit (RP). Deshalb das Blatt bei jeder
  Reihe gleich legen und fixieren.
- **Würfelaufkleber:** mittig auf die Oberseite kleben, Versatz höchstens etwa
  1 mm. Der 18-mm-Marker muss ganz auf der 20-mm-Oberseite liegen. Sonst wird
  die Kante abgeschnitten gemessen.
- **Aufkleber-Versatz kalibrieren:** Der Aufkleber sitzt nie exakt mittig.
  Der Versatz ist ein konstanter Fehler und wird einmal bestimmt:
  1. Würfel auf die Ablage legen und messen (Versuch N1).
  2. Würfel von Hand um 180° drehen, am selben Punkt ablegen und messen (Versuch N2).
  3. Ausführen:
     ```bash
     python3 tools/use_case_auswertung.py data/use_case/reihe_NNN --aufkleber-kalibrieren N1 N2
     ```
     Das schreibt `data/use_case/aufkleber_versatz.json`. Die Argumente sind
     Versuchsnummern oder Bilddateien der Reihe.
  Für bestehende Reihen danach mit `--neu-messen` neu auswerten.

### Kamerapose (Frontend, Branch `usecase-foto`, webskillcomposition)

- Die Handkamera steht **20 cm senkrecht über der Soll-Mitte** (Ablagepunkt).
  Bezug ist die Tischoberfläche bzw. das Blatt: optisches Zentrum bei
  `placeInBase + (0, 0, 0,20 m)`, also 18 cm über der Würfeloberseite.
  Die optische Achse zeigt nach unten, Bild-x ist parallel zur Tisch-x-Achse.
- Die Pose kommt aus `poseFlangeCam` der Hand-Auge-Kalibrierung. Sie ist fest
  und wird zur Laufzeit nicht geregelt (keine Bildregelung).
- Der Flansch wird aus der Kamerapose berechnet:
  `flange = composePoses(cameraInBase, invertPose(flangeToCamera))`.
- Ist die Pose nicht erreichbar, probiert der Ablauf eine Drehung der Kamera um
  die optische Achse von 90°, −90° und 180°. Für die Messung ist die Drehung
  unkritisch.
- Ohne Hand-Auge-Kalibrierung ist der Start gesperrt (Meldung „Keine
  Hand-Auge-Kalibrierung auf dem Hand-Pi“).
- **Warum über der Mitte:** Der Fußpunkt der optischen Achse liegt dann auf der
  Würfeloberseite. Es ist keine Seitenfläche sichtbar, die Oberseite ist also
  eben und vollständig im Bild. Bei 20 cm Höhe und 640 × 480 px ergibt das etwa
  0,44 mm je Pixel und ein Bildfeld von etwa 280 × 210 mm.

## Messverfahren

`measure_trial` in `src/use_case_eval/measure.py`:

1. **ArUco-Erkennung:** `tagloc.detector.build_detector("aruco_4x4_50", …)` mit
   Subpixel-Ecken. Bei doppelten IDs gewinnt der erste Fund.
2. **Kantenverfeinerung** (Marker der Blatt- und Würfelmarker):
   - Querprofile senkrecht zu jeder Kante, bilinear abgetastet.
   - Die Kantenlage folgt aus der Fläche unter dem normierten Profil, nicht aus
     einem Gradientenmaximum. Das ist unabhängig von der Phase der Kante zum
     Pixelraster.
   - Je Kante eine Gerade per robustem Ausreißerausschluss (Median-Abstand).
   - Ecken als Schnittpunkte benachbarter Geraden, entzerrt (`tagloc.lens`, Pinhole
     und Fisheye).
   - Zweistufig: erst um die ArUco-Ecken (max. 4 px Abweichung), dann um die
     verfeinerte Lage (max. 1 px). Scheitert eine Stufe, gelten die ArUco-Ecken.
     `cube_edge_refined` und `sheet_edges_refined` zeigen, was benutzt wurde.
3. **Blattpose:** `cv2.solvePnP` mit `SOLVEPNP_IPPE` und anschließend
   `solvePnPRefineLM` über alle Ecken der erkannten Blattmarker (mindestens 2).
   Die Reprojektions-RMS steht in `reprojection_rms_px`.
4. **Würfel:** Die vier entzerrten Ecken von Marker 10 werden als Strahlen von der
   Kameramitte in das Blattsystem gerechnet und mit der Ebene z = 20 mm
   (Würfeloberseite) geschnitten.
5. **Mitte und Winkel:** Die Markermitte ist der Mittelwert der vier
   Schnittpunkte. Die Kantenrichtungen werden zirkular gemittelt (modulo 90°),
   daraus Δθ, gewickelt auf [−45°, 45°). Positiv ist gegen den Uhrzeigersinn
   (Blatt, y nach oben).
6. **Aufkleber-Versatz:** Die Würfelmitte ist die Markermitte, abzüglich des
   Versatzes, der in Blattachsen gedreht wird.
7. **Plausibilität:** Die Kamera muss über der Ebene z = 20 mm stehen, sonst
   `ok=False` mit dem Grund „Blattpose unplausibel“.

Fehler (kein Würfelmarker, zu wenige Blattmarker, Pose nicht lösbar, Bildgröße
passt nicht zur Kalibrierung, Ausnahme) liefern `ok=False` mit deutschem
`grund`. `measure_trial` wirft nie.

### Ergebnisfelder `TrialMeasurement`

| Feld | Bedeutung |
| --- | --- |
| `ok`, `grund` | Messung gültig; sonst Grund als Text |
| `dx_mm`, `dy_mm` | Würfelmitte relativ zur Scheibenmitte, Blatt-x/-y, Aufkleber-Versatz abgezogen |
| `dtheta_deg` | Gierwinkel des Würfels, modulo 90°, auf [−45, 45) |
| `r_mm` | Abstand der Würfelmitte zur Scheibenmitte |
| `zone` | `gruen` / `gelb` / `rot` / `ausserhalb` nach der äußersten Würfelecke |
| `sheet_markers` | IDs der erkannten Blattmarker |
| `reprojection_rms_px` | RMS der Reprojektion der Blattmarker (Qualität der Blattpose) |
| `camera_height_mm` | Höhe des Kameramittelpunkts über der Blattebene |
| `camera_tilt_deg` | Winkel zwischen optischer Achse und Senkrechten (0 = senkrecht nach unten) |
| `nadir_offset_mm` | Abstand des Punkts, den die optische Achse auf der Würfeloberseite trifft, zur Würfelmitte. Klein = Kamera steht über dem Würfel |
| `cube_corners_mm` | Vier Würfelecken im Blattsystem, `[[x, y], ...]`, oben-links beginnend |
| `marker_center_mm`, `marker_yaw_deg` | Mitte des Markers ohne Aufkleber-Versatz und sein voller Gierwinkel (0..360), für die Aufkleber-Kalibrierung |
| `cube_edge_refined` | Würfelmarker-Kanten subpixelgenau verfeinert (sonst ArUco-Ecken) |
| `sheet_edges_refined` | Anzahl der Blattmarker mit verfeinerten Kanten |
| `sticker_offset_mm` | verwendeter Aufkleber-Versatz im Markersystem |

`to_dict()` liefert die JSON-fähige Form (ohne Overlay), `from_dict()` die
Umkehrung.

## Genauigkeit (synthetisch)

Testbilder aus `synthetic.render_trial`: 640 × 480 px, fx = 466, Kamera 22 cm
über dem Blatt, Supersampling 4. Ergebnis in 12 Zufallsfällen:

| Verfahren | Position | Winkel |
| --- | --- | --- |
| mit Kantenverfeinerung | ≤ 0,01 mm | ≤ 0,02° |
| ohne Kantenverfeinerung | 0,05 bis 0,13 mm | 0,6 bis 0,8° |

Das ist der Stand auf synthetischen Bildern. Am realen Aufbau steht die
Messung aus (Rauschen, Druck, Unschärfe, Verzeichnung der echten Kamera).
Die Tests verlangen weniger streng: Rückgewinnung auf < 0,1 mm und < 0,2°.

## Kennzahlen (ISO 9283, angelehnt)

`summarize` in `measure.py` über alle erfolgreichen Messungen einer Reihe.
Fehlgeschlagene Versuche werden gezählt, aber nicht gemittelt.

- **Genauigkeit:**
  - `AP_mm` = Abstand des Mittels (dx, dy) vom Soll (Scheibenmitte).
  - `AP_theta_deg` = |mittleres Δθ|. Das Mittel ist zirkular modulo 90°.
- **Wiederholgenauigkeit:**
  - `l` = Abstand jeder Ablage zum Mittelpunkt der Ablagen.
  - `RP_mm` = l̄ + 3 · S_l.
  - `RP_theta_deg` = 3 · S_θ (Streuung des Winkels um das Mittel, modulo 90°).
- Zusätzlich `dx_mean_mm`, `dx_std_mm`, `dy_mean_mm`, `dy_std_mm`, `dtheta_mean_deg`,
  `dtheta_std_deg`, `n`, `n_gesamt`, `n_fehlgeschlagen`, `zonen` (Anzahl je Zone).
- S ist die Stichproben-Standardabweichung (ddof 1), bei nur einem Wert 0.

## Schnittstellen

### OPC UA

`SaveUseCaseImage` mit Signatur `(Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error: Int32)`.
Vollständig in [`vision-server-interface.md`, Abschnitt 18a](../vision-server-interface.md).
`ResultJson` ist `TrialMeasurement.to_dict()` als JSON-Text, bei Fehler-Codes `""`.
Ein Messfehler ist kein Fehler-Code.

### Python

```python
# Modul: src/use_case_eval/measure.py
def measure_trial(image, calibration, cube_marker_offset_mm: Sequence[float] = (0.0, 0.0), *,
                  refine_edges: bool = True) -> TrialMeasurement:
    """Misst die Würfelablage. Wirft nie; Fehler stehen in ok/grund.
    image: BGR- oder Graubild, Größe = calibration.image_size.
    calibration: tagloc.calibration.CameraCalibration der Aufnahme."""

def summarize(measurements: Iterable[TrialMeasurement]) -> dict:
    """Kennzahlen nach dem Schema oben. Ohne erfolgreiche Messung nur n, n_gesamt, n_fehlgeschlagen, zonen."""

def sticker_offset_from_pair(m0: TrialMeasurement, m180: TrialMeasurement) -> tuple[float, float]:
    """Aufkleber-Versatz (mm, Markersystem) aus zwei Messungen vor/nach 180°-Drehung.
    Wirft ValueError: Messung nicht ok, oder Drehung weicht mehr als 30° von 180° ab."""

def annotate(image, measurement: TrialMeasurement):
    """Kontrollbild: Kopie mit Blatt- und Würfelmarkern, Toleranzkreisen, Würfelumriss und Messwerten."""

@dataclass
class TrialMeasurement:  # Felder siehe Tabelle oben
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "TrialMeasurement": ...  # unbekannte Schlüssel ignoriert

# Modul: src/use_case_eval/target.py (nur numpy); Konstanten u. a.:
#   CUBE_TOP_Z_MM = 20.0, CUBE_MARKER_ID = 10, CUBE_SIZE_MM = 20.0,
#   ZONE_RADII_MM = (("gruen", 15.15), ("gelb", 17.15), ("rot", 19.15)), SHEET_MARKERS (IDs 0-3)
def sheet_marker_corners(marker_id: int) -> np.ndarray: ...   # (4, 3), Blattkoordinaten
def cube_corners_xy(center_xy, yaw_deg: float) -> np.ndarray: ...  # (4, 2)
def zone_for_radius(radius_mm: float) -> str: ...

# Modul: src/use_case_eval/live.py (Anbindung an SaveUseCaseImage, wirft nie)
def load_sticker_offset(base_dir: Path) -> tuple[float, float]:
    """Liest <base_dir>/aufkleber_versatz.json; fehlt oder ist defekt: (0.0, 0.0)."""
def write_sticker_offset(base_dir: Path, offset_mm) -> Path:
    """Schreibt {"x_mm", "y_mm"} nach <base_dir>/aufkleber_versatz.json."""
def intrinsics_meta(calibration, source: str) -> dict:
    """pi.intrinsics; source: calibration | realsense_factory | placeholder."""
def calibration_from_meta(meta: dict) -> CameraCalibration:
    """Umkehrung von intrinsics_meta (Offline-Auswertung)."""
def measure_for_save(image, calibration, sticker_offset_mm=(0.0, 0.0)) -> tuple[dict, Any]:
    """(messung_dict, kontrollbild oder None). Ohne Kalibrierung: {"ok": False, "grund": "Keine Kamerakalibrierung verfuegbar"}."""

# Modul: src/use_case_eval/synthetic.py (nur Tests und Entwicklung)
def make_calibration(fx: float = 466.0, size=(640, 480), distortion=None) -> CameraCalibration: ...
def look_down_pose(x_mm=0.0, y_mm=0.0, height_mm=200.0, yaw_deg=0.0, tilt_deg=0.0) -> np.ndarray:
    """4x4 T_sheet_cam, Blick senkrecht nach unten."""
def render_trial(calibration, camera_pose_in_sheet, cube_dx_mm, cube_dy_mm, cube_dtheta_deg,
                 sticker_offset_mm=(0.0, 0.0), with_cube=True, with_sheet_markers=None,
                 supersample=1) -> np.ndarray:
    """Graubild (uint8) von Blatt und Würfel. Wahrheit = die übergebenen Werte."""
```

### Daten: Ablage und JSON

- `data/use_case/reihe_NNN/versuch_NNN.png` und `.json` (Schema `adp.usecase.trial/1`).
  Die Messung steht im Feld `messung` (siehe `TrialMeasurement`).
- `data/use_case/reihe_NNN/versuch_NNN_messung.png`: Kontrollbild.
- `data/use_case/aufkleber_versatz.json`: `{"x_mm": ..., "y_mm": ...}`, gilt für alle Reihen
  im Ordner `data/use_case`.
- `pi.intrinsics` in der Versuchs-JSON: Kameramatrix und Verzeichnung samt Quelle.
  Die Offline-Auswertung nimmt diese Intrinsik, sonst `--intrinsik <Kalibrierdatei>`.

### Offline-Auswertung

```bash
python3 tools/use_case_auswertung.py data/use_case/reihe_NNN [--neu-messen] [--bilder] \
    [--aufkleber-kalibrieren VOR NACH] [--intrinsik KALIBRIERDATEI]
```

- Ohne Optionen: nimmt die gespeicherte Messung aus der JSON. Fehlt sie dort
  (ältere Versuche), wird gemessen und die JSON um das Feld `messung` ergänzt.
- `--neu-messen`: alle Bilder neu messen, mit dem aktuellen Aufkleber-Versatz,
  JSON aktualisieren.
- `--bilder`: Kontrollbilder `versuch_NNN_messung.png` schreiben (misst neu).
- `--aufkleber-kalibrieren VOR NACH`: Versatz aus zwei Versuchen (Bilddatei oder
  Versuchsnummer) bestimmen und nach `data/use_case/aufkleber_versatz.json`
  schreiben.
- `--intrinsik`: Kalibrierdatei, falls die JSON keine Intrinsik enthält.

**Ausgabe im Reihenordner:**

- `auswertung.csv`, Spalten: `versuch`, `ok`, `grund`, `dx_mm`, `dy_mm`,
  `dtheta_deg`, `r_mm`, `zone`, `reprojection_rms_px`, `blattmarker`
  (IDs mit Leerzeichen), `camera_height_mm`, `camera_tilt_deg`, `nadir_offset_mm`.
- `auswertung.json`:
  ```json
  {
    "reihe": "reihe_003",
    "aufkleber_versatz_mm": [0.0, 0.0],
    "kennzahlen": { "n": 10, "n_gesamt": 10, "AP_mm": 0.0, "RP_mm": 0.0, "...": "..." },
    "versuche": [ { "versuch": 1, "ok": true, "dx_mm": 0.0, "...": "alle Felder von TrialMeasurement" } ]
  }
  ```
  `kennzahlen` enthält die Felder aus `summarize`. In `versuche` fehlt nur `blattmarker`.

### Netz / Betrieb

Keine neuen Ports oder Services. Die Messung läuft im Server
(`opcua-server.service`) auf dem Hand-Pi in einem Thread (`asyncio.to_thread`).
OpenCV wird erst beim Aufruf importiert.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Kein Würfelmarker (ID 10) | `ok=false`, „Kein Wuerfelmarker (ID 10) erkannt“, Foto wird gespeichert |
| Weniger als 2 Blattmarker | `ok=false`, Anzahl genannt |
| Kamera nicht über dem Blatt (Pose unplausibel) | `ok=false` |
| Bildgröße passt nicht zur Kalibrierung | `ok=false` |
| Keine Kamerakalibrierung | `ok=false`, „Keine Kamerakalibrierung verfuegbar“ |
| Kantenverfeinerung scheitert | ArUco-Ecken werden benutzt, keine Fehlermeldung (`cube_edge_refined=false`) |
| Aufkleber-Kalibrierung: keine 180°-Drehung | `ValueError`, Befehl bricht mit Meldung ab |
| Aufkleber-Datei fehlt oder defekt | Versatz (0, 0) |
| Kontrollbild schlägt fehl | Messung wird trotzdem gespeichert, nur `_messung.png` fehlt |

## Vorgehen

1. Zielscheibe erzeugen (`make_use_case_target.py --pdf`), PDF mit dem
   Read-Tool sichtbar prüfen (Kreise, Marker, 50-mm-Maßstab). Danach die
   Synthetik-Messung auf dem gerenderten Blatt.
2. Frontend: Fotopose nach „Kamerapose“ oben, Aufruf von `SaveUseCaseImage` mit
   `ResultJson` (webskillcomposition).
3. Tests: `tests.test_use_case_eval`, `tests.test_use_case_image` grün, danach
   die gesamte Suite.
4. Inbetriebnahme und Versuchsablauf an der Zelle (siehe unten).
5. Nach dem Versuch: Ergebnisse in Abschnitt „Genauigkeit“ eintragen, den
   Status auf `fertig` setzen.

## Inbetriebnahme und Versuchsablauf

Die Schritte sind aus der Verifikation übernommen:

1. Blatt in 100 % drucken, 50-mm-Maßstab nachmessen.
2. Blatt auf dem Soll-Ablagepunkt fixieren (Mitte auf den Punkt, 0°-Achse parallel
   zur Tisch-x-Kante).
3. Aufkleber auf den Würfel kleben, Versatz höchstens etwa 1 mm.
4. Aufkleber-Versatz einmal kalibrieren: zwei Ablagen mit 180°-Drehung, dann
   `tools/use_case_auswertung.py data/use_case/reihe_001 --aufkleber-kalibrieren N1 N2`.
5. Reihe 1 mit etwa 10 Ablagen fahren.
6. Prüfen:
   - Die App zeigt live Δx, Δy, Δθ und die Zone.
   - `tools/use_case_auswertung.py data/use_case/reihe_001` liefert
     `auswertung.csv` und `auswertung.json`.
7. Plausibilität: Den Würfel von Hand um etwa 2 mm versetzt hinlegen. Die Messung
   soll etwa 2 mm zeigen.

Server nach dem Stand-Update neu starten (`sudo systemctl restart opcua-server.service`),
danach die Methode am Vision-System prüfen (siehe `use-case-foto.md`, Inbetriebnahme).

## Offene Fragen

- **Verzeichnung der Handkamera:** Die synthetischen Werte gelten für eine
  Pinhole-Kamera ohne Verzeichnung. Die echte Kalibrierung (Pinhole oder Fisheye)
  wird im Versuch geprüft.
- **Druckqualität:** Die Schwelle für die Kantenverfeinerung (Kontrast 60 Grauwerte)
  ist an synthetischen Bildern eingestellt und muss am realen Papier geprüft werden.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD übernommen (Abschnitt 18a in
  `vision-server-interface.md` ist bereits angepasst).
