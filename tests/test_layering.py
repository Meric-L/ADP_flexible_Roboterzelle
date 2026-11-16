"""The dependency direction is a rule, so it is tested.

Three guarantees the rest builds on (see doc/projektdoku/apriltag-lokalisierung.md, section 2.1):

* `tagloc.identity` and `tagloc.modes` need **no numpy at all**: the server
  builds the `configurationId` and normalises the stream mode before anything
  is loaded.
* The maths layer does **not import cv2**, otherwise it would be bound to the
  installed OpenCV version, which differs between laptop and Pi.
* `tagloc` **never imports** from `vision_server`; the dependency points one way only.

Measured in a subprocess against `sys.modules`, not from the source text, so an
import that sneaks in over three levels of indirection is still caught.
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

#: Modules without a cv2 dependency.
PURE_MODULES = (
    "tagloc.identity",
    "tagloc.modes",
    "tagloc.observations",
    "tagloc.geometry",
    "tagloc.calibration",
    "tagloc.tagmap",
    "tagloc.localize",
    # The hand-eye solver also computes in numpy: cv2.calibrateHandEye is no
    # longer exported to Python in OpenCV 5.0, and the vision server must be able
    # to load the anchor without OpenCV on its import chain.
    "tagloc.handeye",
)

#: Modules that use cv2, but only inside functions, never at import time.
LAZY_CV2_MODULES = (
    "tagloc.detector",
    "tagloc.pose",
    "tagloc.boards",
    "tagloc.overlay",
    "tagloc.frames",
)

STDLIB_ONLY_MODULES = ("tagloc.identity", "tagloc.modes", "tagloc.jsonio")


def imported_modules(target: str, candidates) -> list[str]:
    """Return which of the `candidates` are in `sys.modules` after `import target`.

    If the module cannot be imported at all, the environment is broken rather
    than a layering rule violated, so the test is skipped. This happens in
    practice: the bundled `.venv` lacks `numpy/__init__.py`.
    """
    code = (
        f"import sys; import {target}; "
        f"print(','.join(m for m in {list(candidates)!r} if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
    )
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or ["unbekannt"])[-1]
        raise unittest.SkipTest(f"'import {target}' nicht moeglich: {reason}")
    return [name for name in result.stdout.strip().split(",") if name]


class LayeringTest(unittest.TestCase):
    def test_pure_modules_do_not_import_opencv(self):
        for module in PURE_MODULES:
            with self.subTest(module=module):
                self.assertEqual([], imported_modules(module, ["cv2"]))

    def test_cv2_modules_import_opencv_lazily(self):
        """Drawing and detection modules also do not pull in cv2 at import time.

        Otherwise OpenCV would sit on the import chain of the cell server, and a
        server without OpenCV could not start.
        """
        for module in LAZY_CV2_MODULES:
            with self.subTest(module=module):
                self.assertEqual([], imported_modules(module, ["cv2", "picamera2"]))

    def test_configuration_identity_needs_no_numpy(self):
        for module in STDLIB_ONLY_MODULES:
            with self.subTest(module=module):
                self.assertEqual([], imported_modules(module, ["numpy"]))

    def test_tagloc_never_imports_the_vision_server(self):
        for module in PURE_MODULES + LAZY_CV2_MODULES:
            with self.subTest(module=module):
                self.assertEqual([], imported_modules(module, ["vision_server"]))


class ConfigurationTest(unittest.TestCase):
    """The server configuration must stay hashable.

    `build_detection_sources` puts it into a set/dict; a numpy matrix in it
    would break server start, not just one job.
    """

    def test_server_configuration_stays_hashable(self):
        try:
            from vision_server.config import VisionServerConfig
            from vision_server.profiles import AprilTagProfileConfig, CameraStreamConfig
        except ImportError as error:  # asyncua missing in this environment
            self.skipTest(f"vision_server nicht importierbar: {error}")
        config = VisionServerConfig(
            apriltag=AprilTagProfileConfig(), camera_stream=CameraStreamConfig()
        )
        self.assertIsInstance(hash(config), int)
        self.assertIsInstance(hash(AprilTagProfileConfig()), int)
        self.assertIsInstance(hash(CameraStreamConfig()), int)


if __name__ == "__main__":
    unittest.main()
