"""Tests fuer `AprilTagStreamAnnotator`s Kalibrier-Modus.

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
class AprilTagCanvasDetectionTest(unittest.TestCase):
    def test_detects_and_draws_on_the_same_resized_canvas(self):
        """Erkennung und Zeichnen teilen sich seit dem CPU-Fix (pi1,
        Job-Timeouts durch Overlay-Erkennung in voller Aufloesung) dieselbe
        verkleinerte Leinwand -- kein separater Ecken-Rescale-Schritt mehr."""
        from tagloc.calibration import default_calibration
        from tagloc.geometry import identity
        from tagloc.observations import TagObservation, TagPose

        image = np.zeros((300, 400, 3), dtype=np.uint8)
        observation = TagObservation(7, ((100, 60), (140, 60), (140, 100), (100, 100)))
        detector = SimpleNamespace(detect=lambda gray: [observation])
        annotator = AprilTagStreamAnnotator(
            CONFIG,
            detector=detector,
            calibration=default_calibration((400, 300)),
            tag_map=None,
            detection_max_width=200,
        )
        tag_pose = TagPose(tag_id=7, pose_cam_tag=identity(), observation=observation)
        with patch("tagloc.pose.estimate_tag_poses", return_value=[tag_pose]) as estimate:
            with patch("tagloc.overlay.draw_tag_overlay") as draw:
                result = annotator.annotate(image, "apriltag")

        self.assertEqual(result.shape[:2], (150, 200))
        # Erkennung laeuft auf der verkleinerten Leinwand (200x150), nicht
        # mehr auf dem vollen Frame (400x300) -- die Kalibrierung ist deshalb
        # bereits auf die Canvas-Groesse skaliert.
        self.assertEqual(estimate.call_args.args[0], [observation])
        self.assertEqual(estimate.call_args.args[1].image_size, (200, 150))
        # Kein separater Rescale mehr: die vom (gefakten) Detektor gelieferten
        # Koordinaten kommen unveraendert bei draw_tag_overlay an.
        self.assertEqual(draw.call_args.args[1][0].observation.corners[0], (100.0, 60.0))
        self.assertEqual(draw.call_args.args[2].image_size, (200, 150))
        self.assertEqual(annotator._last_tag_poses[0].observation.corners[0], (100, 60))
        self.assertFalse(np.any(image))

    def test_detector_receives_the_resized_canvas_not_the_full_frame(self):
        """Regression fuer den CPU-Fix: die teure Erkennung darf nicht mehr
        auf dem vollen (hier 400x300) Frame laufen."""
        from tagloc.calibration import default_calibration

        image = np.zeros((300, 400, 3), dtype=np.uint8)
        detector = SimpleNamespace(detect=lambda gray: [])
        with patch.object(detector, "detect", wraps=detector.detect) as detect:
            annotator = AprilTagStreamAnnotator(
                CONFIG,
                detector=detector,
                calibration=default_calibration((400, 300)),
                tag_map=None,
                detection_max_width=200,
            )
            with patch("tagloc.overlay.draw_tag_overlay"):
                annotator.annotate(image, "apriltag")

        self.assertEqual(detect.call_args.args[0].shape[:2], (150, 200))


if __name__ == "__main__":
    unittest.main()
