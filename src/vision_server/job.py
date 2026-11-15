"""Validation and execution of single jobs (and of continuous operation)."""

import asyncio
import contextlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from asyncua import ua

from .config import VisionServerConfig
from .detection import DetectionSource
from .detection.base import DetectionRequest
from .errors import VisionErrorCode, VisionJobError
from .events import VisionEvents, fire_result_ready
from .payload import build_error_payload, build_result_payload
from .result_management import PublishedResult, ResultStore
from .state_machine import VisionStateMachines

_log = logging.getLogger(__name__)

#: How long `cancel_running` waits for the end of the cancelled job.
_CANCEL_WAIT_S = 2.0


@dataclass(frozen=True)
class JobRequest:
    """The validated inputs of a StartSingleJob call."""

    meas_id: str | None
    part_id: str | None
    recipe_id: str | None
    product_id: str | None
    parameters: tuple[Any, ...]
    profile_id: str = ""

    def to_detection_request(self, job_id: str) -> DetectionRequest:
        """Build the request for the detection source; keeps OPC UA out of `detection/`."""
        return DetectionRequest(
            job_id=job_id,
            recipe_id=self.recipe_id or "",
            parameters=self.parameters,
            meas_id=self.meas_id,
            part_id=self.part_id,
            product_id=self.product_id,
        )


def coerce_id(value: Any, field: str, max_length: int) -> str | None:
    """Normalise an id input to a string.

    The ids are declared as structures in the nodeset; a client cannot build them
    without `load_data_type_definitions()` and therefore sends strings, empty values
    or Variants. All three forms are accepted.
    """
    if value is None:
        return None
    if isinstance(value, ua.Variant):
        return coerce_id(value.Value, field, max_length)
    if hasattr(value, "Id"):
        return coerce_id(value.Id, field, max_length)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        raise VisionJobError(
            VisionErrorCode.INVALID_ARGUMENT,
            f"{field} muss ein String sein, nicht {type(value).__name__}",
        )
    value = value.strip()
    if len(value) > max_length:
        raise VisionJobError(
            VisionErrorCode.INVALID_ARGUMENT,
            f"{field} ist laenger als {max_length} Zeichen",
        )
    return value or None


def _coerce_parameters(value: Any, config: VisionServerConfig) -> tuple[Any, ...]:
    """Normalise the parameter list to a tuple of scalars."""
    if value is None:
        return ()
    if isinstance(value, ua.Variant):
        return _coerce_parameters(value.Value, config)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise VisionJobError(
            VisionErrorCode.INVALID_ARGUMENT, "Parameters muss eine Liste sein"
        )
    if len(value) > config.max_parameters:
        raise VisionJobError(
            VisionErrorCode.INVALID_ARGUMENT,
            f"Parameters hat mehr als {config.max_parameters} Einträge",
        )
    items = []
    for entry in value:
        if isinstance(entry, ua.Variant):
            entry = entry.Value
        if not isinstance(entry, (bool, int, float, str)) and entry is not None:
            raise VisionJobError(
                VisionErrorCode.INVALID_ARGUMENT,
                f"Parameters enthaelt einen nicht skalaren Wert: {type(entry).__name__}",
            )
        items.append(entry)
    return tuple(items)


def build_job_request(
    config: VisionServerConfig, meas_id, part_id, recipe_id, product_id, parameters
) -> JobRequest:
    """Validate the StartSingleJob inputs; raises VisionJobError."""
    request = JobRequest(
        meas_id=coerce_id(meas_id, "MeasId", config.max_id_length),
        part_id=coerce_id(part_id, "PartId", config.max_id_length),
        recipe_id=coerce_id(recipe_id, "RecipeId", config.max_id_length),
        product_id=coerce_id(product_id, "ProductId", config.max_id_length),
        parameters=_coerce_parameters(parameters, config),
    )
    known = ", ".join(sorted(x for x in config.known_recipe_ids if x))
    if not request.recipe_id and "" not in config.known_recipe_ids:
        raise VisionJobError(
            VisionErrorCode.UNKNOWN_RECIPE,
            f"Kein Job gewaehlt: RecipeId ist leer (bekannt: {known})",
        )
    if (request.recipe_id or "") not in config.known_recipe_ids:
        raise VisionJobError(
            VisionErrorCode.UNKNOWN_RECIPE,
            f"Unbekannte RecipeId '{request.recipe_id}' (bekannt: {known})",
        )
    return replace(request, profile_id=config.profile_for(request.recipe_id))


class JobRunner:
    """Run single jobs and keep states and result nodes consistent."""

    def __init__(
        self,
        config: VisionServerConfig,
        states: VisionStateMachines,
        events: VisionEvents,
        results: ResultStore,
        sources: Mapping[str, DetectionSource],
    ) -> None:
        self._config = config
        self._states = states
        self._events = events
        self._results = results
        self._sources = sources
        self._lock = asyncio.Lock()
        self._busy = False
        self._job_counter = 0
        self._task: asyncio.Task | None = None
        #: Set by `stop(abort=True)`. The aborted task only receives `CancelledError`
        #: and otherwise cannot know which command hit it -- but Stop and Abort take
        #: different transitions.
        self._aborting = False
        #: Observers of the job end. Needed by layers that run their own state machine --
        #: e.g. the Part-10 program in `vision_program.py`, which has to return from
        #: `Running` to `Ready`.
        self._finish_listeners: list[Callable[[str, VisionErrorCode], None]] = []

    @property
    def busy(self) -> bool:
        """Return whether a job is running right now.

        For layers that share the same detector/camera (e.g. `CalibrationSession`)
        and therefore must exclude each other.
        """
        return self._busy

    def add_finish_listener(
        self, listener: Callable[[str, VisionErrorCode], None]
    ) -> None:
        """Register an observer for the end of a job.

        The observer is called **synchronously**, because the job end lies in the
        `finally` of a possibly just-cancelled task: an `await` at this point could
        immediately raise `CancelledError` again. Whoever has to work asynchronously
        starts a task of their own.
        """
        self._finish_listeners.append(listener)

    def _notify_finished(self, job_id: str, code: VisionErrorCode) -> None:
        """Call all observers; an error there must not affect the job."""
        for listener in self._finish_listeners:
            try:
                listener(job_id, code)
            except Exception:
                _log.exception("Abschluss-Beobachter von Job %s fehlgeschlagen", job_id)

    async def cancel_running(self) -> None:
        """Cancel a job that is still running; used for shutdown.

        Best effort: errors during the cancellation are swallowed, since the process
        is shutting down anyway. For the `Stop` method use `stop()` instead, which
        reports the failure as a `VisionErrorCode`.
        """
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(task, _CANCEL_WAIT_S)

    async def stop(self, *, abort: bool = False) -> VisionErrorCode:
        """Cancel a running job; OK if none is running.

        Fire-and-forget from the frontend, regardless of the current state -- unlike
        `start_single_job`, there is deliberately **no** `is_ready()` guard here. Waits
        for the complete abort (including the state transition back to `Ready` in
        `_cancel`), so that a `StartSingleJob` right after it does not hit a job that
        is still cleaning up.
        """
        task = self._task
        if task is None or task.done():
            return VisionErrorCode.OK
        self._aborting = abort
        task.cancel()
        try:
            await asyncio.wait_for(task, self._config.stop_timeout)
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            _log.error(
                "Job liess sich nicht innerhalb von %.1f s abbrechen",
                self._config.stop_timeout,
            )
            return VisionErrorCode.INTERNAL
        except Exception:
            _log.exception("Abbruch des laufenden Jobs fehlgeschlagen")
            return VisionErrorCode.INTERNAL
        return VisionErrorCode.OK

    def start_single_job(
        self, meas_id, part_id, recipe_id, product_id, parameters
    ) -> tuple[str, VisionErrorCode]:
        """Check state and inputs synchronously and start the job as a task.

        The method is deliberately synchronous: there is no `await` between the busy
        check and setting the flag, which makes the admission atomic on the event loop.
        An `async def` variant with `Lock.locked()` would not protect, because a second
        call could slip through at every `await` in between.
        """
        if self._busy:
            return "", VisionErrorCode.BUSY
        if not self._states.is_ready():
            outer, inner = self._states.state_names()
            _log.warning("StartSingleJob im Zustand %s/%s abgelehnt", outer, inner)
            return "", VisionErrorCode.INVALID_STATE
        try:
            request = build_job_request(
                self._config, meas_id, part_id, recipe_id, product_id, parameters
            )
        except VisionJobError as error:
            _log.warning("StartSingleJob abgelehnt: %s", error.message)
            return "", error.code
        self._busy = True
        self._job_counter += 1
        job_id = f"job-{self._job_counter:06d}"
        self._task = asyncio.create_task(self._run(job_id, request))
        return job_id, VisionErrorCode.OK

    def start_continuous(
        self, meas_id, part_id, recipe_id, product_id, parameters
    ) -> tuple[str, VisionErrorCode]:
        """Like `start_single_job`, but the job only ends on Stop or Abort.

        The same atomic admission: synchronous, with no `await` between the check and
        setting `_busy`.
        """
        if self._busy:
            return "", VisionErrorCode.BUSY
        if not self._states.is_ready():
            outer, inner = self._states.state_names()
            _log.warning("StartContinuous im Zustand %s/%s abgelehnt", outer, inner)
            return "", VisionErrorCode.INVALID_STATE
        try:
            request = build_job_request(
                self._config, meas_id, part_id, recipe_id, product_id, parameters
            )
        except VisionJobError as error:
            _log.warning("StartContinuous abgelehnt: %s", error.message)
            return "", error.code
        self._busy = True
        self._job_counter += 1
        job_id = f"job-{self._job_counter:06d}"
        self._task = asyncio.create_task(self._run_continuous(job_id, request))
        return job_id, VisionErrorCode.OK

    async def _run_continuous(self, job_id: str, request: JobRequest) -> None:
        """Detect continuously until the task is cancelled.

        Each pass publishes its own result under its own id (`<job>-0001`, `-0002`, ...),
        so that a client can tell the sequence apart. A single failed pass ends continuous
        operation -- this is intentional: if it kept running, the same fault would produce
        the same error message at one-second intervals.
        """
        source = self._sources[request.profile_id]
        cycle = 0
        outcome = VisionErrorCode.OK
        try:
            async with self._lock:
                await self._states.to_continuous_execution()
                await self._events.job_started.trigger(message=job_id)
                while True:
                    cycle += 1
                    cycle_id = f"{job_id}-{cycle:04d}"
                    detections = await asyncio.wait_for(
                        source.acquire_and_detect(request.to_detection_request(cycle_id)),
                        self._config.job_timeout,
                    )
                    await self._events.acquisition_done.trigger(message=cycle_id)
                    now = datetime.now(timezone.utc)
                    result_id = f"res-{cycle_id}"
                    payload = build_result_payload(
                        vision_system_id=self._config.vision_system_id,
                        result_id=result_id,
                        job_id=cycle_id,
                        creation_time=now,
                        detections=detections,
                        frame_id=source.frame_id or self._config.frame_id,
                        frame_convention=source.frame_convention,
                        configuration_id=source.configuration_id,
                        world_tags=source.world_tags,
                        camera_localization=source.camera_localization,
                    )
                    await self._publish(
                        source, request, result_id, cycle_id, now,
                        int(VisionErrorCode.OK), payload,
                    )
                    await asyncio.sleep(self._config.continuous_interval_s)
        except asyncio.CancelledError:
            outcome = VisionErrorCode.CANCELLED
            await self._cancel(source, request, job_id, continuous=True)
            raise
        except TimeoutError:
            outcome = VisionErrorCode.DETECTION_FAILED
            await self._fail(
                source, request, job_id,
                VisionJobError(
                    VisionErrorCode.DETECTION_FAILED,
                    f"Erkennung ueberschritt {self._config.job_timeout:g} s",
                ),
                continuous=True,
            )
        except VisionJobError as error:
            outcome = error.code
            await self._fail(source, request, job_id, error, continuous=True)
        except Exception as error:
            outcome = VisionErrorCode.INTERNAL
            _log.exception("Dauerbetrieb %s unerwartet fehlgeschlagen", job_id)
            await self._fail(
                source, request, job_id,
                VisionJobError(VisionErrorCode.INTERNAL, str(error)),
                continuous=True,
            )
        finally:
            self._busy = False
            self._notify_finished(job_id, outcome)

    async def _run(self, job_id: str, request: JobRequest) -> None:
        """Run a single job, including events and result storage."""
        source = self._sources[request.profile_id]
        outcome = VisionErrorCode.OK
        try:
            async with self._lock:
                await self._states.to_single_execution()
                await self._events.job_started.trigger(message=job_id)
                # Without a timeout `_busy` would stay set forever when the camera hangs:
                # every further call would get BUSY, Abort would not be linked to it, and
                # the only rescue would be a service restart. wait_for aborts the coroutine,
                # but cannot kill the worker thread.
                detections = await asyncio.wait_for(
                    source.acquire_and_detect(request.to_detection_request(job_id)),
                    self._config.job_timeout,
                )
                await self._events.acquisition_done.trigger(message=job_id)

                now = datetime.now(timezone.utc)
                result_id = f"res-{job_id}"
                payload = build_result_payload(
                    vision_system_id=self._config.vision_system_id,
                    result_id=result_id,
                    job_id=job_id,
                    creation_time=now,
                    detections=detections,
                    frame_id=source.frame_id or self._config.frame_id,
                    frame_convention=source.frame_convention,
                    configuration_id=source.configuration_id,
                    world_tags=source.world_tags,
                    camera_localization=source.camera_localization,
                )
                await self._publish(
                    source, request, result_id, job_id, now, int(VisionErrorCode.OK), payload
                )
                await self._states.to_ready()
                await self._events.ready.trigger(message=job_id)
                _log.info("Job %s abgeschlossen", job_id)
        except asyncio.CancelledError:
            # `stop()` cancels this task (`Stop` method). Clean up and roll the automaton
            # back before the cancellation is propagated further up -- otherwise the
            # automaton would stay stuck in SingleExecution forever and every further job
            # would fail.
            outcome = VisionErrorCode.CANCELLED
            await self._cancel(source, request, job_id)
            raise
        except TimeoutError:
            outcome = VisionErrorCode.DETECTION_FAILED
            await self._fail(
                source,
                request,
                job_id,
                VisionJobError(
                    VisionErrorCode.DETECTION_FAILED,
                    f"Erkennung ueberschritt {self._config.job_timeout:g} s",
                ),
            )
        except VisionJobError as error:
            outcome = error.code
            await self._fail(source, request, job_id, error)
        except Exception as error:
            outcome = VisionErrorCode.INTERNAL
            _log.exception("Job %s unerwartet fehlgeschlagen", job_id)
            await self._fail(
                source, request, job_id, VisionJobError(VisionErrorCode.INTERNAL, str(error))
            )
        finally:
            self._busy = False
            self._notify_finished(job_id, outcome)

    async def _publish(
        self,
        source: DetectionSource,
        request: JobRequest,
        result_id: str,
        job_id: str,
        now: datetime,
        result_state: int,
        payload: str,
    ) -> None:
        """Write the result nodes and fire the ResultReadyEvent."""
        await self._results.publish(
            PublishedResult(
                result_id=result_id,
                job_id=job_id,
                creation_time=now,
                result_state=result_state,
                payload_json=payload,
                is_simulated=source.is_simulated,
                recipe_id=request.recipe_id or "",
                configuration_id=source.configuration_id,
            )
        )
        await fire_result_ready(
            self._events,
            result_id=result_id,
            payload_json=payload,
            creation_time=now,
            result_state=result_state,
            is_simulated=source.is_simulated,
        )

    async def _fail(
        self,
        source: DetectionSource,
        request: JobRequest,
        job_id: str,
        error: VisionJobError,
        continuous: bool = False,
    ) -> None:
        """Report the error as a result and roll the state machine back.

        A failed job also fires a ResultReadyEvent -- otherwise an event-driven client
        would only learn of the error through a timeout.
        """
        _log.error("Job %s fehlgeschlagen: %s (%s)", job_id, error.message, error.code.name)
        try:
            now = datetime.now(timezone.utc)
            result_id = f"res-{job_id}"
            payload = build_error_payload(
                vision_system_id=self._config.vision_system_id,
                result_id=result_id,
                job_id=job_id,
                creation_time=now,
                code=error.code,
                message=error.message,
                frame_id=source.frame_id or self._config.frame_id,
                frame_convention=source.frame_convention,
                configuration_id=source.configuration_id,
                world_tags=source.world_tags,
                camera_localization=source.camera_localization,
            )
            await self._publish(
                source, request, result_id, job_id, now, int(error.code), payload
            )
            if continuous:
                await self._states.continuous_to_ready(stopped=False)
            else:
                await self._states.abort_to_ready()
            await self._states.to_error(error.message)
            await self._states.recover()
            await self._events.ready.trigger(message=job_id)
        except Exception:
            _log.exception("Fehlerpfad des Jobs %s fehlgeschlagen", job_id)

    async def _cancel(
        self,
        source: DetectionSource,
        request: JobRequest,
        job_id: str,
        continuous: bool = False,
    ) -> None:
        """Report the user abort as a result and roll the state machine back.

        Unlike `_fail`: no detour to `Error` -- a `Stop` is a deliberate command, not an
        error state. Runs while `_busy` is still set (see `_run`), so there is no
        competition with a new job.
        """
        _log.info(
            "Job %s durch %s abgebrochen", job_id, "Abort" if self._aborting else "Stop"
        )
        try:
            now = datetime.now(timezone.utc)
            result_id = f"res-{job_id}"
            payload = build_error_payload(
                vision_system_id=self._config.vision_system_id,
                result_id=result_id,
                job_id=job_id,
                creation_time=now,
                code=VisionErrorCode.CANCELLED,
                message=f"Job durch {'Abort' if self._aborting else 'Stop'} abgebrochen",
                frame_id=source.frame_id or self._config.frame_id,
                frame_convention=source.frame_convention,
                configuration_id=source.configuration_id,
            )
            await self._publish(
                source,
                request,
                result_id,
                job_id,
                now,
                int(VisionErrorCode.CANCELLED),
                payload,
            )
            if continuous:
                await self._states.continuous_to_ready(stopped=not self._aborting)
            elif self._aborting:
                await self._states.abort_to_ready()
            else:
                await self._states.stop_to_ready()
            await self._events.ready.trigger(message=job_id)
        except Exception:
            _log.exception("Abbruchpfad des Jobs %s fehlgeschlagen", job_id)
