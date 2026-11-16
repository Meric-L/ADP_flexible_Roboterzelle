"""Detection source that runs an external Python script as a subprocess."""

import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from ..errors import VisionErrorCode, VisionJobError
from .base import Detection, DetectionRequest, DetectionSource

_log = logging.getLogger(__name__)

#: Grace period between SIGTERM and SIGKILL when a stop aborts the job.
TERMINATE_GRACE_S = 2.0


class ScriptDetectionSource(DetectionSource):
    """Run a script and wrap its output as a pseudo detection.

    Placeholder for calibration and image detection: the executed script has no real
    logic yet, it only prints the message text to stdout. Later, only the script
    content is replaced by the real calibration/detection logic, without this class
    or the job flow having to change.
    """

    #: The subprocess runs without a real camera; the result is a placeholder.
    is_simulated = True
    frame_id = "world"

    def __init__(self, profile_id: str, script_path: Path) -> None:
        self.profile_id = profile_id
        self._script_path = script_path

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Start the script and return its stdout as a detection message.

        The subprocess does not block the event loop and keeps the script's
        dependencies out of the server process. Trade-off: no state carries over
        between jobs, and each job starts a process -- for a camera that must stay
        open, `run_blocking` is the right way.
        """
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self._script_path),
            *(str(parameter) for parameter in request.parameters),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            # `Stop` cancels this `await`; without terminate the script would keep
            # running as an orphaned subprocess (e.g. with the camera still open).
            await self._terminate(process)
            raise
        if process.returncode != 0:
            raise VisionJobError(
                VisionErrorCode.DETECTION_FAILED,
                f"Script {self._script_path.name} beendete mit Code "
                f"{process.returncode}: {stderr.decode('utf-8', errors='replace').strip()}",
            )
        message = stdout.decode("utf-8", errors="replace").strip()
        return [
            Detection(
                module_id=self.profile_id.upper(),
                instance_id="det-1",
                position=(0.0, 0.0, 0.0),
                orientation=(0.0, 0.0, 0.0, 1.0),
                confidence=1.0,
                attributes={"message": message, "recipeId": request.recipe_id},
            )
        ]

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        """Send SIGTERM; if the process does not exit within `TERMINATE_GRACE_S`, send SIGKILL."""
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), TERMINATE_GRACE_S)
        except asyncio.TimeoutError:
            _log.warning(
                "Script %s reagierte nicht auf terminate(), kill()",
                self._script_path.name,
            )
            process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
