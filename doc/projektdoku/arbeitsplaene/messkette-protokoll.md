# Messkette vollständig mitschreiben

**Status:** in Arbeit — 03.11.2026
**Verantwortlich:** Meric, `Agent: Messkette im Versuchs-JSON protokollieren`
**Thema:** vision
**Branch:** `messkette-protokoll` (dieses Repo und webskillcomposition)

## Ziel

Die Versuchs-JSON eines Use-Case-Versuchs (`data/use-case/reihe_NNN/versuch_NNN.json`)
enthält heute nur Endergebnisse: die fertige Tischpose aus Layer 2 und die fertige
Bildmessung. Hinterher soll jede Zwischengröße der Messkette darin stehen, damit
Fehlerquellen offline zerlegt werden können: jede `MeasureTags`-Antwort mit
Gelenkwinkeln und Flanschpose, jedes Einzelbild, die Fusion der Ansichten,
die Basiskorrektur, die Ist-Gelenke jeder Fahrt und die Rohdaten der Fotomessung.

## Bereits gelesen

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/use-case-foto.md`, `use-case-messung.md`
- `doc/projektdoku/arbeitsplaene/layer2-measuretags.md`
- `doc/projektdoku/vision-server-interface.md` §17, §18a
- `doc/projektdoku/bewegungsplanung-und-visual-servoing.md`

## Betroffene Dateien

Dieses Repo:
- `src/vision_server/measuretag.py` — Einzelbilder je Tag
- `src/use_case_eval/measure.py` — Zwischenwerte der Fotomessung
- `tests/test_measuretag.py`, `tests/test_use_case_eval.py`
- `doc/projektdoku/vision-server-interface.md` — Nachträge zu §17 und §18a

webskillcomposition (`frontend/src/features/cell-modules/`):
- `model/measureTagsCall.ts` — Roh-Antwort des Pi weiterreichen
- `model/layer2Chain.ts` (neu) — Protokoll eines Layer-2-Laufs, Ablage je Modul
- `components/ApproachPlanPanel.tsx` — Protokoll füllen, beim Übernehmen ablegen
- `model/useCaseRecord.ts`, `model/usePickPlaceRun.ts` — Protokoll und Fahrten in den Versuch
- Tests zu `layer2Chain` und `useCaseRecord`

## Schnittstellen

### OPC UA

Keine neuen Knoten oder Methoden. `MeasureTags` und `SaveUseCaseImage` behalten
Signatur und Schema-Version: alle Felder kommen **hinzu**, keines entfällt.

### Daten: `MeasureTags`-Ergebnis (`wsc.vision.measuretag.result/1`), neu je Tag

```json
"frames": [
  {
    "capturedAt": "ISO-8601, Aufnahmezeit des Bildes",
    "poseInCamera": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
    "reprojectionErrorPx": 0.4,
    "ambiguous": false,
    "ambiguityRatio": 0.12,
    "cornersPx": [[u, v], [u, v], [u, v], [u, v]],
    "decisionMargin": 54.0
  }
]
```

Ein Eintrag je Bild, in dem der Tag erkannt wurde, in Aufnahmereihenfolge. Die
bisherigen Felder (`poseInCamera` = Mittel, `cornersPx` = letztes Bild …) bleiben
unverändert. Ecken roh vom Detektor (verzeichnet), Pose in OpenCV-Optikrahmen.

### Daten: Bildmessung (`messung` / `ResultJson`), neues Feld `zwischenwerte`

Nur bei `ok: true` vollständig; bei Fehlschlag soweit berechnet. Längen in mm im
Blattsystem, Pixel im Rohbild (verzeichnet) bzw. entzerrt wie angegeben.

```json
"zwischenwerte": {
  "marker": {
    "<id>": {
      "eckenRohPx": [[u, v], ...],
      "eckenEntzerrtPx": [[u, v], ...],
      "kanteVerfeinert": true,
      "reprojektionRmsPx": 0.6
    }
  },
  "blattPose": {
    "rvec": [rx, ry, rz],
    "tvecMm": [tx, ty, tz],
    "rotationBlattZuKamera": [[...], [...], [...]],
    "kameraImBlattMm": [x, y, z],
    "optischeAchseImBlatt": [x, y, z],
    "punkte": 16
  },
  "wuerfel": {
    "eckenOberseiteMm": [[x, y, z], ...],
    "kantenYawDeg": [a, b, c, d],
    "markerMitteMm": [x, y],
    "versatzImBlattMm": [x, y]
  },
  "durchstosspunktMm": [x, y, z]
}
```

`reprojektionRmsPx` je Blattmarker aus der Blattpose; beim Würfelmarker fehlt er
(nicht Teil des PnP).

### Daten: Client-JSON (`wsc.usecase.trial/1`), neue Felder

| Feld | Inhalt |
| --- | --- |
| `layer2` | Protokoll des Layer-2-Laufs, aus dem die Tischpose stammt (`wsc.layer2.chain/1`, unten), oder `null` (Tisch nur aus Layer 1, oder Lauf vor dem Neuladen der Seite ohne gespeichertes Protokoll) |
| `drives` | Jede Fahrt des Pick-&-Place-Laufs: `{ what, slow, target, reached, flangeInBase, status, reason?, at }` — Soll- und Ist-Gelenke, Flanschpose per FK aus den Ist-Gelenken |

`wsc.layer2.chain/1`:

```json
{
  "schema": "wsc.layer2.chain/1",
  "runId": "ISO-Startzeit",
  "startedAt": "...", "finishedAt": "...", "acceptedAt": "...",
  "robotId": "...",
  "poseFlangeCam": { "position": [...], "orientation": [...] },
  "standoffM": 0.35, "anchorStandoffM": 1.0,
  "baseLayer1": { "position": [...], "orientation": [...] },
  "baseCorrected": { "...": "Basis nach dem Anker, oder null" },
  "home": [ ... ], "startJoints": [ ... ],
  "tags": [
    {
      "tagId": 7, "moduleId": "...", "moduleKey": "...", "moduleName": "...",
      "purpose": "anchor | module",
      "planned": { "tagInBase", "cameraInBase", "flangeInBase", "tiltDeg", "angles", "approximate" },
      "measurements": [
        {
          "at": "ISO", "phase": "Statustext der Anzeige zum Zeitpunkt der Messung",
          "requestedTagIds": [7], "frames": 3,
          "joints": [ ... ], "flangeInBase": { ... }, "cameraInBase": { ... },
          "status": "measured | failed", "reason": "...",
          "result": { "...": "MeasureTags-Ergebnis des Pi, unverändert" },
          "tagsInBase": [ { "tagId": 7, "tagInBase": { ... } } ]
        }
      ],
      "centred": { "status", "rounds", "searchSteps", "lateralErrorM", "distanceErrorM", "tagInBase", "note" },
      "close": { "...": "gleich, Nahmessung; oder null" },
      "views": { "seen": [ { "index", "ambiguous", "tagInBase" } ], "warnings": [ ... ], "outcome": "..." },
      "fused": { "tagInBase", "spreadMm", "spreadDeg", "used", "rejected", "unambiguous", "warnings" },
      "result": { "...": "Anker: Basiskorrektur; Modul: neue Pose, Verschiebung, Drehung" },
      "status": "done | failed", "detail": "..."
    }
  ]
}
```

Das Protokoll gehört zum Lauf. Beim „Übernehmen“ wird es für jedes übernommene
Modul abgelegt (im Speicher und im `localStorage`, Schlüssel
`wsc.layer2.chain`), beim „Verwerfen“ nicht. Der Versuch nimmt das Protokoll des
Tisches, auf den abgelegt wird.

### Fehlerfälle

- Gelenke vor einer Messung nicht lesbar: `joints`/`flangeInBase` = `null`, die
  Messung selbst läuft wie bisher.
- `localStorage` voll oder gesperrt: Protokoll nur bis zum Neuladen; der Versuch
  läuft ohne Fehler, `layer2` ist dann `null`.
- Alter Pi ohne `frames`: Feld fehlt im Ergebnis, sonst nichts.

## Vorgehen

1. Pi: `frames` in `MeasureTags`, `zwischenwerte` in der Bildmessung, Tests.
2. Frontend: Roh-Antwort in `measureTags`, `layer2Chain.ts`, Aufzeichnung im Layer-2-Lauf, Ablage beim Übernehmen.
3. Frontend: `layer2` und `drives` im Client-JSON, Tests.
4. Doku: Nachträge in `vision-server-interface.md`.

## Offene Fragen

- Größe: ein Layer-2-Lauf mit 5 Tags ergibt grob 100–300 kB JSON je Versuch.
  Reicht das, oder soll das Protokoll als eigene Datei neben dem Versuch liegen?

## Abweichungen vom Plan

—
