# Deckenkamera: Subpixel-Ecken (subpix) gegen Pose-Zittern

**Status:** in Arbeit — 10.11.2026 (Code umgestellt, am Pi noch nicht bestätigt)
**Verantwortlich:** NobbisCode, `Agent: Subpixel-Ecken auf dem Decken-Pi`
**Thema:** vision
**Branch:** `messkette-protokoll`

## Ziel

Die Deckenkamera (`cam_ceiling`, Layer 1) misst Tagposen ruhiger. Bisher
liefert ArUco dort ganzzahlige Ecken. Ein Modultag ist auf 1,6 m nur etwa
85 px groß, die Rundung schlägt als Zittern in Tiefe und Drehung durch.
Hinterher verfeinert der Job die Ecken mit `CORNER_REFINE_SUBPIX`.

## Bereits gelesen

- `doc/projektdoku/kamera-und-detektor-analyse.md` (Abschnitt 2 und 5: `CORNER_REFINE_APRILTAG` verworfen, SUBPIX „nur 0,1–0,3 px Rauschen“)
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md` (Subpixel 24.09. aus, ~46 % CPU — gemessen mit `CORNER_REFINE_APRILTAG`)
- `doc/projektdoku/vision-server-interface.md` (Abschnitt 15 Subpixel-Ecken, 18c Helligkeitstest)
- `doc/projektdoku/code-handbuch.md` (Pi-Presets, `VISION_SUBPIXEL_CORNER_REFINEMENT`)

## Ausgangslage: Messung

**Helligkeitstest 09.11.2026, Decke, Reihe 1** (gleicher Tag, je 25 Bilder):
Ecken-Standardabweichung je Ecke, gemittelt.

| Licht | ArUco ohne Verfeinerung | pupil_apriltags (subpixelgenau) | z-sd | Drehung RMS |
| --- | --- | --- | --- | --- |
| 100 % | 0,68 px | 0,08 px | 5,9 mm | 8,7° |
| 80 % | 1,00 px | 0,11 px | 6,4 mm | 8,9° |
| 50 % | 1,07 px | 0,24 px | 8,0 mm | 9,1° |

**Kosten, PC, 19 echte 12-MP-Deckenbilder** (Reihe 1 und 2, alle Lichtstufen),
Detektor wie auf dem Pi (`perspectiveRemovePixelPerCell` 8):

- Gefundene IDs mit und ohne `subpix`: in allen 19 Bildern identisch.
- Laufzeit `detect()`: Median 86,7 ms ohne, 89,0 ms mit `subpix` (+3 %).
- Die ~46 % CPU aus dem Plan `deckenkamera-volle-aufloesung.md` betrafen
  `CORNER_REFINE_APRILTAG`. Das sucht das ganze Bild neu ab. `subpix`
  verfeinert nur ein kleines Fenster um jede schon gefundene Ecke.

## Betroffene Dateien

- `src/vision_server/server.py` (Preset `cam_ceiling`)
- `tests/test_subpixel_settings.py`, `tests/test_server_config.py`
- `doc/projektdoku/vision-server-interface.md`, `doc/projektdoku/code-handbuch.md`,
  `doc/projektdoku/kamera-und-detektor-analyse.md` (neuer Abschnitt)

## Schnittstellen

Keine neue Schnittstelle. Geändert wird nur ein Standardwert.

### Python

```python
# Modul: src/vision_server/server.py
PI_APRILTAG_PRESETS["cam_ceiling"]["subpixel_corner_refinement"] = True   # neu, vorher fehlend = aus
PI_APRILTAG_PRESETS["cam_ceiling"]["corner_refinement_method"] = "subpix"

def subpixel_corner_settings(preset: dict) -> dict:
    """Unverändert. Liefert {"subpixel_corner_refinement": bool,
    "corner_refinement_method": "subpix" | "apriltag"}; wirft ValueError
    bei unbekanntem Wert der Umgebungsvariable."""
```

### Daten / Payload

Keine Formatänderung. `cornersPx` in Job-Ergebnissen und im Helligkeitstest
(`frames[].aruco.cornersPx`) sind auf der Decke ab jetzt Gleitkommawerte statt
ganzer Pixel. Der Helligkeitstest schreibt den Schalter in
`detectors.aruco.subpixelCornerRefinement` mit.

### Netz / Betrieb

- Wirkt nach `git pull` und Neustart von `opcua-server.service` auf dem Decken-Pi.
- Zurückschalten ohne Code: `VISION_SUBPIXEL_CORNER_REFINEMENT=0` in der Unit
  (`sudo systemctl edit opcua-server.service`).
- Betroffen sind Job (`DetectAprilTags`), Zellen-Überwachung und Helligkeitstest.
  Der Livestream-Overlay-Detektor bleibt ohne Verfeinerung (`runner/livestream.py`).

### Fehlerfälle

- Unbekannter Wert in `VISION_SUBPIXEL_CORNER_REFINEMENT`: Start bricht mit `ValueError` ab (wie bisher).
- Bei sehr dunklen Stufen kann `cornerSubPix` einzelne Ecken um 1–3 px
  verschieben (im PC-Test bei 35 % Licht einmal 2,8 px). Das prüft die Reihe am Pi.

## Vorgehen

1. Preset umstellen, Tests anpassen, Doku nachziehen. Erledigt am 10.11.
2. Decken-Pi: `git pull`, Dienst neu starten, eine Helligkeitstest-Reihe mit
   denselben Stufen aufnehmen.
3. Mit Reihe 1 und 2 vergleichen: Eckenrauschen, z-sd, Drehung RMS,
   Erkennungsrate, Laufzeit je Stufe.
4. Ergebnis in `kamera-und-detektor-analyse.md` eintragen und Status auf `fertig` setzen.
   Wenn es schlechter wird: Preset zurück und Status auf `verworfen`.

## Offene Fragen

- Wie stark sinkt die Drehungs-Streuung? Etwa 2/3 der Bilder sind `ambiguous`
  (IPPE-Mehrdeutigkeit eines flach gesehenen Tags). Das löst Subpixel allein
  vermutlich nicht.
- Ist die Laufzeit auf dem Pi ebenfalls nur wenige Prozent höher? Am PC waren es +3 %.

## Abweichungen vom Plan

(noch keine)

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index aktualisiert.
- Messergebnis in `kamera-und-detektor-analyse.md`.
