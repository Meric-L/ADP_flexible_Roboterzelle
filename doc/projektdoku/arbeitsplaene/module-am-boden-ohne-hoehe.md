# Module am Boden: die gemessene Tag-Höhe aus der Lage heraushalten

**Status:** in Arbeit — 02.11.2026
**Verantwortlich:** Meric, `Agent: Module am Boden ohne Höhe`
**Thema:** apriltag
**Branch:** dieses Repo: `apriltag-aufraeumen` (Punkt 2 und 4, Pi-Teil); webskillcomposition: `ungetestet-aufraeumen` (Punkt 1, 3, 4 Frontend-Teil und 5)

## Ziel

Layer 1 und Layer 2 sollen ohne z auskommen: Jedes Modul steht auf dem Boden, gemessen werden nur x, y und der
Kurs. Die Höhe, die die Posen-Schätzung für einen Tag liefert, streut im Betrieb deutlich (mehrere Zentimeter).
Sie ist zwar schon heute für die Modulhöhe nicht maßgeblich (`placeModule` setzt den Boden ein), wirkt aber an
vier Stellen trotzdem:

1. **Layer-2-Route ohne Kurs** verschiebt das Modul um den vollen 3D-Versatz, also auch in der Höhe.
2. **Zellen-Überwachung** misst die Verschiebung im Raum: eine Höhenstreuung von einigen Zentimetern
   reicht für ein falsches „verschoben“ (Schwelle 5 cm).
3. **Warnung „Tags widersprechen sich“** misst die Streuung zwischen den Tags eines Moduls im Raum.
4. **Der Tiefenfehler verschiebt auch x/y.** Die Höhenstreuung ist der Tiefenfehler der PnP-Schätzung entlang
   des Sehstrahls. Ein Tag, der nicht direkt unter der Kamera liegt, wird schräg gesehen; jeder Zentimeter
   Tiefenfehler wird dort zu einem Versatz in x/y (bei 45° etwa 1:1). z wegzuwerfen hilft dagegen nicht,
   der Fehler steckt schon in x/y.
5. **Basiskorrektur am Welttag übernimmt die gemessene Höhe** (`baseCorrection` in `tagServo.ts`). Die Basis
   wird aus `tagInWorld − R·tagInBase` gerechnet, auch in z. `tagInBase` misst die Handkamera über die
   Hand-Auge-Kalibrierung; deren Fehler entlang der Blickrichtung (beim Blick nach unten: z) und der
   Tiefenfehler des Tags landen 1:1 in der Höhe der Basis. Übernimmt man den Vorschlag, steht der Zwilling
   höher oder tiefer, und jede Ablage beim Pick & Place (Würfel, Use Case) kommt um denselben Betrag zu hoch
   oder zu tief: „der Roboter kommt auf die Modulebene oder deutlich drüber, wir spielen mit z“.

Hinterher: Keine Höhenstreuung schlägt mehr auf die Lage am Boden, auf die Überwachung oder auf die Warnungen
durch. Für 4 wird der Tag nicht mehr aus der geschätzten Tiefe platziert, sondern als Schnitt des Sehstrahls
(Kamera → Tag-Mitte) mit der bekannten Tag-Höhe.

## Bereits gelesen

- [`../vision-server-interface.md`](../vision-server-interface.md), `cameraLocalization` (Zeile mit `poseInWorld`) und
  Abschnitt 16 (Zellen-Überwachung)
- [`zellen-ueberwachung.md`](zellen-ueberwachung.md): Tracker, Referenz, Schwellen
- [`welttags-eine-quelle.md`](welttags-eine-quelle.md) (geplant): berührt die Welttag-Lagen, nicht die
  Modulplatzierung; keine Kollision. `floorHeightM` liest weiter die Welttags aus dem Katalog.
- [`../altlasten-zelle.md`](../altlasten-zelle.md): `cellwatch.py` steht dort als „heiß“ und für Refactorings
  gesperrt. Dieser Plan ändert dort nur Fachlogik, kein Umbau; vom Nutzer am 02.11. ausdrücklich beauftragt.

## Betroffene Dateien

Dieses Repo:

- `src/tagloc/geometry.py` (neuer Helfer `point_on_ray_at_height`)
- `src/vision_server/detection/apriltag/job.py` (`_localization_summary`: neues Feld)
- `src/vision_server/cellwatch.py` (`TagSighting`, `_sightings`, Abstand im Tracker)
- `tests/test_geometry*.py`, `tests/test_cellwatch*.py`, `tests/test_apriltag_*` (Opus)

webskillcomposition:

- `frontend/src/features/cell-modules/model/routeProgress.ts` (`measuredModule`)
- `frontend/src/features/cell-modules/model/cellModules.ts` (`placeModule`, `placeDetections`, neuer Helfer)
- `frontend/src/features/opcua-server/model/visionResult.ts` (Typ `VisionCameraLocalization`)
- Aufrufer von `placeLayer1Detections` / `placeLayer2Detections`, die die Kameraposition durchreichen
- zugehörige `*.test.ts` (Opus)

## Schnittstellen

### Daten / Payload

`cameraLocalization` im Ergebnis-Payload (`wsc.vision.result/…`, auch im Fehler-Payload) bekommt ein Feld,
**unabhängig vom Solver**, sobald eine Kamerapose existiert:

```json
{
  "cameraLocalization": {
    "solver": "joint | single | average | anchor",
    "cameraPositionInWorld": [1.234, 2.345, 2.810]
  }
}
```

- `cameraPositionInWorld`: Ursprung des Kamera-KS im Welt-KS, Meter, `[x, y, z]` (Translation von
  `T_world_cam`), gerundet wie die übrigen Payload-Werte.
- Fehlt, wenn es keine Kamerapose gibt (dann fehlt ohnehin `cameraLocalization`).
- Rein additiv. `poseInWorld` bleibt wie bisher nur bei `joint` (die Zellen-Überwachung übernimmt daraus die Pose;
  diese Regel ändert sich nicht).
- Ältere Pis senden das Feld nicht: Das Frontend fällt dann auf das heutige Verhalten zurück.

### Python

```python
# Modul: src/tagloc/geometry.py
def point_on_ray_at_height(
    origin: Sequence[float],      # Kameraposition, Welt-KS, m
    through: Sequence[float],     # gemessener Punkt (Tag-Mitte), Welt-KS, m
    height_m: float,              # Höhe der Ebene z = height_m
    *,
    min_slope: float = 0.2,       # |Richtung_z| / |Richtung| mindestens
) -> tuple[float, float, float] | None:
    """Schnitt des Strahls origin -> through mit der Ebene z = height_m.

    None, wenn der Strahl zu flach verläuft (|dz|/|d| < min_slope, z. B. ein Tag fast auf Kamerahöhe)
    oder die Ebene hinter der Kamera liegt (t <= 0). Wirft nicht.
    """
```

`src/vision_server/cellwatch.py`:

- `TagSighting` bekommt `camera_position: tuple[float, float, float] | None = None`.
- `_sightings(locations, camera_position=None)` setzt es; `sightings_from_locations` und der Pfad mit
  gespeicherter Kamerapose übergeben die Translation von `localization.pose_world_cam`.
- Tracker: Vor dem Median wird jede Sichtung mit `camera_position` auf die Ebene
  `z = reference.position[2]` projiziert (`point_on_ray_at_height`; bei `None` bleibt die gemessene Position).
  Der Abstand zur Referenz ist danach **waagrecht**: `hypot(dx, dy)`. Payload-Felder (`distanceM`,
  `referencePosition` …) behalten Namen und Einheit; `distanceM` ist ab jetzt der waagrechte Abstand.

### Frontend (TypeScript)

```ts
// cellModules.ts
/** Schnitt des Strahls camera -> through mit z = heightM; null wie in Python (flach, hinter der Kamera). */
export function pointOnRayAtHeight(camera: Vec3, through: Vec3, heightM: number, minSlope = 0.2): Vec3 | null;
```

- `VisionCameraLocalization` bekommt `cameraPositionInWorld: Vec3 | null`.
- `placeDetections` nimmt optional `cameraPositionInWorld` entgegen. Ist sie da, wird für jeden Modul-Tag die
  Tag-Position vor `levelModulePose` ersetzt durch `pointOnRayAtHeight(camera, detection.position, tagHeightM)`
  mit `tagHeightM = floorZ − bounds.min[2] + tag.position[2]` (Höhe des Tags am aufrecht stehenden Modul;
  `tag.position` ist im Modul-KS, +Z nach oben). Bei `null` bleibt die gemessene Position.
- `placeModule`: `tagSpreadM` wird waagrecht gemessen (nur x, y).
- `measuredModule` (Zweig ohne `heading`): der Weltversatz bekommt `shiftM[2] = 0`; die Höhe des Moduls bleibt.
  `describeModulePosition` zeigt dann Δz = 0.
- `baseCorrection` (`tagServo.ts`): `baseInWorld.position[2] = plannedBaseInWorld.position[2]`, also die Höhe
  aus Layer 1 (Boden minus Unterkante des Roboter-Moduls laut Katalog). `shiftM[2]` ist damit 0,
  `distanceM` waagrecht. Die gemessene Höhendifferenz wird als Diagnose behalten:
  `BaseCorrection.measuredHeightOffsetM: number` (gemessene minus geplante Basishöhe), und
  `describeBaseCorrection` nennt sie („Höhe nicht übernommen: gemessen +12 mm“).
  Folge: Die Ablagehöhe hängt nur noch an Katalog-Höhen (Tischplatte des Zielmoduls, Unterkante des
  Roboter-Moduls) und ist reproduzierbar. Stimmen diese nicht, ist der Fehler konstant und wird einmal im
  Katalog korrigiert statt bei jedem Lauf.

Annahme, im Code zu prüfen: `detection.position` ist die Tag-Mitte im Welt-KS. Das gilt, solange die Tag-Map
für Modul-Tags kein `tagToModule` setzt (heute nirgends gesetzt; die Versätze stehen im Frontend-Katalog).

### Fehlerfälle

- Kein Payload-Feld (alter Pi), Strahl zu flach oder Ebene hinter der Kamera: gemessene Position wie bisher.
- Welttags fehlen im Katalog: `floorHeightM` liefert 0 (wie bisher).
- Layer 2 (Handkamera, `solver: "anchor"`): Kameraposition ist die fortgeschriebene; der Schnitt gilt genauso.

## Vorgehen

Rollen nach Absprache: Sonnet-Agenten schreiben den Code, Opus schreibt und fährt die Tests und reviewt,
Haiku zieht die Fach-MDs nach.

1. **Frontend, Punkt 1 und 3** (Sonnet): `measuredModule` ohne z-Versatz, `tagSpreadM` waagrecht.
2. **Pi, Punkt 2** (Sonnet): waagrechter Abstand im Tracker der Zellen-Überwachung.
3. **Pi, Punkt 4a** (Sonnet): `point_on_ray_at_height`, `cameraPositionInWorld` im Payload, Projektion der
   Sichtungen in der Zellen-Überwachung.
4. **Frontend, Punkt 4b** (Sonnet): Typ, Durchreichen der Kameraposition, Strahl-Schnitt in `placeDetections`.
4a. **Frontend, Punkt 5** (Sonnet): Basishöhe in `baseCorrection` nicht aus der Messung.
5. **Tests** (Opus): Helfer in beiden Sprachen (senkrecht, schräg, flach, hinter der Kamera), Tracker (Höhen-
   streuung löst kein „verschoben“ aus, echte 6-cm-Verschiebung schon), Payload-Feld, Platzierung mit und ohne
   Kameraposition, `measuredModule` ohne Kurs. Beide Suiten komplett.
6. **Doku** (Haiku): `vision-server-interface.md` (Feld `cameraLocalization.cameraPositionInWorld`, `distanceM`
   waagrecht), Plan auf `fertig`.
7. **Abnahme an der Zelle** (Meric): Scan mehrfach wiederholen, Streuung von x/y vorher und nachher vergleichen;
   Zellen-Überwachung über einige Minuten ohne Bewegung laufen lassen: kein „verschoben“.

## Offene Fragen

- Reicht `min_slope = 0.2` (Strahl höchstens ~78° gegen die Senkrechte)? Die Deckenkamera sieht die Zelle aus
  ~2,8 m; ein Tag am Rand liegt bei ~45–60°. Bei der Abnahme die Zahl der Fälle mit `None` ansehen.
- Punkt 5: Stimmt die Unterkante des Roboter-Moduls (Wagenhöhe) im Katalog? Bei der Abnahme einmal mit dem
  Zollstock nachmessen; `measuredHeightOffsetM` über mehrere Anker zeigt, ob die Messung konstant daneben liegt
  (Katalog falsch) oder streut (Hand-Auge).
- Die Referenz der Zellen-Überwachung trägt selbst eine gemessene Höhe. Projiziert wird auf diese Höhe, also auf
  eine feste, aber nicht exakte Ebene; für den Vergleich mit der Referenz genügt das. Falls nötig, später die
  Katalog-Höhe vom Frontend mitgeben.

## Abweichungen vom Plan

—

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `README.md` aktualisiert.
- Fachwissen in `vision-server-interface.md` übernommen.
