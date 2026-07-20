# Offene Punkte – Konzept Lokalisierung

## Konzeptuelle Lücken

### 1. Koordinatensystem-Kette
Die Welt-Tags (ArUco-Board an Boden/Wand) sind als Hardware-Komponente vorhanden, tauchen aber im Ablaufdiagramm nicht auf.
- **Offen:** Wann und wie wird die Transformation Kamera-KS → Welt-KS aufgebaut?
- **Offen:** Wird der Welt-Tag in der Kalibrierungsphase referenziert oder bei jedem Layer-1-Scan?

### 2. Übergabe Layer 1 → Layer 2
Layer 1 liefert eine grobe Modulposition — aber der Roboter braucht eine konkrete TCP-Zielpose.
- **Offen:** In welchem Format wird die grobe Position übergeben (Pixel-Koordinate, 3D-Pose im Weltkoordinatensystem, direkte Roboter-Zielpose)?
- **Offen:** Wer führt die Koordinatentransformation Welt-KS → Roboter-Basis-KS durch?

### 3. Repositionierungsstrategie Layer 2
Layer 2 hat einen Repositionierungspfad, falls kein AprilTag gefunden wird — die Strategie selbst ist undefiniert.
- **Offen:** Maximale Anzahl Versuche vor Abbruch?
- **Offen:** Suchmuster (Spiralsuche, fixe Offsets, zufällig)?

### 4. Rolle des Robotertisch-Tags
Der Robotertisch hat einen eigenen AprilTag, der im Ablaufdiagramm nicht explizit auftaucht.
- **Offen:** Wann wird dieser Tag detektiert?
- **Offen:** Dient er zur Kalibrierung, als Layer-2-Referenzpunkt oder zur Validierung?

### 5. Fehlerbehandlung bei Layer-1-Ausfall
Wenn die Deckenkamera kein Modul erkennt, gibt es einen `To Fix`-Pfad — das weitere Verhalten ist unklar.
- **Offen:** Kann Layer 2 ohne Layer-1-Ergebnis gestartet werden (Blindsuche)?
- **Offen:** Wird das Modul übersprungen oder blockiert der Fehler den gesamten Scan?

### 6. Abbruchkriterium / Vollständigkeit
Der Prozess endet mit "Map complete, all positions detected".
- **Offen:** Wer/was definiert die erwartete Soll-Anzahl der Module?
- **Offen:** Wird die Modulanzahl statisch konfiguriert oder dynamisch aus Layer 1 ermittelt?

---

## Anforderung: Update-Loop

Die gesamte Kette (Calibration → Layer 1 → Layer 2) soll wiederholt ausgeführt werden können.

**Trigger:**
- Zeitgesteuert: automatisch alle 60 Minuten
- Manuell: auf expliziten User Input (z.B. Konsolenbefehl oder GUI-Button)

**Noch zu klären:**
- Verhalten bei laufendem Scan, wenn ein neuer Trigger ausgelöst wird:
  - [ ] Neuen Trigger in Queue einreihen
  - [ ] Laufenden Scan abbrechen und neu starten
  - [ ] Trigger ignorieren bis Scan abgeschlossen
- Wird die Kalibrierungsphase bei jedem Loop-Durchlauf wiederholt oder nur beim ersten Start?
- Sollen Positions-Deltas zwischen zwei Durchläufen geloggt werden (Veränderungserkennung)?
