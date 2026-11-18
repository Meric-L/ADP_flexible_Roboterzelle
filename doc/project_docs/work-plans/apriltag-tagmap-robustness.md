# Tag-map robustness: an old map must not switch detection off

**Status:** done — 22.09.2026
**Owner:** `Agent: Tag-map migration and placeholder modules`
**Topic:** apriltag
**Branch:** apriltag

## Goal

Layer 1 loads the modules again the way the state before the world-tag restructuring did:
every detected tag comes out as a module, even without a map entry, and the frontend gets
its boxes. An outdated or broken `config/tagmap.json` now costs accuracy and module names —
but not the entire server.

## The bug

During the world-tag restructuring (`apriltag-world-tag-concept.md`) two things were turned
into hard errors:

1. `load_tag_map` raised a `ValueError` for schema `wsc.vision.tagmap/1`.
2. The same for the roles `robot_table` and `reference`.

The reasoning was that silently reinterpreting is more dangerous than a clear rejection.
That is right in itself — but the **call site** was overlooked: `AprilTagDetectionSource.open`
called `load_tag_map` unprotected. The exception propagated up to `runner._open_source`, the
source did not open, and the vision system stayed in `Preoperational`. Result: no job ran any
more, no detections came in, and in the frontend not a single box appeared — because of a
text file left over from the previous state.

Of this, only the message "keine Referenz gefunden" (no reference found) was visible, and the
fact that, unlike the old `vision-server` state, no modules were loaded any more.

## Already read

- `CLAUDE.md`, `doc/work-plans/README.md`
- `doc/work-plans/apriltag-world-tag-concept.md` (the restructuring that caused it)
- `doc/apriltag-localization.md`, `doc/apriltag-reference.md`
- `doc/vision-server-interface.md` (payload, `frameId` rule)

## Affected files

- `src/tagloc/tagmap.py` — migration instead of rejection
- `src/vision_server/detection/apriltag.py` — fallback to an empty map
- `tests/test_welttags.py`, `tests/test_apriltag_source.py`
- `doc/apriltag-localization.md`, `doc/apriltag-reference.md`

## Interfaces

### Migration of old maps

`load_tag_map` accepts `wsc.vision.tagmap/1` and maps the roles:

| old role | new role | World pose |
| --- | --- | --- |
| `world` | `world` | remains |
| `reference` | `world` | remains — has always been a fixed anchor with a world pose |
| `robot_table` | `robot` | **discarded** — the table is movable |
| `module` | `module` | remains `null` |

```python
LEGACY_ROLES = {"reference": WORLD_ROLE, "robot_table": ROBOT_ROLE}
```

`moduleId`, `instanceId`, `sizeM` and `tagToModule` remain untouched — the module assignment
of the old map therefore does not get lost.

**Every** reinterpretation is reported individually via `logging.WARNING`; discarding a world
pose is reported explicitly with the word `IGNORIERT` (IGNORED). This settles the objection
from the predecessor plan without making the map unusable.

Still a `ValueError`: an **unknown schema** and a **typo in the role**. A tag with the role
`wrold` would otherwise silently be neither anchor nor module.

To switch permanently: have it rewritten once with `python -m tagloc.cli.build_tagmap` —
`save_tag_map` always writes `/2`.

### Fallback in `open()`

```python
try:
    self._tag_map = await self.run_blocking(load_tag_map, path)
except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
    _log.error("Tag-Map %s ist unbrauchbar (...) -- weiter OHNE Karte. ...")
    self._tag_map = empty_tag_map()
```

Without a map, `locate_modules` reports every detected tag as
`moduleId = "TAG-<id>"`, `instanceId = "tag-<id>"`, without CAD offset and in the camera CS
(`frameId = cam_ceiling`). That is exactly the placeholder behaviour the frontend needs to
draw the boxes.

### Failure cases

| Case | Behaviour |
| --- | --- |
| Map in schema `/1` | migrated, message per reinterpreted tag |
| Map with `robot_table` | becomes `robot`, world pose discarded, message with `IGNORIERT` |
| Map unreadable / broken JSON | `ERROR`, continues **without** a map, tags as `TAG-<id>` |
| Unknown schema | `ERROR`, continues without a map |
| Typo in the role | `ERROR`, continues without a map (the `ValueError` remains, `open` catches it) |
| Tag without a map entry | reported as `TAG-<id>` — no error, that was always so |

## Approach

1. `tagmap.py`: `LEGACY_ROLES`, `_migrate_role`, accept schema `/1`.
2. `detection/apriltag.py`: secure the loading, fall back to `empty_tag_map`.
3. Tests: migration, discarded world pose, messages, placeholder modules, `open()` with a broken map.
4. Update the topic docs.

## Open questions

- None.

## Deviations from the plan

- None.

## After completion

- [x] Status set to `done`, actual interfaces entered.
- [x] Line in the index `doc/work-plans/README.md`.
- [x] `doc/apriltag-localization.md` and `doc/apriltag-reference.md` updated; the statement „Schema /1 wird abgelehnt“ (schema /1 is rejected) there corrected.
- [x] Test run: **328 tests, OK** (previously 316).
