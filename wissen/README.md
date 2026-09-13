# wissen/ — gemeinsamer Wissensstand

Wir arbeiten in getrennten Chats und getrennten Arbeitsbereichen. Ohne
gemeinsame Ablage findet jeder dasselbe zweimal heraus. Dieser Ordner ist die
Ablage: **drei Dateien, eine pro Arbeitsbereich.**

| Datei | Bereich |
| --- | --- |
| [`aufbau.md`](aufbau.md) | Physischer Aufbau — Zelle, Kameras, Halterungen, Referenz-Tags, Ablaufkette |
| [`vision.md`](vision.md) | Vision-System — Kalibrierung, Markererkennung, Pose-Schätzung |
| [`opcua.md`](opcua.md) | OPC UA — Server, OPC 40100, Adressraum, Integration |

Jede Datei hat denselben Aufbau: **Kernfakten** (der aktuelle Stand, kurz),
**Tiefendokumente** (Links auf die ausführlichen Dokumente in `doc/`,
`concept/`, `hardware/`, `src/`), **Offene Fragen**, **Log**.

## Warum Links statt Inhalte

Die Dateien wiederholen nichts, was schon in `doc/` oder `concept/` steht — sie
verweisen darauf. Zwei Kopien desselben Inhalts laufen auseinander, und dann
weiß niemand, welche stimmt. Wer etwas Ausführliches schreibt, schreibt es
weiterhin in `doc/` und verlinkt es hier.

## Was ins Log gehört

Alles, was sonst nur im eigenen Chat stünde und die anderen betrifft: ein
Messergebnis, eine getroffene Entscheidung, ein Irrweg, eine widerlegte
Annahme, eine Stolperfalle. Nicht: Zwischenschritte und Vermutungen.

Einträge werden **unten angehängt, nie überschrieben** — auch falsche bleiben
stehen und bekommen einen neuen Eintrag mit `Status: verworfen`. Ein sichtbarer
Irrweg verhindert, dass ihn der nächste wiederholt.

Format:

```markdown
### JJJJ-MM-TT — Name — Kurztitel
**Status:** offen | erledigt | verworfen
**Betrifft:** aufbau | vision | opcua

Was herausgefunden wurde. Konkret: Zahlen, Dateinamen, Fehlermeldungen.

**Konsequenz:** Was die anderen deswegen anders machen müssen.
```

Widerlegt ein Eintrag einen Kernfakt oben in der Datei, wird der Kernfakt
mitkorrigiert. Sonst veralten sie und niemand vertraut ihnen mehr.

## Für Claude-Nutzer

`CLAUDE.md` im Repo-Root ist der Index: er beschreibt die drei Bereiche, damit
der Chat selbst entscheidet, welche Datei er für die aktuelle Aufgabe braucht,
statt alles zu laden. Zum Anhängen von Einträgen gibt es die Skill
`projektwissen` unter `.claude/skills/projektwissen/` — sie liegt im Repo, also
hat sie jeder nach dem Clonen automatisch. Sie hält das Format einheitlich und
schlägt den Commit vor, ohne zu pushen.

Ohne Claude funktioniert der Ordner genauso — dann wird das Format per Hand
eingehalten.

## Merge-Konflikte

Im `## Log` beide Einträge behalten und chronologisch sortieren. Ein Konflikt
in einem Log ist nie eine Entweder-oder-Entscheidung.
