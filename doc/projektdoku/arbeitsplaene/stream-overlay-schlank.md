# Livestream-Overlay: nur grüner Kasten und Tag-ID

**Status:** in Arbeit — 21.10.2026
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
die CLI (`tagloc detect`) zeigt weiterhin das ausführliche Debug-Overlay.

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
