# Refactoring: Redundanzen und toten Code abbauen

**Status:** in Arbeit — 23.09.2026
**Verantwortlich:** Meric Lotz, `Agent: Refactoring Redundanzen`
**Thema:** sonstiges
**Branch:** refactoring (abgezweigt von `feature/vision-server` @ 7ed0f6e)

## Ziel

Doppelte Logik an einer Stelle bündeln, toten Code entfernen, überlange
Funktionen zerlegen — **ohne** Verhaltensänderung an einer dokumentierten
Schnittstelle. Hinterher gibt es für jede Aufgabe (Kalibrierung an die
Bildgröße anpassen, Kamerapose aus Welttags, Payload veröffentlichen, JSON
schreiben, Board-Geometrie aus der Config …) genau eine Implementierung.

Nebenbei behoben: `src/jobs/calibrate.py` kannte die echten Hostnamen der Pis
nicht (eigene, veraltete Tabelle `pi-decke`/`pi-hand`) und suchte darum ohne
`VISION_FRAME_ID` die Kalibrierung unter `data/calibration/world.json`.

## Bereits gelesen

- `CLAUDE.md`, `doc/arbeitsplaene/README.md` — Doppelarbeits-Check: einziger
  Plan auf `in Arbeit` ist `deckenkamera-volle-aufloesung.md` (John Glanz).
  Dessen Dateien werden hier **nicht** strukturell angefasst (siehe unten).
- `doc/altlasten.md` (C3, C4, C5, C7, D3, D5, D11), `doc/apriltag-lokalisierung.md`
  (Schritt 8, `src/apriltag/` auflösen — zurückgestellt),
  `doc/apriltag-referenz.md`, `doc/vision-server-interface.md` (§6, §12, §13.1),
  `doc/part10-programm-schnittstelle.md` (§4, §5)

## Betroffene Dateien

- `src/tagloc/` (calibration, tagmap, localize, boards, detector, frames,
  overlay, geometry, cli/*) — neu: `tagloc/jsonio.py`, `tagloc/synthetic.py`
- `src/vision_server/` — job.py, runner.py, address_space.py,
  result_management.py, vision_program.py, nodeset_ids.py, events.py,
  state_machine.py, detection/*, calibration_session.py, stream_overlay.py,
  camera_health.py, tools/* — neu: `vision_server/tools/_client.py`,
  `vision_server/ua_nodes.py`
- `src/ua_program/program.py` (Vorlage der Betreuung, Änderungsliste im Kopf
  wird fortgeschrieben)
- `src/jobs/calibrate.py`, `tools/make_synthetic_scene.py`, `tools/make_tag_sheet.py`
- `tests/` — neu: `tests/_support.py` (unittest lädt keine `conftest.py`)

**Ausdrücklich nicht** (reserviert durch `deckenkamera-volle-aufloesung.md`):
`camera.py`, `camera_stream.py`, `profiles.py`, `server.py` — dort nur, was
zwingend für einen Import nötig ist.

## Schnittstellen

### OPC UA

**Keine Änderung.** Alle NodeIds, BrowsePaths, Methodensignaturen und
StatusCodes aus `vision-server-interface.md` §13.1 und
`part10-programm-schnittstelle.md` §4/§5 bleiben byte-gleich. Vor dem Umbau
der Methoden-Handler in `runner.py` und `ua_program/program.py` kommen Tests
dazu, die genau das festhalten.

### Payload

`wsc.vision.detections/1` (Interface §6) unverändert.

### Python (neu, intern)

```python
# tagloc/jsonio.py  (stdlib-only)
def write_json(path, payload, *, indent=2) -> None      # atomar: tmp + os.replace
def read_schema_json(path, schema: str, what: str) -> dict  # FileNotFoundError / ValueError

# tagloc/calibration.py
def fit_to_resolution(calibration, image_size, *, allow_scaling: bool) -> CameraCalibration
    # ValueError, wenn Größen abweichen und allow_scaling False ist

# tagloc/localize.py  (cv2-frei)
def locate_in_frame(tag_poses, tag_map, *, camera_frame_id, max_reprojection_error_px)
    -> tuple[Pose | None, list[ModuleLocation]]

# tagloc/boards.py
def chessboard_object_points(spec, dtype=np.float32) -> np.ndarray   # bisher privat
```

Bestehende öffentliche Funktionen behalten Namen und Signatur. Entfernt wird
nur Code ohne jeden Aufrufer (per grep über `src/ tests/ tools/ doc/` geprüft).

### Betrieb

`src/jobs/calibrate.py` bekommt Frame-ID, Kalibrier- und Tag-Map-Pfad vom
`ScriptDetectionSource` per Umgebung (`VISION_FRAME_ID`,
`VISION_CALIBRATION_PATH`, `VISION_TAG_MAP_PATH`, `PYTHONPATH`) statt sie zu
raten. Manuell gestartet verhält es sich wie bisher.

### Fehlerfälle

Unverändert. Einzige Ausnahme: `ImageFolderSource.read` endet bei lauter
unlesbaren Bildern mit `None` statt mit `RecursionError`.

## Vorgehen

1. Strang A (tagloc/tools/Bildpfad) und Strang B (Job/Runner/OPC UA) auf
   eigenen Branches, danach Merge nach `refactoring`.
2. Jeder Schritt einzeln committet, nach jedem Schritt die ganze Suite
   (`PYTHONPATH=src python -m unittest discover -s tests -t .`).
3. Test-Hilfen (`tests/_support.py`) nach dem Merge.

## Offene Fragen

- `src/apriltag/` (Altbestand, von niemandem importiert) löschen? Laut
  `apriltag-lokalisierung.md` Schritt 8 bis zur Abnahme am echten Pi
  zurückgestellt — Teamentscheidung, hier **nicht** gelöscht.
- Tote Felder in `profiles.AprilTagProfileConfig` (`camera_index`,
  `use_picamera`, `warmup_s`): `profiles.py` ist reserviert, erst nach
  Abschluss des Deckenkamera-Plans entfernen.
- RealSense-Code liegt dreifach vor (`frames.py`, `camera.py`,
  `tools/list_realsense_profiles.py`) — betrifft `camera.py`, darum später.
- LDS: `discovery/lds.py` hat `DEFAULT_LDS_URL = ""`, die Doku sagt, der
  Server registriert sich bei `10.10.38.27`. Kein Refactoring-Thema, nur
  gemeldet.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- Neue öffentliche Helfer in `doc/apriltag-referenz.md` (eigener Abschnitt).
