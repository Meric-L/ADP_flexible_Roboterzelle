"""Fetch images from the shared camera: freshness, calibration matched to the image size, overlay state.

`CaptureMixin` is the part of the AprilTag source that all measurement paths
(job, MeasureTags, MeasureBoard, reference edges, cell watch) share. It expects
the object to provide `camera`, `_config`, `_calibration` and `latest_tags`;
`AprilTagDetectionSource.__init__` sets them.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

from ...errors import VisionErrorCode, VisionJobError
from .snapshot import TagSnapshot

POLL_INTERVAL_S = 0.02


def captured_at(frame) -> datetime:
    """Return the capture time of a camera image in UTC.

    ``frame.timestamp`` is the loop time when the image arrived. It is converted
    to wall-clock time via the image age, so the result is the time of the image,
    not of the call. Call only inside the event loop.
    """
    age_s = asyncio.get_running_loop().time() - frame.timestamp
    return datetime.now(timezone.utc) - timedelta(seconds=age_s)


class CaptureMixin:
    """Wait for fresh images and evaluate them against the calibration."""

    def _latest_timestamp(self):
        """Return the timestamp of the newest image already available, or ``None``.

        Callers that want only images **after** the call pass it as
        ``seen_timestamp`` to ``_next_frame``. Otherwise the first image returned
        would be one already in the buffer before the call, up to one frame
        period old.
        """
        latest = self.camera.latest_frame
        return latest.timestamp if latest is not None else None

    async def _next_frame(self, seen_timestamp: float | None):
        """Wait for an image newer than the last one processed.

        Uses its own timeout, because the runner's job timeout cannot stop a
        hung worker thread (see ``base.run_blocking``).
        """
        deadline = time.monotonic() + self._config.capture_timeout_s
        while time.monotonic() < deadline:
            frame = self.camera.latest_frame
            if frame is not None and frame.timestamp != seen_timestamp:
                return frame
            await asyncio.sleep(POLL_INTERVAL_S)
        raise VisionJobError(
            VisionErrorCode.DETECTION_FAILED,
            f"Kein Kamerabild innerhalb von {self._config.capture_timeout_s:.1f} s",
        )

    def _calibration_for(self, image):
        """Return the calibration matching the resolution of this image."""
        from tagloc import frames as frame_tools
        from tagloc.calibration import check_resolution, scale_to_resolution

        size = frame_tools.image_size(image)
        calibration = self._calibration
        if tuple(calibration.image_size) != tuple(size):
            if not self._config.allow_resolution_mismatch:
                check_resolution(calibration, size)
            calibration = scale_to_resolution(calibration, size)
        return calibration

    def _remember_tags(self, tag_poses, image, origin: str) -> None:
        """Store `tag_poses` as the latest result for the live stream overlay."""
        from tagloc import frames as frame_tools

        self.latest_tags = TagSnapshot(
            tag_poses=tuple(tag_poses),
            image_size=tuple(frame_tools.image_size(image)),
            origin=origin,
            taken_at=time.monotonic(),
        )
