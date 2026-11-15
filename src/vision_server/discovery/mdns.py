"""mDNS announcement of an OPC UA server on the local network.

Implements the maintainer's quick guide. The file is not in the repo, because
`betreuer/` was removed on 23.09.2026. It uses the service type
`_opcua-tcp._tcp.local.`, the TXT entries `path` and `caps`, registration when
the server runs, and deregistration on shutdown.

Two deliberate deviations from the guide:

1. The guide hard-codes an IP in the source, which also fixes the endpoint.
   Here the LAN IPv4 is detected at runtime, and the endpoint is not changed.
   The server still binds to `0.0.0.0`, so it is reachable on every interface
   **and** on 127.0.0.1. The real LAN address is still announced, because
   `0.0.0.0` is useless to a client. A DHCP reservation in the router makes this
   stable, without a statically configured address on the Pi.
2. `zeroconf` is optional. If the package is missing or registration fails, the
   server keeps running and logs a warning. A server reachable by URL is worth
   more than none.

**On by default** (again since 21.10.2026; it was off from 23.09. to 21.10.2026).
`OPCUA_MDNS=0` (or `false`/`no`/`off`) disables the announcement. The server then
still runs and is reachable by URL.
"""

import contextlib
import logging
import os
import re
import socket
from collections.abc import AsyncIterator

_log = logging.getLogger(__name__)

#: Service type for OPC UA servers over TCP.
SERVICE_TYPE = "_opcua-tcp._tcp.local."

#: Target used only to pick the route; no packet is sent (TEST-NET-1).
_ROUTE_PROBE = ("192.0.2.1", 9)

_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9-]+")


def enabled() -> bool:
    """Return whether to announce; yes by default.

    `OPCUA_MDNS=0` (or `false`/`no`/`off`) disables the announcement.
    Empty or unset counts as enabled.
    """
    return os.getenv("OPCUA_MDNS", "").strip().lower() not in ("0", "false", "no", "off")


def sanitize_instance_name(name: str) -> str:
    """Turn any name into a valid mDNS instance name.

    Only letters, digits and hyphens are allowed. An empty result cannot be
    addressed on the network, so it falls back to `opcua-server`.
    """
    cleaned = _NAME_ALLOWED.sub("-", name).strip("-")
    return cleaned or "opcua-server"


def detect_lan_ipv4() -> str | None:
    """Detect the LAN IPv4 address of this machine.

    `OPCUA_ADVERTISE_IP` takes precedence. It forces the announcement onto a
    specific interface, for example when LAN and WLAN are both connected.
    Otherwise the routing table decides. A connected UDP socket reveals the
    source address without sending anything.
    """
    override = os.getenv("OPCUA_ADVERTISE_IP")
    if override:
        return override
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(_ROUTE_PROBE)
        address = sock.getsockname()[0]
    except OSError:
        _log.warning("Keine LAN-IPv4 ermittelbar; mDNS wird uebersprungen")
        return None
    finally:
        sock.close()
    if address.startswith("127."):
        _log.warning("Nur Loopback (%s) gefunden; mDNS wird uebersprungen", address)
        return None
    return address


@contextlib.asynccontextmanager
async def announce(
    name: str,
    port: int,
    path: str,
    address: str | None = None,
    caps: str = "DA",
) -> AsyncIterator[str | None]:
    """Announce the server and deregister it on exit.

    Args:
        name: Instance name, unique on the network. It is sanitized.
        port: Port of the endpoint.
        path: Path of the endpoint, e.g. `/raspi/server/`. Clients build
            `opc.tcp://<ip>:<port><path>` from it. A wrong path gives every
            client an unusable URL.
        address: IPv4 to announce. If not given, it is detected.
        caps: Server capabilities in the TXT entry `caps`. The quick guide says
            `NA`. The other cell modules (CardDispenser, Conveyor) announce
            themselves with `DA` (Data Access) since 2026-09-21. A client that
            filters on that would miss us with `NA`, so `DA` is used here.

    Yields:
        The announced address, or ``None`` if nothing was announced.
    """
    if not enabled():
        _log.info("mDNS abgeschaltet (OPCUA_MDNS=0); keine Ankuendigung")
        yield None
        return

    ip = address or detect_lan_ipv4()
    if ip is None:
        yield None
        return

    try:
        from zeroconf import IPVersion, ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf
    except ImportError:
        _log.warning(
            "Paket 'zeroconf' fehlt -- keine mDNS-Ankuendigung. "
            "Installation: pip install zeroconf"
        )
        yield None
        return

    instance = sanitize_instance_name(name)
    info = ServiceInfo(
        type_=SERVICE_TYPE,
        name=f"{instance}.{SERVICE_TYPE}",
        server=f"{instance}.local.",
        parsed_addresses=[ip],
        port=port,
        properties={"path": path, "caps": caps},
    )

    mdns = None
    try:
        # Bind to the detected interface: on a Pi with LAN and WLAN, an
        # announcement on the wrong side would reach nobody.
        mdns = AsyncZeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
        await (await mdns.async_register_service(info))
        _log.info(
            "mDNS aktiv: %s -> opc.tcp://%s:%d%s", f"{instance}.local.", ip, port, path
        )
        yield ip
    except Exception:
        _log.exception("mDNS-Ankuendigung fehlgeschlagen; Server laeuft ohne sie weiter")
        yield None
    finally:
        if mdns is not None:
            # Deregister so that no client keeps a dead address in its cache.
            # This only runs if the process catches SIGTERM.
            with contextlib.suppress(Exception):
                await mdns.async_unregister_service(info)
            with contextlib.suppress(Exception):
                await mdns.async_close()
            _log.info("mDNS-Ankuendigung zurueckgezogen")
