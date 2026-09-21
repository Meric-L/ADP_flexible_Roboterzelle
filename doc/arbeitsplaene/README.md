# Arbeitspläne — wer macht gerade was

Dieser Ordner ist der **Doppelarbeits-Check vor der Umsetzung**. Jede und jeder —
Mensch wie Agent — schaut hier zuerst hinein, bevor mit einer Aufgabe begonnen
wird (siehe `CLAUDE.md`, Abschnitt „Bevor du anfängst").

Die Fach-MDs in `doc/` beschreiben, **was ist** — der fertige Stand, die
dokumentierten Schnittstellen. Dieser Ordner beschreibt, **was jemand vorhat**
und welche Schnittstellen dabei neu entstehen. Wir arbeiten parallel in
getrennten Chats und auf getrennten Rechnern; ohne diesen Index baut jemand
zum zweiten Mal, was schon läuft, oder gegen eine Schnittstelle, die es so
nicht gibt.

## Index

| Plan | Thema | Status | Verantwortlich | Aktualisiert |
| --- | --- | --- | --- | --- |
| [`lds-registrierung.md`](lds-registrierung.md) | mdns | fertig | `Agent: LDS-Registrierung für den Aggregation-Server` | 21.09.2026 |

Themen: `opcua` · `vision` · `apriltag` · `mdns` · `frontend` · `sonstiges`
Status: `geplant` · `in Arbeit` · `fertig` · `verworfen`

## So legst du einen Plan an

1. **Vorher lesen:** die Fach-MDs unter `doc/`, die das Thema betreffen — siehe
   Tabelle unten. Mindestens die, gegen deren Schnittstelle du arbeitest.
2. `_vorlage.md` nach `<thema>.md` kopieren (klein, mit Bindestrichen, z. B.
   `opcua-part10-wrapper.md`).
3. Kopf ausfüllen (Status, Verantwortlich, Thema, Branch). Den Abschnitt
   **Schnittstellen** so konkret schreiben, dass ein anderes Teammitglied ohne
   diesen Code dagegen entwickeln kann: NodeIds, Methodensignaturen,
   Python-Signaturen, Payloads, Ports, Fehlerfälle.
4. Eine Zeile in die Tabelle oben eintragen — das ist die Stelle, an der alle
   sehen, was läuft.
5. Nach Abschluss: Status auf `fertig`, tatsächliche Schnittstellen nachtragen,
   dauerhaftes Fachwissen in die passende Fach-MD unter `doc/` übernehmen.

## Kollision

Gehört ein Plan zum Thema bereits jemand anderem und steht auf `in Arbeit`:
nicht parallel implementieren. Stattdessen im bestehenden Plan einen Abschnitt
ergänzen oder die Person ansprechen.

## Fachdokumentation unter `doc/`

Vor dem Anlegen eines Plans lesen — hier steht der dokumentierte Stand:

| Datei | Inhalt |
| --- | --- |
| [`../vision-server-interface.md`](../vision-server-interface.md) | OPC-UA-Vision-Server: Adressraum, Methoden, Backend-Anbindung |
| [`../vision-system.md`](../vision-system.md) | Vision-System — Ist-Stand auf dem Raspberry Pi |
| [`../vision-system-integration.md`](../vision-system-integration.md) | Integration der Vision-Systeme in WebSkillComposition |
| [`../vision-system-next-steps.md`](../vision-system-next-steps.md) | Vision-System — offene Punkte und nächste Schritte |
| [`../apriltag-lokalisierung.md`](../apriltag-lokalisierung.md) | Konzept der AprilTag-Lokalisierung |
| [`../apriltag-referenz.md`](../apriltag-referenz.md) | AprilTag-Referenz |
| [`../apriltag-e2e-test.md`](../apriltag-e2e-test.md) | End-to-End-Testanleitung AprilTag |
| [`../altlasten.md`](../altlasten.md) | Bekannte Altlasten und Stolperfallen |
| [`../praesentation.md`](../praesentation.md) | Foliengrundlage der Betreuer-Präsentation |

Kommt eine Fach-MD dazu, hier eine Zeile ergänzen. Für Agenten listet das
Skript `.claude/hooks/doku_regeln.py` den Ordnerinhalt ohnehin automatisch auf.

## Technische Erinnerung

`.claude/settings.json` ruft bei jedem Sessionstart, bei jedem Subagenten und
vor dem ersten Schreibzugriff `.claude/hooks/doku_regeln.py` auf. Das Skript
gibt die Regeln aus `CLAUDE.md` mit und listet auf, welche Fach-MDs und welche
laufenden Pläne es gerade gibt. Alles liegt im Repo — nach dem Clonen gilt es
für jeden automatisch. Ohne Claude funktioniert der Ordner genauso, dann wird
die Regel per Hand eingehalten.
