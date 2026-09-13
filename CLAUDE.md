# ADP flexible Roboterzelle — Arbeitsanweisung für Claude

Gruppenarbeit zur Positions- und Topologieerfassung mechatronischer Module in
einer rekonfigurierbaren Labor-Roboterzelle (ca. 5x5 m). Das Repo enthält
Implementierung (`src/`, `hardware/`, `data/`) und die LaTeX-Ausarbeitung (`doc/`).

**Sprache: Deutsch.** Alle Dateien in diesem Repo — Code-Kommentare,
Dokumentation, Commit-Messages, Log-Einträge — werden auf Deutsch geschrieben.

## Wie dieses Repo gelesen wird

Das Team arbeitet in drei Arbeitsbereichen. Für jeden gibt es eine
Wissensdatei unter `wissen/`. **Lies nur den Bereich, den die aktuelle
Aufgabe betrifft** — nicht alle drei. Jede Bereichsdatei ist selbst ein
Index: sie verweist auf die ausführlichen Dokumente, statt sie zu
wiederholen. Lies die verlinkten Tiefendokumente erst, wenn die
Bereichsdatei allein die Frage nicht beantwortet.

| Bereich | Datei | Lesen bei Aufgaben zu … | Dahinter liegt |
| --- | --- | --- | --- |
| Physischer Aufbau | [`wissen/aufbau.md`](wissen/aufbau.md) | Zellengeometrie, Kamerapositionen, Halterungen, Welt- und Tischtags, Hardware-Architektur, Ablauf der Scan-Kette, offene Konzeptfragen | Hardware-Architekturbild, Ablaufdiagramm, `concept/offene_punkte.md` |
| Vision-System | [`wissen/vision.md`](wissen/vision.md) | Kamerakalibrierung, AprilTag-/ArUco-Erkennung, Pose-Schätzung, Bildpipeline, Tag-Familien und -Größen, Layer-1-/Layer-2-Detektion | Skripte in `src/apriltag/`, `src/apriltag/MANUAL_TEST.md` |
| OPC UA | [`wissen/opcua.md`](wissen/opcua.md) | OPC-UA-Server auf dem Raspberry Pi, OPC 40100 Machine Vision, Adressraum, Nodeset, `asyncua`, Ergebnis-Payload, Integration in WebSkillComposition | `doc/vision-system.md`, `doc/vision-system-next-steps.md`, `doc/vision-system-integration.md` |

Betrifft eine Erkenntnis zwei Bereiche, gehört sie in den Bereich, der sie
besitzt, und wird im anderen mit einer Zeile plus Link erwähnt — nicht
doppelt ausgeschrieben.

## Erkenntnisse festhalten

Wir arbeiten parallel in getrennten Chats. Alles, was sonst nur im Chat
stünde und die anderen betrifft — ein Messergebnis, eine getroffene
Entscheidung, ein Irrweg, eine widerlegte Annahme — wird als Log-Eintrag in
die passende Bereichsdatei geschrieben. Das ist der einzige Weg, wie
Erkenntnisse aus meinem Chat in den Chat der anderen kommen.

Dafür gibt es die Skill `projektwissen` (`.claude/skills/projektwissen/`).
Sie lädt den richtigen Bereich, hängt den Eintrag im festen Format an und
schlägt den Commit vor. **Format und Regeln stehen in der Skill** — bei
Arbeit an den `wissen/`-Dateien immer die Skill benutzen, damit die
Einträge einheitlich und gut mergebar bleiben.

## Regeln für Änderungen

- Vor dem Lesen einer Bereichsdatei `git pull` — die anderen schreiben
  in dieselben Dateien.
- Bestehende Dokumente in `doc/` und `concept/` nicht umschreiben, um etwas
  Neues unterzubringen. Neue Erkenntnisse kommen in das Log der
  Bereichsdatei.
- Nichts pushen ohne ausdrückliche Zustimmung. Commit vorschlagen, Diff
  zeigen, warten.
- `doc/*.tex` ist die schriftliche Ausarbeitung. Nur anfassen, wenn die
  Aufgabe ausdrücklich die Ausarbeitung betrifft.
