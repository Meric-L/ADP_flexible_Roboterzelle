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

## Offene Fragen

- Unklare Werkzeuge (z. B. `src/vision_server/tools/pi_load_profiler.py`) werden nicht gelöscht,
  sondern im Audit als Frage an das Team geführt.

## Abweichungen vom Plan

Wird während der Umsetzung gefüllt.

## Nach Abschluss

- Status auf `fertig`, Index aktualisiert.
- Merge von `apriltag-aufraeumen` in `apriltag` und `ungetestet-aufraeumen` in `Ungetestet` nur auf Ansage.
