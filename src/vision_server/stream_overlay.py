"""Overlay for the camera livestream: what the server marks in the image.

The stream is a debugging tool. It should show not just that an image
arrives, but what detection sees in it -- detected tags as a green box with tag ID
(no axis cross, no status bar), or the found board corners in calibration mode.

Two rules keep this sustainable in production:

1. **Never modify the shared frame.** `SharedCamera.latest_frame` belongs to
   all readers; drawing happens on a copy here.
2. **Don't re-detect on every frame.** Between runs (`overlay_interval_s`),
   the last result is redrawn. Invisible for a fixed-mounted camera, but not
   to the Pi's CPU.

Im Modus "apriltag" gilt seit 21.10.2026 zusaetzlich: die Tags kommen aus
einer Erkennung in **voller** Kameraaufloesung, gestreamt wird trotzdem nur
das kleine Bild. Das Overlay erkennt dafuer nicht im Stream-Takt -- das kostete
am Pi eine ganze Core und liess Jobs in den Timeout laufen (py-spy,
24.09.2026) --, sondern zeichnet das neueste Ergebnis, das Job,
Zellen-Ueberwachung oder Messung ohnehin berechnet haben
(`AprilTagDetectionSource.latest_tags`). Nur wenn das zu alt ist, erkennt es
selbst, selten, im Hintergrund und nie waehrend eines Jobs. Siehe
`doc/projektdoku/arbeitsplaene/livestream-tags-volle-aufloesung.md`.

Runs entirely in the publisher's worker thread -- `annotate` is never called
directly on the event loop.
"""

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
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
        detection_max_width: int | None = None,
        full_resolution_tags: Callable[[], Any] | None = None,
        detect_interval_s: float = 5.0,
    ) -> None:
        self._config = config
        self._detector = detector
        self._calibration = calibration
        self._tag_map = tag_map
        self._interval_s = interval_s
        #: Im Kalibrier-Modus wird vor `detect_board` auf diese Breite
        #: herunterskaliert (typischerweise dieselbe wie
        #: `CameraStreamConfig.max_stream_width`) -- Ecken-Erkennung auf dem
        #: vollen Kamera-Frame (z. B. 2028x1520 bei cam_ceiling) dauert auf
        #: dem Pi spuerbar lang und drueckt die Stream-Framerate. `None`
        #: laesst den Original-Frame unangetastet.
        self._detection_max_width = detection_max_width
        #: Liefert das neueste `TagSnapshot` der Quelle (Job, Zellen-
        #: Ueberwachung, Messung) oder `None`.
        self._full_resolution_tags = full_resolution_tags
        #: Ist das neueste Ergebnis aelter, erkennt das Overlay selbst in
        #: voller Aufloesung. `<= 0`: nie selbst erkennen.
        self._detect_interval_s = detect_interval_s
        #: Solange das `True` liefert (Job, Kamera-Kalibrierung), startet
        #: keine eigene Erkennung -- der Job soll die CPU fuer sich haben.
        self._busy: Callable[[], bool] | None = None
        #: Solange das `True` liefert (MeasureTags laeuft, Hand-Auge-Sitzung offen),
        #: zeichnet "apriltag" nichts und gibt das Rohbild zurueck: Die Messung
        #: soll die CPU des Pi fuer sich haben, und der Stream bleibt nicht stehen.
        self._overlay_paused: Callable[[], bool] | None = None
        #: Ergebnis der eigenen Erkennung, `TagSnapshot` oder `None`.
        self._own_tags: Any = None
        #: Eigener Ein-Thread-Pool: `annotate()` wartet nie auf die
        #: Erkennung (am Pi ~1 s auf 12 MP), der Stream stockt nicht.
        self._executor: ThreadPoolExecutor | None = None
        self._pending: Future | None = None
        #: Zeitpunkt des letzten eigenen Versuchs, auch eines gescheiterten --
        #: sonst wuerde ein Fehler jeden Tick neu versucht.
        self._last_own_attempt = float("-inf")
        self._last_run = 0.0
        self._last_board = None
        self._scaled: dict[tuple[int, int], Any] = {}
        #: Waehrend `runner.py`s `StartCalibration`/`FinishCalibration`/
        #: `AbortCalibration` gesetzt bzw. wieder auf `None` -- siehe
        #: `set_calibration_session`.
        self._calibration_session: Any = None
        #: Entzerr-Maps je Stream-Aufloesung fuer den Modus "undistorted" --
        #: `initUndistortRectifyMap` ist teuer, `remap` mit fertigen Maps nicht.
        self._undistort_maps: dict[tuple[int, int], Any] = {}
        #: Einmess-Vorschau (Modus "tagreference"). Alle drei Felder sind
        #: unveraenderliche Objekte (`tagreference_preview`), die der Event-
        #: Loop setzt und der Overlay-Thread liest -- Attribut-Zuweisung genuegt.
        self._preview: Any = None
        self._view: Any = None
        #: Zuletzt berechneter Status fuer `TagReferencePreviewStatus`; der
        #: Publisher schreibt ihn, wenn sich das Objekt geaendert hat.
        self.reference_preview_status: dict | None = None
        #: Waehrend `MeasureTagReference` wertet die Vorschau nicht selbst aus:
        #: die Messung liefert ihre Debug-Daten (`show_reference_debug`).
        self._reference_measuring = False
        self._preview_pending: Future | None = None
        self._last_preview_start = float("-inf")
        self._last_preview_error: str | None = None
        #: `(View, Ausgabegroesse, skalierte Debug-Daten, Ecken)` des letzten Zeichnens.
        self._scaled_cache: tuple | None = None

    def apply_calibration(self, calibration: Any) -> None:
        """Ersetzt die Kalibrierung, die das Overlay fuers Achsenkreuz nutzt
        -- Gegenstueck zu `AprilTagDetectionSource.apply_calibration`, damit
        Job und Livestream nach einer frischen interaktiven Kalibrierung
        wieder dieselbe Quelle zeigen. `_scaled` muss dabei geleert werden,
        sonst rechnet `_calibration_for` mit dem alten, jetzt falschen
        Ergebnis von `scale_to_resolution` weiter."""
        self._calibration = calibration
        self._scaled = {}
        self._undistort_maps = {}

    def set_calibration_session(self, session: Any) -> None:
        """Haengt eine laufende `CalibrationSession` ein oder aus (`None`).

        Aufgerufen von `runner.py`s Kalibrier-Methoden; der Publisher-Loop
        laeuft in einem eigenen Worker-Thread, das Setzen selbst aber auf dem
        Event-Loop -- eine einfache Attribut-Zuweisung ist dafuer sicher
        genug, ohne dass eine Sperre noetig waere.
        """
        self._calibration_session = session

    def set_reference_preview(self, request: Any) -> None:
        """Schaltet die Einmess-Vorschau fuer `request` ein, `None` = aus.

        Event-Loop-seitig aufgerufen (OPC-UA-Methoden); die Auswertung selbst
        laeuft im Overlay-Thread, nur im Modus "tagreference".
        """
        from .tagreference_preview import PREVIEW_TTL_S, ReferencePreview, build_status

        if request is None:
            self._preview = None
            self._view = None
            self.reference_preview_status = build_status(
                active=False, tag_id=None, tag_seen=False
            )
            return
        self._preview = ReferencePreview(request, time.monotonic() + PREVIEW_TTL_S)
        # Neue Anfrage (anderer Tag, andere Ebenen): das alte Bild gilt nicht
        # mehr, die naechste Auswertung darf sofort starten.
        self._view = None
        self._last_preview_start = float("-inf")
        self.reference_preview_status = build_status(
            active=True, tag_id=request.tag_id, tag_seen=False, layers=request.overlay
        )

    def expire_reference_preview(self, now: float | None = None) -> None:
        """Schaltet eine abgelaufene Vorschau aus (auch ausserhalb des Modus "tagreference")."""
        preview = self._preview
        if preview is not None and (time.monotonic() if now is None else now) > preview.expires_at:
            self.set_reference_preview(None)

    def _status_for_current_preview(self) -> None:
        """Status passend zur aktuellen Vorschau (nach einem Wettlauf mit dem Auswerter)."""
        from .tagreference_preview import build_status

        preview = self._preview
        if preview is None:
            self.reference_preview_status = build_status(active=False, tag_id=None, tag_seen=False)
        else:
            self.reference_preview_status = build_status(
                active=True,
                tag_id=preview.request.tag_id,
                tag_seen=False,
                layers=preview.request.overlay,
            )

    def set_reference_measuring(self, measuring: bool) -> None:
        """`MeasureTagReference` laeuft (True) bzw. ist fertig (False)."""
        self._reference_measuring = bool(measuring)

    def show_reference_debug(
        self, debug, corners_px, image_size, title: str, layers: dict
    ) -> None:
        """Debug-Daten einer laufenden Messung anzeigen (aus dem Mess-Thread aufgerufen)."""
        from .tagreference_preview import MEASURE_HOLD_S, ReferenceView

        self._view = ReferenceView(
            debug=tuple(debug),
            corners_px=corners_px,
            image_size=tuple(image_size),
            title=title,
            layers=dict(layers),
            taken_at=time.monotonic(),
            hold_s=MEASURE_HOLD_S,
        )

    def set_busy(self, busy: Callable[[], bool] | None) -> None:
        """Haengt das Belegt-Signal ein (`runner.py`: Job oder Kalibrierung).

        Erst nach dem Annotator gebaut, deshalb nicht im Konstruktor.
        """
        self._busy = busy

    @property
    def calibration_progress(self) -> dict | None:
        """Fortschritt der aktiven Session, oder `None` ohne eine."""
        session = self._calibration_session
        return session.progress if session is not None else None

    def _calibration_for(self, size: tuple[int, int]):
        """Return calibration matching the stream resolution.

        The stream usually runs smaller than the job (1280x720 vs 2028x1520).
        Without scaling, the axis cross would visibly sit off -- and the
        calibration would look suspect instead of the scale.
        """
        from tagloc.calibration import scale_to_resolution

        if tuple(self._calibration.image_size) == tuple(size):
            return self._calibration
        cached = self._scaled.get(size)
        if cached is None:
            cached = scale_to_resolution(self._calibration, size)
            self._scaled[size] = cached
        return cached

    def _board_spec(self):
        """Return the board geometry from config.

        Dieselbe Quelle wie `CalibrationSession._spec()` -- fruher las diese
        Methode `self._calibration.board`, was bei einer Platzhalter-
        Kalibrierung (kein `board`-Feld) auf den `BoardSpec()`-Default
        (9x6/30mm) zurueckfiel und damit eine andere Geometrie annahm als die
        tatsaechlich laufende Session: die Ecken-Erkennung im Stream fand nie
        etwas, obwohl `CaptureCalibrationSample` (mit der Config-Geometrie)
        das Board korrekt fand.
        """
        from tagloc.boards import BoardSpec

        return BoardSpec(
            type=self._config.calibration_board_type,
            cols=self._config.calibration_board_cols,
            rows=self._config.calibration_board_rows,
            square_size_m=self._config.calibration_board_square_size_m,
            marker_size_m=self._config.calibration_board_marker_size_m,
            dictionary=self._config.calibration_board_dictionary,
        )

    def set_overlay_paused(self, paused: Callable[[], bool] | None) -> None:
        """Haengt das Signal ein, das das Tag-Overlay anhaelt (`runner.py`)."""
        self._overlay_paused = paused

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
                draw_tag_boxes,
            )

            if mode == "undistorted":
                return self._undistorted(image)

            if mode == "apriltag" and self._overlay_paused is not None and self._overlay_paused():
                return image  # Rohbild, ohne Erkennung und ohne Zeichnen

            now = time.monotonic()
            due = now - self._last_run >= self._interval_s

            if mode == "calibration":
                canvas = image.copy()
                if self._detection_max_width is not None:
                    from .camera_stream import _resize_for_stream

                    # Gleiche Zielbreite wie der Stream selbst: Ecken-Koordi-
                    # naten aus `detect_board` bleiben damit zwischen Ticks
                    # gueltig, weil jeder Tick auf derselben Groesse zeichnet.
                    canvas = _resize_for_stream(canvas, self._detection_max_width)
                if due:
                    from tagloc.boards import detect_board

                    self._last_board = detect_board(
                        frame_tools.to_gray(canvas), self._board_spec()
                    )
                    self._last_run = now
                # Keine kumulierte Abdeckung mehr hier: seit `capture()` nur
                # noch den Frame merkt statt sofort zu erkennen (siehe
                # `calibration_session.py`), ist sie erst nach der
                # Hintergrund-Auswertung bekannt (`progress["result"]`).
                # `self._last_board` bleibt trotzdem nuetzlich -- die
                # Vorschau hier laeuft unabhaengig davon periodisch auf dem
                # kleinen Bild und hilft beim Positionieren.
                draw_board_overlay(canvas, self._last_board)
                progress = self.calibration_progress
                if progress is None:
                    draw_status_bar(
                        canvas,
                        [
                            f"Modus: Kalibrierung   Board: {self._board_spec().type}",
                            f"Kalibrierung: {self._calibration.calibration_id or 'unbenannt'}",
                        ],
                    )
                elif progress["processing"]:
                    draw_status_bar(
                        canvas,
                        [
                            "Modus: Kalibrierung   Auswertung laeuft ...",
                            f"{progress['samples']} Aufnahmen werden ausgewertet",
                        ],
                    )
                elif progress.get("result") is not None:
                    result = progress["result"]
                    if result.get("error") == 0:
                        lines = [
                            "Modus: Kalibrierung   Fertig",
                            f"RMS {result.get('rms')} px, {result.get('samples')} Aufnahmen",
                        ]
                    else:
                        lines = [
                            "Modus: Kalibrierung   Fehlgeschlagen",
                            result.get("message", ""),
                        ]
                    draw_status_bar(canvas, lines)
                else:
                    draw_status_bar(
                        canvas,
                        [
                            "Modus: Kalibrierung   Session laeuft",
                            f"Aufnahmen {progress['samples']}/{progress['minSamples']}",
                        ],
                    )
                return canvas

            if mode == "tagreference":
                return self._tag_reference_view(image, now)

            # Gezeichnet wird auf dem kleinen Bild, die Tags stammen aus einer
            # Erkennung in voller Aufloesung (siehe Modul-Docstring). Bis
            # 21.10.2026 erkannte das Overlay selbst auf dieser kleinen
            # Leinwand -- billig, aber kein Beleg dafuer, was der Job findet.
            if self._detection_max_width is not None:
                from .camera_stream import _resize_for_stream

                canvas = _resize_for_stream(image, self._detection_max_width)
            else:
                canvas = image.copy()
            output_size = frame_tools.image_size(canvas)

            snapshot = self._newest_tags()
            if self._own_detection_due(snapshot, now):
                self._start_own_detection(image, now)

            tag_poses = (
                _scale_corners(snapshot.tag_poses, snapshot.image_size, output_size)
                if snapshot is not None
                else []
            )
            draw_tag_boxes(canvas, tag_poses)
            return canvas
        except Exception:
            _log.exception("Overlay fehlgeschlagen, sende unmarkiertes Bild")
            return image

    def _tag_reference_view(self, image: Any, now: float) -> Any:
        """Modus "tagreference": Kantensuche der Einmessung live im Stream.

        Zeichnet die letzte Auswertung (siehe `_evaluate_reference`) auf die
        verkleinerte Leinwand, Koordinaten vom Quellbild umgerechnet. Die
        Auswertung selbst startet hoechstens 3 Mal je Sekunde im Hintergrund;
        dieser Aufruf wartet nie darauf.
        """
        from tagloc import frames as frame_tools
        from tagloc.overlay import draw_tag_boxes
        from tagloc.reference_overlay import draw_reference_debug

        if self._detection_max_width is not None:
            from .camera_stream import _resize_for_stream

            canvas = _resize_for_stream(image, self._detection_max_width)
        else:
            canvas = image.copy()
        output_size = frame_tools.image_size(canvas)

        self.expire_reference_preview(now)
        preview = self._preview
        if preview is not None and self._reference_preview_due(now):
            self._start_reference_preview(image, preview, now)

        # Tag-Rahmen der uebrigen Tags: wie im Modus "apriltag" aus dem neuesten
        # Ergebnis, eigene Erkennung nur, wenn keine Vorschau-Auswertung laeuft.
        snapshot = self._newest_tags()
        evaluating = self._preview_pending is not None and not self._preview_pending.done()
        if not evaluating and self._own_detection_due(snapshot, now):
            self._start_own_detection(image, now)

        view = self._view
        # Eine laufende Auswertung haelt das alte Bild: kein Flackern bei langsamer Rechnung.
        if view is not None and (now - view.taken_at <= view.hold_s or evaluating):
            debug, corners = self._scaled_view(view, output_size)
            if corners is None and snapshot is not None:
                draw_tag_boxes(
                    canvas, _scale_corners(snapshot.tag_poses, snapshot.image_size, output_size)
                )
            return draw_reference_debug(
                canvas, debug, tag_corners_px=corners, title=view.title, layers=view.layers
            )

        if snapshot is not None:
            draw_tag_boxes(canvas, _scale_corners(snapshot.tag_poses, snapshot.image_size, output_size))
        title = (
            f"Tag {preview.request.tag_id} - Vorschau startet"
            if preview is not None
            else "keine Einmess-Vorschau"
        )
        return draw_reference_debug(canvas, [], title=title)

    def _scaled_view(self, view: Any, output_size: tuple) -> tuple:
        """`(Debug, Ecken)` der View auf Stream-Groesse; je (View, Groesse) nur einmal gerechnet."""
        import numpy as np
        from tagloc.reference_overlay import scale_debug

        cached = self._scaled_cache
        if cached is not None and cached[0] is view and cached[1] == tuple(output_size):
            return cached[2], cached[3]
        sx = output_size[0] / view.image_size[0]
        sy = output_size[1] / view.image_size[1]
        corners = None
        if view.corners_px is not None:
            corners = np.asarray(view.corners_px, dtype=np.float64) * [sx, sy]
        debug = scale_debug(view.debug, sx, sy)
        self._scaled_cache = (view, tuple(output_size), debug, corners)
        return debug, corners

    def _reference_preview_due(self, now: float) -> bool:
        from .tagreference_preview import PREVIEW_INTERVAL_S

        if self._detector is None or self._reference_measuring:
            return False
        if self._preview_pending is not None and not self._preview_pending.done():
            return False
        if now - self._last_preview_start < PREVIEW_INTERVAL_S:
            return False
        return not (self._busy is not None and self._busy())

    def _start_reference_preview(self, image: Any, preview: Any, now: float) -> None:
        """Startet eine Vorschau-Auswertung im Hintergrund.

        Derselbe Ein-Thread-Pool wie die eigene Erkennung: der Detektor ist
        nicht fuer zwei Threads gebaut, so laufen beide nacheinander.
        """
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="stream-overlay-detect"
            )
        self._last_preview_start = now
        self._preview_pending = self._executor.submit(self._evaluate_reference, image, preview)

    def _evaluate_reference(self, image: Any, preview: Any) -> None:
        """Tag erkennen, Kanten messen (mit Debug-Daten). Laeuft im Overlay-Thread."""
        try:
            from tagloc import frames as frame_tools
            from tagloc.pose import estimate_tag_poses
            from tagloc.reference_edges import measure_reference_lines

            from .measuretag import SizeLookup
            from .tagreference_preview import PREVIEW_HOLD_S, ReferenceView, build_status

            request = preview.request
            size = frame_tools.image_size(image)
            calibration = self._calibration_for(size)
            gray = frame_tools.to_gray(image)
            sizes = SizeLookup(
                {request.tag_id: request.tag_size_m} if request.tag_size_m else {},
                self._tag_map,
                self._config.tag_size_m,
            )
            tag_poses = estimate_tag_poses(
                self._detector.detect(gray),
                calibration,
                tag_map=sizes,
                default_size_m=self._config.tag_size_m,
                max_reprojection_error_px=self._config.max_reproj_error_px,
            )
            tag = next((item for item in tag_poses if item.tag_id == request.tag_id), None)
            debug: list = []
            corners = None
            if tag is not None:
                measure_reference_lines(
                    gray,
                    calibration,
                    tag.pose_cam_tag,
                    request.pose_module_tag_guess,
                    request.lines,
                    tag_size_m=sizes.size_for(request.tag_id),
                    search_band_m=request.search_band_m,
                    debug=debug,
                )
                if tag.observation is not None:
                    corners = [list(corner) for corner in tag.observation.corners]
            if self._preview is not preview:
                return  # inzwischen aus- oder umgeschaltet
            title = (
                f"Tag {request.tag_id} - Vorschau"
                if tag is not None
                else f"Tag {request.tag_id} nicht im Bild"
            )
            self._view = ReferenceView(
                debug=tuple(debug),
                corners_px=corners,
                image_size=tuple(size),
                title=title,
                layers=request.overlay,
                taken_at=time.monotonic(),
                hold_s=PREVIEW_HOLD_S,
            )
            self.reference_preview_status = build_status(
                active=True,
                tag_id=request.tag_id,
                tag_seen=tag is not None,
                debug=debug,
                layers=request.overlay,
            )
            if self._preview is not preview:
                # Wettlauf: waehrend der Zuweisung wurde umgeschaltet (Event-Loop).
                self._view = None
                self._status_for_current_preview()
            self._last_preview_error = None
        except Exception as error:
            # Bei 3 Hz nicht jedes Mal den ganzen Stacktrace ins Log.
            if str(error) != self._last_preview_error:
                self._last_preview_error = str(error)
                _log.exception("Einmess-Vorschau fehlgeschlagen")

    def _newest_tags(self):
        """Das juengere von Quellen-Ergebnis und eigener Erkennung, oder `None`."""
        candidates = [self._own_tags]
        if self._full_resolution_tags is not None:
            candidates.append(self._full_resolution_tags())
        candidates = [snapshot for snapshot in candidates if snapshot is not None]
        return max(candidates, key=lambda snapshot: snapshot.taken_at, default=None)

    def _own_detection_due(self, snapshot, now: float) -> bool:
        """Selbst erkennen nur, wenn niemand sonst ein frisches Ergebnis hat."""
        if self._detect_interval_s <= 0 or self._detector is None:
            return False
        if self._pending is not None and not self._pending.done():
            return False
        if now - self._last_own_attempt < self._detect_interval_s:
            return False
        if snapshot is not None and now - snapshot.taken_at < self._detect_interval_s:
            return False
        return not (self._busy is not None and self._busy())

    def _start_own_detection(self, image: Any, now: float) -> None:
        """Startet eine Erkennung in voller Aufloesung im Hintergrund.

        `image` ist der geteilte Frame -- `to_gray` legt ein neues Array an,
        der Frame selbst wird nicht veraendert.
        """
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="stream-overlay-detect"
            )
        self._last_own_attempt = now
        self._pending = self._executor.submit(self._detect_full_resolution, image)

    def _detect_full_resolution(self, image: Any) -> None:
        """Eigene Erkennung auf dem vollen Frame. Laeuft im Overlay-Thread."""
        try:
            from tagloc import frames as frame_tools
            from tagloc.pose import estimate_tag_poses

            from .detection.apriltag import TagSnapshot

            size = frame_tools.image_size(image)
            tag_poses = estimate_tag_poses(
                self._detector.detect(frame_tools.to_gray(image)),
                self._calibration_for(size),
                tag_map=self._tag_map,
                default_size_m=self._config.tag_size_m,
                max_reprojection_error_px=self._config.max_reproj_error_px,
            )
            self._own_tags = TagSnapshot(
                tag_poses=tuple(tag_poses),
                image_size=tuple(size),
                origin="overlay",
                taken_at=time.monotonic(),
            )
        except Exception:
            _log.exception("Overlay-Erkennung in voller Aufloesung fehlgeschlagen")

    def close(self) -> None:
        """Gibt den Erkennungs-Thread frei; eine laufende Erkennung endet von selbst."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _undistorted(self, image: Any) -> Any:
        """Das Bild entzerrt mit der aktiven Kalibrierung, ohne Markierungen.

        Erst auf Stream-Breite verkleinern, dann entzerren: `remap` auf dem
        kleinen Bild kostet einen Bruchteil, und die Maps passen zur
        skalierten Kalibrierung. Ohne echte Kalibrierung (Platzhalter) gibt
        es nichts zu entzerren -- dann das Rohbild mit Hinweis, damit niemand
        ein unverzerrtes Bild fuer ein entzerrtes haelt.
        """
        import cv2

        from tagloc import frames as frame_tools
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID
        from tagloc.lens import undistortion_maps
        from tagloc.modes import NO_CALIBRATION_HINT
        from tagloc.overlay import draw_status_bar

        if self._detection_max_width is not None:
            from .camera_stream import _resize_for_stream

            canvas = _resize_for_stream(image, self._detection_max_width)
        else:
            canvas = image
        if self._calibration.calibration_id == PLACEHOLDER_CALIBRATION_ID:
            return draw_status_bar(canvas.copy(), [NO_CALIBRATION_HINT])
        size = frame_tools.image_size(canvas)
        maps = self._undistort_maps.get(size)
        if maps is None:
            maps = undistortion_maps(self._calibration_for(size), alpha=1.0)
            self._undistort_maps[size] = maps
        # `remap` schreibt in ein neues Bild -- der geteilte Frame bleibt unberuehrt.
        return cv2.remap(canvas, maps[0], maps[1], cv2.INTER_LINEAR)


def _scale_corners(tag_poses, source_size, target_size) -> list:
    """Rechnet die Pixel-Ecken von `source_size` auf `target_size` um.

    Nur die Ecken: mehr braucht `draw_tag_boxes` nicht, die metrische Pose
    bleibt unveraendert.
    """
    if tuple(source_size) == tuple(target_size):
        return list(tag_poses)
    sx = target_size[0] / source_size[0]
    sy = target_size[1] / source_size[1]
    return [
        replace(
            pose,
            observation=replace(
                pose.observation,
                corners=tuple((x * sx, y * sy) for x, y in pose.observation.corners),
            ),
        )
        if pose.observation is not None
        else pose
        for pose in tag_poses
    ]


__all__ = ["AprilTagStreamAnnotator"]
