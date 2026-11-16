"""Make the server discoverable on the network: mDNS and the Local Discovery Server.

Two paths side by side, because they serve different readers:

* `mdns` announces the service on the subnet. Clients such as the WSC frontend
  find us through it directly. On by default (back on since 2026-10-21);
  `OPCUA_MDNS=0` turns it off.
* `lds` registers the server with the cell's Local Discovery Server. **Only**
  through it does the aggregation server pick up the module; an mDNS
  announcement alone is not enough for it. Measured in the lab on 2026-09-21,
  reasoning in the module. On by default (back on since 2026-10-21);
  `OPCUA_LDS_URL=""` turns it off.

Both are deliberately fault-tolerant: if one fails, the server keeps running
and logs a warning. A server reachable by URL is worth more than none at all.
"""

from . import lds, mdns

__all__ = ["lds", "mdns"]
