"""Tests fuer ScriptDetectionSource: insbesondere den Abbruchpfad.

Ein `Stop` waehrend `calibration` laeuft muss den Subprozess wirklich
beenden statt ihn als Waisen weiterlaufen zu lassen (z. B. mit offener
Kamera). Das ist genau der Fall aus `job.py`s `except asyncio.CancelledError`.
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vision_server.config import VisionServerConfig
from vision_server.detection import (
    DETECTION_SOURCES,
    SRC_DIR,
    calibration_script_env,
)
from vision_server.detection.base import DetectionRequest
from vision_server.detection.script_runner import ScriptDetectionSource
from vision_server.profiles import AprilTagProfileConfig


class TerminateTest(unittest.IsolatedAsyncioTestCase):
    async def test_terminates_a_running_subprocess(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)"
        )
        source = ScriptDetectionSource("calibration", Path("unused.py"))

        await source._terminate(process)

        self.assertIsNotNone(process.returncode)

    async def test_is_a_no_op_for_an_already_finished_process(self):
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        await process.wait()
        source = ScriptDetectionSource("calibration", Path("unused.py"))

        await source._terminate(process)  # darf keinen toten Prozess anfassen

        self.assertEqual(process.returncode, 0)


class AcquireAndDetectCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_kills_the_subprocess_instead_of_leaking_it(self):
        terminated: list[asyncio.subprocess.Process] = []

        class RecordingSource(ScriptDetectionSource):
            async def _terminate(self, process):
                await super()._terminate(process)
                terminated.append(process)

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "slow.py"
            script.write_text("import time\ntime.sleep(30)\n")
            source = RecordingSource("calibration", script)

            task = asyncio.create_task(
                source.acquire_and_detect(DetectionRequest(job_id="job-1"))
            )
            await asyncio.sleep(0.3)  # Subprozess ist jetzt sicher gestartet
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(len(terminated), 1)
        self.assertIsNotNone(terminated[0].returncode)


class SubprocessEnvTest(unittest.TestCase):
    def test_src_steht_vorn_im_pythonpath(self):
        source = ScriptDetectionSource("calibration", Path("unused.py"), env={"A": "1"})
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/woanders", "B": "2"}):
            env = source.subprocess_env()
        self.assertEqual(env["PYTHONPATH"], os.pathsep.join([str(SRC_DIR), "/woanders"]))
        self.assertEqual((env["A"], env["B"]), ("1", "2"))

    def test_ohne_vorhandenen_pythonpath(self):
        source = ScriptDetectionSource("calibration", Path("unused.py"))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(source.subprocess_env()["PYTHONPATH"], str(SRC_DIR))

    def test_src_dir_ist_das_paket_verzeichnis(self):
        self.assertTrue((SRC_DIR / "vision_server" / "__init__.py").is_file())
        self.assertTrue((SRC_DIR / "jobs" / "calibrate.py").is_file())


class CalibrationScriptEnvTest(unittest.TestCase):
    def test_ohne_apriltag_nur_der_rahmen(self):
        env = calibration_script_env(VisionServerConfig(frame_id="cam_flange"))
        self.assertEqual(env, {"VISION_FRAME_ID": "cam_flange"})

    def test_mit_apriltag_rahmen_und_pfade_des_profils(self):
        config = VisionServerConfig(
            frame_id="cam_flange",
            apriltag=AprilTagProfileConfig(
                frame_id="cam_flange",
                calibration_path=Path("/data/cal/cam_flange.json"),
                tag_map_path=None,
            ),
        )
        self.assertEqual(
            calibration_script_env(config),
            {
                "VISION_FRAME_ID": "cam_flange",
                "VISION_CALIBRATION_PATH": "/data/cal/cam_flange.json",
                "VISION_TAG_MAP_PATH": "",
            },
        )


class CalibrationJobTest(unittest.IsolatedAsyncioTestCase):
    """`jobs/calibrate.py` sucht dort, wo der Server es sagt.

    Frueher riet das Script die Frame-ID ueber eine eigene, veraltete
    Hostname-Tabelle und landete ohne `VISION_FRAME_ID` bei `world.json`.
    """

    async def test_nimmt_rahmen_und_pfad_vom_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "cam_flange.json"
            config = VisionServerConfig(
                frame_id="cam_flange",
                apriltag=AprilTagProfileConfig(
                    frame_id="cam_flange", calibration_path=missing, tag_map_path=None
                ),
            )
            source = DETECTION_SOURCES["calibration"](config)
            with mock.patch.dict(os.environ, {"VISION_FRAME_ID": "falsch"}):
                detections = await source.acquire_and_detect(DetectionRequest(job_id="job-1"))

        message = detections[0].attributes["message"]
        self.assertTrue(
            message.startswith(f"Rahmen cam_flange: KEINE KALIBRIERUNG unter {missing}."),
            message,
        )


if __name__ == "__main__":
    unittest.main()
