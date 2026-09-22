"""Overlay for the camera livestream: what the server marks in the image.

The stream is a debugging tool. It should show not just that an image
arrives, but what detection sees in it -- detected tags with axis cross and
outline, or the found board corners in calibration mode.

Two rules keep this sustainable in production:

1. **Never modify the shared frame.** `SharedCamera.latest_frame` belongs to
   all readers; drawing happens on a copy here.
2. **Don't re-detect on every frame.** Between runs (`overlay_interval_s`),
   the last result is redrawn. Invisible for a fixed-mounted camera, but not
   to the Pi's CPU.

Runs entirely in the publisher's worker thread -- `annotate` is never called
directly on the event loop.
"""

import logging
import time
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
        self._last_run = 0.0
        self._last_tag_poses: list = []
        self._last_board = None
        self._scaled: dict[tuple[int, int], Any] = {}
        #: Waehrend `runner.py`s `StartCalibration`/`FinishCalibration`/
        #: `AbortCalibration` gesetzt bzw. wieder auf `None` -- siehe
        #: `set_calibration_session`.
        self._calibration_session: Any = None

    def apply_calibration(self, calibration: Any) -> None:
        """Ersetzt die Kalibrierung, die das Overlay fuers Achsenkreuz nutzt
        -- Gegenstueck zu `AprilTagDetectionSource.apply_calibration`, damit
        Job und Livestream nach einer frischen interaktiven Kalibrierung
        wieder dieselbe Quelle zeigen. `_scaled` muss dabei geleert werden,
        sonst rechnet `_calibration_for` mit dem alten, jetzt falschen
        Ergebnis von `scale_to_resolution` weiter."""
        self._calibration = calibration
        self._scaled = {}

    def set_calibration_session(self, session: Any) -> None:
        """Haengt eine laufende `CalibrationSession` ein oder aus (`None`).

        Aufgerufen von `runner.py`s Kalibrier-Methoden; der Publisher-Loop
        laeuft in einem eigenen Worker-Thread, das Setzen selbst aber auf dem
        Event-Loop -- eine einfache Attribut-Zuweisung ist dafuer sicher
        genug, ohne dass eine Sperre noetig waere.
        """
        self._calibration_session = session

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
                draw_tag_overlay,
                summarise,
            )

            canvas = image.copy()
            size = frame_tools.image_size(canvas)
            calibration = self._calibration_for(size)
            now = time.monotonic()
            due = now - self._last_run >= self._interval_s

            if mode == "calibration":
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
                progress = self.calibration_progress
                draw_board_overlay(
                    canvas,
                    self._last_board,
                    coverage=(progress["coverageX"], progress["coverageY"]) if progress else None,
                )
                if progress is not None:
                    draw_status_bar(
                        canvas,
                        [
                            "Modus: Kalibrierung   Session laeuft",
                            f"Aufnahmen {progress['samples']}/{progress['minSamples']}"
                            f"   Abdeckung x {progress['coverageX'] * 100:.0f}%"
                            f" y {progress['coverageY'] * 100:.0f}%",
                        ],
                    )
                else:
                    draw_status_bar(
                        canvas,
                        [
                            f"Modus: Kalibrierung   Board: {self._board_spec().type}",
                            f"Kalibrierung: {self._calibration.calibration_id or 'unbenannt'}",
                        ],
                    )
                return canvas

            if due:
                from tagloc.pose import estimate_tag_poses

                self._last_tag_poses = estimate_tag_poses(
                    self._detector.detect(frame_tools.to_gray(canvas)),
                    calibration,
                    tag_map=self._tag_map,
                    default_size_m=self._config.tag_size_m,
                    max_reprojection_error_px=self._config.max_reproj_error_px,
                )
                self._last_run = now
            draw_tag_overlay(
                canvas,
                self._last_tag_poses,
                calibration,
                tag_map=self._tag_map,
                default_size_m=self._config.tag_size_m,
            )
            draw_status_bar(
                canvas,
                [
                    f"Modus: AprilTag   Familie {self._config.tag_family}",
                    summarise(self._last_tag_poses, self._tag_map),
                ],
            )
            return canvas
        except Exception:
            _log.exception("Overlay fehlgeschlagen, sende unmarkiertes Bild")
            return image


__all__ = ["AprilTagStreamAnnotator"]
