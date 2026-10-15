# Layer 2: mehrere Blickkandidaten je Schritt statt eines einzigen Fahrziels

**Status:** fertig — 15.10.2026 (WSC liest die Liste, nutzt sie aber noch nicht; an echter Hardware nicht getestet)
**Verantwortlich:** John Glanz, `Agent: Layer 2 Blickkandidaten`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Bisher veröffentlicht der Hand-Pi je Schritt genau ein Fahrziel (`flangeInBase`). Er prüft dabei nur, ob es in einer Kugel um die Basis liegt. Findet die IK in WebSkillComposition keine Stellung oder liegt der Weg in einem Hindernis, bleibt nur „nicht erreicht“.

Jetzt liefert der Pi zusätzlich eine **Rangliste gleichwertiger Blicke auf denselben Tag** (`candidates`), den besten zuerst. Die Bahnplanung auf der Roboterseite nimmt den ersten Kandidaten, den sie kollisionsfrei anfahren kann.

Die Aufteilung folgt der Literatur zu zweistufiger Lokalisierung (globale Kamera grob, Handkamera fein; siehe Plan `layer2-bahnplanung`, folgt):

- **Pi:** *was* angeschaut wird, mit welcher Güte.
- **WSC:** *wie* der Roboter hinkommt, also IK, Gelenkgrenzen, Kollision, Zwischenpunkte.

## Bereits gelesen

- `vision-server-interface.md` Abschnitt 14 (Layer-2-Lauf, `Layer2Target`)
- `arbeitsplaene/layer2-lauf-hand-pi.md`, `layer2-annaeherung.md`, `layer2-schraege-ansicht.md`
- `concept/offene_punkte.md`

## Betroffene Dateien

- `src/vision_server/layer2/plan.py`:
  - `view_target(..., azimuth_deg)`
  - `PlannedStep.candidates`
  - `plan_step(..., max_candidates)`
- `src/vision_server/layer2/run.py`: `Layer2Target.candidates`
- `tests/test_layer2_plan.py`, `tests/test_layer2_run.py`
- `doc/projektdoku/vision-server-interface.md` Abschnitt 14.2
- WSC `frontend/src/features/cell-modules/model/layer2Run.ts` (+ Test)

## Schnittstellen

### Daten / Payload

`Layer2Target` (`wsc.vision.layer2.target/1`) bekommt ein **optionales** Feld. Alle bisherigen Felder bleiben unverändert. Ältere Clients ignorieren das neue Feld, ältere Pis lassen es weg.

```json
{
  "flangeInBase": "… wie bisher, = candidates[0].flangeInBase, falls reachable",
  "candidates": [
    {
      "flangeInBase": "Pose im Basis-KS: Fahrziel dieses Kandidaten",
      "cameraInWorld": "Pose im Welt-KS: Kamera, für die Vorschau",
      "reachM": "Abstand Flansch–Basis, m",
      "tiltDeg": "Neigung aus der Senkrechten auf den Tag, Grad (0 = frontal)",
      "azimuthDeg": "Kipprichtung relativ zur Richtung Tag→Basis, Grad (0 = zur Basis hin, ±45 = seitlich)"
    }
  ]
}
```

- **Reihenfolge = Rang**, der beste zuerst. Der Pi gibt bewusst keine Zahl als „Score“ heraus. Die Güte steckt in der Reihenfolge, die Regel steht hier:
  1. Zur Basis gekippt (`azimuthDeg = 0`) vor seitlich gekippt. So ist der erste Kandidat immer das bisherige Fahrziel.
  2. Mit Luft zur Reichweite (≤ 0,9 · `reachM`) vor knapp in Reichweite.
  3. Frontaler vor schräger.
  4. Weniger seitlich vor mehr seitlich.
- Alle Kandidaten zeigen auf **denselben Tag** (`tagId` des Ziels), aus demselben Abstand (`standoff`) und genau auf die Tag-Mitte. Welcher Kandidat gefahren wurde, muss dem Pi deshalb nicht gemeldet werden. `ReportRobotPose` meldet wie bisher die **gemeldete** Flanschpose, und der Pi rechnet mit ihr.
- Nur Kandidaten in Reichweite (`reachM ≤ options.reachM`), höchstens 5. Ist der Schritt `reachable = false`, ist die Liste leer.
- Mit `maxTiltDeg = 0` gibt es genau einen Kandidaten: den frontalen.

### Python

```python
# Modul: src/vision_server/layer2/plan.py
def view_target(pose_world_tag, pose_world_base, pose_flange_cam, standoff_m,
                tilt_deg=0.0, azimuth_deg=0.0) -> ViewTarget:
    """azimuth_deg dreht die Kipprichtung um die Tag-Normale weg von der Basis."""

@dataclass(frozen=True)
class PlannedStep:
    ...
    #: Rangliste erreichbarer Blicke auf `tag_id`, der beste zuerst;
    #: `candidates[0] is target`, wenn `reachable`.
    candidates: tuple[ViewTarget, ...] = ()
```

`plan_step` wählt `target` wie bisher; das Ergebnis ist für `azimuth = 0` bitgleich. Die seitlichen Kandidaten kommen nur in die Liste.

### Fehlerfälle

- **Kein Kandidat in Reichweite:** `candidates = []`, `reachable = false`. Der Schritt wird wie bisher übersprungen.
- **Client kennt `candidates` nicht:** er fährt `flangeInBase`, wie bisher.

## Vorgehen

1. `view_target` um `azimuth_deg` erweitern.
2. `plan_step` sammelt alle Neigungen × Kipprichtungen (0°, +45°, −45°), sortiert sie nach der Regel oben und behält die besten fünf in Reichweite.
3. `run.target_json` schreibt `candidates`.
4. Abschnitt 14.2 in `vision-server-interface.md` ergänzen.
5. WSC: Typ `Layer2Candidate`, Helfer `layer2Candidates(target)` mit Rückfall auf `flangeInBase` für ältere Pis.
6. Tests in beiden Repos.

## Offene Fragen

- **`expectedTagPx` je Kandidat:** braucht `sizeM` je Tag im Lauf-Request und `focalPx` aus der Kalibrierung. Beides fehlt noch im Request, deshalb später.
- **Nutzung in WSC:** IK und Kollision je Kandidat, das ist Schritt 2 im Gesamtplan. Das Format der Bounding-Boxen / `avoidance_zones` liefert John nach.

## Abweichungen vom Plan

—

## Nach Abschluss

- Status auf `fertig`, Index aktualisiert, Abschnitt 14.2 der Schnittstellen-MD nachgezogen.
