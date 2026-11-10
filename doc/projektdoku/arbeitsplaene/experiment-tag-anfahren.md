# Experiment: Werkzeugspitze über einen AprilTag fahren (Ende-zu-Ende-Prüfung)

**Status:** fertig — 10.11.2026 (an Hardware nicht getestet)
**Verantwortlich:** Meric, `Agent: Experiment Tag anfahren`
**Thema:** apriltag
**Branch:** `roboter-wiederholgenauigkeit` (dieses Repo: Plan; `webskillcomposition`: Umsetzung im Wiederholgenauigkeits-Panel)

## Ziel

Die ganze Messkette wird einmal unabhängig von Tischmaßen und CAD geprüft: Kamera → Hand-Auge → Roboter → Werkzeug. Der Hand-Pi misst einen AprilTag mit einem Bild, die App rechnet die Tag-Mitte in die Roboterbasis, und der Roboter fährt die Werkzeugspitze senkrecht über diese Stelle. Der Bediener misst den verbleibenden Versatz zwischen Spitze und Tag-Mitte von Hand. Vorher gab es nur die Abgleiche gegen Handmaße (etwa 3 mm); danach gibt es einen direkten Versatzwert der Kette.

Hintergrund: Die Kamera ist laut Distanzstück-Test sehr genau (siehe `doc/auswertung/distanztest_20261110.md`: Seitentest in x/y 0,043 mm und 0,064 mm). Der Abgleich gegen Handmaße ist dagegen nur etwa 3 mm genau, weil die Maße der Zelle von Hand kommen. Dieses Experiment prüft deshalb nicht die Kamera allein, sondern die Summe aus Hand-Auge, Gelenkkinematik und Werkzeugmodell.

## Bereits gelesen

- `CLAUDE.md`, `arbeitsplaene/README.md`, `arbeitsplaene/_vorlage.md`
- `arbeitsplaene/roboter-wiederholgenauigkeit.md` (Stand 09.11.2026: Ausgangsstellung, Kamerahöhe ~0,35 m, Nachträge zu AprilTag als Messziel und erster Messung am Roboter)
- `arbeitsplaene/layer2-measuretags.md` (Stand `origin/apriltag`, nur gelesen): Schnittstelle `MeasureTags`, Ergebnisformat, Fehlerwerte
- `arbeitsplaene/handeye-genauigkeit.md`, `arbeitsplaene/handeye-ueber-opcua.md` (Hand-Auge-Kalibrierung, `T_flansch_cam`)
- `arbeitsplaene/layer2-annaeherung.md` (Anfahren mit Vorposition und Stop-and-go)
- `doc/auswertung/distanztest_20261110.md` (Genauigkeit der Kamera, Kamerahöhe in der Ausgangsstellung)

## Betroffene Dateien

Dieses Repo (nur der Plan, keine Code- oder Testdateien in dieser Aufgabe):

- `doc/projektdoku/arbeitsplaene/experiment-tag-anfahren.md` (neu)
- `doc/projektdoku/arbeitsplaene/README.md` (eine Zeile im Index)

`webskillcomposition` (Umsetzung, später):

- `frontend/src/features/cell-modules/model/tagTouch.ts` (neu): reine Funktion `tagTipFlangeTarget` und Konstanten
- Hook für Messen und Anfahren (neu, Name und Pfad bei Umsetzung festlegen)
- `frontend/src/features/.../components/RepeatabilityPanel.tsx`: Abschnitt „Experiment: Tag anfahren“

Der Abschnitt in `RepeatabilityPanel.tsx` reserviert die Datei. Wer parallel am Panel arbeitet, spricht sich vorher ab.

## Schnittstellen

### OPC UA

Keine neuen Knoten. Benutzt wird `MeasureTags` auf dem Hand-Pi (siehe `layer2-measuretags.md`). Die Methode antwortet erst nach der Messung, das Ergebnis kommt direkt zurück.

| Element | NodeId | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| MeasureTags | `ns=<vision>;s=VisionMachine.MeasureTags` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` | Aufruf | Ein Bild messen, Tag-Pose in der Kamera |

Anfrage für dieses Experiment:

```json
{ "schema": "wsc.vision.measuretag.request/1", "tagIds": [7] | null, "frames": 1 }
```

- `tagIds`: `[id]` für einen bestimmten Tag. `null` heißt alle erkannten Tags. Sieht der Pi mehr als einen Tag und ist `tagIds` `null`, bricht die Steuerung ab (Fehlerfall „mehrere Tags“).
- `frames: 1`: ein frisches Bild, keine Mittelung. Der Regelschritt der Steuerung (mindestens zwei Bilder) entfällt hier, das Experiment misst nur einmal.

Ausgewertet werden je Tag `poseInCamera` (= `T_cam_tag`, Kamera-KS wie OpenCV, Tag-KS +Z aus dem Tag heraus), `distanceM`, `reprojectionErrorPx`, `ambiguous`, `nearEdge`, `sizeM`, `sizeSource`, `intrinsicsSource`, `calibrationId`, `capturedAt`.

Fehlerwerte, die die Steuerung behandelt: `OK` (auch mit `tags: []`), `DETECTION_FAILED` (`error: "NO_FRAME"`), `INVALID_ARGUMENT`, `INVALID_STATE`, `BUSY`. Fehlt die Methode im Adressraum, läuft der Hand-Pi ohne `hand_eye_path` (laut `layer2-measuretags.md`: Elemente nur mit Hand-Auge).

### Frontend (TypeScript, webskillcomposition)

```ts
// Modul: frontend/src/features/cell-modules/model/tagTouch.ts
// Pose-Typ wie im Frontend (Position + Quaternion), Länge in Metern.
export function tagTipFlangeTarget(
  tagInBase: Pose,        // T_base_tag, Tag-Mitte in der Roboterbasis
  flangeToTool: Pose,     // Werkzeugspitze relativ zum Flansch (FR3: z = 0,1034 m, laut URDF tool_point, zu bestätigen)
  offsetM: number,        // Abstand Spitze–Tag-Ebene in m, −0,020 … +0,020
  yawDeg: number = 0,     // Drehung des Werkzeugs um seine z-Achse
): { target: Pose; approach: Pose }  // Rückgabeform vorgeschlagen, bei Umsetzung festlegen
```

Rechnung:

- Ziel der Werkzeugspitze: `p_tip = p_tag + offsetM · n`, mit `n` = Tag-Normale aus dem Tag heraus (aus `tagInBase`).
- Werkzeug-z zeigt auf den Tag, also antiparallel zu `n`. `yawDeg` dreht um diese Achse.
- Flanschpose: `T_base_flange = T_base_tag · … · inv(flangeToTool)`, so dass die Spitze auf `p_tip` liegt.
- `approach`: dieselbe Pose mit 0,06 m zusätzlich entlang `n` (Vorposition, „60 mm über dem Ziel“).

Konstanten: Abstand −20 / +20 mm, Standard 10 mm, Vorposition 0,06 m.

### Frontend: Hook und Datensatz

Der Hook verbindet Messen, Ausgangsstellung und Anfahren. Er liefert den Datensatz `wsc.tagtouch.record/1`, den das Panel als JSON anzeigt und kopierbar macht:

```json
{
  "schema": "wsc.tagtouch.record/1",
  "measuredAt": "2026-11-10T12:00:00.000Z",
  "tagId": 7,
  "tagSizeM": 0.08,
  "joints": [0, 0, 0, 0, 0, 0, 0],
  "flangeInBase": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
  "flangeToCamera": { "position": [x, y, z], "orientation": [qx, qy, qz, qw], "calibrationId": "…" },
  "cameraToTag": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
  "tagInBase": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
  "reprojectionErrorPx": 0.4,
  "ambiguous": false,
  "offsetMm": 10,
  "yawDeg": 0,
  "targetToolInBase": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
  "reachedToolInBase": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
  "reachedJoints": [0, 0, 0, 0, 0, 0, 0],
  "reachedErrorMm": 0.0,
  "manualOffsetMm": [0.0, 0.0]
}
```

- `joints`: Gelenkwinkel vor dem Bild. Gleich gelesen auch nach dem Bild (Toleranz offen, siehe Offene Fragen).
- `flangeToCamera` und `calibrationId`: aus der aktiven Hand-Auge-Kalibrierung. `T_base_tag = FK(q) · T_flansch_cam · T_cam_tag`.
- `reachedToolInBase` und `reachedErrorMm`: die Spitze laut Roboter nach dem Anfahren, mit `FK(reachedJoints)`, und ihr Abstand zu `targetToolInBase`.
- `manualOffsetMm`: vom Bediener eingetragener Versatz Spitze ↔ Tag-Mitte als `[x, y]` in mm, oder `null`, wenn nicht gemessen.

### Netz / Betrieb

Keine neuen Ports, Services oder systemd-Units. Benötigt werden der Hand-Pi mit `hand_eye_path` (Hand-Auge-Kalibrierung), das Vision-System im Zustand `Operational` und die Steuerung mit Take Control und Sync auf den Roboter.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Kein Tag im Bild (`tags: []`) | Abbruch mit Hinweis „Tag im Bild?“ |
| Mehrere Tags, `tagIds: null` | Abbruch mit Hinweis, eine Tag-ID angeben |
| Tag-ID nicht gefunden | Abbruch, Liste der gesehenen IDs anzeigen |
| `error: "NO_FRAME"` / `DETECTION_FAILED` | Abbruch, keine Fahrt |
| `intrinsicsSource: "placeholder"` | Abbruch, keine Messung mit Platzhalter-Intrinsik |
| Roboter bewegt sich beim Messen (Gelenke vor/nach dem Bild unterschiedlich) | Messung verwerfen, Fehler anzeigen, neu messen |
| Kein Hand-Auge (`flangeToCamera` fehlt) | Abbruch vor dem Messen |
| `MeasureTags` fehlt (Methode nicht im Adressraum) | Abbruch mit Hinweis „Hand-Pi ohne MeasureTags“ |
| `ambiguous: true` | Warnung, Position bleibt verwendbar, Orientierung wird nicht genutzt |
| Tag am Bildrand (`nearEdge` nicht leer) | Warnung, in der Auswertung markieren |
| IK nicht lösbar, Gelenkgrenze, Kollisionsprüfung verletzt | Abbruch vor der Fahrt, keine Teilfahrt |
| Negativer Abstand (−20 … 0 mm) | Nur mit Warnung „Werkzeug drückt unter die Tag-Ebene“ bestätigen |
| Stopp | Jederzeit über den Stopp-Knopf, Fahrt sofort abbrechen; Datensatz mit dem Erreichten schreiben, `reachedErrorMm` als „abgebrochen“ markieren |

## Vorgehen

1. Roboter einschalten und in einer stehenden Pose halten, nicht in der Ruhestellung (dort sackt er etwa 10 mm unsichtbar ab). Tag im Bild, am besten mittig.
2. „Tag messen (1 Bild)“: Gelenke vor dem Bild lesen, `MeasureTags` mit `tagIds` (leer = einziger Tag) und `frames: 1`, Gelenke nach dem Bild lesen. Bewegt sich der Roboter, wird die Messung verworfen.
3. „Ausgangsstellung, dann Tag anfahren“: Take Control und Sync, Ausgangsstellung (`lookDownHomeAngles`), dann Vorposition 60 mm über dem Ziel, dann das Ziel. IK, Gelenkgrenzen und Kollisionsprüfung wie bei der Versatz-Prüfung im Hand-Auge-Panel.
4. Anzeige im Panel: gemessene Tag-Mitte, Ziel der Spitze und die laut Roboter erreichte Spitze (aus `reachedJoints`).
5. Der Bediener misst den Versatz Spitze ↔ Tag-Mitte und trägt `x` und `y` in mm ein. Der Datensatz wird als JSON angezeigt und kopiert.
6. Wiederholung an mehreren Tag-Lagen im Bild (Mitte, Rand), siehe Offene Fragen.

Ergebnis-Auswertung: `reachedErrorMm` zeigt, ob der Roboter das Ziel erreicht hat (Regelgenauigkeit). `manualOffsetMm` zeigt den Versatz der Kette. Beides getrennt betrachten.

## Offene Fragen

- Welches Werkzeug ist montiert? Die Spitze ist 0,1034 m (URDF `tool_point`), das ist im Panel zu bestätigen oder zu korrigieren, falls abweichend.
- Wie wird der Versatz gemessen? Lineal oder Messschieber, Fotos mit Maßstab, oder beides? Welche Genauigkeit ist für den Messwert realistisch?
- Bezugsachse für `manualOffsetMm`: Tag-Koordinatensystem oder Basis-Koordinatensystem? Vorschlag: Tag-KS, damit der Wert ohne Roboter lesbar ist.
- Wiederholung an mehreren Tag-Lagen im Bild: Mitte und Rand. Vor allem Rand mit `nearEdge`, dort ist die Messung laut Distanzstück-Test schwächer.

## Abweichungen vom Plan

Umgesetzt in webskillcomposition `5022124` (Branch `roboter-wiederholgenauigkeit`, lokal; Remote archiviert): `model/tagTouch.ts` (reine Funktionen), `model/useTagTouchRun.ts` (Hook), Abschnitt im `RepeatabilityPanel.tsx`, Tests `model/tagTouch.test.ts` (12).

- Werkzeugorientierung: Tag-Frame um 180° um seine x-Achse gedreht (Werkzeug-z = −n, Werkzeug-x = Tag-x), danach `yawDeg` um die Werkzeug-z. Tag-Frame wie im Pi: +z aus dem Tag heraus.
- Gierwinkel: ohne Vorgabe werden 0°, 90°, −90°, 180° versucht; gefahren wird der erste, der in Vorposition und Ziel erreichbar, in den Gelenkgrenzen und kollisionsfrei ist (`yawDeg` im Datensatz).
- Geschwindigkeit: ein Viertel der in den Settings eingestellten (`TAG_TOUCH_SPEED_FACTOR = 0,25`), weil die letzte Fahrt wenige Millimeter über bzw. unter der Tischebene endet.
- Kollisionsprüfung wie in `ApproachPlanPanel` (`collision(armPoints …)`, `keepOutFromRobotBounds`, `legCollision` über alle Wege): sie kennt Robotertisch/Wagen und Boden, **nicht** den Tisch mit dem Tag. Bei negativem Abstand schützt nur die Warnung im Panel.
- Stillstand beim Messen: Gelenke dreimal vor und einmal nach dem Bild gelesen (`readsSettled`, `jointsMoved` aus `jointSettling.ts`, dieselben Schwellen wie in der Wiederholgenauigkeit).
- Hand-Auge-Quelle wie in `ApproachPlanPanel`: `useHandEyeCalibration(PI_2_SERVER_URL)`, `poseFlangeCam` und `calibrationId` des aktiven Stands; Flanschrahmen = Frontend-Vorwärtskinematik, derselbe wie bei der Hand-Auge-Rohaufnahme.
- `tagSizeM` im Datensatz ist `null` (MeasureTags liefert die Tag-Größe nicht mit).
- `manualOffsetMm`: zwei freie Felder x/y; ist nur eines gefüllt, wird das andere 0.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/` übernommen (nicht nur hier im Plan stehen lassen).
