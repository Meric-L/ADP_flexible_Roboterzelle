"""Registrierung des OPC-UA-Servers beim Local Discovery Server der Zelle.

Ergaenzt die mDNS-Ankuendigung aus `ua_mdns`, ersetzt sie nicht.

Warum beides noetig ist (am 2026-09-21 im Labor gemessen):
Der Aggregation-Server (`opc.tcp://10.10.38.27:48400/`) sammelt seine Module
**nicht** per mDNS ein, obwohl die Kurzanleitung des Betreuers das behauptet.
Auf demselben Rechner laeuft unter `opc.tcp://10.10.38.27:4840/` ein
open62541-Discovery-Server, und der Aggregation-Server fuehrt genau die Server,
die dort per `RegisterServer2` registriert sind -- UR5e, Conveyor,
CardDispenser, Franka, EVA. In diesen Bestand kommt man nur durch einen
aktiven Aufruf; eine mDNS-Ankuendigung allein traegt sich dort nicht ein.
Gegenprobe: der Aggregation-Server startete um 15:56 neu und nahm beim frischen
Scan alle fuenf registrierten Module auf -- unsere beiden Pis, die zu dem
Zeitpunkt seit zehn Minuten liefen und funkten, blieben aussen vor.

Die mDNS-Ankuendigung bleibt trotzdem: Clients im Subnetz (unter anderem das
WSC-Frontend) finden uns darueber ohne Umweg ueber den Discovery-Server.

Zwei Entscheidungen, die von `Server.register_to_discovery()` abweichen:

1. `asyncua` traegt als DiscoveryUrl `server.endpoint.geturl()` ein, und das ist
   hier `opc.tcp://0.0.0.0:4840/raspi/server/`. Ein Aggregation-Server kann mit
   `0.0.0.0` nichts anfangen -- er wuerde die Adresse uebernehmen und ins Leere
   verbinden. Deshalb wird dieselbe LAN-IPv4 eingetragen, die auch die
   mDNS-Ankuendigung nennt (`ua_mdns.detect_lan_ipv4`).
2. Scheitert die Registrierung, laeuft der Server weiter und es steht eine
   Warnung im Log. Ein Server, den man per URL erreicht, ist mehr wert als gar
   keiner -- dieselbe Linie wie bei `ua_mdns`.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Sequence

from asyncua import Client, ua

import ua_mdns

_log = logging.getLogger(__name__)

#: Discovery-Server der Zelle. `OPCUA_LDS_URL=""` schaltet die Registrierung ab.
DEFAULT_LDS_URL = "opc.tcp://10.10.38.27:4840/"

#: Teil 12 der Spezifikation verlangt eine Erneuerung mindestens alle 10
#: Minuten. 60 s laesst Raum fuer ausgefallene Versuche, ohne den LDS zu fluten.
DEFAULT_RENEW_SECONDS = 60

#: ProductUri der uebrigen Module der Zelle -- alle laufen auf asyncua.
PRODUCT_URI = "urn:freeopcua.github.io:python:server"


def lds_url() -> str:
    """URL des Discovery-Servers; leer bedeutet: nicht registrieren."""
    return os.getenv("OPCUA_LDS_URL", DEFAULT_LDS_URL).strip()


def _registered_server(
    application_uri: str,
    server_name: str,
    discovery_url: str,
    is_online: bool,
) -> ua.RegisteredServer:
    """Baut den `RegisteredServer`-Datensatz fuer RegisterServer2.

    `ClientAndServer` und die freeopcua-ProductUri entsprechen dem, womit
    Conveyor, CardDispenser und die Roboter im LDS stehen.
    """
    server = ua.RegisteredServer()
    server.ServerUri = application_uri
    server.ProductUri = PRODUCT_URI
    server.ServerNames = [ua.LocalizedText(server_name)]
    server.ServerType = ua.ApplicationType.ClientAndServer
    server.DiscoveryUrls = [discovery_url]
    server.IsOnline = is_online
    return server


async def _send(
    url: str,
    registered: ua.RegisteredServer,
    mdns_name: str,
    caps: Sequence[str],
) -> None:
    """Schickt eine RegisterServer2-Anfrage an den Discovery-Server.

    Registrierung ist ein sessionloser Dienst -- es wird nur ein sicherer Kanal
    aufgebaut, keine Session. Aeltere Discovery-Server kennen `RegisterServer2`
    nicht; dann greift der Rueckfall auf `RegisterServer`, der ohne die
    mDNS-Angaben auskommt.
    """
    client = Client(url=url, timeout=10)
    await client.connect_sessionless()
    try:
        params = ua.RegisterServer2Parameters()
        params.Server = registered
        params.DiscoveryConfiguration = [
            ua.MdnsDiscoveryConfiguration(
                MdnsServerName=mdns_name,
                ServerCapabilities=list(caps),
            )
        ]
        try:
            await client.uaclient.register_server2(params)
        except ua.UaStatusCodeError as exc:
            _log.info(
                "RegisterServer2 abgelehnt (%s); fallback auf RegisterServer", exc
            )
            await client.uaclient.register_server(registered)
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect_sessionless()


@contextlib.asynccontextmanager
async def register(
    application_uri: str,
    server_name: str,
    port: int,
    path: str,
    mdns_name: str,
    address: str | None = None,
    url: str | None = None,
    caps: Sequence[str] = ("DA",),
    renew_seconds: int = DEFAULT_RENEW_SECONDS,
) -> AsyncIterator[str | None]:
    """Registriert den Server beim LDS und meldet ihn beim Verlassen ab.

    Parameters
    ----------
    application_uri
        Die ApplicationUri dieses Servers. Unter diesem Namen fuehrt der
        Aggregation-Server das Modul -- nicht unter dem mDNS-Namen.
    server_name
        Klartextname, wie ihn `Server.set_server_name()` gesetzt hat.
    port, path
        Port und Pfad des Endpoints; daraus entsteht mit der LAN-IPv4 die
        DiscoveryUrl, die der Aggregation-Server spaeter anwaehlt.
    mdns_name
        Name, unter dem der Discovery-Server uns seinerseits per mDNS fuehrt.
    address
        Anzukuendigende IPv4; ohne Angabe wird sie ermittelt.
    url
        Discovery-Server; ohne Angabe entscheidet `lds_url()`.
    caps
        Server-Faehigkeiten, wie im mDNS-TXT-Eintrag `caps`.
    renew_seconds
        Abstand der Erneuerungen; `0` registriert einmalig.

    Yields
    ------
    Die registrierte DiscoveryUrl, oder `None`, wenn nicht registriert wurde.
    """
    target = url if url is not None else lds_url()
    if not target:
        _log.info("Kein Discovery-Server konfiguriert; keine Registrierung")
        yield None
        return

    ip = address or ua_mdns.detect_lan_ipv4()
    if ip is None:
        _log.warning("Keine LAN-IPv4 ermittelbar; LDS-Registrierung uebersprungen")
        yield None
        return

    discovery_url = f"opc.tcp://{ip}:{port}{path}"
    online = _registered_server(application_uri, server_name, discovery_url, True)

    try:
        await _send(target, online, mdns_name, caps)
    except Exception:
        _log.exception(
            "LDS-Registrierung bei %s fehlgeschlagen; Server laeuft ohne sie weiter",
            target,
        )
        yield None
        return

    _log.info("Beim Discovery-Server %s registriert: %s", target, discovery_url)

    async def renew() -> None:
        # Laeuft die Registrierung ab, verschwindet das Modul aus dem
        # Aggregation-Server -- ein einzelner Fehlversuch darf die Schleife
        # deshalb nicht beenden.
        while True:
            await asyncio.sleep(renew_seconds)
            try:
                await _send(target, online, mdns_name, caps)
                _log.debug("LDS-Registrierung erneuert")
            except Exception as exc:
                _log.warning("LDS-Erneuerung fehlgeschlagen: %s", exc)

    task = asyncio.create_task(renew()) if renew_seconds else None
    try:
        yield discovery_url
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # Abmelden, damit der Aggregation-Server nicht auf eine tote Adresse
        # verbindet. Greift nur, wenn der Prozess SIGTERM abfaengt.
        offline = _registered_server(application_uri, server_name, discovery_url, False)
        try:
            await _send(target, offline, mdns_name, caps)
            _log.info("Beim Discovery-Server abgemeldet")
        except Exception as exc:
            _log.warning("LDS-Abmeldung fehlgeschlagen: %s", exc)
