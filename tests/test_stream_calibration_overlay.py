"""Livestream im Kalibriermodus: keine dunkle Kontur, Eckenzahl oben links,
"Kalibriermodus" unten links, Ecken im Bild markiert (Wunsch 28.10.2026)."""

import unittest
from types import SimpleNamespace

try:
    import cv2
    import numpy as np

    from tagloc.overlay import COLOR_BOARD
except Exception:  # OpenCV nicht installiert
    cv2 = None
    np = None

from vision_server.profiles import AprilTagProfileConfig
from vision_server.stream_overlay import AprilTagStreamAnnotator

#: Das Brett am Aufbau: 8x11 Felder = 7x10 Innenecken.
CONFIG = AprilTagProfileConfig(
    calibration_board_type="chessboard",
    calibration_board_cols=7,
    calibration_board_rows=10,
    calibration_board_square_size_m=0.023,
)
SQUARE_PX = 30
MARGIN_PX = 40
WIDTH, HEIGHT = 640, 480


def annotator():
    return AprilTagStreamAnnotator(
        CONFIG,
        detector=None,
        calibration=SimpleNamespace(image_size=(WIDTH, HEIGHT), calibration_id="test"),
        tag_map=None,
    )


def board_image():
    """8 Felder quer, 11 hoch, weisser Rand; liefert Bild und Innenecken (x, y)."""
    image = np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)
    left = (WIDTH - 8 * SQUARE_PX) // 2
    for row in range(11):
        for col in range(8):
            if (row + col) % 2 == 0:
                x, y = left + col * SQUARE_PX, MARGIN_PX + row * SQUARE_PX - 20
                image[y : y + SQUARE_PX, x : x + SQUARE_PX] = 0
    corners = [
        (left + col * SQUARE_PX, MARGIN_PX - 20 + row * SQUARE_PX)
        for row in range(1, 11)
        for col in range(1, 8)
    ]
    return image, corners


def is_board_colour(pixel):
    return bool(np.all(np.abs(pixel.astype(int) - np.array(COLOR_BOARD)) <= 30))


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class CalibrationStreamOverlayTest(unittest.TestCase):
    def test_nothing_dark_is_drawn_only_count_and_mode(self):
        grey = np.full((HEIGHT, WIDTH, 3), 128, dtype=np.uint8)
        result = annotator().annotate(grey.copy(), "calibration")
        changed = np.any(result != grey, axis=2)
        self.assertTrue(changed.any(), "es muss etwas gezeichnet sein")
        # Keine schwarze Kontur, kein dunkler Balken: nichts Gezeichnetes ist dunkler als das Bild.
        self.assertGreaterEqual(int(result[changed].min()), 60)
        # Oben links die Eckenzahl in Brettfarbe, unten links weisse Schrift -- sonst nichts.
        top_left = result[: HEIGHT // 6, : WIDTH // 3]
        self.assertTrue(any(is_board_colour(p) for p in top_left.reshape(-1, 3)))
        bottom_left = result[HEIGHT * 5 // 6 :, : WIDTH // 3]
        self.assertTrue(np.any(np.all(bottom_left >= 200, axis=2)))
        rows, cols = np.nonzero(changed)
        in_corners = (rows < HEIGHT // 6) & (cols < WIDTH // 3) | (rows >= HEIGHT * 5 // 6) & (cols < WIDTH // 3)
        self.assertTrue(np.all(in_corners), "gezeichnet nur oben links und unten links")

    def test_found_corners_are_marked_in_the_picture(self):
        image, corners = board_image()
        result = annotator().annotate(image.copy(), "calibration")
        for x, y in corners:
            patch = result[y - 2 : y + 3, x - 2 : x + 3].reshape(-1, 3)
            self.assertTrue(any(is_board_colour(p) for p in patch), f"Ecke ({x}, {y}) nicht markiert")


if __name__ == "__main__":
    unittest.main()
