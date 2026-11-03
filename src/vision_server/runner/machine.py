"""Das fertig verdrahtete Vision-System (`VisionMachine`) und sein Herunterfahren."""

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
    """Loggt, wenn der Event-Loop blockiert war.

    Vergisst eine Quelle `run_blocking`, sieht man es hier statt als
    unerklaerlichen Verbindungsabbruch woanders. Ersetzt `RaspiDevice/Counter`
    als Referenzsignal (doc/projektdoku/altlasten.md A3/A5).
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
    """Das fertig verdrahtete Vision-System eines Servers."""

    config: VisionServerConfig
    states: VisionStateMachines
    events: VisionEvents
    results: ResultStore
    sources: Mapping[str, DetectionSource]
    jobs: JobRunner
    lag_watchdog: asyncio.Task | None = None
    camera_stream: CameraStreamPublisher | None = None
    #: MJPEG-Livestream ueber HTTP; `None`, wenn aus oder der Port belegt war.
    camera_http: MjpegServer | None = None
    #: Anlagensicht nach OPC 40100-2; `None`, wenn Part 2 nicht konfiguriert ist.
    assets: VisionAssetNodes | None = None
    #: Schreibt `DeviceHealth` der Anlagensicht; `None` ohne Part 2, ohne
    #: Zustandsknoten oder ohne geoeffnete Kamera.
    camera_health: CameraHealthPublisher | None = None
    #: Part-10-Programm als generische Bedienoberflaeche auf denselben Jobs.
    program: VisionProgram | None = None
    #: `None`, wenn `config.apriltag` nicht gesetzt ist -- kein
    #: `StartCalibration`/`CaptureCalibrationSample`/`FinishCalibration`/
    #: `AbortCalibration`.
    calibration_session: CalibrationSession | None = None
    #: Layer-2-Lauf (`layer2/run.py`); `None` ausser auf dem Hand-Pi.
    layer2_run: Any = None
    #: Hand-Auge-Kalibrierung (`handeye_session.py`); `None` ausser auf dem Hand-Pi.
    hand_eye_session: Any = None
    #: Zellen-Ueberwachung (`cellwatch.py`); `None` ausser auf der Deckenkamera.
    cell_watch: Any = None

    async def aclose(self) -> None:
        """Faehrt Watchdog, Livestream, laufenden Job und Quelle herunter.

        Best-Effort und idempotent; braucht im Aufrufer einen Signal-Handler,
        sonst laeuft es unter systemd nicht.
        """
        if self.calibration_session is not None and self.calibration_session.running:
            await self.calibration_session.abort()
        if self.layer2_run is not None:
            await self.layer2_run.abort("Server faehrt herunter")
        if self.hand_eye_session is not None and self.hand_eye_session.active:
            await self.hand_eye_session.abort()
        if self.cell_watch is not None:
            await self.cell_watch.aclose()
        # Vor Stream und Quellen: das geordnete Schliessen der Kamera schlaege
        # sonst als Haenger durch, und der letzte Wert im Adressraum waere
        # OFF_SPEC statt des letzten echten Zustands.
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
