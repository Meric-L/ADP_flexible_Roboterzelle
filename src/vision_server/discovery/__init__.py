"""Make the server discoverable on the network: mDNS and the Local Discovery Server.

Two paths side by side, because they serve different readers:

* `mdns` announces the service on the subnet, where clients such as the WSC frontend
  find it directly. `OPCUA_MDNS=0` turns it off.
* `lds` registers the server with the cell's Local Discovery Server. Only through it
  does the aggregation server pick up the module. `OPCUA_LDS_URL=""` turns it off.

Both are on by default and fault-tolerant: if one fails, the server keeps running and
logs a warning.
"""

from . import lds, mdns

__all__ = ["lds", "mdns"]
