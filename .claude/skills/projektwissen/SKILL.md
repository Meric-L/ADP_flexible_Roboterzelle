---
name: projektwissen
description: Gemeinsames Projektwissen der ADP-Roboterzelle lesen und fortschreiben. Benutzen beim Einstieg in eine Aufgabe zu physischem Aufbau, Vision-System oder OPC UA, beim Festhalten einer Erkenntnis, Entscheidung oder widerlegten Annahme, und bei Fragen wie "was wissen wir schon über X" oder "was hat das Team dazu rausgefunden".
---

# Projektwissen lesen und fortschreiben

Drei Leute arbeiten in getrennten Chats am selben Projekt. Ohne gemeinsame
Ablage findet jeder dasselbe zweimal heraus und die Arbeitsstände laufen
auseinander. Diese Skill hält die Ablage unter `wissen/` konsistent.

Alles auf **Deutsch** — Dateiinhalte, Log-Einträge, Commit-Messages.

## Die drei Bereiche

| Datei | Gehört hierher |
| --- | --- |
| `wissen/aufbau.md` | Zellengeometrie, Kamerapositionen, Halterungen, Welt- und Tischtags, Hardware-Architektur, Ablauf der Scan-Kette, Konzeptentscheidungen |
| `wissen/vision.md` | Kamerakalibrierung, Marker-Erkennung, Pose-Schätzung, Bildpipeline, Tag-Familien und -Größen — bis zur fertig berechneten Pose |
| `wissen/opcua.md` | OPC-UA-Server, OPC 40100, Adressraum, Nodeset, `asyncua`, Ergebnis-Payload, Integration in WebSkillComposition — ab der fertigen Pose |

Die Grenze zwischen `vision.md` und `opcua.md` ist die fertige Pose: *wie sie
berechnet wird* ist Vision, *wie sie ausgeliefert wird* ist OPC UA.

## Lesen

1. `git pull` — die anderen schreiben in dieselben Dateien.
2. **Nur den Bereich lesen, den die Aufgabe betrifft.** Nicht alle drei.
   Unklar? Nach dem Ziel der Aufgabe gehen, nicht nach erwähnten Stichwörtern:
   "AprilTag über OPC UA melden" ist eine OPC-UA-Aufgabe.
3. Die Bereichsdatei ist ein Index. Erst wenn sie die Frage nicht beantwortet,
   die dort verlinkten Tiefendokumente lesen — und dann gezielt den relevanten
   Teil, nicht das ganze Dokument.
4. Bei bereichsübergreifenden Aufgaben den besitzenden Bereich lesen und aus
   dem anderen nur den verlinkten Abschnitt.

## Eintrag anhängen

Festgehalten wird, was die anderen betrifft: ein Messergebnis, eine getroffene
Entscheidung, ein Irrweg, eine widerlegte Annahme, eine geklärte offene Frage,
eine Stolperfalle. **Nicht** festgehalten: Zwischenschritte, Vermutungen ohne
Ergebnis, Dinge die nur im aktuellen Chat gebraucht werden.

Eintrag **unten** an den Abschnitt `## Log` der passenden Bereichsdatei
anhängen, in diesem Format:

```markdown
### JJJJ-MM-TT — Name — Kurztitel in einer Zeile
**Status:** offen | erledigt | verworfen
**Betrifft:** aufbau | vision | opcua (mehrere mit Komma)

Was herausgefunden wurde, in zwei bis fünf Sätzen. Konkret: Zahlen, Dateinamen,
Fehlermeldungen. Keine Zusammenfassung des Chats.

**Konsequenz:** Was die anderen deswegen anders machen müssen. Entfällt, wenn
es keine gibt.
```

Regeln dazu:

- **Anhängen, nicht umschreiben.** Bestehende Einträge bleiben stehen, auch
  wenn sie sich als falsch erwiesen haben — dann kommt ein neuer Eintrag mit
  `**Status:** verworfen` und einem Verweis darauf. Ein Irrweg, der als Eintrag
  sichtbar bleibt, verhindert, dass ihn der nächste wiederholt.
- **Widerlegt der Eintrag einen Kernfakt, denselben Kernfakt oben in der Datei
  mitkorrigieren.** Sonst veralten die Kernfakten und niemand vertraut ihnen
  mehr. Der Log-Eintrag begründet die Änderung, der Kernfakt trägt den neuen
  Stand.
- Betrifft der Eintrag zwei Bereiche, kommt er vollständig in den besitzenden
  Bereich und in den anderen nur als eine Zeile mit Link.
- Wird eine Frage aus `concept/offene_punkte.md` geklärt, dort als geklärt
  markieren und die Entscheidung hier loggen.
- Bestehende Dokumente in `doc/` und `concept/` nicht umschreiben, um etwas
  Neues unterzubringen.

## Commit vorschlagen

Nach dem Anhängen:

1. `git diff` zeigen.
2. Commit-Message vorschlagen, Format: `wissen(<bereich>): <kurztitel>` —
   z. B. `wissen(opcua): HasNotifier reicht für Event-Subscribe nicht`.
3. **Warten.** Nicht committen und nicht pushen ohne ausdrückliche Zustimmung.
   Das ist ein geteiltes Repo.

Bei Merge-Konflikt in einem `## Log`: beide Einträge behalten, chronologisch
sortieren. Ein Konflikt in einem Log ist nie eine Entweder-oder-Entscheidung.

## Wonach nicht gefragt werden muss

Steht die Antwort in der Bereichsdatei oder einem verlinkten Dokument, wird sie
gelesen statt erfragt. Fehlt sie dort, ist das selbst eine Erkenntnis — nach
der Klärung kommt sie als Eintrag rein.
