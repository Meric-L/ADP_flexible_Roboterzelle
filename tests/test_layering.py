"""Die Abhaengigkeitsrichtung ist eine Regel, also wird sie getestet.

Drei Zusicherungen, auf denen der Rest aufbaut (doc/projektdoku/apriltag-lokalisierung.md 2.1):

* `tagloc.identity` und `tagloc.modes` brauchen **nicht einmal numpy** -- der
  Server baut die `configurationId` und normalisiert den Stream-Modus, bevor
  irgendetwas geladen ist.
* Die Mathematikschicht importiert **kein cv2**. Sonst waere sie an die
  installierte OpenCV-Version gebunden, die sich zwischen Laptop und Pi
  unterscheidet.
* `tagloc` importiert **nie** aus `vision_server`. Die Abhaengigkeit laeuft
  nur in eine Richtung.

Gemessen in einem Subprozess gegen `sys.modules`, nicht am Quelltext: Ein
Import, der sich ueber drei Ebenen Umweg einschleicht, faellt trotzdem auf.
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

#: Module ohne cv2-Abhaengigkeit.
PURE_MODULES = (
    "tagloc.identity",
    "tagloc.modes",
    "tagloc.observations",
    "tagloc.geometry",
    "tagloc.calibration",
    "tagloc.tagmap",
    "tagloc.localize",
    # Auch der Hand-Auge-Solver rechnet in numpy: cv2.calibrateHandEye ist in
    # OpenCV 5.0 nicht mehr nach Python exportiert, und der Vision-Server soll
    # den Anker ohne OpenCV auf der Importkette laden koennen.
    "tagloc.handeye",
)

#: Module, die cv2 benutzen -- aber nur innerhalb von Funktionen, nie beim Import.
LAZY_CV2_MODULES = (
    "tagloc.detector",
    "tagloc.pose",
    "tagloc.boards",
    "tagloc.overlay",
    "tagloc.frames",
)

STDLIB_ONLY_MODULES = ("tagloc.identity", "tagloc.modes")


def imported_modules(target: str, candidates) -> list[str]:
    """Liefert, welche der `candidates` nach `import target` in `sys.modules` stehen.

    Laesst sich das Modul gar nicht importieren, ist das eine kaputte
    Umgebung, keine verletzte Schichtregel -- also wird uebersprungen. Das
    kommt in der Praxis vor: In der mitgelieferten `.venv` fehlt
    `numpy/__init__.py`.
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
        """Auch die Zeichen- und Erkennungsmodule ziehen cv2 nicht beim Import herein.

        Sonst laege OpenCV auf der Importkette des Zellenservers, und ein
        Server ohne OpenCV koennte nicht starten.
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
    """Die Serverkonfiguration muss hashbar bleiben.

    `build_detection_sources` legt sie in ein set/dict; eine numpy-Matrix
    darin wuerde den Serverstart brechen, nicht nur einen Job.
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
