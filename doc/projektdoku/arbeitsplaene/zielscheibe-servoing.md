# Würfel über der Zielscheibe per Visual Servoing ablegen

**Status:** in Arbeit — 03.11.2026
**Verantwortlich:** Meric, `Agent: Zielscheiben-Servoing beim Ablegen`
**Thema:** vision
**Branch:** `zielscheibe-servoing` (dieses Repo und webskillcomposition)

## Ziel

Use Case 1 legt den Würfel heute rein nach Plan ab (Tischpose aus Layer 1/2, Hand-Auge,
Greifgeometrie). Neu: steht der Arm mit dem Würfel in der Schwebehöhe über dem Ziel, übernimmt
Visual Servoing. Die Handkamera misst die Zielscheibe über ihre ArUco-Blattmarker (IDs 0–3),
der Flansch wird in x/y und um die Senkrechte korrigiert, bis die Würfelmitte (aus
`objectInFlange`) über der Scheibenmitte steht und die Würfelkanten parallel zu den
Scheibenachsen liegen. Danach senkrecht absetzen mit dieser Korrektur.

## Bereits gelesen

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/use-case-messung.md` (Zielscheibe, Messverfahren)
- `doc/projektdoku/arbeitsplaene/layer2-measuretags.md` (Muster für Messmethoden)
- `doc/projektdoku/vision-server-interface.md` §17, §18a

## Betroffene Dateien

Dieses Repo:
- `src/use_case_eval/measure.py` — vorhandenes `measure_sheet` (Blattpose ohne Würfel) wird genutzt
- `src/vision_server/measuretarget.py` (neu) — Anfrage/Ergebnis, Mittelung über Bilder
- `src/vision_server/detection/apriltag/target.py` (neu) — Mixin: frische Bilder messen
- `src/vision_server/detection/apriltag/__init__.py` — Mixin einhängen
- `src/vision_server/runner/measure_target.py` (neu), `runner/install.py` — OPC-UA-Methode
- `tests/test_measuretarget.py` (neu)

webskillcomposition (`frontend/src/features/`):
- `opcua-server/config/piServers.ts` — Knoten `MeasureTarget`
- `cell-modules/model/measureTargetCall.ts` (neu), `cell-modules/model/targetServo.ts` (neu)
- `cell-modules/model/usePickPlaceRun.ts` — Servoing vor dem Absetzen
- `cell-modules/model/useCaseRecord.ts` — Protokoll `targetServo`

## Schnittstellen

### OPC UA (nur Hand-Pi)

| Element | Signatur |
| --- | --- |
| `MeasureTarget` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)`, am Vision-System (`ns=<own_idx>;s=<vision_system_name>.MeasureTarget`) |

`Error`: `0` gemessen (auch „nicht gefunden“, dann `found: false`), `2` ungültige Anfrage,
`3` BUSY (Job oder Kalibrierung), `5` DETECTION_FAILED (kein frisches Bild).
Antwortet erst nach der Messung; nur Bilder, die nach dem Aufruf eintreffen.

### Daten

Anfrage `wsc.vision.measuretarget.request/1`: `{ "frames": 3 }` (1–10, Standard 3; leer = Standard).

Ergebnis `wsc.vision.measuretarget.result/1`:

```json
{
  "schema": "wsc.vision.measuretarget.result/1",
  "capturedAt": "ISO", "capturedUntil": "ISO",
  "frameId": "cam_flange", "imageSize": [640, 480],
  "intrinsicsSource": "calibration", "calibrationId": "...",
  "framesRequested": 3, "framesUsed": 3,
  "found": true,
  "poseInCamera": { "position": [x, y, z], "orientation": [qx, qy, qz, qw] },
  "reprojectionRmsPx": 0.4,
  "sheetMarkers": [0, 1, 2, 3],
  "frames": [
    { "capturedAt": "ISO", "found": true, "sheetMarkers": [0, 1, 3],
      "poseInCamera": { ... }, "reprojectionRmsPx": 0.4, "reason": "" }
  ],
  "error": null
}
```

`poseInCamera` = `T_cam_sheet` in **Metern**, OpenCV-Kamerarahmen (+Z aus der Linse).
Blattsystem wie in `use_case_eval/target.py`: Ursprung Scheibenmitte auf dem Blatt,
x/y in der Blattebene (Marker 1 bei +x/+y), +z vom Blatt zur Kamera. Mittel über alle Bilder
mit gefundener Pose (Position arithmetisch, Orientierung gemittelt). Mindestens 2 Blattmarker
je Bild (`MIN_SHEET_MARKERS`), sonst zählt das Bild als nicht gefunden.

### Frontend: Ablauf beim Ablegen

Vor dem Schritt `drop` (Arm steht über dem Ziel, Würfel 10 cm über der Platte), bis zu 5 Runden:
1. `MeasureTarget` → `T_base_sheet = FK(Gelenke) · T_flange_cam · T_cam_sheet`.
2. Würfelmitte = `FK · objectInFlange`; Versatz in der Basis-xy-Ebene zur Scheibenmitte,
   Drehfehler = Gierwinkel der Scheibe minus Gierwinkel des Würfels, auf ±45° gefaltet.
3. Fertig, wenn Versatz < 1 mm und Drehung < 0,3°; sonst Flansch um die Senkrechte durch die
   Würfelmitte drehen und verschieben, IK, langsam fahren.
Die gesamte Korrektur (Starrkörper in der Basis) wird auf `drop`, `drop-lift` und `photo`
übertragen. Ohne `MeasureTarget` oder Hand-Auge-Kalibrierung: Ablage wie bisher.

### Fehlerfälle

- Scheibe nicht gefunden / Methode fehlt: Hinweis, Ablage nach Plan (kein Abbruch).
- Korrektur > 30 mm oder > 20°: unplausibel, nicht übernommen, Ablage nach Plan mit Hinweis.
- Korrigierte Pose nicht erreichbar: Ablage mit der letzten erreichbaren Korrektur.

## Vorgehen

1. Pi: `measuretarget.py` (auf `measure_sheet`), Mixin, Methode, Tests.
2. Frontend: Knoten, Aufruf, `targetServo.ts`, Einbau in den Lauf, Protokoll, Tests.
3. Nachtrag in `vision-server-interface.md`.

## Offene Fragen

- Sieht die Handkamera in Schwebehöhe mindestens 2 Blattmarker neben Greifer und Würfel?
  Falls nicht: Schwebehöhe für das Servoing anheben.

## Abweichungen vom Plan

—
