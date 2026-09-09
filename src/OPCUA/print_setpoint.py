import asyncio
from asyncua import Client

# Läuft auf demselben Pi wie der Server -> localhost reicht.
# Falls du es von einem anderen Rechner aus testen willst, hier die echte Pi-IP eintragen.
SERVER_URL = "opc.tcp://127.0.0.1:4840/raspi/server/"
SETPOINT_NODE_ID = "ns=2;i=4"  # RaspiDevice/Setpoint


class SetpointHandler:
    def datachange_notification(self, node, val, data):
        print(f"Setpoint aktualisiert: {val}")


async def main():
    async with Client(url=SERVER_URL) as client:
        node = client.get_node(SETPOINT_NODE_ID)
        handler = SetpointHandler()
        subscription = await client.create_subscription(500, handler)
        await subscription.subscribe_data_change(node)

        print(f"Verbunden mit {SERVER_URL}, warte auf Setpoint-Updates (Strg+C zum Beenden)...")
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
