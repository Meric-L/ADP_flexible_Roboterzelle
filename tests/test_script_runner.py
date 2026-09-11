"""Tests fuer ScriptDetectionSource: insbesondere den Abbruchpfad.

Ein `Stop` waehrend `calibration` laeuft muss den Subprozess wirklich
beenden statt ihn als Waisen weiterlaufen zu lassen (z. B. mit offener
Kamera). Das ist genau der Fall aus `job.py`s `except asyncio.CancelledError`.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from vision_server.detection.base import DetectionRequest
from vision_server.detection.script_runner import ScriptDetectionSource


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


if __name__ == "__main__":
    unittest.main()
