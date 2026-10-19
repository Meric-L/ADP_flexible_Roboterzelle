# Zellen-Überwachung mit der Deckenkamera

**Status:** fertig — 13.10.2026 (an echter Hardware nicht getestet)
**Verantwortlich:** `Agent: Zellen-Überwachung (Layer 1)`; Frontend: Sitzung `opcua-f1` (`webskillcomposition`, Branch `Ungetestet`)
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Die Deckenkamera (Pi 1) wertet laufend aus, ob ein Modul verschoben, hinzugefügt oder entfernt wurde. Sie meldet nur **bestätigte** Änderungen, damit nicht bei jedem Ausreißer ein Alarm kommt. Das Frontend zeigt dann einen Alarm mit „Layer 1 jetzt ausführen“ und mit „Ignorieren“.

## Bereits gelesen

- `doc/projektdoku/vision-server-interface.md` (Payload, `worldTags`, `cameraLocalization`, Sperren)
- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md`, `handeye-ueber-opcua.md` (Muster Ports und Fakes)

## Betroffene Dateien

- `src/vision_server/cellwatch.py` (neu): Tracker, Ablauf, JSON
- `src/vision_server/detection/apriltag.py`: `observe_cell`, eine Auswertung ohne Seiteneffekte
- `src/vision_server/result_management.py`: `result_for_job`, die letzten 20 Ergebnisse
- `src/vision_server/address_space.py`: Knoten `CellWatchStatus`
- `src/vision_server/runner.py`: Methoden, Verdrahtung, `aclose`
- `tests/test_cellwatch.py` (neu)
- `doc/projektdoku/vision-server-interface.md`: Abschnitt 16

## Schnittstellen

### OPC UA

Alles liegt unter `VisionMachine`, zusätzlich unter `VisionProgram` verlinkt. Es gibt die Knoten **nur auf der Deckenkamera**, also mit AprilTag und ohne `hand_eye_path`.

| Element | NodeId | Typ |
| --- | --- | --- |
| StartCellWatch | `ns=<vision>;s=VisionMachine.StartCellWatch` | `(OptionsJson: String) -> (Error: Int32)` |
| StopCellWatch | `ns=<vision>;s=VisionMachine.StopCellWatch` | `() -> (Error: Int32)` |
| CellWatchStatus | `ns=<vision>;s=VisionMachine.CellWatchStatus` | String (JSON), Lesen/Abo |

| Methode | Code | Wann |
| --- | --- | --- |
| Start | `OK` | scharf geschaltet. Ein erneuter Aufruf ersetzt die Referenz. |
| Start | `INVALID_ARGUMENT` | JSON oder Option ungültig, `referenceJobId` unbekannt (nicht unter den letzten 20 Ergebnissen) |
| Start | `INVALID_STATE` | Referenz-Job fehlgeschlagen oder nicht im Welt-KS; Vision-System nicht `Operational` |
| Stop | `OK` / `INVALID_STATE` | aus / lief nicht |

Nach einem Neustart steht die Überwachung auf `off`. Das Frontend schaltet sie dann neu scharf.

### Daten

**`OptionsJson`**, Schema `wsc.vision.cellwatch.start/1`. Alle Felder sind optional.

```json
{ "referenceJobId": "job-000123" | null, "movedThresholdM": 0.05, "rotatedThresholdDeg": 10,
  "persistS": 5, "missingPersistS": 20, "intervalS": 2, "maxCameraErrorPx": 6 }
```

- `referenceJobId`: die JobId des Ergebnisses, das im Viewport steht. Referenz ist **je Tag** die Pose der Detektion, zu der dieser Tag gehört. Bei zusammengefassten Detektionen (`attributes.tagIds`) bekommt jeder Tag dieselbe Pose.
- `null` („Ignorieren“): Referenz sind die Mediane der letzten `persistS` Sekunden, nur Tags aus der Mehrzahl der Bilder. Reicht die Beobachtung noch nicht, bleibt `referenceId` `null`, bis sie reicht.

**`CellWatchStatus`**, Schema `wsc.vision.cellwatch.status/1`:

```json
{ "schema": "wsc.vision.cellwatch.status/1",
  "state": "off|watching|changed|paused|failed", "message": "MOD-A verschoben",
  "referenceId": "cw-000002" | null, "referenceJobId": "job-000123" | null,
  "referenceSource": "job" | "observation" | null,
  "lastCheckAt": "2026-10-13T12:00:00+00:00" | null, "watchedTagIds": [7, 20, 21],
  "visibleTagIds": [7, 583, 584] | null,
  "tagVisibility": [ { "tagId": 2, "seenRatio": 0.2, "framesChecked": 10,
                       "lastSeenAt": "ISO" | null, "unreliable": true } ],
  "options": { "movedThresholdM": 0.05, ... },
  "changes": [ { "tagId": 7, "moduleId": "MOD-A", "kind": "moved|appeared|disappeared",
                 "referencePosition": [x,y,z] | null, "currentPosition": [x,y,z] | null,
                 "distanceM": 0.12 | null, "rotationDeg": 4.0 | null, "since": "ISO" } ] }
```

- `referenceId` ändert sich mit jeder neuen Referenz.
- `changes` enthält nur bestätigte Änderungen. Die Positionen sind Modulposen (mit CAD-Versatz) im Welt-KS.
- `paused` gilt während eines Jobs oder einer Kamera-Kalibrierung, ohne Kamerabild oder wenn das Bild keine Aussage erlaubt. Der Grund steht in `message`, danach geht es von selbst weiter.

### Regeln (`CellWatchTracker`)

- **Gültiges Bild:** Kamerapose aus dem gemeinsamen PnP (`joint`, mindestens zwei Welttags) mit RMS ≤ `maxCameraErrorPx`. Alles andere ist „keine Aussage“: Es zählt weder als gesehen noch als fehlend. Der Default ist 6 px: Auf Pi 1 lag das gemeinsame PnP bei einer Kalibrierung mit 0,43 px stabil bei 3,3 px, der Rest ist rund 1 cm Ungenauigkeit der Tag-Map. Bei einer Pause wegen zu großem Fehler nennt `message` den Welttag mit dem größten `jointReprojectionErrorPx`, also den Kandidaten zum Nachmessen.
- **moved:** Der Median der Position (bzw. Drehung um die Hochachse) der letzten `persistS` Sekunden weicht länger als `persistS` um mehr als den Schwellwert ab. Bei mehrdeutigen Tags zählt nur die Position.
- **appeared:** Ein Tag außerhalb der Referenz ist seit mindestens `persistS` sichtbar, und zwar in der Mehrzahl der Bilder des Fensters.
- **disappeared:** Ein Referenz-Tag fehlt länger als `missingPersistS`, und zwar in so vielen gültigen Bildern hintereinander, dass ein zufälliges Verpassen bei seiner bisherigen Erkennungsrate unter 0,001 % liegt (mindestens 3, höchstens 60; Rate vorsichtig als untere 95-%-Grenze). Nachgeschärft am 15.10.2026: Ein Tag an der Erkennungsgrenze (nur in jedem fünften Bild erkannt) löste nach 20 s Lücke einen Fehlalarm aus, obwohl der Scan ihn fand.
- **Hysterese:**
  - moved erlischt erst, wenn der Tag `persistS` lang unter dem halben Schwellwert liegt.
  - disappeared erlischt erst, wenn der Tag wieder in der Mehrzahl der Bilder ist.
  - appeared erlischt erst, wenn der Tag `persistS` lang nicht mehr zu sehen war.
- **Welttags:** Sie werden nicht überwacht. `locate_modules` liefert sie gar nicht erst.

### Ressourcen

- Die Überwachung liest `SharedCamera.latest_frame`, also dieselben Bilder wie Livestream und Jobs, und nimmt kein eigenes Bild auf. Ein Bild, das schon ausgewertet ist, wird nicht noch einmal ausgewertet.
- Sie rechnet mit einem **eigenen Detektor** in einem eigenen Thread (`asyncio.to_thread`). `observe_cell` verändert weder Anker noch `world_tags` noch `_localization`.
- Während eines Jobs pausiert sie, damit ein Layer-1-Job jederzeit volle CPU hat.
- `intervalS` steht auf 2 statt 1, weil die Erkennung auf 2028×1520 auf Pi 1 teuer ist.

## Offene Fragen

- Wie viel CPU die Überwachung auf Pi 1 kostet (alle 2 s eine Vollbild-Erkennung), ist nicht gemessen. Falls sie zu teuer ist: `intervalS` erhöhen oder die Erkennung auf der verkleinerten Stream-Auflösung laufen lassen.
- An echter Hardware ist das nicht getestet. Die Schwellwerte sind Vorschläge.

## Nach Abschluss

- [x] Status `fertig`, Schnittstellen eingetragen.
- [x] Indexzeile in `README.md`.
- [x] `vision-server-interface.md` Abschnitt 16.
- [x] Tests: `tests/test_cellwatch.py` (17).
