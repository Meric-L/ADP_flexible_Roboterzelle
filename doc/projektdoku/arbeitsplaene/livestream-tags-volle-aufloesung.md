# Live stream shows the tags from detection at full resolution

**Status:** done — 21.10.2026 (code and tests; not measured on the Pi, see Open questions)
**Owner:** Meric, `Agent: livestream tags at full resolution`
**Topic:** vision
**Branch:** apriltag-stream-volle-aufloesung (from `apriltag`)

## Goal

In the overlay mode "AprilTags markieren" (mark AprilTags) (`apriltag`) the live stream shows the
tags from a detection at **full camera resolution** (ceiling camera
4056×3040). Only the small image (`max_stream_width`,
960 px) is still streamed. Until now (since `e1c677c`, 24.09.2026) the overlay itself detected on the
downscaled image, with its own detector without corner refinement. The stream was
therefore no evidence of what the job finds.

At the same time the problem of 24.09.2026 must not return. Back then the overlay detected every 0.5 s on
12 MP. According to py-spy this cost about 102 % of a core, and AprilTag jobs ran into the 20 s timeout.

## Already read

- `doc/projektdoku/vision-server-interface.md`, section 10 (live stream, overlay, two image streams)
- `doc/projektdoku/arbeitsplaene/deckenkamera-volle-aufloesung.md` (deviations: overlay detection downscaled, own detector)
- `doc/projektdoku/kamera-und-detektor-analyse.md`, section 2.1 (runtime of `detect()` on 12 MP: PC 0.09–0.13 s, Pi 4 roughly 5–10× slower)
- `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md` (own detector, pause during job/calibration)

## Solution

The overlay **no longer detects at the stream rate**. It draws the latest result at full resolution,
regardless of who computed it:

| Source (`origin`) | When | Additional CPU |
| --- | --- | --- |
| `job` | every image of an AprilTag job (`_locate`) | none, computes anyway |
| `cellwatch` | every image of the cell monitoring (`observe_cell`, every 2 s, when armed) | none |
| `measure` | `MeasureTag`/`MeasureTags`, hand-eye captures | none |
| `overlay` | own detection of the overlay, only if the latest result is older than `overlay_detect_interval_s` | about 1 s compute time per 5 s on the Pi |

The own detection runs
- in its own background thread: `annotate()` does not wait for it, the stream does not stall;
- at most one at a time;
- **not** while a job or a camera calibration is running. This is the same signal with which the cell monitoring pauses;
- with the stream detector: the same parameters as in the job, only without corner refinement. For `cam_ceiling` the refinement is also off in the job, so the hits are then identical.

Drawing takes place on the downscaled image. The corners are converted from the resolution of the result to the
stream size. The axis cross projects the metric pose with the calibration scaled to stream size
(`scale_to_resolution`).

The status line states resolution, source and age, so that nobody takes an old
result for a current one:
`Volle Aufl. 4056x3040, Job vor 3 s` (full res. 4056x3040, job 3 s ago) respectively `Noch keine Erkennung in voller Aufloesung` (no detection at full resolution yet).

## Affected files

- `src/vision_server/detection/apriltag.py`: `TagSnapshot`, `latest_tags`, setting in `_locate`, `observe_cell`, `_measure_tag`, `_detect_in_camera`
- `src/vision_server/stream_overlay.py`: mode `apriltag` new
- `src/vision_server/runner.py`: `_build_annotator` (snapshot source, interval), set the busy signal
- `src/vision_server/profiles.py`: new field `overlay_detect_interval_s`
- `src/vision_server/camera_stream.py`, `src/vision_server/server.py`: only outdated comments
- `tests/test_stream_overlay.py`, `tests/test_apriltag_source.py`
- `doc/projektdoku/vision-server-interface.md`: new section 10.4

## Interfaces

### OPC UA

No change. `CameraStreamMode` keeps the values `off`/`apriltag`/
`calibration`/`undistorted`. `LatestCameraFrame` and MJPEG continue to deliver
JPEGs at most `max_stream_width` wide.

### Python

```python
# Module: src/vision_server/detection/apriltag.py
@dataclass(frozen=True)
class TagSnapshot:
    tag_poses: tuple          # tagloc TagPose, corners in pixels of image_size
    image_size: tuple[int, int]  # (width, height) of the evaluated image
    origin: str               # "job" | "cellwatch" | "measure" | "overlay"
    taken_at: float           # time.monotonic() after the detection

class AprilTagDetectionSource:
    latest_tags: TagSnapshot | None   # latest result at full resolution, None until the first
```

`latest_tags` is only replaced by a simple assignment, never modified.
Reading from another thread without a lock is therefore safe.

```python
# Module: src/vision_server/stream_overlay.py
class AprilTagStreamAnnotator:
    def __init__(self, config, *, detector, calibration, tag_map,
                 interval_s=0.5, detection_max_width=None,
                 full_resolution_tags: Callable[[], TagSnapshot | None] | None = None,
                 detect_interval_s: float = 5.0) -> None: ...
    def set_busy(self, busy: Callable[[], bool] | None) -> None:
        """While busy() is True, the overlay does not start its own detection."""
    def close(self) -> None:
        """Releases the detection thread (CameraStreamPublisher.stop calls it)."""
```

`detect_interval_s <= 0` switches off the own detection. The overlay then shows
only results from the job, cell monitoring and measurements.

### Network / operation

New field in `CameraStreamConfig`:

| Field | Default | Meaning |
| --- | --- | --- |
| `overlay_detect_interval_s` | 5.0 | If the latest result at full resolution is older, the overlay detects itself (background, not during job/calibration). `0` = never detect itself |

`overlay_interval_s` (0.5 s) still applies only to the `calibration` mode.

### Failure cases

- Error in the own detection: log, the last result stays, next attempt after the interval.
- Long job (up to 20 s): the job delivers results itself, the overlay shows them.
- No result yet: the image comes without a marking, with a note in the status line.
- Result from a different resolution (e.g. after a calibration change): the corners are converted using the ratio of the image sizes.

## Approach

1. `TagSnapshot` and `latest_tags` in the source, tests
2. Annotator: draw snapshot, own detection in the background, tests
3. Wire up the runner, config field
4. Docs: section 10.4, plan to `done`

## Open questions

- Measure on the Pi: how long does a `detect()` at 4056×3040 actually take, and how much CPU does the overlay cost at 5 s?
- If the own detection starts right before a job, the two overlap by at most ~1 s. Is that enough, or should the job wait for a running overlay detection?

## Deviations from the plan

- In addition `AprilTagStreamAnnotator.close()`. `CameraStreamPublisher.stop()` calls it and thereby releases the detection thread.
- A failed own attempt counts as an attempt. The next one comes only after `overlay_detect_interval_s`, not in the next tick.
- Interfaces implemented as planned. The topic doc has the new section `vision-server-interface.md` 10.4.

## Measurement (PC, 21.10.2026)

`data/rohbilder/cam_ceiling_20261019-142133.png`, stream detector with the
parameters of `cam_ceiling` (`perspectiveRemovePixelPerCell` 8, without
corner refinement):

| Resolution | `detect()` | IDs found |
| --- | --- | --- |
| 4056×3040 | 153–166 ms | 0, 1, 1, 2, 3, 583, 586 |
| 960×720 | 26–32 ms | 0, 1, 1, 2, 583, 584 |

The old overlay (960) therefore did not show two tags (3, 586) and showed one (584) that full
resolution does not find. The assumption "in case of doubt a tag is missing from the overlay, never the other way round"
did not hold. On the Pi 4 (5–10× slower) one run at full resolution costs about 0.8–1.6 s, i.e. 16–32 % of a core every 5 s.

## Addendum 21.10.2026: merge with the lean overlay

Merged into `apriltag` together with [`stream-overlay-schlank.md`](stream-overlay-schlank.md).
The data source (`latest_tags`, own detection in the background, corner conversion `_scale_corners`) remains as above. Only
`draw_tag_boxes` (green box and `#<id>`) is drawn now. The axis cross and the status line with `Volle Aufl. …` respectively
`Noch keine Erkennung …` are dropped, `_snapshot_line` is removed. Before the first result the image therefore comes
without a marking and without a note.
