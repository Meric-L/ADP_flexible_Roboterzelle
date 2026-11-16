"""Interactive calibration against the shared camera, driven by the frontend.

Reads from the already running `SharedCamera`, so the server and the live stream
keep running during calibration. `capture()` only stores the current frame; there
is no live board check during capture. `finish()` evaluates the captures in a
background task and returns at once. The result appears in `progress["result"]`,
and `progress["processing"]` is `True` while it is computed.
"""

import asyncio
import dataclasses
import importlib
import inspect
import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .camera import SharedCamera
from .errors import VisionErrorCode
from .profiles import AprilTagProfileConfig

_log = logging.getLogger(__name__)

#: RMS reprojection error in px above which a warning is shown. Only a warning: an
#: imprecise calibration is still saved.
RMS_WARNING_PX = 0.5

#: Computational lower bound for `calibrate_from_samples` (see
#: `_compute_and_save`), independent of how low `calibration_min_samples` is
#: configured. That setting is a *recommended* threshold, not a mathematical
#: minimum. Below this bound, `finish()` returns `DETECTION_FAILED` right away,
#: without even starting the (expensive) background evaluation.
MIN_SAMPLES_FOR_CALIBRATION = 3

#: Time limit in seconds for one `detect_board` call in `_process()`. On the Pi a call
#: on the full camera frame can hang without returning. A hung thread cannot be
#: aborted in Python, so the capture counts as "board not found" and the pool is
#: discarded. Without the limit the single-worker pool would block the whole evaluation.
CAPTURE_DETECT_TIMEOUT_S = 20.0


def _lazy(module: str, name: str) -> Callable:
    """Resolve a `tagloc` function only at call time.

    This file never imports `tagloc` or `cv2` at module level.
    """

    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    return call


#: Working captures (`_pending_dir`) are JPEG: PNG writes to the Pi SD card were too
#: slow, and lossy compression does not noticeably move chessboard corners. The
#: optional archive uses lossless PNG (`_ARCHIVE_IMAGE_SUFFIX`), so intrinsics can
#: be recomputed offline exactly.
_CAPTURE_IMAGE_SUFFIX = ".jpg"
_ARCHIVE_IMAGE_SUFFIX = ".png"
_ARCHIVE_SCHEMA = "adp.calibration.captures/1"
_CAPTURE_JPEG_QUALITY = 92


def _save_capture_image(path: Any, image: Any) -> None:
    """Write a capture; the format follows the extension: `.png` is lossless (fast
    compression), anything else is JPEG. No `tagloc` dependency, hence no `_lazy(...)`."""
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    if str(path).lower().endswith(".png"):
        cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    else:
        cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, _CAPTURE_JPEG_QUALITY])


def _load_capture_image(path: Any) -> Any:
    """Read back a previously saved capture for the evaluation in `_process()`.

    Returns `None` if the file is missing or not an image. `_process()` then
    skips it instead of aborting.
    """
    import cv2

    return cv2.imread(str(path))


class CalibrationSession:
    """Collect camera frames until `finish()` evaluates them in the background.

    Reusable: `start()` resets the internal state completely, so
    `runner/calibration.py` keeps a single instance across several start/finish
    cycles.

    The `tagloc` functions (`detect_board`, `build_board`,
    `calibrate_from_samples`, `compute_coverage`, `save_calibration`) can be
    injected. Tests thus run without `cv2`, without a camera and without a
    calibration file, following the same pattern as the `detector`,
    `calibration` and `tag_map` parameters of `AprilTagDetectionSource`.
    """

    def __init__(
        self,
        camera: SharedCamera,
        config: AprilTagProfileConfig,
        *,
        detect_board: Callable | None = None,
        build_board: Callable | None = None,
        calibrate_from_samples: Callable | None = None,
        compute_coverage: Callable | None = None,
        save_calibration: Callable | None = None,
        on_calibrated: Callable[[Any], Any] | None = None,
        save_capture_image: Callable | None = None,
        load_capture_image: Callable | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._camera = camera
        self._config = config
        self._out_path = config.calibration_path
        #: Intermediate storage for the captures of the running session, until
        #: `finish()` evaluates them. Emptied on every `start()` (see there).
        #: Separate from `calibration_capture_dir` (optional permanent debug
        #: archive, see below): this folder is pure working storage, and its
        #: content has no meaning after a successful evaluation.
        self._pending_dir = self._out_path.parent / f"{config.frame_id}_pending"
        #: Paths of the captures made in this session, in order.
        self._captured_paths: list[Any] = []
        #: `None` (default) archives nothing extra. If set, every capture is
        #: additionally written here, for re-checking away from the server,
        #: independent of `_pending_dir` (which is emptied on every `start()`).
        self._capture_dir = config.calibration_capture_dir
        #: Session folder of the archive (`_capture_dir / <UTC stamp>`), set by
        #: `start()`. `None` if there is no archive or creating it failed.
        self._archive_dir: Path | None = None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._saved_captures = 0
        self._save_capture_image = save_capture_image or _save_capture_image
        self._load_capture_image = load_capture_image or _load_capture_image
        #: Running background tasks from `_schedule_capture_save`. Holding a
        #: reference keeps them alive; otherwise the garbage collector may collect
        #: an `asyncio.Task` without a held reference too early.
        self._pending_saves: set = set()
        #: Called after a successful save (async or sync), with the freshly
        #: computed `CameraCalibration` object. `runner/calibration.py` sets this
        #: via `set_on_calibrated`, to bring detection and overlay to the new state
        #: without a server restart.
        self._on_calibrated = on_calibrated
        self._detect_board = detect_board or _lazy("tagloc.boards", "detect_board")
        self._build_board = build_board or _lazy("tagloc.boards", "build_board")
        self._calibrate_from_samples = calibrate_from_samples or _lazy(
            "tagloc.boards", "calibrate_from_samples"
        )
        self._compute_coverage = compute_coverage or _lazy("tagloc.boards", "compute_coverage")
        self._save_calibration = save_calibration or _lazy(
            "tagloc.calibration", "save_calibration"
        )
        self._board_spec: Any = None  # lazy: needs cv2, see `_spec()`
        self._samples: list = []
        self._image_size: tuple[int, int] = (0, 0)
        self._executor: ThreadPoolExecutor | None = None
        #: Own pool only for `_schedule_capture_save`. See there why it must NOT
        #: share `_executor` with `detect_board`.
        self._save_executor: ThreadPoolExecutor | None = None
        self.running = False
        #: `True` while `_process()` (the background evaluation after `finish()`)
        #: runs. See `busy` and `wait_for_processing`.
        self._processing = False
        #: Set as soon as no background evaluation is running (also initially).
        #: `runner/calibration.py` waits for this to release the paused live
        #: stream again.
        self._processing_done = asyncio.Event()
        self._processing_done.set()
        #: Result of `finish()` or of the background evaluation; `None` until
        #: then. It is also delivered by `progress`, so that an observer of the
        #: pure progress node sees the final result too.
        self.last_result: dict | None = None

    def set_on_calibrated(self, callback: Callable[[Any], Any] | None) -> None:
        """Set or remove the `on_calibrated` callback afterwards.

        `runner/calibration.py` needs both the detection source and the stream
        annotator for this, and both are only built after the session. A setter
        makes this possible without changing the build order.
        """
        self._on_calibrated = callback

    @property
    def archive_dir(self) -> Path | None:
        """Return the archive folder of the current or last session, otherwise `None`."""
        return self._archive_dir

    @property
    def processing(self) -> bool:
        """`True` only during the background evaluation after `finish()`.

        For `MeasureBoard`: measuring is allowed during the capture phase (the
        automatic calibration drive asks before each capture whether the board is
        entirely in view). During the evaluation, the CPU belongs to it.
        """
        return self._processing

    @property
    def busy(self) -> bool:
        """`True` during a capture OR a background evaluation.

        For `StartCalibration`: a new `start()` while an evaluation is still
        running would empty `_pending_dir`, which `_process()` is still reading
        from. This must be prevented, even if `running` is already `False` at that
        point (see `finish()`).
        """
        return self.running or self._processing

    def _spec(self):
        """Build `BoardSpec` from the scalar config values.

        `tagloc` is imported only here, so that `profiles.py` stays free of the
        dependency.
        """
        if self._board_spec is None:
            from tagloc.boards import BoardSpec

            self._board_spec = BoardSpec(
                type=self._config.calibration_board_type,
                cols=self._config.calibration_board_cols,
                rows=self._config.calibration_board_rows,
                square_size_m=self._config.calibration_board_square_size_m,
                marker_size_m=self._config.calibration_board_marker_size_m,
                dictionary=self._config.calibration_board_dictionary,
            )
        return self._board_spec

    def _pool(self) -> ThreadPoolExecutor:
        """Own single-worker pool for `detect_board` and `calibrate_from_samples`.

        Never runs on the event loop; same pattern as `DetectionSource.run_blocking`.
        """
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="vision-calibration"
            )
        return self._executor

    def _backup_existing(self) -> Path | None:
        """Set the previous calibration file aside before the new one replaces it.

        The copy sits next to the file as `<name>.<UTC>.bak` (as with the
        hand-eye calibration). Each backup gets its own name, and none is
        overwritten. Returns the path, or `None` if there was no old file.
        """
        path = Path(self._out_path)
        if not path.is_file():
            return None
        stamp = self._now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        counter = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.{stamp}-{counter}.bak")
            counter += 1
        shutil.copy2(path, backup)
        return backup

    async def _run_blocking(self, func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool(), lambda: func(*args, **kwargs))

    def _save_pool(self) -> ThreadPoolExecutor:
        """Own single-worker pool only for saves, separate from `_pool()`.

        See `_schedule_capture_save` for why this matters.
        """
        if self._save_executor is None:
            self._save_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="vision-calibration-save"
            )
        return self._save_executor

    def _schedule_capture_save(self, path: Any, image: Any) -> None:
        """Save a capture in the background, without making `capture()` wait for it.

        Runs on `_save_pool()`, not on `_pool()`: a slow save must not block the
        `detect_board` worker, and the other way round.
        """
        task = asyncio.ensure_future(
            self._run_on_save_pool(self._save_capture_image, path, image)
        )
        self._pending_saves.add(task)
        task.add_done_callback(self._on_capture_save_done)

    async def _run_on_save_pool(self, func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._save_pool(), lambda: func(*args, **kwargs))

    def _on_capture_save_done(self, task: "asyncio.Task") -> None:
        self._pending_saves.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _log.warning("Aufnahme konnte nicht gespeichert werden: %s", error)

    async def wait_for_pending_saves(self) -> None:
        """Wait for all background saves to finish.

        This is needed before `_process()` reads the files again, otherwise the
        last captures may still be missing. It is also a test seam for tests that
        check the result of `_schedule_capture_save`.
        """
        if self._pending_saves:
            await asyncio.gather(*list(self._pending_saves), return_exceptions=True)

    async def wait_for_processing(self) -> None:
        """Wait until a background evaluation started by `finish()` is done.

        `progress["processing"]` becomes `False` again. `runner/calibration.py`
        uses this to release the live stream that was paused during the evaluation.
        """
        await self._processing_done.wait()

    def start(self) -> None:
        """Begin a new session: captures are reset and old `_pending_dir` content is discarded.

        From now on, captures arrive only via `capture()`.
        """
        self._captured_paths = []
        self._samples = []
        self._image_size = (0, 0)
        self._saved_captures = 0
        self.last_result = None
        self._processing = False
        self._processing_done.set()
        self.running = True
        shutil.rmtree(self._pending_dir, ignore_errors=True)
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir = None
        if self._capture_dir is not None:
            self._start_archive()

    def _start_archive(self) -> None:
        """Create the session folder of the archive, including `session.json`.

        Errors disable only this session's archive, never the calibration.
        """
        started = self._now()
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        try:
            self._capture_dir.mkdir(parents=True, exist_ok=True)
            directory = self._capture_dir / stamp
            suffix = 2
            while True:
                try:
                    directory.mkdir()
                    break
                except FileExistsError:
                    directory = self._capture_dir / f"{stamp}_{suffix}"
                    suffix += 1
            cfg = self._config
            active = None
            try:
                active = json.loads(cfg.calibration_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                _log.warning("Aktive Kalibrierung nicht lesbar, Archiv ohne sie: %s", error)
            meta = {
                "schema": _ARCHIVE_SCHEMA,
                "frameId": cfg.frame_id,
                "startedAt": started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "imageFormat": _ARCHIVE_IMAGE_SUFFIX.lstrip("."),
                "pendingFormat": _CAPTURE_IMAGE_SUFFIX.lstrip("."),
                "board": {
                    "type": cfg.calibration_board_type,
                    "cols": cfg.calibration_board_cols,
                    "rows": cfg.calibration_board_rows,
                    "squareSizeM": cfg.calibration_board_square_size_m,
                    "markerSizeM": cfg.calibration_board_marker_size_m,
                    "dictionary": cfg.calibration_board_dictionary,
                },
                "model": cfg.calibration_model,
                "activeCalibration": active,
            }
            (directory / "session.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except (OSError, ValueError) as error:
            _log.warning("Kalibrier-Archiv fuer diese Sitzung aus: %s", error)
            return
        self._archive_dir = directory

    async def capture(self) -> bool:
        """Remember the most recent camera frame as a capture.

        Does not check whether the board is visible; that is decided in `finish()`.
        Returns `False` only if no session is active or no camera frame is available.
        """
        if not self.running:
            return False
        frame = self._camera.latest_frame
        if frame is None:
            return False
        self._saved_captures += 1
        path = self._pending_dir / f"kalib_{self._saved_captures:03d}{_CAPTURE_IMAGE_SUFFIX}"
        self._captured_paths.append(path)
        self._schedule_capture_save(path, frame.image)
        if self._archive_dir is not None:
            archive_path = (
                self._archive_dir / f"kalib_{self._saved_captures:03d}{_ARCHIVE_IMAGE_SUFFIX}"
            )
            self._schedule_capture_save(archive_path, frame.image)
        _log.info("Kalibrierung: Aufnahme %d gespeichert", len(self._captured_paths))
        return True

    @property
    def progress(self) -> dict:
        """JSON-serializable progress object for `CalibrationProgress`.

        Coverage is only known after the evaluation, in `progress["result"]`.
        """
        data = {
            "running": self.running,
            "processing": self._processing,
            "samples": len(self._captured_paths),
            "minSamples": self._config.calibration_min_samples,
            #: Model with which this session computes (or has computed). It is
            #: already known during the capture, so the frontend can show it in advance.
            "model": self._config.calibration_model,
            "archiveDir": str(self._archive_dir) if self._archive_dir is not None else None,
        }
        if self.last_result is not None:
            data["result"] = self.last_result
        return data

    async def _stop(self) -> None:
        self.running = False
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        if self._save_executor is not None:
            self._save_executor.shutdown(wait=False, cancel_futures=True)
            self._save_executor = None

    async def finish(self) -> tuple[VisionErrorCode, dict]:
        """End the capture phase and start the evaluation in the background.

        Returns immediately, without waiting for the result. A synchronous evaluation
        would risk an RPC timeout. The result appears in `progress["result"]` once
        `_process()` is done.
        """
        count = len(self._captured_paths)
        if count < MIN_SAMPLES_FOR_CALIBRATION:
            await self._stop()
            result = {
                "message": f"Zu wenige Aufnahmen: {count}",
                "samples": count,
                "model": self._config.calibration_model,
            }
            self.last_result = {"error": int(VisionErrorCode.DETECTION_FAILED), **result}
            return VisionErrorCode.DETECTION_FAILED, result

        self.running = False
        self._processing = True
        self._processing_done.clear()
        paths = list(self._captured_paths)
        asyncio.ensure_future(self._process(paths))
        summary = {"message": "Auswertung gestartet", "samples": count}
        return VisionErrorCode.OK, summary

    async def _process(self, paths: list) -> None:
        """Evaluate the saved captures.

        Loads each capture back, searches it for the board, then computes as
        before in `_compute_and_save`. Runs as a background task started by
        `finish()`. Errors here must not go unhandled, otherwise
        `progress["processing"]` would stay `True` forever.
        """
        try:
            # Wait for all running saves first, otherwise the last captures may be
            # missing when the files are read back in.
            await self.wait_for_pending_saves()

            from tagloc import frames as frame_tools

            spec = self._spec()
            board = self._build_board(spec)
            samples: list = []
            image_size: tuple[int, int] = (0, 0)
            for path in paths:
                image = await self._run_blocking(self._load_capture_image, path)
                if image is None:
                    _log.warning("Auswertung: Aufnahme %s nicht lesbar, uebersprungen", path)
                    continue
                image_size = frame_tools.image_size(image)
                try:
                    sample = await asyncio.wait_for(
                        self._run_blocking(
                            self._detect_board, frame_tools.to_gray(image), spec, board
                        ),
                        timeout=CAPTURE_DETECT_TIMEOUT_S,
                    )
                except TimeoutError:
                    # See CAPTURE_DETECT_TIMEOUT_S: the hung thread keeps running in
                    # the background, the pool is discarded, and this one capture
                    # counts as not found.
                    _log.error(
                        "Auswertung: detect_board haengt seit ueber %.0f s bei %s "
                        "-- Aufnahme wird uebersprungen, Pool wird verworfen",
                        CAPTURE_DETECT_TIMEOUT_S,
                        path,
                    )
                    if self._executor is not None:
                        self._executor.shutdown(wait=False, cancel_futures=True)
                        self._executor = None
                    continue
                if sample is not None:
                    samples.append(sample)
            _log.info(
                "Auswertung: %d von %d Aufnahmen zeigten das Board",
                len(samples),
                len(paths),
            )
            self._samples = samples
            self._image_size = image_size
            error, summary = await self._compute_and_save()
            # Also set on failure, so that the frontend can show e.g. "Fisheye
            # calibration failed". On success, the model of the calibration that
            # was actually computed is already in there.
            summary.setdefault("model", self._config.calibration_model)
            self.last_result = {"error": int(error), **summary}
        except Exception:
            _log.exception("Auswertung unerwartet fehlgeschlagen")
            self.last_result = {
                "error": int(VisionErrorCode.DETECTION_FAILED),
                "message": "Auswertung unerwartet fehlgeschlagen, siehe Server-Log",
                "model": self._config.calibration_model,
            }
        finally:
            self._processing = False
            self._processing_done.set()

    async def _compute_and_save(self) -> tuple[VisionErrorCode, dict]:
        """Compute from the samples detected in `_process()` and save them.

        Always cleans up, also on failure, so a session never stays unfinished.
        """
        samples = list(self._samples)
        image_size = self._image_size
        await self._stop()

        if len(samples) < MIN_SAMPLES_FOR_CALIBRATION:
            return VisionErrorCode.DETECTION_FAILED, {
                "message": f"Zu wenige verwertbare Aufnahmen: {len(samples)}",
                "samples": len(samples),
            }

        spec = self._spec()
        board = self._build_board(spec)
        try:
            calibration = await self._run_blocking(
                self._calibrate_from_samples,
                samples,
                image_size,
                spec,
                board,
                frame_id=self._config.frame_id,
                model=self._config.calibration_model,
            )
        except Exception as error:
            # Not only ValueError: cv2.calibrateCamera fails with `cv2.error` on
            # numerically unsuitable captures (e.g. too little tilt or distance
            # variation despite good image coverage), not with a ValueError.
            # `asyncio.CancelledError` is not caught here, because it inherits from
            # `BaseException`, not `Exception`.
            _log.exception("Kalibrierung fehlgeschlagen (%d Aufnahmen)", len(samples))
            return VisionErrorCode.DETECTION_FAILED, {"message": str(error)}

        coverage = self._compute_coverage(samples, image_size)
        # Assign the ID with its timestamp here already, not only when writing.
        # Otherwise detection and the live-stream overlay would get the object with
        # an empty ID, and the overlay would show no date for the new calibration
        # until a restart. The file and the running server then carry the same ID.
        if dataclasses.is_dataclass(calibration) and not getattr(calibration, "calibration_id", ""):
            stamp = datetime.now(timezone.utc).isoformat()
            calibration = dataclasses.replace(
                calibration,
                calibration_id=f"{calibration.frame_id or self._out_path.stem}@{stamp}",
            )
        backup = await self._run_blocking(self._backup_existing)
        await self._run_blocking(self._save_calibration, self._out_path, calibration)
        if self._on_calibrated is not None:
            outcome = self._on_calibrated(calibration)
            if inspect.isawaitable(outcome):
                await outcome
        rms = round(calibration.rms_reprojection_error, 4)
        summary = {
            "rms": rms,
            "model": calibration.model,
            "samples": calibration.sample_count,
            "coverageX": round(coverage[0], 3),
            "coverageY": round(coverage[1], 3),
            "path": str(self._out_path),
        }
        if backup is not None:
            summary["backup"] = str(backup)
        if rms > RMS_WARNING_PX:
            summary["warning"] = (
                f"RMS {rms} px ueber dem Zielwert {RMS_WARNING_PX} px -- "
                "vermutlich zu wenig Neigung/Distanz-Variation beim Aufnehmen "
                "(eine Abdeckung, die nur die Position im Bild misst, sagt "
                "nichts ueber die Vielfalt der Posen). Kalibrierung ist "
                "trotzdem gespeichert."
            )
        _log.info(
            "Kalibrierung gespeichert (%s): RMS %.4f px, %d Aufnahmen, "
            "Abdeckung x %.0f%% y %.0f%%",
            summary["model"],
            summary["rms"],
            summary["samples"],
            coverage[0] * 100,
            coverage[1] * 100,
        )
        return VisionErrorCode.OK, summary

    async def abort(self) -> None:
        """Stop without saving.

        Cannot be called while a background evaluation is running (see `busy`).
        `runner/calibration.py` checks this before the call.
        """
        await self._stop()


__all__ = ["CalibrationSession"]
