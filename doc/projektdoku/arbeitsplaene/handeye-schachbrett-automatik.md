# Hand-Auge-Kalibrierung mit Schachbrett, Posen fährt der Roboter selbst

**Status:** in Arbeit — 13.10.2026
**Verantwortlich:** John Glanz, `Agent: Hand-Auge automatisch mit Schachbrett`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Bisher wird die Hand-Auge-Kalibrierung (`handeye-ueber-opcua.md`) gegen einen einzelnen AprilTag gemacht, und der Bediener fährt jede Pose von Hand. Mit diesem Plan
1. kann der Pi statt eines Tags ein **Kalibrier-Schachbrett** messen: das gelaserte Brett mit 6×7 Feldern zu 23 mm, also 5×6 Innenecken. Viele Ecken ergeben eine genauere und eindeutige Pose.
2. erzeugt das Frontend (`webskillcomposition`, Branch `Ungetestet`) die Posen selbst, zeigt sie vorher als Geist-Roboter und fährt sie dann ab. Je Pose meldet es die Aufnahme, am Ende löst es „Berechnen“ aus.

## Bereits gelesen

- `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md` (Sitzung, Methoden, Status, Fehlercodes; offene Frage „Automatisches Anfahren“)
- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md` (Solver, Dateiformat)
- `doc/projektdoku/arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md` (Entzerrung für beide Linsenmodelle)
- `src/tagloc/boards.py` (Board-Erkennung, Eckenreihenfolge von `findChessboardCornersSB`)

## Betroffene Dateien

- `src/tagloc/boards.py`: `chessboard_is_unambiguous`, `canonical_chessboard_corners`, `estimate_board_pose` (neu)
- `src/vision_server/detection/apriltag.py`: `measure_board` (neu)
- `src/vision_server/handeye_session.py`: Ziel ist Tag **oder** Board
- `src/vision_server/runner.py`: Port `measure_board` verdrahten
- `tests/test_boards_pose.py` (neu), `tests/test_handeye_session.py` (Board-Fälle)
- `doc/projektdoku/vision-server-interface.md` Abschnitt 15: Nachtrag Board

## Schnittstellen

### OPC UA

Keine neuen Knoten oder Methoden. `StartHandEyeCalibration(OptionsJson)` nimmt zusätzlich ein Board. Alle anderen Methoden, Codes und Sperren bleiben, wie sie in `handeye-ueber-opcua.md` stehen.

### Daten

**`OptionsJson`**, Schema `wsc.vision.handeye.start/1`. Genau **eins** von `tagId` und `board` muss angegeben sein:

```json
{ "schema": "wsc.vision.handeye.start/1",
  "board": { "type": "chessboard", "cols": 5, "rows": 6, "squareSizeM": 0.023 } }
```

- `cols`/`rows` sind die **Innenecken**: ein Brett mit 6×7 Feldern hat 5×6.
- `cols + rows` muss ungerade sein. Nur dann ist das Brett um 180° gedreht vom Original zu unterscheiden. Sonst antwortet der Pi `INVALID_ARGUMENT`.
- Mit `tagId` gilt die alte Form unverändert, einschließlich `tagInBase`.

**`HandEyeStatus`**: zusätzlich das Feld `target`.

```json
"target": { "type": "tag", "tagId": 12 }
"target": { "type": "chessboard", "cols": 5, "rows": 6, "squareSizeM": 0.023 }
```

- Bei einem Board ist `tagId` gleich `-1`.
- `samples[].camToTag` ist dann `T_cam_board`.

**Board-Frame:**
- Ursprung in der ersten Innenecke, so festgelegt, dass das Feld zwischen den ersten vier Ecken **schwarz** ist.
- +x entlang der Zeile, +y zur nächsten Zeile.
- +z von der Kamera weg, also ins Brett.

### Python

```python
# src/tagloc/boards.py
def chessboard_is_unambiguous(spec: BoardSpec) -> bool
def canonical_chessboard_corners(gray, corners, spec) -> np.ndarray     # (N, 2)
def estimate_board_pose(corners, spec, calibration) -> tuple[Pose, float]  # (T_cam_board, px); ValueError

# src/vision_server/detection/apriltag.py
async def AprilTagDetectionSource.measure_board(spec) -> tuple[Pose | None, str]

# src/vision_server/handeye_session.py
HandEyePorts.measure_board: Callable[[BoardSpec], Awaitable[tuple[Pose | None, str]]] | None
```

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Board nicht vollständig im Bild | Aufnahme `accepted: false`, `DETECTION_FAILED`, Grund im Status, Sitzung läuft weiter |
| Reprojektionsfehler > `max_reproj_error_px` (falsche Feldgröße oder falsches Raster) | wie oben, Grund nennt den Fehler in px |
| `cols + rows` gerade, `tagId` **und** `board`, oder keins von beiden | `Start` → `INVALID_ARGUMENT` |

### Frontend (nur zur Orientierung, Code in `webskillcomposition`)

- Der Bediener gibt die Board-Mitte im Roboter-Basis-KS, den Abstand Kamera–Board und den Kameraversatz entlang Flansch-z an.
- Das Frontend erzeugt etwa 12 Kameraposen: über der Mitte, geneigt und um die Blickachse gedreht, die Kamera blickt immer auf die Mitte. Daraus rechnet es Flanschposen und die IK. Unerreichbare Posen fallen weg.
- Die Posen werden vorher als Geist-Roboter gezeigt.
- Nach „Start“ fährt das Frontend je Pose mit `go_to`, wartet, bis der Roboter steht, und ruft `CaptureHandEyeSample` mit der **erreichten** Flanschpose. Am Ende ruft es `FinishHandEyeCalibration`.

## Offene Fragen

- An der echten Hardware nicht getestet. Die Posen gehen davon aus, dass die Kamera ungefähr entlang Flansch-z blickt. Sitzt sie anders, sieht sie das Board in manchen Posen nicht: Diese Aufnahmen werden verworfen, die Kalibrierung bleibt richtig, sie wird nur kürzer.

## Abweichungen vom Plan

—

## Nach Abschluss

- [ ] Status `fertig`, Schnittstellen eingetragen.
- [ ] Indexzeile in `README.md`.
- [ ] `vision-server-interface.md` Abschnitt 15 ergänzt.
- [ ] Testlauf.
