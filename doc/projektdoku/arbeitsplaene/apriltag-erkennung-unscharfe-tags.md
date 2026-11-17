# AprilTag detection for blurred tags of the ceiling camera

**Status:** done — 19.10.2026
**Owner:** Meric (`Agent: Erkennung unscharfe Tags`)
**Topic:** vision
**Branch:** apriltag

## Goal

On the ceiling camera image `data/rohbilder/cam_ceiling_20261019-114704.png`
(4056×3040, fisheye) all five visible tag36h11 tags are found
(IDs 0, 1, 2, 3, 583). Today `ArucoTagDetector` with the OpenCV default parameters
only returns [1, 2, 583]. This belongs to Meric's plan
[`deckenkamera-volle-aufloesung.md`](deckenkamera-volle-aufloesung.md): that one is about
resolution and stream, here it is about detection quality on the full image. This plan
supplements it; nothing is changed in the other plan.

## Finding

- The image is optically soft: edges 4–8 px wide for cells of 6–13 px.
- Tag 0 (approx. 60 px edge) has 4 bit errors with 3 correctable, so it is discarded.
- Tag 3 (approx. 50 px edge, contrast only 60 grey levels) is decoded incorrectly.
- Three `DetectorParameters` fix this: `errorCorrectionRate = 1.0`
  (default 0.6), `perspectiveRemoveIgnoredMarginPerCell = 0.3` (default
  0.13), `perspectiveRemovePixelPerCell = 8` (default 4).
  Result [0, 1, 2, 3, 583] in 0.1 s on the PC, no additional cost.
- The parameter field is robust: margin 0.2–0.4 and ppc 8–10 all deliver all five.
- The 40 chessboard images in `data/LV1` (no tags) produce 0 false alarms.
- **Risk:** With `errorCorrectionRate` 1.0 up to 5 instead of 3 bit errors are corrected.
  The theoretical false-alarm risk per quad candidate rises from about 0.03 % to about
  1.5 %. The border check before decoding filters out most of the candidates; no false
  alarm appeared on the test images. Keep an eye on it in case ghost IDs appear in
  operation (then lower `error_correction_rate` to about 0.8).
- **Subpixel:** `subpixel_corner_refinement` (`CORNER_REFINE_APRILTAG`)
  stays off. With default parameters it loses tags (only [583]) and takes
  2.7 s. It is not touched here.

## Already read

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md` (no other plan on this topic)
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md`
- `doc/projektdoku/apriltag-referenz.md`

## Affected files

- `src/tagloc/detector.py`
- `src/tagloc/cli/_common.py`, `detect.py`, `build_tagmap.py`,
  `calibrate_handeye.py`
- `src/vision_server/profiles.py`
- `src/vision_server/server.py`
- `src/vision_server/detection/apriltag.py`
- `src/vision_server/runner.py`
- `tests/test_detector.py`
- `doc/projektdoku/apriltag-referenz.md` (new section at the end)

## Interfaces

### Python

```python
# Module: src/tagloc/detector.py
class ArucoTagDetector:
    def __init__(self, family="tag36h11", *, parameters=None,
                 subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                 error_correction_rate: float | None = None,
                 perspective_remove_margin_per_cell: float | None = None,
                 perspective_remove_pixel_per_cell: int | None = None) -> None: ...

def build_detector(family="tag36h11", backend="aruco", *,
                   subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
                   error_correction_rate: float | None = None,
                   perspective_remove_margin_per_cell: float | None = None,
                   perspective_remove_pixel_per_cell: int | None = None) -> TagDetector: ...
```

| Parameter | Type | Default | `DetectorParameters` field | Meaning |
| --- | --- | --- | --- | --- |
| `error_correction_rate` | `float \| None` | `None` | `errorCorrectionRate` | Share of correctable bit errors; OpenCV default 0.6, 1.0 = all |
| `perspective_remove_margin_per_cell` | `float \| None` | `None` | `perspectiveRemoveIgnoredMarginPerCell` | ignored cell margin during bit evaluation; default 0.13 |
| `perspective_remove_pixel_per_cell` | `int \| None` | `None` | `perspectiveRemovePixelPerCell` | resolution of the rectified cell; default 4 |

`None` keeps the OpenCV default; existing callers do not change. The values only apply to
`"aruco"`/`"auto"`; `pupil` ignores them. An attribute that the OpenCV version does not
know is skipped (debug log).

### Configuration

`AprilTagProfileConfig` (`src/vision_server/profiles.py`) gets three fields of the same
name with default `None`. `PI_APRILTAG_PRESETS["cam_ceiling"]` sets them to 1.0 / 0.3 / 8,
`cam_flange` stays unchanged. Job detector, stream overlay and cell watch get the same values.

### CLI

`--error-correction-rate`, `--perspective-remove-margin`,
`--perspective-remove-ppc` in `detect`, `build_tagmap`, `calibrate_handeye`
(via `add_detection_arguments`), all default `None`.

### Failure cases

No new error path. Unknown attributes in older OpenCV versions are skipped; detection
then runs with the default.

## Approach

1. Plan (this document).
2. Parameters in `detector.py`, config, preset, all detector creations, CLI.
3. Tests: parameters are set, `None` keeps the default, regression test on the raw image
   with the real `cam_ceiling` values.
4. New section in `apriltag-referenz.md`.

## Open questions

- Are the three attributes with the same name present on the Pi (OpenCV 4.x)? The code
  sets them defensively; this has not yet been verified there.
- False-alarm rate in continuous operation of the cell (only test images checked).

## Deviations from the plan

- `calibrate_handeye` does not use `add_detection_arguments`, so it receives the three
  options via the new helper `_common.add_decoder_arguments` (likewise called by
  `add_detection_arguments`). `_common.detector_options(args)` returns the keywords for
  `build_detector`.
- Tests: in addition to `tests/test_detector.py`, adapted `tests/test_camera_owner.py`
  (stream detector, new test) and `tests/test_cellwatch.py` (dummy config extended by the
  three fields).
- Implemented as planned: detector, config fields, preset `cam_ceiling` 1.0 / 0.3 / 8,
  job (`detection/apriltag.py`), stream overlay and cell watch (`runner.py`) with the same
  values.

## After completion

- Status set to `done`, actual interfaces entered.
- Index line updated.
- Lasting knowledge carried over into `apriltag-referenz.md`.

## Addendum 19.10.2026

The camera has been refocused, the values 1.0 / 0.3 / 8 no longer apply and have been
discarded. The individual keywords described above `error_correction_rate`,
`perspective_remove_margin_per_cell` and `perspective_remove_pixel_per_cell` (in
`ArucoTagDetector`, `build_detector`, `AprilTagProfileConfig`) as well as the CLI options
`--error-correction-rate`, `--perspective-remove-margin`, `--perspective-remove-ppc` have
been **removed**. Instead there is a generic mapping `detector_parameters` (OpenCV
`DetectorParameters` attribute name → value); the preset `cam_ceiling` is empty (OpenCV
default). The regression test on the old raw image passes the values explicitly.
Interfaces: [`kamera-controls-und-detektor-parameter.md`](kamera-controls-und-detektor-parameter.md).
