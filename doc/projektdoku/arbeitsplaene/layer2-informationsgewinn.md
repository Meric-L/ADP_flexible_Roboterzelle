# Layer 2: Informationsgewinn durch Frames und Ansichten messen

**Status:** fertig — 30.10.2026 (Hardwareaufnahme steht noch aus)
**Verantwortlich:** `Agent: Layer-2-Vergleichsversuch`
**Thema:** apriltag
**Branch:** `experiment/layer2-information-gain`

## Ziel

Ein reproduzierbarer Versuch speichert die tatsächlich ausgewerteten Kamerabilder und vergleicht gepaart den Positions- und Orientierungsfehler von einer Aufnahme, drei Aufnahmen am selben Ort und drei verschiedenen Roboterposen. Der Bericht trennt Genauigkeit gegen eine unabhängige Referenz von Wiederholbarkeit.

## Bereits gelesen

- `doc/projektdoku/arbeitsplaene/README.md`, `_vorlage.md`, `layer2-measuretags.md`, `rohbild-speichern.md`, `layer2-lauf-hand-pi.md`
- `doc/projektdoku/apriltag-referenz.md`, `vision-server-interface.md`, `apriltag-e2e-test.md`

## Betroffene Dateien

- `tools/layer2_capture_view.py`: Aufnahme einer Ansicht über SaveRawImage
- `tools/layer2_information_gain.py`: Offline-Auswertung der gespeicherten Rohbilder
- `tests/test_layer2_information_gain.py`: Metriken und Paarung
- `doc/projektdoku/layer2-informationsgewinn.md`: Messprotokoll und Datenformat
- `doc/projektdoku/arbeitsplaene/README.md`: Index

## Schnittstellen

### Python / CLI

`python3 tools/layer2_capture_view.py <session.json> --trial N --view N --pose-file <pose.json> --endpoint <opc.tcp://...> --machine-node <NodeId> --method-node <NodeId> --repo-root <Pfad>` speichert drei PNGs und ergänzt die Session. Das Programm läuft auf dem Hand-Pi nach Roboterstillstand.

`PYTHONPATH=src python3 tools/layer2_information_gain.py <session.json> --out <bericht.json>` liest Bilder mit OpenCV und rechnet die vorhandene AprilTag-Detektion und Posenschätzung erneut. Der Prozess fährt keinen Roboter und verändert den Vision-Server nicht.

### Daten

Session-JSON `adp.layer2.information-gain/1`: `tagId`, `tagSizeM`, `calibration`, `handEye`, Detektoreinstellungen, optional `referenceTagInBase`, und `trials[]` mit jeweils `views[]` (`flangeInBase`, `images[]`). Alle Pfade liegen relativ zur Session-Datei. Posen sind `position` in Metern und `orientation` als Quaternion xyzw. Ein Trial hat drei Ansichten mit je drei PNGs. Referenz ist unabhängig vermessen.

Bericht `adp.layer2.information-gain.result/1`: Erkennungsquoten und je Strategie gepaarte Fehler in mm/Grad samt Bootstrap-Intervall der mittleren Differenz. Ohne Referenz: nur Wiederholbarkeit, keine Genauigkeitsaussage. Die Strategien vergleichen ein Bild, drei Bilder einer Ansicht, je ein Bild aus drei Ansichten und drei Bilder aus drei Ansichten.

### Betrieb und Fehlerfälle

Aufnahmen entstehen über die bestehende OPC-UA-Methode `SaveRawImage` nach Roboterstillstand. Die Ist-Flanschpose muss aus den Ist-Gelenken per FK stammen. Fehlende Bilder, unpassende Bildgröße oder fehlende Kalibrierdateien brechen mit Klartext ab; nicht erkannte Tags zählen als Fehlschlag und werden nicht stillschweigend ersetzt. Keine neuen Ports oder OPC-UA-Knoten.

## Vorgehen

1. Session-Format und kontrolliertes Aufnahmeprotokoll festlegen.
2. Offline-Auswertung mit den bestehenden Detektor- und Geometrie-Funktionen bauen.
3. Gepaarte Metriken mit synthetischen Daten prüfen.

## Offene Fragen

- Art und erreichbare Genauigkeit der unabhängigen Referenz an der realen Zelle.
- Ob für alle drei Ansichten in jedem Trial eine kollisionsfreie Roboterfahrt möglich ist.

## Abweichungen vom Plan

Der Aufnahmehelfer kam als zweite Datei hinzu. Für den Vergleich bei gleichem Bildbudget kam die Strategie mit je einem Bild aus drei Ansichten hinzu. Beide sind in der Fachdokumentation beschrieben.

## Nach Abschluss

- [x] Schnittstellen und Aufnahmeprotokoll in `doc/projektdoku/layer2-informationsgewinn.md` dokumentiert.
- [x] Fünf gezielte Tests, darunter ein Replay mit synthetischen PNGs und eine simulierte OPC-UA-Aufnahme, erfolgreich.
- [ ] Messung an der echten Hardware mit unabhängiger Referenz; dafür ist eine Roboterfahrt und Vermessung vor Ort erforderlich.
