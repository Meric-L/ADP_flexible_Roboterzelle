"""Event generators of the vision system.

Creates the four job events on the VisionSystem node and fires the
ResultReady event with its JSON payload.
"""

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
    """The four job events. All use the VisionSystem node as source node."""

    job_started: EventGenerator
    acquisition_done: EventGenerator
    result_ready: EventGenerator
    ready: EventGenerator


async def create_event_generators(space: VisionAddressSpace) -> VisionEvents:
    """Create all event generators on the VisionSystem node.

    The source node matters. asyncua only delivers events to subscriptions
    placed exactly on this node. The HasNotifier reference does not make the
    server bubble events up.
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
    events: VisionEvents,
    *,
    result_id: str,
    payload_json: str,
    creation_time,
    result_state: int,
    is_simulated: bool = True,
    is_partial: bool = False,
) -> None:
    """Fire the ResultReady event with the JSON payload in `ResultContent[0]`.

    Only the fields that can be decoded without `load_data_type_definitions()`
    are filled. The mandatory fields with structure types (`ResultId`, `JobId`,
    `InternalRecipeId`, `InternalConfigurationId`) stay empty. Their values are
    in the payload, because an asyncua client cannot decode the ExtensionObjects
    of this nodeset (asyncua issue #1693).

    Each field must be assigned as a finished `ua.Variant`. asyncua takes the
    field type from the nodeset and gets VariantType Null for these fields. A
    raw Python value would then reach the client as `None`.
    """
    event = events.result_ready.event
    event.ResultContent = ua.Variant([payload_json], ua.VariantType.String)
    event.CreationTime = ua.Variant(creation_time, ua.VariantType.DateTime)
    event.IsPartial = ua.Variant(is_partial, ua.VariantType.Boolean)
    event.IsSimulated = ua.Variant(is_simulated, ua.VariantType.Boolean)
    event.ResultState = ua.Variant(result_state, ua.VariantType.Int32)
    await events.result_ready.trigger(message=result_id)
