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

Erreicht die Abdeckung `calibration_coverage_threshold`, rechnet und speichert
die Session von selbst (kein Aufruf von `FinishCalibration` noetig) -- das
Ergebnis landet in `progress["result"]`, das Frontend muss also nur den
Fortschritts-Knoten beobachten. Abdeckung allein sagt aber nichts ueber die
tatsaechliche Genauigkeit: ein Board, das nie gekippt wurde, kann trotz guter
Abdeckung einen hohen Reprojektionsfehler ergeben (RMS). Das Ergebnis traegt
deshalb bei einem RMS ueber `RMS_WARNING_PX` zusaetzlich eine `warning` --
verhindert das automatische Speichern nicht, macht die Ungenauigkeit aber
sichtbar statt sie zu verstecken.
"""

import asyncio
import importlib
import inspect
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .camera import SharedCamera
from .errors import VisionErrorCode
from .profiles import AprilTagProfileConfig

_log = logging.getLogger(__name__)

#: Deckt sich mit dem Abbruchkriterium aus dem Testplan ("RMS unter 0,5 px").
#: Nur eine Anzeige-Warnung, kein Fehlschlag -- eine ungenaue, aber
#: gespeicherte Kalibrierung ist beim ersten Funktionstest eher gewollt als
#: ein automatischer Abbruch ohne jedes Ergebnis.
RMS_WARNING_PX = 0.5

#: Rechnerische Untergrenze fuer `calibrate_from_samples` (siehe
#: `_compute_and_save`) -- unabhaengig davon, wie niedrig
#: `calibration_min_samples` konfiguriert ist (die ist ein *empfohlener*
#: Schwellwert, kein mathematisches Minimum). Der automatische Abschluss darf
#: nie unterhalb dieser Zahl ausloesen, sonst wuerde eine Session mit
#: DETECTION_FAILED enden, ohne dass der Operator das wollte.
MIN_SAMPLES_FOR_CALIBRATION = 3


def _lazy(module: str, name: str) -> Callable:
    """Loest eine `tagloc`-Funktion erst beim Aufruf auf -- diese Datei
    importiert `tagloc`/`cv2` nie auf Modulebene."""

    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    return call


def _save_capture_image(path: Any, image: Any) -> None:
    """Schreibt eine Aufnahme als PNG -- Debug-Artefakt, kein Teil der
    Kalibrierung selbst. Kein `tagloc`-Bezug, deshalb kein `_lazy(...)`."""
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)


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
        on_calibrated: Callable[[Any], Any] | None = None,
        save_capture_image: Callable | None = None,
    ) -> None:
        self._camera = camera
        self._config = config
        self._out_path = config.calibration_path
        #: `None` (Standard) speichert keine Bilder. Gesetzt, schreibt jede
        #: uebernommene Aufnahme zusaetzlich als PNG hier ab -- zum
        #: Nachpruefen/Neu-Rechnen abseits vom Server.
        self._capture_dir = config.calibration_capture_dir
        self._saved_captures = 0
        self._save_capture_image = save_capture_image or _save_capture_image
        #: Laufende Hintergrund-Tasks aus `_schedule_capture_save` -- Referenz
        #: haelt sie am Leben (sonst kann der Garbage Collector eine
        #: `asyncio.Task` ohne gehaltene Referenz vorzeitig einsammeln).
        self._pending_saves: set = set()
        #: Nach erfolgreichem Speichern aufgerufen (async oder sync), mit dem
        #: frisch berechneten `CameraCalibration`-Objekt -- `runner.py` setzt
        #: das per `set_on_calibrated`, um Erkennung/Overlay ohne
        #: Server-Neustart auf den neuen Stand zu bringen.
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
        self._board_spec: Any = None  # lazy: braucht cv2, siehe `_spec()`
        self._samples: list = []
        self._image_size: tuple[int, int] = (0, 0)
        self._executor: ThreadPoolExecutor | None = None
        self.running = False
        #: Ergebnis von `finish()` bzw. des automatischen Abschlusses ueber
        #: `calibration_coverage_threshold`; `None` bis dahin. Wird von
        #: `progress` mit ausgeliefert, damit ein Beobachter des reinen
        #: Fortschritts-Knotens auch das Endergebnis sieht.
        self.last_result: dict | None = None

    def set_on_calibrated(self, callback: Callable[[Any], Any] | None) -> None:
        """Setzt/entfernt den `on_calibrated`-Callback nachtraeglich.

        `runner.py` braucht dafuer sowohl die Erkennungsquelle als auch den
        Stream-Annotator, die beide erst nach der Session gebaut werden --
        ein Setter macht das moeglich, ohne die Bau-Reihenfolge umzustellen.
        """
        self._on_calibrated = callback

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

    def _schedule_capture_save(self, path: Any, image: Any) -> None:
        """Speichert die Debug-Aufnahme im Hintergrund, ohne `capture()` --
        und damit die Antwort auf `CaptureCalibrationSample` -- darauf warten
        zu lassen.

        Live gefunden 2026-09-23 an der Deckenkamera (12 MP): ein
        synchrones `await self._run_blocking(...)` an dieser Stelle liess
        `cv2.imwrite` als PNG mehrere Sekunden brauchen, bevor die
        OPC-UA-Antwort ueberhaupt rausging -- derselbe Timeout
        ("Failed to send request to OPC UA server"), der zuvor schon durch
        `CALIB_CB_ACCURACY` verursacht wurde (siehe `boards.py`). Das
        Speichern ist reines Debug-Artefakt, kein Teil des Ergebnisses --
        es darf also ruhig noch laufen, waehrend der Operator schon die
        naechste Aufnahme macht. Der Ein-Worker-Pool (`_pool()`) haelt die
        Schreibreihenfolge trotzdem ein, dieselbe Warteschlange wie fuer
        `detect_board`."""
        task = asyncio.ensure_future(self._run_blocking(self._save_capture_image, path, image))
        self._pending_saves.add(task)
        task.add_done_callback(self._on_capture_save_done)

    def _on_capture_save_done(self, task: "asyncio.Task") -> None:
        self._pending_saves.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _log.warning("Debug-Aufnahme konnte nicht gespeichert werden: %s", error)

    async def wait_for_pending_saves(self) -> None:
        """Wartet auf alle im Hintergrund laufenden Debug-Speicherungen --
        fuer Tests, die das Ergebnis von `_schedule_capture_save` pruefen
        wollen, bevor sie fortfahren. Im Produktivbetrieb ungenutzt."""
        if self._pending_saves:
            await asyncio.gather(*list(self._pending_saves), return_exceptions=True)

    def start(self) -> None:
        """Setzt Samples zurueck. Aufnahmen kommen ab jetzt nur noch ueber
        `capture()`."""
        self._samples = []
        self._image_size = (0, 0)
        self._saved_captures = 0
        self.last_result = None
        self.running = True

    async def capture(self) -> bool:
        """Versucht, aus dem aktuellsten Kamera-Frame eine Aufnahme zu machen.

        Manuell ausgeloest, kein automatisches Zeitintervall. Gibt zurueck,
        ob das Board gefunden und die Aufnahme uebernommen wurde -- bei
        `False` liegt es meist an Unschaerfe, falschem Bildausschnitt oder
        einer falschen Board-Geometrie in der Konfiguration.

        Reicht die Abdeckung danach fuer `calibration_coverage_threshold`,
        schliesst diese Aufnahme die Session gleich mit ab (siehe
        `_maybe_auto_finish`) -- der Rueckgabewert bleibt trotzdem nur "Board
        gefunden?", das Ergebnis steht in `progress["result"]`.
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
        if self._capture_dir is not None:
            self._saved_captures += 1
            path = self._capture_dir / f"kalib_{self._saved_captures:03d}.png"
            self._schedule_capture_save(path, frame.image)
        await self._maybe_auto_finish()
        return True

    def _coverage_threshold_reached(self) -> bool:
        threshold = self._config.calibration_coverage_threshold
        min_samples = max(MIN_SAMPLES_FOR_CALIBRATION, self._config.calibration_min_samples)
        if threshold is None or len(self._samples) < min_samples:
            return False
        coverage = self._compute_coverage(self._samples, self._image_size)
        return coverage[0] >= threshold and coverage[1] >= threshold

    async def _maybe_auto_finish(self) -> None:
        if self._coverage_threshold_reached():
            _log.info("Kalibrierung: Abdeckungs-Schwelle erreicht, schliesse automatisch ab")
            error, summary = await self._compute_and_save()
            self.last_result = {"error": int(error), **summary}

    @property
    def progress(self) -> dict:
        """JSON-faehiges Fortschritts-Objekt fuer `CalibrationProgress`."""
        coverage = (
            self._compute_coverage(self._samples, self._image_size)
            if self._samples
            else (0.0, 0.0)
        )
        data = {
            "running": self.running,
            "samples": len(self._samples),
            "minSamples": self._config.calibration_min_samples,
            "coverageX": round(coverage[0], 3),
            "coverageY": round(coverage[1], 3),
        }
        if self.last_result is not None:
            data["result"] = self.last_result
        return data

    async def _stop(self) -> None:
        self.running = False
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    async def _compute_and_save(self) -> tuple[VisionErrorCode, dict]:
        """Rechnet und speichert. Immer aufraeumend, auch bei Fehlschlag --
        eine Session bleibt nie unbeendet haengen. Gemeinsamer Kern von
        `finish()` und dem automatischen Abschluss ueber die Abdeckungs-
        Schwelle in `capture()`."""
        samples = list(self._samples)
        image_size = self._image_size
        await self._stop()

        if len(samples) < MIN_SAMPLES_FOR_CALIBRATION:
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
        except Exception as error:
            # Nicht nur ValueError: cv2.calibrateCamera scheitert bei
            # numerisch ungeeigneten Aufnahmen (z. B. zu wenig Neigungs-/
            # Distanz-Variation trotz guter Bildabdeckung) mit `cv2.error`,
            # keinem ValueError. Ungefangen wuerde das hier durchschlagen --
            # `self.running` ist zu diesem Zeitpunkt bereits `False` (siehe
            # `_stop()` oben), die Session bliebe also unsichtbar tot haengen:
            # jede weitere `capture()` liefert danach nur noch "Board nicht
            # gefunden", ein spaeteres manuelles `finish()` nur noch
            # `INVALID_STATE`, ohne dass je ein Ergebnis oder eine
            # Fehlermeldung zu sehen war. `asyncio.CancelledError` faengt das
            # nicht ab (die erbt von `BaseException`, nicht `Exception`).
            _log.exception("Kalibrierung fehlgeschlagen (%d Aufnahmen)", len(samples))
            return VisionErrorCode.DETECTION_FAILED, {"message": str(error)}

        coverage = self._compute_coverage(samples, image_size)
        await self._run_blocking(self._save_calibration, self._out_path, calibration)
        if self._on_calibrated is not None:
            outcome = self._on_calibrated(calibration)
            if inspect.isawaitable(outcome):
                await outcome
        rms = round(calibration.rms_reprojection_error, 4)
        summary = {
            "rms": rms,
            "samples": calibration.sample_count,
            "coverageX": round(coverage[0], 3),
            "coverageY": round(coverage[1], 3),
            "path": str(self._out_path),
        }
        if rms > RMS_WARNING_PX:
            summary["warning"] = (
                f"RMS {rms} px ueber dem Zielwert {RMS_WARNING_PX} px -- "
                "vermutlich zu wenig Neigung/Distanz-Variation beim Aufnehmen "
                "(eine Abdeckung, die nur die Position im Bild misst, sagt "
                "nichts ueber die Vielfalt der Posen). Kalibrierung ist "
                "trotzdem gespeichert."
            )
        _log.info(
            "Kalibrierung gespeichert: RMS %.4f px, %d Aufnahmen, Abdeckung x %.0f%% y %.0f%%",
            summary["rms"],
            summary["samples"],
            coverage[0] * 100,
            coverage[1] * 100,
        )
        return VisionErrorCode.OK, summary

    async def finish(self) -> tuple[VisionErrorCode, dict]:
        """Beendet die Session manuell -- siehe `_compute_and_save`."""
        error, summary = await self._compute_and_save()
        self.last_result = {"error": int(error), **summary}
        return error, summary

    async def abort(self) -> None:
        """Stoppt ohne zu speichern."""
        await self._stop()


__all__ = ["CalibrationSession"]
