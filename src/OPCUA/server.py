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

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vision_server.config import VisionServerConfig  # noqa: E402
from vision_server.runner import install_vision_machine  # noqa: E402

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("raspi-opcua")

TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
NODESET_PATH = Path(__file__).parent / "Opc.Ua.MachineVision.NodeSet2.xml"
MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"
RESULT_TYPE_NODEID = 2002  # 1:ResultType im Machine-Vision-Nodeset

ENDPOINT = "opc.tcp://0.0.0.0:4840/raspi/server/"
SERVER_NAME = "Raspberry Pi OPC UA Server"


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
    "pi-decke": ("vision-ceiling-01", "cam_ceiling"),
    "pi-hand": ("vision-flange-01", "cam_flange"),
}


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


def vision_config() -> VisionServerConfig:
    """Konfiguration des eingebauten Vision-Systems.

    Endpoint, ApplicationURI und ServerName sind die dieses Servers; das
    Vision-System nutzt daraus nur seinen eigenen Namespace, den Instanznamen
    und den Nodeset-Pfad.
    """
    vision_system_id, frame_id = vision_identity()
    _log.info("Vision-Identitaet: %s (Rahmen %s)", vision_system_id, frame_id)
    return VisionServerConfig(
        endpoint=ENDPOINT,
        server_name=SERVER_NAME,
        nodeset_path=NODESET_PATH,
        vision_system_id=vision_system_id,
        frame_id=frame_id,
    )


async def main():
    """Baut den Adressraum auf und haelt die Werte des Raspi-Interfaces aktuell."""
    server = Server()
    await server.init()

    server.set_endpoint(ENDPOINT)
    server.set_server_name(SERVER_NAME)
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

    try:
        async with server:
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
