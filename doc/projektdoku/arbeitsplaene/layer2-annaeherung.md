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

## Nachtrag 27.10.2026: Jeden Tag aus 3 Ansichten messen und vereinen

> **Ersetzt am 10.11.2026** durch den Nachtrag „Eine Pose aus 30–45° Schrägansicht“ weiter unten. Mehrere Ansichten werden in Layer 2 nicht mehr gemittelt.

### Anlass

- Bisher maß die Layer-2-Fahrt jeden Tag nur aus der zentrierten Stellung: eine Ansicht, 2–8 Bilder.
- Module wurden nur verschoben. Ihre Drehung blieb die aus Layer 1, weil die Orientierung eines einzelnen Tags bei ~35 cm oft unsicher ist und gespiegelt sein kann.
- Der Anker setzte die Roboterbasis aus dieser einen Messung.

### Ablauf

- Anfahren und Zentrieren wie bisher.
- Danach 3 weitere Ansichten um die Tag-Mitte, im selben Abstand (Standoff des Sichtpunkts):

| Ansicht | Drehung |
| --- | --- |
| 1 | +12° um Tag-x |
| 2 | −12° um Tag-x |
| 3 | +12° um Tag-y |

  Dieselbe Geometrie wie bei den Roboter-Tags.
- Jede Ansicht wird mit Rollwinkeln 0/90/180/−90° und IK-Startwerten geplant: aktuelle Gelenke, Ausgangsstellung, Bereichsmitte, Winkel schon gemessener Ansichten. Wagen- und Wegprüfung wie bei der Fahrt.
- Nicht erreichbare oder nicht messbare Ansichten werden übersprungen und in der Zeile genannt.
- `T_base_tag` je Ansicht = FK(gelesene Gelenke) · T_Flansch_Kamera · T_Kamera_Tag.

### Vereinen (`fuseTagViews`)

Datei: `frontend/src/features/cell-modules/model/tagViews.ts` (Repo `webskillcomposition`).

Eingang sind die zentrierte Messung und die Ansichten.

- Position = Mittel.
- Orientierung = Mittel der nicht gespiegelten (eindeutigen) Ansichten, sonst die geplante Orientierung.
- Streuung = RMS.
- Ausreißer: ab 3 Messungen wird höchstens eine verworfen, wenn sie > 3 mm bzw. > 1° vom Mittel der übrigen liegt und die übrigen untereinander auf 1,5 mm / 0,5° übereinstimmen.
- Weniger als 2 gültige Messungen: wie bisher nur die zentrierte Messung („nur 1 Ansicht“).

### Ergebnis Modul

- Verschiebung aus dem Mittel.
- Drehung wird übernommen, wenn mindestens 2 eindeutige Ansichten vorliegen und die Winkelstreuung ≤ 0,5° ist (`LAYER2_HEADING_MAX_SPREAD_DEG`). Dann wird die waagerechte Modulpose wie in Layer 1 aus der Tag-Pose berechnet (`levelModulePose`: nur Gierwinkel, Höhe bleibt die des Bodens).
- Sonst bleibt die Drehung aus Layer 1 („Drehung aus Layer 1 behalten“).

### Ergebnis Anker

- Basiskorrektur aus dem Mittel.
- Kurs nur unter derselben Bedingung. Vorher reichte eine einzige nicht gespiegelte Messung.

### Anzeige in der Zeile

- „N Ansichten, Streuung x,x mm / y,y°“
- übersprungene und verworfene Ansichten
- bei gedrehtem Modul „Δ x,x°“

### Grenzen

- Pro Sichtpunkt ~20–30 s länger.
- Ein fester Versatz der Hand-Auge-Kalibrierung mittelt sich nicht heraus. Dafür neu kalibrieren.
- Nah am Wagen sind nicht alle Ansichten erreichbar.

### Schnittstellen

- Keine Änderung am Pi. Betroffen ist nur `MeasureTags`.
- Frontend, Repo `webskillcomposition`, Branch `Ungetestet`:

| Commit | Inhalt |
| --- | --- |
| `eaab176` | gemeinsamer Teil `tagViews.ts`, Roboter-Tags unverändert |
| `a9137d3` | Layer-2-Fahrt |
| `53a8c55` | Wagen-Prüfung lockerer: Glieder 6 cm, Hand 8 cm, Rand 2 cm |
| `6bce599` | Layer-2-Vorschlag als Kästen und Basis-Markierung statt Modellkopien |

### Offen

- Am Roboter noch nicht geprüft.
- Erwartung: Wiederholgenauigkeit der Modulposition ~1–3 mm, Drehung ~0,3°.

## Nachtrag 10.11.2026: Eine Pose aus 30–45° Schrägansicht

Stand 10.11.2026 nachmittags: Soll-Abstand 0,35 m, bevorzugt 30°, ohne nahes Heranfahren.

### Grundsatz

> Je Tag wird genau eine Pose ausgewertet: Der Roboter fährt zum Tag und stellt sich in etwa 0,35 m Abstand möglichst 30° schräg zur Tag-Normale (höchstens 45°), und misst von dort. Mehrere Ansichten werden nicht zusammengefasst oder gemittelt.

### Warum

- Ein planarer Tag frontal gesehen ist in der Orientierung mehrdeutig (Spiegel-Lösung, `ambiguous`).
- Ab 30° ist die Lösung eindeutig. Der Tag ist noch groß genug für eine genaue Position.
- 30° ist der kleinste Winkel, der die Orientierung sicher eindeutig macht. Flacher ist die Position genauer.
- Mitteln verteilte Fehler der Hand-Auge-Kalibrierung und der Gelenkstellungen auf eine Zahl. Eine gute Einzelmessung war darin nicht zu erkennen.
- Der kurze Arm erreichte oft nur 1–2 Ansichten. Das Mittel stand dann auf wenig Basis.

### Ablauf

1. Sichtpunkt aus der Planung anfahren, Tag im geplanten Abstand zentrieren.
2. Schräge Ansicht um die Tag-Mitte anfahren, Kippwinkel möglichst 30° zur Tag-Normale:
   - Reihenfolge: zuerst 30° in allen 8 Richtungen, danach 35°, 40°, 45°. Das sind 32 Kandidaten.
   - Richtungen um die Tag-Normale: 0°, ±45°, ±90°, ±135°, 180°, ab der Seite, auf der die Kamera steht. Steht die Kamera frontal, ab der Richtung zur Roboterbasis.
   - Die erste erreichbare Ansicht, die den Tag sieht, gilt.
3. Abstand der schrägen Ansicht: `obliqueStandoffM` = max(0,35 m; Abstand, bei dem die vordere Tag-Kante höchstens 90 % der Bildhöhe einnimmt, `OBLIQUE_VIEW_MAX_FILL = 0.9`; 0,12 m Fixfokus; Greiferspitze ≥ 5 cm vor der Tag-Ebene).
   - 8-cm-Modultag: immer 0,35 m.
   - 20-cm-Welttag: 0,35 m bei 30–35°, bei 40–45° 0,354–0,36 m.
   - Ohne Tag-Größe: 0,35 m.
4. Liegt der Tag neben der Bildmitte, wird einmal seitlich nachgeregelt (`recentre`). Fehlt er im Bild, fährt die Kamera 30 % zurück (`backOff`).
5. Diese eine Messung (2–8 Bilder derselben Roboterstellung) ergibt `T_base_tag = FK(gelesene Gelenke) · T_Flansch_Kamera · T_Kamera_Tag`.

### Übernahme

- **Position:** aus der einen Pose.
- **Drehung (Modul):** wird übernommen, wenn die Messung eindeutig ist (`ambiguous: false`). Sonst bleibt die Drehung aus Layer 1.
- **Anker (Welttag):** Basiskorrektur aus der einen Pose. Der Kurs wird nur bei eindeutiger Messung übernommen.

### Fallback

- Ist keine schräge Ansicht erreichbar oder sieht keine den Tag, gilt die zentrierte Messung im geplanten Abstand als die eine Pose.
- Die Zeile sagt dann „schräg nicht erreichbar — frontale Messung“.

### Anzeige

- Statuszeile, z. B. „1 Pose, 37° schräg, 0,17 m“.

### Umsetzung

- `frontend/src/features/cell-modules/components/ApproachPlanPanel.tsx`
- `frontend/src/features/cell-modules/model/tagViews.ts` (`obliqueViews`, `OBLIQUE_VIEW_TILTS_DEG = [30, 35, 40, 45]`)
- `frontend/src/features/cell-modules/model/tagServo.ts` (`obliqueStandoffM`, `OBLIQUE_VIEW_STANDOFF_M = 0.35`, `OBLIQUE_VIEW_MAX_FILL = 0.9`)
- Commits `7cd0105`, `a3057c8` (`webskillcomposition`, Branch `layer2-nah-ran-2`). `closeStandoffM` und `CLOSE_TAG_FILL` sind entfernt.
- Die Roboter-Tags (`robotTagRun.ts`) messen weiter aus mehreren Ansichten.

### Offen

- Am echten Aufbau prüfen, wie oft 30° (bzw. bis 45°) erreichbar ist.
- Prüfen, ob die Wiederholgenauigkeit der Einzelpose an die gemittelte heranreicht.

## Nachtrag 11.11.2026: Weg in die Ausgangsstellung erst 30 cm senkrecht hoch

- Anlass:
  - Der direkte Gelenkweg von dort, wo ein Lauf den Arm gelassen hat, in die Ausgangsstellung berührte oft den Wagen.
  - Meldung: „Weg zur Ausgangsstellung würde den Wagen berühren“.
- Neu: `frontend/src/features/cell-modules/model/homeViaLift.ts` (webskillcomposition, Branch `handauge-schachbrett-offline`, Commit `4c4ec2b`).
  - `planHomeViaLift({ kinematics, from, home, clearance, liftsM, maxJointStepRad })` (rein):
    - Hebt den Flansch mit gleicher Orientierung um `HOME_LIFT_M = 0.3` m in Basis-z. IK mit den aktuellen Gelenken als Startwert.
    - Gültig, wenn:
      - die IK konvergiert,
      - die Gelenkgrenzen eingehalten sind,
      - kein Gelenk mehr als 1,2 rad dreht (kein Umklappen),
      - Hub-Stellung, Weg dorthin und Weg vom Hub nach Hause frei von Wagen und Boden sind.
    - Sonst Versuch mit 0,2 m, dann 0,1 m (`HOME_LIFT_FALLBACKS_M`).
    - Keiner gültig: direkt fahren, wenn der direkte Weg frei ist. Sonst `ok: false` mit „… auch über 30/20/10 cm Anheben nicht frei. Den Arm von Hand freifahren.“ Dann wird nicht gefahren.
    - Schon in der Ausgangsstellung (alle Gelenke < 0,01 rad): keine Fahrt.
  - `driveHomeViaLift(deps, home, from, { clearance, onPhase })`: fährt erst den Hub, dann die Ausgangsstellung. Scheitert der Hub, wird nicht weitergefahren.
- Wo es gilt:
  - Layer 2: vor jedem Ziel und am Ende, mit Wagen- und Bodenprüfung. Anzeige: „fährt erst 30 cm hoch…“, „fährt dann in die Ausgangsstellung…“.
  - Pick & Place: Start, Ende und die Rückweg-Prüfung der Route. Dort wie bisher ohne Wagenprüfung.
  - Hand-Auge-Automatik und Brett-Kalibrierung: ohne Wagenprüfung, weil es dort keine gibt.
  - Der Knopf „Ausgangsstellung“ im Viewport bewegt nur den Zwilling und bleibt unverändert.
- Hinweis:
  - Fahrten sind Gelenkraum-Fahrten. Der Hub ist deshalb nur näherungsweise senkrecht (IK-Toleranz 2 mm / 1°).
  - Die Prüfung tastet den Gelenkweg ab.
- Tests: `homeViaLift.test.ts` (10 Tests).
- Am Roboter noch nicht geprüft.
