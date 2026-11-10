# Wiederholbarkeit der Kalibrierung: drei Läufe vom 10.11.2026

Drei unabhängige Hand-Auge-Rohaufnahmen in 1920×1080, ChArUco (gemessener Maßstab 274,8 mm / 11 Felder), jeweils mit Bildrand-Gruppe F, ohne durch Armbewegung verworfene Bilder. Jeder Lauf offline für sich gerechnet (eigene Intrinsik, Hand-Auge starr), zusätzlich mit gemeinsamer Intrinsik. Skript: `tools/handeye_offline_cv4/laeufe_vergleich.py`, Zahlen: `doc/auswertung/handeye_cv4/laeufe_vergleich_20261110.json`.

| Lauf | Posen | online akzeptiert |
|---|---|---|
| L1 `20261110T104151Z_77e0dd17` (10:42 UTC) | 59 | 49 (Pi noch mit alter Intrinsik) |
| L2 `20261110T112640Z_7919557e` (11:27 UTC) | 41 | 41 |
| L3 `20261110T113752Z_dca1c37b` (11:38 UTC) | 57 (55 auswertbar) | 54 |

## Intrinsik (volles Modell)

| Lauf | RMS | fx / fy | cx / cy | k1 / k2 / k3 |
|---|---|---|---|---|
| L1 | 0,274 px | 1356,7 / 1357,3 | 988,3 / 547,6 | 0,138 / −0,427 / 0,367 |
| L2 | 0,226 px | 1358,9 / 1358,9 | 987,8 / 549,0 | 0,126 / −0,382 / 0,298 |
| L3 | 0,240 px | 1357,4 / 1357,4 | 988,3 / 548,8 | 0,124 / −0,356 / 0,257 |
| gemeinsam (155 Bilder) | 0,253 px | 1357,8 / 1357,9 | 988,1 / 547,5 | 0,131 / −0,402 / 0,336 |

Spannweite fx 2,2 px (0,16 %), cx 0,5 px, cy 1,4 px. Kreuztest (Intrinsik eines Laufs auf die Bilder eines anderen): höchstens 0,05 px schlechter als die eigene. Die Intrinsik ist reproduzierbar.

## Hand-Auge

| Lauf | Reprojektion | Brettstreuung | t (mm) | Neigung / Verdrehung |
|---|---|---|---|---|
| L1 | 3,63 px | 1,41 mm | −47,32 / −34,24 / 60,26 | 0,36° / 90,52° |
| L2 | 2,77 px | 1,00 mm | −46,38 / −36,06 / 59,85 | 0,17° / 90,60° |
| L3 | 5,41 px | 1,68 mm | −47,55 / −35,69 / 60,10 | 0,28° / 90,55° |

- Translation: Mittel −47,08 / −35,33 / 60,07 mm, σ je Achse 0,62 / 0,96 / 0,20 mm, größter Paarabstand 2,09 mm (L1–L2); L1–L3 1,48 mm, L2–L3 1,25 mm.
- Rotation: Paarunterschiede 0,13–0,25°.
- Mit gemeinsamer Intrinsik praktisch identisch (Unterschied ≤ 0,2 mm): Die Streuung kommt aus der Hand-Auge-Schätzung, nicht aus der Intrinsik.

## Was das für einen gemessenen Punkt bedeutet

Translation und Neigung der Lösungen gleichen sich teilweise aus. Entscheidend ist, wo ein von der Kamera gemessener Punkt im Flansch landet:

| Abstand | Bildmitte: max. Abstand zweier Läufe / RMS um Mittel | Bildecken und -ränder |
|---|---|---|
| 300 mm | 0,91 / 0,44 mm | 1,26–1,30 / 0,56–0,58 mm |
| 380 mm (typ. Arbeitsabstand) | 0,61 / 0,31 mm | 1,20–1,29 / 0,51–0,57 mm |
| 500 mm | 0,32 / 0,15 mm | 1,29–1,54 / 0,54–0,64 mm |

In der Bildmitte im Arbeitsabstand liegen die Läufe innerhalb ~0,6 mm (RMS 0,3 mm), an den Bildrändern innerhalb ~1,3 mm. Die Streuung *innerhalb* eines Laufs (Brettstreuung 1,0–1,7 mm) bleibt die größere Fehlerquelle; L3 ist der schwächste Lauf.
