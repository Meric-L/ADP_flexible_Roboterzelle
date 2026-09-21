"""Interaktive Kalibrierung gegen die geteilte Kamera, frontend-gesteuert.

Der bisherige Weg (`tagloc.cli.calibrate`) oeffnet die Kamera selbst und
exklusiv -- der Server muss dafuer gestoppt sein. Diese Klasse tut das
Gegenteil: sie liest nur aus der bereits laufenden `SharedCamera` mit, genau
wie `AprilTagDetectionSource` und der Livestream. Damit kann ein Operator per
Frontend kalibrieren, waehrend Server und Livestream weiterlaufen.

Automatisches Erfassen statt eines Buttons pro Aufnahme: sobald das Board
erkannt wird und seit der letzten Aufnahme `capture_interval_s` vergangen
sind, wird ein neuer Sample genommen. Kein Bewegungsabgleich -- ein Operator,
der das Board sichtbar bewegt, erzeugt von selbst unterschiedliche Posen.
"""

import asyncio
import contextlib
import importlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .camera import SharedCamera
from .errors import VisionErrorCode
from .profiles import AprilTagProfileConfig

_log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.05


def _lazy(module: str, name: str) -> Callable:
    """Loest eine `tagloc`-Funktion erst beim Aufruf auf -- diese Datei
    importiert `tagloc`/`cv2` nie auf Modulebene."""

    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    return call


class CalibrationSession:
    """Sammelt Board-Samples aus der geteilten Kamera, bis `finish()` rechnet.

    Wiederverwendbar: `start()` setzt den internen Zustand vollstaendig
    zurueck, `runner.py` haelt darum eine einzige Instanz ueber mehrere
    Start/Finish-Zyklen.

    Die `tagloc`-Funktionen (`detect_board`, `build_board`,
    `calibrate_from_samples`, `compute_coverage`, `save_calibration`) sind
    injizierbar -- Tests laufen damit ohne `cv2`, ohne Kamera und ohne
    Kalibrierdatei, nach demselben Muster wie `AprilTagDetectionSource`s
    `detector`/`calibration`/`tag_map`.
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
    ) -> None:
        self._camera = camera
        self._config = config
        self._out_path = config.calibration_path
        self._detect_board = detect_board or _lazy("tagloc.boards", "detect_board")
        self._build_board = build_board or _lazy("tagloc.boards", "build_board")
        self._calibrate_from_samples = calibrate_from_samples or _lazy(
            "tagloc.boards", "calibrate_from_samples"
        )
        self._compute_coverage = compute_coverage or _lazy("tagloc.boards", "compute_coverage")
        self._save_calibration = save_calibration or _lazy(
            "tagloc.calibration", "save_calibration"
        )
        self._board_spec: Any = None  # lazy: braucht cv2, siehe `_spec()`
        self._samples: list = []
        self._image_size: tuple[int, int] = (0, 0)
        self._last_seen_timestamp: float | None = None
        self._last_capture_monotonic: float = 0.0
        self._executor: ThreadPoolExecutor | None = None
        self._task: asyncio.Task | None = None
        self.running = False

    def _spec(self):
        """Baut `BoardSpec` aus den Skalaren der Config. Importiert `tagloc`
        erst hier, damit `profiles.py` frei von der Abhaengigkeit bleibt."""
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
        """Eigener Ein-Worker-Pool fuer `detect_board`/`calibrate_from_samples`
        -- nie auf dem Event-Loop, gleiches Muster wie
        `DetectionSource.run_blocking`."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="vision-calibration"
            )
        return self._executor

    async def _run_blocking(self, func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool(), lambda: func(*args, **kwargs))

    def start(self) -> None:
        """Setzt Samples zurueck und startet den Erfassungs-Loop."""
        self._samples = []
        self._image_size = (0, 0)
        self._last_seen_timestamp = None
        self._last_capture_monotonic = 0.0
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        from tagloc import frames as frame_tools

        spec = self._spec()
        board = self._build_board(spec)  # None fuer chessboard
        try:
            while True:
                frame = self._camera.latest_frame
                if frame is not None and frame.timestamp != self._last_seen_timestamp:
                    self._last_seen_timestamp = frame.timestamp
                    self._image_size = frame_tools.image_size(frame.image)
                    now = time.monotonic()
                    due = (
                        now - self._last_capture_monotonic
                        >= self._config.calibration_capture_interval_s
                    )
                    if due:
                        sample = await self._run_blocking(
                            self._detect_board, frame_tools.to_gray(frame.image), spec, board
                        )
                        if sample is not None:
                            self._samples.append(sample)
                            self._last_capture_monotonic = now
                            _log.info(
                                "Kalibrierung: Aufnahme %d (%d Ecken)",
                                len(self._samples),
                                sample.count(),
                            )
                await asyncio.sleep(POLL_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("Kalibrier-Session abgebrochen")

    @property
    def progress(self) -> dict:
        """JSON-faehiges Fortschritts-Objekt fuer `CalibrationProgress`."""
        coverage = (
            self._compute_coverage(self._samples, self._image_size)
            if self._samples
            else (0.0, 0.0)
        )
        return {
            "running": self.running,
            "samples": len(self._samples),
            "minSamples": self._config.calibration_min_samples,
            "coverageX": round(coverage[0], 3),
            "coverageY": round(coverage[1], 3),
        }

    async def _stop_loop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    async def finish(self) -> tuple[VisionErrorCode, dict]:
        """Stoppt den Loop, rechnet und speichert. Immer aufraeumend, auch
        bei Fehlschlag -- eine Session bleibt nie unbeendet haengen."""
        samples = list(self._samples)
        image_size = self._image_size
        await self._stop_loop()

        if len(samples) < 3:
            return VisionErrorCode.DETECTION_FAILED, {
                "message": f"Zu wenige Aufnahmen: {len(samples)}",
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
            )
        except ValueError as error:
            return VisionErrorCode.DETECTION_FAILED, {"message": str(error)}

        coverage = self._compute_coverage(samples, image_size)
        await self._run_blocking(self._save_calibration, self._out_path, calibration)
        summary = {
            "rms": round(calibration.rms_reprojection_error, 4),
            "samples": calibration.sample_count,
            "coverageX": round(coverage[0], 3),
            "coverageY": round(coverage[1], 3),
            "path": str(self._out_path),
        }
        _log.info(
            "Kalibrierung gespeichert: RMS %.4f px, %d Aufnahmen, Abdeckung x %.0f%% y %.0f%%",
            summary["rms"],
            summary["samples"],
            coverage[0] * 100,
            coverage[1] * 100,
        )
        return VisionErrorCode.OK, summary

    async def abort(self) -> None:
        """Stoppt ohne zu speichern."""
        await self._stop_loop()


__all__ = ["CalibrationSession"]
