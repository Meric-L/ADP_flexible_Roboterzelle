"""OPC 40100-2 (AMCM): what this vision system consists of and how it is doing.

Part 1 answers "how do I operate the system", part 2 answers "what is it built from
and what state is it in" -- computing device, image sensor, lens, with identification
per component, plus a `Health` block with `DeviceHealth`. `camera_health.py` writes
into this block what it derives from the camera watchdog; this module only builds the
nodes. It has no runtime coupling to the job path: nothing here can make a job fail,
and a job cannot change anything here.

Only the folders that we actually fill are created. Instantiating the type with all
optional folders would also turn their `<Placeholder>` templates into real nodes --
a node literally named `<ComputingDevice>` is wrong, and removing them afterwards
took 9 s for 26 deletions, because asyncua's recursive delete is painfully slow.
Building up instead of tearing down leaves about 50 nodes instead of about 700 and
runs in well under a second.
"""

import logging
from dataclasses import dataclass, field

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate
from asyncua.common.node import Node

from .address_space import VisionAddressSpace
from .nodeset_ids import (
    DI_CHECK_FUNCTION_ALARM_TYPE,
    DI_DEVICE_HEALTH_ENUMERATION,
    DI_DEVICE_HEALTH_INTERFACE,
    DI_FAILURE_ALARM_TYPE,
    DI_NAMESPACE_URI,
    DI_OFF_SPEC_ALARM_TYPE,
    VISION_COMPUTING_DEVICE_TYPE,
    VISION_HEALTH_INFO_TYPE,
    VISION_IMAGE_SENSOR_TYPE,
    VISION_ITEM_FOLDER_TYPE,
    VISION_LENS_TYPE,
    VISION_SYSTEM_ASSET_TYPE,
    DeviceHealth,
    node_id,
)
from .profiles import AssetConfig

_log = logging.getLogger(__name__)

#: DI Identification fields that we can fill honestly. Anything we do not know stays
#: empty -- an invented serial number is worse than an empty one.
_IDENTIFICATION_FIELDS = ("Manufacturer", "Model", "SerialNumber", "SoftwareRevision")

#: Fields that the specification types as LocalizedText instead of String.
_LOCALIZED_FIELDS = frozenset({"Manufacturer", "Model"})

#: Mandatory in DI's Identification, so instantiation always creates them.
_MANDATORY_FIELDS = frozenset({"Manufacturer", "SerialNumber", "ProductInstanceUri"})


@dataclass(frozen=True)
class VisionAssetNodes:
    """Nodes of the asset view, as far as other code needs them."""

    root: Node
    computing_device: Node | None = None
    image_sensor: Node | None = None
    lens: Node | None = None
    #: All `DeviceHealth` nodes that mirror the same camera state: the one at the root
    #: and, if the image sensor is known, one at the sensor.
    #: Empty if part 2 is missing or the block could not be created.
    #:
    #: No separate field per component: there is no state source for the computing
    #: device or the lens, and a node that nobody writes would be read as "NORMAL
    #: forever" -- worse than no node at all.
    device_health: tuple[Node, ...] = ()
    #: The alarm objects under `VisionAsset/Health/DeviceHealthAlarms`, one per state.
    #: Created only at the root, not additionally at the image sensor: both state nodes
    #: carry the same value, and each alarm instance costs 36 nodes (measured). Events
    #: already name the affected component in `SourceNode`.
    health_alarms: dict[DeviceHealth, Node] = field(default_factory=dict)


async def _write_identification(
    node: Node, di_idx: int, own_idx: int, values: dict[str, str]
) -> None:
    """Fill DI's Identification and add the optional fields we can answer.

    The type is instantiated without optional children, so at first only Manufacturer,
    SerialNumber and ProductInstanceUri exist. Model and SoftwareRevision are optional
    in DI -- a server publishes them if it supports them, and that is exactly what
    adding them here means. Fields without a value are omitted instead of left empty.
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
    server: Server, root: Node, amcm_idx: int, own_idx: int, folder_name: str, root_id: str
) -> Node:
    """Return the component folder, creating it on first use."""
    try:
        return await root.get_child(f"{amcm_idx}:{folder_name}")
    except Exception:
        # Folder still missing: on first access this is the normal case.
        _log.debug("Ordner '%s' nicht vorhanden, wird angelegt", folder_name)
        nodes = await instantiate(
            root,
            server.get_node(node_id(VISION_ITEM_FOLDER_TYPE, amcm_idx)),
            nodeid=ua.NodeId(f"{root_id}.{folder_name}", own_idx),
            bname=ua.QualifiedName(folder_name, amcm_idx),
            instantiate_optional=False,
        )
        folder = nodes[0]
        # `<VisionItem>` carries the MandatoryPlaceholder modelling rule, so asyncua
        # creates it as a real node -- regardless of the Optional switch. A placeholder
        # is a template of the type, not an instance: it does not belong in a running
        # address space.
        # Delete per created folder, not across the whole type -- asyncua's recursive
        # delete is expensive (measured about 0.35 s per node).
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
    root_id: str,
) -> Node | None:
    """Instantiate a component in its folder and identify it."""
    try:
        nodes = await instantiate(
            folder,
            server.get_node(node_id(type_identifier, amcm_idx)),
            nodeid=ua.NodeId(f"{root_id}.{name}", own_idx),
            bname=f"{own_idx}:{name}",
            instantiate_optional=False,
        )
    except Exception:
        _log.exception("Komponente '%s' konnte nicht angelegt werden", name)
        return None
    await _write_identification(nodes[0], di_idx, own_idx, values)
    return nodes[0]


async def _add_health(
    server: Server, owner: Node, amcm_idx: int, di_idx: int, own_idx: int
) -> Node | None:
    """Attach `Health` to a component and return its `DeviceHealth` node.

    Three quirks, all explained by the nodeset or by asyncua:

    * `Health` is optional on every item and referenced via `HasAddIn`.
      asyncua's `instantiate()` always sets the reference to `HasComponent`
      (`copy_node_util._rdesc_from_node` only knows `Organizes` for folders and
      otherwise `HasComponent`), so it is swapped afterwards.
    * `VisionHealthInfoType` has only optional children; the instantiated block is
      empty. `DeviceHealth` is added separately, just like the optional Identification
      fields in `_write_identification`.
    * The start value is CHECK_FUNCTION, not NORMAL: while the address space is being
      built, the camera is not even opened yet. NORMAL would be a claim that holds only
      until the publisher runs. The explicit Int32 start value also avoids
      `BadTypeMismatch`: asyncua checks a write request against the VariantType of the
      existing value.

    Never raises -- the asset view is an additional view, not an operating resource.
    """
    base = owner.nodeid.Identifier
    try:
        nodes = await instantiate(
            owner,
            server.get_node(node_id(VISION_HEALTH_INFO_TYPE, amcm_idx)),
            nodeid=ua.NodeId(f"{base}.Health", own_idx),
            bname=ua.QualifiedName("Health", amcm_idx),
            instantiate_optional=False,
        )
    except Exception:
        _log.exception("Zustandsblock unter %s nicht anlegbar", base)
        return None
    health = nodes[0]
    try:
        # As `ua.NodeId`, not as a bare number: asyncua's `_to_nodeid` turns an int into
        # a `TwoByteNodeId` and fails above 255 -- and HasAddIn (17604), like
        # HasInterface (17603), lies above that.
        # Set the correct reference first, then delete the wrong one: if it aborts in
        # between, the node is reachable twice rather than not at all.
        await owner.add_reference(health, ua.NodeId(ua.ObjectIds.HasAddIn))
        await owner.delete_reference(health, ua.NodeId(ua.ObjectIds.HasComponent))
        await health.add_reference(
            node_id(DI_DEVICE_HEALTH_INTERFACE, di_idx),
            ua.NodeId(ua.ObjectIds.HasInterface),
        )
    except Exception:
        # A wrong reference type makes the node ugly, not unusable.
        _log.debug("HasAddIn-Referenz fuer %s nicht setzbar", base, exc_info=True)
    try:
        return await health.add_variable(
            ua.NodeId(f"{base}.Health.DeviceHealth", own_idx),
            ua.QualifiedName("DeviceHealth", di_idx),
            int(DeviceHealth.CHECK_FUNCTION),
            ua.VariantType.Int32,
            datatype=node_id(DI_DEVICE_HEALTH_ENUMERATION, di_idx),
        )
    except Exception:
        _log.exception("DeviceHealth unter %s nicht anlegbar", base)
        return None


#: Which DI alarm type belongs to which state. `NORMAL` has none -- it is the absence
#: of an alarm. `MAINTENANCE_REQUIRED` is deliberately missing, see `nodeset_ids.py`.
_ALARM_TYPES: tuple[tuple[DeviceHealth, int, str], ...] = (
    (DeviceHealth.FAILURE, DI_FAILURE_ALARM_TYPE, "FailureAlarm"),
    (DeviceHealth.CHECK_FUNCTION, DI_CHECK_FUNCTION_ALARM_TYPE, "CheckFunctionAlarm"),
    (DeviceHealth.OFF_SPEC, DI_OFF_SPEC_ALARM_TYPE, "OffSpecAlarm"),
)


async def _add_health_alarms(
    server: Server, health: Node, di_idx: int, own_idx: int
) -> dict[DeviceHealth, Node]:
    """Create `DeviceHealthAlarms` and the alarms in it that we operate.

    The second part of DI's `IDeviceHealthType`: besides the state variable, the
    standard defines real OPC UA alarms. Only these give a client an event with
    timestamp, source and severity, instead of forcing it to poll a number.

    Only what is actually fired is created -- three of the four types. Each instance
    costs 36 nodes (measured), so there is one set at the root instead of one per
    component.

    Never raises; without alarms the state variable remains the reporting path.
    """
    base = health.nodeid.Identifier
    try:
        folder = await health.add_object(
            ua.NodeId(f"{base}.DeviceHealthAlarms", own_idx),
            ua.QualifiedName("DeviceHealthAlarms", di_idx),
            objecttype=ua.NodeId(ua.ObjectIds.FolderType),
        )
    except Exception:
        _log.exception("DeviceHealthAlarms unter %s nicht anlegbar", base)
        return {}
    alarms: dict[DeviceHealth, Node] = {}
    for value, type_identifier, name in _ALARM_TYPES:
        try:
            nodes = await instantiate(
                folder,
                server.get_node(node_id(type_identifier, di_idx)),
                nodeid=ua.NodeId(f"{base}.DeviceHealthAlarms.{name}", own_idx),
                bname=ua.QualifiedName(name, di_idx),
                instantiate_optional=False,
            )
        except Exception:
            _log.exception("Alarm '%s' nicht anlegbar", name)
            continue
        alarms[value] = nodes[0]
    _log.info("Zustandsalarme unter %s: %s", base, ", ".join(a.name for a in alarms))
    return alarms


async def attach_asset_model(
    space: VisionAddressSpace, config: AssetConfig
) -> VisionAssetNodes | None:
    """Build the part 2 asset view under the vision system.

    Returns `None` if part 2 is not loaded. Never raises: the asset view is a service
    and maintenance aid, and its loss must not stop the server from running the job path.
    """
    if space.amcm_idx is None:
        return None
    server, amcm_idx, own_idx = space.server, space.amcm_idx, space.own_idx
    # The prefix follows the instance name and is not written separately: otherwise a
    # renamed instance would keep NodeIds that say "VisionMachine" -- two truths about
    # the same node.
    root_id = f"{space.config.vision_system_name}.VisionAsset"
    try:
        di_idx = await server.get_namespace_index(DI_NAMESPACE_URI)
        nodes = await instantiate(
            space.vision_system,
            server.get_node(node_id(VISION_SYSTEM_ASSET_TYPE, amcm_idx)),
            nodeid=ua.NodeId(root_id, own_idx),
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

    # Singular spelled out per folder: "Lenses"[:-1] would be "Lense".
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
        folder = await _ensure_folder(
            server, root, amcm_idx, own_idx, folder_name, root_id
        )
        components[attribute] = await _add_component(
            server, folder, type_identifier, amcm_idx, di_idx, own_idx,
            item_name, {"Manufacturer": config.manufacturer, "Model": model}, root_id,
        )

    # Health block at the root and at the image sensor. The root always gets one: without
    # a known camera model there is no ImageSensor node, and then there would be no
    # reporting point at all. Both carry the same value today -- there is exactly one
    # state source. If a second component with its own state is added, the root needs a
    # real rule; that requires a ranking over the NE-107 states, which DI does not
    # define, so it is not anticipated here.
    health_owners = [root]
    if components["image_sensor"] is not None:
        health_owners.append(components["image_sensor"])
    device_health: list[Node] = []
    health_alarms: dict[DeviceHealth, Node] = {}
    for owner in health_owners:
        node = await _add_health(server, owner, amcm_idx, di_idx, own_idx)
        if node is None:
            continue
        device_health.append(node)
        if owner is root:
            # Only at the root: it always exists, even without a known camera model, and is
            # therefore the stable address for clients.
            block = await node.get_parent()
            if block is not None:
                health_alarms = await _add_health_alarms(
                    server, block, di_idx, own_idx
                )

    _log.info(
        "Anlagensicht (OPC 40100-2) unter %s, Komponenten: %s, Zustandsknoten: %d",
        root.nodeid.to_string(),
        ", ".join(name for name, node in components.items() if node is not None) or "keine",
        len(device_health),
    )
    return VisionAssetNodes(
        root=root,
        device_health=tuple(device_health),
        health_alarms=health_alarms,
        **components,
    )


__all__ = ["VisionAssetNodes", "attach_asset_model"]
