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

### Dokumentation (LaTeX)
* `/doc` - Die LaTeX-Quelldateien der schriftlichen Ausarbeitung.

### Schnittstellen-Dokumentation

| Dokument | Wofür |
| --- | --- |
| [`doc/vision-server-interface.md`](doc/vision-server-interface.md) | OPC-UA-Schnittstelle des Vision-Servers nach OPC 40100 (Machine Vision) |
| [`doc/part10-programm-schnittstelle.md`](doc/part10-programm-schnittstelle.md) | **Part-10-Programm und mDNS** — die generische Bedienoberfläche, mit der die Zelle ihre Module steuert, und die Auffindbarkeit im Netz |

### Implementierung (Source Code)
* `/src` - Quellcode für die Sensorik, Datenverarbeitung und Sensorfusion.
* `/hardware` - CAD-Dateien, Schaltpläne oder 3D-Druck-Modelle für die Sensorhalterungen.
* `/data` - Aufgezeichnete Sensordaten oder Test-Datensätze zur Evaluierung der Algorithmen.
