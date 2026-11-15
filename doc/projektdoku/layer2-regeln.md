# Layer 2 und Handkamera-Kalibrierung: Regeln

Festgelegt am 15.11.2026 (Branch `ADP_Lokaliserung`).

## Layer 2: Ein Tag pro Modul

Hat ein Modul mehrere Tags, fährt Layer 2 nur **einen** davon an, nicht alle.

- Gewählt wird der Tag mit dem besten Sichtpunkt, in dieser Reihenfolge:
  1. erreichbar vor nicht erreichbar,
  2. echte IK-Lösung vor einer angenäherten Pose,
  3. Abstand am nächsten am Soll (0,35 m),
  4. möglichst frontal (kleinste Kippung),
  5. bei Gleichstand der nächstgelegene Tag.
- Das sind dieselben Kriterien wie bei der Wahl des Welt-Tags zum Ankern.
- Die übrigen Tags des Moduls tauchen im Plan nicht auf.
- Die Modullage kommt aus diesem einen Foto: Lage und Gierwinkel des Tags, über den Katalog auf das Modul umgerechnet.
- Umsetzung: `planApproach(..., { onePerModule: true })` in `frontend/src/features/cell-modules/model/approachPlan.ts`, eingeschaltet nur in `ApproachPlanPanel.tsx` (Layer 2). Die Tag-Referenzmessung (`TagReferencePanel.tsx`) plant weiter alle Tags.

## Layer 2: Nicht anfahrbarer Tag wird übersprungen

Ein Tag, der sich nicht anfahren oder messen lässt, wird **übersprungen**. Layer 2 macht mit dem nächsten Tag weiter.

Übersprungen wird, wenn:

- die Planung keine erreichbare Pose findet (der Tag kommt gar nicht erst in die Route),
- der Sichtpunkt oder der Weg dorthin den Wagen oder den Boden berühren würde,
- die Fahrt zum Sichtpunkt scheitert (`go_to` meldet einen Fehler). Neu seit 15.11.2026: Vorher endete hier der ganze Lauf. Danach liest der Lauf die Gelenkwinkel neu und fährt den nächsten Tag wie üblich aus der Ausgangsstellung an.
- der Tag beim Zentrieren nicht gefunden oder nicht gemessen wird,
- ein Welt-Tag gemessen wird, der nicht im Katalog steht.

Der übersprungene Tag steht in der Liste rot mit Grund. Die Schlussmeldung nennt alle übersprungenen Tags, z. B.
„Fertig: 4 von 5 Sichtpunkten angefahren, zurück in der Ausgangsstellung. Übersprungen: Tag 7 (Förderband).“

Der Lauf endet weiterhin ganz bei:

- **Angehalten** (Knopf „Anhalten“),
- **Ausgangsstellung nicht erreichbar:** Die Fahrt dorthin scheitert, oder der Weg dorthin würde Wagen oder Boden berühren. Dann ist unklar, wo der Arm steht, und weitere Fahrten wären nicht sicher.
- Gelenkwinkel nicht lesbar.

## Handkamera: Eine Kalibrierung für Intrinsik und Hand-Auge

- Im Einstellungsmenü hat die Handkamera nur noch **einen** Knopf „Kalibrierung“. Er startet die Gesamtkalibrierung: Der Roboter fährt die Posen selbst ab, der Pi rechnet aus denselben Rohbildern Intrinsik und Hand-Auge (`fullCalibration: true`). Der eigene Knopf „Kalibrierung Hand-Eye“ ist entfallen.
- Grund: Intrinsik und Hand-Auge gehören zusammen. Eine Hand-Auge-Lösung mit einer anderen Intrinsik passt nicht: Im Drehtest vom 13.11.2026 ergab die alte Hand-Auge-Lösung mit der neuen Intrinsik rund 2° Achsfehler, mit der eigenen nur 0,25°.
- Die Deckenkamera behält ihre reine Intrinsik-Kalibrierung (`CameraCalibrationModal`), weil sie kein Hand-Auge hat.
- Gestartet wird nur per Knopf, nicht automatisch. Beim Abschluss rechnet der Pi und übernimmt Intrinsik und Hand-Auge sofort live.

## Alte Kalibrierungen bleiben erhalten

Neue Kalibrierdaten ersetzen die aktiven Dateien, die alten werden aber nicht verworfen.

- Gesamtkalibrierung der Handkamera: Beide alten Dateien (`data/calibration/<frame>.json` und `data/handeye/<frame>.json`) werden vorher als `<name>.<UTC>.bak` daneben kopiert. Scheitert das Schreiben, stellt der Pi beide aus der Sicherung wieder her.
- Reine Intrinsik-Kalibrierung (Deckenkamera): seit 15.11.2026 ebenso. Vorher wurde die Datei ohne Sicherung überschrieben. `result.backup` im Status nennt die Sicherung. Umsetzung im Pi-Repo `ADP_flexible_Roboterzelle`, Branch `apriltag-aufraeumen`, `src/vision_server/calibration_session.py`.
- Jede Sicherung bekommt einen eigenen Namen, keine wird überschrieben. Die Rohbilder jeder Kalibrierung bleiben unter `data/rohbilder/calibration/<UTC>_<id>/` auf dem Pi.
