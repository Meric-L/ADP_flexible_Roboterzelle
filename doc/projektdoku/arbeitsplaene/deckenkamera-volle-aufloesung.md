# Deckenkamera (Pi 1) mit voller Sensorauflösung, flüssiger Livestream

**Status:** in Arbeit — 22.09.2026
**Verantwortlich:** Meric
**Thema:** vision
**Branch:** apriltag-optimierung

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

### Überholt (nicht umgesetzt)

Ursprünglicher Plan — wurde durch Team-Absprache vom 22.09.2026 überholt:

1. `CameraFrame.preview`, Picamera2-Konfiguration mit `lores`, Umrechnung
2. Stream nimmt `preview`
3. Preset `cam_ceiling` mit `preview_resolution` (960×720)
4. Tests, Doku
5. Auf Pi 1: Framerate messen (`measure_framerate.py`), Stream prüfen,
   **neu kalibrieren**

### Aktuelle Umsetzung

1. **Livestream-Overlay-Optimierung:** Erkennung in `AprilTagStreamAnnotator.annotate()`
   auf `max_stream_width`-Auflösung (960) verlegen, statt auf volle 12-MP-Auflösung
   (Datei: `src/vision_server/stream_overlay.py`)

2. **Subpixel-Eckenverfeinerung konfigurierbar machen:**
   - Neues Feld `subpixel_corner_refinement: bool = True` in `AprilTagProfileConfig`
     (`src/vision_server/profiles.py`) — siehe „Abweichungen vom Plan" unten: Default
     war kurzzeitig `False`, brach Layer 1, noch am selben Tag zurueckgenommen.
   - Detektor-Logik in `src/tagloc/detector.py` (`ArucoTagDetector`) anpassen

3. **Tests und lokale Verifizierung:**
   - Unit-Tests für die Overlay-Optimierung
   - CI-Tests (Windows, ohne Kamera)

4. **Auf Pi 1 verifizieren:**
   - AprilTag-Jobs: keine CPU-Überlastung mehr, keine 20s-Timeouts (DETECTION_FAILED)
   - Livestream „AprilTags markieren": flüssig, auch bei 10 fps und 12 MP
   - Livestream „Rohbild": unverändert

## Offene Fragen

- Genügt die Stream-Auflösung 960×720 für „AprilTags markieren”? Ein 10-cm-Tag
  aus Deckenhöhe ist darin deutlich kleiner als im Job-Bild. Im Zweifel zeigt
  das Overlay einen Tag nicht an, den der Job trotzdem findet — nie umgekehrt.
- Welcher Pi steckt in Pi 1 (`/proc/device-tree/model`)? Ein Pi 4 schafft
  12 MP bei 10 fps im ISP; ein Pi 3 nicht sicher.
- **A/B-Test 2026-09-24 (Stream aus vs. an):** getrennte Detektor-Instanzen
  (Commit 3d91ee1) und `VISION_DISABLE_STREAM=1` (Commit 85e06ff) bringen
  Job-Erfolg von 0/10 auf 3/4 -- klare Verbesserung, aber kein vollstaendiger
  Fix. Ein Job scheiterte auch ganz ohne Stream-Konkurrenz mit echtem
  20s-Timeout (`asyncio.wait_for` in job.py, nicht "kein Tag im Bild"). py-spy
  zeigt `_locate -> detect` allein bei 46,4 % des Profils
  (`subpixel_corner_refinement=True` auf vollen 12 MP). Vermutete Ursache:
  `samples_per_job=3` × (bis zu `capture_timeout_s=5.0` s Wartezeit +
  Detektionszeit) + `warmup_s=2.0` s laesst dem 20s-Budget zu wenig Puffer,
  auch ohne Overlay.
- **Gegenmassnahmen (2026-09-24), noch nicht auf dem Pi validiert:**
  - `job_timeout` global von 20.0 auf 30.0 s angehoben (`config.py`) --
    reine Sicherheitsmarge, senkt die Last nicht.
  - Neuer Tuning-Parameter `AprilTagProfileConfig.apriltag_quad_decimate`
    (Default `0.0`, ueber `VISION_APRILTAG_QUAD_DECIMATE` testbar) fuer
    `cv2.aruco.DetectorParameters.aprilTagQuadDecimate` -- laesst die
    AprilTag-Verfeinerungssuche auf einer verkleinerten Kopie laufen statt
    auf dem vollen Frame, wirkt nur zusammen mit `subpixel_corner_refinement`.
    Standard-AprilTag-Parameter, senkt typischerweise Rechenzeit deutlich,
    kostet aber Reichweite/Robustheit bei sehr kleinen/entfernten Tags --
    **noch mit echten Tag-Distanzen und py-spy auf pi1 messen**, bevor ein
    produktiver Default (Literatur-Richtwert 1,5-2,0) gesetzt wird.

### Subpixel-Nachbearbeitung der Kalibrierbilder (nicht Teil der aktuellen Umsetzung)

Angedacht ist eine Subpixel-Nachbearbeitung der bereits gespeicherten Kalibrierbilder
(Feld `calibration_capture_dir` in `AprilTagProfileConfig`). Eine genauere, aber
langsame Ecken-Verfeinerung soll als **Nachbearbeitungsschritt nach Abschluss aller
Kalibrieraufnahmen** auf den gespeicherten Bildern laufen — nicht live während der
Aufnahme, nicht in einem Stream angezeigt. **Offene Design-Fragen dazu:**

1. Soll das über eine neue OPC-UA-Methode ausgelöst sein, oder reicht ein
   Offline-CLI-Skript?
2. Ersetzt die Nachbearbeitung die gespeicherte Kalibrierdatei direkt, oder wird
   erst verglichen (RMS könnte sich auch verschlechtern)?
3. Ist eine serielle Laufzeit von ca. 4 Sekunden pro Bild (gemessen für die genaue
   Methode `CALIB_CB_ACCURACY` bei ~4000px Breite, siehe Kommentar in
   `src/tagloc/boards.py`) über alle Aufnahmen hinweg akzeptabel, oder braucht es
   Parallelisierung/einen Fortschrittsanzeiger?

Diese Anforderung ist **nicht Teil der aktuellen Umsetzung** und wird als Folgepunkt
offengehalten.

## Abweichungen vom Plan

Der ursprüngliche Ansatz (separater Picamera2-`lores`-Stream 960×720 aus dem ISP)
wurde **nicht umgesetzt**. Stattdessen folgt die Umsetzung einer späteren
Team-Absprache vom **22.09.2026** (dokumentiert in Kommentar `stream_overlay.py`
/ `camera_stream.py`): *„Overlay soll exakt das Bild zeigen, auf dem auch der Job
erkennt"*.

**Neue Lösung:** Die AprilTag-Erkennung für den Livestream-Overlay wird direkt in
`AprilTagStreamAnnotator.annotate()` (`src/vision_server/stream_overlay.py`) auf
die bereits vorhandene, per Software herunterskalierte Stream-Auflösung verlegt.
Das Feld `max_stream_width` in `CameraStreamConfig` (`src/vision_server/profiles.py`,
aktuell Default 960) bestimmt diese Auflösung. **Kein neuer Kamera-Stream nötig.**

**Grund:** py-spy-Messung auf pi1 zeigte, dass die Erkennung auf dem vollen
12-MP-Frame im Livestream-Overlay derart viel CPU frisst, dass die eigentlichen
AprilTag-Jobs den 20-Sekunden-Timeout reißen (DETECTION_FAILED). Die Job-Erkennung
(`AprilTagDetectionSource`) und Kalibrierung bleiben unverändert auf voller Auflösung
und Genauigkeit.

### Subpixel-Eckenverfeinerung (neue Konfigurierbarkeit) — zwei Regressionsrunden

Ein neues Konfigurationsfeld `subpixel_corner_refinement` wurde zu `AprilTagProfileConfig`
hinzugefügt (`src/vision_server/profiles.py`), das `cv2.aruco.CORNER_REFINE_APRILTAG`
in `src/tagloc/detector.py` (`ArucoTagDetector`) steuert. Die Funktionalität bleibt im
Code erhalten (nur abschaltbar), wird nicht gelöscht. Zwei Fehlschläge, bevor es passte:

**Runde 1 — Default `False` (Begründung: vermutete CPU-Last auf pi1).** Noch am selben
Tag zurückgenommen: mit `subpixel_corner_refinement=False` fand Layer 1 (Deckenkamera,
~2 m Distanz, „Module suchen" im Frontend) **live auf pi1 gar keine Tags mehr**. Die
Verfeinerung entscheidet bei kleinen/entfernten Tags nicht nur über Pose-Genauigkeit,
sondern teilweise darüber, ob die ID überhaupt dekodierbar ist — das war exakt der
Fall, für den `CORNER_REFINE_APRILTAG` ursprünglich eingebaut wurde (siehe historischer
Kommentar in `detector.py`: „exactly the Layer-1 case"). Die tatsächliche CPU-Notlage
(Job-Timeouts durch Overlay-Läufe > 8 s) kam vom vollen Sensor-Frame im
Livestream-Overlay (siehe oben), nicht von dieser Verfeinerung → Default zurück auf `True`.

**Runde 2 — Default `True`, aber Job und Stream teilten sich EINE Detektor-Instanz.**
Job-Erkennungs-Pfad (`AprilTagDetectionSource._locate`, volle Auflösung) und
Livestream-Overlay (`AprilTagStreamAnnotator.annotate`, verkleinerte Auflösung, siehe
oben) nutzten bis dahin denselben `ArucoTagDetector` aus `AprilTagDetectionSource.open()`
(siehe `runner.py`, `_build_annotator`, das den Detektor per `source._detector`
weiterreichte). Mit `subpixel_corner_refinement=True` liefen dadurch **beide** Pfade mit
aktivem `CORNER_REFINE_APRILTAG` — und liefen sich gegenseitig die CPU weg: py-spy-Dump
auf pi1 zeigte den Job-Thread (`vision-apriltag_0`) und den Overlay-Thread gleichzeitig
in `detector.py:136 detect()`. Job-Timeouts (`DETECTION_FAILED` nach 20 s) traten erneut
auf. py-spy-Top-Frames (45 s, deckte einen fehlgeschlagenen Job ab): 37,4 %
`_locate → detect` (Job, volle Auflösung), 21,0 % `_read_frames → make_array`
(Kamera-Framegrab, unabhängig von dieser Änderung), 18,6 % `annotate → detect`
(Overlay, verkleinerte Auflösung), 10,6 % `annotate → _resize_for_stream`.

**Fix: getrennte Detektor-Instanzen.** `runner._build_annotator` baut jetzt einen
eigenen Stream-Detektor (`build_detector(..., subpixel_corner_refinement=False)`),
unabhängig vom Konfigurationswert für den Job-Pfad. Der Job behält seine eigene
Instanz mit `subpixel_corner_refinement` aus `AprilTagProfileConfig` (aktuell `True`,
Layer 1 braucht das). Der Stream braucht die Verfeinerung nicht — kleineres Demo-Bild,
keine Pose-Genauigkeit produktiv im Einsatz. Job und Stream können sich damit nicht
mehr gegenseitig CPU wegnehmen, indem beide gleichzeitig die teure Verfeinerung laufen
lassen.

**Aktueller Stand:** `AprilTagProfileConfig.subpixel_corner_refinement` (Default
`True`) steuert **nur noch den Job-Pfad**. Der Livestream-Detektor hat die
Verfeinerung immer aus, fest im Code (`runner._build_annotator`), nicht konfigurierbar
— dafür bestand kein Bedarf. Die Konstruktor-Defaults von `ArucoTagDetector`/
`build_detector` selbst bleiben bewusst konservativ `False`, Aufrufer entscheiden
explizit.

### Ursprüngliche Implementierungsdetails (überholt)

Die folgenden Punkte beschreiben den ursprünglichen Ansatz und wurden nicht umgesetzt:

- `picamera2_video_configuration(config)` mit `lores`-Strom
- Preset `cam_ceiling` mit `preview_resolution` (960×720)
- ISP-seitige Skalierung für den Livestream

**Aktuelle Architektur (korrigiert):** Der `lores`-Stream existiert weiterhin und ist
für `cam_ceiling` aktiv (`PI_CAMERA_STREAM_PRESETS["cam_ceiling"]["preview_resolution"]
= (960, 720)` in `src/vision_server/server.py`) — genutzt aber nur vom
`"calibration"`-Modus des Livestreams (`camera_stream.py._encoded` waehlt `frame.preview`
nur dort). Die Modi `"apriltag"`/`"off"` gehen weiterhin vom vollen `frame.image` aus;
`"apriltag"` verkleinert seitdem selbst per Software in `annotate()` (siehe oben).

## Nach Abschluss

- Status auf `fertig`, Schnittstellen wie umgesetzt
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert
- Fachwissen in `doc/vision-server-interface.md` und `doc/vision-system.md`
