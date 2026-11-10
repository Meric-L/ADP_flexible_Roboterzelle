# Messkette: wie Layer 2 einen Tag misst, und Auswertung der Messreihen

**Stand:** 03.11.2026
**Branch:** `messkette-protokoll` (Frontend `webskillcomposition`, Pi dieses Repo)
**Thema:** vision

Diese Datei beschreibt, wie aus den Messungen eines Tags das Ergebnis für Layer 2 entsteht, und wertet die Messreihen vom 02. und 03.11.2026 aus. Das Format des Protokolls (`wsc.layer2.chain/1`) steht in [`arbeitsplaene/messkette-protokoll.md`](arbeitsplaene/messkette-protokoll.md) und wird hier nicht wiederholt.

Abkürzungen: KS = Koordinatensystem, T_A_B = Pose von B im System A.

## Teil 1: Wie ein Tag-Ergebnis aus den Ansichten entsteht

### Dateien

| Datei (Repo `webskillcomposition`, `frontend/src/features/cell-modules/`) | Aufgabe |
| --- | --- |
| `model/approachPlan.ts` | Sichtpunkte planen |
| `model/centreOnTag.ts` | Zentrieren (auch beim Nahblick) und Suche |
| `model/tagServo.ts` | Korrekturschritt beim Zentrieren, Nahabstand |
| `model/tagViews.ts` | Zusatzansichten und Vereinigen (`fuseTagViews`) |
| `components/ApproachPlanPanel.tsx` | Ablauf der Layer-2-Fahrt und Anzeige |

### Ablauf je Tag

1. **Sichtpunkt anfahren.** Der Sichtpunkt wird aus der Layer-1-Lage geplant. Modul-Tag: 0,35 m Abstand (`DEFAULT_APPROACH_STANDOFF_M`). Welttag: 1,0 m Abstand (`DEFAULT_ANCHOR_STANDOFF_M`). Die Neigung zum Roboter hin wird in den Stufen 0°, 15°, 30°, 45° und 60° probiert. Ist eine Stufe nicht erreichbar, geht der Sichtpunkt weiter weg (×1,3, ×1,6, ×2). Die Drehung um die Sichtlinie wird in den Stufen 0°, 90°, −90° und 180° probiert. Die erste erreichbare Stufe gilt.

2. **Zentrieren (`centreOnTag`).** Der Hand-Pi misst den Tag mit `MeasureTags`. Die Kamera wird seitlich so verschoben, dass der Tag auf der optischen Achse im geplanten Abstand liegt.
   - Fertig bei höchstens 5 mm seitlichem Fehler und höchstens 10 mm Abstandsfehler.
   - Höchstens 3 Korrekturen, je höchstens 5 cm.
   - Liegt der Tag nicht im Bild, sucht die Kamera in bis zu 17 Schritten (15°-Ringe).
   - Liegt der Tag nur in einem Bild, wird mit 8 Bildern noch einmal gemessen.
   - Ist der Abstand nicht erreichbar, wird nur seitlich zentriert (Status `far`).

3. **Nah heranfahren.** Der Nahabstand ist so gewählt, dass das schwarze Quadrat 75 % der Bildhöhe füllt (Sichtfeld ±21°).

   | Tag | Nahabstand (etwa) |
   | --- | --- |
   | 8-cm-Tag | 0,14 m |
   | 20-cm-Welttag | 0,35 m |

   Der Nahabstand liegt nie unter 0,12 m. Die Greiferspitze bleibt mindestens 5 cm vor dem Tag. Der Weg ist ein Zug entlang der Sichtlinie. Er wird gegen Wagen und Boden geprüft. Danach wird noch einmal zentriert. Gelingt der Nahblick, ersetzt diese Messung die zentrierte. Sonst bleibt die zentrierte Messung. Beim Welttag (am Boden, Roboterbasis 1 m darüber) ist der Nahblick meist nicht erreichbar (`far`).

4. **Zusatzansichten (`measureTagViews`).** Es gibt drei Hauptansichten. Sie sind um die Tag-Mitte um 12° gedreht, in den Richtungen 0° (zur Roboterbasis), +90° und −90°. Nach einem gelungenen Nahblick werden die Zusatzansichten aus dem 1,3-fachen Nahabstand geplant (`CLOSE_VIEW_DISTANCE_FACTOR`), sonst aus dem Abstand der zentrierten Messung.

   Ersatzansichten werden nur gemessen, bis drei Zusatzansichten vorliegen. Verfügbar sind neun Stück:
   - 24° Neigung, Richtung 0°
   - 20° Neigung, Richtung ±45°
   - 12° Neigung, Richtung 180°
   - 20° Neigung, Richtung ±90°
   - aus dem 1,25-fachen Abstand: 0° Neigung, Richtung 0°; 12° Neigung, Richtung ±90°

   Je Ansicht werden die Drehungen 0°, 90°, 180° und −90° um die Sichtlinie probiert.

   Nachgeregelt wird, wenn der Tag mehr als 3° neben der Achse liegt oder nah am Bildrand. Dann wird einmal seitlich verschoben und neu gemessen. Liegt der Tag nicht im Bild, fährt der Roboter um 30 % des Abstands zurück.

   Die Lage je Ansicht ergibt sich aus:

   `T_Basis_Tag = Vorwärtskinematik(gelesene Gelenke) · T_Flansch_Kamera · T_Kamera_Tag`

   Nummerierung im Protokoll (Feld `index`): 0 ist die zentrierte oder nahe Messung, 1 bis 3 sind die Hauptansichten, ab 4 die Ersatzansichten.

5. **Vereinigen (`fuseTagViews`).**
   - Position: Mittelwert aller Messungen.
   - Drehung: Mittel der eindeutigen Ansichten (nicht `ambiguous`). Gibt es keine eindeutige Ansicht, bleibt die Drehung aus Layer 1.
   - Streuung: RMS-Abstand zum Mittel, in mm und Grad.
   - Ausreißer: Ab 3 Messungen wird höchstens eine verworfen. Das geschieht nur, wenn sie mehr als 3 mm oder 1° vom Mittel der übrigen abweicht und die übrigen untereinander auf 1,5 mm und 0,5° übereinstimmen.

6. **Übernahme.**
   - Position: schon ab 1 Messung (`LAYER2_MIN_MEASUREMENTS = 1`). Die Messung der Roboter-Tags verlangt weiterhin 3 Messungen.
   - Drehung: nur, wenn mindestens 1 eindeutige Ansicht vorliegt und die Streuung der Drehung höchstens 0,5° beträgt (`LAYER2_HEADING_MAX_SPREAD_DEG`). Sonst bleibt die Drehung aus Layer 1.
   - Modul: Die Lage wird waagerecht übernommen. Nur der Gierwinkel wird übernommen, die Höhe bleibt die des Bodens.
   - Anker (Welttag): Die Roboterbasis wird in x und y korrigiert. Die Höhe bleibt die geplante. Der Gierwinkel wird nur unter derselben Bedingung übernommen wie bei Modulen.

7. **Protokoll.** Im Use-Case-Versuch steht die ganze Kette unter `client.layer2`. Das Format steht in [`arbeitsplaene/messkette-protokoll.md`](arbeitsplaene/messkette-protokoll.md). Je Tag werden folgende Teile abgelegt:
   - `measurements`: jede einzelne Messung mit Gelenken, Flansch- und Kamerapose, dem Ergebnis von `MeasureTags` und dem Tag im Basis-KS
   - `centred` und `close`
   - `views.seen` (je Ansicht `index`, `ambiguous`, `tagInBase`) und `views.warnings`
   - `fused` (`tagInBase`, `spreadMm`, `spreadDeg`, `used`, `rejected`, `unambiguous`)
   - `result`

   Beim Messrauschen-Lauf (`SaveImage` mit Zweck `lv2_messrauschen`) legt der Pi je angefahrenem Tag `data/LV2_Messrauschen/lauf_NNN/messung_NNN.png` und `messung_NNN.json` ab. Im JSON steht unter `client` (Schema `wsc.layer2.noise/1`) dieselbe Kette, ergänzt um `durchlauf` und `durchlaeufe`.

## Teil 2: Auswertung der Messreihen vom 02. und 03.11.2026

### 2.1 Use-Case-Reihen 1 bis 6 (Würfelablage auf das Zielblatt)

**Methode.** Das Kontrollfoto entsteht senkrecht über dem geplanten Ablagepunkt. Der Punkt, an dem die optische Achse das Blatt trifft, zeigt, wohin Roboter und Modell zielen. Dieser Punkt heißt hier Ablageziel. Er ergibt sich aus Layer-2-Tischlage, Hand-Auge-Kalibrierung und Roboter. Würfelmitte minus Ablageziel ist der Anteil von Greifen und Absetzen.

**Ergebnisse.**
- Innerhalb einer Reihe streut die Ablage um 1 bis 2 mm, in den Reihen 4 und 5 um etwa 1 mm.
- Je Tischposition liegt die Ablage fest 3 bis 8 mm daneben. Der Versatz entsteht fast ganz im Ablageziel.

**Ablageziel im Tisch-KS (mm):**

| Reihe | Tischlage | x | y |
| --- | --- | --- | --- |
| 2 | rechts | +0,2 | +6,2 |
| 3 | hinten rechts, schräg | +5,0 | +7,4 |
| 4 | links | +1,1 | +4,8 |
| 5 | links | +0,4 | +5,2 |
| 6 | links, Tisch um 180° gedreht | −3,3 | +5,1 |

**180°-Test (Reihen 5 und 6).** Der Versatz in Tisch-y bleibt im Tisch-KS gleich (+5,2 und +5,1 mm). Im Roboter-KS wechselt er das Vorzeichen. Daraus folgt eine Zerlegung: etwa 5 mm fest am Tisch und etwa 2 mm fest am Roboter (Basis-x).

**Prüfung der Referenzen.** Zielblatt (auf etwa 1 mm mittig), Tischmodell und Modul sind nachgemessen. Jedes weicht um höchstens etwa 1 mm ab.

**Wahrscheinliche Ursache der 5 mm.** Der Fehler der Tischdrehung aus Layer 2 ist die wahrscheinlichste Ursache.
- Tag 3 sitzt am Tischende (x = 640 mm), der Ablagepunkt in der Tischmitte. Der Hebel beträgt etwa 325 mm. Ein Drehfehler von 1° ergibt dort 5 bis 6 mm.
- Der Fehler dreht mit dem Tisch.
- Vermutlich wird er vom Drehfehler der Hand-Auge-Kalibrierung gespeist.
- Das Blatt erscheint relativ zum Modelltisch in allen Reihen um etwa −0,4° gedreht.

**Wiederholgenauigkeit.** Reihe 5 wurde an derselben Stelle wie Reihe 4 gemessen, Stunden später. Ergebnis: −0,9 und +2,6 mm gegen −1,2 und +2,6 mm.

**Absetzhöhe.** Die Absetzhöhe musste um −10 bis −16 mm korrigiert werden. Ursache: `objectInFlange` nahm an, der Würfel liege beim Einlernen auf der Wagenplatte (z = 0). Tatsächlich liegt er auf einer etwa 16 mm hohen Halterung. Behoben im Frontend (Commit `fef58ac`): Das Feld „Halterungshöhe“ hat den Standard 16 mm. Die Absetzhöhe startet wieder bei +3 mm.

**Ausreißer und Ausschluss.**
- Reihe 1, Versuche 1 und 2: Die Roboterbasis sprang beim Ankern um bis zu 6 cm.
- Einzelne Fotos aus etwa 14 mm höherer Stellung (R4 V1, R4 V6, R5 V3) taugen nicht für die Zerlegung.
- Drei Versuche ohne Messung, weil der Würfelmarker nicht erkannt wurde.

### 2.2 Höhentest der Hand-Auge-Kalibrierung

**Methode.** Die Kamera steht senkrecht über dem Zielblatt, in 350, 300, 250, 200 und 150 mm Höhe. Verglichen werden die Höhe laut Modell und die gemessene Höhe (Blattmessung). Ein konstanter Unterschied ist der Hand-Auge-z-Fehler. Ein wachsender Unterschied ist ein Maßstabsfehler.

| Kalibrierung | Lauf | Ergebnis | Drehfehler (Mittel, maximal) |
| --- | --- | --- | --- |
| alt (02.11., mit Maßstabsschätzung, Brettmaßstab 1,0104) | Lauf 4 | konstant 9,8 mm, Steigung −0,2 % | 0,74°, bis 1,35° |
| alt (02.11.) | Lauf 5 | 8,1 ± 1,3 mm | 0,74°, bis 1,35° |
| neu (03.11., ohne Maßstabsschätzung, Kamera-z 57,3 statt 50,0 mm vor dem Flansch) | Lauf 6 | Differenz 1,1 ± 0,7 mm, konstant 0,3 mm | 0,92°, bis 1,95° |

- Bei Lauf 4 war der Versatz konstant. Er entspricht also dem Hand-Auge-z. Die Steigung ist klein.
- Ursache des Fehlers der alten Kalibrierung: Maßstab und Lage der Kamera entlang der Blickachse sind in der Rechnung gekoppelt.
- Der Drehfehler der neuen Kalibrierung ist größer als der der alten.

**Senkrechtes Abwärtsfahren.** Beim senkrechten Abwärtsfahren wandert die Kamera relativ zum Blatt um etwa 1,8 mm je 100 mm. Roboter-Senkrechte und Blattnormale weichen um etwa 1° ab. Vermutlich steht Wagen oder Tisch schief.

### 2.3 Level 2: zehnmal dieselbe Messung

**Datenbasis.** `data/LV2_Messrauschen/lauf_002`, 03.11.2026. Ohne bewegte Module. Je Durchlauf werden der Welttag 583 aus etwa 1 m (Anker) und der Tag 3 am Tisch gemessen, je 4 Messungen. Verworfen wurde nichts.

**Tag 3 (Tisch).** Endergebnis über 10 Durchläufe:
- Streuung x 1,1 mm, y 0,35 mm, z 0,5 mm
- Gierwinkel 0,02°
- größte Abweichung 1,9 mm

Feste Versätze je Ansicht zum Mittel:

| Ansicht | Versatz | Anmerkung |
| --- | --- | --- |
| 0 | 0,8 mm | |
| 1 | 1,5 mm | |
| 2 | 1,2 mm | z +2,0 mm |
| 3 | 1,4 mm | z −1,8 mm |

Die Streuung je Ansicht über die Durchläufe liegt bei 0,2 bis 1,3 mm. Die gekippten Ansichten 2 und 3 liegen in z gegenläufig. Das ist ein Rest-Fehler der Hand-Auge-Kalibrierung bei schrägem Blick. Das Mitteln gleicht ihn weitgehend aus.

**Welttag 583 (Anker).** Die Streuung der vier Messungen je Durchlauf liegt bei 5 bis 10 mm und 0,9 bis 2,9°.

Feste Versätze je Ansicht:

| Ansicht | x (mm) | y (mm) |
| --- | --- | --- |
| 0 | 1,3 | −4,5 |
| 2 | 5,5 | −1,3 |
| 3 | 0,4 | −1,2 |
| 5 (Ersatz) | −4,8 | +7,1 |
| 7 (Ersatz) | 0,2 | 4,1 |
| 8 (Ersatz) | −11,6 | 1,0 |

Die Streuung je Ansicht über die Durchläufe 1 bis 3 mm, bei Ansicht 8 bis 15 mm.

Ursache: 1 m Abstand und 30° Neigung. Schon 0,5° Winkelfehler ergeben 9 mm. Der Versatz hängt an der Stellung, vermutlich am Drehfehler der Hand-Auge-Kalibrierung. Die Ausreißerregel greift nie, weil die übrigen Messungen nie auf 1,5 mm übereinstimmen. Das ist richtig, es handelt sich um feste Versätze. Die Drehung wurde nie übernommen.

**Ausreißer.** Durchlauf 7 und Durchlauf 9. Die Ansichten 2 und 5 waren nicht erreichbar. Die Ersatzansichten 7 und 8 sprangen ein. Wegen ihrer eigenen Versätze liegt die Basis 3,1 bzw. 2,6 mm neben dem Mittel. Sonst liegt sie 0,8 bis 1,9 mm daneben.

**Was bei der Ablage ankommt.**

| Größe | x (mm) | y (mm) |
| --- | --- | --- |
| Roboterbasis | 1,32 | 1,35 |
| Tisch im Welt-KS | 1,57 | 1,68 |
| Tisch relativ zur Basis | 0,36 | 1,13 |

Der Tisch wackelt im Welt-KS fast nur mit dem Anker (Korrelation in x 0,98). Die Ablage rechnet im Roboter-KS, die Basiskorrektur kürzt sich dort heraus. Level 2 trägt damit etwa 1 mm Streuung zur Ablage bei. Der Anker zählt nur dort, wo das Welt-KS selbst zählt, zum Beispiel beim Abgleich mit der Deckenkamera.

### 2.4 Offene Punkte und Empfehlungen

- Drehtest im Hand-Auge-Fenster laufen lassen. Er zeigt seitlichen Versatz, Kippfehler der Kamera am Flansch und Neigung von Tisch und Roboter.
- Drehfehler der Hand-Auge-Kalibrierung verbessern: mehr Drehung zwischen den Posen, alle 18 Posen erreichen.
- Anker: immer dieselben Ansichten messen. Alternativ einen Durchlauf ohne Hauptansicht nicht übernehmen, damit die Zusammensetzung nicht springt.
- Ursache der 5 mm in Tisch-y mit dem Drehtest und einer Prüfung der Tischdrehung bestätigen.
- Die Auswertungen sind mit Skripten außerhalb des Repos gerechnet. Eine Ablage unter `tools/` steht aus.
- Die Diagramme liegen auf der Auswertungsseite (Claude-Artifact „Use-Case Ablagegenauigkeit“).
