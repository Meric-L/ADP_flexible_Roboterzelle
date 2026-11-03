# AprilTag-Erkennung für unscharfe Tags der Deckenkamera

**Status:** fertig — 19.10.2026
**Verantwortlich:** Meric (`Agent: Erkennung unscharfe Tags`)
**Thema:** vision
**Branch:** apriltag

## Ziel

Auf dem Deckenkamerabild `data/rohbilder/cam_ceiling_20261019-114704.png`
(4056×3040, Fischauge) werden alle fünf sichtbaren tag36h11-Tags gefunden
(IDs 0, 1, 2, 3, 583). Heute liefert `ArucoTagDetector` mit den
OpenCV-Standardparametern nur [1, 2, 583]. Das gehört zu Merics Plan
[`deckenkamera-volle-aufloesung.md`](deckenkamera-volle-aufloesung.md): dort
geht es um Auflösung und Stream, hier um die Erkennungsgüte auf dem vollen
Bild. Dieser Plan ergänzt ihn, im anderen Plan wird nichts geändert.

## Befund

- Das Bild ist optisch weich: Kanten 4–8 px breit bei Zellen von 6–13 px.
- Tag 0 (ca. 60 px Kante) hat 4 Bitfehler bei 3 korrigierbaren, wird also
  verworfen.
- Tag 3 (ca. 50 px Kante, Kontrast nur 60 Graustufen) wird falsch dekodiert.
- Drei `DetectorParameters` beheben das: `errorCorrectionRate = 1.0`
  (Standard 0.6), `perspectiveRemoveIgnoredMarginPerCell = 0.3` (Standard
  0.13), `perspectiveRemovePixelPerCell = 8` (Standard 4).
  Ergebnis [0, 1, 2, 3, 583] in 0,1 s auf dem PC, keine Mehrkosten.
- Das Parameterfeld ist robust: Rand 0.2–0.4 und ppc 8–10 liefern alle fünf.
- Die 40 Schachbrettbilder in `data/LV1` (keine Tags) liefern 0 Fehlalarme.
- **Risiko:** Mit `errorCorrectionRate` 1.0 werden bis zu 5 statt 3 Bitfehler
  korrigiert. Das theoretische Fehlalarmrisiko je Viereck-Kandidat steigt von
  etwa 0,03 % auf etwa 1,5 %. Die Randprüfung vor der Dekodierung filtert
  den Großteil der Kandidaten; auf den Testbildern zeigte sich kein
  Fehlalarm. Bleibt im Blick, falls im Betrieb Geister-IDs auftauchen
  (dann `error_correction_rate` auf etwa 0.8 senken).
- **Subpixel:** `subpixel_corner_refinement` (`CORNER_REFINE_APRILTAG`)
  bleibt aus. Mit Standardparametern verliert es Tags (nur [583]) und braucht
  2,7 s. Wird hier nicht angefasst.

## Bereits gelesen

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md` (kein anderer Plan
  zum Thema)
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md`
- `doc/projektdoku/apriltag-referenz.md`

## Betroffene Dateien

- `src/tagloc/detector.py`
- `src/tagloc/cli/_common.py`, `detect.py`, `build_tagmap.py`,
  `calibrate_handeye.py`
- `src/vision_server/profiles.py`
- `src/vision_server/server.py`
- `src/vision_server/detection/apriltag.py`
- `src/vision_server/runner.py`
- `tests/test_detector.py`
- `doc/projektdoku/apriltag-referenz.md` (neuer Abschnitt am Ende)

## Schnittstellen

### Python

```python
# Modul: src/tagloc/detector.py
class ArucoTagDetector:
    def __init__(self, family="tag36h11", *, parameters=None,
                 subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                 error_correction_rate: float | None = None,
                 perspective_remove_margin_per_cell: float | None = None,
                 perspective_remove_pixel_per_cell: int | None = None) -> None: ...

def build_detector(family="tag36h11", backend="aruco", *,
                   subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                   error_correction_rate: float | None = None,
                   perspective_remove_margin_per_cell: float | None = None,
                   perspective_remove_pixel_per_cell: int | None = None) -> TagDetector: ...
```

| Parameter | Typ | Default | `DetectorParameters`-Feld | Bedeutung |
| --- | --- | --- | --- | --- |
| `error_correction_rate` | `float \| None` | `None` | `errorCorrectionRate` | Anteil der korrigierbaren Bitfehler; OpenCV-Standard 0.6, 1.0 = alle |
| `perspective_remove_margin_per_cell` | `float \| None` | `None` | `perspectiveRemoveIgnoredMarginPerCell` | ignorierter Zellrand bei der Bitauswertung; Standard 0.13 |
| `perspective_remove_pixel_per_cell` | `int \| None` | `None` | `perspectiveRemovePixelPerCell` | Auflösung der entzerrten Zelle; Standard 4 |

`None` lässt den OpenCV-Standard, bestehende Aufrufer ändern sich nicht. Die
Werte gelten nur für `"aruco"`/`"auto"`, `pupil` ignoriert sie. Ein Attribut,
das die OpenCV-Version nicht kennt, wird übersprungen (Debug-Log).

### Konfiguration

`AprilTagProfileConfig` (`src/vision_server/profiles.py`) bekommt drei Felder
gleichen Namens mit Default `None`. `PI_APRILTAG_PRESETS["cam_ceiling"]` setzt
sie auf 1.0 / 0.3 / 8, `cam_flange` bleibt unverändert. Job-Detektor,
Stream-Overlay und Zellüberwachung bekommen dieselben Werte.

### CLI

`--error-correction-rate`, `--perspective-remove-margin`,
`--perspective-remove-ppc` in `detect`, `build_tagmap`, `calibrate_handeye`
(über `add_detection_arguments`), alle Default `None`.

### Fehlerfälle

Kein neuer Fehlerpfad. Unbekannte Attribute in älteren OpenCV-Versionen werden
übersprungen, die Erkennung läuft dann mit dem Standard.

## Vorgehen

1. Plan (dieses Dokument).
2. Parameter in `detector.py`, Config, Preset, alle Detektor-Erzeugungen, CLI.
3. Tests: Parameter werden gesetzt, `None` lässt den Standard,
   Regressionstest auf dem Rohbild mit den echten `cam_ceiling`-Werten.
4. Neuer Abschnitt in `apriltag-referenz.md`.

## Offene Fragen

- Gibt es die drei Attribute mit gleichem Namen auf dem Pi (OpenCV 4.x)? Der
  Code setzt sie defensiv, geprüft ist es dort noch nicht.
- Fehlalarmrate im Dauerbetrieb der Zelle (nur Testbilder geprüft).

## Abweichungen vom Plan

- `calibrate_handeye` nutzt `add_detection_arguments` nicht, bekommt die drei
  Optionen deshalb über den neuen Helfer `_common.add_decoder_arguments`
  (ebenso von `add_detection_arguments` aufgerufen). `_common.detector_options(args)`
  liefert die Keywords für `build_detector`.
- Tests: Zusätzlich zu `tests/test_detector.py` angepasst
  `tests/test_camera_owner.py` (Stream-Detektor, neuer Test) und
  `tests/test_cellwatch.py` (Attrappen-Config um die drei Felder ergänzt).
- Umgesetzt wie geplant: Detektor, Config-Felder, Preset `cam_ceiling`
  1.0 / 0.3 / 8, Job (`detection/apriltag.py`), Stream-Overlay und
  Zellüberwachung (`runner.py`) mit denselben Werten.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index aktualisiert.
- Fachwissen in `apriltag-referenz.md` übernommen.

## Nachtrag 19.10.2026

Die Kamera ist neu fokussiert, die Werte 1.0 / 0.3 / 8 gelten nicht mehr und
sind verworfen. Die oben beschriebenen Einzel-Keywords
`error_correction_rate`, `perspective_remove_margin_per_cell` und
`perspective_remove_pixel_per_cell` (in `ArucoTagDetector`, `build_detector`,
`AprilTagProfileConfig`) sowie die CLI-Optionen `--error-correction-rate`,
`--perspective-remove-margin`, `--perspective-remove-ppc` sind **entfernt**.
Stattdessen gibt es ein generisches Mapping `detector_parameters`
(OpenCV-`DetectorParameters`-Attributname → Wert), das Preset `cam_ceiling`
ist leer (OpenCV-Standard). Der Regressionstest auf dem alten Rohbild
übergibt die Werte ausdrücklich. Schnittstellen:
[`kamera-controls-und-detektor-parameter.md`](kamera-controls-und-detektor-parameter.md).
