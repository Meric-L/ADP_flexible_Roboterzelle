"""Ergebnisablage im Adressraum."""

import logging
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
    """Die Werte, die pro Job in die Ergebnisknoten geschrieben werden."""

    result_id: str
    job_id: str
    creation_time: datetime
    result_state: int
    payload_json: str


class ResultStore:
    """Haelt eine wiederverwendete ResultType-Instanz und einen JSON-Spiegel."""

    def __init__(self, result_node: Node, json_node: Node, children: dict[str, Node]) -> None:
        self._result_node = result_node
        self._json_node = json_node
        self._children = children

    @classmethod
    async def create(cls, space: VisionAddressSpace) -> "ResultStore":
        """Legt die Ergebnisknoten an und macht `ResultContent` beschreibbar.

        `ResultContent` kommt aus dem Nodeset mit einer Null-NodeId als DataType,
        wodurch jeder Write mit BadTypeMismatch scheitert. Der Override auf
        `String` bei ValueRank 1 erlaubt den Array-Write und erhaelt damit die
        `ResultContent[0]`-Semantik des Payload-Schemas.
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
        """Schreibt ein Ergebnis in alle Mandatory-Felder und den JSON-Spiegel.

        `ResultId` und `JobId` sind im Nodeset strukturtypisiert, werden hier
        aber als String geschrieben — dieselbe bewusste Abweichung wie beim
        `StartSingleJob`-Ausgang, damit jeder Client sie lesen kann.
        """
        children = self._children
        await children["ResultId"].write_value(result.result_id, ua.VariantType.String)
        await children["JobId"].write_value(result.job_id, ua.VariantType.String)
        await children["IsPartial"].write_value(False, ua.VariantType.Boolean)
        await children["IsSimulated"].write_value(True, ua.VariantType.Boolean)
        await children["ResultState"].write_value(result.result_state, ua.VariantType.Int32)
        await children["InternalRecipeId"].write_value("", ua.VariantType.String)
        await children["InternalConfigurationId"].write_value("", ua.VariantType.String)
        await children["CreationTime"].write_value(result.creation_time, ua.VariantType.DateTime)
        await children["ResultContent"].write_value(
            ua.Variant([result.payload_json], ua.VariantType.String)
        )
        await self._json_node.write_value(result.payload_json, ua.VariantType.String)
