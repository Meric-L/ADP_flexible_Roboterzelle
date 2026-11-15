# Positionsgenauigkeit Layer 1 und Layer 2 (Use Case „Würfel ablegen“)

**Stand:** 15.11.2026 · **Datengrundlage:** Branch `handauge-schachbrett-offline`, `data/use-case/reihe_010/` (Commits `9d22307` vom 11.11. und `8a83a17` vom 13.11.2026) und `data/use-case/reihe_001/`  
**Erstellt aus:** den Versuchs-JSONs (`versuch_NNN.json`), Felder `client` und `messung`. Alle Zahlen sind direkt daraus berechnet.

## 1. Kurzfassung

| | Layer 1 (nur Deckenkamera) | Layer 2 (mit Handkamera) |
| --- | --- | --- |
| Versuche (13.11., Positionen A–E) | 25 | 25 |
| Ablagefehler r, Mittel | 10,7 mm | 5,5 mm |
| Ablagefehler r, Median | 9,2 mm | 4,6 mm |
| Ablagefehler r, Maximum | 33,0 mm | 11,8 mm |
| Versuche mit r ≤ 5 mm | 4 von 25 | 15 von 25 |

- Layer 2 halbiert den Ablagefehler gegenüber Layer 1 etwa.
- Layer 2 ist sehr wiederholgenau (Streuung je Position meist unter 1 mm). Was bleibt, ist ein fester Versatz je Position von 2–8 mm.
- Layer 1 streut je Position 4–10 mm und hat an den Positionen A, B und C einen gemeinsamen Versatz von 11–17 mm in −y.

## 2. Versuchsaufbau

- **Ablauf:** Der Franka-Roboter nimmt einen Würfel und legt ihn auf einem Messblatt auf dem Modul „Tisch leer mit Rad“ (`module-MOD-8`) ab. Danach fotografiert die Handkamera das Blatt aus 0,2 m Höhe, und die Bildauswertung misst, wie weit der Würfel vom Ziel liegt.
- **Layer 1** (`table.precision = coarse`): Die Tischpose kommt nur von der Deckenkamera.
- **Layer 2** (`table.precision = precise`): Die Tischpose wird zusätzlich mit der Handkamera nachgemessen.
- **Würfel:** 20 mm Kantenlänge, Marker ID 10 (18 mm) auf der Oberseite.
- **Handkamera:** RealSense, 1920 × 1080, Kalibrierung und Hand-Auge `cam_flange@2026-11-10T10:41:51Z-offline`, Subpixel-Ecken an (`subpix`).
- **Ablagehöhe** (`dropHeightMm`): −12 bis −16 mm. **Geschwindigkeit** (`maxSpeed`): 0,18–0,23.
- **Ziel:** Mitte des Tisches (`place.source = centre`), bei Versuch 063–064 ein Tag (`tag`).

## 3. Bedeutung der Messwerte

| Feld | Bedeutung |
| --- | --- |
| `dx`, `dy` | Versatz der Würfelmitte gegenüber dem Ziel in Blattkoordinaten, in mm |
| `r` | Betrag des Versatzes, √(dx² + dy²), in mm |
| `dθ` | Verdrehung des Würfels gegenüber dem Blatt, in Grad |
| Bezug | `sheet` = Messblatt mit Markern, `tag` = Rand eines AprilTags als Bezug |
| Zone | Lage der äußersten Würfelecke in den Toleranzkreisen des Blatts. Kreisdurchmesser = Würfeldiagonale 28,3 mm + 2 × 1 / 3 / 5 mm. Ohne Verdrehung entspricht das grob r ≤ 1 mm (grün), ≤ 3 mm (gelb), ≤ 5 mm (rot), darüber „außerhalb“. Verdrehung verschiebt die Zone nach außen. |

## 4. Ergebnis je Position (13.11.2026, je 5 Versuche pro Layer)

Position = Tischmitte in Weltkoordinaten (x / y in m). Streuung = Standardabweichung des Versatzvektors (dx, dy).

| Position | Layer | Versuche | dx Mittel | dy Mittel | r Mittel | r max | Streuung | dθ Mittel |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A (1,29 / 0,27) | L1 | 011–015 | −1,8 mm | −16,6 mm | 17,0 mm | 33,0 mm | 9,9 mm | −0,84° |
|  | L2 | 016–020 | −4,5 mm | −6,5 mm | 7,9 mm | 8,1 mm | 0,4 mm | −0,46° |
| B (0,41 / 0,23) | L1 | 021–025 | −3,5 mm | −11,7 mm | 12,5 mm | 19,3 mm | 6,7 mm | −0,49° |
|  | L2 | 026–030 | −5,6 mm | −5,6 mm | 8,6 mm | 11,8 mm | 4,4 mm | −0,17° |
| C (−0,12 / 0,86) | L1 | 031–035 | 1,4 mm | −11,4 mm | 12,6 mm | 24,1 mm | 9,2 mm | −2,20° |
|  | L2 | 036–040 | −0,9 mm | −4,4 mm | 4,5 mm | 4,6 mm | 0,6 mm | −1,10° |
| D (−0,11 / 1,32) | L1 | 041–045 | 0,2 mm | −3,9 mm | 4,6 mm | 9,2 mm | 4,4 mm | −0,44° |
|  | L2 | 046–050 | 1,4 mm | −1,7 mm | 2,2 mm | 2,3 mm | 0,3 mm | −0,38° |
| E (1,18 / 1,43) | L1 | 051–055 | 4,1 mm | −2,6 mm | 6,9 mm | 9,6 mm | 5,7 mm | −0,56° |
|  | L2 | 056–060 | 0,7 mm | −3,9 mm | 4,0 mm | 4,8 mm | 0,7 mm | −0,35° |

## 5. Beobachtungen

- **Layer 2 wiederholt sehr gut.** Streuung je Position 0,3–0,7 mm, nur Position B 4,4 mm. Der verbleibende Fehler ist ein fester Versatz je Position (2–8 mm). Das spricht für einen systematischen Anteil, etwa aus Hand-Auge-Kalibrierung oder Tag-Einmessung, nicht für Zufall.
- **Layer 1 streut deutlich mehr** (4,4–9,9 mm je Position).
- **Layer 1 hat einen ortsabhängigen Versatz:** An A, B und C (y ≤ 0,86 m) liegt der Würfel im Mittel 11–17 mm zu weit in −y, an D und E (y ≥ 1,32 m) nur 2,6–3,9 mm. Mögliche Ursachen: Kalibrierung oder Verzerrung der Deckenkamera zum Bildrand hin. Nicht geprüft.
- **Vorserie 11.11.** (Versuch 001–010, nur Layer 2, Position 1,45 / 0,78): sechs Versuche mit 4,2–7,5 mm, vier Ausreißer mit 15,8–45,6 mm (006, 007, 008, 010). Ob dabei etwas gestört hat, geht aus den Daten nicht hervor.

## 6. Alle Versuche, Reihe 10

| Nr. | Datum, Zeit | Layer | Tisch x / y (m) | Bezug | dx | dy | r | dθ | Zone | Drop (mm) | maxSpeed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 001 | 11.11. 14:30 | L2 | 1,45 / 0,78 | sheet | −2,6 | −3,5 | 4,3 | −0,95° | rot | −14,0 | 0,18 |
| 002 | 11.11. 14:34 | L2 | 1,45 / 0,78 | sheet | −4,6 | −5,0 | 6,8 | −1,20° | ausserhalb | −14,0 | 0,18 |
| 003 | 11.11. 14:40 | L2 | 1,45 / 0,78 | sheet | −2,8 | −3,5 | 4,5 | −0,81° | rot | −14,0 | 0,18 |
| 004 | 11.11. 14:42 | L2 | 1,45 / 0,78 | sheet | −4,8 | −5,8 | 7,5 | −1,05° | ausserhalb | −14,0 | 0,23 |
| 005 | 11.11. 14:47 | L2 | 1,45 / 0,79 | sheet | −2,6 | −3,4 | 4,2 | 0,04° | rot | −14,0 | 0,23 |
| 006 | 11.11. 14:48 | L2 | 1,45 / 0,79 | sheet | 14,7 | 15,4 | 21,3 | −2,54° | ausserhalb | −14,0 | 0,23 |
| 007 | 11.11. 14:50 | L2 | 1,45 / 0,79 | sheet | 14,0 | 10,3 | 17,4 | −3,25° | ausserhalb | −14,0 | 0,23 |
| 008 | 11.11. 14:52 | L2 | 1,45 / 0,79 | sheet | 41,8 | 18,1 | 45,6 | −3,52° | ausserhalb | −14,0 | 0,23 |
| 009 | 11.11. 14:53 | L2 | 1,45 / 0,79 | sheet | −6,3 | 1,0 | 6,3 | 0,07° | ausserhalb | −14,0 | 0,23 |
| 010 | 11.11. 14:54 | L2 | 1,45 / 0,79 | sheet | 9,5 | 12,7 | 15,8 | −1,09° | ausserhalb | −14,0 | 0,23 |
| 011 | 13.11. 10:22 | L1 | 1,30 / 0,28 | sheet | −2,9 | −32,8 | 33,0 | 0,32° | ausserhalb | −14,0 | 0,23 |
| 012 | 13.11. 10:23 | L1 | 1,29 / 0,27 | sheet | 2,8 | −10,8 | 11,2 | −2,42° | ausserhalb | −14,0 | 0,23 |
| 013 | 13.11. 10:25 | L1 | 1,29 / 0,27 | sheet | 0,6 | −10,7 | 10,7 | −4,86° | ausserhalb | −14,0 | 0,23 |
| 014 | 13.11. 10:26 | L1 | 1,29 / 0,27 | sheet | −5,1 | −13,9 | 14,8 | 1,83° | ausserhalb | −14,0 | 0,23 |
| 015 | 13.11. 10:27 | L1 | 1,29 / 0,27 | sheet | −4,4 | −14,9 | 15,5 | 0,93° | ausserhalb | −14,0 | 0,23 |
| 016 | 13.11. 10:33 | L2 | 1,29 / 0,26 | sheet | −3,9 | −6,6 | 7,7 | −0,75° | ausserhalb | −14,0 | 0,23 |
| 017 | 13.11. 10:35 | L2 | 1,29 / 0,26 | sheet | −4,5 | −6,3 | 7,8 | −0,78° | ausserhalb | −14,0 | 0,23 |
| 018 | 13.11. 10:36 | L2 | 1,29 / 0,26 | sheet | −4,5 | −6,8 | 8,1 | 0,66° | ausserhalb | −14,0 | 0,23 |
| 019 | 13.11. 10:38 | L2 | 1,29 / 0,26 | sheet | −4,7 | −6,4 | 8,0 | −0,90° | ausserhalb | −14,0 | 0,23 |
| 020 | 13.11. 10:40 | L2 | 1,29 / 0,26 | sheet | −4,9 | −6,5 | 8,1 | −0,52° | ausserhalb | −14,0 | 0,23 |
| 021 | 13.11. 11:04 | L1 | 0,41 / 0,24 | sheet | −7,4 | −17,8 | 19,3 | −2,13° | ausserhalb | −14,0 | 0,20 |
| 022 | 13.11. 11:06 | L1 | 0,41 / 0,23 | sheet | 1,8 | −3,7 | 4,1 | −3,95° | rot | −14,0 | 0,20 |
| 023 | 13.11. 11:08 | L1 | 0,42 / 0,24 | sheet | −4,2 | −11,8 | 12,5 | −1,40° | ausserhalb | −14,5 | 0,20 |
| 024 | 13.11. 11:11 | L1 | 0,41 / 0,25 | sheet | −5,6 | −16,1 | 17,0 | −1,53° | ausserhalb | −12,0 | 0,20 |
| 025 | 13.11. 11:13 | L1 | 0,41 / 0,22 | sheet | −2,0 | −9,3 | 9,6 | 6,57° | ausserhalb | −12,0 | 0,20 |
| 026 | 13.11. 11:22 | L2 | 0,42 / 0,22 | sheet | −10,7 | −5,1 | 11,8 | −1,46° | ausserhalb | −12,0 | 0,20 |
| 027 | 13.11. 11:25 | L2 | 0,42 / 0,22 | sheet | −9,0 | −4,4 | 10,1 | 2,23° | ausserhalb | −15,5 | 0,20 |
| 028 | 13.11. 11:27 | L2 | 0,42 / 0,22 | sheet | −2,1 | −7,5 | 7,7 | 1,86° | ausserhalb | −16,0 | 0,20 |
| 029 | 13.11. 11:29 | L2 | 0,42 / 0,22 | sheet | −1,7 | −7,3 | 7,5 | −0,62° | ausserhalb | −16,0 | 0,20 |
| 030 | 13.11. 11:33 | L2 | 0,42 / 0,22 | sheet | −4,2 | −3,9 | 5,8 | −2,88° | ausserhalb | −16,0 | 0,20 |
| 031 | 13.11. 11:35 | L1 | −0,12 / 0,86 | sheet | −0,6 | −8,2 | 8,3 | −1,13° | ausserhalb | −16,0 | 0,20 |
| 032 | 13.11. 11:37 | L1 | −0,13 / 0,86 | sheet | −3,2 | −7,2 | 7,9 | −1,95° | ausserhalb | −16,0 | 0,20 |
| 033 | 13.11. 11:39 | L1 | −0,13 / 0,86 | sheet | 2,5 | −7,6 | 8,0 | −2,53° | ausserhalb | −16,0 | 0,20 |
| 034 | 13.11. 11:40 | L1 | −0,11 / 0,87 | sheet | −5,5 | −13,8 | 14,8 | −3,26° | ausserhalb | −16,0 | 0,20 |
| 035 | 13.11. 11:41 | L1 | −0,11 / 0,86 | sheet | 13,6 | −19,9 | 24,1 | −2,11° | ausserhalb | −16,0 | 0,20 |
| 036 | 13.11. 11:47 | L2 | −0,11 / 0,91 | sheet | −1,7 | −4,3 | 4,6 | −0,38° | rot | −16,0 | 0,20 |
| 037 | 13.11. 11:50 | L2 | −0,11 / 0,91 | sheet | −1,3 | −4,3 | 4,5 | −2,12° | rot | −16,0 | 0,20 |
| 038 | 13.11. 11:52 | L2 | −0,11 / 0,91 | sheet | −0,7 | −4,6 | 4,6 | −1,88° | rot | −16,0 | 0,20 |
| 039 | 13.11. 11:55 | L2 | −0,13 / 0,88 | sheet | −1,0 | −4,3 | 4,4 | −0,70° | rot | −16,0 | 0,20 |
| 040 | 13.11. 11:58 | L2 | −0,13 / 0,88 | sheet | −0,1 | −4,5 | 4,5 | −0,41° | rot | −16,0 | 0,20 |
| 041 | 13.11. 12:06 | L1 | −0,11 / 1,32 | sheet | 4,9 | −4,6 | 6,7 | −1,30° | ausserhalb | −16,0 | 0,20 |
| 042 | 13.11. 12:04 | L1 | −0,12 / 1,32 | sheet | −0,2 | −0,2 | 0,3 | 0,15° | gruen | −16,0 | 0,20 |
| 043 | 13.11. 12:11 | L1 | −0,12 / 1,32 | sheet | −1,2 | −3,5 | 3,7 | 0,09° | rot | −16,0 | 0,20 |
| 044 | 13.11. 12:15 | L1 | −0,11 / 1,33 | sheet | −0,5 | −9,2 | 9,2 | −1,21° | ausserhalb | −16,0 | 0,20 |
| 045 | 13.11. 12:17 | L1 | −0,12 / 1,32 | sheet | −2,2 | −1,9 | 2,9 | 0,06° | gelb | −16,0 | 0,20 |
| 046 | 13.11. 12:20 | L2 | −0,11 / 1,32 | sheet | 1,6 | −1,6 | 2,2 | −0,35° | gelb | −16,0 | 0,20 |
| 047 | 13.11. 12:24 | L2 | −0,11 / 1,32 | sheet | 1,3 | −1,4 | 1,9 | −0,18° | gelb | −16,0 | 0,20 |
| 048 | 13.11. 12:27 | L2 | −0,11 / 1,32 | sheet | 1,6 | −1,6 | 2,3 | −0,17° | gelb | −16,0 | 0,20 |
| 049 | 13.11. 12:33 | L2 | −0,11 / 1,32 | sheet | 1,1 | −2,0 | 2,3 | −0,66° | gelb | −16,0 | 0,20 |
| 050 | 13.11. 12:35 | L2 | −0,11 / 1,32 | sheet | 1,4 | −1,9 | 2,3 | −0,51° | gelb | −16,0 | 0,20 |
| 051 | 13.11. 12:38 | L1 | 1,18 / 1,43 | sheet | −5,3 | −3,9 | 6,6 | −1,77° | ausserhalb | −16,0 | 0,20 |
| 052 | 13.11. 12:39 | L1 | 1,18 / 1,43 | sheet | 5,4 | −3,1 | 6,3 | −0,59° | ausserhalb | −16,0 | 0,20 |
| 053 | 13.11. 12:42 | L1 | 1,18 / 1,43 | sheet | 6,8 | −0,3 | 6,8 | 0,11° | ausserhalb | −16,0 | 0,20 |
| 054 | 13.11. 12:43 | L1 | 1,19 / 1,43 | sheet | 8,3 | −4,7 | 9,6 | −0,72° | ausserhalb | −16,0 | 0,20 |
| 055 | 13.11. 12:46 | L1 | 1,18 / 1,43 | sheet | 5,2 | −0,9 | 5,2 | 0,15° | rot | −16,0 | 0,20 |
| 056 | 13.11. 12:50 | L2 | 1,19 / 1,42 | sheet | −0,0 | −4,1 | 4,1 | −0,62° | rot | −16,0 | 0,20 |
| 057 | 13.11. 12:53 | L2 | 1,19 / 1,42 | sheet | 1,1 | −4,7 | 4,8 | −0,32° | rot | −16,0 | 0,20 |
| 058 | 13.11. 12:56 | L2 | 1,19 / 1,42 | sheet | 1,0 | −3,8 | 3,9 | −0,30° | rot | −16,0 | 0,20 |
| 059 | 13.11. 12:57 | L2 | 1,19 / 1,42 | sheet | 0,8 | −3,5 | 3,6 | −0,30° | rot | −16,0 | 0,20 |
| 060 | 13.11. 13:00 | L2 | 1,19 / 1,41 | sheet | 0,7 | −3,5 | 3,6 | −0,23° | rot | −16,0 | 0,20 |
| 061 | 13.11. 13:04 | L2 | 1,51 / 0,72 | sheet | −3,2 | −3,2 | 4,5 | −0,09° | rot | −16,0 | 0,20 |
| 062 | 13.11. 13:07 | L2 | 1,51 / 0,72 | sheet | −4,5 | −5,3 | 6,9 | −0,01° | ausserhalb | −16,0 | 0,20 |
| 063 | 13.11. 13:14 | L1 | 1,43 / 0,16 | tag | 6,5 | −12,2 | 13,8 | 0,49° | ausserhalb | −16,0 | 0,20 |
| 064 | 13.11. 13:18 | L2 | 1,12 / 0,96 | tag | 3,5 | 3,1 | 4,7 | 0,70° | rot | −16,0 | 0,20 |

dx, dy, r in mm.

## 7. Ältere Versuche, Reihe 1

Alle mit Layer 2. Versuch 028 vom 09.11., die übrigen vom 11.11.2026.

| Nr. | Datum, Zeit | Layer | Tisch x / y (m) | Bezug | dx | dy | r | dθ | Zone |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 028 | 09.11. 09:38 | L2 | 1,48 / 0,76 | – | 3,7 | −2,0 | 4,2 | −2,50° | rot |
| 029 | 11.11. 12:11 | L2 | 0,26 / 0,51 | sheet | – | – | – | – | Fehler: Kein Wuerfelmarker (ID 10) erkannt |
| 030 | 11.11. 12:13 | L2 | 0,26 / 0,51 | sheet | 1,4 | 2,7 | 3,1 | 1,96° | gelb |
| 031 | 11.11. 12:16 | L2 | 0,26 / 0,51 | sheet | 0,0 | 3,5 | 3,5 | −6,16° | gelb |
| 032 | 11.11. 12:21 | L2 | 0,26 / 0,51 | sheet | 3,2 | 5,7 | 6,5 | −1,76° | ausserhalb |
| 033 | 11.11. 12:27 | L2 | 0,25 / 0,51 | tag | – | – | – | – | Fehler: Kein Wuerfelmarker (ID 10) erkannt |
| 034 | 11.11. 12:29 | L2 | 0,25 / 0,51 | sheet | 0,6 | 5,1 | 5,1 | −7,56° | rot |
| 035 | 11.11. 12:30 | L2 | 0,25 / 0,51 | sheet | −1,8 | 3,9 | 4,3 | −0,34° | rot |
| 036 | 11.11. 12:31 | L2 | 0,25 / 0,51 | sheet | −1,8 | 3,8 | 4,2 | −0,40° | rot |
| 037 | 11.11. 12:34 | L2 | 0,25 / 0,51 | sheet | −1,7 | 4,2 | 4,5 | −0,71° | rot |
| 038 | 11.11. 12:37 | L2 | 0,55 / 0,30 | sheet | −3,8 | 2,5 | 4,6 | −0,16° | rot |
| 039 | 11.11. 12:39 | L2 | 0,55 / 0,30 | sheet | −4,8 | 1,4 | 5,0 | −0,56° | rot |
| 040 | 11.11. 12:47 | L2 | 0,83 / 0,26 | tag | – | – | – | – | Fehler: Kein Wuerfelmarker (ID 10) erkannt |
| 041 | 11.11. 12:48 | L2 | 0,83 / 0,26 | sheet | – | – | – | – | Fehler: Kein Wuerfelmarker (ID 10) erkannt |
| 042 | 11.11. 12:54 | L2 | 1,39 / 0,84 | sheet | 8,7 | −3,9 | 9,5 | −0,22° | ausserhalb |
| 043 | 11.11. 12:56 | L2 | 1,39 / 0,84 | sheet | 8,3 | −3,7 | 9,0 | −0,10° | ausserhalb |

## 8. Dateien

- Je Versuch: `versuch_NNN.json` (alle Werte), `versuch_NNN.png` (Foto der Handkamera), `versuch_NNN_messung.png` (Auswertebild mit Würfel, Blattmarkern und Zonen).
- Bildauswertung: `src/use_case_eval/` (`measure.py`, `target.py`), Auswerteskript `tools/use_case_auswertung.py`.
