# Layer-2-Lauf: schrittweise Annäherung an den Tag

**Status:** fertig — 12.10.2026 (an echter Hardware nicht getestet, siehe Offene Fragen)
**Verantwortlich:** `Agent: Layer-2-Annäherung auf dem Hand-Pi`; Frontend: Sitzung `opcua-f1` (`webskillcomposition`, Branch `Ungetestet`)
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Bisher fuhr der Roboter in einem Schritt auf 0,3 m vor den Tag. Das Ziel stammte aus der groben Pose aus Layer 1. Lag diese Pose ein paar Zentimeter daneben, lag der Tag im nahen Bild schief oder gar nicht im Bild.

Jetzt läuft jeder Anker- und Mess-Schritt so ab:
1. Der Roboter fährt zuerst auf 0,6 m Abstand vor den Tag (Annäherung).
2. Der Pi erkennt den Tag im Bild und misst dabei seine Pose.
3. Der Pi plant das nahe Ziel aus dieser **gemessenen** Pose neu.
4. Erst im nahen Bild wird geankert bzw. gemessen.

Grundlage bleibt [`layer2-lauf-hand-pi.md`](layer2-lauf-hand-pi.md).

## Bereits gelesen

- `doc/projektdoku/arbeitsplaene/layer2-lauf-hand-pi.md` (Ablauf, Schemas, Fehlerfälle)
- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md` (Anker, `T_flansch_cam`)
- `doc/projektdoku/vision-server-interface.md` Abschnitt 14

## Betroffene Dateien

- `src/vision_server/layer2/plan.py`, `src/vision_server/layer2/run.py`
- `tests/test_layer2_plan.py`, `tests/test_layer2_run.py`, `tests/test_layer2_opcua.py`
- `doc/projektdoku/vision-server-interface.md` Abschnitt 14
- `doc/projektdoku/arbeitsplaene/README.md`: Indexzeile

## Schnittstellen

Die OPC-UA-Knoten und die Methoden bleiben unverändert. Die Schemas behalten ihre Versionen, alle Änderungen kommen nur hinzu.

### Daten

**`RunJson`** (`wsc.vision.layer2.run/1`) hat eine neue Option:

```json
"options": { "standoffM": 0.3, "reachM": 0.85, "approachStandoffM": 0.6 }
```

- `approachStandoffM`: Abstand der Kamera vor dem Tag beim Annäherungsbild, in Metern.
  - Standard: `0.6`.
  - `0` schaltet die Annäherung ab. Der Lauf ist dann genau der von vorher.
  - Andere Werte müssen größer als `standoffM` sein, sonst antwortet der Pi mit `INVALID_ARGUMENT`.

**Neuer `purpose`-Wert `"approach"`** in `Layer2Target` und `Layer2Status.steps[]`:
- Jedem Schritt mit `anchor` oder `measure` geht ein Schritt mit `approach` voraus. `stepIndex` zählt dabei streng fortlaufend weiter.
- Die Liste der Schritte steht ab dem Start vollständig in `steps[]`, alle mit dem Status `pending`.
- Ein Annäherungsschritt hat dieselben `name`, `moduleId` und `tagId` wie der nahe Schritt danach.

Beispiel mit einem Modul:

| stepIndex | purpose | name |
| --- | --- | --- |
| 0 | approach | Welttag 583 |
| 1 | anchor | Welttag 583 |
| 2 | approach | Modul A |
| 3 | measure | Modul A |

Der Client fährt einen `approach`-Schritt genauso wie jeden anderen Schritt: auf `flangeInBase` fahren und danach `ReportRobotPose` mit der **gemeldeten** Flanschpose aufrufen. Das Ziel des nahen Schritts ändert sich erst, nachdem die Annäherung ausgewertet ist. Der Client fährt deshalb immer das aktuelle `Layer2Target`.

`steps[].detail` nach einer erfolgreichen Annäherung, zum Beispiel: `"Tag 7 gesehen, nahes Ziel um 42 mm korrigiert"`. `result` bleibt bei der Annäherung `null`.

### Rechnung

`view_target` bleibt unverändert und wird zweimal aufgerufen: einmal mit `approachStandoffM`, einmal mit `standoffM`. Beide Bilder liegen auf derselben Blickachse.

Nach dem Annäherungsbild gilt:
- **Welttag:** Ein Welttag erscheint nicht als Detection, sondern als neuer `RobotAnchor` in der Quelle. Dessen `T_world_base` stammt aus dem weiten Bild und dient nur dem Zielen. Die Pose des Welttags selbst bleibt die exakte aus der Tag-Map. Das nahe Ziel wird geplant mit:

  ```
  T_base_flansch = inv(T_world_base_weit) · T_world_tag · T_tag_cam · inv(T_flansch_cam)
  ```

  Der Lauf übernimmt diesen Anker **nicht**. `Layer2Status.anchor` bleibt `null`, bis der nahe Anker-Schritt gelingt.
- **Modultag:** Die Detection des Tags liefert `T_world_tag`. Kennt die Tag-Map den Tag, ist die gemeldete Pose die des Moduls. Dann wird der CAD-Versatz zurückgerechnet: `T_world_tag = T_world_modul · inv(tag_to_module)`. Das nahe Ziel wird mit dieser Pose und dem Anker des Laufs geplant.

Iteriert wird **nicht**: Es gibt genau eine Annäherung je Schritt. So steht die Liste der Schritte schon zum Start fest. Außerdem ist das nahe Bild selbst die Messung, eine weitere Korrektur davor würde nichts bringen.

### Flansch-Frame

Mit dem Frontend abgestimmt (Sitzung `opcua-f1`, 12.10.2026): „Flansch“ ist der mechanische Werkzeugflansch ohne Greifer. Für diese URDF-Links gelten:
- `flangeInBase` (das Frontend rechnet es per FK aus den echten Gelenkwinkeln),
- die IK für das Fahrziel,
- **die Roboterposen der Hand-Auge-Aufnahmen**. `T_flansch_cam` ist nur gültig, wenn es auf denselben Link kalibriert wird.

| Roboter | URDF-Link |
| --- | --- |
| EVA, Demo1 | `model_6` (letztes Glied; `tool_point` ist die Greiferspitze, 17 cm davor) |
| UR5e | `flange` |
| FR3 | `eef_attach_point` |

Das Frontend schickt `approachStandoffM` vorerst nicht mit. Es gilt also der Standard 0,6 m.

### Python

```python
# src/vision_server/layer2/plan.py
APPROACH = "approach"
DEFAULT_APPROACH_STANDOFF_M = 0.6
RunRequest.approach_standoff_m: float          # 0 = aus
RunRequest.standoff_for(purpose) -> float
StepSpec.seen_tag: tuple[int, Pose] | None     # gemessene Weltpose, ersetzt die grobe
def find_detection(detections, tag_id) -> dict | None          # auch über attributes.tagIds
def tag_in_world_from_detection(detection, tag_id, tag_map) -> Pose   # ValueError
```

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Welttag im Annäherungsbild nicht gesehen, Job-Fehler | Annäherung `failed`, Lauf `aborted` (wie beim Anker) |
| Modultag im Annäherungsbild nicht gesehen, Pose nicht im Welt-KS | Annäherung und Mess-Schritt `skipped` mit Grund, weiter |
| Job-Fehler bei der Annäherung an ein Modul | Annäherung `failed`, Mess-Schritt `skipped`, weiter |
| `reached: false` bei der Annäherung (z. B. keine IK-Lösung) | Annäherung `skipped`, der nahe Schritt folgt mit dem Ziel aus der groben Pose |
| Annäherungsziel außer Reichweite | Annäherung `skipped` ohne Fahrt, der nahe Schritt folgt mit grober Pose |
| Nahes Modulziel außer Reichweite | Annäherung und Mess-Schritt `skipped` ohne Fahrt |

## Offene Fragen

- **Re-Ankern durch Beifang:** Liegt im Bild eines Modul-Schritts (Annäherung oder nah) zufällig ein Welttag, ankert die AprilTag-Quelle neu (`_locate`: „der optische Wert gewinnt immer“). Das war schon vor dieser Änderung so. Durch das weite Annäherungsbild passiert es jetzt aber häufiger, und der Anker kommt dann aus größerer Entfernung. `Layer2Status.anchor` bleibt dabei der Anker des Laufs. Die Weltposen der Module rechnet die Quelle aber mit ihrem eigenen, neuen Anker. Abhilfe wäre ein Job-Parameter „nicht neu ankern“. Bei Bedarf umsetzen.
- An echter Hardware ist das nicht getestet. `T_flansch_cam` fehlt noch (siehe `apriltag-hand-auge-ankern.md`).

## Abweichungen vom Plan

- Der Vorschlag von `opcua-f1` sah vor, die Tag-Pose im Basis-KS direkt aus `T_flansch_cam` und der gemeldeten Flanschpose zu rechnen. Umgesetzt ist der gleichwertige Weg über das Welt-KS: Die Quelle liefert die Pose bereits mit dem Anker im Welt-KS, und mit derselben Basis geht sie zurück ins Basis-KS. Der Anker kürzt sich dabei heraus. Für den Welttag dient der vorläufige Anker aus dem weiten Bild als Messung.
- `_evaluate_measure` findet den Tag jetzt auch dann, wenn die Quelle mehrere Tags eines Moduls zu einer Detection zusammenfasst (`attributes.tagIds`).

## Nach Abschluss

- [x] Status `fertig`, Schnittstellen eingetragen.
- [x] Indexzeile in `README.md`.
- [x] `vision-server-interface.md` Abschnitt 14 ergänzt.
- [x] Testlauf: 536 Tests, davon 1 Fehlschlag, der nichts mit dieser Änderung zu tun hat (`test_mjpeg_server…viewers_run_faster`, schlägt auch ohne diese Änderung fehl). Neu: `test_layer2_plan.py` (+3), `test_layer2_run.py` (+8, Klasse `Layer2ApproachTest`).

## Nachtrag: Arm fährt nicht mehr in den eigenen Wagen

Repo `webskillcomposition`, Branch `Ungetestet`.

### Problem

Bei der Layer-2-Fahrt kam der FR3 seinem Wagen sehr nah oder fuhr hinein, obwohl es die Sperrzone gibt (`frontend/src/features/cell-modules/model/cellClearance.ts`, aus `bounds` von ROBOT_FRANKA: x −0,20…0,89 m, y −0,41…0,36 m, Tischplatte z = 0; passt zu `wagon.stl` mit 1,09 × 0,72 m).

Ursachen:
1. Es wurden nur Punkte geprüft (Gelenkmittelpunkte, Flansch, Kamera), kein Volumen. Die FR3-Glieder haben etwa 6–8 cm Radius, die Hand etwa 10 cm.
2. Hand und Finger bis `tool_point` (0,1034 m vor dem Flansch) fehlten.
3. `legCollision` prüfte fest 10 Schritte, unabhängig davon, wie weit die Fahrt ging.

### Lösung

- Der Arm wird als Kette von Kugeln modelliert. Die Segmente werden in Abständen von höchstens 2 cm abgetastet.
- `ARM_RADIUS_M = 0,08` gilt für die Glieder, `HAND_RADIUS_M = 0,10` für Flansch, Werkzeugspitze und Kamera.
- Der Abstand beträgt `TABLE_CLEARANCE_M = 0,05` ab der Kugeloberfläche, der Boden wird entsprechend mit `FLOOR_CLEARANCE_M` geprüft.
- `flangeKinematics.points()` liefert Flansch und Werkzeugspitze aus dem URDF. Das Feld `toolFrom` markiert, ab wo der Hand-Radius gilt.
- `legCollision` tastet so fein ab, dass kein Gelenk pro Schritt mehr als 0,05 rad fährt (höchstens 200 Schritte).
- Link0 (Fuß auf dem Wagen) wird nicht geprüft. Die Kette beginnt an Gelenk 1 (z ≈ 0,333 m).
- Die Rückgabe bleibt `"Wagen"` | `"Boden"` | `null`, die Aufrufer bleiben unverändert.
- Zentrier-Korrekturen (`centreOnTag`) prüfen weiterhin nur ihr Ziel. Sie sind klein.

### Folge und Risiko

Die Prüfung ist strenger. Manche Sichtpunkte nah am Wagen werden ausgelassen („Sichtpunkt/Weg würde den Wagen berühren — ausgelassen“). Das ist gewollt. Werden es zu viele, lassen sich die Radien oder `TABLE_CLEARANCE_M` gezielt verkleinern.

### Verifikation

- Unit-Tests (`cellClearance.test.ts`) und Typprüfung.
- Am Roboter noch offen: kein Sichtpunkt und kein Weg unter etwa 5 cm an die Wagenplatte. Die Ausgangsstellung gilt nicht als „Wagen“.
