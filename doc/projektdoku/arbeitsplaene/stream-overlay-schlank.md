# Livestream-Overlay: nur grüner Kasten und Tag-ID

**Status:** fertig — 21.10.2026 (Tests grün, am echten Pi-Stream nicht angesehen)
**Verantwortlich:** Meric, `Agent: Stream-Overlay verschlanken`
**Thema:** frontend
**Branch:** apriltag-overlay-schlank

## Ziel

Im Livestream (Overlay-Modus `apriltag`, wie ihn das Frontend anzeigt) wird je
erkanntem Tag nur noch ein **grüner Kasten** (Tag-Umriss) und die **Tag-ID**
gezeichnet. Entfällt: orange Färbung und Schriftzug „MEHRDEUTIG“, Achsenkreuz,
Eck-Punkt, Modulname, Distanz, Reprojektionsfehler und die Statusleiste unten
(„Modus: AprilTag  Familie …“ und die Liste der Tags im Bild).

## Bereits gelesen

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/vision-server-interface.md` (Tabelle der Overlay-Modi)
- `doc/projektdoku/apriltag-referenz.md` (Abschnitt `tagloc.overlay`)
- `doc/projektdoku/apriltag-e2e-test.md` (Overlay-Modi, Sichtprüfung)

## Betroffene Dateien

- `src/tagloc/overlay.py` — neue Funktion `draw_tag_boxes`
- `src/vision_server/stream_overlay.py` — Modus `apriltag` nutzt `draw_tag_boxes`, keine Statusleiste
- `tests/test_stream_overlay.py`, `tests/test_overlay_scaling.py`
- `doc/projektdoku/vision-server-interface.md`, `doc/projektdoku/apriltag-referenz.md`,
  `doc/projektdoku/apriltag-e2e-test.md`

## Schnittstellen

### OPC UA

Keine Änderung. Die Overlay-Modi (`apriltag`, `calibration`, `undistorted`,
`off`) und ihre Anzeigenamen bleiben gleich; nur das Bild im Modus `apriltag`
sieht anders aus.

### Python

```python
# Modul: src/tagloc/overlay.py
def draw_tag_boxes(image, tag_poses: Sequence[TagPose]) -> Any:
    """Zeichnet je Tag den Umriss in COLOR_OK (grün) und darüber "#<id>".

    Ändert `image` in-place und gibt es zurück. Mehrdeutige Posen werden
    nicht anders gezeichnet. Kein Achsenkreuz, kein Eck-Punkt, keine
    Distanz, kein Reprojektionsfehler. Strichstärke und Schriftgröße skalieren
    wie bisher mit der Bildbreite (`_scale_for`). Posen ohne `observation`
    werden übersprungen. Wirft nichts Eigenes.
    """
```

`draw_tag_overlay`, `draw_status_bar` und `summarise` bleiben unverändert —
die CLI (`python -m tagloc.cli.detect --overlay`) zeigt weiterhin das ausführliche Debug-Overlay.

`AprilTagStreamAnnotator.annotate(image, "apriltag")`: ruft `draw_tag_boxes`
statt `draw_tag_overlay` und **kein** `draw_status_bar` mehr. Die Modi
`calibration` und `undistorted` bleiben unverändert (Statusleiste dort
weiterhin, sie trägt den Kalibrierfortschritt).

### Daten / Payload

Keine Änderung. Mehrdeutigkeit wird weiterhin in den Job-Ergebnissen
(`ambiguous`, `ambiguityRatio`, `confidence`) gemeldet — nur nicht mehr im
Stream-Bild.

### Netz / Betrieb

Keine Änderung.

### Fehlerfälle

Wie bisher: ein Fehler beim Zeichnen wird geloggt, der Stream sendet das
unmarkierte Bild.

## Offene Fragen

- Keine.

## Umsetzung

Wie geplant umgesetzt, Schnittstelle unverändert gegenüber oben.

- `draw_tag_boxes`: Umriss per `cv2.polylines` in `COLOR_OK`; Label `#<id>` an
  der linken Kante der Eck-Bounding-Box, knapp über deren Oberkante
  (`max(4, round(6 * scale))` px), nach oben auf die Schrifthöhe begrenzt,
  damit es nicht aus dem Bild fällt.
- `stream_overlay.py`: Die Pose-Schätzung im Modus `apriltag` läuft wie bisher
  (ihr Ergebnis liefert die Ecken für die Kästen); nur das Zeichnen ist reduziert.
- Tests: `tests/test_stream_overlay.py` (Stream ruft `draw_tag_boxes`, kein
  `draw_status_bar`), `tests/test_overlay_scaling.py` (mehrdeutige Pose wird
  grün gezeichnet, Pose ohne Observation wird übersprungen). Gesamte Suite:
  691 Tests OK (`PYTHONPATH=src python -m unittest discover -s tests -t .`).
- Doku angepasst: `vision-server-interface.md`, `apriltag-referenz.md`,
  `apriltag-e2e-test.md`, Hinweis in `praesentation.md`.

## Nachtrag 21.10.2026: Merge mit „Livestream-Tags in voller Auflösung“

Zusammengeführt mit [`livestream-tags-volle-aufloesung.md`](livestream-tags-volle-aufloesung.md).
Das Overlay schätzt seitdem nicht mehr selbst auf der kleinen Leinwand. Es
zeichnet das neueste Ergebnis in voller Auflösung (`latest_tags`), mit auf
Stream-Größe umgerechneten Ecken, über `draw_tag_boxes`. Die Schnittstelle
oben bleibt gültig.

