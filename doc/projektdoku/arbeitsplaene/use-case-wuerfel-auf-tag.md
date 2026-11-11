# Use Case 1: Würfel mittig auf den Tisch-Tag legen, Foto misst gegen den Tag-Rand

**Status:** fertig — 11.11.2026
**Verantwortlich:** Meric Lotz, Agent: Use Case Würfel auf Tag
**Thema:** apriltag
**Branch:** ADP `handauge-schachbrett-offline` (Commit `6d3bdce`); webskillcomposition `handauge-schachbrett-offline` (Commit `4eb681a`, nur lokal, Remote archiviert)

## Ziel

Bisher legt der Roboter den 20-mm-Würfel auf die Zielscheibe (ArUco-Blatt). Jetzt legt er ihn mittig auf den Tag des Tisches. Danach misst ein Foto, wie mittig der Würfel auf dem Tag liegt, orientiert an den Rändern des Tags.

Der Tag wird dafür nicht dekodiert und nicht gemessen. Seine Mitte ist vom Würfel verdeckt: 20-mm-Würfel auf 10-mm-Zellen des 80-mm-tag36h11.

## Bereits gelesen

- `doc/projektdoku/arbeitsplaene/_vorlage.md`, `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/arbeitsplaene/layer2-annaeherung.md` (Stilvorbild)
- Der Stand der Fachdoku stammt aus dem Auftrag.

## Betroffene Dateien

- `src/use_case_eval/measure.py` (neu: `_find_tag_quad`, `_refine_quad`, `_tag_pose`)
- `src/vision_server/runner/save_use_case_image.py` (`_reference_from_metadata`)
- `src/use_case_eval/live.py` (`measure_for_save`)
- `src/use_case_eval/synthetic.py` (`render_tag_scene`)
- `tests/test_use_case_tag.py` (neu, 12 Tests)
- webskillcomposition `frontend/src/features/cell-modules/model/pickPlace.ts`
- webskillcomposition `frontend/src/features/cell-modules/model/pickPlaceTag.test.ts`
- webskillcomposition `frontend/src/features/cell-modules/model/usePickPlaceRun.ts`
- webskillcomposition `frontend/src/features/cell-modules/model/useCaseRecord.ts`
- webskillcomposition `opcua-server/model/useCaseMeasurement.ts`, `useCaseMeasurement.test.ts`
- `doc/projektdoku/arbeitsplaene/README.md`: Indexzeile

## Schnittstellen

Die OPC-UA-Methode bleibt unverändert. Alle Änderungen kommen nur als optionale Felder hinzu.

### OPC UA

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SaveUseCaseImage` | unverändert | Methode am Hand-Pi (Pi 2) | Aufruf | `(Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error)` |

### Daten / Payload

**`MetadataJson`** (Client → Pi), neues optionales Feld auf oberster Ebene:

```json
"reference": { "kind": "tag", "tagId": 3, "tagSizeM": 0.08, "tagXInImage": [ux, uy] }
```

- `kind`: `"tag"` schaltet auf die Messung gegen den Tag. Fehlt das Feld oder ist `kind` ≠ `"tag"`, wird wie bisher gegen die Zielscheibe gemessen.
- `tagId`: nur `int`.
- `tagSizeM`: Kantenlänge des Tags in Metern. Gültig 0,01 bis 0,5. Sonst Standard 0,08.
- `tagXInImage`: Richtung der Tag-+x-Achse im Bild (x rechts, y unten, Einheitsvektor). Zwei endliche Zahlen, nicht beide 0. Sonst `null`.
- `tagXInImage` berechnet das Frontend als `tagXInImage(cameraInBase, tagInBase)` = normalise((R_camᵀ·R_tag·[1,0,0]).xy). Die Kamerapose kommt aus den beim Foto gelesenen Gelenken (FK · Hand-Auge).
- Das Feld bleibt in den gespeicherten Metadaten.

Die Prüfung läuft auf dem Pi in `_reference_from_metadata` (`src/vision_server/runner/save_use_case_image.py`).

Weitere Felder im `MetadataJson` aus dem Frontend (`useCaseRecord.ts`): `place.source` (`"tag"` | `"centre"`), `place.tagId`, `plan.placeTag`.

**`ResultJson`** (Pi → Client), zusätzliche Felder:

```json
{
  "reference": "sheet" | "tag",
  "tag_size_mm": 80,
  "tag_id": 3,
  "tag_edges_refined": true,
  "tag_corners_px": [[x, y], [x, y], [x, y], [x, y]],
  "sheet_markers": []
}
```

- `tag_corners_px`: Rohecken im Bild, Reihenfolge wie unter „Tag-System“.
- Im Tag-Modus ist `sheet_markers = []`.
- `dx_mm`, `dy_mm`, `dtheta_deg`, `r_mm`, `zone` wie bisher. Sie sind aber relativ zur Tag-Mitte in Tag-Achsen.
- Das Label im Opcua-Server heißt „Abweichung zur Tag-Mitte“.

### Fehlerfälle

Bei `ok: false` steht der Grund in `grund`:

- „Tag-Rand nicht gefunden (kein Viereck um den Wuerfelmarker)“
- „Tag-Pose nicht berechenbar (solvePnP)“
- „Tag-Pose unplausibel (Kamera nicht ueber dem Tag)“
- „Kein Wuerfelmarker (ID 10) erkannt“

Frontend ohne Tag am Tisch: kein Plan, Meldung „… hat im Modulkatalog keinen Tag …“.

### Python

```python
# src/use_case_eval/measure.py
def measure_trial(image, calibration, cube_marker_offset_mm=(0, 0), *,
                  refine_edges=True, reference=None) -> TrialMeasurement:
    """Wirft nie. Fehler stehen im Ergebnis."""

# src/use_case_eval/live.py
def measure_for_save(image, calibration, sticker_offset_mm=(0, 0), reference=None): ...

# src/use_case_eval/synthetic.py (Testhilfe)
def render_tag_scene(calibration, camera_pose_in_sheet, cube_dx_mm, cube_dy_mm,
                     cube_dtheta_deg, sticker_offset_mm, tag_size_mm=80,
                     pattern_seed=1, with_cube=True, supersample=1): ...
```

### Frontend

```ts
// cell-modules/model/pickPlace.ts
tagTopCentreInBase(table, tag, baseInWorld): PlaceTagTarget   // {tagId, sizeM, poseInBase}
tagXInImage(cameraInBase, tagInBase): [number, number]
planPickPlace(..., flangeToCamera, placeTag?: ModuleTag | null)

// cell-modules/model/usePickPlaceRun.ts
type PlaceSource = "tag" | "centre"
```

### Netz / Betrieb

- Hand-Pi: ADP-Branch `handauge-schachbrett-offline` auf mindestens `6d3bdce`.
- Umsetzen: `git pull`, dann `sudo systemctl restart opcua-server.service`.
- Ein Pi ohne diesen Stand ignoriert `reference` und meldet auf dem Tag „Nur 0 Blattmarker erkannt“.

### Tag-System

- Ursprung: Tag-Mitte.
- z: zur Kamera.
- x: die Kante, deren Bildrichtung am besten zu `tagXInImage` passt. Ohne Angabe: Bild-+x.
- y: rechtshändig. Von oben gesehen zeigt Bild-oben bei x = Bild-rechts.
- Eckenreihenfolge: (−s/2,+s/2), (+s/2,+s/2), (+s/2,−s/2), (−s/2,−s/2).

## Vorgehen

1. Layer 2 durchfahren, Vorschlag übernehmen. Der Tisch ist danach „präzise“.
2. Pick-&-Place-Panel: „Ziel auf dem Tisch“ = „Mitte Tag (gemessen in Layer 2)“ (Standard) oder „Tischmitte“.
   - Tag = erster Tag des Tischs im Katalog.
   - Tisch leer: Tag 20. Tisch leer mit Rad: Tag 3. Je 8 cm, waagerecht auf der Platte.
3. „Würfel ablegen“:
   - Würfelmitte auf die Tag-Mitte: `tagTopCentreInBase` = Tischpose · Katalog-Tag über `tagWorldPose`.
   - Würfelkanten parallel zu den Tag-Kanten: `squareToTable` mit der Tag-Orientierung, kleinste Drehung.
   - Absetzhöhe wie bisher.
4. Foto: Handkamera 20 cm senkrecht über der Tag-Mitte. `SaveUseCaseImage` mit Tag-Bezug im `MetadataJson`.
5. Der Pi misst den Würfelaufkleber (ArUco 4x4_50, ID 10, 18 mm) gegen den Tag-Rand.
   - Anzeige: „Abweichung zur Tag-Mitte: Δx, Δy, Δθ (Zone)“.
   - Eintrag im Versuchsprotokoll.

## Messverfahren

Pi, `src/use_case_eval/measure.py`:

1. Otsu-Schwelle. Nur äußere Konturen (`RETR_EXTERNAL`), Innenmuster und Würfel stören nicht.
2. `_find_tag_quad`: konvexes Viereck, das die Mitte des Würfelmarkers enthält.
   - Fläche ≥ 6× Marker und ≤ 60 % des Bilds.
   - Seitenverhältnis ≤ 1,5.
   - Das kleinste Viereck gewinnt.
3. `_refine_quad`: Ecken über die Außenkanten verfeinert.
4. `_tag_pose`: Pose Tag → Kamera per `solvePnP` (IPPE + LM) mit Kantenlänge `tagSizeM`.
5. Danach dieselbe Rechnung wie beim Blatt: Aufkleber-Ecken als Strahlen mit Ebene z = 20 mm. Ergebnis: dx, dy, Δθ (mod 90°), r, Zone.

Voraussetzung: heller Tisch um den Tag.

## Genauigkeit und Tests

- `tests/test_use_case_tag.py` (12 Tests), synthetisch in Full HD 1920×1080, fx 1390, Kamera 220 mm über der Tag-Ebene.
  - Abweichung ≤ 0,05 mm und ≤ 0,15° bei Kameradrehung 0, 30, 90, 180 und −90°.
- Bei 640×480 scheitert die Kantenverfeinerung des Aufklebers. Ein 1 mm breiter Rand vor dem schwarzen Tag-Muster ist dann etwa 2 px. Fehler bis etwa 1° und 0,25 mm.
- Die Handkamera läuft seit 10.11.2026 überall in 1920×1080 (siehe Standard für alle ADP-Branches).
- Frontend: `pickPlaceTag.test.ts`, `useCaseMeasurement.test.ts`. Gesamt 723 Frontend-Tests grün.

## Verwandt

- Experiment „Tag messen (1 Bild) und mit der Werkzeugspitze anfahren“ auf webskillcomposition `roboter-wiederholgenauigkeit` (Commits `5022124`, `21e67af`, `533e68f`). Dort: Tag aus einer von Hand eingestellten Stellung messen, Spitze über die Tag-Mitte, Versatz von Hand messen.
- Hier dagegen: Tag-Pose aus Layer 2, Versatz per Foto.
- Nicht in `handauge-schachbrett-offline` enthalten.

## Offene Fragen

- Am Aufbau prüfen:
  - Ist die Tischfarbe um den Tag hell genug für die Otsu-Schwelle?
  - Ist der Aufkleber ID 10 auf dem Würfel?
  - Wie wiederholgenau ist die Ablage?
- Nur der erste Katalog-Tag des Tischs wird benutzt.
- Use Case 2 (`planReturn`) rechnet die Würfelhöhe weiter mit der Tischmitte. Der Unterschied liegt höchstens im mm-Bereich.

## Abweichungen vom Plan

Keine festgehalten.
