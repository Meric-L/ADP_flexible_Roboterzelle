"""Publish frames of the shared camera as Base64 JPEG in one node.

Transport as agreed with the backend: the backend subscribes to this node in
the usual way through the existing `subscribeNode` infrastructure, so no new
backend logic is needed. This module therefore knows only the node, not the
WebSocket or backend details.

The stream can be annotated: an `annotator` draws detected AprilTags onto the
image, and a second, **writable** node selects the mode
("off", "apriltag", "undistorted", "tagreference"). The publisher itself knows
neither OpenCV nor detection -- it only passes the mode and the image along.
"""

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

from asyncua.common.node import Node

from tagloc.modes import normalise_mode

from .aio import cancel_and_wait
from .camera import SharedCamera
from .profiles import CameraStreamConfig

_log = logging.getLogger(__name__)


def _encode_jpeg_base64(image, quality: int) -> str | None:
    """Run in the worker thread: JPEG encode plus Base64. `None` on encode failure.

    The import is deliberately inside the function instead of at module level, so
    that importing this module (e.g. via `runner/`) does not need `cv2` while no
    stream is running.
    """
    import cv2

    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    return base64.b64encode(buffer).decode("ascii")


def _no_calibration_hint(image):
    """Return the raw image with a hint, for "undistorted" without an annotator.

    Without an annotator no calibration is loaded: the AprilTag source either
    did not come up or is not configured at all. Not an error and not an abort:
    the image is sent out, just visibly undistorted.
    """
    from tagloc.modes import NO_CALIBRATION_HINT
    from tagloc.overlay import draw_status_bar

    return draw_status_bar(image.copy(), [NO_CALIBRATION_HINT])


def _resize_for_stream(image, max_width: int):
    """Downscale only for publishing, if wider than `max_width`.

    Detection and calibration still see the full camera frame. This function
    runs only after the overlay, on a copy for encoding. Without it, cam_ceiling
    (2028x1520), for example, would JPEG-encode a ~3 MP image on every tick,
    noticeably lowering the stream frame rate.
    """
    import cv2

    height, width = image.shape[:2]
    if width <= max_width:
        return image
    scale = max_width / width
    return cv2.resize(image, (max_width, round(height * scale)), interpolation=cv2.INTER_AREA)


class CameraStreamPublisher:
    """Periodically write the newest camera frame into a String node.

    Does **not** open or close the camera itself. The camera belongs to the
    detection source, which passes the same `SharedCamera` instance (see
    `runner/livestream.py`). Deliberately writes on every tick, even for an
    unchanged frame: the frontend should see a simple "image arrives / does not
    arrive" signal, not diff logic.

    **No health monitoring here.** Whether the camera is alive is answered solely
    by `Health/DeviceHealth` per OPC 40100-2 (`camera_health.py`), fed from the
    watchdog in `camera.py`. This publisher used to check the image age as well
    and clear the node -- two sources of truth for the same question, one of
    which was off the standard. With a hung camera the live image therefore now
    freezes; the status indicator says that it froze, not the missing image.
    """

    def __init__(
        self,
        camera: SharedCamera,
        node: Node,
        config: CameraStreamConfig,
        *,
        encode_frame: Callable[[Any, int], str | None] = _encode_jpeg_base64,
        annotator: Any = None,
        mode_node: Node | None = None,
        progress_node: Node | None = None,
        preview_status_node: Node | None = None,
    ) -> None:
        self._camera = camera
        self._node = node
        self._config = config
        #: Replaceable for tests that should run without `cv2`.
        self._encode_frame = encode_frame
        #: Draws detection results onto the image; `None` = always raw frame.
        self._annotator = annotator
        #: Writable node through which the frontend selects the mode.
        self._mode_node = mode_node
        #: `CalibrationProgress` node; written only while the annotator has a
        #: `CalibrationSession` attached (see
        #: `AprilTagStreamAnnotator.calibration_progress`).
        self._progress_node = progress_node
        #: `TagReferencePreviewStatus` node; written only when the annotator has
        #: a new state (`reference_preview_status`).
        self._preview_status_node = preview_status_node
        self._preview_status_written: Any = None
        self._mode = normalise_mode(config.overlay_mode)
        self._task: asyncio.Task | None = None
        #: The last overlay run; while it is still running, no new one is started.
        self._overlay_run: asyncio.Future | None = None
        #: The last encoded image (Base64) with its sequence number. The node and
        #: the MJPEG server both read it here -- each frame is marked up and
        #: encoded only once, no matter how many consumers there are.
        self._latest: str | None = None
        self._seq = 0
        #: (frame timestamp, mode) of the last encoded image; the same key means
        #: no new frame, nothing to re-encode.
        self._encoded_key: tuple | None = None
        self._frame_ready = asyncio.Condition()
        #: Running MJPEG connections; without them the loop ticks only at
        #: `stream_fps`, to spare the Pi's CPU.
        self._viewers = 0
        self._next_node_write = 0.0

    @property
    def mode(self) -> str:
        """Return the last-read overlay mode."""
        return self._mode

    async def _read_mode(self) -> str:
        """Read the selected mode. Keeps the last value on failure.

        An unreadable node is no reason to stop the stream -- the image
        matters more than the markup.
        """
        if self._mode_node is None:
            return self._mode
        try:
            self._mode = normalise_mode(await self._mode_node.read_value())
        except Exception:
            _log.debug("Overlay-Modus nicht lesbar, bleibe bei '%s'", self._mode)
        return self._mode

    def start(self) -> None:
        self._task = asyncio.create_task(self._publish_loop())

    @property
    def latest(self) -> tuple[int, str] | None:
        """Sequence number and Base64 JPEG of the newest image.

        Deliberately without an age limit: whether the camera is alive is said by
        `DeviceHealth` per OPC 40100-2 (camera_health.py), not by this publisher.
        A still image is an image, not a health signal -- two truths about it
        would be one too many.
        """
        if self._latest is None:
            return None
        return self._seq, self._latest

    @contextlib.asynccontextmanager
    async def viewer(self):
        """Register an MJPEG viewer; while one is connected, the loop ticks faster."""
        self._viewers += 1
        try:
            yield
        finally:
            self._viewers -= 1

    async def next_frame(self, after_seq: int, timeout: float) -> tuple[int, str] | None:
        """Wait for an image with a higher number than `after_seq`.

        Returns `None` after `timeout` -- for example because the camera hangs and
        the frame is stale. A slow reader misses images instead of building up a
        backlog: there is always only the newest one.
        """
        async with self._frame_ready:
            try:
                await asyncio.wait_for(
                    self._frame_ready.wait_for(
                        lambda: self.latest is not None and self._seq > after_seq
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                return None
            return self.latest

    async def _annotate(self, loop: asyncio.AbstractEventLoop, image, mode: str):
        """Run the overlay with a timeout; on timeout or a run still going, return the raw image.

        Returns `(image, complete)`; `False` means the raw image went out as a
        stand-in and must not count as a fully marked-up image.

        A hung run blocks a thread of the default executor, which cannot be
        cancelled. So no new run starts until the previous one has finished --
        otherwise the executor would fill up tick after tick.
        """
        if self._overlay_run is not None and not self._overlay_run.done():
            return image, False
        # In the worker thread: detection and drawing are blocking and have no
        # business on the event loop. The annotator works on a copy.
        run = loop.run_in_executor(None, self._annotator.annotate, image, mode)
        # Retrieve the exception of a detached run, otherwise asyncio reports
        # "Future exception was never retrieved".
        run.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._overlay_run = run
        try:
            annotated = await asyncio.wait_for(
                asyncio.shield(run), timeout=self._config.overlay_timeout_s
            )
        except TimeoutError:
            _log.warning(
                "Overlay laenger als %.1f s, sende das Rohbild",
                self._config.overlay_timeout_s,
            )
            return image, False
        return annotated, True

    async def _encoded(self, loop: asyncio.AbstractEventLoop, frame) -> str | None:
        """Mark up and encode a frame, but only if it is new."""
        mode = await self._read_mode()
        key = (frame.timestamp, mode)
        if key == self._encoded_key and self._latest is not None:
            return self._latest
        # Every mode works from the full frame, not from the small ISP preview:
        # - "apriltag" gets the full frame as input and draws on a canvas scaled
        #   down to `detection_max_width`. The tags come from a detection at full
        #   resolution -- usually that of the job or the cell watch, otherwise a
        #   separate rare one in the background (since 2026-10-21, see
        #   stream_overlay.py). Detecting on the full frame at stream rate cost
        #   about 102% of one core on pi1 according to py-spy and made jobs run
        #   into the timeout.
        # - "off" is the debug raw image: it must show exactly what the Pi really
        #   sees (to check focus, exposure, framing), not the downscaled preview.
        #   It costs barely more than before -- without detection/overlay only the
        #   larger encode is new.
        # - "undistorted" also uses the full frame: the calibration belongs to the
        #   sensor image, not to the ISP preview with its own crop.
        # The mode "calibration" (board corners on the small preview image) was
        # removed on 2026-11-17; `CameraFrame.preview` is no longer read here.
        source = frame.image
        image, complete = source, True
        if mode != "off" and self._annotator is not None:
            image, complete = await self._annotate(loop, source, mode)
        elif mode == "undistorted":
            image = await loop.run_in_executor(None, _no_calibration_hint, source)
        if self._config.max_stream_width is not None:
            # After the overlay, and only for publishing -- both paths (MJPEG and
            # node) get the same downscaled image. In "apriltag" mode `annotate()`
            # has already downscaled to `detection_max_width` (== `max_stream_width`)
            # itself, so `_resize_for_stream` is a no-op here (the width already
            # fits). For "off" (full frame) it does the real work.
            image = await loop.run_in_executor(
                None, _resize_for_stream, image, self._config.max_stream_width
            )
        encoded = await loop.run_in_executor(
            None, self._encode_frame, image, self._config.jpeg_quality
        )
        if encoded is None:
            return None
        self._latest = encoded
        self._seq += 1
        # A stand-in raw image is retried on the next tick.
        self._encoded_key = key if complete else None
        async with self._frame_ready:
            self._frame_ready.notify_all()
        return encoded

    async def _publish_progress(self) -> None:
        """Write the calibration progress, at the node's rate.

        Independent of the selected `mode` -- the progress should also be visible
        while the viewer is showing "off".
        """
        if self._progress_node is None or self._annotator is None:
            return
        try:
            progress = self._annotator.calibration_progress
            await self._progress_node.write_value(
                json.dumps(
                    progress
                    if progress is not None
                    else {"running": False, "processing": False}
                )
            )
        except Exception:
            _log.exception("Kalibrier-Fortschritt konnte nicht veroeffentlicht werden")

    async def _publish_preview_status(self) -> None:
        """Write the state of the referencing preview, if it is new (in the event loop)."""
        if self._preview_status_node is None or self._annotator is None:
            return
        expire = getattr(self._annotator, "expire_reference_preview", None)
        if expire is not None:
            expire()
        status = getattr(self._annotator, "reference_preview_status", None)
        if status is None or status is self._preview_status_written:
            return
        self._preview_status_written = status
        try:
            await self._preview_status_node.write_value(json.dumps(status))
        except Exception:
            _log.exception("TagReferencePreviewStatus konnte nicht geschrieben werden")

    async def _publish_loop(self) -> None:
        """Tick at `http_fps` while MJPEG viewers are connected, otherwise at `stream_fps`.

        The node is written at most at `stream_fps`, regardless -- it is the
        fallback path, not a video channel.
        """
        loop = asyncio.get_running_loop()
        node_interval = 1.0 / self._config.stream_fps
        live_interval = 1.0 / max(self._config.http_fps, self._config.stream_fps)
        while True:
            started = loop.time()
            node_due = started >= self._next_node_write
            await self._publish_preview_status()
            frame = self._camera.latest_frame
            if frame is not None:
                try:
                    encoded = await self._encoded(loop, frame)
                    if encoded is not None and node_due:
                        await self._node.write_value(encoded)
                except Exception:
                    _log.exception("Kamera-Frame konnte nicht veroeffentlicht werden")
            if node_due:
                # Fixed schedule instead of "since the last write", otherwise the
                # rate drops below stream_fps when the tick is fast.
                self._next_node_write = max(self._next_node_write + node_interval, started)
                await self._publish_progress()
            interval = live_interval if self._viewers else node_interval
            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def stop(self) -> None:
        if self._task is not None:
            await cancel_and_wait(self._task)
            self._task = None
        # The annotator holds its own detection thread (stream_overlay.py).
        close = getattr(self._annotator, "close", None)
        if close is not None:
            close()
