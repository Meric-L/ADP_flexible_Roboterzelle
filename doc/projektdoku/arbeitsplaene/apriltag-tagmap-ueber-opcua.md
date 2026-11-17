# Set the tag map via OPC UA, and the size rule for tags

**Status:** done — 22.09.2026
**Owner:** `Agent: Tag-Map per OPC-UA-Methode setzen`
**Topic:** apriltag
**Branch:** apriltag

## Goal

Adding a new module requires **no intervention on the Pi**. Two parts:

1. **Fix the size rule.** The world tags are printed in a different size
   from all other tags; all module tags have the same size among themselves. The module size is thus stated
   once in the profile (`tag_size_m`), and the tag map contains only the world tags — with their deviating `sizeM`.
   A new module then needs exactly one entry in the frontend catalog.
2. **Variant A as the extension path.** Should a tag in a third size ever be added, the Pi must know about it.
   Instead of editing a file by hand for this, the backend sets the tag map via an OPC UA method.
   The Pi writes it locally and uses it immediately.

## Why the map nevertheless lives on the Pi

`localize_camera` needs `T_world_tag` and `sizeM` **per image** in the detection job. If the map lived in the
backend, the Pi would have to deliver raw `T_cam_tag`, `frameId` would always be the camera frame, and every
consumer would need the chaining — against the rule from `apriltag-lokalisierung.md` section 1.2 that exactly **one**
function performs it. The local file also remains the reason why the Pi can measure without a backend.

Variant A changes nothing here: the backend becomes the **maintenance point**, the file remains the local cache.

## Already read

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md` (no open `apriltag` plan)
- `doc/projektdoku/apriltag-lokalisierung.md` (sections 1.2, 3.2)
- `doc/projektdoku/apriltag-referenz.md` (tag-map schema, `tagloc` interfaces)
- `doc/projektdoku/vision-server-interface.md` (address space, methods, error codes)
- `doc/projektdoku/arbeitsplaene/apriltag-tagmap-robustheit.md` (predecessor)

## Affected files

- `src/tagloc/tagmap.py` — `tag_map_from_json` / `tag_map_to_json`
- `src/vision_server/detection/apriltag.py` — `apply_tag_map`
- `src/vision_server/runner.py` — method `SetTagMap`, node `TagMapJson`
- `src/vision_server/address_space.py` — node `TagMapJson`
- `src/vision_server/server.py` — `tag_size_m` per Pi (module tag size)
- `tests/test_tag_map.py`, `tests/test_apriltag_source.py`
- `doc/projektdoku/apriltag-lokalisierung.md`, `apriltag-referenz.md`,
  `vision-server-interface.md`

## Interfaces

### Size rule

| Tag | Size from | Where changed |
| --- | --- | --- |
| **World tag** | `sizeM` of the entry in `config/tagmap.json` | Tag map (changes only when the cell is rebuilt) |
| **Module tag** | `tag_size_m` of the profile, `PI_APRILTAG_PRESETS` | once, applies to all modules |
| **deviating tag** | own entry with `sizeM` in the tag map | via `SetTagMap`, no file access needed |

`TagMap.size_for(tag_id, default_m)` already implements this: if the tag is in the map, its `sizeM` wins,
otherwise the profile value applies. The only new thing is that `tag_size_m` now explicitly denotes the
**module tag size**.

A wrong tag size scales the measured distance linearly and otherwise goes unnoticed — that is why it belongs
in exactly one place per type.

### OPC UA

| Element | NodeId | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| `SetTagMap` | `ns=<vision>;s=<VisionSystemName>.SetTagMap` | Method | Call | Sets the cell's tag map |
| `TagMapJson` | `ns=<vision>;s=VisionMachine.TagMapJson` | Variable (String) | Read | The currently loaded map, as JSON |

```
SetTagMap(
    TagMapJson : String     -- complete map, schema wsc.vision.tagmap/2
) -> (
    Error : Int32           -- VisionErrorCode
)
```

| `Error` | When |
| --- | --- |
| `OK` (0) | map accepted, file written, source uses it immediately |
| `INVALID_ARGUMENT` (2) | invalid JSON, unknown schema, unknown role |
| `BUSY` (3) | a job or a calibration session is running |
| `INTERNAL` (6) | file not writable |

The map is **checked first, then written**: a rejected map leaves the running one untouched. Complaints from
`validate_tag_map` (e.g. only one world tag instead of four) are **not** an error — they are logged and the map
is accepted.

### Python

```python
# Module: src/tagloc/tagmap.py
def tag_map_from_json(text: str, *, source: str = "<string>") -> TagMap:
    """Map from JSON text. Raises ValueError on schema/role, like load_tag_map."""

def tag_map_to_json(tag_map: TagMap) -> str:
    """Map as JSON text, schema /2, sorted by tag id."""

# Module: src/vision_server/detection/apriltag.py
def apply_tag_map(self, tag_map: TagMap) -> None:
    """Takes over a new map into the running source.

    Discards the anchor: it was calculated against the old map and would
    silently be wrong with respect to the new one.
    """
```

### Data

The method's payload is exactly the file content of `config/tagmap.json` (schema `wsc.vision.tagmap/2`).
Thus the method and the file are the same format — what the backend sends can be put unchanged into the
file, and vice versa.

### Operation

Written to `config/tagmap.json` (path from `AprilTagProfileConfig.tag_map_path`). After a restart the most
recently set map therefore remains active — the Pi stays operable without a backend.

### Failure cases

| Case | Behaviour |
| --- | --- |
| job or calibration running | `BUSY`, map unchanged |
| invalid JSON / schema / role | `INVALID_ARGUMENT`, map unchanged |
| file not writable | `INTERNAL`, **map nevertheless active** — measuring takes precedence over persistence; at the next start the old one applies again |
| map without world tag | `OK` with log note; poses then remain in the camera frame |
| hand camera anchor | discarded, see `apply_tag_map` |

## Approach

1. `tagmap.py`: `tag_map_from_json` / `tag_map_to_json`, pull `load`/`save` onto them.
2. `detection/apriltag.py`: `apply_tag_map`.
3. `runner.py` + `address_space.py`: method `SetTagMap`, node `TagMapJson`.
4. `server.py`: comment `tag_size_m` as module tag size.
5. Tests.
6. Topic docs: add size rule and method.

## Open questions

- The actual edge lengths (world tag vs. module tag) have not yet been measured. Until then the previous values remain.
- **Frontend does not use `SetTagMap`/`TagMapJson` yet.** In the repo `webskillcomposition`
  (`frontend/src/features/opcua-server/`) there has so far been no hit for `TagMap`/`SetTagMap` -- neither a
  type/parser analogous to `cameraCalibration.ts` (`parseCalibrationProgress`), nor a hook analogous to
  `useCameraCalibration.ts`, nor a UI component analogous to `CameraCalibrationModal.tsx`. For entering a new
  module via the interface, the following is therefore still missing:
  - a model for the tag map (parsing/validating the JSON from `TagMapJson`, building the JSON for `SetTagMap`),
  - a hook that subscribes to `TagMapJson` and calls `SetTagMap`, including handling of `BUSY` / `INVALID_ARGUMENT` / `INTERNAL`,
  - a UI location where a new module (tag ID, role, possibly deviating `sizeM`) can be entered and accepted,
    instead of editing the map by hand on the Pi.

## Deviations from the plan

- **`TagMapJson` and `SetTagMap` hang on the `apriltag` source, not on the calibration session.** The plan
  said only "if there is a source"; in the code the nearest hook was the block of calibration methods. The method
  is therefore mirrored under `VisionProgram` via the same `calibration_methods` collection — this is not
  coincidence, but the same route the calibration methods already take.
- **`tag_size_m` was not changed.** The real edge lengths have not yet been measured; a guessed value would be
  worse than the previous one. The rule is in the topic doc; the number is entered after measuring.

## After completion

- [x] Status set to `done`, actual interfaces entered.
- [x] Index line in `doc/projektdoku/arbeitsplaene/README.md`.
- [x] Size rule in `apriltag-lokalisierung.md` section 3.2, method in `vision-server-interface.md` section 12.5a/12.5b and in the address-space tree.
- [x] Test run: **333 tests, OK** (previously 328).

Still open is entering the **measured** edge lengths: `sizeM` of the world tags in `config/tagmap.json`,
`tag_size_m` of the module tags in the profile.
