"""Tests fuer `src/jobs/calibrate.py`, den Pruef-Job des Rezepts `calibration`.

Der Job laeuft im Betrieb als Subprozess; hier wird `main()` direkt gerufen,
mit `REPO_ROOT` auf einen Tempordner umgebogen.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jobs import calibrate as job
from tagloc.calibration import default_calibration, save_calibration
from vision_server.server import PI_IDENTITIES


class FrameIdTest(unittest.TestCase):
    def test_umgebung_hat_vorrang(self):
        with mock.patch.dict(os.environ, {"VISION_FRAME_ID": "cam_test"}):
            with mock.patch("socket.gethostname", return_value="ADP-Roboter-Lokalisierung"):
                self.assertEqual(job._frame_id(), "cam_test")

    def test_echte_hostnamen_der_pis(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("VISION_FRAME_ID", None)
            for host, frame in (
                ("ADP-Roboter-Lokalisierung", "cam_ceiling"),
                ("ADP-HandInEye-Kamera-Pi", "cam_flange"),
            ):
                with mock.patch("socket.gethostname", return_value=host):
                    self.assertEqual(job._frame_id(), frame)

    def test_unbekannter_host_ist_world(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("VISION_FRAME_ID", None)
            with mock.patch("socket.gethostname", return_value="irgendein-laptop"):
                self.assertEqual(job._frame_id(), "world")

    def test_tabelle_spiegelt_pi_identities_des_servers(self):
        self.assertEqual(
            job.FRAME_BY_HOST,
            {host: frame for host, (_, frame) in PI_IDENTITIES.items()},
        )


class MainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        patches = (
            mock.patch.object(job, "REPO_ROOT", self.root),
            mock.patch.dict(os.environ, {"VISION_FRAME_ID": "cam_test"}),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.calibration_path = self.root / "data" / "calibration" / "cam_test.json"
        self.tag_map_path = self.root / "config" / "tagmap.json"

    def _write_calibration(self):
        save_calibration(self.calibration_path, default_calibration((640, 480), "cam_test"))

    def test_kalibrierung_fehlt(self):
        message = job.main()
        self.assertIn("Rahmen cam_test: KEINE KALIBRIERUNG", message)
        self.assertIn(str(self.calibration_path), message)

    def test_hinweis_nennt_das_schachbrett_der_pis(self):
        message = job.main()
        self.assertIn("--board chessboard --cols 7 --rows 10", message)
        self.assertNotIn("charuco", message)
        self.assertIn("--frame-id cam_test", message)

    def test_kalibrierung_vorhanden_tag_map_fehlt(self):
        self._write_calibration()
        message = job.main()
        self.assertIn("Rahmen cam_test", message)
        self.assertIn("Kalibrierung cam_test#", message)
        self.assertIn("640x480", message)
        self.assertIn("Tag-Map FEHLT", message)

    def test_kalibrierung_und_tag_map_vorhanden(self):
        self._write_calibration()
        self.tag_map_path.parent.mkdir(parents=True)
        self.tag_map_path.write_text("{}")
        message = job.main()
        self.assertIn("Tag-Map tagmap#", message)
        self.assertNotIn("FEHLT", message)

    def test_unlesbare_kalibrierung_ist_ein_befund_kein_absturz(self):
        self.calibration_path.parent.mkdir(parents=True)
        self.calibration_path.write_text("kein json")
        self.assertIn("Kalibrierung nicht lesbar", job.main())


if __name__ == "__main__":
    unittest.main()
