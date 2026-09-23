"""OPC 40100-2 (AMCM): what this vision system is made of, and how it is doing.

Part 1 answers "how do I operate the system"; Part 2 answers "what is it built
from and what is its condition" -- computing device, image sensor, lens, with
identification per component, plus a `Health` block carrying `DeviceHealth`.
Written into that block is what `camera_health.py` derives from the camera
watchdog; this module only builds the nodes. It carries no runtime coupling to
the job path: nothing here can fail a job, and a job cannot change anything
here.

Only the folders we actually fill are created. Instantiating the type with all
its optional folders would also materialise their `<Placeholder>` templates as
real nodes -- a node literally named `<ComputingDevice>` is wrong, and removing
them afterwards measured 9 s for 26 deletions, because asyncua's recursive
delete is pathologically slow. Building up instead of tearing down leaves ~50
nodes rather than ~700 and runs in well under a second.
"""

import logging
from dataclasses import dataclass, field

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate
from asyncua.common.node import Node

from .address_space import VisionAddressSpace
from .nodeset_ids import (
    DI_CHECK_FUNCTION_ALARM_TYPE,
    DI_DEVICE_HEALTH_ALARMS,
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
    #: Alle `DeviceHealth`-Knoten, die denselben Kamerazustand spiegeln:
    #: der an der Wurzel und, falls der Bildsensor bekannt ist, seiner.
    #: Leer, wenn Part 2 fehlt oder der Block nicht anlegbar war.
    #:
    #: Kein eigenes Feld je Komponente: fuer Recheneinheit und Objektiv gibt
    #: es keine Zustandsquelle, und ein Knoten, den niemand schreibt, wird als
    #: "NORMAL fuer immer" gelesen -- schlimmer als gar kein Knoten.
    device_health: tuple[Node, ...] = ()
    #: Die Alarmobjekte unter `VisionAsset/Health/DeviceHealthAlarms`, je
    #: Zustand einer. Nur an der Wurzel angelegt, nicht zusaetzlich am
    #: Bildsensor: beide Zustandsknoten tragen denselben Wert, und jede
    #: Alarminstanz kostet 36 Knoten (gemessen). Die Ereignisse nennen die
    #: betroffene Komponente ohnehin in `SourceNode`.
    health_alarms: dict[DeviceHealth, Node] = field(default_factory=dict)


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
    server: Server, root: Node, amcm_idx: int, own_idx: int, folder_name: str, root_id: str
) -> Node:
    """The component folder, created on first use."""
    try:
        return await root.get_child(f"{amcm_idx}:{folder_name}")
    except Exception:
        nodes = await instantiate(
            root,
            server.get_node(node_id(VISION_ITEM_FOLDER_TYPE, amcm_idx)),
            nodeid=ua.NodeId(f"{root_id}.{folder_name}", own_idx),
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
    root_id: str,
) -> Node | None:
    """Instantiate one component into its folder and identify it."""
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
    """Haengt `Health` an eine Komponente und gibt deren `DeviceHealth` zurueck.

    Drei Eigenheiten, alle im Nodeset bzw. in asyncua begruendet:

    * `Health` ist an jedem Item optional und per `HasAddIn` referenziert.
      asyncuas `instantiate()` setzt die Referenz aber immer auf
      `HasComponent` (`copy_node_util._rdesc_from_node` kennt nur
      `Organizes` fuer Ordner und sonst `HasComponent`) -- sie wird danach
      getauscht.
    * `VisionHealthInfoType` hat ausschliesslich optionale Kinder; der
      instanziierte Block ist leer. `DeviceHealth` kommt einzeln dazu, genau
      wie die optionalen Identification-Felder in `_write_identification`.
    * Der Startwert ist CHECK_FUNCTION, nicht NORMAL: beim Aufbau des
      Adressraums ist die Kamera noch nicht einmal geoeffnet. NORMAL waere
      hier eine Behauptung, die genau so lange steht, bis der Publisher
      laeuft. Der ausdrueckliche Int32-Startwert haelt ausserdem
      `BadTypeMismatch` fern: asyncua prueft eine Schreibanfrage gegen den
      VariantType des vorhandenen Werts.

    Wirft nie -- die Anlagensicht ist eine Zusatzsicht, kein Betriebsmittel.
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
        # Als `ua.NodeId`, nicht als blanke Zahl: asyncuas `_to_nodeid` macht
        # aus einem int einen `TwoByteNodeId` und wirft ueber 255 -- und
        # HasAddIn (17604) wie HasInterface (17603) liegen darueber.
        # Erst die richtige Referenz setzen, dann die falsche loeschen: bricht
        # es dazwischen ab, ist der Knoten doppelt erreichbar statt gar nicht.
        await owner.add_reference(health, ua.NodeId(ua.ObjectIds.HasAddIn))
        await owner.delete_reference(health, ua.NodeId(ua.ObjectIds.HasComponent))
        await health.add_reference(
            node_id(DI_DEVICE_HEALTH_INTERFACE, di_idx),
            ua.NodeId(ua.ObjectIds.HasInterface),
        )
    except Exception:
        # Ein falscher Referenztyp macht den Knoten unschoen, nicht unbrauchbar.
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


#: Welcher DI-Alarmtyp zu welchem Zustand gehoert. `NORMAL` hat keinen --
#: es ist die Abwesenheit eines Alarms. `MAINTENANCE_REQUIRED` fehlt
#: bewusst, siehe `nodeset_ids.py`.
_ALARM_TYPES: tuple[tuple[DeviceHealth, int, str], ...] = (
    (DeviceHealth.FAILURE, DI_FAILURE_ALARM_TYPE, "FailureAlarm"),
    (DeviceHealth.CHECK_FUNCTION, DI_CHECK_FUNCTION_ALARM_TYPE, "CheckFunctionAlarm"),
    (DeviceHealth.OFF_SPEC, DI_OFF_SPEC_ALARM_TYPE, "OffSpecAlarm"),
)


async def _add_health_alarms(
    server: Server, health: Node, di_idx: int, own_idx: int
) -> dict[DeviceHealth, Node]:
    """Legt `DeviceHealthAlarms` an und darin die Alarme, die wir bedienen.

    Der zweite Teil von DIs `IDeviceHealthType`: neben der Zustandsvariablen
    sieht die Norm echte OPC-UA-Alarme vor. Erst damit bekommt ein Client ein
    Ereignis mit Zeitstempel, Quelle und Schweregrad, statt eine Zahl abfragen
    zu muessen.

    Angelegt wird nur, was auch gefeuert wird -- drei der vier Typen. Jede
    Instanz kostet 36 Knoten (gemessen), deshalb gibt es sie einmal an der
    Wurzel und nicht zusaetzlich je Komponente.

    Wirft nie; ohne Alarme bleibt die Zustandsvariable der Meldeweg.
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
    """Build the Part 2 asset view under the vision system.

    Returns `None` when Part 2 is not loaded. Never raises: the asset view is a
    service and maintenance aid, and losing it must not keep the server from
    running the job path.
    """
    if space.amcm_idx is None:
        return None
    server, amcm_idx, own_idx = space.server, space.amcm_idx, space.own_idx
    # Der Praefix folgt dem Instanznamen und wird nicht danebengeschrieben:
    # sonst haette eine umbenannte Instanz weiterhin NodeIds, die "VisionMachine"
    # sagen -- zwei Wahrheiten ueber denselben Knoten.
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
        folder = await _ensure_folder(
            server, root, amcm_idx, own_idx, folder_name, root_id
        )
        components[attribute] = await _add_component(
            server, folder, type_identifier, amcm_idx, di_idx, own_idx,
            item_name, {"Manufacturer": config.manufacturer, "Model": model}, root_id,
        )

    # Zustandsblock an der Wurzel und am Bildsensor. Die Wurzel bekommt ihn
    # immer: ohne bekanntes Kameramodell gibt es keinen ImageSensor-Knoten,
    # und dann bliebe gar keine Meldestelle. Beide tragen heute denselben
    # Wert -- es gibt genau eine Zustandsquelle. Kommt eine zweite Komponente
    # mit eigenem Zustand dazu, braucht die Wurzel eine echte Regel; die
    # verlangt eine Rangfolge ueber die NE-107-Zustaende, die DI nicht
    # definiert, und wird deshalb nicht vorweggenommen.
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
            # Nur an der Wurzel: sie existiert immer, auch ohne bekanntes
            # Kameramodell, und ist damit die stabile Adresse fuer Clients.
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
