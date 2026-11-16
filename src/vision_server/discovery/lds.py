"""Registration of the OPC UA server with the cell's Local Discovery Server.

Complements the mDNS announcement from `mdns`, does not replace it.

Why both are needed (measured in the lab on 2026-09-21):
The aggregation server (`opc.tcp://10.10.38.27:48400/`) does **not** collect its
modules via mDNS, although the supervisor's quick guide claimed it did. On the same
machine runs an open62541 discovery server at `opc.tcp://10.10.38.27:4840/`, and the
aggregation server lists exactly the servers registered there. One only gets into
this inventory through an active call; an mDNS announcement alone does not get in.
Cross-check: the aggregation server restarted at 15:56 and, on the fresh scan, picked
up all five registered modules -- our two Pis, which had been running and broadcasting
for ten minutes at that point, were left out.

The mDNS announcement stays anyway: clients on the subnet (among them the WSC frontend)
find us through it without a detour via the discovery server.

The actual work is done by `asyncua.Server.register_to_discovery()`: it registers and
keeps the registration fresh with its own loop (a fresh channel on each pass, so it
survives an LDS restart). This module is only the wrapper around it:

1. **The advertised address must be correct.** `register_to_discovery()` enters
   `server.endpoint.geturl()` as DiscoveryUrl. If that contains `0.0.0.0` -- the
   obvious value, so that the server is reachable on every interface -- the
   aggregation server takes the address literally and connects to nothing. The fix is
   `Server.socket_address`: the endpoint names the LAN IPv4, while listening still
   happens on `0.0.0.0`. `advertised_endpoint()` builds the URL, `server.py` sets both.
   As a safety net against regressions, `register()` refuses the registration if the
   endpoint still contains `0.0.0.0`.
2. **An unreachable LDS must not prevent startup.** `register_to_discovery()` then
   raises; here it stays a warning in the log. A server reachable by URL is worth more
   than none -- the same line as for `mdns`.
3. **`Server.stop()` does not unregister.** It only cancels the renewal loop and
   disconnects; the entry would remain in the LDS until it expires, and the aggregation
   server would show a module it can no longer reach. Hence
   `unregister_from_discovery()` in the `finally`.

**On by default** (again since 21.10.2026; from 23.09. to 21.10.2026 it was off): without
`OPCUA_LDS_URL` the server registers at `CELL_LDS_URL`. `OPCUA_LDS_URL=""` disables the
registration, another URL redirects it.

Without `MdnsDiscoveryConfiguration`, i.e. via a plain `RegisterServer` -- just as
Conveyor, CardDispenser and the robots do it. `caps=DA` is in any case contained in our
own mDNS announcement. (`register_to_discovery()` only sends the configuration if it is
passed to it.)
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

#: Registration is **on** by default (again since 21.10.2026; from 23.09. to 21.10.2026
#: it was off and the frontend could not find the Pis).
#: `OPCUA_LDS_URL=""` disables it.
DEFAULT_LDS_URL = CELL_LDS_URL

#: Renewal is necessary, not optional -- measured on 2026-09-21: the LDS restarted at
#: 15:56, and Conveyor (continuously since 08.09.) as well as CardDispenser (since 07.09.)
#: were in the registration inventory again afterwards, without having restarted
#: themselves. A one-time registration at our own start lay in the old LDS process and
#: would have been gone.
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
