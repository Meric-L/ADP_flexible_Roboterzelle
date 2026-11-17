# Live stream overlay: only green box and tag ID

**Status:** done — 21.10.2026 (tests green, not checked on the real Pi stream)
**Owner:** Meric, `Agent: Slim down stream overlay`
**Topic:** frontend
**Branch:** apriltag-overlay-schlank

## Goal

In the live stream (overlay mode `apriltag`, as the frontend displays it) only a **green box**
(tag outline) and the **tag ID** are drawn for each detected tag. Dropped: orange coloring and
the lettering „MEHRDEUTIG“ (AMBIGUOUS), crosshair, corner point, module name, distance,
reprojection error, and the status bar at the bottom („Modus: AprilTag  Familie …“ (mode:
AprilTag family …) and the list of tags in the image).

## Already read

- `CLAUDE.md`
- `doc/projektdoku/arbeitsplaene/README.md`
- `doc/projektdoku/vision-server-interface.md` (table of overlay modes)
- `doc/projektdoku/apriltag-referenz.md` (section `tagloc.overlay`)

## Affected files

- `src/tagloc/overlay.py` — new function `draw_tag_boxes`
- `src/vision_server/stream_overlay.py` — mode `apriltag` uses `draw_tag_boxes`, no status bar
- `tests/test_stream_overlay.py`, `tests/test_overlay_scaling.py`
- `doc/projektdoku/vision-server-interface.md`, `doc/projektdoku/apriltag-referenz.md`

## Interfaces

### OPC UA

No change. The overlay modes (`apriltag`, `calibration`, `undistorted`, `off`) and their
display names remain the same; only the image in mode `apriltag` looks different.

### Python

```python
# Module: src/tagloc/overlay.py
def draw_tag_boxes(image, tag_poses: Sequence[TagPose]) -> Any:
    """Draws per tag the outline in COLOR_OK (green) and above it "#<id>".

    Changes `image` in place and returns it. Ambiguous poses are not drawn
    differently. No crosshair, no corner point, no distance, no reprojection
    error. Line thickness and font size scale with the image width as before
    (`_scale_for`). Poses without `observation` are skipped. Raises nothing
    of its own.
    """
```

`draw_tag_overlay`, `draw_status_bar` and `summarise` remain unchanged — the CLI
(`python -m tagloc.cli.detect --overlay`) still shows the detailed debug overlay.

`AprilTagStreamAnnotator.annotate(image, "apriltag")`: calls `draw_tag_boxes` instead of
`draw_tag_overlay` and **no longer** `draw_status_bar`. The modes `calibration` and
`undistorted` remain unchanged (the status bar stays there; it carries the calibration progress).

### Data / payload

No change. Ambiguity is still reported in the job results (`ambiguous`, `ambiguityRatio`,
`confidence`) — only no longer in the stream image.

### Network / operations

No change.

### Failure cases

As before: an error while drawing is logged, and the stream sends the unmarked image.

## Open questions

- None.

## Implementation

Implemented as planned; interface unchanged from the above.

- `draw_tag_boxes`: outline via `cv2.polylines` in `COLOR_OK`; label `#<id>` at the left edge
  of the corner bounding box, just above its upper edge (`max(4, round(6 * scale))` px),
  limited upwards to the text height so that it does not fall out of the image.
- `stream_overlay.py`: the pose estimation in mode `apriltag` runs as before (its result
  provides the corners for the boxes); only the drawing is reduced.
- Tests: `tests/test_stream_overlay.py` (stream calls `draw_tag_boxes`, no `draw_status_bar`),
  `tests/test_overlay_scaling.py` (ambiguous pose is drawn green, pose without observation is
  skipped). Whole suite: 691 tests OK (`PYTHONPATH=src python -m unittest discover -s tests -t .`).
- Docs adjusted: `vision-server-interface.md`, `apriltag-referenz.md`,
  note in `praesentation.md`.

## Addendum 21.10.2026: merge with live-stream tags at full resolution

Merged with [`livestream-tags-volle-aufloesung.md`](livestream-tags-volle-aufloesung.md).
Since then the overlay no longer estimates on the small canvas itself. It draws the latest
result at full resolution (`latest_tags`), with corners rescaled to stream size, via
`draw_tag_boxes`. The interface above remains valid.

## Addendum 21.10.2026: thinner frame, tag ID with black border

- The green frame in `draw_tag_boxes` is exactly 2 px wide (`_TAG_BOX_PX`), regardless of the
  image width. It is drawn as a 1-px line, offset 2 × 2 times by 1 px each. Via `thickness` this
  does not work: OpenCV rasterizes a line thickness t as 2t − 1 pixels, so 2 becomes 3 px.
  Previously at 960 px it was about 5 px (thickness 3). A first attempt with 1 px was too thin.
- The tag ID has an exactly 1-px-wide black border. The text is drawn in black, offset by 1 px
  in all eight directions, then green on top. A thicker line for the outline does not work
  reliably: OpenCV 5 (PC) ignores the line thickness for `putText`, OpenCV 4.x (Pi) does not.
- The interface remains unchanged.
