"""Overlay for the camera livestream: what the server marks in the image.

The stream is a debugging tool. It should show not just that an image arrives,
but what detection sees in it: detected tags as a green box with the tag ID
(no axis cross, no status bar).

Two rules keep this sustainable in production:

1. **Never modify the shared frame.** `SharedCamera.latest_frame` belongs to
   all readers; drawing happens on a copy here.
2. **Don't re-detect on every frame.** Between runs, the last result is
   redrawn. Invisible for a fixed-mounted camera, but not to the Pi's CPU.

Since 21.10.2026, mode "apriltag" also works as follows. The tags come from a
detection at **full** camera resolution, yet only the small image is streamed.
The overlay does not detect at the stream tick. Doing so cost a whole core on
the Pi and let jobs run into the timeout (py-spy, 24.09.2026). Instead, it draws
the newest result that a job, the cell watch or a measurement has already
computed anyway (`AprilTagDetectionSource.latest_tags`). Only if that result is
too old does it detect by itself, rarely, in the background, and never during a
job. See `doc/project_docs/work-plans/livestream-tags-full-resolution.md`.

Runs entirely in the publisher's worker thread -- `annotate` is never called
directly on the event loop.
"""

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from typing import Any

from .profiles import AprilTagProfileConfig

_log = logging.getLogger(__name__)


class AprilTagStreamAnnotator:
    """Draw detection results into a stream frame.

    Receives detector, calibration and tag map from outside. These are typically
    the already-loaded objects from `AprilTagDetectionSource`, so stream and job
    are guaranteed to use the same calibration. If the stream shows something
    different from the job result, the cause is not two different configurations.
    """

    def __init__(
        self,
        config: AprilTagProfileConfig,
        *,
        detector: Any,
        calibration: Any,
        tag_map: Any,
        detection_max_width: int | None = None,
        full_resolution_tags: Callable[[], Any] | None = None,
        detect_interval_s: float = 5.0,
    ) -> None:
        self._config = config
        self._detector = detector
        self._calibration = calibration
        self._tag_map = tag_map
        #: The drawing canvas is scaled down to this width (typically the same as
        #: `CameraStreamConfig.max_stream_width`). `None` leaves the original
        #: frame size unchanged.
        self._detection_max_width = detection_max_width
        #: Returns the newest `TagSnapshot` of the source (job, cell watch,
        #: measurement), or `None`.
        self._full_resolution_tags = full_resolution_tags
        #: If the newest result is older, the overlay detects by itself at full
        #: resolution. `<= 0`: never detect by itself.
        self._detect_interval_s = detect_interval_s
        #: While this returns `True` (job, camera calibration), no own detection
        #: starts -- the job should have the CPU to itself.
        self._busy: Callable[[], bool] | None = None
        #: While this returns `True` (MeasureTags running, hand-eye session open),
        #: "apriltag" draws nothing and returns the raw image. The measurement
        #: should have the Pi's CPU to itself, and the stream does not stall.
        self._overlay_paused: Callable[[], bool] | None = None
        #: Result of the own detection, `TagSnapshot` or `None`.
        self._own_tags: Any = None
        #: Own single-thread pool: `annotate()` never waits for the detection
        #: (about 1 s on the Pi at 12 MP), so the stream does not stall.
        self._executor: ThreadPoolExecutor | None = None
        self._pending: Future | None = None
        #: Time of the last own attempt, including a failed one. Otherwise an
        #: error would be retried on every tick.
        self._last_own_attempt = float("-inf")
        self._scaled: dict[tuple[int, int], Any] = {}
        #: Set by the calibration methods (`runner/calibration.py`) and reset to
        #: `None` -- see `set_calibration_session`. Only used for
        #: `calibration_progress`; the overlay draws nothing for it.
        self._calibration_session: Any = None
        #: Undistortion maps per stream resolution for mode "undistorted".
        #: `initUndistortRectifyMap` is expensive, `remap` with ready-made maps is not.
        self._undistort_maps: dict[tuple[int, int], Any] = {}
        #: Reference preview (mode "tagreference"). All three fields are immutable
        #: objects (`tagreference_preview`) that the event loop sets and the overlay
        #: thread reads -- a plain attribute assignment is enough.
        self._preview: Any = None
        self._view: Any = None
        #: Last computed status for `TagReferencePreviewStatus`. The publisher
        #: writes it when the object has changed.
        self.reference_preview_status: dict | None = None
        #: During `MeasureTagReference` the preview does not evaluate by itself.
        #: The measurement supplies its debug data (`show_reference_debug`).
        self._reference_measuring = False
        self._preview_pending: Future | None = None
        self._last_preview_start = float("-inf")
        self._last_preview_error: str | None = None
        #: `(View, output size, scaled debug data, corners)` of the last drawing.
        self._scaled_cache: tuple | None = None

    def apply_calibration(self, calibration: Any) -> None:
        """Replace the calibration that the overlay uses for the axis cross.

        Counterpart to `AprilTagDetectionSource.apply_calibration`. After a fresh
        interactive calibration, job and livestream then show the same source
        again. `_scaled` must be cleared in the process. Otherwise
        `_calibration_for` keeps using the old, now wrong, result of
        `scale_to_resolution`.
        """
        self._calibration = calibration
        self._scaled = {}
        self._undistort_maps = {}

    def set_calibration_session(self, session: Any) -> None:
        """Attach or detach a running `CalibrationSession` (`None` detaches).

        Called by the calibration methods in `runner/calibration.py`. The publisher loop runs
        in its own worker thread, but the assignment itself happens on the event
        loop. A plain attribute assignment is safe enough for this, without a lock.
        """
        self._calibration_session = session

    def set_reference_preview(self, request: Any) -> None:
        """Turn the reference preview on for `request`; `None` turns it off.

        Called on the event loop (OPC UA methods). The evaluation itself runs in
        the overlay thread, and only in mode "tagreference".
        """
        from .tagreference_preview import PREVIEW_TTL_S, ReferencePreview, build_status

        if request is None:
            self._preview = None
            self._view = None
            self.reference_preview_status = build_status(
                active=False, tag_id=None, tag_seen=False
            )
            return
        self._preview = ReferencePreview(request, time.monotonic() + PREVIEW_TTL_S)
        # New request (other tag, other layers): the old image no longer applies,
        # and the next evaluation may start immediately.
        self._view = None
        self._last_preview_start = float("-inf")
        self.reference_preview_status = build_status(
            active=True, tag_id=request.tag_id, tag_seen=False, layers=request.overlay
        )

    def expire_reference_preview(self, now: float | None = None) -> None:
        """Turn off an expired preview, also outside mode "tagreference"."""
        preview = self._preview
        if preview is not None and (time.monotonic() if now is None else now) > preview.expires_at:
            self.set_reference_preview(None)

    def _status_for_current_preview(self) -> None:
        """Set the status to match the current preview (after a race with the evaluator)."""
        from .tagreference_preview import build_status

        preview = self._preview
        if preview is None:
            self.reference_preview_status = build_status(active=False, tag_id=None, tag_seen=False)
        else:
            self.reference_preview_status = build_status(
                active=True,
                tag_id=preview.request.tag_id,
                tag_seen=False,
                layers=preview.request.overlay,
            )

    def set_reference_measuring(self, measuring: bool) -> None:
        """Record whether `MeasureTagReference` is running (True) or finished (False)."""
        self._reference_measuring = bool(measuring)

    def show_reference_debug(
        self, debug, corners_px, image_size, title: str, layers: dict
    ) -> None:
        """Show the debug data of a running measurement (called from the measurement thread)."""
        from .tagreference_preview import MEASURE_HOLD_S, ReferenceView

        self._view = ReferenceView(
            debug=tuple(debug),
            corners_px=corners_px,
            image_size=tuple(image_size),
            title=title,
            layers=dict(layers),
            taken_at=time.monotonic(),
            hold_s=MEASURE_HOLD_S,
        )

    def set_busy(self, busy: Callable[[], bool] | None) -> None:
        """Attach the busy signal (`runner.py`: job or calibration).

        It is set up only after the annotator is built, so it is not in the constructor.
        """
        self._busy = busy

    @property
    def calibration_progress(self) -> dict | None:
        """Progress of the active session, or `None` if there is none."""
        session = self._calibration_session
        return session.progress if session is not None else None

    def _calibration_for(self, size: tuple[int, int]):
        """Return calibration matching the stream resolution.

        The stream usually runs smaller than the job (1280x720 vs 2028x1520).
        Without scaling, the axis cross would visibly sit off -- and the
        calibration would look suspect instead of the scale.
        """
        from tagloc.calibration import scale_to_resolution

        if tuple(self._calibration.image_size) == tuple(size):
            return self._calibration
        cached = self._scaled.get(size)
        if cached is None:
            cached = scale_to_resolution(self._calibration, size)
            self._scaled[size] = cached
        return cached

    def set_overlay_paused(self, paused: Callable[[], bool] | None) -> None:
        """Attach the signal that pauses the tag overlay (`runner.py`)."""
        self._overlay_paused = paused

    def annotate(self, image: Any, mode: str) -> Any:
        """Return an annotated copy. Never lets an exception escape.

        An overlay error must not stop the livestream -- the stream matters
        more than the markup. So it's logged and the unchanged image is
        returned instead.
        """
        try:
            from tagloc import frames as frame_tools
            from tagloc.overlay import draw_tag_boxes

            if mode == "undistorted":
                return self._undistorted(image)

            if mode == "apriltag" and self._overlay_paused is not None and self._overlay_paused():
                return image  # raw image, without detection and without drawing

            now = time.monotonic()

            if mode == "tagreference":
                return self._tag_reference_view(image, now)

            # Drawing happens on the small image, but the tags come from a
            # detection at full resolution (see module docstring). Until
            # 21.10.2026 the overlay detected on this small canvas itself --
            # cheap, but no evidence of what the job finds.
            if self._detection_max_width is not None:
                from .camera_stream import _resize_for_stream

                canvas = _resize_for_stream(image, self._detection_max_width)
            else:
                canvas = image.copy()
            output_size = frame_tools.image_size(canvas)

            snapshot = self._newest_tags()
            if self._own_detection_due(snapshot, now):
                self._start_own_detection(image, now)

            tag_poses = (
                _scale_corners(snapshot.tag_poses, snapshot.image_size, output_size)
                if snapshot is not None
                else []
            )
            draw_tag_boxes(canvas, tag_poses)
            return canvas
        except Exception:
            _log.exception("Overlay fehlgeschlagen, sende unmarkiertes Bild")
            return image

    def _tag_reference_view(self, image: Any, now: float) -> Any:
        """Mode "tagreference": edge search of the reference measurement, live in the stream.

        Draws the last evaluation (see `_evaluate_reference`) onto the reduced
        canvas, with coordinates converted from the source image. The evaluation
        itself starts at most 3 times per second in the background. This call
        never waits for it.
        """
        from tagloc import frames as frame_tools
        from tagloc.overlay import draw_tag_boxes
        from tagloc.reference_overlay import draw_reference_debug

        if self._detection_max_width is not None:
            from .camera_stream import _resize_for_stream

            canvas = _resize_for_stream(image, self._detection_max_width)
        else:
            canvas = image.copy()
        output_size = frame_tools.image_size(canvas)

        self.expire_reference_preview(now)
        preview = self._preview
        if preview is not None and self._reference_preview_due(now):
            self._start_reference_preview(image, preview, now)

        # Tag boxes of the other tags: as in mode "apriltag", from the newest
        # result. Own detection only if no preview evaluation is running.
        snapshot = self._newest_tags()
        evaluating = self._preview_pending is not None and not self._preview_pending.done()
        if not evaluating and self._own_detection_due(snapshot, now):
            self._start_own_detection(image, now)

        view = self._view
        # A running evaluation keeps the old image: no flicker during a slow computation.
        if view is not None and (now - view.taken_at <= view.hold_s or evaluating):
            debug, corners = self._scaled_view(view, output_size)
            if corners is None and snapshot is not None:
                draw_tag_boxes(
                    canvas, _scale_corners(snapshot.tag_poses, snapshot.image_size, output_size)
                )
            return draw_reference_debug(
                canvas, debug, tag_corners_px=corners, title=view.title, layers=view.layers
            )

        if snapshot is not None:
            draw_tag_boxes(canvas, _scale_corners(snapshot.tag_poses, snapshot.image_size, output_size))
        title = (
            f"Tag {preview.request.tag_id} - Vorschau startet"
            if preview is not None
            else "keine Einmess-Vorschau"
        )
        return draw_reference_debug(canvas, [], title=title)

    def _scaled_view(self, view: Any, output_size: tuple) -> tuple:
        """Return ``(debug, corners)`` of the view at stream size.

        Computed only once per (view, size) pair.
        """
        import numpy as np
        from tagloc.reference_overlay import scale_debug

        cached = self._scaled_cache
        if cached is not None and cached[0] is view and cached[1] == tuple(output_size):
            return cached[2], cached[3]
        sx = output_size[0] / view.image_size[0]
        sy = output_size[1] / view.image_size[1]
        corners = None
        if view.corners_px is not None:
            corners = np.asarray(view.corners_px, dtype=np.float64) * [sx, sy]
        debug = scale_debug(view.debug, sx, sy)
        self._scaled_cache = (view, tuple(output_size), debug, corners)
        return debug, corners

    def _reference_preview_due(self, now: float) -> bool:
        """Return True if a new preview evaluation may start now."""
        from .tagreference_preview import PREVIEW_INTERVAL_S

        if self._detector is None or self._reference_measuring:
            return False
        if self._preview_pending is not None and not self._preview_pending.done():
            return False
        if now - self._last_preview_start < PREVIEW_INTERVAL_S:
            return False
        return not (self._busy is not None and self._busy())

    def _start_reference_preview(self, image: Any, preview: Any, now: float) -> None:
        """Start a preview evaluation in the background.

        It uses the same single-thread pool as the own detection. The detector is
        not built for two threads, so both run one after the other.
        """
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="stream-overlay-detect"
            )
        self._last_preview_start = now
        self._preview_pending = self._executor.submit(self._evaluate_reference, image, preview)

    def _evaluate_reference(self, image: Any, preview: Any) -> None:
        """Detect the tag and measure its edges (with debug data). Runs in the overlay thread."""
        try:
            from tagloc import frames as frame_tools
            from tagloc.pose import estimate_tag_poses
            from tagloc.reference_edges import measure_reference_lines

            from .measuretag import SizeLookup
            from .tagreference_preview import PREVIEW_HOLD_S, ReferenceView, build_status

            request = preview.request
            size = frame_tools.image_size(image)
            calibration = self._calibration_for(size)
            gray = frame_tools.to_gray(image)
            sizes = SizeLookup(
                {request.tag_id: request.tag_size_m} if request.tag_size_m else {},
                self._tag_map,
                self._config.tag_size_m,
            )
            tag_poses = estimate_tag_poses(
                self._detector.detect(gray),
                calibration,
                tag_map=sizes,
                default_size_m=self._config.tag_size_m,
                max_reprojection_error_px=self._config.max_reproj_error_px,
            )
            tag = next((item for item in tag_poses if item.tag_id == request.tag_id), None)
            debug: list = []
            corners = None
            if tag is not None:
                measure_reference_lines(
                    gray,
                    calibration,
                    tag.pose_cam_tag,
                    request.pose_module_tag_guess,
                    request.lines,
                    tag_size_m=sizes.size_for(request.tag_id),
                    search_band_m=request.search_band_m,
                    debug=debug,
                )
                if tag.observation is not None:
                    corners = [list(corner) for corner in tag.observation.corners]
            if self._preview is not preview:
                return  # meanwhile switched off or to another preview
            title = (
                f"Tag {request.tag_id} - Vorschau"
                if tag is not None
                else f"Tag {request.tag_id} nicht im Bild"
            )
            self._view = ReferenceView(
                debug=tuple(debug),
                corners_px=corners,
                image_size=tuple(size),
                title=title,
                layers=request.overlay,
                taken_at=time.monotonic(),
                hold_s=PREVIEW_HOLD_S,
            )
            self.reference_preview_status = build_status(
                active=True,
                tag_id=request.tag_id,
                tag_seen=tag is not None,
                debug=debug,
                layers=request.overlay,
            )
            if self._preview is not preview:
                # Race: the event loop switched the preview during the assignment.
                self._view = None
                self._status_for_current_preview()
            self._last_preview_error = None
        except Exception as error:
            # At 3 Hz, do not log the whole stack trace every time.
            if str(error) != self._last_preview_error:
                self._last_preview_error = str(error)
                _log.exception("Einmess-Vorschau fehlgeschlagen")

    def _newest_tags(self):
        """Return the newer of the source result and the own detection, or `None`."""
        candidates = [self._own_tags]
        if self._full_resolution_tags is not None:
            candidates.append(self._full_resolution_tags())
        candidates = [snapshot for snapshot in candidates if snapshot is not None]
        return max(candidates, key=lambda snapshot: snapshot.taken_at, default=None)

    def _own_detection_due(self, snapshot, now: float) -> bool:
        """Detect by itself only if nobody else has a fresh result."""
        if self._detect_interval_s <= 0 or self._detector is None:
            return False
        if self._pending is not None and not self._pending.done():
            return False
        if now - self._last_own_attempt < self._detect_interval_s:
            return False
        if snapshot is not None and now - snapshot.taken_at < self._detect_interval_s:
            return False
        return not (self._busy is not None and self._busy())

    def _start_own_detection(self, image: Any, now: float) -> None:
        """Start a full-resolution detection in the background.

        `image` is the shared frame. `to_gray` creates a new array, and the frame
        itself is not modified.
        """
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="stream-overlay-detect"
            )
        self._last_own_attempt = now
        self._pending = self._executor.submit(self._detect_full_resolution, image)

    def _detect_full_resolution(self, image: Any) -> None:
        """Run the own detection on the full frame. Runs in the overlay thread."""
        try:
            from tagloc import frames as frame_tools
            from tagloc.pose import estimate_tag_poses

            from .detection.apriltag import TagSnapshot

            size = frame_tools.image_size(image)
            tag_poses = estimate_tag_poses(
                self._detector.detect(frame_tools.to_gray(image)),
                self._calibration_for(size),
                tag_map=self._tag_map,
                default_size_m=self._config.tag_size_m,
                max_reprojection_error_px=self._config.max_reproj_error_px,
            )
            self._own_tags = TagSnapshot(
                tag_poses=tuple(tag_poses),
                image_size=tuple(size),
                origin="overlay",
                taken_at=time.monotonic(),
            )
        except Exception:
            _log.exception("Overlay-Erkennung in voller Aufloesung fehlgeschlagen")

    def close(self) -> None:
        """Release the detection thread. A running detection ends on its own."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _undistorted(self, image: Any) -> Any:
        """Return the image undistorted with the active calibration, without markings.

        First scale down to stream width, then undistort. `remap` on the small
        image costs only a fraction, and the maps match the scaled calibration.
        Without a real calibration (placeholder) there is nothing to undistort.
        Then the raw image is returned with a hint, so that nobody mistakes an
        uncorrected image for a corrected one.
        """
        import cv2

        from tagloc import frames as frame_tools
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID
        from tagloc.lens import undistortion_maps
        from tagloc.modes import NO_CALIBRATION_HINT
        from tagloc.overlay import draw_status_bar

        if self._detection_max_width is not None:
            from .camera_stream import _resize_for_stream

            canvas = _resize_for_stream(image, self._detection_max_width)
        else:
            canvas = image
        if self._calibration.calibration_id == PLACEHOLDER_CALIBRATION_ID:
            return draw_status_bar(canvas.copy(), [NO_CALIBRATION_HINT])
        size = frame_tools.image_size(canvas)
        maps = self._undistort_maps.get(size)
        if maps is None:
            maps = undistortion_maps(self._calibration_for(size), alpha=1.0)
            self._undistort_maps[size] = maps
        # `remap` writes to a new image, so the shared frame stays untouched.
        return cv2.remap(canvas, maps[0], maps[1], cv2.INTER_LINEAR)


def _scale_corners(tag_poses, source_size, target_size) -> list:
    """Scale the pixel corners from `source_size` to `target_size`.

    Only the corners are scaled. `draw_tag_boxes` needs nothing more, and the
    metric pose stays unchanged.
    """
    if tuple(source_size) == tuple(target_size):
        return list(tag_poses)
    sx = target_size[0] / source_size[0]
    sy = target_size[1] / source_size[1]
    return [
        replace(
            pose,
            observation=replace(
                pose.observation,
                corners=tuple((x * sx, y * sy) for x, y in pose.observation.corners),
            ),
        )
        if pose.observation is not None
        else pose
        for pose in tag_poses
    ]


__all__ = ["AprilTagStreamAnnotator"]
