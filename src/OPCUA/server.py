"""OPC-UA-Server der Roboterzelle: Raspi-Interface und Vision-System.

Ein Server, zwei Baeume: das gewachsene `RaspiDevice`-Interface (CPU-Temperatur,
Zaehler, Sollwert) und das Vision-System nach OPC 40100 aus `vision_server`.
Reihenfolge und Namespace-Registrierung bleiben unveraendert, damit vorhandene
NodeIds (z. B. `ns=2;i=4` fuer den Sollwert) weiter gueltig sind.
"""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vision_server.config import VisionServerConfig  # noqa: E402
from vision_server.profiles import (  # noqa: E402
    AprilTagProfileConfig,
    AssetConfig,
    CameraStreamConfig,
)
from vision_server.runner import install_vision_machine  # noqa: E402

import ua_lds  # noqa: E402
import ua_mdns  # noqa: E402

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("raspi-opcua")

TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
NODESET_PATH = Path(__file__).parent / "Opc.Ua.MachineVision.NodeSet2.xml"
MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"
RESULT_TYPE_NODEID = 2002  # 1:ResultType im Machine-Vision-Nodeset

ENDPOINT = "opc.tcp://0.0.0.0:4840/raspi/server/"
SERVER_NAME = "Raspberry Pi OPC UA Server"

#: Port und Pfad fuer die mDNS-Ankuendigung, aus dem Endpoint abgeleitet statt
#: daneben gepflegt. Ein Client baut `opc.tcp://<ip>:<port><path>` zusammen --
#: ein abweichender Pfad ergibt bei jedem Client eine unbrauchbare URL.
_ENDPOINT_URL = urlparse(ENDPOINT)
MDNS_PORT = _ENDPOINT_URL.port or 4840
MDNS_PATH = _ENDPOINT_URL.path or "/"


def read_cpu_temp() -> float:
    """Liest die CPU-Temperatur in Grad Celsius."""
    try:
        return int(TEMP_PATH.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return float("nan")


#: Hostname -> Identitaet und Bezugsrahmen. `vision_system_name` darf NICHT
#: variieren: Interface-Doku und Frontend nageln `ns=<vision>;s=VisionMachine`
#: fest, ein pi-spezifischer BrowseName bricht jeden Client.
PI_IDENTITIES: dict[str, tuple[str, str]] = {
    "ADP-Roboter-Lokalisierung": ("vision-ceiling-01", "cam_ceiling"),
    "ADP-HandInEye-Kamera-Pi": ("vision-flange-01", "cam_flange"),
}

#: Hostname -> Kamera-Backend (siehe `CameraStreamConfig.backend`). Deckel-Pi
#: nutzt weiterhin Picamera2, der Hand-Pi eine Intel RealSense per
#: `pyrealsense2`. Fehlt ein Host hier, gilt "picamera2" als bisheriger
#: Default -- ein frisch aufgesetzter dritter Pi bricht damit nicht stumm.
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
        "resolution": (2028, 1520),
        "tag_size_m": 0.100,
        "samples_per_job": 3,
        "max_reproj_error_px": 3.0,
    },
    "cam_flange": {
        # Muss zu CameraStreamConfig.realsense_resolution passen: Die echten
        # Frames kommen ueber SharedCamera in dieser Aufloesung an, nicht in
        # der hier eingetragenen. Eine falsche Abweichung faellt erst beim
        # ersten Job als ValueError ("Kalibrierung gilt fuer ...") auf.
        "resolution": (640, 480),
        "tag_size_m": 0.050,
        "samples_per_job": 5,
        "max_reproj_error_px": 1.5,
    },
}

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def apriltag_config(frame_id: str) -> AprilTagProfileConfig:
    """Return this Pi's AprilTag profile.

    Calibration belongs to the physical camera, hence named after the frame
    and stored under `data/` (not versioned). The tag map describes the
    cell, is the same for both Pis, and lives under `config/`.

    `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` is a deliberate, temporary
    bypass (`AprilTagProfileConfig.allow_placeholder_calibration`) for
    testing detection/overlay/job path before the real calibration run
    (Testplan Abschnitt 3.3) exists. Poses are then not to scale. Default
    off -- must be set explicitly per Pi, never baked into the committed
    default.
    """
    preset = PI_APRILTAG_PRESETS.get(frame_id, {})
    return AprilTagProfileConfig(
        calibration_path=REPO_ROOT / "data" / "calibration" / f"{frame_id}.json",
        tag_map_path=REPO_ROOT / "config" / "tagmap.json",
        frame_id=frame_id,
        allow_placeholder_calibration=os.getenv("VISION_ALLOW_PLACEHOLDER_CALIBRATION") == "1",
        **preset,
    )


#: Woraus die beiden Vision-Systeme bestehen (OPC 40100-2). Nur eintragen, was
#: wirklich verbaut ist -- ein erfundenes Modell ist schlechter als ein leeres
#: Feld, weil die Anlagensicht fuer Service und Instandhaltung gedacht ist.
PI_ASSET_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        "computing_device_model": "Raspberry Pi",
        "image_sensor_model": "Raspberry Pi Camera Module",
    },
    "cam_flange": {
        "computing_device_model": "Raspberry Pi",
        #: Genaues Modell (D415? D435?) noch nicht bestaetigt -- leer waere
        #: hier schlechter als "irgendein RealSense", aber ein erfundenes
        #: Modell (z. B. "D315", existiert nicht) waere schlimmer als das.
        "image_sensor_model": "Intel RealSense",
    },
}


def asset_config(frame_id: str, vision_system_id: str) -> AssetConfig:
    """Anlagensicht dieses Pis. Die Seriennummer ist seine Vision-Identitaet."""
    return AssetConfig(
        serial_number=vision_system_id,
        **PI_ASSET_PRESETS.get(frame_id, {}),
    )


def vision_identity() -> tuple[str, str]:
    """Identitaet dieses Pis: Env, dann Hostname-Abbildung, dann Hostname.

    Der Hostname-Rueckfall ist wichtig, weil die systemd-Unit nirgends
    versioniert ist — ein frisch aufgesetzter Pi darf nicht stillschweigend
    dieselbe Id senden wie der andere.
    """
    host = socket.gethostname()
    mapped_id, mapped_frame = PI_IDENTITIES.get(host, (f"vision-{host}", "world"))
    return (
        os.getenv("VISION_SYSTEM_ID") or mapped_id,
        os.getenv("VISION_FRAME_ID") or mapped_frame,
    )


def vision_camera_backend() -> str:
    """Kamera-Backend dieses Pis: Env, dann Hostname-Abbildung, dann Picamera2."""
    host = socket.gethostname()
    return os.getenv("VISION_CAMERA_BACKEND") or PI_CAMERA_BACKENDS.get(host, "picamera2")


def mdns_instance_name() -> str:
    """Dienstname dieses Servers im lokalen Netz.

    Muss im Netz eindeutig sein: kuendigen beide Pis denselben Namen an,
    haengt zeroconf zur Konfliktaufloesung ein `-2` an und der Name wird
    unvorhersehbar. Die Vision-Identitaet ist bereits pro Pi eindeutig --
    deshalb keine zweite Namensquelle danebenstellen.
    """
    return os.getenv("OPCUA_MDNS_NAME") or vision_identity()[0]


#: Praefix der ApplicationUri, nach der Konvention der Zelle: der Roboterserver
#: meldet `urn:plcm:robot-server:ur5e`, wir entsprechend `camera-server`.
APPLICATION_URI_PREFIX = "urn:plcm:camera-server"

#: Vision-Identitaet -> Name in der ApplicationUri. Bewusst eine eigene Tabelle
#: und nicht die Identitaet selbst: die Uri benennt den *Einbauort* in der
#: Zelle, die Vision-Identitaet benennt das Erkennungssystem. Ein unbekannter
#: Pi faellt auf seine Identitaet zurueck, damit er nicht namenlos auftaucht.
PI_APPLICATION_NAMES: dict[str, str] = {
    "vision-ceiling-01": "ceiling-01",
    "vision-flange-01": "roboter-hand-01",
}


def application_uri() -> str:
    """Eindeutige ApplicationUri dieses Servers.

    Der Aggregation-Server der Zelle (`opc.tcp://10.10.38.27:48400/`) fuehrt
    seine Module unter ihrer ApplicationUri -- `urn:plcm:robot-server:ur5e`,
    `urn:smart-business-card-factory:conveyor-system` und so fort. Ohne eigene
    Uri meldete asyncua seinen Default `urn:freeopcua:python:server`:
    nichtssagend, und beide Pis meldeten denselben Wert.
    """
    vision_system_id = vision_identity()[0]
    name = PI_APPLICATION_NAMES.get(vision_system_id, vision_system_id)
    return os.getenv("OPCUA_APPLICATION_URI") or f"{APPLICATION_URI_PREFIX}:{name}"


def vision_config() -> VisionServerConfig:
    """Konfiguration des eingebauten Vision-Systems.

    Endpoint, ApplicationURI und ServerName sind die dieses Servers; das
    Vision-System nutzt daraus nur seinen eigenen Namespace, den Instanznamen
    und den Nodeset-Pfad.
    """
    vision_system_id, frame_id = vision_identity()
    backend = vision_camera_backend()
    _log.info(
        "Vision-Identitaet: %s (Rahmen %s, Kamera-Backend %s)",
        vision_system_id,
        frame_id,
        backend,
    )
    return VisionServerConfig(
        endpoint=ENDPOINT,
        server_name=SERVER_NAME,
        nodeset_path=NODESET_PATH,
        vision_system_id=vision_system_id,
        frame_id=frame_id,
        camera_stream=CameraStreamConfig(backend=backend),
        apriltag=apriltag_config(frame_id),
        assets=asset_config(frame_id, vision_system_id),
    )


async def main():
    """Baut den Adressraum auf und haelt die Werte des Raspi-Interfaces aktuell."""
    server = Server()
    await server.init()

    server.set_endpoint(ENDPOINT)
    server.set_server_name(SERVER_NAME)
    # Vor dem Aufbau des Adressraums: die ApplicationUri landet im
    # Namespace-Array auf ns=1 und ist der Name, unter dem der
    # Aggregation-Server dieses Modul fuehrt.
    await server.set_application_uri(application_uri())
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    idx = await server.register_namespace("http://launch-rm.de/raspi")

    device = await server.nodes.objects.add_object(idx, "RaspiDevice")

    cpu_temp = await device.add_variable(
        idx, "CpuTemperature", 0.0, ua.VariantType.Double
    )
    counter = await device.add_variable(idx, "Counter", 0, ua.VariantType.Int64)
    setpoint = await device.add_variable(idx, "Setpoint", 0.0, ua.VariantType.Double)

    await setpoint.set_writable()

    _log.info("Importiere Machine-Vision-Nodeset von %s", NODESET_PATH)
    await server.import_xml(str(NODESET_PATH))
    mv_idx = await server.get_namespace_index(MACHINE_VISION_NAMESPACE_URI)

    vision_system_type = await server.nodes.base_object_type.get_child(
        f"{mv_idx}:VisionSystemType"
    )
    vision_system = await server.nodes.objects.add_object(
        idx, "VisionSystem", objecttype=vision_system_type
    )

    await server.nodes.server.add_reference(
        vision_system, ua.ObjectIds.HasNotifier, forward=True
    )
    await vision_system.set_event_notifier([ua.EventNotifier.SubscribeToEvents])

    result_management = await vision_system.get_child(f"{mv_idx}:ResultManagement")
    results_folder = await result_management.get_child(f"{mv_idx}:Results")

    result_type = server.get_node(ua.NodeId(RESULT_TYPE_NODEID, mv_idx))
    result_nodes = await instantiate(
        results_folder, result_type, bname=f"{idx}:CpuTemperatureResult"
    )
    temperature_result = result_nodes[0]
    result_content = await temperature_result.get_child(f"{mv_idx}:ResultContent")
    await result_content.write_attribute(
        ua.AttributeIds.DataType,
        ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.Double), ua.VariantType.NodeId)),
    )

    machine = await install_vision_machine(server, vision_config())

    _log.info("Server startet auf %s", server.endpoint.geturl())

    # Ohne Signal-Handler laeuft `finally` unter systemd nicht: SIGTERM beendet
    # den Prozess, ohne dass asyncio.run aufraeumt.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    instance = mdns_instance_name()

    try:
        async with server:
            # Erst der Server, dann Ankuendigung und Registrierung -- wer den
            # Dienst findet, soll ihn auch erreichen. Beim Verlassen werden
            # beide zurueckgezogen.
            async with (
                ua_mdns.announce(instance, MDNS_PORT, MDNS_PATH),
                # Die Ankuendigung allein genuegt dem Aggregation-Server der
                # Zelle nicht: er nimmt nur auf, was beim Discovery-Server
                # registriert ist. Messung und Begruendung stehen in `ua_lds`.
                ua_lds.register(
                    application_uri=application_uri(),
                    server_name=SERVER_NAME,
                    port=MDNS_PORT,
                    path=MDNS_PATH,
                    mdns_name=instance,
                ),
            ):
                n = 0
                while not stop.is_set():
                    n += 1
                    temp = read_cpu_temp()
                    await counter.write_value(n)
                    await cpu_temp.write_value(temp)
                    await result_content.write_value(temp, ua.VariantType.Double)
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=1.0)
    finally:
        await machine.aclose()


if __name__ == "__main__":
    asyncio.run(main())
