"""Validierung und Ausfuehrung von Einzeljobs."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from asyncua import ua

from .config import VisionServerConfig
from .detection import DetectionSource
from .errors import VisionErrorCode, VisionJobError
from .events import VisionEvents, fire_result_ready
from .payload import build_error_payload, build_result_payload
from .result_management import PublishedResult, ResultStore
from .state_machine import VisionStateMachines

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobRequest:
    """Die validierten Eingaben eines StartSingleJob-Aufrufs."""

    meas_id: str | None
    part_id: str | None
    recipe_id: str | None
    product_id: str | None
    parameters: tuple[Any, ...]


def coerce_id(value: Any, field: str, max_length: int) -> str | None:
    """Normalisiert eine Id-Eingabe zu einem String.

    Die Ids sind im Nodeset als Strukturen deklariert; ein Client kann sie ohne
    `load_data_type_definitions()` nicht bauen und schickt daher Strings, leere
    Werte oder Variants. Alle drei Formen werden akzeptiert.
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
    """Normalisiert die Parameterliste zu einem Tupel aus Skalaren."""
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
    """Validiert die StartSingleJob-Eingaben; wirft VisionJobError."""
    request = JobRequest(
        meas_id=coerce_id(meas_id, "MeasId", config.max_id_length),
        part_id=coerce_id(part_id, "PartId", config.max_id_length),
        recipe_id=coerce_id(recipe_id, "RecipeId", config.max_id_length),
        product_id=coerce_id(product_id, "ProductId", config.max_id_length),
        parameters=_coerce_parameters(parameters, config),
    )
    if (request.recipe_id or "") not in config.known_recipe_ids:
        known = ", ".join(sorted(x for x in config.known_recipe_ids if x))
        raise VisionJobError(
            VisionErrorCode.UNKNOWN_RECIPE,
            f"Unbekannte RecipeId '{request.recipe_id}' (bekannt: {known})",
        )
    return request


class JobRunner:
    """Fuehrt Einzeljobs aus und haelt Zustaende und Ergebnisknoten konsistent."""

    def __init__(
        self,
        config: VisionServerConfig,
        states: VisionStateMachines,
        events: VisionEvents,
        results: ResultStore,
        sources: dict[str, DetectionSource],
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

    def start_single_job(
        self, meas_id, part_id, recipe_id, product_id, parameters
    ) -> tuple[str, VisionErrorCode]:
        """Prueft Zustand und Eingaben synchron und startet den Job als Task.

        Die Methode ist absichtlich synchron: zwischen der Busy-Pruefung und dem
        Setzen des Flags liegt kein `await`, wodurch die Zulassung auf dem
        Event-Loop atomar ist. Eine `async def`-Variante mit `Lock.locked()`
        waere kein Schutz, weil ein zweiter Aufruf an jedem `await` dazwischen
        durchkommen koennte.
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

    async def _run(self, job_id: str, request: JobRequest) -> None:
        """Durchlaeuft einen Einzeljob inklusive Events und Ergebnisablage."""
        try:
            async with self._lock:
                await self._states.to_single_execution()
                await self._events.job_started.trigger(message=job_id)
                source = self._sources[request.recipe_id or ""]
                detections = await source.acquire_and_detect(request.parameters)
                await self._events.acquisition_done.trigger(message=job_id)

                now = datetime.now(timezone.utc)
                result_id = f"res-{job_id}"
                payload = build_result_payload(
                    vision_system_id=self._config.vision_system_id,
                    result_id=result_id,
                    job_id=job_id,
                    creation_time=now,
                    detections=detections,
                )
                await self._publish(result_id, job_id, now, int(VisionErrorCode.OK), payload)
                await self._states.to_ready()
                await self._events.ready.trigger(message=job_id)
                _log.info("Job %s abgeschlossen", job_id)
        except VisionJobError as error:
            await self._fail(job_id, error)
        except Exception as error:
            _log.exception("Job %s unerwartet fehlgeschlagen", job_id)
            await self._fail(job_id, VisionJobError(VisionErrorCode.INTERNAL, str(error)))
        finally:
            self._busy = False

    async def _publish(
        self, result_id: str, job_id: str, now: datetime, result_state: int, payload: str
    ) -> None:
        """Schreibt die Ergebnisknoten und feuert das ResultReadyEvent."""
        await self._results.publish(
            PublishedResult(
                result_id=result_id,
                job_id=job_id,
                creation_time=now,
                result_state=result_state,
                payload_json=payload,
            )
        )
        await fire_result_ready(
            self._events,
            result_id=result_id,
            payload_json=payload,
            creation_time=now,
            result_state=result_state,
        )

    async def _fail(self, job_id: str, error: VisionJobError) -> None:
        """Meldet den Fehler als Ergebnis und fuehrt den Automaten zurueck.

        Auch ein fehlgeschlagener Job feuert ein ResultReadyEvent — sonst
        erfaehrt ein eventgetriebener Client den Fehler nur per Timeout.
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
            )
            await self._publish(result_id, job_id, now, int(error.code), payload)
            await self._states.abort_to_ready()
            await self._states.to_error(error.message)
            await self._states.recover()
            await self._events.ready.trigger(message=job_id)
        except Exception:
            _log.exception("Fehlerpfad des Jobs %s fehlgeschlagen", job_id)
