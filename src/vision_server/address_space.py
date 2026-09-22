"""Aufbau des Adressraums: Nodeset-Import und VisionSystem-Instanz."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from asyncua import Server, ua
from asyncua.common.node import Node

from .config import VisionServerConfig
from .nodeset_ids import (
    AMCM_NAMESPACE_URI,
    MACHINE_VISION_NAMESPACE_URI,
    VISION_SYSTEM_TYPE,
    mv,
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalibrationNodes:
    """`VisionMachine.Calibration`: remote camera calibration, not part of OPC 40100.

    The methods are created unlinked (the server answers `BadNothingToDo`)
    and linked by `runner.py`, like the nodeset methods.
    """

    folder: Node
    #: JSON status (`wsc.vision.calibration-status/1`), written by the server.
    status: Node
    start: Node
    capture: Node
    compute: Node
    save: Node
    cancel: Node


#: Every calibration method answers `(Error: Int32, Message: String)`.
_CALIBRATION_OUTPUTS = (
    ("Error", ua.VariantType.Int32, "0 = OK, sonst Fehlercode wie bei StartSingleJob"),
    ("Message", ua.VariantType.String, "Meldung fuer den Bediener"),
)

#: Method name -> input arguments. Order is the order of creation.
_CALIBRATION_METHODS: dict[str, tuple[tuple[str, ua.VariantType, str], ...]] = {
    "Start": (("Settings", ua.VariantType.String, "Board und Aufnahmeziel als JSON"),),
    "Capture": (),
    "Compute": (),
    "Save": (),
    "Cancel": (),
}


def _argument(name: str, variant_type: ua.VariantType, description: str) -> ua.Argument:
    """A scalar method argument with a name the backend can show in error messages."""
    argument = ua.Argument()
    argument.Name = name
    argument.DataType = ua.NodeId(variant_type.value)
    argument.ValueRank = -1
    argument.Description = ua.LocalizedText(description)
    return argument


async def _attach_calibration(vision_system: Node, own_idx: int, name: str) -> CalibrationNodes:
    """Create `Calibration` with its status node and five methods, all with string NodeIds."""
    root = f"{name}.Calibration"
    folder = await vision_system.add_object(
        ua.NodeId(root, own_idx), ua.QualifiedName("Calibration", own_idx)
    )
    status = await folder.add_variable(
        ua.NodeId(f"{root}.Status", own_idx),
        ua.QualifiedName("Status", own_idx),
        "",
        ua.VariantType.String,
    )
    methods: dict[str, Node] = {}
    for method_name, inputs in _CALIBRATION_METHODS.items():
        method_id = f"{root}.{method_name}"
        # Without argument lists here: asyncua would give the argument
        # properties running numeric NodeIds in our namespace. The frontend
        # still subscribes `ns=<vision>;i=1` as the legacy camera node, so
        # every node here gets a string id, the properties included.
        method = await folder.add_method(
            ua.NodeId(method_id, own_idx), ua.QualifiedName(method_name, own_idx), None
        )
        for property_name, arguments in (
            ("InputArguments", inputs),
            ("OutputArguments", _CALIBRATION_OUTPUTS),
        ):
            if not arguments:
                continue
            argument_property = await method.add_property(
                ua.NodeId(f"{method_id}.{property_name}", own_idx),
                ua.QualifiedName(property_name, 0),
                [_argument(*spec) for spec in arguments],
                varianttype=ua.VariantType.ExtensionObject,
                datatype=ua.ObjectIds.Argument,
            )
            await argument_property.set_modelling_rule(True)
        methods[method_name] = method
    return CalibrationNodes(
        folder=folder,
        status=status,
        start=methods["Start"],
        capture=methods["Capture"],
        compute=methods["Compute"],
        save=methods["Save"],
        cancel=methods["Cancel"],
    )


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
    stop: Node
    start_continuous: Node
    abort: Node
    halt: Node
    reset: Node
    results_folder: Node
    #: `None`, wenn `config.camera_stream` nicht gesetzt ist -- kein Livestream.
    latest_camera_frame: Node | None
    #: Writable: the frontend selects what the stream shows through this
    #: ("off", "apriltag", "calibration"). `None` without a livestream.
    camera_stream_mode: Node | None = None
    #: Int32: port of the MJPEG stream (`mjpeg_server.py`), 0 = none. The
    #: frontend builds the URL from the host it reaches OPC UA under. `None`
    #: without a livestream.
    camera_stream_http_port: Node | None = None
    #: Namespace index of OPC 40100-2 (AMCM); `None` when Part 2 was not
    #: loaded. Never hardcode it -- it shifts with every added nodeset.
    amcm_idx: int | None = None
    #: Remote calibration; `None` without a livestream, since it needs the
    #: same camera.
    calibration: CalibrationNodes | None = None


async def configure_server(server: Server, config: VisionServerConfig) -> None:
    """Richtet einen eigenstaendigen Server fuer den Vision-Betrieb ein.

    Wird nur gebraucht, wenn der Vision-Server als eigener Prozess laeuft.
    Haengt er in einem bestehenden Server, hat dieser Endpoint und
    ApplicationURI schon gesetzt — dann direkt `attach_vision_system` rufen.
    ApplicationURI muss vor dem Nodeset-Import gesetzt sein, weil der Import
    die Namespace-Indizes fixiert.
    """
    await server.init()
    server.set_endpoint(config.endpoint)
    await server.set_application_uri(config.application_uri)
    server.set_server_name(config.server_name)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])


async def _ensure_nodeset(server: Server, namespace_uri: str, path: Path) -> int:
    """Importiert ein Nodeset, falls sein Namensraum noch nicht da ist.

    Haengt das Vision-System in einem Server, der das Nodeset schon geladen
    hat, wuerde ein zweiter Import den Adressraum verdoppeln.
    """
    try:
        return await server.get_namespace_index(namespace_uri)
    except ValueError:
        pass
    if not path.is_file():
        raise FileNotFoundError(f"Nodeset nicht gefunden: {path}")
    _log.info("Importiere Nodeset %s von %s", namespace_uri, path)
    await server.import_xml(str(path))
    return await server.get_namespace_index(namespace_uri)


async def _ensure_amcm_nodesets(server: Server, config: VisionServerConfig) -> int | None:
    """Laedt OPC 40100-2 samt DI und Machinery, in dieser Reihenfolge.

    Nur wenn `config.assets` gesetzt ist -- Part 2 kostet gemessen ~13 MB RSS
    und ~1,6 s Startzeit und traegt zum Job-Pfad nichts bei. Scheitert der
    Import, laeuft der Server ohne Anlagensicht weiter: sie ist eine
    Zusatzsicht fuer Service und Instandhaltung, kein Betriebsmittel.
    """
    if config.assets is None:
        return None
    try:
        for path in config.amcm_nodeset_paths:
            await _ensure_nodeset(server, _namespace_uri_for(path), path)
        return await server.get_namespace_index(AMCM_NAMESPACE_URI)
    except Exception:
        _log.exception("OPC 40100-2 (AMCM) nicht ladbar -- Server laeuft ohne Anlagensicht")
        return None


def _namespace_uri_for(path: Path) -> str:
    """Liest den ModelUri aus dem Kopf eines Nodesets.

    Billiger und ehrlicher als eine zweite Liste von URIs neben der Pfadliste:
    die Datei sagt selbst, welchen Namensraum sie mitbringt, und kann damit
    nicht gegen eine Konstante auseinanderlaufen.
    """
    head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    match = re.search(r'<Model\s[^>]*ModelUri="([^"]+)"', head)
    if match is None:
        raise ValueError(f"Kein ModelUri im Kopf von {path}")
    return match.group(1)


async def attach_vision_system(server: Server, config: VisionServerConfig) -> VisionAddressSpace:
    """Haengt Nodeset und VisionSystem-Instanz an einen initialisierten Server.

    Muss nach `server.init()` und vor `server.start()` laufen. Der eigene
    Namespace wird nach dem Import registriert; alle NodeIds werden
    ausschliesslich ueber die zur Laufzeit ermittelten Indizes gebildet.
    """
    mv_idx = await _ensure_nodeset(
        server, MACHINE_VISION_NAMESPACE_URI, config.nodeset_path
    )
    # Part 2 vor dem eigenen Namensraum, damit dessen Index stabil hinter allen
    # importierten Nodesets liegt.
    amcm_idx = await _ensure_amcm_nodesets(server, config)
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
    stop = await automatic_state_machine.get_child(f"{mv_idx}:Stop")
    start_continuous = await automatic_state_machine.get_child(f"{mv_idx}:StartContinuous")
    abort = await automatic_state_machine.get_child(f"{mv_idx}:Abort")
    halt = await vision_state_machine.get_child(f"{mv_idx}:Halt")
    reset = await vision_state_machine.get_child(f"{mv_idx}:Reset")
    result_management = await vision_system.get_child(f"{mv_idx}:ResultManagement")
    results_folder = await result_management.get_child(f"{mv_idx}:Results")

    latest_camera_frame: Node | None = None
    camera_stream_mode: Node | None = None
    camera_stream_http_port: Node | None = None
    if config.camera_stream is not None:
        # Additive Knoten, nicht Teil des 40100-Nodesets -- analog zu den
        # eigenen `RaspiDevice`-Variablen in `OPCUA/server.py`. Nur der Server
        # schreibt hierhin, daher kein `set_writable()`.
        # Explicit string NodeId instead of the auto-assigned numeric one:
        # `add_variable(own_idx, ...)` would produce `ns=X;i=<running number>`,
        # which shifts whenever someone adds a node before it. The backend
        # subscribes to these nodes by fixed address -- they must be stable
        # and match the form documented in doc/vision-server-interface.md.
        latest_camera_frame = await vision_system.add_variable(
            ua.NodeId(f"{name}.{config.camera_stream.node_name}", own_idx),
            ua.QualifiedName(config.camera_stream.node_name, own_idx),
            "",
            ua.VariantType.String,
        )
        # This one is the exception: the frontend selects the stream's
        # overlay mode through it, so it **must** be writable. An invalid
        # value can't stop the stream -- the publisher normalises it
        # (see `tagloc.overlay.normalise_mode`).
        camera_stream_mode = await vision_system.add_variable(
            ua.NodeId(f"{name}.{config.camera_stream.mode_node_name}", own_idx),
            ua.QualifiedName(config.camera_stream.mode_node_name, own_idx),
            config.camera_stream.overlay_mode,
            ua.VariantType.String,
        )
        await camera_stream_mode.set_writable()
        # Starts at 0: the runner writes the real port only once the MJPEG
        # server is actually listening, so a failed bind never advertises a
        # dead URL.
        camera_stream_http_port = await vision_system.add_variable(
            ua.NodeId(f"{name}.{config.camera_stream.http_port_node_name}", own_idx),
            ua.QualifiedName(config.camera_stream.http_port_node_name, own_idx),
            0,
            ua.VariantType.Int32,
        )

    calibration = (
        await _attach_calibration(vision_system, own_idx, name)
        if config.camera_stream is not None
        else None
    )

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
        stop=stop,
        start_continuous=start_continuous,
        abort=abort,
        halt=halt,
        reset=reset,
        results_folder=results_folder,
        latest_camera_frame=latest_camera_frame,
        camera_stream_mode=camera_stream_mode,
        camera_stream_http_port=camera_stream_http_port,
        amcm_idx=amcm_idx,
        calibration=calibration,
    )
