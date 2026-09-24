# AprilTag-Posenschätzung effizienter ausführen

**Status:** fertig — 23.09.2026
**Verantwortlich:** Agent: AprilTag-Optimierung
**Thema:** apriltag
**Branch:** apriltag-optimierung

## Ziel

Bei mehreren Tags im selben Bild werden gemeinsame Kameradaten und Tag-Geometrien nur einmal vorbereitet. Der Livestream zeichnet die Markierungen erst auf die kleinere Ausgabeauflösung, erkennt die Tags aber weiterhin auf dem Originalbild. Die ermittelten Posen und Qualitätswerte bleiben gleich.

## Bereits gelesen

- `doc/projektdoku/apriltag-lokalisierung.md`
- `doc/projektdoku/apriltag-referenz.md`
- `doc/projektdoku/arbeitsplaene/README.md`

## Betroffene Dateien

- `src/tagloc/pose.py`
- `src/vision_server/stream_overlay.py`
- `tests/test_tag_pipeline.py`
- `tests/test_stream_overlay.py`
- `doc/projektdoku/apriltag-referenz.md`

## Schnittstellen

### Python

`estimate_tag_pose(observation, size_m, calibration) -> TagPose` und `estimate_tag_poses(observations, calibration, *, tag_map=None, default_size_m=0.05, max_reprojection_error_px=None) -> list[TagPose]` behalten Signatur, Rückgabewerte und Fehlerverhalten. Die gemeinsame Vorbereitung ist intern.

`AprilTagStreamAnnotator.annotate(image, mode) -> image` behält seine Signatur. Bei gesetzter `detection_max_width` wird das markierte AprilTag-Bild bereits hier auf diese Ausgabebreite gebracht; die Detektion erhält weiterhin das unskalierte Eingabebild.

### Daten / Payload

Keine Formatänderung. `pose_cam_tag`, Reprojektionsfehler und Mehrdeutigkeitsverhältnis behalten ihre Definition.

### Fehlerfälle

Nicht lösbare einzelne Tags werden weiterhin übersprungen und protokolliert; Programmierfehler brechen weiterhin ab.

## Vorgehen

1. Bestehende Laufzeit mit reproduzierbaren synthetischen Beobachtungen messen.
2. Gemeinsame Eingaben pro Bild vorbereiten und die numerische Gleichheit prüfen.
3. Zeichnen auf die Ausgabeauflösung verlegen und die Koordinatenskalierung testen.
4. Relevante Tests und Messung wiederholen.

## Offene Fragen

- Keine.

## Abweichungen vom Plan

- Zusätzlich zur Posenschätzung wurde das Zeichnen des Livestream-Overlays auf die Ausgabeauflösung verlegt; die Detektion bleibt auf voller Auflösung.

## Ergebnis

- Synthetische Messung auf diesem Rechner: 24 Tags × 300 Bilder, Median aus fünf Läufen: 0,962 s vorher, 0,880 s nachher.
- Livestream-Pfad ohne laufende Erkennung: 40 Frames bei 2028×1520 → 640 px, Median aus drei Läufen: 0,304 s vorher, 0,243 s nachher.
- Die Messungen isolieren diese Pfade; sie sind keine Framerate-Messung auf dem Raspberry Pi.

## Nach Abschluss

- Öffentliche Signaturen, Payload und Fehlercodes unverändert.
- Numerische Gleichheit von Einzel- und Bildverarbeitung mit synthetischen Tags geprüft.
- Dauerhafter Ablauf in `doc/projektdoku/apriltag-referenz.md` beschrieben.
