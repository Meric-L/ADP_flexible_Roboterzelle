# Aufräumen beider Repos und Code-Handbuch

**Status:** in Arbeit — 30.10.2026
**Verantwortlich:** Meric, `Agent: Aufräumen und Code-Handbuch`
**Thema:** sonstiges
**Branch:** Doku auf `apriltag`; Code auf `apriltag-aufraeumen` (dieses Repo) und `ungetestet-aufraeumen` (webskillcomposition) — beide lokal, ungemergt

## Ziel

Über Wochen paralleler Arbeit haben sich in beiden Repos toter Code, Doppelungen
(z. B. mehrere Kalibrier-Skripte), Behelfe und unsaubere Stellen angesammelt. Die
bisherigen Listen ([`../altlasten.md`](../altlasten.md), [`../altlasten-toter-code.md`](../altlasten-toter-code.md))
sind vom 21.09./21.10. und beziehen sich bei WSC noch auf Branch `tag-einmessen`.

Hinterher gibt es:

1. zwei aktuelle Audits — [`../altlasten-zelle.md`](../altlasten-zelle.md) (dieses Repo, `apriltag`) und
   [`../altlasten-wsc.md`](../altlasten-wsc.md) (webskillcomposition, nur unser Anteil `origin/dev..Ungetestet`);
2. ein Code-Handbuch [`../code-handbuch.md`](../code-handbuch.md), in dem man für jede Datei unseres
   Codes nachlesen kann, wozu sie da ist und wo die Details stehen;
3. die Funde abgearbeitet auf eigenen Branches, die erst auf Ansage gemergt werden.

## Bereits gelesen

- `CLAUDE.md`: Regeln, Arbeitsplan-Pflicht, Deutsch als Sprache, kein Löschen/Umbenennen von Fach-MDs
- `doc/projektdoku/arbeitsplaene/README.md` und `_vorlage.md`: Index, Format
- `doc/projektdoku/altlasten.md`, `doc/projektdoku/altlasten-toter-code.md`: bisherige Funde (werden übernommen und neu geprüft)
- Branch `apriltag-refactoring` (Plan `refactoring-apriltag.md` dort): Stand 23.09., 149 Commits hinter `apriltag`, wird nicht gemergt — nur Ideenquelle

## Betroffene Dateien

Doku (Branch `apriltag`):

- `doc/projektdoku/altlasten-zelle.md`, `doc/projektdoku/altlasten-wsc.md`, `doc/projektdoku/code-handbuch.md` (neu)
- `doc/projektdoku/altlasten.md`, `doc/projektdoku/altlasten-toter-code.md` (nur Kopfhinweis „abgelöst durch …“)
- `doc/projektdoku/arbeitsplaene/README.md` (Indexzeile, Fachdoku-Tabelle), `README.md` (Doku-Tabelle)

Code: die in den Audits mit ID genannten Stellen. Jede Änderung trägt die Fund-ID in der
Commit-Message, damit sie sich einzeln nachvollziehen und zurücknehmen lässt.

## Schnittstellen

Das Aufräumen ändert **keine** dokumentierte Schnittstelle: OPC-UA-NodeIds, Methoden und
ihre Argumente, Payload-Felder, Dateiformate (`tagmap.json`, Kalibrier-JSON) und die
CLI-Befehle `python -m tagloc.cli.*` bleiben, wie sie in den Fach-MDs stehen. Wo ein Fund
das nur mit einer Schnittstellenänderung lösen könnte, bleibt er im Audit offen und wird
mit dem Team abgestimmt.

Python-Importpfade innerhalb von `src/` können sich ändern, wenn toter Code wegfällt oder
Doppelungen zusammengeführt werden. Die Tests (`PYTHONPATH=src python -m unittest discover -s tests -t .`)
müssen danach unverändert grün sein.

## Vorgehen

1. Audit beider Repos (toter Code, Doppelungen, Behelfe, Richtlinien, Testlücken), Funde mit Beleg und ID.
2. Audits und Code-Handbuch auf `apriltag` schreiben und pushen.
3. Aufräumen auf `apriltag-aufraeumen` bzw. `ungetestet-aufraeumen`, nach Risiko: toter Code →
   Doppelungen → Richtlinien → Behelfe (nur, wenn die Abbaubedingung erfüllt ist). Dateien, an
   denen auf `apriltag` gerade viel gearbeitet wird, nur behutsam anfassen (Konfliktrisiko beim
   späteren Merge).
4. Nach jedem Block alle Tests; erledigte Funde im Audit mit Branch und Commit markieren.


## Aufteilung auf zwei Sitzungen (ab 30.10.2026)

Ab hier arbeiten zwei Claude-Sitzungen parallel. Jede hat **ihr eigenes Repo, ihren eigenen Branch,
ihren eigenen Audit und ihren eigenen Handbuch-Teil**. Dadurch fassen sie nie dieselbe Datei an.

| | Sitzung A — Zelle | Sitzung B — webskill |
| --- | --- | --- |
| Worktree | `/home/meric/ADP/ADP_flexible_Roboterzelle/.claude/worktrees/apriltag-aufraeumen` | `/home/meric/ADP/webskillcomposition/.claude/worktrees/ungetestet-aufraeumen` |
| Branch (lokal, ungemergt) | `apriltag-aufraeumen` (Basis `apriltag`) | `ungetestet-aufraeumen` (Basis `Ungetestet` `5812275`) |
| Funde | [`../altlasten-zelle.md`](../altlasten-zelle.md) | [`../altlasten-wsc.md`](../altlasten-wsc.md) |
| Handbuch | `code-handbuch.md`, nur **Teil A** | `code-handbuch.md`, nur **Teil B** |
| Status nachtragen in | `altlasten-zelle.md`, Abschnitt 12 „Umsetzungsstand“ | `altlasten-wsc.md`, Abschnitt 11 „Umsetzungsstand“ |
| Tests | `PYTHONPATH=src /home/meric/ADP/ADP_flexible_Roboterzelle/.venv/bin/python -m unittest discover -s tests -t .` (Ausgangsstand 877 OK) | in `frontend/`: `npx tsc --noEmit`, `npx vitest run` (79 Dateien / 836 Tests); in `backend/`: `/home/meric/ADP/webskillcomposition/backend/.venv/bin/python -m pytest -q` (81 + 1 übersprungen) |

**Gemeinsame Regeln**

- Claude Code **im eigenen Worktree starten**, nicht in `/home/meric/ADP` — sonst blockiert der Hook
  `doku_regeln.py` jedes Write/Edit (Fund B14 in `altlasten-zelle.md`).
- Ein Commit je Fund-ID oder kleiner Gruppe, ID vorn in der Message (`aufraeumen(T1): …`). Nach jedem Block
  alle Tests. Code-Branches nicht pushen, nichts mergen, kein Force-Push.
- Keine dokumentierte Schnittstelle ändern (OPC-UA-Knoten/Methoden, Payloads `wsc.vision.*`, `tagmap.json`,
  CLI-Optionen). Was nur mit Schnittstellenänderung geht, bleibt offen.
- Vor jedem Löschen die Verwender-Suche selbst wiederholen; Belege im Audit können veraltet sein.
- Funde unter „Entscheidung nötig“ werden **nicht** umgesetzt, sondern beim Nutzer nachgefragt.

### Sitzung A — Zelle (`apriltag-aufraeumen`)

**Nicht anfassen:**

- heiße Dateien: `src/vision_server/runner.py`, `detection/apriltag.py`, `server.py`, `stream_overlay.py`,
  `cellwatch.py`, `config.py`, `src/tagloc/overlay.py`, `src/tagloc/boards.py` (Ausnahme: einzeilige
  Kommentar-/Docstring-Korrektur);
- vom Plan [`handeye-genauigkeit.md`](handeye-genauigkeit.md) reserviert: `src/vision_server/handeye_session.py`,
  `src/tagloc/handeye.py`, `src/tagloc/cli/calibrate_handeye.py`, `src/tagloc/pose.py`, `src/tagloc/detector.py`;
- `apriltag-refactoring` nicht mergen. Einzelne Commits per Cherry-pick (`-x`) nur, wenn sie ohne Konflikt
  passen und keine der obigen Dateien berühren; sonst Fund offen lassen.

**Reihenfolge:**

1. **T1** Prototyp `src/apriltag/` löschen, Verweise in Docstrings und Doku mitziehen.
2. **Text und Hygiene:** T11, B2 (`.gitignore`-Ausnahmen), B3, B5 (README-Struktur), B6 (`betreuer/`-Verweise), B7 (`cli/calibrate.py`: Taste „S“, `--capture-to` headless), B10 (`diagnose_board` 7x10), B14 (Hook robust machen).
3. **Toter Code:** T2, T3 (Cherry-pick-Kandidat `7e67251`), T4, T5, T9 (nur `profiles.py`), T10 (ohne heiße/reservierte Dateien), T14 (nur `cancel_running`).
4. **D11/B9** Hostnamen in `src/jobs/calibrate.py` an die echten Pi-Namen angleichen, ohne `server.py` zu ändern; dazu Test **L3**.
5. **Doppelungen:** D12 (`tests/_support.py`, Kandidaten `1c938f6`, `9a7942f`), D9, D13, D15 und D10 nur in nicht heißen Dateien, D16.
6. **Tests:** L1 + L6 (`VisionProgram`, `ua_program`; Vorlage `test_ua_methoden.py` aus `dfcfa4d`), L7, L12.
7. **U4, U6**; zuletzt **U2** (englische Docstrings übersetzen) nur in nicht heißen, nicht reservierten Dateien.

**Entscheidung nötig:** T6/T7, T8, T12/T13, B1 (`data/` versionieren?), B11, B13, D4 und alle Zerlegungen aus
Abschnitt 7 (erst nach Merge von `usecase-foto`; D4 nach `handeye-genauigkeit`).

### Sitzung B — webskill (`ungetestet-aufraeumen`)

**Vorab:** Block 1 (T1–T16, T18–T20, B9, B12, B13) hat Sitzung A noch an einen Agenten vergeben. Sitzung B
beginnt erst, wenn Sitzung A meldet, dass Block 1 committet ist, und macht nach dem letzten Commit auf
`ungetestet-aufraeumen` weiter (Log `Ungetestet..ungetestet-aufraeumen`).

**Umgebung:** `frontend/node_modules` und die Inhalte von `frontend/public/urdf/` sind Symlinks aus dem
Haupt-Checkout (Submodul, `info/exclude`) — nie committen, Dateien immer einzeln hinzufügen. 3D-Modelle sind
nur LFS-Zeiger. `origin` (git.rwth-aachen.de) braucht einen HTTPS-Login; ohne ihn bleibt `origin/dev` auf
`d824975`. Kommentare im Code englisch (wie `dev`).

**Nicht anfassen:** Dateien und Zeilen aus dem Revert `839857c` (Spalte „dev?“ = Revert: `features/task/*`,
Docs, Backend-Mischdateien), dev-Code, B5 (Spielmodelle, Lizenzfrage), B7 (Testphase-Schalter),
B8 (Alt-Pi-Zweige), B10, D13/B6 (Katalog-Default), D14/U6 (tag-tool), die Welttag-Kette (Abschnitt 10 —
eigenes Feature mit eigenem Arbeitsplan).

**Reihenfolge:**

1. **Gemeinsame Helfer:** D6 + D15 (`vec3.ts`), D7 (Winkel), D9 (Format/Parse), D17, D18 (eine Geschwindigkeitskonstante), D10 (`createStore`).
2. **Pi-Aufrufe:** D1 (ein `callPiMethod` mit `MethodSpec`), D4 (`useSubscribedNode`/`usePiBinding`), D5 (`useRobotModelLoaded`), D2 (Warte-Helfer), dann D3 (`usePiCommand`) — **zuerst** Test für den neuen Hook (L1).
3. **Konfiguration:** D19 + B1–B4 (eine `PI_SERVERS`-Liste mit Rolle, Label, Adresse); U4 (Hook in Schleife in `PiActivity.tsx`).
4. **Struktur:** U5 (Zyklen zwischen Features auflösen, `index.ts` je Feature), danach U3-Zerlegungen (`ModuleCatalogModal`, `TagReferencePanel`, `ApproachPlanPanel`, `usePickPlaceRun`, `HandEyeAutoPanel`, `modelPlanes`), eine Datei je Commit.

**Doku von Sitzung B** liegt im Zellen-Repo auf `apriltag`: eigenen temporären Worktree vom aktuellen
`origin/apriltag` anlegen (nicht den von Sitzung A benutzen), nur `altlasten-wsc.md` (Abschnitt 11) und Teil B
von `code-handbuch.md` ändern, committen, mit Rebase auf `origin/apriltag` ziehen, fast-forward pushen
(Doku-Push auf `apriltag` ist freigegeben), Worktree entfernen.

## Offene Fragen

- Unklare Werkzeuge (z. B. `src/vision_server/tools/pi_load_profiler.py`) werden nicht gelöscht,
  sondern im Audit als Frage an das Team geführt.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt.

## Nach Abschluss

- Status auf `fertig`, Index aktualisiert.
- Merge von `apriltag-aufraeumen` in `apriltag` und `ungetestet-aufraeumen` in `Ungetestet` nur auf Ansage.
