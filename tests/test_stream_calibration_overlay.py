"""Livestream im Kalibriermodus: keine dunkle Kontur, Eckenzahl oben links,
eine Kalibrier-Zeile unten links, Ecken im Bild markiert (Wunsch 28.10.2026)."""

import unittest
import unittest.mock
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


def annotator(calibration_id="test"):
    return AprilTagStreamAnnotator(
        CONFIG,
        detector=None,
        calibration=SimpleNamespace(image_size=(WIDTH, HEIGHT), calibration_id=calibration_id),
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
    def test_only_count_top_left_and_one_caption_line_bottom_left(self):
        grey = np.full((HEIGHT, WIDTH, 3), 128, dtype=np.uint8)
        result = annotator("cam_flange@2026-09-23T15:25:06.698737+00:00").annotate(
            grey.copy(), "calibration"
        )
        changed = np.any(result != grey, axis=2)
        self.assertTrue(changed.any(), "es muss etwas gezeichnet sein")
        # Oben links die Eckenzahl in Brettfarbe, unten links weisse Schrift.
        top_left = result[: HEIGHT // 6, : WIDTH // 3]
        self.assertTrue(any(is_board_colour(p) for p in top_left.reshape(-1, 3)))
        bottom = result[HEIGHT * 5 // 6 :, :]
        self.assertTrue(np.any(np.all(bottom == 255, axis=2)))
        rows, cols = np.nonzero(changed)
        in_corners = (rows < HEIGHT // 6) & (cols < WIDTH // 3) | (rows >= HEIGHT * 5 // 6)
        self.assertTrue(np.all(in_corners), "gezeichnet nur oben links und unten")

    def test_caption_is_pure_white_with_one_pixel_black_border(self):
        grey = np.full((HEIGHT, WIDTH, 3), 128, dtype=np.uint8)
        result = annotator("cam_flange@2026-09-23T15:25:06.698737+00:00").annotate(
            grey.copy(), "calibration"
        )
        bottom = result[HEIGHT * 5 // 6 :, :]
        changed = np.any(bottom != 128, axis=2)
        # Nur drei Werte: Hintergrund, Weiss, Schwarz -- keine Anti-Alias-Grautoene,
        # keine Transparenz, kein Schatten.
        values = {tuple(p) for p in bottom[changed]}
        self.assertEqual(values, {(255, 255, 255), (0, 0, 0)})
        # Jedes weisse Pixel hat ringsum nur weisse oder schwarze Nachbarn im Abstand 1:
        # der Rand ist 1 px breit, danach folgt gleich der Hintergrund.
        white = np.all(bottom == 255, axis=2)
        black = np.all(bottom == 0, axis=2)
        grown = np.zeros_like(white)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= np.roll(np.roll(white, dy, axis=0), dx, axis=1)
        self.assertTrue(np.all(grown[black] | white[black]), "Rand breiter als 1 px")
        self.assertTrue(np.all(black <= grown), "schwarze Pixel ausserhalb des 1-px-Rands")

    def test_caption_text_is_one_line_without_raw_id(self):
        calls = []
        real = cv2.putText

        def spy(img, text, *args, **kwargs):
            calls.append(text)
            return real(img, text, *args, **kwargs)

        grey = np.full((HEIGHT, WIDTH, 3), 128, dtype=np.uint8)
        with unittest.mock.patch("cv2.putText", spy):
            annotator("cam_flange@2026-09-23T15:25:06.698737+00:00").annotate(
                grey.copy(), "calibration"
            )
        captions = [t for t in calls if "Hand-Auge" in t]
        self.assertEqual(
            set(captions),
            {"Hand-Auge-Kalibrierung \u00b7 Letzte Kalibrierung: 23.09.2026, 17:25 Uhr"},
        )
        for text in calls:
            for forbidden in ("Modus", "Board:", "chessboard", "cam_flange", "698737", "T15"):
                self.assertNotIn(forbidden, text)

    def test_found_corners_are_marked_in_the_picture(self):
        image, corners = board_image()
        result = annotator().annotate(image.copy(), "calibration")
        for x, y in corners:
            patch = result[y - 2 : y + 3, x - 2 : x + 3].reshape(-1, 3)
            self.assertTrue(any(is_board_colour(p) for p in patch), f"Ecke ({x}, {y}) nicht markiert")


class CalibrationStampTest(unittest.TestCase):
    def fmt(self, calibration_id):
        from tagloc.overlay import format_calibration_stamp

        return format_calibration_stamp(calibration_id)

    def test_summer_time_is_utc_plus_two(self):
        self.assertEqual(
            self.fmt("cam_flange@2026-09-23T15:25:06.698737+00:00"), "23.09.2026, 17:25 Uhr"
        )

    def test_winter_time_is_utc_plus_one(self):
        self.assertEqual(self.fmt("cam@2026-01-15T15:25:06+00:00"), "15.01.2026, 16:25 Uhr")

    def test_z_suffix_and_day_rollover(self):
        # Handeye schreibt "Z"; 23:30 UTC im Sommer ist am Folgetag 01:30 Berlin.
        self.assertEqual(self.fmt("cam@2026-06-30T23:30:00Z"), "01.07.2026, 01:30 Uhr")

    def test_other_offset_is_converted(self):
        self.assertEqual(self.fmt("cam@2026-09-23T10:00:00-05:00"), "23.09.2026, 17:00 Uhr")

    def test_resolution_suffix_after_timestamp_is_skipped(self):
        self.assertEqual(
            self.fmt("cam_flange@2026-09-23T15:25:06+00:00@1280x720"), "23.09.2026, 17:25 Uhr"
        )

    def test_unusable_input_gives_dash_not_an_error(self):
        for value in (None, "", "cam_flange", "cam@", "cam@kaputt", "cam@2026-09-23T15:25:06"):
            with self.subTest(value=value):
                self.assertEqual(self.fmt(value), "\u2013")

    def test_missing_calibration_caption(self):
        if cv2 is None:
            self.skipTest("OpenCV nicht verfuegbar")
        from tagloc.overlay import CALIBRATION_CAPTION_PREFIX

        self.assertEqual(
            CALIBRATION_CAPTION_PREFIX + self.fmt("unkalibriert"),
            "Hand-Auge-Kalibrierung \u00b7 Letzte Kalibrierung: \u2013",
        )


if __name__ == "__main__":
    unittest.main()
