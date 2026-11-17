# Ceiling camera: detect tags only in a fixed image region (crop before processing)

**Status:** done (no hardware run) — 17.11.2026
**Owner:** NobbisCode, `Agent: ceiling camera detection region`
**Topic:** vision
**Branch:** apriltag-aufraeumen

## Goal

The ceiling camera (`cam_ceiling`, 4056×3040) sees much more than the cell: walls,
windows, desks, 3D printers at the fisheye edge. Tag detection runs on the whole
12 MP frame anyway. Afterwards the detector only searches a fixed rectangle (the
cell area). This saves CPU time and avoids hits outside the cell.

Region chosen by NobbisCode on the frame of 17.11.2026
(`data/rohbilder/cam_ceiling_20261117-120339.png`, marked as a white box):

| Side | Cut off |
| --- | --- |
| left | 917 px |
| right | 1025 px |
| top | 568 px |
| bottom | 393 px |

What is left: `x = 917`, `y = 568`, `width = 2114`, `height = 2079` (in 4056×3040 pixels).

## Already read

- `CLAUDE.md`, `doc/projektdoku/arbeitsplaene/README.md` (no plan on this topic)
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md`
- `src/tagloc/detector.py`, `src/vision_server/profiles.py`, `src/vision_server/server.py`,
  `src/vision_server/runner/livestream.py`, `src/vision_server/runner/cell_watch.py`,
  `src/vision_server/detection/apriltag/resources.py`

## Affected files

- `src/tagloc/detector.py` — new `RoiTagDetector`, `build_detector(..., roi=...)`
- `src/vision_server/profiles.py` — new field `AprilTagProfileConfig.detection_roi`
- `src/vision_server/server.py` — preset for `cam_ceiling`, override `VISION_DETECTION_ROI`
- `src/vision_server/detection/apriltag/resources.py`, `src/vision_server/runner/livestream.py`,
  `src/vision_server/runner/cell_watch.py` — pass the region to `build_detector`
- `tests/test_detector_roi.py` (new)

## Interfaces

### Design decision: crop inside the detector, coordinates stay full-frame

The frame itself is **not** cropped. Only the grayscale image handed to the
detector is cut out (a numpy view, no copy), and the found corners are shifted
back by the offset. For everyone after the detector — pose estimation,
calibration (`K`, `D`, `image_size`), overlay, tag map, cell watch, saved raw
images — nothing changes: corner coordinates are still in full-frame pixels.
Cropping the frame itself would shift the principal point and invalidate the
calibration (fisheye model).

Not affected: the calibration session and `MeasureBoard` (board detection, own
code path) and saving raw/use-case images — they keep the full frame.

### Python

```python
# Module: src/tagloc/detector.py
class RoiTagDetector:
    def __init__(self, inner: TagDetector, roi: tuple[int, int, int, int],
                 reference_size: tuple[int, int]) -> None:
        """roi = (x, y, width, height) in pixels of reference_size (width, height).
        Raises ValueError if the region is empty or lies outside reference_size."""
    family: str                      # from inner
    changed_parameters: dict         # from inner (if present)
    def detect(self, gray) -> list[TagObservation]:
        """Detect only inside the region. If the image size differs from
        reference_size (e.g. the downscaled overlay image), the region is scaled
        proportionally. Corners are returned in coordinates of `gray`."""

def build_detector(family, backend, *, ..., roi: tuple[int, int, int, int] | None = None,
                   roi_reference_size: tuple[int, int] | None = None) -> TagDetector:
    """roi=None: unchanged behavior. With roi, wraps the detector in RoiTagDetector;
    roi_reference_size is then required (ValueError otherwise)."""
```

```python
# Module: src/vision_server/profiles.py
@dataclass(frozen=True)
class AprilTagProfileConfig:
    #: (x, y, width, height) in pixels of `resolution`; None = whole image.
    detection_roi: tuple[int, int, int, int] | None = None
```

### Network / operations

- Preset `cam_ceiling`: `detection_roi = (917, 568, 2114, 2079)`.
- Environment variable `VISION_DETECTION_ROI` overrides it without a code change:
  `"x,y,width,height"` (e.g. `917,568,2114,2079`) or `off` / `none` / `0` for the whole
  image. Invalid value → `ValueError` at startup.

### Failure cases

- Region outside the configured resolution or empty → `ValueError` at startup
  (`build_detector`), not during a job.
- Tags outside the region are **not** found — including world tags. The world tags
  used for the camera pose must lie inside the region.

## Approach

1. `RoiTagDetector` + `build_detector(roi=...)` + tests.
2. Config field, preset, environment variable.
3. Wiring at the three detector build sites (job source, overlay, cell watch).
4. Check on the real frame from 17.11. that all cell tags are still found.

## Measurement (development PC, 17.11.2026)

Frame `cam_ceiling_20261117-120339.png`, ceiling preset (subpix, `perspectiveRemovePixelPerCell: 8`),
fastest of 5–7 runs. The region has 4.4 MP instead of 12.3 MP (36 %).

| Run | Whole image | Region | Tags found |
| --- | --- | --- | --- |
| OpenCV multithreaded | 267 ms | 226 ms | 0, 1, 2, 3, 583, 584, 585, 586 — identical |
| OpenCV 1 thread | 1055 ms | 680 ms (−35 %) | identical |

The saving is smaller than the pixel ratio: the cut-off edge is mostly dark, low-detail
area, while the expensive per-candidate work (contours, decoding, subpixel corners)
happens inside the cell and stays. A crop as a view or as a copy makes no difference
(231 vs. 218 ms). Still to be measured on Pi 1.

## Open questions

- Are all world tags inside the region? To be checked on the Pi (live overlay / job).

## Deviations from the plan

—

## After completion

- Status set to `done`, actual interfaces entered.
- Line in the index `doc/projektdoku/arbeitsplaene/README.md` updated.
- Section in `doc/projektdoku/vision-system.md`.
