"""Interaktive Kalibrierung gegen die geteilte Kamera, frontend-gesteuert.

Der bisherige Weg (`tagloc.cli.calibrate`) oeffnet die Kamera selbst und
exklusiv -- der Server muss dafuer gestoppt sein. Diese Klasse tut das
Gegenteil: sie liest nur aus der bereits laufenden `SharedCamera` mit, genau
wie `AprilTagDetectionSource` und der Livestream. Damit kann ein Operator per
Frontend kalibrieren, waehrend Server und Livestream weiterlaufen.

Aufnahmen werden manuell ausgeloest (`capture()`, z. B. per Leertaste im
Stream-Viewer oder ein Frontend-Button) statt automatisch nach Zeitintervall
-- der Operator sieht das Live-Bild und entscheidet selbst, wann eine Pose
gut ist, statt dass die Kamera im Sekundentakt mitschreibt.
"""

import asyncio
import importlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .camera import SharedCamera
from .errors import VisionErrorCode
from .profiles import AprilTagProfileConfig

_log = logging.getLogger(__name__)


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
        self._executor: ThreadPoolExecutor | None = None
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
        """Setzt Samples zurueck. Aufnahmen kommen ab jetzt nur noch ueber
        `capture()`."""
        self._samples = []
        self._image_size = (0, 0)
        self.running = True

    async def capture(self) -> bool:
        """Versucht, aus dem aktuellsten Kamera-Frame eine Aufnahme zu machen.

        Manuell ausgeloest, kein automatisches Zeitintervall. Gibt zurueck,
        ob das Board gefunden und die Aufnahme uebernommen wurde -- bei
        `False` liegt es meist an Unschaerfe, falschem Bildausschnitt oder
        einer falschen Board-Geometrie in der Konfiguration.
        """
        if not self.running:
            return False
        frame = self._camera.latest_frame
        if frame is None:
            return False

        from tagloc import frames as frame_tools

        spec = self._spec()
        board = self._build_board(spec)  # None fuer chessboard
        self._image_size = frame_tools.image_size(frame.image)
        sample = await self._run_blocking(
            self._detect_board, frame_tools.to_gray(frame.image), spec, board
        )
        if sample is None:
            return False
        self._samples.append(sample)
        _log.info(
            "Kalibrierung: Aufnahme %d (%d Ecken)", len(self._samples), sample.count()
        )
        return True

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

    async def _stop(self) -> None:
        self.running = False
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    async def finish(self) -> tuple[VisionErrorCode, dict]:
        """Rechnet und speichert. Immer aufraeumend, auch bei Fehlschlag --
        eine Session bleibt nie unbeendet haengen."""
        samples = list(self._samples)
        image_size = self._image_size
        await self._stop()

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
        await self._stop()


__all__ = ["CalibrationSession"]
