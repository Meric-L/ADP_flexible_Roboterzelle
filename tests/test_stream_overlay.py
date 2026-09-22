"""Tests fuer `AprilTagStreamAnnotator`s Kalibrier-Modus.

`annotate()` importiert `tagloc`/`cv2` nur deferred und nicht injizierbar --
diese Tests laufen deshalb mit echten kleinen Arrays und echtem `cv2`
(`skipUnless`, gleiches Muster wie `test_tag_pipeline.py`), statt die
tagloc-Funktionen zu faken.
"""

import unittest
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
