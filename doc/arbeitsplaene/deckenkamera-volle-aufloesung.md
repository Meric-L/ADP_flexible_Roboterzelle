# Deckenkamera (Pi 1) mit voller Sensorauflösung, flüssiger Livestream

**Status:** fertig — 23.09.2026
**Verantwortlich:** John Glanz, `Agent: volle Auflösung Deckenkamera`
**Thema:** vision
**Branch:** feature/vision-server

## Ziel

Pi 1 (`ADP-Roboter-Lokalisierung`, `cam_ceiling`, Raspberry Pi HQ Camera,
Sensor IMX477) nimmt mit der vollen Sensorauflösung **4056×3040 (12,3 MP)**
auf statt mit dem 2×2-gebinnten Modus 2028×1520. Die AprilTag-Jobs bekommen
damit die volle Auflösung — kleine Tags aus der Deckenhöhe werden sicherer
gefunden und genauer vermessen.

Der Livestream bleibt dabei flüssig, **mit beiden Overlay-Modi**
(„Rohbild“ und „AprilTags markieren“). Heute schon ruckelt „AprilTags
markieren“ bei 3 MP, weil das Overlay jedes Bild in voller Auflösung kopiert,
darauf alle 0,5 s Tags sucht und erst danach verkleinert. Bei 12 MP wäre das
viermal so teuer.

## Bereits gelesen

- `CLAUDE.md`, `doc/arbeitsplaene/README.md` (kein laufender Plan zum Thema)
- `doc/arbeitsplaene/livestream-mjpeg-und-watchdog.md`
- `doc/vision-server-interface.md` (Abschnitt 10, 10.2)
- `doc/vision-system.md`
- `doc/apriltag-e2e-test.md` (Abschnitt 3.3, Kalibrierung)

## Lösung

> **Stand nach Abschluss:** Die Tabelle unten ist der ursprüngliche Plan.
> Seit 22.09.2026 laufen „AprilTags markieren“ und „Rohbild“ auf dem vollen
> Bild, nicht mehr auf `lores` — siehe „Abweichungen vom Plan“.

Die Pi-Kamera liefert neben dem Hauptbild (`main`) einen zweiten, kleinen
Bildstrom (`lores`) aus demselben Frame. Den skaliert der ISP in Hardware —
praktisch ohne CPU-Last. Aufteilung:

| Wer | Bild | Auflösung |
| --- | --- | --- |
| AprilTag-Job, Kalibrier-Session | `main` | 4056×3040 |
| Livestream (MJPEG und Knoten), beide Overlays | `lores` | 960×720 |

Das Overlay rechnet damit auf 0,7 MP statt 12,3 MP; die Kalibrierung wird
dafür wie bisher per `scale_to_resolution` auf die Stream-Größe umgerechnet
(Seitenverhältnis 960×720 vs. 4056×3040 weicht um 0,07 % ab, unter der
Schranke von 0,1 %).

Weitere Einsparung: Hauptstrom im Format `RGB888`, das Picamera2 im Speicher
als B,G,R ablegt — also direkt OpenCV-BGR. Die bisherige Farbumrechnung
(`XBGR8888` → `cv2.cvtColor`) auf jedem 12-MP-Frame entfällt.

## Betroffene Dateien

- `src/vision_server/camera.py` — `CameraFrame.preview`, Picamera2 mit
  `lores`, `RGB888`, Umrechnung YUV420 → BGR
- `src/vision_server/profiles.py` — neue Felder in `CameraStreamConfig`
- `src/vision_server/camera_stream.py` — Stream nimmt `preview`, wenn vorhanden
- `src/vision_server/server.py` — Preset für `cam_ceiling`
- `tests/test_camera.py`, `tests/test_camera_stream.py`, neuer Test für die
  Serverkonfiguration
- `doc/vision-server-interface.md`, `doc/vision-system.md`

## Schnittstellen

### OPC UA

**Keine Änderung.** Alle Knoten bleiben, wie sie sind. `LatestCameraFrame`
und der MJPEG-Stream liefern weiterhin höchstens 960 px breite JPEGs.

### Python

```python
# Modul: src/vision_server/camera.py
@dataclass(frozen=True)
class CameraFrame:
    image: Any            # BGR, volle Aufnahmeauflösung -- Jobs, Kalibrierung
    timestamp: float
    preview: Any = None   # BGR, kleiner Strom fuer den Livestream; None ohne lores
```

`SharedCamera.latest_frame.image` bleibt bei allen Backends das volle Bild.
`preview` ist nur bei Picamera2 mit gesetzter `preview_resolution` gefüllt;
RealSense und OpenCV liefern `None`, der Stream verkleinert dann selbst wie
bisher.

### Konfiguration (`CameraStreamConfig`)

| Feld | Standard | Bedeutung |
| --- | --- | --- |
| `preview_resolution` | `None` | Picamera2: Größe des `lores`-Stroms für den Livestream. `None` = kein zweiter Strom |
| `buffer_count` | `None` | Picamera2: Anzahl Kamerapuffer. `None` = Picamera2-Standard (6). Bei 12 MP zu viel für den CMA-Speicher |

Preset `cam_ceiling` (nur Backend `picamera2`):

| | vorher | nachher |
| --- | --- | --- |
| `resolution` (Job und Kamera) | 2028×1520 | **4056×3040** |
| `preview_resolution` | — | 960×720 |
| `capture_fps` | 15 | **10** (Obergrenze des IMX477 im Vollauflösungsmodus) |
| `buffer_count` | 6 | 2 |

### Fehlerfälle

- **Kalibrierung für 2028×1520 vorhanden:** Der 2028×1520-Modus ist das
  2×2-Binning des vollen Sensors, also dasselbe Sichtfeld. Die Intrinsik lässt
  sich exakt verdoppeln. Für `cam_ceiling` wird deshalb
  `allow_resolution_mismatch` gesetzt, bis die Neukalibrierung bei 4056×3040
  vorliegt. **Danach wieder entfernen.**
- **Keine Kalibrierung:** Die Platzhalter-Kalibrierung rechnet sich aus der
  Auflösung und passt sich selbst an.
- **Kamerapuffer lassen sich nicht anlegen** (CMA zu klein): `open()` scheitert
  wie jeder Kamerafehler, die Quelle bleibt zu. Abhilfe `buffer_count` oder
  CMA erhöhen.
- **Farben vertauscht** (Rot/Blau): hieße, `RGB888` liegt auf diesem
  Picamera2-Stand nicht als BGR vor. Erkennung arbeitet auf Graustufen und ist
  kaum betroffen, das Overlay schon.

## Vorgehen

1. `CameraFrame.preview`, Picamera2-Konfiguration mit `lores`, Umrechnung
2. Stream nimmt `preview`
3. Preset `cam_ceiling`
4. Tests, Doku
5. Auf Pi 1: Framerate messen (`measure_framerate.py`), Stream prüfen,
   **neu kalibrieren**

## Offene Fragen

- Genügt die Stream-Auflösung 960×720 für „AprilTags markieren“? Ein 10-cm-Tag
  aus Deckenhöhe ist darin deutlich kleiner als im Job-Bild. Im Zweifel zeigt
  das Overlay einen Tag nicht an, den der Job trotzdem findet — nie umgekehrt.
- Welcher Pi steckt in Pi 1 (`/proc/device-tree/model`)? Ein Pi 4 schafft
  12 MP bei 10 fps im ISP; ein Pi 3 nicht sicher.

## Abweichungen vom Plan

Keine an den Schnittstellen. Umgesetzt wie oben, zusätzlich:

- `picamera2_video_configuration(config)` in `camera.py` baut die Argumente
  für `create_video_configuration()` — ohne Kamera testbar, lehnt einen
  `lores`-Strom größer als den Hauptstrom ab.
- `SharedCamera._read_frames()` holt `main` und `lores` aus **einem**
  `capture_request()` und gibt den Request auch im Fehlerfall zurück (bei zwei
  Puffern stünde die Kamera sonst sofort). `_read_frame()` bleibt für
  `tools/measure_framerate.py` unverändert nutzbar und liest nur `main`.
- Preset als `PI_CAMERA_STREAM_PRESETS` in `server.py`, nur für Backend
  `picamera2`.
- Tests: `tests/test_camera.py` (Konfiguration, YUV420 mit und ohne
  Zeilenausrichtung, ein Request für beide Bilder), `tests/test_camera_stream.py`
  (`PreviewSourceTest`), neu `tests/test_server_config.py`.

Nachträglich geändert (NobbisCode, 22.09.2026, `9e9febd`, `4b7f473`,
`9214d94`) — Abweichung vom Ziel „beide Overlay-Modi auf `lores`“:

- „AprilTags markieren“ erkennt auf dem **vollen** 12-MP-Frame und
  verkleinert erst danach fürs Publizieren. Grund: Stream und Job sollen
  dieselben Treffer zeigen; auf `lores` war ein im Stream fehlender Tag kein
  Beleg dafür, dass der Job ihn verpasst.
- „Rohbild“ zeigt ebenfalls den vollen Frame (verkleinert), damit Fokus,
  Belichtung und Ausschnitt dem entsprechen, was der Pi wirklich sieht.
- Nur „Kalibrierboard markieren“ bleibt auf `preview` (960×720).
- `overlay_timeout_s` für `cam_ceiling` auf 8,0 s (Default 2,0 s reichte auf
  4056×3040 nicht, das Overlay fiel sonst jeden Tick aufs Rohbild zurück).
- Die geringere Stream-Framerate wird dafür bewusst in Kauf genommen. Die
  offene Frage „genügt 960×720 fürs Overlay?“ ist damit gegenstandslos.

Maßgeblich ist [`../vision-server-interface.md`](../vision-server-interface.md)
Abschnitt 10.3.

**Ergebnis auf Pi 1 (getestet, 23.09.2026):** Die Kamera läuft mit
4056×3040; gemessen **5–10 fps** statt stabiler 10 fps. Der Plan ist damit
abgeschlossen.

Ursprünglich offene Prüfpunkte auf Pi 1:

1. `measure_framerate.py` bei 4056×3040 — **5–10 fps** gemessen, die
   angeforderten 10 fps werden nicht ganz erreicht.
2. Livestream und Farben — getestet.
3. Kamerapuffer — getestet, `open()` läuft mit `buffer_count` 2.
4. `allow_resolution_mismatch` bleibt im Preset `cam_ceiling` stehen: Bei
   einer Kalibrierung für 4056×3040 ist der Schalter wirkungslos, bei einer
   alten für 2028×1520 rechnet er sie hoch. Entfernen, sobald sicher nur noch
   eine 4056×3040-Kalibrierung auf Pi 1 liegt — dann fällt eine falsche
   Auflösung wieder als Fehler auf.

## Nach Abschluss

- Status auf `fertig`, Schnittstellen wie umgesetzt
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert
- Fachwissen in `doc/vision-server-interface.md` und `doc/vision-system.md`
