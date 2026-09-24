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
    MACHINERY_NAMESPACE_URI,
    MACHINES_FOLDER,
    MACHINE_VISION_NAMESPACE_URI,
    VISION_SYSTEM_TYPE,
    mv,
    node_id,
)

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
    #: Live-Fortschritt einer `CalibrationSession` (JSON-String), siehe
    #: `runner.py`. `None`, wenn `config.apriltag` nicht gesetzt ist.
    calibration_progress: Node | None = None
    #: Die aktuell geladene Tag-Map als JSON. Nur lesen -- geschrieben wird
    #: ueber die Methode `SetTagMap`, damit eine ungueltige Karte abgelehnt
    #: werden kann, statt stumm im Knoten zu stehen.
    tag_map_json: Node | None = None
    #: Metadaten der gerade aktiven (fuer Posen/Job genutzten) Kalibrierung
    #: (JSON-String) -- anders als `calibration_progress` unabhaengig vom
    #: Session-Zustand: bleibt stehen, egal ob/wann als naechstes wieder
    #: `StartCalibration` laeuft. `None`, wenn `config.apriltag` nicht
    #: gesetzt ist.
    active_calibration_info: Node | None = None
    #: Int32: port of the MJPEG stream (`mjpeg_server.py`), 0 = none. The
    #: frontend builds the URL from the host it reaches OPC UA under. `None`
    #: without a livestream.
    camera_stream_http_port: Node | None = None
    #: Namespace index of OPC 40100-2 (AMCM); `None` when Part 2 was not
    #: loaded. Never hardcode it -- it shifts with every added nodeset.
    amcm_idx: int | None = None


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


async def _vision_parent(server: Server) -> Node:
    """`Objects/Machines`, falls Machinery geladen ist, sonst `Objects`.

    Warum ueberhaupt: Direkt unter `Objects` stand `VisionMachine` gleichrangig
    neben `VisionProgram`, und wer mit UaExpert browste, sah zwei Einstiege und
    musste raten, welcher gemeint ist. Genau das hat in der Betreuung Verwirrung
    ausgeloest. `Machines` ist der von OPC UA Machinery dafuer vorgesehene Ort;
    `VisionProgram` bleibt damit der einzige Punkt unter `Objects`.

    Fuer Clients aendert sich nichts: `VisionMachine` hat eine feste
    String-NodeId, und die haengt nicht an der Browse-Position.

    Faellt ohne Machinery-Nodeset (also ohne `config.assets`) auf `Objects`
    zurueck -- ein fehlender Ordner darf den Serverstart nicht verhindern.
    """
    try:
        machinery_idx = await server.get_namespace_index(MACHINERY_NAMESPACE_URI)
    except ValueError:
        _log.info("Machinery nicht geladen -- VisionSystem haengt unter Objects")
        return server.nodes.objects
    machines = server.get_node(node_id(MACHINES_FOLDER, machinery_idx))
    try:
        await machines.read_browse_name()
    except Exception:
        _log.warning("Machines-Ordner nicht lesbar -- VisionSystem haengt unter Objects")
        return server.nodes.objects
    return machines


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
    parent = await _vision_parent(server)
    vision_system = await parent.add_object(
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
    if config.camera_stream is not None and config.camera_stream.stream_enabled:
        # Additive Knoten, nicht Teil des 40100-Nodesets: OPC 40100 kennt
        # keinen Livestream. Nur der Server schreibt hierhin, daher kein
        # `set_writable()`.
        # Explicit string NodeId instead of the auto-assigned numeric one:
        # `add_variable(own_idx, ...)` would produce `ns=X;i=<running number>`,
        # which shifts whenever someone adds a node before it. The backend
        # subscribes to these nodes by fixed address -- they must be stable
        # and match the form documented in doc/projektdoku/vision-server-interface.md.
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

    calibration_progress: Node | None = None
    tag_map_json: Node | None = None
    active_calibration_info: Node | None = None
    if config.apriltag is not None:
        # Nur lesen: die laufende `CalibrationSession` in `runner.py` schreibt
        # hierhin, das Frontend abonniert. Nicht an `camera_stream` gekoppelt --
        # eine Kalibrier-Session ist auch ohne Livestream denkbar (z. B. lokale
        # Entwicklung), auch wenn sie in der Praxis immer zusammen laufen.
        calibration_progress = await vision_system.add_variable(
            ua.NodeId(f"{name}.CalibrationProgress", own_idx),
            ua.QualifiedName("CalibrationProgress", own_idx),
            '{"running": false}',
            ua.VariantType.String,
        )
        # Bewusst nicht schreibbar: eine Tag-Map muss geprueft werden, bevor
        # sie gilt. Das geht nur ueber eine Methode mit Rueckgabewert --
        # ein Schreibzugriff koennte eine unbrauchbare Karte nicht ablehnen.
        tag_map_json = await vision_system.add_variable(
            ua.NodeId(f"{name}.TagMapJson", own_idx),
            ua.QualifiedName("TagMapJson", own_idx),
            "",
            ua.VariantType.String,
        )
        # Leer, bis `runner.py` nach dem Oeffnen der Erkennungsquelle den
        # ersten Wert schreibt (echte Datei oder Platzhalter) -- kein
        # sinnvoller Default vorher, ohne die Kalibrierdatei gelesen zu haben.
        active_calibration_info = await vision_system.add_variable(
            ua.NodeId(f"{name}.ActiveCalibrationInfo", own_idx),
            ua.QualifiedName("ActiveCalibrationInfo", own_idx),
            "",
            ua.VariantType.String,
        )

    _log.info(
        "VisionSystem '%s' als %s unter %s angelegt",
        name,
        vision_system.nodeid.to_string(),
        (await parent.read_browse_name()).Name,
    )
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
        calibration_progress=calibration_progress,
        tag_map_json=tag_map_json,
        active_calibration_info=active_calibration_info,
        camera_stream_http_port=camera_stream_http_port,
        amcm_idx=amcm_idx,
    )
