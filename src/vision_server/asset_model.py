"""OPC 40100-2 (AMCM): what this vision system is made of, and how it is doing.

Part 1 answers "how do I operate the system"; Part 2 answers "what is it built
from and what is its condition" -- computing device, image sensor, lens, with
identification per component. It carries no runtime coupling to the job path:
nothing here can fail a job, and a job cannot change anything here.

Only the folders we actually fill are created. Instantiating the type with all
its optional folders would also materialise their `<Placeholder>` templates as
real nodes -- a node literally named `<ComputingDevice>` is wrong, and removing
them afterwards measured 9 s for 26 deletions, because asyncua's recursive
delete is pathologically slow. Building up instead of tearing down leaves ~50
nodes rather than ~700 and runs in well under a second.
"""

import logging
from dataclasses import dataclass

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate
from asyncua.common.node import Node

from .address_space import VisionAddressSpace
from .nodeset_ids import (
    DI_NAMESPACE_URI,
    VISION_COMPUTING_DEVICE_TYPE,
    VISION_IMAGE_SENSOR_TYPE,
    VISION_ITEM_FOLDER_TYPE,
    VISION_LENS_TYPE,
    VISION_SYSTEM_ASSET_TYPE,
    node_id,
)
from .profiles import AssetConfig

_log = logging.getLogger(__name__)

#: DI Identification fields we can fill honestly. What we do not know stays
#: empty -- an invented serial number is worse than a blank one.
_IDENTIFICATION_FIELDS = ("Manufacturer", "Model", "SerialNumber", "SoftwareRevision")

#: Fields the spec types as LocalizedText rather than String.
_LOCALIZED_FIELDS = frozenset({"Manufacturer", "Model"})

#: Mandatory in DI's Identification, so instantiation always creates them.
_MANDATORY_FIELDS = frozenset({"Manufacturer", "SerialNumber", "ProductInstanceUri"})


@dataclass(frozen=True)
class VisionAssetNodes:
    """The asset view's nodes, as far as anything else needs them."""

    root: Node
    computing_device: Node | None = None
    image_sensor: Node | None = None
    lens: Node | None = None


async def _write_identification(
    node: Node, di_idx: int, own_idx: int, values: dict[str, str]
) -> None:
    """Fill DI's Identification, adding the optional fields we can answer.

    The type is instantiated without optional children, so only Manufacturer,
    SerialNumber and ProductInstanceUri exist up front. Model and
    SoftwareRevision are optional in DI -- a server publishes them when it
    supports them, which is exactly what adding them here means. Fields we have
    no value for stay absent rather than empty.
    """
    try:
        identification = await node.get_child(f"{di_idx}:Identification")
    except Exception:
        _log.debug("Kein Identification-Block unter %s", node.nodeid.to_string())
        return
    for field in _IDENTIFICATION_FIELDS:
        value = values.get(field, "")
        if not value:
            continue
        content = ua.LocalizedText(value) if field in _LOCALIZED_FIELDS else value
        try:
            if field in _MANDATORY_FIELDS:
                child = await identification.get_child(f"{di_idx}:{field}")
                await child.write_value(content)
            else:
                await identification.add_variable(
                    ua.NodeId(f"{node.nodeid.Identifier}.{field}", own_idx),
                    ua.QualifiedName(field, di_idx),
                    content,
                )
        except Exception:
            _log.debug("Identification-Feld %s nicht schreibbar", field, exc_info=True)


async def _ensure_folder(
    server: Server, root: Node, amcm_idx: int, own_idx: int, folder_name: str
) -> Node:
    """The component folder, created on first use."""
    try:
        return await root.get_child(f"{amcm_idx}:{folder_name}")
    except Exception:
        nodes = await instantiate(
            root,
            server.get_node(node_id(VISION_ITEM_FOLDER_TYPE, amcm_idx)),
            nodeid=ua.NodeId(f"VisionMachine.VisionAsset.{folder_name}", own_idx),
            bname=ua.QualifiedName(folder_name, amcm_idx),
            instantiate_optional=False,
        )
        folder = nodes[0]
        # `<VisionItem>` traegt die Modelling Rule MandatoryPlaceholder, und
        # asyncua legt es deshalb als echten Knoten an -- unabhaengig vom
        # Optional-Schalter. Ein Platzhalter ist aber eine Vorlage des Typs,
        # keine Instanz: er gehoert nicht in einen laufenden Adressraum.
        # Ein Loeschen je angelegtem Ordner, nicht ueber den ganzen Typ --
        # asyncuas rekursives Loeschen ist teuer (gemessen ~0,35 s je Knoten).
        for child in await folder.get_children():
            if (await child.read_browse_name()).Name.startswith("<"):
                await child.delete(recursive=True)
        return folder


async def _add_component(
    server: Server,
    folder: Node,
    type_identifier: int,
    amcm_idx: int,
    di_idx: int,
    own_idx: int,
    name: str,
    values: dict[str, str],
) -> Node | None:
    """Instantiate one component into its folder and identify it."""
    try:
        nodes = await instantiate(
            folder,
            server.get_node(node_id(type_identifier, amcm_idx)),
            nodeid=ua.NodeId(f"VisionMachine.VisionAsset.{name}", own_idx),
            bname=f"{own_idx}:{name}",
            instantiate_optional=False,
        )
    except Exception:
        _log.exception("Komponente '%s' konnte nicht angelegt werden", name)
        return None
    await _write_identification(nodes[0], di_idx, own_idx, values)
    return nodes[0]


async def attach_asset_model(
    space: VisionAddressSpace, config: AssetConfig
) -> VisionAssetNodes | None:
    """Build the Part 2 asset view under the vision system.

    Returns `None` when Part 2 is not loaded. Never raises: the asset view is a
    service and maintenance aid, and losing it must not keep the server from
    running the job path.
    """
    if space.amcm_idx is None:
        return None
    server, amcm_idx, own_idx = space.server, space.amcm_idx, space.own_idx
    try:
        di_idx = await server.get_namespace_index(DI_NAMESPACE_URI)
        nodes = await instantiate(
            space.vision_system,
            server.get_node(node_id(VISION_SYSTEM_ASSET_TYPE, amcm_idx)),
            nodeid=ua.NodeId("VisionMachine.VisionAsset", own_idx),
            bname=f"{own_idx}:VisionAsset",
            instantiate_optional=False,
        )
    except Exception:
        _log.exception("Anlagensicht (OPC 40100-2) nicht aufbaubar")
        return None

    root = nodes[0]
    await _write_identification(
        root,
        di_idx,
        own_idx,
        {
            "Manufacturer": config.manufacturer,
            "Model": config.model,
            "SerialNumber": config.serial_number,
            "SoftwareRevision": config.software_revision,
        },
    )

    # Singular je Ordner ausgeschrieben: "Lenses"[:-1] waere "Lense".
    components: dict[str, Node | None] = {}
    for attribute, folder_name, item_name, type_identifier, model in (
        ("computing_device", "ComputingDevices", "ComputingDevice",
         VISION_COMPUTING_DEVICE_TYPE, config.computing_device_model),
        ("image_sensor", "ImageSensors", "ImageSensor",
         VISION_IMAGE_SENSOR_TYPE, config.image_sensor_model),
        ("lens", "Lenses", "Lens", VISION_LENS_TYPE, config.lens_model),
    ):
        if not model:
            components[attribute] = None
            continue
        folder = await _ensure_folder(server, root, amcm_idx, own_idx, folder_name)
        components[attribute] = await _add_component(
            server, folder, type_identifier, amcm_idx, di_idx, own_idx,
            item_name, {"Manufacturer": config.manufacturer, "Model": model},
        )

    _log.info(
        "Anlagensicht (OPC 40100-2) unter %s, Komponenten: %s",
        root.nodeid.to_string(),
        ", ".join(name for name, node in components.items() if node is not None) or "keine",
    )
    return VisionAssetNodes(root=root, **components)


__all__ = ["VisionAssetNodes", "attach_asset_model"]
