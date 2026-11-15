"""Strategy interface for the detection stage (part 4.5 of the plan)."""

import asyncio
import functools
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    """A detected module with its pose in meters and a quaternion in xyzw order."""

    module_id: str
    instance_id: str
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    confidence: float
    attributes: dict[str, Any] = field(default_factory=dict)
    bounding_box: tuple[float, ...] | None = None  # 2D profile, part 4.5


@dataclass(frozen=True)
class DetectionRequest:
    """Job inputs for a detection source.

    A single object instead of positional arguments, so that adding a field
    does not break every source.
    """

    job_id: str
    recipe_id: str = ""
    parameters: tuple[Any, ...] = ()
    meas_id: str | None = None
    part_id: str | None = None
    product_id: str | None = None
    deadline: float | None = None


class DetectionSource(ABC):
    """Provides detections for a job. Knows nothing about OPC UA details.

    May keep state. The instance lives for the whole process runtime, so a
    camera stays open across jobs.
    """

    profile_id: str

    #: Controls `IsSimulated`. A real camera source sets `False`.
    is_simulated: bool = True

    #: Reference frame of the poses. `""` means the value from the server configuration.
    frame_id: str = ""

    #: Direction +Z points in, e.g. "z_forward_x_right_y_down".
    frame_convention: str = ""

    #: Identity of the active configuration, e.g. calibration file plus mtime.
    configuration_id: str = ""

    #: World tags seen in the last job, ready for JSON (`worldTags` in the payload).
    #: Empty for sources without a world frame. Also applies to a failed job: an
    #: image with only world tags ends in `DETECTION_FAILED`, but it is exactly
    #: the image needed to check the map.
    world_tags: tuple = ()

    #: How the camera pose of the last job was determined, ready for JSON
    #: (`cameraLocalization` in the payload); ``None`` without a camera pose.
    #: Like `world_tags`, also kept for a failed job.
    camera_localization: dict | None = None

    _executor: ThreadPoolExecutor | None = None

    @abstractmethod
    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Capture and detect. Raises VisionJobError on detection failure."""

    async def open(self) -> None:
        """Acquire resources. Idempotent.

        Runs before the transition to Operational. If it raises, the state
        machine stays in Preoperational and jobs get INVALID_STATE.
        """

    async def close(self) -> None:
        """Release resources. Idempotent, also after a failed `open()`."""
        await self.shutdown_executor()

    async def run_blocking(self, func, /, *args) -> Any:
        """Run blocking work in a dedicated thread. Every OpenCV call goes through this.

        Running it directly on the loop would freeze the state machine, the events
        and the 1 Hz loop of the cell server. A private single-worker pool is used
        instead of `asyncio.to_thread`, because that shares the default executor
        and does not serialize camera access. A hung worker blocks this profile
        for good, so a source also needs its own capture timeouts.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool(), functools.partial(func, *args))

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"vision-{self.profile_id}"
            )
        return self._executor

    async def shutdown_executor(self) -> None:
        """Shut down the worker thread, if there is one."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
