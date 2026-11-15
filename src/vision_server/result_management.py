"""Result storage in the address space."""

import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from asyncua import ua
from asyncua.common.instantiate_util import instantiate
from asyncua.common.node import Node

from .address_space import VisionAddressSpace
from .nodeset_ids import RESULT_TYPE, mv

_log = logging.getLogger(__name__)

RESULT_BROWSE_NAME = "LatestResult"
RESULT_JSON_BROWSE_NAME = "LatestResultJson"


@dataclass(frozen=True)
class PublishedResult:
    """The values written to the result nodes for each job."""

    result_id: str
    job_id: str
    creation_time: datetime
    result_state: int
    payload_json: str
    is_simulated: bool = True
    is_partial: bool = False
    recipe_id: str = ""
    configuration_id: str = ""


class ResultStore:
    """Holds a reused ResultType instance and a JSON mirror."""

    #: Number of results `result_for_job` keeps in memory.
    HISTORY = 20

    def __init__(self, result_node: Node, json_node: Node, children: dict[str, Node]) -> None:
        self._result_node = result_node
        self._json_node = json_node
        self._children = children
        #: The last payloads per JobId. The cell watch uses the result that the
        #: frontend has in its viewport as its reference.
        self._history: OrderedDict[str, str] = OrderedDict()

    def result_for_job(self, job_id: str) -> str | None:
        """Return the payload of one of the last `HISTORY` results, else `None`."""
        return self._history.get(job_id)

    @property
    def json_node(self) -> Node:
        """The JSON mirror node `LatestResultJson`.

        Public because the Part 10 program additionally links it under itself --
        as a reference to the same node, not as a copy.
        """
        return self._json_node

    @classmethod
    async def create(cls, space: VisionAddressSpace) -> "ResultStore":
        """Create the result nodes and make `ResultContent` writable.

        `ResultContent` comes from the nodeset with a null NodeId as DataType,
        so every write fails with BadTypeMismatch. Overriding it to `String` with
        ValueRank 1 allows the array write and keeps the `ResultContent[0]`
        semantics of the payload schema.
        """
        result_node = (
            await instantiate(
                space.results_folder,
                space.server.get_node(mv(RESULT_TYPE, space.mv_idx)),
                bname=f"{space.own_idx}:{RESULT_BROWSE_NAME}",
            )
        )[0]
        names = (
            "ResultId",
            "JobId",
            "IsPartial",
            "IsSimulated",
            "ResultState",
            "InternalRecipeId",
            "InternalConfigurationId",
            "CreationTime",
            "ResultContent",
        )
        children = {
            name: await result_node.get_child(f"{space.mv_idx}:{name}") for name in names
        }
        await children["ResultContent"].write_attribute(
            ua.AttributeIds.DataType,
            ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.String), ua.VariantType.NodeId)),
        )
        json_node = await space.vision_system.add_variable(
            ua.NodeId(f"{space.config.vision_system_name}.{RESULT_JSON_BROWSE_NAME}", space.own_idx),
            ua.QualifiedName(RESULT_JSON_BROWSE_NAME, space.own_idx),
            "",
            varianttype=ua.VariantType.String,
        )
        return cls(result_node, json_node, children)

    async def publish(self, result: PublishedResult) -> None:
        """Write a result to all mandatory fields and the JSON mirror.

        `ResultId` and `JobId` are structure-typed in the nodeset, but are written
        here as String. This is the same deliberate deviation as for the
        `StartSingleJob` output, so that every client can read them.
        """
        children = self._children
        await children["ResultId"].write_value(result.result_id, ua.VariantType.String)
        await children["JobId"].write_value(result.job_id, ua.VariantType.String)
        await children["IsPartial"].write_value(result.is_partial, ua.VariantType.Boolean)
        await children["IsSimulated"].write_value(result.is_simulated, ua.VariantType.Boolean)
        await children["ResultState"].write_value(result.result_state, ua.VariantType.Int32)
        await children["InternalRecipeId"].write_value(result.recipe_id, ua.VariantType.String)
        await children["InternalConfigurationId"].write_value(
            result.configuration_id, ua.VariantType.String
        )
        await children["CreationTime"].write_value(result.creation_time, ua.VariantType.DateTime)
        await children["ResultContent"].write_value(
            ua.Variant([result.payload_json], ua.VariantType.String)
        )
        await self._json_node.write_value(result.payload_json, ua.VariantType.String)
        self._history[result.job_id] = result.payload_json
        self._history.move_to_end(result.job_id)
        while len(self._history) > self.HISTORY:
            self._history.popitem(last=False)
