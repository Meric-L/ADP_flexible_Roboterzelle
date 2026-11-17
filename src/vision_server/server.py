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

#: Fallback endpoint. Normally the LAN IPv4 is used at runtime (``lds.advertised_endpoint``),
#: because the discovery server gets this address as DiscoveryUrl and ``0.0.0.0`` would be
#: useless there. Listening is always on all interfaces. If no LAN IPv4 is found, this
#: value stays and the server runs without LDS registration.
#: Keep the path ``/raspi/server/``: it appears in the mDNS announcement, the LDS
#: registration and every client configuration.
ENDPOINT = "opc.tcp://0.0.0.0:4840/raspi/server/"
SERVER_NAME = "Raspberry Pi OPC UA Server"

#: Port and path of the mDNS announcement, derived from the endpoint so that clients
#: build a valid URL.
_ENDPOINT_URL = urlparse(ENDPOINT)
MDNS_PORT = _ENDPOINT_URL.port or 4840
MDNS_PATH = _ENDPOINT_URL.path or "/"


#: Hostname -> identity and reference frame. ``vision_system_name`` must not vary per Pi:
#: documentation and frontend hard-code ``ns=<vision>;s=VisionMachine``.
PI_IDENTITIES: dict[str, tuple[str, str]] = {
    "ADP-Roboter-Lokalisierung": ("vision-ceiling-01", "cam_ceiling"),
    "ADP-HandInEye-Kamera-Pi": ("vision-flange-01", "cam_flange"),
}

#: Hostname -> camera backend (see ``CameraStreamConfig.backend``). A host missing here
#: falls back to "picamera2".
PI_CAMERA_BACKENDS: dict[str, str] = {
    "ADP-Roboter-Lokalisierung": "picamera2",
    "ADP-HandInEye-Kamera-Pi": "realsense",
}


#: Per-camera AprilTag values; the only difference between Layer 1 and Layer 2, which run
#: the same server. Ceiling: large tags at a distance, full resolution, looser error bound.
#: Flange: small tags up close, more samples, tighter bound (moves use its pose).
PI_APRILTAG_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        # Full sensor resolution (IMX477); the livestream uses the small lores stream instead.
        "resolution": (4056, 3040),
        # Transition: the old 2028x1520 calibration is 2x2 binning of the same sensor, so its
        # intrinsics scale exactly. Remove after recalibrating at 4056x3040.
        "allow_resolution_mismatch": True,
        # Fallback size (edge of the black square) for tags not in the tag map, i.e. all
        # module and robot tags. A wrong size scales the distance and shifts the tag sideways.
        "tag_size_m": 0.080,
        "samples_per_job": 3,
        "max_reproj_error_px": 3.0,
        # Same printed board as cam_flange: 7x10 inner corners, 23 mm squares. If it is not
        # found, test cols/rows against a real frame with ``tools/diagnose_board.py``.
        "calibration_board_type": "chessboard",
        "calibration_board_cols": 7,
        "calibration_board_rows": 10,
        "calibration_board_square_size_m": 0.023,
        # Kannala-Brandt fits the HQ camera at 4056x3040 best.
        "calibration_model": "fisheye",
        # A finer sampling grid (8 instead of 4 px per cell) adds hit reserve without false
        # alarms. errorCorrectionRate stays default: more range, but wrong IDs.
        # See doc/projektdoku/analysen/kamera-und-detektor-analyse.md; try others via
        # VISION_APRILTAG_DETECTOR_PARAMS.
        "detector_parameters": {"perspectiveRemovePixelPerCell": 8},
        # Subpixel corners: integer corners jitter 5-10 mm in depth for a tag ~85 px wide.
        # "subpix" costs ~3% time; "apriltag" re-searches the whole 12 MP image (~46% CPU)
        # and loses tags (kamera-und-detektor-analyse.md).
        "subpixel_corner_refinement": True,
        "corner_refinement_method": "subpix",
        # Detect only in the cell area (x, y, width, height at 4056x3040): skips walls and
        # desks at the fisheye edge. Override: VISION_DETECTION_ROI.
        "detection_roi": (917, 568, 2114, 2079),
    },
    "cam_flange": {
        # Subpixel corners: tag corner rounding would act as a fixed offset in approach moves.
        "subpixel_corner_refinement": True,
        "corner_refinement_method": "subpix",
        # Must match CameraStreamConfig.realsense_resolution: frames arrive at that size, and a
        # mismatch fails the first job with a ValueError.
        "resolution": (1920, 1080),
        # The same 8 cm module tags as on the ceiling (see cam_ceiling).
        "tag_size_m": 0.080,
        "samples_per_job": 5,
        "max_reproj_error_px": 1.5,
        # Hand-eye / calibration board: plain chessboard (not ChArUco), 7x10 inner corners,
        # 23 mm. 7 + 10 is odd, so the board's 180-degree rotation is distinguishable for the
        # hand-eye calibration. Too few corners find a partial grid with wrong assignments.
        # The hand camera's intrinsics must be recalibrated with this board.
        "calibration_board_type": "chessboard",
        "calibration_board_cols": 7,
        "calibration_board_rows": 10,
        "calibration_board_square_size_m": 0.023,
        # Brown-Conrady fits best at 640x480.
        "calibration_model": "pinhole",
    },
}

#: Per-camera settings, Picamera2 backend only (RealSense has its own fields in
#: ``CameraStreamConfig``). cam_ceiling captures at 12 MP; the livestream uses the
#: ISP-scaled lores stream. 960x720 keeps the sensor's aspect ratio (within the
#: ``scale_to_resolution`` limit). 10 fps is the IMX477 limit at full resolution. Two
#: buffers because the default six (6 x 37 MB) do not fit the Pi's CMA memory.
PI_CAMERA_STREAM_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        "preview_resolution": (960, 720),
        "capture_fps": 10.0,
        "buffer_count": 2,
        # Mode "off" still encodes the full 12 MP frame, which alone exceeded the 2.0 s default
        # and made the overlay fall back to the raw image. Kept as a safety margin.
        "overlay_timeout_s": 8.0,
        # Best of 14 measured settings (kamera-und-detektor-analyse.md): slightly darker so
        # tag whites do not burn out; contrast >= 1.25 or matrix metering does burn them out.
        # Calibrate with these values. Try others via VISION_CAMERA_CONTROLS.
        "camera_controls": {
            "ExposureValue": -0.5,
            "Sharpness": 1.5,
            "Contrast": 1.15,
            "NoiseReductionMode": "Fast",
        },
    },
}

#: Repo root. ``data/`` and ``config/`` sit next to the source, not in the package:
#: calibrations belong to the hardware, the tag map to the cell.
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


def detection_roi_setting(
    preset: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    """Return the tag detection region from preset and ``VISION_DETECTION_ROI``.

    The environment variable overrides the preset: ``"x,y,width,height"`` in pixels of the
    configured resolution, or ``off``/``none``/``0`` for the whole image. Unset or empty
    means the preset applies.

    Raises:
        ValueError: If the value is neither a switch-off word nor four integers. This
            happens at startup, not in the middle of operation.
    """
    raw = os.getenv("VISION_DETECTION_ROI", "").strip().lower()
    if not raw:
        return tuple(preset) if preset is not None else None
    if raw in ("off", "none", "0", "false"):
        return None
    parts = [part.strip() for part in raw.split(",")]
    try:
        values = tuple(int(part) for part in parts)
    except ValueError:
        values = ()
    if len(values) != 4:
        raise ValueError(
            f"VISION_DETECTION_ROI='{raw}': erwartet 'x,y,breite,hoehe' (ganze Zahlen) oder off"
        )
    return values


def apriltag_config(frame_id: str) -> AprilTagProfileConfig:
    """Return this Pi's AprilTag profile.

    Calibration belongs to the physical camera, hence named after the frame and stored
    under ``data/`` (not versioned). The tag map describes the cell, is the same for both
    Pis, and lives under ``config/``.

    ``VISION_ALLOW_PLACEHOLDER_CALIBRATION=1`` temporarily allows a missing calibration
    (``AprilTagProfileConfig.allow_placeholder_calibration``); poses are then not to scale.
    Off by default, set it explicitly per Pi.
    """
    preset = dict(PI_APRILTAG_PRESETS.get(frame_id, {}))
    subpixel = subpixel_corner_settings(preset)
    for key in subpixel:
        preset.pop(key, None)
    # Detector parameters: preset, overridden per key by the env variable (``null`` removes one).
    preset["detector_parameters"] = merged_json_object_env(
        "VISION_APRILTAG_DETECTOR_PARAMS", preset.get("detector_parameters")
    )
    # Override the preset's lens model, e.g. for a comparison run.
    model_override = os.getenv("VISION_CALIBRATION_MODEL")
    if model_override:
        preset["calibration_model"] = model_override
    preset["detection_roi"] = detection_roi_setting(preset.get("detection_roi"))
    # Hand-eye only for the camera on the robot; the fixed ceiling camera has none.
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
        # Debug artifact, off by default (extra SD card load): archives every capture as PNG.
        calibration_capture_dir=(
            REPO_ROOT / "data" / "calibration" / f"{frame_id}_captures"
            if os.getenv("VISION_SAVE_CALIBRATION_CAPTURES") == "1"
            else None
        ),
        # From the preset; VISION_SUBPIXEL_CORNER_REFINEMENT overrides per Pi.
        **subpixel,
        # Tuning switch, see ``AprilTagProfileConfig.apriltag_quad_decimate``.
        apriltag_quad_decimate=float(os.getenv("VISION_APRILTAG_QUAD_DECIMATE", "0.0")),
        **preset,
    )


#: What the two vision systems consist of (OPC 40100-2). Enter only what is actually
#: installed: an invented model is worse than an empty field.
PI_ASSET_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        "computing_device_model": "Raspberry Pi",
        "image_sensor_model": "Raspberry Pi Camera Module",
    },
    "cam_flange": {
        "computing_device_model": "Raspberry Pi",
        #: Exact model (D415/D435) not confirmed; do not invent one.
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


#: Prefix of the ApplicationUri, following the robot server's ``urn:plcm:robot-server:ur5e``.
APPLICATION_URI_PREFIX = "urn:plcm:camera-server"

#: Vision identity -> name in the ApplicationUri. Separate from the identity: the URI names
#: the installation point, the identity the recognition system. Unknown Pis use their identity.
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
    # Picamera2/OpenCV open the camera with ``CameraStreamConfig.resolution``; if its aspect
    # ratio differs from ``AprilTagProfileConfig.resolution``, the overlay fails in
    # ``scale_to_resolution``. RealSense uses ``realsense_resolution`` instead.
    # ``VISION_DISABLE_STREAM=1`` only suppresses the stream (see
    # ``CameraStreamConfig.stream_enabled``); ``resolution`` stays set for the job camera.
    stream_enabled = os.getenv("VISION_DISABLE_STREAM") != "1"
    # Camera controls: preset, overridden per key by the env variable. Read for all backends
    # so a set ``VISION_CAMERA_CONTROLS`` triggers the "ignored" warning there.
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

    # Advertise the LAN IPv4 (the discovery server needs a reachable address) but listen on
    # all interfaces, so 127.0.0.1 keeps working for the local handshake client.
    endpoint = lds.advertised_endpoint(MDNS_PORT, MDNS_PATH) or ENDPOINT
    server.set_endpoint(endpoint)
    server.socket_address = ("0.0.0.0", MDNS_PORT)
    server.set_server_name(SERVER_NAME)
    # Before the address space is built: the ApplicationUri is the module's name in the
    # aggregation server.
    await server.set_application_uri(application_uri())
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    # The whole address space is built in ``install_vision_machine``.
    machine = await install_vision_machine(server, vision_config(endpoint))

    _log.info("Server startet auf %s", server.endpoint.geturl())

    # Signal handlers make SIGTERM (systemd) run the ``finally`` cleanup.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    instance = mdns_instance_name()

    try:
        async with server:
            # Server first, then announcement and registration; both are withdrawn on exit
            # (disable with ``OPCUA_MDNS=0`` / ``OPCUA_LDS_URL=""``).
            async with (
                mdns.announce(instance, MDNS_PORT, MDNS_PATH),
                # The aggregation server only admits modules registered with the discovery
                # server (see ``discovery.lds``).
                lds.register(server),
            ):
                await stop.wait()
    finally:
        await machine.aclose()


if __name__ == "__main__":
    asyncio.run(main())
