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

**Aufnahme und Auswertung sind entkoppelt (seit 2026-09-28).** `capture()`
merkt sich nur noch den aktuellen Kamera-Frame -- kein `detect_board` mehr im
RPC-Pfad von `CaptureCalibrationSample`. Grund: `detect_board` auf dem vollen
Kamera-Frame kann auf schwaecherer Pi-Hardware (Deckenkamera, 12 MP) so lange
dauern, dass entweder der OPC-UA-Client die Verbindung aufgibt oder --
schlimmer -- der Aufruf gar nicht mehr zurueckkommt und damit den einzigen
Worker der Session dauerhaft blockiert (live gefunden 2026-09-28, siehe
`CAPTURE_DETECT_TIMEOUT_S`). `finish()` stoesst stattdessen die eigentliche
Auswertung (Ecken-Erkennung je Aufnahme + `calibrate_from_samples`) als
Hintergrund-Aufgabe an und kehrt sofort zurueck; das Ergebnis erscheint in
`progress["result"]`, sobald es fertig ist (`progress["processing"]` zeigt an,
dass noch gerechnet wird). `runner.py` pausiert dafuer den Livestream, um dem
Pi die volle CPU zu geben.

Preis dieser Entkopplung: waehrend der Aufnahme gibt es kein Live-Feedback
mehr, ob eine bestimmte Aufnahme das Board tatsaechlich zeigt, und keine
laufende Abdeckungsanzeige -- beides stellt sich erst nach `finish()` heraus.
Der Live-Vorschaustream (`CameraStreamMode="calibration"`) zeigt weiterhin
Board-Ecken auf dem kleinen Vorschaubild, unabhaengig von dieser Klasse
(`stream_overlay.py`) -- das hilft beim Positionieren, sagt aber nichts
darueber aus, welche Aufnahmen tatsaechlich uebernommen wurden.
"""

import asyncio
import importlib
import inspect
import logging
import shutil
import time
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
#: Schwellwert, kein mathematisches Minimum). `finish()` gibt darunter sofort
#: `DETECTION_FAILED` zurueck, ohne die (teure) Hintergrund-Auswertung
#: ueberhaupt erst anzustossen.
MIN_SAMPLES_FOR_CALIBRATION = 3

#: Zeitlimit fuer einen einzelnen `detect_board`-Aufruf waehrend der
#: Auswertung in `_process()`. Live an der Deckenkamera (12 MP) gefunden,
#: 2026-09-28: ein Aufruf auf dem vollen Kamera-Frame kann auf der
#: Pi-Hardware haengen bleiben (kein Fehler, kein Rueckgabewert, ueber 5
#: Minuten beobachtet -- also nicht nur sehr langsam, echt haengend). Da
#: `_pool()` nur einen Worker hat, wuerde das ohne Zeitlimit nicht nur diese
#: eine Aufnahme, sondern die gesamte Auswertung fuer immer blockieren. Der
#: haengende Thread selbst laesst sich in Python nicht abbrechen (leakt im
#: Hintergrund weiter), aber `_process()` gibt die betroffene Aufnahme
#: wenigstens auf (zaehlt als "Board nicht gefunden") und verwirft den Pool,
#: damit die naechste Aufnahme der Auswertung einen frischen Worker bekommt.
CAPTURE_DETECT_TIMEOUT_S = 20.0


def _lazy(module: str, name: str) -> Callable:
    """Loest eine `tagloc`-Funktion erst beim Aufruf auf -- diese Datei
    importiert `tagloc`/`cv2` nie auf Modulebene."""

    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    return call


#: JPEG statt PNG: auf der Deckenkamera (12 MP) war PNG-Schreiben auf der
#: Pi-SD-Karte langsam genug, um selbst im Hintergrund-Pool zum Flaschenhals
#: zu werden (live gefunden 2026-09-28). Fuer den Zweck (Ecken-Erkennung,
#: ggf. spaeteres Nachpruefen) veraendert die verlustbehaftete Kompression
#: die erkannten Eckenpositionen eines Schachbrettmusters nicht spuerbar.
_CAPTURE_IMAGE_SUFFIX = ".jpg"
_CAPTURE_JPEG_QUALITY = 92


def _save_capture_image(path: Any, image: Any) -> None:
    """Schreibt eine Aufnahme als JPEG. Kein `tagloc`-Bezug, deshalb kein
    `_lazy(...)`."""
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, _CAPTURE_JPEG_QUALITY])


def _load_capture_image(path: Any) -> Any:
    """Liest eine zuvor gespeicherte Aufnahme zurueck, fuer die Auswertung
    in `_process()`. `None`, wenn die Datei fehlt oder kein Bild ist --
    `_process()` ueberspringt sie dann, statt abzubrechen."""
    import cv2

    return cv2.imread(str(path))


class CalibrationSession:
    """Sammelt Kamera-Frames, bis `finish()` sie im Hintergrund auswertet.

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
        load_capture_image: Callable | None = None,
    ) -> None:
        self._camera = camera
        self._config = config
        self._out_path = config.calibration_path
        #: Zwischenspeicher fuer die Aufnahmen der laufenden Session, bis
        #: `finish()` sie auswertet -- bei jedem `start()` geleert (siehe
        #: dort). Getrennt von `calibration_capture_dir` (optionales
        #: dauerhaftes Debug-Archiv, siehe unten): dieser Ordner ist reine
        #: Arbeitsablage, ihr Inhalt hat nach einer erfolgreichen Auswertung
        #: keine Bedeutung mehr.
        self._pending_dir = self._out_path.parent / f"{config.frame_id}_pending"
        #: Pfade der in dieser Session gemachten Aufnahmen, in Reihenfolge.
        self._captured_paths: list[Any] = []
        #: `None` (Standard) archiviert nichts zusaetzlich. Gesetzt, schreibt
        #: jede Aufnahme zusaetzlich hierhin -- zum Nachpruefen abseits vom
        #: Server, unabhaengig vom (bei jedem `start()` geleerten)
        #: `_pending_dir`.
        self._capture_dir = config.calibration_capture_dir
        self._saved_captures = 0
        self._save_capture_image = save_capture_image or _save_capture_image
        self._load_capture_image = load_capture_image or _load_capture_image
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
        #: Eigener Pool nur fuer `_schedule_capture_save` -- siehe dort, warum
        #: er sich NICHT `_executor` mit `detect_board` teilen darf.
        self._save_executor: ThreadPoolExecutor | None = None
        self.running = False
        #: `True` waehrend `_process()` (der Hintergrund-Auswertung nach
        #: `finish()`) laeuft -- siehe `busy`/`wait_for_processing`.
        self._processing = False
        #: Gesetzt, sobald keine Hintergrund-Auswertung mehr laeuft (auch
        #: initial). `runner.py` wartet darauf, um den pausierten Livestream
        #: wieder freizugeben.
        self._processing_done = asyncio.Event()
        self._processing_done.set()
        #: Ergebnis von `finish()`/der Hintergrund-Auswertung; `None` bis
        #: dahin. Wird von `progress` mit ausgeliefert, damit ein Beobachter
        #: des reinen Fortschritts-Knotens auch das Endergebnis sieht.
        self.last_result: dict | None = None

    def set_on_calibrated(self, callback: Callable[[Any], Any] | None) -> None:
        """Setzt/entfernt den `on_calibrated`-Callback nachtraeglich.

        `runner.py` braucht dafuer sowohl die Erkennungsquelle als auch den
        Stream-Annotator, die beide erst nach der Session gebaut werden --
        ein Setter macht das moeglich, ohne die Bau-Reihenfolge umzustellen.
        """
        self._on_calibrated = callback

    @property
    def busy(self) -> bool:
        """`True` waehrend Aufnahme ODER Hintergrund-Auswertung.

        Fuer `StartCalibration`: ein neuer `start()` waehrend noch eine
        Auswertung laeuft wuerde `_pending_dir` leeren, aus dem `_process()`
        gerade noch liest -- das muss verhindert werden, auch wenn `running`
        zu diesem Zeitpunkt schon `False` ist (siehe `finish()`).
        """
        return self.running or self._processing

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

    def _save_pool(self) -> ThreadPoolExecutor:
        """Eigener Ein-Worker-Pool nur fuer Speicherungen -- getrennt von
        `_pool()` (siehe `_schedule_capture_save`, warum das wichtig ist)."""
        if self._save_executor is None:
            self._save_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="vision-calibration-save"
            )
        return self._save_executor

    def _schedule_capture_save(self, path: Any, image: Any) -> None:
        """Speichert eine Aufnahme im Hintergrund, ohne `capture()` darauf
        warten zu lassen.

        Live gefunden 2026-09-23 an der Deckenkamera (12 MP): ein
        synchrones Speichern an dieser Stelle liess `cv2.imwrite` mehrere
        Sekunden brauchen, bevor die OPC-UA-Antwort ueberhaupt rausging.
        Zweiter Fund, 2026-09-28: das Hintergrund-Speichern lief zunaechst
        ueber denselben Ein-Worker-Pool wie `detect_board` -- eine noch
        laufende Speicherung blockierte damit jede weitere Aufnahme. Beide
        Male am Hand-Pi (640x480) nie beobachtbar. Deshalb `_save_pool()`:
        ein eigener Pool, getrennt von allem anderen."""
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
        """Wartet auf alle im Hintergrund laufenden Speicherungen -- noetig,
        bevor `_process()` die Dateien wieder einliest (sonst fehlen ggf.
        noch die letzten Aufnahmen), und ein Testseam fuer Tests, die das
        Ergebnis von `_schedule_capture_save` pruefen wollen."""
        if self._pending_saves:
            await asyncio.gather(*list(self._pending_saves), return_exceptions=True)

    async def wait_for_processing(self) -> None:
        """Wartet, bis eine per `finish()` gestartete Hintergrund-Auswertung
        fertig ist (`progress["processing"]` wird wieder `False`).
        `runner.py` nutzt das, um den waehrend der Auswertung pausierten
        Livestream wieder freizugeben."""
        await self._processing_done.wait()

    def start(self) -> None:
        """Beginnt eine neue Session: Aufnahmen zurueckgesetzt, alter
        `_pending_dir`-Inhalt verworfen. Aufnahmen kommen ab jetzt nur noch
        ueber `capture()`."""
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

    async def capture(self) -> bool:
        """Merkt sich den aktuellsten Kamera-Frame als Aufnahme.

        Manuell ausgeloest, kein automatisches Zeitintervall. Prueft NICHT
        mehr, ob das Board sichtbar ist -- das entscheidet sich erst bei
        der Auswertung in `finish()` (siehe Moduldoc). `False` heisst hier
        nur noch "keine Session aktiv" oder "kein Kamera-Frame verfuegbar".
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
        if self._capture_dir is not None:
            archive_path = (
                self._capture_dir / f"kalib_{self._saved_captures:03d}{_CAPTURE_IMAGE_SUFFIX}"
            )
            self._schedule_capture_save(archive_path, frame.image)
        _log.info("Kalibrierung: Aufnahme %d gespeichert", len(self._captured_paths))
        return True

    @property
    def progress(self) -> dict:
        """JSON-faehiges Fortschritts-Objekt fuer `CalibrationProgress`.

        Keine `coverageX`/`coverageY` mehr waehrend der Aufnahme -- die
        Abdeckung ist erst nach der Auswertung bekannt (siehe Moduldoc),
        `progress["result"]` traegt sie dann als Teil des Ergebnisses,
        genau wie bisher.
        """
        data = {
            "running": self.running,
            "processing": self._processing,
            "samples": len(self._captured_paths),
            "minSamples": self._config.calibration_min_samples,
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
        """Beendet die Aufnahme-Phase und stoesst die Auswertung im
        Hintergrund an -- kehrt sofort zurueck, ohne auf das Ergebnis zu
        warten (siehe Moduldoc: eine synchrone Auswertung von N
        Vollaufloesungs-Aufnahmen in der RPC-Antwort haette auf der
        Deckenkamera dasselbe Timeout-Risiko wie eine einzelne haengende
        `detect_board`-Aufnahme, live 2026-09-28 gefunden). Das Ergebnis
        erscheint in `progress["result"]`, sobald `_process()` fertig ist.
        """
        count = len(self._captured_paths)
        if count < MIN_SAMPLES_FOR_CALIBRATION:
            await self._stop()
            result = {
                "message": f"Zu wenige Aufnahmen: {count}",
                "samples": count,
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
        """Wertet die gespeicherten Aufnahmen aus: laedt jede zurueck, sucht
        das Board darin, rechnet danach wie zuvor `_compute_and_save`.
        Laeuft als Hintergrund-Task, von `finish()` angestossen -- Fehler
        hier duerfen nicht unbehandelt bleiben, sonst bliebe
        `progress["processing"]` fuer immer `True` haengen."""
        try:
            # Erst alle noch laufenden Speicherungen abwarten, sonst fehlen
            # ggf. die letzten Aufnahmen beim Wiedereinlesen.
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
                    # Siehe CAPTURE_DETECT_TIMEOUT_S: der haengende Thread
                    # laeuft im Hintergrund weiter, der Pool wird verworfen,
                    # diese eine Aufnahme zaehlt als nicht gefunden.
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
            self.last_result = {"error": int(error), **summary}
        except Exception:
            _log.exception("Auswertung unerwartet fehlgeschlagen")
            self.last_result = {
                "error": int(VisionErrorCode.DETECTION_FAILED),
                "message": "Auswertung unerwartet fehlgeschlagen, siehe Server-Log",
            }
        finally:
            self._processing = False
            self._processing_done.set()

    async def _compute_and_save(self) -> tuple[VisionErrorCode, dict]:
        """Rechnet aus den in `_process()` erkannten Samples und speichert.
        Immer aufraeumend, auch bei Fehlschlag -- eine Session bleibt nie
        unbeendet haengen."""
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
            )
        except Exception as error:
            # Nicht nur ValueError: cv2.calibrateCamera scheitert bei
            # numerisch ungeeigneten Aufnahmen (z. B. zu wenig Neigungs-/
            # Distanz-Variation trotz guter Bildabdeckung) mit `cv2.error`,
            # keinem ValueError. `asyncio.CancelledError` faengt das nicht ab
            # (die erbt von `BaseException`, nicht `Exception`).
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

    async def abort(self) -> None:
        """Stoppt ohne zu speichern. Waehrend einer laufenden
        Hintergrund-Auswertung nicht aufrufbar (siehe `busy`) -- `runner.py`
        prueft das vor dem Aufruf."""
        await self._stop()


__all__ = ["CalibrationSession"]
