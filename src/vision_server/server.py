"""The server as it runs on the cell's Raspberry Pi.

One server, one tree: ``VisionMachine`` (OPC 40100) under ``Objects/Machines``, and next
to it ``VisionProgram`` (OPC UA Part 10) as the operator interface for the frontend. Both
operate the same job.

This module is the **installation point**, not the vision system itself. It holds only
what depends on this Pi: its identity, its camera backend, its AprilTag and asset values,
plus the mDNS announcement and LDS registration. The address space is built entirely in
``runner.install_vision_machine``.

To start::

    python3 -m vision_server.server

The early CPU-temperature demo -- ``RaspiDevice``, the empty second instance
``2:VisionSystem``, ``CpuTemperatureResult`` and the namespace
``http://launch-rm.de/raspi`` -- has been removed (tech debt A1-A6). As a result, all
namespace indices shift down by one. Anyone resolving them via
``get_namespace_index(uri)`` will not notice.
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
from pathlib import Path
from urllib.parse import urlparse

from asyncua import Server, ua

from .config import DEFAULT_NODESET_PATH, VisionServerConfig
from .discovery import lds, mdns
from .profiles import AprilTagProfileConfig, AssetConfig, CameraStreamConfig
from .runner import install_vision_machine

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("vision-cell-server")

NODESET_PATH = DEFAULT_NODESET_PATH

#: Fallback endpoint. Normally the endpoint is set to the LAN IPv4 at runtime (see
#: ``lds.advertised_endpoint``), because ``register_to_discovery()`` passes exactly this
#: address on as the DiscoveryUrl to the discovery server. With ``0.0.0.0`` the
#: aggregation server would connect to nothing. Listening always happens on all interfaces
#: regardless (``Server.socket_address``), so that local tools can still reach the server
#: via 127.0.0.1.
#: If no LAN IPv4 can be determined, this value stays in use, and the server keeps
#: running without LDS registration.
#:
#: The path stays ``/raspi/server/`` even though the Raspi interface has been removed. It
#: appears in the mDNS announcement, in the LDS registration and in every client
#: configuration. Renaming it would break every existing connection without improving
#: anything.
ENDPOINT = "opc.tcp://0.0.0.0:4840/raspi/server/"
SERVER_NAME = "Raspberry Pi OPC UA Server"

#: Port and path for the mDNS announcement, derived from the endpoint instead of being
#: maintained separately. A client builds ``opc.tcp://<ip>:<port><path>``; a different path
#: would give every client an unusable URL.
_ENDPOINT_URL = urlparse(ENDPOINT)
MDNS_PORT = _ENDPOINT_URL.port or 4840
MDNS_PATH = _ENDPOINT_URL.path or "/"


#: Hostname -> identity and reference frame. ``vision_system_name`` must NOT vary:
#: the interface documentation and the frontend hard-code ``ns=<vision>;s=VisionMachine``,
#: and a Pi-specific BrowseName would break every client.
PI_IDENTITIES: dict[str, tuple[str, str]] = {
    "ADP-Roboter-Lokalisierung": ("vision-ceiling-01", "cam_ceiling"),
    "ADP-HandInEye-Kamera-Pi": ("vision-flange-01", "cam_flange"),
}

#: Hostname -> camera backend (see ``CameraStreamConfig.backend``). The ceiling Pi
#: still uses Picamera2; the hand Pi uses an Intel RealSense via ``pyrealsense2``. If a
#: host is missing here, "picamera2" applies as the previous default, so that a freshly
#: installed third Pi does not fail silently.
PI_CAMERA_BACKENDS: dict[str, str] = {
    "ADP-Roboter-Lokalisierung": "picamera2",
    "ADP-HandInEye-Kamera-Pi": "realsense",
}


#: What distinguishes Layer 1 from Layer 2 -- nothing else. Both run the same
#: server with the same detection source; only these values differ.
#: Ceiling camera: large tags at a distance, full resolution, looser error
#: bound. Flange camera: small tags up close, more samples, tighter bound
#: since moves are made from its pose.
PI_APRILTAG_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        # Full sensor resolution of the HQ camera (IMX477, 12.3 MP). The livestream does
        # not compute at this resolution, but on the small lores stream
        # (PI_CAMERA_STREAM_PRESETS below) -- otherwise the overlay would stutter.
        "resolution": (4056, 3040),
        # Transition until recalibration at 4056x3040 is done: an existing calibration for
        # 2028x1520 comes from 2x2 binning of the same sensor, hence the same field of view.
        # Its intrinsics can be doubled exactly (``scale_to_resolution``). Remove this after
        # recalibration, so that an accidentally wrong resolution is noticed again.
        "allow_resolution_mismatch": True,
        # Fallback size for tags that are not in the tag map -- that is, for all module and
        # robot tags. Our module tags are 8 cm (edge of the black square). A wrong size
        # scales the distance to the camera and pushes the tag sideways (13.10.2026).
        "tag_size_m": 0.080,
        "samples_per_job": 3,
        "max_reproj_error_px": 3.0,
        # The same printed board as on the hand Pi (see cam_flange below) -- only the
        # resolution and camera distance differ, not the sheet. The new board has been in
        # use since 28.10.2026: 7x10 inner corners (8x11 squares), 23 mm per square.
        # If it is not found during the calibration run, test several cols/rows combinations
        # against a real frame with the diagnostic script (``tools/diagnose_board.py``).
        "calibration_board_type": "chessboard",
        "calibration_board_cols": 7,
        "calibration_board_rows": 10,
        "calibration_board_square_size_m": 0.023,
        # Kannala-Brandt instead of Brown-Conrady: in the image tests of 29.09.2026, the best
        # model for the HQ camera at 4056x3040 (work plan kalibrierung-linsenmodell-je-kamera.md).
        "calibration_model": "fisheye",
        # Detector parameters (OpenCV DetectorParameters, attribute name -> value).
        # Status 19.10.2026, camera refocused: the OpenCV default finds all tags. A finer
        # sampling grid (8 instead of 4 pixels per cell) gives +3 to +5 hits of reserve under
        # artificial degradation, without false alarms. errorCorrectionRate deliberately NOT
        # raised -- more range, but wrong IDs. Measurement: doc/projektdoku/kamera-und-detektor-analyse.md.
        # To try alternatives, set VISION_APRILTAG_DETECTOR_PARAMS.
        "detector_parameters": {"perspectiveRemovePixelPerCell": 8},
        # Subpixel corners on (method subpix, since 10.11.2026). Without them, ArUco returns
        # integer corners; a module tag at 1.6 m is only about 85 px in size, and the rounding
        # jitters as 0.6-1.1 px corner noise and 5-10 mm in depth (brightness test 09.11.).
        # subpix refines only a window around each corner found: same hits, about 3% more
        # computing time. NOT "apriltag" -- that searches the whole 12 MP image again, costs
        # about 46% CPU and loses tags (kamera-und-detektor-analyse.md).
        "subpixel_corner_refinement": True,
        "corner_refinement_method": "subpix",
    },
    "cam_flange": {
        # Subpixel corners on (method subpix): the hand camera measures tags for approach
        # moves, and every corner rounding acts as a fixed offset. The CPU load (see
        # AprilTagProfileConfig) was no issue at 640x480; observe it on the Pi at 1920x1080
        # (since 10.11.2026).
        "subpixel_corner_refinement": True,
        "corner_refinement_method": "subpix",
        # Must match CameraStreamConfig.realsense_resolution: the real frames arrive via
        # SharedCamera at that resolution, not at the one entered here. A wrong deviation
        # only shows up at the first job, as a ValueError ("Kalibrierung gilt fuer ...").
        # Full resolution 1920x1080 since 10.11.2026 (previously 640x480).
        "resolution": (1920, 1080),
        # The same 8 cm module tags as on the ceiling (see cam_ceiling).
        "tag_size_m": 0.080,
        "samples_per_job": 5,
        "max_reproj_error_px": 1.5,
        # Hand-eye / calibration board since 28.10.2026: 8x11 squares = 7x10 inner corners,
        # 23 mm per square. 7 + 10 = 17 is odd, so it is unambiguous for the automatic
        # hand-eye calibration (the 180-degree rotation of the board is recognizable). Too
        # small a corner count finds a partial grid with wrongly assigned corners ("board pose
        # fits poorly", about 18 px). The hand camera's intrinsics must be recalibrated with
        # this board (the old 7x9 board with 22 mm per square is obsolete). A plain
        # chessboard, not a ChArUco board.
        "calibration_board_type": "chessboard",
        "calibration_board_cols": 7,
        "calibration_board_rows": 10,
        "calibration_board_square_size_m": 0.023,
        # Brown-Conrady with 5 coefficients: in the image tests of 29.09.2026 the best model
        # at 640x480. Corresponds to the previous behavior, stated here explicitly only.
        "calibration_model": "pinhole",
    },
}

#: Camera settings per frame, only for the Picamera2 backend (the RealSense on the hand Pi
#: has its own fields in ``CameraStreamConfig``).
#:
#: cam_ceiling captures at 12 MP. The livestream is fed by the second stream, scaled by the
#: ISP (lores) -- 960x720 is the stream width (``max_stream_width``) in the sensor's aspect
#: ratio (4056x3040 -> 719.5, rounded to 720; 0.07% deviation, below the limit of
#: ``scale_to_resolution``). 10 fps is the upper limit of the IMX477 in full-resolution
#: mode; requesting more brings nothing. Two buffers instead of the six that Picamera2
#: allocates for video: 6 x 37 MB does not fit into the CMA memory of the Pi.
PI_CAMERA_STREAM_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        "preview_resolution": (960, 720),
        "capture_fps": 10.0,
        "buffer_count": 2,
        # "off" still encodes the full 12 MP frame in the stream instead of the small lores
        # stream (agreed 2026-09-22, camera_stream.py._encoded). The global default of 2.0 s
        # (profiles.py) was not enough: encoding a 4056x3040 image alone reliably exceeded it,
        # and the overlay fell back to the unmarked raw image on every tick (bug: overlay text
        # was missing entirely on Pi 1). Since 21.10.2026, "apriltag" only draws a finished
        # result at full resolution onto the reduced image; its own detection runs in the
        # background (see stream_overlay.py). The reserve is no longer strictly needed, but
        # the generous value stays as a safety margin. cam_flange does not need this: its
        # detection resolution has never changed (640x480 was always the full frame).
        "overlay_timeout_s": 8.0,
        # libcamera controls (ISP/exposure). Best of 14 measured settings (exposure test
        # 19.10.2026, see doc/projektdoku/kamera-und-detektor-analyse.md): slightly darker, so
        # that the white of the tags does not burn out (auto: 15-48%), and somewhat more ISP
        # sharpness and contrast. By contrast, contrast >= 1.25 and matrix metering burn the
        # tags out completely. The calibration must be recorded with these values. To try
        # alternatives, set VISION_CAMERA_CONTROLS.
        "camera_controls": {
            "ExposureValue": -0.5,
            "Sharpness": 1.5,
            "Contrast": 1.15,
            "NoiseReductionMode": "Fast",
        },
    },
}

#: Root of the repo, three levels up from ``src/vision_server/server.py``. Points to
#: ``data/`` and ``config/``. Both deliberately sit next to the source code, not in the
#: package: calibrations belong to the hardware, the tag map to the cell.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def merged_json_object_env(name: str, preset: dict | None) -> dict:
    """Override ``preset`` with the JSON object in environment variable ``name``.

    For each key, the environment variable wins; ``null`` removes a preset entry. Unset or
    empty means the preset is used unchanged.

    Raises:
        ValueError: If the JSON is invalid or is not an object. This happens at startup,
            not in the middle of operation.
    """
    merged = dict(preset or {})
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return merged
    try:
        override = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} ist kein gueltiges JSON ({error}): {raw!r}") from error
    if not isinstance(override, dict):
        raise ValueError(
            f"{name} muss ein JSON-Objekt sein, z. B. '{{\"name\": 1.0}}', nicht {raw!r}"
        )
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def subpixel_corner_settings(preset: dict) -> dict:
    """Return ``subpixel_corner_refinement`` and ``corner_refinement_method`` from preset and environment.

    Preset setting (``cam_ceiling`` and ``cam_flange``: on, method ``"subpix"``; a preset
    without an entry: off). ``VISION_SUBPIXEL_CORNER_REFINEMENT`` overrides it: ``0``/``none``
    off, ``1`` on with the preset's method, ``apriltag``/``subpix`` on with that method.
    Unknown values raise ``ValueError`` at startup.
    """
    enabled = bool(preset.get("subpixel_corner_refinement", False))
    method = str(preset.get("corner_refinement_method", "subpix"))
    raw = os.getenv("VISION_SUBPIXEL_CORNER_REFINEMENT", "").strip().lower()
    if raw in ("0", "none", "false"):
        enabled = False
    elif raw in ("1", "true"):
        enabled = True
    elif raw in ("apriltag", "subpix"):
        enabled, method = True, raw
    elif raw:
        raise ValueError(
            f"VISION_SUBPIXEL_CORNER_REFINEMENT='{raw}': erwartet 0, 1, none, apriltag oder subpix"
        )
    return {"subpixel_corner_refinement": enabled, "corner_refinement_method": method}


def apriltag_config(frame_id: str) -> AprilTagProfileConfig:
    """Return this Pi's AprilTag profile.

    Calibration belongs to the physical camera, hence named after the frame and stored
    under ``data/`` (not versioned). The tag map describes the cell, is the same for both
    Pis, and lives under ``config/``.

    ``VISION_ALLOW_PLACEHOLDER_CALIBRATION=1`` is a deliberate, temporary bypass
    (``AprilTagProfileConfig.allow_placeholder_calibration``) for testing the detection,
    overlay and job path before the real calibration run (test plan section 3.3) exists.
    Poses are then not to scale. Off by default -- it must be set explicitly per Pi, never
    baked into the committed default.
    """
    preset = dict(PI_APRILTAG_PRESETS.get(frame_id, {}))
    subpixel = subpixel_corner_settings(preset)
    for key in subpixel:
        preset.pop(key, None)
    # OpenCV detector parameters: preset, overridden per key by the environment variable
    # (``null`` removes an entry).
    preset["detector_parameters"] = merged_json_object_env(
        "VISION_APRILTAG_DETECTOR_PARAMS", preset.get("detector_parameters")
    )
    # Overrides the preset's lens model, e.g. for a comparison run with the other model,
    # without touching the code.
    model_override = os.getenv("VISION_CALIBRATION_MODEL")
    if model_override:
        preset["calibration_model"] = model_override
    # Hand-eye only where a camera sits on the robot. The ceiling camera is fixed and has no
    # flange -- a path for it would be a file that is never created and would produce a
    # warning at every start.
    hand_eye_path = (
        REPO_ROOT / "data" / "handeye" / f"{frame_id}.json"
        if frame_id == "cam_flange"
        else None
    )
    return AprilTagProfileConfig(
        calibration_path=REPO_ROOT / "data" / "calibration" / f"{frame_id}.json",
        tag_map_path=REPO_ROOT / "config" / "tagmap.json",
        hand_eye_path=hand_eye_path,
        frame_id=frame_id,
        allow_placeholder_calibration=os.getenv("VISION_ALLOW_PLACEHOLDER_CALIBRATION") == "1",
        # Debug artifact, off by default (introduced 2026-09-23, first enabled for both Pis
        # unconditionally, switched to an environment variable on 2026-09-28): every
        # accepted interactive capture is also saved as a PNG per session here, for checking
        # or recomputing outside the server. It was still a noticeable extra SD card load
        # even on the ceiling camera (12 MP) live, even after moving to a separate background
        # pool (see ``calibration_session.py._schedule_capture_save``). That is why it is no
        # longer the default and is only enabled when needed. ``data/`` is gitignored, so no
        # cleanup mechanism is needed; it is emptied by hand.
        calibration_capture_dir=(
            REPO_ROOT / "data" / "calibration" / f"{frame_id}_captures"
            if os.getenv("VISION_SAVE_CALIBRATION_CAPTURES") == "1"
            else None
        ),
        # From the preset (both Pis: on, subpix), see subpixel_corner_settings().
        # VISION_SUBPIXEL_CORNER_REFINEMENT overrides per Pi.
        **subpixel,
        # Diagnostic/tuning switch (2026-09-24): try it on the Pi with real tag distances and
        # py-spy before a production default changes -- see
        # ``AprilTagProfileConfig.apriltag_quad_decimate``.
        apriltag_quad_decimate=float(os.getenv("VISION_APRILTAG_QUAD_DECIMATE", "0.0")),
        **preset,
    )


#: What the two vision systems consist of (OPC 40100-2). Enter only what is actually
#: installed -- an invented model is worse than an empty field, because the asset view is
#: meant for service and maintenance.
PI_ASSET_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        "computing_device_model": "Raspberry Pi",
        "image_sensor_model": "Raspberry Pi Camera Module",
    },
    "cam_flange": {
        "computing_device_model": "Raspberry Pi",
        #: Exact model (D415? D435?) not yet confirmed -- empty would be worse here than
        #: "some RealSense", but an invented model (e.g. "D315", which does not exist) would
        #: be worse still.
        "image_sensor_model": "Intel RealSense",
    },
}


def asset_config(frame_id: str, vision_system_id: str) -> AssetConfig:
    """Asset view of this Pi. The serial number is its vision identity."""
    return AssetConfig(
        serial_number=vision_system_id,
        **PI_ASSET_PRESETS.get(frame_id, {}),
    )


def vision_identity() -> tuple[str, str]:
    """Return this Pi's identity: from the environment, then the hostname mapping, then the hostname.

    The hostname fallback matters because the systemd unit is not versioned anywhere -- a
    freshly installed Pi must not silently send the same ID as the other one.
    """
    host = socket.gethostname()
    mapped_id, mapped_frame = PI_IDENTITIES.get(host, (f"vision-{host}", "world"))
    return (
        os.getenv("VISION_SYSTEM_ID") or mapped_id,
        os.getenv("VISION_FRAME_ID") or mapped_frame,
    )


def vision_camera_backend() -> str:
    """Return this Pi's camera backend: from the environment, then the hostname mapping, then Picamera2."""
    host = socket.gethostname()
    return os.getenv("VISION_CAMERA_BACKEND") or PI_CAMERA_BACKENDS.get(host, "picamera2")


def mdns_instance_name() -> str:
    """Return the service name of this server on the local network.

    It must be unique on the network: if both Pis announce the same name, zeroconf appends
    a ``-2`` to resolve the conflict and the name becomes unpredictable. The vision identity
    is already unique per Pi, so no second name source is set up alongside it.
    """
    return os.getenv("OPCUA_MDNS_NAME") or vision_identity()[0]


#: Prefix of the ApplicationUri, following the cell's convention: the robot server reports
#: ``urn:plcm:robot-server:ur5e``, so we use ``camera-server`` accordingly.
APPLICATION_URI_PREFIX = "urn:plcm:camera-server"

#: Vision identity -> name in the ApplicationUri. A separate table on purpose, not the
#: identity itself: the URI names the *installation point* in the cell, while the vision
#: identity names the recognition system. An unknown Pi falls back to its identity, so that
#: it does not appear without a name.
PI_APPLICATION_NAMES: dict[str, str] = {
    "vision-ceiling-01": "ceiling-01",
    "vision-flange-01": "roboter-hand-01",
}


def application_uri() -> str:
    """Return this server's unique ApplicationUri.

    The cell's aggregation server (``opc.tcp://10.10.38.27:48400/``) lists its modules under
    their ApplicationUri -- ``urn:plcm:robot-server:ur5e``,
    ``urn:smart-business-card-factory:conveyor-system`` and so on. Without a URI of its own,
    asyncua reported its default ``urn:freeopcua:python:server``: meaningless, and both Pis
    reported the same value.
    """
    vision_system_id = vision_identity()[0]
    name = PI_APPLICATION_NAMES.get(vision_system_id, vision_system_id)
    return os.getenv("OPCUA_APPLICATION_URI") or f"{APPLICATION_URI_PREFIX}:{name}"


def vision_config(endpoint: str = ENDPOINT) -> VisionServerConfig:
    """Configuration of the built-in vision system.

    Endpoint, ApplicationURI and ServerName belong to this server. The vision system uses
    from them only its own namespace, its instance name and the nodeset path.
    """
    vision_system_id, frame_id = vision_identity()
    backend = vision_camera_backend()
    apriltag = apriltag_config(frame_id)
    _log.info(
        "Vision-Identitaet: %s (Rahmen %s, Kamera-Backend %s)",
        vision_system_id,
        frame_id,
        backend,
    )
    # Picamera2/OpenCV open the shared camera with ``CameraStreamConfig.resolution`` (see
    # ``camera.py:_open_picamera2``). If that differs from ``AprilTagProfileConfig.resolution``,
    # the captured image has a different aspect ratio than the calibration (placeholder or
    # real), and the stream overlay fails with "Seitenverhaeltnis aendert sich"
    # (``tagloc.calibration.scale_to_resolution``). RealSense is not affected: it has its own
    # field ``realsense_resolution``, already matched to cam_flange's resolution (640x480).
    # Diagnostic switch (2026-09-24): ``VISION_DISABLE_STREAM=1`` for an A/B test of a
    # suspected job timeout, to see whether the stream publisher/overlay is involved at all.
    # ``stream_enabled=False`` only suppresses the stream nodes, the publisher and the MJPEG
    # server (see ``CameraStreamConfig.stream_enabled``). ``resolution`` stays set, because
    # the same ``CameraStreamConfig`` also opens the job camera.
    stream_enabled = os.getenv("VISION_DISABLE_STREAM") != "1"
    # Camera controls: preset, overridden per key by the environment variable. Also read for
    # the other backends, so that a set ``VISION_CAMERA_CONTROLS`` triggers the "is ignored"
    # warning there.
    stream_preset = dict(PI_CAMERA_STREAM_PRESETS.get(frame_id, {}) if backend == "picamera2" else {})
    camera_controls = merged_json_object_env(
        "VISION_CAMERA_CONTROLS", stream_preset.pop("camera_controls", None)
    )
    camera_stream = (
        CameraStreamConfig(
            backend=backend, stream_enabled=stream_enabled, camera_controls=camera_controls
        )
        if backend == "realsense"
        else CameraStreamConfig(
            backend=backend,
            resolution=apriltag.resolution,
            stream_enabled=stream_enabled,
            camera_controls=camera_controls,
            **stream_preset,
        )
    )
    return VisionServerConfig(
        endpoint=endpoint,
        server_name=SERVER_NAME,
        nodeset_path=NODESET_PATH,
        vision_system_id=vision_system_id,
        frame_id=frame_id,
        camera_stream=camera_stream,
        apriltag=apriltag,
        assets=asset_config(frame_id, vision_system_id),
    )


async def main():
    """Build the address space and keep the server running."""
    server = Server()
    await server.init()

    # The endpoint names the LAN IPv4, because ``register_to_discovery()`` passes exactly this
    # on as the DiscoveryUrl to the discovery server. ``0.0.0.0`` would be useless there.
    # Listening still happens on all interfaces, otherwise we would lose 127.0.0.1 -- the
    # handshake client on the Pi uses that.
    endpoint = lds.advertised_endpoint(MDNS_PORT, MDNS_PATH) or ENDPOINT
    server.set_endpoint(endpoint)
    server.socket_address = ("0.0.0.0", MDNS_PORT)
    server.set_server_name(SERVER_NAME)
    # Before the address space is built: the ApplicationUri lands in the namespace array at
    # ns=1 and is the name under which the aggregation server lists this module.
    await server.set_application_uri(application_uri())
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    # The whole address space is built in ``install_vision_machine``: nodesets,
    # ``VisionMachine`` under ``Objects/Machines`` and ``VisionProgram`` next to it. This
    # server no longer creates any nodes itself -- the CPU-temperature demo (``RaspiDevice``,
    # ``2:VisionSystem``, ``CpuTemperatureResult``) has been removed, along with its namespace
    # ``http://launch-rm.de/raspi``.
    machine = await install_vision_machine(server, vision_config(endpoint))

    _log.info("Server startet auf %s", server.endpoint.geturl())

    # Without signal handlers, ``finally`` does not run under systemd: SIGTERM ends the
    # process without asyncio.run cleaning up.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    instance = mdns_instance_name()

    try:
        async with server:
            # The server first, then announcement and registration -- whoever finds the
            # service should also be able to reach it. Both are withdrawn on exit.
            # Both are enabled by default (``OPCUA_MDNS=0`` or ``OPCUA_LDS_URL=""``
            # switch them off).
            async with (
                mdns.announce(instance, MDNS_PORT, MDNS_PATH),
                # The announcement alone is not enough for the cell's aggregation server: it
                # only admits what is registered with the discovery server. The measurement
                # and reasoning are in ``discovery.lds``. On by default; with
                # ``OPCUA_LDS_URL=""`` this returns immediately, and the module does not
                # appear there.
                lds.register(server),
            ):
                # This used to run a 1 Hz loop that kept the demo values current. It ran in the
                # same event loop as the vision system; the loop-lag watchdog in ``runner.py``
                # now reports blockages more reliably than a counter ever could.
                await stop.wait()
    finally:
        await machine.aclose()


if __name__ == "__main__":
    asyncio.run(main())
