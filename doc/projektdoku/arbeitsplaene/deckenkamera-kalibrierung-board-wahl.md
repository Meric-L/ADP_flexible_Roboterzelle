# Deckenkamera-Kalibrierung: Board im Frontend wählen (auch ChArUco)

**Status:** fertig (ohne Hardwarefahrt) — 17.11.2026
**Verantwortlich:** Meric, `Agent: Layer-1-Kalibrierung mit wählbarem Board`
**Thema:** vision
**Branch:** `worktree-kalibrierung-layer1-board` (Zelle), `layer1-kalibrierung-board` (webskillcomposition)

## Ziel

Die Deckenkamera (Layer 1) lässt sich mit demselben Brett kalibrieren wie die
Handkamera in Layer 2, auch mit dem ChArUco-Brett. Welches Brett (Typ, Felder,
Feldgröße, Markergröße, Wörterbuch) benutzt wird, stellt das Frontend beim
Start ein, wie in der Hand-Auge-Kalibrierung. Bisher stand das Brett fest in
`AprilTagProfileConfig`.

Dazu im Frontend (nur webskillcomposition): Beliebig viele Roboterposen für
die Deckenkamera-Kalibrierung einlernen statt genau 16.

## Bereits gelesen

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/vision-server-interface.md`, Abschnitt 12 (Kalibrierung) und
  Abschnitt zum Stream-Overlay `calibration`
- `doc/projektdoku/arbeitsplaene/handauge-charuco-auswahl.md` (Board-JSON der
  Hand-Auge-Kalibrierung, wird hier wiederverwendet)
- `doc/projektdoku/part10-programm-schnittstelle.md` (Verweis auf `StartCalibration`)

## Betroffene Dateien

- `src/vision_server/calibration_session.py`
- `src/vision_server/runner/calibration.py`
- `src/vision_server/stream_overlay.py`
- `tests/test_calibration_session.py`, `tests/test_calibration_start_board.py`
- `doc/projektdoku/vision-server-interface.md` (Abschnitt 12.1, neuer Absatz)

## Schnittstellen

### OPC UA

`StartCalibration` bekommt **ein** Eingangsargument:

| Eingabe | Typ | Bedeutung |
| --- | --- | --- |
| `OptionsJson` | `String` | leer oder `{}`: Brett aus der Konfiguration (wie bisher). Sonst `wsc.vision.calibration.start/1` mit `board` (siehe unten). |

| Ausgabe | Typ | Bedeutung |
| --- | --- | --- |
| `Error` | `Int32` | `0` OK, `1` INVALID_STATE, `2` INVALID_ARGUMENT (Optionen kein gültiges JSON / Brett unbrauchbar, Grund im Log und in `CalibrationProgress.startError`), `3` BUSY |

Kompatibilität: Ein direkter asyncua-Aufruf ohne Argument funktioniert
weiter (Standardwert leer). Das webskillcomposition-Backend prüft die Anzahl
aber streng gegen `InputArguments`: Ein altes Frontend (ruft mit `[]`) wird
von einem neuen Pi abgelehnt, ein neues Frontend (ruft mit `[json]`) von einem
alten Pi. Pi und Frontend werden zusammen aktualisiert.

`CalibrationProgress` bekommt das Feld `board`: das Brett der laufenden bzw.
zuletzt gelaufenen Session (`BoardSpec.as_dict()`, also `type`, `cols`,
`rows`, `squareSizeM`, bei ChArUco zusätzlich `markerSizeM`, `dictionary`).
Nach einer abgelehnten Start-Option zusätzlich `startError` (Text), bis zum
nächsten erfolgreichen Start.

`MeasureBoard` bleibt unverändert; das Frontend schickt beim Roboterlauf der
Deckenkamera das gewählte Brett mit (`board` im Request, bisher `null`).

### Daten / Payload

```json
{
  "schema": "wsc.vision.calibration.start/1",
  "board": {
    "type": "charuco",
    "cols": 11, "rows": 7,
    "squareSizeM": 0.025, "markerSizeM": 0.0185,
    "dictionary": "DICT_5X5_100"
  }
}
```

`board` folgt denselben Regeln wie in `wsc.vision.handeye.start/1`
(`handeye_session._parse_board` und `check_raw_board`): Schachbrett mit
`cols`/`rows` = Innenecken, `cols + rows` ungerade, Feld 5–100 mm; ChArUco mit
`cols`/`rows` = Felder (mindestens 3×3), Marker kleiner als das Feld,
Wörterbuch aus `DICT_{4X4,5X5,6X6,7X7}_{50,100,250,1000}` mit genug Markern.
Fehlt `board`, gilt das Brett der Konfiguration.

### Python

```python
# Modul: src/vision_server/calibration_session.py
def parse_start_options(text: str) -> BoardSpec | None:
    """`OptionsJson` von StartCalibration -> Brett, None = Brett der Konfiguration.
    Wirft ValueError mit Klartext."""

class CalibrationSession:
    def start(self, board: BoardSpec | None = None) -> None: ...
    @property
    def board_spec(self) -> BoardSpec: ...   # Brett der laufenden/letzten Session
```

Die Auswertung (`_process`, `_compute_and_save`), das Archiv (`session.json`,
Feld `board`) und das Stream-Overlay im Modus `calibration` nehmen das Brett der
Session. Die gespeicherte Kalibrierdatei trägt es wie bisher im Feld `board`.

### Fehlerfälle

- Ungültige Optionen: `INVALID_ARGUMENT`, keine Session gestartet, eine
  vorherige Session bleibt unberührt.
- ChArUco auf der Deckenkamera: Die Erkennung läuft auf dem vollen Bild
  (`detect_board` verkleinert nur Schachbretter). Ein Bild ohne erkannte
  Ecken zählt bei der Auswertung wie bisher als „Board nicht gefunden“.

## Vorgehen

1. Pi: `parse_start_options`, `CalibrationSession.start(board)`, Board in
   Progress, Archiv und Overlay; `StartCalibration` mit `OptionsJson`.
2. Tests für Parser, Session und Methode.
3. Frontend: Brett-Einstellung (Schachbrett/ChArUco) im Kalibrierfenster der
   Deckenkamera, gemeinsam gespeichert mit der Layer-2-Einstellung; Start mit
   Optionen; `MeasureBoard` mit dem Brett.
4. Frontend: Posen ohne feste Anzahl einlernen, „Fertig“ speichert.

## Offene Fragen

- Wie schnell ist die ChArUco-Erkennung auf dem vollen 12-MP-Bild der
  Deckenkamera auf dem Pi? (`MeasureBoard` vor jeder Aufnahme.) Noch nicht
  auf der Hardware gemessen.

## Abweichungen vom Plan

Keine.
