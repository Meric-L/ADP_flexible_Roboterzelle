# Manuelle Testskripte – AprilTags (UNGETESTET)

> **Abgelöst.** Diese Skripte sind der ungetestete Erstentwurf. Produktiv ist
> das Paket `src/tagloc/`:
>
> | statt | benutze |
> |---|---|
> | `python src/apriltag/calibrate_camera.py` | `python -m tagloc.cli.calibrate` |
> | `python src/apriltag/detect_apriltags.py` | `python -m tagloc.cli.detect` |
>
> Siehe [`doc/projektdoku/apriltag-referenz.md`](../../doc/projektdoku/apriltag-referenz.md) und
> [`doc/projektdoku/apriltag-e2e-test.md`](../../doc/projektdoku/apriltag-e2e-test.md). Der Ordner bleibt
> vorerst als Vergleichsmöglichkeit stehen, bis die neue Kette einmal an der
> echten Pi-Kamera abgenommen wurde.

Erste Einstiegs-Skripte zum Vertrautmachen mit AprilTags. Noch nicht mit echter Kamera getestet.

## Setup
```bash
pip install -r requirements.txt
```

## calibrate_camera.py
Kamerakalibrierung mit Live-UI. Board-Typ oben per `USE_CHARUCO` umschalten.

| Taste | Aktion |
|-------|--------|
| `SPACE` | Frame als Sample erfassen (nur bei erkanntem Muster) |
| `C` | Kalibrierung berechnen (ab 15 Samples) |
| `S` | Ergebnis als `calibration.yaml` speichern |
| `Q` | Beenden |

## detect_apriltags.py
Live-Erkennung mit Pose-Overlay. Benötigt `calibration.yaml` im selben Ordner.

| Taste | Aktion |
|-------|--------|
| `Q` | Beenden |

## Hinweise
- Kameraquelle über `CAMERA_SOURCE` (oben im Skript) anpassbar.
- Tag-Familie / Größe über `TAG_FAMILY`, `TAG_SIZE_M`.
