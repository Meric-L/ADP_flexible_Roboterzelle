# Ceiling camera (Pi 1) at full sensor resolution, smooth live stream

**Status:** in progress — 22.09.2026
**Owner:** Meric
**Topic:** vision
**Branch:** apriltag-optimierung

## Goal

Pi 1 (`ADP-Roboter-Lokalisierung`, `cam_ceiling`, Raspberry Pi HQ Camera,
sensor IMX477) captures at the full sensor resolution **4056×3040 (12.3 MP)**
instead of the 2×2-binned mode 2028×1520. The AprilTag jobs thereby get the
full resolution — small tags at ceiling height are found more reliably and
measured more accurately.

The live stream stays smooth, **with both overlay modes** ("Rohbild" (raw
image) and "AprilTags markieren" (mark AprilTags)). Already today "AprilTags
markieren" stutters at 3 MP, because the overlay copies every image at full
resolution, searches it for tags every 0.5 s and only downscales afterwards.
At 12 MP this would be four times as expensive.

## Already read

- `CLAUDE.md`, `doc/arbeitsplaene/README.md` (no ongoing plan on the topic)
- `doc/arbeitsplaene/livestream-mjpeg-und-watchdog.md`
- `doc/vision-server-interface.md` (section 10, 10.2)
- `doc/vision-system.md`

## Solution

Besides the main image (`main`), the Pi camera delivers a second, small image
stream (`lores`) from the same frame. The ISP scales that in hardware —
practically without CPU load. Split:

| Who | Image | Resolution |
| --- | --- | --- |
| AprilTag job, calibration session | `main` | 4056×3040 |
| Live stream (MJPEG and nodes), both overlays | `lores` | 960×720 |

The overlay thereby computes on 0.7 MP instead of 12.3 MP; the calibration is
converted to the stream size via `scale_to_resolution` as before (the aspect
ratio of 960×720 vs. 4056×3040 deviates by 0.07 %, below the 0.1 % threshold).

Further saving: main stream in the format `RGB888`, which Picamera2 stores in
memory as B,G,R — i.e. directly OpenCV BGR. The previous color conversion
(`XBGR8888` → `cv2.cvtColor`) on every 12 MP frame is dropped.

## Affected files

- `src/vision_server/camera.py` — `CameraFrame.preview`, Picamera2 with
  `lores`, `RGB888`, conversion YUV420 → BGR
- `src/vision_server/profiles.py` — new fields in `CameraStreamConfig`
- `src/vision_server/camera_stream.py` — stream takes `preview`, if present
- `src/vision_server/server.py` — preset for `cam_ceiling`
- `tests/test_camera.py`, `tests/test_camera_stream.py`, new test for the
  server configuration
- `doc/vision-server-interface.md`, `doc/vision-system.md`

## Interfaces

### OPC UA

**No change.** All nodes remain as they are. `LatestCameraFrame` and the MJPEG
stream continue to deliver JPEGs at most 960 px wide.

### Python

```python
# Module: src/vision_server/camera.py
@dataclass(frozen=True)
class CameraFrame:
    image: Any            # BGR, full capture resolution -- jobs, calibration
    timestamp: float
    preview: Any = None   # BGR, small stream for the live stream; None without lores
```

`SharedCamera.latest_frame.image` remains the full image for all backends.
`preview` is only filled for Picamera2 with `preview_resolution` set; RealSense
and OpenCV deliver `None`, and the stream then downscales itself as before.

### Configuration (`CameraStreamConfig`)

| Field | Default | Meaning |
| --- | --- | --- |
| `preview_resolution` | `None` | Picamera2: size of the `lores` stream for the live stream. `None` = no second stream |
| `buffer_count` | `None` | Picamera2: number of camera buffers. `None` = Picamera2 default (6). At 12 MP too many for the CMA memory |

Preset `cam_ceiling` (only backend `picamera2`):

| | before | after |
| --- | --- | --- |
| `resolution` (job and camera) | 2028×1520 | **4056×3040** |
| `preview_resolution` | — | 960×720 |
| `capture_fps` | 15 | **10** (upper limit of the IMX477 in full-resolution mode) |
| `buffer_count` | 6 | 2 |

### Failure cases

- **Calibration for 2028×1520 present:** The 2028×1520 mode is the 2×2 binning
  of the full sensor, i.e. the same field of view. The intrinsics can be
  exactly doubled. For `cam_ceiling`, `allow_resolution_mismatch` is therefore
  set until the recalibration at 4056×3040 is available. **Remove it again
  afterwards.**
- **No calibration:** The placeholder calibration computes itself from the
  resolution and adapts automatically.
- **Camera buffers cannot be allocated** (CMA too small): `open()` fails like any
  camera error, and the source stays closed. Remedy: raise `buffer_count` or
  CMA.
- **Colors swapped** (red/blue): would mean `RGB888` is not present as BGR in
  this Picamera2 version. Detection works on grayscale and is hardly affected;
  the overlay is.

## Approach

### Superseded (not implemented)

Original plan — superseded by the team agreement of 22.09.2026:

1. `CameraFrame.preview`, Picamera2 configuration with `lores`, conversion
2. Stream takes `preview`
3. Preset `cam_ceiling` with `preview_resolution` (960×720)
4. Tests, docs
5. On Pi 1: measure the frame rate (`measure_framerate.py`), check the stream,
   **recalibrate**

### Current implementation

1. **Live-stream overlay optimization:** move the detection in
   `AprilTagStreamAnnotator.annotate()` to the `max_stream_width` resolution
   (960) instead of the full 12 MP resolution (file:
   `src/vision_server/stream_overlay.py`)

2. **Make subpixel corner refinement configurable:**
   - New field `subpixel_corner_refinement: bool = True` in
     `AprilTagProfileConfig` (`src/vision_server/profiles.py`) — see "Deviations
     from the plan" below: the default was briefly `False`, broke Layer 1, and
     was reverted the same day.
   - Adjust the detector logic in `src/tagloc/detector.py` (`ArucoTagDetector`)

3. **Tests and local verification:**
   - Unit tests for the overlay optimization
   - CI tests (Windows, without camera)

4. **Verify on Pi 1:**
   - AprilTag jobs: no more CPU overload, no more 20 s timeouts (DETECTION_FAILED)
   - Live stream "AprilTags markieren": smooth, also at 10 fps and 12 MP
   - Live stream "Rohbild": unchanged

## Open questions

- Is the stream resolution 960×720 sufficient for "AprilTags markieren"? A
  10 cm tag at ceiling height is clearly smaller in it than in the job image. In
  doubt, the overlay does not show a tag that the job still finds — never the
  reverse.
- Which Pi is in Pi 1 (`/proc/device-tree/model`)? A Pi 4 manages 12 MP at
  10 fps in the ISP; a Pi 3 not reliably.
- **Subpixel refinement finally off (2026-09-24):** confirmed live on pi1 that
  Layer 1 finds tags reliably even without `subpixel_corner_refinement` (no
  reprojection-error exclusion, see section below) -- default now `False`,
  saves ~46 % CPU in the job thread. `job_timeout=30.0` and
  `apriltag_quad_decimate` (both `config.py`/`profiles.py`) remain as a safety
  margin resp. tuning levers, but are currently not needed.
- **Open idea, not implemented:** use `subpixel_corner_refinement` specifically
  for more precise localization on a second detection layer or for calibration.

### Subpixel post-processing of the calibration images (not part of the current implementation)

The idea is a subpixel post-processing of the already stored calibration images
(field `calibration_capture_dir` in `AprilTagProfileConfig`). A more precise but
slow corner refinement is to run as a **post-processing step after all
calibration captures are complete**, on the stored images — not live during the
capture, not shown in a stream. **Open design questions:**

1. Should this be triggered via a new OPC UA method, or is an offline CLI script
   sufficient?
2. Does the post-processing replace the stored calibration file directly, or is
   it first compared (RMS could also get worse)?
3. Is a serial runtime of about 4 seconds per image (measured for the accurate
   method `CALIB_CB_ACCURACY` at ~4000 px width, see comment in
   `src/tagloc/boards.py`) across all captures acceptable, or are
   parallelization / a progress indicator needed?

This requirement is **not part of the current implementation** and is kept open
as a follow-up item.

## Deviations from the plan

The original approach (separate Picamera2 `lores` stream 960×720 from the ISP)
was **not implemented**. Instead, the implementation follows a later team
agreement of **22.09.2026** (documented in a comment in `stream_overlay.py` /
`camera_stream.py`): *"The overlay should show exactly the image on which the
job also detects."*

**New solution:** The AprilTag detection for the live-stream overlay is moved
directly into `AprilTagStreamAnnotator.annotate()` (`src/vision_server/stream_overlay.py`)
to the already existing, software-downscaled stream resolution. The field
`max_stream_width` in `CameraStreamConfig` (`src/vision_server/profiles.py`,
currently default 960) determines this resolution. **No new camera stream
needed.**

**Reason:** py-spy measurement on pi1 showed that detection on the full 12 MP
frame in the live-stream overlay eats so much CPU that the actual AprilTag jobs
exceed the 20-second timeout (DETECTION_FAILED). The job detection
(`AprilTagDetectionSource`) and calibration remain unchanged at full resolution
and accuracy.

### Subpixel corner refinement (new configurability)

The new field `AprilTagProfileConfig.subpixel_corner_refinement` (`profiles.py`)
controls `cv2.aruco.CORNER_REFINE_APRILTAG` (`ArucoTagDetector`, `detector.py`)
— it remains in the code and can only be switched off. `runner._build_annotator`
builds its own detector with the refinement hard-wired for the live-stream
overlay; since then, job and stream no longer share a detector instance (this
prevents mutual CPU competition when `detect()` runs concurrently).

**Result after several live tests on pi1:** default now `False` — Layer 1 finds
tags reliably even without the refinement (no reprojection-error exclusion in
`estimate_tag_poses`), saving ~46 % CPU in the job thread. Constructor defaults
of `ArucoTagDetector`/`build_detector` remain conservatively `False`,
independently of this. Open idea (not implemented): use the refinement
specifically for a second detection layer or for calibration.

### Original implementation details (superseded)

The following points describe the original approach and were not implemented:

- `picamera2_video_configuration(config)` with `lores` stream
- Preset `cam_ceiling` with `preview_resolution` (960×720)
- ISP-side scaling for the live stream

**Current architecture (corrected):** The `lores` stream still exists and is
active for `cam_ceiling` (`PI_CAMERA_STREAM_PRESETS["cam_ceiling"]["preview_resolution"]
= (960, 720)` in `src/vision_server/server.py`) — but it is only used by the
`"calibration"` mode of the live stream (`camera_stream.py._encoded` selected
`frame.preview` only there; the mode was removed on 17.11.2026, so now no mode reads
the preview). The modes `"apriltag"`/`"off"` still start from the
full `frame.image`; `"apriltag"` has since then itself downscaled in software in
`annotate()` (see above).

## After completion

- Status set to `done`, interfaces as implemented
- Entry in the index `doc/arbeitsplaene/README.md` updated
- Domain knowledge in `doc/vision-server-interface.md` and `doc/vision-system.md`

## See also

- [`apriltag-erkennung-unscharfe-tags.md`](apriltag-erkennung-unscharfe-tags.md):
  detector parameters for the soft 12 MP images of the ceiling camera (tags 0
  and 3 were not found with the OpenCV default values).

## Addendum 10.11.2026: subpixel back on, method `subpix`

The ~46 % CPU above applied to `CORNER_REFINE_APRILTAG`. Since 10.11.2026 the
ceiling camera uses `CORNER_REFINE_SUBPIX`: same detections, +3 % runtime, 5 to
8 times less corner noise. Details are in the work plan
[`deckenkamera-subpixel-ecken.md`](deckenkamera-subpixel-ecken.md).
