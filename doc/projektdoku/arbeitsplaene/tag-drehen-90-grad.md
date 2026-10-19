# AprilTag im Modulkatalog per Button um 90° drehen

**Status:** fertig — 19.10.2026
**Verantwortlich:** Meric, `Agent: Tag um 90° drehen`
**Thema:** frontend
**Branch:** `Ungetestet` (Repo `webskillcomposition`), Commit `862e49a`

## Ziel

Im Modulkatalog (webskillcomposition, `frontend/src/features/cell-modules/`) zwei Buttons ↺ / ↻ hinzufügen, die einen AprilTag um ±90° um seine eigene Normale (Tag-+Z) drehen. Die Konvention: ↺ = +90° (gegen den Uhrzeigersinn beim Blick auf den Tag), ↻ = −90° (gleiche Konvention wie `spin()` im Tag-Tool `frontend/src/tag-tool/TagToolApp.tsx`).

Die Buttons erscheinen an zwei Stellen:
1. Im Platzier-Panel „Tag auf eine Fläche des Modells setzen" (Schritt 4, neben „Oberkante des Drucks nach oben")
2. In jeder Tag-Zeile neben „Auf Modell platzieren" (auch für Tags ohne Modell, d. h. Flächen-Voreinstellung oder eigene Drehung)

**Bisheriges Problem:** Die Drehung ging nur über Häkchen + Zahlenfeld „Drehung ab der Kante (°)". Abwählen des Häkchens setzte 0° und ließ den Tag springen. Mit den neuen Buttons bleibt die aktuelle Drehung erhalten und wird inkrementell angepasst.

## Bereits gelesen

- `CLAUDE.md` (Arbeitsplanpflicht, Regeln für Änderungen)
- `doc/projektdoku/arbeitsplaene/README.md` (kein offener `frontend`-Plan zum Thema)
- `doc/projektdoku/arbeitsplaene/modell-flaechen-tag-platzierung.md` (Datenformat `placement`, Schnittstellen-Stil)
- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md` (AprilTag-Kontext)
- `doc/projektdoku/apriltag-referenz.md` §1 (Tag-Frame-Konvention: +X rechts, +Y Oberkante, +Z aus dem Tag)

## Betroffene Dateien

Alle Pfade liegen in `webskillcomposition/frontend/src/features/cell-modules/`.

**Geändert:**
- `model/tagPlacement.ts`: neue Funktion `turnPlacement`
- `model/tagGeometry.ts`: neue Funktion `turnRotationDegAboutTagZ`
- `components/ModuleCatalogModal.tsx`: Buttons rendern, Klicks handhaben
- `model/tagPlacement.test.ts`: Test für `turnPlacement`
- `model/tagGeometry.test.ts`: Test für `turnRotationDegAboutTagZ`

**Nicht betroffen:**
- Datenformat `wsc.cellModules.catalog/1` — unverändert
- Downstream-Schnittstellen: an den Pi (Layer 2, `wsc.vision.layer2.run/1`, `poseInModule`) gehen weiterhin nur `position` und `rotationDeg`; `placement` verlässt den Browser nicht.
- Pi, Tag-Map, Layer 2 — nicht betroffen.

## Schnittstellen

### TypeScript: Drehung in `model/tagPlacement.ts`

```ts
export function turnPlacement(placement: TagPlacement, quarterTurns: 1 | -1): TagPlacement;
```

Dreht eine Platzierung um ±90° um die Tag-+Z-Achse (Normale der Fläche):
- **Berechnung:** Der aktuelle Winkel wird aus `placementAxes` (die Achsen aus der Platzierung) als `edgeA.direction` in der Fläche projiziert, dann `atan2` um die Tag-Z-Achse verwendet.
- **Funktioniert auch bei** `inPlaneRotationDeg === null` („Oberkante nach oben"): Der implizite Winkel wird aus den Achsen berechnet und ±90° addiert.
- **Normierung:** Das Ergebnis wird auf (−180°, 180°] normiert, dann auf 1e-6° gerundet.
- **Rückgabe:** `{ ...placement, inPlaneRotationDeg: <Zahl> }` — die neue Platzierung unterscheidet sich nur im Winkelfeld.
- **Folge:** Aus `null` wird ein Zahlenwert; das Häkchen „Oberkante des Drucks nach oben" ist danach falsch und sollte aus sein; der Tag springt nicht.

**Beispiel:**
```js
turnPlacement({ mode: "corner", normal: [0, 0, 1], inPlaneRotationDeg: null, ... }, 1)
// → { ..., inPlaneRotationDeg: 90 }

turnPlacement({ ..., inPlaneRotationDeg: 90 }, -1)
// → { ..., inPlaneRotationDeg: 0 }

turnPlacement({ ..., inPlaneRotationDeg: 180 }, 1)
// → { ..., inPlaneRotationDeg: -90 }  (normiert auf (−180°, 180°])
```

### TypeScript: Drehung in `model/tagGeometry.ts`

```ts
export function turnRotationDegAboutTagZ(rotationDeg: Vec3, quarterTurns: 1 | -1): Vec3;
```

Dreht einen rpy-Rotationsvektor um ±90° um die Tag-+Z-Achse:
- **Berechnung:** Quaternion aus `rotationDeg`, dann Multiplikation von rechts mit der 90°-Drehung um die Tag-Z-Achse (`multiplyQuaternions`), Ergebnis zurück zu rpy.
- **Normierung:** Das Ergebnis wird auf 1e-6° gerundet.
- **Rückgabe:** neuer `rotationDeg`-Vektor [r, p, y].
- **Verwendung:** Für Tags ohne Platzierungsdatensatz (nur rpy gegeben, nicht `inPlaneRotationDeg`).

**Beispiel:**
```js
turnRotationDegAboutTagZ([0, 0, 0], 1)
// → [0, 0, 90]

turnRotationDegAboutTagZ([0, 0, 0], -1)
// → [0, 0, -90]

turnRotationDegAboutTagZ([90, 0, 30], 1)
// → neuer rpy nach 90°-Drehung um Tag-+Z
```

### Datenformat `placement` (unverändert)

Das Format bleibt wie in `modell-flaechen-tag-platzierung.md` beschrieben. Nach einer Drehung per Button ändert sich nur `inPlaneRotationDeg`:

```json
{
  "mode": "corner",
  "normal": [0, 0, 1],
  "edgeA": { "point": [0, 0, 0], "direction": [1, 0, 0], "inward": [0, 1, 0] },
  "distancesM": [0.1, 0.05],
  "inPlaneRotationDeg": 90,
  "offsetM": 0.005
}
```

### UI: Buttons und Zustandsübergänge

**Komponente `TurnButtons` in `components/ModuleCatalogModal.tsx`:**
- Zwei Buttons ↺ / ↻, Stil `button-ghost`.
- `title` und `aria-label`: „Um 90° gegen den Uhrzeigersinn drehen (Blick auf den Tag)" / „Um 90° im Uhrzeigersinn drehen (Blick auf den Tag)".

**Im TagPlacementPanel (Schritt 4):**
- Buttons vor dem Häkchen „Oberkante des Drucks nach oben".
- Klick: `onPlacement(turnPlacement(placement, ±1))`.

**In der Tag-Zeile (TagEditor):**
- Buttons direkt nach „Auf Modell platzieren".
- Deaktiviert, während ein Tag gerade platziert wird.
- **Platzierter Tag** (`placementMatches`): `turnPlacement(placement, ±1)` auf den Platzierungsdatensatz, dann Pose per `tagPoseFromPlacement`.
- **Sonst** (keine Platzierung): `turnRotationDegAboutTagZ(rotationDeg, ±1)` auf den rpy-Vektor; `placement` wird entfernt. Wenn keine Flächen-Voreinstellung mehr passt, springt die Auswahl „Fläche" auf „Eigene Drehung…" mit r/p/y angezeigt.

### Fehlerfälle

- **Numerische Ungenauigkeit:** Alle Ergebnisse werden auf 1e-6° gerundet, um Floating-Point-Artefakte zu vermeiden.

## Vorgehen (umgesetzt)

1. Beide Funktionen berechnen einen neuen Winkel aus der aktuellen Pose und addieren ±90°, normiert auf (−180°, 180°].
2. `turnPlacement` liest die Achsen aus der Platzierung (über `placementAxes`), nicht aus Face-Presets.
3. Buttons in `ModuleCatalogModal.tsx` im Platzier-Panel vor dem Häkchen und in der Tag-Zeile nach „Auf Modell platzieren".
4. Platzierte Tags: `turnPlacement` + `tagPoseFromPlacement`; sonst `turnRotationDegAboutTagZ` auf `rotationDeg`.
5. Tests: `turnPlacement` mit vier Achsen-Orientierungen (waagerecht, Wand, geflippt, 30° vorgedreht), Rückdrehung, vier Umdrehungen, Normierung (180 + 90 → −90, −90 − 90 → 180).
6. Gleich für `turnRotationDegAboutTagZ`: flaches Tag [0,0,0] → [0,0,90], senkrecht um Tag-+Z (Y → X), vier Umdrehungen.

## Offene Fragen

- Keine. Entscheidungen: Buttons an beiden Orten; Drehung immer aus der sichtbaren Lage.

## Abweichungen vom Plan

Keine inhaltlichen Abweichungen; die Implementierung folgt dem Plan genau.

**Tests:**
- `tagPlacement.test.ts`: vier Achsen-Orientierungen (`level`, `wall`, `flipped`, `turned`), Rückdrehung und vier Umdrehungen, Normierung auf (−180°, 180°].
- `tagGeometry.test.ts`: flaches Tag [0,0,0] ↔ ±90°, senkrecht [90,0,0] um Tag-+Z (Y wird zu X bzw. −X), vier Umdrehungen äquivalent zur Identität.

## Nach Abschluss

- [x] Status auf `fertig` gesetzt — 19.10.2026.
- [x] Tatsächliche Funktionssignaturen nachtragen.
- [x] Schnittstellen präzisieren (Achsen, atan2, Normierung, multiplyQuaternions).
- [x] UI-Platzierung und Verhalten dokumentieren.
