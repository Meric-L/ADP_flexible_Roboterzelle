"""Aufbau des Adressraums: Nodeset-Import und VisionSystem-Instanz."""

import logging
from dataclasses import dataclass

from asyncua import Server, ua
from asyncua.common.node import Node

from .config import VisionServerConfig
from .nodeset_ids import MACHINE_VISION_NAMESPACE_URI, VISION_SYSTEM_TYPE, mv

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisionAddressSpace:
    """Die beim Aufbau aufgeloesten Knoten des Vision-Systems."""

    server: Server
    config: VisionServerConfig
    mv_idx: int
    own_idx: int
    vision_system: Node
    vision_state_machine: Node
    automatic_state_machine: Node
    start_single_job: Node
    results_folder: Node


async def build_address_space(server: Server, config: VisionServerConfig) -> VisionAddressSpace:
    """Baut den Adressraum in der von asyncua geforderten Reihenfolge auf.

    ApplicationURI muss vor `import_xml` gesetzt sein, weil der Import den
    Namespace-Index fixiert. Der eigene Namespace wird nach dem Import
    registriert; alle NodeIds werden ausschliesslich ueber die zur Laufzeit
    ermittelten Indizes gebildet.
    """
    if not config.nodeset_path.is_file():
        raise FileNotFoundError(f"Nodeset nicht gefunden: {config.nodeset_path}")

    await server.init()
    server.set_endpoint(config.endpoint)
    await server.set_application_uri(config.application_uri)
    server.set_server_name(config.server_name)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    _log.info("Importiere Machine-Vision-Nodeset von %s", config.nodeset_path)
    await server.import_xml(str(config.nodeset_path))

    mv_idx = await server.get_namespace_index(MACHINE_VISION_NAMESPACE_URI)
    own_idx = await server.register_namespace(config.namespace_uri)

    name = config.vision_system_name
    vision_system = await server.nodes.objects.add_object(
        ua.NodeId(name, own_idx),
        ua.QualifiedName(name, own_idx),
        objecttype=mv(VISION_SYSTEM_TYPE, mv_idx),
    )
    await server.nodes.server.add_reference(
        vision_system, ua.ObjectIds.HasNotifier, forward=True
    )
    await vision_system.set_event_notifier([ua.EventNotifier.SubscribeToEvents])

    vision_state_machine = await vision_system.get_child(f"{mv_idx}:VisionStateMachine")
    automatic_state_machine = await vision_state_machine.get_child(
        f"{mv_idx}:AutomaticModeStateMachine"
    )
    start_single_job = await automatic_state_machine.get_child(f"{mv_idx}:StartSingleJob")
    result_management = await vision_system.get_child(f"{mv_idx}:ResultManagement")
    results_folder = await result_management.get_child(f"{mv_idx}:Results")

    _log.info("VisionSystem '%s' als %s angelegt", name, vision_system.nodeid.to_string())
    return VisionAddressSpace(
        server=server,
        config=config,
        mv_idx=mv_idx,
        own_idx=own_idx,
        vision_system=vision_system,
        vision_state_machine=vision_state_machine,
        automatic_state_machine=automatic_state_machine,
        start_single_job=start_single_job,
        results_folder=results_folder,
    )
