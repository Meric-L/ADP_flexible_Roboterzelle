# AprilTag pose estimation: run more efficiently

**Status:** done — 23.09.2026
**Owner:** Agent: AprilTag optimization
**Topic:** apriltag
**Branch:** apriltag-optimierung

## Goal

When several tags are in the same image, shared camera data and tag geometries
are prepared only once. The live stream draws the markers only onto the smaller
output resolution, but still detects the tags on the original image. The
determined poses and quality values stay the same.

## Already read

- `doc/project_docs/topics/apriltag-localization.md`
- `doc/project_docs/topics/apriltag-reference.md`
- `doc/project_docs/work-plans/README.md`

## Affected files

- `src/tagloc/pose.py`
- `src/vision_server/stream_overlay.py`
- `tests/test_tag_pipeline.py`
- `tests/test_stream_overlay.py`
- `doc/project_docs/topics/apriltag-reference.md`

## Interfaces

### Python

`estimate_tag_pose(observation, size_m, calibration) -> TagPose` and `estimate_tag_poses(observations, calibration, *, tag_map=None, default_size_m=0.05, max_reprojection_error_px=None) -> list[TagPose]` keep their signature, return values and error behavior. The shared preparation is internal.

`AprilTagStreamAnnotator.annotate(image, mode) -> image` keeps its signature. If `detection_max_width` is set, the annotated AprilTag image is already brought to this output width here; the detection still receives the unscaled input image.

### Data / payload

No format change. `pose_cam_tag`, reprojection error and ambiguity ratio keep their definition.

### Failure cases

Individual tags that cannot be solved are still skipped and logged; programming errors still abort.

## Approach

1. Measure the existing runtime with reproducible synthetic observations.
2. Prepare shared inputs per image and verify numerical equality.
3. Move drawing to the output resolution and test the coordinate scaling.
4. Repeat the relevant tests and measurement.

## Open questions

- None.

## Deviations from the plan

- In addition to the pose estimation, drawing of the live-stream overlay was moved to the output resolution; detection stays at full resolution.

## Result

- Synthetic measurement on this machine: 24 tags × 300 images, median of five runs: 0.962 s before, 0.880 s after.
- Live-stream path without running detection: 40 frames at 2028×1520 → 640 px, median of three runs: 0.304 s before, 0.243 s after.
- The measurements isolate these paths; they are not a frame-rate measurement on the Raspberry Pi.

## After completion

- Public signatures, payload and error codes unchanged.
- Numerical equality of single and image processing checked with synthetic tags.
- The permanent flow is described in `doc/project_docs/topics/apriltag-reference.md`.
