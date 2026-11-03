# Tag-Einmessen: Live-Ansicht der Kantensuche und Testphase „alle Tags“

**Status:** fertig — 21.10.2026 (Tests grün, an echter Hardware nicht getestet)
**Verantwortlich:** Meric, `Agent: Live-Ansicht Tag-Einmessen`
**Thema:** apriltag
**Branch:** `tag-einmessen-liveview` (dieses Repo und `webskillcomposition`), abgezweigt von `tag-einmessen`

## Ziel

Vorgabe des Nutzers (21.10.2026, wörtlich):

> „Ich brauche eine Live-View, um zu sehen, wie er die Linien zieht und was er genau macht —
> der erste Versuch hat so semi gut funktioniert. Für das Erste sollen immer alle möglichen Tags
> neu eingescannt werden, damit wir es öfter probieren können. Für später soll vermerkt werden,
> dass es nur einmal pro neuem Modul gemacht werden soll.“

Nachtrag desselben Tages: „Es soll auch wieder alles toggle-bar sein, so wie auch beim Einscannen und Co. schon ist.“

Hinterher sieht man im Fenster „Tags einmessen“ live im Kamerabild, welche Bezugskanten der
Hand-Pi sucht, welche Linien er zieht und warum er eine Kante verwirft. Nach jeder Messung
liegen Debug-Bilder und Rohbilder auf dem Pi, und ein Lauf misst in der Testphase alle messbaren
Tags neu, damit der Versuch oft wiederholt werden kann. Vorher war die Kantensuche eine
Blackbox: nur das Endergebnis war sichtbar, und ein Lauf prüfte nur die ausstehenden Tags.
Alle Ebenen und Schalter sind im Fenster umschaltbar.

## Bereits gelesen

- `doc/projektdoku/arbeitsplaene/tag-einmessen-bezugskanten.md` (Vorgängerplan, Stil, Kontext
  der Bezugskanten und der Roboter-Variante)
- `doc/projektdoku/vision-server-interface.md` §10 (Kamera-Livestream, Overlay-Modi) und §19
  (`MeasureTagReference` / `PlanTagReferenceView`)
- `doc/projektdoku/arbeitsplaene/stream-overlay-schlank.md` (Overlay-Modus `apriltag`, Stil des
  Stream-Overlays)
- `doc/projektdoku/arbeitsplaene/livestream-tags-volle-aufloesung.md` (Tags im Livestream aus
  voller Auflösung)
- `doc/projektdoku/arbeitsplaene/rohbild-speichern.md` (Rohbilder unter `data/`, gitignored)
- Spezifikation: `/home/meric/.claude/jobs/41073b2a/tmp/liveview-spec.md` (A, B, C)

## Betroffene Dateien

Pi (ADP_flexible_Roboterzelle):

- `src/tagloc/reference_edges.py` (Debug-Daten, `debug`-Parameter, `_debug_step`)
- `src/tagloc/reference_overlay.py` (neu: Zeichnen, `LAYER_KEYS`, `effective_layers`)
- `src/tagloc/modes.py` (Overlay-Modus `tagreference`)
- `src/vision_server/stream_overlay.py` (Vorschau-Zustand, Auswertung höchstens 3 Hz, Ablauf)
- `src/vision_server/tagreference_preview.py` (neu: `ReferencePreview`, `build_status`, Konstanten)
- `src/vision_server/tagreference_log.py` (neu: Mess-Protokoll, Aufräumen)
- `src/vision_server/tagreference.py` (Anfrage mit `overlay` und `saveDebug`, `debugDir`)
- `src/vision_server/detection/apriltag.py` (Debug-Bild der Messung, Rohbild-Sink)
- `src/vision_server/camera_stream.py` (Status-Schreiben, `expire_reference_preview`)
- `src/vision_server/mjpeg_server.py` (`GET /tagreference.jpg`)
- `src/vision_server/runner.py` (`SetTagReferencePreview`, Protokoll-Aufruf, `debugDir`)
- `src/vision_server/address_space.py` (Knoten `TagReferencePreviewStatus`)
- `src/vision_server/config.py` (`tag_reference_debug_dir`)
- Tests unter `tests/` (u. a. `tests/test_tagreference_liveview.py`)

Frontend (webskillcomposition, `frontend/src/features/cell-modules/`):

- `opcua-server/config/piServers.ts` (Bindings, B1)
- `model/tagReferenceCall.ts` (Aufrufe und Parser, B2)
- `model/tagReferencePreview.ts` (neu: Vorschau-Steuerung, Erneuerung, B3/B4)
- `model/tagReferenceRun.ts` (Phasen, Vorschau, Bestätigung, B3)
- `model/robotTagRun.ts` (Phasen mit `view`, B3)
- `model/tagReferenceLiveView.ts`, `model/tagReferenceProgress.ts`, `model/tagReferenceSettings.ts` (neu, Live-Ansicht, Fortschritt, Schalter, C2)
- `model/tagMeasurementState.ts` (`TAG_MEASURE_ALL_TESTPHASE`, `measurableTags`, `selectTagReferenceTags`, Startwert, B5)
- `components/TagReferenceWindow.tsx` (Fenster, B4)
- `components/TagReferencePanel.tsx` (Liste, Steuerung, Testphase-Hinweis, B4/B5)
- `components/TagReferenceLiveParts.tsx` (Live-Werte, Kantentabelle, Debug-Bild)
- Tests (vitest, B6)

Diese Liste reserviert die Dateien. Wer sie parallel anfassen will, spricht sich vorher ab.

## Schnittstellen

Stand nach der Umsetzung, gegen den Code geprüft (21.10.2026). Abweichungen vom ursprünglichen
Plan stehen unter „Abweichungen vom Plan“.

### OPC UA (Hand-Pi)

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| Methode `SetTagReferencePreview` | `ns=<vision>;s=VisionMachine.SetTagReferencePreview` | Methode, `(RequestJson: String) -> (Error: Int32)` | Aufruf | Vorschau für einen Tag an oder aus. Nur Hand-Pi (`hand_eye_path`). Unter VisionProgram verlinkt. |
| Status `TagReferencePreviewStatus` | `ns=<vision>;s=VisionMachine.TagReferencePreviewStatus` | String (JSON) | Lesen / Abo | Stand der Vorschau, nach jeder Auswertung (höchstens 3 Hz). |
| `MeasureTagReference` | bestehend, siehe `vision-server-interface.md` §19 | — | Aufruf | Setzt die Vorschau automatisch auf die eigene Anfrage. Signatur unverändert, Anfrage um `saveDebug` und `overlay` erweitert, Ergebnis um `debugDir`. |

**`SetTagReferencePreview`**

- Eingang `RequestJson`: `wsc.vision.tagreference.request/1`, derselbe Parser wie bei
  `MeasureTagReference`. Felder `frames` und `view` werden ignoriert. Ein Tag wird angegeben,
  dann ist die Vorschau an für diesen Tag. `""` schaltet die Vorschau aus. Optional `overlay`
  (siehe unten, Ebenen).
- Rückgabe `Error: Int32`:
  - `OK`: Vorschau an oder aus geschaltet.
  - `INVALID_ARGUMENT`: ungültiges JSON, fehlender Tag, `overlay` oder ein Wert darin kein Objekt bzw. kein Bool.
  - `INVALID_STATE`: Server nicht `Operational`, oder der Livestream hat kein Overlay (kein Annotator).
  - Nie `BUSY`.
- Die Vorschau läuft ab, wenn sie 120 s (`PREVIEW_TTL_S`) nicht erneuert wurde. Die Prüfung läuft
  auch außerhalb des Modus `tagreference` (`expire_reference_preview`).

**Knoten `TagReferencePreviewStatus`**

Wird nach jeder Vorschau-Auswertung geschrieben (höchstens 3 Hz, `PREVIEW_INTERVAL_S`). Inhalt JSON:

```json
{
  "schema": "wsc.vision.tagreference.preview/1",
  "active": true,
  "tagId": 7,
  "tagSeen": true,
  "updatedAt": "2026-10-21T10:00:00+00:00",
  "overlay": { "guess": true, "band": true, "candidates": true, "rejected": true,
               "chosen": true, "mask": true, "labels": true, "tagBox": true },
  "lines": [
    { "id": "A", "found": true, "offsetMm": 1.8, "angleDeg": 0.4,
      "ambiguous": false, "reason": null, "supportPx": 212, "contrast": 0.37 }
  ]
}
```

`tagId` ist `null` und `lines` leer, wenn keine Vorschau aktiv ist (`active: false`). `reason` ist
`NO_EDGE`, `NOT_VISIBLE` oder `null`. `overlay` sind die wirksamen Ebenen (fehlende Schlüssel = true).
`tagSeen` ist `false`, solange der Tag nicht gesehen wurde. `updatedAt` ist ISO-8601 mit Offset.

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
    "measuredPx": [[u, v], [u, v]] | None,      # gewählte Linie, Endpunkte im Rohbild
    "measuredPolyPx": [[u, v], ...],            # gewählte Linie als Polygon mit Saum (nur bei gefunden)
    "found": bool,
    "supportPx": float,              # Stützlänge der gewählten Linie
    "contrast": float,
    "ambiguous": bool,
    "offsetMm": float | None,
    "angleDeg": float | None,
}
```

Zusätzlich einmal je Aufruf der Eintrag `{"id": "_tag", "maskOutlinePx": [[u, v], ...]}`
(Umriss Tag und Rand). `draw_reference_debug` und `build_status` überspringen diesen Eintrag.

```python
# Modul: src/tagloc/reference_overlay.py (neu)
LAYER_KEYS = ("guess", "band", "candidates", "rejected", "chosen", "mask", "labels", "tagBox")

def effective_layers(layers: Mapping | None) -> dict[str, bool]:
    """Fehlende Schlüssel = True, unbekannte werden ignoriert."""

def draw_reference_debug(image: np.ndarray, debug: list[dict], *,
                         tag_corners_px=None, title: str = "",
                         layers: Mapping[str, bool] | None = None) -> np.ndarray:
    """BGR. Zeichnet auf eine Kopie des Bildes und gibt sie zurück.
    layers None = alle Ebenen an. Der Titel bleibt immer."""
```

Farben: Startwert-Kante gelb gestrichelt, Bandränder dünn gelb, Kandidatenpunkte cyan,
verworfene RANSAC-Linien (Inlier) orange, gewählte Linie grün (rot bei `ambiguous`), Tag-Masken-
Umriss grau, Tag-Rahmen grün. Je Kante ein Label an der Kante, ASCII, z. B. `A  +1.8 mm  0.4 deg`
bzw. `A  NO_EDGE` / `A  ambiguous`. Titel oben links in dunklem Kasten, z. B. `Tag 7 - Vorschau`
oder `Tag 7 - Messung 3 Bilder`. Strichstärken für 640 px Breite, skaliert mit der Bildbreite.

```python
# Modul: src/vision_server/tagreference.py
# parse_request(text) -> TagReferenceRequest
#   Felder: ... , overlay: dict (Ebenen, fehlende = True), save_debug: bool (Standard True).
#   ValueError bei: kein JSON, overlay kein Objekt, Ebene kein Bool, saveDebug kein Bool.
# build_result(..., debug_dir: str | None = None) -> dict   # "debugDir" immer vorhanden, bei Ablehnung null

# Modul: src/vision_server/tagreference_preview.py
PREVIEW_SCHEMA = "wsc.vision.tagreference.preview/1"
PREVIEW_TTL_S = 120.0       # ohne Erneuerung abgelaufen
PREVIEW_INTERVAL_S = 1 / 3  # höchstens 3 Hz
PREVIEW_HOLD_S = 1.0        # Vorschau-Bild zwischen zwei Auswertungen
MEASURE_HOLD_S = 5.0        # Debug-Bild einer Messung
def build_status(*, active, tag_id, tag_seen, debug=(), layers=None) -> dict: ...

# Modul: src/vision_server/tagreference_log.py
MAX_FOLDERS = 50
OVERLAY_NAME = "overlay.jpg"
def write_protocol(directory: Path, tag_id: int, request_json: str, result: dict,
                   frames: list, overlay) -> Path: ...   # wirft OSError bei Schreibfehler
def prune(directory: Path, keep: int = MAX_FOLDERS, protect: Path | None = None) -> None: ...
def latest_overlay(directory: Path | None) -> Path | None: ...
```

Overlay-Modus: `tagloc/modes.py` `OVERLAY_MODES = ("off", "apriltag", "calibration", "undistorted",
"tagreference")`, Anzeige „Tag einmessen“. Stream-Knoten `CameraStreamMode`, Quelle voller Frame wie
`apriltag`.

### Mess-Protokoll und HTTP

- Jede `MeasureTagReference` mit `saveDebug: true` (Standard) legt
  `data/tagreference/<YYYYmmdd-HHMMSS>_tag<id>/` an (Config `tag_reference_debug_dir`, Standard
  `<repo>/data/tagreference`; `None` = nichts schreiben). Namensmuster
  `^\d{8}-\d{6}_tag\d+(_\d+)?$`, der Zusatz `_n` steht bei Namenskollision.
- Inhalt: `request.json`, `result.json` (mit `debugDir`), `frame<k>.png` (Rohbilder, verlustfrei),
  `overlay.jpg` (Debug des letzten Bilds).
- Aufräumen: höchstens `MAX_FOLDERS` = 50 eigene Ordner (nur Ordner mit dem Namensmuster), nach
  mtime, der neu angelegte Ordner wird nie gelöscht. Fremde Ordner bleiben unangetastet.
- Fehler beim Schreiben → Log-Warnung, Messung läuft weiter, `debugDir: null`.
- `ResultJson` bekommt `"debugDir": "<repo-relativer Pfad>" | null` (bei Ablehnung `null`).
- HTTP `GET /tagreference.jpg` (am MJPEG-Port, Pfad wie oben): liefert `overlay.jpg` aus dem
  **neuesten** Messordner. 404, wenn keiner da ist. CORS `*`, `Cache-Control: no-store`.

### Frontend (webskillcomposition)

```ts
// opcua-server/config/piServers.ts
interface TagReferenceBinding {
  previewNodeId: string;        // ...SetTagReferencePreview
  previewStatusNodeId: string;  // ...TagReferencePreviewStatus
}
// CAMERA_OVERLAY_MODES um "tagreference" erweitert (Label "Tag einmessen")
function buildTagReferenceImageUrl(serverUrl: string, port: number): string;
// -> "http://<host>:<port>/tagreference.jpg"

// cell-modules/model/tagReferenceCall.ts
function setTagReferencePreview(
  controller, serverUrl: string, nodeId: string,
  requestJson: string,            // "" = aus
  opts?: { sleep?, timeoutMs? }   // Standard-Timeout 10 s
): Promise<{ status: "done" } | { status: "failed"; reason: string }>;
function parseTagReferencePreviewStatus(raw: string): TagReferencePreviewStatus;
// TagReferenceResult.debugDir?: string | null

// cell-modules/model/tagReferenceRun.ts und robotTagRun.ts
type TagReferencePhase = "approach" | "centre" | "plan" | "drive_view" | "measure" | "return";
onPhase?(job, phase, view?: { index: number; count: number }): void;  // view nur Roboter
preview?(job, lines: TagReferenceLine[] | null): void | Promise<void>; // nur Modul-Lauf
confirm?(job, phase: "measure"): Promise<boolean>;  // false = Job überspringen
onResult?(result): void;

// cell-modules/model/tagReferencePreview.ts
export const PREVIEW_RENEW_MS = 60_000;   // Vorschau wird erneuert, bevor der Pi sie abläuft (120 s)
```

Einstellungen (`cell-modules/model/tagReferenceSettings.ts`): localStorage-Schlüssel
`wsc.tagReference.settings`, alle Zugriffe in try/catch, gespeicherte Werte mit falschem Typ
fallen auf den Standard zurück. `overlayRequest(layers)` liefert `null`, solange alle Ebenen an
sind (Anfrage bleibt dann wie vorher), sonst das `overlay`-Objekt.

Testphase (`cell-modules/model/tagMeasurementState.ts`):

```ts
export const TAG_MEASURE_ALL_TESTPHASE = true;  // Standardwert des Schalters "Alle Tags neu einmessen"
export function measurableTags(catalog): PendingTag[];   // alle Nicht-Welt-Tags
export function selectTagReferenceTags(catalog, options: { measureAll: boolean; ... }): ...;
```

### Netz / Betrieb

- Keine neuen Ports. Der Overlay-Modus läuft über den bestehenden MJPEG-Server, der Pfad
  `/tagreference.jpg` liegt am selben Port.
- Keine neuen systemd-Units.
- Protokollordner `data/tagreference/` auf dem Pi, gitignored.

### Fehlerfälle

- Timeout beim Setzen der Vorschau (10 s im Frontend): der Lauf zeigt einen Hinweis als Notiz,
  die Messung läuft weiter.
- Vorschau nicht gesetzt oder abgelaufen (120 s ohne Erneuerung): Overlay zeigt normale Tag-Rahmen
  und Titel „keine Einmess-Vorschau“.
- Tag nicht im Bild: Titel „Tag 7 nicht im Bild“, normale Tag-Rahmen.
- Server nicht `Operational` oder kein Livestream-Overlay: `INVALID_STATE`, nie `BUSY`.
- Ungültiges Request-JSON, fehlender Tag, ungültiges `overlay`: `INVALID_ARGUMENT`.
- `GET /tagreference.jpg` ohne vorherige Messung: 404.
- Fehler beim Schreiben des Protokolls: Log-Warnung, Messung läuft weiter, `debugDir: null`.
- Debug-Erzeugung schlägt fehl: Log-Eintrag, Messung läuft weiter.
- Kamera fehlt im Overlay-Modus: Titel mit Hinweis, Stream bleibt leer, kein Absturz.
- Abbruch des Laufs oder Schließen des Fensters während eines Laufs: Vorschau wird mit `""` ausgeschaltet.
- Roboter-Lauf: ruft `preview` nicht auf (keine Kanten), zeigt dafür einen eigenen Hinweis.

## C. Nachtrag 21.10.2026: alles schaltbar

Vorgabe des Nutzers: „Es soll auch wieder alles toggle-bar sein, so wie auch beim Einscannen und
Co. schon ist.“

### C1 Pi: Overlay-Ebenen und Protokoll schaltbar

- Anfrage von `SetTagReferencePreview` und `MeasureTagReference` bekommt optional
  `"overlay": {"guess": bool, "band": bool, "candidates": bool, "rejected": bool, "chosen": bool,
  "mask": bool, "labels": bool, "tagBox": bool}`. Fehlende Schlüssel = `true`. Unbekannte Schlüssel
  werden ignoriert. Kein Objekt oder kein Bool als Wert → `INVALID_ARGUMENT`.
- `MeasureTagReference` bekommt optional `"saveDebug": bool` (Standard `true`). Bei `false`:
  kein Protokollordner, `debugDir: null`, `/tagreference.jpg` zeigt weiter den letzten Stand.
- `TagReferencePreviewStatus` enthält `"overlay": {...}` (wirksame Ebenen).

### C2 Frontend: Einstellungen als Toggles

Im Fenster „Tags einmessen“ ein aufklappbarer Bereich „Einstellungen“ mit Toggles. Gespeichert im
localStorage-Schlüssel `wsc.tagReference.settings`.

- **Live-Ansicht** (Overlay-Modus `tagreference` im Stream an/aus; aus = vorheriger Modus)
- **Vorschau während des Laufs** (Vorschau-Aufrufe an/aus)
- Ebenen: **Startwert-Kante**, **Suchband**, **Kandidatenpunkte**, **Verworfene Linien**,
  **Gewählte Linie**, **Tag-Maske**, **Beschriftung**, **Tag-Rahmen** (→ `overlay`-Objekt; Änderung
  wirkt sofort, indem die aktive Vorschau mit neuem `overlay` erneut gesetzt wird)
- **Schritt für Schritt** (vor jeder Messung anhalten)
- **Alle Tags neu einmessen (Testphase)**, Standard an (Standardwert = `TAG_MEASURE_ALL_TESTPHASE`)
- **Modul-Tags** und **Roboter-Tags** (welche Gruppen der Lauf misst)
- **Mess-Protokoll speichern** (`saveDebug`)
- **Debug-Bild nach der Messung anzeigen**, **Kantenwerte live anzeigen**

Tests: Standardwerte, Parsen gespeicherter Werte, Ableitung des `overlay`-Objekts aus den Toggles,
Gruppenauswahl Module/Roboter (`selectTagReferenceTags`).

## Abweichungen vom Plan

Ergebnis der Umsetzung gegen den Plan und die Spezifikation.

**Pi**

- Die Vorschau-Auswertung läuft im **selben Ein-Thread-Pool** wie `stream-overlay-detect`, nicht in
  einem eigenen Pool. Der Detektor ist nicht für zwei Threads gebaut, also laufen beide nacheinander.
- Das Debug-Dict je Kante enthält zusätzlich `found`, `supportPx`, `contrast` und `measuredPolyPx`
  (gewählte Linie mit Saum). Im Status `overlay` (wirksame Ebenen).
- Die Schrift ist **ASCII** („deg“, „-“ statt „°“ und „–“), weil der OpenCV-Hershey-Font keine
  Umlaute und kein Grad-Zeichen kann.
- Titel **oben links in einem dunklen Kasten** statt `draw_status_bar`.
- Protokoll-Tag-Rahmen = projizierter Umriss der Tag-Ecken (wie im Stream).
- `INVALID_STATE` auch dann, wenn der Livestream kein Overlay hat (kein Annotator).
- `debugDir` steht in jedem Ergebnis, bei Ablehnung `null`.
- Das Debug-Bild einer Messung wird 5 s im Stream gehalten (`MEASURE_HOLD_S`).
- `/tagreference.jpg` liefert nur aus dem neuesten Messordner, sonst 404.
- Aufräumen nur eigener Ordner (Namensmuster), nach mtime, der neue Ordner ist geschützt.
- Der Ablauf der Vorschau (120 s) wird auch außerhalb des Modus `tagreference` geprüft
  (`expire_reference_preview`).

**Frontend**

- `preview?(job, lines | null)` statt `requestJson`: der Modul-Lauf baut die Anfrage selbst aus den
  Kanten, `tagReferencePreview.ts` ruft `setTagReferencePreview` mit dem fertigen JSON auf.
- Die Vorschau wird nach `onPhase("plan")` gesetzt und bei `fits: false` mit den übrigen Kanten
  neu gesetzt.
- Roboter-Lauf: vor jeder der drei Ansichten fragt `confirm` nach („Tag überspringen“ verwirft den
  Job). Keine Vorschau bei Roboter-Tags (keine Kanten).
- Zusätzlich: `onResult` und die Rückfahrt laufen auch nach einem Überspringen.
- „Letzte Messung“ ist ein Block; ältere Messungen stehen im Pi-Protokoll.
- Die Testphase ist ein Toggle mit Standard `TAG_MEASURE_ALL_TESTPHASE` (siehe C2).
- Der Vorschau-Timeout (10 s) ist nur eine Notiz, kein Abbruch der Messung.
- Die Vorschau wird alle 60 s erneuert (`PREVIEW_RENEW_MS`, Pi-Ablauf 120 s).
- Schließen des Fensters während eines Laufs stoppt den Lauf wie „Halt“ und schaltet die Vorschau
  endgültig aus; danach wird keine Vorschau mehr eingeschaltet.
- Tag-Auswahl als reine Funktion `selectTagReferenceTags` (testbar ohne React).

## Review

- **Opus-Review** am 21.10.2026 gegen diesen Plan und die Spezifikation: **Freigabe mit Auflagen**.
- Alle Auflagen sind behoben (Commits `b004346` Pi, `f5d7856` Frontend). Die fünf Hauptpunkte:
  1. `/tagreference.jpg` liefert nur aus der neuesten Messung.
  2. Aufräumen löscht nur eigene Protokollordner (Namensmuster), nach mtime, der neue Ordner ist geschützt.
  3. Tag-Rahmen im Modus `tagreference` frieren nicht mehr ein.
  4. Beschriftung lesbar (Kasten), Linien mit Saum.
  5. Der Debug-Pfad kann die Messung nie scheitern lassen (Fehler werden geloggt).
- Frontend-Auflagen: Vorschau-Erneuerung alle 60 s, Schließen stoppt Lauf und Vorschau, Live-Werte
  bei ausgeschalteter Live-Ansicht ausgeblendet, eigener Hinweis bei Roboter-Ansichten, Tag-Auswahl
  als reine Funktion, Overlay-Modus wird über `enterLiveView` zurückgesetzt.

## Umsetzung und Tests

- **Pi:** 797 Tests grün (34 neu gegenüber 763). Am 21.10.2026 mit `unittest` über die Projekt-venv
  nachgeprüft: `Ran 797 tests … OK`.
- **Frontend:** 595 Tests grün (35 neu gegenüber 560), `tsc` und `vite build` ohne Fehler (laut Umsetzung).
- **Commits Pi** (Branch `tag-einmessen-liveview`): `146724c` (Plan), `95eb2a6` (Umsetzung),
  `b004346` (Review-Befunde, neuester Stand). Ein gleichnamiger Plan-Commit `3c7b6e0` liegt nicht auf
  diesem Branch.
- **Commits Frontend** (Branch `tag-einmessen-liveview`): `aec572a` (Umsetzung), `f5d7856` (Review-Befunde).
- Nicht committet in diesem Dokumentationsschritt: Der Stand dieses Plans liegt im Worktree.

## Testphase und spätere Einmal-Regel

**Testphase (jetzt):** Der Schalter „Alle Tags neu einmessen (Testphase)“ im Fenster ist standardmäßig
**an**. Der Standardwert kommt aus `TAG_MEASURE_ALL_TESTPHASE = true` in `tagMeasurementState.ts`.
Jeder Lauf misst dann alle messbaren Tags neu (`measurableTags`). Das Panel zeigt
„Alle Tags einmessen (n) — Testphase“ und den Hinweis „Testphase: alle Tags werden bei jedem Lauf
neu eingemessen“. Zweitmessungen behalten den ursprünglichen Startwert
(`guess: tag.measurement?.guess ?? {position, rotationDeg}`), damit `placementMatches` nicht bricht.

**Später (Zielzustand, Vorgabe des Nutzers):** Jeder Tag wird nur **einmal pro neuem bzw.
bearbeitetem Modul** eingemessen. Dafür den Schalter ausschalten bzw. `TAG_MEASURE_ALL_TESTPHASE = false`
setzen. Dann gilt `pendingTags` mit der Einmal-Regel (`invalidateChangedMeasurements`). Diese Regel
bleibt im Code und wird nicht entfernt. Der Label wird dann „Ausstehende Tags einmessen (n)“.

## Offene Fragen

- **Rechenzeit der Vorschau am Pi:** Testrechner 52–62 ms je Auswertung. Am Hand-Pi nicht gemessen,
  ob 3 Hz neben dem Livestream reichen. Notfalls auf 1–2 Hz senken (`PREVIEW_INTERVAL_S`).
- **Lesbarkeit am echten Stream:** Strichstärken und Schrift sind für 640 px geschätzt und am echten
  Stream noch nicht angesehen.
- **Label-Überlappung:** Die Beschriftung kann die Kante überdecken. Noch nicht geprüft.
- **DOM-Tests im Frontend:** nicht vorhanden (vitest mit `environment: node`). Die Komponenten sind
  nur über die reinen Funktionen in `model/` getestet.
- **Platzbedarf Protokollordner:** 50 Ordner mit Rohbildern (PNG) können viele MB ergeben. Ob das auf
  der SD-Karte des Pi passt, ist nicht geprüft.
- **Startwert bei Zweitmessung:** Ob der ursprüngliche Startwert bei einer Testphase-Messung immer der
  richtige Bezug ist, wenn ein Modul von Hand bewegt wurde, ist offen.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` auf `fertig` gesetzt.
- [x] `vision-server-interface.md`: Abschnitt „19.1 Live-Ansicht und Mess-Protokoll“ ergänzt, Zeile
      `tagreference` in der Overlay-Tabelle (§10).
- [x] `apriltag-referenz.md`: Abschnitt zur Live-Ansicht der Kantensuche und zur Testphase ergänzt (§14).
- [ ] Nachtrag in `tag-einmessen-bezugskanten.md` mit Link auf diesen Plan (nicht Teil dieses Auftrags).
- [ ] Am Pi mit echtem Stream angesehen, Overlay-Strichstärken und Rechenlast bewertet.
