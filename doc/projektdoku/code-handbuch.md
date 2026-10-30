# Code-Handbuch

Stand: 30.10.2026 — dieses Repo auf Branch `apriltag` (`d55df91`), webskillcomposition auf
Branch `Ungetestet` (`5812275`). Das Handbuch beschreibt den Code, wie er ist. Was daran
aufgeräumt werden soll, steht in [`altlasten-zelle.md`](altlasten-zelle.md) und
[`altlasten-wsc.md`](altlasten-wsc.md); der zugehörige Plan ist
[`arbeitsplaene/aufraeumen-und-code-handbuch.md`](arbeitsplaene/aufraeumen-und-code-handbuch.md).

**Wofür.** Ein Nachschlagewerk für alle im Team: Zu jeder Datei unseres Codes steht hier,
wozu sie da ist, wie man sie startet oder wer sie aufruft, wo sie im Datenfluss sitzt und
welche Fach-MD die Details hat. Schnittstellen (OPC-UA-Knoten, Methoden, Payloads,
Dateiformate) werden **nicht** wiederholt, sondern verlinkt — maßgeblich sind die Fach-MDs.

**Was „unser Code“ ist.**

- **Teil A — dieses Repo** (`ADP_flexible_Roboterzelle`): vollständig. Pfade relativ zur Repo-Wurzel.
- **Teil B — webskillcomposition** (Uni-Repo): nur, was unsere Gruppe gegenüber `dev`
  beigetragen hat (Vergleich `origin/dev..Ungetestet`, Abzweig `d824975`). Code, der schon in
  `dev` stand, wird nicht beschrieben; bei geänderten Dateien steht „aus dev, ergänzt um …“.
  Pfade relativ zur Wurzel des webskill-Repos (`frontend/…`, `backend/…`).

## Gesamtbild

```
Deckenkamera ── Decken-Pi (Layer 1) ─┐
                                     ├─ vision_server (OPC UA, OPC 40100 + Teil 10)
Handkamera ──── Hand-Pi  (Layer 2) ──┘   rechnet mit tagloc (Kalibrierung, Tags, Posen)
                                                │ OPC UA
                                                ▼
                 webskillcomposition-Backend (FastAPI, asyncua) ── OPC UA ── Roboter
                                                │ WebSocket / REST
                                                ▼
                 Frontend (React, three.js): Zellmodule, Autolocate, Layer 2,
                 Hand-Auge, Einmessen, Pick & Place, Tag-Tool
                 (den Livestream holt der Browser direkt per MJPEG vom Pi)
```

- `src/tagloc/` ist der Rechenkern ohne OPC UA: Kamera-Kalibrierung, Tag-Erkennung,
  Posenschätzung, Tag-Map, Lokalisierung, Hand-Auge, Bezugskanten. Er hat eigene
  Kommandozeilenbefehle (`python -m tagloc.cli.*`) und importiert nie `vision_server`.
- `src/vision_server/` ist die OPC-UA-Hülle auf den Pis: Adressraum, Jobs, geteilte Kamera,
  Livestream, Sitzungen (Kalibrierung, Hand-Auge, Layer 2, Zellen-Überwachung, Einmessen),
  Discovery.
- Im Frontend liegt die Fachlogik in `frontend/src/features/cell-modules/model/`, die
  Oberfläche in `frontend/src/features/cell-modules/components/`, die Server-Anbindung in
  `frontend/src/features/opcua-server/`.

## Schnellstart

Dieses Repo (Python 3.12, aus der Repo-Wurzel):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t .   # alle Tests
PYTHONPATH=src .venv/bin/python -m tagloc.cli.detect --help            # Rechenkern ohne Server
PYTHONPATH=src .venv/bin/python -m vision_server --help                # Server, Entwicklungsstart
```

Auf den Pis läuft `python3 -m vision_server.server` mit Presets je Hostname, mDNS und LDS
(Teil A2, „Start & Konfiguration“, und [`apriltag-e2e-test.md`](apriltag-e2e-test.md)).

webskillcomposition: Einrichtung laut `docs/development.md` im webskill-Repo
(Backend `uv run main.py`, Frontend `npm install` und `npm run dev`). Tests: im Ordner
`frontend/` `npx tsc --noEmit` und `npx vitest run`, im Ordner `backend/` `uv run pytest`.

## Inhalt

- **Teil A1 — tagloc, CLI und Hilfsskripte:** `src/tagloc/`, `src/tagloc/cli/`, `src/jobs/`, `src/OPCUA/`, `src/ua_program/`, `tools/`, `config/`, `data/`, Repo-Wurzel, Testübersicht
- **Teil A2 — Vision-Server:** Start & Konfiguration, Adressraum, Jobs, Erkennungsquellen, Kamera & Livestream, Sitzungen, Discovery, Werkzeuge
- **Teil B1 — Zellmodule, Modelle:** Koordinaten & Szenenmodell, Modulkatalog, Tag-Platzierung, Layer 1, Zellen-Überwachung, Layer 2, Einmessen, Hand-Auge, Kamera-1-Kalibrierung, Pick & Place, Freigaben, Kollisionsprüfung, Zustand
- **Teil B2 — Zellmodule, Oberfläche:** die Bedienbereiche zu den Modellen aus B1
- **Teil B3 — OPC-UA-Anbindung, Tag-Tool, gemeinsame Bausteine und Backend**

Begriffe, die überall vorkommen: **KS** = Koordinatensystem. **T_a_b** = Pose von b in a
(Meter, Radiant). **Welttag** = fester AprilTag am Zellenrand, definiert das Welt-KS.
**Layer 1** = Messung mit der Deckenkamera, **Layer 2** = Nachmessung mit der Handkamera am
Roboter.

---

# Teil A — ADP_flexible_Roboterzelle

## Teil A1 — tagloc, CLI und Hilfsskripte

Geltungsbereich: `src/tagloc/` mit `cli/`, `src/jobs/`, `src/OPCUA/`, `src/ua_program/`, `tools/`, `config/`, `data/`, die Befehle der Repo-Wurzel und die Testübersicht. `src/vision_server/` gehört zu einem anderen Teil; hier steht nur, wo `tagloc` dort benutzt wird. Stand: Branch `apriltag` (`d55df91`).

Begriffe: **KS** = Koordinatensystem. **T_a_b** = Pose von b, ausgedrückt in a (Meter, Radiant). **Welttag** = fester AprilTag am Zellenrand; er definiert das Weltkoordinatensystem. **Modultag** = Tag auf einem beweglichen Modul. **Roboter** = Modul mit Rolle `robot`; er wird wie jedes andere Modul gemessen.

### `src/tagloc/`

#### Wie die Module zusammenspielen

```
Kalibrierung → Erkennung → Pose → Tag-Map → Lokalisierung → Hand-Auge → Layer 2 / Bezugskanten
```

1. **Kalibrierung** (`boards.py`, `lens.py`, `calibration.py`): Kameramatrix und Verzeichnung aus Brettaufnahmen. Ergebnis: eine Datei je Kamera.
2. **Erkennung** (`frames.py`, `detector.py`): Bild → Tags mit ihren Ecken (`TagObservation`).
3. **Pose** (`pose.py`): Ecken entzerren, Pose je Tag `T_cam_tag` mit Reprojektionsfehler und Mehrdeutigkeit (`TagPose`).
4. **Tag-Map** (`tagmap.py`): ordnet jeder Tag-ID eine Rolle zu (`world`, `module`, `robot`). Nur Welttags haben eine feste Weltpose und dienen als Anker.
5. **Lokalisierung** (`localize.py`): Kamerapose `T_world_cam` aus den sichtbaren Welttags, Modulposen aus den Modultags. Ohne sichtbaren Welttag bleibt das Ergebnis im Kamera-KS.
6. **Hand-Auge** (`handeye.py`): Die Handkamera sitzt starr am Roboter, `T_flansch_cam` wird einmal bestimmt. Ein Welttag verankert die Roboterbasis (`RobotAnchor`); danach trägt die Kinematik die Kamerapose von Modul zu Modul.
7. **Layer 2 / Bezugskanten** (`reference_edges.py`, `reference_overlay.py`): Tag-Position auf dem Modul mit der Handkamera über zwei physische Kanten einmessen.

Layer 1 ist die Deckenkamera (`pi-decke`), Layer 2 die Handkamera (`pi-hand`); Quelle: [apriltag-lokalisierung.md](apriltag-lokalisierung.md), Abschnitt 1.3.

Schichtung nach Abhängigkeiten, von `tests/test_layering.py` geprüft: `identity` und `modes` brauchen nur die Standardbibliothek. `geometry`, `observations`, `calibration`, `tagmap`, `localize` und `handeye` importieren kein `cv2`. `detector`, `pose`, `boards`, `overlay` und `frames` importieren `cv2` erst beim Aufruf. Kein Modul im Paket importiert `vision_server`. Nicht im Test erfasst sind `lens.py` und `reference_edges.py`; beide importieren `cv2` nur in Funktionen.

Benutzt wird das Paket außerdem von (nur Verweis): `src/vision_server/detection/apriltag.py` (Erkennungsquelle, größter Nutzer), `stream_overlay.py` und `camera_stream.py` (Overlay, Modi), `handeye_session.py`, `calibration_session.py`, `cellwatch.py`, `layer2/plan.py`, `tagreference.py`, `measureboard.py`, `measuretag.py`, `runner.py`, `camera.py`, `src/vision_server/tools/diagnose_board.py`.

**Zweck**: Bibliothek für die AprilTag-Lokalisierung. Sie rechnet Kamerakalibrierung, Tag-Erkennung und Posen, Tag-Map und Modulposen in einem gemeinsamen Weltkoordinatensystem. Netzwerk und OPC UA kommen im Paket nicht vor. Die Befehle unter `cli/` nutzen dieselben Funktionen ohne Server.

**Einstieg**: Aus der Repo-Wurzel, mit der venv (Anleitung unter „Repo-Wurzel“ weiter unten) und `src` auf dem Pfad:

```
PYTHONPATH=src .venv/bin/python -c "from tagloc.localize import localize_camera; print('ok')"
```

Lesereihenfolge für Einsteiger: `geometry.py`, `observations.py`, `calibration.py`, `detector.py` und `pose.py`, `tagmap.py`, `localize.py`, `handeye.py`. `overlay.py` und `reference_edges.py` danach.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/tagloc/__init__.py` | Paketdocstring mit Schichtung und Importregeln. Keine Exporte. | — |
| `src/tagloc/geometry.py` | Posen als 4×4-Matrizen in numpy, Verkettung, Inversion, Mittelung, Quaternionen (xyzw), Rodrigues ohne cv2 | `compose`, `invert`, `from_rvec_tvec`, `to_position_quaternion`, `average_poses`, `pose_to_dict`, `pose_from_dict` |
| `src/tagloc/observations.py` | Datentypen zwischen Bild und Mathematik: Tag im Bild ohne Pose, Tag mit Pose | `TagObservation`, `TagPose` (`is_ambiguous`, `is_usable`), `AMBIGUITY_THRESHOLD` |
| `src/tagloc/identity.py` | Kennung einer Konfigurationsdatei (`<name>#<mtime>`), nur Standardbibliothek. Der Server nutzt sie für die `configurationId`. | `file_identity`, `calibration_identity`, `tag_map_identity` |
| `src/tagloc/modes.py` | Overlay-Modi des Livestreams und ihre Beschriftungen, nur Standardbibliothek | `OVERLAY_MODES`, `DEFAULT_OVERLAY_MODE`, `normalise_mode` |
| `src/tagloc/calibration.py` | Kamerakalibrierung als JSON lesen und schreiben, Auflösung prüfen und skalieren, Platzhalter ohne Messung | `CameraCalibration`, `load_calibration`, `save_calibration`, `default_calibration`, `check_resolution`, `scale_to_resolution` |
| `src/tagloc/lens.py` | Alle linsenmodellabhängigen OpenCV-Aufrufe an einer Stelle: Pinhole (Brown-Conrady) und Fisheye (Kannala-Brandt) | `undistort_points`, `project_points`, `undistortion_maps`, `calibrate` |
| `src/tagloc/boards.py` | Schachbrett und ChArUco: finden, auch teilweise sichtbar, Abdeckung, Brettpose, Kalibrierung aus Aufnahmen | `BoardSpec`, `detect_board`, `detect_partial_board`, `diagnose_board`, `compute_coverage`, `estimate_board_pose`, `calibrate_from_samples` |
| `src/tagloc/detector.py` | Tag-Erkennung hinter einem Protokoll: `cv2.aruco` (Standard) oder `pupil_apriltags` (Vergleich). Die Pose berechnet `pose.py`. | `TagDetector`, `ArucoTagDetector`, `PupilAprilTagDetector`, `build_detector`, `apply_detector_parameters`, `ARUCO_DICTIONARIES` |
| `src/tagloc/pose.py` | Pose je Tag aus den Ecken. Entzerrt vor dem Lösen, verwirft Tags über der Reprojektionsgrenze, löst gemeinsam über alle Ecken mehrerer Welttags | `estimate_tag_pose`, `estimate_tag_poses`, `solve_camera_from_world_tags`, `JointCameraPose`, `undistort_corners`, `poses_by_tag` |
| `src/tagloc/tagmap.py` | Tag-Map: Rollen, Welttag-Posen, CAD-Versatz der Modultags, Laden und Speichern, Platzieren von Tags aus gemeinsamen Sichtungen, Schließfehler | `TagMap`, `TagEntry`, `load_tag_map`, `tag_map_from_json`, `save_tag_map`, `place_tags`, `residuals`, `with_world_poses`, `nearest_world_tag`, `validate_tag_map` |
| `src/tagloc/localize.py` | Kamerapose aus Welttags, Modulposen aus Modultags, Zusammenführung von Decken- und Handmessung, Anker für die Handkamera, Drift-Prüfung | `localize_camera`, `locate_modules`, `merge_samples`, `merge_by_module`, `merge_locations`, `anchor_from_localization`, `localize_camera_from_anchor`, `anchor_drift`, `expected_but_missing` |
| `src/tagloc/handeye.py` | Hand-Auge-Kalibrierung (`T_flansch_cam`), Anker der Roboterbasis, robuste Lösung und Verfeinerung gegen Brettecken. Ohne cv2, nur numpy und Standardbibliothek. | `solve_hand_eye`, `solve_hand_eye_robust`, `refine_hand_eye_robust`, `leave_one_out_errors`, `anchor_world_base`, `camera_pose_from_robot`, `load_hand_eye`, `save_hand_eye` |
| `src/tagloc/reference_edges.py` | Tag-Position über Bezugskanten einmessen: Kantensuche im Bild, RANSAC-Geraden, Korrektur in der Tag-Ebene, Mittelung über Bilder, Kameraplanung | `measure_reference_lines`, `fit_in_plane_correction`, `average_corrections`, `plan_reference_view`, `catalog_rpy_deg`, `ReferenceLine`, `LineMeasurement` |
| `src/tagloc/reference_overlay.py` | Zeichnet die Zwischenschritte der Kantensuche (Debug-Ansicht). Rechnet nichts. | `draw_reference_debug`, `scale_debug`, `effective_layers`, `LAYER_KEYS` |
| `src/tagloc/overlay.py` | Zeichnet Ergebnisse ins Bild: schlanker Stream-Modus, ausführliche Variante mit Achsenkreuz für die CLI, Statusleiste, Fenster. Rechnet nichts. | `draw_tag_boxes`, `draw_tag_overlay`, `draw_board_overlay`, `draw_calibration_caption`, `draw_status_bar`, `Window`, `has_display`, `summarise` |
| `src/tagloc/frames.py` | Bildquellen: Webcam, Pi-Kamera (Picamera2), RealSense, Bildordner, Einzelbild. Löst `--source` auf. Der Server benutzt diesen Weg nicht, er hat `SharedCamera`. | `open_source`, `ImageFolderSource`, `to_gray`, `image_size` |

**Ablauf**

1. **Kalibrierung.** `boards.detect_board` findet das Brett in jedem Bild. `calibrate_from_samples` rechnet mit `lens.calibrate` (Pinhole: `cv2.calibrateCamera`, Fisheye: `cv2.fisheye.calibrate`). `save_calibration` schreibt die Datei.
2. **Erkennung.** `frames.open_source` liefert Bilder, `to_gray` macht Graustufen, `build_detector(...).detect(gray)` liefert `TagObservation`s mit den Ecken im Uhrzeigersinn ab oben links.
3. **Pose.** `estimate_tag_poses` entzerrt die Ecken, nimmt die Tag-Größe aus der Karte oder aus `default_size_m`, löst beide IPPE-Lösungen, verfeinert sie und nimmt die mit dem kleineren Reprojektionsfehler. Tags über `max_reprojection_error_px` fallen weg.
4. **Tag-Map.** `load_tag_map` liest Schema `/2`; das alte `/1` wird migriert, nicht abgelehnt. Ein Welttag ohne Weltpose ist kein Anker (`TagEntry.is_reference`).
5. **Kamera.** `localize_camera` liefert `CameraLocalization`: `pose_world_cam`, den nächsten Welttag (`primary_tag_id`), die Streuung `spread_m` und `solver` (`joint`, `single` oder `average`). Ohne Welttag: `None`.
6. **Module.** `locate_modules` rechnet je Modultag `pose = T_world_cam · T_cam_tag · T_tag_module`. Ohne Kamerapose entfällt der erste Faktor, ohne Map-Eintrag der letzte. Unbekannte Tags bleiben als `TAG-<id>` erhalten.
7. **Hand-Auge und Anker.** `solve_hand_eye` bestimmt `T_flansch_cam` aus Roboterposen und Tag-Posen (Verfahren nach Park und Martin). `anchor_from_localization` berechnet `T_world_base = T_world_cam · inv(T_flansch_cam) · inv(T_base_flansch)`. Danach trägt `localize_camera_from_anchor` die Pose ohne Welttag weiter; `anchor_drift` prüft sie, sobald wieder ein Welttag sichtbar ist.
8. **Zusammenführen.** `merge_locations(decke, hand)` übernimmt pro Modul die Messung des Rangs `SOURCE_PRECEDENCE` (Handkamera vor Decke), bei Gleichstand die höhere Konfidenz. Die Funktion ist rein; zwischen den Pis gibt es keine zweite Verbindung.
9. **Bezugskanten.** `measure_reference_lines` misst die Kanten im Tag-KS, `fit_in_plane_correction` bestimmt die starre 2D-Korrektur in der Tag-Ebene, `average_corrections` mittelt über mehrere Bilder und meldet die Streuung. `plan_reference_view` plant vorab eine Kameraposition.

**Konfiguration & Dateien**

- **Umgebungsvariablen:** keine eigenen. `DISPLAY` oder `WAYLAND_DISPLAY` entscheiden in `overlay.has_display()`, ob ein Fenster geöffnet wird.
- **Schemata** (Namen aus dem Code): Kalibrierung `wsc.vision.calibration/2` (`/1` wird als Pinhole gelesen), Tag-Map `wsc.vision.tagmap/2` (`/1` wird migriert), Hand-Auge `wsc.vision.handeye/1`. Die Feldlisten stehen in [apriltag-referenz.md](apriltag-referenz.md), Abschnitte 4, 5 und 7.
- **Pfade:** Die Bibliothek kennt keine festen Dateipfade. Die CLI-Beispiele und `src/jobs/calibrate.py` nutzen `data/calibration/<frame_id>.json`, `data/handeye/<frame_id>.json` und `config/tagmap.json`.
- **Konstanten** (Auswahl):
  - `observations.AMBIGUITY_THRESHOLD = 0.6`: ab hier gilt `TagPose.is_ambiguous`. Mehrdeutig heißt nicht falsch, aber die Pose nicht ungeprüft verwenden.
  - `calibration.PLACEHOLDER_CALIBRATION_ID = "placeholder-unkalibriert"`: Kennung der Notkalibrierung aus `default_calibration`. Sie ist nicht gemessen; das Sichtfeld von 70° ist angenommen (`DEFAULT_HORIZONTAL_FOV_DEG`).
  - `calibration.check_resolution` bricht ab, wenn Kalibrierung und Bild verschiedene Auflösungen haben. `scale_to_resolution` skaliert nur bei gleichem Seitenverhältnis.
  - `tagmap.EXPECTED_WORLD_TAG_COUNT = 4`: erwartete Zahl der Welttags, kein Limit.
  - `localize.SOURCE_PRECEDENCE = ("ceiling", "flange")`: die spätere Quelle gewinnt.
  - `handeye.MIN_SAMPLES = 3`, `MIN_AXES = 3`, `AXIS_THRESHOLD_DEG = 5.0`: Mindestzahl von Aufnahmen und deutlich gedrehten Achsen. Außerdem `OUTLIER_MAD_FACTOR = 3.0`, `HUBER_DELTA_PX = 1.0`, `REFINE_MAX_ITERATIONS = 60`.
  - `reference_edges.MIN_LINE_LENGTH_M = 0.02`, `PERPENDICULAR_LIMIT_DEG = 10.0`, `MIN_ANGLE_FULL_DEG = 20.0`: Grenzen für Bezugskanten.
  - `modes.OVERLAY_MODES = ("off", "apriltag", "calibration", "undistorted", "tagreference")`, Standard `apriltag`.
  - `detector.build_detector(backend=...)`: `"aruco"` (Standard), `"pupil"`, `"auto"` (weicht auf `pupil` aus, wenn `cv2.aruco` nicht nutzbar ist).

**Tests**: Schwerpunkte sind `test_layering.py` (Importregeln), `test_tag_pipeline.py` (ganze Kette auf synthetischen Bildern), `test_reference_edges.py`, `test_handeye*.py`, `test_joint_camera_pose.py` und `test_lens.py`. Die Zuordnung je Modul steht im Abschnitt `tests/` unten.

**Weiterlesen**

- [apriltag-lokalisierung.md](apriltag-lokalisierung.md): Konzept. Abschnitt 1.2 (Die Kette), 1.4 (Ankern), 1.5 (Ablauf in der Zelle), 2.1 (Schichtung), 3 (Datenformate).
- [apriltag-referenz.md](apriltag-referenz.md): Funktionsreferenz. Abschnitt 3 `geometry`, 4 `calibration`, 5 `tagmap`, 6 `detector` und `pose`, 7 `localize` und `handeye`, 8 `overlay`, 14 `reference_edges`. Lücke: Die Modulübersicht in Abschnitt 2 nennt `handeye`, `lens`, `reference_edges` und `reference_overlay` nicht.
- Arbeitspläne: [apriltag-welttag-konzept.md](arbeitsplaene/apriltag-welttag-konzept.md) (Welttags), [apriltag-hand-auge-ankern.md](arbeitsplaene/apriltag-hand-auge-ankern.md) (Anker), [kalibrierung-linsenmodell-je-kamera.md](arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md) (`lens`), [tag-einmessen-bezugskanten.md](arbeitsplaene/tag-einmessen-bezugskanten.md) (`reference_edges`; laut Plan an echter Hardware noch nicht getestet).

### `src/tagloc/cli/`

**Zweck**: Fünf Kommandozeilenbefehle als dünne Adapter über das Paket: Kamerakalibrierung, Hand-Auge-Kalibrierung, Erkennung, Koordinatentransformation und Bau der Tag-Map. `_common.py` enthält die Argumente, das Laden und die Ausgabe, die die Befehle gemeinsam haben. Hinweis: Die Paketdoku in `cli/__init__.py` nennt nur vier Befehle; `calibrate_handeye` fehlt dort.

**Einstieg**:

```
PYTHONPATH=src .venv/bin/python -m tagloc.cli.detect --help
```

Jeder Befehl hat `--help`. `transform` braucht kein OpenCV. Die anderen vier brauchen `opencv-contrib-python`, weil sie Bilder lesen oder `cv2.aruco` benutzen.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/tagloc/cli/__init__.py` | Paketdoku mit der Befehlsliste (vier Befehle) | — |
| `src/tagloc/cli/_common.py` | Gemeinsame Argumente, Laden von Kalibrierung und Tag-Map mit Hinweis auf den nächsten Befehl, Auflösungsprüfung, JSON-Ein- und Ausgabe (Schema `wsc.vision.detections.cli/1`) | `add_source_arguments`, `add_detection_arguments`, `add_decoder_arguments`, `open_source`, `load_inputs`, `fit_calibration`, `tag_poses_to_json`, `poses_from_json`, `parse_detector_params` |
| `src/tagloc/cli/calibrate.py` | Kamerakalibrierung: live mit Fenster oder headless aus einem Bildordner | `build_parser`, `main`, `MIN_SAMPLES` (15) |
| `src/tagloc/cli/calibrate_handeye.py` | Hand-Auge-Kalibrierung aus einer Aufnahmeliste oder aus einer Sitzung des Hand-Pis (`--session`) | `build_parser`, `main`, `load_samples`, `solve_session`, `SPREAD_WARNING_M` |
| `src/tagloc/cli/detect.py` | Erkennung auf Kamera oder Bildern. Ausgabe auf der Konsole, als Overlay-Bild oder als JSON | `build_parser`, `main` |
| `src/tagloc/cli/transform.py` | Reine Rechnung: Pose oder Ergebnisdatei in ein anderes KS transformieren, auch umgekehrt | `build_parser`, `main`, `_load_transform` |
| `src/tagloc/cli/build_tagmap.py` | Tag-Map aus Bildern bauen, auf denen mehrere Tags gemeinsam zu sehen sind | `build_parser`, `main` (mit `place_tags`, `residuals`, `with_world_poses`) |

**Ablauf**

- **calibrate**: Quelle öffnen, pro Bild `boards.detect_board`, Aufnahmen sammeln, dann `calibrate_from_samples` und `save_calibration`. Mit Kamera und Fenster: `SPACE` nimmt auf, `C` rechnet ab drei Aufnahmen, `Q` beendet. `Q` rechnet ebenfalls, sofern Aufnahmen vorliegen. Die Kopfzeile von `calibrate.py` nennt außerdem „S = save“; eine S-Taste gibt es im Code nicht.
- **calibrate** mit Bildordner oder `--no-gui`: Der Code liest alle Bilder und nimmt jedes erkannte Brett auf. Mit `--no-gui` und einer Kamera gibt es im Code keinen Abbruch: Die Schleife nimmt jedes erkannte Brett auf, bis die Quelle `None` liefert. Das tut eine Live-Kamera im Normalbetrieb nicht. Der Modus ist deshalb nur mit Bildordner sinnvoll.
- **calibrate** `--capture-to`: Die Bilder werden als `kalib_NNN.png` abgelegt, im Fenstermodus und mit `--no-gui` bei Kamera-Quelle (jedes aufgenommene Bild). Bildordner werden nicht kopiert.
- **calibrate_handeye --samples**: Jedes Bild wird mit der Kalibrierung gemessen; gesucht wird die Tag-ID `tagId` aus der Liste, die Tag-Größe kommt aus `tagSizeM`. Ein Bild mit anderer Auflösung als die Kalibrierung bricht ab. `solve_hand_eye` rechnet, `save_hand_eye` schreibt. Eine Streuung über 5 mm gibt eine Warnung; die Datei wird trotzdem geschrieben.
- **calibrate_handeye --session**: Nur Aufnahmen mit `accepted` zählen. Gerechnet wird in drei Stufen: `solve_hand_eye` (Park-Martin), `solve_hand_eye_robust` (mit Ausreißerverwerfung) und `refine_hand_eye_robust` gegen die Brettecken, falls Ecken und Intrinsik im Datensatz stehen. Ab vier verwendeten Aufnahmen folgt die Leave-one-out-Prüfung. Ohne `--out` nur Ausgabe.
- **detect**: Pro Bild Auflösung der Kalibrierung prüfen (`fit_calibration`), erkennen, Posen schätzen. Mit sichtbarem Welttag: `localize_camera` und `locate_modules` im Welt-KS, sonst im Kamera-KS. `--overlay` öffnet ein Fenster (ohne Bildschirm: Hinweis, weiter ohne Fenster). `--save-overlay` schreibt markierte Bilder. `--json` schreibt die Posen des letzten Bildes. Keine Bilder gelesen: Exit-Code 1. Mit Kamera läuft der Befehl ohne `--limit` weiter, bis Strg+C oder (mit Fenster) `q`.
- **transform**: Die Transformation kommt aus `--camera-pose` (JSON mit `T_ziel_quelle`) oder aus den Welttags der Eingabe (`--from-reference`, braucht `--tag-map`). Ohne beides ist es die Identität. `--invert` kehrt sie um.
- **build_tagmap**: Bilder mit mindestens `--min-tags` Tags → `poses_by_tag` → `place_tags` (Anker `--anchor`) → Tags ohne Pfad zum Anker melden → `residuals` (Schließfehler je Kante) → `with_world_poses` → `save_tag_map`. Mit `--tag-map` bleiben Rollen, Größen und CAD-Versätze der bestehenden Karte erhalten; nur die Weltposen werden neu gerechnet. Kein Bild mit zwei gemeinsamen Tags: Exit-Code 1.

**Konfiguration & Dateien**

Gemeinsame Optionen (aus `_common.py`):

- `--source` (Standard `camera:0`): `camera[:n]`, `picamera`, `realsense`, Bildordner oder Bilddatei.
- `--resolution BREITE HOEHE`, `-v` / `--verbose`.
- Erkennung: `--calibration` (Pflicht), `--tag-map`, `--family` (Standard `tag36h11`), `--backend {aruco,pupil,auto}` (Standard `aruco`), `--detector-param NAME=WERT` (wiederholbar), `--tag-size-m` (0.05), `--max-reproj-error-px` (3.0), `--allow-resolution-mismatch`.

Befehlsspezifisch (Standardwerte in Klammern):

- `calibrate`: `--board {chessboard,charuco}` (chessboard), `--cols` (9), `--rows` (6), `--square-size-m` (0.030), `--marker-size-m` (0.022), `--dictionary` (DICT_4X4_50), `--model {pinhole,fisheye}` (pinhole), `--out` (Pflicht), `--frame-id`, `--min-samples` (15), `--capture-to ORDNER`, `--no-gui`.
- `calibrate_handeye`: `--samples` oder `--session`, `--calibration`, `--out`, `--no-scale` (Brettmaßstab nicht mitschätzen), `--family`, `--backend` (aruco), `--detector-param`, `--frame-id`, `--max-reproj-error-px` (**2.0**, bei `detect` 3.0), `-v`. Eine Option für die Tag-Größe gibt es nicht; sie kommt aus der Aufnahmeliste.
- `detect`: `--overlay`, `--overlay-wait-ms` (0 im Bildordner, 1 an der Kamera), `--save-overlay ORDNER`, `--json DATEI`, `--limit N`, `--frame-id`.
- `build_tagmap`: `--out` (Pflicht), `--anchor` (0), `--frame-id` (world), `--min-tags` (2), `--tag-map` (bestehende Karte).
- `transform`: `--input`, `--position X Y Z`, `--orientation X Y Z W` (0 0 0 1), `--camera-pose`, `--tag-map`, `--from-reference`, `--invert`, `--to` (world), `--out`.

Dateien (Konvention, nicht im Code erzwungen): Kalibrierung `data/calibration/<frame_id>.json`; Hand-Auge `data/handeye/<frame_id>.json`; Sitzungsordner `data/handeye/sessions/<Zeit>/` mit `session.json` (Schema `wsc.vision.handeye.session/1`, geschrieben vom Hand-Pi); Aufnahmeliste (Schema `wsc.vision.handeye.samples/1`); Tag-Map `config/tagmap.json`.

**Tests**: Keine eigenen Tests für die `main()`-Funktionen. `tests/test_frame_sources.py` importiert `tagloc.cli._common`. Die Logik dahinter (Kalibrierung, Hand-Auge, Tag-Map) ist über die Modul-Tests abgedeckt.

**Weiterlesen**

- [apriltag-referenz.md](apriltag-referenz.md): Abschnitt 9.1 `calibrate`, 9.2 `detect`, 9.3 `transform`, 9.3a `calibrate_handeye`, 9.4 `build_tagmap`, 12 („Wenn etwas nicht stimmt“).
- [apriltag-e2e-test.md](apriltag-e2e-test.md): Abschnitt 2 (Webcam), 3.3 (Kalibrieren mit der Pi-Kamera), 3.6 (Tag-Map), 3.6a (Hand-Auge).
- [handeye-genauigkeit.md](arbeitsplaene/handeye-genauigkeit.md): beschreibt `--session` und die gemeinsame Verfeinerung. Status: in Arbeit.

### `src/apriltag/` (Prototyp, entfernt)

Der erste Entwurf für Kamerakalibrierung und Erkennung (`calibrate_camera.py`, `detect_apriltags.py`, `MANUAL_TEST.md`) wurde am 30.10.2026 gelöscht (Fund T1 in [altlasten-zelle.md](altlasten-zelle.md)). Ersatz: `python -m tagloc.cli.calibrate` und `python -m tagloc.cli.detect`. Die Löschbedingung war erfüllt: Der Hand-Pi ist mit der neuen Kette real kalibriert (siehe [vision-server-interface.md](vision-server-interface.md)). In der Versionsgeschichte ist der Prototyp weiterhin einsehbar.

### `src/jobs/` (OPC-UA-Job, nur Prüfung)

**Zweck**: `calibrate.py` ist ein Prüfauftrag für OPC UA. Er kalibriert nicht. Er meldet, ob es für den Rahmen dieses Pi eine Kalibrierung und eine Tag-Map gibt, und beschreibt sie. Laut Modul-Docstring startet der Vision-Server ihn als Unterprozess (`ScriptDetectionSource`); das Ergebnis erscheint in `attributes.message` (Job `calibration`, siehe [vision-server-interface.md](vision-server-interface.md)).

**Einstieg**: `python src/jobs/calibrate.py` gibt eine Zeile aus. Die Datei setzt `src` selbst auf den Pfad.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/jobs/calibrate.py` | Ermittelt die Rahmen-ID, prüft Kalibrierdatei und Tag-Map, baut die Statuszeile | `REPO_ROOT`, `FRAME_BY_HOST`, `_frame_id`, `main` |

**Ablauf**

1. Rahmen-ID: Umgebungsvariable `VISION_FRAME_ID`, sonst Zuordnung nach Hostname (`ADP-Roboter-Lokalisierung` → `cam_ceiling`, `ADP-HandInEye-Kamera-Pi` → `cam_flange`), sonst `world`.
2. Fehlt `data/calibration/<frame_id>.json`: Meldung mit dem Befehl zum Erzeugen, Ende.
3. Datei nicht lesbar: Meldung mit dem Fehler, Ende.
4. Sonst Statuszeile: Kennung, Auflösung, Linsenmodell, RMS, Anzahl Aufnahmen, Alter in Tagen. Dazu die Tag-Map (Kennung) oder „Tag-Map FEHLT“.

**Konfiguration & Dateien**: Umgebungsvariable `VISION_FRAME_ID`. Die Hostname-Zuordnung steht als Tabelle `FRAME_BY_HOST` im Code; sie spiegelt `PI_IDENTITIES` aus `vision_server/server.py` (nicht importiert, da `vision_server.server` asyncua lädt), ein Test vergleicht beide. Gelesen werden `data/calibration/<frame_id>.json` und `config/tagmap.json`, jeweils ab der Repo-Wurzel.

**Tests**: `tests/test_jobs_calibrate.py` (Rahmen-ID, Kalibrierung fehlt/vorhanden/unlesbar, Tag-Map fehlt/vorhanden, Tabellenvergleich).

**Weiterlesen**: [vision-server-interface.md](vision-server-interface.md) (Job `calibration`).

### `src/OPCUA/` (Startskript)

**Zweck**: `server.py` startet den Vision-Server auf den Pis. Die Datei enthält keine Logik; sie existiert, damit die systemd-Unit `opcua-server.service` unverändert weiterläuft. Die Unit selbst liegt nicht im Repo (siehe [altlasten.md](altlasten.md), D5).

**Einstieg**: Die Unit ruft `python src/OPCUA/server.py` auf. Direkt geht es laut Docstring über den Modulweg: `PYTHONPATH=src python3 -m vision_server.server`.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/OPCUA/server.py` | Ergänzt `sys.path` um `src` und startet `vision_server.server.main` mit `asyncio.run` | `main` (aus `vision_server.server`) |

**Ablauf**: Unit → `server.py` → `sys.path` → `asyncio.run(main())`.

**Konfiguration & Dateien**: keine eigene. Der Repo-Pfad auf dem Pi steht nur in der Unit.

**Tests**: keine.

**Weiterlesen**: [altlasten.md](altlasten.md) (D5), [vision-server-interface.md](vision-server-interface.md).

### `src/ua_program/` (Part-10-Gerüst)

**Zweck**: Rudimentäre Umsetzung von OPC-UA-Programmen nach Part 10: die Basisklasse `Program` mit Zustandsmaschine. Sie ist aus der Vorlage des Betreuers übernommen; das Docstring von `program.py` listet die Änderungen. Benutzt wird sie von `src/vision_server/vision_program.py`.

**Einstieg**: `from ua_program import Program, ProgramException` (mit `src` auf dem Pfad). Eine Unterklasse implementiert `reset`, `start`, `halt`, `suspend` und `resume`, jeweils mit einer `*_inputs`-Property.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/ua_program/__init__.py` | Re-Export | `Program`, `ProgramException`, `ProgramStateMachine` |
| `src/ua_program/program.py` | Basisklasse mit asyncua-Anbindung: OPC-UA-Methoden für Start, Halt, Suspend, Resume und Reset, Zustandswechsel | `Program` (ABC), `Program.init`, `ProgramStateMachine`, `ProgramException` |

**Ablauf**: Die Zustandsmaschine (asyncua `FiniteStateMachine`) und die Methodenhandler `_start_method` bis `_reset_method` setzen die Übergänge um. Die Handler rufen die abstrakten Methoden der Unterklasse auf.

**Konfiguration & Dateien**: Optionale String-NodeIds über `nodeid=` (laut Docstring stabile, ratbare Ids statt numerischer). Keine Dateien. Hinweis: Das Docstring verweist auf `betreuer/OPC_UA_Program.py`; den Ordner `betreuer/` gibt es in diesem Stand nicht.

**Tests**: Kein Test importiert das Paket direkt.

**Weiterlesen**: [part10-programm-schnittstelle.md](part10-programm-schnittstelle.md), [altlasten-abbau-part10-fassade.md](arbeitsplaene/altlasten-abbau-part10-fassade.md).

### `tools/` (Handwerkzeuge)

**Zweck**: Handwerkzeuge, keine Produktivkomponenten: Druckbogen für Tags und Schachbretter (SVG, in Millimetern), synthetische Bildserien mit bekannter Wahrheit ohne Kamera und eine Messung des Speicherbedarfs beim Import der Nodesets.

**Einstieg**:

```
PYTHONPATH=src .venv/bin/python tools/make_tag_sheet.py --ids 0 1 2 3 --size-mm 100 --out /tmp/tags.svg
PYTHONPATH=src .venv/bin/python tools/make_synthetic_scene.py --out /tmp/szene --count 20 --calibration /tmp/szene/kalibrierung.json
PYTHONPATH=src .venv/bin/python tools/measure_nodeset_import.py
```

Die Messung sollte auch auf dem Pi laufen; dort zählen die Zahlen (so das Docstring).

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `tools/make_tag_sheet.py` | Druckbogen als SVG (A4, in mm): AprilTags oder ChArUco-Board, mit 100-mm-Maßstab zum Nachmessen | `build_parser`, `main`, `_dictionary`, `_png_data_uri` |
| `tools/make_synthetic_scene.py` | Rendert Bildserien mit Tags oder Schachbrett, deren Posen bekannt sind (`wahrheit.json`); optional die passende Kalibrierung | `synthetic_calibration`, `write_tag_scene`, `write_chessboard_scene`, `render_reference_scene`, `TRUTH_FILE`, `main` |
| `tools/measure_nodeset_import.py` | Misst RSS, Dauer und Knotenzahl beim Import der Nodesets: Part 1 MachineVision, DI 1.04.0, Machinery 1.03.0, AMCM 1.00.0. Nur Linux (`/proc/self/status`). | `main` (async), `rss_mb`, `IMPORT_ORDER` |

**Ablauf**: `make_synthetic_scene.py` schreibt Bilder, `wahrheit.json` und optional die Kalibrierung. Mit diesen Bildern laufen `tagloc.cli.build_tagmap` oder `tagloc.cli.calibrate` (Schachbrettmodus) ohne Kamera. Die Tests vergleichen das Ergebnis mit den bekannten Werten statt mit sich selbst.

**Konfiguration & Dateien**

- `make_tag_sheet.py`: `--ids` (0 1 2 3), `--size-mm` (100), `--family` (`DICT_APRILTAG_36h11`), `--charuco`, `--cols` (7), `--rows` (5), `--square-mm` (30), `--marker-mm` (22), `--board-dictionary` (DICT_4X4_50), `--out` (Pflicht, `.svg`). **Achtung:** `--family` erwartet hier den OpenCV-Namen `DICT_…`; die `tagloc`-Befehle erwarten `tag36h11`.
- `make_synthetic_scene.py`: `--out` (Pflicht), `--count` (20), `--mode {tags,chessboard}` (tags), `--calibration`, `--width` (1600), `--height` (1200), `--focal-px` (Standard: Bildbreite), `--frame-id` (cam_synth), `--family` (tag36h11), `--supersample` (2), `--cols` (9), `--rows` (6), `--square-size-m` (0.030), `--distance-m` (0.55), `--seed` (20260917).
- `measure_nodeset_import.py`: keine Optionen. Die Nodesets liegen in `src/vision_server/nodesets/` (Bereich Vision-Server).

**Tests**: `tests/test_tag_pipeline.py`, `tests/test_tagreference.py` und `tests/test_reference_edges.py` importieren aus `tools.make_synthetic_scene`. `make_tag_sheet.py` und `measure_nodeset_import.py` haben keine Tests.

**Weiterlesen**: [apriltag-referenz.md](apriltag-referenz.md) (Abschnitt 9.5, Hilfswerkzeuge), [apriltag-e2e-test.md](apriltag-e2e-test.md) (Abschnitt 0.2 Tags drucken, 0.3 Kalibrierboard, 1.2 Werkzeuge von Hand), [apriltag-lokalisierung.md](apriltag-lokalisierung.md) (Abschnitt 7, End-to-End-Testbarkeit).

### `config/`

**Zweck**: Die Tag-Map, also das Zellenlayout: welche Tag-ID welche Rolle hat und wo die Welttags stehen. `config/tagmap.json` ist die aktuelle Karte und liegt versioniert im Repo.

**Einstieg**: Gelesen von `tagloc.cli.detect --tag-map config/tagmap.json` und `tagloc.cli.transform --tag-map …`. Geschrieben von `tagloc.cli.build_tagmap --out …`, das überschreibt die Datei.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `config/tagmap.json` | Aktuelle Zellen-Karte: vier Welttags (IDs 583 bis 586), Anker 583, Frame `world`, Familie `tag36h11`. Keine Modultags. Stand dieses Branches. | Schema `wsc.vision.tagmap/2` |
| `config/tagmap.example.json` | Vorlage: Welttags 0 bis 3, Robotertags 20 und 21, Modultag 7 | — |
| `config/tagmap.test.example.json` | Vorlage für den kleinen Aufbautest: ein Welttag (0), ein Modultag (7) | — |

**Ablauf**: entfällt.

**Konfiguration & Dateien**: Schema und Feldbedeutung stehen in [apriltag-referenz.md](apriltag-referenz.md), Abschnitt 5 („Dateiformat `config/tagmap.json`“), nicht hier. Rollen: `world` (fest, Anker), `module` und `robot` (beweglich, werden gemessen). Wer was pflegt, steht in [apriltag-lokalisierung.md](apriltag-lokalisierung.md), Abschnitt 3.2.

**Tests**: Keine Testdatei liest `config/`. Die Tag-Map-Tests arbeiten mit temporären Dateien (`test_tag_map.py`, `test_calibration_io.py`).

**Weiterlesen**: [apriltag-referenz.md](apriltag-referenz.md) (Abschnitt 5), [apriltag-lokalisierung.md](apriltag-lokalisierung.md) (Abschnitt 3.2), [apriltag-e2e-test.md](apriltag-e2e-test.md) (Abschnitt 3.6, Vorlagen im Aufbautest), Arbeitspläne [apriltag-welttag-konzept.md](arbeitsplaene/apriltag-welttag-konzept.md), [apriltag-tagmap-robustheit.md](arbeitsplaene/apriltag-tagmap-robustheit.md), [apriltag-tagmap-ueber-opcua.md](arbeitsplaene/apriltag-tagmap-ueber-opcua.md).

### `data/`

**Zweck**: Maschinenspezifische Kalibrier- und Aufnahmedaten. Die README-Datei sagt „nicht versioniert“. Das stimmt nur zum Teil: `.gitignore` schließt `data/*` aus, `data/rohbilder/` ist ausgenommen, und einige Bilder liegen bereits im Index (Tabelle unten).

**Einstieg**: entfällt. Die Befehle schreiben mit `--out` hierhin; die Konventionspfade stehen unten.

**Dateien** (versioniert, Stand `d55df91`)

| Pfad | Inhalt | Hinweis |
| --- | --- | --- |
| `data/.gitkeep`, `data/rohbilder/.gitkeep` | Platzhalter | — |
| `data/LV1/Train/kalib_*.jpg` (29), `data/LV1/Val/kalib_*.jpg` (11) | Schachbrettbilder für die Deckenkamera `cam_ceiling` (Bildsatz `LV1`, siehe [kalibrierung-linsenmodell-je-kamera.md](arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md)) | Im Index, obwohl `.gitignore` sie ausschließt (`data/*`, `*.jpg`). Kein Python-Code liest sie; die Doku nennt sie als Referenz. |
| `data/rohbilder/cam_ceiling_<Datum>-<Uhrzeit>.png` (9) | Rohbilder der Deckenkamera, gespeichert über die Methode `SaveRawImage` (siehe [rohbild-speichern.md](arbeitsplaene/rohbild-speichern.md)) | Versioniert: `!data/rohbilder/` in `.gitignore`. |

**Ablauf**: entfällt.

**Konfiguration & Dateien** (Konvention):

- `data/calibration/<frame_id>.json`: Kalibrierung je Kamera. Jeder Pi erzeugt seine eigene; die Datei ist nicht versioniert (Docstring `calibration.py`).
- `data/handeye/<frame_id>.json`: Hand-Auge-Kalibrierung. `data/handeye/sessions/<Zeit>/`: Sitzungen des Hand-Pis.
- Auf einem frisch geklonten Repo fehlen diese Ordner; `src/jobs/calibrate.py` meldet das.

**Tests**: keine. Laut Plan [apriltag-erkennung-unscharfe-tags.md](arbeitsplaene/apriltag-erkennung-unscharfe-tags.md) liefern die 40 Schachbrettbilder ohne Tags null Fehlalarme.

**Weiterlesen**: [altlasten-toter-code.md](altlasten-toter-code.md) (P7 zu `data/LV1/`), [apriltag-erkennung-unscharfe-tags.md](arbeitsplaene/apriltag-erkennung-unscharfe-tags.md), [kalibrierung-linsenmodell-je-kamera.md](arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md).

### Repo-Wurzel: `requirements.txt`, `setup.sh`, `setup.bat`

**Zweck**: `requirements.txt` listet die Python-Pakete. `setup.sh` und `setup.bat` richten **kein** Projekt-Setup ein, sondern das ShareLaTeX-Remote und zwei Git-Aliase für `doc/`. Die venv legt man selbst an (siehe [apriltag-e2e-test.md](apriltag-e2e-test.md), Abschnitt 0.1, und [altlasten.md](altlasten.md), D4).

**Einstieg**:

```
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 -c "import cv2; print('aruco ok:', hasattr(cv2.aruco, 'DICT_APRILTAG_36h11'))"
```

Die letzte Zeile muss `aruco ok: True` ausgeben.

**Dateien**

| Datei | Aufgabe | Wichtigste Inhalte |
| --- | --- | --- |
| `requirements.txt` | Python-Abhängigkeiten | `opencv-contrib-python>=4.8.0` (liefert `cv2.aruco`; `opencv-python` allein reicht nicht), `numpy`, `pupil-apriltags` (nur für `--backend pupil`), `asyncua>=1.1.5`, `zeroconf>=0.131` (zur Laufzeit optional), `tzdata`. Bewusst nicht enthalten: `picamera2` (per apt, venv mit `--system-site-packages`) und `pyrealsense2` (auf dem Hand-Pi von Hand). |
| `setup.sh` | Bash-Skript: legt das ShareLaTeX-Remote `sharelatex` an und setzt die Git-Aliase `pushall` und `pullsl` | — |
| `setup.bat` | Windows-Gegenstück zu `setup.sh` | — |

**Ablauf**: siehe Einstieg.

**Konfiguration & Dateien**

- `.gitignore` enthält `*.txt`, `*.sh` und `*.bat`. Die drei Dateien sind trotzdem versioniert. Neue Dateien mit diesen Endungen würden stillschweigend nicht committet (`git check-ignore` bestätigt das).
- Veraltete Verweise in `requirements.txt`: Der Kommentar nennt `src/ua_mdns.py`. Diese Datei gibt es nicht mehr; die Funktion liegt in `src/vision_server/discovery/mdns.py` ([altlasten-abbau-part10-fassade.md](arbeitsplaene/altlasten-abbau-part10-fassade.md)). Die Verweise `doc/altlasten.md` und `doc/apriltag-e2e-test.md` zeigen auf `doc/projektdoku/`.

**Tests**: keine eigenen.

**Weiterlesen**: [altlasten.md](altlasten.md) (D2 bis D5: venv, requirements, Setup-Skripte, systemd-Unit), [apriltag-e2e-test.md](apriltag-e2e-test.md) (Abschnitt 0.1).

### `tests/` (Übersicht)

**Zweck**: Unit- und Integrationstests mit `unittest`. Die Tests für `tagloc` sind `tests/test_*.py` mit `tagloc`-Imports. CLAUDE.md nennt sie „Pytest-Tests“; gestartet werden sie aber mit `unittest`, und die Dateien nutzen `unittest.TestCase`.

**Einstieg**: Aus der Repo-Wurzel:

```
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t .
```

Eine einzelne Datei: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_tag_pipeline -v`.

**Ablauf**: entfällt.

**Dateien** (Zuordnung Paket → Testdateien, nach den Imports geprüft)

| Paket / Modul | Testdateien |
| --- | --- |
| `tagloc.geometry` | `test_tag_geometry.py`, `test_boards_pose.py`, `test_tag_map.py`, `test_welttags.py`, `test_handeye.py`, `test_handeye_session.py`, `test_handeye_session_refine.py`, `test_handeye_refine.py`, `test_handeye_robust.py`, `test_handeye_opcua.py`, `test_layer2_opcua.py`, `test_layer2_plan.py`, `test_layer2_run.py`, `test_cellwatch.py`, `test_cellwatch_posestore.py`, `test_cellwatch_storedpose.py`, `test_measureboard.py`, `test_measuretag.py`, `test_lens.py`, `test_joint_camera_pose.py`, `test_apriltag_source.py`, `test_overlay_scaling.py`, `test_tagreference.py`, `test_stream_overlay.py`, `test_reference_edges.py`, `test_tag_pipeline.py` |
| `tagloc.calibration` | `test_calibration_info.py`, `test_calibration_io.py`, `test_calibration_session.py`, `test_boards_pose.py`, `test_apriltag_source.py`, `test_handeye_session_refine.py`, `test_measureboard.py`, `test_measuretag.py`, `test_stream_overlay.py`, `test_tagreference.py`, `test_cellwatch.py`, `test_cellwatch_storedpose.py`, `test_lens.py`, `test_joint_camera_pose.py` |
| `tagloc.boards` | `test_boards_pose.py`, `test_calibration_session.py`, `test_handeye_refine.py`, `test_lens.py`, `test_measureboard.py`, `test_tag_pipeline.py` |
| `tagloc.detector` | `test_detector.py`, `test_reference_edges.py`, `test_tag_pipeline.py`, `test_tagreference.py`, `test_tagreference_liveview.py`; `test_camera_owner.py` patcht nur `tagloc.detector.build_detector` |
| `tagloc.frames` (und `tagloc.cli._common`) | `test_frame_sources.py`, `test_calibration_session.py`, `test_reference_edges.py`, `test_tag_pipeline.py` |
| `tagloc.pose` | `test_apriltag_source.py`, `test_joint_camera_pose.py`, `test_lens.py`, `test_reference_edges.py`, `test_stream_overlay.py`, `test_tag_pipeline.py` |
| `tagloc.localize` | `test_apriltag_source.py`, `test_handeye.py`, `test_joint_camera_pose.py`, `test_tag_pipeline.py`, `test_welttags.py` |
| `tagloc.tagmap` | `test_tag_map.py`, `test_welttags.py`, `test_handeye.py`, `test_apriltag_source.py`, `test_joint_camera_pose.py`, `test_cellwatch.py`, `test_cellwatch_storedpose.py`, `test_layer2_plan.py`, `test_layer2_run.py`, `test_layer2_opcua.py`, `test_measureboard.py`, `test_measuretag.py`, `test_tagreference.py`, `test_tag_pipeline.py` |
| `tagloc.handeye` | `test_handeye.py`, `test_handeye_refine.py`, `test_handeye_robust.py`, `test_handeye_session.py`, `test_handeye_session_refine.py`, `test_layer2_opcua.py`, `test_layer2_run.py` |
| `tagloc.observations` | `test_pose_filter.py`, `test_overlay_scaling.py`, `test_lens.py`, `test_welttags.py`, `test_joint_camera_pose.py`, `test_stream_overlay.py`, `test_handeye.py`, `test_apriltag_source.py` |
| `tagloc.overlay` | `test_overlay_scaling.py`, `test_lens.py`, `test_stream_overlay.py`, `test_stream_calibration_overlay.py` |
| `tagloc.reference_edges` | `test_reference_edges.py`, `test_tagreference.py`, `test_tagreference_liveview.py` |
| `tagloc.reference_overlay` | `test_tagreference_liveview.py` |
| `tagloc.lens` | `test_lens.py` |
| `tagloc.identity` | `test_calibration_io.py` |
| `tagloc.modes` | `test_camera_stream.py` |
| Importregeln des ganzen Pakets | `test_layering.py` |
| `tools.make_synthetic_scene` | `test_tag_pipeline.py`, `test_tagreference.py`, `test_reference_edges.py` |
| Ohne direkten tagloc-Import (vision_server, OPC UA, mDNS, Kamera, Payload u. a.) | `test_asset_model.py`, `test_camera_health.py`, `test_camera.py`, `test_detection_source.py`, `test_discovery_lds.py`, `test_discovery_mdns.py`, `test_job_runner.py`, `test_mjpeg_server.py`, `test_part10_fassade.py`, `test_payload.py`, `test_raw_image.py`, `test_recipe_routing.py`, `test_script_runner.py`, `test_server_config.py`, `test_shared_camera.py`, `test_subpixel_settings.py` |

**Konfiguration**: `PYTHONPATH=src` ist Pflicht. Für `cv2.aruco` braucht die venv `opencv-contrib-python`. Einige Tests überspringen sich selbst, wenn ein Import fehlt (z. B. in `test_layering.py`). Nach einem Lauf auf „skipped“ achten, sonst wirkt ein Test grün, der nie gelaufen ist.

**Weiterlesen**: [apriltag-e2e-test.md](apriltag-e2e-test.md) (Abschnitt 1.1 „Automatisierte Tests“, 5 „Kurzreferenz Fehlersuche“); [apriltag-lokalisierung.md](apriltag-lokalisierung.md) (Abschnitt 2.1, Schichtung nach Testbarkeit).

---

## Teil A2 — Vision-Server (src/vision_server)

### Wie der Server aufgebaut ist

Der Vision-Server ist ein OPC-UA-Server auf Basis von `asyncua`. Er stellt ein Vision-System nach OPC 40100 (Machine Vision) bereit. Er läuft auf den beiden Raspberry Pis der Zelle: der Decken-Pi ist Layer 1, der Hand-Pi (Kamera am Roboterflansch) ist Layer 2. Das Paket umfasst rund 15 000 Zeilen Python. Die Rechnung (Tag-Posen, Kalibrierung, Brettsuche, Einmessung) steckt im Rechenkern `tagloc` unter `src/tagloc/`. Dieser Abschnitt beschreibt nur die OPC-UA-Hülle darum.

Ablauf im Überblick:

1. **Start.** Produktiv startet `python3 -m vision_server.server` (`src/vision_server/server.py`) mit Pi-Presets, mDNS und LDS. Die Entwicklung nutzt `python -m vision_server` (`src/vision_server/__main__.py`) ohne Presets. Beide bauen eine `VisionServerConfig` und rufen `install_vision_machine()` aus `src/vision_server/runner.py` auf.
2. **Adressraum.** Die Nodesets werden importiert. `VisionMachine` (OPC 40100) hängt unter `Objects/Machines`. `VisionProgram` (OPC UA Teil 10) liegt daneben und nutzt denselben `JobRunner`. Die Anlagensicht (Part 2) ist optional.
3. **Erkennungsquellen.** Jedes Profil bekommt eine Quelle. `apriltag` ist die AprilTag-Erkennung. `calibration` startet ein Prüfscript als Subprozess.
4. **Geteilte Kamera.** `SharedCamera` hält die Kamera offen. Job, Livestream, Sitzungen und Überwachung lesen dieselben Frames.
5. **Livestream.** Ein Publisher schreibt Base64-JPEGs in einen Knoten. Ein Overlay markiert die Bilder. Ein MJPEG-Server liefert sie zusätzlich per HTTP.
6. **Jobs.** `StartSingleJob` läuft über `JobRunner` (`job.py`), dann über `acquire_and_detect()` der Quelle, dann über `payload.py` und `result_management.py` in die Ergebnisknoten. `events.py` meldet das Ergebnis.
7. **Sitzungen.** Kamera-Kalibrierung, Hand-Auge, Layer 2, Zellen-Überwachung, Messungen auf Anfrage und Einmessen. Sie laufen über eigene Methoden und sperren sich gegenseitig.
8. **Discovery.** mDNS-Ankündigung und Anmeldung beim Local Discovery Server. Das passiert nur im Pi-Start über `server.py`.

Regeln, die der Code durchsetzt:

- `tagloc` importiert nie `vision_server`. Das prüft `tests/test_layering.py`.
- numpy, OpenCV und die Kamera-SDKs werden erst beim Gebrauch importiert. Die Fabriken in `src/vision_server/detection/__init__.py` zeigen das Muster.
- Umgebungsvariablen liest der Server nicht in `config.py`. Sie stehen in `server.py` und in `discovery/`.

---

### Start & Konfiguration

**Zweck**
Baut die Konfiguration und startet den Server. Der Pi-Start nutzt Presets je Hostname (Identität, Kamera, Erkennung, Anlagensicht) und meldet den Server im Netz an. Der Standalone-Start nutzt nur die Standardwerte aus `profiles.py`.

**Einstieg**
- Pi: `python3 -m vision_server.server` ruft `main()` in `server.py`. Die systemd-Unit liegt laut Kommentar in `server.py` nicht im Repo.
- Entwicklung: `python -m vision_server` ruft `main()` in `__main__.py`, dieses ruft `run()` in `runner.py`.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/__init__.py` | Paketmarker, leer | – |
| `src/vision_server/__main__.py` | Standalone-Start: Kommandozeile, Konfiguration, Lauf | `build_config()`, `main()`, `DEFAULT_PORT` |
| `src/vision_server/server.py` | Einbauort auf dem Pi: Identität, Presets, mDNS und LDS, Signalbehandlung | `main()`, `vision_config()`, `apriltag_config()`, `vision_identity()`, `vision_camera_backend()`, `application_uri()`, `merged_json_object_env()`, `subpixel_corner_settings()`, `PI_IDENTITIES`, `PI_CAMERA_BACKENDS`, `PI_APRILTAG_PRESETS`, `PI_CAMERA_STREAM_PRESETS`, `PI_ASSET_PRESETS` |
| `src/vision_server/config.py` | Konfigurationsobjekt, Standardpfade, Zuordnung RecipeId zu Profil | `VisionServerConfig`, `DEFAULT_RECIPE_PROFILES`, `DEFAULT_NODESET_PATH`, `DEFAULT_AMCM_NODESET_PATHS`, `profile_for()` |
| `src/vision_server/profiles.py` | Betriebsparameter als frozen dataclasses | `AprilTagProfileConfig`, `CameraStreamConfig`, `AssetConfig`, `CAMERA_BACKENDS` |
| `src/vision_server/runner.py` | Einbau des Vision-Systems in einen Server und Standalone-Lauf. Größte Datei des Pakets | `install_vision_machine()`, `VisionMachine` (`aclose()`), `run()` |

**Ablauf**

- *Pi (`server.py`, `main()`)*: Server anlegen und initialisieren. Der Endpoint nennt die LAN-IPv4, gelauscht wird auf allen Schnittstellen. Dann `install_vision_machine()`. Danach starten mDNS und LDS. Auf SIGTERM oder SIGINT wird `aclose()` gerufen.
- *Standalone (`runner.run()`)*: `configure_server()`, `install_vision_machine()`, warten, `aclose()`. Keine mDNS, keine LDS, keine Presets. `apriltag`, `camera_stream` und `assets` sind dann `None`. Die Quelle nimmt die Standardwerte aus `profiles.py` (Backend `picamera2`, Kalibrierdatei `data/calibration/camera.json`). Ohne Kalibrierdatei bleibt der Server in `Preoperational`.
- *Reihenfolge in `install_vision_machine()`* (grob): Adressraum, optional Anlagensicht, Automaten, Events, Ergebnisse, Quellen bauen, `JobRunner`, Methoden `StartSingleJob`, `Stop`, `StartContinuous`, `Abort`, `Halt`, `Reset`. Dann werden alle Quellen geöffnet. Nur wenn alle offen sind, geht der Server nach `Operational`. Danach folgen Kalibrier-Session, Livestream, Kamerazustand, MJPEG und die Installer der Sitzungen. Zuletzt wird das Part-10-Programm aufgesetzt.
- *Pi-Identität*: Vorrang hat die Umgebungsvariable, dann die Hostname-Abbildung, dann ein Rückfall. Ein unbekannter Hostname bekommt die Identität `vision-<Hostname>` und den Rahmen `world`, ohne Presets. So sendet ein frisch aufgesetzter Pi nicht stillschweigend die Id eines anderen.

**Konfiguration**

Umgebungsvariablen (16 Stück):

| Variable | Wirkung | Gelesen in |
| --- | --- | --- |
| `VISION_SYSTEM_ID`, `VISION_FRAME_ID` | Identität und Bezugsrahmen. Überschreiben die Hostname-Abbildung | `server.py`, `vision_identity()` |
| `VISION_CAMERA_BACKEND` | Kamera-Backend: `picamera2`, `realsense` oder `opencv` | `server.py`, `vision_camera_backend()` |
| `VISION_DISABLE_STREAM=1` | Keine Livestream-Knoten, kein Publisher, kein MJPEG | `server.py`, `vision_config()` |
| `VISION_CAMERA_CONTROLS` | JSON-Objekt. Überschreibt die libcamera-Controls des Presets. `null` entfernt einen Eintrag | `server.py`, `vision_config()` |
| `VISION_APRILTAG_DETECTOR_PARAMS` | JSON-Objekt mit OpenCV-DetectorParameters. Überschreibt das Preset | `server.py`, `apriltag_config()` |
| `VISION_APRILTAG_QUAD_DECIMATE` | Dezimierungsfaktor der Eckensuche, Standard `0.0` | `server.py`, `apriltag_config()` |
| `VISION_SUBPIXEL_CORNER_REFINEMENT` | `0`, `none`, `false` schalten aus. `1`, `true` schalten an. `apriltag` oder `subpix` schalten an mit diesem Verfahren. Unbekannte Werte brechen den Start ab | `server.py`, `subpixel_corner_settings()` |
| `VISION_CALIBRATION_MODEL` | Linsenmodell (`pinhole` oder `fisheye`), überschreibt das Preset | `server.py`, `apriltag_config()` |
| `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` | Notausgang: Platzhalter-Intrinsik ohne Kalibrierdatei. Posen sind dann nicht maßstabsgetreu | `server.py`, `apriltag_config()` |
| `VISION_SAVE_CALIBRATION_CAPTURES=1` | Kalibrieraufnahmen zusätzlich als JPEG archivieren | `server.py`, `apriltag_config()` |
| `OPCUA_APPLICATION_URI` | ApplicationUri. Standard ist Präfix und Name aus `PI_APPLICATION_NAMES` | `server.py`, `application_uri()` |
| `OPCUA_MDNS_NAME` | Dienstname im Netz. Standard ist die Vision-Identität | `server.py`, `mdns_instance_name()` |
| `OPCUA_MDNS` | `0`, `false`, `no` oder `off` schalten die mDNS-Ankündigung ab | `discovery/mdns.py`, `enabled()` |
| `OPCUA_ADVERTISE_IP` | Erzwingt die angekündigte IPv4-Adresse | `discovery/mdns.py`, `detect_lan_ipv4()` |
| `OPCUA_LDS_URL` | Ziel der LDS-Anmeldung. Leer schaltet sie ab. Standard ist `CELL_LDS_URL` | `discovery/lds.py`, `lds_url()` |

Optionen von `__main__.py` (Standalone-Start):

- `--port` (Standard `DEFAULT_PORT`), `--endpoint`. Der Standardport ist nicht der Pi-Port. Verbindungsdaten stehen in [vision-server-interface.md](vision-server-interface.md) §2.
- `--app-uri`, `--name`, `--namespace`, `--vision-system-name`, `--vision-system-id`.
- `--profile` erzwingt ein Profil für alle Rezepte.
- `--nodeset` (Standard `DEFAULT_NODESET_PATH`), `--log-level` (Standard `INFO`).

Wichtige Standardwerte in `config.py` und `profiles.py`:

- `job_timeout` 30 s (Obergrenze eines Jobs), `stop_timeout` 5 s, `continuous_interval_s` 1 s, `max_id_length` 128, `max_parameters` 16.
- `recipe_profiles`: RecipeId `calibration` läuft über das Profil `calibration`, `apriltag` über `apriltag`. Ein leerer RecipeId wird abgelehnt.
- `frame_id`: `world` in `VisionServerConfig`, `cam_ceiling` in `AprilTagProfileConfig`. Die Pi-Presets setzen `cam_ceiling` oder `cam_flange`.
- Pfade relativ zur Repo-Wurzel: `data/calibration/<Rahmen>.json` (Kamera-Kalibrierung), `data/handeye/cam_flange.json` (nur Hand-Pi), `config/tagmap.json` (Tag-Map, versioniert), `data/rohbilder/` (`SaveRawImage`), `data/tagreference/` (Mess-Protokoll). `data/` ist per `.gitignore` ausgeschlossen.
- Pi-Presets (laut Kommentaren in `server.py`): Beide Rahmen nutzen ein Schachbrett mit 7 × 10 inneren Ecken und 23 mm Feldgröße. Die Modultags sind 80 mm groß. Decke: Backend `picamera2`, Auflösung 4056 × 3040, Linsenmodell `fisheye`, Livestream über einen 960 × 720-Strom. Hand: Backend `realsense`, Auflösung 640 × 480, Linsenmodell `pinhole`, Subpixel-Ecken an.

Stolpersteine:

- `VisionServerConfig.configuration_id` (Standard `hello-world-config`) wird nirgends gelesen. Die `configurationId` der Ergebnisse kommt aus der Quelle (`AprilTagDetectionSource._build_configuration_id()`). Das Feld ist ein Rest.
- `__main__.py` nutzt einen anderen Standardport als der Pi-Endpoint. `vision-system.md` nennt den Standalone-Start „nicht den Produktionsweg“.
- Die Layer-1/2-Tabelle in [apriltag-lokalisierung.md](apriltag-lokalisierung.md) nennt andere Werte (etwa Rahmen `world`, Modultag 0,100 m). Maßgeblich sind die Presets in `server.py`.
- Der Modul-Docstring von `server.py` verweist auf den Einbau. Der Adressraum entsteht vollständig in `runner.install_vision_machine()`.

**Tests**
- `tests/test_server_config.py`: Presets und Umgebungsvariablen je Pi.
- `tests/test_subpixel_settings.py`: `VISION_SUBPIXEL_CORNER_REFINEMENT` und Preset.
- `tests/test_layering.py`: Abhängigkeitsrichtung, Hashbarkeit der Konfiguration.
- `tests/test_part10_fassade.py`: `VisionProgram` als einziger Einstieg, `VisionMachine` unter `Machines`.

**Weiterlesen**
- [vision-system.md](vision-system.md), Abschnitt „Starten“
- [vision-server-interface.md](vision-server-interface.md) §2 (Verbindungsdaten), §8 (Ablauf einmal durchspielen)
- [apriltag-e2e-test.md](apriltag-e2e-test.md) §3.7 (Server starten und messen)
- [altlasten.md](altlasten.md) (Altlasten-Liste)

---

### OPC-UA-Adressraum & Modell

**Zweck**
Baut den Adressraum auf: Nodesets (Part 1, optional Part 2), `VisionMachine` als Instanz des Typs VisionSystem, Ergebnis- und Statusknoten, Methoden und Events. Namespace-Indizes stehen nicht im Code. Sie werden zur Laufzeit ermittelt.

**Einstieg**
`runner.install_vision_machine()` ruft `attach_vision_system()` (`address_space.py`), `attach_asset_model()` (nur mit `config.assets`), `VisionStateMachines.bind()`, `create_event_generators()` und `ResultStore.create()`. Das Part-10-Programm kommt zuletzt über `install_vision_program()`.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/address_space.py` | Nodeset-Import, eigener Namespace, `VisionSystem`-Instanz, Variablenknoten. Livestream-Knoten nur bei aktivem Stream | `VisionAddressSpace`, `configure_server()`, `attach_vision_system()`, `_ensure_nodeset()`, `_ensure_amcm_nodesets()`, `_vision_parent()`, `_namespace_uri_for()` |
| `src/vision_server/nodeset_ids.py` | Namespace-URIs und numerische IDs der Specs. `DeviceHealth` von Hand, weil das Nodeset beim Laden scheitert | `node_id()`, `mv`, `DeviceHealth`, `MACHINE_VISION_NAMESPACE_URI`, `AMCM_NAMESPACE_URI` |
| `src/vision_server/asset_model.py` | Part 2 (AMCM): Anlagensicht mit Recheneinheit, Bildsensor und Objektiv, Identifikation, Health-Block und Alarme | `attach_asset_model()`, `VisionAssetNodes`, `_add_component()`, `_add_health()`, `_add_health_alarms()` |
| `src/vision_server/vision_program.py` | Part 10: `VisionProgram` als zweite Bedienoberfläche auf demselben `JobRunner` | `VisionProgram` (`start`, `halt`, `reset`, `suspend`, `resume`), `install_vision_program()`, `PROGRAM_NAME` |

**Ablauf**

1. Part-1-Nodeset importieren, wenn der Namespace fehlt (`_ensure_nodeset()`).
2. Nur mit `config.assets`: DI, Machinery und AMCM, in dieser Reihenfolge. Scheitert der Import, läuft der Server ohne Anlagensicht weiter.
3. Eigenen Namespace registrieren. Die `VisionMachine` liegt unter `Objects/Machines`, wenn Machinery geladen ist. Sonst liegt sie unter `Objects`.
4. Variablenknoten anlegen. Der Livestream-Knoten entsteht nur bei `stream_enabled`. Die Basismethoden (`StartSingleJob`, `StartContinuous`, `Stop`, `Abort`, `Halt`, `Reset`) stammen aus dem Nodeset-Typ. `install_vision_machine()` verknüpft sie mit ihren Handlern (`link_method`). Die übrigen Methoden legt `install_vision_machine()` mit `add_method()` an, mit festen String-NodeIds aus dem Namen der Instanz. So bleiben sie stabil, wenn ein Nodeset dazukommt.
5. `VisionProgram` übernimmt Referenzen (keine Kopien) auf vorhandene Ergebnis- und Statusknoten sowie auf die Kalibrier-Methoden.
6. Ohne Part 2 ist die Anlagensicht `None`. Der Kamerazustand wird dann nicht geschrieben.

Welche Statusknoten entstehen, hängt von der Konfiguration ab. Kalibrier-, Tag-Map- und Info-Knoten brauchen `config.apriltag`. Die Knoten für Layer 2, Hand-Auge, MeasureTags und Einmessen brauchen `hand_eye_path` und liegen damit nur auf dem Hand-Pi. Die Überwachungsknoten liegen nur auf dem Decken-Pi.

**Konfiguration**

- `namespace_uri` (Standalone: `--namespace`), `vision_system_name` (`--vision-system-name`), `vision_system_id` (`--vision-system-id`). Der Name der Instanz darf sich nicht ändern, Clients adressieren darüber.
- `nodeset_path` (Standard: Part 1 in `src/vision_server/nodesets/`), `amcm_nodeset_paths` (Standard: DI, Machinery, AMCM).
- `assets` (`AssetConfig`): `None` lädt keine Anlagensicht. Die Seriennummer ist die Vision-Identität. Der Pi setzt nur verbaute Modelle (`PI_ASSET_PRESETS`).
- Laut `config.py` kostet Part 2 rund 13 MB RSS und 1,6 s Startzeit.
- Die vollständige Knotenliste und die Aufrufe stehen in [vision-server-interface.md](vision-server-interface.md) §1 und §13.

Stolpersteine:

- Ein fest eingetragener Namespace-Index bricht bei jedem neuen Nodeset. Immer `get_namespace_index(uri)` nutzen.

**Tests**
- `tests/test_asset_model.py`: lädt die AMCM-Nodesets wirklich und prüft die Anlagensicht.
- `tests/test_part10_fassade.py`: Einstieg über `VisionProgram`, Einhängen unter `Machines`.
- `tests/test_calibration_info.py`: Infoknoten der aktiven Kalibrierung.
- `tests/test_handeye_opcua.py`, `tests/test_layer2_opcua.py`: Methoden über OPC UA.

**Weiterlesen**
- [vision-server-interface.md](vision-server-interface.md) §1 (Systemaufbau), §11 (Anlagensicht OPC 40100-2), §13 (Schnittstelle auf einen Blick)
- [part10-programm-schnittstelle.md](part10-programm-schnittstelle.md) §3 (Namespace), §4 (Adressraum des Programms)
- [vision-system.md](vision-system.md), Abschnitt „Adressraum“
- [arbeitsplaene/altlasten-abbau-part10-fassade.md](arbeitsplaene/altlasten-abbau-part10-fassade.md) (Entfernen der alten Demo-Knoten)

---

### Zustandsautomat, Jobs & Ergebnisse

**Zweck**
Führt einen Job von der Zulassung über die Erkennung bis zum Ergebnis. Der `JobRunner` ruft nur die Erkennungsquelle auf und kennt keine Bildverarbeitung. Er hält die beiden OPC-40100-Automaten synchron und veröffentlicht jedes Ergebnis, auch Fehler.

**Einstieg**
Die Methoden `StartSingleJob`, `StartContinuous`, `Stop` und `Abort` in `runner.install_vision_machine()` rufen den `JobRunner`. Das Part-10-Programm (`VisionProgram.start`) und die Layer-2-Läufe nutzen denselben Runner. Sie hängen sich über `add_finish_listener` an das Job-Ende.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/job.py` | Eingaben prüfen, Zulassung, Job-Ablauf, Fehler- und Abbruchpfad, Beobachter | `JobRequest`, `build_job_request()`, `coerce_id()`, `JobRunner` (`start_single_job`, `start_continuous`, `stop`, `cancel_running`, `add_finish_listener`) |
| `src/vision_server/state_machine.py` | Bindet die beiden Automaten an ihre Knoten und führt die Übergänge aus | `VisionStateMachines` (`enter_operational`, `to_single_execution`, `to_ready`, `to_continuous_execution`, `continuous_to_ready`, `halt`, `to_error`, `recover`, `is_ready`) |
| `src/vision_server/events.py` | Vier Job-Events am VisionSystem-Knoten. `ResultReady` trägt den Payload | `VisionEvents`, `create_event_generators()`, `fire_result_ready()` |
| `src/vision_server/result_management.py` | Ergebnisknoten und JSON-Spiegel, Verlauf der letzten Ergebnisse | `ResultStore` (`create`, `publish`, `result_for_job`, `json_node`), `PublishedResult`, `HISTORY` |
| `src/vision_server/payload.py` | JSON-Ergebnis für Erfolg und Fehler, gleicher Aufbau. NaN wird abgelehnt | `build_result_payload()`, `build_error_payload()`, `PAYLOAD_SCHEMA` |
| `src/vision_server/errors.py` | Fehlercodes des `Error`-Ausgangs und Ausnahme mit Code | `VisionErrorCode`, `VisionJobError` |

**Ablauf (Einzeljob)**

1. `start_single_job()` prüft synchron, ohne `await`. Ist ein Job aktiv, kommt `BUSY`. Der Automat muss außen in `Operational` und innen in `Ready` sein. Danach prüft `build_job_request()` RecipeId, Längen und Parameter. Eine Abweisung liefert sofort einen Code.
2. Der Job-Task setzt `Ready` nach `SingleExecution` und feuert `JobStarted`.
3. `source.acquire_and_detect()` läuft unter `asyncio.wait_for(job_timeout)`. Danach feuert `AcquisitionDone`.
4. `_publish()`: Payload bauen, `ResultStore.publish()` schreibt die Ergebnisknoten, `ResultReady` wird gefeuert.
5. `SingleExecution` geht zurück nach `Ready`, und `Ready` wird gefeuert.

Fehler (Fehlercode, Timeout oder Ausnahme, Pfad `_fail`): Die Fehlerpayload ist das Ergebnis. Der innere Automat geht von `SingleExecution` nach `Ready`, der äußere kurz nach `Error` und dann zurück nach `Operational`. Ein Stop oder Abort (Pfad `_cancel`) liefert `CANCELLED` und geht nicht über `Error`. `Stop` bricht auch einen Layer-2-Lauf ab.

Dauerbetrieb (`start_continuous()`): `Ready` geht nach `ContinuousExecution`. Jede Runde veröffentlicht ein eigenes Ergebnis mit eigener Id aus Job-Id und laufender Nummer. Zwischen den Runden wartet `continuous_interval_s`. Eine fehlgeschlagene Runde beendet den Dauerbetrieb.

**Konfiguration**

- `job_timeout` (30 s): Obergrenze für eine Erkennung. Der Kommentar in `config.py` begründet den Wert mit der Laufzeit der AprilTag-Quelle.
- `stop_timeout` (5 s): Frist, bis `stop()` den Abbruch meldet.
- `continuous_interval_s` (1 s): Pause zwischen zwei Runden im Dauerbetrieb.
- `max_id_length` (128), `max_parameters` (16): Eingabegrenzen. Parameter müssen Skalare sein.
- `recipe_profiles` bestimmt die Zuordnung RecipeId zu Profil. Mit `--profile` zeigen alle Rezepte auf ein Profil.
- `ResultStore.HISTORY` (20) begrenzt den Verlauf im Speicher.
- Die Bedeutung der Fehlercodes steht in [vision-server-interface.md](vision-server-interface.md) §5.

Stolpersteine:

- Die Zulassung ist bewusst synchron. Ein `await` vor dem Setzen von `_busy` würde doppelte Starts zulassen. Die Kommentare in `job.py` erklären das.
- Ohne Timeout bliebe `_busy` bei einer hängenden Kamera dauerhaft gesetzt. Deshalb läuft die Erkennung in `wait_for` (Kommentar in `job.py`, Methode `_run`).

**Tests**
- `tests/test_job_runner.py`: Zulassung, Timeout, Wahrheit des Ergebnisses.
- `tests/test_recipe_routing.py`: Zulassung und Routing der RecipeIds.
- `tests/test_payload.py`: Aufbau des Ergebnis-JSON.
- `tests/test_layer2_run.py`: Lauf gegen Fakes, auch Stopp und Ablehnungen.

**Weiterlesen**
- [vision-server-interface.md](vision-server-interface.md) §4 (Handshake: ein Job), §5 (StartSingleJob, Stop, Fehlercodes), §6 (Ergebnis-Payload)
- [part10-programm-schnittstelle.md](part10-programm-schnittstelle.md) §5 (Ablauf), §6 (Events), §7 (Fehler), §8 (Ergebnis holen)

---

### Erkennungsquellen (detection/)

**Zweck**
Eine Erkennungsquelle liefert die Erkennungen für einen Job und kennt kein OPC UA. Pro Profil gibt es eine Instanz, Rezepte mit gleichem Profil teilen sich Quelle und Kamera. Die AprilTag-Quelle stellt zusätzlich Messungen für Layer 2, Hand-Auge, Einmessen und Überwachung bereit.

**Einstieg**
`build_detection_sources()` in `runner.install_vision_machine()` erzeugt die Instanzen. `JobRunner._run()` ruft `acquire_and_detect()`. Die Installer rufen Messmethoden der AprilTag-Quelle auf (`measure_tags`, `measure_board`, `measure_tag_reference`, `observe_cell`, `apply_*`).

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/detection/__init__.py` | Registry von Profilname zu Fabrik. Die Fabriken importieren ihr Modul erst beim Aufruf | `DETECTION_SOURCES`, `build_detection_sources()`, `JOBS_DIR` |
| `src/vision_server/detection/base.py` | Schnittstelle `DetectionSource` (ABC) mit Datenklassen und einem eigenen Ein-Thread-Executor für blockierende Arbeit | `DetectionSource` (`acquire_and_detect`, `open`, `close`, `run_blocking`), `Detection`, `DetectionRequest` |
| `src/vision_server/detection/apriltag.py` | Profil `apriltag`: Kamera öffnen, Kalibrierung und Tag-Map laden, Bilder auswerten, Modulposen liefern, Messungen für andere Sitzungen. Zweitgrößte Datei des Pakets | `AprilTagDetectionSource` (`open`, `acquire_and_detect`, `measure_tags`, `measure_tag`, `measure_board`, `measure_board_view`, `measure_tag_reference`, `plan_tag_reference_view`, `observe_cell`, `apply_calibration`, `apply_tag_map`, `apply_hand_eye`), `TagSnapshot`, `robot_pose_from_parameters()` |
| `src/vision_server/detection/script_runner.py` | Profil `calibration`: startet `src/jobs/calibrate.py` als Subprozess. Dessen stdout wird die Nachricht des Ergebnisses | `ScriptDetectionSource`, `TERMINATE_GRACE_S` |

**Ablauf (AprilTag-Quelle)**

1. `open()` beim Start: Kalibrierdatei laden. Fehlt sie, gibt es zwei Ausnahmen. Die RealSense liefert ihre Werks-Intrinsik. Sonst bricht `open()` ab, außer `VISION_ALLOW_PLACEHOLDER_CALIBRATION` ist gesetzt. Danach wird die Tag-Map geladen. Ist sie unbrauchbar, läuft die Quelle ohne Karte weiter, und Tags heißen `TAG-<id>`. Am Hand-Pi wird zusätzlich die Hand-Auge-Datei geladen. Dann wird der Detektor gebaut und die geteilte Kamera geöffnet.
2. `acquire_and_detect()`: `samples_per_job` frische Bilder, nur solche nach dem Jobstart. Je Bild läuft `_locate()` im Worker-Thread. Dabei werden Tag-Posen geschätzt, die Kamerapose aus den Welttags bestimmt, optional am Anker fortgeschrieben und die Module lokalisiert.
3. Die Samples werden gemittelt und je Modul zusammengeführt. Fehlende Module werden gemeldet.
4. Ohne Welttag bleiben die Posen im Kamera-Bezugsrahmen, und das Log warnt. Ohne erkanntes Modul endet der Job mit `DETECTION_FAILED`.

`ScriptDetectionSource` liefert immer eine Platzhalter-Erkennung. Sie enthält die Nachricht des Scripts und keine echte Pose.

**Konfiguration**

- `AprilTagProfileConfig` (`profiles.py`): `tag_family` (Standard `tag36h11`), `detector_backend` (`aruco`), `tag_size_m`, `samples_per_job`, `max_reproj_error_px`, `capture_timeout_s`, `subpixel_corner_refinement` (Standard aus), `detector_parameters`, `resolution`, `frame_id`, `frame_convention`, `calibration_path`, `tag_map_path`, `hand_eye_path`, `allow_placeholder_calibration`, `allow_resolution_mismatch`, `apriltag_quad_decimate`.
- Pi-Presets je Rahmen in `server.py` (`PI_APRILTAG_PRESETS`). Decke: 3 Samples je Job, Fehlergrenze 3,0 px. Hand: 5 Samples, 1,5 px, Subpixel-Ecken an.
- Kalibrierdatei `data/calibration/<Rahmen>.json`, Tag-Map `config/tagmap.json`. Beide werden beim Start geladen. Die Konfigurations-ID der Ergebnisse setzt sich aus Tag-Familie, Kalibrier- und Tag-Map-Identität zusammen (`tagloc.identity`).
- `JOBS_DIR` ist `src/jobs/`.

Stolpersteine:

- `src/jobs/calibrate.py` ist laut Docstring eine Bereitschaftsprüfung, keine Kalibrierung. Es ordnet Hostnamen anders zu (`pi-decke`, `pi-hand`) als `PI_IDENTITIES` in `server.py`. Ohne `VISION_FRAME_ID` meldet es auf den Pis den Rahmen `world`. Die Datei liegt außerhalb dieses Pakets und ist hier nur vermerkt.
- Eine neue Erkennungsquelle braucht eine Klasse in `detection/` und einen Eintrag in `DETECTION_SOURCES` (siehe [vision-system.md](vision-system.md), Abschnitt „Aufbau“).
- Bausteine aus `tagloc`, die die Quelle benutzt (hier nur genannt): `tagloc.detector` (`build_detector`), `tagloc.calibration`, `tagloc.tagmap`, `tagloc.pose`, `tagloc.localize`, `tagloc.geometry`, `tagloc.identity`, `tagloc.handeye`, `tagloc.boards`, `tagloc.reference_edges`.

**Tests**
- `tests/test_apriltag_source.py`: AprilTag-Quelle ohne echte Kamera.
- `tests/test_detection_source.py`: Schnittstelle, `run_blocking`, Lebenszyklus.
- `tests/test_script_runner.py`: Abbruchpfad des Subprozesses.
- `tests/test_detector.py`: Subpixel-Ecken als Konfigurationsflag.
- `tests/test_handeye.py`: Ankern am Welttag, danach Messen ohne Welttag im Bild.

**Weiterlesen**
- [apriltag-lokalisierung.md](apriltag-lokalisierung.md) (Konzept), [apriltag-referenz.md](apriltag-referenz.md) (Referenz)
- [kamera-und-detektor-analyse.md](kamera-und-detektor-analyse.md), [arbeitsplaene/kamera-controls-und-detektor-parameter.md](arbeitsplaene/kamera-controls-und-detektor-parameter.md)
- [arbeitsplaene/apriltag-welttag-konzept.md](arbeitsplaene/apriltag-welttag-konzept.md) (Welttags), [arbeitsplaene/apriltag-erkennung-unscharfe-tags.md](arbeitsplaene/apriltag-erkennung-unscharfe-tags.md)
- [vision-system-integration.md](vision-system-integration.md) (Zielplan, Erkennungsstrategie)

---

### Kamera, Gesundheit & Livestream

**Zweck**
Eine Kamera, ein Capture-Loop, mehrere Leser. Der Watchdog eskaliert bis zum Prozessabbruch, damit systemd neu startet, und der Kamerazustand wird als DeviceHealth in die Anlagensicht geschrieben. Der Livestream läuft über OPC UA und zusätzlich flüssiger per MJPEG über HTTP.

**Einstieg**
`SharedCamera.open()` wird von der AprilTag-Quelle aufgerufen. `runner._start_camera_stream()`, `_start_camera_health()` und `_start_mjpeg_server()` bauen den Livestream auf. `_install_save_raw_image()` legt `SaveRawImage` an.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/camera.py` | Geteilte Kamera: Capture-Loop, Backends `picamera2`, `realsense` und `opencv`, Watchdog mit Neuöffnung und Prozessabbruch, Kamera-Controls | `SharedCamera` (`open`, `latest_frame`, `status`, `set_give_up_handler`, `close`), `CameraFrame`, `CameraStatus`, `exit_process()`, `picamera2_video_configuration()`, `resolve_camera_controls()`, `validate_camera_controls()`, `format_camera_metadata()`, `yuv420_to_bgr()` |
| `src/vision_server/camera_health.py` | Watchdog-Zustand auf DI-DeviceHealth abbilden, Alarm beim Wechsel, Schreiben in die Anlagensicht | `device_health()`, `CameraHealthPublisher`, `HealthAlarms`, `write_device_health()` |
| `src/vision_server/camera_stream.py` | Livestream-Publisher: Frame, optional Overlay, Base64-JPEG. Dazu Knoten für Modus, Fortschritt und Vorschau | `CameraStreamPublisher` (`start`, `stop`, `latest`, `viewer`, `next_frame`), `_encode_jpeg_base64()`, `_resize_for_stream()` |
| `src/vision_server/stream_overlay.py` | Zeichnet auf eine Kopie des Bilds: Tags, Brettecken, entzerrtes Bild, Einmess-Vorschau | `AprilTagStreamAnnotator` (`annotate`, `apply_calibration`, `set_calibration_session`, `set_busy`, `set_overlay_paused`, `set_reference_preview`), `_scale_corners()` |
| `src/vision_server/mjpeg_server.py` | MJPEG über HTTP mit asyncio, ohne Framework. Drei GET-Pfade, siehe [vision-server-interface.md](vision-server-interface.md) §10.2 | `MjpegServer` (`start`, `stop`, `port`) |
| `src/vision_server/raw_image.py` | `SaveRawImage`: aktuelles Vollbild verlustfrei als PNG | `save_raw_image()` |

**Ablauf**

1. `SharedCamera.open()` startet einen Worker-Thread. Der öffnet die Hardware und wartet `warmup_s`. Danach nimmt der Loop mit `capture_fps` auf. Jede Aufnahme hat ein Zeitlimit (`frame_timeout_s`).
2. Watchdog: Fehlschläge werden gezählt. Nach `max_capture_failures` wird neu geöffnet. Ein Hänger zählt sofort voll. Nach `max_reopen_attempts` ohne Bild ruft der Watchdog den Give-up-Handler auf. Der Standard `exit_process()` beendet den Prozess hart, systemd startet ihn neu. Ein hängender Worker-Thread lässt sich in Python nicht beenden. Er bleibt zurück.
3. Livestream: Der Publisher tickt mit `stream_fps`. Sind MJPEG-Zuschauer verbunden, tickt er mit `http_fps`. Das Overlay läuft im Worker-Thread mit dem Zeitlimit `overlay_timeout_s`. Bei Überschreitung geht das Rohbild raus. Kodiert wird nur, wenn ein neues Bild da ist.
4. Overlay: Der Annotator erkennt nicht im Stream-Takt. Er zeichnet das letzte Ergebnis in voller Auflösung, das Job, Überwachung oder Messung ohnehin berechnet haben. Eine eigene Erkennung läuft selten, im Hintergrund, nie während eines Jobs (`set_busy`). Sie nutzt eine eigene Detektor-Instanz ohne Eckenverfeinerung (`runner._build_annotator()`).
5. Kamerazustand: Der Publisher schreibt nur bei einem Zustandswechsel. Vor dem Prozessabbruch schreibt er noch ein letztes FAILURE, mit einer Frist von 1 s.
6. MJPEG: Der Server bindet nach dem Start. Gelingt das nicht, läuft der Server ohne MJPEG weiter. Der Portknoten bleibt dann 0.
7. `SaveRawImage` wird abgelehnt, solange ein Job läuft. Die Datei heißt `<Rahmen>_<Zeit>.png`. Vorhandene Dateien werden nie überschrieben.

**Konfiguration**

- `CameraStreamConfig` (`profiles.py`): `backend` (Werte aus `CAMERA_BACKENDS`), `resolution` (Hauptstrom für Jobs), `preview_resolution` (Livestream-Strom, nur `picamera2`), `camera_controls` (nur `picamera2`, sonst Warnung), `warmup_s` (2 s), `capture_fps` (15), `stream_fps` (5), `http_fps` (15), `max_stream_width` (960), `overlay_interval_s` (0,5 s), `overlay_detect_interval_s` (5 s), `overlay_timeout_s` (2 s, im Decken-Preset 8 s), `frame_timeout_s` (3 s), `max_capture_failures` (3), `max_reopen_attempts` (2), `stale_frame_s` (2 s), `health_interval_s` (0,5 s), `jpeg_quality` (70), `realsense_resolution`, `realsense_fps`, `stream_enabled`, `camera_index`, `http_port` (Wert siehe [§10.2](vision-server-interface.md)).
- Pi-Presets: `PI_CAMERA_STREAM_PRESETS` in `server.py`. Decke: Livestream-Strom 960 × 720 mit 10 fps, zwei Puffer, Belichtungs-, Schärfe- und Kontrast-Controls.
- Umgebung: `VISION_DISABLE_STREAM`, `VISION_CAMERA_CONTROLS`, `VISION_CAMERA_BACKEND` (siehe „Start & Konfiguration“).
- Die Modi des Livestreams stehen in `src/tagloc/modes.py` (`OVERLAY_MODES`). Der Modul-Docstring von `camera_stream.py` nennt nicht alle Modi. Die Bedeutung je Modus steht in [vision-server-interface.md](vision-server-interface.md) §10.1.
- Pfad: `data/rohbilder/` für `SaveRawImage`.

Stolpersteine:

- Eine Kamera ist nur einmal offen. Quelle, Livestream und Sitzungen teilen dieselbe Instanz. Ein zweiter Zugriff schlägt fehl.
- Der Livestream sagt nichts über den Kamerazustand. Dafür ist DeviceHealth zuständig.

**Tests**
- `tests/test_camera.py`: Backend-Auswahl, Kamera-Controls, Schließen der Hardware.
- `tests/test_shared_camera.py`: Watchdog der geteilten Kamera.
- `tests/test_camera_health.py`: Abbildung des Zustands, Alarme.
- `tests/test_camera_stream.py`, `tests/test_mjpeg_server.py`: Publisher und HTTP.
- `tests/test_stream_overlay.py`, `tests/test_stream_calibration_overlay.py`: Overlay-Modi.
- `tests/test_camera_owner.py`, `tests/test_raw_image.py`: Eigentümer der Kamera und `SaveRawImage`.

**Weiterlesen**
- [arbeitsplaene/livestream-mjpeg-und-watchdog.md](arbeitsplaene/livestream-mjpeg-und-watchdog.md), [arbeitsplaene/livestream-tags-volle-aufloesung.md](arbeitsplaene/livestream-tags-volle-aufloesung.md), [arbeitsplaene/stream-overlay-schlank.md](arbeitsplaene/stream-overlay-schlank.md)
- [arbeitsplaene/opcua-kamera-condition-monitoring.md](arbeitsplaene/opcua-kamera-condition-monitoring.md) (DeviceHealth), [arbeitsplaene/rohbild-speichern.md](arbeitsplaene/rohbild-speichern.md)
- [vision-server-interface.md](vision-server-interface.md) §10 (Livestream), §11.4 (Zustand), §18 (SaveRawImage)

---

### Kalibrier-Sitzung

**Zweck**
Kamera-Kalibrierung über OPC UA, während Server und Livestream weiterlaufen. Der Operator löst die Aufnahmen manuell aus, die Auswertung läuft danach im Hintergrund, und das Ergebnis wird sofort in die laufende Quelle übernommen. Der alte Weg (`tagloc.cli.calibrate`) öffnet die Kamera selbst und verlangt einen gestoppten Server.

**Einstieg**
Die Methoden `StartCalibration`, `CaptureCalibrationSample`, `FinishCalibration` und `AbortCalibration` legt `runner.install_vision_machine()` an. Testclient: `src/vision_server/tools/calibration_client.py`. Aufnahme per Tastendruck: `src/vision_server/tools/stream_viewer.py`.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/calibration_session.py` | Aufnahmen merken, Auswertung im Hintergrund, Speichern, Fortschritt | `CalibrationSession` (`start`, `capture`, `finish`, `abort`, `progress`, `_process`, `_compute_and_save`), `MIN_SAMPLES_FOR_CALIBRATION`, `RMS_WARNING_PX`, `CAPTURE_DETECT_TIMEOUT_S`, `_save_capture_image()`, `_lazy()` |
| `src/vision_server/runner.py` (Teil) | Methoden und Sperren, Live-Übernahme, Fortschrittsknoten während der Auswertung | `install_vision_machine()`, `_pause_stream_during_processing()`, `_write_calibration_progress()`, `_calibration_info_payload()` |
| `src/vision_server/profiles.py` (Teil) | Schachbrett-Geometrie, Linsenmodell, Archiv | `AprilTagProfileConfig.calibration_board_*`, `calibration_model`, `calibration_min_samples`, `calibration_capture_dir` |

**Ablauf**

1. `StartCalibration` wird abgelehnt, wenn ein Job, ein Layer-2-Lauf, eine Hand-Auge-Sitzung oder eine laufende Auswertung aktiv ist. Der Server muss `Operational` und `Ready` sein. Dann beginnt eine neue Sitzung, und der Ordner `data/calibration/<Rahmen>_pending/` wird geleert.
2. `CaptureCalibrationSample` speichert den aktuellen Frame als JPEG. Die Brettsuche findet hier noch nicht statt. Sie würde den Methodenaufruf so lange blockieren, dass der Client die Verbindung aufgeben kann.
3. `FinishCalibration` kehrt sofort zurück. Bei weniger als drei Aufnahmen kommt `DETECTION_FAILED`. Sonst startet die Auswertung im Hintergrund. Der Livestream pausiert dafür, damit der Pi die CPU hat.
4. `_process()` sucht je Aufnahme das Brett, mit einem Zeitlimit von 20 s. `calibrate_from_samples` (tagloc) rechnet die Kalibrierung, `compute_coverage` die Abdeckung. `save_calibration` schreibt `data/calibration/<Rahmen>.json`. `apply_calibration` übernimmt sie in die laufende Quelle. Der Infoknoten wird aktualisiert.
5. `CalibrationProgress` meldet den Stand laufend. Während der Auswertung steht dort `processing`, am Ende das Ergebnis.
6. `AbortCalibration` verwirft alles, ohne zu speichern.

**Konfiguration**

- Schachbrett (`AprilTagProfileConfig`): `calibration_board_type` (`chessboard`), `calibration_board_cols` und `calibration_board_rows` (innere Ecken), `calibration_board_square_size_m`. Pi-Wert: 7 × 10 innere Ecken, 23 mm.
- `calibration_model`: `pinhole` (Brown-Conrady) oder `fisheye` (Kannala-Brandt). Pi: Decke `fisheye`, Hand `pinhole`. Überschreibbar mit `VISION_CALIBRATION_MODEL`. Für die Posenschätzung zählt das Modell in der geladenen Datei, nicht das Feld.
- `calibration_capture_dir`: zusätzliches Archiv. Es ist nur mit `VISION_SAVE_CALIBRATION_CAPTURES=1` aktiv und liegt in `data/calibration/<Rahmen>_captures/`.
- Konstanten: `MIN_SAMPLES_FOR_CALIBRATION` = 3 (darunter Ablehnung), `CAPTURE_DETECT_TIMEOUT_S` = 20 s, `RMS_WARNING_PX` = 0,5 (nur Warnung im Log).

Stolpersteine:

- `calibration_min_samples` (Standard 15) lehnt nichts ab. Der Wert erscheint nur als `minSamples` im Fortschritt. Die Ablehnung erfolgt bei weniger als drei Aufnahmen. Der Kommentar am Feld in `profiles.py` beschreibt das anders.
- Eine neu berechnete Kalibrierung gilt sofort für den nächsten Job. Eine ältere Datei behält ihr Modell, bis sie ersetzt wird.

**Tests**
- `tests/test_calibration_session.py`: Sitzung ohne Kamera, über Fakes.
- `tests/test_calibration_info.py`: Infoknoten.
- `tests/test_stream_calibration_overlay.py`: Kalibriermodus im Stream.
- `tests/test_calibration_io.py`: Kalibrierdateien (zum großen Teil tagloc).

**Weiterlesen**
- [vision-server-interface.md](vision-server-interface.md) §12 (Interaktive Kalibrierung, 12.1 bis 12.10)
- [arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md](arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md)
- [apriltag-e2e-test.md](apriltag-e2e-test.md) §3.3 (Kalibrieren mit der Pi-Kamera)

---

### Hand-Auge-Sitzung

**Zweck**
Bestimmt `T_flansch_cam`, also die feste Lage der Handkamera am Roboterflansch. Dafür werden Paare aus gemeldeter Flanschpose und gemessenem Tag oder Schachbrett gesammelt. Das Ergebnis wird gespeichert und sofort wirksam, die Sitzung gibt es nur am Hand-Pi.

**Einstieg**
`StartHandEyeCalibration`, `CaptureHandEyeSample`, `FinishHandEyeCalibration` und `AbortHandEyeCalibration` legt `runner._install_hand_eye_session()` an. Die Steuerung meldet je Aufnahme die Flanschpose. Die Methoden entstehen nur, wenn `hand_eye_path` gesetzt ist.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/handeye_session.py` | Sitzung: Aufnahmen, Berechnung mit Ausreißerprüfung, optionale Verfeinerung am Brett, Speichern mit Sicherung, Datensatz je Sitzung | `HandEyeSession` (`start`, `capture`, `finish`, `abort`, `status_json`), `HandEyePorts`, `parse_start()`, `parse_sample()`, `save_with_backup()`, `_refine_board()`, `_write_session_dataset()`, `_intrinsics_warnings()` |
| `src/vision_server/runner.py` (Teil) | Installer und Sperren | `_install_hand_eye_session()` |
| `src/vision_server/detection/apriltag.py` (Teil) | Messung des Ziels im Kamera-Bezugsrahmen, Anwendung der neuen Kalibrierung | `measure_tag()`, `measure_board()`, `apply_hand_eye()`, `intrinsics_source()` |

**Ablauf**

1. `StartHandEyeCalibration` legt das Ziel fest, Tag oder Brett.
2. `CaptureHandEyeSample` misst das Ziel zur gemeldeten Flanschpose. Die Antwort kommt erst danach. `OK` heißt angenommen. `DETECTION_FAILED` heißt verworfen, der Grund steht im Status.
3. `FinishHandEyeCalibration` prüft zuerst, ob der Flansch um alle drei Achsen gekippt wurde. Fehlt das, bleibt die Sitzung offen. Dann rechnet die robuste Lösung aus `tagloc.handeye`. Liegt ein Schachbrett vor, folgt eine Verfeinerung gegen die Brettecken. Weicht der Brettmaßstab um mehr als 0,3 % von 1 ab, gibt es eine Warnung.
4. Gespeichert wird nach `data/handeye/cam_flange.json`. Die alte Datei bleibt als Sicherung daneben. Der Datensatz der Sitzung liegt unter `data/handeye/sessions/<Zeit>/`. Die neue Kalibrierung wird sofort übernommen (`apply_hand_eye`).
5. `AbortHandEyeCalibration` verwirft die Sitzung.

Sperren: Eine Hand-Auge-Sitzung blockiert Kamera-Kalibrierung, `SetTagMap` und Layer 2. `MeasureTags` läuft währenddessen weiter. `MeasureTagReference` ist gesperrt.

**Konfiguration**

- `hand_eye_path` (nur `cam_flange`): `data/handeye/cam_flange.json`. Gesetzt wird der Pfad in `apriltag_config()` in `server.py`.
- Konstanten in `handeye_session.py`: `RECOMMENDED_SAMPLES` = 8 (Empfehlung an den Bediener), `REQUIRED_AXES` = 3, `SCALE_WARNING` = 0,003. `MIN_SAMPLES` und `AXIS_THRESHOLD_DEG` kommen aus `tagloc.handeye`.
- Brettgeometrie: aus den `calibration_board_*`-Feldern der Quelle.

Stolpersteine:

- `handeye_session.py` importiert zwei private Hilfen aus `layer2/plan.py` (`_load`, `_pose`). Die beiden Module sind damit eng gekoppelt.

**Tests**
- `tests/test_handeye_session.py`, `tests/test_handeye_robust.py`: Sitzung mit Fakes, Ausreißer.
- `tests/test_handeye_session_refine.py`, `tests/test_handeye_refine.py`: Verfeinerung am Brett.
- `tests/test_handeye_opcua.py`: Methoden, Statusknoten, Datei.
- `tests/test_handeye.py`: Ankern am Welttag, danach Messung ohne Welttag.
- `tests/test_measureboard.py`: Brettmessung, die die Sitzung nutzt.

**Weiterlesen**
- [arbeitsplaene/handeye-ueber-opcua.md](arbeitsplaene/handeye-ueber-opcua.md), [arbeitsplaene/handeye-schachbrett-automatik.md](arbeitsplaene/handeye-schachbrett-automatik.md), [arbeitsplaene/handeye-genauigkeit.md](arbeitsplaene/handeye-genauigkeit.md)
- [arbeitsplaene/apriltag-hand-auge-ankern.md](arbeitsplaene/apriltag-hand-auge-ankern.md)
- [vision-server-interface.md](vision-server-interface.md) §15 (Hand-Auge über OPC UA)
- [apriltag-e2e-test.md](apriltag-e2e-test.md) §3.6a

---

### Layer 2

**Zweck**
Verankert die Zelle am Welttag und misst danach jedes Modul aus der Nähe mit der Handkamera. Die Steuerung plant und fährt, der Pi rechnet die Fahrziele (`plan.py`) und führt den Ablauf aus (`run.py`). Jeder Schritt ist ein gewöhnlicher AprilTag-Job mit denselben Events und Ergebnissen wie `StartSingleJob`.

**Einstieg**
`StartLayer2Run` und `ReportRobotPose` legt `runner._install_layer2_run()` an. Der Lauf existiert nur, wenn die AprilTag-Quelle offen ist und die Layer-2-Knoten vorhanden sind. Das ist nur am Hand-Pi der Fall. `Layer2Run` hängt sich über `JobRunner.add_finish_listener` an das Job-Ende.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/layer2/__init__.py` | Paketbeschreibung, keine Logik. Die Module werden erst bei Bedarf importiert | – |
| `src/vision_server/layer2/plan.py` | Fahrziele: Welttag und Modultag durch dieselbe Rechnung. Annäherung, schräge Ansicht, Erreichbarkeit | `plan_run()`, `plan_step()`, `view_target()`, `tag_facing_base()`, `find_detection()`, `parse_run_request()`, `parse_report()`, `pose_parameters()`, `RunRequest`, `PlannedStep`, `ANCHOR`, `MEASURE`, `APPROACH` |
| `src/vision_server/layer2/run.py` | Ablaufsteuerung als asyncio-Zustandsmaschine ohne asyncua. Der Server wird über `Layer2Ports` angeschlossen | `Layer2Run` (`start`, `report`, `on_job_finished`, `abort`, `status_json`, `target_json`), `Layer2Ports`, `_Step`, `_State` |
| `src/vision_server/runner.py` (Teil) | Verdrahtung: Ports, Methoden, Sperren | `_install_layer2_run()` |

**Ablauf**

1. Schritt 0 zielt auf einen Welttag. Gelingt der Anker-Job, entsteht der Anker. Danach kommen die Module.
2. Jeder Schritt beginnt mit einer Annäherung aus größerem Abstand. Deren Bild liefert die Tag-Pose, aus der der nahe Schritt neu geplant wird.
3. Der Pi veröffentlicht das Fahrziel. Die Steuerung fährt und meldet die erreichte Pose (`ReportRobotPose`). Dann startet der Pi einen AprilTag-Job mit dieser Pose.
4. Am Job-Ende zählt der Anker als geglückt, wenn der Job einen neuen Anker hinterlassen hat. Der Ergebniscode des Jobs spielt dabei keine Rolle. Danach werden alle folgenden Schritte mit der gemessenen Basis neu geplant (`_replan_after`).
5. Ein Ziel außerhalb der Reichweite wird gezeigt und übersprungen, nicht versteckt.
6. `Stop`, `Abort` oder ein Serverstopp brechen den Lauf ab.

Schräge Ansicht: Liegt der frontale Blick außerhalb der Reichweite, kippt die Kamera in Stufen bis zum maximalen Kippwinkel zur Basis hin.

**Konfiguration** (Standards in `plan.py`)

- `DEFAULT_STANDOFF_M` = 0,3 m (Abstand des nahen Bilds).
- `DEFAULT_APPROACH_STANDOFF_M` = 0,6 m (Abstand der Annäherung, 0 schaltet sie ab).
- `DEFAULT_REACH_M` = 0,85 m (Reichweite, nur als Hinweis).
- `DEFAULT_MAX_TILT_DEG` = 60,0 (größter Kippwinkel).
- Anfrageparameter: siehe [vision-server-interface.md](vision-server-interface.md) §14.

Stolpersteine:

- Ein Lauf sperrt Kamera-Kalibrierung, `SetTagMap` und Hand-Auge. Der Grund: Eine neue Tag-Map würde den Anker des Laufs verwerfen.

**Tests**
- `tests/test_layer2_plan.py`: Fahrziele, Welttag und Modultag.
- `tests/test_layer2_run.py`: Ablauf gegen Fakes, Anker, Neuplanung, Abbruch.
- `tests/test_layer2_opcua.py`: Methoden und Job-Start.

**Weiterlesen**
- [arbeitsplaene/layer2-lauf-hand-pi.md](arbeitsplaene/layer2-lauf-hand-pi.md), [arbeitsplaene/layer2-annaeherung.md](arbeitsplaene/layer2-annaeherung.md), [arbeitsplaene/layer2-schraege-ansicht.md](arbeitsplaene/layer2-schraege-ansicht.md)
- [vision-server-interface.md](vision-server-interface.md) §14 (Layer-2-Lauf)

---

### Messungen auf Anfrage (MeasureTags, MeasureBoard)

**Zweck**
Zwei Methoden, die nur messen und keine Fahrbefehle schicken. `MeasureTags` liefert Tags im Kamera-Bezugsrahmen, damit die Steuerung den nächsten Flanschpunkt rechnet (Layer 2). `MeasureBoard` liefert das Schachbrett im Bild, damit die Steuerung es vor einer Hand-Auge-Aufnahme zentrieren kann.

**Einstieg**
`MeasureTags` legt `runner._install_measure_tags()` an, nur am Hand-Pi. `MeasureBoard` legt `runner._install_measure_board()` an, an beiden Pis. Die Steuerung ruft beide direkt auf. Beide antworten mit dem Ergebnis.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/measuretag.py` | Anfrage lesen, Tag-Größe nachschlagen (Anfrage, dann Tag-Map, dann Rückfallwert), Ergebnis bauen, Randnähe und Blickwinkel | `parse_request()`, `build_result()`, `SizeLookup`, `near_edge()`, `view_angle_deg()`, `DEFAULT_FRAMES`, `MAX_FRAMES`, `EDGE_MARGIN` |
| `src/vision_server/measureboard.py` | Brettmessung: ganzes Brett (Pose, Ecken), Teilraster (Schwerpunkt, Bildränder) oder nichts | `parse_request()`, `build_result()`, `FrameBoard`, `BoardRequest` |
| `src/vision_server/detection/apriltag.py` (Teil) | Die Messungen selbst | `measure_tags()`, `measure_board_view()`, `_observe_board()` |
| `src/vision_server/runner.py` (Teil) | Installer und Sperren | `_install_measure_tags()`, `_install_measure_board()`, `_Activity` |

**Ablauf**

- Beide nehmen nur Bilder, die nach dem Aufruf eintreffen. Ein älteres Bild aus dem Puffer würde an der falschen Stelle messen.
- `MeasureTags` mittelt über einige Bilder (Standard 3, höchstens 10). Ohne Welt-Bezugsrahmen und ohne Anker.
- `MeasureBoard` sucht auf einem auf 640 Pixel verkleinerten Bild. Ein Fehlversuch kostet so am Pi wenig. Danach werden die Ecken im vollen Bild nachgeschärft.
- Ein Tag oder Brett nahe am Bildrand wird gemeldet. Die Warnung kommt, solange er noch ganz im Bild ist.

Sperren: `MeasureTags` ist nur für Job und Kamera-Kalibrierung gesperrt. Es läuft also auch während einer Hand-Auge-Sitzung. `MeasureTags` und `MeasureTagReference` sperren sich gegenseitig. `MeasureBoard` steht außerhalb dieser Sperre und ist während Job und Kalibrier-Auswertung gesperrt.

**Konfiguration**

- `DEFAULT_FRAMES` = 3, `MAX_FRAMES` = 10 (`measuretag.py`).
- `EDGE_MARGIN` = 0,05 (Anteil der Bildgröße, ab dem ein Tag als randnah gilt).
- `OBSERVE_MAX_SIDE` = 640 (`detection/apriltag.py`).
- Brettgeometrie aus den `calibration_board_*`-Feldern.
- Tag-Größe: Anfrage, dann Tag-Map, dann der Rückfallwert `tag_size_m`.

**Tests**
- `tests/test_measuretag.py`: Tags aus frischen Bildern, Ergebnis.
- `tests/test_measureboard.py`: ganzes Brett, Teilraster, zu kleine Rasterangabe.

**Weiterlesen**
- [arbeitsplaene/layer2-measuretags.md](arbeitsplaene/layer2-measuretags.md), [arbeitsplaene/hand-auge-brett-zentrieren.md](arbeitsplaene/hand-auge-brett-zentrieren.md)
- [vision-server-interface.md](vision-server-interface.md) §15.4 (MeasureBoard), §17 (MeasureTags)

---

### Einmessen (Tag-Referenz)

**Zweck**
Misst die Position eines Tags auf einem Modul über dessen Bezugskanten im Bild, ohne Roboterkinematik. Die Steuerung schickt den Katalog-Startwert und die Kanten, der Pi liefert die korrigierte Tag-Pose. Dazu gibt es eine Live-Vorschau im Livestream und ein Mess-Protokoll.

**Einstieg**
`PlanTagReferenceView`, `MeasureTagReference` und `SetTagReferencePreview` legt `runner._install_tag_reference()` an. Die Steuerung ruft sie direkt auf. Die Methoden gibt es nur am Hand-Pi.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/tagreference.py` | Anfrage prüfen, Tag-Pose aus mehreren Bildern, Korrektur, Zusammenfassung, Hinweise, Ergebnis und Ansichtsplan bauen | `parse_request()`, `combine_tag_pose()`, `keep_guess_rotation()`, `summarize_lines()`, `reference_warnings()`, `build_result()`, `build_view()`, `TagReferenceRequest` |
| `src/vision_server/tagreference_preview.py` | Zustand der Live-Vorschau, ohne Threads: Anfrage, Ablaufzeit, Status | `ReferencePreview`, `ReferenceView`, `build_status()`, `PREVIEW_TTL_S`, `MEASURE_HOLD_S` |
| `src/vision_server/tagreference_log.py` | Mess-Protokoll je Messung, Aufräumen alter Ordner | `write_protocol()`, `prune()`, `latest_overlay()`, `MAX_FOLDERS`, `OVERLAY_NAME` |
| `src/vision_server/detection/apriltag.py` (Teil) | Messung in der Quelle: Tag erkennen, Kanten messen, Korrektur, Ansicht planen | `measure_tag_reference()`, `_measure_reference()`, `plan_tag_reference_view()` |
| `src/vision_server/stream_overlay.py` (Teil) | Live-Ansicht der Kantensuche im Modus `tagreference` | `set_reference_preview()`, `_tag_reference_view()`, `_evaluate_reference()` |

**Ablauf**

1. `PlanTagReferenceView` braucht kein Bild. Es rechnet mit der Kalibrierung aus, wo die Kamera stehen muss, damit die Kanten ins Bild passen. Es ist nie `BUSY`.
2. `SetTagReferencePreview` schaltet die Live-Kantensuche im Stream ein. Die Vorschau endet nach 120 s, wenn sie nicht erneuert wird. Eine leere Anfrage schaltet sie aus.
3. `MeasureTagReference` nimmt frische Bilder. Aus den Bildern mit dem Tag entsteht eine gemeinsame Tag-Pose. Im Rohbild werden die Kanten gemessen, die Korrektur wird je Bild berechnet und danach gemittelt.
4. Ist die Lage des Tags nicht eindeutig, bleibt die Drehung des Startwerts stehen, und es gibt eine Warnung. Fehlerfälle sind „kein frisches Bild“ und „Tag nicht gesehen“.
5. Standardmäßig schreibt die Messung ein Protokoll. Das Debug-Bild der letzten Messung liefert der MJPEG-Server (siehe [vision-server-interface.md](vision-server-interface.md) §10.2).

Sperren: `MeasureTagReference` ist gesperrt bei Job, Kamera-Kalibrierung, Hand-Auge-Sitzung, `MeasureTags` und einer weiteren Einmessung. `PlanTagReferenceView` sperrt nichts und liefert nie `BUSY`. Es braucht nur den äußeren Zustand `Operational`.

**Konfiguration**

- `tag_reference_debug_dir` (Standard `data/tagreference/`). `None` schreibt nichts.
- `MAX_FOLDERS` = 50 Protokollordner. Die ältesten werden gelöscht.
- Vorschau: `PREVIEW_TTL_S` = 120 s, `PREVIEW_INTERVAL_S` = 1/3 s, `PREVIEW_HOLD_S` = 1 s, `MEASURE_HOLD_S` = 5 s.
- Standardwerte in `tagreference.py`: `DEFAULT_FRAMES` = 3, `MAX_FRAMES` = 10, `DEFAULT_SEARCH_BAND_M` = 0,015 m (Grenzen 0,003 bis 0,05 m), `DEFAULT_TILT_DEG` = 10, `MAX_TILT_DEG` = 80, `MAX_LINES` = 4.

**Tests**
- `tests/test_tagreference.py`: Anfrage, Ergebnis, Quelle, OPC UA.
- `tests/test_tagreference_liveview.py`: Live-Ansicht, Vorschau, Protokoll, HTTP.
- `tests/test_reference_edges.py`: Kantensuche und Korrektur (zum großen Teil tagloc).

**Weiterlesen**
- [arbeitsplaene/tag-einmessen-bezugskanten.md](arbeitsplaene/tag-einmessen-bezugskanten.md), [arbeitsplaene/tag-einmessen-liveview.md](arbeitsplaene/tag-einmessen-liveview.md)
- [vision-server-interface.md](vision-server-interface.md) §19 (MeasureTagReference, PlanTagReferenceView)

---

### Zellen-Überwachung

**Zweck**
Prüft laufend, ob Module in der Zelle verschoben, hinzugekommen oder entfernt wurden. Eine Änderung wird erst gemeldet, wenn sie bestätigt ist. Ein einzelnes schlechtes Bild oder ein Arm vor einem Tag löst keinen Alarm aus.

**Einstieg**
`StartCellWatch`, `StopCellWatch`, `PauseCellWatch` und `ResumeCellWatch` legt `runner._install_cell_watch()` an. Die Methoden gibt es nur an der Decke, also ohne `hand_eye_path`.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/cellwatch.py` | Logik ohne Uhr und ohne asyncua: Regeln für bewegt, neu und verschwunden. Start, Stopp, Pause und Takt. Kamerapose merken | `CellWatchTracker` (`update`, `pending`, `changes`, `stable_observation`, `detection_rate`, `visibility`), `CellWatch` (`start`, `stop`, `pause`, `resume`, `tick`, `status_json`), `CellWatchOptions`, `CameraPoseKeeper` (`judge`, `adopt`, `stored_for`, `load`), `CellWatchPorts`, `parse_start()`, `reference_from_payload()`, `camera_pose_from_payload()`, `sightings_from_locations()`, `is_flat()`, `conservative_rate()`, `required_misses()` |
| `src/vision_server/detection/apriltag.py` (Teil) | Beobachtung je Bild mit eigenem Detektor, ohne Seiteneffekte | `observe_cell()` |
| `src/vision_server/result_management.py` (Teil) | Die Referenz kommt aus dem Ergebnis eines früheren Jobs | `ResultStore.result_for_job()` |
| `src/vision_server/runner.py` (Teil) | Verdrahtung: Beobachtung, Veröffentlichung, Pausen bei Job und Kalibrierung | `_install_cell_watch()` |

**Ablauf**

1. `StartCellWatch` mit Optionen. Die Referenz kommt aus dem Ergebnis eines Jobs, dessen Id in der Anfrage steht. Der Start ist nur im Zustand `Operational` möglich.
2. Im Takt `interval_s` prüft der Pi das aktuelle Bild. `observe_cell` liefert je Tag die Modulpose. `CameraPoseKeeper.judge` filtert Bilder ohne klare Aussage heraus.
3. Mit zwei oder mehr Welttags schätzt der Pi die Kamerapose neu und merkt sie sich. Mit einem Welttag nutzt er die gemerkte Pose und prüft nur auf Konsistenz.
4. Der Tracker meldet bewegt, neu oder verschwunden erst, wenn der Befund lange genug anhält. Ein Tag gilt erst als verschwunden, wenn er in genug gültigen Bildern fehlt. Die Anzahl hängt von seiner Erkennungsrate ab.
5. `PauseCellWatch` pausiert, etwa während der Roboter fährt. Standardmäßig 600 s, höchstens 3600 s. Danach läuft die Überwachung von selbst weiter, auch ohne `ResumeCellWatch`.
6. Der Zustand steht im Statusknoten (Werte in [vision-server-interface.md](vision-server-interface.md) §16).

Während eines Jobs oder einer Kalibrierung pausiert die Überwachung automatisch. Sie hat einen eigenen Detektor, weil ein Detektor nicht für zwei Threads gebaut ist.

**Konfiguration**

- `CellWatchOptions` (Standards): `interval_s` 2 s, `persist_s` 5 s, `missing_persist_s` 20 s, `moved_threshold_m` 0,05, `rotated_threshold_deg` 10, `max_camera_error_px` 6, `min_world_tags` 1, `stored_pose_margin_px` 2. Die Anfrage kann sie ändern.
- Konstanten: `DEFAULT_PAUSE_S` 600, `MAX_PAUSE_S` 3600, `MIN_FRAMES_FOR_MISSING` 3, `MISS_BY_CHANCE` 1e-5, `MAX_REQUIRED_MISSES` 60, `FLAT_MAX_TILT_DEG` 30.
- Datei `data/calibration/cellwatch_camera_pose.json`. Sie überlebt Neustarts. Eine neue Tag-Map verwirft sie.

Stolpersteine:

- Welttags werden nicht überwacht. Die Quelle liefert sie gar nicht erst.
- Der Tracker bekommt die Zeitpunkte als Daten, er hat keine eigene Uhr. Deshalb laufen die Tests ohne Wartezeit.

**Tests**
- `tests/test_cellwatch.py`: Regeln, Hysterese, Pausen.
- `tests/test_cellwatch_posestore.py`: gemerkte Kamerapose über Neustarts.
- `tests/test_cellwatch_storedpose.py`: ein Welttag mit gespeicherter Pose.

**Weiterlesen**
- [arbeitsplaene/zellen-ueberwachung.md](arbeitsplaene/zellen-ueberwachung.md)
- [vision-server-interface.md](vision-server-interface.md) §16 (Zellen-Überwachung)

---

### Discovery (mDNS und LDS)

**Zweck**
Der Pi-Server ist auf zwei Wegen zu finden. mDNS macht ihn im Subnetz sichtbar, zum Beispiel für das Frontend. Die LDS-Anmeldung ist nötig, damit der Aggregation-Server der Zelle das Modul aufnimmt. Beide sind fehlertolerant: Scheitern sie, läuft der Server weiter und schreibt eine Warnung ins Log.

**Einstieg**
`server.main()` startet nach dem Serverstart die Kontextmanager `mdns.announce()` und `lds.register()`. Im Standalone-Lauf (`runner.run()`) gibt es keine Discovery.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/discovery/__init__.py` | Paket, exportiert `lds` und `mdns` | `__all__` |
| `src/vision_server/discovery/mdns.py` | mDNS-Ankündigung über zeroconf (optional), LAN-IPv4 ermitteln, Instanznamen bereinigen | `announce()`, `enabled()`, `detect_lan_ipv4()`, `sanitize_instance_name()`, `SERVICE_TYPE` |
| `src/vision_server/discovery/lds.py` | Anmeldung beim Local Discovery Server mit Erneuerung, Abmeldung beim Beenden | `register()`, `advertised_endpoint()`, `lds_url()`, `CELL_LDS_URL`, `DEFAULT_RENEW_SECONDS` |

**Ablauf**

1. Der Endpoint nennt die LAN-IPv4 (`advertised_endpoint()`). Gelauscht wird weiterhin auf allen Schnittstellen, weil `0.0.0.0` für Clients wertlos wäre.
2. `mdns.announce()`: kündigt den Dienst mit Pfad und Fähigkeiten an, sofern `OPCUA_MDNS` nicht aus ist. Fehlt zeroconf, kommt eine Warnung. Gibt es nur eine Loopback-Adresse, wird mDNS übersprungen.
3. `lds.register()`: meldet über `register_to_discovery` an und erneuert die Anmeldung regelmäßig (60 s). Nennt der Endpoint `0.0.0.0`, wird nicht angemeldet.
4. Beim Beenden wird die mDNS-Ankündigung zurückgezogen und die LDS-Anmeldung entfernt. `Server.stop()` allein meldet nicht ab. Deshalb geschieht die Abmeldung im `finally`.

**Konfiguration**

- `OPCUA_MDNS` (`0`, `false`, `no`, `off` schalten aus), `OPCUA_ADVERTISE_IP` (erzwingt die IPv4-Adresse), `OPCUA_MDNS_NAME` (Dienstname, Standard Vision-Identität), `OPCUA_LDS_URL` (leer schaltet die Anmeldung ab, eine andere URL ersetzt das Ziel). Die Variablen stehen im Abschnitt „Start & Konfiguration“.
- Konstanten: `CELL_LDS_URL` (Discovery-Server der Zelle), `DEFAULT_RENEW_SECONDS` = 60, `SERVICE_TYPE` (DNS-SD-Typ für OPC UA über TCP).
- Das Paket `zeroconf` ist optional. Die Warnung nennt die Installation.

Stolpersteine:

- Der Modul-Docstring von `mdns.py` verweist auf eine Betreuer-Kurzanleitung. Die Datei liegt nicht im Repo.

**Tests**
- `tests/test_discovery_lds.py`: Anmeldung, Erneuerung, Abmeldung, Schalter.
- `tests/test_discovery_mdns.py`: Schalter der Ankündigung.

**Weiterlesen**
- [lds-registrierung.md](arbeitsplaene/lds-registrierung.md) (Begründung der LDS-Anmeldung)
- [part10-programm-schnittstelle.md](part10-programm-schnittstelle.md) §2 (mDNS statt fest eingetragener IP)
- [vision-server-interface.md](vision-server-interface.md) §2 (Verbindungsdaten)

---

### Werkzeuge (tools/)

**Zweck**
Die Dateien in `src/vision_server/tools/` sind **Testclients und Handwerkzeuge**, und der Server benutzt keines davon. Die Testclients sprechen mit einem laufenden Server. `measure_framerate.py` braucht dagegen exklusiven Kamerazugriff, der Server muss vorher gestoppt sein.

**Einstieg**
Direkt als Skript, etwa `python3 src/vision_server/tools/pi_load_profiler.py --duration 120 --csv load.csv` (Beispiel aus dem Docstring des Werkzeugs). Für `measure_framerate.py` nennt der Docstring den Ablauf: Server stoppen, Werkzeug starten, Server wieder starten.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/tools/__init__.py` | Paketmarker, leer | – |
| `src/vision_server/tools/handshake_client.py` (Testclient) | Referenz-Handshake: abonnieren, `StartSingleJob` rufen, Ergebnis per Event abholen, ohne Polling | `run()`, `EventCollector`, `_payload_of()` |
| `src/vision_server/tools/calibration_client.py` (Testclient) | Kalibrier-Sitzung über die Methoden, Fortschritt im Log. Strg+C beendet mit `FinishCalibration`, mit `--abort` ohne Speichern | `run()`, `_start_session()` |
| `src/vision_server/tools/stream_viewer.py` (Handwerkzeug) | Lokales OpenCV-Fenster für den Livestream. Die Leertaste löst eine Kalibrieraufnahme aus | `run()` |
| `src/vision_server/tools/diagnose_board.py` (Diagnose) | Holt ein unmarkiertes Bild vom Server und testet Rastergrößen des Schachbretts | `run()`, `_parse_combo()`, `DEFAULT_COMBOS` |
| `src/vision_server/tools/measure_framerate.py` (Messwerkzeug) | Maximale Bildrate der Kamera, ohne die Drosselung des Servers. Ausgabe min, max, Mittelwert | `run()`, `_measure()`, `_summarise()` |
| `src/vision_server/tools/list_realsense_profiles.py` (Handwerkzeug) | Listet die Farb-Profile einer angeschlossenen RealSense. Öffnet keine Pipeline | `main()` |
| `src/vision_server/tools/pi_load_profiler.py` (Messwerkzeug) | CPU und RAM je Prozess und Thread, Temperatur und Throttling. Nur Standardbibliothek, optional `py-spy top` | `run()`, `_find_vision_server_pid()`, `_read_throttled_flags()`, `_run_pyspy()` |

Optionen (Auszug):

- Testclients sowie `stream_viewer.py` und `diagnose_board.py`: `--url` (Verbindungsdaten in [vision-server-interface.md](vision-server-interface.md) §2), `--namespace`, `--vision-system`.
- `handshake_client.py`: `--recipe-id` (Pflicht), `--meas-id`, `--parameter` (mehrfach), `--timeout` (Standard 15 s).
- `calibration_client.py`: `--poll-interval` (Standard 1,0 s), `--abort`.
- `stream_viewer.py`: `--mode` (Standard `calibration`), `--fps` (Standard 10).
- `diagnose_board.py`: `--combo COLSxROWS` (mehrfach), `--save-to` (Standard `board_diagnose.jpg`), `--settle-s` (Standard 1,0 s).
- `measure_framerate.py`: `--backend`, `--resolution W H`, `--realsense-fps`, `--duration` (Standard 10 s), `--warmup` (Standard 2 s).
- `pi_load_profiler.py`: `--interval` (Standard 2 s), `--top` (Standard 10), `--duration`, `--csv`, `--pid`, `--no-threads`, `--pyspy`.
- `list_realsense_profiles.py` hat keine Optionen.

**Konfiguration**

- Standardkombinationen für die Brettsuche: `DEFAULT_COMBOS` in `diagnose_board.py`.
- Der Lastprofiler liest `/proc` und `/sys` (nur Linux). Den Throttling-Status liefert `vcgencmd`, das es nur auf dem Pi gibt.

Stolpersteine:

- `DEFAULT_COMBOS` stammt vom alten 7 × 9-Brett. Das aktuelle Brett hat 7 × 10 innere Ecken und fehlt in der Liste. Ohne `--combo` testet `diagnose_board.py` das aktuelle Brett deshalb nicht. Dann `--combo 7x10` angeben.
- Die Tests im Ordner `tests/` nutzen `tools/make_synthetic_scene.py` aus dem Repo-Root, nicht dieses Paket. Kein Test importiert `src/vision_server/tools/`.

**Tests**
- Keine eigenen Tests für dieses Verzeichnis.

**Weiterlesen**
- [apriltag-e2e-test.md](apriltag-e2e-test.md) (Stufen 1 bis 4, Werkzeuge von Hand)
- [vision-server-interface.md](vision-server-interface.md) §8 (Referenz-Handshake)
- [vision-system.md](vision-system.md) (`handshake_client.py` als Referenzimplementierung)
- [kamera-und-detektor-analyse.md](kamera-und-detektor-analyse.md) (Messung der Kamera-Einstellungen)

---

### nodesets/ (vendorierte Companion Specs)

**Zweck**
XML-Dateien der OPC-UA-Companion-Specs, die der Server beim Start lädt. Sie stammen aus dem offiziellen UA-Nodeset-Repository. Sie liegen im Repo, damit der Server ohne Netz startet. Das Projekt hat sie nicht geschrieben.

**Einstieg**
`address_space.attach_vision_system()` lädt Part 1 immer. `_ensure_amcm_nodesets()` lädt DI, Machinery und AMCM nur, wenn `config.assets` gesetzt ist.

**Dateien**

| Datei | Aufgabe | Wichtigste Bausteine |
| --- | --- | --- |
| `src/vision_server/nodesets/Opc.Ua.MachineVision.NodeSet2.xml` | OPC 40100 Part 1 (Job- und Ergebnispfad). Immer geladen | `DEFAULT_NODESET_PATH` in `config.py` |
| `src/vision_server/nodesets/Opc.Ua.Di.NodeSet2.xml` | Device Integration. Abhängigkeit von Part 2 | `DEFAULT_AMCM_NODESET_PATHS` in `config.py` |
| `src/vision_server/nodesets/Opc.Ua.Machinery.NodeSet2.xml` | Machinery (Ordner `Machines`). Abhängigkeit von Part 2 | `DEFAULT_AMCM_NODESET_PATHS` in `config.py` |
| `src/vision_server/nodesets/Opc.Ua.MachineVision.AMCM.NodeSet2.xml` | OPC 40100-2 (AMCM), Anlagensicht. Nur mit `config.assets` | `DEFAULT_AMCM_NODESET_PATHS` in `config.py` |
| `src/vision_server/nodesets/README.md` | Herkunft, gepinnte Versionen, Importreihenfolge, Kosten | – |

**Konfiguration**

- Die Versionen sind absichtlich gepinnt (laut README): DI 1.04.0, Machinery 1.03.0, AMCM 1.00.0. DI 1.05.0 lässt sich laut README nicht importieren.
- Importreihenfolge: DI, Machinery, AMCM. Part 1 ist davon unabhängig.
- Bei einer Aktualisierung soll `tools/measure_nodeset_import.py` im Repo-Root neu laufen (laut README).

Stolpersteine:

- Die README-Zeile „Part 1 liegt eine Ebene höher“ stimmt nicht mehr. Die Datei liegt im selben Ordner wie die übrigen Nodesets (`DEFAULT_NODESET_PATH`).

**Tests**
- `tests/test_asset_model.py`: lädt die AMCM-Nodesets wirklich und prüft die Anlagensicht.
- `tests/test_part10_fassade.py`: Adressraum-Aufbau über `attach_vision_system()` (lädt Part 1), Einbau über `install_vision_machine()`.

**Weiterlesen**
- [vision-system.md](vision-system.md), Abschnitt „Dateien“
- [vision-server-interface.md](vision-server-interface.md) §1 (Systemaufbau, Namespace-Indizes)

---

# Teil B — webskillcomposition (unser Anteil gegenüber `dev`)

Alle Pfade in Teil B sind relativ zur Wurzel des webskill-Repos. Grundlage ist der Vergleich
`origin/dev..Ungetestet` (Abzweig `d824975`, Stand `5812275`).

## Teil B1 — Zellmodule: Modelle (frontend/src/features/cell-modules/model)

Die Zellmodule bilden die Roboterzelle als 3D-Szene in der App ab. Auf den Modulen (Tische, Feeder, Roboter) kleben AprilTags. Die Deckenkamera (Pi 1, „Kamera 1“, Layer 1) und die Hand-Kamera des Roboters (Pi 2, „Hand-Pi“, Layer 2) erkennen diese Tags und liefern Posen. Die Dateien in `model/` rechnen daraus: Koordinaten, Modulkatalog, Flächen der 3D-Modelle, Mess- und Fahrpläne, Kollisionsprüfung und Zustand. Sie zeichnen nichts; die Darstellung liegt in `frontend/src/features/cell-modules/components/` (anderer Abschnitt). Die meisten Dateien sind reine Mathematik und Logik und lassen sich ohne Browser testen. Einige sind React-Hooks (`use…`), eine ist ein Web Worker (`planeWorker.ts`).

**Begriffe in diesem Teil**

- **Welt-KS**: Koordinatensystem der Zelle. Der Welttag auf dem Boden legt es fest, Z zeigt nach oben.
- **Szenen-KS**: Koordinaten des 3D-Viewports (three.js, Y nach oben). Umrechnung in `worldToScenePosition`.
- **Modul-KS**: Koordinatensystem eines Moduls; sein Ursprung kommt aus dem Modulkatalog.
- **Pose**: Position plus Lage. Die Lage ist ein Quaternion in der Reihenfolge xyzw.
- **Hand-Auge (`T_flansch_cam`)**: Lage der Hand-Kamera am Roboterflansch, einmal kalibriert.
- **Take Control / Sync**: Schalter des Roboter-Servers. Ohne sie fährt die App den Roboter nicht.
- **go_to**: Fahrbefehl des Roboter-Servers, der nur Gelenkwinkel annimmt. Die inverse Kinematik (IK) rechnet das Frontend.

**Wo es in der UI erscheint**: Im Viewport unter „Einstellungen → Zellüberwachung“ (`frontend/src/features/viewport/components/ViewportHeaderControls.tsx`) liegen „Modulkatalog“, „Tags einmessen“ und „Hand-Kamera kalibrieren“. Dort steht auch die Zellüberwachung mit „Zelle überwachen“, „Autolocate Layer 1“, „Annäherung“ und „Pick & Place“ (`frontend/src/features/opcua-server/components/AutolocateActions.tsx`). Die Kamera-1-Kalibrierung mit dem Brett ist ein eigenes Fenster (`frontend/src/features/opcua-server/components/CameraCalibrationModal.tsx`).

### Grundlagen: Koordinaten, Geometrie und Szenenmodell

**Zweck**: Hier liegen das Datenmodell der platzierten Module (`PlacedCellModule`), die Umrechnung zwischen Welt- und Szenen-Koordinaten und die Rechnung mit Posen. Alle anderen Gruppen bauen darauf auf.

Benutzt von: `sceneState.ts` (Szene), `AutolocateLayer1Window.tsx`, `TagPositionsWindow.tsx`.

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/cellModules.ts` | Wandelt die Erkennungen der Deckenkamera in Module der Szene um (nur im Welt-KS; Tags eines Moduls werden zu einem Modul gemittelt). Fügt Scan und Layer-2-Ergebnis in die Szene ein. | `PlacedCellModule`, `PlacedWorldTag`, `PlacementResult`, `placeLayer1Detections`, `placeWorldTags`, `mergeLayer1Scan`, `mergeLayer2Result`, `worldToScenePosition`, `levelModulePose` |
| `frontend/src/features/cell-modules/model/tagGeometry.ts` | Quaternionen und Posen; Roll/Pitch/Yaw nach der URDF-Konvention (Grad). Enthält die letzte Stufe der Kette: Modul-Pose = Welt-Tag-Pose · Tag-in-Modul. | `Pose`, `IDENTITY_POSE`, `composePoses`, `invertPose`, `multiplyQuaternions`, `rotateVector`, `quaternionFromRpyDeg`, `rpyDegFromQuaternion`, `moduleWorldPose`, `tagWorldPose`, `turnRotationDegAboutTagZ` |
| `frontend/src/features/cell-modules/model/format.ts` | Formatiert Weltkoordinaten als Text in Millimetern. | `formatWorldMm` |
| `frontend/src/features/cell-modules/model/tagPositions.ts` | Liste aller Tags der Zelle im Welt-KS (Katalog, gesehen, berechnet), als Tabelle zum Kopieren. | `TagPositionRow`, `listTagPositions`, `tagPositionsText` |

**Ablauf**: Pi 1 liefert `VisionDetection` mit `position` und `orientation` im `frameId` → nur bei `frameId === "world"` (sonst übersprungen) → `levelModulePose` (Höhe kommt vom Boden, übernommen wird nur die Drehung um Z) → Mittel je Modul, Drehung vom sichersten Tag → `PlacedCellModule` im Welt-KS → `worldToScenePosition` (Szene: `[x, z, −y]`) → Szene (`sceneState.ts`).

**Konstanten/Konfiguration**

- `WORLD_FRAME_ID = "world"`.
- `MOVED_THRESHOLD_M = 0.1`: Ein Modul, das mehr als 10 cm vom Stand abweicht, gilt als verschoben.
- `TAG_SPREAD_WARNING_M = 0.05`: Tags desselben Moduls, die mehr als 5 cm auseinander liegen, widersprechen sich. Dann stimmt ein Katalogeintrag nicht.
- Der Boden ist die mittlere Höhe der Welttags aus dem Katalog (`floorHeightM`, 0 ohne Welttags).
- Einheiten: Positionen in Metern. Winkel im Katalog in Grad, in der Szene (`yaw`) in Radiant.

**Tests**: `cellModules.test.ts`, `tagGeometry.test.ts`, `tagPositions.test.ts`. `format.ts` hat keinen Test.

**Weiterlesen**: [apriltag-lokalisierung.md](apriltag-lokalisierung.md) (Abschnitt 1.1 „Drei Rollen von Tags“ und 1.2 „Die Kette“), [apriltag-referenz.md](apriltag-referenz.md) (Abschnitt 1 „Begriffe und Konventionen“), [vision-system-integration.md](vision-system-integration.md).

### Modulkatalog und Modelldateien

**Zweck**: Der Modulkatalog ist die Liste der Module der Zelle: Name, Modelldatei, Umriss (`bounds`), AprilTags mit ihrer Lage im Modul-KS, Robotereinträge (Robotermodell, OPC-UA-Adresse). Er liegt auf dem Backend, damit alle Browser denselben Stand sehen. Die Typen und der Standardkatalog stehen in `frontend/src/features/cell-modules/config/moduleCatalog.ts` (nicht in diesem Teil). Modelldateien laden nacheinander, weil große Dateien (STL, Collada mit Texturen, URDF mit Netzen) sonst den Browser überlasten.

Benutzt von: `ModuleCatalogModal.tsx`, `CellModuleModel.tsx`, `AutolocateActions.tsx`, `sceneState.ts`.

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/catalogStore.ts` | Lädt und speichert den Katalog am Backend. Gespeichert wird mit einer Revision: Wer auf einer alten Fassung speichert, bekommt einen Konflikt und die neuere Fassung. Abgleich alle 15 s und beim Fokus. | `parseCatalog`, `serializeCatalog`, `upsertModule`, `refreshModuleCatalog`, `saveModuleCatalog`, `CatalogConflictError`, `useModuleCatalog`, `useModuleCatalogSync` |
| `frontend/src/features/cell-modules/model/catalogValidation.ts` | Prüft einen Eintrag vor dem Speichern. Fehler: doppelte Modul- oder Tag-IDs, Robotermodell fehlt, Adresse ohne `opc.tcp://`. Warnungen: Tag-Größe zu keiner Kamera passend, Tag nicht auf der Umrissbox. Meldungen auf Englisch. | `validateModule`, `tagSizeMatches`, `describeTagSize`, `distanceToBoxSurface`, `MAX_TAG_ID`, `TAG_SURFACE_TOLERANCE_M` |
| `frontend/src/features/cell-modules/model/modelFit.ts` | Modelltyp aus der Dateiendung (urdf, collada, gltf, stl) und Wurzelordner der `package://`-Pfade einer URDF. | `ModelFormat`, `modelFormat`, `urdfPackageRoot` |
| `frontend/src/features/cell-modules/model/modelLoadQueue.ts` | Lädt Modelle nacheinander, 50 ms Pause dazwischen. Je Datei nur ein Promise, damit React dieselbe Ladung teilt. Eine fehlgeschlagene Ladung wird vergessen und kann wiederholt werden. | `createSerialQueue`, `createPromiseCache`, `enqueueModelLoad` |

**Ablauf**: Backend `GET` und `PUT` auf `/api/module-catalog` (`backend/src/backend/api/module_catalog.py`; Datei `backend/catalog/module_catalog.json`, liegt im Git; mit `WSC_DATA_DIR` an einem anderen Ort) → `parseCatalog` → Store (aktuelle Fassung, Kopie im localStorage) → `useModuleCatalog()` → Viewport und Modulkatalog-Dialog. Speichern: `validateModule` → `saveModuleCatalog` schickt die Revision mit → bei HTTP 409 lädt der Store die neuere Fassung und wirft `CatalogConflictError`.

**Konstanten/Konfiguration**

- `CATALOG_URL = "/api/module-catalog"`, `REFRESH_INTERVAL_MS = 15000`.
- localStorage: `wsc.cellModules.catalog.cache` (letzte Fassung vom Backend, für die Anzeige bei Ausfall); `wsc.cellModules.catalog` (altes Format, wird einmal ins Backend übernommen).
- `MIN_REFERENCE_LINE_M = 0.02`: kürzere Bezugskanten werden verworfen.
- `MAX_TAG_ID = 586`: die Familie tag36h11 hat 587 Codes. `TAG_SURFACE_TOLERANCE_M = 0.01`.
- `PAUSE_BETWEEN_LOADS_MS = 50`.

**Tests**: `catalogStore.test.ts`, `catalogSync.test.ts`, `catalogValidation.test.ts`, `modelFit.test.ts`, `modelLoadQueue.test.ts`.

**Weiterlesen**: [apriltag-referenz.md](apriltag-referenz.md) (Abschnitt 14, „Datenformat im Modulkatalog“), [arbeitsplaene/modell-flaechen-tag-platzierung.md](arbeitsplaene/modell-flaechen-tag-platzierung.md).

### Tag-Platzierung auf Modellflächen

**Zweck**: Wo ein AprilTag auf einem Modul klebt, wird am 3D-Modell vermessen und nicht an der Umrissbox. Bei einem Tisch mit Roboter sagt die Box nichts über die Tischplatte. Das Modell wird nach ebenen Flächen durchsucht. Der Bediener wählt eine Ecke mit zwei Abständen oder zwei Kanten. Daraus entstehen Position und Drehung des Tags im Modul-KS. Die Kanten werden als Bezugskanten gespeichert, damit das Einmessen mit der Kamera sie wiederfindet.

Benutzt von: `ModelPlanes.tsx` (Worker und Flächen), `ModulePreview.tsx` (Kanten, Übereinstimmung), `ModuleCatalogModal.tsx` (Platzierung, Drehung).

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/modelPlanes.ts` | Findet die größten ebenen Flächen eines Dreiecksnetzes mit Randkanten und Ecken. Reine Mathematik ohne three.js. Höchstens zwölf Flächen. | `findPlanes`, `ModelPlane`, `PlaneSegment`, `PlaneCorner`, `PlaneOptions`, `DEFAULT_PLANE_OPTIONS`, `MeshInput` |
| `frontend/src/features/cell-modules/model/planeWorker.ts` | Web Worker: rechnet `findPlanes` außerhalb des Hauptthreads, damit der Dialog bedienbar bleibt. | `PlaneRequest`, `PlaneResponse` |
| `frontend/src/features/cell-modules/model/modelEdges.ts` | Klick auf eine Modellkante. Nahe am Ende ist es die Ecke, sonst ein Punkt auf der Kante. Die Richtung der Kante bleibt erhalten. | `pickOnEdge`, `closestPointOnSegment`, `distanceBetween`, `EdgePick`, `CORNER_FRACTION`, `CORNER_MAX_M` |
| `frontend/src/features/cell-modules/model/tagPlacement.ts` | Rechnet aus einer Ecke oder zwei Kanten Position und Drehung des Tags. Dreht um ±90° um die Tag-Achse. Prüft, ob eine gespeicherte Pose noch zur Platzierung passt. | `placementFromCorner`, `placementFromEdges`, `tagPoseFromPlacement`, `turnPlacement`, `placementMatches`, `EdgeLine`, `edgeInPlane` |

**Ablauf**: Das Modell wird geladen (`CellModuleModel.tsx`, über `modelLoadQueue`) → `ModelPlanes.tsx` gibt die Dreiecke im Modul-KS an den Worker (`planeWorker.ts`) → `findPlanes`: Dreiecke nach Normale und Abstand bündeln, Ebenen bilden, Netz verschweißen, Randkanten und Ecken bestimmen → `ModelPlane[]`, größte zuerst → Bediener wählt Ecke oder Kante (`pickOnEdge`) → `placementFromCorner` bzw. `placementFromEdges` → `tagPoseFromPlacement` → `ModuleTag.position` und `rotationDeg` sowie `placement` und `lines` im Katalog.

**Konstanten/Konfiguration** (`DEFAULT_PLANE_OPTIONS`)

- Winkel 2° und Abstand 3 mm, damit ein Dreieck zur Fläche zählt. Mindestfläche 25 cm², Mindestbreite 3 cm.
- Kanten: Glättung 1 mm, mindestens 2 cm lang. Eine Ecke entsteht, wenn zwei Kanten höchstens 6 cm Rand trennen und sich um mindestens 20° drehen.
- Lücken bis 1 cm gelten als eine Fläche (Alu-Profile mit Schlitzen).
- `MIN_EDGE_ANGLE_DEG = 10`: Zwei Kanten, die fast parallel sind, haben keinen Schnittpunkt. `LEVEL_FACE_COS`: eine Fläche bis 45° von der Waagerechten gilt als liegend.
- `CORNER_FRACTION = 0.25`, `CORNER_MAX_M = 0.08`: Ein Klick nahe am Kantenende zählt als Ecke.

**Tests**: `modelPlanes.test.ts`, `modelEdges.test.ts`, `tagPlacement.test.ts`. `planeWorker.ts` hat keinen Test.

**Weiterlesen**: [arbeitsplaene/modell-flaechen-tag-platzierung.md](arbeitsplaene/modell-flaechen-tag-platzierung.md), [arbeitsplaene/tag-drehen-90-grad.md](arbeitsplaene/tag-drehen-90-grad.md).

### Lokalisierung Layer 1 und Autolocate (Deckenkamera)

**Zweck**: „Autolocate Layer 1“ (Fenster mit „Zelle scannen“) nutzt die Deckenkamera. Sie sieht Welttags, Roboter und Module in einem Bild. Der Scan zeigt zuerst, was er bringen würde (`planLayer1Import`), und der Bediener übernimmt ihn. Danach „verfolgt“ das Fenster die Zelle: Jeder Durchlauf wird mit der Szene verglichen, und Verschiebungen werden gemeldet. Die Welttags dienen als Fehlermaß für die Kamera.

Benutzt von: `AutolocateLayer1Window.tsx`, `AutolocateActions.tsx`, `WatchTimingFields.tsx`, `ViewportHeaderControls.tsx` (`autolocateUi`), `WorldTagMarkers.tsx` (`worldTagCheck`).

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/layer1Import.ts` | Berechnet vor der Übernahme, was ein Scan bringt: Module, Roboter (mit Robotermodell und Platzierung) und die Welttag-Prüfung. Verändert nichts. | `planLayer1Import`, `describeLayer1Import`, `DetectedRobot`, `Layer1Import` |
| `frontend/src/features/cell-modules/model/worldTagCheck.ts` | Vergleicht die gesehenen Welttags mit ihrer eingemessenen Pose: Abweichung je Tag, Abstandsfehler je Tagpaar. | `checkWorldTags`, `pairWorldTags`, `rateDeviation`, `describeWorldTagCheck`, `WorldTagCheck`, `WORLD_TAG_WARN_M`, `WORLD_TAG_BAD_M` |
| `frontend/src/features/cell-modules/model/usePlaceRobots.ts` | Hook: setzt gefundene Roboter dorthin, wo ihre Tags sagen. Hat der Roboter eine OPC-UA-Adresse und ist sein Server noch nicht verbunden, wird er verbunden. Sonst entsteht nur ein Zwilling. | `usePlaceRobots` |
| `frontend/src/features/cell-modules/model/moduleTracking.ts` | „Verfolgen“: Eine Abweichung zählt erst als Verschiebung, wenn sie lange genug hält und mehrfach gemessen wurde. | `trackObservation`, `trackingObservation`, `trackingReferences`, `movedEntries`, `referencesSignature`, `DEFAULT_TRACKING_THRESHOLDS` |
| `frontend/src/features/cell-modules/model/moveTracker.ts` | `applyModuleMoves` setzt bestätigte Verschiebungen in die Szene (Nachziehen). `trackModuleMoves` (drei gleiche Scans in Folge) wird nur in Tests benutzt. | `applyModuleMoves`, `trackModuleMoves`, `DEFAULT_MOVE_TRACKER` |
| `frontend/src/features/cell-modules/model/autolocateUi.ts` | Store: Prüfintervall und Meldezeit der Zellüberwachung (im Browser gespeichert). Außerdem, ob das Fenster „Tags einmessen“ offen ist (nicht gespeichert). | `autolocateUi`, `useAutolocateUi`, `AutolocateUiState`, `DEFAULT_WATCH_INTERVAL_S`, `DEFAULT_WATCH_CHANGE_S` |
| `frontend/src/features/cell-modules/model/useCameraOverlay.ts` | Hook: setzt den Overlay-Modus einer Kamera (`apriltag` oder `off`) im Viewport und am Pi, damit das Bild die Tags zeigt. | `useCameraOverlay` |

**Ablauf**: Scan auf Pi 1 (AprilTag-Job, `useVisionJob`) → `planLayer1Import` (intern `placeLayer1Detections`, `checkWorldTags`, Roboter über `robotOriginFromBase`) → Übernahme („Erkannte Module und Roboter in den Viewport übernehmen“) → `sceneState.applyLayer1Detections` → `mergeLayer1Scan` (Grobpositionen werden ersetzt; präzise Module bleiben, außer sie wurden deutlich verschoben) → `usePlaceRobots` setzt die Roboter. Verfolgen: laufende Jobs (`useContinuousVisionJob`) → `planLayer1Import` → `trackingObservation` → `trackObservation` (Zustand je Modul) → `movedEntries` → Hinweis „Neues Autolocate Layer 1 empfohlen“. Ein Scan kann auf die gemeldeten Tags beschränkt werden (`followTagIds`). Dann zieht er nur diese Module nach (`applyModuleMoves`).

**Konstanten/Konfiguration**

- Verfolgen (`DEFAULT_TRACKING_THRESHOLDS`): Abstand 10 cm, Winkel 10°, 5 s Dauer, mindestens zwei Messungen, Lücken bis 15 s überbrückt. Einstellbar im Fenster und gespeichert unter `wsc.autolocate.layer1.trackingThresholds` (`AutolocateLayer1Window.tsx`).
- `DEFAULT_MOVE_TRACKER` (nur Tests): 10 cm, 10°, drei Scans.
- Welttag-Prüfung: Warnung ab 2 cm (`WORLD_TAG_WARN_M`), schlecht ab 5 cm (`WORLD_TAG_BAD_M`).
- Zellüberwachung: `DEFAULT_WATCH_INTERVAL_S = 3` (Prüfintervall), `DEFAULT_WATCH_CHANGE_S = 8` (Meldezeit). Gespeichert unter `wsc.cellWatch.timing`.

**Tests**: `layer1Import.test.ts`, `moduleTracking.test.ts`, `moveTracker.test.ts`, `worldTagCheck.test.ts`. `autolocateUi.ts`, `usePlaceRobots.ts` und `useCameraOverlay.ts` haben keinen Test.

**Weiterlesen**: [apriltag-lokalisierung.md](apriltag-lokalisierung.md) (Abschnitte 1.4 „Ankern“ und 1.5 „Der Ablauf in der Zelle“), [arbeitsplaene/apriltag-welttag-konzept.md](arbeitsplaene/apriltag-welttag-konzept.md), [arbeitsplaene/zellen-ueberwachung.md](arbeitsplaene/zellen-ueberwachung.md).

### Zellen-Überwachung

**Zweck**: Die Deckenkamera prüft in festem Takt, ob Module verschoben, neu aufgetaucht oder entfernt wurden. Sie meldet nur Änderungen, die eine Weile bestehen. Die App startet und stoppt die Überwachung, zeigt den Alarm und merkt sich quittierte Alarme. Änderungen, die der Roboter selbst verursacht, werden ausgeblendet.

Benutzt von: `useCellWatch.ts` (Start und Status, opcua-server), `AutolocateActions.tsx`, `CellChangeAlert.tsx`, `PiActivity.tsx`, `Viewport.tsx` (Sichtungen).

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/cellWatch.ts` | Liest den Status des Pi (`wsc.vision.cellwatch.status/1`), filtert Welttags und Roboter-Änderungen heraus und bildet den Schlüssel für quittierte Alarme. | `parseCellWatchStatus`, `cellWatchStartJson`, `onlyModuleTags`, `withoutChangesDuringOperation`, `cellChangeKey`, `worldTagIds`, `CellWatchStatus`, `CellChange`; `describeCellChange` nur in Tests |
| `frontend/src/features/cell-modules/model/cellWatchSwitch.ts` | Ereignis: „Job abbrechen“ schaltet den Überwachungsschalter in der App mit aus. | `requestCellWatchOff`, `onCellWatchOffRequest` |
| `frontend/src/features/cell-modules/model/tagSightings.ts` | Merkt sich für jeden Tag, was die letzten zehn Prüfungen sahen. „Nicht im Bild“ erst nach drei Prüfungen in Folge. | `addSighting`, `tagSighting`, `recentlySeenTagIds`, `SightingHistory`, `TagSighting`, `SIGHTING_WINDOW`, `MISSING_CHECKS` |

**Ablauf**: Start mit `cellWatchStartJson(referenceJobId, timing)` an Pi 1 → Pi prüft in festem Takt → Status als JSON → `parseCellWatchStatus` → `onlyModuleTags` (Welttags raus) → `withoutChangesDuringOperation` (Änderungen seit Beginn eines Roboterlaufs raus, siehe Robotersteuerung) → `CellChangeAlert` zeigt den Alarm, `cellChangeKey` merkt die Quittierung → `tagSighting` für die Anzeige „überwacht“ oder „nicht im Bild“. Ein Alarm ist ein Zustand, den der Bediener quittiert; neue Änderungen erzeugen einen neuen Schlüssel.

**Konstanten/Konfiguration**

- `CELL_WATCH_MAX_CAMERA_ERROR_PX = 6`: größter Fehler der Kamerapose in Pixeln, bei dem ein Bild noch zählt.
- `OPERATION_CLOCK_LEEWAY_MS = 5000`: Toleranz für unterschiedliche Uhren von Pi und Browser. Änderungen ohne Zeitstempel gelten bis eine Minute nach einem Lauf als dessen Folge (`UNTIMED_AFTER_OPERATION_MS`).
- `SIGHTING_WINDOW = 10` Prüfungen, `MISSING_CHECKS = 3`.
- Prüfintervall und Meldezeit: siehe Gruppe Lokalisierung (`autolocateUi.ts`).

**Tests**: `cellWatch.test.ts`, `tagSightings.test.ts`. `cellWatchSwitch.ts` hat keinen Test.

**Weiterlesen**: [arbeitsplaene/zellen-ueberwachung.md](arbeitsplaene/zellen-ueberwachung.md), [vision-server-interface.md](vision-server-interface.md) (Abschnitt 16 „Zellen-Überwachung (nur Deckenkamera)“).

### Layer 2 und Annäherung (Hand-Kamera)

**Zweck**: Nach Layer 1 fährt die Steuerung den Roboter mit der Hand-Kamera vor die Tags. Zuerst kommt ein Welttag (der Anker, der die Roboterbasis genau festlegt), dann die Modul-Tags, um jedes Modul genauer zu messen. Die App plant und fährt. Der Hand-Pi misst nur (`MeasureTags`). Das Ergebnis ist ein Vorschlag, den der Bediener übernimmt oder verwirft. Die Schritte steuert `ApproachPlanPanel.tsx`; die Rechnungen stehen hier.

Benutzt von: `ApproachPlanPanel.tsx` (Hauptnutzer), `HandEyeCalibrationWindow.tsx` (`lookDownHomeAngles`), `RouteList.tsx`, `Layer2ProposalMarkers.tsx`, `TagReferencePanel.tsx` (`layer2Gate`), `sceneState.ts`.

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/approachPlan.ts` | Plant je Tag eine Kamerapose per IK: Anker zuerst, dann die nächsten Tags. Probiert weiter weg und schräger, prüft den Freiraum und zeichnet den Weg. | `planApproach`, `approachTags`, `anchorCandidates`, `approachTrajectory`, `cameraPoseFacing`, `lookDownHomeAngles`, `ApproachStop`, `DEFAULT_APPROACH_STANDOFF_M`, `DEFAULT_ANCHOR_STANDOFF_M` |
| `frontend/src/features/cell-modules/model/layer2.ts` | Roboterbasis aus Layer 1 oder aus dem Zwilling. Umrechnung zwischen Basis-Pose und Ursprung des Roboters in der Szene. Prüfung, ob der Roboter noch steht. | `RobotBase`, `poseOf`, `robotBaseFrom`, `robotBaseFromOrigin`, `robotOriginFromBase`, `robotBaseHeading`, `robotStayedPut` |
| `frontend/src/features/cell-modules/model/layer2Gate.ts` | Vorbedingungen: Ein Layer-1-Scan ist übernommen, der Roboter steht in der Szene, und zum Fahren ist er verbunden. | `layer2Gate`, `Layer2Gate` |
| `frontend/src/features/cell-modules/model/layer2Run.ts` | Typen für Ziel und Status, wie sie der frühere Pi-Lauf verwendete. Die Parser stehen in der App nicht in Benutzung (nur Tests). | `Layer2Target`, `Layer2Pose`, `Layer2Status`, `Layer2Purpose`, `LAYER2_PURPOSE_LABELS`, `parseLayer2Target`, `parseLayer2Status`, `layer2Report` |
| `frontend/src/features/cell-modules/model/routeProgress.ts` | Neue Position eines Moduls aus der Hand-Messung (gemessen statt geplant, als „präzise“ markiert). Status je Schritt der Route. | `measuredModule`, `describeModulePosition`, `RouteProgress`, `RouteStatus` |
| `frontend/src/features/cell-modules/model/tagServo.ts` | Ein Nachregel-Schritt: Kamera so verschieben, dass der Tag auf der optischen Achse liegt (höchstens 5 cm). Basiskorrektur am Welttag. Auswertung der Antwort von `MeasureTags`. | `servoStep`, `baseCorrection`, `tagInBaseFrom`, `parseMeasureTagsResult`, `measureTagsRequestJson`, `ServoStep`, `BaseCorrection` |
| `frontend/src/features/cell-modules/model/measureTagsCall.ts` | Ruft `MeasureTags` am Hand-Pi und wartet auf die Antwort. | `measureTags`, `MeasureOutcome` |
| `frontend/src/features/cell-modules/model/centreOnTag.ts` | Kamera auf einen Tag ausrichten: bis zu drei Korrekturen. Ist der Tag nicht im Bild, ein Suchmuster aus zwei Ringen mit je acht Ansichten. | `centreOnTag`, `searchViews`, `CentreOutcome`, `MAX_CENTRING_ROUNDS`, `SEARCH_STEP_DEG` |

**Ablauf**: Layer-1-Szene → `layer2Gate` → `approachTags` (Tags der Module) und `anchorCandidates` (Welttags, die Layer 1 gesehen hat) → `planApproach` (IK je Halt, Freiraum über `cellClearance`) → `approachTrajectory` (Zeichnung im Viewport) → Take Control und Sync (`useRobotHold`) → je Halt: `driveJoints` → `centreOnTag` (`measureTags` am Hand-Pi, dann `servoStep`) → Anker: `baseCorrection` korrigiert die Roboterbasis, danach wird neu geplant → Modul: `measureTagViews` (siehe Einmessen) → `measuredModule` → Vorschlag (`sceneState.setLayer2Proposal`) → „Übernehmen“: `applyModuleMoves` und neuer Roboter-Ursprung.

**Konstanten/Konfiguration**

- Abstand zum Tag: 0,35 m für Modul-Tags (`DEFAULT_APPROACH_STANDOFF_M`), 1,0 m für den Welttag (`DEFAULT_ANCHOR_STANDOFF_M`).
- Suche nach einer Pose: Neigungen 0, 15, 30, 45 und 60°. Ist das nicht erreichbar, weiter weg (1,3-, 1,6- und 2-fach) und bis 75° geneigt. Drehungen 0, 90, −90 und 180°. `IN_VIEW_DEG = 20`: Der Tag gilt als im Bild, solange er höchstens 20° neben der optischen Achse liegt.
- `DEFAULT_LAYER2_OPTIONS` (0,3 m zum Messen, 0,6 m Annäherung, bis 60° geneigt) gehört zur Pi-Anfrage `buildLayer2Request`, die die App nicht stellt.
- Roboter gilt als still bei höchstens 5 cm (`ROBOT_STILL_DISTANCE_M`) und 5° (`ROBOT_STILL_YAW_RAD`).
- Nachregeln: zentriert, wenn der Tag bis 5 mm seitlich und 1 cm im Abstand liegt (`CENTRED_LATERAL_M`, `CENTRED_DISTANCE_M`). Höchstens 5 cm je Schritt (`MAX_STEP_M`).
- Zeitlimit `MeasureTags`: 20 s.

**Hinweis**: Der Kopfkommentar von `layer2.ts` beschreibt noch den älteren Stand, in dem der Hand-Pi den ganzen Lauf fährt. Maßgeblich ist `arbeitsplaene/layer2-measuretags.md`: Die Steuerung plant und fährt, der Pi misst.

**Tests**: `approachPlan.test.ts`, `layer2.test.ts`, `layer2Gate.test.ts`, `layer2Run.test.ts`, `routeProgress.test.ts`, `tagServo.test.ts`, `centreOnTag.test.ts`. `measureTagsCall.ts` hat keinen Test.

**Weiterlesen**: [arbeitsplaene/layer2-measuretags.md](arbeitsplaene/layer2-measuretags.md), [arbeitsplaene/layer2-annaeherung.md](arbeitsplaene/layer2-annaeherung.md) (auch „Nachtrag 27.10.2026: Jeden Tag aus 3 Ansichten messen und vereinen“), [arbeitsplaene/layer2-schraege-ansicht.md](arbeitsplaene/layer2-schraege-ansicht.md), [arbeitsplaene/layer2-lauf-hand-pi.md](arbeitsplaene/layer2-lauf-hand-pi.md), [arbeitsplaene/apriltag-hand-auge-ankern.md](arbeitsplaene/apriltag-hand-auge-ankern.md), [vision-server-interface.md](vision-server-interface.md) (Abschnitte 14 und 17).

### Einmessen mit der Hand-Kamera (Bezugskanten und Roboter-Tags)

**Zweck**: Das Fenster „Tags einmessen · Hand-Kamera“ misst die Lage jedes Tags genauer. Modul-Tags werden gegen die Bezugskanten gemessen, die im Modell gewählt wurden. Tags des Roboters werden über die Gelenkwinkel gemessen (Hand-Auge). Das Fenster fährt die Tags nacheinander an, misst aus mehreren Ansichten, fusioniert sie und speichert das Ergebnis am Tag (`measurement`). Der Katalogwert bleibt als Startwert erhalten.

Benutzt von: `TagReferencePanel.tsx`, `TagReferenceWindow.tsx`, `TagReferenceLiveParts.tsx`, `TagPositionsWindow.tsx`, `ModuleCatalogModal.tsx` (`invalidateChangedMeasurements`, `describeTagMeasurement`), `ViewportHeaderControls.tsx` (öffnet das Fenster).

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/tagReferenceRun.ts` | Ablauf je Modul-Tag: anfahren, zentrieren, Messansicht planen, Ansichten anfahren und messen, fusionieren, zurückfahren. Stop-and-go. Nichts wird angefahren, das die IK nicht erreicht oder das Tisch oder Boden trifft. | `measureTagReferences`, `planViewMove`, `driveClear`, `askConfirm`, `TAG_REFERENCE_VIEWS`, `TagReferenceJob`, `TagReferencePhase`, `referenceScore`, `compareReferenceScores` |
| `frontend/src/features/cell-modules/model/tagReferenceCall.ts` | Aufrufe der Pi-Methoden `MeasureTagReference`, `PlanTagReferenceView` und `SetTagReferencePreview` mit ihren JSON-Formaten. | `measureTagReference`, `planTagReferenceView`, `setTagReferencePreview`, `tagReferenceRequestJson`, `parseTagReferenceResult`, `parseTagReferenceView`, `TagReferenceResult` |
| `frontend/src/features/cell-modules/model/tagReferenceFusion.ts` | Fusioniert mehrere Ansichten zu einer Korrektur: gewichtete kleinste Quadrate in der Tag-Ebene. Ausreißer fallen heraus. | `fuseReferenceViews`, `FusedReference`, `FUSION_OUTLIER_MM`, `FUSION_OUTLIER_DEG` |
| `frontend/src/features/cell-modules/model/tagReferenceProgress.ts` | Fortschrittsleiste je Tag: Anfahren, Zentrieren, Planen, Messansicht, Messen, Rückfahrt. | `phaseMarks`, `phaseLabel`, `PHASE_LABELS`, `MODULE_PHASES`, `ROBOT_PHASES` |
| `frontend/src/features/cell-modules/model/tagReferencePreview.ts` | Kantenvorschau am Pi ein- und ausschalten. Die Vorschau wird alle 60 s erneuert, weil der Pi sie nach 120 s zurücknimmt. Nach dem Schließen des Fensters schaltet sie nicht mehr ein. | `createPreviewController`, `startPreviewRenewal`, `PREVIEW_RENEW_MS` |
| `frontend/src/features/cell-modules/model/tagReferenceLiveView.ts` | Schaltet das eingebettete Bild auf die Kantensuche und stellt beim Verlassen den vorherigen Modus wieder her, auch am Pi. | `enterLiveView` |
| `frontend/src/features/cell-modules/model/tagReferenceSettings.ts` | Einstellungen des Fensters: Live-Ansicht, Ebenen im Bild, Schritt für Schritt, Testphase. Im Browser gespeichert. | `tagReferenceSettings`, `useTagReferenceSettings`, `parseTagReferenceSettings`, `overlayRequest`, `OVERLAY_LAYERS`, `DEFAULT_TAG_REFERENCE_SETTINGS` |
| `frontend/src/features/cell-modules/model/useTagReferenceLive.ts` | Hooks: Live-Werte der Kantenvorschau und Adresse des Debugbildes vom Pi. | `useTagReferencePreviewStatus`, `useTagReferenceImageUrl` |
| `frontend/src/features/cell-modules/model/tagMeasurementState.ts` | Welche Tags noch zu messen sind. Schreibt das Messergebnis in den Katalog. Verwirft Messungen, wenn sich der Tag geändert hat. | `pendingTags`, `measurableTags`, `selectTagReferenceTags`, `referenceLinesOf`, `applyTagMeasurement`, `invalidateChangedMeasurements`, `TAG_MEASURE_ALL_TESTPHASE` |
| `frontend/src/features/cell-modules/model/tagMethodComparison.ts` | Gegenprobe eines Roboter-Tags: Ergebnis über die Gelenke gegen das über die Bezugskanten. Nur Anzeige, nichts wird übernommen. | `compareRobotTagWithEdges`, `TagMethodComparison`, `EDGE_CHECK_MAX_MM`, `EDGE_CHECK_MAX_DEG` |
| `frontend/src/features/cell-modules/model/tagEdgeDistance.ts` | Abstand der Tag-Mitte von jeder Bezugskante in der Tag-Ebene, zum Nachmessen mit dem Maßband. | `tagEdgeDistances`, `tagEdgeDistanceMm`, `describeEdgeDistances` |
| `frontend/src/features/cell-modules/model/tagViews.ts` | Mehrere Ansichten eines Tags messen (Kamera auf einer Kugel um den Tag) und fusionieren, mit Ausreißerprüfung. Die Annäherung nutzt sie ebenfalls. | `tagViews`, `viewsAround`, `measureTagViews`, `fuseTagViews`, `angleBetween`, `TAG_VIEW_TILTS`, `MODULE_VIEW_TURNS` |
| `frontend/src/features/cell-modules/model/robotTagRun.ts` | Einmessen der Roboter-Tags. Jede Ansicht ergibt `T_base_tag = FK(Gelenke) · T_flansch_cam · T_cam_tag`. Mindestens zwei gültige Ansichten. | `measureRobotTags`, `robotTagJob`, `combineRobotTagViews`, `RobotTagJob`, `RobotTagMeasured`, `ROBOT_TAG_MIN_VIEWS` |

**Ablauf (Modul-Tag)**: Auswahl der Tags (`selectTagReferenceTags`) → `measureTagReferences` → Vorschau an (`setTagReferencePreview`) → `centreOnTag` (über `measureTags`) → `planTagReferenceView` (der Pi liefert die Kamerapose, aus der Tag und Kanten zugleich im Bild sind) → fünf Ansichten (`TAG_REFERENCE_VIEWS`: ±10° und 20° geneigt) → `planViewMove` (IK, Drehungen) → `driveClear` → `measureTagReference` am Pi → `fuseReferenceViews` → `applyTagMeasurement` schreibt die Korrektur in den Katalog.

**Ablauf (Roboter-Tag)**: `measureRobotTags` → `centreOnTag` → `measureTagViews` (Gelenkwinkel an jeder Stelle, Hand-Auge) → `combineRobotTagViews` → `compareRobotTagWithEdges` (nur Anzeige) → Katalog.

**Konstanten/Konfiguration**

- `TAG_MEASURE_ALL_TESTPHASE = true`: Testphase (Vorgabe vom 21.10.2026). Jeder Lauf misst alle messbaren Tags neu. Später nur noch die ausstehenden.
- `DERIVED_LINE_LENGTH_M = 0.15`: Länge einer Bezugskante, die aus der Platzierung abgeleitet wird.
- `DEFAULT_CENTRING_STANDOFF_M = 0.35` und `ROBOT_TAG_STANDOFF_M = 0.35`: Abstand Kamera zu Tag.
- Toleranzen: Fusion verwirft Ansichten ab 1 mm und 0,5°. Warnung bei Streuung ab 1 mm und 0,5°. Roboter-Tags: Streuung ab 3 mm und 1°. Gegenprobe: 2 mm und 1°.
- Zeitlimits: `MeasureTagReference` 60 s, `PlanTagReferenceView` 15 s, Vorschau 10 s.
- localStorage: `wsc.tagReference.settings`.

**Hinweis**: Der Typname `TagReferenceView` steht zweimal. `tagReferenceCall.ts` meint die Antwort des Pi, `tagReferenceRun.ts` eine Messansicht des Ablaufs. Beim Lesen aufpassen.

**Tests**: `tagReferenceCall.test.ts`, `tagReferenceRun.test.ts`, `tagReferenceFusion.test.ts`, `tagReferenceProgress.test.ts`, `tagReferencePreview.test.ts`, `tagReferenceSettings.test.ts`, `tagMeasurementState.test.ts`, `tagMethodComparison.test.ts`, `tagEdgeDistance.test.ts`, `tagViews.test.ts`, `robotTagRun.test.ts`. `tagReferenceLiveView.ts` und `useTagReferenceLive.ts` haben keinen Test.

**Weiterlesen**: [arbeitsplaene/tag-einmessen-bezugskanten.md](arbeitsplaene/tag-einmessen-bezugskanten.md), [arbeitsplaene/tag-einmessen-liveview.md](arbeitsplaene/tag-einmessen-liveview.md), [apriltag-referenz.md](apriltag-referenz.md) (Abschnitt 14, „Tags auf dem Roboter selbst (über Gelenkwinkel)“), [vision-server-interface.md](vision-server-interface.md) (Abschnitt 19).

### Hand-Auge-Kalibrierung (Hand-Kamera und Schachbrett)

**Zweck**: Die Hand-Auge-Kalibrierung bestimmt, wo die Hand-Kamera am Roboterflansch sitzt (`T_flansch_cam`). Der Roboter zeigt dem Hand-Pi ein Schachbrett aus 18 Ansichten. Der Pi nimmt jede Ansicht auf und rechnet am Ende. Die App sorgt dafür, dass das Brett mittig im Bild steht und die Gelenke ruhig stehen, bevor eine Aufnahme ausgelöst wird.

Benutzt von: `HandEyeAutoPanel.tsx` und `HandEyeCalibrationWindow.tsx` (Fenster „Hand-Kamera kalibrieren“, geöffnet aus `ViewportHeaderControls.tsx`), `useHandEyeCalibration.ts` (Pi-Status, opcua-server), `measureBoardCall.ts` wird auch von der Kamera-1-Kalibrierung genutzt.

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/handEye.ts` | Formate der Hand-Auge-Methoden des Pi (Start, Aufnahme, Status) und die Fehlertexte für den Bediener. | `parseHandEyeStatus`, `handEyeBoardStartJson`, `handEyeSampleJson`, `describeHandEyeRefusal`, `HandEyeStatus`, `HandEyeResult`, `HAND_EYE_MIN_SAMPLES` |
| `frontend/src/features/cell-modules/model/handEyeAutoPoses.ts` | 18 Kamerapositionen um das Brett (senkrecht, 25° und 40° geneigt, mit Drehungen), umgerechnet in Flanschposen. | `generateHandEyePoses`, `boardIsUnambiguous`, `HandEyeAutoPose`, `FAR_DISTANCE_SCALE` |
| `frontend/src/features/cell-modules/model/handEyeBoard.ts` | Maße des Bretts (Innenecken, Feldgröße). Prüft sie und speichert sie im Browser. | `HAND_EYE_DEFAULT_BOARD`, `validateHandEyeBoard`, `loadHandEyeBoard`, `saveHandEyeBoard`, `HandEyeBoardSettings` |
| `frontend/src/features/cell-modules/model/handEyeBoardOrigin.ts` | Prüffahrt „Brettursprung anfahren“: Werkzeugspitze vor die erste Innenecke fahren, um den Versatz zu messen. | `boardOriginFlangeTarget`, `flangeToToolPose`, `BOARD_ORIGIN_STANDOFF_M`, `BOARD_ORIGIN_APPROACH_M`, `FR3_TOOL_OFFSET_M` |
| `frontend/src/features/cell-modules/model/boardCentring.ts` | Auswertung von `MeasureBoard`. Schiebt das Brett seitlich in die Bildmitte, höchstens 8 cm je Schritt. | `measureBoardRequestJson`, `parseMeasureBoardResult`, `boardCentred`, `boardCentringStep`, `boardViewScore`, `describeBoardCheck`, `BoardMeasurement` |
| `frontend/src/features/cell-modules/model/measureBoardCall.ts` | Ruft `MeasureBoard` an einem Pi auf und wartet darauf, höchstens 45 s. | `measureBoard`, `MeasureBoardOutcome` |
| `frontend/src/features/cell-modules/model/jointSettling.ts` | Prüft, ob die Gelenke vor und nach einer Aufnahme ruhig standen. | `readsSettled`, `meanJoints`, `jointsMoved`, `JOINT_SETTLE_TOLERANCE_RAD` |

**Ablauf**: Startstellung (`lookDownHomeAngles`) → je Ansicht: `generateHandEyePoses` → `planMove` → `driveJoints` → `measureBoard` und `boardCentringStep` (bis zu drei Zentrierrunden, bis das Brett mittig liegt) → `readsSettled` und `meanJoints` (Gelenke ruhig) → `handEyeSampleJson` (Aufnahme am Pi, `CaptureHandEyeSample`) → `jointsMoved` prüft, ob sich der Arm dabei bewegt hat → nach genug Aufnahmen: `FinishHandEyeCalibration` am Pi → `parseHandEyeStatus` → `HandEyeResult` (Fehler, Ausreißer, Kontrollwerte). Die Prüffahrt „Brettursprung anfahren“ ist eine eigene Aktion.

**Konstanten/Konfiguration**

- `HAND_EYE_DEFAULT_BOARD`: 7 × 10 Innenecken, Feld 23 mm (Standard im Code). Feldgröße 5 bis 100 mm. Das Brett muss eindeutig sein: Summe von Spalten und Zeilen ungerade.
- `HAND_EYE_MIN_SAMPLES = 3`, `HAND_EYE_GOOD_SAMPLES = 10`.
- `FAR_DISTANCE_SCALE = 1.3`: weiter entfernte Ansichten.
- `HAND_CAMERA_HFOV_DEG = 69` (Blickwinkel der Hand-Kamera), `BOARD_CENTRED_SHARE = 0.08` (zentriert, wenn der Brettmittelpunkt höchstens 8 % der Bildbreite vom Bildmittelpunkt liegt), `MAX_CENTRING_STEP_M = 0.08`.
- `JOINT_SETTLE_TOLERANCE_RAD = 0.0002`.
- `FR3_TOOL_OFFSET_M = 0.1034` (Werkzeugspitze am FR3), `BOARD_ORIGIN_STANDOFF_M = 0.01`, `BOARD_ORIGIN_APPROACH_M = 0.06`.
- localStorage: `wsc.handEye.board/1`.

**Hinweis**: Die Arbeitspläne nennen unterschiedliche Brettgrößen. `handeye-genauigkeit.md` spricht von 6 × 7 Innenecken, der Nachtrag in `handeye-schachbrett-automatik.md` von 7 × 10. Der Code verwendet 7 × 10.

**Tests**: `handEye.test.ts`, `handEyeAutoPoses.test.ts`, `handEyeBoard.test.ts`, `handEyeBoardOrigin.test.ts`, `boardCentring.test.ts`, `jointSettling.test.ts`. `measureBoardCall.ts` hat keinen Test.

**Weiterlesen**: [arbeitsplaene/handeye-ueber-opcua.md](arbeitsplaene/handeye-ueber-opcua.md), [arbeitsplaene/handeye-schachbrett-automatik.md](arbeitsplaene/handeye-schachbrett-automatik.md), [arbeitsplaene/hand-auge-brett-zentrieren.md](arbeitsplaene/hand-auge-brett-zentrieren.md), [arbeitsplaene/handeye-genauigkeit.md](arbeitsplaene/handeye-genauigkeit.md) (in Arbeit), [vision-server-interface.md](vision-server-interface.md) (Abschnitte 15 und 15.4).

### Kamera-1-Kalibrierung mit dem Brett im Greifer

**Zweck**: Die Deckenkamera (Kamera 1) wird kalibriert. Dazu hält der Roboter ein Kalibrierbrett in der Zange an eingelernten Positionen vor die Kamera. Die App fährt die Positionen und lässt den Pi nur dann eine Aufnahme machen, wenn das ganze Brett im Bild ist. Am Ende wertet der Pi aus. Die Positionen speichert der Browser.

Benutzt von: `CameraCalibrationModal.tsx` (Fenster der Kamera-Kalibrierung, opcua-server), `BoardCalibrationRobot.tsx` (Positionen und Anzeige).

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/boardCalibrationPoses.ts` | Eingelernte Positionen (Gelenkwinkel) speichern. Reihenfolge hin und zurück über mehrere Durchgänge. Handgelenk-Variante ab dem zweiten Durchgang. „Neuer Winkel“ dreht den Greifer um seine Werkzeugspitze. | `taughtPositionsFromStorage`, `taughtPositionsForStorage`, `stopsFromPositions`, `stopAtStep`, `angleVariant`, `boardRunTrajectory`, `BOARD_ROBOT_TAG_ID`, `CALIBRATION_POSITION_COUNT` |
| `frontend/src/features/cell-modules/model/boardCalibrationRunner.ts` | Ablaufsteuerung als Zustandsmaschine: halten, Sitzung, Neutralstellung, anfahren, prüfen, aufnehmen, auswerten, anhalten. Ohne React; Abhängigkeiten sind Funktionen. | `BoardRunner`, `BoardRunState`, `BoardRunDeps`, `BoardRunStatus`, `stepsForPasses`, `TARGET_PICTURES`, `MIN_PICTURES` |
| `frontend/src/features/cell-modules/model/useBoardCalibrationRun.ts` | Hook: verbindet Runner, Roboter (Take Control, Sync), Pi-Sitzung (Start, Aufnahme, Auswerten) und die gespeicherten Positionen. | `useBoardCalibrationRun`, `BoardCalibrationRun`, `BOARD_DRIVE_MAX_SPEED` |

**Ablauf**: Positionen einlernen (Arm von Hand führen, jede Stellung übernehmen; das liest die Gelenkwinkel) → Start: Roboter halten (`sharedRobotHolds`), Kalibriersitzung am Pi starten (`StartCalibration`) → Neutralstellung anfahren, Brett in die Zange nehmen („Zange zugreifen“), dann „Kalibrierfahrt starten“ → je Position: `drive`, dann `measureBoard` am Pi 1 → ganzes Brett im Bild: Aufnahme (`CaptureCalibrationSample`), sonst bis zu zwei neue Winkel (`angleVariant`) → nach 20 Bildern oder zwei Durchgängen: Auswertung (`FinishCalibration`) → zurück in die Neutralstellung.

**Konstanten/Konfiguration**

- `CALIBRATION_POSITION_COUNT = 16` Positionen. `TARGET_PICTURES = 20` Bilder. `MAX_PASSES = 2` Durchgänge. `MIN_PICTURES = 3`: Mit weniger Bildern verweigert die Auswertung. `ANGLE_TRIES = 2` neue Winkel je Position.
- `SETTLE_MS = 1000`: Wartezeit, bis das Brett nach der Fahrt nicht mehr schwingt.
- `TOOL_POINT_M = 0.1034`. Greiferdrehungen (`ANGLE_CHANGES`): ±20° um X und Y, ±45° um Z. `MAX_ANGLE_STEP_RAD = 1.2`, `JOINT_LIMIT_MARGIN_RAD = 0.05`.
- `BOARD_ROBOT_TAG_ID = 1`: der Roboter (Franka), dessen Katalogeintrag diesen Tag trägt.
- Fahrt mit `AUTO_DRIVE_MAX_SPEED` (0,1).
- localStorage: `wsc.cameraCalibration.camera1Positions`.

**Tests**: `boardCalibrationPoses.test.ts`, `boardCalibrationRunner.test.ts`.

**Weiterlesen**: [vision-server-interface.md](vision-server-interface.md) (Abschnitt 12 „Interaktive Kalibrierung (frontend-gesteuert)“, 12.1 bis 12.5), [arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md](arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md).

### Pick & Place

**Zweck**: Der Roboter nimmt einen Würfel (2 cm) von einer eingelernten Aufnahmestelle auf seinem eigenen Tisch. Er legt ihn in der Mitte eines anderen Tisches ab, dessen Lage Layer 2 gemessen hat. Vom Ablageort holt er ihn wieder zurück. Die Dateien planen den Weg, prüfen ihn (Erreichbarkeit, Tische) und merken sich, wo der Würfel liegt.

Benutzt von: `PickPlacePanel.tsx` (Oberfläche, eingebunden in `AutolocateActions.tsx`).

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/pickPlace.ts` | Schrittfolge für Ablegen (Aufnahme, Transfer, Ablage) und Rückweg. Lage des Würfels in der Hand, Drehung zur Tischkante, Ablageort in der Tischmitte. Freiraumprüfung beim Tragen. Maßband-Hilfen. | `planPickPlace`, `planReturn`, `carryClearance`, `placedObjectFrom`, `measurePlaceFromCorner`, `TaughtPick`, `PickPlacePlan`, `ReturnPlan`, `MEASURED_PLACE_TARGET` |
| `frontend/src/features/cell-modules/model/pickPlaceRoute.ts` | Löst jeden Schritt per IK, jeweils von der Stellung des vorigen aus. Prüft jede Strecke auf Freiraum (24 Zwischenstellungen). Probiert Drehungen für die Ablage. Speichert die Aufnahmestelle. | `routePickPlace`, `PickPlaceRoute`, `RoutedStep`, `PICK_PLACE_STEP_LABELS`, `DROP_TURNS_DEG`, `storedPickFromString`, `storedPickToString` |
| `frontend/src/features/cell-modules/model/usePickPlaceRun.ts` | Hook: Aufnahmepunkt einlernen, Tisch wählen, Plan berechnen, Lauf nach Bestätigung starten und anhalten. Die Greifer-Schritte kann der Bediener übernehmen. | `usePickPlaceRun`, `PickPlaceRun`, `PickPlaceStatus`, `RunKind` |

**Ablauf**: „Aufnahmepunkt einlernen“ liest die Gelenke → `storedPickToString` → localStorage (`wsc.pickPlace.pick`). Beim Start: Tisch gewählt → `planPickPlace` (über der Aufnahme, Aufnahme, Greifer zu, hoch, Transfer, langsam ablegen, Greifer auf, hoch) → `routePickPlace` (IK, `legCollision`, Prüfung des Tragens) → Bestätigung durch den Bediener → `driveJoints` (Ablegen langsam, ein Drittel der Geschwindigkeit) mit Greifer-Befehlen über die Roboter-Aktionen → `placedObjectFrom` → localStorage (`wsc.pickPlace.placed`). Rückweg: `planReturn` → `routePickPlace` → gleiche Fahrt in umgekehrter Richtung.

**Konstanten/Konfiguration**

- `CUBE_SIZE_M = 0.02`. `DEFAULT_LIFT_M = 0.1`: Anheben und Absetzen 10 cm über der Fläche. `CARRY_CLEARANCE_M = 0.02`: Abstand des Würfels zur Tischplatte beim Tragen. `EDGE_MARGIN_M = 0.05`: Weg über die Tischkante. `DEFAULT_GRASP_MARGIN_M = 0.003`: beim Greifen 3 mm höher.
- `DEFAULT_DROP_HEIGHT_M = 0`: Der Würfel wird auf die Tischplatte gesetzt. Im Hook einstellbar.
- `GRIPPER_OPEN_HALF_M = 0.04`: Finger offen, je Seite. Greifer-Zeitlimit 15 s, Wartezeit nach dem Greifen 1,5 s.
- Geschwindigkeit: `AUTO_DRIVE_MAX_SPEED` (0,1) für Fahrten, ein Drittel davon für das langsame Ablegen und Aufnehmen.
- `MEASURED_PLACE_TARGET` (Ecke und Abstände am Kalibrierblatt auf dem Tisch „Tisch leer“, Tag 3) dient nur als Standardecke für die Maßband-Anzeige.
- localStorage: `wsc.pickPlace.pick` (Aufnahmestelle), `wsc.pickPlace.placed` (Ablageort).

**Hinweis**: Der Kommentar am Hook `usePickPlaceRun` nennt 5 cm Ablagehöhe und einen gemessenen Ablagepunkt. Im Code gelten 0 mm (`DEFAULT_DROP_HEIGHT_M`) und die Tischmitte, denn die App übergibt `placeAt` nicht. Ein Ablegen am gemessenen Punkt (`placeTargetWorldPose`, `storedPlaceTargetFromString`) ist nur im Modell vorbereitet und wird in Tests geprüft.

**Tests**: `pickPlace.test.ts`, `pickPlaceFrame.test.ts`, `pickPlaceReturn.test.ts`, `pickPlaceRoute.test.ts`.

**Weiterlesen**: Im ADP-Repo gibt es keinen Arbeitsplan zu Pick & Place. Die Begründungen stehen in den Kommentaren von `frontend/src/features/cell-modules/model/pickPlace.ts` und `pickPlaceRoute.ts`.

### Robotersteuerung und Freigaben

**Zweck**: Gemeinsame Grundlage aller Fahrten der Zellfunktionen. Sie findet den Roboter der Zelle und lädt seine Kinematik. Sie nimmt Take Control und Sync und gibt sie wieder ab. Sie fährt Gelenke per `go_to` und erkennt die Ankunft. Außerdem meldet sie, dass der Roboter von selbst fährt.

Benutzt von: `ApproachPlanPanel.tsx`, `TagReferencePanel.tsx`, `HandEyeAutoPanel.tsx`, `PickPlacePanel.tsx`, `BoardCalibrationRobot.tsx`, `AutolocateActions.tsx` (`useRobotMotionWatch`, `useRobotOperation`), `FlangeTrajectoryMarker.tsx` (`FlangeTrajectory`), `HandCameraMarker.tsx` (`useCellRobot`).

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/robotDrive.ts` | Ein `go_to` mit Gelenkwinkeln, dann Warten, bis der Roboter steht. Zeitlimits. Setzt den `go_to`-Skill zurück, wenn er im Zustand Halted hängt. Die Flanschpose wird aus den gemeldeten Gelenken berechnet, nicht aus dem Ziel. | `driveJoints`, `createRobotDriver`, `planMove`, `hasArrived`, `RobotDriver`, `RobotDriveDeps`, `DriveOutcome`, `JointsTimeoutError` |
| `frontend/src/features/cell-modules/model/robotHold.ts` | Nimmt Take Control und Sync nacheinander. Gibt beim Loslassen nur zurück, was es selbst eingeschaltet hat. Gemeinsame Haltung für alle Panels. | `holdRobot`, `createSharedRobotHolds`, `sharedRobotHolds`, `RobotHoldApi`, `RobotHold`, `AUTO_DRIVE_MAX_SPEED` |
| `frontend/src/features/cell-modules/model/useRobotHold.ts` | Hook: hält den Roboter, solange das Panel offen ist. Meldet, wenn Take Control oder Sync von Hand ausgeschaltet wurden. | `useRobotHold`, `RobotHoldState`, `RobotHoldStatus` |
| `frontend/src/features/cell-modules/model/robotOperation.ts` | Store: „der Roboter fährt selbst“, mit dem Zeitpunkt, an dem der letzte Lauf endete. | `beginRobotOperation`, `endRobotOperation`, `useRobotOperation`, `RobotOperation` |
| `frontend/src/features/cell-modules/model/useRobotMotionWatch.ts` | Hook: erkennt jede Bewegung synchronisierter Roboter (alle 0,5 s) und meldet sie als Roboterbetrieb. | `useRobotMotionWatch` |
| `frontend/src/features/cell-modules/model/controllerRobotDrive.ts` | Verbindet den App-Controller mit der Fahrschnittstelle (Gelenke lesen, `go_to` senden). Prüfliste „bereit zum Fahren“. | `controllerRobotDeps`, `driveReadiness`, `robotModelLoaded`, `DriveReadiness` |
| `frontend/src/features/cell-modules/model/useCellRobot.ts` | Hook: der Roboter der Zelle mit Katalogeintrag, verbundenem Roboter und geladener Kinematik. | `useCellRobot`, `useCatalogRobot`, `CellRobot` |
| `frontend/src/features/cell-modules/model/flangeTrajectory.ts` | Datentyp der geplanten Fahrt, die vor dem Fahren im 3D-Fenster gezeichnet wird: Pfad, Halte, Pfeile, Würfel und Finger. | `FlangeTrajectory` |

**Ablauf**: `useCellRobot` (Katalog, `useRobotControl`, `loadFlangeKinematics`) → `driveReadiness` und `robotModelLoaded` (Vorbedingungen) → `holdRobot` über `sharedRobotHolds` (erst Take Control, dann Sync) → `driveJoints` → `controllerRobotDeps.sendGoto` (mit `max_speed`) → Ankunft, wenn `go_to` bereit ist, der Befehl angenommen wurde und die Gelenke drei Lesungen lang stillstehen, und zwar am Ziel (0,005 rad) oder nach vier ruhigen Lesungen in der Nähe → `beginRobotOperation` und `endRobotOperation` → die Zellenüberwachung ignoriert Änderungen während des Laufs. Der Roboter-Server nimmt `go_to` nur mit Gelenkwinkeln an; deshalb rechnet das Frontend die IK (`loadFlangeKinematics`).

**Konstanten/Konfiguration**

- `AUTO_DRIVE_MAX_SPEED = 0.1`: `max_speed` für alle Fahrten der App. Die Server veröffentlichen keine Einheit.
- `DRIVE_TIMEOUT_MS = 120000`: nach zwei Minuten gilt eine Fahrt als gescheitert. Gelenke werden alle 250 ms gelesen. `ARRIVED_RAD = 0.005`, `STILL_RAD = 0.0005`, `START_GRACE_MS = 3000`, `NO_ANSWER_MS = 60000`. Acht fehlgeschlagene Lesungen in Folge brechen ab (`MAX_FAILED_READS`).
- `SYNC_START_TIMEOUT_MS = 5000`, `READ_JOINTS_TIMEOUT_MS = 3000`.
- Bewegungswächter: alle 500 ms prüfen, Bewegung ab 0,003 rad, nach 3 s Ruhe gilt der Roboter als still (`useRobotMotionWatch`).
- Geteilte Haltung: 1 s Nachlauf, bevor Take Control abgegeben wird (`SHARED_RELEASE_DELAY_MS`). Mit `keepHeld: true` gibt die App die Haltung beim Schließen eines Panels nicht ab.

**Tests**: `robotDrive.test.ts`, `robotDriveReset.test.ts`, `robotHold.test.ts`. `robotOperation.ts`, `useRobotHold.ts`, `useRobotMotionWatch.ts`, `useCellRobot.ts`, `controllerRobotDrive.ts` und `flangeTrajectory.ts` haben keinen Test.

**Weiterlesen**: [vision-system-integration.md](vision-system-integration.md) (Abschnitt „B4 — `goto` ist Gelenkraum, die IK sitzt im Frontend“, Teil 3).

### Kollisions- und Freiraumprüfung

**Zweck**: Vor jeder Fahrt wird geprüft, ob der Arm den Tisch, auf dem der Roboter steht, oder den Boden berührt. Der Arm wird als Kette von Kugeln angenähert. Geprüft werden die Ziele und die gerade Verbindung dazwischen im Gelenkraum. Pick & Place prüft damit auch das Tragen des Würfels.

Benutzt von: `approachPlan.ts`, `tagReferenceRun.ts`, `centreOnTag.ts`, `pickPlace.ts`, `pickPlaceRoute.ts`, `usePickPlaceRun.ts`, `ApproachPlanPanel.tsx`, `TagReferencePanel.tsx`, `Layer2GhostRobot.tsx`.

**Dateien**

| Datei | Aufgabe | wichtigste Exporte |
|---|---|---|
| `frontend/src/features/cell-modules/model/cellClearance.ts` | Verbotszone aus dem Umriss des Roboters im Katalog (Tisch und Boden). Arm als Kugelkette. Prüft einen Gelenkwinkel oder eine Strecke. | `keepOutFromRobotBounds`, `armPoints`, `collision`, `legCollision`, `KeepOut`, `ArmSphere`, `TABLE_CLEARANCE_M`, `FLOOR_CLEARANCE_M`, `ARM_RADIUS_M`, `HAND_RADIUS_M` |

**Ablauf**: Gelenkwinkel → `armPoints` (Gelenke, Flansch, Werkzeug, Kamera; Kugeln höchstens 2 cm auseinander) → `collision` (Boden: Kugelmittelpunkt zu nah am Boden; Tisch: über der Tischfläche und unter der Oberkante plus Radius und 2 cm) → „Wagen“, „Boden“ oder frei → `legCollision` (Zwischenstellungen, höchstens 200, je Gelenk höchstens 0,05 rad) → der Planer verwirft das Ziel, wenn es blockiert ist.

**Konstanten/Konfiguration**

- `ARM_RADIUS_M = 0.06` (Gelenke), `HAND_RADIUS_M = 0.08` (Flansch, Werkzeug, Kamera). `TABLE_CLEARANCE_M = 0.02`, `FLOOR_CLEARANCE_M = 0.1`. `SPHERE_SPACING_M = 0.02`, `MAX_JOINT_STEP_RAD = 0.05`, `MAX_LEG_STEPS = 200`.
- Die Tischfläche kommt aus dem Umriss (`bounds`) des Roboter-Katalogeintrags. Die Oberkante der Roboterbasis liegt bei z = 0.
- Der Fuß des Roboters (Gelenk 0) wird nicht geprüft; die Kette beginnt bei Gelenk 1.

**Tests**: `cellClearance.test.ts`.

**Weiterlesen**: [arbeitsplaene/layer2-annaeherung.md](arbeitsplaene/layer2-annaeherung.md) (Abschnitt „Nachtrag: Arm fährt nicht mehr in den eigenen Wagen“).

**Hinweis**: Geprüft werden nur die Ziele und die gerade Strecke im Gelenkraum. Den Weg, den `go_to` tatsächlich nimmt, kennt die App nicht. Der Code vermerkt das ausdrücklich.

### Zustand und Speicher (Übersicht)

**Zweck**: Die Dateien halten Zustand entweder im Browser (localStorage) oder nur im Speicher der Seite. Gespeichertes gilt pro Browser. Gemeinsam für alle ist nur der Modulkatalog, und der liegt am Backend. Die Zugriffe auf den localStorage sind mit try/catch abgesichert. Der Zustand der Szene selbst (`cellModules`, Vorschläge, Pfade) liegt in `frontend/src/features/viewport/model/sceneState.ts` und ist nicht Teil dieses Abschnitts.

**Dateien und Schlüssel**

| Schlüssel oder Speicher | Datei | Inhalt |
|---|---|---|
| `wsc.cellModules.catalog.cache` | `frontend/src/features/cell-modules/model/catalogStore.ts` | letzte Katalogfassung vom Backend (Anzeige bei Ausfall) |
| `wsc.cellModules.catalog` | `frontend/src/features/cell-modules/model/catalogStore.ts` | altes Katalogformat; wird einmal ins Backend übernommen |
| `wsc.cellWatch.timing` | `frontend/src/features/cell-modules/model/autolocateUi.ts` | Prüfintervall und Meldezeit der Zellüberwachung |
| `wsc.autolocate.layer1.trackingThresholds` | `frontend/src/features/cell-modules/components/AutolocateLayer1Window.tsx` (Komponente) | Schwellen für „Verfolgen“ |
| `wsc.tagReference.settings` | `frontend/src/features/cell-modules/model/tagReferenceSettings.ts` | Einstellungen des Fensters „Tags einmessen“ |
| `wsc.handEye.board/1` | `frontend/src/features/cell-modules/model/handEyeBoard.ts` | Brettmaße der Hand-Auge-Kalibrierung |
| `wsc.cameraCalibration.camera1Positions` | `frontend/src/features/cell-modules/model/useBoardCalibrationRun.ts` | eingelernte Positionen der Kamera-1-Kalibrierung |
| `wsc.pickPlace.pick` | `frontend/src/features/cell-modules/model/usePickPlaceRun.ts` (Format in `pickPlaceRoute.ts`) | eingelernte Aufnahmestelle |
| `wsc.pickPlace.placed` | `frontend/src/features/cell-modules/model/usePickPlaceRun.ts` (Format in `pickPlace.ts`) | Ablageort des Würfels |

Nur im Speicher der Seite liegen: `robotOperation.ts` (Roboterbetrieb), `cellWatchSwitch.ts` (Listener), `autolocateUi.ts` (ob das Fenster „Tags einmessen“ offen ist), `sharedRobotHolds` in `robotHold.ts` (gemeinsame Haltung) und der aktuelle Katalog im `catalogStore.ts`.

**Tests**: keine eigene Testdatei. Die Tests stehen bei den Gruppen oben.

---

## Teil B2 — Zellmodule: Oberfläche (frontend/src/features/cell-modules/components, config)

Dieser Teil beschreibt die Fenster, Dialoge und Panels, mit denen man die Zellmodule bedient, sowie die 3D-Helfer im Viewport. Die Datenmodelle und Hooks unter `frontend/src/features/cell-modules/model/` beschreibt ein eigener Abschnitt; hier steht nur, welche davon eine Komponente benutzt.

Pfadangaben: Dateien in den Tabellen stehen als Pfad ab `frontend/src/`. In den Spalten "Modelle/Hooks" und im Fließtext bezieht sich `model/…` und `config/…` auf `frontend/src/features/cell-modules/`, andere Feature-Ordner (zum Beispiel `opcua-server/…`) auf `frontend/src/features/`, `tag-tool/…` auf `frontend/src/`. Dateinamen ohne Ordner liegen im Ordner der Tabelle des jeweiligen Abschnitts; `AutolocateActions.tsx` und `CellWatchPanel.tsx` liegen in `frontend/src/features/opcua-server/components/`, `ViewportHeaderControls.tsx` und `Viewport.tsx` in `frontend/src/features/viewport/components/`.

Die Komponenten halten keinen eigenen Zustand über die App hinweg. Sie lesen den Szenenzustand `ViewportSceneState` (`frontend/src/features/viewport/model/sceneState.ts`) und rufen Hooks aus `model/` auf. Die Kamerabilder kommen aus der gemeinsamen Komponente `CameraView` (`frontend/src/features/opcua-server/components/CameraView.tsx`). Die Kamerafenster dieser Gruppe (`Layer2CameraWindow`, das Kamerabild in `AutolocateLayer1Window`, `HandEyeCalibrationWindow`, `TagReferenceWindow`) binden sie nur ein; sie sind in den Abschnitten Layer 1, Layer 2, Hand-Auge und Einmessen beschrieben.

**Einstiegspunkte und Einhängung in die App**

```
frontend/src/app/layout/DesktopLayout.tsx
├── Viewport-Panel
│   ├── Kopfzeile: ViewportHeaderControls   (features/viewport/components)
│   │   Knopf "Settings" öffnet das Menü mit den Abschnitten:
│   │   ├── "Viewport":          Schalter "Welt-Tags", "Welt-Tag-Fehler (%)"
│   │   ├── "Zellüberwachung":   je Kamera "Kalibrierung"; bei Kamera 2 "Kalibrierung Hand-Eye";
│   │   │                        "Überwachung" (WatchTimingFields); Knöpfe "Modulkatalog", "Tags einmessen"
│   │   │                        Fenster: ModuleCatalogModal, HandEyeCalibrationWindow, TagReferenceWindow
│   │   │                        Modal (außerhalb des Menüs): CameraCalibrationModal (opcua-server)
│   │   └── "Solver", "Gizmo Options" (nicht Teil dieses Abschnitts)
│   └── Viewport                            (features/viewport/components/Viewport.tsx), 3D-Szene
│       CellModuleMarkers, WorldTagMarkers, WorldTagDistances, FlangeTrajectoryMarker,
│       HandCameraMarker, Layer2GhostRobot, Layer2ProposalMarkers
└── rechte Spalte (Panel ab Zeile 211)
    ├── CellWatchPanel                      (features/opcua-server/components)
    │   └── AutolocateActions               (features/opcua-server/components)
    │       Knopf "Level 1 Positionierung ausführen" -> Fenster "Zelle scannen" (AutolocateLayer1Window, compact)
    │       Knopf "Tag-Positionen anzeigen"  -> TagPositionsWindow
    │       nur bei laufender Überwachung:
    │         ApproachPlanPanel (compact)   -> RouteList, Layer2CameraWindow
    │         PickPlacePanel
    │       CellChangeAlert                  (Alarm, oben mittig)
    └── ServerManager                        (nicht Teil dieses Abschnitts)
```

Die größte Datei des Bereichs ist `ModuleCatalogModal.tsx` mit 1642 Zeilen, gefolgt von `TagReferencePanel.tsx` (1115).

### Modulkatalog und Platzieren

**Zweck:** Der Modulkatalog legt fest, welcher AprilTag an welchem Modul, Roboter oder Welt-Tag hängt. Dort ordnet man 3D-Modelle zu, platziert Tags auf Modellflächen und exportiert, importiert oder setzt den Katalog zurück. Gespeichert wird im Backend unter `/api/module-catalog`.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/ModuleCatalogModal.tsx` | Dialog "Module catalog" aus dem Knopf "Modulkatalog". Liste mit Vorschaubild je Eintrag, Knöpfe "Edit", "Delete", "+ New module", "+ New world tag", "Export JSON", "Import JSON", "Reset to defaults". Editor mit Feldern Name, Module ID, Type (Module / Robot / World tag), Robot-OPC-UA-Adresse und Robot model (nur bei Robot), Bounding-Box in cm (ohne Modell), 3D model (Auswahl oder "Other URL…"), AprilTags des Moduls ("+ Another tag", "Remove tag", "Place on model", Drehknöpfe). Unten "Cancel" und "Save". | `useModuleCatalog`, `useModuleCatalogSync`, `saveModuleCatalog`, `resetModuleCatalog` (`model/catalogStore`); `validateModule` (`model/catalogValidation`); `invalidateChangedMeasurements` (`model/tagMeasurementState`); `placementFromCorner`, `placementFromEdges`, `tagPoseFromPlacement`, `turnPlacement` (`model/tagPlacement`); `ROBOT_MODEL_OPTIONS` (`robot-control/model/robotModels`); `KNOWN_ROBOT_SERVER_URLS` (`opcua-server/config/robotServers`); `modelFiles` (virtuelles Modul) |
| `frontend/src/features/cell-modules/components/ModuleCatalogButton.tsx` | Ein Knopf "Module catalog", der den Dialog öffnet. Die Komponente wird im App-Code nirgends eingebunden (siehe Auffälligkeiten). | `ModuleCatalogModal` |
| `frontend/src/features/cell-modules/components/ModulePreview.tsx` | Vorschau eines Moduls im Dialog (Miniatur je Eintrag und große Ansicht im Editor): Box, Modell, Tags mit Achsen. Fährt man über eine Modellkante, wird sie hervorgehoben; ein Klick greift Kante, Fläche oder Ecke für die Platzierung. Kamera per Maus drehen und verschieben. | `CellModuleModel`, `ModelErrorBoundary`, `geometryEdges`, `HighlightedEdge`, `MeshEdgeLines` (`ModelEdges`), `PlaneOverlay`, `PlacementMeasure`, `snapToSegments`, `useModelPlanes` (`ModelPlanes`), `pickOnEdge`, `closestPointOnSegment` (`model/modelEdges`), `placementMatches` (`model/tagPlacement`), `AprilTagPlane` (`WorldTagMarkers`) |
| `frontend/src/features/cell-modules/components/ModelPlanes.tsx` | Nach "Place on model" werden die großen ebenen Flächen des Modells farbig gezeigt. Klick auf eine Fläche, dann "a corner" oder "two edges", dann eine Kante ("along the edge"). Dazu Messlinien vom Ursprung. Hinweis "Bridge gaps up to (mm)": Teile einer Ebene bis zu diesem Abstand gelten als eine Fläche. | `useModelPlanes` (startet `model/planeWorker.ts` als Web Worker, ohne Worker synchron über `findPlanes`), `DEFAULT_PLANE_OPTIONS`, `ModelPlane` (`model/modelPlanes`), `placementFootPoint`, `placementPosition` (`model/tagPlacement`), `SurfaceLines` (`ModelEdges`) |
| `frontend/src/features/cell-modules/components/ModelEdges.tsx` | Zeichnet Modellkanten nur dort, wo sie sichtbar sind, und nur im Platzierungsmodus. Die Kanten eines Netzes werden erst beim ersten Hovern berechnet und gecacht. | `THREE.EdgesGeometry`, `LineSegments2` (three.js), `Vec3` (`config/moduleCatalog.ts`) |
| `frontend/src/features/cell-modules/components/CellModuleModel.tsx` | Lädt die 3D-Datei eines Moduls (URDF, DAE, glTF/GLB, STL) und setzt sie so, wie der Katalog es vorgibt (Skalierung, Drehung, Ursprung). Dieselbe Komponente zeichnet den Viewport und die Vorschau. | `URDFLoader` (`viewport/components`), `modelFormat`, `urdfPackageRoot` (`model/modelFit`), `createPromiseCache`, `enqueueModelLoad` (`model/modelLoadQueue`) |
| `frontend/src/features/cell-modules/components/ModelErrorBoundary.tsx` | Keine Anzeige. Lädt eine Modelldatei nicht, bleibt die Box stehen, und es erscheint nur eine Konsolenmeldung. | React-Error-Boundary (Klassenkomponente) |
| `frontend/src/features/cell-modules/config/modelFiles.d.ts` | Keine Anzeige. Typdeklaration für das virtuelle Modul `virtual:cell-module-files`, die Liste der auswählbaren Modelldateien. | liefert `vite.config.ts` |
| `frontend/src/features/cell-modules/config/moduleCatalog.ts` | Keine Anzeige. Datentypen des Katalogs, Konstanten und Standardkatalog (siehe Konfiguration). | `findModuleByTag` wird von `CellChangeAlert` und vom Viewport genutzt |
| `backend/catalog/module_catalog.json` | Keine Anzeige. Der gespeicherte Katalog im Repo: Revision 35, 8 Einträge (MOD-1, MOD-7, MOD-8, ROBOT_FRANKA, WORLD-0, WORLD-0-2, WORLD-0-3, WORLD-0-4). | gelesen und geschrieben über `GET` und `PUT /api/module-catalog` (`backend/src/backend/api/module_catalog.py`) und `services/module_catalog_store.py` |

**Ablauf**

1. Der Knopf "Modulkatalog" im Menü "Settings" öffnet den Dialog. Die Liste zeigt jeden Eintrag mit Name, Modul-ID, Größe, Lage der Tags und Modelldatei; Roboter und Welt-Tags sind gekennzeichnet.
2. Der Katalog kommt aus `catalogStore.ts` per `GET /api/module-catalog`. Die Browser-Kopie ist nur ein Ersatz, falls das Backend nicht erreichbar ist. Der Sync-Status steht oben im Dialog.
3. "Edit" und "+ New module" öffnen den Editor. Ein Tag, dessen Position sich geändert hat, verliert seine Messung (`invalidateChangedMeasurements`).
4. "Place on model" startet die Platzierung: Die Flächen werden gesucht, ein Klick wählt eine Fläche, dann Ecke oder Kanten. Daraus entsteht Position und Drehung des Tags im Modulkoordinatensystem (`tagPoseFromPlacement`). Mit "Done" wird übernommen, mit "Cancel" verworfen.
5. "Save" ist gesperrt, solange die Validierung Fehler meldet. Sonst schreibt `saveModuleCatalog(upsertModule(...))` den Katalog mit `PUT`. Jeder Schreibvorgang nennt die Revision, auf der er beruht. Bei gleichzeitiger Bearbeitung bekommt der spätere Schreiber einen Konflikt und lädt neu (Kommentar in `backend/src/backend/services/module_catalog_store.py`).
6. "Export JSON" lädt `module-catalog.json` herunter. "Import JSON" prüft jeden Eintrag mit `validateModule` und speichert erst danach. "Delete" fragt nach Bestätigung, "Reset to defaults" ebenfalls.

**Konfiguration**

- `frontend/src/features/cell-modules/config/moduleCatalog.ts`: `TAG_FAMILY = "tag36h11"`. `WORLD_TAG_IDS = [583, 584, 585, 586]`: feste Welt-Tags, die kein Modul tragen darf. `PI_ASSUMED_TAG_SIZE_M` = 0,08 m für Layer 1 und Layer 2, die Größe, die der Pi für Tags ohne Karteneintrag annimmt. `WORLD_TAG_THICKNESS_M = 0.002`. `TAG_FACE_PRESETS` (Flächenvorgaben für Tag-Drehungen), `UNKNOWN_MODULE_BOUNDS` (0,2 m Würfel), `MODULE_CATALOG` (Standardkatalog mit 7 Einträgen).
- Datenfelder: `CellModuleDefinition` mit `moduleId`, `name`, `kind` (`module`, `robot` oder `world`), `bounds`, `model`, `tags`, zusätzlich `robotModelId`, `serverUrl`, `worldPose` je nach Art. `ModuleModel` mit `url`, `scale`, `rotationDeg`, `origin`. `ModuleTag` mit `tagId`, `sizeM`, `position`, `rotationDeg` und den Messdaten.
- `ModelEdges.tsx`: `THRESHOLD_DEG = 25` (ab diesem Knickwinkel gilt eine Kante), `MAX_TRIANGLES = 120000` (größere Netze werden übergangen).
- `ModuleCatalogModal.tsx`: Das Feld "Bridge gaps up to (mm)" erlaubt 0 bis 100 mm. Neue Tags übernehmen die Größe des letzten Tags; ohne einen solchen Tag gilt `PI_ASSUMED_TAG_SIZE_M`.
- `vite.config.ts` bildet die Modelliste: Dateien unter `public/models/` (ohne die Meshes, die eine URDF nachlädt) und `public/urdf/*.urdf`. Erlaubte Endungen: urdf, dae, glb, gltf, stl.
- Speicherort: `backend/catalog/module_catalog.json` im Repo. Mit der Umgebungsvariable `WSC_DATA_DIR` nutzt das Backend stattdessen `$WSC_DATA_DIR/module_catalog.json` (`default_catalog_path`).

**Tests**

- Keine Komponententests in diesem Bereich.
- Logik in den Modellen: `model/catalogStore.test.ts`, `model/catalogSync.test.ts`, `model/catalogValidation.test.ts`, `model/tagPlacement.test.ts`, `model/modelEdges.test.ts`, `model/modelPlanes.test.ts`, `model/modelFit.test.ts`, `model/modelLoadQueue.test.ts`, `model/tagMeasurementState.test.ts`.
- Backend: `backend/tests/test_module_catalog_api.py`.

**Weiterlesen**

- `arbeitsplaene/modell-flaechen-tag-platzierung.md`: Tags über Flächen und Kanten des Modells platzieren (`ModelPlanes.tsx`).
- `arbeitsplaene/tag-drehen-90-grad.md`: Drehknöpfe im Katalog (`TurnButtons`).
- `arbeitsplaene/apriltag-welttag-konzept.md`: Welt-Tags und Tag-Größenregel.
- `arbeitsplaene/apriltag-tagmap-ueber-opcua.md`: Tag-Größenregel und Tag-Map.
- `vision-system-integration.md`: Überblick über die Vision-Integration.

### Autolocate Layer 1 (Zelle scannen und Tag-Positionen)

**Zweck:** Die Deckenkamera (Kamera 1, Pi 1) erkennt alle AprilTags. Daraus entstehen Module und Roboter in der 3D-Szene. Das Fenster zeigt das Ergebnis, bevor es übernommen wird.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/AutolocateLayer1Window.tsx` | Fenster "Zelle scannen" (mittig, 1000 × 580) mit dem Bild von Kamera 1 und den markierten Tags. Während des Scans "Deckenkamera erkennt die Module…". Danach "Erkannte Module" (Liste) oder "Kein Modul erkannt — nur Welt-Tags im Bild". Knöpfe "Module laden", "Ohne Module überwachen", "Abbrechen". "Module laden" ist nur möglich, wenn die Kamera über die Welt-Tags verortet ist. | `useVisionJob` (Scan-Job auf Pi 1), `useContinuousVisionJob` (nur im ungenutzten Zweig), `useCameraHealth`, `useCameraOverlay`, `usePlaceRobots`, `useModuleCatalog`, `useAppFeedback`; `planLayer1Import`, `describeLayer1Import` (`model/layer1Import`); `trackingObservation`, `trackingReferences` (`model/moduleTracking`); `CameraView`, `CameraHealthBadge` (opcua-server) |
| `frontend/src/features/cell-modules/components/TagPositionsWindow.tsx` | Fenster "Tag-Positionen · vom Ursprung 0 / 0 / 0" (mittig) mit einer Tabelle aller Tags: Tag, Name, x, y, z in mm, Drehung, Quelle ("Katalog", "gesehen" oder "berechnet", mit "· Layer 2" bei genauer Messung). Der Knopf "Kopieren" legt die Tabelle für eine Tabellenkalkulation in die Zwischenablage. | `useModuleCatalog` (`model/catalogStore`), `listTagPositions`, `tagPositionsText` (`model/tagPositions`), `FloatingWindow` |

**Ablauf**

1. Der Knopf "Level 1 Positionierung ausführen" im Panel der Zellüberwachung ruft `scan()` in `AutolocateActions.tsx` auf. Das erhöht `scanRequest` und öffnet das Fenster.
2. Das Fenster startet den Scan-Job auf Pi 1 (`useVisionJob`). `planLayer1Import` macht daraus einen Plan: Module als Boxen (grob, orange im Viewport), Roboter als Zwilling und Welt-Tags.
3. "Module laden" übernimmt den Scan in die Szene (`onImported`) und schließt das Fenster (`onClose`). Der Scan wird dann die neue Referenz der Überwachung. "Ohne Module überwachen" setzt die leere Zelle als Referenz (`onWatchWithoutModules`). "Abbrechen" beendet ohne Laden (`onCancel`).
4. Für die Nachverfolgung nach einer Verschiebung läuft derselbe Scan ohne Fenster (`headless`, mit `followTagIds`). Das Ergebnis wird nur als Vorschlag gemeldet (`onProposal`). Die Szene ändert sich dabei nicht von selbst.
5. Im App-Code wird das Fenster nur mit `compact` gerendert. Der Zweig ohne `compact` (Fenster "Autolocate Layer 1 · Kamera 1" mit "Tags erkennen", "In Viewport übernehmen", Tracking-Schalter) ist daher nicht erreichbar, siehe Auffälligkeiten.

**Konfiguration**

- `AUTO_RESCAN_MS = 1500` in `AutolocateLayer1Window.tsx`: Abstand bis zum nächsten Scan der Zellüberwachung (laut Kommentar).
- Fenstertitel über `LAYER_LABELS[PI_1_SERVER_URL]` (`opcua-server/config/piServers.ts`): "First Layer".
- Nur im ungenutzten Zweig: `CONFIRM_S_RANGE` (1 bis 30 s), `DISTANCE_CM_RANGE` (1 bis 50 cm), Browser-Speicher unter `wsc.autolocate.layer1.trackingThresholds`, `DEFAULT_TRACKING_THRESHOLDS` (`model/moduleTracking`).

**Tests**

- Keine Komponententests.
- Logik: `model/layer1Import.test.ts`, `model/moduleTracking.test.ts`. Das Format des Scan-Ergebnisses prüft `opcua-server/model/visionResult.test.ts`.

**Weiterlesen**

- `arbeitsplaene/zellen-ueberwachung.md`: Zellüberwachung mit der Deckenkamera.
- `arbeitsplaene/apriltag-welttag-konzept.md`: die vier festen Welt-Tags.

### Status und Alarme (Zellüberwachung)

**Zweck:** Die Überwachung prüft laufend, ob Module verschoben, entfernt oder neu hinzugekommen sind. Ein Hinweis erscheint erst, wenn die Änderung so lange besteht, wie es die eingestellte Zeit verlangt. Der Nutzer übernimmt die Änderung oder ignoriert sie.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/CellChangeAlert.tsx` | Alarmkasten oben mittig mit der Überschrift "Zelle verändert" und "N Änderungen erkannt". Je Änderung ein Eintrag: "Verschoben" (gelb, mit Abstand und Drehung), "Neu" (grün) oder "Entfernt" (rot). Knöpfe "Übernehmen" (entfernte Module herausnehmen, neue und verschobene dort platzieren), "Ignorieren" (der jetzige Zustand wird die Referenz) und das Kreuz oben rechts zum Schließen, ohne etwas zu ändern. Während die Deckenkamera misst, ist "Übernehmen" gesperrt. | `CellChange` (`model/cellWatch`), `findModuleByTag` (`config/moduleCatalog.ts`), `createPortal` (in `document.body`) |
| `frontend/src/features/cell-modules/components/WatchTimingFields.tsx` | Im Menü "Settings" unter "Zellüberwachung", Bereich "Überwachung": Felder "Prüfen alle (s)" und "Änderung melden nach (s)". Leer heißt Standardwert, grau dargestellt. | `useAutolocateUi`, `autolocateUi.setTiming` (`model/autolocateUi`), `SecondsField` (`shared/ui`) |

**Ablauf**

1. Der Schalter "Zelle überwachen" im Panel der Zellüberwachung schaltet die Überwachung ein. Das Panel zeigt "Pausiert, solange der Roboter sich bewegt".
2. Die Felder legen fest, wie oft die Deckenkamera prüft und wie lange eine Änderung bestehen muss. Der Pi übernimmt die Werte. Ein geänderter Wert startet die Überwachung auf derselben Referenz neu.
3. Hält eine Änderung lange genug, setzt `AutolocateActions.tsx` den Alarm, und `CellChangeAlert` erscheint. Im 3D-Fenster zeigt `Layer2ProposalMarkers` die alte Lage rot und die neue grün.
4. "Übernehmen" ruft `acceptChanges` auf. "Ignorieren" ruft `dropChanges` mit neuer Referenz auf. Das Kreuz schließt den Alarm, ohne die Referenz zu ändern. Der Hinweis bleibt zu, bis sich wieder etwas ändert.

**Konfiguration**

- `model/autolocateUi.ts`: `DEFAULT_WATCH_INTERVAL_S = 3` (Prüfintervall), `DEFAULT_WATCH_CHANGE_S = 8` (Meldezeit). Das Feld "Änderung melden nach" setzt zugleich `persistS` und `missingPersistS`.

**Tests**

- Keine Komponententests. Logik der Änderungserkennung: `model/cellWatch.test.ts`.

**Weiterlesen**

- `arbeitsplaene/zellen-ueberwachung.md`.

### Layer 2 (Annäherung und Messung mit der Handkamera)

**Zweck:** Nach Layer 1 fährt der Roboter mit der Handkamera (Pi 2) vor jeden Modul-Tag. Dort misst er den Tag genauer und zentriert ihn. An einem Welt-Tag ankert er die Roboterbasis neu. Das Ergebnis erscheint zuerst als Vorschlag (alt rot, neu grün) und wird erst mit "Übernehmen" in die Szene übernommen.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/ApproachPlanPanel.tsx` | Panel "Layer 2" unter der Überwachung. Knöpfe "Level 2 Positionierung ausführen", "Neu berechnen" (neue Planung aus der aktuellen Stellung des Zwillings), "Halt", "Erneut übernehmen" (Roboter), "Hand-Auge-Sitzung beenden", "Neu ankern" und nach dem Lauf "Übernehmen" oder "Ignorieren". Statuszeile mit dem Fortschritt ("Fährt zu …", "Korrektur n: … mm neben der Bildmitte"). Im App-Panel ist `compact` gesetzt: Erklärungstext und die Felder "Abstand Modul-Tag (cm)" und "Abstand Welt-Tag (cm)" sind ausgeblendet. | `useOpcuaServer`, `useRobotControl`, `useHandEyeCalibration`, `useCellRobot`, `useCameraOverlay`, `useRobotHold`, `useModuleCatalog`; `planApproach`, `approachTags`, `approachTrajectory`, `anchorCandidates` (`model/approachPlan`); `measureTagViews`, `fuseTagViews`, `viewsAround` (`model/tagViews`); `layer2Gate` (`model/layer2Gate`); `measureTags` (`model/measureTagsCall`); `centreOnTag` (`model/centreOnTag`); `driveJoints` (`model/robotDrive`); `baseCorrection` (`model/tagServo`); `beginRobotOperation`, `endRobotOperation` (`model/robotOperation`) |
| `frontend/src/features/cell-modules/components/RouteList.tsx` | Liste der Anfahrpunkte in Fahrreihenfolge, zuerst der Roboter als Anker. Der angefahrene Punkt pulsiert, gemessene Punkte bekommen einen grünen Haken. Fehlgeschlagene oder nicht erreichbare Punkte sind gelb, mit Grund (zum Beispiel "jede Pose würde den Wagen berühren"). | Props `stops` (`ApproachStop`) und `progress` (`RouteProgress`, `model/routeProgress`) |
| `frontend/src/features/cell-modules/components/Layer2CameraWindow.tsx` | Fenster "Level 2 · Second Layer" unten links (520 × 420) mit dem Bild der Handkamera und dem gesuchten Tag. Es öffnet sich beim Lauf und schließt sich am Laufende selbst. Schließen blendet nur das Bild aus; der Lauf geht weiter. | `useCameraHealth` (opcua-server), `CameraView`, `CameraHealthBadge`, `LAYER_LABELS`, `PI_2_SERVER_URL` (opcua-server) |

**Ablauf**

1. Voraussetzungen: Die Überwachung läuft, die Handkamera ist verbunden, und das Fenster "Tags einmessen" ist nicht offen (dann pausiert die Annäherung). Sonst bleibt der Knopf gesperrt, bis `layer2Gate` die Lage freigibt.
2. Die Planung: Mit verbundenem Roboter wird aus den echten Gelenkwinkeln geplant. Ohne Roboter entsteht nur eine Vorschau aus der Lage, die Layer 1 dem Roboter gab (Kopfkommentar von `ApproachPlanPanel.tsx`). Die Bahn geht als `flangeTrajectory` in die Szene.
3. Der Lauf fährt die Sichtpunkte nacheinander an (`driveJoints`), misst dort den Tag (Pi-Methode `MeasureTags` über `measureTags`) und zentriert ihn (`centreOnTag`). Nach dem Lauf geht es zurück in die Ausgangsstellung.
4. Am Anker (Welt-Tag) wird die Basis korrigiert (`baseCorrection`), der Zwilling verschoben und die Module neu geplant.
5. Das Ergebnis geht über `setLayer2Proposal` in die Szene. "Übernehmen" übernimmt die gemessenen Positionen, "Ignorieren" verwirft sie. "Halt" stoppt den Lauf.

**Konfiguration**

- `LAYER2_DRIVE_MAX_SPEED = AUTO_DRIVE_MAX_SPEED` = 0,1 (`model/robotHold.ts`): Fahrten mit `go_to` und `max_speed` 0,1.
- `AT_HOME_RAD = 0.01`: Toleranz je Gelenk, ab der der Arm als in der Ausgangsstellung gilt.
- `DEFAULT_APPROACH_STANDOFF_M`, `DEFAULT_ANCHOR_STANDOFF_M` (`model/approachPlan`); `LAYER2_HEADING_MAX_SPREAD_DEG`, `MIN_TAG_MEASUREMENTS`, `MODULE_VIEW_SPARE_TURNS` (`model/tagViews`).
- Eingabegrenzen: 10 bis 100 cm für den Modul-Tag, 10 bis 200 cm für den Welt-Tag.

**Tests**

- Keine Komponententests.
- Logik in den Modellen: `model/approachPlan.test.ts`, `model/tagViews.test.ts`, `model/layer2.test.ts`, `model/layer2Gate.test.ts`, `model/layer2Run.test.ts`, `model/centreOnTag.test.ts`, `model/tagServo.test.ts`, `model/cellClearance.test.ts`, `model/routeProgress.test.ts`.

**Weiterlesen**

- `arbeitsplaene/layer2-annaeherung.md`: schrittweise Annäherung an den Tag.
- `arbeitsplaene/layer2-lauf-hand-pi.md`: Anker, Planung und Messung auf dem Hand-Pi.
- `arbeitsplaene/layer2-measuretags.md`: MeasureTags.
- `arbeitsplaene/layer2-schraege-ansicht.md`: schräger Blick auf Tags.
- `arbeitsplaene/apriltag-hand-auge-ankern.md`: Ankern am Welt-Tag.

### Tags einmessen (Hand-Kamera, Bezugskanten)

**Zweck:** Die Handkamera misst Tags gegen Bezugskanten. Modul-Tags werden gegen Kanten am Modell gemessen, Roboter-Tags über die Gelenkwinkel. Die Ergebnisse können in den Katalog übernommen werden. Neue oder geänderte Tags werden einmal eingemessen.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/TagReferenceWindow.tsx` | Knopf "Tags einmessen" im Menü. Das Fenster "Tags einmessen · Hand-Kamera" (unten links, 860 × 600) hat links bei eingeschalteter "Live-Ansicht" das Kamerabild mit der Kantensuche. Rechts klappt der Abschnitt "Einstellungen" auf, mit den Schaltern "Vorschau während des Laufs", "Schritt für Schritt", "Alle Tags neu einmessen (Testphase)", "Modul-Tags", "Roboter-Tags", "Roboter-Tags mit Kanten gegenprüfen", "Mess-Protokoll speichern", "Debug-Bild nach der Messung anzeigen", "Kantenwerte live anzeigen" und dem Abschnitt "Ebenen im Bild". Darunter die Liste (`TagReferencePanel`). | `useTagReferenceSettings`, `tagReferenceSettings` (`model/tagReferenceSettings`); `enterLiveView` (`model/tagReferenceLiveView`); `useOpcuaServer` (`writeNodeValue`); `CameraView`, `DisclosureSection`, `Toggle` |
| `frontend/src/features/cell-modules/components/TagReferencePanel.tsx` | Liste der ausstehenden Tags mit Häkchen, getrennt in Modul-Tags und den Abschnitt "Roboter-Tags (über Gelenkwinkel)". Knöpfe "Ausstehende Tags einmessen (…)" oder "Alle Tags einmessen", je nach Testphase, "Halt" und "Erneut übernehmen". Je Ergebnis "In Katalog übernehmen" (bei Testphase-Ergebnissen). "Vorschau für Tag" zeigt die Kantensuche für einen gewählten Tag, ohne zu fahren, und "Vorschau an" und "Vorschau aus" schalten die Vorschau während des Laufs um. Bei Schritt für Schritt wartet der Lauf auf "Messen" oder "Überspringen" bzw. "Tag überspringen". | `useCellRobot`, `useRobotHold`, `useHandEyeCalibration`, `useRobotControl`, `useTagReferenceImageUrl`, `useTagReferencePreviewStatus` (`model/useTagReferenceLive`); `measureTagReference`, `planTagReferenceView`, `setTagReferencePreview` (`model/tagReferenceCall`); `measureTagReferences` (`model/tagReferenceRun`); `measureRobotTags` (`model/robotTagRun`); `applyTagMeasurement`, `selectTagReferenceTags` (`model/tagMeasurementState`); `createPreviewController`, `startPreviewRenewal` (`model/tagReferencePreview`); `compareRobotTagWithEdges` (`model/tagMethodComparison`); `tagEdgeDistances` (`model/tagEdgeDistance`) |
| `frontend/src/features/cell-modules/components/TagReferenceLiveParts.tsx` | Bausteine der Anzeige: die Fortschrittsleiste je Tag (Schritte mit offenem, aktivem, erledigtem oder fehlgeschlagenem Zustand), die Live-Werte der Kantensuche ("Live · Tag n") und das Ergebnis der letzten Messung mit einer Zeile je Kante, Korrektur und dem Protokollbild. | `phaseMarks`, `phaseLabel`, `PHASE_LABELS` (`model/tagReferenceProgress`); Typen aus `model/tagReferenceCall` |

**Ablauf**

1. Im Menü "Settings" unter "Zellüberwachung" öffnet "Tags einmessen" das Fenster. Die Handkamera wechselt auf den Modus "tagreference", der Pi wird per OPC-UA-Schreibvorgang umgeschaltet, und beim Schließen wird der vorige Modus wiederhergestellt.
2. Je Tag fährt der Roboter vor den Tag (`planTagReferenceView`). Der Pi sucht die Kanten (`measureTagReference`, Pi-Methode `MeasureTagReference`). Fehlt sie auf dem Pi, meldet das Panel: "neuen Stand aufspielen". Roboter-Tags werden über die Gelenkwinkel gemessen (`measureRobotTags`).
3. Das Ergebnis zeigt Abweichung und Korrektur. "In Katalog übernehmen" schreibt die Messung in den Katalog (`applyTagMeasurement`). Mit "Alle Tags neu einmessen (Testphase)" werden Ergebnisse nicht automatisch gespeichert.
4. Bei "Schritt für Schritt" wartet der Lauf vor jeder Messung auf den Nutzer.

**Konfiguration**

- Standardwerte der Schalter: `model/tagReferenceSettings.ts`. Ebenen im Bild: `OVERLAY_LAYERS` aus demselben Modul.
- `DEFAULT_CENTRING_STANDOFF_M` (`model/tagReferenceRun.ts`): Abstand beim Zentrieren vor dem Tag.
- `TEST_PHASE_NOTE` in `TagReferencePanel.tsx`: Hinweis "Testphase: nicht in den Katalog übernommen".
- Overlay-Modus `tagreference` (`CameraOverlayMode` aus `opcua-server/config/piServers.ts`).

**Tests**

- Keine Komponententests. Logik in den Modellen: `model/tagReferenceCall.test.ts`, `model/tagReferenceRun.test.ts`, `model/tagReferenceProgress.test.ts`, `model/tagReferencePreview.test.ts`, `model/tagReferenceSettings.test.ts`, `model/tagReferenceFusion.test.ts`, `model/tagMethodComparison.test.ts`, `model/tagEdgeDistance.test.ts`, `model/tagMeasurementState.test.ts`, `model/robotTagRun.test.ts`.

**Weiterlesen**

- `arbeitsplaene/tag-einmessen-bezugskanten.md`: Tag-Position per Hand-Kamera einmessen.
- `arbeitsplaene/tag-einmessen-liveview.md`: Live-Ansicht der Kantensuche und Testphase.

### Hand-Auge-Kalibrierung (Kamera 2)

**Zweck:** Die Kalibrierung bestimmt, wo die Handkamera am Roboterflansch sitzt. Der Roboter fährt selbst Posen über einem Schachbrett ab, der Hand-Pi nimmt die Bilder auf und berechnet das Ergebnis.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/HandEyeCalibrationWindow.tsx` | Knopf "Kalibrierung Hand-Eye" im Menü bei Kamera 2. Fenster "Hand-Kamera kalibrieren · Kamera 2 · Second Layer" (mittig, 1000 × 600): links das Kamerabild, rechts Roboterstatus, Flansch-Link, die Steuerung (`HandEyeAutoPanel`), der Befehlsstatus, die Liste der Aufnahmen und das Ergebnis. Zum Ergebnis gehören der Vorhersagefehler, der Reprojektionsfehler, der Brett-Maßstab und die Gegenprobe mit der Tag-Lage im Katalog. Warnungen gibt es für Kamera-Intrinsics aus Werkseinstellung oder Platzhalter. | `useHandEyeCalibration`, `useCameraHealth`, `useCellRobot`; `lookDownHomeAngles` (`model/approachPlan`); `rpyDegFromQuaternion` (`model/tagGeometry`); `HandEyeResult` (`model/handEye`) |
| `frontend/src/features/cell-modules/components/HandEyeAutoPanel.tsx` | Knopf "Kalibrierung starten" und Helfer: "Nur Ausgangslage anfahren", "Brett prüfen", "Brettursprung anfahren" (mit Rückfrage "Ja, anfahren"), "Stopp — Roboter anhalten". Der Ablauf ist: Ausgangsstellung, dann die Posen rund ums Brett, je Pose warten bis der Arm steht und eine Aufnahme machen, am Ende berechnen und speichern. | `useOpcuaServer`, `useRobotControl`; `generateHandEyePoses` (`model/handEyeAutoPoses`); `handEyeSampleJson`, `handEyeBoardStartJson`, `HAND_EYE_MIN_SAMPLES` (`model/handEye`); `loadHandEyeBoard`, `saveHandEyeBoard`, `validateHandEyeBoard` (`model/handEyeBoard`); `measureBoard` (`model/measureBoardCall`); `boardOriginFlangeTarget`, `flangeToToolPose` (`model/handEyeBoardOrigin`); `readsSettled`, `jointsMoved`, `meanJoints` (`model/jointSettling`); `createRobotDriver`, `driveJoints`, `planMove` (`model/robotDrive`); `boardCentred`, `boardCentringStep` (`model/boardCentring`); `beginRobotOperation`, `endRobotOperation` (`model/robotOperation`) |
| `frontend/src/features/cell-modules/components/HandCameraMarker.tsx` | 3D: ein kleiner blauer Quader mit Sichtkegel am Flansch des Roboter-Zwillings, dort, wo die Kalibrierung die Kamera setzt. Die Anzeige folgt in jedem Bild den Gelenken des Zwillings. Sie erscheint, sobald eine aktuelle oder neue Kalibrierung vorliegt. | `useHandEyeCalibration`, `useRobotControl`, `useCellRobot`; `resolveRobotMountRotation` (`robot-control/model/robotModels`) |

**Ablauf**

1. Der Knopf bei Kamera 2 ist deaktiviert, solange Pi 2 nicht verbunden ist oder der Roboter fehlt (außer das Fenster ist schon offen). Ohne Roboter steht der Hinweis "Erst den Roboter verbinden -- die Kalibrierung fährt ihn selbst".
2. "Kalibrierung starten" fährt zuerst die Ausgangsstellung an, bei der die Kamera senkrecht auf den Tisch zeigt (`lookDownHomeAngles`). Dann folgen die Posen rund ums Brett. Je Pose wartet der Ablauf, bis der Arm ruhig steht (`readsSettled`), und löst eine Aufnahme aus. Die Sitzung kennt die Befehle `start`, `capture` und `finish`; sie gehen über den Hook `useHandEyeCalibration` per OPC UA an den Hand-Pi (siehe Weiterlesen). Am Ende berechnet der Pi und sichert die vorige Kalibrierung.
3. "Brett prüfen" lässt den Pi über `MeasureBoard` melden, ob das ganze Brett im Bild ist. "Brettursprung anfahren" fährt die Werkzeugspitze 10 mm vor die erste innere Brettecke und misst den Versatz.
4. Die Warnungen im Ergebnis: Brett-Maßstab weicht ab mehr als 0,003 von 1; die Gegenprobe mit dem Katalog weicht mehr als 10 mm ab.

**Konfiguration**

- `HandEyeAutoPanel.tsx`: `DISTANCE_M = 0.45` (Abstand Kamera zum Brett), `CAMERA_OFFSET_M = 0.05`, `SETTLE_MS = 800`, `FLANGE_READS = 5`, `FLANGE_READ_GAP_MS = 100`, `SETTLE_RETRIES = 3`, `CAPTURE_TIMEOUT_MS = 60000`, `AIM_MAX_RMS_M = 0.01`, `AIM_MAX_RMS_DEG = 2`, `ORIGIN_YAWS_DEG = [0, 90, -90, 180]`, `MAX_CENTRING_ROUNDS = 3`.
- `HandEyeCalibrationWindow.tsx`: `BOARD_SCALE_WARN = 0.003`. `intrinsicsTrusted`: die Quellen `calibration` und `file` gelten als vertrauenswürdig, `realsense_factory` und `placeholder` lösen Warnungen aus.
- Brett-Einstellungen werden über `model/handEyeBoard` gespeichert.

**Tests**

- `frontend/src/features/cell-modules/components/HandEyeCalibrationWindow.test.ts`: der einzige Komponententest des Bereichs. Er prüft `intrinsicsTrusted` und `intrinsicsWarning`.
- Logik in den Modellen: `model/handEye.test.ts`, `model/handEyeAutoPoses.test.ts`, `model/handEyeBoard.test.ts`, `model/handEyeBoardOrigin.test.ts`, `model/jointSettling.test.ts`, `model/boardCentring.test.ts`.

**Weiterlesen**

- `arbeitsplaene/handeye-schachbrett-automatik.md`: Schachbrett, Posen fährt der Roboter selbst.
- `arbeitsplaene/handeye-genauigkeit.md`: fester Versatz beim Anfahren.
- `arbeitsplaene/handeye-ueber-opcua.md`: Hand-Auge per OPC UA.
- `arbeitsplaene/hand-auge-brett-zentrieren.md`: Brett zentrieren (MeasureBoard).
- `arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md`: Linsenmodell je Kamera.

### Kalibrierung Kamera 1 mit Roboter

**Zweck:** Der Roboter hält das Kalibrierbrett an gespeicherten Positionen vor die Deckenkamera. Der Pi nimmt die Bilder auf und berechnet das Linsenmodell. Die Option gibt es nur für Kamera 1. Bei den anderen Kameras bleibt die Kalibrierung von Hand.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/BoardCalibrationRobot.tsx` | Drei Abschnitte im Kalibrierdialog der Kamera 1 (`CameraCalibrationModal`, opcua-server). `BoardRobotSetup`: "Positionen auswählen" oder "Neue Positionen", "Position übernehmen (n/N)" (der Arm wird von Hand gebracht), "Letzte verwerfen", "Abbrechen", Blättern mit den Knöpfen ◀ und ▶ samt Gelenkwinkeln. `BoardRobotStatus`: "Roboterfahrt starten", "Zange zugreifen", "Kalibrierfahrt starten", "Halt", "Weiter", dazu "Schritt n/N" und "n/N Bilder". `BoardRobotAfterRun`: Rückfahrt in die Neutralstellung und "Zange öffnen". | `useBoardCalibrationRun` (`model/useBoardCalibrationRun`, Typ `BoardCalibrationRun`); `CALIBRATION_POSITION_COUNT` (`model/boardCalibrationPoses`); `TARGET_PICTURES` (`model/boardCalibrationRunner`) |
| `frontend/src/features/cell-modules/components/RobotRunConfirm.tsx` | Bestätigungsdialog "Roboterfahrt starten" über dem Rest der Oberfläche. Er nennt, was passiert: Take Control und Sync werden automatisch ein- und danach ausgeschaltet, `go_to` mit `max_speed` 0,1, Bereich um den Roboter freihalten, Not-Halt bereit. Nur die Knöpfe "Abbrechen" und "Roboterfahrt starten" schließen ihn, ein Klick daneben nicht. Im Code wird er nur von `CameraCalibrationModal` benutzt. | `AUTO_DRIVE_MAX_SPEED` (`model/robotHold`), über `maxSpeed` mit `BOARD_DRIVE_MAX_SPEED` belegt |

**Ablauf**

1. Menü "Settings", Abschnitt "Zellüberwachung", "Kalibrierung" bei Kamera 1. Der Dialog zeigt "Kalibrierung mit Roboter starten".
2. Positionen: Den Arm von Hand oder über den Goal-Marker an die Stelle bringen, "Position übernehmen" drücken. Die gespeicherten Positionen gelten, bis alle neuen übernommen sind. Gehören die gespeicherten Positionen zu einem anderen Robotermodell, verlangt das Panel neue.
3. Start: Der Roboter fährt in die Neutralstellung. Der Nutzer legt das Brett in die Zange und drückt "Zange zugreifen" (schaltet den Greifer über den Befehl `toggleEndEffector`). Danach "Kalibrierfahrt starten".
4. Der Decken-Pi prüft an jeder Position, ob das ganze Brett im Bild ist. Ist es das nicht, probiert er bis zu zwei andere Winkel, dann die nächste Position. Nach dem Lauf fährt der Roboter zurück, und "Zange öffnen" gibt das Brett frei.

**Konfiguration**

- `BOARD_DRIVE_MAX_SPEED = AUTO_DRIVE_MAX_SPEED` = 0,1 (`model/useBoardCalibrationRun.ts`).
- `CALIBRATION_POSITION_COUNT` (`model/boardCalibrationPoses.ts`) und `TARGET_PICTURES` (`model/boardCalibrationRunner.ts`) legen Anzahl der Positionen und der Bilder fest.

**Tests**

- Keine Komponententests. Logik in den Modellen: `model/boardCalibrationPoses.test.ts`, `model/boardCalibrationRunner.test.ts`.

**Weiterlesen**

- `arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md`.

### Pick & Place

**Zweck:** Der Roboter nimmt einen Würfel vom eingelernten Aufnahmepunkt und legt ihn mittig auf einen gewählten Tisch (Anwendungsfall 1). Nach einer Verschiebung des Tisches holt er ihn von dort zurück (Anwendungsfall 2).

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/PickPlacePanel.tsx` | Panel "Pick & Place" unter Layer 2, nur bei laufender Überwachung. "Aufnahmepunkt einlernen" bzw. "Neu einlernen": den Arm von Hand an die Greifstelle bringen. "Ablegen auf" wählt einen Tisch (Hinweis "(nur grob)" bei ungenauer Lage). "Absetzhöhe (mm)" nimmt Werte von 0 bis 30. Mit "Messen von Ecke" wählt man die Ecke, von der aus die Würfelmitte gemessen wird. "Würfel ablegen" startet Anwendungsfall 1, "Würfel zurückholen" Anwendungsfall 2. Vor der Fahrt kommt ein Kästchen zur Bestätigung ("Der Greifer ist offen, …") mit "Roboter fährt los" und "Abbrechen". Während des Laufs stehen die Schritte da, mit "Weiter" (wenn der Greifer von Hand geschaltet wird) und "Halt". | `usePickPlaceRun` (`model/usePickPlaceRun.ts`); `PICK_PLACE_STEP_LABELS`, `pickPlaceStepKey`, `PickPlaceRoute` (`model/pickPlaceRoute`); `TableCorner` (`model/pickPlace`); `ViewportSceneState` |

**Ablauf**

1. "Aufnahmepunkt einlernen" liest die Gelenkstellung des Arms und merkt sie sich.
2. Die Tische kommen aus dem Katalog und aus Layer 1 und Layer 2. Der Weg wird vor der Fahrt geplant und als Bahn in der Szene gezeichnet (`setFlangeTrajectory`, `FlangeTrajectoryMarker`).
3. "Würfel ablegen" zeigt die Bestätigung. Danach läuft der Ablauf Schritt für Schritt: Greifen, Transport, Absetzen. Bewegungen nah am Tisch sind als langsam markiert. Nach dem Absetzen merkt sich die App den Tisch, damit "Würfel zurückholen" weiß, wo der Würfel liegt.
4. Gibt es am Roboter keinen Greifer-Befehl, schaltet der Nutzer den Greifer von Hand. Der Ablauf wartet dann auf "Weiter".

**Konfiguration**

- `CORNERS` (vier Tischecken) und `cornerLabel` in `PickPlacePanel.tsx`, mit dem Zusatz "(bei Tag 3)" für die Ecke x max, y min.
- Absetzhöhe: Eingabe von 0 bis 30 mm.

**Tests**

- Keine Komponententests. Logik in den Modellen: `model/pickPlace.test.ts`, `model/pickPlaceRoute.test.ts`, `model/pickPlaceFrame.test.ts`, `model/pickPlaceReturn.test.ts`.

**Weiterlesen**

- Keine Arbeitsplan-Doku im ADP-Repo gefunden (Suche nach „Pick and Place“ im Ordner `doc/` ohne Treffer).

### 3D-Darstellung im Viewport (Marker und Vorschauen)

**Zweck:** Die Komponenten zeichnen Module, Welt-Tags, Hand-Kamera, Roboter-Vorschauen und geplante Bahnen in die 3D-Szene. Sie zeigen nur an und ändern die Szene nicht.

**Dateien**

| Datei | Was der Nutzer sieht/tut | Benutzte Modelle/Hooks |
|---|---|---|
| `frontend/src/features/cell-modules/components/CellModuleMarkers.tsx` | Jedes erkannte Modul als Box. Gelb bedeutet nur grob von der Deckenkamera gesehen, grün bedeutet mit Layer 2 genau gemessen. Mit Modell darin, das die Box umrahmt. Beim Überfahren erscheint ein Tooltip mit Name, Tags, Genauigkeit, Warnungen (Tags widersprechen sich, Tag nicht im Katalog) und Weltkoordinaten. Roboter werden hier nicht gezeichnet, sondern vom Zwilling. | `CellModuleModel`, `ModelErrorBoundary`; `TAG_SPREAD_WARNING_M`, `PlacedCellModule` (`model/cellModules`) |
| `frontend/src/features/cell-modules/components/WorldTagMarkers.tsx` | Welt-Tags als Tafel mit dem echten tag36h11-Muster auf dem Boden, darüber ein Schloss-Symbol. Blau, solange sie der letzte übernommene Layer-1-Scan nicht gesehen hat, grün danach, gelb bei zu großer Eckenabweichung. Während der Überwachung zeigt ein Auge, ob die Deckenkamera den Tag gerade erkennt. `WorldTagDistances` zeigt die Strecke zwischen zwei Welt-Tags mit dem prozentualen Fehler, grün, gelb oder rot. | `AprilTagPlane`; `tagTexture` (`frontend/src/tag-tool/components/sceneParts.tsx`), `printedTagSizeM` (`frontend/src/tag-tool/model/tagFrame.ts`); `pairWorldTags`, `formatErrorPercent`, `CORNER_ERROR_WARN_PX`, `WorldTagCheck` (`model/worldTagCheck`) |
| `frontend/src/features/cell-modules/components/FlangeTrajectoryMarker.tsx` | Geplante Roboterbewegung: Punkte, Pfad, Pfeile, Halte, Beschriftungen, gestrichelte Hilfslinien und Boxen (Greiferfinger, Werkstück). Unerreichbare Ziele sind gelb. Quelle ist `sceneState.flangeTrajectory`, gesetzt von der Annäherung, der Kalibrierfahrt und Pick & Place. | `FlangeTrajectory` (`model/flangeTrajectory`); `resolveRobotMountRotation` (`robot-control/model/robotModels`) |
| `frontend/src/features/cell-modules/components/Layer2GhostRobot.tsx` | Transparente Kopie des Roboters in der Pose des nächsten Ziels: blau, wenn erreichbar, rot, wenn nicht. Man sieht Kollisionen mit dem Tisch, bevor sich der Arm bewegt. Die Kopie hat eine eigene URDF-Instanz; der echte Zwilling bleibt unberührt. | `URDFLoader` (`viewport/components`), `resolveRobotMountRotation`, `RobotOrigin` (`robot-control/model/robotModels`), `RobotPosePreview` (`viewport/model/sceneState`) |
| `frontend/src/features/cell-modules/components/Layer2ProposalMarkers.tsx` | Layer-2-Ergebnis vor der Übernahme: alte Lage rot, neue Lage grün, als Boxen. Dazu die Verschiebung in mm, die Roboterbasis als Scheibe mit Richtungspfeil, und "nicht entdeckt" für fehlende Module. | `Layer2Proposal` (`viewport/model/sceneState`); `robotBaseHeading` (`model/layer2`); `boundsCentre`, `boundsSize` (`config/moduleCatalog.ts`) |

**Ablauf (wann was erscheint)**

- `CellModuleMarkers`: immer, für alle Module des Szenenzustands.
- `WorldTagMarkers`: nur wenn der Schalter "Welt-Tags" im Menü aktiv und die Überwachung eingeschaltet ist. `WorldTagDistances` braucht den Schalter "Welt-Tag-Fehler (%)" und die Überwachung.
- `FlangeTrajectoryMarker`: sobald eine Bahn gesetzt ist.
- `HandCameraMarker`: sobald eine Hand-Auge-Lage vorliegt.
- `Layer2GhostRobot`: wenn eine Roboter-Vorschau und ein passender Roboter vorliegen.
- `Layer2ProposalMarkers`: wenn ein Layer-2-Vorschlag vorliegt, aus der Annäherung oder aus einer Änderung der Überwachung (`source: "watch"`).

**Konfiguration** (Farben als Konstanten)

- `PRECISION_COLORS`: grob `#f59e0b`, genau `#34d399` (`CellModuleMarkers.tsx`).
- `LOCK_COLOR` `#38bdf8`, `SEEN_COLOR` `#34d399`, `MISFIT_COLOR` `#fbbf24`, `LOCK_HEIGHT_M = 0.15` (`WorldTagMarkers.tsx`).
- `REACHABLE_COLOR` `#38bdf8`, `UNREACHABLE_COLOR` `#ef4444` (`Layer2GhostRobot.tsx`).
- `OLD_COLOR` `#ef4444`, `NEW_COLOR` `#22c55e` (`Layer2ProposalMarkers.tsx`).
- `COLOR` `#a6ddff` für Bahnen, `UNREACHABLE_COLOR` `#f59e0b` (`FlangeTrajectoryMarker.tsx`).
- `CONE_M = 0.08`, `CONE_HALF_WIDTH_M = 0.055`, `CONE_HALF_HEIGHT_M = 0.042` (`HandCameraMarker.tsx`).

**Tests**

- Keine Komponententests. Logik in den Modellen: `model/worldTagCheck.test.ts`, `model/modelEdges.test.ts`, `model/cellModules.test.ts`, `model/layer2.test.ts`.

**Weiterlesen**

- `arbeitsplaene/apriltag-welttag-konzept.md`.
- `vision-system-integration.md`.

---

## Teil B3 — OPC-UA-Anbindung, Tag-Tool, gemeinsame Bausteine und Backend

**Stand:** Branch `Ungetestet` (`5812275`), Vergleich gegen `origin/dev` (`d824975`).

**Umfang.** Alle neuen (A) und geänderten (M) Nicht-Test-Dateien aus `git diff origin/dev..HEAD`, soweit sie nicht unter `frontend/src/features/cell-modules/` oder `frontend/public/models/` liegen. Die Abschnitte zu `cell-modules` stehen in einem eigenen Teil des Handbuchs. Gelöschte Dateien (D) sind im passenden Abschnitt genannt. Bei Status M steht „aus dev, ergänzt um …“: beschrieben ist nur, was wir ergänzt oder geändert haben. Was in dev stand und unverändert ist, wird nicht beschrieben. Testanzahlen sind Zählungen über `it(`/`test(`-Zeilen (Frontend) bzw. `def test_` (Backend); Tabellentests (`.each`) zählen als eine Zeile.

**Zusammenspiel in einem Bild.**

```
Browser (React, three.js)
  |-- WebSocket /ws ............> Backend (FastAPI, asyncua) --OPC UA--> Roboter-Server
  |-- REST /api/module-catalog > Backend                    --OPC UA--> Vision-Pis
  |-- MJPEG-Livestream: direkt vom Pi, http://<Pi>:<Port>/stream.mjpg (am Backend vorbei)
```

- Die Oberfläche spricht nicht selbst mit OPC UA. Sie schickt Befehle (`connectServer`, `callRawMethod`, `subscribeNode` …) über den WebSocket. Das Backend führt sie aus und meldet Ergebnisse und Zustandsänderungen zurück.
- Die Vision-Pis sind ebenfalls OPC-UA-Server. Das Kamerabild holt der Browser direkt per HTTP vom Pi (`useMjpegStreamUrl`, `CameraLiveImage.tsx`).

---

### OPC-UA-Verbindung und Pi-Server

**Zweck.** Verbindungen zu OPC-UA-Servern aufbauen, überwachen und trennen. Zwei Vision-Pis (Decken-Kamera „First Layer“, Hand-Kamera „Second Layer“) sind fest eingetragen und verbinden sich selbst, sobald der Server-Manager erscheint. Roboter-Server fügt man über das „+“ hinzu. Danach liest das Backend ihre Struktur aus (Discovery).

**Einstieg.**
- Rechte Spalte des Desktop-Layouts (`frontend/src/app/layout/DesktopLayout.tsx`): `ServerManager` (Liste der Roboter-Server) unter dem `CellWatchPanel`.
- Der „+“-Knopf im `ServerManager` öffnet `ConnectOpcUa`.
- Verbindungsstatus der Pis: `PiStatus` in der Kopfleiste der Ansicht (`ViewportHeaderControls`, Kamera-Abschnitt) und in `PiStatusList`.

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/src/features/opcua-server/config/piServers.ts` (A) | Feste Adressen der Vision-Pis, Auflösung der Pi-Knoten-IDs aus dem Namespace-Array, Bau der Stream-URLs, Fehlertexte, Gesundheitszustände. Rund 560 Zeilen. | `PI_1_SERVER_URL`, `PI_2_SERVER_URL`, `LAYER_LABELS`, `resolveVisionBinding`, `VisionBinding`, `buildMjpegStreamUrl`, `START_SINGLE_JOB_ERRORS`, `DEVICE_HEALTH` |
| `frontend/src/features/opcua-server/config/robotServers.ts` (A) | Vorschläge für Roboter-Server-Adressen im „+“-Dialog. | `KNOWN_ROBOT_SERVER_URLS` (127.0.0.1 und 10.10.38.25 bis .28) |
| `frontend/src/features/opcua-server/context/OpcuaServerContext.tsx` (M) | aus dev, ergänzt um `disconnectServerCleanly` und optionale `eventTypeNodeIds` bei `subscribeEvent`; entfernt: die `…AndWait`-Varianten (`browseAddressSpaceChildrenAndWait`, `readAddressSpaceSubtreeAndWait`, `callRawMethodAndWait`, `writeNodeValueAndWait`). | `OpcuaServerProvider`, `useOpcuaServer`, `OpcuaServerContextValue` |
| `frontend/src/features/opcua-server/context/PiActivityContext.tsx` (A) | Hält fest, welches Fenster einen Pi gerade belegt (Job oder Kalibrierung). | `PiActivityProvider`, `usePiActivity`, `usePiBlocker` |
| `frontend/src/features/opcua-server/model/piActivity.ts` (A) | Reine Funktionen für die Belegungsliste. | `PiActivity`, `addPiActivity`, `removePiActivity`, `findBlockingPiActivity` |
| `frontend/src/features/opcua-server/model/piMethodCall.ts` (A) | Methode auf einem Pi aufrufen und auf die Antwort warten (Zeitlimit 15 s, Abfrage alle 100 ms). | `callPiMethod`, `PiCallOutcome` |
| `frontend/src/features/opcua-server/model/useConnectServerWithFeedback.ts` (A) | Verbinden plus Discovery mit Lade- und Fehlermeldungen. Benutzt vom „+“-Dialog und vom Zellen-Schalter. | `useConnectServerWithFeedback` |
| `frontend/src/features/opcua-server/components/ConnectOpcUa.tsx` (M) | aus dev, ergänzt um: Vorschläge aus `KNOWN_ROBOT_SERVER_URLS`; die Ereignisbehandlung für Lade-Meldungen (rund 110 Zeilen) wurde nach `useConnectServerWithFeedback` ausgelagert. | `ConnectOpcUa` |
| `frontend/src/features/opcua-server/components/ServerManager.tsx` (M) | aus dev, ergänzt um: verbindet Pi 1 und Pi 2 beim ersten Mount, blendet beide aus der Serverliste aus, trennt Roboter-Server über `disconnectServerCleanly`, Panel füllt die Höhe. | `ServerManager`, `PI_URLS`, `hasAutoConnectedRef` |
| `frontend/src/features/opcua-server/components/PiStatusList.tsx` (A) | Verbindungsstatus eines Pi (grün oder rot) und seine Belegung. | `PiStatus`, `PiStatusList`, `isPiConnected` |
| `frontend/src/features/opcua-server/components/PiActivity.tsx` (A) | Belegungstext je Pi und der Knopf „Job abbrechen“ für beide Pis. | `PiActivityLine`, `ResetPisButton`, `stateText` |
| `frontend/src/features/opcua-server/components/MessageLog.tsx` (M) | aus dev, ergänzt um: Entfernen der Protokollzeilen für Nachrichten, die es nicht mehr gibt (Not-Halt-Abo, `readAddressSpaceSubtree`, `robotEmergencyStopChanged`). | `MessageLog` |
| `frontend/src/features/opcua-server/components/index.ts` (M) | aus dev, ergänzt um die Exporte `CameraStreamPanel` und `CellWatchPanel`. | Barrel-Datei |

**Ablauf.**
1. **Pis:** Beim ersten Mount von `ServerManager` ruft die App `connectServer` für Pi 1 und Pi 2 auf (`hasAutoConnectedRef`). Das Backend verbindet (`AsyncUaServerConnection.connect`, Zeitlimit 120 s), liest das Namespace-Array (`_read_namespace_uris`), markiert den Server als verbunden und startet die Überwachung. Für Pis läuft **keine** Roboter-Discovery (`is_robotics_server=False`).
2. **Roboter-Server:** Das „+“ ruft `useConnectServerWithFeedback` auf: erst `connectServer`, dann `discoverRobots`. Die Discovery (Zeitlimit 600 s) liest Motion Devices, Achsen, Methoden und Skills (`discover_connected_server` in `backend/src/backend/opcua/asyncua_discovery.py`).
3. **Knoten-IDs der Pis** stehen nicht fest im Code. `resolveVisionBinding` liest sie aus dem Namespace-Array: Die URI `http://launch-rm.de/vision` liefert den Index, und daraus entstehen die Knoten `ns=<Index>;s=VisionMachine.<Methode>`. Fehlt das Array oder die URI, ist das Ergebnis `kind: "unknown"`, und die Aufrufer starten nichts. Der Index des MachineVision-Namespace (für die Ereignistypen) fällt nur dann auf 2 zurück, wenn das Array vorhanden ist, die URI darin aber fehlt. Der Kommentar begründet das damit, dass sich der Index schon zweimal verschoben hat.
4. **Überwachung (Liveness):** `_watch_server_liveness` prüft alle 5 s (`LIVENESS_POLL_INTERVAL_S`) mit `check_alive`. Dazu liest es den Standardknoten `i=2258` (Serverzeit) mit 30 s Zeitlimit. Eine Verbindung gilt auch dann als tot, wenn ein Keepalive-Task von asyncua beendet ist (`_keepalive_running`). Bei Ausfall: Server als getrennt markiert, `serverDisconnected` an alle verbundenen Seiten, dann Wiederverbindung im 5-s-Takt. Nach Erfolg: `serverConnected`, Wiederherstellung der Abos (`restore_subscriptions`) und der Gelenk-Abos (`_resubscribe_robot_joints`).
5. **Trennen:** Bei Roboter-Servern gibt `disconnectServerCleanly` zuerst Sync und Take Control jedes Roboters zurück und ruft danach `disconnectServer` auf. Scheitert die Rückgabe, wird nur gewarnt, und das Trennen geht weiter. Pis lassen sich nicht manuell trennen, weil sie aus der Liste ausgeblendet sind.
6. **Belegung:** Läuft auf einem Pi ein Job oder eine Kalibrierung, meldet das Fenster das über `usePiActivity`. Andere Fenster sehen es über `usePiBlocker` und deaktivieren ihren Knopf, statt auf die BUSY-Antwort des Pi zu warten. `ResetPisButton` beendet auf beiden Pis laufende Jobs, Kalibrierungen und die Zellen-Überwachung und setzt hängende Zustände zurück.

**Konfiguration.**
- `PI_1_SERVER_URL = "opc.tcp://10.10.38.104:4840/raspi/server/"` und `PI_2_SERVER_URL = "opc.tcp://10.10.38.109:4840/raspi/server/"` in `piServers.ts` sind hartcodiert (Altlast B1 in `altlasten.md`). `LAYER_LABELS` legt die Bezeichnungen „First Layer“ und „Second Layer“ fest (Altlast B3).
- `KNOWN_ROBOT_SERVER_URLS` (`robotServers.ts`) sind nur Vorschläge für das „+“-Feld.
- Backend-Zeitlimits: `CONNECT_TIMEOUT_S = 120.0`, `DISCOVER_TIMEOUT_S = 600.0`, `LIVENESS_POLL_INTERVAL_S = 5.0` (`backend/src/backend/runtime/application_service.py`). `CHECK_ALIVE_TIMEOUT_S = 30.0`, `SUBSCRIBE_TIMEOUT_S = 60.0`, `REQUEST_TIMEOUT_S = 120.0` (Klasse `AsyncUaServerConnection` in `backend/src/backend/opcua/server_connection.py`).
- Keine Umgebungsvariablen in diesem Abschnitt.

**Tests.** Frontend: `piActivity.test.ts` (4), `piMethodCall.test.ts` (3). Ohne Test im Diff sind `ServerManager`, `ConnectOpcUa`, `PiActivityContext` und `useConnectServerWithFeedback`. Backend: `backend/tests/test_server_connection.py` (8) prüft Wiederverbindung, Abo-Wiederherstellung und die Keepalive-Erkennung. `backend/tests/test_application_service.py` (27) enthält u. a. `test_liveness_watch_resubscribes_robot_joints_after_reconnect`.

**Weiterlesen.**
- [vision-system-integration.md](vision-system-integration.md): Teile 1.3 (Robot-Discovery), 1.7 (Frontend) und 8 (Risiko R9, „kein Reconnect/Watchdog“). Die Fachdoku führt R9 als offen. Im Code gibt es dafür jetzt `_watch_server_liveness` und `restore_subscriptions`.
- [vision-server-interface.md](vision-server-interface.md): §2 (Verbindungsdaten) und §7 (Stolpersteine).
- [altlasten.md](altlasten.md): Abschnitt B (B1 und B3).

---

### Kamera-Livestream

**Zweck.** Live-Bild der beiden Pi-Kameras in einem eigenen Fenster, mit wählbarem Overlay (Rohbild, AprilTags, Kalibrierboard, entzerrt, Tag einmessen), Vergrößerung, Speichern des Rohbilds und Anzeige des Kamerazustands. Der Strom kommt direkt vom Pi und nicht über das Backend. Laut Kommentar in `CameraStreamPanel.tsx` ist das ein reines Debugwerkzeug.

**Einstieg.**
- Kopfleiste der Ansicht (`ViewportHeaderControls`, Kamera-Abschnitt): Der Schalter je Kamera setzt `cameraStreams[serverUrl].enabled` über `updateCameraStream`.
- `DesktopLayout` zeigt für jede eingeschaltete Kamera (`LIVESTREAM_CAMERAS`) ein `CameraStreamPanel` als schwebendes Fenster.
- Der Vergrößern-Knopf (⤢) der `CameraPreview` öffnet `CameraZoomModal`.

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/src/features/opcua-server/components/CameraStreamPanel.tsx` (A) | Schwebendes Fenster je Kamera mit Gesundheitsabzeichen. Schließen schaltet den Stream aus. | `CameraStreamPanel` (nutzt `FloatingWindow`, `CameraView`, `useCameraHealth`) |
| `frontend/src/features/opcua-server/components/CameraView.tsx` (A) | Bild, Auswahl des Overlay-Modus und Rohbild-Knopf. Der Modus wird auf den Pi geschrieben. | `CameraView`, `CameraHealthBadge`, `cameraStreamModeNodeId` |
| `frontend/src/features/opcua-server/components/CameraLiveImage.tsx` (A) | Bildquelle: MJPEG-`<img>`, bei Fehler Base64-Bilder aus dem OPC-UA-Knoten. | `CameraLiveImage`, `MJPEG_RETRY_MS` |
| `frontend/src/features/opcua-server/components/CameraPreview.tsx` (A) | Füllt den verfügbaren Platz und enthält den Vergrößern-Knopf. | `CameraPreview` |
| `frontend/src/features/opcua-server/components/CameraZoomModal.tsx` (A) | Vollauflösung mit Zoom und Verschieben, in einem `FloatingWindow`. | `CameraZoomModal`, `CameraLiveImage` |
| `frontend/src/features/opcua-server/components/SaveRawImageButton.tsx` (A) | Knopf neben der Overlay-Auswahl: der Pi speichert das aktuelle Vollbild als PNG. | `SaveRawImageButton`, `useSaveRawImage` |
| `frontend/src/features/opcua-server/model/useMjpegStreamUrl.ts` (A) | MJPEG-URL aus dem Port-Knoten `CameraStreamHttpPort`. Ohne Port keine URL. | `useMjpegStreamUrl` |
| `frontend/src/features/opcua-server/model/useMjpegWatchdog.ts` (A) | Erkennt einen stehenden Stream und baut ihn neu auf, wenn der Pi noch Bilder liefert. | `useMjpegWatchdog`, `fingerprint`, `snapshotHash`, `CHECK_MS`, `STALL_MS`, `RECONNECT_GAP_MS`, `PATCH_PX` |
| `frontend/src/features/opcua-server/model/useCameraFrame.ts` (A) | Base64-Bilder aus dem OPC-UA-Knoten als Ausweichweg. | `useCameraFrame` |
| `frontend/src/features/opcua-server/model/useCameraHealth.ts` (A) | Abonniert den Gesundheitsknoten `DeviceHealth`. | `useCameraHealth`, `CameraHealthState` |
| `frontend/src/features/opcua-server/model/useSaveRawImage.ts` (A) | Ruft die Methode `SaveRawImage` auf dem Pi auf und übersetzt Fehlercodes. | `useSaveRawImage`, `parseSaveRawImageOutput`, `saveRawImageErrorText` |

**Ablauf.**
1. Der Pi liefert MJPEG über HTTP, laut Kommentar mit etwa 15 Bildern pro Sekunde. Die Port-Nummer steht im OPC-UA-Knoten `CameraStreamHttpPort`. Sie bleibt 0, solange der HTTP-Server nicht lauscht, und der Knoten fehlt auf älteren Pi-Builds. Der Host kommt aus der OPC-UA-Adresse des Pi, der Pfad ist `/stream.mjpg`.
2. `CameraLiveImage` lädt die MJPEG-URL in ein `<img>` mit `crossOrigin="anonymous"` (der Pi sendet `Access-Control-Allow-Origin: *`). Schlägt das Laden fehl, zeigt das Bild die Base64-Bilder aus `cameraFrameNodeId` (laut Kommentar etwa 5 Bilder pro Sekunde). Nach `MJPEG_RETRY_MS` (10 s) wird die MJPEG-URL erneut versucht.
3. Der Watchdog prüft jede Sekunde einen 64-Pixel-Ausschnitt aus der Bildmitte (Hash). Steht das Bild 5 s lang still, fragt er `/snapshot.jpg` ab. Ändert sich das Standbild, liefert der Pi noch, und nur die Verbindung hängt. Dann wird das `<img>` über einen neuen `key` neu geladen, höchstens alle 5 s. Bleibt auch das Standbild gleich, steht die Kamera selbst, und eine neue Verbindung hilft nicht. Verweigert der Browser das Auslesen der Pixel (fehlender CORS-Header), gibt der Watchdog auf. Bei einem verborgenen Tab wird nichts beurteilt.
4. Overlay: Die Auswahl wird als Wert auf den Pi geschrieben (`writeNodeValue` auf `cameraStreamModeNodeId`). Der Pi zeichnet das Overlay. Hält eine Kalibrierung den Modus, wird er nicht überschrieben (`calibrationHoldsMode`).
5. Gesundheit: Der Wert von `DeviceHealth` (0 bis 4, OPC 40100-2) wird mit Text und Farbton angezeigt (`DEVICE_HEALTH`). Der Wert 4 („Wartung nötig“) kommt laut Kommentar serverseitig nie vor.
6. Speichern: `SaveRawImage` legt das Vollbild auf dem Pi unter `data/rohbilder` ab. Die Antwort besteht aus `[FileName, Error]`.

**Konfiguration.**
- `CAMERA_OVERLAY_MODES` in `piServers.ts`: `off`, `apriltag`, `calibration`, `undistorted`, `tagreference`. Die Liste muss zu `tagloc.modes.OVERLAY_MODES` im Zellen-Repo passen. Ein unbekannter Wert hält den Stream nicht an, der Pi fällt dann auf `apriltag` zurück.
- `LIVESTREAM_CAMERAS` (`frontend/src/features/viewport/model/sceneState.ts`): „Kamera 1“ (Pi 1) und „Kamera 2“ (Pi 2). `DEFAULT_CAMERA_STREAM`: `enabled: false`, `overlayMode: "apriltag"`.
- Konstanten: `MJPEG_RETRY_MS = 10_000`. Im Watchdog `CHECK_MS = 1000`, `STALL_MS = 5000`, `RECONNECT_GAP_MS = 5000`, `PATCH_PX = 64`.
- URLs: `http://<Host>:<Port>/stream.mjpg`, `…/snapshot.jpg` und `…/tagreference.jpg` (`buildTagReferenceImageUrl`).
- Keine Umgebungsvariablen.

**Tests.** `useSaveRawImage.test.ts` (6). Für Watchdog, MJPEG-URL-Bau und Overlay-Logik gibt es im Diff keine Tests.

**Weiterlesen.**
- [vision-server-interface.md](vision-server-interface.md): §10 (Kamera-Livestream: Overlay, MJPEG über HTTP, zwei Bildströme).
- [arbeitsplaene/livestream-mjpeg-und-watchdog.md](arbeitsplaene/livestream-mjpeg-und-watchdog.md), [arbeitsplaene/stream-overlay-schlank.md](arbeitsplaene/stream-overlay-schlank.md), [arbeitsplaene/rohbild-speichern.md](arbeitsplaene/rohbild-speichern.md), [arbeitsplaene/livestream-tags-volle-aufloesung.md](arbeitsplaene/livestream-tags-volle-aufloesung.md).
- [arbeitsplaene/opcua-kamera-condition-monitoring.md](arbeitsplaene/opcua-kamera-condition-monitoring.md): Zustand nach OPC 40100-2 (`DeviceHealth`).

---

### Vision-Jobs, Kamerakalibrierung und Zellen-Überwachung

**Zweck.** Die Pis über OPC-UA-Methoden steuern: Einzel- und Dauerjobs (Module lokalisieren), Kamerakalibrierung aus dem Browser, Zellen-Überwachung (Cell Watch) und Hand-Auge-Kalibrierung. Die Fenster, die diese Hooks benutzen, liegen größtenteils in `features/cell-modules` (eigener Teil). Hier stehen die Verbindungsstücke.

**Einstieg.**
- Zellen-Schalter `AutolocateActions` im `CellWatchPanel` (rechte Spalte, oben): schaltet die Zellen-Überwachung ein und aus, startet den Scan auf Layer 1 und reicht Ereignisse an die Fenster der cell-modules weiter.
- Kopfleiste (`ViewportHeaderControls`): Kalibrier-Dialog je Kamera (`calibratingUrl`), Anzeige „zuletzt kalibriert“ (`LastCalibrationBadge`) und das Hand-Auge-Fenster (`handEyeOpen`).

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/src/features/opcua-server/model/useVisionJob.ts` (A) | Einzel-Job: `StartSingleJob` starten, das Ergebnis aus den Ereignissen holen, stoppen. | `useVisionJob`, `VisionJobState`, `VisionJobOptions`, `VisionJobStatus` |
| `frontend/src/features/opcua-server/model/useContinuousVisionJob.ts` (A) | Dauerbetrieb über `StartContinuous`. Jeder Zyklus liefert ein Ergebnis. Endet der Lauf, startet der Hook neu, solange er aktiv ist. | `useContinuousVisionJob`, `RESTART_DELAY_MS`, `RETRY_DELAY_MS`, `STALL_TIMEOUT_MS` |
| `frontend/src/features/opcua-server/model/visionResult.ts` (A) | Parser für das JSON-Ergebnis eines Jobs: Erkennungen, Welt-Tags, Kameraposition. | `parseVisionResult`, `VisionDetection`, `VisionWorldTag`, `VisionCameraLocalization` |
| `frontend/src/features/opcua-server/model/cameraCalibration.ts` (A) | Typen und reine Logik der Kalibrierung: Fortschritt lesen, Phase ableiten, Schwellen. | `CalibrationProgress`, `deriveCalibrationPhase`, `parseCalibrationProgress`, `parseActiveCalibrationInfo`, `RMS_WARNING_THRESHOLD_PX`, `MIN_FINISH_SAMPLES` |
| `frontend/src/features/opcua-server/model/useCameraCalibration.ts` (A) | Kalibriersitzung am Pi mit den Befehlen `start`, `capture`, `finish`, `abort`. Liest den Fortschritt. | `useCameraCalibration`, `CalibrationCommand`, `COMMAND_TIMEOUT_MS` |
| `frontend/src/features/opcua-server/model/useHandEyeCalibration.ts` (A) | Hand-Auge-Kalibrierung am Hand-Pi (gleiche Befehle, Status `HandEyeStatus`). Der Hook löst nichts selbst. | `useHandEyeCalibration`, `HandEyeCommand` |
| `frontend/src/features/opcua-server/model/useCellWatch.ts` (A) | Zellen-Überwachung: Status abonnieren, starten, stoppen, pausieren, fortsetzen. | `useCellWatch`, `CellWatchState` |
| `frontend/src/features/opcua-server/model/usePauseWhileRobotMoves.ts` (A) | Pausiert die Zellen-Überwachung, solange der Roboter fährt. | `usePauseWhileRobotMoves` |
| `frontend/src/features/opcua-server/components/CameraCalibrationModal.tsx` (A) | Kalibrier-Dialog für eine Kamera: Aufnahmen, Auswertung, Ergebnis (Reprojektionsfehler, Linsenmodell, Abdeckung). | `CameraCalibrationModal` |
| `frontend/src/features/opcua-server/components/LastCalibrationBadge.tsx` (A) | „Zuletzt kalibriert“-Anzeige je Pi, unabhängig vom Dialog. | `LastCalibrationBadge` |
| `frontend/src/features/opcua-server/components/AutolocateActions.tsx` (A) | Zellen-Schalter mit Überwachung, Scan, Anzeige und Roboterfahrt zu den Tags. Rund 740 Zeilen, der Großteil ist cell-modules-Logik. | `AutolocateActions`, `describeWatch`, `MAX_REARMS`, `REARM_GAP_MS` |
| `frontend/src/features/opcua-server/components/CellWatchPanel.tsx` (A) | Panel oben in der rechten Spalte. Reicht den Verbindungsstatus der beiden Pis an `AutolocateActions` weiter. | `CellWatchPanel` |

**Ablauf.**
1. **Einzel-Job:** `useVisionJob.start(recipeId, parameters?)` ruft `StartSingleJob` mit einer fünfteiligen Argumentliste auf: `["", "", recipeId, "", parameters]`. Die Rezept-ID steht an dritter Stelle. Der Pi antwortet mit einem `Error`-Code (Tabelle `START_SINGLE_JOB_ERRORS`, z. B. 3 = BUSY). Das Ergebnis kommt als Ereignis `ResultReady`. Der Hook sucht es in der Ereignisliste des Servers (`opcuaEvents`), parst es mit `parseVisionResult` und setzt den Status auf `succeeded` oder `failed`.
2. Abonniert werden die Ereignisse mit `subscribeEvent(serverUrl, visionNodeId, eventTypeNodeIds)`. Die Liste der Ereignistypen gehört dazu, sonst liefert der Pi laut Kommentar keine brauchbare Nutzlast (Backend: `event_type_node_ids`, siehe unten).
3. **Stopp:** `AutomaticModeStateMachine.Stop` mit `[0, ""]` (Ursache, Beschreibung). Antwortet der Pi mit `BadNothingToDo`, weil nichts läuft, wird das still geschluckt.
4. **Dauerbetrieb:** `StartContinuous` mit denselben Argumenten. Jeder Zyklus ist ein eigenes Ergebnis mit der Kennung `<jobId>-0001`, `-0002` und so weiter. Endet der Lauf von selbst (ein Zyklus schlägt fehl) oder von außen, wechselt der Status auf `restarting`, und der Lauf startet neu, solange der Hook aktiv ist. Ohne Ergebnis über `STALL_TIMEOUT_MS` (60 s) gilt der Lauf als hängend.
5. **Kamerakalibrierung:** Vier Befehle (`start`, `capture`, `finish`, `abort`). `capture` speichert nur das aktuelle Bild und ist schnell, damit das Livebild nicht stockt. Die eigentliche Auswertung startet `finish` im Hintergrund am Pi. Das Ergebnis erscheint erst im Fortschritt (`CalibrationProgress.result`). Die Phase leitet `deriveCalibrationPhase` aus den Daten ab. Der Kommentar in `cameraCalibration.ts` warnt, dass `processing: true` nicht sicher ankommt.
6. **Zeitlimits:** `COMMAND_TIMEOUT_MS` (330 s) liegt über dem Backend-Zeitlimit für Pi-Methoden (120 s). So gibt das Frontend nicht vor der spezifischen Antwort des Servers auf.
7. **Zellen-Überwachung:** `useCellWatch` abonniert `CellWatchStatus` und ruft `StartCellWatch(OptionsJson)`, `StopCellWatch`, `PauseCellWatch(ReasonText, MaxPauseS)` oder `ResumeCellWatch` über die Knoten des Bindings auf. `usePauseWhileRobotMoves` pausiert, während der Roboter fährt.
8. Methodenaufrufe ohne eigenen Hook (etwa der Reset beider Pis) laufen über `callPiMethod`. Der Aufruf wird leise verfolgt. Kommt 15 s lang keine Antwort, meldet er „keine Antwort vom Pi“.

**Konfiguration.**
- `VISION_RECIPES` in `piServers.ts`: `apriltag` („Module lokalisieren“) und `calibration` („Messbereitschaft prüfen“). Die Liste muss zu `DEFAULT_RECIPE_PROFILES` im Zellen-Repo passen. Unbekannte Rezepte lehnt der Pi mit Fehler 4 ab.
- Ereignistyp-Knoten im MachineVision-Namespace: `ResultReady` (1024), `JobStarted` (1013), `AcquisitionDone` (1025), `Ready` (1023), `StateChanged` (1018), jeweils als `RESULT_READY_EVENT_TYPE_IDENTIFIER` und Verwandte in `piServers.ts`.
- Zeitlimits und Intervalle: `COMMAND_TIMEOUT_MS = 330000` (`useCameraCalibration.ts`), `TIMEOUT_MS = 15_000` und `POLL_MS = 100` (`piMethodCall.ts`), `RESTART_DELAY_MS = 2000`, `RETRY_DELAY_MS = 5000`, `STALL_TIMEOUT_MS = 60000` (`useContinuousVisionJob.ts`), `MAX_REARMS = 3` und `REARM_GAP_MS = 10_000` (`AutolocateActions.tsx`).
- `RMS_WARNING_THRESHOLD_PX = 0.5` und `MIN_FINISH_SAMPLES = 3` (`cameraCalibration.ts`).
- Umgebungsvariablen: keine.

**Tests.** `cameraCalibration.test.ts` (22), `visionResult.test.ts` (31), `piMethodCall.test.ts` (3), `piActivity.test.ts` (4), `useSaveRawImage.test.ts` (6). Die Hooks `useVisionJob`, `useContinuousVisionJob`, `useCameraCalibration`, `useCellWatch` und `useHandEyeCalibration` sowie die Komponenten haben im Diff keine eigenen Tests.

**Weiterlesen.**
- [vision-server-interface.md](vision-server-interface.md): §5 (`StartSingleJob`), §6 (Ergebnis-Payload), §7.1 (ohne Ereignistyp-Liste kommt keine Nutzlast) und §12 (interaktive Kalibrierung).
- [../frontend-integration-kalibrierung.md](../frontend-integration-kalibrierung.md): Kalibrier-Workflow (§3), Fallstricke (§4) und Linsenmodell (§7).
- [arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md](arbeitsplaene/kalibrierung-linsenmodell-je-kamera.md), [arbeitsplaene/zellen-ueberwachung.md](arbeitsplaene/zellen-ueberwachung.md), [arbeitsplaene/handeye-ueber-opcua.md](arbeitsplaene/handeye-ueber-opcua.md), [arbeitsplaene/layer2-annaeherung.md](arbeitsplaene/layer2-annaeherung.md).

---

### Tag-Tool (eigenständige Seite)

**Zweck.** Werkzeug, um einen AprilTag auf einem Modul-Modell (URDF) zu platzieren und die Lage des Tags zum Ursprung des Moduls zu exportieren. Ergebnis sind drei Dateien: eine JSON-Datei mit der Transformation Modul→Tag, ein URDF-Snippet mit festem Tag-Link und ein Eintrag für `tags` in `config/tagmap.json` des Pi (Schema `wsc.vision.tagmap/2`). Die Seite läuft getrennt von der Haupt-App, mit eigener HTML-Datei und eigenem Vite-Einstieg.

**Einstieg.**
- Entwicklung: `npm run dev` im Ordner `frontend/`, dann `http://localhost:1234/tag-tool.html`.
- Build: `vite build` erzeugt beide Seiten. `tag-tool.html` ist der zweite Einstieg (`build.rollupOptions.input.tagTool` in `frontend/vite.config.ts`).
- Die Seite hat vier Abschnitte (`TagToolApp.tsx`): **1 · Modul** (gebündelte URDFs oder ein Ordner von der Festplatte, Ursprungs-Link), **2 · Tag** (Familie `tag36h11`, ID, Kantenlänge), **3 · Platzierung** (Klick auf eine Fläche, Raster, Drehung, Abstand und Ausleger mit Versatz x/y), **4 · Bestätigen & Export** (Modulname, Rolle, Instanz-ID, Downloads).

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/tag-tool.html` (A) | HTML-Einstieg mit dem Titel „Tag-Platzierung“. Lädt `frontend/src/tag-tool/main.tsx`. | Vite-Eintrag |
| `frontend/src/tag-tool/main.tsx` (A) | Mount der Seite im `StrictMode`; lädt `../index.css`. | `TagToolApp` |
| `frontend/src/tag-tool/TagToolApp.tsx` (A) | Die Seite: Zustand (Modul, Tag, Platzierung, Gizmo-Modus), die vier Abschnitte und das Export-Panel. | `TagToolApp`, `Placement`, `ModuleChoice`, `handleFolderPicked`, `setPoseInOrigin`, `nudge`, `spin` |
| `frontend/src/tag-tool/loadModule.ts` (A) | URDF laden: gebündelt oder aus einem gewählten Ordner. | `BUNDLED_MODULES`, `loadBundledModule`, `pickedFolder`, `loadPickedModule` |
| `frontend/src/tag-tool/components/ModuleScene.tsx` (A) | three.js-Szene. Ein Klick auf das Mesh liefert Punkt und Flächennormale. | `ModuleScene`, `SceneTag`, `GizmoSettings` |
| `frontend/src/tag-tool/components/sceneParts.tsx` (A) | Szenenteile: Achsen, Ursprungs-Frame, Tag-Textur, Tag-Modell, Halterung. | `TagModel`, `Bracket`, `tagTexture`, `AxesArrows`, `OriginFrame` |
| `frontend/src/tag-tool/components/ExportPanel.tsx` (A) | Modulname, Instanz-ID, Rolle und Export (Download oder Kopieren in die Zwischenablage). | `ExportPanel`, `downloadText`, `WORLD_ANCHOR_TAG_ID` |
| `frontend/src/tag-tool/components/NumberField.tsx` (A) | Zahlenfeld, das die Eingabe hält und erst bei Enter oder beim Verlassen übernimmt. | `NumberField` |
| `frontend/src/tag-tool/components/Section.tsx` (A) | Abschnittsrahmen, Hinweisbox und Auswahlschalter. | `Section`, `Hint`, `Segmented` |
| `frontend/src/tag-tool/model/exportTagPose.ts` (A) | Die drei Exportformate: JSON, URDF-Snippet, Tag-Map-Eintrag in Pi-Konvention. | `buildTagExport`, `buildTagMapEntry`, `buildUrdfSnippet`, `formatExportJson`, `TagMapRole` |
| `frontend/src/tag-tool/model/placement.ts` (A) | Tag auf eine Fläche legen: Oben-Richtung, Drehung um die Normale, Halterung (Abstand, Versatz). | `tagPoseOnSurface`, `defaultImageUp`, `spinAboutFaceNormal`, `bracketOffsets`, `withBracketOffsets`, `SurfaceAnchor` |
| `frontend/src/tag-tool/model/pose.ts` (A) | Starre Transformationen aus Position und Quaternion, Matrizen, RPY-Winkel. | `Pose`, `composePoses`, `invertPose`, `quaternionFromRpy`, `rpyFromQuaternion`, `frameAxes` |
| `frontend/src/tag-tool/model/tag36h11.ts` (A) | Codes der Familie tag36h11 und das 10×10-Raster eines Tags. | `TAG36H11_CODES`, `TAG36H11_MAX_ID`, `tag36h11Cells` |
| `frontend/src/tag-tool/model/tagFrame.ts` (A) | Tag-Koordinatensystem (AprilTag-Konvention und OpenCV des Pi), Kantenmaße. | `DEFAULT_TAG_SIZE_M`, `printedTagSizeM`, `toPiTagFrame`, `APRILTAG_TO_PI_TAG_FRAME` |
| `frontend/src/tag-tool/model/units.ts` (A) | Umrechnung m/mm und rad/deg. `snapToStep` hat keinen Aufrufer. | `mToMm`, `mmToM`, `radToDeg`, `degToRad`, `snapToStep` |
| `frontend/src/tag-tool/model/uploadedFiles.ts` (A) | Auflösung von Pfaden aus einem gewählten Ordner über `upload://` und `upload-package://`. | `UPLOAD_SCHEME`, `UPLOAD_PACKAGE_SCHEME`, `resolveUploadedPath`, `normalizePath` |
| `frontend/src/tag-tool/model/urdfTree.ts` (A) | URDF-Baum: Ursprungs-Link, bewegliche Gelenke zwischen Links, Link-Pose. | `defaultOriginLink`, `linkPoseInRoot`, `movableJointsBetween`, `owningLink` |

**Ablauf.**
1. **Modul laden:** `BUNDLED_MODULES` sind die eindeutigen URLs aus `ROBOT_MODEL_OPTIONS` (eva, fr3, fr3_wagon, ur5e). `demo1` hat dieselbe URL wie `eva` und wird deshalb nicht doppelt angeboten. Ein Ordner wird mit `webkitdirectory` gewählt. Seine Mesh-Pfade löst `uploadedFiles.ts` über die Schemata `upload://` und `upload-package://` auf. Den Ursprungs-Link wählt `defaultOriginLink`: „base“, wenn der Link existiert.
2. **Tag wählen:** Familie `tag36h11`, ID und Kantenlänge (Standard 0,08 m). Die Kantenlänge misst die schwarze Randfläche. Der gedruckte Tag mit weißer Ruhezone ist 10/8 davon (`printedTagSizeM`).
3. **Klick auf eine Fläche:** Der Treffer liefert Punkt und Normale in der URDF-Wurzel. `tagPoseOnSurface` legt den Tag flach auf die Fläche, die Druckseite nach außen. `defaultImageUp` nimmt die +z-Achse des Moduls als „oben“, sodass Tags auf Seitenflächen aufrecht stehen. Verschoben wird im Raster (Startwert 5 mm), gedreht um die Flächennormale (Startwert 15°).
4. **Halterung (optional):** `bracketOffsets` zerlegt den Mittelpunkt des Tags gegenüber dem Anker in den Abstand entlang der Normale und den Versatz x/y in der Fläche.
5. **Export:** `buildTagExport` erzeugt die JSON-Datei mit `moduleToTag` (`T_module_tag`: Matrix, xyz, rpy). `buildUrdfSnippet` erzeugt einen festen Tag-Link unter dem Ursprungs-Link in der AprilTag-Konvention. `buildTagMapEntry` erzeugt den Eintrag mit `tagToModule` in der Pi-Konvention (invertiert, OpenCV-Achsen). Sein `poseInWorld` ist `null`, weil die Kamera bewegliche Tags misst und die Map sie nicht vorgibt. Dateinamen: `tag_<id>_<modul>.json`, `.urdf-snippet.xml` und `.tagmap-entry.json`.
6. **Doppelung:** Die Platzierung im Modulkatalog (`frontend/src/features/cell-modules/model/tagPlacement.ts` und `tagGeometry.ts`) setzt dieselbe Aufgabe noch einmal um. Siehe [altlasten-toter-code.md](altlasten-toter-code.md), Abschnitt 3.2 (P5).

**Konfiguration.**
- `DEFAULT_TAG_SIZE_M = 0.08` (`tagFrame.ts`). Schrittweiten als Startwerte in `TagToolApp.tsx`: 5 mm und 15°.
- `WORLD_ANCHOR_TAG_ID = 0` (`ExportPanel.tsx`). Entspricht `anchorTagId` in `config/tagmap.json` (im ADP-Repo).
- `TAG36H11_FAMILY = "tag36h11"` (`tag36h11.ts`).
- Einheiten: intern Meter und Radiant (wie URDF), angezeigt in mm und Grad. Umgerechnet wird nur an der Oberfläche (`units.ts`).
- Keine Umgebungsvariablen.

**Tests.** `exportTagPose.test.ts` (5), `placement.test.ts` (6), `pose.test.ts` (3), `tag36h11.test.ts` (3), `uploadedFiles.test.ts` (5), `urdfTree.test.ts` (4). Ohne Test im Diff sind `loadModule.ts`, `TagToolApp.tsx`, `ModuleScene.tsx`, `ExportPanel.tsx` und `units.ts`.

**Weiterlesen.**
- [arbeitsplaene/modell-flaechen-tag-platzierung.md](arbeitsplaene/modell-flaechen-tag-platzierung.md): Tags auf Flächen und Kanten des Modulmodells.
- [apriltag-referenz.md](apriltag-referenz.md): Konventionen (§1), Posen (§3), Tag-Map (§5) und Datenformat im Modulkatalog (§14).
- [arbeitsplaene/apriltag-tagmap-ueber-opcua.md](arbeitsplaene/apriltag-tagmap-ueber-opcua.md): Tag-Map über OPC UA und Schema `wsc.vision.tagmap/2`.

---

### Gemeinsame UI-Bausteine

**Zweck.** Oberflächenteile, die mehrere Fenster teilen: schwebende Fenster, ein Sekundenfeld, die Schritt-Schaltfläche und die Fenstergeometrie als reine Funktionen. Dazu kommen die beiden Dateien des WebSocket-Clients, in denen wir die neuen Befehle nachgezogen haben.

**Einstieg.**
- `FloatingWindow`: `CameraStreamPanel`, `CameraZoomModal`, `CameraCalibrationModal` und mehrere Fenster aus `features/cell-modules`.
- `StepButton`: `AutolocateActions.tsx` und cell-modules-Komponenten. `SecondsField`: cell-modules (z. B. `WatchTimingFields.tsx`).
- `WscWebSocketClient` (`websocketClient.ts`): benutzt von `applicationController.ts`.

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/src/shared/ui/FloatingWindow.tsx` (A) | Nicht-modales Fenster: an der Titelleiste verschiebbar, an der Ecke vergrößerbar, bei Berührung nach vorn. | `FloatingWindow`, `FloatingWindowProps` (`title`, `defaultSize`, `minSize`, `placement`, `headerExtra`, `onClose`) |
| `frontend/src/shared/ui/floatingWindowGeometry.ts` (A) | Reine Geometrie: Startposition, Begrenzung auf den Bildschirm, Kaskade und Kacheln. | `initialWindowRect`, `clampMove`, `clampResize`, `MIN_VISIBLE_PX`, `TITLE_BAR_PX`, `EDGE_MARGIN_PX`, `CASCADE_STEP_PX`, `TILE_GAP_PX` |
| `frontend/src/shared/ui/SecondsField.tsx` (A) | Eingabe von Sekunden. Übernahme bei Enter oder Verlassen, leer heißt Standardwert. | `SecondsField` |
| `frontend/src/shared/ui/StepButton.tsx` (A) | Hauptaktion eines Schritts: Nummer, Bezeichnung, optionale Zeile, Pfeil. Mit `highlight` pulsiert der Knopf. | `StepButton`, CSS-Klasse `step-attention` |
| `frontend/src/shared/api/messages.ts` (M) | aus dev, ergänzt um `eventTypeNodeIds` bei `subscribeEvent`; entfernt: `subscribeRobotEmergencyStop`, `unsubscribeRobotEmergencyStop`, `robotEmergencyStopChanged`, `readAddressSpaceSubtree`. Die Typen spiegeln das Backend-Protokoll (`backend/src/backend/models/messages.py`). | Nachrichtentypen `ClientMessage` und `ServerMessage` |
| `frontend/src/shared/api/websocketClient.ts` (M) | aus dev, ergänzt um den optionalen Parameter `eventTypeNodeIds` bei `subscribeEvent`; entfernt: `subscribeRobotEmergencyStop`, `unsubscribeRobotEmergencyStop`, `readAddressSpaceSubtree`. | `WscWebSocketClient`, `subscribeEvent` |

**Ablauf.** Die Geometrie der schwebenden Fenster:
1. **Start:** `initialWindowRect` setzt Fenster in der Mitte kaskadiert (32 px Versatz). Fenster in den Ecken reihen sich nebeneinander und beginnen eine neue Reihe, wenn der Browser voll ist. Der Abstand zum Rand beträgt 24 px.
2. **Ziehen:** `clampMove` lässt die Titelleiste seitlich über den Rand hinaus, nie nach oben oder unten. Mindestens 64 px bleiben sichtbar.
3. **Größe ändern:** `clampResize` hält die Mindestgröße ein und begrenzt am Browserrand.
4. **Nach vorn:** Jede Berührung im Fenster hebt es über `topZIndex` nach vorn. Klicks auf Bedienelemente (`button, a, input, select, textarea`) starten kein Ziehen (`isControl`).
5. **Sekundenfeld:** Der Wert wird bei Enter oder beim Verlassen übernommen, nicht bei jedem Tastendruck, weil jede Übernahme etwas neu starten kann (etwa die Zellen-Überwachung). Nur positive Zahlen.

**Konfiguration.**
- Fenstergeometrie (`floatingWindowGeometry.ts`): `MIN_VISIBLE_PX = 64`, `TITLE_BAR_PX = 40`, `EDGE_MARGIN_PX = 24`, `CASCADE_STEP_PX = 32`, `TILE_GAP_PX = 16`.
- WebSocket-Adresse (`frontend/src/shared/api/websocketUrls.ts`, aus dev, nicht von uns geändert): Vorrang hat die Umgebungsvariable `VITE_WSC2_WS_URL`. Im Dev-Modus ist es `ws://127.0.0.1:8000/ws`, direkt am Backend. Sonst wird die Adresse aus der Seiten-URL gebildet (`ws(s)://<host>/ws`). REST-Aufrufe (`/api`) laufen im Dev-Modus über den Vite-Proxy (`frontend/vite.config.ts`).

**Tests.** `floatingWindowGeometry.test.ts` (7). Ohne Test im Diff sind `FloatingWindow.tsx`, `StepButton.tsx`, `SecondsField.tsx`, `messages.ts` und `websocketClient.ts`.

**Weiterlesen.** Keine Fachdoku dazu. Verwendung siehe die Abschnitte oben.

---

### Task-Editor (`features/task`)

**Zweck.** Grafischer Editor für Roboter-Tasks. Skill-Schritte, Variablen und Konstanten werden als Graph (React Flow) bearbeitet. Gespeichert, geladen und gestartet wird über die Task-Steuerung des Roboter-Servers (OPC UA). Das Dokumentmodell ist neu (`taskDocument.ts`), der Graph ist nur noch der Editor-Zustand. Außerhalb des Codes ist `docs/task-editor.md` (D) entfallen.

**Einstieg.**
- `TaskPanel` (aus dev, unverändert) im Desktop-Layout unter dem Viewport. `DesktopLayout.tsx` umschließt die Ansicht mit `TaskEditorProvider`.
- `TaskEditor` zeigt den Graphen, `TaskInsertionMenu` fügt Knoten hinzu, `TaskProgramPanel` verwaltet die Programmliste mit Speichern, Laden, Start und Stopp.

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/src/features/task/model/taskDocument.ts` (A) | Dokumentmodell (Name, Hash, Metadaten, Konstanten, Zeitachse aus Skill-Schritten) mit Parser und Serialisierung für den Server. Akzeptiert auch die alten Formate `waypoints` und `graph`. | `TaskDocument`, `TaskPayload`, `SkillTaskStep`, `TaskConstant`, `parseTaskDocument`, `serializeTaskDocumentForServer`, `createEmptyTaskDocument`, `LegacyTaskGraphDocument` |
| `frontend/src/features/task/model/editorGraph.ts` (M) | aus dev, ergänzt um: Graph-Modell mit den Knotenarten `root`, `skill`, `variable`, `constant`, Umwandlung in das Dokument und zurück sowie die Prüfung vor dem Speichern. Entfernt: die Typen für Methoden- und Steuerfluss-Knoten und `toProgramEditorLabel`. | `validateTaskEditorGraphForSave`, `exportTaskDocumentFromGraph`, `importTaskDocumentToGraph`, `createEmptyTaskEditorGraphState`, `getSkillParameterSpec`, `COLUMN_WIDTH`, `ROW_HEIGHT` |
| `frontend/src/features/task/context/TaskEditorContext.tsx` (M) | aus dev, umgebaut: Zustand des Editors als React-Context (Knoten, Kanten, Dokument). Die frühere Tab- und Arbeitsbereich-Verwaltung (`TaskEditorTab`, `TaskEditorWorkspace`) ist durch eine Sitzung ersetzt (`TaskEditorSessionState`). | `TaskEditorProvider`, `useTaskEditor` |
| `frontend/src/features/task/components/TaskEditor.tsx` (M) | aus dev, auf die Graph-Ansicht verkürzt (171 Zeilen entfernt, 14 ergänzt). | `TaskEditor` |
| `frontend/src/features/task/components/TaskProgramPanel.tsx` (M) | aus dev, stark erweitert: Programmliste, Speichern, Laden, Start und Stopp über OPC UA; Anzeige des Laufzustands. | `TaskProgramPanel`, `TaskSaveControls`, `resolveTaskRuntimeNodeIds`, `buildAddTaskArgs`, `TASK_NODE_NUMERIC_IDS` |
| `frontend/src/features/task/components/TaskInsertionMenu.tsx` (M) | aus dev, ergänzt um Startwerte für Skill-Eingaben. | `buildInitialSkillInputs`, `buildNode`, `makeNodeId` |
| `frontend/src/features/task/components/GenericEditorNode.tsx` (M) | aus dev, ergänzt um Griffe für Steuerfluss und die Normalisierung von JSON-Werten. Entfernt: die alten Knotentypen. | `ControlInHandle`, `ControlOutHandle`, `normalizeJsonValue` |
| `frontend/src/features/task/model/taskScene.ts` (M) | aus dev, angepasst: Wegpunkte aus dem Dokument für die Marker im Viewport. | `getTaskSceneWaypointMarkers`, `TaskSceneWaypointMarker` |
| `frontend/src/features/task/model/types.ts` (M) | aus dev, reduziert auf Re-Exports aus `taskDocument.ts`; der Export aus `valueTypes.ts` ist entfallen. | Re-Exports |
| `frontend/src/features/task/model/programGraphOpcua.ts` (D), `programValues.ts` (D), `valueTypes.ts` (D) | Gelöscht. Ihre Aufgaben übernimmt `taskDocument.ts`. Gelöscht sind außerdem ihre Tests `programGraphOpcua.test.ts` und `valueTypes.test.ts`. | – |

**Ablauf.**
1. **Speichern:** `validateTaskEditorGraphForSave` prüft den Graphen. `exportTaskDocumentFromGraph` erzeugt das Dokument: die Skill-Schritte werden zur Zeitachse `task.timeline`, die Konstanten nach `task.constants`. `buildAddTaskArgs` stellt die Argumente zusammen. Dann ruft `TaskProgramPanel` die Methode `addTask` des Servers mit Name und JSON auf.
2. **Vor dem Senden:** `serializeTaskDocumentForServer` entfernt die alte Graph-Form (`graph: undefined`) und liefert das reine JSON.
3. **Laden:** `LoadByName` auf der Zustandsmaschine der Task-Steuerung. Danach liest der Editor den Knoten `TaskJson` des Programms und übernimmt ihn mit `parseTaskDocument`.
4. **Start und Stopp:** Die Methoden `Start` und `Stop` auf `TaskControlStateMachine`. Der Laufzustand (`CurrentStepId`, `CurrentStepState`, `StepStatesJson`) wird aus dem Serverbaum gelesen und angezeigt.
5. **Löschen:** Die Methode `deleteTask`.

**Konfiguration.**
- `TASK_NODE_NUMERIC_IDS` in `TaskProgramPanel.tsx` legt numerische Knoten-IDs fest: `controller` 29, `taskProgramName` 44, `taskProgramLoaded` 45, `finalResultData` 5056. Der Namespace-Index kommt vom Server (`parseNamespaceIndex`, `numericNodeId`). Weitere Knoten werden über ihre Namen gesucht (`TASK_CONTROL_CHILD_NAMES`, `TASK_CONTROL_STATE_MACHINE_CHILD_NAMES`, `TASK_PROGRAM_CHILD_NAMES`, `FINAL_RESULT_CHILD_NAMES`).
- `COLUMN_WIDTH = 280` und `ROW_HEIGHT = 150` legen das Raster des Graph-Layouts fest (`editorGraph.ts`).
- Keine Umgebungsvariablen.

**Tests.** `taskDocument.test.ts` (2), `editorGraph.test.ts` (7), `taskScene.test.ts` (2), `TaskInsertionMenu.test.ts` (1). Ohne Test im Diff sind `TaskProgramPanel.tsx`, `TaskEditorContext.tsx`, `TaskEditor.tsx` und `GenericEditorNode.tsx`.

**Weiterlesen.** Keine eigene Fachdoku im ADP-Repo. [altlasten-toter-code.md](altlasten-toter-code.md), Abschnitt 1.2: `describeTaskEditorNode` und `createVariableNodeData` in `editorGraph.ts` haben keine Verwendung.

---

### Viewport und Robot-Control

**Zweck.** Der Viewport zeigt Roboter, Module, Tags, Geister-Roboter und Pfade. Seine Kopfleiste enthält die Schalter für Zellen-Überwachung, Kameras, Modulkatalog und Hand-Auge. Robot-Control hält den digitalen Zwilling im Gleichlauf mit dem echten Roboter, zeigt Gelenke und Aktionen und verwaltet die Steuerungshoheit (Take Control).

**Einstieg.**
- `Viewport` und `ViewportHeaderControls` in der Mitte des Desktop-Layouts (`DesktopLayout.tsx`).
- `JointAnglesPanel` (Gelenkwinkel, Sync), `RobotActionsPanel` (Aktionen) und `RobotManager` (aktiver Roboter).

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/src/features/viewport/components/Viewport.tsx` (M) | aus dev, ergänzt um Markierungen aus cell-modules (Module, Welt-Tags, Handkamera, Layer 2), den Geister-Roboter, den Tag-Verlauf der Zellen-Überwachung und die Wegpunkte aus dem Task-Editor. | `Viewport`, `useCellWatch`, `addSighting`, `recentlySeenTagIds` |
| `frontend/src/features/viewport/components/ViewportHeaderControls.tsx` (M) | aus dev, ergänzt um: Schalter „Zellüberwachung“, Kamera-Abschnitt mit `PiStatus`, `LastCalibrationBadge` und Kalibrier-Dialog, Modulkatalog, Hand-Auge-Fenster. | `ViewportHeaderControls`, `CameraCalibrationModal`, `ModuleCatalogModal`, `HandEyeCalibrationWindow` |
| `frontend/src/features/viewport/components/TaskWaypointMarkers.tsx` (M) | Kugelgröße der Wegpunkte im Ruhezustand von 0,01 auf 0,022. | `TaskWaypointMarkers` |
| `frontend/src/features/viewport/components/URDFLoader.d.ts` (M) | Typdeklaration für den URDF-Loader, ergänzt um `packages`, `workingPath` und `parse`. | `URDFLoader` |
| `frontend/src/features/viewport/model/sceneState.ts` (M) | aus dev, ergänzt um den Zustand der Szene: Livestream-Einstellungen je Pi, Roboter-Vorschau, Layer-2-Vorschlag und Zellen-Überwachung. | `useViewportSceneState`, `CameraStreamSettings`, `LIVESTREAM_CAMERAS`, `RobotPosePreview`, `Layer2Proposal` |
| `frontend/src/features/viewport/model/robotIk.ts` (M) | aus dev, ergänzt um: `findToolPoint` bleibt die Vorgabe; entfernt: `getToolPointBasePose`. `solvePositionIk` hat keinen Aufrufer. | `findToolPoint`, `solvePositionIk` |
| `frontend/src/features/viewport/model/robotIkModel.ts` (M) | aus dev, ergänzt um den Parameter `endEffectorLink` (Ziellink der IK) und den abgefangenen Solver-Fehler (`IK_SOLVE_STATUS_STALLED = 1`). | `createRobotIkModel` |
| `frontend/src/features/robot-control/components/JointAnglesPanel.tsx` (M) | aus dev, frühe Rückgaben zu einer Bedingung zusammengefasst. | `JointAnglesPanel` |
| `frontend/src/features/robot-control/components/RobotActionsPanel.tsx` (M) | aus dev, überarbeitet (136 Zeilen ergänzt, 137 entfernt): Zustandsanzeige je Aktion über `formatActionStateLabel`. | `RobotActionsPanel`, `formatActionStateLabel` |
| `frontend/src/features/robot-control/components/RobotManager.tsx` (M) | aus dev, die Anzeige „Emergency stop active“ entfernt. | `RobotManager` |
| `frontend/src/features/robot-control/model/robotJointRuntime.ts` (M) | aus dev, ergänzt um die Heilung der Sync-Sitzung (`healSync`) und die Erkennung laufender Manipulation (`isManipulating`). | `healSync`, `isManipulating`, `staleManipulationMs` |
| `frontend/src/features/robot-control/model/robotModels.ts` (M) | aus dev, ergänzt um die Felder `lookDownAngles` (Grundpose für Automatikläufe) und `flangeLink` (Flansch-Link), neue Modelle `demo1` und `fr3_wagon`. | `RobotModelConfig`, `ROBOT_MODEL_OPTIONS` |
| `frontend/src/features/robot-control/model/flangeKinematics.ts` (A) | Kinematik des Flansches nur aus den Gelenken (ohne Meshes), Vorwärts- und Rückwärtsrechnung mit Toleranzen. | `createFlangeKinematics`, `loadFlangeKinematics`, `flangeLinkOf`, `IK_TRANSLATION_TOLERANCE_M`, `IK_ROTATION_TOLERANCE_RAD` |

Gelöscht (D): `frontend/src/features/viewport/model/robotIkPose.test.ts`.

**Ablauf.**
1. **Gleichlauf:** Der Zwilling folgt den Gelenkwinkeln, die das Backend als `robotJointState` meldet. `robotJointRuntime` führt je Roboter eine Sync-Sitzung. Eigene Bewegungen (Ziehen, IK-Schritte, Animation) haben Vorrang (`isManipulating`). `healSync` repariert die Sitzung nach Zustandswechseln: Haben sich die Modellgelenke geändert, startet der Sync neu. Eine Manipulation, die länger als 15 s dauert, wird beendet. Eine angehaltene Sitzung wird fortgesetzt.
2. **Take Control:** Die App übernimmt die Steuerung über die Aktion `take_control` (Namen `take_control` oder `takeControl`) und hält sie mit Keepalive-Aufrufen. Je nach Angebot des Servers sind das `renew_session` oder `renew_lock`, alle 20 s. Jeder Aufruf hat 15 s Zeitlimit. Schlägt er fehl, folgen bis zu drei Versuche mit 2 s Pause. Die Aufrufe je Roboter laufen nacheinander. Die Freigabe ist `release_control`.
3. **Geschwindigkeitsgrenze:** `capGotoSpeed` begrenzt `max_speed` bei `go_to` auf höchstens 0,1 (`MAX_GOTO_SPEED`). Ist der Wert größer oder ungültig, etwa der Serverstandard −1, wird 0,1 gesendet. Fehlt der Schlüssel, bleibt der Aufruf unverändert.
4. **Trennen:** `disconnectServerCleanly` gibt bei jedem Roboter des Servers zuerst Sync und Take Control zurück und trennt dann (siehe Abschnitt zur OPC-UA-Verbindung).
5. **Greifer:** `findEndEffectorActions` und `findToggleEndEffectorKey` finden die Aktionen anhand des Namens. Verglichen wird ohne Groß- und Kleinschreibung und ohne Trennzeichen, sodass etwa `open_end_eff`, `closeEndEffector` und `toggle_end_eff` passen. Das Setzen eines Zustands (öffnen oder schließen) wird dem Umschalten vorgezogen. Benutzt von `applicationController.ts` und von cell-modules.
6. **Layer 2 und Hand-Auge:** `RobotPosePreview` zeigt die Gelenkwinkel des nächsten Ziels als Geister-Roboter. Die IK löst auf den Flansch-Link (`flangeLink`). Die Hand-Auge-Aufnahmen und die Vorwärts- und Rückwärtskinematik nutzen laut Kommentar in `robotModels.ts` denselben Link.

**Konfiguration.**
- `ROBOT_MODEL_OPTIONS` (`robotModels.ts`): `eva` (`flangeLink` `model_6`), `fr3` (`eef_attach_point`, `lookDownAngles` gesetzt), `fr3_wagon` (`eef_attach_point`, `lookDownAngles` gesetzt), `ur5e` (`flange`), `demo1` (`model_6`, gleiches Modell wie `eva` unter eigenem Namen für den Modulkatalog).
- Take Control und Gelenkwerte in `applicationController.ts`: `MAX_GOTO_SPEED = 0.1`; `TAKE_CONTROL_KEEPALIVE_INTERVAL_MS = 20_000` (vorher 60 000); `TAKE_CONTROL_CALL_TIMEOUT_MS = 15_000`; `TAKE_CONTROL_KEEPALIVE_ATTEMPTS = 3`; `TAKE_CONTROL_KEEPALIVE_ATTEMPT_TIMEOUT_MS = 15_000`; `TAKE_CONTROL_KEEPALIVE_RETRY_GAP_MS = 2_000`; `JOINT_REFRESH_MS = 2000`; `JOINT_REFRESH_TIMEOUT_MS = 5000`; `STANDING_AXIS_DELTA = 0.002`; `TWIN_BEHIND_RAD = 0.01`.
- `staleManipulationMs = 15000` (`robotJointRuntime.ts`); `IK_TRANSLATION_TOLERANCE_M = 0.002` (2 mm) und `IK_ROTATION_TOLERANCE_RAD` (1°) in `flangeKinematics.ts`.
- `CELL_WATCH_CLOSED = "44px"` und `CELL_WATCH_OPEN = "70%"` (`DesktopLayout.tsx`).
- Keine Umgebungsvariablen.

**Tests.** `robotIkModel.test.ts` (1). Ohne Test im Diff sind `Viewport.tsx`, `robotJointRuntime.ts`, `robotModels.ts`, `flangeKinematics.ts` und die Panels von Robot-Control.

**Weiterlesen.**
- [vision-system-integration.md](vision-system-integration.md): Teil 7 (Frontend: Status und CAD-Overlay).
- [arbeitsplaene/handeye-ueber-opcua.md](arbeitsplaene/handeye-ueber-opcua.md) und [arbeitsplaene/layer2-annaeherung.md](arbeitsplaene/layer2-annaeherung.md): Hand-Auge und Layer-2-Lauf.
- [altlasten-toter-code.md](altlasten-toter-code.md): Abschnitt 1.2 nennt `solvePositionIk` als toten Code.

---

### Entities und App-Schicht

**Zweck.** Zustandsspeicher für Roboter und Server (Reducer), Anwendungssteuerung über den `ApplicationController`, Wurzelkomponente und Meldungen zu Methodenaufrufen.

**Einstieg.**
- `frontend/src/App.tsx` → `SolverConfigProvider` → `MethodCallFeedbackBridge` → `DesktopLayout`.
- `ApplicationController` (Klasse in `frontend/src/app/model/applicationController.ts`) hält den WebSocket-Client und den Zustand. Die Oberfläche greift über `useOpcuaServer` darauf zu.

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/src/App.tsx` (M) | aus dev, ohne die Fehlergrenze. | `App` |
| `frontend/src/app/components/AppErrorBoundary.tsx` (D) | Gelöscht; die Fehlergrenze um die Anwendung entfällt. | – |
| `frontend/src/app/components/MethodCallFeedbackBridge.tsx` (M) | aus dev, angepasst: Meldungen für Methodenaufrufe. Aufrufe mit `quiet` werden übersprungen. Für Roboteraktionen außer den Fahr-Aktionen und dem Greifer lautet der Ladetext „Calling method...“. | `loadingText`, `successText`, `errorText`, `feedbackKey` |
| `frontend/src/app/layout/DesktopLayout.tsx` (M) | aus dev, ergänzt um: Panels mit `react-resizable-panels`, Zellen-Panel oben rechts (eingeklappt 44 px, offen 70 %), Kamera-Fenster, Task-Panel unter dem Viewport, `PiActivityProvider` und `TaskEditorProvider`. | `DesktopLayout`, `CELL_WATCH_CLOSED`, `CELL_WATCH_OPEN` |
| `frontend/src/app/model/applicationController.ts` (M) | aus dev, ergänzt um: Take Control mit Keepalive, sauberes Trennen (`disconnectServerCleanly`), Geschwindigkeitsgrenze für `go_to` (`capGotoSpeed`), Platzierung der Roboter je Server (`placeServerRobots`, `ServerRobotPlacement`), Zähler für Abonnements (`addHolder`, `removeHolder`), Greifer-Schalter. Entfernt: das Not-Halt-Abo, die `…AndWait`-Methoden. `isGotoStateReady` ist jetzt exportiert. | `ApplicationController`, `capGotoSpeed`, `MAX_GOTO_SPEED`, `setRobotTakeControl`, `disconnectServerCleanly`, `placeServerRobots` |
| `frontend/src/entities/robot/model/endEffector.ts` (A) | Findet die Greifer-Aktionen unter den Namen, die ein Roboter anbietet. | `findToggleEndEffectorKey`, `findEndEffectorActions`, `EndEffectorActions` |
| `frontend/src/entities/robot/model/store.ts` (M) | aus dev, angepasst: Der Not-Halt-Zustand entfällt (`robotEmergencyStopChanged`). Bei Verbindungsabbruch bleiben die Roboter erhalten und werden als `disconnected` markiert, statt gelöscht zu werden. | `applyRobotMessage`, `initialRobotStoreState`, `RobotStoreState` |
| `frontend/src/entities/robot/model/types.ts` (M) | aus dev, `emergencyStopActive` und `resultTypes` entfernt; `editorVisible` und `discoveryPath` ergänzt. | Robot-Typen |
| `frontend/src/entities/server/model/store.ts` (M) | aus dev, angepasst: Methodenstatus wird nur für Aufrufe aufgelöst, die mit `trackMethodCallRequest` registriert wurden. Sonst entstanden Meldungen für Hintergrundaufrufe wie `subscribeNode`. Das Flag `quiet` ist neu, `latestChildRequestIdByNodeId` entfällt. | `trackMethodCallRequest`, `markNodeSubscription`, `getNodeLiveValue`, `selectActiveServer` |
| `frontend/src/entities/server/model/types.ts` (M) | aus dev, `label` entfernt. | Server-Typen |

Gelöscht (Tests): `frontend/src/entities/server/model/store.test.ts` wurde im Zuge des Umbaus verkürzt und ist nicht gelöscht; der Dateiname bleibt bestehen.

**Ablauf.**
1. Zustandsmeldungen kommen über den `WscWebSocketClient`. `applyRobotMessage` aktualisiert die Roboter. Der Server-Speicher verarbeitet Knotenwerte, Ereignisse und den Verbindungszustand.
2. Methodenaufrufe: `trackMethodCallRequest` registriert einen Aufruf über seine `requestId`. Eine Antwort schließt ihn ab. `quiet` unterdrückt die Meldung, nicht die Verfolgung.
3. Beim Verbindungsabbruch eines Servers werden seine Roboter `disconnected`, und ihre Zuordnung zum Motion-Device wird gelöst.

**Konfiguration.** Keine.

**Tests.** `endEffector.test.ts` (3), `entities/robot/model/store.test.ts` (7), `entities/server/model/store.test.ts` (12), `applicationController.test.ts` (39, M).

**Weiterlesen.** Keine Fachdoku dazu. Zu `applicationController.ts` siehe die Abschnitte „Viewport und Robot-Control“ und „OPC-UA-Verbindung und Pi-Server“.

---

### Build, Konfiguration und sonstige Frontend-Dateien

**Zweck.** Vite-Konfiguration, Abhängigkeiten, Styles und Typdeklarationen, die im Frontend gemeinsam gelten.

**Einstieg.**
- `cd frontend`, dann `npm run dev` (Port 1234), `npm run build` (`tsc --noEmit && vite build`), `npm test` (`vitest run`) und `npm run typecheck`.

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `frontend/vite.config.ts` (M) | aus dev, ergänzt um: Plugin `cellModuleFiles()`, Proxy `/api` auf das Backend, zwei Build-Eingänge (`main`, `tagTool`). | `cellModuleFiles`, `virtual:cell-module-files`, `server.proxy`, `build.rollupOptions.input` |
| `frontend/package.json` (M) | aus dev, `vite` ersetzt durch den npm-Alias `npm:rolldown-vite@7.2.5` (vorher `7.3.6`). Eine Begründung steht weder hier noch in `vite.config.ts`. | Abhängigkeiten |
| `frontend/package-lock.json` (M) | aus dev. Folge des Vite-Wechsels: `rolldown` und `@rolldown/pluginutils` kommen hinzu. Insgesamt 23 `node_modules`-Einträge neu, 59 entfallen (teils durch Versionswechsel). | Lockfile |
| `frontend/src/index.css` (M) | aus dev, ergänzt um `.select` (Stil für native Auswahlfelder mit eigenem Hintergrund) und `step-attention` (Puls für `StepButton`). Bei `.input-ghost` wurde die feste Höhe `h-6` entfernt. `.select` hat in `src/` derzeit keine Verwendung. | `.select`, `step-attention`, `.input-ghost` |
| `frontend/src/test/vitest.d.ts` (M) | aus dev, Typdeklaration für die Teile von `vitest`, die das Projekt nutzt. Ergänzt um `it.each`, `vi.useFakeTimers` und weitere Matcher. | `declare module 'vitest'` |
| `frontend/tsconfig.tsbuildinfo` (A) | Build-Artefakt des TypeScript-Compilers. Der Ordner `frontend/.gitignore` schließt es nicht aus. Die Datei nennt Pfade, die es nicht gibt (z. B. `./src/app.tsx`, die Datei heißt `App.tsx`). Empfehlung: aus dem Repo entfernen und `*.tsbuildinfo` ignorieren. | – |

**Ablauf.** Beim Dev-Start liest das Plugin `cellModuleFiles` alle Modelldateien unter `public/models` und die URDFs unter `public/urdf` (ohne `meshes`-Ordner). Die Liste steht im virtuellen Modul `virtual:cell-module-files`, das `cell-modules/components/ModuleCatalogModal.tsx` importiert. Werden Dateien dort hinzugefügt oder entfernt, lädt der Dev-Server neu (`configureServer`). Der Proxy leitet `/api` an `http://127.0.0.1:8000`.

**Konfiguration.**
- Dev-Server: `server.port = 1234`. Proxy: `"/api": "http://127.0.0.1:8000"`.
- Build-Eingänge: `main` (`index.html`) und `tagTool` (`tag-tool.html`).
- Umgebungsvariable: `VITE_WSC2_WS_URL` (siehe „Gemeinsame UI-Bausteine“).

**Tests.** Frontend: `npm test` führt alle Vitest-Dateien aus. Die Tests dieses Teils zählen insgesamt 173 `it`/`test`-Zeilen (Frontend, ohne cell-modules). Backend: `cd backend && uv run pytest` (siehe unten).

**Weiterlesen.** [altlasten.md](altlasten.md): Abschnitt D (Umgebung, Build und Repo-Hygiene).

---

### Backend: API und WebSocket

**Zweck.** FastAPI-Anwendung mit zwei Wegen. REST dient dem Modulkatalog (`/api/module-catalog`). Der WebSocket `/ws` nimmt alle Befehle der Oberfläche entgegen und schickt Ereignisse zurück. Der Katalog liegt als Datei im Repo und wird versioniert.

**Einstieg.**
- Start: `main()` in `backend/src/backend/app.py` startet uvicorn mit `backend.app:app` auf `0.0.0.0:8000`. Das Skript `backend` in `pyproject.toml` ruft dieselbe Funktion auf.
- REST: `GET /api/module-catalog` und `PUT /api/module-catalog` (`backend/src/backend/api/module_catalog.py`).
- WebSocket: `/ws` (`websocket_endpoint` in `backend/src/backend/websocket/router.py`).

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `backend/src/backend/app.py` (M) | aus dev, ergänzt um: `create_app(module_catalog_store=None)`, Speicher des Katalogs in `app.state`, Router des Katalogs vor dem statischen Mount. Der statische Mount von `./www` hängt an der Umgebungsvariablen `HOST` (Altlast B7). | `create_app`, `main` |
| `backend/src/backend/api/__init__.py` (A) | Leeres Paketmarker-Modul. | – |
| `backend/src/backend/api/module_catalog.py` (A) | REST-Router `/api/module-catalog`. `GET` liefert Revision, Zeitstempel und Katalog. `PUT` speichert nur bei passender Revision: 422 bei ungültigem Katalog, 409 mit dem aktuellen Stand bei einem Konflikt. | `router`, `SaveCatalogRequest` (`baseRevision`, `catalog`), `read_catalog`, `save_catalog` |
| `backend/src/backend/services/module_catalog_store.py` (A) | Speichert den Katalog in einer Datei. Prüft das Schema `wsc.cellModules.catalog/1`, die Größe (höchstens 5 MiB) und die Revision. Schreibt über eine temporäre Datei und `os.replace`. | `ModuleCatalogStore`, `validate_catalog`, `default_catalog_path`, `InvalidCatalogError`, `RevisionConflictError`, `CATALOG_SCHEMA` |
| `backend/catalog/module_catalog.json` (A) | Der Modulkatalog der Zelle (496 Zeilen). Änderungen in der App verändern diese Datei; sie wird wie Code eingecheckt. | Datendatei |
| `backend/src/backend/websocket/router.py` (M) | aus dev, ergänzt um einen `ClientHub` je Anwendung und einen `LiveEmitter` je Seite. Ereignisse ohne `requestId` gehen an alle Seiten, Antworten nur an die Seite, die gefragt hat. | `websocket_endpoint`, `get_client_hub`, `get_registry`, `send_event` |
| `backend/src/backend/websocket/client_hub.py` (A) | Verwaltung der verbundenen Seiten und ihrer Zustellwege. Abos bleiben bestehen, auch wenn eine Seite neu lädt. | `ClientHub`, `LiveEmitter`, `broadcast`, `live_emitter` |
| `backend/src/backend/models/messages.py` (M) | aus dev, ergänzt um `event_type_node_ids` im Befehl `SubscribeEventCommand`. Entfernt: die Befehle `SubscribeRobotEmergencyStopCommand`, `UnsubscribeRobotEmergencyStopCommand`, `ReadAddressSpaceSubtreeCommand` und das Ereignis `RobotEmergencyStopChangedEvent`. | `ClientMessage`, `ServerMessage`, `SubscribeEventCommand` |
| `backend/src/backend/config/__init__.py` (A) | Leeres Paketmarker-Modul. Die Konfiguration steht in den Modulen selbst (siehe unten). | – |
| `backend/.gitignore` (M) | aus dev, ergänzt um `data/` (Kommentar: Modulkatalog zur Laufzeit gespeichert). Der Standardpfad des Katalogs liegt unter `backend/catalog/`, nicht in `data/`. Die Regel greift nur, wenn `WSC_DATA_DIR` auf ein Verzeichnis namens `data` zeigt. | – |

**Ablauf.**
1. **Katalog lesen:** `GET` liefert `{revision, updatedAt, catalog}`. Ohne Datei sind `revision` 0 und `catalog` `null`. Dann verwendet das Frontend seine Vorgabe.
2. **Katalog speichern:** `PUT` mit `baseRevision` und `catalog`. Stimmt die Revision nicht, antwortet das Backend mit 409 und liefert den aktuellen Stand. Die Oberfläche lädt dann neu. Sonst erhöht das Backend die Revision, setzt den Zeitstempel und schreibt die Datei.
3. **Befehle:** Die Oberfläche verbindet sich mit `/ws`. Jede Nachricht prüft `parse_client_message_json`. Eine ungültige Nachricht beantwortet das Backend mit dem Fehler `invalidMessage`. Gültige Nachrichten gehen an `handle_client_message` in `backend/src/backend/runtime/application_service.py`. Die Ergebnisse gehen zurück.
4. **Ereignisse:** Zustand der Zelle ohne `requestId` (z. B. Gelenkwerte, Verbindungsabbruch) geht an alle verbundenen Seiten (`ClientHub.broadcast`). Eine Antwort mit `requestId` geht nur an die anfragende Seite. Wenn eine Seite neu lädt, bekommt die neue Seite die Ereignisse, die die alte Seite gestartet hat.

**Konfiguration.**
- Umgebungsvariable `HOST`: Ist sie gesetzt, liefert das Backend `./www` als statische Dateien aus (Altlast B7).
- Umgebungsvariable `WSC_DATA_DIR`: Ordner für `module_catalog.json` statt `backend/catalog/` (`services/module_catalog_store.py`, beschrieben in `docs/development.md`).
- Port 8000 und Host `0.0.0.0` sind in `main()` fest eingetragen.
- Katalog: Schema `wsc.cellModules.catalog/1`, Obergrenze `MAX_CATALOG_BYTES = 5 * 1024 * 1024`.
- Nachrichten sind Pydantic-Modelle mit camelCase-Aliasen (`to_camel` in `backend/src/backend/models/base.py`). Auf dem Draht heißen die Felder also z. B. `requestId` und `eventTypeNodeIds`.

**Tests.**
- `backend/tests/test_module_catalog_api.py` (6, neu): leerer Katalog bis zum ersten Speichern, Speichern und Ausliefern, Revisionskonflikt, ungültiger Katalog, Speicherort mit `WSC_DATA_DIR`, Format der Datei.
- `backend/tests/test_client_hub.py` (8, neu): Zustellung an alle Seiten, Neuladen, Antwort nur an die anfragende Seite, ausgefallene Seiten.
- `backend/tests/test_websocket_router.py` (3, M): ungültige Nachricht, Robotererkennung über den WebSocket, Demo-Snapshot.
- Aufruf: `cd backend && uv run pytest`. Die Konfiguration steht in `pyproject.toml` (`pythonpath = ["src"]`).

**Weiterlesen.**
- [vision-system-integration.md](vision-system-integration.md): Teil 1.2 (vier Schichten im Backend).
- [altlasten.md](altlasten.md): Abschnitt B (B7, HOST-Mount).
- [apriltag-referenz.md](apriltag-referenz.md): §14, Datenformat im Modulkatalog.
- [altlasten-toter-code.md](altlasten-toter-code.md): Abschnitt 3.2 (P10 zum Katalog, P11: das Backend kennt keinen AprilTag-Code).

---

### Backend: OPC-UA-Dienste

**Zweck.** Das Backend hält je Server genau eine asyncua-Verbindung. Es liest die Struktur eines Roboter-Servers aus (Motion Devices, Achsen, Methoden, Skills) und übersetzt die Befehle der Oberfläche in OPC-UA-Aufrufe und -Abos. Die Verbindung wird überwacht und nach einem Abbruch wieder aufgebaut.

**Einstieg.**
- Befehle wie `connectServer`, `discoverRobots`, `disconnectServer` und alle anderen: `handle_client_message` in `backend/src/backend/runtime/application_service.py`, eine Kette von `isinstance`-Prüfungen.
- Verbindung: `AsyncUaServerConnection` in `backend/src/backend/opcua/server_connection.py`.
- Discovery: `discover_connected_server` in `backend/src/backend/opcua/asyncua_discovery.py`.

**Dateien.**

| Datei | Aufgabe | Wichtigste Bausteine |
|---|---|---|
| `backend/src/backend/opcua/asyncua_discovery.py` (M) | aus dev, ergänzt um: Lesen von Anzeige- und Browsenamen mit Klassenprüfung, Namens-Hilfen, Discovery über Knotenlisten (`*_from_nodes`). Neu, aber ohne Aufrufer: `discover_methods`, `discover_skills` und die Wrapper `discover_method_bindings` und `discover_skill_bindings`. Die Wrapper rufen nur die beiden ersten auf, der Live-Pfad nutzt die `*_from_nodes`-Varianten. Entfernt: `find_motion_device_system_emergency_stop`, `normalize_method_argument_value_type`, `read_node_class_name`. | `discover_connected_server`, `discover_motion_device_descriptors`, `read_class_display_and_browse_name`, `children_by_names`, `discover_method_bindings_from_nodes`, `discover_skill_bindings_from_nodes`, `KNOWN_MOTION_DEVICE_ROOT_PATHS` |
| `backend/src/backend/opcua/discovery.py` (M) | aus dev. Die Methoden-Bindung übernimmt `discovery_path` und verzichtet auf `parameter_types` und `result_types`. Die Skill-Bindung übernimmt `discovery_path`. | `MotionDeviceDescriptor`, `ServerDiscoveryResult`, `build_robot_session_infos`, `build_method_action_binding`, `build_skill_action_binding` |
| `backend/src/backend/opcua/method_calls.py` (M) | aus dev, ergänzt um: Array-Argumente mit Basistyp `BaseDataType` werden elementweise als Variant übergeben (`coerce_array_value`). `to_jsonable` kodiert Bytes als Base64 und Ereignisse als Objekt mit `eventType`. Fehlt die Methode auf dem Server, lautet der Fehler `method not found on the server`. | `coerce_array_value`, `coerce_scalar_value`, `to_jsonable`, `call_robot_method`, `call_raw_method` |
| `backend/src/backend/opcua/server_connection.py` (M) | aus dev, ergänzt um: Verbindungsaufbau auf einem frischen Client (`_connect_fresh`, `reconnect`), Lebenszeichen (`check_alive`, `_keepalive_running`), Wiederherstellung der Abos (`restore_subscriptions`, `_restore`), `read_namespace_uris`, Ereignisabo mit Ereignistypen (`subscribe_events`). Entfernt: Not-Halt-Abo, `read_address_space_subtree`, `_coerce_boolean`. | `AsyncUaServerConnection`, `check_alive`, `reconnect`, `restore_subscriptions`, `subscribe_events`, `call_raw_method` |
| `backend/src/backend/runtime/application_service.py` (M) | aus dev, ergänzt um: Zeitlimits, Überwachung der Verbindung (`_watch_server_liveness`), Wiederherstellung der Gelenk-Abos (`_resubscribe_robot_joints`), Beobachtung des Skill-Zustands (`_start_skill_action_watch`, `_watch_skill_action_state`), `_read_namespace_uris`. Entfernt: die Not-Halt-Befehle, `readAddressSpaceSubtree`, `_read_skill_result_status`, `_skill_result_indicates_failure`. | `handle_client_message`, `ensure_connection`, `discover_and_register`, `error_event` |
| `backend/src/backend/runtime/server_session.py` (M) | aus dev, ergänzt um: `liveness_task` je Server, `replace_liveness_task` (alte Aufgabe abbrechen) und `mark_disconnected`. | `ServerSession` |
| `backend/src/backend/models/opcua.py` (M) | aus dev. `value_type` aus `MethodArgument` entfernt. | `NodeBinding`, `MethodArgument` |
| `backend/src/backend/models/robot.py` (M) | aus dev. `RobotActionBinding` bekommt `discovery_path`, `result_types` entfällt. | `RobotActionBinding`, `RobotActionState`, `RobotJointState` |

**Ablauf.**
1. **Verbinden (`connectServer`):** `ensure_connection` legt die Verbindung je Server an. `connect()` hat ein Zeitlimit von 120 s. Danach liest `_read_namespace_uris` das Namespace-Array, der Server wird als verbunden markiert, und die Überwachung startet. Die Überwachung startet nur bei Verbindungen über den WebSocket (`emit_event` ist gesetzt).
2. **Discovery (`discoverRobots`, Zeitlimit 600 s):** `discover_connected_server` liest den Namespace-Kontext, die Motion Devices (Wurzelpfade nach `KNOWN_MOTION_DEVICE_ROOT_PATHS`, höchstens 4 Ebenen tief), Achsen, Methoden und Skills. Daraus entstehen `RobotSessionInfo` und `ServerSessionInfo`.
3. **Methodenaufruf (`callRawMethod`, `callRobotMethod`):** Die Argumente werden anhand der Typen auf dem Server gewandelt (`coerce_method_args`, `coerce_array_value`, `coerce_scalar_value`). Das Ergebnis geht über `to_jsonable` als JSON zurück. Fehler kommen als Ereignis mit Code, z. B. `invalidMethodInputs` oder `rawMethodCallFailed`.
4. **Abos:** `subscribeNode` und `subscribeEvent` (mit `event_type_node_ids`) schicken Ereignisse als `opcuaEvent` an die Seiten. Die Gelenke eines Roboters schicken `robotJointState`.
5. **Abbruch und Wiederverbindung:** Die Überwachung ruft `check_alive` alle 5 s auf. Das Lesen von `i=2258` schlägt fehl, oder ein Keepalive-Task von asyncua ist beendet. Dann gilt die Verbindung als tot. Die Überwachung meldet `serverDisconnected`, verbindet im 5-s-Takt neu, stellt die Abos mit denselben Callbacks wieder her (`restore_subscriptions`) und die Gelenk-Abos der synchronisierten Roboter (`_resubscribe_robot_joints`). Ein Abo, das die Oberfläche beendet hat, bleibt beendet.
6. **Trennen (`disconnectServer`):** `registry.disconnect_and_remove_server` entfernt den Server und seine Roboter.

**Konfiguration.**
- In `application_service.py`: `CONNECT_TIMEOUT_S = 120.0`, `DISCOVER_TIMEOUT_S = 600.0`, `LIVENESS_POLL_INTERVAL_S = 5.0`.
- In `AsyncUaServerConnection`: `CHECK_ALIVE_TIMEOUT_S = 30.0`, `SUBSCRIBE_TIMEOUT_S = 60.0` (ein Server antwortet auf ein Abo für unbekannte Knoten mitunter nie), `REQUEST_TIMEOUT_S = 120.0` (der Standard von asyncua von 4 s reicht für Pi-Methoden nicht; der Kommentar nennt rund 27 s für die Kalibrierung eines Bildes).
- In `asyncua_discovery.py`: `ROBOTICS_NAMESPACE_URI = "http://opcfoundation.org/UA/Robotics/"`, `DEVICE_INTEGRATION_NAMESPACE_URI = "http://opcfoundation.org/UA/DI/"`, `MAX_MOTION_DEVICE_DISCOVERY_DEPTH = 4`.
- Keine Umgebungsvariablen in diesem Abschnitt.

**Tests.**
- `backend/tests/test_server_connection.py` (8, M): Wechsel des Clients nach Verbindungsabbruch, Abos nach Wiederverbindung, Erkennung eines toten Keepalive-Tasks.
- `backend/tests/test_application_service.py` (27, M): Befehle für Verbinden, Discovery, Abos und Methoden, Überwachung mit `test_liveness_watch_resubscribes_robot_joints_after_reconnect`.
- `backend/tests/test_asyncua_discovery.py` (4), `test_method_calls.py` (12), `test_robot_models.py` (3): Übersicht, Discovery, Methodenargumente und Modelle (M).

**Weiterlesen.**
- [vision-system-integration.md](vision-system-integration.md): Teile 1.2 und 1.3 (Robot-Discovery: was heute fest verdrahtet ist), Teil 8 (Risiko R9).
- [vision-server-interface.md](vision-server-interface.md): §7 (Stolpersteine, z. B. die Ereignistyp-Liste).
- [altlasten-toter-code.md](altlasten-toter-code.md): Abschnitt 1.2 (`discover_methods`, `discover_skills`).
