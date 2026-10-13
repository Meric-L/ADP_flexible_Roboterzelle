# Hand-Auge-Kalibrierung per OPC UA

**Status:** fertig — 13.10.2026 (an echter Hardware nicht getestet)
**Verantwortlich:** `Agent: Hand-Auge-Kalibrierung per OPC UA`; Frontend: Sitzung `opcua-f1` (`webskillcomposition`, Branch `Ungetestet`, Fenster „Hand-Kamera kalibrieren“)
**Thema:** apriltag
**Branch:** apriltag

## Ziel

`T_flansch_cam` lässt sich bisher nur über die CLI (`tagloc.cli.calibrate_handeye`) bestimmen. Mit dieser Änderung geht das per Knopf aus dem Frontend:
1. Der Bediener fährt den Roboter in mehrere Posen über einem liegenden Tag.
2. Pro Pose meldet das Frontend die Flanschpose.
3. Der Pi misst dazu den Tag, rechnet am Ende `T_flansch_cam` aus, speichert das Ergebnis und nutzt es sofort, ohne Neustart.

Ohne diese Datei startet kein Layer-2-Lauf (`layer2-lauf-hand-pi.md`).

## Bereits gelesen

- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md`: Solver, Dateiformat `wsc.vision.handeye/1`
- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md`, `layer2-annaeherung.md`: Muster Ports/Fakes, Flansch-Links
- `doc/projektdoku/vision-server-interface.md` Abschnitt 12: Kamera-Kalibrierung, Sperren

## Betroffene Dateien

- `src/vision_server/handeye_session.py` (neu)
- `src/vision_server/detection/apriltag.py`: neue Methoden `measure_tag` und `apply_hand_eye`; `_calibration_for` aus `_locate` herausgezogen
- `src/vision_server/address_space.py`: Knoten `HandEyeStatus`
- `src/vision_server/runner.py`: Methoden, Verdrahtung, Sperren, `aclose`
- `tests/test_handeye_session.py`, `tests/test_handeye_opcua.py` (neu); `tests/test_apriltag_source.py` (`MeasureTagTest`)
- `doc/projektdoku/vision-server-interface.md`: Abschnitt 15

## Schnittstellen

### OPC UA

Alle Elemente liegen unter `VisionMachine` im Namensraum `http://launch-rm.de/vision`. Es gibt sie nur auf dem Hand-Pi (`hand_eye_path` gesetzt). Sie sind zusätzlich unter `VisionProgram` verlinkt.

| Element | NodeId | Typ | Richtung |
| --- | --- | --- | --- |
| StartHandEyeCalibration | `ns=<vision>;s=VisionMachine.StartHandEyeCalibration` | `(OptionsJson: String) -> (Error: Int32)` | Aufruf |
| CaptureHandEyeSample | `ns=<vision>;s=VisionMachine.CaptureHandEyeSample` | `(SampleJson: String) -> (Error: Int32)` | Aufruf |
| FinishHandEyeCalibration | `ns=<vision>;s=VisionMachine.FinishHandEyeCalibration` | `() -> (Error: Int32)` | Aufruf |
| AbortHandEyeCalibration | `ns=<vision>;s=VisionMachine.AbortHandEyeCalibration` | `() -> (Error: Int32)` | Aufruf |
| HandEyeStatus | `ns=<vision>;s=VisionMachine.HandEyeStatus` | String (JSON) | Lesen/Abo |

**Alle Methoden antworten erst, wenn sie fertig sind.**
- `CaptureHandEyeSample` macht `samples_per_job` Bilder (Standard 3) und mittelt die Tag-Pose. Das dauert etwa so lange wie ein Job.
- `FinishHandEyeCalibration` rechnet und speichert. Mit einer Handvoll Posen dauert das Millisekunden.

| Methode | Code | Wann |
| --- | --- | --- |
| alle | `OK` | angenommen bzw. erledigt |
| Start | `BUSY` | Sitzung läuft schon; Job, Kamera-Kalibrierung oder Layer-2-Lauf aktiv |
| Start, Capture | `INVALID_ARGUMENT` | JSON oder Schema falsch, `tagId` fehlt oder ist negativ |
| Capture | `INVALID_STATE` | keine Sitzung im Zustand `capturing` |
| Capture | `BUSY` | eine Aufnahme misst noch, oder ein Job belegt die Kamera |
| Capture | `DETECTION_FAILED` | Aufnahme verworfen: Tag nicht gesehen, mehrdeutig oder kein Kamerabild. Der Grund steht in `samples[].detail` und `message`. Die Sitzung läuft weiter. |
| Capture | `CANCELLED` | Während der Messung wurde abgebrochen. |
| Finish | `INVALID_STATE` | keine Sitzung; **oder** zu wenige bzw. zu ähnliche Posen. Der Grund steht in `message`, die Sitzung bleibt `capturing`, und weitere Aufnahmen sind möglich. |
| Finish | `INTERNAL` | Berechnet, aber nicht gespeichert (Dateifehler). Der Zustand wird `failed`. |
| Abort | `INVALID_STATE` | keine Sitzung aktiv |

### Daten

**`OptionsJson`**, Schema `wsc.vision.handeye.start/1`:

```json
{ "schema": "wsc.vision.handeye.start/1", "tagId": 12,
  "tagInBase": { "position": [..], "orientation": [..] } }
```

`tagInBase` (`T_base_tag` aus dem Katalog-Eintrag des Roboters) ist optional, `null` ist erlaubt. Es dient nur der Gegenprobe.

**`SampleJson`**, Schema `wsc.vision.handeye.sample/1`:

```json
{ "schema": "wsc.vision.handeye.sample/1", "flangeInBase": { "position": [..], "orientation": [..] } }
```

`flangeInBase` ist die **gemeldete** Pose: FK aus den echten Gelenkwinkeln, auf den Flansch-Link. Die Links sind EVA/Demo1 `model_6`, UR5e `flange`, FR3 `eef_attach_point`, wie in `layer2-annaeherung.md`. Kalibriert man hier auf einen anderen Link als bei Layer 2, ist der Versatz falsch.

**`HandEyeStatus`**, Schema `wsc.vision.handeye.status/1`:

```json
{ "schema": "wsc.vision.handeye.status/1",
  "state": "capturing", "message": "5 Aufnahmen, Drehung um 2 Achsen -- berechenbar, mehr wird genauer",
  "tagId": 12, "tagInBase": {..} | null,
  "acceptedCount": 5, "rotationSpreadDeg": 41.3, "rotationAxes": 2,
  "samples": [ { "index": 0, "accepted": true, "detail": "Tag 12 gemessen",
                 "flangeInBase": {..}, "camToTag": {..} | null } ],
  "result": null }
```

- `state` ist einer von: `idle`, `capturing`, `solving`, `finished`, `failed`.
- `rotationSpreadDeg`: die größte Drehung zwischen zwei angenommenen Flanschposen.
- `rotationAxes` (0–3): um wie viele unabhängige Achsen deutlich gedreht wurde, mit mindestens 5° Drehung je Achse. Unter 2 ist die Rotation nicht bestimmt, und `message` sagt das.
- `message` bei `capturing`: der Hinweis für den Bediener, d. h. wie viele Aufnahmen noch fehlen (Minimum 3, empfohlen 8) und ob um eine weitere Achse gedreht werden muss.
- `result` nach `finished`:

```json
{ "poseFlangeCam": {..}, "rmsPositionM": 0.0012, "rmsRotationDeg": 0.21, "sampleCount": 8,
  "knownTagCheckM": 0.004 | null, "knownTagCheckDeg": 0.6 | null,
  "path": ".../data/handeye/cam_flange.json", "backupPath": ".../cam_flange.json.20261013T101500Z.bak" | null }
```

- `rmsPositionM`/`rmsRotationDeg`: die größte Abweichung des rekonstruierten Tag-Orts vom Mittel (`tagloc.handeye.target_spread`). Das Gütemaß kommt ohne zweite Messkette aus.
- `knownTagCheckM`/`Deg`: nur mit `tagInBase`. Das ist die Abweichung von `mittel(T_base_flansch · T_flansch_cam · T_cam_tag)` zu `tagInBase`. Ein großer Wert spricht eher gegen den Katalog als gegen die Kalibrierung.

### Python

```python
# src/vision_server/handeye_session.py   (numpy, tagloc; kein asyncua)
class HandEyeSession:
    active: bool                                   # capturing oder solving
    async def start(options_json) -> VisionErrorCode
    async def capture(sample_json) -> VisionErrorCode
    async def finish() -> VisionErrorCode
    async def abort() -> VisionErrorCode
def rotation_diversity(poses_base_flange) -> tuple[float, int]   # (Grad, Achsen)
def save_with_backup(path, hand_eye) -> Path | None

# src/vision_server/detection/apriltag.py
async def AprilTagDetectionSource.measure_tag(tag_id) -> tuple[Pose | None, str]
def AprilTagDetectionSource.apply_hand_eye(hand_eye) -> None    # verwirft den Anker
```

`measure_tag` misst rein im Kamera-KS: ohne Welt-KS und ohne CAD-Versatz aus der Tag-Map. Nur die Tag-Größe kommt aus der Karte. Mehrdeutige Posen (`is_ambiguous`) verwirft es, denn eine falsch gekippte Messung verdirbt die ganze Kalibrierung.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Tag nicht gesehen, mehrdeutig, kein Kamerabild | Aufnahme `accepted: false` mit Grund, `DETECTION_FAILED`, Sitzung läuft weiter |
| Weniger als 3 angenommene Posen, Drehung nur um eine Achse | `Finish` → `INVALID_STATE`, Grund in `message`, Sitzung bleibt offen |
| Datei nicht schreibbar | `Finish` → `INTERNAL`, Zustand `failed`, alte Kalibrierung bleibt aktiv |
| Abbruch während einer Aufnahme | Diese Aufnahme antwortet `CANCELLED`, Zustand `idle` |
| Server fährt herunter | Sitzung wird abgebrochen, nichts gespeichert |

## Offene Fragen

- **Automatisches Anfahren** der Posen um den bekannten Tag kommt später, im Frontend. `tagInBase` und `rotationAxes` sind dafür schon da.
- An echter Hardware ist das nicht getestet. Wie gut die Kalibrierung wird, hängt an der FK des Frontends (Gelenkwinkel → `model_6`/`flange`/`eef_attach_point`).

## Abweichungen vom Plan

- Zusätzlich zum Vorschlag von `opcua-f1`: `rotationAxes`, `acceptedCount`, `tagInBase`, `knownTagCheckDeg`, `path` und `backupPath` im Status bzw. Ergebnis.
- Eine fehlgeschlagene Berechnung (zu wenige oder zu ähnliche Posen) führt **nicht** zu `failed`: Die Sitzung bleibt offen, damit keine Aufnahme verloren geht. `failed` gibt es nur, wenn das Speichern scheitert.

## Nach Abschluss

- [x] Status `fertig`, Schnittstellen eingetragen.
- [x] Indexzeile in `README.md`.
- [x] `vision-server-interface.md` Abschnitt 15.
- [x] Testlauf: 547 Tests, davon 1 Fehlschlag, der nichts mit dieser Änderung zu tun hat (`test_mjpeg_server…viewers_run_faster`, schlägt auch ohne diese Änderung fehl). Neu: `test_handeye_session.py` (6), `test_handeye_opcua.py` (1), `MeasureTagTest` (4).
