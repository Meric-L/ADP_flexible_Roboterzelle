# Tag-Einmessen: Live-Ansicht der Kantensuche und Testphase „alle Tags“

**Status:** in Arbeit — 21.10.2026
**Verantwortlich:** Meric, `Agent: Live-Ansicht Tag-Einmessen`
**Thema:** apriltag
**Branch:** `tag-einmessen-liveview` (dieses Repo und `webskillcomposition`), abgezweigt von `tag-einmessen`

## Ziel

Vorgabe des Nutzers (21.10.2026, wörtlich):

> „Ich brauche eine Live-View, um zu sehen, wie er die Linien zieht und was er genau macht —
> der erste Versuch hat so semi gut funktioniert. Für das Erste sollen immer alle möglichen Tags
> neu eingescannt werden, damit wir es öfter probieren können. Für später soll vermerkt werden,
> dass es nur einmal pro neuem Modul gemacht werden soll.“

Hinterher sieht man im Fenster „Tags einmessen“ live im Kamerabild, welche Bezugskanten der
Hand-Pi sucht, welche Linien er zieht und warum er eine Kante verwirft. Nach jeder Messung
liegen Debug-Bilder und Rohbilder auf dem Pi, und ein Lauf misst in der Testphase alle messbaren
Tags neu, damit der Versuch oft wiederholt werden kann. Vorher war die Kantensuche eine
Blackbox: nur das Endergebnis war sichtbar, und ein Lauf prüfte nur die ausstehenden Tags.

## Bereits gelesen

Ausgewertet für diesen Plan (Stand: Kopf und Gliederung gelesen, Abschnitte nach Bedarf):

- `doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md` (Vorgängerplan, Stil, Kontext
  der Bezugskanten und der Roboter-Variante)
- `doc/projektdoku/vision-server-interface.md` §10 (Kamera-Livestream, `LatestCameraFrame`) und
  §19 (`MeasureTagReference` / `PlanTagReferenceView`, Gliederung)
- `doc/projektdoku/arbeitsplaene/stream-overlay-schlank.md` (Overlay-Modus `apriltag`, Stil des
  Stream-Overlays)
- `doc/projektdoku/arbeitsplaene/livestream-tags-volle-aufloesung.md` (Tags im Livestream aus
  voller Auflösung, Overlay-Modus `apriltag`)
- `doc/projektdoku/arbeitsplaene/rohbild-speichern.md` (Rohbilder unter `data/`, gitignored)
- Spezifikation, verbindlich: `/home/meric/.claude/jobs/41073b2a/tmp/liveview-spec.md`

## Betroffene Dateien

Pi (ADP_flexible_Roboterzelle):

- `src/tagloc/reference_edges.py` (Debug-Daten, A1)
- `src/tagloc/reference_overlay.py` (neu, Zeichnen, A2)
- `src/tagloc/modes.py` (Overlay-Modus `tagreference`, A3)
- `src/vision_server/stream_overlay.py` (Overlay-Auswertung mit höchstens 3 Hz, A3)
- `src/vision_server/mjpeg_server.py` (`GET /tagreference.jpg`, A4)
- `src/vision_server/runner.py` (Protokoll-Ordner, `debugDir`, A4)
- `src/vision_server/address_space.py` (Methode `SetTagReferencePreview`, Knoten `TagReferencePreviewStatus`, A3)
- `src/vision_server/detection/apriltag.py` (Tag-Erkennung für die Vorschau, A3)
- `src/vision_server/tagreference.py` (Vorschau-Zustand, Ablauf nach 120 s, Protokoll, A3/A4)
- Config (Schlüssel `tag_reference_debug_dir`, A4)
- Tests unter `tests/` (A5)

Frontend (webskillcomposition; die Verzeichnisse sind hier nicht festgehalten):

- `piServers.ts` (Bindings, B1)
- `tagReferenceCall.ts` (Aufrufe und Parser, B2)
- `tagReferenceRun.ts` (Phasen, Vorschau, Bestätigung, B3)
- `robotTagRun.ts` (Phasen mit `view`, B3)
- `tagMeasurementState.ts` (Konstante `TAG_MEASURE_ALL_TESTPHASE`, `measurableTags`, Startwert, B5)
- `TagReferenceWindow.tsx` (Fenster, B4)
- `TagReferencePanel.tsx` (Liste, Steuerung, Testphase-Hinweis, B4/B5)
- Tests (vitest, B6)

Diese Liste reserviert die Dateien. Wer sie parallel anfassen will, spricht sich vorher ab.

## Schnittstellen

Die Schnittstellen sind 1:1 aus der Spezifikation übernommen. Abweichungen während der Umsetzung
werden hier nachgetragen.

### OPC UA (Hand-Pi)

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| Methode `SetTagReferencePreview` | `ns=<vision>;s=VisionMachine.SetTagReferencePreview` | Methode, `(RequestJson: String) -> (Error: Int32)` | Aufruf | Vorschau für einen Tag an oder aus. Nur Hand-Pi (`hand_eye_path`). Unter VisionProgram verlinkt. |
| Status `TagReferencePreviewStatus` | `ns=<vision>;s=VisionMachine.TagReferencePreviewStatus` | String (JSON) | Lesen / Abo | Stand der Vorschau, nach jeder Auswertung (höchstens 3 Hz). |
| `MeasureTagReference` | bestehend, siehe `vision-server-interface.md` §19 | — | Aufruf | Setzt die Vorschau automatisch auf die eigene Anfrage. Signatur ändert sich nicht. |

**`SetTagReferencePreview`**

- Eingang `RequestJson`: `wsc.vision.tagreference.request/1`, derselbe Parser wie bei
  `MeasureTagReference`. Felder `frames` und `view` werden ignoriert. Ein Tag wird angegeben,
  dann ist die Vorschau an für diesen Tag. `""` schaltet die Vorschau aus.
- Rückgabe `Error: Int32`:
  - `OK`: Vorschau an oder aus geschaltet.
  - `INVALID_ARGUMENT`: ungültiges JSON oder fehlender Tag.
  - `INVALID_STATE`: Server nicht Operational.
  - Nie `BUSY`.
- Die Vorschau läuft ab, wenn sie 120 s nicht erneuert wurde.

**Knoten `TagReferencePreviewStatus`**

Wird nach jeder Vorschau-Auswertung geschrieben (höchstens 3 Hz). Inhalt JSON:

```json
{
  "schema": "wsc.vision.tagreference.preview/1",
  "active": true,
  "tagId": 7,
  "tagSeen": true,
  "updatedAt": "2026-10-21T10:00:00+02:00",
  "lines": [
    { "id": "A", "found": true, "offsetMm": 1.8, "angleDeg": 0.4,
      "ambiguous": false, "reason": null, "supportPx": 212, "contrast": 0.37 }
  ]
}
```

`tagId` ist `null`, wenn keine Vorschau aktiv ist. `reason` ist ein Grund aus `NO_EDGE`,
`NOT_VISIBLE` oder `null`.

### Python

```python
# Modul: src/tagloc/reference_edges.py
def measure_reference_lines(..., debug: list[dict] | None = None) -> LineMeasurement:
    """Ist debug eine Liste, wird je Kante ein Dict angehängt (siehe unten).
    Ergebnis und LineMeasurement sind mit und ohne debug bitgleich."""
```

Die bestehenden Parameter bleiben unverändert (`...`). Das Debug-Dict je Kante, Pixel des
**Rohbilds**, verzeichnet, volle Auflösung:

```python
{
    "id": str,
    "reason": str | None,
    "guessPx": [[u, v], ...],        # sichtbares Stück der projizierten Startwert-Kante
    "profilesPx": [[u, v], ...],     # Profilmittelpunkte
    "normalsPx": [[nx, ny], ...],
    "bandPx": float,                 # Halbbreite des Suchbands
    "edgePointsPx": [[u, v], ...],   # alle Kandidatenpunkte (Gradientenmaxima)
    "candidates": [
        {"inliersPx": [[u, v], ...], "score": float, "chosen": bool}
    ],
    "measuredPx": [[u, v], [u, v]] | None,  # gewählte Linie, Endpunkte im Rohbild
    "ambiguous": bool,
    "offsetMm": float | None,
    "angleDeg": float | None,
}
```

Zusätzlich einmal je Aufruf der Eintrag `{"id": "_tag", "maskOutlinePx": [[u, v], ...]}`
(Umriss Tag und Rand).

```python
# Modul: src/tagloc/reference_overlay.py (neu)
def draw_reference_debug(image: np.ndarray, debug: list[dict], *,
                         tag_corners_px=None, title: str = "") -> np.ndarray:
    """BGR. Zeichnet auf eine Kopie des Bildes und gibt sie zurück."""
```

Farben: Startwert-Kante gelb gestrichelt, Bandränder dünn gelb, Kandidatenpunkte cyan (1–2 px),
verworfene RANSAC-Linien (Inlier) orange, gewählte Linie grün (rot bei `ambiguous`), Tag-Masken-
Umriss grau, Tag-Rahmen wie `draw_tag_boxes`. Je Kante ein Label an der Kante, z. B.
`A  +1,8 mm  0,4°` bzw. `A  NO_EDGE` / `A  mehrdeutig`. Titelleiste oben (`draw_status_bar`),
z. B. `Tag 7 · Vorschau` oder `Tag 7 · Messung 3 Bilder`. Strichstärken für 640×480.

```python
# Modul: src/vision_server/stream_overlay.py
# Overlay-Modus "tagreference" in tagloc/modes.py OVERLAY_MODES (Stream-Knoten CameraStreamMode).
# Quelle: voller Frame, wie "apriltag".
```

### Protokoll-Ordner und HTTP

- Jede `MeasureTagReference` legt `data/tagreference/<YYYYmmdd-HHMMSS>_tag<id>/` an. Config
  `tag_reference_debug_dir`, Standard `<repo>/data/tagreference`. `data/` ist gitignored.
- Inhalt: `request.json`, `result.json`, `frame<k>.png` (Rohbilder, verlustfrei), `overlay.jpg`
  (Debug des letzten Bilds).
- Höchstens 50 Ordner, die ältesten werden gelöscht.
- `ResultJson` bekommt `"debugDir": "<repo-relativer Pfad>" | null`.
- HTTP `GET /tagreference.jpg` liefert `overlay.jpg` der letzten Messung. 404, wenn keine
  Messung vorliegt. CORS `*`, `Cache-Control: no-store`.

### Daten / Payload

Vorschau-Status: `wsc.vision.tagreference.preview/1` (siehe oben).

### Frontend (webskillcomposition)

```ts
// piServers.ts
interface TagReferenceBinding {
  previewNodeId: string;        // ...SetTagReferencePreview
  previewStatusNodeId: string;  // ...TagReferencePreviewStatus
}
// CAMERA_OVERLAY_MODES um "tagreference" erweitert (Label "Tag einmessen")
function buildTagReferenceImageUrl(serverUrl: string, port: number): string;
// -> "http://<host>:<port>/tagreference.jpg"

// tagReferenceCall.ts
function setTagReferencePreview(
  controller, serverUrl: string, nodeId: string,
  requestJson: string,            // "" = aus
  opts: { sleep?: ... }
): Promise<{ status: "done" } | { status: "failed"; reason: string }>;  // Timeout 10 s
function parseTagReferencePreviewStatus(raw: string): TagReferencePreviewStatus;
// TagReferenceResult.debugDir?: string | null

// tagReferenceRun.ts und robotTagRun.ts
type TagPhase = "approach" | "centre" | "plan" | "drive_view" | "measure" | "return";
// Roboter zusätzlich: view: { index: number; count: number } bei drive_view/measure
onPhase?(job, phase: TagPhase): void;
preview?(job, requestJson: string | null): void;   // Modul: Anfrage vor "plan", null nach dem Job; Roboter: nicht aufgerufen
confirm?(job, phase: "measure"): Promise<boolean>; // false = Job überspringen
```

Testphase (`tagMeasurementState.ts`):

```ts
export const TAG_MEASURE_ALL_TESTPHASE = true;
// Kommentar: Testphase (Vorgabe 21.10.2026): jeder Lauf misst alle messbaren Tags neu.
// Später auf false: dann nur pendingTags, einmal pro neuem/bearbeitetem Modul.
export function measurableTags(catalog): ModuleTag[];  // alle Nicht-Welt-Tags, ohne Filter auf measurement
```

### Netz / Betrieb

- Keine neuen Ports. Der Overlay-Modus läuft über den bestehenden MJPEG-Server, der Pfad
  `/tagreference.jpg` liegt am selben Port.
- Keine neuen systemd-Units.
- Protokollordner `data/tagreference/` auf dem Pi, gitignored.

### Fehlerfälle

- Timeout beim Setzen der Vorschau (10 s im Frontend): Lauf zeigt „Vorschau nicht gesetzt“,
  die Messung läuft weiter.
- Vorschau nicht gesetzt oder abgelaufen (120 s ohne Erneuerung): Overlay zeigt normale Tag-Rahmen
  und Titel „keine Einmess-Vorschau“.
- Tag nicht im Bild: Titel „Tag 7 nicht im Bild“, normale Tag-Rahmen.
- Server nicht Operational: `INVALID_STATE`, nie `BUSY`.
- Ungültiges Request-JSON oder fehlender Tag: `INVALID_ARGUMENT`.
- `GET /tagreference.jpg` ohne vorherige Messung: 404.
- Fehler beim Schreiben des Protokolls: Log-Warnung, Messung läuft weiter.
- Kamera fehlt im Overlay-Modus: Titel mit Hinweis, Stream bleibt leer, kein Absturz.
- Abbruch des Laufs: Vorschau wird mit `null` ausgeschaltet.
- Roboter-Lauf: ruft `preview` nicht auf (keine Kanten).

## Vorgehen

Aufteilung nach Rollen (Nutzerwunsch: Sonnet baut, Opus prüft, Haiku dokumentiert):

1. **H5 (Haiku):** dieser Plan (Status, Schnittstellen, Index-Zeile).
2. **S6 (Sonnet):** Pi-Seite nach A1–A5 (`src/tagloc/`, `src/vision_server/`, Config, Tests).
3. **S7 (Sonnet):** Frontend nach B1–B6 (Bindings, Aufrufe, Phasen, Fenster, Testphase, vitest).
4. **R1 (Opus):** Review des Diffs beider Repos gegen diesen Plan. Abweichungen werden hier
   vermerkt oder korrigiert.
5. **H6 (Haiku):** Dokumentation nach Abschluss (siehe „Nach Abschluss“).

Reihenfolge: S6 und S7 können parallel laufen, wenn die Schnittstellen oben feststehen. S7 hält
sich an die Namen aus diesem Plan, auch wenn der Pi-Teil noch nicht fertig ist.

## Testphase und spätere Einmal-Regel

**Testphase (jetzt):** Die Konstante `TAG_MEASURE_ALL_TESTPHASE = true` in
`tagMeasurementState.ts` sorgt dafür, dass jeder Lauf alle messbaren Tags neu misst
(`measurableTags`). Das Panel zeigt dann „Alle Tags einmessen (n) — Testphase“ und den Hinweis
„Testphase: alle Tags werden bei jedem Lauf neu eingemessen“. Zweitmessungen behalten den
ursprünglichen Startwert (`guess: tag.measurement?.guess ?? {position, rotationDeg}`), damit
`placementMatches` nicht bricht.

**Später (Zielzustand, Vorgabe des Nutzers):** Jeder Tag wird nur **einmal pro neuem bzw.
bearbeitetem Modul** eingemessen. Dafür die Konstante auf `false` setzen. Dann gilt wieder
`pendingTags` mit der Einmal-Regel (`invalidateChangedMeasurements`). Diese Regel bleibt im Code
und wird nicht entfernt. Der Label wird dann „Ausstehende Tags einmessen (n)“.

## Offene Fragen

- **Rechenlast:** 3 Hz Tag-Erkennung und Kantensuche mit Debug-Overlay auf dem Hand-Pi. Ob das
  neben dem Livestream reicht, ist nicht gemessen. Notfalls auf 1–2 Hz senken.
- **Overlay-Strichstärken bei 640×480:** Die Werte sind geschätzt. Am echten Stream prüfen.
- **Platzbedarf Protokollordner:** 50 Ordner mit Rohbildern (PNG) können viele MB ergeben. Ob das
  auf der SD-Karte des Pi passt, ist nicht geprüft.
- **Startwert bei Zweitmessung:** Ob der ursprüngliche Startwert bei einer Testphase-Messung
  immer der richtige Bezug ist, wenn ein Modul von Hand bewegt wurde, ist offen.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt.

## Nach Abschluss

- [ ] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [ ] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` auf `fertig` gesetzt.
- [ ] `vision-server-interface.md`: Abschnitt zu `SetTagReferencePreview`,
      `TagReferencePreviewStatus` und `GET /tagreference.jpg` ergänzt.
- [ ] `apriltag-referenz.md`: Abschnitt zur Live-Ansicht und zur Testphase ergänzt.
- [ ] Nachtrag in `tag-einmessen-bezugskanten.md` mit Link auf diesen Plan.
- [ ] Am Pi mit echtem Stream angesehen, Overlay-Strichstärken und Rechenlast bewertet.
