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
from tagloc.frames import ImageFolderSource, open_source

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


if __name__ == "__main__":
    unittest.main()
