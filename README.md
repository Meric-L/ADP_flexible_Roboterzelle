# ADP_flexible_Roboterzelle
Theorie &amp; Praxis: Untersuchung und Implementierung von Lokalisierungsmethoden für rekonfigurierbare Roboterzellen.
# Lokalisierung in modularen Roboterzellen

Dieses Repository enthält sowohl die praktische Software-Implementierung als auch die LaTeX-Quelldateien unserer Gruppenarbeit zur Positions- und Topologieerfassung von mechatronischen Modulen. 

## Projektfokus
Ziel der Arbeit ist die Untersuchung, Evaluation und **praktische Umsetzung** verschiedener Lokalisierungstechnologien für den Einsatz in einer flexiblen, modularen Labor-Roboterzelle (ca. 5x5 Meter). Betrachtet werden unter anderem:
* **optische Marker Erkennung:** 
* **Topologische Erkennung:** Nahbereichslokalisierung an mechanischen Schnittstellen (z.B. Near Field Magnetic Positioning).
* **UBW Erkennung:**

## Repository-Struktur
Das Projekt ist strikt in Dokumentations- und Code-Bestandteile getrennt:

### Dokumentation
* `/doc` - Die LaTeX-Quelldateien der schriftlichen Ausarbeitung **und** die
  technische Dokumentation als Markdown (siehe unten).
* `/concept` - Ablaufdiagramm und offene Konzeptfragen.

### Implementierung (Source Code)
* `/src/tagloc` - AprilTag-Lokalisierung: Kalibrierung, Erkennung, Koordinatensysteme.
* `/src/vision_server` - OPC-UA-Vision-Server (OPC 40100) mit der Erkennungsquelle.
* `/src/vision_server` - Das Vision-System (OPC 40100 + Teil 10) samt dem Server
  der Zelle (`cell_server.py`), der Auffindbarkeit (`discovery/`) und den
  vendorierten Nodesets (`nodesets/`).
* `/tools` - Druckbogen für Tags und Boards, synthetische Testszenen.
* `/config` - Tag-Map (Zellenlayout), versioniert.
* `/hardware` - CAD-Dateien, Schaltpläne oder 3D-Druck-Modelle für die Sensorhalterungen.
* `/data` - Kalibrierdateien und Aufnahmen. Maschinenspezifisch, **nicht** versioniert.
* `/tests` - `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

## Technische Dokumentation

| Dokument | Wofür |
| --- | --- |
| [`doc/apriltag-lokalisierung.md`](doc/apriltag-lokalisierung.md) | Konzept: was AprilTags hier bedeuten, wie Layer 1 und Layer 2 dieselbe Funktionalität benutzen |
| [`doc/apriltag-referenz.md`](doc/apriltag-referenz.md) | Funktions- und CLI-Referenz, Dateiformate, Payload |
| [`doc/apriltag-e2e-test.md`](doc/apriltag-e2e-test.md) | **Wie man alles durchtestet** — vom PC bis zur Raspberry-Pi-Kamera |
| [`doc/vision-server-interface.md`](doc/vision-server-interface.md) | OPC-UA-Schnittstelle für das Backend (OPC 40100) |
| [`doc/part10-programm-schnittstelle.md`](doc/part10-programm-schnittstelle.md) | **Part-10-Programm und mDNS** — die generische Bedienoberfläche der Zelle und die Auffindbarkeit im Netz |
| [`doc/vision-system.md`](doc/vision-system.md) | Ist-Stand des Vision-Systems |
| [`doc/altlasten.md`](doc/altlasten.md) | Was warum noch drin ist und wann es rausfliegt |
| [`concept/offene_punkte.md`](concept/offene_punkte.md) | Offene Konzeptfragen der Lokalisierung |
