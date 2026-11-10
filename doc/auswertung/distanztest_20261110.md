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
