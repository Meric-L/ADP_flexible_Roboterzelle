"""Schachbrett-Pose fuer die Hand-Auge-Kalibrierung, an gerenderten Bildern.

Der Kern: dasselbe Brett muss in jedem Bild dasselbe Koordinatensystem
bekommen -- auch wenn die Kamera um 180 Grad gedreht darauf schaut und OpenCV
die Ecken deshalb in anderer Reihenfolge liefert.
"""

import math
import unittest

try:
    import cv2
    import numpy as np

    from tagloc import geometry
    from tagloc.boards import (
        CHESSBOARD,
        BoardSpec,
        canonical_chessboard_corners,
        chessboard_is_unambiguous,
        detect_board,
        estimate_board_pose,
    )
    from tagloc.calibration import CameraCalibration
except Exception:  # pragma: no cover - cv2/numpy fehlen in dieser Umgebung
    cv2 = None

#: Das gelaserte Brett: 6x7 Felder zu 23 mm, also 5x6 Innenecken.
SPEC = BoardSpec(type=CHESSBOARD, cols=5, rows=6, square_size_m=0.023) if cv2 else None
SIZE = (1280, 960)
PIXELS_PER_M = 4000.0
MARGIN_M = 0.03


def calibration():
    camera_matrix = np.array([[1000.0, 0, SIZE[0] / 2], [0, 1000.0, SIZE[1] / 2], [0, 0, 1]])
    return CameraCalibration(camera_matrix=camera_matrix, distortion=np.zeros(5), image_size=SIZE)


def texture():
    """Das Brett von oben, weisser Rand. Feld (i, j) ist schwarz bei (i + j) gerade,
    damit liegt das Feld zwischen den ersten vier Innenecken (1, 1) schwarz."""
    s = SPEC.square_size_m
    width = int(((SPEC.cols + 1) * s + 2 * MARGIN_M) * PIXELS_PER_M)
    height = int(((SPEC.rows + 1) * s + 2 * MARGIN_M) * PIXELS_PER_M)
    image = np.full((height, width), 255, dtype=np.uint8)
    for i in range(SPEC.cols + 1):
        for j in range(SPEC.rows + 1):
            if (i + j) % 2 == 0:
                x0 = int((MARGIN_M + i * s) * PIXELS_PER_M)
                y0 = int((MARGIN_M + j * s) * PIXELS_PER_M)
                image[y0 : y0 + int(s * PIXELS_PER_M), x0 : x0 + int(s * PIXELS_PER_M)] = 0
    return image


def render(pose_cam_board):
    """Das Brett so, wie die Kamera es bei `T_cam_board` sieht."""
    s = SPEC.square_size_m
    # Texturpixel -> Board-Frame (Ursprung in der ersten Innenecke), Meter.
    to_board = np.array(
        [[1 / PIXELS_PER_M, 0, -(MARGIN_M + s)], [0, 1 / PIXELS_PER_M, -(MARGIN_M + s)], [0, 0, 1]]
    )
    pose = np.asarray(pose_cam_board)
    plane = np.column_stack([pose[:3, 0], pose[:3, 1], pose[:3, 3]])
    homography = calibration().camera_matrix @ plane @ to_board
    return cv2.warpPerspective(texture(), homography, SIZE, borderValue=255)


def looking_at_board(rx_deg=0.0, ry_deg=0.0, rz_deg=0.0, distance=0.35):
    """Kamera schaut aus `distance` auf die Brettmitte, um die Achsen gedreht."""
    s = SPEC.square_size_m
    centre = np.array([(SPEC.cols - 1) * s / 2, (SPEC.rows - 1) * s / 2, 0.0])
    rotation = np.eye(3)
    for axis, angle in ((0, rx_deg), (1, ry_deg), (2, rz_deg)):
        rvec = [0.0, 0.0, 0.0]
        rvec[axis] = math.radians(angle)
        rotation = rotation @ geometry.rotation_from_rvec(rvec)
    # T_cam_board: Brettmitte liegt `distance` vor der Kamera.
    translation = np.array([0.0, 0.0, distance]) - rotation @ centre
    return geometry.from_rotation_translation(rotation, translation)


def measure(image):
    sample = detect_board(image, SPEC)
    if sample is None:
        return None
    corners = canonical_chessboard_corners(image, sample.corners, SPEC)
    return estimate_board_pose(corners, SPEC, calibration())


@unittest.skipUnless(cv2 is not None, "cv2/numpy fehlen")
class BoardPoseTest(unittest.TestCase):
    def assert_same_pose(self, measured, truth):
        self.assertLess(geometry.translation_distance_m(measured, truth), 0.001)
        self.assertLess(math.degrees(geometry.rotation_distance_rad(measured, truth)), 0.5)

    def test_the_lasered_board_is_unambiguous(self):
        self.assertTrue(chessboard_is_unambiguous(SPEC))
        self.assertFalse(chessboard_is_unambiguous(BoardSpec(type=CHESSBOARD, cols=6, rows=6)))
        self.assertFalse(chessboard_is_unambiguous(BoardSpec(type=CHESSBOARD, cols=7, rows=9)))

    def test_straight_on(self):
        truth = looking_at_board()
        pose, error_px = measure(render(truth))
        self.assertLess(error_px, 1.0)
        self.assert_same_pose(pose, truth)

    def test_turned_and_tilted_views_give_the_same_board_frame(self):
        # Um 180 Grad gedreht liefert OpenCV die Ecken von der anderen Seite;
        # die Pose muss trotzdem dieselbe sein.
        for angles in ((0, 0, 180), (0, 0, 90), (20, 0, 30), (0, -25, 200), (15, 15, -120)):
            with self.subTest(angles=angles):
                truth = looking_at_board(*angles)
                result = measure(render(truth))
                self.assertIsNotNone(result, "Brett nicht gefunden")
                self.assert_same_pose(result[0], truth)

    def test_a_board_partly_outside_the_image_is_not_found(self):
        truth = looking_at_board()
        truth[0, 3] += 0.2  # weit nach rechts geschoben
        self.assertIsNone(detect_board(render(truth), SPEC))


if __name__ == "__main__":
    unittest.main()
