# Use-Case-Foto: OPC-UA-Methode SaveUseCaseImage

**Status:** in Arbeit — 29.10.2026
**Verantwortlich:** Meric, `Agent: Use-Case-Foto`
**Thema:** vision
**Branch:** `usecase-foto`

## Ziel

Nach dem Ablegen eines Würfels in Use Case 1 der Web-App fährt der Roboter
die Handkamera (Hand-Pi, PI_2) in die Fotopose: 20 cm senkrecht über der
Soll-Mitte der Ablage, aus der Hand-Auge-Kalibrierung. Dort wird ein Foto
aufgenommen. Bild und Metadaten landen pro Versuch auf dem Pi, und das Bild
wird direkt gemessen (Δx, Δy, Δθ, siehe `use-case-messung.md`). Damit lassen
sich Genauigkeit und Wiederholgenauigkeit der Ablage auswerten. Vorher gab
es dafür keine Ablage, die Versuche ließen sich nicht einzeln nachvollziehen.

## Bereits gelesen

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/README.md`, `_vorlage.md`, `rohbild-speichern.md`
- `doc/projektdoku/vision-server-interface.md` (Abschnitt 18 SaveRawImage, 13.6 Betrieb)

## Betroffene Dateien

- `src/vision_server/use_case_image.py` (neu): Dateiablage, Versuchsnummer, JSON
- `src/vision_server/config.py`: Feld `use_case_dir`
- `src/vision_server/runner.py`: `_install_save_use_case_image`, `_build_use_case_pi_meta`
- `src/use_case_eval/live.py` (neu): Anbindung der Messung an die Methode (`measure_for_save`, Aufkleber-Versatz, `pi.intrinsics`)
- `tests/test_use_case_image.py` (neu)
- `doc/projektdoku/vision-server-interface.md` (Abschnitt 18a)
- `doc/projektdoku/arbeitsplaene/use-case-messung.md` (Messung, Zielscheibe)
- Frontend (Web-App): Versuchsreihe-Feld, Aufruf der Methode nach Use Case 1,
  Übergabe des Client-JSON (Schema `wsc.usecase.trial/1`)

## Schnittstellen

### OPC UA

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SaveUseCaseImage` | `ns=<own_idx>;s=<vision_system_name>.SaveUseCaseImage` | Methode | Aufruf | speichert Kamerabild und Metadaten als Versuch |

Am Vision-System angelegt und – wie `SaveRawImage` – über `calibration_methods`
gespiegelt. Namespace-Index über die URI auflösen, nie hartcodieren.

Signatur: `SaveUseCaseImage(Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error: Int32)`

- `Series`: Versuchsreihe aus der Web-App, muss `>= 1` sein.
- `MetadataJson`: JSON-Text des Web-Clients. Der Server prüft den Inhalt nicht.
- `FileName`: PNG-Pfad relativ zur Repo-Wurzel mit `/`, z. B.
  `data/use_case/reihe_003/versuch_012.png`. Liegt das Ziel außerhalb der
  Repo-Wurzel, der absolute Pfad. Bei Fehler `""`.
- `ResultJson`: Bildmessung des Versuchs als JSON-Text (Felder und Bedeutung
  siehe `use-case-messung.md`). Bei Fehler-Codes `""`. Ein Messfehler ist
  **kein** Fehler-Code: das Foto wird gespeichert, `ResultJson` enthält dann
  `{"ok": false, "grund": "..."}`.
- `Error` (`VisionErrorCode`):

| Code | Name | Bedeutung |
| --- | --- | --- |
| `0` | OK | gespeichert |
| `1` | INVALID_STATE | kein frisches Bild (≥ 0,3 s nach Aufruf) innerhalb 5 s |
| `2` | INVALID_ARGUMENT | `Series` < 1 |
| `3` | BUSY | ein Job läuft |
| `6` | INTERNAL | Speichern fehlgeschlagen, Ausnahme im Log |

Die Codes 4, 5 und 7 kommen bei dieser Methode nicht vor.

### Python

```python
# Modul: src/vision_server/use_case_image.py
SCHEMA = "adp.usecase.trial/1"

def next_trial_number(directory: Path) -> int:
    """Höchste vorhandene Versuchsnummer + 1; leer oder fehlend: 1. Lücken bleiben."""

def save_use_case_image(image, base_dir: Path, series: int, client_meta: str,
                        pi_meta: dict, now: datetime | None = None,
                        measurement: dict | None = None,
                        annotated=None) -> tuple[Path, int]:
    """Schreibt PNG und JSON, liefert (PNG-Pfad, Versuchsnummer).
    `measurement` wird als Feld `messung` in die JSON geschrieben, `annotated`
    (Kontrollbild) als `versuch_NNN_messung.png` (best effort).
    Wirft ValueError bei series < 1, OSError bei Schreibfehlern.
    Nie überschreibend: exklusives Anlegen, bei Kollision nächste Nummer."""

# Modul: src/use_case_eval/live.py (Anbindung an SaveUseCaseImage)
def measure_for_save(image, calibration, sticker_offset_mm=(0.0, 0.0)) -> tuple[dict, Any]:
    """Misst ohne Ausnahmen; liefert (messung_dict, kontrollbild oder None).
    Ohne Kalibrierung: {"ok": False, "grund": "Keine Kamerakalibrierung verfuegbar"}."""
```

Konfiguration: `VisionServerConfig.use_case_dir: Path | None`, Default
`<Repo>/data/use_case`. `None` = Methode wird nicht angelegt. Die Methode
entsteht in `install_vision_machine`, wenn eine Kamera-Quelle vorhanden und
`use_case_dir` nicht `None` ist.

### Daten: Ablage

```
data/use_case/
└── reihe_003/
    ├── versuch_001.png
    ├── versuch_001.json
    ├── versuch_001_messung.png   (Kontrollbild, nur bei vorliegender Messung)
    └── versuch_002.png
```

Außerdem liegt `data/use_case/aufkleber_versatz.json` (eine Datei je
Use-Case-Ordner, nicht je Reihe) mit dem Aufkleber-Versatz, falls kalibriert.

- Ordner `reihe_NNN` und Dateien `versuch_NNN` dreistellig, bei mehr als 999
  Versuchen wächst die Nummer einfach weiter.
- Die Versuchsnummer ist die höchste vorhandene Nummer (PNG oder JSON) plus 1.
  Gelöschte Versuche werden nicht aufgefüllt, solange ein höherer existiert.
- Schlägt das Schreiben der JSON fehl, wird das PNG wieder entfernt.

### Daten: JSON je Versuch

```json
{
  "schema": "adp.usecase.trial/1",
  "reihe": 3,
  "versuch": 12,
  "bild": "versuch_012.png",
  "gespeichert": "2026-10-29T14:03:12",
  "pi": { "...": "siehe unten" },
  "client": { "...": "Objekt aus MetadataJson, Schema wsc.usecase.trial/1" }
}
```

| Feld | Bedeutung |
| --- | --- |
| `schema` | Kennung `adp.usecase.trial/1` |
| `reihe` | `Series` |
| `versuch` | fortlaufende Nummer in der Reihe |
| `bild` | Dateiname des PNG neben der JSON |
| `gespeichert` | Zeitpunkt des Speicherns, lokale Zeit des Pi, ISO 8601 ohne Zeitzone, Sekunden genau |
| `pi` | Metadaten des Pi (siehe unten) |
| `messung` | Bildmessung (`TrialMeasurement`, gleiche Felder wie `ResultJson`). Vor dem Speichern berechnet; bei Messfehler `{"ok": false, "grund": ...}` |
| `client` | `MetadataJson` als Objekt. Schema `wsc.usecase.trial/1` (Soll-Pose, Tisch, Route, Ist-Gelenke usw.) |
| `client_raw` | statt `client`: der Text, wenn `MetadataJson` kein gültiges JSON-Objekt ist |

Felder von `pi`, jeweils nur vorhanden, wenn ermittelbar:

- `frame_timestamp`, `frame_age_s` (Alter des Bildes beim Speichern, s), `image_shape` (`[H, W, C]`)
- `frame_id`, `vision_system_name`, `vision_system_id`, `hostname`, `endpoint`
- `camera_stream` (Kameraeinstellungen als Objekt)
- `intrinsics` (nur bei geladener Kalibrierung): `source` (`calibration` / `realsense_factory` / `placeholder`), `calibrationId`, `imageSize` (`[Breite, Höhe]`), `cameraMatrix` (3x3), `distortionCoefficients`, `distortionModel` (`pinhole` / `fisheye`). Damit nimmt die Offline-Auswertung dieselbe Intrinsik wie die Live-Messung.
- `apriltag` (Profil als Objekt), `calibration` und `hand_eye` (je `{path, exists, content}`)

`content` ist die geparste Datei (JSON oder YAML), sonst deren Text. Fehlt eine
Datei, steht `exists: false` und kein `content`.

### Netz / Betrieb

Keine neuen Ports oder Services. Der Server bleibt `opcua-server.service`.
Bildkodierung mit OpenCV (`cv2`), wird erst beim Aufruf importiert.
`frame_id` = `config.apriltag.frame_id`, ohne AprilTag-Konfiguration
`config.vision_system_name`.

### Fehlerfälle

- Kein frisches Bild innerhalb 5 s (z. B. Kamera hängt): `INVALID_STATE`, nichts geschrieben.
- `Series` < 1: `INVALID_ARGUMENT`, nichts geschrieben.
- Job läuft: `BUSY`, sofort, ohne Warten auf ein Bild.
- Platte voll, kein Schreibrecht, PNG-Kodierung fehlgeschlagen: `INTERNAL`, Ausnahme im Log, keine Teildatei übrig (PNG wird bei JSON-Fehler gelöscht).
- Ungültiges `MetadataJson`: kein Fehler, Versuch wird mit `client_raw` gespeichert.
- Fehlende Pi-Metadaten: einzelne Felder fehlen, der Versuch wird trotzdem gespeichert.
- Messfehler (kein Würfelmarker, zu wenige Blattmarker, keine Kalibrierung,
  Ausnahme in der Messung): kein Fehler-Code. Das Foto wird gespeichert,
  `messung.ok` ist `false` mit `grund`; ohne Kontrollbild fehlt nur die
  `_messung.png`.

## Vorgehen

1. Plan und Fach-Doku (diese Datei, Abschnitt 18a in `vision-server-interface.md`).
2. `use_case_image.py`, Config-Feld, Handler in `runner.py`.
3. Tests in `tests/test_use_case_image.py` (Versuchsnummer, Lücken, Kollision, `client_raw`, Reihe < 1, Fehlercodes).
4. Frontend: Versuchsreihe-Feld und Aufruf nach Use Case 1.
5. Inbetriebnahme auf dem Hand-Pi (siehe unten), Probelauf mit echter Ablage.

## Inbetriebnahme

Auf dem Hand-Pi (PI_2):

```bash
cd <repo auf dem Pi>
git pull                                  # Stand mit SaveUseCaseImage holen
sudo systemctl restart opcua-server.service
```

Danach prüfen, ob die Methode am Vision-System vorhanden ist (Browse über den
Client, z. B. `tools/handshake_client.py` als Vorlage). Fehlt sie, läuft noch
eine ältere Server-Version oder `use_case_dir` ist `None`. Dann die Pi-Software
aktualisieren und den Server erneut neu starten.

## Test

`tests/test_use_case_image.py` (unittest) deckt ab:

- Ablage: Versuchsnummer ohne und mit Lücken, neue Reihe beginnt bei 1, Kollision
  (nie überschreiben), JSON-Inhalt, `client_raw`, nicht serialisierbare Werte,
  `Series` < 1, Kodierfehler.
- OPC-UA-Methode an einem echten asyncua-Server: Speichern, Warten auf ein Bild
  nach dem Aufruf, Timeout → `INVALID_STATE`, `INVALID_ARGUMENT`, `BUSY`,
  `INTERNAL`.

- Messung und Ablage mit Messung: `messung` in der JSON, `ResultJson` bei
  Erfolg, Kontrollbild, Messfehler ohne Abbruch, Messung ohne Kalibrierung.
  Details: `use-case-messung.md`.

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_use_case_eval tests.test_use_case_image
```

Stand des Worktrees: beide Module zusammen 45 Tests, alle grün (rund 100 s,
ein Teil davon sind synthetische Messungen).

Manuell am Pi: Methode zweimal mit derselben Reihe aufrufen, dann
`data/use_case/reihe_NNN/` prüfen: zwei PNG, zwei JSON, Nummern fortlaufend.

## Offene Fragen

- Aufräumen alter Versuche auf der SD-Karte ist nicht Teil dieser Aufgabe.

## Abweichungen vom Plan

- **Ausgabe erweitert:** `SaveUseCaseImage` liefert jetzt
  `(FileName, ResultJson, Error)`. `ResultJson` ist die Bildmessung als JSON
  (bei Fehler-Codes leer). Der Stand `(FileName, Error)` vom Commit `d0a5e90`
  ist damit überholt.
- **Messung im Speichern:** Die Messung läuft vor dem Speichern (in einem Thread)
  und steht als `messung` in der JSON. Ein Messfehler lässt das Speichern nicht
  scheitern.
- **Fotopose:** Nicht mehr Flansch 30 cm über der Ablage, sondern die Kamera
  20 cm senkrecht über der Soll-Mitte, aus `poseFlangeCam` der
  Hand-Auge-Kalibrierung berechnet. Bildachsen parallel zur Tisch-x-Achse.
  Details im Arbeitsplan `use-case-messung.md`. Frontend: webskillcomposition,
  Branch `usecase-foto`.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD übernommen (Abschnitt 18a
  in `vision-server-interface.md` ist bereits angelegt).
