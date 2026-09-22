# AprilTag-Lokalisierung — Konzept und Umsetzungsplan

Wie Module, die mit AprilTags bestückt sind, in der Zelle lokalisiert werden, und
wie die dafür nötige Funktionalität so geschnitten wird, dass Layer 1 und Layer 2
sie gemeinsam benutzen und dass sie ohne Hardware end-to-end testbar bleibt.

Nachbardokumente: [`apriltag-referenz.md`](apriltag-referenz.md) (Funktionsreferenz),
[`apriltag-e2e-test.md`](apriltag-e2e-test.md) (Testanleitung inkl. Pi-Kamera),
[`vision-server-interface.md`](vision-server-interface.md) (OPC-UA-Schnittstelle),
[`altlasten.md`](altlasten.md), [`concept/offene_punkte.md`](../concept/offene_punkte.md).

> **Stand:** umgesetzt. Was hier steht, beschreibt den gebauten Zustand; die
> Abschnitte 6 und 9 tragen ab, was davon noch offen ist.

---

## 1. Vorstellung: was AprilTags in diesem Projekt sind

Ein AprilTag ist in diesem Projekt **kein Erkennungsmerkmal, sondern ein Messpunkt
mit Identität**. Die Kamera liefert daraus immer genau eine Sache: die Relation
`T_cam_tag` — wo der Tag relativ zur Kamera steht, in Metern und mit voller
Orientierung. Alles Weitere ist Verkettung solcher Relationen.

Daraus folgt die Leitidee der ganzen Umsetzung:

> **Ein Tag trägt eine Nummer, keine Bedeutung.** Dass Tag 7 das Modul `MOD-A` ist
> und dessen Ursprung 40 mm unter der Tag-Mitte liegt, steht in einer Datei
> (der Tag-Map), nicht im Code. Der Code kennt nur Tags, Posen und Verkettung.

### 1.1 Drei Rollen von Tags

**Nur der Welt-Tag steht fest.** Alles andere in der Zelle ist beweglich — die
Module und der Roboter gleichermaßen. Der Roboter ist ein Modul wie jedes
andere; er ist nur dasjenige, das immer verwendet wird.

| Rolle | Hardware | Wofür | Pose |
|---|---|---|---|
| **Welt-Tag** (`world`) | vier AprilTags im Randbereich der Zelle | spannen das Welt-KS auf; jede Kamera, die einen davon sieht, kennt darüber ihre eigene Pose | **fest**, steht in der Tag-Map |
| **Roboter-Tag** (`robot`) | AprilTags am Roboter, mehrere erlaubt | *ist* die Roboterpose, versetzt um den CAD-Offset Tag→Roboterbasis | **beweglich — wird gemessen, nicht gelesen** |
| **Modul-Tag** (`module`) | AprilTag am Modul | *ist* die Modulpose, versetzt um den konstanten CAD-Offset Tag→Modulursprung | **beweglich — wird gemessen, nicht gelesen** |

Referenz-Tags (Welt) und Mess-Tags (Module, Roboter) durchlaufen exakt denselben
Code. Sie unterscheiden sich nur darin, ob ihre Weltpose in der Tag-Map steht
oder von dort als `null` kommt.

Die vier Welttags sind Erwartung, keine Codegrenze: `localize_camera` arbeitet
auch mit einem einzigen. `validate_tag_map` beanstandet eine abweichende Anzahl,
damit ein fehlender Tag auffällt, statt still Genauigkeit zu kosten.

Mehrere Tags am Roboter sind Absicht: von der Decke aus verdeckt der Arm leicht
einen einzelnen Tag. Jeder trägt seinen eigenen CAD-Offset auf dieselbe
Roboterbasis, `merge_by_module` mittelt sie zu einer Pose.

Die früheren Rollen `robot_table` und `reference` entfallen ersatzlos. Sie
erklärten den Robotertisch zu einem **festen** Anker — genau der Denkfehler,
den dieses Konzept behebt.

### 1.2 Die Kette

```
                       T_world_cam                T_cam_tag          T_tag_module
   Welt-KS  <───────────────────────  Kamera-KS ───────────>  Tag-KS ──────────>  Modul-KS
               aus den Welttags im          gemessen              konstant,
               selben Bild — bei                                  aus der Tag-Map
               beiden Kameras gleich
```

`T_world_cam` kann aus **zwei** Quellen kommen, und das Ergebnis sagt in
`cameraPoseOrigin`, aus welcher:

| Herkunft | Wie | Wann |
|---|---|---|
| `world_tags` | optisch: `T_world_cam = T_world_tag · inv(T_cam_tag)` | sobald ein Welttag im Bild liegt — die Messung, gewinnt immer |
| `robot_pose` | `T_world_cam = T_world_base · T_base_flansch · T_flansch_cam` | dazwischen, wenn kein Welttag zu sehen ist |

Der zweite Weg ist der Grund, warum die **Hand-Auge-Kalibrierung gebraucht
wird**. Die Handkamera sieht den Welttag nicht dauerhaft: steht sie 0,3 m vor
einem Modul, liegt der nächste Welttag ein bis zwei Meter entfernt außerhalb
des Bildfelds. Der Welttag wird deshalb **einmal zu Beginn eines
Lokalisierungsvorgangs** gebraucht — zum Ankern —, danach trägt die Kinematik.
Siehe Abschnitt 1.5.

Zusätzlich bekommt jedes Modul den Welttag, dem es am nächsten steht, und seine
Pose relativ zu diesem Tag (`T_worldtag_module`). Die Zelle ist um die Welttags
herum aufgebaut, also ist das die Zahl, gegen die ein Modul eingerichtet und
geprüft wird. Das gemeinsame Welt-KS bleibt daneben erhalten — die vier
Welttags spannen **ein** KS auf, einmalig per `build_tagmap` vermessen.

Die gesuchte Modulpose ist schlicht

```
T_world_module = T_world_cam · T_cam_tag · T_tag_module
```

Genau **eine** Funktion im Code darf diese Verkettung ausführen. Sie ist reine
Mathematik, kennt weder Kamera noch OPC UA, und ist deshalb mit Golden Values
prüfbar. Das ist die Antwort auf Risiko R11 aus
[`vision-system-integration.md`](vision-system-integration.md) (mm↔m, xyzw↔wxyz,
`T_base_cam`↔`T_cam_base` — der klassische stille Fehler).

### 1.3 Warum beide Kameras dieselbe Funktionalität sind

| | Deckenkamera (`ADP-Roboter-Lokalisierung`) | Handkamera (`ADP-HandInEye-Kamera-Pi`) |
|---|---|---|
| Aufgabe | Übersicht: wo steht der Roboter, wo stehen die Module, welcher Welttag ist dem Roboter am nächsten | genaue 6-DOF-Pose eines Moduls |
| Kamera | fest montiert | bewegt sich mit dem Roboter |
| Sicht | Welttags, Roboter und Module **gleichzeitig** | ein Welttag und das Modul aus der Nähe |
| Herkunft von `T_world_cam` | aus den Welttags im selben Bild | aus dem Welttag im selben Bild |
| Tag-Größe | groß (Modul-Tags aus Distanz) | klein (Nahaufnahme) |
| `source` im Payload | `ceiling` | `flange` |
| Ergebnis-`frameId` | `world`, sobald ein Welttag im Bild ist | `world`, sobald ein Welttag im Bild ist |

Die Unterschiede sind **ausschließlich Konfiguration und was die Kamera zu
sehen bekommt**. Erkennung, Posenschätzung, Ausreißerfilter, Mittelung,
Tag-Map-Auswertung und Payload-Aufbau sind identisch. Deshalb: eine Bibliothek,
eine `DetectionSource`, zwei Konfigurationen — kein kameraspezifischer Code.

Sieht die Handkamera gerade keinen Welttag — zwischen zwei Welttags —, bleiben
ihre Posen im Kamera-KS mit gesetztem `frameConvention`. Das ist kein
Provisorium, sondern der ehrliche Fall: ohne Anker im Bild wäre eine Weltpose
geraten.

### 1.4 Ankern: einmal am Welttag, dann alle Module in Reichweite

Die Kamera sitzt **starr** am Roboter; nur der Roboter als Ganzes bewegt sich.
`T_flansch_cam` ist damit eine Konstante der Hardware und wird **einmal**
kalibriert (`python -m tagloc.cli.calibrate_handeye`), nicht je Vorgang.

```
Einmalig:            T_flansch_cam                     Hand-Auge, Konstante

Einmal je Vorgang:   T_world_base = T_world_cam · inv(T_flansch_cam) · inv(T_base_flansch)
                     mit T_world_cam optisch aus dem Welttag

Je Modul danach:     T_world_cam  = T_world_base · T_base_flansch(jetzt) · T_flansch_cam
                     kein Welttag im Bild noetig
```

Kommt später doch wieder ein Welttag ins Bild, wird neu geankert — und die
Abweichung zwischen optischem und weitergerechnetem Wert ist die aufgelaufene
**Drift** (`anchorDriftM`/`anchorDriftDeg` im Payload). Ein Anker, der still
wegwandert, ist genau die Sorte Fehler, die sonst erst beim Danebengreifen
auffällt.

Die Roboterpose `T_base_flansch` kommt als Job-Parameter an den Hand-Pi (sieben
Floats, Meter und Quaternion xyzw). Fehlt sie oder fehlt die Hand-Auge-Datei,
bleibt alles beim optischen Weg: ohne Welttag im Bild dann eben im Kamera-KS.
Geraten wird nicht — eine falsche Roboterpose verschiebt jede Modulpose, ohne
dass man es dem Ergebnis ansieht.

### 1.5 Der Ablauf in der Zelle

1. **Deckenkamera**: sieht die Welttags, den Roboter und die Module in einem
   Bild. Sie liefert die Grobübersicht und beantwortet als Einzige die Frage,
   welcher Welttag dem Roboter am nächsten steht (`world_tag_for_robot`) —
   denn nur sie sieht beides gleichzeitig.
2. **Roboter**: richtet seine Hand-in-Eye-Kamera auf genau diesen Welttag und
   **ankert** sich daran (`anchor_from_localization`) — einmal je Vorgang.
3. **Handkamera**: fährt danach jedes Modul in Reichweite an und misst es genau
   ein. Der Welttag muss dabei nicht mehr im Bild sein; die Kamerapose kommt
   aus dem Anker und der Kinematik (`localize_camera_from_anchor`). Die
   Ergebnisse bleiben trotzdem im Welt-KS und auf die Welttags bezogen.
4. **Zusammenführen**: `merge_locations(decke, hand)` vereint beide Bilder der
   Zelle. Wo beide dasselbe Modul gemessen haben, gewinnt die Handmessung; was
   nur die Decke gesehen hat, bleibt erhalten. Bewusst eine reine Funktion und
   keine zweite Netzverbindung zwischen den Pis: beide Server publizieren
   weiter eigenständig, der Konsument ruft sie auf.

---

## 2. Modulschnitt

Neues Paket `src/tagloc/` (Tag-Lokalisierung). Nicht `src/apriltag/`, weil dieser
Ordnername mit dem PyPI-Paket `apriltag` kollidiert und das Paket mehr enthält als
Tag-Erkennung (Kalibrierung, Transformationen, Tag-Map).

```
src/tagloc/
  identity.py      Stdlib          "<name>#<mtime>" fuer configurationId
  modes.py         Stdlib          die drei Overlay-Modi des Livestreams
  observations.py  numpy-Typen     TagObservation, TagPose
  geometry.py      numpy           Posen, Verkettung, Quaternionen, Mittelung
  calibration.py   numpy           Kalibrierdatei lesen/schreiben, Auflösungsprüfung
  tagmap.py        numpy           Tag-Map lesen/schreiben, Tags in ein KS platzieren
  localize.py      numpy           Observations + Tag-Map -> Modulposen
  boards.py        cv2             Schachbrett/ChArUco auf einem Frame, Kalibrierrechnung
  detector.py      cv2 | pupil     Detektor-Adapter -> TagObservation
  pose.py          cv2             TagObservation -> T_cam_tag inkl. Reprojektionsfehler
  overlay.py       cv2             Erkennungsergebnisse ins Bild zeichnen
  frames.py        cv2/picamera2   Frame-Quellen: Pi-Kamera, Webcam, Bildordner, Einzelbild
  cli/
    calibrate.py       Kalibrierskript
    detect.py          AprilTag-Erkennungsskript
    transform.py       Koordinatentransformationsskript
    build_tagmap.py    Tags in ein gemeinsames KS platzieren
```

Dazu `src/vision_server/stream_overlay.py` als Brücke zwischen der Bibliothek
und dem Livestream sowie `tools/make_tag_sheet.py` (maßhaltiger Druckbogen) und
`tools/make_synthetic_scene.py` (Szene mit bekannter Wahrheit).

Zwei Module sind nachträglich dazugekommen, weil sich die Regel „der Server darf
cv2 nicht auf der Importkette haben" als zu schwach erwies: `identity` und
`modes` brauchen **nicht einmal numpy**. Der Server bildet die
`configurationId` beim Bauen der Quelle und normalisiert den Stream-Modus — beides,
bevor irgendetwas geladen ist.

### 2.1 Schichtung nach Testbarkeit

Die Reihenfolge ist Absicht: **je weiter oben, desto weniger Abhängigkeiten, desto
härter testbar.**

| Schicht | Module | Braucht | Testbar mit |
|---|---|---|---|
| Mathematik | `geometry` | numpy | Golden Values, kein IO |
| Daten | `calibration`, `tagmap` | numpy, stdlib-`json` | Tempdir, Round-Trip |
| Bild | `boards`, `detector`, `pose` | cv2 | synthetisch gerendertes Bild |
| Zusammenbau | `localize` | numpy | Fake-Posen |
| Aufnahme | `frames` | cv2 / picamera2 | Bildordner statt Kamera |
| Adapter | `cli/*`, `detection/apriltag.py` | alles | Fake-Kamera, Fake-Detektor |

**Harte Regel:** `geometry`, `calibration`, `tagmap` und `localize` importieren
**kein cv2**. Das ist kein Stilfrage — es bedeutet, dass der Vision-Server-Kern
die Kalibrieridentität für `configurationId` bilden kann, ohne OpenCV zu laden,
und dass diese Tests auf jedem Rechner laufen, auch bei dem
OpenCV-Versionskonflikt aus [`altlasten.md`](altlasten.md) D2 (lokal 5.0.0.93,
Pi 4.x, abweichende `aruco`-API). `from_rvec_tvec` implementiert Rodrigues
deshalb in fünf Zeilen numpy statt `cv2.Rodrigues` zu rufen.

### 2.2 Detektor-Adapter

Der Prototyp benutzt `pupil_apriltags`, die geplante Architektur laut
[`profiles.py`](../src/vision_server/profiles.py) und [`altlasten.md`](altlasten.md) D3
`cv2.aruco`. Statt jetzt zu entscheiden und später umzubauen, kommt beides hinter
ein Protokoll:

```python
@dataclass(frozen=True)
class TagObservation:
    tag_id: int
    corners: tuple[tuple[float, float], ...]   # 4 Bildecken, feste Reihenfolge
    decision_margin: float | None = None

class TagDetector(Protocol):
    family: str
    def detect(self, gray: np.ndarray) -> list[TagObservation]: ...
```

`ArucoTagDetector` (Standardweg) und `PupilAprilTagDetector` (Übergang, Vergleichsmessung)
implementieren es; `build_detector(family, backend)` wählt aus. Die
Posenschätzung liegt **bewusst nicht im Detektor**, sondern in `pose.py` — sonst
hängt die Posenqualität am Backend und ist nicht vergleichbar. Tests injizieren
einen Fake, der eine Liste von `TagObservation` zurückgibt; damit ist die gesamte
Kette ohne cv2 durchlaufbar.

### 2.3 Tags in ein gemeinsames Koordinatensystem platzieren

Das ist die Funktion, die aus einzelnen Messungen eine Karte macht:

```python
def place_tags(
    observations: Sequence[Mapping[int, Pose]],   # je Aufnahme: tag_id -> T_cam_tag
    *, anchor_tag_id: int,
) -> dict[int, Pose]:
```

Verfahren: Jede Aufnahme, die zwei Tags **gleichzeitig** zeigt, liefert die
kamerafreie Relation `T_a_b = T_cam_a⁻¹ · T_cam_b`. Diese Relationen spannen einen
Graphen auf (Knoten = Tag-IDs, Kanten = Ko-Beobachtungen). Eine Breitensuche vom
Anker-Tag aus verkettet sie zu Posen in einem einzigen KS. Tags ohne Pfad zum
Anker bleiben außen vor und werden gemeldet, nicht stillschweigend weggelassen.

Dazu zwei Begleiter:

- `merge_tag_poses(poses)` — mehrere Pfade zum selben Tag ergeben mehrere
  Schätzungen; gemittelt wird über Quaternion-Mittel statt komponentenweise.
- `residuals(observations, placed)` — Schließfehler je Kante in mm und Grad.
  **Das ist die Qualitätszahl der Karte.** Ein Board, das man aus zwei Richtungen
  aufgenommen hat, muss sich schließen; tut es das nicht, ist die Kalibrierung
  oder die Tag-Größe falsch.

Der Ablauf für den Bediener: Bilderserie aufnehmen, in der sich benachbarte Tags
jeweils paarweise überlappen → `build_tagmap.py` → Karte mit Schließfehlern →
Karte nach `config/tagmap.json`.

---

## 3. Datenformate

### 3.1 Kalibrierung — `data/calibration/<name>.json`

Weg vom OpenCV-`FileStorage`-YAML, hin zu JSON. Begründung:

- ohne cv2 lesbar → Identität und Auflösungsprüfung ohne OpenCV-Import
- `rms`, Bildgröße, Board-Geometrie und Zeitstempel passen mit hinein; heute
  landet `rms` nur auf stdout und `image_width/height` werden geschrieben, aber
  **nie gelesen**
- diffbar und im Fehlerfall von Hand lesbar

```jsonc
{
  "schema": "wsc.vision.calibration/1",
  "calibrationId": "cam_ceiling@2026-09-17T14:03:11Z",
  "frameId": "cam_ceiling",
  "imageSize": [2028, 1520],
  "cameraMatrix": [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
  "distortionCoefficients": [k1, k2, p1, p2, k3],
  "rmsReprojectionError": 0.31,
  "sampleCount": 22,
  "board": { "type": "charuco", "cols": 7, "rows": 5,
             "squareSizeM": 0.030, "markerSizeM": 0.022, "dictionary": "DICT_4X4_50" },
  "createdAt": "2026-09-17T14:03:11Z"
}
```

Ablage unter `data/` ist richtig: Die Kalibrierung gehört zu **einer physischen
Kamera**, nicht zum Repo. `.gitignore:3` schließt `data/` aus — das ist hier
erwünscht, jeder Pi erzeugt seine eigene.

`check_resolution()` wirft, wenn die Kalibrierung zu einer anderen Auflösung
gehört als das aktuelle Bild. Das ist keine Theorie: `AprilTagProfileConfig.resolution`
steht auf `(2028, 1520)`, der Kalibrierprototyp läuft über `cv2.VideoCapture(0)`
mit Default-Auflösung. Ohne Prüfung wären alle Posen um den Skalierungsfaktor falsch —
und zwar plausibel falsch, also unauffällig.

### 3.2 Tag-Map — `config/tagmap.json`

```jsonc
{
  "schema": "wsc.vision.tagmap/2",
  "frameId": "world",
  "anchorTagId": 0,
  "tagFamily": "tag36h11",
  "tags": [
    // Die vier Welttags im Randbereich der Zelle — das Einzige, was feststeht.
    { "tagId": 0, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 1, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 0, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 2, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [2.400, 1.800, 0], "orientation": [0, 0, 0, 1] } },
    { "tagId": 3, "role": "world", "sizeM": 0.100,
      "poseInWorld": { "position": [0, 1.800, 0], "orientation": [0, 0, 0, 1] } },

    // Der Roboter ist ein Modul: beweglich, mehrere Tags auf dieselbe Basis.
    { "tagId": 20, "role": "robot", "sizeM": 0.080,
      "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [0, 0, -0.120], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null },
    { "tagId": 21, "role": "robot", "sizeM": 0.080,
      "moduleId": "UR5e", "instanceId": "ur5e-1",
      "tagToModule": { "position": [-0.150, 0, -0.120], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null },

    { "tagId": 7, "role": "module", "sizeM": 0.050,
      "moduleId": "MOD-A", "instanceId": "mod-a-1",
      "tagToModule": { "position": [0, 0, -0.040], "orientation": [0, 0, 0, 1] },
      "poseInWorld": null }
  ]
}
```

`poseInWorld: null` heißt „beweglich, wird gemessen". `role` entscheidet die
Verwendung: **nur** `world` liefert `T_world_cam`, `module` und `robot` werden
lokalisiert. Die Positionen oben sind Platzhalter — die echten Weltposen der
vier Welttags werden einmalig per `python -m tagloc.cli.build_tagmap`
eingemessen.

Für den ersten Aufbautest braucht es die vier Welttags noch nicht: ein Welttag
im Ursprung (Identitätspose, dann *ist* er das Welt-KS) und ein Modultag
genügen. Vorlage dafür ist
[`config/tagmap.test.example.json`](../config/tagmap.test.example.json);
`validate_tag_map` beanstandet die fehlenden Welttags dann, bricht aber nicht ab
(siehe [E2E-Testanleitung](apriltag-e2e-test.md), „Kleiner Aufbautest").

Eine Karte im Schema `/1` wird beim Laden **migriert**: `reference` → `world`,
`robot_table` → `robot` unter Verlust seiner Weltpose. Jede Umdeutung wird
einzeln protokolliert, damit nichts still passiert — das war der Grund, sie
zunächst ganz abzulehnen. In der Praxis legte die Ablehnung aber die gesamte
Erkennung still, weil `AprilTagDetectionSource.open` daran scheiterte. Eine
unbrauchbare Karte lässt den Server jetzt **ohne Karte** weiterlaufen: die Tags
werden als `TAG-<id>` im Kamera-KS gemeldet, das Frontend bekommt also seine
Platzhalter-Boxen.

**Neuer Ordner `config/`**, weil die Tag-Map das Zellenlayout beschreibt und
versioniert gehören muss — `data/`, `concept/` und `hardware/` sind alle
gitignored, neue Dateien dort wären für `git add` still unsichtbar
([`altlasten.md`](altlasten.md) D1).

---

## 4. Die vier Werkzeuge

Alle vier akzeptieren `--source` als Kamera **oder als Bildordner**. Das ist der
zentrale Kunstgriff für Testbarkeit: der komplette Pfad ist auf abgelegten
Bildern reproduzierbar, ohne Pi und ohne Kamera.

| Skript | Aufgabe | Ohne Hardware nutzbar |
|---|---|---|
| `python -m tagloc.cli.calibrate` | Kamerakalibrierung, interaktiv oder headless aus einem Bildordner | ja, `--source ./bilder/` |
| `python -m tagloc.cli.detect` | Tags erkennen, Posen ausgeben, optional Overlay | ja |
| `python -m tagloc.cli.transform` | Pose von KS A nach KS B rechnen, Tag-Map anwenden | ja, reine Rechnung |
| `python -m tagloc.cli.build_tagmap` | Tags aus einer Bilderserie in ein gemeinsames KS platzieren | ja |

Der wesentliche Unterschied zum heutigen Prototyp: `calibrate_camera.py` hat
**keinen** Weg, ohne GUI aus vorhandenen Bildern zu kalibrieren — der gesamte
Zustand lebt in der Tastaturschleife. Der Kalibrierkern wird als
`calibrate_from_samples(samples, image_size, board)` herausgelöst; die Live-UI
wird ein dünner Aufsatz darüber.

---

## 5. Integration in den Vision-Server

Der Server-Kern bleibt unberührt. Konkret: nichts in `job.py`, `payload.py`,
`events.py`, `result_management.py`, `state_machine.py`, `address_space.py`.

### 5.1 Neue Erkennungsquelle

`src/vision_server/detection/apriltag.py`:

```python
class AprilTagDetectionSource(DetectionSource):
    profile_id = "apriltag"
    is_simulated = False

    def __init__(self, config: AprilTagProfileConfig, *,
                 camera=None, detector=None, calibration=None, tag_map=None) -> None:
```

- `frame_id` / `frame_convention` aus der Config statt hartkodiert
- `configuration_id` = Tag-Familie + Kalibrieridentität + Tag-Map-Identität →
  schließt [`altlasten.md`](altlasten.md) C4 (heute ist das Feld gebaut, aber leer)
- `open()` lädt Kalibrierung und Tag-Map, baut den Detektor, öffnet die Kamera
- `acquire_and_detect()` nimmt `samples_per_job` Frames, rechnet **jede**
  cv2-Operation über `run_blocking`, mittelt die Posen, verwirft Tags über
  `max_reproj_error_px`, respektiert `capture_timeout_s` als **eigenes** Timeout
  (`asyncio.wait_for` kann den Worker-Thread nicht töten)
- keine Detektion → `VisionJobError(DETECTION_FAILED, …)`

Attribute je Detection: `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`,
`frameTimestamp`, `recipeId`. Genau diese Keys nimmt `tests/test_payload.py:117-139`
bereits vorweg — das Payload-Schema `wsc.vision.detections/1` bleibt unverändert,
neue Keys sind additiv erlaubt.

### 5.2 Registry und Routing

```python
# detection/__init__.py — Import innerhalb der Factory, cv2 darf nicht auf die Importkette
DETECTION_SOURCES = { "hello_world": …, "calibration": …, "apriltag": _apriltag }

# config.py
DEFAULT_RECIPE_PROFILES = (
    ("", "hello_world"),
    ("hello-world", "hello_world"),
    ("calibration", "calibration"),
    ("apriltag", "apriltag"),
)
```

**Layer 1 und Layer 2 bekommen kein eigenes Profil.** Jeder Pi fährt einen eigenen
Server mit genau einer `AprilTagProfileConfig`; der Unterschied steht in
`src/vision_server/server.py`, wo `PI_IDENTITIES` ohnehin schon zwischen `pi-decke` und
`pi-hand` unterscheidet:

| Feld | Layer 1 (`pi-decke`) | Layer 2 (`pi-hand`) |
|---|---|---|
| `frame_id` | `world` | `cam_flange` |
| `calibration_path` | `data/calibration/cam_ceiling.json` | `data/calibration/cam_flange.json` |
| `tag_size_m` | 0.100 | 0.050 |
| `resolution` | 2028×1520 | 640×480 |
| `samples_per_job` | 3 | 5 |
| `max_reproj_error_px` | 3.0 | 1.5 |

Ein Profil, eine Codebasis, zwei Konfigurationszeilen — das ist die Wiederverwendung,
die gefordert war.

### 5.3 Kamera-Livestream entkoppeln

`runner.py:49-74` greift den Livestream-Handle **namentlich** über
`sources.get("image_recognition")` und dessen Attribut `.camera`. Wird die
QR-Quelle entfernt, stirbt der Livestream still (nur ein `_log.error`). Deshalb
**vor** allem anderen:

```python
def _camera_owner(sources, opened) -> SharedCamera | None:
    """Erste geöffnete Quelle, die eine geteilte Kamera hält."""
    for profile in sorted(sources):
        camera = getattr(sources[profile], "camera", None)
        if camera is not None and opened.get(profile):
            return camera
    return None
```

Damit hängt der Stream an einer Eigenschaft statt an einem Namen, und die
AprilTag-Quelle erbt ihn ohne weiteres Zutun.

---

## 6. Der QR-Code-Test fliegt raus

QR war der Machbarkeitsnachweis für die Kamerastrecke: echte Hardware, echtes
Bild, echtes Ergebnis im 40100-Payload. Der Nachweis ist erbracht, AprilTags sind
die eigentlich geplante Markertechnik. Der Ausbau in dieser Reihenfolge:

| # | Was | Wo | |
|---|---|---|---|
| 1 | Livestream vom Profilnamen entkoppeln (**zuerst**, sonst stirbt der Stream) | `src/vision_server/runner.py` | erledigt |
| 2 | QR-Tests löschen; die generischen Fälle nach `tests/test_apriltag_source.py` übernehmen | `tests/test_image_recognition.py` | erledigt |
| 3 | Quelle löschen, Factory und Registry-Eintrag entfernen | `detection/image_recognition.py`, `detection/__init__.py` | erledigt |
| 4 | Rezept `("image-recognition", …)` durch `("apriltag", "apriltag")` ersetzen | `src/vision_server/config.py` | erledigt |
| 5 | Erwartungslisten anpassen auf `["apriltag", "calibration", "hello_world"]` | `tests/test_recipe_routing.py` | erledigt |
| 6 | `qr_scan_duration_s` aus der geteilten Kamera-Config entfernen | `src/vision_server/profiles.py` | erledigt |
| 7 | `job_timeout` neu begründet: `samples_per_job × capture_timeout_s` plus Reserve → 20.0 | `src/vision_server/config.py` | erledigt |
| 8 | verwaisten QR-Scanner löschen ([`altlasten.md`](altlasten.md) C6) | `src/apriltag/caputure.py` | erledigt |
| 9 | Doku nachziehen: Abschnitte 1, 4 (Jobs), 9, 10 | [`vision-server-interface.md`](vision-server-interface.md) | erledigt |

> Durchgeführt wurde der vollständige Ausbau, weil QR sonst ohne Aufgabe im
> Produktivpfad stehen bliebe. Wiederherstellbar ist alles über
> `git checkout 5036408 -- <datei>`.

Nicht betroffen: `job.py`, `payload.py`, `events.py`, `result_management.py`,
`state_machine.py`, `address_space.py`.

---

## 7. End-to-End-Testbarkeit

Testrahmen bleibt `unittest` aus der Standardbibliothek, Idiom bleibt
handgeschriebene Fakes statt `unittest.mock`, Kommando bleibt:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Vier Stufen, jede für sich lauffähig:

**Stufe 1 — Mathematik, ohne alles.**
`tests/test_tag_geometry.py`, `tests/test_tag_map.py`, `tests/test_calibration_io.py`.
Golden Values: bekannte `rvec`/`tvec` → erwartetes Quaternion (xyzw, Vorzeichen
normiert); `invert(compose(a, b))` als Round-Trip; `place_tags` auf synthetisch
konstruierten Ko-Beobachtungen, deren Ergebnis von Hand nachrechenbar ist;
Kalibrier-Round-Trip über ein Tempdir. Kein cv2, keine Kamera.

**Stufe 2 — Bild, ohne Kamera. Das ist der eigentliche E2E-Test.**
`tools/make_synthetic_scene.py` rendert mit `cv2.aruco.generateImageMarker` und
`cv2.warpPerspective` eine Szene mit **bekannten** Tag-Posen. Der Test lässt die
volle Kette darüber laufen — Detektor → `estimate_tag_pose` → `place_tags` →
`locate_modules` — und vergleicht mit der Wahrheit. Fehlt cv2, greift
`unittest.skipUnless`.

Gemessen über alle Tags und Ansichten: Position höchstens **0,29 mm**
(Schranke 2 mm), Winkel im Median **0,031°** bei einem Ausreißer von **0,50°**
(Schranke 1°), Reprojektionsfehler höchstens **0,063 px** (Schranke 1 px). Der
Ausreißer ist keine Mehrdeutigkeit — für ihn ist die zweite IPPE-Lösung 82-fach
schlechter — sondern die Pixelquantisierung des gerenderten Markers bei 34°
Schrägblick. Die Winkelschranke steht deshalb auf 1° und nicht auf den 0,5° aus
dem Konzept: bei 0,5 saß sie exakt auf der Kante der Verteilung. Eine wirklich
falsche Pose liegt zweistellig daneben (die verworfene Alternative: 67,8°), die
Schranke trennt also weiter richtig von falsch.

Testbilder werden **im Test erzeugt, nicht committet**: `.gitignore:14` schließt
`*.jpg` aus, und das Repo hält es ohnehin so, dass keine Fixture-Dateien existieren.

**Stufe 3 — Server, ohne Kamera.**
`tests/test_apriltag_source.py` mit `FakeCamera` (`open`/`close`/`push_frame`/`latest_frame`)
und Fake-Detektor, nach dem Muster des heutigen `tests/test_image_recognition.py`:
Quelle wird nie `open()`et, Detektor und Kalibrierung werden injiziert. Geprüft
werden `run_blocking`-Nutzung, Aufnahme-Timeout, gefüllte `configurationId`,
`frame_id`/`frame_convention` aus der Config, Attribute im Payload und der
`DETECTION_FAILED`-Pfad ohne Tag.

**Stufe 4 — echte Hardware.** `python -m tagloc.cli.detect --source camera --overlay`,
beschrieben in [`apriltag-referenz.md`](apriltag-referenz.md). Nicht Teil der Suite.

---

## 8. Was Sven gemacht hat

Sven Nachtigal (`NobbisCode`) hat zwischen dem **09. und 11.09.2026** in
**11 Commits** die Kamera- und Job-Seite des Vision-Servers gebaut — ohne eigenen
Branch, erst auf `main`, dann direkt auf `feature/vision-server`; Merge-Punkt mit
Merics parallelem Strang ist `9f8ea16`.

| Bereich | Commits | Ergebnis |
|---|---|---|
| OPC-UA-Grundgerüst | `375311f`, `5e63042`, `29820d6`, `bf00021`, `685f3a1` | legt `src/OPCUA/server.py` überhaupt erst an — die Datei, in die später `install_vision_machine()` eingehängt wurde; dazu der Debug-Client `print_setpoint.py`, `.gitignore` und die `asyncua`-Version |
| Multi-Job-Fähigkeit | `f2aadf9`, `f835e46` | aus dem Ein-Job-Server (nur Hello-World) wird ein Server mit mehreren `RecipeId`-Profilen; neu: `detection/script_runner.py`, eine `DetectionSource`, die ein Python-Skript als Subprozess startet, plus die Platzhalter unter `src/jobs/` |
| Echte Kamera und QR | `3a7b7d9`, `104194e`, `77540d1` | zuerst QR im Subprozess, dann die Umstellung auf `SharedCamera`: genau ein Picamera2-Open, ein Capture-Loop, mehrere Leser. Dazu der Kamera-Livestream als Base64-JPEG im Knoten `LatestCameraFrame` (`camera_stream.py`, `CameraStreamConfig`) und die In-Process-Quelle `detection/image_recognition.py` |
| Job-Abbruch | `5036408` | `Stop` der `AutomaticModeStateMachine` wirklich implementiert: Subprozess `terminate()`, nach 2 s `kill()`, Rückkehr nach `Ready`, `ResultReadyEvent` mit `resultState=7` (`CANCELLED`), `stop_timeout` |

Von ihm angelegt: `src/OPCUA/server.py`, `src/OPCUA/print_setpoint.py`,
`src/vision_server/camera.py`, `camera_stream.py`, `detection/script_runner.py`,
`detection/image_recognition.py` sowie die Tests `test_camera_stream.py`,
`test_image_recognition.py`, `test_script_runner.py` (und Erweiterungen an
`test_job_runner.py`).

Dokumentiert hat er ausschließlich [`vision-server-interface.md`](vision-server-interface.md) —
rund 115 von 462 Zeilen, darunter die Abschnitte „Verfügbare Jobs (`RecipeId`)",
„`Stop`" und vollständig „Kamera-Livestream".

**Bezug zu diesem Plan:** Sven hat an AprilTags selbst nicht gearbeitet — aber
`SharedCamera` und `ImageRecognitionDetectionSource` sind exakt die Infrastruktur
und die Vorlage, auf der die AprilTag-Quelle aufsetzt. Die QR-Erkennung, die
hier ausgebaut wird, war der Beweis, dass diese Strecke trägt; was bleibt, ist
die Kamera darunter. Sein `script_runner.py` bleibt ebenfalls in Betrieb: der
`calibration`-Job läuft weiter darüber.

---

## 9. Umsetzungsreihenfolge

Die Schritte 1–7 und 9 sind umgesetzt, die Suite ist grün.

| # | Schritt | Stand |
|---|---|---|
| 1 | Livestream entkoppeln (`runner.py`) + Test | erledigt, `tests/test_camera_owner.py` |
| 2 | `geometry.py` + Golden-Value-Tests | erledigt |
| 3 | `calibration.py`, `tagmap.py` inkl. `place_tags` + Tests | erledigt |
| 4 | `boards`, `detector`, `pose`, `localize`, `frames`, `overlay` + synthetischer E2E-Test | erledigt |
| 5 | CLI-Skripte; Kalibrierkern aus `calibrate_camera.py` herausgelöst und parametrisiert | erledigt |
| 6 | `detection/apriltag.py` + Registry + Rezept + Tests | erledigt |
| 7 | QR-Ausbau nach Abschnitt 6 | erledigt |
| 8 | `src/apriltag/` auflösen | **zurückgestellt** |
| 9 | Konfiguration je Pi in `server.py`, Doku nachziehen | erledigt |

Zu Schritt 8: Der Prototypordner bleibt vorerst stehen und ist in
[`MANUAL_TEST.md`](../src/apriltag/MANUAL_TEST.md) als abgelöst gekennzeichnet.
Ihn zu löschen, bevor die neue Kette einmal an der echten Pi-Kamera gelaufen
ist, nähme die einzige Vergleichsmöglichkeit weg. `src/jobs/calibrate.py` ist
dagegen bereits umgestellt — es ist jetzt eine Bereitschaftsprüfung statt eines
Platzhalters.

Dazu kamen zwei Dinge, die im ursprünglichen Plan fehlten:

- **Livestream-Overlay mit umschaltbarem Modus** — der beschreibbare Knoten
  `CameraStreamMode` und `stream_overlay.py`; siehe
  [`vision-server-interface.md`](vision-server-interface.md) Abschnitt 10.1.
- **Explizite String-NodeIds** für `LatestCameraFrame` und `CameraStreamMode`.
  `add_variable(own_idx, …)` vergibt sonst automatisch numerische Ids
  (`ns=3;i=1`), die sich verschieben, sobald jemand einen Knoten davor anlegt —
  und das Backend abonniert diese Knoten fest.

---

## 10. Offene Punkte und was dieser Plan dazu vorschlägt

Die Fragen aus [`concept/offene_punkte.md`](../concept/offene_punkte.md) bleiben
Entscheidungen des Teams. Was dieser Entwurf nahelegt:

| Offener Punkt | Vorschlag aus diesem Entwurf |
|---|---|
| Wann wird der Welt-Tag referenziert? | **bei jedem Layer-1-Scan.** Die Deckenkamera sieht das Board ohnehin; `T_world_cam` wird pro Job neu bestimmt und mitgeloggt, damit Drift sichtbar wird statt sich still fortzupflanzen. |
| Format der Übergabe Layer 1 → Layer 2 | **3D-Pose im Welt-KS**, also `position` + `orientation` des bestehenden Payloads — keine Pixelkoordinate. Damit bleibt das Schema `wsc.vision.detections/1` unverändert. |
| Wer transformiert Welt-KS → Roboterbasis? | **die Deckenkamera misst sie**. Die Roboter-Tags tragen den CAD-Versatz Tag→Roboterbasis, `T_world_base` fällt damit als Messergebnis an. Fest hinterlegt ist sie nicht mehr: der Roboter ist beweglich. |
| Rolle des Robotertisch-Tags | **entfällt.** Der Tisch war nie ein fester Anker — das war der Denkfehler. Der Roboter trägt eigene Tags mit der Rolle `robot` und wird gemessen wie jedes Modul. |
| Welcher Welttag gilt für den Roboter? | der ihm nächstgelegene, aus dem Deckenbild bestimmt (`world_tag_for_robot`). Nur die Deckenkamera sieht Roboter und Welttags gleichzeitig, also kann nur sie diese Frage beantworten. |
| Wie fallen Decken- und Handmessung zusammen? | `merge_locations(decke, hand)` — reine Funktion in `tagloc`. Die genauere Quelle (`flange`) gewinnt, was nur die Decke sah bleibt erhalten. Keine Verbindung zwischen den beiden Pis nötig. |
| Hand-Auge-Kalibrierung für die Handkamera | **gerechnet und werkzeugseitig da**, aber an der echten Hardware noch nicht durchgeführt. `T_flansch_cam` ist eine Konstante (Kamera starr am Roboter) und wird einmal per `tagloc.cli.calibrate_handeye` bestimmt. Solange keine gemessene Datei vorliegt, wirkt nur der optische Weg, und `auto_execute` bleibt `False` — ein automatisches Anfahren erkannter Posen darf erst scharf geschaltet werden, wenn die Kalibrierung am Roboter belegt ist. |
| Repositionierungsstrategie Layer 2 | **nicht Teil dieser Bibliothek.** `tagloc` meldet „kein Tag gefunden" als `DETECTION_FAILED`; was der Roboter daraufhin tut, gehört in die Ablaufsteuerung. |
| Soll-Anzahl der Module | aus der Tag-Map ableitbar: jeder Eintrag mit `role: "module"` ist ein erwartetes Modul. Damit ist das Abbruchkriterium „alle Modul-Tags gesehen" ohne zusätzliche Konfiguration formulierbar. |
