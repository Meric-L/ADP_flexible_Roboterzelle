# ADP flexible Roboterzelle — Arbeitsanweisung

Gruppenarbeit zur Positions- und Topologieerfassung mechatronischer Module in
einer rekonfigurierbaren Labor-Roboterzelle (ca. 5x5 m). Das Repo enthält die
Implementierung (`src/`, `tests/`, `hardware/`, `data/`), die Projektdokumentation
(`doc/projektdoku/`) und die LaTeX-Ausarbeitung der Thesis (`doc/`).

Diese Datei ist **verbindlich für alle**: für jedes Teammitglied und für jeden
Agenten (Claude-Chat, Subagent, Hintergrund-Job). Sie liegt im Repository — wer
klont, hat sie. Claude Code lädt sie automatisch, Menschen lesen sie hier.

**Sprache: Deutsch.** Alle Dateien in diesem Repo — Code-Kommentare,
Dokumentation, Commit-Messages — werden auf Deutsch geschrieben.

## Bevor du anfängst

Vor der **ersten** inhaltlichen Aktion — Code schreiben, Datei ändern, Konzept
entwerfen — gilt ausnahmslos:

1. **Schauen, was schon da ist.** `ls doc/projektdoku/*.md doc/projektdoku/arbeitsplaene/*.md`.
   Die für das Thema relevanten MDs lesen, **bevor** etwas entsteht.
   `doc/projektdoku/` ist der gemeinsame Wissensstand des Projekts, nicht bloß
   Beiwerk.
2. **Doppelarbeits-Check.** [`doc/projektdoku/arbeitsplaene/README.md`](doc/projektdoku/arbeitsplaene/README.md)
   listet, wer gerade woran arbeitet. Steht dort zum Thema schon ein Plan auf
   `in Arbeit`, der jemand anderem gehört: **nicht** parallel implementieren,
   sondern dort andocken oder die Person ansprechen.
3. **Plan schreiben, dann bauen** — siehe nächster Abschnitt.

Wir arbeiten parallel in getrennten Chats und auf getrennten Rechnern. Ohne
diese zwei Blicke findet jeder dasselbe zweimal heraus oder baut dasselbe
zweimal.

## Arbeitsplan mit Schnittstellen

Jede Aufgabe, die mehr ist als ein Einzeiler-Fix, bekommt **vor** der Umsetzung
eine eigene Datei `doc/projektdoku/arbeitsplaene/<thema>.md`
(Vorlage: [`doc/projektdoku/arbeitsplaene/_vorlage.md`](doc/projektdoku/arbeitsplaene/_vorlage.md)).

Pflichtangaben im Kopf: Status (`geplant` / `in Arbeit` / `fertig` /
`verworfen`), Verantwortlich, Thema, Branch. Dazu Ziel, betroffene Dateien,
gelesene Dokumente, offene Fragen — und vor allem:

**Schnittstellen.** Der wichtigste Abschnitt, so konkret, dass jemand anderes
**ohne diesen Code** dagegen entwickeln kann:

- OPC UA: NodeIds und BrowsePaths samt Namespace, Methodensignaturen mit
  Argumenten, Datentypen, Rückgaben und Fehler-StatusCodes
- Python: Modul, Funktion, Parameter, Rückgabetyp, geworfene Ausnahmen
- Daten: JSON-/Payload-Format mit Feldbedeutung
- Betrieb: Ports, Services, mDNS-Namen, systemd-Units
- Fehlerfälle: Timeout, fehlende Kamera, ungültiger Job, Abbruch

Danach eine Zeile in die Tabelle in `doc/projektdoku/arbeitsplaene/README.md`
eintragen. Nach Abschluss Status auf `fertig` setzen und die **tatsächlichen**
Schnittstellen nachtragen; weicht die Umsetzung vom Plan ab, wird der Plan
korrigiert. Eine veraltete Schnittstellenbeschreibung ist schlimmer als keine.

Dauerhaftes Fachwissen gehört anschließend in die passende Fach-MD unter
`doc/projektdoku/` — der Arbeitsplan ist die Planung, die Fach-MD der Stand.

Für den ganzen Ablauf gibt es die Skill `projektdoku`
(`.claude/skills/projektdoku/`). Sie liegt im Repo, hat also jeder nach dem
Clonen: sie findet die relevanten MDs, legt den Plan im festen Format an und
committet selbstständig, ohne zu pushen.

## Regeln für Änderungen

- Vor dem Lesen von `doc/projektdoku/` ein `git pull` — die anderen schreiben in
  dieselben Dateien.
- Keine parallele Zweitimplementierung eines Themas, das laut
  `doc/projektdoku/arbeitsplaene/README.md` jemand anderem gehört.
- Eine dokumentierte Schnittstelle wird nicht stillschweigend geändert: erst den
  Arbeitsplan bzw. die Fach-MD anpassen, damit es alle sehen, dann den Code.
- Bestehende Dokumente in `doc/projektdoku/` und `concept/` nicht umschreiben,
  um etwas Neues unterzubringen. Neues kommt in einen eigenen Abschnitt oder
  eine eigene Datei.
- Kein Umbenennen oder Löschen von `doc/projektdoku/*.md` ohne Absprache —
  andere MDs verweisen darauf.
- Committen läuft automatisch: Diff zeigen, committen, weiterarbeiten — kein
  Nachfragen nötig. Nur die Dateien der Aufgabe, nie auf `main`.
- Nichts pushen ohne ausdrückliche Anweisung. Am Ende jedes Features ein kurzer
  Hinweis, dass committet, aber nicht gepusht wurde, samt Branch und
  `git push origin <branch>`.
- `main` bekommt nur fertige, funktionierende Features. Gearbeitet wird auf
  Feature-Branches, aktuell `feature/vision-server`.
- `doc/*.tex` ist die schriftliche Ausarbeitung. Nur anfassen, wenn die Aufgabe
  ausdrücklich die Ausarbeitung betrifft.
- `betreuer/` enthält Vorgaben und Beispielcode der Betreuung — lesen, nicht
  überschreiben.

## Orientierung im Repo

| Pfad | Inhalt |
| --- | --- |
| `doc/projektdoku/*.md` | Fachdokumentation: Schnittstellen, Konzepte, Testanleitungen |
| `doc/projektdoku/arbeitsplaene/` | Laufende und abgeschlossene Arbeitspläne (siehe oben) |
| `doc/*.tex` | LaTeX-Ausarbeitung der Thesis |
| `src/` | Implementierung: OPC-UA-Server, Vision, Sensorik |
| `tests/` | Pytest-Tests |
| `tools/` | Hilfsskripte |
| `betreuer/` | Vorgaben der Betreuung |

## Technische Durchsetzung

`.claude/settings.json` ruft bei jedem Sessionstart, bei jedem Subagenten und
vor dem ersten Schreibzugriff einer Session das Skript
`.claude/hooks/doku_regeln.py` auf. Es gibt diese Regeln mit und listet auf,
welche Fach-MDs und welche laufenden Pläne es gerade gibt — kein Agent kann
behaupten, er habe die Doku nicht gekannt.

Der Hook erinnert, er blockiert nicht. Verbindlich sind die Regeln oben, und sie
gelten auch für Arbeit ohne Claude Code.
