# Tag-Position per Hand-Kamera einmessen — Bezugskanten statt Mesh-Matching

**Status:** in Arbeit — 20.10.2026
**Verantwortlich:** Meric, `Agent: Tag-Position mit der Kamera über Bezugskanten einmessen`
**Thema:** apriltag
**Branch:** tag-einmessen

## Ziel

Die Tag-Position eines Moduls (`T_module_tag`) wird nicht mehr mit dem Zollstock
gemessen, sondern mit der Hand-Kamera über die Bezugskanten. Hinterher liefert der
Hand-Pi pro Bezugskante die gemessene Lage im Tag-KS und eine korrigierte Tag-Pose
im Modul-KS. Vorher wurden die Abstände von Hand gemessen, und der Messfehler ging
1:1 in jede Modulpose ein.

### Kontext

`T_module_tag` (`ModuleTag.position`/`rotationDeg` im Modulkatalog → `tagToModule` in der
Tag-Map) wird heute von Hand bestimmt: im Katalog Fläche + Ecke bzw. zwei Kanten wählen
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

`ValueError` bei ungültigen Eingaben (Kante kürzer als 2 cm, Größe ≤ 0).

Wiederverwendet werden `tagloc.geometry` (`compose`, `invert`, `pose_to_dict`,
`from_position_quaternion`), `tagloc.lens` (`project_points`, `undistort_points`) und
`tagloc.pose.tag_object_points`.

### OPC UA (nur Hand-Pi, `hand_eye_path` gesetzt; unter `VisionMachine`, verlinkt unter `VisionProgram`)

| Element | NodeId | Typ |
| --- | --- | --- |
| `MeasureTagReference` | `ns=<vision>;s=VisionMachine.MeasureTagReference` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` |
| `PlanTagReferenceView` | `ns=<vision>;s=VisionMachine.PlanTagReferenceView` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` — rechnet nur, kein Bild |
| `LastTagReference` | `ns=<vision>;s=VisionMachine.LastTagReference` | String (JSON), nur zur Anzeige |

Error wie bei `MeasureTags`:

| Error | Wann |
| --- | --- |
| `OK` | gemessen bzw. geplant. Auch wenn Kanten fehlen (dann `dofs` reduziert). |
| `DETECTION_FAILED` | `NO_FRAME` bzw. `TAG_NOT_SEEN` |
| `INVALID_ARGUMENT` | JSON bzw. Werte ungültig |
| `INVALID_STATE` | Vision-System nicht `Operational` |
| `BUSY` | Job, Kamera-Kalibrierung, Hand-Auge-Sitzung oder MeasureTags läuft |

`ResultJson` ist immer gültiges JSON.

### Daten / Payload

**Anfrage** `wsc.vision.tagreference.request/1` (für beide Methoden):

```json
{ "schema": "wsc.vision.tagreference.request/1",
  "tagId": 7, "tagSizeM": 0.08,
  "moduleTagGuess": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "lines": [ { "id": "A", "start": [x,y,z], "end": [x,y,z] },
             { "id": "B", "start": [x,y,z], "end": [x,y,z] } ],
  "searchBandM": 0.015, "frames": 3,
  "view": { "lineExtentM": 0.10, "tiltDeg": 0 } }
```

- `moduleTagGuess` = `T_module_tag` aus dem Katalog (`position`/`rotationDeg` umgerechnet).
- `lines` = echte 3D-Kanten im Modul-KS (die `EdgeLine`s der Platzierung, **nicht** auf die
  Fläche projiziert); 1–4 Stück.

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
  "error": null }
```

**Planung** `wsc.vision.tagreference.view/1` (von `PlanTagReferenceView`, Antwort):

```json
{ "schema": "wsc.vision.tagreference.view/1",
  "camInTag": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
  "standoffM": 0.38, "fits": true, "uncovered": [] }
```

`camInTag` ist die Kamerapose im Tag-KS (Kamera-KS OpenCV: +Z aus der Linse). `uncovered`
nennt die Kanten, die nicht ins Bild passen.

### Katalog (Frontend, Vorschlag für die `webskillcomposition`-Sitzung)

Optionales `ModuleTag.measurement = { method: "camera-edges", measuredAt, dofs, deltaMm,
deltaRotDeg, spreadMm, lines: ["A","B"] }`. `position`/`rotationDeg` werden mit
`correction.position`/`correction.rotationDeg` überschrieben, `placement` bleibt als
Protokoll des Startwerts (wird nicht entfernt). Danach Tag-Map wie bisher per `SetTagMap`.

## Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Tag nicht im Bild | `DETECTION_FAILED`, `TAG_NOT_SEEN` |
| Kein frisches Bild | `DETECTION_FAILED`, `NO_FRAME` |
| Kante außerhalb des Bilds | `found: false`, `reason: "NOT_VISIBLE"` |
| Keine Kante im Suchband | `found: false`, `reason: "NO_EDGE"` |
| Kante mehrdeutig (Item-Nut) | `ambiguous: true`; die Kante wird für `dofs: "full"` nicht verwendet |
| Kante (fast) senkrecht zur Tag-Ebene | `reason: "PERPENDICULAR_TO_TAG"` |
| Korrektur größer als das Suchband | Warnung; Startwert vermutlich falsch |
| Tag `ambiguous` | Drehung aus dem Startwert, nur Position korrigiert |

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
7. Mehrere Bilder (`frames`): Kanten je Bild messen, Korrektur über alle gemittelt, Streuung
   melden. Mehrere Ansichten sind dadurch trivial — die Steuerung ruft mehrfach auf und
   mittelt, keine Vorwärtskinematik nötig.

### Kamera passend platzieren

`plan_reference_view` berechnet die Kamerapose **im Tag-KS** (`T_tag_cam`): frontal
(optional Kippwinkel gegen Spiegelung), zentriert über Tag + den tagnahen Stücken beider
Kanten (Standard je 10 cm), Abstand so, dass alles mit 10 % Rand ins Bild passt
(0,25–0,60 m). Die Steuerung rechnet

`Flanschziel = T_base_tag (aus MeasureTags/Layer 2) · T_tag_cam · inv(T_flansch_cam)`,

fährt hin und ruft die Messung auf. Passt es nicht in ein Bild → `fits: false` plus welche
Kante fehlt (v1: dann nur diese Kante weglassen bzw. Teilkorrektur).

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

## Aufteilung

Alle arbeiten im selben Worktree (Basis aktueller `apriltag`-Stand, Branch `tag-einmessen`),
jeweils nur auf ihren Dateien. Die Koordination prüft jeden Diff, lässt die Tests laufen
und committet (`docs:` / `feat(apriltag):`), **nicht pushen**.

| # | Agent | Aufgabe | Dateien | Wann |
| --- | --- | --- | --- | --- |
| H1 | Haiku | Arbeitsplan (dieser Plan, Schnittstellen 1:1), Zeile in `arbeitsplaene/README.md`, Nachtrag in `modell-flaechen-tag-platzierung.md` mit Link; verworfene Mesh-Variante unter „Verworfen“ | nur `doc/projektdoku/…` | sofort, parallel zu S1 |
| S1 | Sonnet | `src/tagloc/reference_edges.py` (Rechnung 2–7, Ansichtsplanung, RPY), synthetischer Renderer `render_reference_scene` in `tools/make_synthetic_scene.py` (Tag auf heller Fläche mit Kanten, optional Nut-Doppelkante, Rauschen, Unschärfe), `tests/test_reference_edges.py` | genannte | sofort |
| S2 | Sonnet | `src/vision_server/tagreference.py` (parse/build wie `measuretag.py`), `measure_tag_reference` in `detection/apriltag.py`, `_install_tag_reference` in `runner.py`, Knoten in `address_space.py`, BUSY-Sperre, `tests/test_tagreference.py` inkl. OPC-UA-Methodentest | genannte | nach S1 |
| H2 | Haiku | Plan auf `fertig`, tatsächliche Schnittstellen aus den Berichten von S1/S2 nachtragen, neuer Abschnitt in `vision-server-interface.md`, eigener Abschnitt „Tag-Position mit der Kamera einmessen“ in `apriltag-referenz.md` (dabei auch das `placement`-Format, offener Punkt aus dem Flächen-Plan), README-Zeile | nur Doku | nach S2 |

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
- **Frontend-Teil** (Ansicht anfahren, Ergebnis in den Katalog schreiben) läuft in einer
  eigenen `webskillcomposition`-Sitzung gegen diese Schnittstelle. Dieser Plan beschreibt
  nur die Schnittstelle, nicht die Umsetzung im Frontend.
- **Echte 3D-Kanten:** Das Frontend muss die `EdgeLine`s als echte 3D-Kanten mitschicken,
  nicht die auf die Fläche projizierten Kanten aus `placement.edges`.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt. Weicht die Realität vom Plan ab, wird der Plan
korrigiert.

## Nach Abschluss

- [ ] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [ ] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` auf `fertig` gesetzt.
- [ ] `vision-server-interface.md`: neuer Abschnitt für `MeasureTagReference`,
      `PlanTagReferenceView` und `LastTagReference`.
- [ ] `apriltag-referenz.md`: eigener Abschnitt „Tag-Position mit der Kamera einmessen“,
      dabei das Datenformat `placement`.
- [x] Nachtrag 20.10.2026 in `modell-flaechen-tag-platzierung.md` mit Link auf diesen Plan.
- [ ] Am Pi mit Zollstock-Referenz verifiziert, Ergebnis in `doc/projektdoku/` festgehalten.
