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
- Tiefe aus der Mutter-Lage nicht bestimmbar (Brett scheinbar 1,8° geneigt und 336,4 mm entfernt statt ~348 mm) — Ursache siehe unten (Roboter in Ruhestellung abgesackt).

## Nachtrag: Brettmaß y und Absacken in Ruhestellung

- Brett in y nachgemessen (Nutzer, aus einem Bild): 174,7 mm über 7 Felder (Feld 24,957 mm, −0,17 %; in x −0,07 %). Mit getrennten Maßstäben für x und y ändert sich der Seitentest kaum: x +0,05 %, Schritte −0,001 / −0,065 / +0,125 mm; y +0,65 %, Schritte +0,053 / +0,086 / +0,310 mm. Der etwas zu große y-Maßstab kommt also nicht vom Druck, sondern im Wesentlichen vom Einzelbild am Bildrand.
- Während des Seitentests stand der Roboter in Ruhestellung (Motoren aus, nur Bremsen). Er meldete dieselben Gelenkwinkel wie eingeschaltet (Kamera rechnerisch auf z 347,72 mm), das Brett erschien an derselben Stelle (Basis x ~408, y ~9) aber 10 mm höher (z 11,2 statt 1,0 mm) und 1,2° stärker geneigt (1,5° statt 0,35°): Die Kamera ist um diesen Betrag abgesackt und gekippt, ohne dass die gemeldeten Gelenkwinkel es zeigen.
- Für den Seitentest unschädlich (Kamera stand während der Reihe still, gezählt wird die Verschiebung in der Brettebene). Für Hand-Auge-Aufnahmen, Layer 2 und jede Rückrechnung in Roboterkoordinaten darf der Roboter nicht in Ruhestellung stehen (~10 mm unsichtbarer Fehler); Langzeitmessungen durchgehend im selben Zustand.
- In Roboterkoordinaten treffen die Distanzstück-Höhen absolut 19,35 / 39,43 / 59,10 mm (soll 19,9 / 39,8 / 59,7), einschließlich Hand-Auge und Roboterpose. Lauf 0 (z 1,0 mm) stand mit 1,6 mm anderer Kamerahöhe (Gelenke bis 0,004 rad anders).

## AprilTag in der Ausgangsstellung (Session `2026-11-10_153201`)

10 Rohbilder (1920×1080), Tag ID 0 (tag36h11, 80 mm), offline erkannt (Subpixel-Ecken, IPPE_SQUARE, aktive Kalibrierung); die Live-Messung des Pi war leer (Panel stand auf ChArUco). Roboter eingeschaltet in der Ausgangsstellung (Gelenke ~ −0,3 / −14,0 / 0,1 / −137,7 / 0,0 / 123,0 / 44,4°).

| | Wert | σ |
|---|---|---|
| Tag-Mitte in Kamera | 17,6 / −16,6 / 349,9 mm | 10 / 23 / 77 µm |
| Tag-Mitte in Roboterbasis | x 421,9 / y 16,3 / z −2,2 mm | 23 / 10 / 77 µm |
| Gierwinkel der Tag-x-Achse in Basis | −90,12° | 0,013° |

- Präzision mit 4 Ecken: seitlich 10–23 µm, Tiefe 77 µm (Brett: ~6 µm).
- z −2,2 mm passt zur Tischebene (Brett 0–1 mm): Roboter nicht abgesackt.
- Tag auf 0,12° parallel zu den Roboterachsen; Neigung des Einzeltags 2,5° (schwach bestimmt).
- Handmessung (Nutzer): Tag 310,8 mm von der dem Roboter zugewandten Tischkante (Richtung vom Roboter weg), 312,2 mm von der linken Kante. Daraus folgen die Kanten in Roboterbasis: zugewandte Kante bei x = 111,1 mm, linke Kante bei y = 328,5 mm. Prüfung offen: Abstand Roboterachse (Gelenk 1) zu diesen Kanten messen.
- Abgleich (Nutzer): CAD Roboterbasis → linke Kante 331,7 mm (Vorhersage 328,5, Δ 3,2 mm), → zugewandte Kante 127,3 mm (Vorhersage 111,1, Δ 16,2 mm; CAD-Bezugspunkt unsicher). Grob von Auge zur Gelenk-1-Achse ~108 mm (Δ ~3 mm). Stimmigkeit also grob ~3 mm je Achse, begrenzt durch die Handmaße; der Franka-Basisursprung liegt auf der Gelenk-1-Achse, nicht in der Fußmitte. Belastbarer Abschluss: Werkzeugspitze über die gemessene Tag-Mitte fahren und den Versatz messen.
