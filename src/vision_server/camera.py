"""One camera, one capture loop, several readers.

Detection (`detection/`) and the live-stream publisher (`camera_stream.py`)
both need images from the same physical camera. Picamera2/libcamera **and** RealSense,
however, allow only one open handle per camera at a time -- a second open while the
stream is running would fail. Therefore exactly one `SharedCamera` keeps the hardware
open and captures frames continuously; all readers get the same cached frame via
`latest_frame` instead of addressing the hardware themselves.

Which hardware is used is chosen by `CameraStreamConfig.backend`
(`profiles.CAMERA_BACKENDS`) -- e.g. Picamera2 on the ceiling Pi, RealSense on the hand
Pi. All backends deliver the same `CameraFrame` (BGR array); the detection source and
the stream publisher do not know the concrete backend type.

`picamera2`/`pyrealsense2`/`cv2` are only imported in `open()`, so that a server without
a `camera_stream` configuration (e.g. local development, tests) starts without these
dependencies.

Watchdog: `capture_array()` can hang forever without any error message when libcamera
no longer delivers a frame (observed on Pi 1 via `py-spy`). The capture
loop therefore bounds every capture, reopens the camera on hangs and, as a last resort,
terminates the process so that systemd restarts it. This applies equally to all
backends -- `wait_for_frames()` of the RealSense also runs under the same timeout. A
hanging worker thread cannot be aborted in Python -- it is left behind, not terminated.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .aio import cancel_and_wait
from .profiles import CAMERA_BACKENDS, CameraStreamConfig

_log = logging.getLogger(__name__)


#: Main stream of the Pi camera. Picamera2 stores `RGB888` in memory as B,G,R -- that is
#: already OpenCV BGR, unlike the Picamera2 default `XBGR8888` (R,G,B,X), which needed a
#: colour conversion per frame. At 12 MP this alone cost a good part of a core.
PICAMERA2_MAIN_FORMAT = "RGB888"
#: The `lores` stream can only do YUV420 on the Pi 4 (Pi 5: RGB as well).
PICAMERA2_PREVIEW_FORMAT = "YUV420"


@dataclass(frozen=True)
class CameraFrame:
    """One captured frame (BGR, as expected by OpenCV) with a timestamp.

    `image` is always the full image -- detection and calibration compute on it.
    `preview` is the same image small, scaled by the camera ISP, for live stream and
    overlay; `None` if the backend has no second stream.
    """

    image: Any
    timestamp: float
    preview: Any = None


@dataclass(frozen=True)
class CameraStatus:
    """Snapshot of what the watchdog knows anyway.

    A data class instead of seven properties: whoever evaluates the state should see a
    self-consistent picture and not catch a reopen between two queries.
    `camera_health.py` turns this into `DeviceHealth` per OPC 40100-2; this file stays
    free of OPC UA.
    """

    #: The capture loop is running (corresponds to `is_open`).
    running: bool
    #: A camera handle is open. Briefly False during `_reopen`.
    has_handle: bool
    #: Outcome of the last capture: "none" | "ok" | "error" | "hung".
    last_outcome: str
    #: Capture failures in a row, reset by every "ok".
    consecutive_failures: int
    #: Re-openings without a frame in between; 0 means "healthy".
    reopen_attempts: int
    #: The reopen budget is exhausted, the process terminates shortly.
    gave_up: bool
    #: Age of the newest frame in seconds, `None` before the first capture.
    frame_age_s: float | None


def yuv420_to_bgr(array: Any, size: tuple[int, int]) -> Any:
    """Convert a Picamera2 `YUV420` buffer (I420) into a BGR image of `size`.

    The buffer has the shape `(height * 3 / 2, stride)`; `stride` can be wider than the
    image because of row alignment. The colour planes then lie on the same grid with
    `stride / 2`, so converting at full `stride` width is consistent -- afterwards the
    result is cropped to the real width.
    """
    import cv2

    width, height = int(size[0]), int(size[1])
    bgr = cv2.cvtColor(array, cv2.COLOR_YUV2BGR_I420)
    if bgr.shape[0] != height:
        raise ValueError(
            f"YUV420-Puffer passt nicht zu {width}x{height}: Form {tuple(array.shape)}"
        )
    return bgr[:, :width] if bgr.shape[1] > width else bgr


def realsense_color_profiles(device) -> set[tuple[int, int, int, str]]:
    """Return the colour stream profiles of a RealSense device as `(width, height, fps, format)`.

    Shared by the error message in `_open_realsense` and the tool
    `tools/list_realsense_profiles.py`. Only queries capabilities, does not open a
    pipeline.
    """
    import pyrealsense2 as rs

    profiles = set()
    for sensor in device.query_sensors():
        for profile in sensor.get_stream_profiles():
            if profile.stream_type() != rs.stream.color:
                continue
            video = profile.as_video_stream_profile()
            profiles.add((video.width(), video.height(), profile.fps(), profile.format().name))
    return profiles


def _list_realsense_color_profiles() -> str:
    """Query the connected RealSense for its colour stream profiles.

    Purely diagnostic for the error message in `_open_realsense` -- it must never raise
    itself, otherwise it would swallow the actual error.
    """
    try:
        import pyrealsense2 as rs

        devices = rs.context().query_devices()
        if len(devices) == 0:
            return "keine RealSense gefunden (Kabel/USB-Port pruefen)"
        profiles = realsense_color_profiles(devices[0])
        if not profiles:
            return "Kamera gefunden, aber keine Farb-Profile gemeldet"
        return ", ".join(
            f"{w}x{h}@{fps}fps({fmt})" for w, h, fps, fmt in sorted(profiles)
        )
    except Exception as error:  # noqa: BLE001 -- purely diagnostic, see docstring
        return f"Profile nicht abrufbar ({error})"


def exit_process() -> None:
    """Terminate the process hard so that systemd (`Restart=always`) restarts it.

    `os._exit` instead of `sys.exit`: a normal exit waits at shutdown for all executor
    threads -- and thus for the one that hangs in `capture_array()`. The process would
    never end.
    """
    logging.shutdown()
    os._exit(1)


def picamera2_video_configuration(config: CameraStreamConfig) -> dict[str, Any]:
    """Return the arguments for `Picamera2.create_video_configuration()` from the config.

    Main stream at full `resolution` for jobs and calibration; with `preview_resolution`
    additionally a `lores` stream for the live stream, which the ISP scales from the same
    frame.
    """
    arguments: dict[str, Any] = {
        "main": {"size": tuple(config.resolution), "format": PICAMERA2_MAIN_FORMAT},
    }
    if config.preview_resolution is not None:
        preview = tuple(config.preview_resolution)
        if preview[0] > config.resolution[0] or preview[1] > config.resolution[1]:
            raise ValueError(
                f"preview_resolution {preview} ist groesser als resolution "
                f"{tuple(config.resolution)} -- der lores-Strom darf nur kleiner sein"
            )
        arguments["lores"] = {"size": preview, "format": PICAMERA2_PREVIEW_FORMAT}
    if config.buffer_count is not None:
        arguments["buffer_count"] = int(config.buffer_count)
    if config.camera_controls:
        # Picamera2 sets its video defaults (`FrameDurationLimits`, `NoiseReductionMode`)
        # under these controls: `create_video_configuration` merges them with the
        # defaults, it does not replace them. Names and enum strings are still unresolved
        # here, see `resolve_camera_controls`.
        arguments["controls"] = dict(config.camera_controls)
    return arguments


#: Enum controls that may be given by name -> path of the enum class in
#: `libcamera.controls`. `NoiseReductionMode` lives there under `draft`.
CAMERA_ENUM_CONTROLS: dict[str, str] = {
    "NoiseReductionMode": "draft.NoiseReductionModeEnum",
    "AeMeteringMode": "AeMeteringModeEnum",
    "AeExposureMode": "AeExposureModeEnum",
    "AeConstraintMode": "AeConstraintModeEnum",
    "AwbMode": "AwbModeEnum",
}


def resolve_camera_controls(controls: dict[str, Any], namespace: Any) -> dict[str, Any]:
    """Replace enum names (`"Fast"`) with the libcamera values.

    `namespace` is `libcamera.controls` (injectable, so that this stays testable without
    libcamera). Only the controls from `CAMERA_ENUM_CONTROLS` are resolved; numbers and
    all other values remain unchanged. Raises `ValueError` listing the valid names for
    an unknown enum name.
    """
    resolved = dict(controls)
    for name, path in CAMERA_ENUM_CONTROLS.items():
        value = resolved.get(name)
        if not isinstance(value, str):
            continue
        enum_class = namespace
        for part in path.split("."):
            enum_class = getattr(enum_class, part)
        if not hasattr(enum_class, value):
            valid = ", ".join(sorted(n for n in dir(enum_class) if n[:1].isupper()))
            raise ValueError(
                f"Kamera-Control {name}: unbekannter Name '{value}' (gueltig: {valid})"
            )
        resolved[name] = getattr(enum_class, value)
    return resolved


def validate_camera_controls(
    controls: dict[str, Any], available: dict[str, Any]
) -> dict[str, Any]:
    """Check the names against `Picamera2.camera_controls` and align number types.

    `available` maps control name to `(minimum, maximum, default)`. An integer value for
    a float control becomes `float` (and vice versa if it is integral), `0`/`1` for a
    bool control becomes `bool`; lists from JSON become tuples. Raises `ValueError` with
    the camera's names if a control does not occur there -- a typo should surface at
    start-up.
    """
    unknown = sorted(name for name in controls if name not in available)
    if unknown:
        raise ValueError(
            f"Unbekannte Kamera-Controls: {', '.join(unknown)} "
            f"(diese Kamera kennt: {', '.join(sorted(available))})"
        )
    checked: dict[str, Any] = {}
    for name, value in controls.items():
        info = available[name]
        default = info[2] if isinstance(info, (tuple, list)) and len(info) >= 3 else None
        if isinstance(value, list):
            value = tuple(value)
        if isinstance(default, bool):
            if isinstance(value, int) and value in (0, 1):
                value = bool(value)
        elif isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        elif (
            isinstance(default, int)
            and isinstance(value, float)
            and value.is_integer()
        ):
            value = int(value)
        checked[name] = value
    return checked


#: Metadata that goes to the log after warm-up: this is how one picks fixed exposure
#: values for `camera_controls`.
CAMERA_METADATA_KEYS: tuple[str, ...] = (
    "ExposureTime",
    "AnalogueGain",
    "DigitalGain",
    "Lux",
    "ColourTemperature",
    "FocusFoM",
)


def format_camera_metadata(metadata: dict[str, Any]) -> str:
    """Return the entries from `CAMERA_METADATA_KEYS` that the camera reports, as one line."""
    return ", ".join(f"{key}={metadata[key]}" for key in CAMERA_METADATA_KEYS if key in metadata)


#: `calibration_id` of a factory intrinsic read from the RealSense.
REALSENSE_FACTORY_CALIBRATION_ID = "realsense-werksintrinsik"


def _realsense_calibration(pipeline: Any, frame_id: str) -> Any:
    """Return the factory intrinsics of the colour stream of a running pipeline.

    librealsense' Brown-Conrady corresponds to the OpenCV pinhole model. For other models
    (the D4xx colour camera reports "Inverse Brown Conrady", practically always with
    coefficients 0) the coefficients are only taken over if they are all 0 -- otherwise
    they would mean something different to OpenCV, and a silently wrong distortion would
    be worse than none.
    """
    import numpy as np
    import pyrealsense2 as rs

    from tagloc.calibration import PINHOLE, CameraCalibration

    profile = pipeline.get_active_profile().get_stream(rs.stream.color)
    intrinsics = profile.as_video_stream_profile().get_intrinsics()
    coefficients = [float(value) for value in intrinsics.coeffs][:5]
    coefficients += [0.0] * (5 - len(coefficients))
    if intrinsics.model != rs.distortion.brown_conrady and any(coefficients):
        _log.warning(
            "RealSense-Verzeichnungsmodell %s mit Koeffizienten %s ist nicht "
            "OpenCV-kompatibel -- ohne Verzeichnung weiter",
            intrinsics.model,
            coefficients,
        )
        coefficients = [0.0] * 5
    return CameraCalibration(
        camera_matrix=np.array(
            [
                [intrinsics.fx, 0.0, intrinsics.ppx],
                [0.0, intrinsics.fy, intrinsics.ppy],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        ),
        distortion=np.array(coefficients, dtype=np.float64),
        image_size=(int(intrinsics.width), int(intrinsics.height)),
        model=PINHOLE,
        frame_id=frame_id,
        calibration_id=REALSENSE_FACTORY_CALIBRATION_ID,
    )


class SharedCamera:
    """Keep one camera open and provide the most recent frame."""

    def __init__(
        self,
        config: CameraStreamConfig,
        *,
        on_give_up: Callable[[], Awaitable[None] | None] = exit_process,
    ) -> None:
        self._config = config
        self._camera: Any = None
        self._executor: ThreadPoolExecutor | None = None
        self._loop_task: asyncio.Task | None = None
        self._latest: CameraFrame | None = None
        #: Exchangeable for tests that must not terminate the process, and for the runner,
        #: which wants to publish a final FAILURE beforehand.
        self._on_give_up = on_give_up
        #: Counter states of the watchdog. Deliberately kept at instance level and not as
        #: local variables in the capture loop: otherwise only the loop would know the
        #: state, and `status()` would have to derive it a second time.
        self._failures = 0
        self._reopens = 0
        self._last_outcome = "none"
        self._gave_up = False
        #: Size of the `lores` stream once Picamera2 is configured with it; otherwise
        #: `None` and every capture delivers only `image`.
        self._preview_size: tuple[int, int] | None = None

    @property
    def latest_frame(self) -> CameraFrame | None:
        """The most recently captured frame, or `None` before the first capture."""
        return self._latest

    @property
    def is_open(self) -> bool:
        """Whether the capture loop is running."""
        return self._loop_task is not None

    def status(self, now: float) -> CameraStatus:
        """Return the state at time `now` (same `loop.time()` time axis).

        `now` is passed in instead of fetched here: `CameraFrame.timestamp` comes from
        `loop.time()`, and only the caller knows whether it runs on the same loop. This
        keeps the method synchronous and testable without a running loop.
        """
        latest = self._latest
        return CameraStatus(
            running=self.is_open,
            has_handle=self._camera is not None,
            last_outcome=self._last_outcome,
            consecutive_failures=self._failures,
            reopen_attempts=self._reopens,
            gave_up=self._gave_up,
            frame_age_s=None if latest is None else now - latest.timestamp,
        )

    def set_give_up_handler(
        self, handler: Callable[[], Awaitable[None] | None]
    ) -> None:
        """Replace what happens on final give-up.

        Needed because the camera is created in `build_detection_sources` -- long before
        there is a state node into which a final FAILURE could be written.
        """
        self._on_give_up = handler

    async def open(self) -> None:
        """Open the camera and start the capture loop. Not idempotent."""
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-camera")
        loop = asyncio.get_running_loop()
        self._camera = await loop.run_in_executor(self._executor, self._open_camera)
        self._loop_task = asyncio.create_task(self._capture_loop())

    async def factory_calibration(self, frame_id: str = "") -> Any | None:
        """Return the factory intrinsics of the camera as `CameraCalibration`, otherwise `None`.

        Only the RealSense knows its intrinsics itself (from manufacturing, stored on the
        device). This is no replacement for an own calibration, but far better than the
        estimated placeholder -- it is the fallback if the calibration file is missing.
        Runs in the camera thread so that it does not compete with the capture loop for
        the pipeline.
        """
        if self._config.backend != "realsense" or self._camera is None or self._executor is None:
            return None
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                self._executor, _realsense_calibration, self._camera, frame_id
            )
        except Exception:
            _log.warning("Werks-Intrinsik der RealSense nicht lesbar", exc_info=True)
            return None

    def _open_camera(self) -> Any:
        """Run in the camera worker thread: open the hardware plus warm-up."""
        import time

        backend = self._config.backend
        if self._config.camera_controls and backend != "picamera2":
            _log.warning(
                "camera_controls %s werden ignoriert: nur das Backend picamera2 setzt sie "
                "(Backend ist '%s')",
                self._config.camera_controls,
                backend,
            )
        if backend == "picamera2":
            camera = self._open_picamera2()
        elif backend == "realsense":
            camera = self._open_realsense()
        elif backend == "opencv":
            camera = self._open_opencv()
        else:
            raise ValueError(
                f"Unbekanntes Kamera-Backend '{backend}' (bekannt: "
                f"{', '.join(CAMERA_BACKENDS)})"
            )
        time.sleep(self._config.warmup_s)
        if backend == "picamera2":
            self._log_metadata(camera)
        return camera

    def _log_metadata(self, camera: Any) -> None:
        """Log the actual exposure values after warm-up, once per opening.

        Runs in the camera worker thread before the capture loop starts -- no race over
        the requests. An error here is only a missing log line, not a reason to stop
        using the camera.
        """
        try:
            text = format_camera_metadata(camera.capture_metadata())
        except Exception:
            _log.warning("Kamera-Metadaten nach dem Warmup nicht lesbar", exc_info=True)
            return
        _log.info("Kamera-Metadaten nach dem Warmup: %s", text or "keine")

    def _open_picamera2(self) -> Any:
        from picamera2 import Picamera2

        arguments = picamera2_video_configuration(self._config)
        camera = Picamera2()
        if "controls" in arguments:
            try:
                from libcamera import controls as libcamera_controls

                arguments["controls"] = resolve_camera_controls(
                    validate_camera_controls(arguments["controls"], camera.camera_controls),
                    libcamera_controls,
                )
            except Exception:
                # Misconfiguration (ValueError) or libcamera missing: do not leave the
                # camera open, otherwise every reopen fails on the handle.
                camera.close()
                raise
            _log.info("Kamera-Controls: %s", self._config.camera_controls)
        camera.configure(camera.create_video_configuration(**arguments))
        camera.start()
        self._preview_size = (
            tuple(self._config.preview_resolution)
            if self._config.preview_resolution is not None
            else None
        )
        return camera

    def _open_realsense(self) -> Any:
        """Start a RealSense pipeline on the colour stream (BGR8).

        `pipeline.start()` raises `RuntimeError("Couldn't resolve requests")` if the
        connected camera does not support the requested profile (resolution + fps +
        format) -- e.g. because the RSUSB backend link needed on the Pi limits the
        bandwidth. In that case we log the colour profiles the camera actually supports,
        instead of guessing blindly.
        """
        import pyrealsense2 as rs

        width, height = self._config.realsense_resolution
        fps = self._config.realsense_fps
        rs_config = rs.config()
        rs_config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        pipeline = rs.pipeline()
        try:
            pipeline.start(rs_config)
        except RuntimeError:
            _log.error(
                "RealSense lehnt %dx%d@%dfps (bgr8) ab. Tatsaechlich "
                "unterstuetzte Farb-Profile dieser Kamera: %s",
                width, height, fps, _list_realsense_color_profiles(),
            )
            raise
        return pipeline

    def _open_opencv(self) -> Any:
        import cv2

        camera = cv2.VideoCapture(self._config.camera_index)
        if not camera.isOpened():
            raise RuntimeError(
                "Kamera konnte nicht geoeffnet werden "
                f"(camera_index={self._config.camera_index})"
            )
        return camera

    def _read_frames(self) -> tuple[Any, Any]:
        """Run in the camera worker thread: `(full image, small image | None)`.

        With a `lores` stream a single request fetches both images -- they are thus
        guaranteed to come from the same capture, and the overlay never shows a different
        image than the one the job sees. `make_array` copies, so the request goes back to
        the camera immediately.
        """
        if self._preview_size is None:
            return self._read_frame(), None
        request = self._camera.capture_request()
        try:
            image = request.make_array("main")
            preview = request.make_array("lores")
        finally:
            request.release()
        return image, yuv420_to_bgr(preview, self._preview_size)

    def _read_frame(self) -> Any:
        """Run in the camera worker thread: one frame in BGR."""
        backend = self._config.backend
        if backend == "picamera2":
            # Already BGR, see PICAMERA2_MAIN_FORMAT.
            return self._camera.capture_array("main")
        if backend == "realsense":
            import numpy as np

            frames = self._camera.wait_for_frames()
            color_frame = frames.get_color_frame()
            if not color_frame:
                raise RuntimeError("RealSense-Pipeline lieferte keinen Farbframe")
            # Copy, do not just view: `asanyarray(get_data())` keeps librealsense' buffer
            # alive as long as the array lives. The hand-eye session keeps one image per
            # capture -- without a copy the RealSense runs out of buffers, `wait_for_frames()`
            # hangs and the live stream stops. A 640x480 copy costs practically nothing.
            return np.array(color_frame.get_data())
        ok, frame = self._camera.read()
        if not ok:
            raise RuntimeError("Kamera lieferte kein Bild")
        return frame

    async def _capture_once(self, loop: asyncio.AbstractEventLoop) -> str:
        """One capture with a timeout: `"ok"`, `"error"` or `"hung"`."""
        if self._camera is None:
            # The last re-opening failed; nothing to read.
            self._last_outcome = "hung"
            return "hung"
        try:
            image, preview = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._read_frames),
                timeout=self._config.frame_timeout_s,
            )
        except TimeoutError:
            _log.error(
                "Kamera liefert seit %.1f s kein Bild -- capture haengt",
                self._config.frame_timeout_s,
            )
            self._last_outcome = "hung"
            return "hung"
        except Exception:
            _log.exception("Kamera-Frame konnte nicht aufgenommen werden")
            self._last_outcome = "error"
            return "error"
        self._latest = CameraFrame(image=image, timestamp=loop.time(), preview=preview)
        self._last_outcome = "ok"
        return "ok"

    async def _capture_loop(self) -> None:
        """Capture frames at `capture_fps` until the task is cancelled.

        Escalation: failures are counted; from `max_capture_failures` on (a hang counts as
        full immediately) the camera is reopened; after `max_reopen_attempts` reopenings
        without a frame in between, it gives up.
        """
        loop = asyncio.get_running_loop()
        interval = 1.0 / self._config.capture_fps
        while True:
            started = loop.time()
            outcome = await self._capture_once(loop)
            if outcome == "ok":
                self._failures = 0
                self._reopens = 0
            elif outcome == "hung":
                self._failures = self._config.max_capture_failures
            else:
                self._failures += 1
            if self._failures >= self._config.max_capture_failures:
                self._failures = 0
                self._reopens += 1
                if self._reopens > self._config.max_reopen_attempts:
                    _log.critical(
                        "Kamera nach %d Neu-Oeffnungen ohne Bild -- beende den Prozess, "
                        "systemd startet ihn neu",
                        self._config.max_reopen_attempts,
                    )
                    self._gave_up = True
                    # The handler may be asynchronous: the runner writes a final FAILURE
                    # into the asset view here, before the process is torn down. The
                    # default handler is synchronous and never returns anyway.
                    result = self._on_give_up()
                    if result is not None:
                        await result
                    return
                await self._reopen(self._reopens)
                continue
            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def _reopen(self, attempt: int) -> None:
        """Release the camera and reopen it with a fresh worker.

        The old worker may still hang in `capture_array()`; it is left behind together
        with its executor. A failure leaves `_camera` as `None`; the next pass then
        escalates further.
        """
        _log.warning(
            "Kamera haengt, oeffne sie neu (Versuch %d/%d)",
            attempt,
            self._config.max_reopen_attempts,
        )
        camera, self._camera = self._camera, None
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if camera is not None:
            await self._close_in_own_thread(camera)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-camera")
        loop = asyncio.get_running_loop()
        try:
            self._camera = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._open_camera),
                timeout=self._config.frame_timeout_s + self._config.warmup_s,
            )
        except Exception:
            _log.exception("Kamera laesst sich nicht neu oeffnen")
            return
        _log.info("Kamera neu geoeffnet")

    async def _close_in_own_thread(self, camera: Any) -> None:
        """Close the camera in a throwaway thread, with a timeout.

        Not in the camera worker: that one may hang in `capture_array()`, and a `close`
        would then wait forever behind it. If closing hangs too, this thread is simply
        left behind.
        """
        closer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision-camera-close")
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(closer, self._close_camera, camera),
                timeout=self._config.frame_timeout_s,
            )
        except Exception:
            _log.exception("Kamera liess sich nicht sauber schliessen")
        finally:
            closer.shutdown(wait=False, cancel_futures=True)

    async def close(self) -> None:
        """Stop the capture loop and release the camera. Idempotent."""
        if self._loop_task is not None:
            await cancel_and_wait(self._loop_task)
            self._loop_task = None
        if self._camera is not None:
            camera, self._camera = self._camera, None
            await self._close_in_own_thread(camera)
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _close_camera(self, camera: Any) -> None:
        """Stop and release the camera, using the call that matches the backend."""
        backend = self._config.backend
        if backend == "picamera2":
            camera.stop()
            camera.close()
        elif backend == "realsense":
            camera.stop()  # `camera` is the `rs.pipeline()`
        else:
            camera.release()
