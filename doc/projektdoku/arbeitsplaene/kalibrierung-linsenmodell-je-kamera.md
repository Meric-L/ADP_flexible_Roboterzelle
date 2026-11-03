# Kalibrierung: Linsenmodell je Kamera (Fisheye für Layer 1, Pinhole für Layer 2)

**Status:** fertig — 29.09.2026 (Code und Tests; Neukalibrierung von LV1 am Pi steht aus)
**Verantwortlich:** `Agent: Linsenmodell je Kamera (Kannala-Brandt / Brown-Conrady)`
**Thema:** vision
**Branch:** apriltag

## Ziel

Jede Kamera wird mit dem Linsenmodell kalibriert, das sich in den Bildtests
als das beste erwiesen hat:

| Layer | Kamera | Pi | Auflösung | Modell |
| --- | --- | --- | --- | --- |
| LV1 | `cam_ceiling` (HQ-Kamera, Decke) | Pi 4 | 4056 × 3040 | OpenCV-Fisheye (Kannala-Brandt, 4 Koeffizienten k1–k4) |
| LV2 | `cam_flange` (Hand) | Pi 5 | 640 × 480 | OpenCV-Pinhole (Brown-Conrady, 5 Koeffizienten k1, k2, p1, p2, k3) |

Bisher gab es nur das Pinhole-Modell (`cv2.calibrateCamera`). Das Modell steht
danach in der Kalibrierdatei, und **alle** Verbraucher (Posenschätzung,
Overlay, Skalierung) richten sich nach der Datei, nicht nach der Config.

## Bereits gelesen

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md` (Meric, in
  Arbeit): betrifft Auflösung/Livestream von `cam_ceiling`, **nicht** das
  Linsenmodell — keine Überschneidung in den Dateien außer dem Preset in
  `server.py` (nur ein zusätzlicher Schlüssel, bestehende bleiben unberührt)
- `doc/projektdoku/vision-server-interface.md` Abschnitt 12
- `doc/frontend-integration-kalibrierung.md`

## Betroffene Dateien

- `src/tagloc/calibration.py` — Feld `model`, Schema `/2`, Lesen von `/1`
- `src/tagloc/lens.py` (neu) — modellabhängige OpenCV-Aufrufe an einer Stelle
- `src/tagloc/boards.py` — `calibrate_from_samples(..., model=...)`
- `src/tagloc/pose.py` — Entzerren über `lens.undistort_points`
- `src/tagloc/overlay.py` — Achsenkreuz über `lens.project_points`
- `src/tagloc/cli/calibrate.py` — Option `--model`
- `src/vision_server/profiles.py` — `AprilTagProfileConfig.calibration_model`
- `src/vision_server/server.py` — Preset je Kamera
- `src/vision_server/calibration_session.py` — Modell durchreichen, im Ergebnis melden
- `src/vision_server/runner.py` — `ActiveCalibrationInfo.model`
- `tests/test_lens.py` (neu), `tests/test_calibration_io.py`, `tests/test_calibration_session.py`
- `doc/projektdoku/vision-server-interface.md`, `doc/frontend-integration-kalibrierung.md`

## Schnittstellen

### Daten: Kalibrierdatei (`data/calibration/<frame_id>.json`)

Neues Schema `wsc.vision.calibration/2`, einziger Unterschied zu `/1` ist das
Feld `distortionModel`:

```jsonc
{
  "schema": "wsc.vision.calibration/2",
  "distortionModel": "fisheye",          // "pinhole" | "fisheye"
  "distortionCoefficients": [k1, k2, k3, k4],   // pinhole: [k1, k2, p1, p2, k3]
  // alles andere unverändert: calibrationId, frameId, imageSize, cameraMatrix,
  // rmsReprojectionError, sampleCount, board, createdAt
}
```

- Dateien mit Schema `/1` werden weiter gelesen und gelten als `pinhole` —
  bestehende Kalibrierungen bleiben gültig, ohne Neukalibrierung.
- Geschrieben wird immer `/2`. Ein alter Code-Stand lehnt eine `/2`-Datei
  laut ab (`Unbekanntes Kalibrierschema`) statt Fisheye-Koeffizienten stumm
  als Pinhole zu deuten.

### Python

```python
# Modul: src/tagloc/calibration.py
PINHOLE = "pinhole"; FISHEYE = "fisheye"; DISTORTION_MODELS = (PINHOLE, FISHEYE)

@dataclass(frozen=True)
class CameraCalibration:
    ...                      # unverändert
    model: str = PINHOLE     # neu, letztes Feld

# Modul: src/tagloc/lens.py (neu, braucht cv2)
def undistort_points(points, calibration) -> np.ndarray       # (N,2), Pixel, ideales Pinhole mit P=K
def project_points(object_points, rvec, tvec, calibration) -> np.ndarray   # (N,2), Pixel, mit Verzeichnung
def calibrate(object_points, image_points, image_size, model) -> tuple[float, K, D]
    # wirft ValueError bei unbekanntem Modell, cv2.error bei numerischem Scheitern

# Modul: src/tagloc/boards.py
def calibrate_from_samples(samples, image_size, spec, board=None, *,
                           frame_id="", model=PINHOLE) -> CameraCalibration
```

`scale_to_resolution` gilt für beide Modelle unverändert (Koeffizienten
beziehen sich auf normierte Koordinaten, nur `K` wird skaliert).

### Konfiguration

`AprilTagProfileConfig.calibration_model: str = "pinhole"` — mit welchem Modell
`CalibrationSession` **neu kalibriert**. Presets in `server.py`:
`cam_ceiling` → `"fisheye"`, `cam_flange` → `"pinhole"`.
Überschreibbar per Env `VISION_CALIBRATION_MODEL=pinhole|fisheye`.

Für die Posenschätzung zählt allein das Modell **in der Datei**.

### OPC UA (nur Ergänzungen, keine Änderung bestehender Felder)

- `CalibrationProgress` bekommt `"model"` während einer Session (Aufnahme,
  Auswertung, Ergebnis), `result.model` in jedem Ergebnis, auch bei
  Fehlschlag (Nachtrag 29.09.2026, damit das Frontend das Modell schon vorab
  und auch bei Fehlschlag anzeigen kann).
- `FinishCalibration`s `Summary` trägt `model` bei sofortiger Ablehnung.
- `ActiveCalibrationInfo` bekommt `"model": "fisheye" | "pinhole"`.

### CLI

`python -m tagloc.cli.calibrate ... --model fisheye` (Standard `pinhole`).

### Fehlerfälle

- Unbekanntes Modell in Config/CLI → `ValueError` beim Rechnen, in der Session
  als `result.error = 5` mit Meldung.
- Fisheye-Kalibrierung numerisch schlecht konditioniert (`CALIB_CHECK_COND`
  schlägt an) → einmaliger Neuversuch ohne diese Prüfung, mit Warnung im Log.
  Scheitert auch der, `result.error = 5` wie bisher.
- Unbekanntes `distortionModel` in einer Datei → `ValueError` beim Laden
  (Quelle bleibt wie bei jeder kaputten Kalibrierung zu).

## Vorgehen

1. `calibration.py` + `lens.py` + Tests (Rundreise Datei, Entzerren ↔ Projizieren
   für beide Modelle, synthetische Fisheye-Kalibrierung findet K/D wieder)
2. `boards.py`, `pose.py`, `overlay.py` auf `lens` umstellen
3. Config, Preset, Session, Runner, CLI
4. Doku (`vision-server-interface.md` Abschnitt 12, Frontend-Briefing)
5. Auf den Pis: **LV1 neu kalibrieren** (die vorhandene Pinhole-Datei gilt
   weiter, bis eine neue Fisheye-Datei geschrieben ist). LV2 muss nicht neu
   kalibriert werden — dort war es schon Pinhole mit 5 Koeffizienten.

## Offene Fragen

- Welche Flags hatten die Bildtests für Fisheye? Umgesetzt ist
  `RECOMPUTE_EXTRINSIC | CHECK_COND | FIX_SKEW` (OpenCV-Tutorial-Standard).
- `allow_resolution_mismatch` für `cam_ceiling` (Meric) nach der
  Neukalibrierung bei 4056 × 3040 entfernen — gehört zu dessen Plan.

## Abweichungen vom Plan

- **Array-Formen für `cv2.fisheye.calibrate`:** `(1, N, 3)` / `(1, N, 2)` je
  Aufnahme statt `(N, 1, 3)`. Letzteres scheitert unter OpenCV 5 mit
  „Sizes of input arguments do not match“ (gemessen 29.09.2026).
- **Flag-Konstanten:** OpenCV 5 führt die Fisheye-Flags nicht mehr unter
  `cv2.fisheye.*`, sondern unter `cv2.*` und mit anderen Zahlenwerten.
  `lens._fisheye_flag` schlägt sie deshalb zur Laufzeit nach; hart kodierte
  Werte wären auf Pi (OpenCV 4) oder Entwicklungsrechner (OpenCV 5) still
  falsch.
- **Neuversuch ohne `CHECK_COND`** nur bei einer „ill-conditioned“-Meldung,
  nicht bei jedem `cv2.error` — sonst verdeckte er echte Fehler.
- **`overlay.draw_tag_axes`** liest `model` per `getattr` (fehlend = Pinhole),
  weil Aufrufer und Test-Fakes Kalibrierobjekte ohne das Feld übergeben.
- Zusätzlich: `src/jobs/calibrate.py` (Kalibrier-Check-Job) nennt das Modell.
- Nebenbefund, nicht Teil dieser Arbeit: `tests/test_mjpeg_server.py::
  test_node_stays_at_stream_fps_while_viewers_run_faster` schlägt auch ohne
  diese Änderungen fehl (Zeitverhalten).

## Stand der Tests

Alle Tests grün außer dem oben genannten, bereits vorher roten MJPEG-Test.
Neu: `tests/test_lens.py` — synthetisch, ohne Kamera: Entzerren ↔
Projizieren für beide Modelle, Tag-Pose am Bildrand aus einem Fisheye-Bild,
Fisheye-Kalibrierung findet K und k1..k4 aus 20 synthetischen
Schachbrett-Aufnahmen wieder (RMS < 0,05 px), Pinhole liefert genau 5
Koeffizienten.

**Nicht getestet:** auf den Pis selbst (OpenCV-Version dort, echte Bilder).

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Fachwissen in `doc/projektdoku/vision-server-interface.md` übernommen.
