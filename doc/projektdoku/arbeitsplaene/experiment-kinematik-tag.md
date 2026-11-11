# Experiment: Kinematik-Test mit festem AprilTag (Lage über Gelenkstellungen)

**Status:** in Arbeit — 11.11.2026 (Code fertig, Messung am Roboter steht aus)
**Verantwortlich:** Meric, `Agent: Experiment Kinematik-Test`
**Thema:** vision
**Branch:** `roboter-wiederholgenauigkeit` (ADP, dieses Repo: Auswertung, Pi-Aufruf, Plan); `wiederholgenauigkeit-apriltag` (webskillcomposition: Frontend-Panel)

## Ziel

Prüfen, ob die gemessene Lage eines festen AprilTags in der Roboterbasis von der Gelenkstellung des Roboters abhängt. Das deutet auf einen Kinematik- oder Hand-Auge-Fehler hin. Ein Tag (tag36h11, Standard 80 mm) liegt fest auf dem Tisch. Zuerst wird eine Referenz in einer Stellung gemessen (mehrere Bilder). Danach führt der Bediener den Arm von Hand in andere Gelenkstellungen, in denen die Kamera ungefähr gleich auf den Tag schaut, und nimmt je Stellung mehrere Bilder auf. Ergebnis ist eine Messreihe je Pose für den Bericht.

## Bereits gelesen

- `CLAUDE.md`, `arbeitsplaene/README.md`, `arbeitsplaene/_vorlage.md`
- `arbeitsplaene/experiment-tag-anfahren.md` (Formatvorlage, Experiment am selben Tag)
- `arbeitsplaene/roboter-wiederholgenauigkeit.md` (Aufnahmeformat, Methode `SaveRepeatabilityImage`, Fehlercodes)
- `doc/auswertung/distanztest_20261110.md` (Bezug: unsichtbares Absacken in der Ruhestellung)

## Betroffene Dateien

Dieses Repo (ADP):

- `doc/projektdoku/arbeitsplaene/experiment-kinematik-tag.md` (neu, dieser Plan)
- `doc/projektdoku/arbeitsplaene/README.md` (eine Zeile im Index)
- `src/repeatability_eval/kinematics.py` (neu): Auswertung einer Session
- `tools/wiederholgenauigkeit/kinematik.py` (neu): CLI
- `tests/test_repeatability_kinematics.py` (neu, 16 Tests)

webskillcomposition (Frontend, `frontend/src/features/cell-modules/`):

- `model/kinematicsTest.ts` (neu): reine Logik (`kinematicsSessionName`, `evaluateSeries`, `evaluatePose`, `summarizePoses`, `messreiheCsv`)
- `model/useKinematicsTest.ts` (neu): Hook
- `components/KinematicsTestPanel.tsx` (neu): Panel „Kinematik-Test“ in der Autolocate-Spalte
- `model/repeatabilityPi.ts` (neu): aus `useRepeatabilityRun.ts` herausgezogene Pi-Aufrufe, ohne Verhaltensänderung
- `model/repeatabilityRecord.ts`: optionales Feld `extra`
- `model/kinematicsTest.test.ts` (neu, 16 Tests)

## Schnittstellen

### OPC UA

Keine Änderung. Genutzt wird die vorhandene Methode `SaveRepeatabilityImage` auf dem Hand-Pi. Die Pi-Software bleibt unverändert.

| Element | NodeId / BrowsePath | Typ | Richtung | Bedeutung |
| --- | --- | --- | --- | --- |
| `SaveRepeatabilityImage` | `ns=<eigener Namespace>;s=<VisionSystemName>.SaveRepeatabilityImage` | Methode | Aufruf | Speichert das aktuelle Kamerabild einer Phase als PNG plus JSON und liefert die Live-Messung |

**Eingangsargumente** (alle `String`):

| Name | Format |
| --- | --- |
| `Session` | `YYYY-MM-DD_HHMMSS_kinematik`, Muster `^[0-9A-Za-z_-]{1,64}$` |
| `Phase` | `rauschen` (Referenzbilder) oder `zyklen_hand` (Posen) |
| `MetadataJson` | JSON-Text des Clients, Schema `wsc.repeatability.record/1` (siehe Daten) |

**Ausgangsargumente:** `FileName` (String, Pfad des PNG, relativ zum Repo; bei Fehler leer), `ResultJson` (String, Live-Messung; bei Fehler leer), `Error` (Int32, `VisionErrorCode`).

**Fehlercodes:** 0 `OK`, 1 `INVALID_STATE` (kein frisches Kamerabild), 2 `INVALID_ARGUMENT` (Session oder Phase ungültig), 3 `BUSY` (ein Job läuft), 6 `INTERNAL` (PNG- oder Schreibfehler).

### Python

```python
# Modul: src/repeatability_eval/kinematics.py
def measure_session(session_dir: Path, tag_id: int | None = None,
                    tag_size_m: float = 0.08, calibration=None,
                    hand_eye=None) -> SessionMeasurement:
    """Wertet einen Session-Ordner aus. Je Bild T_base_tag = T_base_flange · T_flange_cam · T_cam_tag
    (Subpixel-Ecken; Intrinsik und Hand-Auge aus der Record-JSON oder per Option).
    Bilder mit movedDuringCapture oder ohne Erkennung gehen nicht ins Mittel.
    Wirft ValueError, wenn keine Bilder der Phasen rauschen/zyklen_hand oder keine Detektor-Einstellungen vorliegen."""

def summarize_poses(images) -> dict: ...
# Weitere Funktionen des Moduls: pose_table, render_report, plot_deltas, csv_rows
```

### Daten / Payload

Record `wsc.repeatability.record/1` mit zusätzlichen Feldern auf oberster Ebene:

```json
{
  "test": "kinematik",
  "poseIndex": "0 = Referenz, 1, 2, …",
  "poseLabel": "Referenz | Pose n",
  "target": { "type": "apriltag", "tagId": "int", "tagSizeM": "float" },
  "taught": { "P": "Referenzstellung", "V": null },
  "start": null,
  "actual": {
    "joints": "Gelenkwinkel",
    "flangeInBase": "Flanschpose in der Basis",
    "settled": "bool",
    "movedDuringCapture": "bool",
    "jointSpreadRad": "float"
  }
}
```

`actual.*` wie beim Wiederholgenauigkeitstest. Der Pi rechnet `tagInBaseM` aus `actual.flangeInBase`.

**Ablage auf dem Pi:** `data/rohbilder/roboterwiederholgenauigkeit/<session>/`

- Referenzbilder: Phase `rauschen`, Ordner `02_rauschen`, `cycle` = laufende Bildnummer.
- Posen: Phase `zyklen_hand`, Ordner `04_zyklen_hand`, `cycle` = Posennummer (1, 2, …), `burstIndex` / `burstCount` = Bild innerhalb der Pose.

**Auswertung (ADP):** Ausgabe im Session-Ordner, Standard:

- `kinematik_bericht.md`: Kopf (Session, Tag, Datum, Kalibrier- und Hand-Auge-ID), Messreihe je Pose, Δ gegen Referenz in Position und Winkel (inkl. Gieren um Basis-z), Zusammenfassung (RMS und max |Δ|, Spannweite je Achse), Pearson-Korrelation Gelenk-Δ gegen Δ ab 4 Posen.
- `kinematik_posen.csv`: eine Zeile je Pose, Semikolon, Dezimalkomma.
- `kinematik_posen.png`: Δx/Δy/Δz/|Δ| je Pose.

CLI:

```
PYTHONPATH=src .venv/bin/python tools/wiederholgenauigkeit/kinematik.py <session_ordner> \
  [--tag-id N] [--tag-size-mm 80] [--out] [--csv-posen] [--plot] [--csv] \
  [--dezimalpunkt] [--stdout] [--calibration cam.json] [--handeye he.json]
```

**CSV des Frontend-Panels:** Semikolon, Dezimalkomma. Spalten: Pose-Nr, Label, Zeit, Aufnahmen gültig/gesamt, q1..q7 [Grad], Flansch x/y/z [mm], Tag Basis x/y/z [mm], Δx/Δy/Δz/|Δ| [mm], Streuung [µm], bewegt, Tag Kamera x/y/z [mm]. Live-Werte nur Position, der Pi liefert keine Orientierung zurück.

**Session-Name im Frontend:** `YYYY-MM-DD_HHMMSS_kinematik`. Einstellungen in localStorage `wsc.kinematicsTest.settings/1`, Session in `wsc.kinematicsTest.session/1`.

### Netz / Betrieb

Keine neuen Ports, Services oder systemd-Units. Die Frontend-Seite nutzt den bestehenden Pi-Zugriff.

### Fehlerfälle

- Pi nicht verbunden oder alte Software: Buttons gesperrt, Hinweis im Panel.
- Drei Pi-Fehler in Folge: die Serie bricht ab.
- Stopp-Knopf: Serie wird angehalten.
- Referenz ohne Tag in der Basis: Fehlermeldung, keine Referenz wird gesetzt.
- Arm bewegt sich beim Aufnehmen: Pose wird als „bewegt“ markiert.
- Auswertung: `ValueError` bei fehlenden Bildern oder Detektor-Einstellungen.

Der Roboter fährt in diesem Panel nie; es werden nur Gelenkwinkel gelesen.

## Vorgehen

1. Roboter eingeschaltet lassen, nicht in Ruhestellung oder Bremsen (dort etwa 10 mm unsichtbares Absacken, siehe `doc/auswertung/distanztest_20261110.md`).
2. Panel „Kinematik-Test“ (unter dem Wiederholgenauigkeits-Panel, Autolocate-Spalte): „Neue Session“.
3. Tag-ID (leer = einziger Tag) und Größe einstellen.
4. Arm so stellen, dass der Tag gut im Bild ist, dann „Referenz hier messen“ (Standard 10 Bilder).
5. Arm von Hand führen und loslassen, dann „Pose hier aufnehmen“ (2 s Beruhigung, Standard 5 Bilder). Das Panel zeigt Δx/Δy/Δz/|Δ| zur Referenz sowie die Abstands- und seitliche Abweichung der Kamerasicht auf den Tag.
6. Schritt 5 wiederholen, sinnvoll mit mindestens 4 Posen (dann rechnet die Auswertung zusätzlich die Korrelation mit den Gelenken).
7. „Messreihe als CSV“ exportieren, dann `tools/wiederholgenauigkeit/kinematik.py` auf dem Session-Ordner ausführen.

Ergebnis kommt nach `doc/auswertung/`.

## Offene Fragen

- Messung am Roboter steht aus.
- Die Orientierung eines einzelnen 80-mm-Tags ist schwach bestimmt (Neigung etwa 2,5° in der Ausgangsstellung). Winkel-Δ deshalb vorsichtig deuten; Positions-Δ ist die Hauptgröße.
- Smoke-Test der Auswertung auf der vorhandenen Session `2026-11-10_153201` (Tag 0, nur Referenz): Tag-Mitte in der Basis 421,83 / 16,20 / −2,34 mm, Streuung 3D 52,6 µm, Abstand 350,9 mm. Passt zu `doc/auswertung/distanztest_20261110.md` (421,9 / 16,3 / −2,2 mm). Dieser Test ersetzt nicht die Messung mit Posen.

## Abweichungen vom Plan

Noch keine. Wird nach der Messung am Roboter gefüllt.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/` übernommen (nicht nur hier im Plan stehen lassen).
