"""The fully wired vision system (`VisionMachine`) and its shutdown."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..aio import cancel_and_wait
from ..asset_model import VisionAssetNodes
from ..calibration_session import CalibrationSession
from ..camera_health import CameraHealthPublisher
from ..camera_stream import CameraStreamPublisher
from ..config import VisionServerConfig
from ..detection import DetectionSource
from ..events import VisionEvents
from ..job import JobRunner
from ..mjpeg_server import MjpegServer
from ..result_management import ResultStore
from ..state_machine import VisionStateMachines
from ..vision_program import VisionProgram

_log = logging.getLogger(__package__)


LOOP_LAG_INTERVAL_S = 0.25


LOOP_LAG_WARN_S = 0.75


async def _watch_loop_lag(
    interval: float = LOOP_LAG_INTERVAL_S, warn: float = LOOP_LAG_WARN_S
) -> None:
    """Log a warning when the event loop was blocked.

    If a source forgets `run_blocking`, this shows it here. Otherwise it would
    surface as an unexplained connection drop somewhere else. Replaces
    `RaspiDevice/Counter` as the reference signal (doc/project_docs/work-plans/legacy.md A3/A5).
    """
    loop = asyncio.get_running_loop()
    while True:
        before = loop.time()
        await asyncio.sleep(interval)
        elapsed = loop.time() - before
        if elapsed > warn:
            _log.warning(
                "Event-Loop %d ms blockiert (erwartet %d ms)",
                int(elapsed * 1000),
                int(interval * 1000),
            )


@dataclass(frozen=True)
class VisionMachine:
    """The fully wired vision system of one server."""

    config: VisionServerConfig
    states: VisionStateMachines
    events: VisionEvents
    results: ResultStore
    sources: Mapping[str, DetectionSource]
    jobs: JobRunner
    lag_watchdog: asyncio.Task | None = None
    camera_stream: CameraStreamPublisher | None = None
    #: MJPEG live stream over HTTP; `None` if disabled or the port was in use.
    camera_http: MjpegServer | None = None
    #: Plant view per OPC 40100-2; `None` if Part 2 is not configured.
    assets: VisionAssetNodes | None = None
    #: Writes `DeviceHealth` of the plant view; `None` without Part 2, without
    #: state nodes, or without an open camera.
    camera_health: CameraHealthPublisher | None = None
    #: Part 10 program as a generic operator interface on the same jobs.
    program: VisionProgram | None = None
    #: `None` if `config.apriltag` is not set. Then there is no
    #: `StartCalibration`/`CaptureCalibrationSample`/`FinishCalibration`/
    #: `AbortCalibration`.
    calibration_session: CalibrationSession | None = None
    #: Layer 2 run (`layer2/run.py`); `None` except on the hand Pi.
    layer2_run: Any = None
    #: Hand-eye calibration (`handeye_session.py`); `None` except on the hand Pi.
    hand_eye_session: Any = None
    #: Cell watch (`cellwatch.py`); `None` except on the ceiling camera.
    cell_watch: Any = None

    async def aclose(self) -> None:
        """Shut down the watchdog, live stream, running job and sources.

        Best effort and idempotent. The caller needs a signal handler, otherwise
        this does not run under systemd.
        """
        if self.calibration_session is not None and self.calibration_session.running:
            await self.calibration_session.abort()
        if self.layer2_run is not None:
            await self.layer2_run.abort("Server faehrt herunter")
        if self.hand_eye_session is not None and self.hand_eye_session.active:
            await self.hand_eye_session.abort()
        if self.cell_watch is not None:
            await self.cell_watch.aclose()
        if self.program is not None:
            await self.program.aclose()
        # Before the stream and sources: an orderly camera close would otherwise
        # time out as a hang, and the last value in the address space would be
        # OFF_SPEC instead of the last real state.
        if self.camera_health is not None:
            await self.camera_health.stop()
        await cancel_and_wait(self.lag_watchdog)
        if self.camera_http is not None:
            await self.camera_http.stop()
        if self.camera_stream is not None:
            await self.camera_stream.stop()
        await self.jobs.cancel_running()
        for source in self.sources.values():
            try:
                await source.close()
            except Exception:
                _log.exception("Schliessen der Quelle '%s' fehlgeschlagen", source.profile_id)
