# Tag-Position per Hand-Kamera einmessen — Bezugskanten statt Mesh-Matching

**Status:** in Arbeit — 20.10.2026
**Verantwortlich:** Meric, `Agent: Tag-Position mit der Kamera über Bezugskanten einmessen`
**Thema:** apriltag
**Branch:** `tag-einmessen` (dieses Repo und `webskillcomposition`)

## Ziel

Die Tag-Position eines Moduls (`T_module_tag`) wird nicht mehr mit dem Zollstock
gemessen, sondern mit der Hand-Kamera über die Bezugskanten. Hinterher liefert der
Hand-Pi pro Bezugskante die gemessene Lage im Tag-KS und eine korrigierte Tag-Pose
im Modul-KS. Vorher wurden die Abstände von Hand gemessen, und der Messfehler ging
1:1 in jede Modulpose ein.

### Kontext

`T_module_tag` (`ModuleTag.position`/`rotationDeg` im Modulkatalog, nur im Frontend; die
Tag-Map des Pi enthält nur Welt-Tags) wird heute von Hand bestimmt: im Katalog Fläche + Ecke bzw. zwei Kanten wählen
(`placement`, siehe `modell-flaechen-tag-platzierung.md`), dann mit dem Zollstock die
Abstände messen. Der Fehler geht 1:1 in jede Modulpose ein.

Der erste Plan (Tiefen-ICP gegen das ganze Mesh) ist **verworfen** (siehe Abschnitt
„Verworfen“): Die Module ändern sich ständig, die Modelle werden nicht gepflegt — ein
Abgleich gegen das ganze Modell misst dann gegen veraltete Geometrie.

**Neuer Ansatz: Die Kamera ersetzt den Zollstock.** Vom Modell wird nur noch gebraucht,
was der Zollstock auch braucht: die **zwei Bezugskanten**, die beim Platzieren im Katalog
gewählt werden (Tischkante, Profil-Stirnkante …). Das sind langlebige physische Kanten;
der Rest des Modells darf veraltet sein. Die Hand-Kamera sieht Tag und Kanten **im selben
Bild** → Messung im Tag-KS, ganz ohne Roboterkinematik und Hand-Auge-Fehler. Der
Katalogwert bleibt Startwert und bestimmt das Suchfenster.

## Bereits gelesen

- `CLAUDE.md` (Arbeitsplanpflicht, Regeln für Änderungen)
- `doc/projektdoku/arbeitsplaene/_vorlage.md`
- `doc/projektdoku/arbeitsplaene/README.md` (kein offener `apriltag`-Plan zum Thema)
- `doc/projektdoku/arbeitsplaene/layer2-measuretags.md`: Muster für Schnittstellen,
  Intrinsik, Fehlerfälle; `measure_tags` ist die Grundlage dieser Messung
- `doc/projektdoku/arbeitsplaene/modell-flaechen-tag-platzierung.md`: `placement`-Format,
  `EdgeLine`, Bedienung „Auf Modell platzieren“
- Freigegebener Plan `thema-tag-positionen-auf-modulen` (Konzept, Schnittstellen,
  Agenten-Aufteilung, Verifikation)

## Betroffene Dateien

Diese Liste reserviert die Dateien. Parallele Änderungen daran werden vorher abgesprochen.

**Neu:**
- `src/tagloc/reference_edges.py`: reine Funktionen (numpy/cv2), Rechnung und Ansichtsplanung
- `src/vision_server/tagreference.py`: Anfrage parsen, Ergebnis-JSON bauen (wie `measuretag.py`)
- `tests/test_reference_edges.py`
- `tests/test_tagreference.py` inkl. OPC-UA-Methodentest (Muster `tests/test_measuretag.py`)

**Geändert:**
- `tools/make_synthetic_scene.py`: `render_reference_scene` (synthetischer Renderer)
- `src/vision_server/detection/apriltag.py`: `measure_tag_reference` (frische Bilder wie `measure_tags`)
- `src/vision_server/runner.py`: `_install_tag_reference`, BUSY-Sperre
- `src/vision_server/address_space.py`: Knoten `MeasureTagReference`, `PlanTagReferenceView`, `LastTagReference`

## Schnittstellen

### Python — `src/tagloc/reference_edges.py` (neu, reine Funktionen, numpy/cv2)

```python
@dataclass(frozen=True)
class ReferenceLine:          # Bezugskante, Modul-KS, Meter
    id: str
    start: np.ndarray         # (3,)
    end: np.ndarray           # (3,)

@dataclass(frozen=True)
class LineMeasurement:
    id: str
    found: bool
    guess_point_tag: np.ndarray; guess_direction_tag: np.ndarray   # Startwert im Tag-KS
    point_tag: np.ndarray | None; direction_tag: np.ndarray | None # gemessen im Tag-KS
    offset_m: float | None    # senkrechte Verschiebung gemessen − Startwert, in der Tag-Ebene
    angle_deg: float | None   # Drehung gemessen − Startwert um Tag-z
    support_px: float; residual_px: float; contrast: float
    candidates: int; ambiguous: bool
    reason: str | None        # "NOT_VISIBLE" | "NO_EDGE" | "PERPENDICULAR_TO_TAG" | None
    extent_m: float = 0.0     # Länge des gemessenen Kantenstücks in der Tag-Ebene; gewichtet die Drehung, nicht im Ergebnis-JSON

@dataclass(frozen=True)
class InPlaneCorrection:
    dofs: str                 # "full" | "partial" | "none"
    delta_xy_m: np.ndarray    # (2,) im Tag-KS
    delta_rot_deg: float
    pose_module_tag: Pose     # korrigiertes T_module_tag
    residual_m: float

@dataclass(frozen=True)
class ViewPlan:
    pose_tag_cam: Pose; standoff_m: float; fits: bool; uncovered: list[str]

def measure_reference_lines(gray, calib, pose_cam_tag, pose_module_tag_guess,
        lines: Sequence[ReferenceLine], *, tag_size_m: float,
        search_band_m: float = 0.015, tag_margin_m: float = 0.01) -> list[LineMeasurement]
def fit_in_plane_correction(measurements: Sequence[LineMeasurement],
        pose_module_tag_guess: Pose) -> InPlaneCorrection
def average_corrections(corrections: Sequence[InPlaneCorrection]) -> tuple[InPlaneCorrection, dict]  # Streuung mm/deg
def plan_reference_view(pose_module_tag_guess, lines, calib, *, tag_size_m,
        line_extent_m=0.10, margin=0.10, min_standoff_m=0.25, max_standoff_m=0.60,
        tilt_deg=0.0) -> ViewPlan
def catalog_rpy_deg(rotation) -> np.ndarray   # Katalogformat: RPY feste Achsen X,Y,Z, Grad
```

`ValueError` bei ungültigen Eingaben (Kante kürzer als 2 cm, Größe ≤ 0, nicht endliche Werte, Auflösung passt nicht zur Kalibrierung).

Wiederverwendet werden `tagloc.geometry` (`compose`, `invert`, `pose_to_dict`,
`from_position_quaternion`), `tagloc.lens` (`project_points`, `undistort_points`) und
`tagloc.pose.tag_object_points`.

### Python — `src/vision_server/tagreference.py` (neu, ohne asyncua und ohne Kamera)

```python
parse_request(text: str) -> TagReferenceRequest          # ValueError mit Klartext bei Schema-, Pflicht- oder Grenzverletzung
combine_tag_pose(tag_poses) -> tuple[np.ndarray, bool]   # (T_cam_tag, ambiguous) aus den Bildern mit Tag
keep_guess_rotation(correction, pose_module_tag_guess) -> InPlaneCorrection  # nur der Tag-Ursprung wandert, Drehung aus Startwert
summarize_lines(per_frame) -> list[dict]                 # eine Zeile je Kante über alle Bilder
reference_warnings(*, correction, lines, search_band_m, tag_ambiguous, intrinsics_source) -> list[str]
build_result(*, ..., error: str | None = None) -> dict   # wsc.vision.tagreference.result/1
build_view(plan=None, *, error: str | None = None) -> dict  # wsc.vision.tagreference.view/1
dumps(result: dict) -> str                               # json, allow_nan=False
```

`TagReferenceRequest` (frozen): `tag_id`, `tag_size_m` (`None` = Tag-Map bzw. config),
`pose_module_tag_guess`, `lines`, `search_band_m`, `frames`, `line_extent_m`, `tilt_deg`.

### OPC UA (nur Hand-Pi, `hand_eye_path` gesetzt; unter `VisionMachine`, verlinkt unter `VisionProgram`)

| Element | NodeId | Typ |
| --- | --- | --- |
| `MeasureTagReference` | `ns=<vision>;s=VisionMachine.MeasureTagReference` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` |
| `PlanTagReferenceView` | `ns=<vision>;s=VisionMachine.PlanTagReferenceView` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` — rechnet nur, kein Bild |
| `LastTagReference` | `ns=<vision>;s=VisionMachine.LastTagReference` | String (JSON), nur zur Anzeige |

| Error | `MeasureTagReference` | `PlanTagReferenceView` |
| --- | --- | --- |
| `OK` | gemessen. Auch wenn Kanten fehlen (dann `dofs` reduziert bzw. `none`). | geplant, auch mit `fits: false`. |
| `DETECTION_FAILED` | `NO_FRAME`, `TAG_NOT_SEEN`; auch ein `ValueError` der Messung (z. B. Bildgröße passt nicht zur Kalibrierung) | entfällt (kein Bild) |
| `INVALID_ARGUMENT` | Anfrage-JSON bzw. Werte ungültig | wie links |
| `INVALID_STATE` | Vision-System nicht `Operational` | wie links |
| `BUSY` | Job, Kamera-Kalibrierung, Hand-Auge-Sitzung, `MeasureTags` oder weitere Einmessung läuft | kein `BUSY`: rechnet nur, kein Bild |

Bei Ablehnung steht im JSON `"<CODE>: Text"`; bei Mess-Fehlern (`NO_FRAME`, `TAG_NOT_SEEN`)
der nackte Code. `ResultJson` ist immer gültiges JSON.

`LastTagReference` wird nach jeder Messung geschrieben, auch bei `DETECTION_FAILED`, nicht
bei Ablehnungen (`INVALID_ARGUMENT`, `INVALID_STATE`, `BUSY`).

### Daten / Payload

**Anfrage** `wsc.vision.tagreference.request/1` (für beide Methoden):

```json
{ "schema": "wsc.vision.tagreference.request/1",
  "tagId": 7, "tagSizeM": 0.08,
  "moduleTagGuess": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "lines": [ { "id": "A", "start": [x,y,z], "end": [x,y,z] },
             { "id": "B", "start": [x,y,z], "end": [x,y,z] } ],
  "searchBandM": 0.015, "frames": 3,
  "view": { "lineExtentM": 0.10, "tiltDeg": 10 } }
```

- `moduleTagGuess` = `T_module_tag` aus dem Katalog (`position`/`rotationDeg` umgerechnet).
- `lines` = echte 3D-Kanten im Modul-KS (die `EdgeLine`s der Platzierung, **nicht** auf die
  Fläche projiziert); 1–4 Stück.
- `tagSizeM` (optional): Vorrang Anfrage > Tag-Map > `config.tag_size_m`.
- `searchBandM` (Standard 0,015; 0,003–0,05 m), `frames` (Standard 3; 1–10),
  `view.lineExtentM` (Standard 0,10; (0, 1] m), `view.tiltDeg` (Standard **10**; |tiltDeg| < 80).
- `lines`: `id` nicht leer und eindeutig, jede Kante ≥ 2 cm.
- `moduleTagGuess.orientation` ist ein Quaternion `[qx, qy, qz, qw]`: es wird normiert,
  ein Nullquaternion ist ungültig.
- Unbekannte Felder werden ignoriert.

**Ergebnis** `wsc.vision.tagreference.result/1`:

```json
{ "schema": "wsc.vision.tagreference.result/1",
  "capturedAt": "…", "capturedUntil": "…", "imageSize": [640,480],
  "intrinsicsSource": "calibration|realsense_factory|placeholder", "framesUsed": 3,
  "tag": { "poseInCamera": {…}, "reprojectionErrorPx": 0.4, "viewAngleDeg": 3.1, "ambiguous": false },
  "lines": [ { "id": "A", "found": true, "offsetMm": 1.8, "angleDeg": 0.4,
               "supportPx": 210, "residualPx": 0.3, "contrast": 41, "candidates": 2,
               "ambiguous": false, "reason": null } ],
  "correction": { "dofs": "full", "deltaMm": [1.8, -0.6], "deltaRotDeg": 0.4,
                  "moduleTag": { "position": [...], "orientation": [...] },
                  "position": [...], "rotationDeg": [...],
                  "spreadMm": 0.2, "spreadDeg": 0.05, "residualMm": 0.3 },
  "warnings": [],
  "error": null }
```

- `framesUsed`: Bilder, in denen der Tag erkannt wurde.
- Mehrdeutige Tag-Pose: Gibt es mindestens ein eindeutiges Bild, wird die Drehung nur über
  diese Bilder gemittelt, die Position über alle. Eine Pose gilt für alle Bilder (der Roboter
  steht), die Korrektur bleibt `full`. Gibt es kein eindeutiges Bild, werden die Kanten mit der
  mehrdeutigen Pose gemessen; die Orientierung bleibt dann exakt der Startwert
  (`deltaRotDeg`, `spreadDeg` = 0), nur der Tag-Ursprung wandert, und es gibt eine Warnung.
- `lines[]`: je Kante ein Eintrag über alle Bilder. `found`, sobald in einem Bild gefunden;
  Werte (`offsetMm`, `angleDeg`, `supportPx`, ...) als Mittel über die eindeutigen Bilder, sonst
  über die gefundenen; `ambiguous`, sobald in irgendeinem Bild mehrdeutig; `reason` nur bei
  nicht gefundenen Kanten.
- `warnings[]`: Platzhalter-Intrinsik; Tag mehrdeutig; `dofs` `none` (Startwert unverändert) oder
  `partial`; mehrdeutige Kante; Korrektur über dem Suchband bzw. eine Kantenverschiebung
  ≥ 0,9 × Suchband (Startwert vermutlich falsch).
- Fehlerfall: `correction: null`, `lines: []`, `tag: null`. `error` ist bei Mess-Fehlern der nackte
  Code (`TAG_NOT_SEEN`, `NO_FRAME`), bei Ablehnung `"<CODE>: Text"`.

**Planung** `wsc.vision.tagreference.view/1` (von `PlanTagReferenceView`, Antwort):

```json
{ "schema": "wsc.vision.tagreference.view/1",
  "camInTag": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "standoffM": 0.38, "fits": true, "uncovered": [], "error": null }
```

`camInTag` ist die Kamerapose im Tag-KS (Kamera-KS OpenCV: +Z aus der Linse). Die Kamera
schaut entlang −Tag-z; Bild-x = Tag-x, Bild-y = −Tag-y. `tiltDeg` kippt um Tag-x durch den
Zielpunkt (positiv: Kamera nach −y). `fits: false`: Der Tag ist immer im Bild; Kanten werden
nach Abstand zum Tag nacheinander dazugenommen, solange alles bei `max_standoff` passt.
`uncovered` nennt die übrigen Kanten (ggf. `"tag"`). Ablehnung: `camInTag: null`, `fits: false`,
`uncovered: []`, `error` mit `"<CODE>: Text"`.

### Katalog (Frontend)

Die Datenform steht im Abschnitt „Frontend“ (`ModuleTag.measurement`, `placement.lines`).
`position`/`rotationDeg` werden mit `correction.position`/`correction.rotationDeg`
überschrieben, `placement` bleibt als Protokoll des Startwerts. **Modul-Tags stehen bewusst
nicht in der Tag-Map des Pi** (dort nur Welt-Tags, siehe Kopfkommentar `moduleCatalog.ts` in
`webskillcomposition`). Der Katalog wirkt nur im Frontend: Layer-1-Platzierung und
`moduleTagGuess` der Layer-2-Anfragen.

## Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Tag nicht im Bild | `DETECTION_FAILED`, `TAG_NOT_SEEN` |
| Kein frisches Bild | `DETECTION_FAILED`, `NO_FRAME` |
| Kante außerhalb des Bilds | `found: false`, `reason: "NOT_VISIBLE"` |
| Keine Kante im Suchband | `found: false`, `reason: "NO_EDGE"` |
| Kante mehrdeutig (Item-Nut) | `ambiguous: true`; die Kante geht in keine Korrektur ein (`dofs` ggf. reduziert) |
| Kante (fast) senkrecht zur Tag-Ebene | `reason: "PERPENDICULAR_TO_TAG"` |
| Korrektur über dem Suchband bzw. Kantenverschiebung ≥ 0,9 × Suchband | Warnung; Startwert vermutlich falsch |
| Keine Kante verwertbar | `dofs: "none"`, Startwert unverändert, Warnung |
| Tag `ambiguous` | Gibt es eindeutige Bilder: Drehung über sie, Position über alle, Korrektur `full`. Sonst: Drehung exakt aus dem Startwert, nur der Tag-Ursprung wandert (`keep_guess_rotation`), Warnung |

## Vorgehen

### Rechnung (je Bild, Hand-Pi)

1. AprilTag → `T_cam_tag` (vorhandene `measure_tags`-Pipeline, Intrinsik wie MeasureTags).
2. Jede Bezugskante (3D-Strecke im Modul-KS, aus dem Katalog) über den Startwert ins Tag-KS:
   `T_tag_module_guess = inv(T_module_tag_guess)`; Höhe `h` der Kante über der Tag-Ebene
   (Tag-z) merken.
3. Kante ins Bild projizieren (`lens.project_points`, mit Verzeichnung), auf den sichtbaren
   Teil kürzen, Tag-Fläche + Rand ausmaskieren (Tag-Ränder sind selbst starke Kanten).
4. Kantensuche im Band ±`searchBandM` (Standard 15 mm, in px umgerechnet): alle ~4 px ein
   Querprofil auf dem leicht geglätteten Graubild, Gradientenmaxima subpixelgenau
   (Parabel) → Kandidatenpunkte; RANSAC-Geradenfit; Wahl nach Nähe zum Startwert ×
   Stützlänge × Kontrast, gleiche Polarität entlang der Kante. Zweiter Kandidat mit
   ≥ 70 % der Bewertung und > 2 px Abstand → `ambiguous` (Item-Nuten!).
5. Stützpunkte entzerren (`lens.undistort_points`) → Sehstrahlen → Schnitt mit der Ebene
   `z_tag = h` → 3D-Gerade im Tag-KS (PCA).
6. **Korrektur in der Tag-Ebene** (3 DoF: x, y, θ): starre 2D-Transformation, die die
   Startwert-Kanten auf die gemessenen abbildet (kleinste Quadrate Punkt-zu-Gerade).
   2 nicht parallele Kanten (≥ 20°) → `dofs: "full"`; 1 Kante → nur senkrecht + Drehung
   (`"partial"`, entlang der Kante unverändert); 0 → `"none"`.
   `T_tag_module_neu = D · T_tag_module_guess`, `T_module_tag_neu = inv(…)`.
   Neigung und Abstand zur Fläche (`offsetM`) bleiben aus dem Startwert (Tag liegt bündig).
7. Mehrere Bilder (`frames`): Kanten je Bild messen, Korrektur über die Bilder mit der höchsten
   `dofs`-Stufe gemittelt, Streuung melden. Mehrere Ansichten sind dadurch trivial — die Steuerung ruft mehrfach auf und
   mittelt, keine Vorwärtskinematik nötig.

### Kamera passend platzieren

`plan_reference_view` berechnet die Kamerapose **im Tag-KS** (`T_tag_cam`): frontal
(Kippwinkel gegen Spiegelung; Anfrage-Standard 10 Grad, Python-Default 0), zentriert über Tag + den tagnahen Stücken beider
Kanten (Standard je 10 cm), Abstand so, dass alles mit 10 % Rand ins Bild passt
(0,25–0,60 m). Die Steuerung rechnet

`Flanschziel = T_base_tag (aus MeasureTags/Layer 2) · T_tag_cam · inv(T_flansch_cam)`,

fährt hin und ruft die Messung auf. Passt nicht alles in ein Bild → `fits: false`, `uncovered` nennt die fehlenden Kanten. Ohne sie
fehlt dann in der Messung die Kante (`NOT_VISIBLE`); v1 ist das eine Teilkorrektur.

### Verifikation

- `PYTHONPATH=src python3 -m unittest discover -s tests -t .` (mit der Umgebung, in der
  cv2 installiert ist): alle alten und neuen Tests grün.
- Synthetisch (`reference_edges`): Startfehler 1 cm / 2° → Ergebnis < 0,5 mm / < 0,2°;
  Nut-Doppelkante → `ambiguous` oder richtige Kante; eine Kante → `partial`; Kante außerhalb
  → `NOT_VISIBLE`; Ansichtsplanung: projizierte Tag-Ecken und Kantenstücke liegen im Bild.
- OPC UA (`tagreference`): Fake-Kamera mit synthetischem Bild, Aufruf über asyncua-Client,
  Ergebnis-JSON geprüft.
- Am Pi (später, Nutzer): Tag mit Zollstock-Referenz, Ziel < 1–2 mm / < 0,5°. Ergebnis in
  `doc/projektdoku/` festhalten.

## Messwerte (synthetisch)

Synthetische Szenen aus `tools/make_synthetic_scene.py` (`render_reference_scene`), Startfehler
1 cm / 2°. „Detektor-Pose“ heißt: Tag-Pose aus der AprilTag-Erkennung statt der echten Pose.
Fehler = Abweichung von der bekannten Wahrheit. **Nicht am Pi gemessen** (das steht noch aus).

**S1, Einzelbild, 1600 × 1200, f = 1600, Tag 80 mm:**

| Fall | Positionsfehler | Drehfehler |
| --- | --- | --- |
| Echte Pose, frontal | 0,024 mm | 0,014° |
| Detektor-Pose, 15° gekippt | 0,27–0,28 mm | 0,045° |
| Detektor-Pose, frontal | 0,24 mm | 0,013° |
| Verzeichnung k1 = −0,12 | 0,034 mm | – |
| Fisheye-Verzeichnung | 0,029 mm | – |
| Unschärfe 3 px + Rauschen σ = 6 | 0,015 mm | – |
| Kante 3 cm unter der Tag-Ebene, echte Pose | 0,010 mm | – |
| Kante 3 cm unter der Tag-Ebene, Detektor-Pose, Kippung 5–20° | 0,05–0,21 mm | – |

**S2, Messung durch `measure_tag_reference`, 640 × 480, f = 600, 3 Bilder:**

| Ansicht | Positionsfehler | Drehfehler |
| --- | --- | --- |
| Kippung 10°, Abstand 0,296 m | 0,63 mm | 0,07° |
| Kippung 15°, Abstand 0,298 m | 0,23 mm | 0,10° |
| Kippung 10°, Abstand 0,35 m | 0,25 mm | 0,11° |

**Grenzen:**

- Frontale Sicht auf einen erhöhten Tag liefert die Spiegellösung der Tag-Pose (13°). Daher
  Standard-Kippung 10°.
- Nut mit gleicher Polarität wie die Kante: `ambiguous` → Teilkorrektur (`partial`).
- Fast unsichtbare Nut (< 8 Graustufen Kontrast): es wird still die innere Kante gemessen,
  Fehler 5 mm. Physikalisch nicht erkennbar.
- Falsche Tag-Größe: alle Kanten `NO_EDGE`.

## Frontend (webskillcomposition, Branch `tag-einmessen`)

Die Steuerung läuft im Frontend (`webskillcomposition`) gegen die Schnittstellen oben. Pfade
der Dateien: unten im Abschnitt „Dateien“.

### Ablauf

1. **Modulkatalog:** „Place on model“ speichert die echten 3D-Bezugskanten in `placement.lines`
   (corner: beide Eckkanten A und B; edges: die zwei gewählten Kanten).
2. **Layer 1** (Deckenkamera) platziert die Module grob.
3. Grob-Tagpose `tagInBase` aus `approachTags`.
4. `centreOnTag` (MeasureTags, Standoff ca. 0,35 m) liefert die genaue Tagpose.
5. `PlanTagReferenceView` liefert `camInTag`. Flanschziel = `tagInBase · camInTag · inv(T_flansch_cam)`;
   dorthin fahren.
6. `MeasureTagReference` mit `moduleTagGuess` aus dem Katalog und `lines` aus `placement.lines`.
7. Ergebnis: `position`/`rotationDeg` und `measurement` in den Katalog (Backend
   `/api/module-catalog`).
8. Danach Layer 1 neu scannen.

### Einmal-Regel

Vorgabe des Nutzers vom 20.10.2026: Jeder Tag wird nur einmal eingemessen, und zwar dann, wenn
sein Modul im Modulkatalog neu angelegt oder bearbeitet wurde.

- `ModuleTag.measurement` fehlt = ausstehend.
- Speichern aus dem Editor verwirft `measurement` bei Tags, die neu sind oder deren `tagId`,
  `sizeM`, `position`, `rotationDeg` oder `placement` sich geändert haben. Unverändertes
  Speichern behält sie.
- Import und Laden lassen `measurement` unverändert.
- Eingemessen werden nur ausstehende Tags von Modulen, die in der Zelle platziert sind.
- Der Start erfolgt nur per Klick (Roboterbewegung).

### Datenformat

```ts
interface TagReferenceLine { id: string; start: Vec3; end: Vec3 }   // Modul-KS, m, nicht projiziert
TagPlacement.lines?: TagReferenceLine[]
interface TagCameraMeasurement { method: "camera-edges"; measuredAt: string; dofs: "full" | "partial";
  guess: { position: Vec3; rotationDeg: Vec3 }; deltaMm: [number, number]; deltaRotDeg: number;
  spreadMm: number; lines: string[] }
ModuleTag.measurement?: TagCameraMeasurement
```

`placement` bleibt nach der Messung als Protokoll des Startwerts erhalten.

### Alte Platzierungen ohne `lines`

Die Kanten werden aus `edgeA`/`edgeB` abgeleitet: Länge 0,15 m; bei corner wird die zweite Kante
entlang `inward` gelegt (90°-Annahme). Solche Platzierungen zeigt die Oberfläche mit dem Hinweis
„Kanten geschätzt“.

### Dateien

Pfade relativ zu `frontend/src/features/cell-modules/`, außer `piServers.ts`
(`frontend/src/features/opcua-server/config/`):

- `config/moduleCatalog.ts`: Typen `TagReferenceLine`, `TagCameraMeasurement`, `lines`, `measurement`
- `model/catalogStore.ts`
- `model/tagPlacement.ts`
- `model/tagMeasurementState.ts` (neu): Einmal-Regel
- `components/ModuleCatalogModal.tsx`
- `opcua-server/config/piServers.ts`: `TagReferenceBinding`
- `model/tagReferenceCall.ts` (neu)
- `model/tagReferenceRun.ts` (neu)
- `components/TagReferencePanel.tsx` (neu, im Layer-2-Fenster)

Signaturen werden nach Abschluss nachgetragen.

## Aufteilung

Alle Agenten dieses Repos arbeiten im selben Worktree (Basis aktueller `apriltag`-Stand,
Branch `tag-einmessen`), jeweils nur auf ihren Dateien. Die Frontend-Agenten (S3, S4, H2 im
Doku-Teil) arbeiten im Worktree von `webskillcomposition` auf Branch `tag-einmessen`. Die Koordination prüft jeden Diff, lässt die Tests laufen
und committet (`docs:` / `feat(apriltag):`), **nicht pushen**.

| # | Agent | Aufgabe | Dateien | Wann |
| --- | --- | --- | --- | --- |
| H1 | Haiku | Arbeitsplan (dieser Plan, Schnittstellen 1:1), Zeile in `arbeitsplaene/README.md`, Nachtrag in `modell-flaechen-tag-platzierung.md` mit Link; verworfene Mesh-Variante unter „Verworfen“ | nur `doc/projektdoku/…` | sofort, parallel zu S1 |
| S1 | Sonnet | `src/tagloc/reference_edges.py` (Rechnung 2–7, Ansichtsplanung, RPY), synthetischer Renderer `render_reference_scene` in `tools/make_synthetic_scene.py` (Tag auf heller Fläche mit Kanten, optional Nut-Doppelkante, Rauschen, Unschärfe), `tests/test_reference_edges.py` | genannte | sofort |
| S2 | Sonnet | `src/vision_server/tagreference.py` (parse/build wie `measuretag.py`), `measure_tag_reference` in `detection/apriltag.py`, `_install_tag_reference` in `runner.py`, Knoten in `address_space.py`, BUSY-Sperre, `tests/test_tagreference.py` inkl. OPC-UA-Methodentest | genannte | nach S1 |
| S3 | Sonnet | Katalogseite: `ModuleTag.measurement` und `placement.lines` in `config/moduleCatalog.ts`, `model/catalogStore.ts`, `model/tagPlacement.ts`, `model/tagMeasurementState.ts` (neu), `components/ModuleCatalogModal.tsx`; Einmal-Regel; Ableitung alter Platzierungen | webskillcomposition: genannte | parallel zu S1/S2, gegen die Schnittstelle dieses Plans |
| S4 | Sonnet | Aufrufe und Ablauf: `opcua-server/config/piServers.ts` (`TagReferenceBinding`), `model/tagReferenceCall.ts` (neu), `model/tagReferenceRun.ts` (neu), `components/TagReferencePanel.tsx` (neu, Layer-2-Fenster) | webskillcomposition: genannte | nach S3 |
| H2 | Haiku | Plan auf `fertig`, tatsächliche Schnittstellen aus den Berichten von S1/S2 nachtragen, neuer Abschnitt in `vision-server-interface.md`, eigener Abschnitt „Tag-Position mit der Kamera einmessen“ in `apriltag-referenz.md` (dabei auch das `placement`-Format, offener Punkt aus dem Flächen-Plan), README-Zeile; zusätzlich Doku zum Frontend-Teil (Abschnitt „Frontend“ dieses Plans, Stand nach S3/S4) | nur Doku | nach S2 bzw. nach S3/S4 |

## Verworfen: Tiefen-ICP gegen das ganze Mesh

Der erste Ansatz war, die Tiefendaten der Kamera gegen das gesamte 3D-Modell eines Moduls
abzugleichen (ICP). Er ist **verworfen**, Entscheidung des Nutzers vom 20.10.2026:

- Die Module ändern sich ständig (Aufbauten kommen und gehen). Ein Abgleich gegen das
  ganze Modell misst dann gegen Geometrie, die gar nicht mehr da ist.
- Die Modelle werden nicht gepflegt. Sie können veraltet sein, ohne dass es auffällt.
- Die Bezugskanten sind dagegen langlebige physische Kanten. Nur sie werden gemessen.

## Offene Fragen / Risiken

- **Höhe der Kante:** Die Höhe `h` kommt aus dem Startwert. Ein Fehler in `h` verschiebt die
  Messung seitlich um `Δh · tan(Sehwinkel)`. Deshalb frontal messen und die Kante möglichst
  nahe an der Bildmitte halten. Später optional die Tiefe der RealSense nutzen.
- **Kontrastarme Kanten** (Alu auf Alu): Ausleuchtung und Belichtung über die vorhandenen
  Kamera-Controls.
- **Kanten, die nicht in ein Bild mit dem Tag passen:** v1 nicht unterstützt (nur
  Teilkorrektur), später Verkettung über die Vorwärtskinematik.
- **Frontend-Teil** (Ansicht anfahren, Ergebnis in den Katalog schreiben): Umsetzung im Branch
  `tag-einmessen` von `webskillcomposition`, Beschreibung im Abschnitt „Frontend“.
- **Echte 3D-Kanten** (im Branch umgesetzt, siehe Abschnitt „Frontend“): Das Frontend schickt die
  `EdgeLine`s als echte 3D-Kanten in `placement.lines`, nicht die auf die Fläche projizierten
  Kanten aus `placement.edges`.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt. Weicht die Realität vom Plan ab, wird der Plan
korrigiert.

Abweichungen, Stand Umsetzung:

- `LineMeasurement.extent_m: float = 0.0`: Länge des gemessenen Kantenstücks in der Tag-Ebene,
  gewichtet die Drehung in der Korrektur. Nicht im Ergebnis-JSON.
- `view.tiltDeg` Standard **10** statt 0 (frontal kippt die Tag-Pose in die Spiegellösung). Der
  Python-Default von `plan_reference_view` bleibt 0.
- Grenzen der Anfrage: `searchBandM` 0,003–0,05 m, `frames` 1–10, `lineExtentM` (0, 1] m,
  `|tiltDeg|` < 80, `lines` 1–4 mit nicht leerer, eindeutiger `id` und ≥ 2 cm. Quaternion wird
  normiert, Null ist ungültig. Unbekannte Felder werden ignoriert. `tagSizeM`-Vorrang: Anfrage >
  Tag-Map > `config.tag_size_m`.
- Ergebnis: zusätzlich `warnings: []` (Platzhalter-Intrinsik, Tag mehrdeutig, `dofs` none/partial,
  mehrdeutige Kante, Korrektur nahe am Suchband). Fehlerfall: `correction: null`, `lines: []`,
  `tag: null`. `error` ist bei Mess-Fehlern der nackte Code, bei Ablehnung `"<CODE>: Text"`.
- `framesUsed` zählt Bilder mit erkanntem Tag. `lines[]` ist je Kante über die Bilder
  zusammengefasst; `ambiguous` wird true, sobald in irgendeinem Bild mehrdeutig.
- View: zusätzlich `error` (null bei Erfolg). Ablehnung: `camInTag: null`, `fits: false`,
  `uncovered: []`.
- Mehrdeutige Tag-Pose: Ohne eindeutiges Bild bleibt die Orientierung exakt der Startwert, nur der
  Tag-Ursprung wandert (`keep_guess_rotation`); `deltaRotDeg` und `spreadDeg` sind dann 0.
- Error-Codes: `PlanTagReferenceView` hat kein `BUSY` (rechnet nur), braucht aber `Operational`
  (sonst `INVALID_STATE`). `DETECTION_FAILED` auch bei `ValueError` der Messung (z. B. Bildgröße
  passt nicht zur Kalibrierung). `MeasureTags` ist jetzt zusätzlich `BUSY`, solange eine
  Einmessung läuft (**Verhaltensänderung an bestehender Methode**).
- `LastTagReference` wird nach jeder Messung geschrieben, auch bei `DETECTION_FAILED`, nicht bei
  Ablehnungen.
- **Falsch im Plan:** „Danach Tag-Map wie bisher per `SetTagMap`“ und „`tagToModule` in der
  Tag-Map“. Modul-Tags stehen bewusst nicht in der Tag-Map des Pi (dort nur Welt-Tags). Der Katalog
  wirkt nur im Frontend.
- `catalog_rpy_deg` übernommen wie `tagGeometry.ts`: R = Rz · Ry · Rx, Ausgabe `[roll, pitch, yaw]`
  in Grad; bei pitch ±90° alles in roll, yaw = 0.
- Doku: H2 um den Frontend-Teil erweitert statt eine zweite H2-Zeile anzulegen; S3 und S4 neu.

## Nach Abschluss

- [ ] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [ ] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` auf `fertig` gesetzt.
- [ ] `vision-server-interface.md`: neuer Abschnitt für `MeasureTagReference`,
      `PlanTagReferenceView` und `LastTagReference`.
- [ ] `apriltag-referenz.md`: eigener Abschnitt „Tag-Position mit der Kamera einmessen“,
      dabei das Datenformat `placement`.
- [x] Nachtrag 20.10.2026 in `modell-flaechen-tag-platzierung.md` mit Link auf diesen Plan.
- [ ] Am Pi mit Zollstock-Referenz verifiziert, Ergebnis in `doc/projektdoku/` festgehalten.
