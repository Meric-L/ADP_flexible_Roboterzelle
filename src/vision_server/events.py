"""Event-Generatoren des Vision-Systems."""

import logging
from dataclasses import dataclass

from asyncua import ua
from asyncua.server.event_generator import EventGenerator

from .address_space import VisionAddressSpace
from .nodeset_ids import (
    EVENT_ACQUISITION_DONE,
    EVENT_JOB_STARTED,
    EVENT_READY,
    EVENT_RESULT_READY,
    mv,
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisionEvents:
    """Die vier Job-Events, alle mit dem VisionSystem als Quellknoten."""

    job_started: EventGenerator
    acquisition_done: EventGenerator
    result_ready: EventGenerator
    ready: EventGenerator


async def create_event_generators(space: VisionAddressSpace) -> VisionEvents:
    """Erzeugt alle Event-Generatoren am VisionSystem-Knoten.

    Der Quellknoten ist entscheidend: asyncua liefert Events nur an
    Subscriptions aus, die exakt auf diesem Knoten sitzen — die
    HasNotifier-Referenz bewirkt serverseitig kein Bubbling.
    """
    async def generator(identifier: int) -> EventGenerator:
        return await space.server.get_event_generator(
            mv(identifier, space.mv_idx), space.vision_system
        )

    return VisionEvents(
        job_started=await generator(EVENT_JOB_STARTED),
        acquisition_done=await generator(EVENT_ACQUISITION_DONE),
        result_ready=await generator(EVENT_RESULT_READY),
        ready=await generator(EVENT_READY),
    )


async def fire_result_ready(
    events: VisionEvents, *, result_id: str, payload_json: str, creation_time, result_state: int
) -> None:
    """Feuert das ResultReadyEvent mit dem JSON-Payload in `ResultContent[0]`.

    Nur die ohne `load_data_type_definitions()` dekodierbaren Felder werden
    belegt. Die strukturtypisierten Mandatory-Felder (`ResultId`, `JobId`,
    `InternalRecipeId`, `InternalConfigurationId`) bleiben leer — ihre Werte
    stehen im Payload, weil ein asyncua-Client ExtensionObjects dieses Nodesets
    nicht dekodieren kann (asyncua-Issue #1693).

    Jedes Feld muss als fertiger `ua.Variant` zugewiesen werden: asyncua leitet
    den Feldtyp aus dem Nodeset ab und erhaelt fuer diese Felder VariantType
    Null, wodurch ein roher Python-Wert beim Client als `None` ankommt.
    """
    event = events.result_ready.event
    event.ResultContent = ua.Variant([payload_json], ua.VariantType.String)
    event.CreationTime = ua.Variant(creation_time, ua.VariantType.DateTime)
    event.IsPartial = ua.Variant(False, ua.VariantType.Boolean)
    event.IsSimulated = ua.Variant(True, ua.VariantType.Boolean)
    event.ResultState = ua.Variant(result_state, ua.VariantType.Int32)
    await events.result_ready.trigger(message=result_id)
