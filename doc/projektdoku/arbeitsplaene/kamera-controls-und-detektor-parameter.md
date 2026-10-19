# Kamera-Controls und Detektor-Parameter ohne Codeänderung einstellen

**Status:** in Arbeit — 19.10.2026
**Verantwortlich:** Meric (`Agent: Kamera-Controls`)
**Thema:** vision
**Branch:** apriltag

## Ziel

Die Deckenkamera ist neu fokussiert, die festen Dekodier-Werte aus
[`apriltag-erkennung-unscharfe-tags.md`](apriltag-erkennung-unscharfe-tags.md)
(1.0 / 0.3 / 8, gemessen am unscharfen Bild) gelten nicht mehr. Hinterher lassen
sich **OpenCV-Detektor-Parameter** und **Picamera2-Kamera-Controls** per
Preset oder Umgebungsvariable einstellen, ohne Code zu ändern. Gebaut wird nur
die Mechanik; die konkreten Werte folgen nach Messung (beide Presets sind
vorerst leer).

## Bereits gelesen

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md` (kein anderer Plan
  zum Thema)
- `doc/projektdoku/arbeitsplaene/apriltag-erkennung-unscharfe-tags.md`
- `doc/projektdoku/apriltag-referenz.md` (Abschnitt 13), `vision-system.md`

## Betroffene Dateien

- `src/tagloc/detector.py`, `src/tagloc/cli/_common.py`, `detect.py`,
  `build_tagmap.py`, `calibrate_handeye.py`
- `src/vision_server/profiles.py`, `server.py`, `camera.py`, `runner.py`,
  `detection/apriltag.py`
- `tests/test_detector.py`, `test_camera.py`, `test_camera_owner.py`,
  `test_cellwatch.py`, `test_server_config.py` (neu bzw. passende Datei)
- `doc/projektdoku/apriltag-referenz.md` (Nachtrag), `vision-system.md`
  (neuer Abschnitt), `arbeitsplaene/apriltag-erkennung-unscharfe-tags.md`
  (Nachtrag), `arbeitsplaene/README.md`

## Schnittstellen

### Python

```python
# Modul: src/tagloc/detector.py
class ArucoTagDetector:
    def __init__(self, family="tag36h11", *, parameters=None,
                 subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                 detector_parameters: Mapping[str, Any] | None = None) -> None: ...

def build_detector(family="tag36h11", backend="aruco", *,
                   subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                   detector_parameters: Mapping[str, Any] | None = None) -> TagDetector: ...
```

`detector_parameters`: Attributname von `cv2.aruco.DetectorParameters` → Wert,
z. B. `{"errorCorrectionRate": 0.8, "adaptiveThreshWinSizeMax": 33}`. Jeder
Eintrag wird vor dem Bau des `ArucoDetector` gesetzt. Der Wert wird auf den
Typ des vorhandenen Attributs gebracht (int/float/bool). Wirft `ValueError`
bei unbekanntem Namen (Tippfehler fallen beim Start auf) und bei einem Wert,
der nicht in den Typ passt. `pupil` ignoriert das Mapping.
Reihenfolge: `subpixel_corner_refinement` setzt `cornerRefinementMethod`
zuerst, ein Eintrag `cornerRefinementMethod` in `detector_parameters` gewinnt.

```python
# Modul: src/vision_server/profiles.py
AprilTagProfileConfig.detector_parameters: dict[str, Any]   # Default {}
CameraStreamConfig.camera_controls: dict[str, Any]          # Default {}, nur Picamera2

# Modul: src/vision_server/camera.py
def picamera2_video_configuration(config) -> dict          # enthält "controls", falls gesetzt
def resolve_camera_controls(controls, namespace) -> dict   # String-Enums -> libcamera-Werte
def validate_camera_controls(controls, available) -> dict  # Namen prüfen, Typen angleichen
```

Beide Felder sind aus der Preset-Tabelle plus Env-Override in
`server.apriltag_config()` bzw. `server.vision_config()` gemerged.

### Umgebungsvariablen (Daten-Format)

Beide sind ein **JSON-Objekt**. Je Schlüssel gewinnt die Umgebungsvariable
über das Preset; `null` entfernt einen Preset-Eintrag.

| Variable | Wirkt auf | Beispiel |
| --- | --- | --- |
| `VISION_APRILTAG_DETECTOR_PARAMS` | `AprilTagProfileConfig.detector_parameters` (Job, Stream-Overlay, Zellüberwachung) | `'{"errorCorrectionRate": 0.8}'` |
| `VISION_CAMERA_CONTROLS` | `CameraStreamConfig.camera_controls` (nur Picamera2) | `'{"ExposureValue": -0.5, "Sharpness": 1.5, "NoiseReductionMode": "Fast"}'` |

Kamera-Controls: `ExposureTime` in µs (int), `AnalogueGain` (float),
`AeEnable` (bool), `ExposureValue`, `Sharpness`, `Contrast`, `Brightness`,
`Saturation` (float). Die Enum-Controls `NoiseReductionMode`,
`AeMeteringMode`, `AeExposureMode`, `AeConstraintMode`, `AwbMode` dürfen als
Name angegeben werden (`"HighQuality"`, `"Off"`, `"Fast"`), ganze Zahlen
bleiben Zahlen. Gültige Namen liefert `Picamera2().camera_controls`.

Die bestehenden Schalter (`VISION_SUBPIXEL_CORNER_REFINEMENT`,
`VISION_APRILTAG_QUAD_DECIMATE`, …) bleiben unverändert.

CLI (`detect`, `build_tagmap`, `calibrate_handeye`): wiederholbar
`--detector-param NAME=WERT`, Wert per `json.loads`, sonst als String, z. B.
`--detector-param errorCorrectionRate=0.8`.

### Netz / Betrieb

Der Server läuft auf den Pis als systemd-Service `opcua-server.service`
(`/etc/systemd/system/opcua-server.service`, siehe `vision-system.md`). Dort
setzt man die Variablen mit `sudo systemctl edit opcua-server.service`
(Drop-In `/etc/systemd/system/opcua-server.service.d/override.conf`):

```ini
[Service]
Environment='VISION_CAMERA_CONTROLS={"ExposureValue": -0.5, "NoiseReductionMode": "Fast"}'
Environment='VISION_APRILTAG_DETECTOR_PARAMS={"errorCorrectionRate": 0.8}'
```

Danach `sudo systemctl restart opcua-server.service`. Zum Ausprobieren im
Terminal: Variable vor `python3 src/OPCUA/server.py` setzen (Service vorher
stoppen). Beim Start loggt der Server, welche Detektor-Parameter vom Standard
abweichen, welche Kamera-Controls angewendet werden und, nach dem Warmup, die
tatsächlichen Metadaten (`ExposureTime`, `AnalogueGain`, `DigitalGain`,
`Lux`, `ColourTemperature`, `FocusFoM`) — daraus wählt man feste
Belichtungswerte (`journalctl -u opcua-server.service | grep -i "Kamera-Metadaten"`).

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Env-Variable kein gültiges JSON oder kein Objekt | `ValueError` beim Start, Meldung nennt die Variable |
| Unbekannter Detektor-Parameter / falscher Typ | `ValueError` beim Bau des Detektors (Start des Jobs) |
| Unbekannter Kamera-Control-Name | `ValueError` beim Öffnen der Kamera, Meldung listet die Namen der Kamera; Kamera wird wieder geschlossen |
| Unbekannter Enum-Name | `ValueError`, Meldung listet die gültigen Namen |
| Backend RealSense/OpenCV mit `camera_controls` | ignoriert, einmal Warnung im Log |
| Stream-Overlay mit `cornerRefinementMethod` im Mapping | Eintrag wird für den Stream-Detektor entfernt (Subpixel bleibt dort bewusst aus, CPU-Konkurrenz zum Job) |
| Metadaten nach Warmup nicht lesbar | Warnung, Kamera läuft weiter |

## Vorgehen

1. Plan (dieses Dokument).
2. Alte Einzel-Parameter ersetzen durch `detector_parameters`, Preset leer,
   Env-Override, CLI.
3. `camera_controls`: Config, Preset, Env, `camera.py`.
4. Tests, Fach-MDs (Nachträge), README.

## Offene Fragen

- Konkrete Werte für `cam_ceiling` (Detektor und Kamera) folgen nach der
  Messung am neu fokussierten Bild; bis dahin Status `in Arbeit`.
- Ground Truth für `data/rohbilder/cam_ceiling_20261019-142133.png` wird
  geklärt, der Regressionstest darauf wird danach ergänzt.

## Abweichungen vom Plan

- Betroffene Dateien: Tests liegen in `test_server_config.py` (Env-Merge),
  `test_camera.py` (Controls, Enum-Auflösung) und `test_detector.py`; die CLI-
  Werkzeuge `detect.py`, `build_tagmap.py`, `calibrate_handeye.py` blieben
  unverändert, sie nutzen `_common.detector_options(args)`.
- Zusätzliche Schnittstelle: `tagloc.detector.apply_detector_parameters(parameters, values)`
  (gibt die abweichenden Einträge zurück), `ArucoTagDetector.changed_parameters`
  (fürs Start-Log), `server.merged_json_object_env(name, preset)`,
  `camera.format_camera_metadata`, `camera.CAMERA_ENUM_CONTROLS`.
- `detector_parameters` und `camera_controls` sind Felder mit `hash=False`, die
  Dataclasses bleiben hashbar.
- Die Metadaten werden in `SharedCamera._open_camera` direkt nach dem Warmup
  gelesen, bevor der Capture-Loop startet (kein Wettlauf um Requests).
- Die Warnung für RealSense/OpenCV kommt bei jedem Öffnen (auch Neu-Öffnen),
  nicht streng nur einmal.
