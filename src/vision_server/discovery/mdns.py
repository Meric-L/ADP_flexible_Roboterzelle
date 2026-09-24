"""mDNS-Ankuendigung eines OPC-UA-Servers im lokalen Netz.

Setzt die Kurzanleitung des Betreuers um (`betreuer/OPC UA-mDNS-Kurzanleitung.md`):
Dienst-Typ `_opcua-tcp._tcp.local.`, TXT-Eintraege `path` und `caps`,
Anmeldung im laufenden Server, Abmeldung beim Beenden.

Zwei bewusste Abweichungen von der Anleitung:

1. Die Anleitung schreibt eine feste IP in den Quelltext und setzt damit auch
   den Endpoint. Hier wird die LAN-IPv4 zur Laufzeit ermittelt, und der
   Endpoint bleibt davon unberuehrt. Der Server bindet weiter auf `0.0.0.0`
   und ist damit ueber jede Schnittstelle **und** ueber 127.0.0.1 erreichbar;
   angekuendigt wird trotzdem die echte LAN-Adresse, denn `0.0.0.0` waere fuer
   einen Client wertlos. Mit DHCP-Reservierung im Router ist das stabil, ohne
   eine statisch konfigurierte Adresse auf dem Pi.
2. `zeroconf` ist optional. Fehlt das Paket oder scheitert die Anmeldung,
   laeuft der Server weiter und es steht eine Warnung im Log -- ein Server,
   den man per URL erreicht, ist mehr wert als gar keiner.

**Standardmaessig abgeschaltet** (seit 23.09.2026): ohne `OPCUA_MDNS=1` kuendigt
sich der Server nicht an. Er laeuft trotzdem und ist per URL erreichbar.
"""

import contextlib
import logging
import os
import re
import socket
from collections.abc import AsyncIterator

_log = logging.getLogger(__name__)

#: Dienst-Typ fuer OPC-UA-Server ueber TCP.
SERVICE_TYPE = "_opcua-tcp._tcp.local."

#: Ziel nur fuer die Routenwahl; es wird kein Paket gesendet (TEST-NET-1).
_ROUTE_PROBE = ("192.0.2.1", 9)

_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9-]+")


def enabled() -> bool:
    """Ob angekuendigt werden soll; standardmaessig nicht.

    `OPCUA_MDNS=1` (oder `true`/`yes`/`on`) schaltet die Ankuendigung ein.
    """
    return os.getenv("OPCUA_MDNS", "").strip().lower() in ("1", "true", "yes", "on")


def sanitize_instance_name(name: str) -> str:
    """Macht aus einem beliebigen Namen einen gueltigen mDNS-Instanznamen.

    Erlaubt sind Buchstaben, Ziffern und Bindestriche. Ein leerer Rest waere
    im Netz nicht adressierbar, deshalb faellt er auf `opcua-server` zurueck.
    """
    cleaned = _NAME_ALLOWED.sub("-", name).strip("-")
    return cleaned or "opcua-server"


def detect_lan_ipv4() -> str | None:
    """Ermittelt die LAN-IPv4 dieses Rechners.

    `OPCUA_ADVERTISE_IP` hat Vorrang -- damit laesst sich die Ankuendigung auf
    eine bestimmte Schnittstelle zwingen, etwa wenn LAN und WLAN gleichzeitig
    haengen. Sonst entscheidet die Routing-Tabelle: ein verbundener
    UDP-Socket verraet die Quelladresse, ohne dass etwas gesendet wird.
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
    """Kuendigt den Server an und meldet ihn beim Verlassen wieder ab.

    Parameters
    ----------
    name
        Instanzname, im Netz eindeutig (wird bereinigt).
    port
        Port des Endpoints.
    path
        Pfad des Endpoints, z. B. `/raspi/server/`. Ein Client baut daraus
        `opc.tcp://<ip>:<port><path>` -- steht hier der falsche Pfad, bauen
        alle Clients eine unbrauchbare URL.
    address
        Anzukuendigende IPv4; ohne Angabe wird sie ermittelt.
    caps
        Server-Faehigkeiten im TXT-Eintrag `caps`. Die Kurzanleitung nennt
        `NA`; die uebrigen Module der Zelle (CardDispenser, Conveyor) kuendigen
        sich am 2026-09-21 mit `DA` (Data Access) an. Ein Client, der danach
        filtert, wuerde uns mit `NA` uebersehen -- deshalb hier `DA`.

    Yields
    ------
    Die angekuendigte Adresse, oder `None`, wenn nicht angekuendigt wurde.
    """
    if not enabled():
        _log.info("mDNS abgeschaltet (OPCUA_MDNS nicht gesetzt); keine Ankuendigung")
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
        # An die ermittelte Schnittstelle binden: auf einem Pi mit LAN und
        # WLAN wuerde eine Ankuendigung auf der falschen Seite niemanden
        # erreichen.
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
            # Abmelden, damit kein Client eine tote Adresse aus dem Cache
            # bekommt. Greift nur, wenn der Prozess SIGTERM abfaengt.
            with contextlib.suppress(Exception):
                await mdns.async_unregister_service(info)
            with contextlib.suppress(Exception):
                await mdns.async_close()
            _log.info("mDNS-Ankuendigung zurueckgezogen")
