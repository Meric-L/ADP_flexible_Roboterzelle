# Hand-Auge-Kalibrierung mit 9 Punkten prüfen und die Kameralage nachkorrigieren

**Status:** in Arbeit — 04.11.2026 (umgesetzt, Test am Aufbau steht aus)
**Verantwortlich:** `Agent: Hand-Auge-Punkte-Prüfung`
**Thema:** apriltag
**Branch:** `punkte-pruefung` (Pi, auf `messkette-protokoll`); Frontend in webskillcomposition auf `UngetestetAR`

## Ziel

Nach einer Hand-Auge-Kalibrierung soll sich am Aufbau prüfen lassen, ob sie stimmt: Die Handkamera misst das Blatt mit den vier ArUco-Markern der Use-Case-Zielscheibe, der Roboter fährt mit der Greiferspitze nacheinander über die 9 Kreuze des Blatts (Raster 20 mm, einstellbarer Abstand, Standard 30 mm), und der Bediener trägt je Punkt ein, wie weit die Spitze neben dem Kreuz steht. Aus diesen Versätzen rechnet das Frontend eine Korrektur der Kameralage am Flansch (`T_flansch_cam`, nur die Position), die der Hand-Pi mit Sicherung der alten Datei übernimmt.

Das ersetzt im Frontend die Prüfung „Brettursprung anfahren“ aus [`handeye-genauigkeit.md`](handeye-genauigkeit.md).

Die Blattlage misst `MeasureTarget` aus [`zielscheibe-servoing.md`](zielscheibe-servoing.md) (Meric). Neu auf dem Pi ist nur `ApplyHandEyeCorrection`: Eine Methode zum Ändern von `cam_flange.json` gab es nicht.

## Bereits gelesen

- `CLAUDE.md`: Regeln, Arbeitsplan-Pflicht
- `doc/projektdoku/arbeitsplaene/README.md`: Doppelarbeits-Check. Berührt: [`handeye-genauigkeit.md`](handeye-genauigkeit.md) (Meric, in Arbeit: Prüfung „Brettursprung anfahren“, reserviert `handeye_session.py` — hier nur `forget_result` ergänzt) und [`zielscheibe-servoing.md`](zielscheibe-servoing.md) (Meric, in Arbeit: `MeasureTarget`, wird unverändert genutzt)
- `doc/projektdoku/vision-server-interface.md`: Abschnitt 15 (Hand-Auge)
- `src/vision_server/measuretarget.py`: Anfrage und Ergebnis von `MeasureTarget`

## Betroffene Dateien

Hand-Pi (dieses Repo):

- `src/vision_server/handeye_correction.py` (neu): Korrektur prüfen und speichern
- `src/vision_server/runner/hand_eye_correction.py` (neu), `runner/install.py`, `runner/__init__.py`: Methode `ApplyHandEyeCorrection`
- `src/vision_server/handeye_session.py`: nur `forget_result(message)` ergänzt (Status nach einer Korrektur auf `idle`, damit kein altes Ergebnis stehen bleibt)
- `tests/test_handeye_correction.py` (neu)
- `doc/projektdoku/vision-server-interface.md`: neuer Abschnitt 20

Frontend (webskillcomposition): `HandEyePointCheck.tsx`, `handEyePointCheck.ts`, `handEyePointCheckCall.ts`, `toolPoint.ts` (neu), `jointSettling.ts` (`settledJoints`), `HandEyeAutoPanel.tsx` (Brettursprung-Prüfung entfernt), `HandEyeCalibrationWindow.tsx`, `piServers.ts` (`pointCheck`).

## Schnittstellen

### OPC UA (nur Hand-Pi)

| Element | NodeId | Signatur |
| --- | --- | --- |
| `ApplyHandEyeCorrection` | `ns=<vision>;s=VisionMachine.ApplyHandEyeCorrection` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` |
| `MeasureTarget` (vorhanden) | `ns=<vision>;s=VisionMachine.MeasureTarget` | siehe `zielscheibe-servoing.md` |

**`ApplyHandEyeCorrection`** ersetzt `poseFlangeCam` in `data/handeye/cam_flange.json` durch eine korrigierte Lage. Die alte Datei wird vorher als `cam_flange.json.<UTC>.bak` gesichert. Die neue Lage ist sofort aktiv (wie nach `FinishHandEyeCalibration`), der Anker der AprilTag-Quelle wird verworfen. Die Sitzung geht auf `idle`, `HandEyeStatus.current` zeigt die neue Lage. `ResultJson` ist immer gültiges JSON.

- `OK`: übernommen
- `INVALID_ARGUMENT`: Anfrage ungültig, oder die Änderung ist größer als 20 mm bzw. 2°
- `INVALID_STATE`: nicht `Operational`, keine Kalibrierdatei, oder `basedOnCalibrationId` passt nicht zur Datei (so wird auch eine zweimal geschickte Korrektur abgelehnt)
- `BUSY`: Hand-Auge-Sitzung (Aufnahme oder Rechnen), Job, Kamera-Kalibrierung oder Layer-2-Lauf aktiv
- `INTERNAL`: Datei nicht schreibbar, die alte Kalibrierung bleibt aktiv

### Daten

Anfrage `wsc.vision.handeye.correction/1`:

```json
{ "schema": "wsc.vision.handeye.correction/1",
  "basedOnCalibrationId": "cam_flange@2026-11-02T11:49:12Z",
  "poseFlangeCam": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
  "check": { "…": "Protokoll der Prüfung, beliebiges JSON-Objekt, wird nur abgelegt" } }
```

Ergebnis `wsc.vision.handeye.correction.result/1`:

```json
{ "schema": "wsc.vision.handeye.correction.result/1",
  "applied": true,
  "calibrationId": "cam_flange@2026-11-04T10:05:00Z", "previousCalibrationId": "…",
  "poseFlangeCam": { "…": "neu" }, "previousPoseFlangeCam": { "…": "alt" },
  "deltaPositionM": [dx, dy, dz], "deltaRotationDeg": 0.0,
  "path": "data/handeye/cam_flange.json", "backupPath": "…", "logPath": "data/handeye/corrections/<UTC>.json",
  "error": null, "message": "…" }
```

- `deltaPositionM`: neue minus alte Position, im Flansch-KS.
- Bei Ablehnung: `applied: false`, `error` (Name des Codes), `message`.
- Die Gütezahlen der Datei (RMS, Vorhersagefehler, Brettmaßstab) bleiben die der ursprünglichen Kalibrierung. Das Protokoll unter `data/handeye/corrections/` hält Anfrage, alte und neue Lage fest.

### Python

```python
# src/vision_server/handeye_correction.py
def parse_correction(text: str) -> CorrectionRequest:            # ValueError mit Klartext
def apply_correction(request: CorrectionRequest, path: Path, log_dir: Path | None = None) -> CorrectionOutcome:
    """Prüft gegen die Datei, sichert, schreibt neu und protokolliert.
    CorrectionRefused(code, message) bei INVALID_STATE / INVALID_ARGUMENT, OSError beim Schreiben."""
# src/vision_server/handeye_session.py
async def HandEyeSession.forget_result(message: str) -> None
```

## Frontend (webskillcomposition)

Im Fenster „Hand-Kamera kalibrieren“ ersetzt der Abschnitt „Punkte-Prüfung“ die Prüfung „Brettursprung anfahren“:

1. Ausgangsstellung, Blatt messen (`MeasureTarget`), Kamera 20 cm senkrecht über die Blattmitte (wie das Use-Case-Foto), noch einmal messen.
2. Die 9 Punkte in die Basis: `T_base_cam = FK · T_flansch_cam`, Strahl von der Kamera durch den Punkt des gemessenen Blatts, geschnitten mit der Tischebene des Robotertischs (`z = 0`). Liegt das gemessene Blatt mehr als 30 mm über oder unter dem Tisch, fährt nichts.
3. Werkzeugspitze (`tool_point`, 103,4 mm vor dem Flansch) senkrecht nach unten über die Punkte, Abstand per Schieber (5 bis 100 mm, Standard 30 mm), Reihenfolge (0,0), (−20,+20), (0,+20), (+20,+20), (+20,0), (+20,−20), (0,−20), (−20,−20), (−20,0). An jedem Punkt wartet der Lauf auf „Weiter“.
4. Eingabe je Punkt: Wo steht die Spitze neben dem Kreuz, in Blattachsen (mm).
5. Auswertung: mittlerer Versatz, Drehung und Maßstab des Rasters (nur zur Anzeige). Korrektur `Δ = R_basis_flansch(Messpose)ᵀ · (−ē_x, −ē_y, 0)` auf die Kameraposition am Flansch: Die Kamera saß um Δ anders als kalibriert, die Punkte lagen deshalb um −Δ (in der Tischebene) daneben. Höhe und Drehung bleiben.
6. „Kameralage übernehmen“ schickt die neue Lage mit `basedOnCalibrationId` an `ApplyHandEyeCorrection`.

## Offene Fragen

- Am Aufbau zu messen: Wie groß ist der Versatz vor und nach der Korrektur? Ziel: unter 1 mm.
- Ein Versatz, der von der Messpose abhängt (Neigungsfehler der Kamera), lässt sich mit einer einzigen Messpose nicht von einem Positionsfehler trennen. Die Korrektur ist für Blickrichtung senkrecht nach unten aus 20 cm genau, wie beim Use-Case-Foto. Ein gleichzeitiger Höhenfehler streckt die Korrektur leicht mit (4 mm entlang der Achse verfälschen 3 mm seitlich um etwa 0,06 mm).

## Abweichungen vom Plan

- Die erste Fassung (lokal, Branch `punkte-pruefung-v1`) hatte eine eigene Blattmessung `MeasureUseCaseSheet`. Sie entfällt: `MeasureTarget` aus `zielscheibe-servoing.md` misst dasselbe. Die Prüfung nutzt es unverändert.
- `ApplyHandEyeCorrection` hat einen eigenen Installer (`runner/hand_eye_correction.py`); `_install_hand_eye_session` bleibt unverändert.

## Verifikation

- Pi, Projekt-venv (Python 3.11, OpenCV 4.14) auf macOS: `tests/test_handeye_correction.py` 9 Tests OK (Sicherung, neue Kennung, Gütezahlen, Protokoll, Ablehnungen, OPC-UA-Methode samt Sitzungsstatus); `test_handeye_session`, `test_handeye_opcua`, `test_measuretarget` weiter OK.
- Frontend: Rechnung mit einer simulierten Prüfung getestet (Kamera 2 bzw. 3 mm daneben, Korrektur findet sie wieder).
- Am Aufbau noch nicht getestet.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
