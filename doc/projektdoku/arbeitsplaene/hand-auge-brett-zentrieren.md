# Hand-Auge: Brett zentrieren (MeasureBoard) und Aufnahme-Güte

**Status:** fertig — 22.10.2026 (an echter Hardware nicht getestet)
**Verantwortlich:** `Agent: Hand-Auge-Brett zentrieren auf dem Hand-Pi`; Frontend: Sitzung `opcua-1a`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Vor `CaptureHandEyeSample` soll die Steuerung das Kalibrier-Schachbrett (6x7 Innenecken, 23 mm) mittig ins Kamerabild bringen. Dafür braucht sie eine Messung, die auch bei einem nur teilweise sichtbaren Brett etwas liefert. Außerdem soll jede Aufnahme ihre Güte zeigen und nie ein Bild von vor dem Aufruf verwenden.

## Umfang

1. **Erstes Bild nach dem Aufruf:** `measure_tag`, `measure_board` und `MeasureBoard` nutzen nur Bilder, die nach dem Aufruf eintreffen. Das zuletzt gepufferte Bild zählt nur für den Zeitstempel.
2. **`reprojectionErrorPx` je Aufnahme:** `samples[i].reprojectionErrorPx` im `HandEyeStatus`, Mittel über die Bilder der Aufnahme, auch bei verworfenen Aufnahmen. `null`, wenn nichts gefunden wurde.
3. **`MeasureBoard(RequestJson) -> (ResultJson, Error)`:** misst das Brett ohne Fahrbefehle. Siehe `vision-server-interface.md`, Abschnitt 15.4.

Eine Prüfung auf eine zu kleine Rastergröße ist bewusst **nicht** eingebaut: Es gibt nur ein Kalibrierbrett (6x7), die Größe wird nicht je Aufruf geprüft.

## Betroffene Dateien

- `src/vision_server/measureboard.py` (neu): Anfrage, Ergebnis-JSON
- `src/tagloc/boards.py`: `partial_patterns`, `detect_partial_board`
- `src/vision_server/detection/apriltag.py`: `_latest_timestamp`, 3-Tupel-Rückgaben mit Info, `measure_board_view`, `default_board_spec`
- `src/vision_server/handeye_session.py`: `reprojectionErrorPx` je Aufnahme
- `src/vision_server/runner.py`: `_install_measure_board`
- `tests/test_measureboard.py` (neu), `tests/test_apriltag_source.py`

## Schnittstelle `MeasureBoard`

`ns=<vision>;s=VisionMachine.MeasureBoard`, `(RequestJson: String) -> (ResultJson: String, Error: Int32)`. Nur auf dem Hand-Pi. Antwortet erst nach der Messung. Läuft auch **während** einer Hand-Auge-Sitzung.

Anfrage `wsc.vision.measureboard.request/1`, alle Felder optional (`""`/`{}` gültig):

```json
{ "schema": "wsc.vision.measureboard.request/1",
  "board": {"type": "chessboard", "cols": 6, "rows": 7, "squareSizeM": 0.023},
  "frames": 3 }
```

`frames`: 1 bis 10, Standard 3. Ohne `board` gilt das Brett aus der Konfiguration.

| Error | Wann |
| --- | --- |
| `OK` | gemessen, auch wenn nichts gefunden wurde (`found: false`) |
| `DETECTION_FAILED` | kein frisches Kamerabild (`error: "NO_FRAME"`) |
| `INVALID_ARGUMENT` | JSON bzw. Brett ungültig |
| `INVALID_STATE` | Vision-System nicht `Operational` |
| `BUSY` | Job oder Kamera-Kalibrierung läuft |

Ergebnis `wsc.vision.measureboard.result/1` (immer gültiges JSON): siehe Abschnitt 15.4 der Schnittstellenbeschreibung.

## Tests

`tests/test_measureboard.py` (gerenderte 6x7-Bretter in mehreren Lagen, Teilraster am Bildrand, Sitzung, OPC UA), angepasste `MeasureTagTest`. Volle Suite: nur der bekannte `test_mjpeg_server`-Fan-out-Test schlägt fehl.
