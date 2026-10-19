# Layer 2: MeasureTags — Tags in Kamerakoordinaten messen

**Status:** fertig — 15.10.2026 (an echter Hardware nicht getestet)
**Verantwortlich:** `Agent: MeasureTags auf dem Hand-Pi`; Frontend: Sitzung `opcua-4c` (`webskillcomposition`, Branch `Ungetestet`)
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Layer 2 ist neu aufgeteilt:
- Die **Steuerung** (webskillcomposition) plant und fährt. Aus Layer 1 berechnet sie je Tag eine Kamerapose etwa 35 cm vor dem Tag und fährt hin.
- Danach regelt sie im Stop-and-go nach: fahren → Stillstand → messen → `T_base_tag = FK(Gelenke) · T_flansch_cam · T_cam_tag` → neues Flanschziel.
- Der **Hand-Pi misst nur.** Er plant nichts und kennt weder Roboter noch Kinematik.

`StartLayer2Run` und das Ankern bleiben bestehen, werden aber vorerst nicht genutzt.

## Bereits gelesen

- `layer2-lauf-hand-pi.md`, `layer2-annaeherung.md`, `layer2-schraege-ansicht.md`
- `handeye-ueber-opcua.md`: `measure_tag` ist dieselbe Rechnung, `T_flansch_cam` passt also dazu
- `vision-server-interface.md` Abschnitte 14, 15

## Betroffene Dateien

- `src/vision_server/measuretag.py` (neu): Anfrage, Zusammenführung, Ergebnis-JSON
- `src/vision_server/detection/apriltag.py`: `measure_tags`, `intrinsics_source`, Rückfall auf die Werks-Intrinsik in `open()`
- `src/vision_server/camera.py`: `SharedCamera.factory_calibration`, `_realsense_calibration`
- `src/vision_server/address_space.py`: Knoten `LastTagMeasurement`
- `src/vision_server/runner.py`: `_install_measure_tags`
- `tests/test_measuretag.py` (neu)

## Schnittstellen

### OPC UA

Die Elemente gibt es nur auf dem Hand-Pi, also mit `hand_eye_path`. Sie liegen unter `VisionMachine` und sind unter `VisionProgram` verlinkt.

| Element | NodeId | Typ |
| --- | --- | --- |
| MeasureTags | `ns=<vision>;s=VisionMachine.MeasureTags` | `(RequestJson: String) -> (ResultJson: String, Error: Int32)` |
| LastTagMeasurement | `ns=<vision>;s=VisionMachine.LastTagMeasurement` | String (JSON), nur zur Anzeige |

Die Methode antwortet erst nach der Messung. Das Ergebnis kommt **direkt zurück**, so gibt es keinen Wettlauf um einen Ergebnis-Knoten. `ResultJson` ist immer gültiges JSON, auch bei einer Ablehnung (dann mit `tags: []` und `error`).

| Error | Wann |
| --- | --- |
| `OK` | gemessen. Auch wenn kein Tag im Bild war (`tags: []`). |
| `DETECTION_FAILED` | kein frisches Kamerabild (`error: "NO_FRAME"`) |
| `INVALID_ARGUMENT` | JSON bzw. Werte ungültig |
| `INVALID_STATE` | Vision-System nicht `Operational` |
| `BUSY` | Job, Kamera-Kalibrierung oder Hand-Auge-Sitzung läuft |

### Daten

**Anfrage**, `wsc.vision.measuretag.request/1`. Alle Felder sind optional, `""` bzw. `{}` ist gültig.

```json
{ "schema": "wsc.vision.measuretag.request/1",
  "tagIds": [7] | null, "tagSizeM": {"7": 0.08}, "frames": 3 }
```

- `tagIds`: `null` heißt alle erkannten Tags.
- `frames`: 1–10, Standard 3. So viele frische Bilder werden gemittelt.

**Ergebnis**, `wsc.vision.measuretag.result/1`:

```json
{ "schema": "wsc.vision.measuretag.result/1",
  "capturedAt": "2026-10-15T12:00:00.123+00:00", "capturedUntil": "…",
  "frameId": "cam_flange", "frameConvention": "z_forward_x_right_y_down",
  "imageSize": [640, 480],
  "intrinsicsSource": "calibration" | "realsense_factory" | "placeholder", "calibrationId": "…",
  "framesRequested": 3, "framesUsed": 3,
  "tags": [ { "tagId": 7,
              "poseInCamera": { "position": [x,y,z], "orientation": [qx,qy,qz,qw] },
              "centerPx": [u,v], "cornersPx": [[u,v],[u,v],[u,v],[u,v]],
              "reprojectionErrorPx": 0.4, "decisionMargin": 55.0,
              "sizeM": 0.08, "sizeSource": "request" | "tagmap" | "default",
              "ambiguous": false, "ambiguityRatio": 0.3, "frameCount": 3,
              "distanceM": 0.351, "viewAngleDeg": 4.2, "nearEdge": [] } ],
  "error": null | "NO_FRAME" }
```

- **`poseInCamera`** ist `T_cam_tag`. Kamera-KS wie OpenCV: +Z aus der Linse, +X rechts, +Y unten. Tag-KS wie überall: +Z aus dem Tag heraus. Es gibt keine Welt-Transformation und keinen CAD-Versatz.
- **Nur frische Bilder:** Gemessen wird nur auf Bildern, die **nach** dem Aufruf eintreffen. Ein Bild aus der Fahrt wird nie verwendet.
- **Zusammenführung je Tag:**
  - Die Position ist das Mittel über alle Bilder mit dem Tag.
  - Die Orientierung ist das Mittel über die eindeutigen Bilder. Gibt es keine, wird über alle gemittelt und `ambiguous: true` gesetzt. Dann **nicht** auf die Drehung regeln, die Position bleibt verlässlich.
- **`frameCount`:** in wie vielen Bildern der Tag gesehen wurde. Die Steuerung macht unter 2 keinen Regelschritt.
- **`capturedAt`/`capturedUntil`:** Eintreffen des ersten bzw. letzten Bilds auf dem Pi, in UTC. Gemessen wird beim Eintreffen, nicht bei der Belichtung. Das macht rund eine Bildperiode aus.
- **`cornersPx`/`centerPx`/`decisionMargin`:** aus dem letzten Bild. Die Pixel sind roh (verzeichnet). Die Reihenfolge der Ecken ist die von `tag_object_points`: oben links, im Uhrzeigersinn.
- **`sizeM`/`sizeSource`:** die Größe, mit der gerechnet wurde. Vorrang hat die Anfrage, dann die Tag-Map, dann der Rückfall von 8 cm. Eine falsche Größe skaliert den Abstand linear.
- **`viewAngleDeg`:** Winkel zwischen Tag-Normale und Sichtlinie, 0 = frontal.
- **`nearEdge`** (ergänzt am 19.10.2026): die Bildränder (`left`, `right`, `top`, `bottom`), denen eine Ecke des Tags im letzten Bild näher als 5 % der Bildgröße kommt. Für die Suche nach anfangs unerreichbaren Tags: Die Steuerung fährt bestmöglich heran, misst und schwenkt bei einem randnahen Tag gezielt in diese Richtung nach. Ein angeschnittener Tag wird gar nicht erkannt, deshalb kommt der Hinweis vorher, solange er noch ganz im Bild ist.

### Intrinsik

Die Kalibrierdatei `data/calibration/cam_flange.json` hat Vorrang. Fehlt sie, gilt auf der **RealSense** die Werks-Intrinsik aus der laufenden Pipeline (`realsense_factory`, `calibrationId` = `realsense-werksintrinsik`).

Bei der Verzeichnung gilt:
- Brown-Conrady von librealsense entspricht OpenCV und wird übernommen.
- Andere Modelle werden nur übernommen, wenn alle Koeffizienten 0 sind. Andernfalls rechnet der Pi ohne Verzeichnung und schreibt eine Warnung ins Log.

Ohne Werks-Intrinsik gilt der Platzhalter, aber nur mit `allow_placeholder_calibration`. Sonst öffnet die Quelle nicht, wie bisher. Bei `placeholder` regelt die Steuerung nicht.

Achtung: Die Werks-Intrinsik gilt auch für Jobs und Livestream dieses Pis, nicht nur für `MeasureTags`.

## Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Tag nicht im Bild | `tags: []`, `OK` |
| Kein frisches Bild innerhalb `capture_timeout_s` | `error: "NO_FRAME"`, `DETECTION_FAILED`. Wurden schon einige Bilder ausgewertet, zählen die (`framesUsed` < `framesRequested`). |
| Tag mehrdeutig | Pose kommt mit `ambiguous: true` |

## Offene Fragen

- An echter Hardware ist das nicht getestet. Ob die Werks-Intrinsik der verbauten RealSense Brown-Conrady meldet, zeigt erst der Pi.

## Nach Abschluss

- [x] Status `fertig`, Schnittstellen eingetragen.
- [x] Indexzeile in `README.md`.
- [x] `vision-server-interface.md` Abschnitt 17.
- [x] Tests: `tests/test_measuretag.py` (14).
