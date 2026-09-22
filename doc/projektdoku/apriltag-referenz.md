# AprilTag-Funktionalität — Referenz

Funktionsreferenz des Pakets `src/tagloc/`: was jedes Modul kann, wie die
Dateiformate aussehen, wie die Skripte bedient werden und was im OPC-UA-Ergebnis
ankommt.

Nachbardokumente: [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md) (Konzept
und warum es so gebaut ist), [`apriltag-e2e-test.md`](apriltag-e2e-test.md)
(wie man es durchtestet, auch mit der Pi-Kamera),
[`vision-server-interface.md`](vision-server-interface.md) (OPC-UA-Seite).

---

## 1. Begriffe und Konventionen

| Begriff | Bedeutung |
|---|---|
| `T_a_b` | Pose von `b` ausgedrückt in `a`; als 4×4-Matrix. Verkettung: `T_a_c = T_a_b · T_b_c` |
| Pose | `np.ndarray` der Form `(4, 4)`, `float64`, homogen. Kein eigener Typ — numpy reicht, und jede Funktion sagt im Namen, welches KS gemeint ist |
| Einheiten | **Meter** und **Radiant**, durchgehend, ohne Ausnahme |
| Quaternion | **xyzw**, wie im Payload (`"rotation": "quaternion_xyzw"`) und wie three.js es erwartet — *nicht* wxyz |
| Kamera-KS | OpenCV-Optikrahmen: +X rechts im Bild, +Y nach unten, +Z entlang der optischen Achse nach vorn. Als `frameConvention` der String `z_forward_x_right_y_down` |
| Welt-KS | durch den Anker-Tag der Tag-Map definiert, `frameId: "world"` |

**Regel:** Ein Rahmenname allein sagt nicht, wohin +Z zeigt. Jede kamerarelative
Pose trägt deshalb `frameConvention` mit. Wer den Payload liest, nimmt `frameId`
nie an, sondern liest ihn.

---

## 2. Modulübersicht

| Modul | Abhängigkeiten | Aufgabe |
|---|---|---|
| `tagloc.identity` | **nur Stdlib** | `"<name>#<mtime>"` von Kalibrierung und Tag-Map, für `configurationId` |
| `tagloc.modes` | **nur Stdlib** | die drei Overlay-Modi des Livestreams und ihre Beschriftungen |
| `tagloc.observations` | numpy-Typen | `TagObservation` und `TagPose` — die Typen zwischen Bild und Mathematik |
| `tagloc.geometry` | numpy | Posen bauen, verketten, invertieren, mitteln, in Position+Quaternion wandeln |
| `tagloc.calibration` | numpy, `json` | Kalibrierdatei lesen/schreiben, Auflösung prüfen und skalieren |
| `tagloc.tagmap` | numpy, `json` | Tag-Map lesen/schreiben; Tags aus Ko-Beobachtungen in ein gemeinsames KS platzieren |
| `tagloc.localize` | numpy | Tag-Posen + Tag-Map → Modulposen |
| `tagloc.boards` | cv2 | Schachbrett/ChArUco auf einem Frame finden, Kalibrierung rechnen |
| `tagloc.detector` | cv2 *oder* `pupil_apriltags` | Tags im Graubild finden → `TagObservation` |
| `tagloc.pose` | cv2 | `TagObservation` → `T_cam_tag` inkl. Reprojektionsfehler und Mehrdeutigkeit |
| `tagloc.overlay` | cv2 | Erkennungsergebnisse ins Bild zeichnen — Livestream und CLI |
| `tagloc.frames` | cv2 / picamera2 | Frame-Quellen: Pi-Kamera, Webcam, Bildordner, Einzelbild, `SharedCamera` |
| `tagloc.cli.*` | alles | vier Kommandozeilenwerkzeuge |

Die Abhängigkeitsrichtung ist eine harte Regel, keine Stilfrage:

- `identity` und `modes` brauchen **nicht einmal numpy**. Deshalb kann der
  Vision-Server die `configurationId` schon beim Bauen der Quelle bilden und der
  Livestream-Publisher den Modus normalisieren, ohne dass numpy oder OpenCV auf
  der Importkette liegen.
- `geometry`, `calibration`, `tagmap`, `localize` und `observations` importieren
  **kein cv2**. Sie sind damit auf jedem Rechner importierbar und testbar,
  unabhängig von der installierten OpenCV-Version (lokal 5.x, Pi 4.x, mit
  abweichender `aruco`-API).
- `tagloc` importiert **nirgends** aus `vision_server`. Die Verbindung läuft nur
  in eine Richtung.

---

## 3. `tagloc.geometry` — Posen und Verkettung

```python
Pose = np.ndarray   # (4, 4), float64

def identity() -> Pose
def from_rotation_translation(R: np.ndarray, t: Sequence[float]) -> Pose
def from_rvec_tvec(rvec: Sequence[float], tvec: Sequence[float]) -> Pose
def from_position_quaternion(position: Sequence[float],
                             orientation_xyzw: Sequence[float]) -> Pose

def compose(*poses: Pose) -> Pose
def invert(pose: Pose) -> Pose

def to_position_quaternion(pose: Pose) -> tuple[
    tuple[float, float, float],                  # Position in Metern
    tuple[float, float, float, float],           # Quaternion xyzw, w >= 0
]

def translation_distance_m(a: Pose, b: Pose) -> float
def rotation_distance_rad(a: Pose, b: Pose) -> float
def average_poses(poses: Sequence[Pose]) -> Pose

def pose_to_dict(pose: Pose) -> dict[str, list[float]]
    # {"position": [x, y, z], "orientation": [x, y, z, w]}
def pose_from_dict(data) -> Pose   # Umkehrung von pose_to_dict
```

`pose_to_dict`/`pose_from_dict` sind das gemeinsame JSON-Scharnier für **jedes**
Datenformat im Projekt, das eine Pose speichert: Kalibrierung, Tag-Map,
Hand-Auge-Datei und das CLI-Austauschformat von `transform` (Abschnitt 9.3).

Anmerkungen, die im Betrieb zählen:

- `from_rvec_tvec` rechnet Rodrigues **in numpy**, nicht über `cv2.Rodrigues`.
  Damit bleibt das Modul cv2-frei.
- `to_position_quaternion` normiert das Vorzeichen auf `w >= 0`. Ohne diese
  Normierung liefern zwei mathematisch identische Rotationen unterschiedliche
  Zahlen, und jeder Golden-Value-Test wird zufällig.
- `average_poses` mittelt die Translation arithmetisch und die Rotation über den
  Hauptvektor der akkumulierten Quaternion-Matrix — komponentenweises Mitteln von
  Quaternionen ist falsch. Wird für `samples_per_job` gebraucht.

---

## 4. `tagloc.calibration` — Kamerakalibrierung als Datum

```python
@dataclass(frozen=True)
class CameraCalibration:
    calibration_id: str
    frame_id: str
    image_size: tuple[int, int]
    camera_matrix: np.ndarray          # (3, 3)
    distortion: np.ndarray             # (5,)
    rms_reprojection_error: float
    sample_count: int
    board: Mapping[str, Any]

    @property
    def camera_params(self) -> tuple[float, float, float, float]:   # fx, fy, cx, cy

def load_calibration(path: Path) -> CameraCalibration
def save_calibration(path: Path, calibration: CameraCalibration) -> None
def check_resolution(calibration: CameraCalibration, image_size: tuple[int, int]) -> None
def scale_to_resolution(calibration: CameraCalibration,
                        image_size: tuple[int, int]) -> CameraCalibration

PLACEHOLDER_CALIBRATION_ID = "placeholder-unkalibriert"
def default_calibration(resolution: tuple[int, int], frame_id: str = "") -> CameraCalibration
```

- `default_calibration` liefert eine grob aus der Sichtfeldbreite geschätzte
  Kalibrierung — **kein Ersatz** für eine echte Kalibrierfahrt (Posen sind dann
  plausibel orientiert, aber nicht maßhaltig). Nur dazu da, Erkennung/Overlay/
  Job-Pfad ohne Kalibrierdatei lauffähig zu halten; aktiv nur mit
  `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` bzw.
  `AprilTagProfileConfig.allow_placeholder_calibration=True` (Abschnitt 10.1).
  `calibration_id` ist dann `PLACEHOLDER_CALIBRATION_ID`, erkennbar im Payload.

- `calibration_identity(path)` (aus `tagloc.identity`, hier re-exportiert) bildet
  `"<name>#<mtime>"`, **ohne die Datei zu öffnen** und ohne numpy. Das ist der
  Wert, der als `configurationId` in den Payload geht und im Fehlerfall die Frage
  beantwortet, welche Kalibrierung das war.
- `check_resolution` wirft `ValueError`, wenn Kalibrierauflösung und Bildgröße
  nicht zusammenpassen. Ohne diese Prüfung sind alle Posen um den
  Skalierungsfaktor falsch — plausibel falsch, also unauffällig.
- `scale_to_resolution` ist der bewusste Ausweg, wenn bewusst in anderer
  Auflösung gearbeitet wird: `fx, fy, cx, cy` werden skaliert, die
  Verzeichnungskoeffizienten bleiben.

### Dateiformat `data/calibration/<frame_id>.json`

```jsonc
{
  "schema": "wsc.vision.calibration/1",
  "calibrationId": "cam_ceiling@2026-09-17T14:03:11Z",
  "frameId": "cam_ceiling",
  "imageSize": [2028, 1520],
  "cameraMatrix": [[1520.4, 0, 1012.8], [0, 1519.7, 758.2], [0, 0, 1]],
  "distortionCoefficients": [-0.2841, 0.0912, 0.0003, -0.0001, -0.0148],
  "rmsReprojectionError": 0.31,
  "sampleCount": 22,
  "board": { "type": "charuco", "cols": 7, "rows": 5,
             "squareSizeM": 0.030, "markerSizeM": 0.022, "dictionary": "DICT_4X4_50" },
  "createdAt": "2026-09-17T14:03:11Z"
}
```

Die Datei gehört zu **einer physischen Kamera**, nicht zum Repo — `data/` ist
in `.gitignore` und das ist hier richtig. Jeder Pi erzeugt seine eigene.

---

## 5. `tagloc.tagmap` — Tags in einem gemeinsamen Koordinatensystem

```python
@dataclass(frozen=True)
class TagEntry:
    tag_id: int
    role: str                       # "world" (fest) | "robot" | "module" (beweglich)
    size_m: float
    module_id: str = ""
    instance_id: str = ""
    pose_in_world: Pose | None = None      # None = beweglich, wird gemessen
    tag_to_module: Pose = identity()       # konstanter CAD-Versatz

@dataclass(frozen=True)
class TagMap:
    frame_id: str
    anchor_tag_id: int
    tag_family: str
    entries: Mapping[int, TagEntry]

    def __getitem__(self, tag_id: int) -> TagEntry
    def reference_poses(self) -> Mapping[int, Pose]      # alles mit bekannter Weltpose
    def module_entries(self) -> Sequence[TagEntry]       # role == "module"

def load_tag_map(path: Path) -> TagMap
def save_tag_map(path: Path, tag_map: TagMap) -> None
def empty_tag_map(frame_id: str = "world", anchor_tag_id: int = 0) -> TagMap
    # leere Karte -- Betrieb ohne Karte bleibt ein normaler Fall
```

### Die Platzierungsfunktionen

```python
def place_tags(observations: Sequence[Mapping[int, Pose]],
               *, anchor_tag_id: int) -> dict[int, Pose]
def merge_tag_poses(poses: Sequence[Pose]) -> Pose
def residuals(observations: Sequence[Mapping[int, Pose]],
              placed: Mapping[int, Pose]) -> dict[tuple[int, int], tuple[float, float]]
def with_world_poses(tag_map: TagMap, placed: Mapping[int, Pose]) -> TagMap

def observed_tag_ids(observations: Sequence[Observation]) -> set[int]
    # alle jemals gesehenen Tag-IDs
def relative_poses(observations: Sequence[Observation]
                   ) -> dict[tuple[int, int], list[Pose]]
    # kamerafreie Relationen T_a_b aus Ko-Beobachtungen -- Vorstufe von place_tags
def missing_tags(observations: Sequence[Observation],
                 placed: Mapping[int, Pose]) -> list[int]
    # beobachtete Tags ohne Pfad zum Anker
def format_residual_report(
    residual_map: Mapping[tuple[int, int], tuple[float, float]]
) -> str
    # menschenlesbare Zusammenfassung der Schließfehler, groesster zuerst
```

Diese vier sind die Bausteine, aus denen `place_tags`/`residuals` sich
zusammensetzen; `cli/build_tagmap.py` benutzt sie direkt beim schrittweisen
Aufbau der Karte (Abschnitt 9.4).

`place_tags` ist der Kern. Eingabe ist eine Folge von Aufnahmen; jede Aufnahme
ist ein Dict `tag_id → T_cam_tag`, also alles, was in **einem** Bild gleichzeitig
zu sehen war. Daraus:

1. Jedes Paar `(a, b)` aus derselben Aufnahme liefert die kamerafreie Relation
   `T_a_b = invert(T_cam_a) · T_cam_b`. Die Kamerapose kürzt sich heraus — deshalb
   muss man die Kamera nicht kennen.
2. Diese Relationen bilden einen Graphen: Knoten sind Tag-IDs, Kanten sind
   Ko-Beobachtungen.
3. Breitensuche vom Anker-Tag aus verkettet die Relationen zu Posen in einem
   einzigen KS. Der Anker bekommt die Identität.
4. Mehrere Pfade zum selben Tag werden über `merge_tag_poses` zusammengeführt.

Tags ohne Pfad zum Anker tauchen **nicht** im Ergebnis auf — sie werden gemeldet,
nicht stillschweigend weggelassen.

`residuals` gibt je Kante `(Positionsfehler in m, Winkelfehler in rad)` zwischen
gemessener und aus der Karte rekonstruierter Relation zurück. **Das ist die
Qualitätszahl der Karte.** Eine Bilderserie, die einen Ring schließt, muss sich
schließen; tut sie das nicht, stimmt die Kalibrierung oder die eingetragene
Tag-Größe nicht.

> **Genauigkeitsgrenze.** Gemittelt wird über die zum Zeitpunkt des Besuchs
> bereits platzierten Nachbarn, nicht über alle Kanten des fertigen Graphen.
> Das Ergebnis ist deterministisch, aber keine globale Ausgleichung: über lange
> Ketten summiert sich der Fehler auf. Für eine Zelle mit wenigen Tags reicht
> das; `residuals` ist das Maß dafür, ob es noch reicht. Der nächste Schritt
> wäre eine gewichtete Fehlerquadratminimierung über alle Kanten — nötig
> spätestens dann, wenn sich die Schließfehler den Toleranzen nähern.

### Dateiformat `config/tagmap.json`

```jsonc
{
  "schema": "wsc.vision.tagmap/2",
  "frameId": "world",
  "anchorTagId": 0,
  "tagFamily": "tag36h11",
  "tags": [
    // Vier Welttags im Randbereich — das Einzige, was feststeht.
    { "tagId": 0, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 1, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 2, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 1.800, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 3, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 1.800, 0], "orientation": [0, 0, 0, 1] } },

    // Der Roboter: beweglich, mehrere Tags auf dieselbe Basis.
    { "tagId": 20, "role": "robot", "sizeM": 0.080,
      "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [0, 0, -0.120], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null },

    { "tagId": 7, "role": "module", "sizeM": 0.050,
      "moduleId": "MOD-A", "instanceId": "mod-a-1",
      "tagToModule": { "position": [0, 0, -0.040], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null }
  ]
}
```

`validate_tag_map(tag_map) -> list[str]` liefert die Beanstandungen der Karte
(genau vier Welttags, jeder eingemessen; kein bewegliches Tag mit Weltpose;
Anker ist ein Welttag; mindestens ein Robotertag). Leere Liste heißt in
Ordnung. Bewusst ein Bericht statt einer Ausnahme — eine Zelle im Aufbau darf
messen; `AprilTagDetectionSource.open` protokolliert und läuft weiter.

`nearest_world_tag(tag_map, pose_world) -> tuple[int, float] | None` gibt den
nächstgelegenen Welttag und seinen Abstand in Metern.

Eine Karte im Schema `/1` wird beim Laden **migriert, nicht abgelehnt**:
`reference` wird zu `world` (war schon immer ein fester Anker mit Weltpose),
`robot_table` wird zum beweglichen `robot` und **verliert dabei seine
Weltpose**. Jede Umdeutung wird einzeln protokolliert — still passiert nichts.
Modulnamen, Instanz-IDs und CAD-Versätze bleiben erhalten. Dauerhaft umstellen:
einmal mit `build_tagmap` neu schreiben lassen, `save_tag_map` schreibt immer
`/2`.

Ein *unbekanntes* Schema und ein **Tippfehler in der Rolle** werfen weiterhin
`ValueError` — sonst wäre ein Tag stumm weder Anker noch Modul.

`AprilTagDetectionSource.open` fängt diese Fehler ab und läuft **ohne Karte**
weiter: die Tags werden dann als `TAG-<id>` im Kamera-KS gemeldet. Eine
unbrauchbare Textdatei darf nicht das ganze Vision-System stilllegen.

`config/` ist versioniert — die Tag-Map beschreibt das Zellenlayout und gehört
ins Repo. Nicht nach `data/`, `concept/` oder `hardware/`: die sind alle
gitignored, Dateien dort wären für `git add` still unsichtbar.

---

## 6. `tagloc.detector` und `tagloc.pose` — vom Bild zur Pose

```python
@dataclass(frozen=True)
class TagObservation:
    tag_id: int
    corners: tuple[tuple[float, float], ...]   # genau 4, feste Reihenfolge
    decision_margin: float | None = None

class TagDetector(Protocol):
    family: str
    def detect(self, gray: np.ndarray) -> list[TagObservation]: ...

def build_detector(family: str = "tag36h11", backend: str = "aruco") -> TagDetector
```

Zwei Implementierungen hinter demselben Protokoll: `ArucoTagDetector` (`cv2.aruco`,
der Standardweg) und `PupilAprilTagDetector` (`pupil_apriltags`, für den Übergang
und für Vergleichsmessungen). Die Posenschätzung liegt bewusst **nicht** im
Detektor — sonst hinge die Posenqualität am Backend und wäre nicht vergleichbar.

```python
@dataclass(frozen=True)
class TagPose:
    tag_id: int
    pose_cam_tag: Pose
    reprojection_error_px: float = float("nan")
    ambiguity_ratio: float = 0.0    # Fehler der besten / Fehler der zweitbesten
                                    # Lösung, in (0, 1]; nahe 1 = mehrdeutig
    observation: TagObservation | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ambiguous(self) -> bool  # ambiguity_ratio > 0.6

    def is_usable(self, max_reprojection_error_px: float | None = None) -> bool
        # False bei NaN/Inf-Reprojektionsfehler (sonst würde payload.py bei
        # allow_nan=False mit INTERNAL statt DETECTION_FAILED abbrechen);
        # sonst False nur, wenn ueber der Schwelle

def undistort_corners(corners, calibration: CameraCalibration) -> np.ndarray
def estimate_tag_pose(observation: TagObservation, size_m: float,
                      calibration: CameraCalibration) -> TagPose
def estimate_tag_poses(observations: Sequence[TagObservation],
                       calibration: CameraCalibration, *,
                       tag_map: TagMap | None = None,
                       default_size_m: float = 0.05,
                       max_reprojection_error_px: float | None = None,
                       ) -> list[TagPose]
```

Zwei Punkte, die der Prototyp heute falsch macht und die hier behoben sind:

- **Die Ecken werden entzerrt.** `detect_apriltags.py` reicht `D` nur an
  `drawFrameAxes` weiter und schätzt die Pose auf dem verzeichneten Bild
  ([`altlasten.md`](altlasten.md), „Was nicht hier steht").
- **Mehrdeutigkeit wird gemeldet.** Ein flach gesehener quadratischer Marker hat
  zwei fast gleich gute Lösungen. `solvePnPGeneric` mit `SOLVEPNP_IPPE_SQUARE`
  liefert beide; das Verhältnis der Restfehler ist `ambiguity_ratio`. Ein
  mehrdeutiger Tag ist nicht falsch, aber er darf nicht ungeprüft angefahren
  werden — deshalb steht `ambiguous` im Payload.

`estimate_tag_poses` nimmt die Tag-Größe **je Tag aus der Tag-Map**, nicht aus
einer globalen Konstante. Welt-Board und Modul-Tags haben unterschiedliche Größen;
eine falsche Größe skaliert die Distanz linear mit und fällt sonst nicht auf.

---

## 7. `tagloc.localize` — vom Tag zum Modul

```python
@dataclass(frozen=True)
class ModuleLocation:
    module_id: str
    instance_id: str
    pose: Pose
    frame_id: str
    tag_id: int
    reprojection_error_px: float
    ambiguous: bool
    confidence: float

    role: str                       # "module" | "robot", aus der Tag-Map
    source: str                     # "ceiling" | "flange"
    reference_tag_id: int           # naechster Welttag, -1 = unbekannt
    pose_in_reference_tag: Pose | None    # T_worldtag_module
    attributes: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class CameraLocalization:
    pose_world_cam: Pose
    world_tag_ids: tuple[int, ...]  # alle beitragenden Welttags
    primary_tag_id: int             # der naechste Welttag
    spread_m: float                 # Streuung der Einzelschaetzungen
    spread_rad: float
    origin: str = ORIGIN_WORLD_TAGS # oder ORIGIN_ROBOT_POSE, siehe unten

def localize_camera(tag_poses: Sequence[TagPose], tag_map: TagMap, *,
                    max_reprojection_error_px: float = 3.0) -> CameraLocalization | None
def locate_modules(tag_poses: Sequence[TagPose], tag_map: TagMap, *,
                   localization: CameraLocalization | None, frame_id: str,
                   source: str = "",
                   max_reprojection_error_px: float = 3.0) -> list[ModuleLocation]
def merge_by_module(locations, *, tag_map: TagMap | None = None) -> list[ModuleLocation]
def merge_locations(*groups) -> list[ModuleLocation]
def world_tag_for_robot(tag_map: TagMap, locations) -> tuple[int, float] | None

def confidence_from(tag_pose: TagPose, *,
                    max_reprojection_error_px: float = 3.0) -> float
    # aus Reprojektionsfehler + Mehrdeutigkeit, in [0, 1] -- bewusst nicht
    # konstant 1.0, das waere Information ohne Inhalt
def merge_samples(samples: Sequence[Sequence[ModuleLocation]]) -> list[ModuleLocation]
    # mehrere Aufnahmen desselben Jobs -> ein Ergebnis je Modul; Anzahl
    # gemittelter Aufnahmen landet als sampleCount in attributes
def robot_locations(locations: Sequence[ModuleLocation]) -> list[ModuleLocation]
    # role == "robot", normalerweise genau ein Treffer
def expected_but_missing(tag_map: TagMap,
                         located: Sequence[ModuleLocation]) -> list[str]
    # Module aus der Karte, die nicht gefunden wurden (Abbruchkriterium aus
    # concept/offene_punkte.md Nr. 6, ohne zusaetzliche Konfiguration)
```

`merge_samples` ist die Funktion, die `detection/apriltag.py` tatsächlich zum
Mitteln mehrerer Job-Aufnahmen benutzt — nicht zu verwechseln mit
`merge_by_module` (mittelt mehrere Tags **einer** Aufnahme zu einer Modulpose).

`localize_camera` bestimmt `T_world_cam` aus allen sichtbaren Welttags —
**nur** die Rolle `world` zählt. Mehrere Welttags werden nach Konfidenz
gewichtet gemittelt, damit ein weit entfernter, schräg gesehener Tag nicht so
stark zieht wie einer, der das Bild füllt; keiner sichtbar → `None`.
`primary_tag_id` ist der kameranächste Welttag — der, an dem sich die
Handkamera ausrichtet. `spread_m`/`spread_rad` sagen, wie weit die
Einzelschätzungen auseinanderliegen: widersprechen sich die vier Welttags,
stimmt die Vermessung nicht, und die Zahl sagt es, statt im Mittelwert zu
verschwinden.

`locate_modules` rechnet dann für jeden Modul- und Roboter-Tag

```
pose = T_world_cam · T_cam_tag · T_tag_module        # wenn localization gegeben
pose =               T_cam_tag · T_tag_module        # sonst, im Kamera-KS
```

setzt `frame_id` entsprechend und legt zusätzlich den nächstgelegenen Welttag
samt `T_worldtag_module` bei. **Das ist die einzige Stelle im gesamten Code, an
der diese Verkettung steht** — Decken- und Handkamera rufen dieselbe Funktion,
sie unterscheiden sich nur darin, ob `localization` gesetzt ist.

`merge_by_module` mittelt Messungen gleicher `(moduleId, instanceId)` — so
werden mehrere Tags am Roboter zu einer Roboterpose. `merge_locations` führt
Decken- und Handergebnis zusammen, die genauere Quelle gewinnt.
`world_tag_for_robot` beantwortet, welcher Welttag dem Roboter am nächsten
steht.

`camera_pose_from_reference_tags(tag_poses, tag_map) -> Pose | None` bleibt als
schlanke Hülle für die CLI-Werkzeuge erhalten, die nur die Matrix brauchen.

### Hand-Auge und Ankern — `tagloc.handeye`

Die Kamera sitzt starr am Roboter, `T_flansch_cam` ist also eine Konstante der
Hardware und wird **einmal** kalibriert. Sie schließt die Lücke zwischen zwei
Welttag-Sichtungen: die Handkamera sieht den Welttag nur beim Ankern, danach
trägt die Kinematik.

```python
SCHEMA = "wsc.vision.handeye/1"          # data/handeye/<frame_id>.json

@dataclass(frozen=True)
class HandEye:
    pose_flange_cam: Pose
    frame_id: str = ""
    calibration_id: str = ""
    rms_position_m: float = float("nan")   # Streuung des rekonstruierten Tag-Orts
    rms_rotation_deg: float = float("nan")
    sample_count: int = 0

@dataclass(frozen=True)
class RobotAnchor:
    pose_world_base: Pose
    world_tag_id: int = -1     # Welttag, an dem geankert wurde
    spread_m: float = 0.0      # Guete der Lokalisierung beim Ankern
    spread_rad: float = 0.0

def anchor_world_base(pose_world_cam, pose_base_flange, pose_flange_cam) -> Pose
def camera_pose_from_robot(pose_world_base, pose_base_flange, pose_flange_cam) -> Pose
def target_spread(poses_base_flange, poses_cam_tag, pose_flange_cam) -> tuple[float, float]
def solve_hand_eye(poses_base_flange, poses_cam_tag) -> tuple[Pose, float, float]
def load_hand_eye(path) -> HandEye
def save_hand_eye(path, hand_eye) -> None
```

### Dateiformat `data/handeye/<frame_id>.json`

```jsonc
{
  "schema": "wsc.vision.handeye/1",
  "frameId": "cam_flange",
  "calibrationId": "cam_flange@2026-09-22T09:14:03Z",
  "poseFlangeCam": { "position": [0.02, -0.015, 0.045],
                     "orientation": [0, 0, 0.707, 0.707] },
  "rmsPositionM": 0.0031,
  "rmsRotationDeg": 0.42,
  "sampleCount": 14,
  "createdAt": "2026-09-22T09:14:03Z"
}
```

Gehört wie die Kamerakalibrierung zur physischen Hardware, nicht zum Repo
(`data/` ist gitignored). `rmsPositionM`/`rmsRotationDeg` können `null` sein
(NaN lässt sich nicht als JSON schreiben), wenn keine belastbare Streuung
vorliegt.

`solve_hand_eye` rechnet nach **Park und Martin** in numpy, nicht über
`cv2.calibrateHandEye`: OpenCV 5.0 exportiert die Funktion nicht mehr nach
Python (nur die `CALIB_HAND_EYE_*`-Konstanten sind übrig), und das Repo trägt
bewusst 4.x wie 5.x. Damit bleibt `tagloc.handeye` außerdem OpenCV-frei — der
Vision-Server lädt den Anker, ohne OpenCV auf die Importkette zu holen.

Gütemaß ist `target_spread`: der Tag der Kalibrierfahrt lag fest, also muss
`T_base_flansch(i) · T_flansch_cam · T_cam_tag(i)` für jedes `i` dieselbe Pose
ergeben. Streut das, stimmt die Kamerakalibrierung, die Tag-Größe oder die
gemeldete Roboterpose nicht.

In `tagloc.localize` hängen daran:

```python
def anchor_from_localization(localization, pose_base_flange, hand_eye) -> RobotAnchor
def localize_camera_from_anchor(anchor, pose_base_flange, hand_eye) -> CameraLocalization
def anchor_drift(anchor, localization, pose_base_flange, hand_eye) -> tuple[float, float]
```

`CameraLocalization.origin` sagt, woher die Pose stammt: `ORIGIN_WORLD_TAGS`
(optisch gemessen, gewinnt immer wenn ein Welttag im Bild ist) oder
`ORIGIN_ROBOT_POSE` (aus dem Anker fortgeschrieben). Die beiden haben nicht
dieselbe Genauigkeit, deshalb steht es im Payload.

`confidence` wird aus Reprojektionsfehler und Mehrdeutigkeit gebildet, nicht
konstant auf `1.0` gesetzt.

---

## 8. `tagloc.overlay` — Erkennungsergebnisse im Bild markieren

```python
OVERLAY_MODES = ("off", "apriltag", "calibration")     # aus tagloc.modes
def normalise_mode(mode) -> str

def draw_tag_outline(image, tag_pose: TagPose, color) -> None
def draw_tag_axes(image, tag_pose, calibration, axis_length_m: float) -> None
def draw_tag_overlay(image, tag_poses, calibration, *,
                     tag_map=None, default_size_m=0.05, show_distance=True)
def draw_board_overlay(image, board_sample, *, coverage=None)
def draw_status_bar(image, lines: Sequence[str])
def summarise(tag_poses, tag_map=None) -> str

def has_display() -> bool
    # ob ueberhaupt ein Bildschirm erreichbar ist (Linux: $DISPLAY/$WAYLAND_DISPLAY)

class Window:
    def __init__(self, title: str, *, enabled: bool = True) -> None
    @property
    def enabled(self) -> bool
    def show(self, image, wait_ms: int = 1) -> str   # gedrueckte Taste, klein, "" = keine
    def close(self) -> None
```

`Window` faengt ab, dass `cv2.imshow` auf einem Pi ohne Bildschirm bzw. in
einer SSH-Sitzung ohne X-Forwarding **mitten im Lauf** abstürzt — der erste
Fehlschlag wird einmal geloggt, danach läuft die Erkennung ohne Fenster weiter
(das Ziel ist die Erkennung, nicht das Bild). `cli/detect.py` und
`cli/calibrate.py` benutzen sie für ihre interaktiven Fenster.

Die übliche Darstellung bei Markern ist das **Achsenkreuz im Tag** — X rot,
Y grün, Z blau — zusätzlich der Umriss und die ID. Genau das zeichnet
`draw_tag_overlay`. Die Aufteilung hat einen Grund: der Umriss zeigt, **dass**
erkannt wurde, das Achsenkreuz zeigt, **wie** die Pose liegt. Ein verdrehtes
Kreuz fällt sofort auf, eine falsche Zahl in einer Tabelle nicht.

Beschriftet wird je Tag: ID, Modulname aus der Karte, Distanz und
Reprojektionsfehler. **Mehrdeutige Posen werden orange statt grün gezeichnet** —
das ist die Information, nach der man beim Debuggen sucht.

Das Modul zeichnet nur, es rechnet nichts: Posen kommen fertig herein. Deshalb
kann derselbe Code im Livestream, im CLI-Fenster und in einem Standbild laufen.

`normalise_mode` liegt in `tagloc.modes` und kommt ohne numpy aus, weil der
Livestream-Publisher sie braucht, auch wenn gar keine Erkennung konfiguriert ist.
Ein unbekannter, leerer oder falsch getippter Modus fällt auf `apriltag` zurück
statt den Stream anzuhalten.

---

## 9. Die Kommandozeilenwerkzeuge

Alle akzeptieren `--source` wahlweise als Kamera oder als Bildordner. Ein
Bildordner macht jeden Aufruf reproduzierbar und hardwarefrei.

### 9.1 Kalibrierung

```bash
# interaktiv an der Kamera: SPACE = Aufnahme, C = rechnen, S = speichern, Q = Ende
PYTHONPATH=src python3 -m tagloc.cli.calibrate \
    --source camera:0 --board charuco --out data/calibration/cam_ceiling.json \
    --frame-id cam_ceiling

# headless aus einem Bildordner — derselbe Rechenkern, keine GUI
PYTHONPATH=src python3 -m tagloc.cli.calibrate \
    --source ./aufnahmen/decke/ --board charuco --out data/calibration/cam_ceiling.json
```

Wichtige Optionen: `--board chessboard|charuco`, `--cols/--rows`,
`--square-size-m`, `--marker-size-m`, `--dictionary`, `--min-samples`.
Ausgegeben werden RMS-Reprojektionsfehler und die Bildabdeckung in x und y —
eine Kalibrierung, die nur die Bildmitte abdeckt, hat brauchbares RMS und
unbrauchbare Verzeichnungskoeffizienten.

### 9.2 Erkennung

```bash
PYTHONPATH=src python3 -m tagloc.cli.detect \
    --source camera:0 --calibration data/calibration/cam_ceiling.json \
    --tag-map config/tagmap.json --overlay

# als JSON in eine Datei, z. B. zur Weiterverarbeitung oder für einen Report
PYTHONPATH=src python3 -m tagloc.cli.detect \
    --source ./aufnahmen/szene/ --calibration … --tag-map … --json posen.json
```

Gibt je Tag aus: ID, Pose (Position + Quaternion xyzw), Reprojektionsfehler,
Mehrdeutigkeits-Flag. Mit `--tag-map` zusätzlich die Modulzuordnung und, sobald
Referenztags sichtbar sind, die Weltpose.

### 9.3 Koordinatentransformation

```bash
# einzelne Pose mit bekannter Kamerapose transformieren
PYTHONPATH=src python3 -m tagloc.cli.transform \
    --camera-pose roboterpose.json \
    --position 0.12 -0.04 0.85 --orientation 0 0 0 1

# ganze Ergebnisdatei von detect, Kamerapose aus den Referenztags der Eingabe
PYTHONPATH=src python3 -m tagloc.cli.transform \
    --input posen.json --tag-map config/tagmap.json \
    --from-reference --out posen_welt.json

# umgekehrte Richtung
PYTHONPATH=src python3 -m tagloc.cli.transform \
    --camera-pose roboterpose.json --invert \
    --position 1.20 0.33 0.04 --orientation 0 0 0.3827 0.9239
```

Es gibt **kein** `--from`-Flag. Die Herkunft der Transformation `T_ziel_quelle`
ist entweder `--camera-pose <datei>` (JSON mit `position`/`orientation`) oder
`--from-reference` (rechnet `T_world_cam` selbst aus den Referenz-Tags der
`--input`-Datei, braucht dafür `--tag-map`); ohne beides ist die Transformation
die Identität. `--to` benennt nur das Ziel-KS für die Ausgabe (Default
`world`), nicht die Quelle. Weitere Optionen: `--position`/`--orientation` für
eine einzelne Pose ohne `--input`, `--invert` zum Umkehren, `--out` zum
Schreiben. Reine Rechnung, keine Kamera, kein cv2. Das Werkzeug ist auch der
schnellste Weg, eine Konventionsfrage zu klären, bevor man sie sich im
Produktivcode einfängt.

### 9.3a Hand-Auge-Kalibrierung

```bash
PYTHONPATH=src python3 -m tagloc.cli.calibrate_handeye \
    --samples fahrt/aufnahmen.json \
    --calibration data/calibration/cam_flange.json \
    --out data/handeye/cam_flange.json
```

Optionen: `--samples` (Aufnahmeliste, siehe unten, Pflicht), `--calibration`
(Kamerakalibrierung, Pflicht), `--out` (Zieldatei, Pflicht), `--family`
(Tag-Familie, Default `tag36h11`), `--backend` (Detektor-Backend, Default
`aruco`), `--frame-id` (Bezugsrahmen, leer = aus der Kalibrierung),
`--max-reproj-error-px` (Aufnahmen darüber werden verworfen, Default 2.0),
`-v`/`--verbose`.

Aufnahmeliste (`--samples`, getrennt von der Rechnung, damit sie sich ohne
neue Roboterfahrt wiederholen lässt):

```jsonc
{
  "schema": "wsc.vision.handeye.samples/1",
  "tagId": 2,
  "tagSizeM": 0.100,
  "samples": [
    { "image": "pose_000.png",
      "poseBaseFlange": { "position": [0.20, 0.05, 0.50],
                          "orientation": [0, 0.131, 0, 0.991] } },
    { "image": "pose_001.png", "poseBaseFlange": { "...": "..." } }
  ]
}
```

Bildpfade sind relativ zur Aufnahmeliste. Ablauf: einen Tag ortsfest hinlegen
(er muss während der ganzen Fahrt liegen bleiben), den Roboter mindestens ein
Dutzend deutlich verschiedene Posen anfahren lassen (**um mehrere Achsen
drehen**, reines Verschieben bestimmt die Rotation nicht), je Pose ein Bild
plus `T_base_flansch` notieren. Das Gütemaß (`target_spread`) steht danach in
der Ausgabe: der Tag lag fest, also muss er aus jeder Roboterpose an
derselben Stelle herauskommen — streut das über `SPREAD_WARNING_M` (5 mm),
wird die Datei trotzdem geschrieben, aber mit unübersehbarem Hinweis.

### 9.4 Tag-Map bauen

```bash
PYTHONPATH=src python3 -m tagloc.cli.build_tagmap \
    --source ./aufnahmen/zelle/ --calibration data/calibration/cam_ceiling.json \
    --anchor 0 --sizes config/tagsizes.json --out config/tagmap.json
```

Nimmt eine Bilderserie, in der sich benachbarte Tags jeweils paarweise
überlappen, platziert alle Tags relativ zum Anker und schreibt die Karte.
Ausgegeben werden zusätzlich die Schließfehler je Kante und eine Liste der Tags,
die keinen Pfad zum Anker hatten.

**Aufnahmehinweis:** Es genügt nicht, jeden Tag einmal zu fotografieren. Jedes
Bild muss mindestens zwei Tags zeigen, und die Paare müssen eine
zusammenhängende Kette bis zum Anker bilden.

### 9.5 Hilfswerkzeuge unter `tools/`

```bash
# maßhaltiger Druckbogen als SVG: Tags oder ChArUco-Board, mit 100-mm-Maßstab
python tools/make_tag_sheet.py --ids 0 1 2 3 7 12 --size-mm 100 --out tags.svg
python tools/make_tag_sheet.py --charuco --cols 7 --rows 5 \
    --square-mm 30 --marker-mm 22 --out board.svg

# synthetische Szene mit bekannter Wahrheit — die ganze Kette ohne Kamera
python tools/make_synthetic_scene.py --mode tags --count 12 \
    --out /tmp/bilder --calibration /tmp/calib.json
```

`make_tag_sheet.py` gibt bewusst SVG aus, nicht PNG oder PDF: ein SVG trägt echte
Millimeterangaben und bleibt beim Drucken mit „100 %" maßhaltig. Auf jedem Bogen
steht ein 100-mm-Maßstab zum Nachmessen — ein Drucker, der still auf 96 %
skaliert, erzeugt sonst 4 % Distanzfehler, und alles andere sieht dabei plausibel
aus.

---

## 10. Im Vision-Server

### 10.1 Profil und Rezept

| | Wert |
|---|---|
| Profil | `apriltag` |
| Rezept | `RecipeId = "apriltag"` |
| Klasse | `AprilTagDetectionSource` in `src/vision_server/detection/apriltag.py` |
| Konfiguration | `AprilTagProfileConfig` in `src/vision_server/profiles.py` |
| `IsSimulated` | `False` |

Layer 1 und Layer 2 benutzen **dasselbe Profil**; jeder Pi fährt einen eigenen
Server mit einer eigenen `AprilTagProfileConfig`, gesetzt in `src/vision_server/server.py`.

| Feld | Layer 1 (`pi-decke`) | Layer 2 (`pi-hand`) |
|---|---|---|
| `frame_id` | `world` | `cam_flange` |
| `frame_convention` | `z_forward_x_right_y_down` | `z_forward_x_right_y_down` |
| `calibration_path` | `data/calibration/cam_ceiling.json` | `data/calibration/cam_flange.json` |
| `tag_map_path` | `config/tagmap.json` | `config/tagmap.json` |
| `tag_size_m` | 0.100 | 0.050 |
| `resolution` | 2028×1520 | 640×480 |
| `samples_per_job` | 3 | 5 |
| `max_reproj_error_px` | 3.0 | 1.5 |
| `hand_eye_path` | `None` (keine Kamera am Flansch) | `data/handeye/cam_flange.json` |

`resolution` bei Layer 2 muss zu `CameraStreamConfig.realsense_resolution`
passen (Standard 640×480) — die tatsächlichen Frames kommen über die geteilte
`SharedCamera`, nicht direkt aus `AprilTagProfileConfig`. Eine Abweichung
fällt erst beim ersten Job als `ValueError` auf ("Kalibrierung gilt für …").

`tag_size_m` bleibt als Rückfallwert für Tags, die nicht in der Karte stehen;
steht ein Tag in der Karte, gewinnt deren `sizeM`.

**Ohne Kalibrierdatei** scheitert `open()` normalerweise, und der Server
bleibt in `Preoperational` (Absicht: bedeutungslose Zahlen sollen nicht
unbemerkt rausgehen). Für Tests **vor** der echten Kalibrierfahrt (Abschnitt
3.3 im Testplan) gibt es einen expliziten, temporären Notausgang:
`AprilTagProfileConfig.allow_placeholder_calibration = True` — gesetzt über
die Env-Var `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` in
`src/vision_server/server.py`, Standard aus. Dann startet die Quelle mit einer grob
geschätzten Intrinsik (`tagloc.calibration.default_calibration`, aus einer
angenommenen Sichtfeldbreite von 70° gerechnet). Detektor, Overlay und
Job-Pfad lassen sich damit prüfen; die Posen sind aber **nicht masshaltig**.
`calibration_id` im Ergebnis heißt dann `"placeholder-unkalibriert"` — vor
dem Rollout die Env-Var wieder entfernen.

### 10.2 Was im Ergebnis ankommt

Das Payload-Schema bleibt `wsc.vision.detections/1` — **unverändert**, neue Keys
sind additiv erlaubt, ein Versionssprung würde das Backend brechen.

```jsonc
{
  "schema": "wsc.vision.detections/1",
  "visionSystemId": "vision-ceiling-01",
  "jobId": "job-42",
  "resultState": 0,
  "frameId": "world",
  "frameConvention": "z_forward_x_right_y_down",
  "configurationId": "tag36h11@cam_ceiling#1757500000+tagmap#1757499100",
  "lengthUnit": "m", "angleUnit": "rad", "rotation": "quaternion_xyzw",
  "detections": [
    {
      "moduleId": "MOD-A",
      "instanceId": "mod-a-1",
      "confidence": 0.94,
      "position": [1.204, 0.336, 0.041],
      "orientation": [0.0, 0.0, 0.3827, 0.9239],
      "boundingBox": null,
      "attributes": {
        "tagId": "7",
        "reprojErrorPx": "0.42",
        "ambiguous": "false",
        "sampleCount": "3",
        "frameTimestamp": "1757500123.417",
        "recipeId": "apriltag",
        "source": "ceiling",
        "role": "module",
        "referenceTagId": "0",
        "referencePosition": "[1.204, 0.336, 0.041]",
        "referenceOrientation": "[0.0, 0.0, 0.3827, 0.9239]",
        "referenceDistanceM": "1.258",
        "worldTagIds": "[0, 1]",
        "cameraSpreadM": "0.004",
        "cameraSpreadDeg": "0.31",
        "cameraPoseOrigin": "world_tags"
      }
    }
  ]
}
```

Vollständige, tatsächlich mögliche Attribute (alle `attributes`-Werte sind
Strings, OPC UA kennt hier keine gemischten Typen):

| Feld | Wann gesetzt | Bedeutung |
| --- | --- | --- |
| `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`, `frameTimestamp`, `recipeId` | immer | wie oben |
| `source` | immer | `"ceiling"` \| `"flange"` |
| `role` | immer | `"module"` \| `"robot"`, aus der Tag-Map |
| `tagIds`, `tagCount` | mehrere Tags derselben Modulinstanz gemessen | welche Tags beigetragen haben |
| `referenceTagId` | ein Welttag als Bezug bekannt | nächster Welttag |
| `referencePosition`, `referenceOrientation`, `referenceDistanceM` | s. o. | Pose relativ zu diesem Welttag |
| `worldTagIds`, `cameraSpreadM`, `cameraSpreadDeg`, `cameraPoseOrigin` | Kamera lokalisiert (`localize_camera`/Anker) | beitragende Welttags, Streuung, `"world_tags"` (optisch gemessen) oder `"robot_pose"` (aus dem Anker fortgeschrieben) |
| `anchorWorldTagId` | Hand-Auge-Anker gesetzt | Welttag, an dem geankert wurde |
| `anchorDriftM`, `anchorDriftDeg` | s. o., mit aktueller Sichtung vergleichbar | Abweichung Anker vs. jetzige Messung |
| `missingModules` | Module aus der Tag-Map nicht gefunden | kommagetrennte Liste |

- `configurationId` trägt Tag-Familie, Kalibrieridentität und Tag-Map-Identität.
  Damit ist bei einer falschen Pose nachträglich klar, welche Kalibrierung und
  welche Karte gewirkt haben.
- Kein Tag gefunden → kein leeres Ergebnis, sondern `VisionJobError` mit
  `DETECTION_FAILED` (`resultState = 5`).
- `Stop` während eines Jobs → `resultState = 7` (`CANCELLED`), wie von
  `JobRunner.stop()` gehandhabt.

### 10.3 Betriebsregeln der Quelle

- **Jede** cv2-Operation läuft über `run_blocking`. Direkt auf dem Event-Loop
  friert sie Zustandsautomat, Events und die 1-Hz-Schleife des Zellenservers ein.
- Die Quelle hat ein **eigenes** Aufnahme-Timeout (`capture_timeout_s`). Das
  Job-Timeout des Runners kann einen hängenden Worker-Thread nicht töten.
- Die Kamera wird über `SharedCamera` mitbenutzt, nicht selbst geöffnet —
  Picamera2 lässt pro Kamera nur einen offenen Zugriff zu, und der Livestream
  hält ihn.
- `open()` lädt Kalibrierung und Tag-Map. Fehlen sie, öffnet die Quelle nicht,
  und der Server bleibt in Preoperational. Das ist gewollt: ein Vision-Server
  ohne Kalibrierung liefert Zahlen ohne Bedeutung.

---

## 11. Testen

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

| Testdatei | Prüft | Braucht |
|---|---|---|
| `tests/test_tag_geometry.py` | Verkettung, Inversion, Quaternion-Konvention, Mittelung — mit Golden Values | numpy |
| `tests/test_tag_map.py` | `place_tags` auf konstruierten Ko-Beobachtungen, Schließfehler, unerreichbare Tags, Datei-Round-Trip | numpy |
| `tests/test_calibration_io.py` | Schreiben/Lesen, Identität, Auflösungsprüfung, Skalierung | numpy |
| `tests/test_tag_pipeline.py` | **End-to-End auf einem synthetisch gerenderten Bild**: Detektor → Pose → Platzierung → Modulpose gegen die bekannte Wahrheit; Toleranz 2 mm / 1° / 1 px, gemessen 0,29 mm / 0,50° / 0,063 px | cv2 (sonst `skipUnless`) |
| `tests/test_apriltag_source.py` | `run_blocking`, Aufnahme-Timeout, `configurationId`, `frame_id`/`frame_convention`, Payload-Attribute, `DETECTION_FAILED` | — |

Testbilder werden **im Test erzeugt** (`tools/make_synthetic_scene.py`), nicht
committet: `.gitignore` schließt `*.jpg` aus, und das Repo kommt bewusst ohne
Fixture-Dateien aus. Gemockt wird mit handgeschriebenen Fakes (`FakeCamera`,
`FakeDetector`), nicht mit `unittest.mock` — das ist das Idiom dieses Repos.

---

## 12. Wenn etwas nicht stimmt

| Symptom | Wahrscheinliche Ursache |
|---|---|
| Distanz systematisch um einen festen Faktor daneben | falsche `sizeM` in der Tag-Map, oder Kalibrierung gehört zu einer anderen Auflösung → `check_resolution` |
| Pose kippt zwischen zwei Lagen hin und her | mehrdeutiger Tag, `ambiguous: true`. Steiler auf den Tag schauen oder einen zweiten Tag hinzunehmen |
| Posen am Bildrand deutlich schlechter als in der Mitte | Kalibrierung deckt den Rand nicht ab — Abdeckungswerte aus `calibrate` prüfen, nicht nur RMS |
| `place_tags` liefert weniger Tags als aufgenommen | kein Pfad zum Anker; es fehlt ein Bild, das zwei bereits verbundene Tags gemeinsam zeigt |
| Schließfehler in `residuals` groß | Kalibrierung oder eine eingetragene Tag-Größe stimmt nicht; die Karte ist erst brauchbar, wenn sie sich schließt |
| Server bleibt in Preoperational | `open()` der Quelle ist gescheitert — meist fehlende Kalibrier- oder Tag-Map-Datei; Logmeldung nennt den Pfad |
| Livestream weg, obwohl konfiguriert | keine geöffnete Quelle hält eine `SharedCamera` |
| Layer 2 liefert Posen im Kamera-KS statt `world`, obwohl ein Welttag sichtbar sein sollte | `AprilTagProfileConfig.hand_eye_path` fehlt oder zeigt auf keine Datei — ohne Hand-Auge-Kalibrierung ankert nichts |
| `anchorDriftM`/`anchorDriftDeg` im Payload wachsen über die Zeit | Anker ist veraltet oder der Roboter hat sich seit dem letzten Ankern stark bewegt — neu ankern (Welttag wieder ins Bild bringen) |
| `calibrate_handeye` bricht mit „Keine Aufnahmen" oder ähnlichem ab | Aufnahmeliste leer, falsches Schema, oder alle Aufnahmen über `--max-reproj-error-px` verworfen |
| `solve_hand_eye`-Ergebnis mit großer Streuung (`target_spread` > `SPREAD_WARNING_M`) | zu wenige oder zu ähnliche Roboterposen (Rotation nicht ausreichend variiert), oder Kamerakalibrierung/Tag-Größe falsch |
| Job meldet `DETECTION_FAILED`, obwohl ein Tag sichtbar war | `TagPose.is_usable()` hat die Pose verworfen — NaN/Inf-Reprojektionsfehler oder über `max_reprojection_error_px` |
