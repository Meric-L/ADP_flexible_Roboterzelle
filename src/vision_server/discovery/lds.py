"""Registration of the OPC UA server with the cell's Local Discovery Server.

Complements the mDNS announcement from `mdns`, does not replace it. The aggregation
server collects its modules only from the discovery server, not via mDNS, so an active
registration is required. Clients on the subnet still find the server through mDNS.

The actual work is done by `asyncua.Server.register_to_discovery()`, which keeps the
registration fresh with its own renewal loop. This module wraps it and handles three
points:

1. **The advertised address must be the LAN IPv4.** `register_to_discovery()` registers
   `server.endpoint.geturl()`. With `0.0.0.0` in it, the aggregation server connects to
   nothing. `Server.socket_address` fixes this: the endpoint names the LAN IPv4, while
   listening still happens on `0.0.0.0`. `advertised_endpoint()` builds the URL,
   `server.py` sets both. `register()` refuses to register if `0.0.0.0` remains.
2. **An unreachable LDS must not prevent startup.** The error is logged and the server
   runs on without the registration.
3. **`Server.stop()` does not unregister.** The entry would stay in the LDS until it
   expires, so `unregister_from_discovery()` runs in a `finally`.

Registration is on by default and uses `CELL_LDS_URL` unless `OPCUA_LDS_URL` is set;
`OPCUA_LDS_URL=""` disables it. It is a plain `RegisterServer` without
`MdnsDiscoveryConfiguration`, as the other cell modules do it.
"""

import contextlib
import logging
import os
from collections.abc import AsyncIterator

from asyncua import Server

from . import mdns

_log = logging.getLogger(__name__)

#: Discovery server of the cell.
CELL_LDS_URL = "opc.tcp://10.10.38.27:4840/"

#: Registration is on by default. `OPCUA_LDS_URL=""` disables it.
DEFAULT_LDS_URL = CELL_LDS_URL

#: Renewal is necessary: a one-time registration is lost when the LDS restarts, and
#: the LDS does not rebuild its inventory from the modules themselves.
#: 60 s is also the default of `register_to_discovery()`; how long an entry actually
#: survives without renewal has not been measured.
DEFAULT_RENEW_SECONDS = 60


def lds_url() -> str:
    """Return the URL of the discovery server; empty means: do not register."""
    return os.getenv("OPCUA_LDS_URL", DEFAULT_LDS_URL).strip()


def advertised_endpoint(port: int, path: str, address: str | None = None) -> str | None:
    """Return the endpoint URL that the server advertises to the outside.

    Carries the LAN IPv4 instead of `0.0.0.0`, so that the DiscoveryUrl built from it
    is usable for the aggregation server. Listening happens on all interfaces
    regardless -- see `Server.socket_address` in `server.py`.

    Returns
    -------
    The URL, or `None` if no LAN IPv4 could be determined. The previous endpoint is
    then kept, and `register()` does not register.
    """
    ip = address or mdns.detect_lan_ipv4()
    if ip is None:
        return None
    return f"opc.tcp://{ip}:{port}{path}"


@contextlib.asynccontextmanager
async def register(
    server: Server,
    url: str | None = None,
    renew_seconds: int = DEFAULT_RENEW_SECONDS,
) -> AsyncIterator[str | None]:
    """Register the server with the LDS and deregister again on exit.

    Parameters
    ----------
    server
        The already configured server. Its ApplicationUri, its name and
        `server.endpoint` are registered as DiscoveryUrl -- so the endpoint must
        name the LAN IPv4, not `0.0.0.0`.
    url
        Discovery server; if not given, `lds_url()` decides.
    renew_seconds
        Interval between renewals; `0` registers once.

    Yields
    ------
    The registered DiscoveryUrl, or `None` if no registration took place.
    Does not raise -- errors end up in the log.
    """
    target = url if url is not None else lds_url()
    if not target:
        _log.info("Kein Discovery-Server konfiguriert; keine Anmeldung")
        yield None
        return

    advertised = server.endpoint.geturl()
    if server.endpoint.hostname in (None, "0.0.0.0", "::"):
        # Would be worthless for the aggregation server: it takes over the address
        # and connects to nothing. Better not to register at all.
        _log.warning(
            "Endpoint %s nennt keine erreichbare Adresse; LDS-Anmeldung "
            "uebersprungen",
            advertised,
        )
        yield None
        return

    try:
        await server.register_to_discovery(target, period=renew_seconds)
    except Exception:
        _log.exception(
            "LDS-Anmeldung bei %s fehlgeschlagen; Server laeuft ohne sie weiter",
            target,
        )
        yield None
        return

    _log.info("Beim Discovery-Server %s angemeldet: %s", target, advertised)
    try:
        yield advertised
    finally:
        # Deregister so that the aggregation server does not connect to a dead
        # address. Only takes effect if the process catches SIGTERM.
        try:
            await server.unregister_from_discovery(target)
            _log.info("Beim Discovery-Server abgemeldet")
        except Exception as exc:
            _log.warning("LDS-Abmeldung fehlgeschlagen: %s", exc)
