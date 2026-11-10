# Modellfehler der Modul-Lage prüfen und korrigierbar machen

**Status:** geplant — 10.11.2026
**Verantwortlich:** Meric
**Thema:** apriltag
**Branch:** apriltag-aufraeumen (Umsetzung offen)

## Ziel

Die systematischen Modellfehler der Modul-Lage werden geprüft und korrigierbar.
Der Roboter greift an einem Punkt des Moduls, gemessen wird aber nur der Tag.
Der Versatz Tag→Modul steckt im Katalog. Ist er falsch, wirkt der Fehler bei
jeder Messung gleich. Ziel ist ein Abnahmetest je Modul, der diesen Anteil
sichtbar macht, und ein Korrekturvorschlag für den Katalog, der erst nach
Bestätigung übernommen wird.

Ein Klebe- oder CAD-Fehler wirkt bei jeder Messung gleich. Er wird durch
Mittelung oder Kalibrierläufe nicht sichtbar und nicht kleiner. Beispiel: 1°
Verdrehung bei 200 mm Hebel ergibt rund 3,5 mm Versatz am Greifpunkt.

## Bereits gelesen

- `CLAUDE.md`, Abschnitt „Bevor du anfängst“
- `doc/projektdoku/arbeitsplaene/README.md` (kein offener Plan zu diesem Thema außer den bereits fertigen Hand-Auge-Plänen)
- `doc/projektdoku/arbeitsplaene/apriltag-hand-auge-ankern.md`, inkl. Nachtrag 10.11.2026
- `doc/projektdoku/arbeitsplaene/layer2-measuretags.md`
- `doc/projektdoku/apriltag-lokalisierung.md`, Abschnitte 1.1–1.5

## Betroffene Dateien

Noch nicht festgelegt. Die Umsetzung findet im Frontend (webskillcomposition)
statt. Bekannte Anknüpfungspunkte:

- `ApproachPlanPanel.tsx` (Layer-2-Frontend, Hand-Auge-Panel mit „Versatz-Prüfung“)
- `layer2.ts` (Anker- und Modulberechnung)
- `usePickPlaceRun` (Rückrechnung über die Basis des Zwillings)
- Modulkatalog in webskillcomposition (Ort noch zu klären)

Im Repo `apriltag` ist voraussichtlich nichts anzufassen. Eine Datei, die
parallel angefasst wird, wird vorher abgestimmt.

## Schnittstellen

### OPC UA

Keine neuen Knoten, falls die Prüfung im Frontend läuft (Roboterfahrt und
Eingabe durch den Bediener). Läuft sie stattdessen über den Hand-Pi oder den
Roboter-Server, ist das offen (siehe Offene Fragen).

### Python

Nicht vorgesehen. Die Prüfung ist Frontend-Logik; die reine Mathematik
(Korrekturberechnung aus den Abweichungen) wird bei Umsetzung entschieden.

### Daten / Payload

**Modulkatalog (Erweiterung je Modul):**

```json
{
  "checkPoints": [
    {
      "id": "ecke-oben-links",
      "label": "Brettecke oben links",
      "positionM": [0.0, 0.0, 0.0],
      "approachDirection": [0.0, 0.0, 1.0]
    }
  ]
}
```

- `positionM`: Prüfpunkt im Modul-KS in Metern.
- `label`: Bezeichnung für den Bediener.
- `approachDirection`: Anfahrrichtung, Einheitsvektor im Modul-KS. Die
  Werkzeugspitze steht vor dem Punkt, mit Sicherheitsabstand (Vorbild: 10 mm
  vor der ersten Brettecke in der Versatz-Prüfung).

**Ergebnisdatensatz `wsc.modelcheck.result/1`:**

```json
{
  "schema": "wsc.modelcheck.result/1",
  "module": "MOD-A",
  "tagId": 7,
  "checkPointId": "ecke-oben-links",
  "targetInBase": { "position": [0.0, 0.0, 0.0] },
  "measuredDeviationMm": { "x": 0.0, "y": 0.0, "z": 0.0 },
  "confirmedBy": "bediener",
  "measuredAt": "2026-11-10T12:00:00+01:00",
  "calibrationId": "cam_flange@…"
}
```

- `targetInBase`: Soll-Position des Prüfpunkts im Roboterbasis-KS.
- `measuredDeviationMm`: gemessene Abweichung (Ist minus Soll) in Millimetern.
- `calibrationId`: Hand-Auge- bzw. Intrinsik-Kalibrierung, mit der gemessen wurde.

**Korrekturvorschlag `wsc.modelcheck.correction/1`:**

```json
{
  "schema": "wsc.modelcheck.correction/1",
  "tagId": 7,
  "deltaPositionMm": [0.0, 0.0, 0.0],
  "deltaRotationDeg": 0.0,
  "basedOn": ["ecke-oben-links", "bohrung-1", "anschlag-2"],
  "status": "vorschlag"
}
```

- Der Vorschlag wird nur mit `status: "vorschlag"` erzeugt. Die Übernahme
  erfolgt erst nach Bestätigung.

### Netz / Betrieb

Keine neuen Dienste, Ports oder mDNS-Namen, solange die Prüfung im Frontend
läuft.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Prüfpunkt nicht erreichbar oder Kollision | Anfahrt abbrechen, Punkt als „nicht geprüft“ markieren, kein Ersatzpunkt ohne Bestätigung |
| Abweichung größer als Schwelle | Abbruch der Prüfung, kein Auto-Korrigieren |
| Kalibrierung (Intrinsik oder Hand-Auge) seit der Messung geändert | Ergebnis verwerfen oder als veraltet kennzeichnen, neu messen |
| Modul seit Layer 2 bewegt | Prüfung ungültig, Layer 2 neu fahren |

## Vorgehen

1. Abnahmetest je Modul, analog zur „Versatz-Prüfung“ im Hand-Auge-Panel:
   Nach Layer 2 fährt der Roboter mit der Werkzeugspitze definierte Prüfpunkte
   am Modul an, mit Sicherheitsabstand.
2. Prüfpunkte sind im Modulkatalog hinterlegt (Ecken, Bohrungen,
   Anschlagkanten). Mindestens zwei, besser drei Punkte je Modul, damit
   Verschiebung und Verdrehung getrennt werden.
3. Der Bediener misst die Abweichung (Fühlerlehre oder Messschieber) oder
   bestätigt einen Treffer.
4. Aus den Abweichungen wird der Korrekturvorschlag für Tag-Position und
   Tag-Drehung im Katalog berechnet.
5. Übernahme erst nach Bestätigung. Keine automatische Korrektur.

## Offene Fragen

- Wer misst die Abweichung: Bediener, Taster oder eine zweite Kamera?
- Toleranz je Modul, bzw. Schwelle für den Abbruch?
- Welche Module werden zuerst geprüft (Tische für Pick & Place)?
- Wird die Höhe (bisher aus Katalog und Boden) ebenfalls geprüft und korrigiert?
- Liegt die Prüfung im Frontend, oder braucht sie eine OPC-UA-Methode auf dem
  Hand-Pi bzw. Roboter-Server?

## Abweichungen vom Plan

Noch nicht umgesetzt.

## Nach Abschluss

- [ ] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [ ] Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
- [ ] Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/` übernommen.
