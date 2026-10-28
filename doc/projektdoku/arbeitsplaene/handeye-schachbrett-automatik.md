# Hand-Auge-Kalibrierung mit Schachbrett, Posen fährt der Roboter selbst

**Status:** fertig — 13.10.2026 (an echter Hardware nicht getestet)
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

- `estimate_board_pose` nimmt beide IPPE-Lösungen und verfeinert sie mit Levenberg–Marquardt (`solvePnPRefineLM`). Am gerenderten Brett lag frontal sonst die erste Lösung 2,5° bzw. 2 mm daneben, jetzt unter 0,2 mm und 0,2°.
- `HandEyePorts.measure_board` ist optional (`None`), der Runner liest es mit `getattr`. Ein Server ohne die Funktion verwirft Board-Aufnahmen mit Grund, statt abzustürzen.
- Frontend (`webskillcomposition`, Branch `Ungetestet`): Fenster „Hand-Kamera kalibrieren“, Modus „Schachbrett, automatisch“ (`HandEyeAutoPanel.tsx`, `handEyeAutoPoses.ts`). Es gibt 11 Blickposen: senkrecht ±30° um die Blickachse gedreht sowie Ringe mit 20° und 30° Neigung.

## Nach Abschluss

- [x] Status `fertig`, Schnittstellen eingetragen.
- [x] Indexzeile in `README.md`.
- [x] `vision-server-interface.md` Abschnitt 15.1.
- [x] Testlauf: 567 Tests. Einzelne zeitabhängige Livestream-Tests (`test_camera_stream`, `test_mjpeg_server`) schlagen im Gesamtlauf sporadisch fehl, einzeln laufen sie durch, unabhängig von dieser Änderung. Neu: `test_boards_pose.py` (4), `HandEyeBoardTest` (4).

## Nachtrag 28.10.2026: Brettgröße

Das gelaserte Brett hat **7×8 Felder** zu 23 mm, also **6×7 Innenecken**. So steht es im Code: `src/vision_server/server.py` (Kommentar „Neues Brett seit 19.10.2026: 6x7 innere Ecken, 23 mm/Feld“) und `DEFAULT_BOARD` im Frontend (`cols: 6, rows: 7, squareSizeM: 0.023`).

Die Angaben „6×7 Felder / 5×6 Innenecken“ weiter oben in diesem Dokument (Abschnitt Ziel, Punkt 1, und die Erläuterung zu `cols`/`rows`) sind damit überholt. Sie bleiben stehen; maßgeblich ist dieser Nachtrag.

`tests/test_boards_pose.py` rendert bewusst ein 5×6-Innenecken-Brett (`SPEC`). Das ist ein synthetisches Testbild für die Posenberechnung und kein Widerspruch zur Hardware. Der Kommentar über `SPEC` nennt dort noch „6x7 Felder, also 5x6 Innenecken“ für das Labor-Brett. Das ist dort falsch; geändert wurde es nicht.

## Nachtrag 28.10.2026 (2): Target 7×10, Brett einstellbar

Der Nachtrag „Brettgröße“ oben (6×7 Innenecken) ist für das Hand-Auge-Target überholt. Maßgeblich ist jetzt das Target mit 8×11 Feldern zu 23 mm, also **7×10 Innenecken** (`cols` 7, `rows` 10, `squareSizeM` 0,023). Die Pi-Presets `cam_ceiling` und `cam_flange` stehen seit Commit `c615b36` darauf. Im Frontend ist das Brett im Hand-Auge-Fenster einstellbar; Standard ist 7×10 / 23 mm.

Befund, Frontend-Modul, Posen und Livestream stehen in `handeye-genauigkeit.md` im Abschnitt „Nachtrag 28.10.2026: Target 7×10, Brett einstellbar, Livestream“. Die Angaben in diesem Dokument oben bleiben unverändert stehen.
