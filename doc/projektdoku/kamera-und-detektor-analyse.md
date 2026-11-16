# Deckenkamera: Bildqualität, Kamera-Einstellungen und Detektor-Parameter

**Messung:** 19.10.2026 · **Kamera:** Pi 1, `cam_ceiling`, Raspberry Pi HQ Camera (IMX477), M12-Fischauge mit fester Blende, 4056×3040 · **Familie:** tag36h11, Modultags 8 cm

Messbericht mit Stichpunkten und Belegen. Die Rohbilder liegen im Repo, die Abbildungen unter `bilder/kamera-analyse/`.
Zugehörige Arbeitspläne: [`arbeitsplaene/apriltag-erkennung-unscharfe-tags.md`](arbeitsplaene/apriltag-erkennung-unscharfe-tags.md),
[`arbeitsplaene/kamera-controls-und-detektor-parameter.md`](arbeitsplaene/kamera-controls-und-detektor-parameter.md),
[`arbeitsplaene/deckenkamera-subpixel-ecken.md`](arbeitsplaene/deckenkamera-subpixel-ecken.md).

## 1. Datengrundlage

| Bild | Inhalt | Ort |
| --- | --- | --- |
| A, „unscharf“ | erstes Rohbild, Fokus verstellt | `data/rohbilder/cam_ceiling_20261019-114704.png` (Branch `apriltag`) |
| B, „Fokus neu“ | nach Nachstellen des Fokus, Szene umgebaut | `data/rohbilder/cam_ceiling_20261019-142133.png` (Branch `apriltag`) |
| C, Einstellungstest | 14 Aufnahmen hintereinander, je eine Kamera-Einstellung, konstantes Licht | `data/rohbilder/camera_settings_test/20261019-151652/` (nur Branch `kamera-einstellungen-test`) |

- Aufnahme über den Button „📷 Rohbild speichern“ (OPC-UA-Methode `SaveRawImage`, PNG verlustfrei) bzw. das Skript `tools/kamera_einstellungen_test.py`.
- Ground Truth (vom Team bestätigt): A enthält 0, 1, 2, 3, 583. B enthält 0, 1 (zweimal), 2, 3, 583, 586 sowie 585 (beschädigt, optional).
- Detektor: OpenCV-ArUco (`cv2.aruco`, OpenCV 5.0.0 am PC), Graubild ohne Vorverarbeitung, wie im Server (`tagloc.detector.ArucoTagDetector`).

## 2. Ausgangsproblem: unscharfes Bild (A)

- Standard-Detektor: **[1, 2, 583]**. Es fehlen 0 und 3.
- Die Vierecke von 0 und 3 **werden gefunden** (sie liegen in `rejectedImgPoints`). Es scheitert erst das Auslesen der Bits.
  - Tag 0: etwa 60 px Kantenlänge, **4 Bitfehler**. Mit Standard-`errorCorrectionRate` 0.6 sind höchstens 3 korrigierbar.
  - Tag 3: etwa 50 px Kantenlänge, Kontrast nur **60 Graustufen** (Weiß 221, Rand 161), wird falsch dekodiert.
- Ursache ist Defokus: Das Bild ist optisch weich. Kantenbreite (10–90 %) **4–8 px** bei einer Zellgröße von nur 6–13 px. Keine Vorzugsrichtung, also keine Bewegungsunschärfe. Rauschen ist mit einer Standardabweichung unter 1,3 Graustufen vernachlässigbar.
- Weiß ist teilweise ausgebrannt: Tag 1 zu 17 %, Tag 0 zu 6 %, im ganzen Bild 3 % ≥ 250.

### 2.1 Software-Gegenmaßnahmen

Gemessen auf Bild A, Laufzeit am PC auf 12 MP. Der Pi 4 ist grob 5–10× langsamer.

| Variante | erkannte IDs | Zeit |
| --- | --- | --- |
| OpenCV-Standard | 1, 2, 583 | 0,13 s |
| `minMarkerPerimeterRate`, `polygonalApproxAccuracyRate`, `useAruco3Detection`, `adaptiveThreshWinSize*`, Farbkanäle, Schärfen, Skalieren | 1, 2, 583 (teils weniger) | 0,06–0,35 s |
| CLAHE (Clip 2, 8×8) | 0, 1, 2, 3, 583 | 0,14 s |
| **`errorCorrectionRate` 1.0, `perspectiveRemoveIgnoredMarginPerCell` 0.3, `perspectiveRemovePixelPerCell` 8** | **0, 1, 2, 3, 583** | **0,09 s** |
| `CORNER_REFINE_APRILTAG` (Subpixel) | nur 583 | 2,7 s |

- Die drei Dekodier-Parameter wirken genau auf den gescheiterten Schritt: Feineres Raster (8 statt 4 px je Zelle), nur die Zellmitte zählt (Rand 0.3 statt 0.13), 5 statt 3 korrigierbare Bits.
- Robust: Rand 0.2–0.4 und ppc 8–10 finden alle fünf Tags.
- Preis: Das theoretische Fehlalarm-Risiko je Viereck-Kandidat steigt von etwa 0,03 % auf etwa 1,5 %. Auf 41 Bildern ohne Tags gab es 0 Fehlalarme.
- **CLAHE nicht verwenden:** erzeugt auf anderen Bildern Fehlalarme (Pseudo-Tags aus verstärktem Rauschen, z. B. ID 564, ID 80).
- **`CORNER_REFINE_APRILTAG` nicht verwenden:** ersetzt die Viereck-Suche durch einen eigenen Quad-Detektor auf dem Vollbild (20× langsamer) und verliert Tags, weil die um etwa 1 px verschobenen Vierecke mit dem Standard-Raster nicht mehr dekodierbar sind.
- Fazit: Software fängt einen Defokus nur teilweise auf, und nur auf Kosten der Fehlalarm-Sicherheit.

## 3. Hardware-Maßnahme: Fokus nachgestellt (B)

- Die M12-Optik hat **keine Blende**. Einstellbar sind nur der Fokus (Objektiv im Gewinde drehen) und die ISP- bzw. Belichtungs-Controls.
- Nach dem Fokussieren findet der **OpenCV-Standard** alle Tags: **[0, 1, 1, 2, 3, 583, 586]**. Beide physischen Tags mit ID 1 werden erkannt.
- Tag 585 (beschädigt) wird nur mit gelockerten Parametern gefunden. Er wird neu gedruckt, statt dafür zu optimieren.
- Die Software-Parameter aus Abschnitt 2.1 sind damit nicht nötig (Ausnahme: `perspectiveRemovePixelPerCell` 8, siehe Abschnitt 5).
- Detektor-Parameter und Kamera-Controls sind ohne Codeänderung einstellbar, über das Preset oder die Umgebungsvariablen `VISION_APRILTAG_DETECTOR_PARAMS` und `VISION_CAMERA_CONTROLS` (siehe [`vision-system.md`](vision-system.md), Abschnitt „Kamera-Controls (Picamera2)“).

## 4. Kamera-Einstellungen im Vergleich (C)

### 4.1 Versuch

- 14 Aufnahmen. Je Aufnahme wird die Kamera neu geöffnet, damit keine Einstellung in die nächste nachwirkt.
- Jede Einstellung ändert gegenüber der Automatik nur eine Stellschraube (Nummern siehe Tabelle 4.2). Automatik 00, 09 und 99 sind praktisch identisch (Belichtung 20,8 ms, Gain 2,0, etwa 355 lx). Das Licht war also konstant.
- Ergebnis: **Alle 14 Einstellungen** erkennen [0, 1, 2, 3, 583, 586] mit **Bitfehler 0** bei allen Tags. Alle Tags werden sogar mit `errorCorrectionRate` 0.0 gefunden. Die zweite ID 1 war vom Roboterarm verdeckt; das ist kein Erkennungsfehler.
- Weil alle Einstellungen alle Tags finden, wird die **Reserve** gemessen: Die Bilder werden künstlich verschlechtert (Unschärfe 2–5, Verkleinern ×0,3–×0,2, Rauschen σ 24/40, Helligkeit ×0,5/×0,3). Gezählt wird die Summe der Bitfehler. Kleiner heißt mehr Reserve.

### 4.2 Ergebnis

![Stress-Rangliste](bilder/kamera-analyse/04_stress_rangliste.png)

*Abb. 4: Summe der Bitfehler unter künstlicher Verschlechterung, je Einstellung. Grau hinterlegt ist die Streuung der drei identischen Automatik-Bilder (±2–3). Unterschiede ab etwa 8–10 Punkten sind belastbar.*

| Rang | Einstellung | Stress-Fehler | Marge* | Ausgebrannt (Tag 0/1/3) | Bewertung |
| --- | --- | --- | --- | --- | --- |
| 1 | 12 Kombi (EV −0.5, Schärfe 1.5, Kontrast 1.15, NR Fast) | 125 | 44 | 1 / 0,1 / 13,5 % | beste Schwarz-Weiß-Trennung ohne Clipping |
| 2 | 02 EV −1.0 | 123 | 37 | 0 % | gleichauf, aber sehr dunkel; riskant bei weniger Licht |
| 3 | 04 Schärfe 2.5 | 127 | 42 | 30 / 12 / 51 % | mehr Rauschen, leichter Halo |
| 4 | 11 fest −0,7 EV | 128 | 38 | 0 % | |
| 5 | 01 EV −0.5 | 131 | 37 | 0 % | |
| 6 | 03 Schärfe 1.5 | 134 | 41 | 22 / 7 / 45 % | |
| 7 | 10 halbe Zeit, doppelter Gain | 137 | 41 | 24 / 10 / 45 % | |
| 8–12 | 00/09/99 Automatik, 06 NR Off, 07 NR HQ | 139–142 | 34–40 | 15–21 / 5–7 / 38–48 % | Referenz |
| 13 | 05 Kontrast 1.25 | 145 | 43,5 | 100 % | Clipping |
| 14 | 08 Matrix-Messung | 152 | 28 | 100 % | überbelichtet, schlechteste Marge |

\* Marge = kleinster Abstand einer Zelle zum Otsu-Schwellwert, in Graustufen, Minimum über alle Tags.

- **Belichtung leicht runter hilft am meisten.** In der Automatik ist das Weiß der Tags zu 15–48 % ausgebrannt. Überstrahltes Weiß läuft in die schwarzen Nachbarzellen. Die kleinen Tags (50–64 px) profitieren am stärksten.
- **Schärfe hilft auch bei gleicher Belichtung** (2.5: 127 statt 142). Das Rauschen steigt um etwa 45–75 %, absolut höchstens 1,7 Graustufen bei etwa 125 Graustufen Schwarz-Weiß-Abstand. Für die Dekodierung ist das irrelevant. Der schwarze Unterschwinger (Halo) wächst von 4 % auf 9 % (Abb. 7). Die Kantenbreite ändert sich nicht messbar (1,5–1,7 px). Nach dem Fokussieren ist die Optik nicht mehr der Engpass.
- **Kontrast 1.25 und Matrix-Messung nicht verwenden:** Beide brennen das Weiß der Tags vollständig aus.
- **Rauschunterdrückung ist egal** (Off, Fast und HighQuality liegen gleichauf). Off erhöht nur das Rauschen.
- **Eckgenauigkeit:** Dunklere Einstellungen liefern um 0,1–0,4 px größere Vierecke, bei Tag 586 bis 0,7 px. Das liegt über der Wiederholbarkeit, aber unter 1 px. Schärfe verschiebt die Ecken nicht systematisch. Die Kalibrierung sollte mit derselben Einstellung aufgenommen werden, die im Betrieb läuft.
- **Fehlalarme:** Mit Standardparametern gibt es in keinem Bild eine falsche ID. Die Zahl der verworfenen Kandidaten steigt mit Schärfe und Kontrast leicht (48 → 61/62). Mit stark gelockerten Parametern treten falsche IDs auf (315, 379).

![Kantenprofile](bilder/kamera-analyse/07_kantenprofile.png)

*Abb. 7: Kantenprofile über eine Schwarz-Weiß-Kante je Einstellung, mit Unterschwinger bei höherer Schärfe.*

## 5. Detektor-Parameter auf dem scharfen Bild

Gemessen auf Bild 12 und vier weiteren Bildern aus C, unter starker künstlicher Verschlechterung (Blur 3,5–5, Verkleinern ×0,25–×0,15, Rauschen σ 24). Maximal 54 Treffer.

| Parameter | Treffer (Bild 12) | Bemerkung |
| --- | --- | --- |
| OpenCV-Standard | 42 | keine Fehlalarme |
| `perspectiveRemovePixelPerCell` 8 | 45 | +3 bis +5 auf allen fünf Bildern, ohne Fehlalarme und ohne mehr Kandidaten |
| `adaptiveThreshWinSizeStep` 4 | 45 | kein Zusatzgewinn, mehr Kandidaten (66 statt 56) |
| `errorCorrectionRate` 1.0 | 48 | **falsche ID** in 2 von 5 Bildern bei Rauschen σ 24 |
| `errorCorrectionRate` 0.2 | 34 | weniger Reserve |
| `CORNER_REFINE_APRILTAG` | 23 statt 42 | verliert Reichweite, einzelne Ecke springt um 968 px |

- Auf den echten, ungestörten Bildern findet der Standard alles. Eine Änderung ist nur Reserve.
- Höhere Fehlerkorrektur kauft Reichweite mit Fehlalarmen. Für eine Zellüberwachung, die auf fehlende oder neue Tags reagiert, ist das der falsche Tausch; deshalb `errorCorrectionRate` ≤ 0.6.
- `CORNER_REFINE_APRILTAG` wird nicht verwendet (Reichweitenverlust). Zur Subpixel-Verfeinerung mit `CORNER_REFINE_SUBPIX` siehe 5.1.

### 5.1 Eckgenauigkeit: Subpixel-Ecken (`subpix`)

Ohne Verfeinerung liefert ArUco auf der Decke ganzzahlige Ecken. Ein 80-mm-Tag ist auf 1,6 m nur etwa 85 px groß. Gemessen wurde die Streuung der Ecken und der Drehung bei drei Lichtstufen (Helligkeitstest vom 09.11.):

| Licht | Ecken-sd ArUco, ganzzahlig | Ecken-sd pupil_apriltags, subpixelgenau | z-sd | Drehung RMS |
|---|---|---|---|---|
| 100 % | 0,68 px | 0,08 px | 5,9 mm | 8,7° |
| 80 % | 1,00 px | 0,11 px | 6,4 mm | 8,9° |
| 50 % | 1,07 px | 0,24 px | 8,0 mm | 9,1° |

- Das Eckenrauschen ist 5- bis 8-mal geringer als ohne Verfeinerung.
- `CORNER_REFINE_SUBPIX` auf 19 echten 12-MP-Bildern (alle Lichtstufen, PC, OpenCV 5.0) findet dieselben IDs wie ohne Verfeinerung. Die Laufzeit steigt um 3 % (Median 86,7 → 89,0 ms). Die Warnung zu Reichweite und CPU betrifft nur `CORNER_REFINE_APRILTAG`.
- **Entscheidung:** Preset `cam_ceiling` mit `subpix` an (Arbeitsplan [`arbeitsplaene/deckenkamera-subpixel-ecken.md`](arbeitsplaene/deckenkamera-subpixel-ecken.md)).
- Die Drehungs-Streuung von 8–9° kommt zu einem großen Teil aus der IPPE-Mehrdeutigkeit (etwa 2/3 der Bilder `ambiguous`). Subpixel beseitigt sie vermutlich nicht; siehe Abschnitt 7.

## 6. Empfehlung

| Was | Wert | Begründung |
| --- | --- | --- |
| Fokus | auf Bodenebene der Zelle, Kontermutter fest | größter Einzeleffekt (Abschnitt 3) |
| Kamera-Controls `cam_ceiling` | `{"ExposureValue": -0.5, "Sharpness": 1.5, "Contrast": 1.15, "NoiseReductionMode": "Fast"}` | Rang 1, kein Clipping, kein übermäßig dunkles Bild |
| Detektor-Parameter `cam_ceiling` | `{"perspectiveRemovePixelPerCell": 8}` (oder `{}`), dazu `subpix` an | kleine Reserve ohne Fehlalarm-Risiko; Subpixel-Ecken (5.1) |
| Nicht verwenden | `Contrast` ≥ 1.25, `AeMeteringMode: Matrix`, `errorCorrectionRate` > 0.6, CLAHE, `CORNER_REFINE_APRILTAG` | Clipping bzw. Fehlalarme bzw. Reichweitenverlust |
| Tag 585 | neu drucken | beschädigt, nur mit riskanten Parametern lesbar |

## 7. Einschränkungen und offene Punkte

- Die Reserve wurde mit **künstlicher** Verschlechterung gemessen. Echte schwierige Fälle fehlen: Tags am Bildrand, Schräglage, anderes Licht.
- Die Werte gelten für das Hallenlicht vom 19.10.2026 (etwa 355 lx). Bei deutlich weniger Licht ist EV −1.0 zu dunkel. EV −0.5 mit Automatik passt sich an.
- Noch nicht aufgenommen, aber vielversprechend: `{"ExposureValue": -0.5, "Sharpness": 2.5, "Contrast": 1.15}`.
- Am Pi läuft OpenCV 4.x, die Messungen am PC mit 5.0. Die Parameternamen sind gleich; ein Gegentest am Pi steht aus.
- Die Bestätigung von `subpix` am Pi mit einer Helligkeitstest-Reihe steht aus. Die Drehungs-Streuung wird vermutlich nicht allein durch Subpixel beseitigt, sondern zu einem großen Teil durch die IPPE-Mehrdeutigkeit.
- Die Kalibrierung (`data/calibration/cam_ceiling.json`) entstand vor dem Fokussieren. Eine Neukalibrierung mit den finalen Kamera-Einstellungen ist nötig, weil der Fokus die Brennweite leicht verändert.
- Auswerteskripte (Ground Truth, Bitfehler, Stresstests, Parameter-Sweeps) liegen nicht im Repo. Die Kennzahlen dieses Berichts stammen daraus.
