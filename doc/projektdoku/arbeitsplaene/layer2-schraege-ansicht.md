# Layer 2: schräger Blick auf Tags, die frontal außer Reichweite liegen

**Status:** fertig — 13.10.2026 (an echter Hardware nicht getestet)
**Verantwortlich:** John Glanz, `Agent: Layer 2 schräge Ansicht`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Der Planer hat die Handkamera bisher immer **frontal** vor einen Tag gesetzt, also bei einem Welttag am Boden senkrecht darüber. Der FR3 steht aber auf einem Wagen, etwa 1 m über dem Boden, und die Welttags liegen 0,9–1 m neben der Basis. Dort lag schon die Annäherung (0,6 m frontal) außer Reichweite, und der Anker scheiterte.

Jetzt kippt die Kamera um die Tag-Mitte zur Basis hin, in Stufen von 15° bis `maxTiltDeg` (Standard 60°). Abstand und Blick auf die Tag-Mitte bleiben dabei gleich. Genommen wird der frontalste Blick, der mit etwas Luft in Reichweite liegt.

## Bereits gelesen

- `arbeitsplaene/layer2-lauf-hand-pi.md` (Rechnung `view_target`, Optionen)
- `arbeitsplaene/layer2-annaeherung.md` (Annäherungsschritt, `approachStandoffM`)
- `vision-server-interface.md` Abschnitt 14

## Betroffene Dateien

- `src/vision_server/layer2/plan.py`: `view_target(..., tilt_deg)`, `plan_step(..., max_tilt_deg)`, Option `maxTiltDeg`
- `src/vision_server/layer2/run.py`: Neuplanung reicht `max_tilt_deg` durch; `Layer2Target.tiltDeg`
- `tests/test_layer2_plan.py`: `TiltedViewTest`

## Schnittstellen

### Daten

**`RunJson`**, `options` zusätzlich:

```json
"options": { "standoffM": 0.3, "approachStandoffM": 0.6, "reachM": 0.85, "maxTiltDeg": 60 }
```

- `maxTiltDeg` liegt zwischen 0 und 80. `0` heißt immer frontal, das alte Verhalten. Ein Wert außerhalb ergibt `INVALID_ARGUMENT`.

**`Layer2Target`**, zusätzlich:
- `"tiltDeg": 45.0` sagt, wie schräg die Kamera auf den Tag schaut. `0` heißt frontal.

### Python

```python
# src/vision_server/layer2/plan.py
def view_target(pose_world_tag, pose_world_base, pose_flange_cam, standoff_m, tilt_deg=0.0) -> ViewTarget
def plan_step(spec, pose_world_base, pose_flange_cam, standoff_m, reach_m, max_tilt_deg=0.0) -> PlannedStep
ViewTarget.tilt_deg: float
RunRequest.max_tilt_deg: float = 60.0
```

### Rechnung

```
T_tag_cam(tilt) = Rot(a, tilt) · T_tag_cam(frontal),   a = z × (Basis im Tag-Frame, in die Tag-Ebene gelegt)
```

Die Drehung geht um die Tag-Mitte. Die Kamera bleibt also `standoff_m` vom Tag entfernt und schaut auf seine Mitte.

Wahl des Blicks:
1. Der kleinste Kippwinkel mit `reach ≤ 0,9 · reachM`.
2. Gibt es keinen, der kleinste mit `reach ≤ reachM`.
3. Sonst der Blick mit der kleinsten Reichweite. Er gilt dann als `reachable: false` und wird übersprungen.

### Fehlerfälle

Wie bisher. Ein Tag, der auch schräg nicht erreichbar ist, bleibt `skipped` mit „außer Reichweite“.

## Offene Fragen

- Die Reichweite wird vom Basis-Ursprung aus gemessen, nicht von der Schulter. Ob der FR3 eine Pose mit der verlangten Orientierung tatsächlich erreicht, prüft erst die IK im Frontend (Geist-Vorschau).

## Nach Abschluss

- [x] Status `fertig`.
- [x] Indexzeile in `README.md`.
- [x] `vision-server-interface.md` Abschnitt 14.1.
- [x] Testlauf (siehe Commit).
