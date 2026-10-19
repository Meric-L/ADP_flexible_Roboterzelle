# AprilTag im Modulkatalog per Button um 90° drehen

**Status:** in Arbeit — 19.10.2026
**Verantwortlich:** Meric, `Agent: Tag um 90° drehen`
**Thema:** frontend
**Branch:** `Ungetestet` (Repo `webskillcomposition`)

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

**Zu ändern:**
- `model/tagPlacement.ts`: neue Funktion `turnPlacement(placement: TagPlacement, quarterTurns: 1 | -1): TagPlacement`
- `model/tagGeometry.ts`: neue Funktion `turnRotationDegAboutTagZ(rotationDeg: Vec3, quarterTurns: 1 | -1): Vec3`
- `components/ModuleCatalogModal.tsx`: Buttons rendern, Klicks handhaben
- `tests/model/tagPlacement.test.ts`: Test für `turnPlacement`
- `tests/model/tagGeometry.test.ts`: Test für `turnRotationDegAboutTagZ`

**Nicht betroffen:**
- Datenformat `wsc.cellModules.catalog/1` — unverändert
- Downstream-Schnittstellen: an den Pi (Layer 2, `wsc.vision.layer2.run/1`, `poseInModule`) gehen weiterhin nur `position` und `rotationDeg`; `placement` verlässt den Browser nicht.
- Pi, Tag-Map, Layer 2 — nicht betroffen.

## Schnittstellen

### TypeScript: Drehung um die Tag-Z-Achse in `model/tagPlacement.ts`

```ts
function turnPlacement(placement: TagPlacement, quarterTurns: 1 | -1): TagPlacement;
// Berechnet den aktuellen Winkel von edgeA.direction (in die Fläche projiziert)
// zu Tag-+X um Tag-+Z mittels atan2.
// Funktioniert auch wenn inPlaneRotationDeg === null („Oberkante nach oben").
// Addiert ±90°, normiert auf (−180°, 180°], rundet auf 1e-6.
// Rückgabe: { ...placement, inPlaneRotationDeg: <Zahl> }
// Folge: aus null wird ein Zahlenwert, das Häkchen „Oberkante des Drucks nach oben" geht aus, der Tag springt nicht.
// flipped und offsetM bleiben unverändert.
```

**Beispiel:**
```js
// Tag mit inPlaneRotationDeg: null auf einer waagerechten Fläche
let p = {
  mode: "corner",
  normal: [0, 0, 1],
  edgeA: { point: [0, 0, 0], direction: [1, 0, 0], inward: [0, 1, 0] },
  distancesM: [0.1, 0.05],
  inPlaneRotationDeg: null
};

// Nach +90°-Klick:
let p2 = turnPlacement(p, 1);
// p2.inPlaneRotationDeg === 90 (edgeA.direction war Tag-+X, jetzt 90° gedreht)

// Nach weiteren −90°-Klick:
let p3 = turnPlacement(p2, -1);
// p3.inPlaneRotationDeg === 0
```

### TypeScript: Drehung um die Tag-Z-Achse in `model/tagGeometry.ts`

```ts
function turnRotationDegAboutTagZ(rotationDeg: Vec3, quarterTurns: 1 | -1): Vec3;
// Führt Drehung um die eigene Tag-Z-Achse durch: q · Rz(±90°)
// q = Quaternion aus rotationDeg (rpy, Katalogformat)
// Rz(±90°) = 90°-Drehung um die Tag-Z-Achse
// Rückgabe: neuer rotationDeg-Vektor in rpy (Katalogformat)
// Für Tags ohne passende Platzierung (d. h. inPlaneRotationDeg nicht berechenbar).
```

**Beispiel:**
```js
let rotRpy = [0, 0, 0]; // Tag-Frame fällt mit Modul-Frame zusammen
let rotRpy2 = turnRotationDegAboutTagZ(rotRpy, 1); // +90° um Tag-Z
// rotRpy2 ≈ [0, 0, 90] oder ähnlich, abhängig von rpy-Konvention
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

**Button-Verhalten:**
- Beide Buttons sind immer aktiv (auch wenn `inPlaneRotationDeg === null`).
- Klick auf ↺ ruft `turnPlacement(placement, 1)` auf.
- Klick auf ↻ ruft `turnPlacement(placement, -1)` auf.
- Danach wird der Tag sofort mit neuer `inPlaneRotationDeg` neu gezeichnet.
- Das Häkchen „Oberkante des Drucks nach oben" geht aus (da `inPlaneRotationDeg` nun eine Zahl ist).

**Sichtbarkeit:**
- Im Platzier-Panel: neben dem Label „Oberkante des Drucks nach oben" oder unter dem Häkchen.
- In der Tag-Zeile: neben dem Button „Auf Modell platzieren".

### Fehlerfälle

- **Tag ohne Platzierung:** `turnPlacement` wird nicht aufgerufen; stattdessen `turnRotationDegAboutTagZ` auf die aktuelle `rotationDeg` anwenden.
- **Ungültige Platzierung:** Falls `edgeA.direction` nicht normalisiert oder andere Invarianten verletzt sind, wirft `turnPlacement` einen aussagekräftigen Fehler; der Tag bleibt unverändert.
- **Numerische Ungenauigkeit:** Die berechnete Drehung wird auf 1e-6 gerundet, um Floating-Point-Artefakte zu vermeiden.

## Vorgehen

1. **`turnPlacement` in `model/tagPlacement.ts` implementieren:**
   - Berechne den aktuellen Winkel von `edgeA.direction` (in die Fläche projiziert) zu Tag-+X um die Tag-Z-Achse mit `atan2`.
   - Addiere ±90° (je nach `quarterTurns`).
   - Normiere auf (−180°, 180°].
   - Runde auf 1e-6.
   - Gebe `{ ...placement, inPlaneRotationDeg: <Zahl> }` zurück.
   - Behandle den Fall `inPlaneRotationDeg === null` korrekt (berechne den impliziten Winkel aus Face-Presets).

2. **`turnRotationDegAboutTagZ` in `model/tagGeometry.ts` implementieren:**
   - Wandle `rotationDeg` (rpy) in eine Quaternion um.
   - Multipliziere mit einer 90°-Drehung um die Tag-Z-Achse: `q_new = q * Rz(±90°)`.
   - Wandle die neue Quaternion zurück in rpy.
   - Rückgabe: neuer `rotationDeg`-Vektor.

3. **Buttons in `components/ModuleCatalogModal.tsx` rendern:**
   - Im Platzier-Panel neben dem Häkchen „Oberkante des Drucks nach oben".
   - In der Tag-Zeile neben „Auf Modell platzieren".
   - Beide Buttons sagen ↺ / ↻.

4. **Klick-Handler:**
   - Beim Klick auf ↺: `turnPlacement(currentPlacement, 1)` oder `turnRotationDegAboutTagZ(currentRotationDeg, 1)`.
   - Beim Klick auf ↻: `turnPlacement(currentPlacement, -1)` oder `turnRotationDegAboutTagZ(currentRotationDeg, -1)`.
   - UI updaten, `placement` oder `rotationDeg` im Zustand ändern.

5. **Tests schreiben:**
   - `model/tagPlacement.test.ts`: Test für `turnPlacement` mit verschiedenen `placement`-Zuständen, u. a. `inPlaneRotationDeg === null`.
   - `model/tagGeometry.test.ts`: Test für `turnRotationDegAboutTagZ` mit verschiedenen `rotationDeg`-Eingaben.

6. **Integration testen:**
   - Modulkatalog öffnen, einen Tag mit Platzierung wählen.
   - Buttons ↺ / ↻ klicken und verifizieren, dass der Tag um 90° gedreht wird.
   - Häkchen „Oberkante des Drucks nach oben" geht aus, wenn Drehung erstmals angewendet wird.
   - Drehung mehrmals wiederholen; nach 4× ↺ oder 4× ↻ sollte der Tag in der Ursprungslage sein.

## Offene Fragen

- Keine. Entscheidungen: Buttons an beiden Orten; Drehung immer aus der sichtbaren Lage.

## Abweichungen vom Plan

(wird während der Umsetzung gefüllt)

## Nach Abschluss

- Status auf `fertig` setzen.
- Tatsächliche Implementierung der Funktionssignaturen in diesem Plan nachtragen.
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
