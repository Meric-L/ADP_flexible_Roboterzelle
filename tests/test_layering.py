"""The dependency direction is a rule, so it's tested.

Three guarantees the rest builds on (doc/apriltag-lokalisierung.md 2.1):

* `tagloc.identity` and `tagloc.modes` need **not even numpy** -- the server
  builds `configurationId` and normalises the stream mode before anything
  is loaded.
* The math layer imports **no cv2**. Otherwise it would be tied to the
  installed OpenCV version, which differs between laptop and Pi.
* `tagloc` **never** imports from `vision_server`. The dependency runs one
  way only.

Measured in a subprocess against `sys.modules`, not the source text: an
import sneaking in through three levels of indirection still shows up.
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
)

#: Modules that use cv2 -- but only inside functions, never at import time.
LAZY_CV2_MODULES = ("tagloc.detector", "tagloc.pose", "tagloc.boards", "tagloc.overlay", "tagloc.frames")

STDLIB_ONLY_MODULES = ("tagloc.identity", "tagloc.modes")


def imported_modules(target: str, candidates) -> list[str]:
    """Return which of `candidates` end up in `sys.modules` after `import target`.

    If the module can't be imported at all, that's a broken environment, not
    a violated layering rule -- so it's skipped. This happens in practice:
    the shipped `.venv` is missing `numpy/__init__.py`.
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
        """The drawing and detection modules don't pull in cv2 at import time either.

        Otherwise OpenCV would sit on the cell server's import chain, and a
        server without OpenCV wouldn't be able to start.
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

    `build_detection_sources` puts it into a set/dict; a numpy matrix in
    there would break server startup, not just a job.
    """

    def test_server_configuration_stays_hashable(self):
        try:
            from vision_server.config import VisionServerConfig
            from vision_server.profiles import AprilTagProfileConfig, CameraStreamConfig
        except ImportError as error:  # asyncua fehlt in dieser Umgebung
            self.skipTest(f"vision_server nicht importierbar: {error}")
        config = VisionServerConfig(
            apriltag=AprilTagProfileConfig(), camera_stream=CameraStreamConfig()
        )
        self.assertIsInstance(hash(config), int)
        self.assertIsInstance(hash(AprilTagProfileConfig()), int)
        self.assertIsInstance(hash(CameraStreamConfig()), int)


if __name__ == "__main__":
    unittest.main()
