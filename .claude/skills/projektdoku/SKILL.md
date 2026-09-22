---
name: projektdoku
description: Projektdokumentation der ADP-Roboterzelle lesen und fortschreiben. Benutzen beim Einstieg in eine Aufgabe zu OPC UA, Vision-System, AprilTag-Lokalisierung, mDNS oder Frontend-Anbindung, beim Anlegen oder Pflegen eines Arbeitsplans mit Schnittstellen, und bei Fragen wie "was ist dazu schon dokumentiert", "arbeitet daran schon jemand" oder "welche Schnittstelle hat X".
---

# Projektdokumentation lesen und fortschreiben

Mehrere Leute arbeiten in getrennten Chats am selben Projekt. Ohne gemeinsame
Ablage findet jeder dasselbe zweimal heraus, und zwei Implementierungen
derselben Schnittstelle laufen auseinander. Diese Skill hält `doc/projektdoku/` konsistent.

Alles auf **Deutsch** — Dateiinhalte, Kommentare, Commit-Messages.

Gearbeitet wird auf Feature-Branches (aktuell `feature/vision-server`).
Nach `main` kommen nur fertige, funktionierende Features.

## 1. Lesen — immer zuerst

1. `git pull` — die anderen schreiben in dieselben Dateien.
2. `ls doc/projektdoku/*.md doc/projektdoku/arbeitsplaene/*.md`
3. `doc/projektdoku/arbeitsplaene/README.md` lesen: **Arbeitet daran schon jemand?**
   Steht zum Thema ein Plan auf `in Arbeit`, der jemand anderem gehört, wird
   nicht parallel implementiert — andocken oder nachfragen.
4. Die Fach-MDs lesen, die das Thema betreffen. Gezielt den relevanten
   Abschnitt, nicht das ganze Dokument.

| Thema | Zuerst lesen |
| --- | --- |
| OPC-UA-Server, OPC 40100, Adressraum, Methoden, Payload | `doc/projektdoku/vision-server-interface.md`, `doc/projektdoku/vision-system.md` |
| Anbindung an WebSkillComposition, Frontend, mehrere Vision-Systeme | `doc/projektdoku/vision-system-integration.md` |
| Offene Punkte am Vision-System | `doc/projektdoku/vision-system-next-steps.md` |
| AprilTag: Konzept, Tag-Familien, Pose | `doc/projektdoku/apriltag-lokalisierung.md`, `doc/projektdoku/apriltag-referenz.md` |
| AprilTag testen | `doc/projektdoku/apriltag-e2e-test.md` |
| Unerwartetes Verhalten, historische Eigenheiten | `doc/projektdoku/altlasten.md` |
| Präsentation / Betreuung | `doc/projektdoku/praesentation.md`, `betreuer/` |

Steht die Antwort in einer dieser Dateien, wird sie gelesen statt erfragt.
Fehlt sie dort, ist das selbst eine Erkenntnis — nach der Klärung kommt sie rein.

## 2. Arbeitsplan anlegen

Für jede Aufgabe, die mehr ist als ein Einzeiler-Fix, **vor** der Umsetzung:

1. `doc/projektdoku/arbeitsplaene/_vorlage.md` nach `doc/projektdoku/arbeitsplaene/<thema>.md` kopieren
   (klein, mit Bindestrichen, z. B. `opcua-part10-wrapper.md`).
2. Kopf ausfüllen — genau dieses Format, der Hook liest es aus:

```markdown
**Status:** geplant | in Arbeit | fertig | verworfen — TT.MM.JJJJ
**Verantwortlich:** <Name> bzw. `Agent: <Aufgabe>`
**Thema:** opcua | vision | apriltag | mdns | frontend | sonstiges
**Branch:** <branch-name>
```

3. Den Abschnitt **Schnittstellen** ausfüllen. Das ist der Zweck des ganzen
   Dokuments — so konkret, dass jemand anderes **ohne diesen Code** dagegen
   entwickeln kann:
   - OPC UA: NodeId/BrowsePath samt Namespace, Methodensignatur mit Argumenten,
     Datentypen, Rückgaben, Fehler-StatusCodes
   - Python: Modul, Funktion, Parameter, Rückgabetyp, geworfene Ausnahmen
   - Daten: JSON-/Payload-Format mit Feldbedeutung
   - Betrieb: Ports, Services, mDNS-Namen, systemd-Units
   - Fehlerfälle: Timeout, fehlende Kamera, ungültiger Job, Abbruch
4. Unter „Bereits gelesen" die ausgewerteten MDs eintragen.
5. Eine Zeile in die Tabelle in `doc/projektdoku/arbeitsplaene/README.md` eintragen.

Erst danach wird Code geschrieben.

## 3. Während der Umsetzung

- Weicht die Umsetzung vom Plan ab: Abschnitt „Abweichungen vom Plan" füllen
  **und** den Schnittstellenteil korrigieren. Eine veraltete
  Schnittstellenbeschreibung ist schlimmer als gar keine.
- Ändert sich eine bereits dokumentierte Schnittstelle: erst die Fach-MD unter
  `doc/projektdoku/` anpassen, damit es alle sehen, dann den Code.
- Bestehende Dokumente nicht umschreiben, um etwas Neues unterzubringen —
  eigener Abschnitt oder eigene Datei.

## 4. Nach Abschluss

1. Status im Plan auf `fertig`, tatsächliche Schnittstellen eingetragen.
2. Zeile im Index `doc/projektdoku/arbeitsplaene/README.md` aktualisiert.
3. Dauerhaftes Fachwissen in die passende Fach-MD unter `doc/projektdoku/` übernommen —
   der Plan ist die Planung, die Fach-MD der Stand. Neue Fach-MD? Dann auch in
   die Tabelle in `doc/projektdoku/arbeitsplaene/README.md` aufnehmen.

## 5. Committen — ja. Pushen — nur auf Ansage.

1. `git diff` zeigen.
2. Committen, ohne vorher zu fragen. Message im Format wie im Repo üblich:
   `docs: <kurztitel>` für Dokumentation, `feat(<bereich>): <kurztitel>` für
   Code — z. B. `docs: Arbeitsplan Part-10-Wrapper mit Schnittstellen`.
   Nur die Dateien der Aufgabe (`git add <pfade>`, kein `git add -A`), immer auf
   dem Feature-Branch, nie auf `main`.
3. **Nicht pushen.** Ein Push passiert ausschließlich auf ausdrückliche
   Anweisung. Das ist ein geteiltes Repo — solange nichts draußen ist, stört ein
   lokaler Commit niemanden.
4. Am Ende jedes Features ein kurzer Hinweis in der Antwort, etwa:

   > Commit `<sha>` auf `<branch>` — **nicht gepusht**.
   > Zum Teilen: `git push origin <branch>`.

   Der Hinweis fehlt nie, auch nicht bei kleinen Änderungen. Sonst nimmt jemand
   an, der Stand liege bereits auf dem Remote.

Bei Merge-Konflikt in `doc/projektdoku/arbeitsplaene/README.md`: beide Zeilen behalten. Ein
Konflikt im Index ist nie eine Entweder-oder-Entscheidung.
