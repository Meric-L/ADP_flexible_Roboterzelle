"""Camera calibration run remotely: the operator holds the board, the frontend guides.

`tagloc.cli.calibrate` needs someone at a keyboard next to the camera. The Pis
have no screen, so so far that meant X forwarding or a two-step detour via
image files (doc/apriltag-e2e-test.md 3.3). This session runs the same
calculation core (`tagloc.boards`) inside the vision server:

* frames come from the `SharedCamera` that the livestream and the AprilTag
  job already use. The calibration therefore has, by construction, exactly
  the resolution the job sees -- a CLI calibration at another resolution is
  rejected by `check_resolution` at the first job
* views are taken automatically once the board is held still in a new
  position (`tagloc.calibration_guide`), or on request
* progress and the next instruction for the operator are published as JSON
  in a status node; the frontend only displays them
* the result is shown before it is saved. Only `Save` overwrites the
  calibration file (after a backup) and hands it to the AprilTag source

Deliberately outside the OPC 40100 state machines: the standard has no
calibration mode, and a calibration must also be possible while the system
sits in Preoperational -- which is exactly the state without a calibration.
"""

import asyncio
import contextlib
import functools
import json
import logging
import math
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tagloc.boards import CHARUCO, CHESSBOARD, BoardSample, BoardSpec
from tagloc.calibration_guide import (
    GRID_CELLS,
    ViewParams,
    coverage_grid,
    is_novel,
    is_still,
    next_hint,
    progress,
    rate_calibration,
    view_params,
)

from .errors import VisionErrorCode

_log = logging.getLogger(__name__)

SCHEMA = "wsc.vision.calibration-status/1"

IDLE = "idle"
COLLECTING = "collecting"
COMPUTING = "computing"
REVIEW = "review"
SAVING = "saving"
DONE = "done"

#: Same lower bound as `tagloc.cli.calibrate`.
MIN_SAMPLES = 15
DEFAULT_TARGET_SAMPLES = 20
#: `calibrateCamera` grows with every view; on the Pi, more than this takes
#: long without improving anything.
MAX_SAMPLES = 60

#: Minimum gap between two automatic captures -- one hold, one view.
CAPTURE_INTERVAL_S = 1.0
#: Pause after each analysed frame, so the analysis doesn't saturate the Pi.
ANALYSIS_PAUSE_S = 0.1
FRAME_POLL_S = 0.05
#: An analysis older than this no longer describes what the camera sees.
ANALYSIS_FRESH_S = 1.5
#: Without a new frame for this long, the camera counts as not delivering.
CAMERA_STALE_S = 3.0
#: How long the stream shows the green "captured" frame after a capture.
CAPTURE_FLASH_S = 0.6
#: Minimum gap between two status writes while waiting for frames.
IDLE_PUBLISH_INTERVAL_S = 0.5

_BOARD_TYPES = (CHESSBOARD, CHARUCO)
_MIN_GRID = 3
_MAX_GRID = 40
_MIN_SQUARE_M = 0.002
_MAX_SQUARE_M = 0.5


class CalibrationInputError(ValueError):
    """The settings sent with `Start` are unusable. The message goes to the operator."""


@dataclass(frozen=True)
class CalibrationSettings:
    """What the operator chose for one calibration run."""

    board: BoardSpec
    target_samples: int = DEFAULT_TARGET_SAMPLES
    auto_capture: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "board": self.board.as_dict(),
            "targetSamples": self.target_samples,
            "autoCapture": self.auto_capture,
        }


def _integer(data: dict, key: str, low: int, high: int, label: str, default=None) -> int:
    value = data.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise CalibrationInputError(f"{label} fehlt oder ist keine Zahl.")
    if float(value) != int(value):
        raise CalibrationInputError(f"{label} muss eine ganze Zahl sein.")
    if not low <= int(value) <= high:
        raise CalibrationInputError(f"{label} muss zwischen {low} und {high} liegen.")
    return int(value)


def _length(data: dict, key: str, label: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CalibrationInputError(f"{label} fehlt oder ist keine Zahl.")
    if not _MIN_SQUARE_M <= float(value) <= _MAX_SQUARE_M:
        raise CalibrationInputError(
            f"{label} muss zwischen {_MIN_SQUARE_M * 1000:.0f} und "
            f"{_MAX_SQUARE_M * 1000:.0f} mm liegen (Angabe in Metern)."
        )
    return float(value)


def parse_settings(text: str, *, min_samples: int = MIN_SAMPLES) -> CalibrationSettings:
    """Read and check the JSON settings of `Start`.

    Keys as in the calibration file's `board` record (`BoardSpec.as_dict`),
    so what the frontend sends is what ends up as provenance in the file.
    `cols`/`rows` are **inner corners** for a chessboard and **squares** for
    ChArUco -- the same convention as `tagloc.cli.calibrate`.
    """
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError) as error:
        raise CalibrationInputError(f"Einstellungen sind kein gültiges JSON: {error}") from None
    if not isinstance(data, dict):
        raise CalibrationInputError("Einstellungen müssen ein JSON-Objekt sein.")
    board = data.get("board")
    if not isinstance(board, dict):
        raise CalibrationInputError("Einstellungen ohne 'board'.")

    board_type = board.get("type")
    if board_type not in _BOARD_TYPES:
        raise CalibrationInputError(
            f"Unbekannter Board-Typ '{board_type}' (erlaubt: {', '.join(_BOARD_TYPES)})."
        )
    unit = "Innere Ecken" if board_type == CHESSBOARD else "Felder"
    cols = _integer(board, "cols", _MIN_GRID, _MAX_GRID, f"{unit} horizontal")
    rows = _integer(board, "rows", _MIN_GRID, _MAX_GRID, f"{unit} vertikal")
    square = _length(board, "squareSizeM", "Feldgröße")

    spec_args: dict[str, Any] = {
        "type": board_type,
        "cols": cols,
        "rows": rows,
        "square_size_m": square,
    }
    if board_type == CHARUCO:
        marker = _length(board, "markerSizeM", "Markergröße")
        if marker >= square:
            raise CalibrationInputError("Die Markergröße muss kleiner als die Feldgröße sein.")
        dictionary = board.get("dictionary", "DICT_4X4_50")
        if not isinstance(dictionary, str) or not dictionary.startswith("DICT_"):
            raise CalibrationInputError(f"Unbekanntes Marker-Dictionary '{dictionary}'.")
        spec_args["marker_size_m"] = marker
        spec_args["dictionary"] = dictionary

    target = _integer(
        data, "targetSamples", min_samples, MAX_SAMPLES, "Anzahl Aufnahmen", DEFAULT_TARGET_SAMPLES
    )
    auto_capture = data.get("autoCapture", True)
    if not isinstance(auto_capture, bool):
        raise CalibrationInputError("'autoCapture' muss true oder false sein.")
    return CalibrationSettings(
        board=BoardSpec(**spec_args), target_samples=target, auto_capture=auto_capture
    )


@dataclass(frozen=True)
class _Analysis:
    """What one analysed frame showed."""

    image_size: tuple[int, int]
    sample: BoardSample | None
    params: ViewParams | None
    analysed_at: float = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_ASCII = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
     "„": '"', "“": '"', "–": "-", "°": " Grad"}
)


def _ascii(text: str) -> str:
    """`cv2.putText` only knows ASCII; umlauts would come out as question marks."""
    return text.translate(_ASCII)


def read_calibration_summary(path: Path) -> dict[str, Any]:
    """Summarise the calibration file on disk for the frontend. Never raises.

    Reads the JSON directly instead of via `load_calibration`: numpy is not
    needed to show which calibration is active, and `createdAt` is not part
    of `CameraCalibration`.
    """
    path = Path(path)
    if not path.is_file():
        return {"exists": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "exists": True,
            "calibrationId": data.get("calibrationId"),
            "frameId": data.get("frameId"),
            "imageSize": data.get("imageSize"),
            "rmsReprojectionError": data.get("rmsReprojectionError"),
            "sampleCount": data.get("sampleCount"),
            "board": data.get("board") or None,
            "createdAt": data.get("createdAt"),
        }
    except Exception as error:  # a broken file is a finding, not a crash
        return {"exists": True, "error": f"nicht lesbar: {error}"}


class CalibrationSession:
    """One remote calibration at a time, driven by five commands.

    Every command returns `(VisionErrorCode, message)` and is serialised by a
    lock -- two frontends pressing buttons at once must not interleave.
    Computing and saving run as tasks; their outcome shows up in the status.
    """

    def __init__(
        self,
        camera: Any,
        *,
        calibration_path: Path,
        frame_id: str,
        publish: Callable[[str], Awaitable[None]],
        on_saved: Callable[[], Awaitable[tuple[bool, str]]] | None = None,
        mode_node: Any = None,
        min_samples: int = MIN_SAMPLES,
        capture_interval_s: float = CAPTURE_INTERVAL_S,
        analysis_pause_s: float = ANALYSIS_PAUSE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._camera = camera
        self._path = Path(calibration_path)
        self._frame_id = frame_id
        self._publish_text = publish
        self._on_saved = on_saved
        #: Writable `CameraStreamMode` node: the session switches the stream to
        #: its own overlay while collecting and restores the previous mode.
        self._mode_node = mode_node
        self._min_samples = int(min_samples)
        self._capture_interval_s = float(capture_interval_s)
        self._analysis_pause_s = float(analysis_pause_s)
        self._clock = clock

        self._commands = asyncio.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._loop_task: asyncio.Task | None = None
        self._work_task: asyncio.Task | None = None

        self._state = IDLE
        self._counter = 0
        self._session_id = ""
        self._settings: CalibrationSettings | None = None
        self._board: Any = None
        self._error: str | None = None
        self._current: dict[str, Any] = {"exists": False}
        self._previous_mode: str | None = None
        self._last_published: str | None = None
        self._last_publish_at = -math.inf
        self._reset_collection()

    # -- Public state ---------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def collecting(self) -> bool:
        return self._state == COLLECTING

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    # -- Commands -------------------------------------------------------------

    async def start(self, settings_json: str) -> tuple[VisionErrorCode, str]:
        """Begin collecting views with the given board."""
        async with self._commands:
            if self._state in (COLLECTING, COMPUTING, REVIEW, SAVING):
                return (
                    VisionErrorCode.INVALID_STATE,
                    "Es läuft bereits eine Kalibrierung. Erst abbrechen.",
                )
            try:
                settings = parse_settings(settings_json, min_samples=self._min_samples)
            except CalibrationInputError as error:
                return VisionErrorCode.INVALID_ARGUMENT, str(error)
            try:
                from tagloc.boards import build_board

                board = await self._run(build_board, settings.board)
            except Exception as error:
                return VisionErrorCode.INVALID_ARGUMENT, f"Board nicht erzeugbar: {error}"

            self._reset_collection()
            self._counter += 1
            self._session_id = f"cal-{self._counter:04d}"
            self._settings = settings
            self._board = board
            self._error = None
            self._current = await self._run(read_calibration_summary, self._path)
            self._state = COLLECTING
            await self._enter_stream_mode()
            self._loop_task = asyncio.create_task(self._collect_loop())
            await self.publish()
            _log.info(
                "Kalibrierung %s gestartet: %s, Ziel %d Aufnahmen",
                self._session_id,
                settings.board.as_dict(),
                settings.target_samples,
            )
            return VisionErrorCode.OK, f"Kalibrierung {self._session_id} gestartet."

    async def capture(self) -> tuple[VisionErrorCode, str]:
        """Take the current view, whether or not it is new."""
        async with self._commands:
            if self._state != COLLECTING:
                return VisionErrorCode.INVALID_STATE, "Gerade werden keine Aufnahmen gesammelt."
            if len(self._samples) >= MAX_SAMPLES:
                return VisionErrorCode.INVALID_STATE, f"Maximal {MAX_SAMPLES} Aufnahmen."
            analysis = self._fresh_analysis()
            if analysis is None or analysis.sample is None:
                return VisionErrorCode.DETECTION_FAILED, "Board im aktuellen Bild nicht erkannt."
            if analysis.params is None:
                return (
                    VisionErrorCode.DETECTION_FAILED,
                    "Board-Umriss nicht bestimmbar. Board weiter ins Bild halten.",
                )
            duplicate = not is_novel(analysis.params, self._params)
            if not self._add_sample(analysis, "manual"):
                return VisionErrorCode.INVALID_STATE, self._error or "Aufnahme verworfen."
            await self.publish()
            message = f"Aufnahme {len(self._samples)} gespeichert."
            if duplicate:
                message += " Sie ähnelt einer früheren Ansicht."
            return VisionErrorCode.OK, message

    async def compute(self) -> tuple[VisionErrorCode, str]:
        """Stop collecting and compute the calibration in the background."""
        async with self._commands:
            if self._state != COLLECTING:
                return VisionErrorCode.INVALID_STATE, "Gerade werden keine Aufnahmen gesammelt."
            if len(self._samples) < self._min_samples:
                return (
                    VisionErrorCode.INVALID_STATE,
                    f"Erst {len(self._samples)} von mindestens {self._min_samples} Aufnahmen.",
                )
            await self._stop_loop()
            await self._leave_stream_mode()
            self._state = COMPUTING
            self._error = None
            await self.publish()
            self._work_task = asyncio.create_task(self._compute())
            return VisionErrorCode.OK, "Berechnung gestartet."

    async def save(self) -> tuple[VisionErrorCode, str]:
        """Write the reviewed calibration and hand it to the detection source."""
        async with self._commands:
            if self._state != REVIEW or self._calibration is None:
                return VisionErrorCode.INVALID_STATE, "Nichts zu speichern. Erst berechnen."
            self._state = SAVING
            self._error = None
            await self.publish()
            self._work_task = asyncio.create_task(self._save())
            return VisionErrorCode.OK, "Speichern gestartet."

    async def cancel(self) -> tuple[VisionErrorCode, str]:
        """Discard the running calibration. Idempotent."""
        async with self._commands:
            if self._state == SAVING:
                # The write runs in a thread and cannot be interrupted halfway.
                return VisionErrorCode.BUSY, "Speichern läuft, bitte warten."
            if self._state == IDLE:
                return VisionErrorCode.OK, "Keine Kalibrierung aktiv."
            await self._stop_loop()
            await self._stop_work()
            # Once more: a failing `_compute` restarts the collect loop as it
            # dies, which could otherwise outlive this cancel.
            await self._stop_loop()
            await self._leave_stream_mode()
            self._reset_collection()
            self._settings = None
            self._board = None
            self._error = None
            self._state = IDLE
            # `current` is not re-read here: only `Save` changes that file, and
            # the worker may still be busy with the computation just cancelled.
            await self.publish()
            return VisionErrorCode.OK, "Kalibrierung verworfen."

    async def close(self) -> None:
        """Stop everything on server shutdown. Idempotent."""
        await self._stop_loop()
        await self._stop_work()
        await self._stop_loop()
        await self._leave_stream_mode()
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    # -- Collecting -----------------------------------------------------------

    def _reset_collection(self) -> None:
        self._samples: list[BoardSample] = []
        self._params: list[ViewParams] = []
        self._image_size: tuple[int, int] | None = None
        self._analysis: _Analysis | None = None
        self._still = False
        self._novel = False
        self._last_capture_at = -math.inf
        self._last_capture: dict[str, Any] | None = None
        self._last_frame_at: float | None = None
        self._grid: tuple[tuple[int, ...], ...] = tuple(
            (0,) * GRID_CELLS for _ in range(GRID_CELLS)
        )
        self._coverage: tuple[float, float] = (0.0, 0.0)
        self._progress: dict[str, float] = progress([])
        self._calibration: Any = None
        self._result: dict[str, Any] | None = None
        self._saved: dict[str, Any] | None = None

    async def _collect_loop(self) -> None:
        seen: float | None = None
        while self._state == COLLECTING:
            frame = self._camera.latest_frame if self._camera is not None else None
            if frame is None or frame.timestamp == seen:
                if self._clock() - self._last_publish_at >= IDLE_PUBLISH_INTERVAL_S:
                    await self.publish()
                await asyncio.sleep(FRAME_POLL_S)
                continue
            seen = frame.timestamp
            self._last_frame_at = self._clock()
            try:
                analysis = await self._run(self._analyse, frame.image)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("Kalibrierbild konnte nicht ausgewertet werden")
                analysis = None
            self._apply(analysis)
            await self.publish()
            await asyncio.sleep(self._analysis_pause_s)

    def _analyse(self, image: Any) -> _Analysis:
        """Runs in the worker thread: find the board and describe the view."""
        from tagloc import frames as frame_tools
        from tagloc.boards import detect_board

        spec = self._settings.board
        size = frame_tools.image_size(image)
        sample = detect_board(frame_tools.to_gray(image), spec, self._board)
        params = view_params(sample, spec, size) if sample is not None else None
        return _Analysis(image_size=size, sample=sample, params=params)

    def _apply(self, analysis: _Analysis | None) -> None:
        """Runs on the loop: judge the analysed frame and capture it if it earns it."""
        now = self._clock()
        settings = self._settings
        previous = self._analysis
        if analysis is None or settings is None:
            self._analysis = None
            self._still = self._novel = False
            return
        analysis = _Analysis(analysis.image_size, analysis.sample, analysis.params, now)
        self._analysis = analysis
        if analysis.sample is None or analysis.params is None:
            self._still = self._novel = False
            return

        previous_sample = (
            previous.sample
            if previous is not None and now - previous.analysed_at <= ANALYSIS_FRESH_S
            else None
        )
        self._still = is_still(
            analysis.sample, previous_sample, settings.board, analysis.image_size
        )
        self._novel = is_novel(analysis.params, self._params)
        if (
            settings.auto_capture
            and self._still
            and self._novel
            and len(self._samples) < settings.target_samples
            and now - self._last_capture_at >= self._capture_interval_s
            and self._add_sample(analysis, "auto")
        ):
            self._novel = False

    def _fresh_analysis(self) -> _Analysis | None:
        analysis = self._analysis
        if analysis is None or self._clock() - analysis.analysed_at > ANALYSIS_FRESH_S:
            return None
        return analysis

    def _add_sample(self, analysis: _Analysis, reason: str) -> bool:
        if self._image_size is None:
            self._image_size = analysis.image_size
        elif tuple(analysis.image_size) != tuple(self._image_size):
            self._error = (
                f"Bildgröße hat sich geändert ({self._image_size[0]}x{self._image_size[1]} -> "
                f"{analysis.image_size[0]}x{analysis.image_size[1]}). Bitte neu starten."
            )
            return False
        self._samples.append(analysis.sample)
        self._params.append(analysis.params)
        self._last_capture_at = self._clock()
        self._last_capture = {"index": len(self._samples), "reason": reason, "at": _now_iso()}
        from tagloc.boards import compute_coverage

        self._grid = tuple(tuple(row) for row in coverage_grid(self._samples, self._image_size))
        self._coverage = compute_coverage(self._samples, self._image_size)
        self._progress = progress(self._params)
        return True

    # -- Computing and saving ---------------------------------------------------

    async def _compute(self) -> None:
        from tagloc.boards import calibrate_from_samples

        settings = self._settings
        try:
            started = self._clock()
            calibration = await self._run(
                functools.partial(
                    calibrate_from_samples,
                    list(self._samples),
                    self._image_size,
                    settings.board,
                    self._board,
                    frame_id=self._frame_id,
                )
            )
            rms = float(calibration.rms_reprojection_error)
            quality, notes = rate_calibration(rms, self._coverage, self._progress)
            fx, fy, cx, cy = calibration.camera_params
            self._calibration = calibration
            self._result = {
                "rmsReprojectionError": round(rms, 4),
                "sampleCount": calibration.sample_count,
                "imageSize": list(calibration.image_size),
                "coverage": [round(value, 3) for value in self._coverage],
                "progress": dict(self._progress),
                "quality": quality,
                "notes": notes,
                "focalLengthPx": [round(fx, 2), round(fy, 2)],
                "principalPointPx": [round(cx, 2), round(cy, 2)],
                "distortion": [round(float(value), 6) for value in calibration.distortion],
                "durationS": round(self._clock() - started, 2),
            }
            self._state = REVIEW
            _log.info("Kalibrierung %s berechnet: RMS %.3f px (%s)", self._session_id, rms, quality)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log.exception("Kalibrierung %s: Berechnung fehlgeschlagen", self._session_id)
            # Back to collecting: more or better views are the usual remedy.
            self._error = f"Berechnung fehlgeschlagen: {error}"
            self._state = COLLECTING
            await self._enter_stream_mode()
            self._loop_task = asyncio.create_task(self._collect_loop())
        await self.publish()

    def _write_file(self, calibration: Any) -> str | None:
        """Runs in the worker thread: back up the old file, write the new one atomically."""
        from tagloc.calibration import save_calibration

        temporary = self._path.with_name(f"{self._path.name}.tmp")
        save_calibration(temporary, calibration)
        backup: Path | None = None
        if self._path.is_file():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            # No `.json` suffix, so nothing mistakes a backup for a calibration.
            backup = self._path.with_name(f"{self._path.name}.bak-{stamp}")
            shutil.copy2(self._path, backup)
        os.replace(temporary, self._path)
        return str(backup) if backup is not None else None

    async def _save(self) -> None:
        try:
            backup = await self._run(self._write_file, self._calibration)
            applied, message = (True, "")
            if self._on_saved is not None:
                applied, message = await self._on_saved()
            self._saved = {
                "path": str(self._path),
                "backupPath": backup,
                "applied": applied,
                "message": message,
                "at": _now_iso(),
            }
            self._current = await self._run(read_calibration_summary, self._path)
            self._state = DONE
            _log.info("Kalibrierung %s gespeichert unter %s", self._session_id, self._path)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log.exception("Kalibrierung %s: Speichern fehlgeschlagen", self._session_id)
            self._error = f"Speichern fehlgeschlagen: {error}"
            self._state = REVIEW
        await self.publish()

    # -- Status -------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """The status document, as the frontend receives it (without `updatedAt`)."""
        settings = self._settings
        analysis = self._fresh_analysis() if self._state == COLLECTING else None
        visible = analysis is not None and analysis.sample is not None
        camera_ok = (
            self._last_frame_at is not None
            and self._clock() - self._last_frame_at <= CAMERA_STALE_S
        )
        hint = None
        if self._state == COLLECTING and settings is not None:
            hint = next_hint(
                camera_ok=camera_ok,
                board_visible=visible,
                board_still=visible and self._still,
                novel=visible and self._novel,
                sample_count=len(self._samples),
                target_samples=settings.target_samples,
                grid=self._grid,
                covered=self._progress,
                board_type=settings.board.type,
                auto_capture=settings.auto_capture,
            )
        size = self._image_size or (analysis.image_size if analysis is not None else None)
        return {
            "schema": SCHEMA,
            "state": self._state,
            "sessionId": self._session_id or None,
            "frameId": self._frame_id,
            "calibrationPath": str(self._path),
            "minSamples": self._min_samples,
            "maxSamples": MAX_SAMPLES,
            "settings": settings.as_dict() if settings is not None else None,
            "imageSize": list(size) if size is not None else None,
            "sampleCount": len(self._samples),
            "cameraOk": camera_ok,
            "boardVisible": visible,
            "boardStill": visible and self._still,
            "cornerCount": analysis.sample.count() if visible else 0,
            "progress": dict(self._progress),
            "coverage": [round(value, 3) for value in self._coverage],
            "grid": [list(row) for row in self._grid],
            "hint": {"code": hint.code, "text": hint.text} if hint is not None else None,
            "lastCapture": self._last_capture,
            "result": self._result,
            "saved": self._saved,
            "current": self._current,
            "error": self._error,
        }

    async def publish(self) -> None:
        """Write the status if it changed. A failing write never stops the session."""
        document = self.status()
        text = json.dumps(document, ensure_ascii=False, sort_keys=True)
        self._last_publish_at = self._clock()
        if text == self._last_published:
            return
        self._last_published = text
        document["updatedAt"] = _now_iso()
        try:
            await self._publish_text(json.dumps(document, ensure_ascii=False))
        except Exception:
            _log.exception("Kalibrierstatus konnte nicht geschrieben werden")

    async def refresh_current(self) -> None:
        """Re-read the calibration file on disk and publish, e.g. at server start."""
        self._current = await self._run(read_calibration_summary, self._path)
        await self.publish()

    # -- Livestream -----------------------------------------------------------------

    def annotate(self, image: Any) -> Any:
        """Draw the session's view of the frame. Runs in the publisher's worker thread.

        Works on a copy and never raises -- the stream matters more than the
        markup. Detection is not repeated here; the last analysis is drawn.
        """
        try:
            import cv2
            import numpy as np

            from tagloc.overlay import draw_board_overlay, draw_status_bar

            canvas = image.copy()
            height, width = canvas.shape[:2]
            grid = self._grid
            cells = len(grid)
            tint = canvas.copy()
            for row in range(cells):
                for col in range(cells):
                    if grid[row][col] > 0:
                        top_left = (col * width // cells, row * height // cells)
                        bottom_right = ((col + 1) * width // cells, (row + 1) * height // cells)
                        cv2.rectangle(tint, top_left, bottom_right, (80, 200, 80), -1)
            cv2.addWeighted(tint, 0.18, canvas, 0.82, 0.0, dst=canvas)
            for index in range(1, cells):
                cv2.line(canvas, (index * width // cells, 0), (index * width // cells, height),
                         (200, 200, 200), 1)
                cv2.line(canvas, (0, index * height // cells), (width, index * height // cells),
                         (200, 200, 200), 1)

            analysis = self._fresh_analysis()
            draw_board_overlay(
                canvas,
                analysis.sample if analysis is not None else None,
                coverage=self._coverage,
            )
            if self._clock() - self._last_capture_at <= CAPTURE_FLASH_S:
                cv2.rectangle(canvas, (0, 0), (width - 1, height - 1), (80, 220, 80), 12)
            target = self._settings.target_samples if self._settings is not None else 0
            hint = self.status().get("hint") or {}
            draw_status_bar(
                canvas,
                [
                    f"Kalibrierung: {len(self._samples)}/{target} Aufnahmen",
                    _ascii(str(hint.get("text", ""))),
                ],
            )
            return np.ascontiguousarray(canvas)
        except Exception:
            _log.exception("Kalibrier-Overlay fehlgeschlagen, sende unmarkiertes Bild")
            return image

    # -- Plumbing -----------------------------------------------------------------------

    async def _run(self, func, /, *args) -> Any:
        """Blocking work in the session's own worker thread, never on the loop."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="vision-calibration"
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(func, *args))

    async def _stop_loop(self) -> None:
        task, self._loop_task = self._loop_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _stop_work(self) -> None:
        task, self._work_task = self._work_task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _enter_stream_mode(self) -> None:
        if self._mode_node is None:
            return
        try:
            if self._previous_mode is None:
                self._previous_mode = str(await self._mode_node.read_value() or "")
            await self._mode_node.write_value("calibration")
        except Exception:
            _log.exception("Livestream konnte nicht auf den Kalibriermodus gestellt werden")

    async def _leave_stream_mode(self) -> None:
        previous, self._previous_mode = self._previous_mode, None
        if self._mode_node is None or previous is None:
            return
        try:
            # Only undo our own switch; if someone chose another mode since, keep it.
            if str(await self._mode_node.read_value()) == "calibration":
                await self._mode_node.write_value(previous or "apriltag")
        except Exception:
            _log.exception("Livestream-Modus konnte nicht zurueckgesetzt werden")


class StreamAnnotator:
    """Chooses what the livestream draws.

    A collecting calibration session wins in calibration mode -- the board it
    looks for is the one just entered, not the one from the old calibration
    file. Otherwise the AprilTag annotator draws, which only exists once a
    calibration does; until then the stream stays unmarked.
    """

    def __init__(self, base: Any = None, session: CalibrationSession | None = None) -> None:
        self.base = base
        self.session = session

    def annotate(self, image: Any, mode: str) -> Any:
        session = self.session
        if session is not None and session.collecting and mode == "calibration":
            return session.annotate(image)
        if self.base is not None:
            return self.base.annotate(image, mode)
        return image


__all__ = [
    "COLLECTING",
    "COMPUTING",
    "DONE",
    "IDLE",
    "MIN_SAMPLES",
    "REVIEW",
    "SAVING",
    "SCHEMA",
    "CalibrationInputError",
    "CalibrationSession",
    "CalibrationSettings",
    "StreamAnnotator",
    "parse_settings",
    "read_calibration_summary",
]
