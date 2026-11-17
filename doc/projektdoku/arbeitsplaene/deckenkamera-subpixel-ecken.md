# Ceiling camera: subpixel corners (subpix) against pose jitter

**Status:** in progress — 10.11.2026 (code switched, not yet confirmed on the Pi)
**Owner:** NobbisCode, `Agent: Subpixel corners on the ceiling Pi`
**Topic:** vision
**Branch:** `messkette-protokoll`

## Goal

The ceiling camera (`cam_ceiling`, layer 1) measures tag poses more calmly. So far ArUco
delivers integer corners there. A module tag at 1.6 m is only about 85 px in size; the
rounding shows up as jitter in depth and rotation. Afterwards the job refines the corners
with `CORNER_REFINE_SUBPIX`.

## Already read

- `doc/projektdoku/analysen/kamera-und-detektor-analyse.md` (sections 2 and 5: `CORNER_REFINE_APRILTAG`
  discarded, SUBPIX "only 0.1–0.3 px noise")
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md` (subpixel off on 24.09., ~46 % CPU — measured with `CORNER_REFINE_APRILTAG`)
- `doc/projektdoku/vision-server-interface.md` (section 15 subpixel corners, 18c brightness test)
- `doc/projektdoku/code-dokumentation.md` (Pi presets, `VISION_SUBPIXEL_CORNER_REFINEMENT`)

## Starting point: measurement

**Brightness test 09.11.2026, ceiling, row 1** (same tag, 25 images each):
corner standard deviation per corner, averaged.

| Light | ArUco without refinement | pupil_apriltags (subpixel-accurate) | z-sd | Rotation RMS |
| --- | --- | --- | --- | --- |
| 100 % | 0.68 px | 0.08 px | 5.9 mm | 8.7° |
| 80 % | 1.00 px | 0.11 px | 6.4 mm | 8.9° |
| 50 % | 1.07 px | 0.24 px | 8.0 mm | 9.1° |

**Cost, PC, 19 real 12 MP ceiling images** (row 1 and 2, all light levels), detector as on
the Pi (`perspectiveRemovePixelPerCell` 8):

- Detected IDs with and without `subpix`: identical in all 19 images.
- Runtime `detect()`: median 86.7 ms without, 89.0 ms with `subpix` (+3 %).
- The ~46 % CPU from the plan `deckenkamera-volle-aufloesung.md` concerned
  `CORNER_REFINE_APRILTAG`. That searches the whole image anew. `subpix` only refines a
  small window around each corner already found.

## Affected files

- `src/vision_server/server.py` (preset `cam_ceiling`)
- `tests/test_subpixel_settings.py`, `tests/test_server_config.py`
- `doc/projektdoku/vision-server-interface.md`, `doc/projektdoku/code-dokumentation.md`,
  `doc/projektdoku/analysen/kamera-und-detektor-analyse.md` (new section)

## Interfaces

No new interface. Only a default value is changed.

### Python

```python
# Module: src/vision_server/server.py
PI_APRILTAG_PRESETS["cam_ceiling"]["subpixel_corner_refinement"] = True   # new, previously missing = off
PI_APRILTAG_PRESETS["cam_ceiling"]["corner_refinement_method"] = "subpix"

def subpixel_corner_settings(preset: dict) -> dict:
    """Unchanged. Returns {"subpixel_corner_refinement": bool,
    "corner_refinement_method": "subpix" | "apriltag"}; raises ValueError
    for an unknown value of the environment variable."""
```

### Data / payload

No format change. `cornersPx` in job results and in the brightness test
(`frames[].aruco.cornersPx`) are now floating-point values instead of whole pixels on the
ceiling. The brightness test writes the switch to `detectors.aruco.subpixelCornerRefinement`.

### Network / operations

- Takes effect after `git pull` and a restart of `opcua-server.service` on the ceiling Pi.
- Switching back without code: `VISION_SUBPIXEL_CORNER_REFINEMENT=0` in the unit
  (`sudo systemctl edit opcua-server.service`).
- Affected are the job (`DetectAprilTags`), cell monitoring and the brightness test.
  The livestream overlay detector remains without refinement (`runner/livestream.py`).

### Failure cases

- Unknown value in `VISION_SUBPIXEL_CORNER_REFINEMENT`: start aborts with `ValueError`
  (as before).
- At very dark levels `cornerSubPix` can shift individual corners by 1–3 px (once 2.8 px
  in the PC test at 35 % light). The row on the Pi checks this.

## Approach

1. Switch the preset, adapt tests, update docs. Done on 10.11.
2. Ceiling Pi: `git pull`, restart the service, record one brightness-test row with the
   same light levels.
3. Compare with rows 1 and 2: corner noise, z-sd, rotation RMS, detection rate, runtime per level.
4. Enter the result in `kamera-und-detektor-analyse.md` and set the status to `done`.
   If it gets worse: switch the preset back and set the status to `dropped`.

## Open questions

- How much does the rotation spread drop? About 2/3 of the images are `ambiguous`
  (IPPE ambiguity of a tag seen flat). Subpixel alone presumably does not solve that.
- Is the runtime on the Pi also only a few percent higher? On the PC it was +3 %.

## Deviations from the plan

(none yet)

## After completion

- Status set to `done`, actual interfaces entered.
- Line in the index updated.
- Measurement result in `kamera-und-detektor-analyse.md`.
