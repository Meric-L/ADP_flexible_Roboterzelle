# AprilTags über dominante Flächen und Kanten des Modulmodells platzieren

**Status:** fertig (vorläufig) — 29.09.2026. Die Funktion läuft im Modulkatalog und ist in `webskillcomposition` committet (`839aa58`). Sie kann weiterentwickelt werden; siehe „Offene Fragen“.
**Verantwortlich:** Meric, `Agent: Tags auf Modellflächen platzieren`
**Thema:** frontend
**Branch:** `feature/modulkatalog` (Repo `webskillcomposition`)

## Ziel

Wo ein AprilTag auf einem Modul klebt (`ModuleTag.position` / `rotationDeg` im
Modul-KS), wird im Modulkatalog-Dialog nicht mehr an der **Umrissbox** abgelesen,
sondern am **3D-Modell selbst** vermessen. Die Box ist bei Tischen mit Roboter und
Aufbauten unbrauchbar.

Aus dem geladenen Modell (URDF, DAE, glTF, STL) werden die **dominanten ebenen
Flächen** berechnet, dazu pro Fläche ihre **langen Außenkanten** und **Ecken**. Der
Ablauf ist:

1. Fläche wählen.
2. Ecke oder zwei Kanten anklicken.
3. Die am echten Modul gemessenen Abstände eintragen.

Der Tag liegt dann bündig auf der Fläche.

**Item-Profile:** Koplanare Teilflächen mit Spalten bis zu einer einstellbaren
Breite (Standard 10 mm) werden **zu einer Fläche zusammengefasst**. Damit wird ein
Rahmen aus Aluprofilen mit Nuten und Fugen als eine Fläche mit durchgehenden
Außenkanten erkannt.

Zusätzlich gibt es zwei Möglichkeiten:
- **Kanten außerhalb der Fläche:** Die Bezugskanten im Modus „zwei Kanten“ dürfen
  **beliebige Modellkanten** sein, auch außerhalb der gewählten Fläche, z. B. die
  Tischkante unter einem Profil oder die Kante eines Roboterfußes. Sie werden
  auf die Fläche projiziert, und der Abstand wird in der Fläche gemessen.
- **z-Offset:** Der Tag kann um einen **Abstand vor der Fläche** versetzt werden,
  etwa für einen Halter, ein Distanzstück oder eine Platte, die das Modell nicht
  enthält.

Vorher gab es nur die Face-Presets der Umrissbox (`TAG_FACE_PRESETS`) und Zahlen
von Hand. Das vorhandene Kanten-Picking (`modelEdges.ts`) setzte nur den
Modul-Ursprung und die X-Achse, nicht den Tag.

## Bereits gelesen

- `CLAUDE.md` (Arbeitsplanpflicht, Regeln für Änderungen)
- `doc/projektdoku/arbeitsplaene/README.md` (kein offener `frontend`-Plan zum Thema)
- `doc/projektdoku/apriltag-referenz.md`: Tag-Frame-Konvention (+X rechts, +Y
  Oberkante, +Z aus dem Tag), `tagToModule`
- `webskillcomposition/frontend/src/features/cell-modules/config/moduleCatalog.ts`:
  `ModuleTag`, Face-Presets, Modul-KS (+Z oben)

## Betroffene Dateien

Alle Pfade liegen in `webskillcomposition/frontend/src/features/cell-modules/`.

**Neu:**
- `model/modelPlanes.ts`: Flächen-, Kanten- und Eckensuche, reine Mathematik ohne three.js
- `model/tagPlacement.ts`: Tag-Pose aus Ecke + Abständen bzw. zwei Kanten
- `model/planeWorker.ts`: Web Worker um `findPlanes`
- `components/ModelPlanes.tsx`: Meshes einsammeln, Worker-Aufruf, Overlay, Picking, Maßlinien
- Tests: `model/modelPlanes.test.ts`, `model/tagPlacement.test.ts`

**Geändert:**
- `config/moduleCatalog.ts`: `ModuleTag.placement`, `TagPlacement`, `TagPlacementEdge`
- `model/tagGeometry.ts`: `quaternionFromBasis`
- `model/catalogStore.ts`: `placement` importieren und exportieren (defekte Einträge werden verworfen)
- `model/catalogValidation.ts`: keine Umrissbox-Warnung für platzierte Tags
- `components/ModulePreview.tsx`: `PlaneTool`, Overlay und Maßlinien in `ModuleScene`
- `components/ModuleCatalogModal.tsx`: Schaltfläche „Auf Modell platzieren“, `TagPlacementPanel`, Mitführen von `placement` bei neuem Ursprung oder neuer X-Achse
- Tests: `model/catalogStore.test.ts`, `model/catalogValidation.test.ts`

**Außerdem:** Das Submodul `frontend/public/urdf` (`misc/urdf.git`) ist ausgecheckt.
Es enthält nur Roboter-URDFs; `fr3_description_with_wagon` diente als Testmodell.

## Schnittstellen

### Daten: `ModuleTag.placement` im Modulkatalog (`wsc.cellModules.catalog/1`)

Das Feld ist optional und wird mit dem Katalog in `localStorage` gespeichert und
per JSON exportiert. Es ist nur ein **Protokoll**: maßgeblich bleiben `position`
und `rotationDeg`. Pi, Tag-Map und Layer 2 sind nicht betroffen.

- **Längen** in Metern, Richtungen als Einheitsvektoren im Modul-KS.
- **Manuelles Überschreiben:** Wird Position oder Drehung von Hand geändert, wird
  `placement` entfernt.
- **Ursprung oder X-Achse neu gesetzt:** `placement` wird mitverschoben bzw.
  mitgedreht.

```json
{
  "tagId": 4, "sizeM": 0.1,
  "position": [-0.2, -0.15, 0.8], "rotationDeg": [0, 0, 0],
  "placement": {
    "mode": "corner",
    "normal": [0, 0, 1],
    "edgeA": { "point": [-0.3, -0.2, 0.8], "direction": [1, 0, 0], "inward": [0, 1, 0] },
    "distancesM": [0.1, 0.05],
    "inPlaneRotationDeg": null,
    "offsetM": 0.005
  }
}
```

| Feld | Bedeutung |
| --- | --- |
| `mode` | `"corner"`: `distancesM` = [entlang `edgeA` ab `edgeA.point`, senkrecht in die Fläche]. `"edges"`: `distancesM` = [senkrechter Abstand zu Kante A, zu Kante B]. |
| `normal` | Außennormale der Fläche. Tag-+Z zeigt entlang dieser Normalen, bei `flipped` entgegengesetzt. |
| `edgeA`, `edgeB` | Bezugskanten, **auf die Fläche projiziert**: ein Punkt der Kante (bei `corner` die Ecke selbst), die Richtung (bei `corner` von der Ecke weg) und `inward` (in der Fläche, senkrecht zur Kante, zur Fläche hin). `edgeB` gibt es nur bei `edges`. Für Kanten außerhalb der Fläche zeigt `inward` zum Flächenschwerpunkt; negative Abstände sind erlaubt. |
| `inPlaneRotationDeg` | `null` = Oberkante des Drucks „oben“: auf waagerechten Flächen (\|n_z\| ≥ cos 45°) Tag-+Y Richtung Modul-+Y, sonst Richtung Modul-+Z. Das entspricht genau den `TAG_FACE_PRESETS`. Ein Wert = Drehung von `edgeA.direction` zu Tag-+X um die Tag-Z-Achse, in Grad. |
| `flipped` | optional, `true` = Tag schaut gegen die Normale; für Modelle mit nach innen gewickelten Flächen |
| `offsetM` | optional, Abstand des Tags vor der Fläche in m, entlang der Tag-+Z-Achse (Blickrichtung). Fehlt = 0. Die Abstände in `distancesM` beziehen sich auf den Fußpunkt in der Fläche. |

### TypeScript: Flächensuche (`model/modelPlanes.ts`)

```ts
function findPlanes(meshes: MeshInput[], overrides?: Partial<PlaneOptions>): ModelPlane[];
// Größte Flächen zuerst, höchstens maxPlanes. Wirft nicht; leeres Array, wenn nichts passt.

interface MeshInput {
  positions: ArrayLike<number>;        // xyz je Vertex
  index?: ArrayLike<number> | null;    // 3 je Dreieck; fehlt = Triangle-Soup
  matrix?: ArrayLike<number> | null;   // spaltenweise 4x4 (three.js Matrix4.elements) ins Modul-KS
}

interface ModelPlane {
  id: number;
  normal: Vec3; offset: number;        // normal·p = offset
  area: number;                        // Materialfläche in m² (Summe der Dreiecke)
  centroid: Vec3;
  u: Vec3; v: Vec3;                    // u entlang der längsten Kante, v = normal × u
  extent: [number, number];            // Ausdehnung entlang u und v, m
  triangles: Float32Array;             // 9 Zahlen je Dreieck, Modul-KS (zum Zeichnen)
  segments: PlaneSegment[];            // lange Außenkanten, längste zuerst
  corners: PlaneCorner[];
}
interface PlaneSegment { id: number; start: Vec3; end: Vec3; length: number; direction: Vec3; inward: Vec3 }
interface PlaneCorner  { id: number; point: Vec3; incoming: number; outgoing: number; angleDeg: number; virtual: boolean }
// Der Rand läuft so, dass die Fläche links liegt (von der Normalen aus gesehen).
// incoming = Segment, das in die Ecke läuft; outgoing = das hinausläuft.
// angleDeg < 0 = Innenecke; virtual = verrundete oder gefaste Ecke:
// der Punkt, an dem sich die geraden Kanten treffen würden.
```

`PlaneOptions` mit Standardwerten (`DEFAULT_PLANE_OPTIONS`):

| Option | Standard | Bedeutung |
| --- | --- | --- |
| `angleToleranceDeg` | 2 | Normalenabweichung, bis zu der ein Dreieck zur Ebene gehört |
| `distanceToleranceM` | 0.003 | Abstand zur Ebene, bis zu dem ein Dreieck zur Ebene gehört |
| `minAreaM2` | 0.0025 | kleinere Flächen werden verworfen (25 cm²) |
| `minWidthM` | 0.03 | schmalere Flächen werden verworfen, z. B. die Facetten runder Teile |
| `maxPlanes` | 12 | Anzahl der angebotenen Flächen |
| `simplifyToleranceM` | 0.001 | Kollinearitätstoleranz der Kantenstücke |
| `minSegmentM` | 0.02 | kürzere Kanten gelten als Rundung oder Fase |
| `maxCornerGapM` | 0.06 | maximaler Abstand der Kantenenden zur Ecke; erlaubt verrundete Ecken bis zu etwa diesem Radius |
| `minCornerAngleDeg` | 20 | Mindestknick für eine Ecke |
| `mergeGapM` | 0.010 | Spaltbreite, bis zu der Teilflächen zusammengefasst werden (Nuten, Fugen); im Panel einstellbar |

### TypeScript: Tag-Pose (`model/tagPlacement.ts`)

```ts
function tagPoseFromPlacement(p: TagPlacement): { position: Vec3; rotationDeg: Vec3 };
//   rotationDeg: rpy im Katalogformat. Wirft bei mode "edges", wenn die Kanten
//   weniger als 10° gegeneinander gedreht sind ("… (fast) parallel …").
function placementFromCorner(plane, corner, along: "incoming" | "outgoing", previous?): TagPlacement;
//   previous: Abstände, Drehung, flipped und offsetM werden übernommen.
function placementFromEdges(plane, first: EdgeLine, second: EdgeLine, previous?): TagPlacement;
//   EdgeLine = { start: Vec3; end: Vec3 }: eine lange Flächenkante oder eine beliebige Mesh-Kante.
function edgeInPlane(plane, line: EdgeLine): TagPlacementEdge;
//   Projiziert die Kante auf die Fläche. Eine eigene Kante der Fläche behält ihr `inward`,
//   andere zeigen zum Flächenschwerpunkt. Wirft, wenn die Kante weniger als 10°
//   von der Flächennormalen abweicht ("… (fast) senkrecht auf der Fläche …").
function placementFootPoint(p): Vec3;                     // Tag-Mitte in der Fläche (ohne offsetM)
function placementPosition(p): Vec3;                      // Fußpunkt + offsetM · Tag-+Z
function shiftPlacement(p, offset: Vec3): TagPlacement;   // neuer Modul-Ursprung
function rotatePlacement(p, turn: Quat): TagPlacement;    // Modul-KS gedreht; friert "oben" auf waagerechten Flächen ein
function placementMatches(tag): boolean;                  // passt position/rotationDeg noch zum Protokoll?
```

In `model/tagGeometry.ts` gibt es neu
`quaternionFromBasis(x, y, z): Quat`, die Rotation aus orthonormalen Spalten.

### Worker (`model/planeWorker.ts`)

- **Anfrage:** `PlaneRequest = { meshes: MeshInput[]; options?: Partial<PlaneOptions> }`
- **Antwort:** `PlaneResponse = { planes, triangleCount, ms } | { error }`
- Die `triangles`-Puffer werden transferiert.
- Das Frontend startet einen Worker pro Anfrage. Das Ergebnis wird je platziertem
  Modellobjekt und `mergeGapM` gecacht (`useModelPlanes` in
  `components/ModelPlanes.tsx`).

### Fehlerfälle

- **Keine passende Fläche:** Das Panel meldet es, die Position wird von Hand eingetragen.
- **Worker schlägt fehl:** Die Meldung erscheint im Panel, der Dialog bleibt bedienbar.
- **Zwei (fast) parallele Kanten** im Modus „zwei Kanten“: Fehlermeldung im Panel, der Tag bleibt unverändert.
- **Eine Kante steht (fast) senkrecht auf der Fläche:** Fehlermeldung, nur die erste Kante bleibt gewählt.
- **Kein Modell oder Ladefehler:** Die Schaltfläche „Auf Modell platzieren“ ist deaktiviert.
- **Skinned Meshes** werden übersprungen, weil ihre Vertices nicht der gezeichneten Lage entsprechen.

## Vorgehen (Algorithmus)

1. **Kandidaten:** Pro Dreieck werden Normale n, Offset d = n·p und Fläche
   berechnet und über (quantisierte Normale, quantisierter Offset) in Buckets
   gezählt. Nur Dreiecke aus Buckets mit nennenswerter Fläche kommen in die
   nächsten Schritte. Die meisten Dreiecke eines CAD-Modells sind Tessellierung
   runder Teile, deshalb bleibt der Aufwand beherrschbar.
2. **Ebenen-Cluster:** Buckets werden nach absteigender Fläche abgearbeitet. Der
   Bucket legt eine Ebene fest, benachbarte Dreiecke innerhalb der Winkel- und
   Abstandstoleranz schließen sich an. Danach folgt ein Refit.
3. **Körper:** Innerhalb einer Ebene bilden Dreiecke mit gemeinsamen
   (verschweißten, 0,1 mm) Vertices einen Körper.
4. **Zusammenfassen (Item-Profile):** Körper, deren Randkanten höchstens
   `mergeGapM` voneinander entfernt sind, werden **eine Fläche**.
   - Umgesetzt über ein Hash-Gitter der Randkanten und den Segment-Abstand.
   - Tischbein-Oberseiten mit größerem Abstand bleiben getrennt.
5. **Außenkontur:** Eine Randkante wird genau dort **nicht** als Umriss gezählt,
   wo ihr innerhalb von `mergeGapM` eine andere Randkante **gegenübersteht**.
   - „Gegenüber“ heißt: Die Außennormalen zeigen ≥ 45° gegeneinander, und die
     Gegenkante liegt, senkrecht projiziert, vor der Kante. Das trifft Nutlippen,
     Fugen und T-Stöße (deckungsgleiche Kanten).
   - Der verdeckte Bereich wird **exakt als Intervall** berechnet. Er endet genau
     am Vertex der Gegenkante, z. B. wo ein Loch beginnt.
6. **Kanten:** Die Umrissstücke werden auf gemeinsame Geraden verteilt. Die
   Gerade wird fortlaufend an alle Stücke angepasst, gewichtet mit ihrer Länge
   (Hauptachse der Endpunkte). Stücke mit Lücken ≤ `mergeGapM` werden zu einer
   Kante verbunden, damit die von Nuten unterbrochene Stirnkante eines
   Profilrahmens eine Kante wird. Danach gilt der Filter `minSegmentM`.
7. **Ecken:** Für Paare langer Kanten mit Knick ≥ `minCornerAngleDeg` wird der
   Geradenschnitt berechnet. Er ist eine Ecke, wenn er höchstens
   `maxCornerGapM` vom Ende der einen und vom Anfang der anderen Kante liegt.
   Verrundete Ecken werden dadurch zum gedachten Schnittpunkt (`virtual`), also
   zu dem Punkt, an dem man den Zollstock ansetzt.
8. **Filter:** Übrig bleiben Flächen mit Fläche ≥ `minAreaM2` und schmaler
   Ausdehnung ≥ `minWidthM`, sortiert nach Fläche, die ersten `maxPlanes`.

**Bedienung** (Modulkatalog → Modul bearbeiten → am Tag „Auf Modell platzieren“):

1. Die farbigen Flächen im Bild anklicken. Verdeckte Flächen werden sichtbar,
   wenn man die Ansicht dreht.
2. „Messen ab einer Ecke“ oder „ab zwei Kanten“ wählen.
   - Bei einer Ecke mit A/B wählen, entlang welcher Kante gemessen wird.
   - Kleine Punkte markieren verrundete Ecken.
   - Bei zwei Kanten zählt **jede scharfe Kante des Modells**, auch außerhalb der
     Fläche. Die Mesh-Kanten des Teils unter dem Cursor werden orange angezeigt,
     die langen Kanten anderer Flächen grau.
   - Liegt die geklickte Mesh-Kante auf einer langen Flächenkante (≤ 1 mm), wird
     die ganze lange Kante genommen.
   - Jede lange Flächenkante hat einen unsichtbaren Fangbereich, eine Röhre
     mit etwa dem halben Markerradius (mindestens 3 mm). Der Rand der gewählten
     Fläche lässt sich damit auch dort treffen, wo er keine einzelne Mesh-Kante
     ist, und auch knapp neben der Silhouette. Die Röhren der gewählten Fläche
     sind 25 % dicker und gewinnen, wenn zwei Kanten zusammenfallen. Verdeckte
     Röhren nimmt das Modell davor ab.
   - Eine Mesh-Kante außerhalb davon gilt nur, wenn der Cursor höchstens etwa
     0,6 × Markerradius von ihr entfernt ist. Mitten auf einer Fläche wird
     nichts hervorgehoben.
3. Die Abstände bis zur **Tag-Mitte** in cm eintragen. Optional eine eigene
   Drehung, „Tag schaut in die Gegenrichtung“ oder den **Abstand zur Fläche** in
   mm (z-Offset) setzen.

**Ansicht:**
- Linke Maustaste dreht die Ansicht.
- Mittlere und rechte Maustaste verschieben das Modell.
- Das Mausrad zoomt.
- Gewählte Bezugskanten werden kräftig rot (`#ff1a1a`, 5 px) gezeichnet, die
  Kante unter dem Cursor hellrot (3 px).
- Alle Kantenlinien (Flächenkanten, Mesh-Kanten, Auswahl) sind Screen-Space-Linien
  **mit Tiefentest**, per Polygon-Offset leicht zur Kamera gezogen
  (`SurfaceLines` in `components/ModelEdges.tsx`). Nur sichtbare Kanten werden
  markiert; was das Modell verdeckt, bleibt verdeckt.
- Im Kantenmodus gibt es keine Punkte. Ecken-Punkte erscheinen nur im
  Ecken-Modus.

Im Panel lässt sich „Spalten überbrücken bis (mm)“ einstellen. Eine Änderung
löst nach 400 ms eine neue Flächensuche aus. Die Maßlinien (gelb) zeigen die
Messung im Bild. „Abbrechen“ stellt den Tag wieder her.

## Messwerte

Gemessen mit `wagon.stl` aus `fr3_description_with_wagon`: 357 632 Dreiecke,
in Metern, Node/vitest, ein Thread.

| Stand | `mergeGapM` 0 | 10 mm | 20 mm |
| --- | --- | --- | --- |
| ohne Zusammenfassen (erste Version) | 0,9 s | — | — |
| mit Zusammenfassen (aktuell) | 1,8 s | 2,4 s | 3,5 s |

Im Browser läuft die Suche im Web Worker, der Dialog bleibt bedienbar. Das
visuelle `link0.dae` des Wagens ist 250 MB groß; dort dauert schon das Laden
des Modells.

## Offene Fragen

- **Stand 29.09.2026:** Laut Nutzer funktioniert es so weit. Weiterentwicklung
  ist möglich, etwa bei der Bedienung der Kantenauswahl und bei Flächen als Bezug.
  Ein erster Versuch mit Flächen als Bezug wurde auf Wunsch des Nutzers
  zurückgenommen.
- **Noch nicht systematisch geprüft:** der Abgleich der Abstände mit dem
  Zollstock am echten Modul und die Genauigkeit bei echten Item-Profil-Modellen.
- **Tisch-URDFs:** Das Submodul `misc/urdf.git` enthält nur Roboter; eigene
  Tisch-URDFs gibt es dort noch nicht.
- **Deckungsgleiche koplanare Körper** (eine Fläche doppelt im Modell, z. B. ein
  Aufkleber-Körper) werden nicht zusammengelegt, wenn ihre Kanten weit
  auseinanderliegen.
- **Schräge V-Spalte**, deren Kanten weniger als 45° gegeneinander zeigen,
  werden nicht als Spalt erkannt. Für Nuten und Fugen spielt das keine Rolle.
- **Bereits vorher rot:** `src/entities/robot/model/store.test.ts` („keeps
  offline robots selected …“) schlägt schon auf dem Stand vor dieser Arbeit fehl,
  hat also nichts mit ihr zu tun.
- **Fach-MD:** In `apriltag-referenz.md` fehlt noch ein eigener Abschnitt
  „Tag-Position am Modellmodell“ (Datenformat `placement`).

## Abweichungen vom Plan

- **Zusammenfassen geometrisch statt per 2D-Raster.** Geplant waren
  morphologisches Schließen auf einem ~1-mm-Raster und Zusammenhangskomponenten.
  Das Raster war auf dem Wagen zu langsam: 57 Mio. Zellen, 4,5 s. Außerdem
  machte die Zellrundung die Spaltbreite ungenau, ein 20-mm-Spalt wurde bei
  2,5-mm-Zellen mit überbrückt. Das Hash-Gitter der Randkanten mit exakter
  Intervallrechnung (Schritte 4 und 5) hält die Spaltbreite exakt ein und
  skaliert mit der Zahl der Randkanten statt mit der Fläche.
- **Kein Verketten mit Douglas-Peucker mehr:** Die Kanten entstehen durch
  kollineares Zusammenfassen der Umrissstücke, die Ecken paarweise über
  Geradenschnitte.
- **`placement` enthält kein Feld `offset`:** Kantenpunkt und Normale legen die
  Ebene fest. Abstände werden in Metern gespeichert (`distancesM`), im UI in cm
  angezeigt.
- **Nachtrag auf Wunsch des Nutzers:** Kanten außerhalb der Fläche und z-Offset
  (`offsetM`) sind dazugekommen. Das Feld `edgeIds` in der UI-Auswahl wurde
  deshalb durch `edges: EdgeLine[]` ersetzt. `PlaneTool.onEdgePick` nimmt jetzt
  eine `EdgeLine` statt einer Segment-Id.
- **Keine Flächenliste im Panel:** Die Fläche wird nur im Bild gewählt, so wie
  der Nutzer es festgelegt hat.

## Nach Abschluss

- [x] Status auf `fertig (vorläufig)` gesetzt, Code in `webskillcomposition` committet (`839aa58`).
- [x] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- [ ] Das Datenformat `placement` in `apriltag-referenz.md` übernehmen, als eigenen Abschnitt.
- Weiterentwicklung: im bestehenden Plan einen neuen Abschnitt ergänzen oder einen Folgeplan anlegen und hier verlinken.
