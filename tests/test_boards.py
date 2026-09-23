"""Tests fuer Board-Geometrie und Marker-Erzeugung."""

import unittest

try:
    import numpy as np

    from tagloc.boards import CHARUCO, BoardSpec, build_board, chessboard_object_points
except Exception:  # numpy in dieser Umgebung defekt
    np = None

try:
    import cv2

    _ = cv2.aruco
    from tagloc.detector import aruco_dictionary, generate_marker
except Exception:  # ohne OpenCV
    cv2 = None


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ChessboardObjectPointsTest(unittest.TestCase):
    def test_lists_the_inner_corners_row_by_row(self):
        points = chessboard_object_points(BoardSpec(cols=3, rows=2, square_size_m=0.5))

        np.testing.assert_allclose(
            points,
            [[0, 0, 0], [0.5, 0, 0], [1.0, 0, 0], [0, 0.5, 0], [0.5, 0.5, 0], [1.0, 0.5, 0]],
        )

    def test_uses_float32_unless_asked_otherwise(self):
        spec = BoardSpec(cols=9, rows=6, square_size_m=0.03)

        self.assertEqual(chessboard_object_points(spec).dtype, np.float32)
        self.assertEqual(chessboard_object_points(spec, np.float64).dtype, np.float64)
        self.assertAlmostEqual(float(chessboard_object_points(spec, np.float64)[-1, 0]), 0.24)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class MarkerTest(unittest.TestCase):
    def test_generates_a_square_marker_of_the_requested_size(self):
        marker = generate_marker(aruco_dictionary("tag36h11"), 7, 120)

        self.assertEqual(marker.shape, (120, 120))
        self.assertEqual(marker.dtype, np.uint8)

    def test_builds_a_charuco_board_and_none_for_a_chessboard(self):
        self.assertIsNone(build_board(BoardSpec()))
        self.assertIsNotNone(build_board(BoardSpec(type=CHARUCO, cols=5, rows=4)))


if __name__ == "__main__":
    unittest.main()
