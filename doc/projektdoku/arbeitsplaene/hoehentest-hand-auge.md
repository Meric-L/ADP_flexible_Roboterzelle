# Höhentest der Hand-Auge-Kalibrierung

**Status:** fertig — 03.11.2026 (Drehtest und Hand-Auge ohne Maßstab; an Roboter und Pi noch nicht getestet)
**Verantwortlich:** Meric, `Agent: Höhentest Hand-Auge`
**Thema:** vision
**Branch:** messkette-protokoll (dieses Repo und `webskillcomposition`)

## Ziel

In den Use-Case-Reihen 1–4 misst die Handkamera den Abstand zum Zielblatt 9–15 mm kleiner, als das Modell erwartet (Kamerahöhe 183–192 mm statt 200 mm). Das Blatt ist mit dem Lineal nachgemessen und stimmt. Es bleiben zwei Ursachen:

- **Hand-Auge-z:** Die Kamera sitzt entlang ihrer Blickachse etwa 12 mm anders als `T_flansch_cam` sagt. Die Differenz ist dann in jeder Höhe gleich.
- **Maßstab** (Brennweite der Intrinsik): Die Differenz wächst dann mit der Höhe.

Ein Knopf im Hand-Auge-Fenster fährt die Kamera senkrecht über das Zielblatt, fotografiert es aus fünf Höhen und zeigt, welcher Fall vorliegt. Jedes Bild wird mit Messung und Metadaten auf dem Hand-Pi unter `data/hoehentest/` abgelegt.

Dafür wird das Speichern verallgemeinert: eine OPC-UA-Methode `SaveImage`, der Zweck (`Kind`) kommt als Parameter. Ablageordner, Dateinamen und Messung je Zweck stehen in der Pi-Konfiguration; ein weiterer Zweck braucht keine neue Methode, nur einen Eintrag dort. Das Use-Case-Foto läuft ebenfalls über `SaveImage`; `SaveUseCaseImage` bleibt vorerst als veraltete Weiterleitung (Kind `usecase`) für Frontends anderer Branches.

## Bereits gelesen

- `doc/projektdoku/arbeitsplaene/use-case-foto.md`, `use-case-messung.md`, `messkette-protokoll.md`
- `doc/projektdoku/arbeitsplaene/handeye-genauigkeit.md`
- `doc/projektdoku/vision-server-interface.md` (Abschnitt `SaveUseCaseImage`)
- Auswertung der Reihen 1–4 (`data/use_case/reihe_00{1..4}`)

## Betroffene Dateien

Dieses Repo:
- `src/use_case_eval/measure.py`: neue Funktion `measure_sheet` (Blattpose ohne Würfel), Blattteil aus `_measure` herausgelöst
- `src/use_case_eval/live.py`: `measure_sheet_for_save`
- `src/vision_server/use_case_image.py`: Ablage mit wählbaren Ordner- und Dateinamen
- `src/vision_server/runner/save_use_case_image.py`, `runner/install.py`, `runner/__init__.py`: allgemeine Methode `SaveImage`, `SaveUseCaseImage` als Weiterleitung
- `src/vision_server/config.py`: `image_kinds` (Zwecke für `SaveImage`)
- `tests/test_use_case_image.py`, `tests/test_use_case_eval.py`
- `doc/projektdoku/vision-server-interface.md`: neuer Abschnitt

`webskillcomposition` (`frontend/src/features/`):
- `opcua-server/config/piServers.ts`: NodeId `saveImageNodeId`
- `opcua-server/model/useSaveRawImage.ts`: Aufruf und Auswertung von `SaveImage`
- `cell-modules/model/usePickPlaceRun.ts`: Use-Case-Foto über `SaveImage` (Kind `usecase`)
- `cell-modules/model/pickPlace.ts`: Fotopose für beliebige Höhe (`photoFlangeAt`)
- `cell-modules/model/pickPlaceTarget.ts` (neu): gemeinsames Fotoziel von Pick & Place
- `cell-modules/model/heightTest.ts` (neu): Höhen, Auswertung (Ausgleichsgerade)
- `cell-modules/components/HandEyeHeightTest.tsx` (neu), eingebunden in `HandEyeCalibrationWindow.tsx`
- `cell-modules/model/usePickPlaceRun.ts`: veröffentlicht das Fotoziel

## Schnittstellen

### OPC UA (Hand-Pi)

`SaveImage(Kind: String, Series: Int32, MetadataJson: String) -> (FileName: String, ResultJson: String, Error: Int32)`

BrowsePath wie die übrigen Methoden: `<VisionSystem>.SaveImage`.

- `Kind`: Zweck, ein Schlüssel aus `image_kinds` der Pi-Konfiguration. Unbekannt: `INVALID_ARGUMENT` (2). Vorhanden:

| Kind | Ablage | Messung | Schema |
| --- | --- | --- | --- |
| `usecase` | `data/use-case/reihe_NNN/versuch_NNN.*` | `measure_trial` (Würfel auf dem Blatt), Kontrollbild `versuch_NNN_messung.png` | `adp.usecase.trial/1` (Schlüssel `reihe`, `versuch`) |
| `hoehentest` | `data/hoehentest/lauf_NNN/bild_NNN.*` | `measure_sheet` (nur Blattpose) | `adp.hoehentest.image/1` (Schlüssel `lauf`, `bild_nr`) |

- `Series`: `0` legt die nächste freie Reihe bzw. den nächsten Lauf an, `>= 1` legt das Bild in diese(n). Negativ: `INVALID_ARGUMENT` (2).
- `MetadataJson`: beliebiges JSON des Clients, wird unverändert unter `client` abgelegt. Kein gültiges JSON-Objekt: unter `client_raw` als Text.
- `FileName`: Pfad relativ zum Repo, z. B. `data/hoehentest/lauf_003/bild_002.png`. Die Reihen- bzw. Laufnummer steht im Ordnernamen.
- `ResultJson`: Messung des Zwecks, bei Fehler-Codes leer.
- `Error`: 0 OK, 1 kein frisches Bild (`INVALID_STATE`), 2 ungültiger Zweck oder ungültige Reihe, 3 Job läuft (`BUSY`), 6 Schreibfehler (`INTERNAL`). Eine fehlgeschlagene Messung ist kein Fehler: sie steht als `{"ok": false, "grund": ...}` im Ergebnis, das Bild wird trotzdem gespeichert.

Wartet auf ein Bild, das mindestens 0,3 s nach dem Aufruf entstand; während eines Jobs abgelehnt; wird nur angelegt, wenn eine Kamera da ist und `image_kinds` nicht leer ist.

`SaveUseCaseImage(Series, MetadataJson)` bleibt unverändert erreichbar und ruft intern `SaveImage("usecase", Series, MetadataJson)` auf (Series weiterhin `>= 1`). Veraltet: neue Clients nehmen `SaveImage`.

### Pi-Konfiguration

```python
# src/vision_server/config.py
@dataclass(frozen=True)
class ImageKind:
    base_dir: Path        # z. B. data/hoehentest
    folder: str           # "lauf" -> lauf_NNN
    stem: str             # "bild" -> bild_NNN.png/.json
    series_key: str       # Schlüssel der Reihennummer in der JSON ("lauf")
    number_key: str       # Schlüssel der Bildnummer in der JSON ("bild_nr")
    schema: str           # "adp.hoehentest.image/1"
    measure: str          # "trial" | "sheet" | "none"

image_kinds: dict[str, ImageKind]   # Standard: "usecase", "hoehentest"
```

### Ablage Höhentest

`data/hoehentest/lauf_NNN/bild_NNN.png` und `bild_NNN.json`, nie überschreibend.

```json
{
  "schema": "adp.hoehentest.image/1",
  "lauf": 3,
  "bild_nr": 2,
  "bild": "bild_002.png",
  "gespeichert": "2026-11-03T14:05:00",
  "pi": { "...": "wie bei SaveUseCaseImage (Intrinsik, Kamera-Stream, apriltag)" },
  "client": { "...": "MetadataJson" },
  "messung": { "...": "ResultJson" }
}
```

### Python

```python
# src/use_case_eval/measure.py
def measure_sheet(image, calibration, *, refine_edges: bool = True) -> dict:
    """Blattpose aus den Blattmarkern (IDs 0-3), ohne Würfel. Wirft nie.
    {"ok": bool, "grund": str, "camera_height_mm": float | None,
     "camera_tilt_deg": float | None, "camera_in_sheet_mm": [x, y, z] | None,
     "axis_hit_mm": [x, y] | None, "reprojection_rms_px": float | None,
     "sheet_markers": [int], "sheet_edges_refined": int}"""

# src/use_case_eval/live.py
def measure_sheet_for_save(image, calibration) -> dict: ...   # ohne Kalibrierung: ok=False

# src/vision_server/use_case_image.py
def save_use_case_image(image, base_dir, series, client_meta, pi_meta, now=None,
                        measurement=None, annotated=None, *,
                        folder="reihe", stem="versuch", series_key="reihe",
                        number_key="versuch", schema=SCHEMA) -> tuple[Path, int]: ...
def next_series_number(base_dir: Path, folder: str) -> int: ...  # für Series = 0
```

`camera_height_mm`: Abstand der Kamera (optisches Zentrum) zur Blattebene, gleiche Rechnung wie in `measure_trial`. `axis_hit_mm`: wo die optische Achse die Blattebene trifft, Blatt-KS.

### Daten: `MetadataJson` des Frontends

```json
{
  "schema": "wsc.handeye.heighttest/1",
  "robot": { "robotId": "…", "name": "…", "model": "ROBOT_FRANKA" },
  "step": 2, "steps": 5,
  "plannedHeightM": 0.25,
  "expectedHeightMm": 249.6,
  "targetInBase": [x, y, z],
  "tableKey": "module-MOD-8",
  "poseFlangeCam": { "position": [], "orientation": [] },
  "actualJoints": [],
  "actualFlangeInBase": { "position": [], "orientation": [] },
  "clientTime": "ISO"
}
```

`expectedHeightMm`: Kamera laut Modell (Vorwärtskinematik der gelesenen Gelenke · `T_flansch_cam`) über der Tischplatte am Ziel.

### Frontend

- Höhen: 350, 300, 250, 200, 150 mm Kamera über der Tischplatte, von oben nach unten. Kamera senkrecht nach unten, x parallel zur Tischkante, wie das Use-Case-Foto; nicht erreichbare Höhen werden übersprungen.
- Ziel: das Fotoziel aus Pick & Place (Mitte der Tischplatte des gewählten Tisches). Ohne gewählten Tisch ist der Knopf gesperrt.
- Auswertung: Differenz `d = erwartet − gemessen` je Höhe, Ausgleichsgerade `d = a + b · h`. Angezeigt: Tabelle je Höhe, `a` (konstanter Anteil, mm) und `b` (Maßstab, %), Urteil: konstant → Hand-Auge-z, mit der Höhe wachsend → Maßstab.

### Fehlerfälle

- Methode fehlt auf dem Pi: „Pi-Software aktualisieren“, kein Fahren.
- Blatt nicht erkannt: Höhe in der Tabelle als „nicht gemessen“, Lauf geht weiter.
- Halt: Fahrt stoppt sofort, Ergebnis bis dahin bleibt stehen.
- Weniger als 3 gemessene Höhen: keine Ausgleichsgerade, nur die Tabelle.

## Offene Fragen

- Keine Kollisionsprüfung, wie beim Use-Case-Foto: der Tisch muss unter der Kamera frei sein (bei 150 mm hängt der Flansch etwa 200 mm über der Platte).

## Abweichungen vom Plan

- `MetadataJson` des Höhentests enthält zusätzlich `handEye` (`calibrationId`, `rmsPositionM`, `rmsRotationDeg`, `sampleCount` der Kalibrierung, die gerade auf dem Pi liegt), damit später klar ist, welche Kalibrierung gemessen wurde.
- Das Urteil nennt beide Anteile, wenn beide deutlich sind („Beides: …“). Grenzen: konstanter Anteil ab 3 mm, Maßstabsanteil ab 2 mm über die gefahrene Höhenspanne.
- `measure_sheet` meldet zusätzlich „Kamera nicht über dem Blatt“ und „optische Achse parallel zur Blattebene“ als Fehler.
- `VisionServerConfig` hat ein eigenes `__hash__` (das dict `image_kinds` zählt als sortierte Paare), weil die Konfiguration hashbar bleiben muss.
- Frontend: Fehlt `SaveImage` auf dem Pi, merkt das der Höhentest erst beim ersten Foto (nach der Fahrt auf die erste Höhe). Nach Halt, Fahrfehler oder Speicherfehler bleibt der Arm stehen, wo er ist; nur ein vollständiger Lauf fährt zurück in die Ausgangsstellung.
- Die gemeinsame Speicherfunktion liegt in `runner/save_use_case_image.py` (`_make_save`), `SaveUseCaseImage` und `SaveImage` registrieren beide darüber.

## Nach Abschluss

- [x] Status `fertig`, tatsächliche Schnittstellen nachgetragen
- [x] Indexzeile in `README.md`
- [x] `vision-server-interface.md` ergänzt (Abschnitt 18b, Hinweis „veraltet“ in 18a)
- [x] Tests: `tests/test_use_case_eval.py` (`MeasureSheetTest`), `tests/test_use_case_image.py` (`GeneralSaveTest`, `SaveImageOpcUaTest`); Frontend `heightTest.test.ts`, `pickPlaceTarget.test.ts`, `useSaveRawImage.test.ts`, `pickPlace.test.ts` (`photoFlangeAt`)
- [ ] Höhentest am Roboter

## Nachtrag 03.11.2026: Ergebnis Höhentest, Hand-Auge ohne Maßstab, Drehtest

### Ergebnis Höhentest (Läufe 1–4)

Lauf 4 (Ziel mittig, alle 4 Marker): Modell minus Kamera 8,8 / 9,0 / 9,4 / 10,1 / 8,9 mm bei 346–146 mm. Ausgleichsgerade: konstant 9,8 mm, Steigung −0,2 % (über 200 mm −0,5 mm). Kein Maßstabsfehler; Blatt, Tischmodell und Modul sind nachgemessen (je höchstens etwa 1 mm). Folgerung: Hand-Auge-z, die Kamera sitzt etwa 9,5 mm weiter vorn entlang ihrer Blickachse als kalibriert. Zusätzlich: Beim senkrechten Abwärtsfahren wandert die Kamera relativ zum Blatt um etwa 1,8 mm je 100 mm, Roboter-Senkrechte und Blattnormale weichen also um etwa 1° ab.

### Hand-Auge-Kalibrierung ohne Maßstabsschätzung

Die Kalibrierung schätzte den Brettmaßstab mit (1,0104). Maßstab und Lage der Kamera entlang der Blickachse sind in der Rechnung eng gekoppelt; das Brett gilt als genau (Intrinsik und Hand-Auge wurden mit verschiedenen Brettern kalibriert, beide Größen sind nachgemessen). Deshalb:

- `vision_server/handeye_session.py`: `refine_board_session(..., estimate_scale=False)` (Konstante `HAND_EYE_ESTIMATE_SCALE = False`). `boardScale` im Ergebnis bleibt 1,0.
- `tagloc/cli/calibrate_handeye.py`: Standard ohne Schätzung, `--scale` schaltet sie ein; `--no-scale` bleibt als (wirkungsloser) Schalter erlaubt.

### Drehtest

Die Kamera steht senkrecht über dem Fotoziel (250 mm über der Tischplatte) und dreht sich um ihre Blickachse: 0°, 90°, 180°, −90° (nicht erreichbare werden ausgelassen, mindestens 3 nötig). Je Stellung ein Bild über `SaveImage("drehtest", …)`.

Auswertung (Kleinste Quadrate, `R_i` = gemessene Kameradrehung im Blatt-KS, Spalten = Kameraachsen):

- Kameramitte `c_i = c0 + R_i[:, x]·Δx + R_i[:, y]·Δy` (xy-Anteile): `(Δx, Δy)` ist der seitliche Fehler der Hand-Auge-Lage im Kamera-KS (die Kameramitte beschreibt einen Kreis mit Radius |Δ|).
- Blickachse `a_i = g + R_i[:, x]·e1 + R_i[:, y]·e2` (xy-Anteile): `e` ist der Kippfehler der Kamera gegenüber dem Flansch (dreht mit), `g` die Neigung zwischen Roboter-Senkrechter und Blattnormale (bleibt).
- Höhe: Modell minus gemessen wie beim Höhentest (`Δz`).
- Gierwinkel: gemessene Kamera-x im Blatt **plus** gefahrene Drehung (die Drehung um die nach unten zeigende Kamera-z erscheint von oben gespiegelt), Mittel und Streuung (Mittel enthält auch die Tischdrehung aus Layer 2, nicht trennbar).
- Vorschlag: korrigierte Hand-Auge-Pose: Position `p + R_fc·(Δx, Δy, Δz)` (Kamera-KS → Flansch-KS), Drehung `q_fc · Drehung(−e2, e1, 0)` (Kippung im Kamera-KS).

Pi: neuer Zweck in `image_kinds`: `drehtest` → `data/drehtest/lauf_NNN/bild_NNN.*`, Schema `adp.drehtest.image/1`, Messung `sheet`. `measure_sheet` liefert zusätzlich `camera_rotation_in_sheet` (3×3, zeilenweise; Spalten = Kamera-x, -y, -z im Blatt-KS) und `camera_yaw_deg` (Winkel der Kamera-x in der Blattebene).

`MetadataJson` wie beim Höhentest, Schema `wsc.handeye.rotationtest/1`, zusätzlich `turnDeg`.

Frontend: Höhentest und Drehtest teilen sich den Fahr- und Fotoablauf (`HandEyeChecks`), Auswertung in `rotationTest.ts`.

### Umsetzung Drehtest und Hand-Auge ohne Maßstab (03.11.2026)

- Frontend (`webskillcomposition`, `messkette-protokoll`): `model/rotationTest.ts` (Auswertung), `model/useHandEyePhotoSeries.ts` (gemeinsamer Fahr- und Fotoablauf), `components/HandEyeChecks.tsx` (ersetzt `HandEyeHeightTest.tsx`, zwei Knöpfe). Ende-zu-Ende-Test: aus einer Kalibrierung mit 3/−2/9,5 mm Versatz, 0,4°/0,3° Kippung und 1,1° schiefem Blatt wird die wahre Pose auf < 0,5 mm und < 0,1° zurückgerechnet.
- Pi: Zweck `drehtest` in `image_kinds`; `measure_sheet` liefert `camera_rotation_in_sheet` und `camera_yaw_deg`; Hand-Auge-Sitzung mit `HAND_EYE_ESTIMATE_SCALE = False`; CLI `calibrate_handeye` ohne Schätzung, `--scale` schaltet sie ein.
- Tests angepasst: `test_handeye_session_refine.py` (genaues Brett, Maßstab bleibt 1,0; auch bei 1 % falschem Brett keine Schätzung), `test_handeye_dataset.py` (CLI wie der Pi ohne Schätzung, `--scale` mit Hinweis). Neu: `MeasureSheetRotationTest`, `DefaultImageKindsTest`.
- Zur Vermutung „Greifer in der Ausgangsstellung schief“: Für Höhen- und Drehtest ist die Ausgangsstellung unerheblich, die Kamera wird über die Hand-Auge-Pose senkrecht gestellt. Eine schief montierte Kamera ist kein Fehler, solange die Kalibrierung sie kennt; ein Kippfehler der Kalibrierung zeigt sich im Drehtest als mitdrehender Anteil, eine Tisch-/Wagenneigung als stehender.
