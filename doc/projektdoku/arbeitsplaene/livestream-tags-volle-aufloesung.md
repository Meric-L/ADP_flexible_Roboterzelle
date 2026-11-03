# Livestream zeigt die Tags aus der Erkennung in voller Auflösung

**Status:** fertig — 21.10.2026 (Code und Tests; am Pi nicht gemessen, siehe Offene Fragen)
**Verantwortlich:** Meric, `Agent: Livestream-Tags aus voller Auflösung`
**Thema:** vision
**Branch:** apriltag-stream-volle-aufloesung (von `apriltag`)

## Ziel

Im Overlay-Modus „AprilTags markieren“ (`apriltag`) zeigt der Livestream die
Tags aus einer Erkennung in **voller Kameraauflösung** (Deckenkamera
4056×3040). Gestreamt wird weiter nur das kleine Bild (`max_stream_width`,
960 px). Bisher (seit `e1c677c`, 24.09.2026) erkannte das Overlay selbst auf dem
verkleinerten Bild, mit eigenem Detektor ohne Eckenverfeinerung. Der Stream war
damit kein Beleg dafür, was der Job findet.

Dabei darf das Problem vom 24.09.2026 nicht zurückkommen. Damals erkannte das
Overlay alle 0,5 s auf 12 MP. Laut py-spy kostete das ~102 % einer Core, und
AprilTag-Jobs liefen in den 20-s-Timeout.

## Bereits gelesen

- `doc/projektdoku/vision-server-interface.md`, Abschnitt 10 (Livestream, Overlay, zwei Bildströme)
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md` (Abweichungen: Overlay-Erkennung verkleinert, eigener Detektor)
- `doc/projektdoku/kamera-und-detektor-analyse.md`, Abschnitt 2.1 (Laufzeit `detect()` auf 12 MP: PC 0,09–0,13 s, Pi 4 grob 5–10× langsamer)
- `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md` (eigener Detektor, Pause während Job/Kalibrierung)

## Lösung

Das Overlay erkennt **nicht mehr im Stream-Takt**. Es zeichnet das neueste
Ergebnis in voller Auflösung, egal, wer es berechnet hat:

| Quelle (`origin`) | Wann | Zusätzliche CPU |
| --- | --- | --- |
| `job` | jedes Bild eines AprilTag-Jobs (`_locate`) | keine, rechnet ohnehin |
| `cellwatch` | jedes Bild der Zellen-Überwachung (`observe_cell`, alle 2 s, wenn scharf) | keine |
| `measure` | `MeasureTag`/`MeasureTags`, Hand-Auge-Aufnahmen | keine |
| `overlay` | eigene Erkennung des Overlays, nur wenn das neueste Ergebnis älter als `overlay_detect_interval_s` ist | ~1 s Rechenzeit je 5 s am Pi |

Die eigene Erkennung läuft
- in einem eigenen Hintergrund-Thread: `annotate()` wartet nicht darauf, der Stream stockt nicht;
- höchstens einmal gleichzeitig;
- **nicht**, solange ein Job oder eine Kamera-Kalibrierung läuft. Das ist dasselbe Signal, mit dem die Zellen-Überwachung pausiert;
- mit dem Stream-Detektor: dieselben Parameter wie im Job, nur ohne Eckenverfeinerung. Bei `cam_ceiling` ist die Verfeinerung auch im Job aus, die Treffer sind dann identisch.

Gezeichnet wird auf dem verkleinerten Bild. Die Ecken werden von der
Auflösung des Ergebnisses auf die Stream-Größe umgerechnet. Das Achsenkreuz
projiziert die metrische Pose mit der auf Stream-Größe skalierten
Kalibrierung (`scale_to_resolution`).

Die Statuszeile nennt Auflösung, Quelle und Alter, damit niemand ein altes
Ergebnis für ein aktuelles hält:
`Volle Aufl. 4056x3040, Job vor 3 s` bzw. `Noch keine Erkennung in voller Aufloesung`.

## Betroffene Dateien

- `src/vision_server/detection/apriltag.py`: `TagSnapshot`, `latest_tags`, Setzen in `_locate`, `observe_cell`, `_measure_tag`, `_detect_in_camera`
- `src/vision_server/stream_overlay.py`: Modus `apriltag` neu
- `src/vision_server/runner.py`: `_build_annotator` (Snapshot-Quelle, Intervall), Belegt-Signal setzen
- `src/vision_server/profiles.py`: neues Feld `overlay_detect_interval_s`
- `src/vision_server/camera_stream.py`, `src/vision_server/server.py`: nur veraltete Kommentare
- `tests/test_stream_overlay.py`, `tests/test_apriltag_source.py`
- `doc/projektdoku/vision-server-interface.md`: neuer Abschnitt 10.4

## Schnittstellen

### OPC UA

Keine Änderung. `CameraStreamMode` behält die Werte `off`/`apriltag`/
`calibration`/`undistorted`. `LatestCameraFrame` und MJPEG liefern weiter
höchstens `max_stream_width` breite JPEGs.

### Python

```python
# Modul: src/vision_server/detection/apriltag.py
@dataclass(frozen=True)
class TagSnapshot:
    tag_poses: tuple          # tagloc TagPose, Ecken in Pixeln von image_size
    image_size: tuple[int, int]  # (Breite, Hoehe) des ausgewerteten Bilds
    origin: str               # "job" | "cellwatch" | "measure" | "overlay"
    taken_at: float           # time.monotonic() nach der Erkennung

class AprilTagDetectionSource:
    latest_tags: TagSnapshot | None   # neuestes Ergebnis in voller Aufloesung, None bis zum ersten
```

`latest_tags` wird nur durch eine einfache Zuweisung ersetzt, nie verändert.
Damit ist das Lesen aus einem anderen Thread ohne Sperre sicher.

```python
# Modul: src/vision_server/stream_overlay.py
class AprilTagStreamAnnotator:
    def __init__(self, config, *, detector, calibration, tag_map,
                 interval_s=0.5, detection_max_width=None,
                 full_resolution_tags: Callable[[], TagSnapshot | None] | None = None,
                 detect_interval_s: float = 5.0) -> None: ...
    def set_busy(self, busy: Callable[[], bool] | None) -> None:
        """Solange busy() True ist, startet das Overlay keine eigene Erkennung."""
    def close(self) -> None:
        """Gibt den Erkennungs-Thread frei (CameraStreamPublisher.stop ruft es)."""
```

`detect_interval_s <= 0` schaltet die eigene Erkennung ab. Dann zeigt das
Overlay nur Ergebnisse von Job, Zellen-Überwachung und Messungen.

### Netz / Betrieb

Neues Feld in `CameraStreamConfig`:

| Feld | Standard | Bedeutung |
| --- | --- | --- |
| `overlay_detect_interval_s` | 5.0 | Ist das neueste Ergebnis in voller Auflösung älter, erkennt das Overlay selbst (Hintergrund, nicht während Job/Kalibrierung). `0` = nie selbst erkennen |

`overlay_interval_s` (0,5 s) gilt weiter nur für den Modus `calibration`.

### Fehlerfälle

- Fehler in der eigenen Erkennung: Log, das letzte Ergebnis bleibt stehen, nächster Versuch nach dem Intervall.
- Langer Job (bis 20 s): Der Job liefert selbst Ergebnisse, das Overlay zeigt sie.
- Noch kein Ergebnis: Das Bild kommt ohne Markierung, mit Hinweis in der Statuszeile.
- Ergebnis aus anderer Auflösung (z. B. nach Kalibrierwechsel): Die Ecken werden mit dem Verhältnis der Bildgrößen umgerechnet.

## Vorgehen

1. `TagSnapshot` und `latest_tags` in der Quelle, Tests
2. Annotator: Snapshot zeichnen, eigene Erkennung im Hintergrund, Tests
3. Runner verdrahten, Konfigfeld
4. Doku: Abschnitt 10.4, Plan auf `fertig`

## Offene Fragen

- Am Pi messen: Wie lange dauert ein `detect()` auf 4056×3040 tatsächlich, und wie viel CPU kostet das Overlay mit 5 s?
- Startet die eigene Erkennung genau vor einem Job, überlappen sich beide höchstens ~1 s. Reicht das, oder soll der Job eine laufende Overlay-Erkennung abwarten?

## Abweichungen vom Plan

- Zusätzlich `AprilTagStreamAnnotator.close()`. `CameraStreamPublisher.stop()`
  ruft es auf und gibt damit den Erkennungs-Thread frei.
- Ein gescheiterter eigener Versuch zählt als Versuch. Der nächste kommt erst
  nach `overlay_detect_interval_s`, nicht im nächsten Tick.
- Schnittstellen wie geplant umgesetzt. Die Fach-MD hat den neuen Abschnitt
  `vision-server-interface.md` 10.4.

## Messung (PC, 21.10.2026)

`data/rohbilder/cam_ceiling_20261019-142133.png`, Stream-Detektor mit den
Parametern von `cam_ceiling` (`perspectiveRemovePixelPerCell` 8, ohne
Eckenverfeinerung):

| Auflösung | `detect()` | gefundene IDs |
| --- | --- | --- |
| 4056×3040 | 153–166 ms | 0, 1, 1, 2, 3, 583, 586 |
| 960×720 | 26–32 ms | 0, 1, 1, 2, 583, 584 |

Das alte Overlay (960) zeigte also zwei Tags nicht (3, 586) und einen, den die
volle Auflösung nicht findet (584). Die Annahme „im Zweifel fehlt ein Tag im
Overlay, nie umgekehrt“ hielt damit nicht. Am Pi 4 (5–10× langsamer) kostet
ein Lauf in voller Auflösung ca. 0,8–1,6 s, alle 5 s also 16–32 % einer Core.

## Nachtrag 21.10.2026: Merge mit dem schlanken Overlay

Beim Merge in `apriltag` mit [`stream-overlay-schlank.md`](stream-overlay-schlank.md)
zusammengeführt. Die Datenquelle (`latest_tags`, eigene Erkennung im
Hintergrund, Ecken-Umrechnung `_scale_corners`) bleibt wie oben. Gezeichnet
wird aber nur noch `draw_tag_boxes` (grüner Kasten und `#<id>`). Achsenkreuz
und Statuszeile mit `Volle Aufl. …` bzw. `Noch keine Erkennung …` entfallen,
`_snapshot_line` ist entfernt. Vor dem ersten Ergebnis kommt das Bild also
ohne Markierung und ohne Hinweis.

