"""Validierung und Ausfuehrung von Einzeljobs."""

import asyncio
import contextlib
import logging
from collections.abc import Mapping, Sequence
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


@dataclass(frozen=True)
class JobRequest:
    """Die validierten Eingaben eines StartSingleJob-Aufrufs."""

    meas_id: str | None
    part_id: str | None
    recipe_id: str | None
    product_id: str | None
    parameters: tuple[Any, ...]
    profile_id: str = ""

    def to_detection_request(self, job_id: str) -> DetectionRequest:
        """Uebergabe an die Erkennungsquelle; haelt OPC UA aus `detection/` heraus."""
        return DetectionRequest(
            job_id=job_id,
            recipe_id=self.recipe_id or "",
            parameters=self.parameters,
            meas_id=self.meas_id,
            part_id=self.part_id,
            product_id=self.product_id,
        )


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
    return replace(request, profile_id=config.profile_for(request.recipe_id))


class JobRunner:
    """Fuehrt Einzeljobs aus und haelt Zustaende und Ergebnisknoten konsistent."""

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

    async def cancel_running(self, timeout: float = 2.0) -> None:
        """Bricht einen noch laufenden Job ab; fuer das Herunterfahren.

        Best-Effort: Fehler beim Abbruch werden verschluckt, der Prozess
        faehrt ohnehin gerade herunter. Fuer die `Stop`-Methode stattdessen
        `stop()` verwenden, die den Fehlschlag als `VisionErrorCode` meldet.
        """
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(task, timeout)

    async def stop(self) -> VisionErrorCode:
        """Bricht einen laufenden Job ab; OK, wenn gerade keiner laeuft.

        Fire-and-forget vom Frontend, unabhaengig vom aktuellen Zustand --
        anders als `start_single_job` gibt es hier bewusst **keinen**
        `is_ready()`-Guard. Wartet auf den vollstaendigen Abbruch (inkl.
        Zustandswechsel zurueck nach `Ready` in `_cancel`), damit ein
        `StartSingleJob` direkt danach nicht auf einen noch aufraeumenden
        Job trifft.
        """
        task = self._task
        if task is None or task.done():
            return VisionErrorCode.OK
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
        source = self._sources[request.profile_id]
        try:
            async with self._lock:
                await self._states.to_single_execution()
                await self._events.job_started.trigger(message=job_id)
                # Ohne Timeout bliebe `_busy` bei einer haengenden Kamera fuer
                # immer gesetzt: jeder weitere Aufruf BUSY, Abort nicht
                # verlinkt, Rettung nur per Serviceneustart. wait_for bricht
                # die Koroutine ab, kann den Worker-Thread aber nicht toeten.
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
                )
                await self._publish(
                    source, request, result_id, job_id, now, int(VisionErrorCode.OK), payload
                )
                await self._states.to_ready()
                await self._events.ready.trigger(message=job_id)
                _log.info("Job %s abgeschlossen", job_id)
        except asyncio.CancelledError:
            # `stop()` bricht diesen Task ab (`Stop`-Methode). Aufraeumen und
            # den Automaten zurueckfahren, bevor die Cancellation weiter nach
            # oben durchgereicht wird -- sonst bliebe der Automat fuer immer
            # in SingleExecution haengen und jeder weitere Job schluege fehl.
            await self._cancel(source, request, job_id)
            raise
        except TimeoutError:
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
            await self._fail(source, request, job_id, error)
        except Exception as error:
            _log.exception("Job %s unerwartet fehlgeschlagen", job_id)
            await self._fail(
                source, request, job_id, VisionJobError(VisionErrorCode.INTERNAL, str(error))
            )
        finally:
            self._busy = False

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
        """Schreibt die Ergebnisknoten und feuert das ResultReadyEvent."""
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
    ) -> None:
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
                frame_id=source.frame_id or self._config.frame_id,
                frame_convention=source.frame_convention,
                configuration_id=source.configuration_id,
            )
            await self._publish(
                source, request, result_id, job_id, now, int(error.code), payload
            )
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
    ) -> None:
        """Meldet den Nutzerabbruch als Ergebnis und fuehrt den Automaten zurueck.

        Anders als `_fail`: kein Ausflug nach `Error` — ein `Stop` ist ein
        gewolltes Kommando, kein Fehlerzustand. Laeuft, waehrend `_busy` noch
        gesetzt ist (siehe `_run`), also ohne Konkurrenz zu einem neuen Job.
        """
        _log.info("Job %s durch Stop abgebrochen", job_id)
        try:
            now = datetime.now(timezone.utc)
            result_id = f"res-{job_id}"
            payload = build_error_payload(
                vision_system_id=self._config.vision_system_id,
                result_id=result_id,
                job_id=job_id,
                creation_time=now,
                code=VisionErrorCode.CANCELLED,
                message="Job durch Stop abgebrochen",
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
            await self._states.stop_to_ready()
            await self._events.ready.trigger(message=job_id)
        except Exception:
            _log.exception("Abbruchpfad des Jobs %s fehlgeschlagen", job_id)
