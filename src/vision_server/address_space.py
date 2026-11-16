"""Build the address space: nodeset import and the VisionSystem instance."""

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
    """The vision system nodes resolved while building the address space."""

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
    #: `None` if `config.camera_stream` is not set -- no live stream.
    latest_camera_frame: Node | None
    #: Writable: the frontend selects what the stream shows through this
    #: ("off", "apriltag", "calibration"). `None` without a livestream.
    camera_stream_mode: Node | None = None
    #: Live progress of a `CalibrationSession` (JSON string), see
    #: `runner/calibration.py`. `None` if `config.apriltag` is not set.
    calibration_progress: Node | None = None
    #: The currently loaded tag map as JSON. Read-only -- it is written via the
    #: method `SetTagMap`, so that an invalid map can be rejected instead of
    #: silently sitting in the node.
    tag_map_json: Node | None = None
    #: Metadata of the currently active calibration (used for poses and jobs),
    #: as a JSON string. Unlike `calibration_progress`, it is independent of the
    #: session state: it stays as is, whether or when `StartCalibration` runs
    #: next. `None` if `config.apriltag` is not set.
    active_calibration_info: Node | None = None
    #: Int32: port of the MJPEG stream (`mjpeg_server.py`), 0 = none. The
    #: frontend builds the URL from the host it reaches OPC UA under. `None`
    #: without a livestream.
    camera_stream_http_port: Node | None = None
    #: Next target of the layer-2 run (JSON, `""` without a target) and its state
    #: (JSON), see `layer2/run.py`. Only on the hand Pi, i.e. with `hand_eye_path`;
    #: otherwise `None`.
    layer2_target: Node | None = None
    layer2_status: Node | None = None
    #: State of the hand-eye calibration (JSON), see `handeye_session.py`.
    #: Only on the hand Pi, like `layer2_*`.
    hand_eye_status: Node | None = None
    #: State of the cell watch (JSON), see `cellwatch.py`. Only on the ceiling
    #: camera, i.e. with AprilTag and without `hand_eye_path`.
    cell_watch_status: Node | None = None
    #: Result of the last `MeasureTags` (JSON), for display only. Only on the
    #: hand Pi, like `hand_eye_status`.
    last_tag_measurement: Node | None = None
    #: Result of the last `MeasureTagReference` (JSON), for display only. Only on
    #: the hand Pi, like `last_tag_measurement`.
    last_tag_reference: Node | None = None
    #: Live evaluation of the measurement preview (JSON, at most 3 Hz), see
    #: `tagreference_preview.py`. Only on the hand Pi.
    tag_reference_preview_status: Node | None = None
    #: Namespace index of OPC 40100-2 (AMCM); `None` when Part 2 was not
    #: loaded. Never hardcode it -- it shifts with every added nodeset.
    amcm_idx: int | None = None


async def configure_server(server: Server, config: VisionServerConfig) -> None:
    """Set up a standalone server for vision operation.

    Only needed when the vision server runs as its own process. If it is attached
    to an existing server, that server has already set the endpoint and
    ApplicationURI -- then call `attach_vision_system` directly. The
    ApplicationURI must be set before the nodeset import, because the import
    fixes the namespace indices.
    """
    await server.init()
    server.set_endpoint(config.endpoint)
    await server.set_application_uri(config.application_uri)
    server.set_server_name(config.server_name)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])


async def _ensure_nodeset(server: Server, namespace_uri: str, path: Path) -> int:
    """Import a nodeset if its namespace is not present yet.

    If the vision system is attached to a server that already loaded the nodeset,
    a second import would duplicate the address space.
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
    """Load OPC 40100-2 together with DI and Machinery, in this order.

    Only if `config.assets` is set -- measured, Part 2 costs ~13 MB RSS and
    ~1.6 s of startup time and contributes nothing to the job path. If the import
    fails, the server keeps running without the asset view: it is an extra view
    for service and maintenance, not an operational resource.
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
    """Return `Objects/Machines` if Machinery is loaded, otherwise `Objects`.

    Keeps `VisionProgram` the only entry point under `Objects`. Clients are not
    affected, because `VisionMachine` has a fixed string NodeId that does not
    depend on its browse position. Falls back to `Objects` without the Machinery
    nodeset, so a missing folder does not prevent the server from starting.
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
    """Read the ModelUri from the header of a nodeset.

    The file states its own namespace, so no separate URI list can drift out of
    sync with the path list.
    """
    head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    match = re.search(r'<Model\s[^>]*ModelUri="([^"]+)"', head)
    if match is None:
        raise ValueError(f"Kein ModelUri im Kopf von {path}")
    return match.group(1)


async def attach_vision_system(server: Server, config: VisionServerConfig) -> VisionAddressSpace:
    """Attach the nodeset and the VisionSystem instance to an initialized server.

    Must run after `server.init()` and before `server.start()`. The own namespace
    is registered after the import; all NodeIds are built exclusively from the
    indices determined at runtime.
    """
    mv_idx = await _ensure_nodeset(
        server, MACHINE_VISION_NAMESPACE_URI, config.nodeset_path
    )
    # Part 2 before the own namespace, so that its index stays stable behind all
    # imported nodesets.
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
        # Additive nodes, not part of the 40100 nodeset: OPC 40100 has no
        # live stream. Only the server writes here, so no `set_writable()`.
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
        # Read-only: the running `CalibrationSession` in `runner/calibration.py` writes
        # here, the frontend subscribes. Not coupled to `camera_stream` --
        # a calibration session is also possible without a live stream (e.g. local
        # development), even though in practice they always run together.
        calibration_progress = await vision_system.add_variable(
            ua.NodeId(f"{name}.CalibrationProgress", own_idx),
            ua.QualifiedName("CalibrationProgress", own_idx),
            '{"running": false, "processing": false}',
            ua.VariantType.String,
        )
        # Deliberately not writable: a tag map must be checked before it takes
        # effect. That is only possible via a method with a return value --
        # a write access could not reject an unusable map.
        tag_map_json = await vision_system.add_variable(
            ua.NodeId(f"{name}.TagMapJson", own_idx),
            ua.QualifiedName("TagMapJson", own_idx),
            "",
            ua.VariantType.String,
        )
        # Empty until `runner/calibration.py` writes the first value after opening
        # the detection source (real file or placeholder) -- there is no sensible
        # default before that, without having read the calibration file.
        active_calibration_info = await vision_system.add_variable(
            ua.NodeId(f"{name}.ActiveCalibrationInfo", own_idx),
            ua.QualifiedName("ActiveCalibrationInfo", own_idx),
            "",
            ua.VariantType.String,
        )

    layer2_target: Node | None = None
    layer2_status: Node | None = None
    hand_eye_status: Node | None = None
    last_tag_measurement: Node | None = None
    last_tag_reference: Node | None = None
    tag_reference_preview_status: Node | None = None
    cell_watch_status: Node | None = None
    if config.apriltag is not None and config.apriltag.hand_eye_path is None:
        cell_watch_status = await vision_system.add_variable(
            ua.NodeId(f"{name}.CellWatchStatus", own_idx),
            ua.QualifiedName("CellWatchStatus", own_idx),
            "",
            ua.VariantType.String,
        )
    if config.apriltag is not None and config.apriltag.hand_eye_path is not None:
        # Read-only: the run writes, the frontend subscribes. It is controlled via
        # StartLayer2Run/ReportRobotPose, so that every input can be checked and
        # answered with an error code.
        layer2_target = await vision_system.add_variable(
            ua.NodeId(f"{name}.Layer2Target", own_idx),
            ua.QualifiedName("Layer2Target", own_idx),
            "",
            ua.VariantType.String,
        )
        layer2_status = await vision_system.add_variable(
            ua.NodeId(f"{name}.Layer2Status", own_idx),
            ua.QualifiedName("Layer2Status", own_idx),
            "",
            ua.VariantType.String,
        )
        hand_eye_status = await vision_system.add_variable(
            ua.NodeId(f"{name}.HandEyeStatus", own_idx),
            ua.QualifiedName("HandEyeStatus", own_idx),
            "",
            ua.VariantType.String,
        )
        last_tag_measurement = await vision_system.add_variable(
            ua.NodeId(f"{name}.LastTagMeasurement", own_idx),
            ua.QualifiedName("LastTagMeasurement", own_idx),
            "",
            ua.VariantType.String,
        )
        last_tag_reference = await vision_system.add_variable(
            ua.NodeId(f"{name}.LastTagReference", own_idx),
            ua.QualifiedName("LastTagReference", own_idx),
            "",
            ua.VariantType.String,
        )
        tag_reference_preview_status = await vision_system.add_variable(
            ua.NodeId(f"{name}.TagReferencePreviewStatus", own_idx),
            ua.QualifiedName("TagReferencePreviewStatus", own_idx),
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
        layer2_target=layer2_target,
        layer2_status=layer2_status,
        hand_eye_status=hand_eye_status,
        cell_watch_status=cell_watch_status,
        last_tag_measurement=last_tag_measurement,
        last_tag_reference=last_tag_reference,
        tag_reference_preview_status=tag_reference_preview_status,
        amcm_idx=amcm_idx,
    )
