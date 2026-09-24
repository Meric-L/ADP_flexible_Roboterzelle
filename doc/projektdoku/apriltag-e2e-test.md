# AprilTag-Lokalisierung end-to-end testen

Diese Anleitung führt die gesamte Kette einmal durch — von der leeren Maschine
bis zur Modulpose im Frontend. Sie ist in Stufen geteilt, die aufeinander
aufbauen. **Jede Stufe hat ein Abbruchkriterium**: ist es nicht erfüllt, hat es
keinen Sinn, die nächste zu versuchen — der Fehler steckt dann in einer Schicht,
die man später nicht mehr isolieren kann.

| Stufe | Wo | Hardware | Beantwortet |
|---|---|---|---|
| [0](#0-vorbereitung) | PC | keine | Läuft die Software überhaupt? |
| [1](#1-stufe-1--ohne-kamera-am-pc) | PC | keine | Rechnet die Kette richtig? |
| [2](#2-stufe-2--mit-webcam-am-pc) | PC | Webcam | Funktioniert Bild → Pose? |
| [3](#3-stufe-3--auf-dem-raspberry-pi-mit-pi-kamera) | Pi | **Pi-Kamera** | Funktioniert es in der Zelle? |
| [4](#4-stufe-4--im-frontend) | Pi + Frontend | Pi-Kamera | Sieht der Bediener, was passiert? |

Nachbardokumente: [`apriltag-lokalisierung.md`](apriltag-lokalisierung.md) (warum es
so gebaut ist), [`apriltag-referenz.md`](apriltag-referenz.md) (Funktions- und
CLI-Referenz), [`vision-server-interface.md`](vision-server-interface.md) (OPC-UA-Seite).

---

## 0. Vorbereitung

### 0.1 Umgebung am PC

Die Umgebung in diesem Repo ist einsatzbereit (OpenCV 5.0.0, numpy 2.5.3,
asyncua 2.0.1, pupil-apriltags) — springe direkt zur Prüfung unten. Das alte,
defekte `.venv` liegt als `.venv.kaputt/` daneben und kann gelöscht werden.

> **Auf einem anderen Rechner:** `setup.sh` legt **keine** venv an, es richtet
> nur das ShareLaTeX-Remote ein. Lege sie deshalb selbst an — altes
> `.venv`-Verzeichnis entfernen, dann

```bash
cd ADP_flexible_Roboterzelle
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Prüfen — beide Zeilen müssen etwas ausgeben:

```bash
.venv/bin/python3 -c "import cv2, numpy; print('opencv', cv2.__version__, '| numpy', numpy.__version__)"
.venv/bin/python3 -c "import cv2; print('aruco ok:', hasattr(cv2.aruco, 'DICT_APRILTAG_36h11'))"
```

**Abbruchkriterium:** `aruco ok: True`. Ohne `opencv-contrib-python` fehlt
`cv2.aruco` vollständig; das normale `opencv-python` reicht nicht.

### 0.2 Tags drucken

```bash
PYTHONPATH=src .venv/bin/python3 tools/make_tag_sheet.py \
    --ids 0 1 2 3 7 12 --size-mm 100 --out /tmp/tags.svg
```

Die Ausgabe ist SVG in echten Millimetern — im Browser öffnen und drucken,
dabei **Skalierung auf „100 % / Originalgröße" stellen**, nicht „an Seite
anpassen". Auf jedem Bogen steht unten ein 100-mm-Maßstab: **den nach dem
Drucken mit dem Lineal nachmessen.** Stimmt er nicht, hat der Drucker skaliert
und alle Tags darauf sind unbrauchbar.

Danach das Wichtigste dieser ganzen Anleitung:

> **Miss die gedruckte Kante mit dem Lineal nach und trage den gemessenen Wert
> in die Tag-Map ein, nicht den bestellten.**

Gemeint ist die Kante des **schwarzen Quadrats** (inklusive schwarzem Rahmen,
ohne den weißen Rand außen herum). Ein Druckertreiber, der still auf 96 %
skaliert, erzeugt 4 % Distanzfehler — bei 1,5 m sind das 6 cm, und alles andere
sieht dabei völlig plausibel aus.

Tags flach aufkleben. Eine Wölbung von 2 mm über 100 mm Kantenlänge kippt die
geschätzte Orientierung um mehrere Grad.

### 0.3 Kalibrierboard

Ein ChArUco-Board ist einem Schachbrett vorzuziehen: es wird auch dann erkannt,
wenn es teilweise aus dem Bild ragt — und genau das braucht man, um die
Bildränder abzudecken.

```bash
PYTHONPATH=src .venv/bin/python3 tools/make_tag_sheet.py \
    --charuco --cols 7 --rows 5 --square-mm 30 --marker-mm 22 --out /tmp/board.svg
```

Auf steifen Karton kleben. Ein welliges Board ist der häufigste Grund für eine
Kalibrierung mit gutem RMS und schlechten Posen.

---

## 1. Stufe 1 — ohne Kamera am PC

Hier wird nichts fotografiert. Die Bilder werden gerendert, die wahren Posen
sind exakt bekannt, und jede Abweichung ist ein Rechenfehler.

### 1.1 Automatisierte Tests

```bash
PYTHONPATH=src .venv/bin/python3 -m unittest discover -s tests -t .
```

**Abbruchkriterium:** `OK`, und `test_tag_pipeline` darf **nicht** als
`skipped` erscheinen. Ein übersprungener Pipeline-Test heißt, dass cv2 fehlt —
dann prüft gerade niemand die Erkennungskette.

Einzeln, wenn etwas rot ist:

```bash
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_tag_geometry -v    # Mathematik
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_tag_map -v         # Platzierung
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_tag_pipeline -v    # Bild -> Pose
PYTHONPATH=src .venv/bin/python3 -m unittest tests.test_apriltag_source -v # Serverquelle
```

### 1.2 Die Werkzeuge einmal von Hand durchspielen

```bash
mkdir -p /tmp/e2e
PYTHONPATH=src .venv/bin/python3 tools/make_synthetic_scene.py \
    --mode tags --count 12 --out /tmp/e2e/bilder \
    --calibration /tmp/e2e/calib.json

PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source /tmp/e2e/bilder --calibration /tmp/e2e/calib.json --limit 1

PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.build_tagmap \
    --source /tmp/e2e/bilder --calibration /tmp/e2e/calib.json \
    --anchor 0 --out /tmp/e2e/tagmap.json
```

**Abbruchkriterium:** `build_tagmap` platziert alle Tags, die Liste „ohne Pfad
zum Anker" ist leer, und die Schließfehler liegen **unter 1 mm und 0,05°**. Bei
gerenderten Bildern gibt es keine Messunsicherheit — größere Werte sind ein
Rechen- oder Konventionsfehler, kein Rauschen. Zum Vergleich: auf dieser Szene
gemessen wurden 0,00–0,02 mm und 0,000°.

---

## 2. Stufe 2 — mit Webcam am PC

Ab hier gibt es Optik, Rauschen und Bewegungsunschärfe. Diese Stufe ist
optional, spart aber Zeit: jeden Fehler, den man hier findet, hätte man sonst
auf der Leiter unter der Decke gesucht.

```bash
# kalibrieren: Board langsam durchs Bild führen, auch in die Ecken
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source camera:0 --board charuco --cols 7 --rows 5 \
    --square-size-m 0.030 --marker-size-m 0.022 \
    --frame-id webcam --out data/calibration/webcam.json \
    --capture-to /tmp/e2e/webcam_bilder

# erkennen, mit Live-Overlay
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source camera:0 --calibration data/calibration/webcam.json \
    --tag-size-m 0.100 --overlay
```

Tasten beim Kalibrieren: `SPACE` nimmt auf, `C` rechnet und speichert, `Q`
bricht ab.

**Abbruchkriterien:**
- RMS-Reprojektionsfehler **unter 0,5 px**
- Abdeckung in x **und** y über **70 %** (steht am Ende der Kalibrierung). Eine
  Kalibrierung nur aus der Bildmitte hat gutes RMS und unbrauchbare Verzeichnung
  am Rand — also genau dort, wo die Module liegen.
- im Overlay sitzt das Achsenkreuz **im** Tag, nicht daneben, und kippt nicht,
  wenn man den Tag langsam dreht

---

## 3. Stufe 3 — auf dem Raspberry Pi mit Pi-Kamera

Die Stufe, auf die es ankommt. Alles davor war Vorbereitung.

### 3.1 Kamera überhaupt prüfen

Erst die Kamera, dann die Software. Auf Bookworm:

```bash
rpicam-hello --list-cameras     # Bullseye: libcamera-hello --list-cameras
rpicam-still -o /tmp/test.jpg   # Bullseye: libcamera-still
```

**Abbruchkriterium:** Die Kamera wird aufgelistet und `/tmp/test.jpg` ist scharf
und richtig belichtet. Ein unscharfes Bild wird durch keinen Algorithmus besser
— bei Fixfokus-Modulen entscheidet der Arbeitsabstand, bei Autofokus muss der
Fokus sitzen.

### 3.2 Umgebung auf dem Pi

`picamera2` kommt per apt, nicht per pip. Die venv muss die System-Pakete sehen:

```bash
sudo apt update && sudo apt install -y python3-picamera2
cd ~/ADP_flexible_Roboterzelle
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python3 -c "from picamera2 import Picamera2; print('picamera2 ok')"
.venv/bin/python3 -c "import cv2; print('aruco ok:', hasattr(cv2.aruco,'DICT_APRILTAG_36h11'))"
```

> Ohne `--system-site-packages` ist `picamera2` in der venv unsichtbar, die
> `SharedCamera` fällt beim Öffnen mit `ModuleNotFoundError` aus, und der Server
> bleibt in `Preoperational` — ohne dass das offensichtlich mit der Kamera zu
> tun hätte.

### 3.3 Kalibrieren mit der Pi-Kamera

> **Server vorher stoppen.** `tagloc.cli.calibrate` öffnet die Kamera selbst
> und exklusiv -- genau wie `SharedCamera` im laufenden Server. Beide
> gleichzeitig geht nicht (RSUSB/libuvc bzw. Picamera2 lassen nur einen
> offenen Zugriff zu). Solange kalibriert wird, gibt es also **keinen**
> Livestream im Frontend; `sudo systemctl stop opcua-server.service` vorher,
> `start` danach.

Der Pi hat meist keinen Bildschirm. Zwei Wege, hier für den Decken-Pi
(`--source picamera`) — für den Hand-Pi `--source realsense --resolution 640 480`
statt `--source picamera --resolution 2028 1520` einsetzen, sonst identisch:

**Weg A — mit Display (X11-Weiterleitung oder VNC), interaktiv:**

```bash
ssh -X pi@pi-decke
cd ~/ADP_flexible_Roboterzelle
sudo systemctl stop opcua-server.service
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source picamera --resolution 2028 1520 \
    --board charuco --cols 7 --rows 5 --square-size-m 0.030 --marker-size-m 0.022 \
    --frame-id cam_ceiling --out data/calibration/cam_ceiling.json \
    --capture-to data/calibration/aufnahmen_decke
sudo systemctl start opcua-server.service
```

**Weg B — ohne Display, zweistufig.** Robuster, und mit Nebennutzen: die Bilder
bleiben liegen, die Kalibrierung ist am PC exakt reproduzierbar.

```bash
# auf dem Pi: Server stoppen, dann nur aufnehmen
sudo systemctl stop opcua-server.service
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source picamera --resolution 2028 1520 --no-gui \
    --board charuco --capture-to data/calibration/aufnahmen_decke \
    --frame-id cam_ceiling --out data/calibration/cam_ceiling.json
sudo systemctl start opcua-server.service

# am PC nachrechnen (identischer Rechenkern)
scp -r pi@pi-decke:~/ADP_flexible_Roboterzelle/data/calibration/aufnahmen_decke /tmp/
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate \
    --source /tmp/aufnahmen_decke --board charuco \
    --frame-id cam_ceiling --out data/calibration/cam_ceiling.json
scp data/calibration/cam_ceiling.json pi@pi-decke:~/ADP_flexible_Roboterzelle/data/calibration/
```

Beim Aufnehmen: **mindestens 15, besser 20 Ansichten**, Board jeweils gekippt
(±30°) und einmal durch alle vier Bildecken geführt. Ein Board, das immer
parallel zur Bildebene steht, lässt die Brennweite unbestimmt — die Kalibrierung
konvergiert trotzdem und liefert falsche Werte.

**Abbruchkriterium:** RMS unter **0,5 px**, Abdeckung x und y über **70 %**. Die
Datei heißt exakt `data/calibration/<frame_id>.json`, also `cam_ceiling.json`
für die Decke und `cam_flange.json` für den Flansch — unter diesem Namen sucht
der Server sie.

### 3.4 Erkennung live prüfen

Server bleibt gestoppt (siehe 3.3) — auch hier öffnet das CLI-Tool die Kamera
exklusiv. Für den Hand-Pi wieder `--source realsense --resolution 640 480`.

```bash
# mit Display
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --resolution 2028 1520 \
    --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --overlay

# ohne Display: ein Frame, Ergebnis als Text und JSON
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --resolution 2028 1520 \
    --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --limit 1 --json /tmp/posen.json
```

### 3.5 Die zwei Messungen, die wirklich etwas beweisen

Eine Pose sieht immer plausibel aus. Diese beiden Tests zeigen, ob sie stimmt —
beide brauchen nur einen Zollstock.

**Messung A — Maßstab.** Deckt falsche Tag-Größe und falsche Kalibrierung auf.
Zwei Tags in bekanntem Abstand auf ein Brett kleben, Mitte zu Mitte messen
(z. B. exakt 400 mm), beide in einem Bild erfassen und den Abstand aus den
gemessenen Posen rechnen:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --limit 1 --json /tmp/zwei.json

PYTHONPATH=src .venv/bin/python3 - <<'PY'
import json, math
poses = json.load(open("/tmp/zwei.json"))["poses"]
a, b = poses[0]["pose"]["position"], poses[1]["pose"]["position"]
print("gemessener Abstand:", round(math.dist(a, b) * 1000, 1), "mm")
PY
```

**Abnahme:** Abweichung vom Sollwert unter **1 %**. Ist sie größer und
systematisch, stimmt die eingetragene Tag-Größe nicht — sie geht linear in die
Distanz ein.

**Messung B — Wiederholbarkeit.** Deckt Rauschen, Unschärfe und schlechte
Beleuchtung auf. Tag fest stehen lassen, 50 Frames messen, Streuung betrachten:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera --calibration data/calibration/cam_ceiling.json \
    --tag-size-m 0.100 --limit 50 | tee /tmp/serie.txt
```

**Abnahme Layer 1** (Decke, ca. 2 m, 100-mm-Tags): Standardabweichung seitlich
unter **2 mm**, in der Tiefe unter **10 mm**, Orientierung unter **1°**.
**Abnahme Layer 2** (Flansch, ca. 0,3 m, 50-mm-Tags): seitlich unter **0,5 mm**,
Tiefe unter **2 mm**, Orientierung unter **0,5°**.

Die Tiefe ist bei einem einzelnen Marker immer die schlechteste Achse. Das ist
Physik, kein Fehler: die Tiefe folgt aus der scheinbaren Größe, und die ändert
sich über die Distanz nur langsam. Genau deshalb steht `ambiguous` im Ergebnis —
schaut man zu flach auf den Tag, kippt die Lösung.

### 3.6 Tag-Map aufnehmen

Jetzt die Tags im Raum in ein gemeinsames Koordinatensystem bringen. Bilder so
aufnehmen, dass **jedes Bild mindestens zwei Tags zeigt** und die Paare eine
Kette bis zum Anker-Tag bilden:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.build_tagmap \
    --source data/aufnahmen_zelle \
    --calibration data/calibration/cam_ceiling.json \
    --tag-map config/tagmap.json --anchor 0 --out config/tagmap.json
```

Danach die Datei von Hand nachbearbeiten: Rollen setzen (`world` fuer die vier
Welttags, `robot` fuer die Tags am Roboter, sonst `module`), bei den
beweglichen Tags `poseInWorld` auf `null` setzen, Modulzuordnung
(`moduleId`, `instanceId`) und den
CAD-Versatz `tagToModule` eintragen. Vorlage:
[`config/tagmap.example.json`](../../config/tagmap.example.json), Feldbeschreibung
in [`apriltag-referenz.md`](apriltag-referenz.md) Abschnitt 5.

#### Kleiner Aufbautest: ein Welttag, ein Modul

Für den ersten Funktionstest braucht es die vier Welttags **nicht**. Ein
Welttag und ein Modultag genügen, und `build_tagmap` entfällt dabei ganz:
liegt der Welttag im Ursprung mit Identitätspose, *ist* er das Welt-KS, es
gibt also nichts einzumessen.

Vorlage dafür:
[`config/tagmap.test.example.json`](../../config/tagmap.test.example.json) — nach
`config/tagmap.json` kopieren, Tag-IDs und die **gemessenen** Kantenlängen
anpassen, fertig:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.detect \
    --source picamera \
    --calibration data/calibration/cam_ceiling.json \
    --tag-map config/tagmap.json --save-overlay overlays/
```

Beim Start meldet `validate_tag_map` zwei Beanstandungen — „Erwartet 4 Welttags
… gefunden 1" und „Kein Tag mit der Rolle 'robot'". Das ist **kein Abbruch**,
sondern genau der Hinweis, dass der Endausbau noch fehlt; die Erkennung läuft
vollständig. Die zweite Meldung verschwindet, sobald ein Robotertag in der
Karte steht.

Was dieser Aufbau **nicht** liefert: `cameraSpreadM` bleibt bei einem einzigen
Welttag immer 0. Die Zahl misst die Uneinigkeit *zwischen* Welttags, es gibt
hier also keine Gegenprobe — die Genauigkeit hängt vollständig an diesem einen
Tag und an seiner eingetragenen Größe. Aussagekräftig wird der Wert erst mit
dem zweiten Welttag.

Für die Handkamera gilt ohne gemessene Hand-Auge-Datei weiterhin: Welttag und
Modul müssen im **selben Bild** liegen. Das Ankern (Abschnitt 1.4 der
[Konzeptdoku](apriltag-lokalisierung.md)) greift erst mit
`data/handeye/cam_flange.json`.

**Abbruchkriterium:** Schließfehler unter **3 mm** und **0,5°**. Größere Werte
heißen: Kalibrierung oder eine eingetragene Tag-Größe stimmt nicht. Eine Karte,
die sich nicht schließt, ist wertlos — dann zurück zu Schritt 3.3.

### 3.6a Hand-Auge kalibrieren und ankern (nur Handkamera / Layer 2)

Ohne diesen Schritt liefert die Handkamera Posen im Kamera-KS, sobald kein
Welttag im selben Bild liegt (siehe oben). Für `frameId: world` ohne
Welttag-Zwang braucht sie `data/handeye/cam_flange.json`:

```bash
PYTHONPATH=src .venv/bin/python3 -m tagloc.cli.calibrate_handeye \
    --samples fahrt/aufnahmen.json \
    --calibration data/calibration/cam_flange.json \
    --out data/handeye/cam_flange.json
```

Aufnahme davor: einen Tag ortsfest hinlegen, den Roboter mindestens ein
Dutzend deutlich verschiedene Posen anfahren lassen (**um mehrere Achsen
drehen**), je Pose ein Bild und `T_base_flansch` notieren — Format und Ablauf
in [`apriltag-referenz.md`](apriltag-referenz.md) Abschnitt 9.3a.

**Abnahmekriterien:**

| Prüfung | Erwartung |
|---|---|
| `target_spread` (Ausgabe von `calibrate_handeye`) | unter `SPREAD_WARNING_M` (5 mm); sonst neu aufnehmen mit stärker variierten Posen |
| Job ohne Welttag im Bild, mit Hand-Auge-Datei | `frameId = world`, Payload trägt `anchorWorldTagId` und `attributes.cameraPoseOrigin = "robot_pose"` |
| Welttag wieder ins Bild bringen | `cameraPoseOrigin` wechselt zurück auf `"world_tags"`, `anchorDriftM`/`anchorDriftDeg` zeigen die Abweichung zum Anker |
| Hand-Auge-Datei entfernt/verschoben | Server startet weiterhin, Layer 2 fällt auf Kamera-KS zurück (kein Absturz) |

### 3.7 Server starten und über OPC UA messen

```bash
PYTHONPATH=src .venv/bin/python3 -m vision_server.server
```

Im Log muss stehen:

```
Vision-Identitaet: vision-ceiling-01 (Rahmen cam_ceiling)
VisionSystem 'VisionMachine' als ns=6;s=VisionMachine unter Machines angelegt
Livestream aus Profil 'apriltag' mit Overlay
Vision-System 'VisionMachine' bereit (Profile apriltag, calibration, hello_world)
```

> Steht dort **„bleibt in Preoperational"**, hat eine Quelle nicht geöffnet.
> Fast immer fehlt die Kalibrierdatei, oder `picamera2` ist in der venv
> unsichtbar (siehe 3.2). Die Ursache steht eine Zeile darüber im Log.

Jobs auslösen — zuerst die Bereitschaftsprüfung, dann die Messung:

```bash
PYTHONPATH=src .venv/bin/python3 - <<'PY'
import asyncio
from asyncua import Client, ua

async def main():
    async with Client("opc.tcp://pi-decke:4840/freeopcua/server/") as client:
        vis = await client.get_namespace_index("http://launch-rm.de/vision")
        mv = await client.get_namespace_index("http://opcfoundation.org/UA/MachineVision")
        machine = client.get_node(ua.NodeId("VisionMachine", vis))
        sm = await machine.get_child(
            [f"{mv}:VisionStateMachine", f"{mv}:AutomaticModeStateMachine"]
        )
        start = await sm.get_child(f"{mv}:StartSingleJob")
        for recipe in ("calibration", "apriltag"):
            job_id, error = await sm.call_method(start, "", "", recipe, "", [])
            print(f"{recipe:>12}: JobId={job_id} Error={error}")
            await asyncio.sleep(3.0)
        results = await machine.get_child([f"{mv}:ResultManagement", f"{mv}:Results"])
        for node in await results.get_children():
            print((await node.read_browse_name()).Name)

asyncio.run(main())
PY
```

**Abnahmekriterien:**

| Prüfung | Erwartung |
|---|---|
| `calibration`-Job | `attributes.message` nennt Kalibrier-Id, Auflösung, RMS und Tag-Map |
| `apriltag`-Job, Error | `0` |
| `resultState` | `0` |
| `detections` | eine Detektion je sichtbarem Modul-Tag |
| `frameId` | `world`, sobald ein Referenz-Tag im Bild ist; sonst `cam_ceiling` |
| `frameConvention` | `z_forward_x_right_y_down` |
| `configurationId` | enthält Tag-Familie, Kalibrier- **und** Tag-Map-Identität |
| `attributes` | mindestens `tagId`, `reprojErrorPx`, `ambiguous`, `sampleCount`, `recipeId`, `source`, `role`; zusätzlich `referenceTagId`/`referencePosition`/`referenceOrientation`/`referenceDistanceM` sobald ein Welttag als Bezug bekannt ist, `worldTagIds`/`cameraSpreadM`/`cameraSpreadDeg`/`cameraPoseOrigin` sobald die Kamera lokalisiert ist, `anchorWorldTagId`/`anchorDriftM`/`anchorDriftDeg` mit Hand-Auge-Anker — volle Liste [`apriltag-referenz.md`](apriltag-referenz.md) Abschnitt 10.2 |
| `sampleCount` | gleich `samples_per_job` des Profils (Decke 3, Flansch 5) |
| Kein Tag im Bild | `resultState = 5` (`DETECTION_FAILED`), **kein** leeres Erfolgsergebnis |
| `Stop` während des Jobs | `resultState = 7` (`CANCELLED`) |

Und die Gegenprobe, die den ganzen Aufbau bindet: **die Pose aus dem Payload muss
mit der aus `tagloc.cli.detect` gemessenen übereinstimmen** (unter 1 mm). Weicht
sie ab, benutzen Stream und Job unterschiedliche Kalibrierungen — dann
`configurationId` vergleichen.

---

## 4. Stufe 4 — im Frontend

Der Livestream ist das Debugwerkzeug für alles oben. Er zeigt nicht nur, dass ein
Bild ankommt, sondern was die Erkennung darin sieht.

### 4.1 Die Knoten

| Knoten | NodeId | Richtung | Inhalt |
|---|---|---|---|
| Livestream | `ns=<vision>;s=VisionMachine.LatestCameraFrame` | nur lesen | Base64-JPEG, ca. 5 fps |
| Modusauswahl | `ns=<vision>;s=VisionMachine.CameraStreamMode` | **beschreibbar** | `off`, `apriltag`, `calibration` |

`<vision>` ist der Index von `http://launch-rm.de/vision` — auf dem Pi-Server
`4`, im eigenständigen Vision-Server `3`. **Den Index immer zur Laufzeit über
`get_namespace_index` holen, nie hart eintragen.**

### 4.2 Die drei Modi

| Wert | Anzeige | Was markiert wird |
|---|---|---|
| `off` | „Rohbild" | nichts — das unveränderte Kamerabild |
| `apriltag` | „AprilTags markieren" | Umriss jedes erkannten Tags, **Achsenkreuz im Tag** (X rot, Y grün, Z blau), ID, Modulname aus der Karte, Distanz, Reprojektionsfehler. Mehrdeutige Posen orange statt grün |
| `calibration` | „Kalibrierboard markieren" | die gefundenen Board-Ecken und die Abdeckung — damit sieht man, was die Kalibrierung sieht |

Umschalten ist ein einfacher Schreibzugriff:

```python
mode_node = client.get_node(ua.NodeId("VisionMachine.CameraStreamMode", vis))
await mode_node.write_value("calibration")
```

Ein ungültiger Wert hält den Stream **nicht** an — er fällt auf `apriltag`
zurück. Das ist Absicht: das Bild ist wichtiger als die Markierung.

### 4.3 Was im Frontend noch zu tun ist

Die Serverseite ist fertig; das Frontend (`webskillcomposition`) braucht noch die
Auswahl. Der Vertrag ist genau der oben: ein Dropdown mit den drei Werten, das
beim Wechsel `CameraStreamMode` schreibt. Der Livestream-Knoten selbst bleibt
unverändert — die bestehende `subscribeNode`-Infrastruktur trägt weiter. Die
Beschriftungen stehen serverseitig in `tagloc.modes.OVERLAY_MODE_LABELS`.

### 4.4 Abnahme

- Bild kommt an, ca. 5 fps, keine über Minuten wachsende Verzögerung
- Umschalten auf `apriltag` zeigt innerhalb von rund einer Sekunde Markierungen
- das Achsenkreuz sitzt **im** Tag und dreht sich mit, wenn man den Tag dreht
- Umschalten auf `off` zeigt sofort wieder das Rohbild
- ein laufender `apriltag`-Job unterbricht den Stream **nicht** — beide lesen aus
  derselben `SharedCamera`
- im Serverlog erscheint keine Meldung „Event-Loop … ms blockiert"

> Zum letzten Punkt: Das Overlay rechnet höchstens alle `overlay_interval_s`
> (Standard 0,5 s) neu und zeichnet dazwischen das letzte Ergebnis weiter. Bei
> fest montierter Kamera sieht man davon nichts. Erscheinen trotzdem
> Loop-Warnungen: `overlay_interval_s` erhöhen oder Stream-Auflösung senken.

---

## 5. Kurzreferenz Fehlersuche

| Symptom | Was man zuerst prüft |
|---|---|
| Distanz systematisch um festen Faktor daneben | gedruckte Tag-Größe ≠ eingetragene `sizeM` (siehe 0.2) |
| Alle Posen falsch, Erkennung aber stabil | Kalibrierung gehört zu anderer Auflösung — die Quelle meldet beide Größen |
| Pose kippt zwischen zwei Lagen | `ambiguous: true`, zu flacher Blickwinkel |
| Posen am Bildrand viel schlechter | Kalibrierabdeckung zu gering — nicht das RMS ansehen, die Abdeckung |
| `build_tagmap` findet weniger Tags als aufgenommen | kein Pfad zum Anker; es fehlt ein Bild mit zwei bereits verbundenen Tags |
| Server bleibt in Preoperational | fehlende Kalibrierdatei oder `picamera2` unsichtbar (3.2); Ursache steht im Log |
| Livestream weg, obwohl konfiguriert | keine geöffnete Quelle hält eine Kamera |
| Stream läuft, aber ohne Markierung | Quelle ohne geladenen Detektor/Kalibrierung; Log sagt „ohne Overlay" |
| `DETECTION_FAILED` trotz sichtbarem Tag | falsche Tag-Familie, oder alle Tags über `max_reproj_error_px` verworfen — mit `-v` im CLI sichtbar |
