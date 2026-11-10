# Kalibrier-Aufnahmen verlustfrei archivieren (Rohbilder je Sitzung)

**Status:** fertig — 10.11.2026
**Verantwortlich:** Meric, `Agent: Kalibrier-Rohbilder`
**Thema:** vision
**Branch:** `handauge-schachbrett-offline`

## Ziel

Mit `VISION_SAVE_CALIBRATION_CAPTURES=1` speichert die Intrinsik-Kalibrierung
(„Kamera kalibrieren“) jede Aufnahme verlustfrei als PNG in einem eigenen Ordner
je Sitzung, damit die Intrinsik offline nachgerechnet werden kann. Vorher
wurde als JPEG (Qualität 92) archiviert, und jede neue Sitzung überschrieb
`kalib_001.jpg` … der vorigen. Anlass: Die gespeicherte Intrinsik der
Hand-Kamera (`cam_flange.json`, 23.09.2026) weicht vom Werkswert der RealSense
D415 ab (cy 263,4 statt 243,4 px). Für eine Offline-Neukalibrierung braucht es
verlustfreie Rohbilder, und keine Sitzung darf die vorige überschreiben.

Vorgehen für den Nutzer: Variable setzen, Aufnahmen machen, „Abbrechen“ statt
„Fertig“ wählen. Dann wird nichts gerechnet und nichts aktiviert, die Bilder
bleiben im Archiv.

## Bereits gelesen

- `CLAUDE.md` (Abschnitt „Bevor du anfängst“, Arbeitsplan-Pflicht)
- `doc/projektdoku/arbeitsplaene/_vorlage.md`, `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/vision-server-interface.md`, Abschnitt 12.9 (Debug: Aufnahmen als Bilder speichern) und Abschnitt 12 (Kalibrierung, Ablauf)
- `doc/projektdoku/code-handbuch.md`, Tabelle der Umgebungsvariablen (`VISION_SAVE_CALIBRATION_CAPTURES`) und Konfigurationsliste `calibration_capture_dir`

## Betroffene Dateien

Die Liste reserviert die Dateien.

- `src/vision_server/calibration_session.py`: Archiv je Sitzung (`_start_archive()`), PNG-Aufnahme (`_ARCHIVE_IMAGE_SUFFIX = ".png"`), `archive_dir`, `progress["archiveDir"]`
- `src/vision_server/server.py`: Kommentar an `calibration_capture_dir` (PNG je Sitzung statt JPEG), Pfad unverändert `data/calibration/<frame_id>_captures/`
- `tests/test_calibration_session.py`: Tests für Ordnerstempel, Kollisionssuffix, `session.json`, Schreibfehler, unlesbare Kalibrierdatei
- `doc/projektdoku/vision-server-interface.md` (Nachtrag zu 12.9), `doc/projektdoku/code-handbuch.md` (zwei Stellen)

## Schnittstellen

### OPC UA

Keine neuen Knoten, Methoden oder Ports. Die Methode `CaptureCalibrationSample`
und die Fortschrittsanzeige `CalibrationProgress` bleiben unverändert. Das
Feld `archiveDir` kommt im JSON-Fortschritt hinzu (siehe Daten).

### Python

```python
# Modul: src/vision_server/calibration_session.py

class CalibrationSession:
    def __init__(self, ..., now: Callable[[], datetime] | None = None) -> None:
        """`now` ist die injizierbare Uhr (Standard: UTC-Jetzt). Nur für Tests
        und den Archivstempel gedacht."""

    @property
    def archive_dir(self) -> Path | None:
        """Sitzungsordner des Archivs, oder None, wenn das Archiv für diese
        Sitzung aus ist (Variable nicht gesetzt oder Fehler beim Anlegen)."""

    @property
    def progress(self) -> dict:
        """Enthält zusätzlich `archiveDir` (str oder None)."""

    def start(self) -> None:
        """Beginnt eine Sitzung. Legt bei aktivem Archiv den Sitzungsordner
        und `session.json` an. Fehler beim Anlegen werfen nicht, sie stehen
        als Warnung im Log, und das Archiv ist für diese Sitzung aus."""
```

`start()` wirft also keine Ausnahme wegen des Archivs. `capture()` bleibt
unverändert und gibt weiter `bool` zurück.

### Daten / Payload

Ablage (Archiv):

```
data/calibration/<frame_id>_captures/<UTC-Stempel YYYYmmddTHHMMSSZ>/
    session.json
    kalib_001.png
    kalib_002.png
    ...
```

- Stempel: UTC, Format `YYYYmmddTHHMMSSZ`. Bei Kollision in derselben Sekunde
  Suffix `_2`, `_3`, … (also `<Stempel>_2/`).
- Zähler `kalib_NNN` je Sitzung ab 001, dreistellig.
- Kompression: PNG-Stufe 1 (verlustfrei, schnell).

Arbeitsordner (unverändert, Grundlage der Online-Auswertung):

```
data/calibration/<frame_id>_pending/kalib_NNN.jpg    (JPEG, unverändert)
```

`session.json`:

```json
{
  "schema": "adp.calibration.captures/1",
  "frameId": "string, z. B. cam_flange",
  "startedAt": "ISO-8601 UTC mit Z, z. B. 2026-11-10T09:12:03Z",
  "imageFormat": "png",
  "pendingFormat": "jpg",
  "board": {
    "type": "chessboard",
    "cols": "int, Innenecken (Hand-Pi: 7)",
    "rows": "int, Innenecken (Hand-Pi: 10)",
    "squareSizeM": "float, Meter",
    "markerSizeM": "float, Meter (nur ChArUco relevant)",
    "dictionary": "string (nur ChArUco relevant)"
  },
  "model": "pinhole | fisheye",
  "activeCalibration": "Inhalt der beim Start aktiven Kalibrierdatei, oder null"
}
```

`archiveDir` im Fortschritt (`CalibrationProgress`): absoluter Pfad des
Sitzungsordners als String, oder `null`, wenn das Archiv aus ist.

### Netz / Betrieb

- Umgebungsvariable unverändert: `VISION_SAVE_CALIBRATION_CAPTURES=1`
  (systemd-Unit oder Shell vor dem Start). Ohne sie gibt es kein Archiv.
- Kein neuer Port, kein neuer Dienst, keine neue systemd-Unit.
- Auf der 12-MP-Deckenkamera ist PNG deutlich größer und langsamer als JPEG.
  Die Variable dort nur bei Bedarf setzen und die Ordner danach von Hand
  leeren (`data/` ist gitignored, kein Aufräum-Mechanismus).

### Fehlerfälle

- Schreibfehler eines Archivbildes: Log-Eintrag. Die Aufnahme bleibt im
  Arbeitsordner `_pending` nutzbar, die Kalibrierung läuft normal weiter.
- Ordner oder `session.json` nicht anlegbar (z. B. Platte voll, keine
  Schreibrechte): Warnung im Log, `archiveDir` ist `null`, `start()` schlägt
  nicht fehl.
- Aktive Kalibrierdatei nicht lesbar oder kein gültiges JSON: Warnung im Log,
  `activeCalibration: null` in `session.json`.
- Abbruch („Abbrechen“): nichts wird gerechnet oder aktiviert. Das Archiv
  bleibt liegen.

## Vorgehen

1. Plan anlegen und Schnittstellen festlegen (dieser Plan).
2. Umsetzung im Sitzungsmodul: Stempel mit injizierbarer Uhr, Archivordner mit
   Kollisionssuffix, `session.json`, PNG-Speicherung neben dem JPEG-Pending.
3. Tests in `tests/test_calibration_session.py`.
4. Doku-Nachträge (Fach-MD 12.9, Code-Handbuch).

## Offene Fragen

- Speicherbedarf auf dem Pi bei langen Sitzungen mit PNG: noch nicht gemessen.
  Auf der Deckenkamera nicht standardmäßig aktivieren.
- Die Hand-Kamera-Intrinsik (`cam_flange.json`) soll offline mit den
  Rohbildern neu gerechnet werden. Das ist Folgearbeit und nicht Teil dieses
  Plans.

## Abweichungen vom Plan

Noch keine. Wird bei der Umsetzung ergänzt.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in `doc/projektdoku/vision-server-interface.md` (12.9) und
  `doc/projektdoku/code-handbuch.md` übernommen.
