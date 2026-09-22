# Tag-Map-Robustheit: eine alte Karte darf die Erkennung nicht stilllegen

**Status:** fertig — 22.09.2026
**Verantwortlich:** `Agent: Tag-Map-Migration und Platzhalter-Module`
**Thema:** apriltag
**Branch:** apriltag

## Ziel

Layer 1 lädt die Module wieder so, wie es der Stand vor dem Welttag-Umbau tat:
jeder erkannte Tag kommt als Modul heraus, auch ohne Karteneintrag, und das
Frontend bekommt seine Boxen. Eine veraltete oder kaputte `config/tagmap.json`
kostet ab jetzt Genauigkeit und Modulnamen — aber nicht den ganzen Server.

## Der Fehler

Beim Welttag-Umbau (`apriltag-welttag-konzept.md`) wurden zwei Dinge zu harten
Fehlern gemacht:

1. `load_tag_map` warf bei Schema `wsc.vision.tagmap/1` eine `ValueError`.
2. Ebenso bei den Rollen `robot_table` und `reference`.

Die Begründung war, ein stilles Umdeuten sei gefährlicher als eine klare
Ablehnung. Das stimmt für sich genommen — übersehen wurde aber die
**Aufrufstelle**: `AprilTagDetectionSource.open` rief `load_tag_map`
ungeschützt auf. Die Ausnahme lief durch bis `runner._open_source`, die Quelle
öffnete nicht, und das Vision-System blieb in `Preoperational`. Ergebnis: kein
Job lief mehr, es kamen keine Detektionen, und im Frontend erschien keine
einzige Box — wegen einer Textdatei, die aus dem vorherigen Stand stammte.

Sichtbar war davon nur „keine Referenz gefunden" und dass im Gegensatz zum
alten `vision-server`-Stand keine Module mehr geladen wurden.

## Bereits gelesen

- `CLAUDE.md`, `doc/arbeitsplaene/README.md`
- `doc/arbeitsplaene/apriltag-welttag-konzept.md` (verursachender Umbau)
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md`
- `doc/vision-server-interface.md` (Payload, `frameId`-Regel)

## Betroffene Dateien

- `src/tagloc/tagmap.py` — Migration statt Ablehnung
- `src/vision_server/detection/apriltag.py` — Rückfall auf leere Karte
- `tests/test_welttags.py`, `tests/test_apriltag_source.py`
- `doc/apriltag-lokalisierung.md`, `doc/apriltag-referenz.md`

## Schnittstellen

### Migration alter Karten

`load_tag_map` akzeptiert `wsc.vision.tagmap/1` und bildet die Rollen ab:

| alte Rolle | neue Rolle | Weltpose |
| --- | --- | --- |
| `world` | `world` | bleibt |
| `reference` | `world` | bleibt — war schon immer ein fester Anker mit Weltpose |
| `robot_table` | `robot` | **wird verworfen** — der Tisch ist beweglich |
| `module` | `module` | bleibt `null` |

```python
LEGACY_ROLES = {"reference": WORLD_ROLE, "robot_table": ROBOT_ROLE}
```

`moduleId`, `instanceId`, `sizeM` und `tagToModule` bleiben unangetastet — die
Modulzuordnung der alten Karte geht also nicht verloren.

**Jede** Umdeutung wird einzeln über `logging.WARNING` gemeldet, das Verwerfen
einer Weltpose ausdrücklich mit dem Wort `IGNORIERT`. Damit ist der Einwand aus
dem Vorgängerplan erledigt, ohne die Karte unbrauchbar zu machen.

Weiterhin `ValueError`: ein **unbekanntes Schema** und ein **Tippfehler in der
Rolle**. Ein Tag mit der Rolle `wrold` wäre sonst stumm weder Anker noch Modul.

Dauerhaft umstellen: einmal mit `python -m tagloc.cli.build_tagmap` neu
schreiben lassen — `save_tag_map` schreibt immer `/2`.

### Rückfall in `open()`

```python
try:
    self._tag_map = await self.run_blocking(load_tag_map, path)
except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
    _log.error("Tag-Map %s ist unbrauchbar (...) -- weiter OHNE Karte. ...")
    self._tag_map = empty_tag_map()
```

Ohne Karte meldet `locate_modules` jeden erkannten Tag als
`moduleId = "TAG-<id>"`, `instanceId = "tag-<id>"`, ohne CAD-Versatz und im
Kamera-KS (`frameId = cam_ceiling`). Genau das ist das Platzhalter-Verhalten,
das das Frontend zum Zeichnen der Boxen braucht.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| Karte im Schema `/1` | migriert, Meldungen je umgedeutetem Tag |
| Karte mit `robot_table` | wird `robot`, Weltpose verworfen, Meldung mit `IGNORIERT` |
| Karte unlesbar / kaputtes JSON | `ERROR`, weiter **ohne** Karte, Tags als `TAG-<id>` |
| Unbekanntes Schema | `ERROR`, weiter ohne Karte |
| Tippfehler in der Rolle | `ERROR`, weiter ohne Karte (die `ValueError` bleibt, `open` fängt sie) |
| Tag ohne Karteneintrag | wird als `TAG-<id>` gemeldet — kein Fehler, das war schon immer so |

## Vorgehen

1. `tagmap.py`: `LEGACY_ROLES`, `_migrate_role`, Schema `/1` akzeptieren.
2. `detection/apriltag.py`: Laden absichern, auf `empty_tag_map` zurückfallen.
3. Tests: Migration, verworfene Weltpose, Meldungen, Platzhalter-Module,
   `open()` mit kaputter Karte.
4. Fach-MDs nachziehen.

## Offene Fragen

- Keine.

## Abweichungen vom Plan

- Keine.

## Nach Abschluss

- [x] Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- [x] Zeile im Index `doc/arbeitsplaene/README.md`.
- [x] `doc/apriltag-lokalisierung.md` und `doc/apriltag-referenz.md` nachgezogen,
      Aussage „Schema /1 wird abgelehnt" dort korrigiert.
- [x] Testlauf: **328 Tests, OK** (vorher 316).
