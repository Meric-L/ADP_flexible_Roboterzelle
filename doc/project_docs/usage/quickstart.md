# Quickstart

Three minimal copy-paste examples: run a vision job from Python, find the Pis via mDNS, and use them from the WSC web app.

Install first: [`installation.md`](installation.md).

## 1. Run a vision job with `asyncua`

Connect, browse `Objects`, write the recipe, call `Start`, wait for `Ready`, read the result. `Start` takes **no** arguments: the recipe goes into `ParameterSet/RecipeId` first.

```python
import asyncio, json
from asyncua import Client, ua

URL = "opc.tcp://<pi>:4840/raspi/server/"   # ceiling or hand Pi

async def main():
    async with Client(URL) as client:
        ns = await client.get_namespace_index("http://launch-rm.de/vision")
        print([(await n.read_browse_name()).Name for n in await client.nodes.objects.get_children()])
        node = lambda s: client.get_node(ua.NodeId(s, ns))
        await node("VisionProgram.ParameterSet.RecipeId").write_value("apriltag", ua.VariantType.String)
        await node("VisionProgram").call_method(node("VisionProgram.Start"))
        state = await node("VisionProgram").get_child("0:CurrentState")
        while (await state.read_value()).Text != "Ready":
            await asyncio.sleep(0.2)
        print("ErrorCode:", await node("VisionProgram.ResultSet.ErrorCode").read_value())   # 0 = OK
        print(json.loads(await node("VisionMachine.LatestResultJson").read_value())["detections"])

asyncio.run(main())
```

- `RecipeId` is `apriltag` or `calibration`; an empty value is rejected with `ErrorCode` 4 (`UNKNOWN_RECIPE`).
- `Start` returns once the state is `Running`, or `Ready` with `ErrorCode` != 0 (then the loop ends at once).
- `LatestResultJson` is the node the `ResultSet/LatestResultJson` reference points to.

How it works: [`interfaces/part10-program-interface.md`](../interfaces/part10-program-interface.md) (flow in section 5), [`components/part10/README.md`](../components/part10/README.md)

## 2. Find the Pis via mDNS

```python
import time
from zeroconf import Zeroconf, ServiceBrowser

class Listener:
    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info:
            path = info.properties.get(b"path", b"").decode()
            print(name, f"opc.tcp://{info.parsed_addresses()[0]}:{info.port}{path}")
    update_service = remove_service = lambda self, *a: None

zc = Zeroconf()
ServiceBrowser(zc, "_opcua-tcp._tcp.local.", Listener())
time.sleep(15); zc.close()
```

- Prints one line per announced OPC UA server, e.g. `vision-ceiling-01._opcua-tcp._tcp.local.` with its `/raspi/server/` URL.
- Read the path from the TXT entry; other modules use other paths. mDNS stops at the subnet boundary.
- In the lab network the aggregation server (`10.10.38.27`) re-announces registered servers; those entries carry its address, the server's own IP is in the instance name.

How it works: [`components/vision-server/README.md`](../components/vision-server/README.md), [`interfaces/part10-program-interface.md`](../interfaces/part10-program-interface.md) (section 2)

## 3. Use the Pis from WSC

```bash
cd backend  && uv run backend      # terminal 1: http://127.0.0.1:8000
cd frontend && npm run dev         # terminal 2: http://localhost:1234
```

- The backend browses mDNS and announces the servers to the frontend, which connects to each one. Open `http://localhost:1234`; the ceiling and hand Pi appear as devices of kind `vision`.
- Not on the Pis' subnet or mDNS blocked: connect by URL with the existing `ConnectOpcUa` input, or start the backend with `WSC_MDNS=0`.

How it works: [`components/webskill-backend/README.md`](../components/webskill-backend/README.md), [`integration.md`](integration.md)

## Notes

- Examples 1 and 2 were run against the lab network (ceiling Pi `10.10.38.104`); example 1 needs a vision server with a camera, there is no camera-free source.
- The reference client for the 40100 route is `src/vision_server/tools/handshake_client.py` (see [`installation.md`](installation.md#check-that-it-runs)).
