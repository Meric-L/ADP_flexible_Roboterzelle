import asyncio
import logging
from pathlib import Path

from asyncua import Server, ua

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("raspi-opcua")

TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")


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

    _log.info("Server startet auf %s", server.endpoint.geturl())

    async with server:
        n = 0
        while True:
            n += 1
            await counter.write_value(n)
            await cpu_temp.write_value(read_cpu_temp())
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())