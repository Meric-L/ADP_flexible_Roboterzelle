"""Tests for `tagloc.frames.open_source`'s `--source` dispatch.

Only the string-dispatch/error-message layer, no real hardware: `picamera`
and `realsense` need `picamera2`/`pyrealsense2`, which aren't installed
everywhere the tests run (see `_open_camera`'s deferred-import convention in
`vision_server/camera.py` -- `frames.py` follows the same rule).
"""

import tempfile
import unittest
from pathlib import Path

from tagloc.cli._common import is_camera_source
from tagloc.frames import (
    ImageFolderSource,
    SingleImageSource,
    image_size,
    open_source,
    to_gray,
)

try:
    import cv2
    import numpy as np

    _ = cv2.imwrite
except Exception:  # ohne OpenCV
    cv2 = None


class IsCameraSourceTest(unittest.TestCase):
    def test_recognises_all_three_camera_backends(self):
        for spec in ("camera", "camera:0", "picamera", "realsense"):
            with self.subTest(spec=spec):
                self.assertTrue(is_camera_source(spec))

    def test_does_not_treat_a_path_as_a_camera(self):
        self.assertFalse(is_camera_source("/tmp/bilder"))


class OpenSourceDispatchTest(unittest.TestCase):
    def test_rejects_an_unknown_spec_and_names_realsense_as_an_option(self):
        with self.assertRaises(FileNotFoundError) as caught:
            open_source("does-not-exist-and-is-not-a-keyword")

        self.assertIn("realsense", str(caught.exception))
        self.assertIn("picamera", str(caught.exception))


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ImageFolderSourceTest(unittest.TestCase):
    def _folder(self, folder: str, *, broken: int, good: int = 0) -> Path:
        path = Path(folder)
        for index in range(broken):
            (path / f"a_kaputt_{index}.png").write_bytes(b"kein bild")
        for index in range(good):
            cv2.imwrite(str(path / f"b_gut_{index}.png"), np.zeros((4, 6, 3), dtype=np.uint8))
        return path

    def test_skips_unreadable_images_and_ends_with_none(self):
        with tempfile.TemporaryDirectory() as folder:
            source = ImageFolderSource(self._folder(folder, broken=2, good=1))

            with self.assertLogs("tagloc.frames", level="WARNING"):
                first = source.read()
            second = source.read()

        self.assertEqual(first.shape, (4, 6, 3))
        self.assertIsNone(second)

    def test_gives_up_after_one_pass_when_looping_over_unreadable_images(self):
        with tempfile.TemporaryDirectory() as folder:
            source = ImageFolderSource(self._folder(folder, broken=3), loop=True)

            with self.assertLogs("tagloc.frames", level="WARNING") as logs:
                result = source.read()

        self.assertIsNone(result)
        self.assertEqual(len(logs.output), 3)

    def test_loops_back_to_the_first_image(self):
        with tempfile.TemporaryDirectory() as folder:
            source = ImageFolderSource(self._folder(folder, broken=0, good=2), loop=True)

            images = [source.read() for _ in range(5)]

        self.assertTrue(all(image is not None for image in images))


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ImageFolderOrderTest(unittest.TestCase):
    """Reihenfolge, Filter und Ende eines Bildordners mit Tempbildern."""

    def _write(self, folder: Path, name: str, value: int) -> None:
        cv2.imwrite(str(folder / name), np.full((2, 3, 3), value, dtype=np.uint8))

    def test_liest_nach_dateinamen_sortiert_und_nur_bilder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write(folder, "c.png", 30)
            self._write(folder, "a.PNG", 10)  # Endung gross: zaehlt trotzdem
            self._write(folder, "b.bmp", 20)
            (folder / "notizen.txt").write_text("kein bild")
            source = ImageFolderSource(folder)

            self.assertEqual(len(source), 3)
            values = [int(source.read()[0, 0, 0]) for _ in range(3)]
            self.assertEqual(values, [10, 20, 30])
            self.assertIsNone(source.read())
            self.assertIsNone(source.read())  # bleibt am Ende
            source.close()

    def test_ohne_loop_endet_mit_none_mit_loop_nicht(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(Path(tmp), "eins.png", 7)
            endlich = ImageFolderSource(Path(tmp))
            endlos = ImageFolderSource(Path(tmp), loop=True)

            self.assertIsNotNone(endlich.read())
            self.assertIsNone(endlich.read())
            self.assertTrue(all(endlos.read() is not None for _ in range(4)))

    def test_leerer_ordner_ist_ein_fehler_mit_den_erwarteten_endungen(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "readme.txt").write_text("x")
            with self.assertRaises(FileNotFoundError) as caught:
                ImageFolderSource(Path(tmp))
        self.assertIn(".png", str(caught.exception))

    def test_einzelbild_ist_beliebig_oft_lesbar(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(Path(tmp), "x.png", 5)
            source = SingleImageSource(Path(tmp) / "x.png")

            self.assertEqual(len(source), 1)
            self.assertTrue(all(source.read() is not None for _ in range(3)))

    def test_open_source_waehlt_ordner_und_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(Path(tmp), "x.png", 5)
            self.assertIsInstance(open_source(tmp), ImageFolderSource)
            self.assertIsInstance(open_source(str(Path(tmp) / "x.png")), SingleImageSource)

    def test_to_gray_und_image_size(self):
        color = np.zeros((4, 6, 3), dtype=np.uint8)
        gray = to_gray(color)
        self.assertEqual(gray.shape, (4, 6))
        self.assertIs(to_gray(gray), gray)  # schon einkanalig: unveraendert
        self.assertEqual(image_size(color), (6, 4))


if __name__ == "__main__":
    unittest.main()
