"""Rauchtests fuer Werkzeuge und Einstiegspunkte ohne eigene Tests.

Geprueft wird nur, dass sie sich importieren lassen und ihren Einstieg
(`main`) haben -- ein umbenannter Import oder ein verschobenes Modul faellt
sonst erst auf, wenn jemand das Werkzeug auf dem Pi braucht. Dazu der Einstieg
der nicht versionierten systemd-Unit: `src/OPCUA/server.py` muss
`vision_server.server.main` laden.
"""

import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

VISION_TOOLS = (
    "calibration_client",
    "diagnose_board",
    "handshake_client",
    "list_realsense_profiles",
    "measure_framerate",
    "pi_load_profiler",
    "stream_viewer",
)


def load_from_path(name: str, path: Path):
    """Laedt eine Datei ausserhalb eines Pakets, wie `python datei.py` es tut."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VisionToolsImportTest(unittest.TestCase):
    def test_jedes_werkzeug_importiert_und_hat_main(self):
        for name in VISION_TOOLS:
            with self.subTest(tool=name):
                try:
                    module = importlib.import_module(f"vision_server.tools.{name}")
                except ModuleNotFoundError as error:  # cv2/asyncua nicht installiert
                    self.skipTest(f"{name}: {error.name} fehlt")
                self.assertTrue(callable(module.main))

    def test_alle_dateien_sind_in_der_liste(self):
        """Ein neues Werkzeug soll hier nicht vergessen werden."""
        on_disk = {
            path.stem
            for path in (SRC / "vision_server" / "tools").glob("*.py")
            if path.stem != "__init__"
        }
        self.assertEqual(on_disk, set(VISION_TOOLS))


class RepoToolsTest(unittest.TestCase):
    def test_measure_nodeset_import_zeigt_auf_vorhandene_nodesets(self):
        module = load_from_path("measure_nodeset_import", REPO_ROOT / "tools" / "measure_nodeset_import.py")
        self.assertTrue(callable(module.main))
        for label, path in module.IMPORT_ORDER:
            with self.subTest(nodeset=label.strip()):
                self.assertTrue(path.is_file(), path)

    def test_make_tag_sheet_schreibt_ein_massstabs_svg(self):
        try:
            import cv2

            _ = cv2.aruco.DICT_APRILTAG_36h11
        except Exception:
            self.skipTest("OpenCV mit aruco fehlt")
        module = load_from_path("make_tag_sheet", REPO_ROOT / "tools" / "make_tag_sheet.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tags.svg"
            self.assertEqual(module.main(["--ids", "0", "1", "--size-mm", "50", "--out", str(out)]), 0)
            svg = out.read_text(encoding="utf-8")
        self.assertTrue(svg.startswith("<?xml"))
        self.assertEqual(svg.count("<image "), 2)
        self.assertIn("exakt 100 mm", svg)  # Massstabskontrolle

    def test_make_tag_sheet_charuco_und_unbekanntes_woerterbuch(self):
        try:
            import cv2

            _ = cv2.aruco.DICT_APRILTAG_36h11
        except Exception:
            self.skipTest("OpenCV mit aruco fehlt")
        module = load_from_path("make_tag_sheet", REPO_ROOT / "tools" / "make_tag_sheet.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.svg"
            argv = ["--charuco", "--cols", "5", "--rows", "4", "--out", str(out)]
            self.assertEqual(module.main(argv), 0)
            self.assertIn("ChArUco 5x4", out.read_text(encoding="utf-8"))
            with self.assertRaises(SystemExit):
                module.main(["--family", "DICT_GIBT_ES_NICHT", "--out", str(Path(tmp) / "x.svg")])


class SystemdEinstiegTest(unittest.TestCase):
    def test_opcua_server_py_laedt_vision_server_main(self):
        """Die systemd-Unit startet `src/OPCUA/server.py`; die Datei hat keine
        Logik, nur diesen Import. Bricht er, startet auf den Pis nichts."""
        from vision_server import server as vision_server_module

        saved_path = list(sys.path)
        try:
            module = load_from_path("opcua_server_einstieg", SRC / "OPCUA" / "server.py")
        finally:
            sys.path[:] = saved_path
        self.assertIs(module.main, vision_server_module.main)
        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main()
