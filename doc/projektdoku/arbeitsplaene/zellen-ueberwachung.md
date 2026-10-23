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
| PauseCellWatch | `ns=<vision>;s=VisionMachine.PauseCellWatch` | `(ReasonText: String, MaxPauseS: Double) -> (Error: Int32)` (Nachtrag 22.10.2026, siehe `vision-server-interface.md` Abschnitt 16) |
| ResumeCellWatch | `ns=<vision>;s=VisionMachine.ResumeCellWatch` | `() -> (Error: Int32)` (Nachtrag 22.10.2026) |
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
  "minWorldTags": 1, "storedPoseMarginPx": 2,
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
  "pending": [ { "tagId": 3, "moduleId": "MOD-A", "kind": "moved|disappeared|appeared",
                 "since": "ISO", "distanceM": 0.06 | null, "rotationDeg": 4.0 | null, "confirmInS": 3.2 } ],
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

- **Gültiges Bild:** Kamerapose aus dem gemeinsamen PnP (`joint`, mindestens zwei Welt-Tags) mit RMS ≤ `maxCameraErrorPx`, **oder** (`minWorldTags` 1, Standard) genau ein Welt-Tag mit der gemerkten Kamerapose, das zu ihr passt (siehe „Ein Welt-Tag genügt“). Alles andere ist „keine Aussage“: Es zählt weder als gesehen noch als fehlend. Der Default ist 6 px: Auf Pi 1 lag das gemeinsame PnP bei einer Kalibrierung mit 0,43 px stabil bei 3,3 px, der Rest ist rund 1 cm Ungenauigkeit der Tag-Map. Bei einer Pause wegen zu großem Fehler nennt `message` den Welttag mit dem größten `jointReprojectionErrorPx`, also den Kandidaten zum Nachmessen.
- **moved:** Der Median der Position (bzw. Drehung um die Hochachse) der letzten `persistS` Sekunden weicht länger als `persistS` um mehr als den Schwellwert ab. Bei mehrdeutigen Tags zählt nur die Position, außer das Modul liegt flach (Hochachse ≤ 30° zur Welt-Z): Dann zählt auch die Drehung, denn die beiden Lösungen unterscheiden sich nur im Kippen. Nachgeschärft am 19.10.2026: Ein gedrehtes Modul wurde nie gemeldet, weil in der Draufsicht fast jeder flache Tag mehrdeutig ist.
- **appeared:** Ein Tag außerhalb der Referenz ist seit mindestens `persistS` sichtbar, und zwar in der Mehrzahl der Bilder des Fensters.
- **disappeared:** Ein Referenz-Tag fehlt länger als `missingPersistS`, und zwar in so vielen gültigen Bildern hintereinander, dass ein zufälliges Verpassen bei seiner bisherigen Erkennungsrate unter 0,001 % liegt (mindestens 3, höchstens 60; Rate vorsichtig als untere 95-%-Grenze). Nachgeschärft am 15.10.2026: Ein Tag an der Erkennungsgrenze (nur in jedem fünften Bild erkannt) löste nach 20 s Lücke einen Fehlalarm aus, obwohl der Scan ihn fand.
- **Hysterese:**
  - moved erlischt erst, wenn der Tag `persistS` lang unter dem halben Schwellwert liegt.
  - disappeared erlischt erst, wenn der Tag wieder in der Mehrzahl der Bilder ist.
  - appeared erlischt erst, wenn der Tag `persistS` lang nicht mehr zu sehen war.
- **Welttags:** Sie werden nicht überwacht. `locate_modules` liefert sie gar nicht erst.

### Ein Welt-Tag genügt (19.10.2026)

Zwei Welt-Tags gleichzeitig zu sehen war oft nicht gegeben, denn einer ist häufig verdeckt. Die Kamera ist fest montiert. Deshalb gilt: Aus Bildern mit mindestens zwei Welt-Tags schätzt der Pi die Pose (gemeinsames PnP) und merkt sie sich. Bei genau einem sichtbaren Welt-Tag nutzt er die gemerkte Pose und prüft den Tag nur auf Konsistenz.

- **Warum nicht neu aus einem Tag:** Aus 2,5 m ist die Pose aus nur einem Tag um Zentimeter ungenau (simuliert: Modulfehler Median 29–40 mm, 95 % unter ~90–100 mm, Ausreißer bis 1,5 m durch gekippte Lösungen, 24–38 % der Bilder über 5 cm). Mit gemerkter Pose: Median 12 mm, 95 % unter 33 mm, höchstens 46 mm, 0 % über 5 cm. Der Rest ist der Messfehler des Modul-Tags selbst. In der Simulation hatte der Modul-Tag 19,8 cm, bei 8 cm großen Tags ist der Fehler größer. Das ist aber unabhängig von der Zahl der Welt-Tags.
- **Konsistenzprüfung:** Die Ecken des einen Welt-Tags werden unter der gemerkten Pose projiziert und mit den gesehenen verglichen. Verglichen wird mit dem Wert, den derselbe Tag beim Speichern der Pose hatte (er enthält den Kartenfehler von ~1 cm). Mehr als `storedPoseMarginPx` (2 px) darüber heißt: Kamera bewegt? Gemessen: unbewegt höchstens ~1 px, 5 mm Verschiebung ~1,4 px, 10 mm ~3,4 px, 0,1° ~1,4 px.
- **Pausen** (`message` nennt immer „Welt-Tag“):
  - kein Welt-Tag im Bild,
  - Start oder nach Verwerfen mit nur einem Welt-Tag („Kamerapose noch nicht bekannt“),
  - der Welt-Tag passt nicht zur gemerkten Pose („Kamera bewegt?“).
  Nach 5 unpassenden Bildern in Folge wird die Pose verworfen, und es braucht wieder zwei Welt-Tags gleichzeitig.
- **Überlebt Neustarts (22.10.2026):** Die Pose liegt in `data/calibration/cellwatch_camera_pose.json` (Schema `wsc.vision.cellwatch.camerapose/1`: Pose, Grundlinie je Welt-Tag, Tag-Map-Schlüssel, Anzahl). Gespeichert ab 5 Posen, danach höchstens alle 10 min, atomar (temporäre Datei, dann ersetzen). Beim Start wird sie geladen. Zusätzlich liefert der Referenz-Scan sie: `cameraLocalization.poseInWorld`/`tagErrorsPx` bei `solver: "joint"`, `StartCellWatch` übernimmt sie, wenn keine gemerkt ist oder die Kamera sich bewegt hat (> 15 mm / 0,5°). Auch eine wiederhergestellte Pose wird auf Konsistenz geprüft: Wurde die Kamera während der Pause bewegt, fällt sie samt Datei weg. Status: `cameraPose: {source: measured|file|scan|null, poseCount, savedAt}`. Schlägt das Schreiben fehl, kommt eine Warnung je Intervall, kein Absturz.
- **Grenzen:** Ändert sich die Tag-Map, ist die Pose verworfen. Weicht ein neues gemeinsames Ergebnis um mehr als 15 mm oder 0,5° ab, fängt der Verlauf neu an, statt zu mitteln. `minWorldTags: 2` schaltet auf das alte Verhalten zurück.

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
