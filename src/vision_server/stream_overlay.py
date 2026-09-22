"""Overlay for the camera livestream: what the server marks in the image.

The stream is a debugging tool. It should show not just that an image
arrives, but what detection sees in it -- detected tags with axis cross and
outline, or the found board corners in calibration mode.

Two rules keep this sustainable in production:

1. **Never modify the shared frame.** `SharedCamera.latest_frame` belongs to
   all readers; drawing happens on a copy here.
2. **Don't re-detect on every frame.** Between runs (`overlay_interval_s`),
   the last result is redrawn. Invisible for a fixed-mounted camera, but not
   to the Pi's CPU.

Runs entirely in the publisher's worker thread -- `annotate` is never called
directly on the event loop.
"""

import logging
import time
from typing import Any

from .profiles import AprilTagProfileConfig

_log = logging.getLogger(__name__)


class AprilTagStreamAnnotator:
    """Draw detection results into a stream frame.

    Receives detector, calibration and tag map from outside -- typically the
    already-loaded objects from `AprilTagDetectionSource`, so stream and job
    are guaranteed to use the same calibration. If the stream shows
    something different from the job result, it's not because of two
    configurations.
    """

    def __init__(
        self,
        config: AprilTagProfileConfig,
        *,
        detector: Any,
        calibration: Any,
        tag_map: Any,
        interval_s: float = 0.5,
    ) -> None:
        self._config = config
        self._detector = detector
        self._calibration = calibration
        self._tag_map = tag_map
        self._interval_s = interval_s
        self._last_run = 0.0
        self._last_tag_poses: list = []
        self._last_board = None
        #: Scaled copies of exactly this calibration, per image size.
        self._scaled: dict[tuple[int, int], Any] = {}
        self._state = (calibration, self._scaled)

    def set_calibration(self, calibration: Any) -> None:
        """Use a new calibration from now on, e.g. after a remote recalibration.

        Calibration and its scale cache are replaced as one tuple: the
        publisher thread sees either the old pair or the new one, never a
        scaled copy of the old calibration filed under the new one.
        """
        self._calibration = calibration
        self._scaled = {}
        self._state = (calibration, self._scaled)

    def _calibration_for(self, size: tuple[int, int]):
        """Return calibration matching the stream resolution.

        The stream usually runs smaller than the job (1280x720 vs 2028x1520).
        Without scaling, the axis cross would visibly sit off -- and the
        calibration would look suspect instead of the scale.
        """
        from tagloc.calibration import scale_to_resolution

        calibration, scaled = self._state
        if tuple(calibration.image_size) == tuple(size):
            return calibration
        cached = scaled.get(size)
        if cached is None:
            cached = scale_to_resolution(calibration, size)
            scaled[size] = cached
        return cached

    def _board_spec(self):
        """Return board geometry from the calibration file, else the default."""
        from tagloc.boards import BoardSpec

        board = dict(getattr(self._calibration, "board", {}) or {})
        if not board:
            return BoardSpec()
        return BoardSpec(
            type=str(board.get("type", "chessboard")),
            cols=int(board.get("cols", 9)),
            rows=int(board.get("rows", 6)),
            square_size_m=float(board.get("squareSizeM", 0.030)),
            marker_size_m=float(board.get("markerSizeM", 0.022)),
            dictionary=str(board.get("dictionary", "DICT_4X4_50")),
        )

    def annotate(self, image: Any, mode: str) -> Any:
        """Return an annotated copy. Never lets an exception escape.

        An overlay error must not stop the livestream -- the stream matters
        more than the markup. So it's logged and the unchanged image is
        returned instead.
        """
        try:
            from tagloc import frames as frame_tools
            from tagloc.overlay import (
                draw_board_overlay,
                draw_status_bar,
                draw_tag_overlay,
                summarise,
            )

            canvas = image.copy()
            size = frame_tools.image_size(canvas)
            calibration = self._calibration_for(size)
            now = time.monotonic()
            due = now - self._last_run >= self._interval_s

            if mode == "calibration":
                if due:
                    from tagloc.boards import detect_board

                    self._last_board = detect_board(
                        frame_tools.to_gray(canvas), self._board_spec()
                    )
                    self._last_run = now
                draw_board_overlay(canvas, self._last_board)
                draw_status_bar(
                    canvas,
                    [
                        f"Modus: Kalibrierung   Board: {self._board_spec().type}",
                        f"Kalibrierung: {self._calibration.calibration_id or 'unbenannt'}",
                    ],
                )
                return canvas

            if due:
                from tagloc.pose import estimate_tag_poses

                self._last_tag_poses = estimate_tag_poses(
                    self._detector.detect(frame_tools.to_gray(canvas)),
                    calibration,
                    tag_map=self._tag_map,
                    default_size_m=self._config.tag_size_m,
                    max_reprojection_error_px=self._config.max_reproj_error_px,
                )
                self._last_run = now
            draw_tag_overlay(
                canvas,
                self._last_tag_poses,
                calibration,
                tag_map=self._tag_map,
                default_size_m=self._config.tag_size_m,
            )
            draw_status_bar(
                canvas,
                [
                    f"Modus: AprilTag   Familie {self._config.tag_family}",
                    summarise(self._last_tag_poses, self._tag_map),
                ],
            )
            return canvas
        except Exception:
            _log.exception("Overlay fehlgeschlagen, sende unmarkiertes Bild")
            return image


__all__ = ["AprilTagStreamAnnotator"]
