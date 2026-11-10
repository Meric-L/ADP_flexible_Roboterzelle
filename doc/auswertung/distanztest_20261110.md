# Distanzstück-Test der Handkamera (Tiefe), 10.11.2026

Session `data/rohbilder/roboterwiderholgenauigkeit/2026-11-10_143616` (Rauschserien, 4 Läufe à 10 Bilder, 1920×1080), Roboter in der Ausgangsstellung (Kamera ~0,35 m senkrecht über dem Tisch, webskill `5297d8a`). ChArUco 11×7 mit gemessenem Maßstab (274,8 mm / 11 Felder) nacheinander auf 0 / 19,9 / 39,8 / 59,7 mm Distanzstücken (Reihenfolge aus dem steigenden Bildmaßstab 3,95 → 4,16 → 4,44 → 4,74 px/mm). Je Bild PnP, Abstand Kamera–Brettebene = |n·t|.

| | Δ 19,9 mm (19,9 → 39,8) | Δ 39,8 mm (19,9 → 59,7) | Maßstabsfehler |
|---|---|---|---|
| aktive Kalibrierung (offline 10.11., fx 1356,7) | 19,955 mm (+0,055) | 39,796 mm (−0,004) | +0,05 % |
| Werks-Intrinsik D415 (fx 1369,8, Verzeichnung 0) | 20,347 mm (+0,447) | 40,146 mm (+0,346) | +1,14 % |

- Kalibrierfrei aus Bildmaßstab und Distanzstücken (Lochkamera, Verzeichnung der aktiven Kalibrierung): fx ≈ 1354 px — bestätigt die Kalibrierung (0,2 %), Werkswert 1,2 % zu hoch.
- Rauschen des Abstands je Lauf 6–12 µm.
- Lauf 0 (ohne Distanzstück) verworfen: gegenüber den Distanzstück-Läufen um konstant 2,86 mm versetzt (nicht proportional, also kein Maßstabsfehler), Brett 1,4° geneigt (sonst 0,1°), Abstandsstreuung 125 µm statt ~10 µm — Brett lag nicht ruhig/plan auf.

Nächster Schritt: derselbe Test seitlich (Distanzstücke zwischen festem Anschlag und Brettkante, x und y getrennt, optional bis an den Bildrand) für die Genauigkeit in x/y.

## Seitlicher Test (x/y), 10.11.2026 nachmittags

Gleiche Session, Bilder `r_041`–`r_087`, Kamera ~0,34 m. Brettkante gegen einen festen Anschlag (M8-Mutter, gegen das Gewinde gedrückt; diese Lage liegt laut Messschieber 2,25 mm vor der Anlagefläche der Distanzstücke, also Sollwert −2,25 mm), Distanzstücke 19,9 / 39,8 / 59,7 mm dazwischen. Ausgewertet wird die Verschiebung der Brettmitte entlang der Brettachse senkrecht zur Anschlagkante (das Brett rutschte beim Wechsel bis 19 mm quer). Zuordnung der Lagen aus den Abständen: x-Reihe 59,7 → 39,8 → 19,9 → Mutter (`r_041`, `r_042–051`, `r_052–054`, `r_055–064`), y-Reihe Mutter → 19,9 → 39,8 → 59,7 → Mutter (`r_065–068`, `r_069–074`, `r_075–076`, `r_077`, `r_078–087`).

| Reihe | Schritte soll → gemessen (aktive Kalibrierung) | Maßstab | Rest RMS |
|---|---|---|---|
| x | 19,90 → 20,028; 19,90 → 19,835; 22,15 → 22,151 | +0,06 % | 0,043 mm |
| y | 22,15 → 22,210; 19,90 → 19,995; 19,90 → 20,212 | +0,68 % | 0,064 mm |

- Mutter-Lage der y-Reihe zweimal angefahren: 0,012 mm Unterschied (Aufbau wiederholbar).
- Der größte Fehler (+0,31 mm, y, 39,8 → 59,7) ist ein Einzelbild am Bildrand (50 statt 60 Ecken) nach 19 mm seitlichem Rutschen; mit der Werks-Intrinsik (ohne Verzeichnung) fast gleich (+0,33 mm) → eher Aufbau als Verzeichnungsmodell.
- Seitlich praktisch unabhängig von der Intrinsik (Werk: x −0,26 %, y +0,74 %).
- Offen: Brettmaß in y nachmessen (7 Felder, soll 174,87 mm); y-Reihe ggf. mit Brett ganz im Bild, zweitem Anschlag gegen Querrutschen und ~10 Bildern je Lage wiederholen.
- Tiefe aus der Mutter-Lage nicht bestimmbar: Brett in allen Lagen 1,8° geneigt und 336,4 mm entfernt (flach auf dem Tisch ~348 mm) — lag nicht auf dem Tisch.
