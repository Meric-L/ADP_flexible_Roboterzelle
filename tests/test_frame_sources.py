"""Tests for `tagloc.frames.open_source`'s `--source` dispatch.

Only the string-dispatch/error-message layer, no real hardware: `picamera`
and `realsense` need `picamera2`/`pyrealsense2`, which aren't installed
everywhere the tests run (see `_open_camera`'s deferred-import convention in
`vision_server/camera.py` -- `frames.py` follows the same rule).
"""

import unittest

from tagloc.cli._common import is_camera_source
from tagloc.frames import open_source


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


if __name__ == "__main__":
    unittest.main()
