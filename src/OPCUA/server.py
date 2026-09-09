import asyncio
import logging
from pathlib import Path

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("raspi-opcua")

TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
NODESET_PATH = Path(__file__).parent / "Opc.Ua.MachineVision.NodeSet2.xml"
MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"
RESULT_TYPE_NODEID = 2002  # 1:ResultType im Machine-Vision-Nodeset


def read_cpu_temp() -> float:
    try:
        return int(TEMP_PATH.read_text().strip()) / 1000.0
    except (OSError, ValueError):
        return float("nan")


async def main():
    server = Server()
    await server.init()

    server.set_endpoint("opc.tcp://0.0.0.0:4840/raspi/server/")
    server.set_server_name("Raspberry Pi OPC UA Server")
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

    _log.info("Server startet auf %s", server.endpoint.geturl())

    async with server:
        n = 0
        while True:
            n += 1
            temp = read_cpu_temp()
            await counter.write_value(n)
            await cpu_temp.write_value(temp)
            await result_content.write_value(temp, ua.VariantType.Double)
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())