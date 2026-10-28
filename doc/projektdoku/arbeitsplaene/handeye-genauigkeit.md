# Hand-Auge-Kalibrierung: fester Versatz beim Anfahren beseitigen

**Status:** in Arbeit — 28.10.2026
**Verantwortlich:** Meric, `Agent: Hand-Auge-Genauigkeit`
**Thema:** apriltag
**Branch:** `apriltag` (Pi), `handeye-genauigkeit` in webskillcomposition (Frontend)

## Ziel

Beim Anfahren von Tags und Zielen trifft der FR3 systematisch daneben: ein fester Versatz, der bei jedem Anfahren gleich ist (Größe noch nicht gemessen; Ziel ≤ 1 mm). Die Anfahrrechnung `T_base_tag = F · X · C` (`tagServo.ts:100`) nutzt Flanschpose `F` aus der URDF-FK, Hand-Auge `X = T_flange_cam` vom Hand-Pi und Tag-Messung `C` aus `MeasureTags`. Ein fester Versatz kommt aus Fehlern, die alle Ansichten gleich verschieben. Die heutige Gütezahl (Streuung der Brettlage) zeigt sie deshalb nicht.

Hinterher soll die Kalibrierung den Versatz sichtbar machen und ihn klein halten: `X` und der Brettmaßstab werden gemeinsam gegen die Pixelmessung verfeinert, der Tag-Pfad misst genauer, und die Kalibrierung meldet einen echten Vorhersagefehler statt nur einer Konsistenzzahl. Vorher ging das nicht, weil die Gütezahl einen festen Versatz nicht erkennt.

Rahmen (Nutzer, 28.10.2026): Pi läuft auf `apriltag`. Brett = 7×8 Felder, also **6×7 Innenecken**, 23 mm. Keine neue Hardware, Belichtung bleibt, reine Software-Optimierung.

## Bereits gelesen

Ausgewertet für diesen Plan (CLAUDE.md, Abschnitt „Bevor du anfängst“):

- `CLAUDE.md` (Repo-Wurzel): Regeln, Arbeitsplan-Pflicht, Schnittstellenteil
- `.claude/skills/projektdoku/SKILL.md`: Ablauf Lesen, Plan, Umsetzung, Commit
- `doc/projektdoku/arbeitsplaene/_vorlage.md`: Format des Plans
- `doc/projektdoku/arbeitsplaene/README.md`: Doppelarbeits-Check; Index mit Plänen zu Hand-Auge und Brett
- `doc/projektdoku/arbeitsplaene/handeye-schachbrett-automatik.md`: Stilbeispiel, Board-Frame; nennt 5×6 Innenecken, das Brett hat 6×7 (siehe Offene Fragen)
- `doc/projektdoku/arbeitsplaene/hand-auge-brett-zentrieren.md`: Stilbeispiel, `MeasureBoard`, `reprojectionErrorPx`
- Freigegebener Plan `/home/meric/.claude/plans/glittery-riding-hartmanis.md`: Befunde, Umsetzung A/B, Verifikation

Noch nicht ausgewertet (vor der Umsetzung nachzulesen): `doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`, `doc/projektdoku/vision-server-interface.md` (Abschnitt 15), `doc/projektdoku/apriltag-lokalisierung.md`, `doc/projektdoku/altlasten.md`.

## Betroffene Dateien

Diese Liste reserviert die Dateien. Parallele Änderungen daran vorher absprechen.

Hand-Pi (Repo ADP_flexible_Roboterzelle, Branch `apriltag`):

- `src/vision_server/handeye_session.py`: Eckpunkte, Intrinsik und Gelenke je Aufnahme speichern; `intrinsicsSource`; Sitzungsdatensatz; `finish`-Prüfung auf Drehachsen
- `src/tagloc/handeye.py`: neue Funktion `refine_hand_eye` (Levenberg-Marquardt in numpy, Huber, LOO)
- `src/tagloc/cli/calibrate_handeye.py`: `--session <dir>`, Offline-Nachrechnung
- `src/tagloc/pose.py`: IPPE_SQUARE-Lösungen mit `cv2.solvePnPRefineLM` verfeinern (Tag-Pfad)
- `src/tagloc/detector.py`: Subpixel-Ecken für `cam_flange` standardmäßig an (Wahl per Messung im Test)
- `data/handeye/sessions/<UTC-Zeit>/`: Sitzungsdatensätze (JSON + ein Bild je Aufnahme)
- `tests/test_handeye_refine.py` (neu), `tests/test_tag_pipeline.py`, `tests/test_handeye_session.py`

Frontend (Repo webskillcomposition, Branch `handeye-genauigkeit` von `Ungetestet`):

- `robotDrive.ts`: Ankunft nach 3 ruhigen Polls, Gelenke nach der Aufnahme nachlesen
- `HandEyeAutoPanel.tsx`: Stillstand-Logik, Prüfung „Brettursprung anfahren“
- `handEye.ts`: Parser für die neuen Felder
- `HandEyeCalibrationWindow.tsx`: Anzeige der Gütezahlen und Warnhinweise

Doku (nur im Umsetzungsschritt C, nicht in diesem Plan-Commit):

- `doc/projektdoku/arbeitsplaene/handeye-schachbrett-automatik.md`: Brett korrigieren (6×7 Innenecken)
- `doc/projektdoku/vision-server-interface.md`: Abschnitt 15, neue Status- und Ergebnisfelder

## Schnittstellen

**Geplant, wird nach Umsetzung mit den tatsächlichen Schnittstellen nachgetragen.**

Dieser Abschnitt beschreibt den Entwurf. Parameternamen und Typen der Python-Funktionen sind Vorschlag und können sich bei der Umsetzung ändern. Abweichungen werden im Abschnitt „Abweichungen vom Plan“ vermerkt und hier korrigiert.

### OPC UA

Keine neuen Knoten oder Methoden. Erweitert wird der `HandEyeStatus` (Schema `wsc.vision.handeye.status/1`) um die folgenden Felder. Die übrigen Methoden, Codes und Sperren bleiben wie in `handeye-ueber-opcua.md` beschrieben.

- `FinishHandEyeCalibration` liefert `INVALID_STATE`, wenn weniger als 3 Drehachsen vorliegen (`rotation_diversity`).

### Python

```python
# src/tagloc/handeye.py  (geplant)
def refine_hand_eye(
    samples: list[HandEyeSample],   # je Aufnahme: Brettecken (N, 2), Gelenk-/Flanschpose F_i
    intrinsics: CameraIntrinsics,   # verwendete Intrinsik (entzerrte Punkte)
    initial_X: Pose,                # Start: Ergebnis von solve_hand_eye_robust
    *,
    estimate_scale: bool = True,    # Brettmaßstab s mitschätzen
    huber_px: float = 1.0,          # Huber-Schwelle für Reprojektion
) -> RefineResult
# RefineResult: X (Pose), T_base_board (Pose), boardScale (float),
#   refinedReprojectionRmsPx (float), looErrorPx (list[float]),
#   looErrorM (list[float]), looErrorRmsPx (float), looErrorRmsM (float),
#   warnings (list[str])
# Geworfen: ValueError bei weniger als 3 Aufnahmen oder fehlender Drehvielfalt.
```

```python
# src/vision_server/handeye_session.py  (geplant)
HandEyeSession.finish() -> HandEyeResult
# Geworfen: InvalidState, wenn weniger als 3 Drehachsen vorliegen.
```

```python
# src/tagloc/cli/calibrate_handeye.py  (geplant)
# Aufruf: python -m tagloc.cli.calibrate_handeye --session data/handeye/sessions/<UTC-Zeit>/
# Liest session.json, rechnet X mit refine_hand_eye neu und schreibt das Ergebnis in den Sitzungsordner.
```

### Daten / Payload

**`HandEyeStatus`**, Schema `wsc.vision.handeye.status/1`, neue Felder (geplant):

```json
{
  "intrinsicsSource": "calibrated | realsense_factory | ...",
  "result": {
    "refinedReprojectionRmsPx": 0.0,
    "boardScale": 1.00,
    "looErrorRmsPx": 0.0,
    "looErrorRmsM": 0.0,
    "targetInBase": {
      "position": [0.0, 0.0, 0.0],
      "orientation": [0.0, 0.0, 0.0, 1.0]
    },
    "warnings": ["..."]
  },
  "samples": [
    { "looErrorPx": 0.0, "looErrorM": 0.0 }
  ]
}
```

- `intrinsicsSource`: Herkunft der verwendeten Intrinsik (top-level).
- `refinedReprojectionRmsPx`: RMS der gemeinsamen Verfeinerung über alle Brettecken.
- `boardScale`: geschätzter Maßstab des Bretts; 1,00 bedeutet nominal 23 mm.
- `looErrorRmsPx`, `looErrorRmsM`: RMS des Leave-one-out-Vorhersagefehlers über alle Aufnahmen.
- `targetInBase`: Ursprung des Bretts im Roboter-Basis-KS; `orientation` als Quaternion `xyzw`.
- `warnings`: Hinweise als Text, z. B. bei `realsense_factory` oder `|boardScale − 1| > 0,3 %`.
- `samples[i].looErrorPx`, `samples[i].looErrorM`: Vorhersagefehler der Aufnahme `i`, wenn `i` aus der Verfeinerung herausgenommen wird.

**Sitzungsdatensatz** `data/handeye/sessions/<UTC-Zeit>/session.json` (geplant): Eckpunkte je Aufnahme, verwendete Intrinsik, Gelenkwerte (nachgelesen), Flanschpose, Bildname je Aufnahme. Ein Bild je Aufnahme liegt im selben Ordner.

### Netz / Betrieb

Keine neuen Ports, Services oder mDNS-Namen. Der Offline-Lauf läuft als CLI auf dem Hand-Pi oder auf einem Entwicklungsrechner mit demselben Repo-Stand.

### Fehlerfälle

| Fall | Verhalten (geplant) |
| --- | --- |
| weniger als 3 Drehachsen bei `Finish` | `INVALID_STATE`, Sitzung bleibt offen |
| Intrinsik fehlt, stiller Rückfall auf Werks-Intrinsik | `intrinsicsSource: "realsense_factory"` plus Warnung |
| `|boardScale − 1| > 0,3 %` | Warnung „Brett nachmessen / Intrinsik prüfen“ |
| Ausreißer-Aufnahme | Huber-Gewichtung hält die Schätzung; LOO-Fehler dieser Aufnahme ist sichtbar |
| weniger als 3 Aufnahmen beim Verfeinern | `ValueError` in `refine_hand_eye` |
| Brett nicht vollständig im Bild (Aufnahme) | Aufnahme verworfen, wie bisher; Sitzung läuft weiter |
| Gelenke nach Aufnahme weichen ab | Aufnahme verworfen und wiederholt (Frontend) |

## Vorgehen

Umsetzung in zwei Teilen, parallel bearbeitet, je von einem Umsetzungsagenten. Tests schreibt und prüft der Hauptagent.

**A — Hand-Pi (Branch `apriltag`):**

1. Eckpunkte je Aufnahme speichern (`HandEyeSession.capture`): Brettecken als Mittel über 5 frische Frames, verwendete Intrinsik. Sitzung als Datensatz nach `data/handeye/sessions/<UTC-Zeit>/`.
2. Gemeinsame Verfeinerung `refine_hand_eye` in `src/tagloc/handeye.py`: Start = Ergebnis von `solve_hand_eye_robust`; Levenberg-Marquardt in numpy (kein scipy auf dem Pi) über `X` (6), `T_base_board` (6) und optional Maßstab `s` (1). Residuen: Reprojektion aller Brettecken aller Aufnahmen in Pixeln, ohne Verzerrung auf entzerrten Punkten wie in `boards.py`. Huber-Gewichtung. Wiederverwendet werden `compose`, `invert`, `rvec_from_rotation`, `average_poses` aus `src/tagloc/geometry.py`.
3. Leave-one-out-Vorhersagefehler: `X` ohne Aufnahme `i` verfeinern, Ecken in `i` vorhersagen → `looErrorPx` und `looErrorM` je Aufnahme, RMS im Ergebnis.
4. Status und Prüfungen: `intrinsicsSource` im Status; Hinweis bei `realsense_factory`; Hinweis bei `|boardScale − 1| > 0,3 %`; `finish` verweigert bei weniger als 3 Drehachsen.
5. Tag-Pfad: beide IPPE_SQUARE-Lösungen in `src/tagloc/pose.py` mit `cv2.solvePnPRefineLM` verfeinern, die kleinere Reprojektion gewinnt (Muster aus `boards.py`, `estimate_board_pose`). Für `cam_flange` Subpixel-Ecken standardmäßig an (`detector.py`).

**B — Frontend (webskillcomposition, Branch `handeye-genauigkeit`):**

1. Stillstand: Ankunft nach 3 ruhigen Polls (`robotDrive.ts`, `HandEyeAutoPanel.tsx`). `settledJoints` verwirft Posen bei Streuung über 0,0002 rad. Nach `CaptureHandEyeSample` Gelenke nachlesen; bei Abweichung Aufnahme verwerfen und wiederholen.
2. Anzeige: `looErrorMm`-Ersatz `looErrorRmsM`, `refinedReprojectionRmsPx`, `boardScale`, `intrinsicsSource` mit Warnhinweisen (`handEye.ts`, `HandEyeCalibrationWindow.tsx`).
3. Prüfung „Brettursprung anfahren“ in `HandEyeAutoPanel`: nach `Finish` 10 mm über den `targetInBase`-Ursprung (Brett-Normale) fahren, über bestehendes `driver.moveFlangeTo` / `planMove`. Der Bediener misst den Versatz. Damit lässt sich der Hand-Auge-Fehler vom Tag-Messfehler trennen.

**Befunde, die den Plan begründen:**

1. Translation aus fixer Rotation (`solve_hand_eye`): Park-Martin für R, danach ungewichtetes `lstsq` für t. Ein Rotationsfehler von 0,1° ergibt etwa 0,5–1 mm Translationsfehler. Es gibt keine gemeinsame Verfeinerung gegen die Pixelmessung.
2. Maßstab nicht geprüft: Feldgröße des Bretts (23 mm nominal) und Intrinsik (noch vom alten Brett 7×9/22 mm; stiller Rückfall auf Werks-Intrinsik möglich) skalieren alle `t_C` gleich und erzeugen einen Bias in `t_X`. Mit bekannten Roboter-Translationen ist der Maßstab beobachtbar und kann mitgeschätzt werden.
3. Tag-Pfad unverfeinert: `CORNER_REFINE_NONE` (`profiles.py`, `subpixel_corner_refinement=False`), IPPE_SQUARE ohne `solvePnPRefineLM`, erste Lösung (`src/tagloc/pose.py`). Der Brett-Pfad macht es bereits richtig.
4. Gütezahl ist nicht Genauigkeit: Konsistenz statt Vorhersagefehler; kein Wert, der einen Versatz zeigt; `intrinsicsSource` fehlt im Status.
5. Kleinere Punkte: Stillstand = 1 ruhiger Poll (`robotDrive.ts:343`); Gelenke nach der Aufnahme nicht nachgelesen; `Finish` erzwingt keine 3 Drehachsen; `handeye-schachbrett-automatik.md` nennt falsches Brett (5×6 Innenecken).

## Verifikation

**Pi:**

- `tests/test_handeye_refine.py` (neu): synthetische Ecken mit 0,3 px Rauschen → verfeinertes `X` genauer als Park-Martin. Eingebauter Maßstabsfehler von 1 % → `boardScale` ≈ 1,01, und der Translationsbias von `X` verschwindet. Ausreißer-Aufnahme → Huber hält. LOO-Fehler plausibel.
- `tests/test_tag_pipeline.py`: gerenderter Tag mit LM genauer als ohne.
- `tests/test_handeye_session.py`: weniger als 3 Achsen wird verweigert; `intrinsicsSource` im Status.
- Ausführung: `PYTHONPATH=src python -m unittest discover -s tests -t .`

**Frontend:**

- vitest für Stillstand, Nachlesen der Gelenke und den Status-Parser.
- `npm test` und `npx tsc --noEmit`.

**Am Aufbau (Nutzer):**

- Kalibrierfahrt → `looErrorRmsM` und `boardScale` ablesen.
- „Brettursprung anfahren“ und Tag-Anfahren vorher und nachher messen. Ziel: Versatz ≤ 1 mm.

## Offene Fragen

- **Messung am Aufbau steht aus.** Ob der feste Versatz nach der Umsetzung unter 1 mm liegt, ist noch nicht gemessen.
- Plan-Abschnitt nannte `looErrorMm`; dieser Plan verwendet nach Vorgabe `looErrorM` bzw. `looErrorRmsM` (Meter). Bei der Umsetzung entsprechend angleichen.
- `handeye-schachbrett-automatik.md` nennt 5×6 Innenecken; das Brett hat 6×7 Innenecken (7×8 Felder). Korrektur gehört in den Umsetzungsschritt C.
- Die Fach-MD `vision-server-interface.md` (Abschnitt 15) und `handeye-ueber-opcua.md` sind vor der Umsetzung nachzulesen, damit die neuen Felder dort passend ergänzt werden.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/projektdoku/` übernommen (nicht nur hier im Plan stehen lassen).
