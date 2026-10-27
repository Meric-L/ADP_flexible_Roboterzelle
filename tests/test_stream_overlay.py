"""Tests fuer `AprilTagStreamAnnotator`s Kalibrier- und Entzerr-Modus.

`annotate()` importiert `tagloc`/`cv2` nur deferred und nicht injizierbar --
diese Tests laufen deshalb mit echten kleinen Arrays und echtem `cv2`
(`skipUnless`, gleiches Muster wie `test_tag_pipeline.py`), statt die
tagloc-Funktionen zu faken.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    import cv2
    import numpy as np

    _ = (cv2.circle, np.ndarray)
except Exception:  # OpenCV nicht installiert
    cv2 = None
    np = None

from vision_server.profiles import AprilTagProfileConfig
from vision_server.stream_overlay import AprilTagStreamAnnotator

CONFIG = AprilTagProfileConfig(
    calibration_board_type="chessboard",
    calibration_board_cols=7,
    calibration_board_rows=9,
    calibration_board_square_size_m=0.022,
    calibration_board_marker_size_m=0.011,
    calibration_board_dictionary="DICT_5X5_100",
)


def _fake_calibration(size: tuple[int, int]):
    """Reicht fuer `_calibration_for`, solange `image_size` zur Bildgroesse
    passt -- dann wird `scale_to_resolution` gar nicht erst aufgerufen."""
    return SimpleNamespace(image_size=size, calibration_id="test-cal")


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class BoardSpecFromConfigTest(unittest.TestCase):
    def test_uses_config_geometry_not_the_calibration_file(self):
        """Frueher las `_board_spec` `calibration.board` und fiel bei einer
        Platzhalter-Kalibrierung (kein `board`-Feld) auf den BoardSpec()-
        Default (9x6/30mm) zurueck -- eine andere Geometrie als die, mit der
        `CalibrationSession` tatsaechlich sucht. Die Kalibrierungs-Objekt hat
        hier bewusst gar kein `board`-Attribut, um das zu erzwingen."""
        annotator = AprilTagStreamAnnotator(
            CONFIG,
            detector=None,
            calibration=_fake_calibration((100, 100)),
            tag_map=None,
        )

        spec = annotator._board_spec()

        self.assertEqual(spec.cols, 7)
        self.assertEqual(spec.rows, 9)
        self.assertEqual(spec.square_size_m, 0.022)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class CalibrationModeDownscaleTest(unittest.TestCase):
    def test_downscales_before_detection_when_configured(self):
        image = np.zeros((300, 400, 3), dtype=np.uint8)
        annotator = AprilTagStreamAnnotator(
            CONFIG,
            detector=None,
            calibration=_fake_calibration((400, 300)),
            tag_map=None,
            detection_max_width=200,
        )

        result = annotator.annotate(image, "calibration")

        self.assertEqual(result.shape[1], 200)
        self.assertEqual(result.shape[0], 150)

    def test_keeps_native_size_without_a_configured_width(self):
        image = np.zeros((300, 400, 3), dtype=np.uint8)
        annotator = AprilTagStreamAnnotator(
            CONFIG,
            detector=None,
            calibration=_fake_calibration((400, 300)),
            tag_map=None,
        )

        result = annotator.annotate(image, "calibration")

        self.assertEqual(result.shape[1], 400)

    def test_a_board_that_is_not_found_does_not_raise(self):
        """Ein leeres Bild enthaelt kein Schachbrett -- `detect_board` muss
        `None` liefern statt zu werfen, und `annotate` gibt trotzdem ein
        Bild zurueck (die Overlay-Faenger-Klausel darf hier nicht greifen)."""
        image = np.zeros((300, 400, 3), dtype=np.uint8)
        annotator = AprilTagStreamAnnotator(
            CONFIG,
            detector=None,
            calibration=_fake_calibration((400, 300)),
            tag_map=None,
        )

        result = annotator.annotate(image, "calibration")

        self.assertIsNotNone(result)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ApplyCalibrationTest(unittest.TestCase):
    """`apply_calibration` -- Gegenstueck zu `AprilTagDetectionSource.
    apply_calibration`, damit Job und Livestream nach einer frischen
    interaktiven Kalibrierung wieder dieselbe Quelle zeigen."""

    def test_replaces_the_calibration_object(self):
        annotator = AprilTagStreamAnnotator(
            CONFIG, detector=None, calibration=_fake_calibration((100, 100)), tag_map=None
        )
        new_calibration = _fake_calibration((100, 100))

        annotator.apply_calibration(new_calibration)

        self.assertIs(annotator._calibration, new_calibration)

    def test_clears_the_scaled_cache(self):
        annotator = AprilTagStreamAnnotator(
            CONFIG, detector=None, calibration=_fake_calibration((100, 100)), tag_map=None
        )
        annotator._scaled[(50, 50)] = "veraltet, gehoert zur alten Kalibrierung"

        annotator.apply_calibration(_fake_calibration((100, 100)))

        self.assertEqual(annotator._scaled, {})


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class AprilTagFullResolutionTest(unittest.TestCase):
    """Modus "apriltag" seit 21.10.2026: gezeichnet wird auf dem kleinen
    Stream-Bild, die Tags stammen aus einer Erkennung in voller Aufloesung.
    Das Overlay erkennt nicht mehr im Stream-Takt -- auf pi1 kostete das in
    voller Aufloesung eine Core und liess Jobs in den Timeout laufen."""

    CORNERS = ((100.0, 60.0), (140.0, 60.0), (140.0, 100.0), (100.0, 100.0))

    def _tag_pose(self):
        from tagloc.geometry import identity
        from tagloc.observations import TagObservation, TagPose

        return TagPose(
            tag_id=7, pose_cam_tag=identity(), observation=TagObservation(7, self.CORNERS)
        )

    def _snapshot(self, origin="job", age_s=0.0):
        import time

        from vision_server.detection.apriltag import TagSnapshot

        return TagSnapshot(
            tag_poses=(self._tag_pose(),),
            image_size=(400, 300),
            origin=origin,
            taken_at=time.monotonic() - age_s,
        )

    def _annotator(self, *, detector=None, snapshot=None, detect_interval_s=5.0):
        from tagloc.calibration import default_calibration

        return AprilTagStreamAnnotator(
            CONFIG,
            detector=detector or SimpleNamespace(detect=lambda gray: []),
            calibration=default_calibration((400, 300)),
            tag_map=None,
            detection_max_width=200,
            full_resolution_tags=lambda: snapshot,
            detect_interval_s=detect_interval_s,
        )

    def _annotate(self, annotator, image):
        with patch("tagloc.overlay.draw_tag_boxes") as draw:
            with patch("tagloc.overlay.draw_status_bar") as status:
                result = annotator.annotate(image, "apriltag")
        return result, draw, status

    def test_draws_the_full_resolution_result_scaled_to_the_stream(self):
        image = np.zeros((300, 400, 3), dtype=np.uint8)
        annotator = self._annotator(snapshot=self._snapshot())

        result, draw, status = self._annotate(annotator, image)

        self.assertEqual(result.shape[:2], (150, 200))
        # Ecken von 400x300 auf 200x150 umgerechnet.
        self.assertEqual(draw.call_args.args[1][0].observation.corners[0], (50.0, 30.0))
        # Seit dem schlanken Overlay: keine Statusleiste im Modus "apriltag".
        status.assert_not_called()
        self.assertFalse(np.any(image))

    def test_overlay_pauses_while_measuring_and_returns_the_raw_image(self):
        image = np.zeros((300, 400, 3), dtype=np.uint8)
        annotator = self._annotator(snapshot=self._snapshot())
        paused = {"value": True}
        annotator.set_overlay_paused(lambda: paused["value"])

        result, draw, _ = self._annotate(annotator, image)
        self.assertIs(result, image)  # Rohbild, nichts gezeichnet, nichts erkannt
        draw.assert_not_called()

        # Danach laeuft das Overlay wieder wie gewaehlt.
        paused["value"] = False
        result, draw, _ = self._annotate(annotator, image)
        self.assertEqual(result.shape[:2], (150, 200))
        draw.assert_called_once()

    def test_pause_does_not_touch_other_modes(self):
        annotator = self._annotator(snapshot=self._snapshot())
        annotator.set_overlay_paused(lambda: True)
        result = annotator.annotate(np.zeros((300, 400, 3), dtype=np.uint8), "calibration")
        self.assertEqual(result.shape[:2], (150, 200))  # Kalibrier-Vorschau unveraendert

    def test_no_own_detection_while_a_fresh_result_exists(self):
        detector = SimpleNamespace(detect=lambda gray: [])
        with patch.object(detector, "detect", wraps=detector.detect) as detect:
            annotator = self._annotator(detector=detector, snapshot=self._snapshot())
            self._annotate(annotator, np.zeros((300, 400, 3), dtype=np.uint8))

        detect.assert_not_called()
        self.assertIsNone(annotator._pending)

    def test_own_detection_runs_on_the_full_frame_in_the_background(self):
        """Ohne frisches Ergebnis erkennt das Overlay selbst -- auf dem
        vollen Frame (400x300), nicht auf der Leinwand (200x150)."""
        image = np.zeros((300, 400, 3), dtype=np.uint8)
        detector = SimpleNamespace(detect=lambda gray: [])
        with patch.object(detector, "detect", wraps=detector.detect) as detect:
            annotator = self._annotator(detector=detector)
            with patch("tagloc.pose.estimate_tag_poses", return_value=[self._tag_pose()]):
                _, draw, status = self._annotate(annotator, image)
                annotator._pending.result(timeout=5)

        self.assertEqual(detect.call_args.args[0].shape[:2], (300, 400))
        # Der erste Tick wartet nicht auf die Erkennung ...
        self.assertEqual(draw.call_args.args[1], [])
        status.assert_not_called()
        # ... der naechste zeigt ihr Ergebnis.
        _, draw, status = self._annotate(annotator, image)
        self.assertEqual(draw.call_args.args[1][0].observation.corners[0], (50.0, 30.0))
        status.assert_not_called()
        annotator.close()

    def test_an_old_result_triggers_an_own_detection(self):
        annotator = self._annotator(snapshot=self._snapshot(age_s=10.0))

        with patch("tagloc.pose.estimate_tag_poses", return_value=[]):
            self._annotate(annotator, np.zeros((300, 400, 3), dtype=np.uint8))
            annotator._pending.result(timeout=5)

        self.assertEqual(annotator._own_tags.origin, "overlay")
        annotator.close()

    def test_no_own_detection_while_busy(self):
        annotator = self._annotator()
        annotator.set_busy(lambda: True)

        self._annotate(annotator, np.zeros((300, 400, 3), dtype=np.uint8))

        self.assertIsNone(annotator._pending)

    def test_own_detection_can_be_switched_off(self):
        annotator = self._annotator(detect_interval_s=0)

        self._annotate(annotator, np.zeros((300, 400, 3), dtype=np.uint8))

        self.assertIsNone(annotator._pending)

    def test_a_failed_own_detection_is_not_retried_every_tick(self):
        def broken(gray):
            raise RuntimeError("kaputt")

        annotator = self._annotator(detector=SimpleNamespace(detect=broken))
        image = np.zeros((300, 400, 3), dtype=np.uint8)
        with self.assertLogs("vision_server.stream_overlay", level="ERROR"):
            self._annotate(annotator, image)
            first = annotator._pending
            first.result(timeout=5)

        self._annotate(annotator, image)

        self.assertIs(annotator._pending, first)
        self.assertIsNone(annotator._own_tags)
        annotator.close()

    def test_the_newer_of_source_and_own_result_is_shown(self):
        annotator = self._annotator(snapshot=self._snapshot(origin="cellwatch", age_s=1.0))
        annotator._own_tags = self._snapshot(origin="overlay", age_s=3.0)

        self.assertEqual(annotator._newest_tags().origin, "cellwatch")


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class UndistortedModeTest(unittest.TestCase):
    """Modus "undistorted": nur entzerrt, ohne Markierungen."""

    def _calibration(self, calibration_id="echt"):
        from tagloc.calibration import PINHOLE, CameraCalibration

        return CameraCalibration(
            camera_matrix=np.array([[80.0, 0, 50], [0, 80.0, 40], [0, 0, 1]]),
            distortion=np.array([-0.3, 0.08, 0, 0, 0]),
            image_size=(100, 80),
            model=PINHOLE,
            calibration_id=calibration_id,
        )

    def _image(self, width=100, height=80):
        image = np.zeros((height, width, 3), dtype=np.uint8)
        image[::10, :] = 255  # Gitterlinien: entzerrt sehen sie anders aus
        image[:, ::10] = 255
        return image

    def test_the_image_is_remapped_and_the_maps_are_cached(self):
        annotator = AprilTagStreamAnnotator(
            CONFIG, detector=None, calibration=self._calibration(), tag_map=None
        )
        image = self._image()
        original = image.copy()

        out = annotator.annotate(image, "undistorted")

        self.assertEqual(out.shape, image.shape)
        self.assertFalse(np.array_equal(out, image))
        np.testing.assert_array_equal(image, original)  # geteilter Frame unberuehrt
        self.assertIn((100, 80), annotator._undistort_maps)

        annotator.apply_calibration(self._calibration())
        self.assertEqual(annotator._undistort_maps, {})

    def test_a_stream_smaller_than_the_calibration_is_undistorted_at_its_size(self):
        annotator = AprilTagStreamAnnotator(
            CONFIG,
            detector=None,
            calibration=self._calibration(),
            tag_map=None,
            detection_max_width=50,
        )
        out = annotator.annotate(self._image(), "undistorted")
        self.assertEqual(out.shape[:2], (40, 50))
        self.assertIn((50, 40), annotator._undistort_maps)

    def test_a_placeholder_calibration_sends_the_raw_image_with_a_hint(self):
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

        annotator = AprilTagStreamAnnotator(
            CONFIG,
            detector=None,
            calibration=self._calibration(PLACEHOLDER_CALIBRATION_ID),
            tag_map=None,
        )
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        with patch("tagloc.overlay.draw_status_bar", side_effect=lambda img, lines: img) as bar:
            out = annotator.annotate(image, "undistorted")
        bar.assert_called_once()
        self.assertEqual(bar.call_args[0][1], ["Keine Kalibrierung -- Rohbild"])
        self.assertIsNot(out, image)
        self.assertEqual(annotator._undistort_maps, {})


if __name__ == "__main__":
    unittest.main()
