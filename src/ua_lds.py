"""Registrierung des OPC-UA-Servers beim Local Discovery Server der Zelle.

Ergaenzt die mDNS-Ankuendigung aus `ua_mdns`, ersetzt sie nicht.

Warum beides noetig ist (am 2026-09-21 im Labor gemessen):
Der Aggregation-Server (`opc.tcp://10.10.38.27:48400/`) sammelt seine Module
**nicht** per mDNS ein, obwohl die Kurzanleitung des Betreuers das behauptete.
Auf demselben Rechner laeuft unter `opc.tcp://10.10.38.27:4840/` ein
open62541-Discovery-Server, und der Aggregation-Server fuehrt genau die Server,
die dort angemeldet sind -- UR5e, Conveyor, CardDispenser, Franka, EVA. In
diesen Bestand kommt man nur durch einen aktiven Aufruf; eine mDNS-Ankuendigung
allein traegt sich dort nicht ein.
Gegenprobe: der Aggregation-Server startete um 15:56 neu und nahm beim frischen
Scan alle fuenf angemeldeten Module auf -- unsere beiden Pis, die zu dem
Zeitpunkt seit zehn Minuten liefen und funkten, blieben aussen vor.

Die mDNS-Ankuendigung bleibt trotzdem: Clients im Subnetz (unter anderem das
WSC-Frontend) finden uns darueber ohne Umweg ueber den Discovery-Server.

Drei Entscheidungen, die von `Server.register_to_discovery()` abweichen:

1. `asyncua` traegt als DiscoveryUrl `server.endpoint.geturl()` ein, und das ist
   hier `opc.tcp://0.0.0.0:4840/raspi/server/`. Ein Aggregation-Server kann mit
   `0.0.0.0` nichts anfangen -- er wuerde die Adresse uebernehmen und ins Leere
   verbinden. Deshalb wird dieselbe LAN-IPv4 eingetragen, die auch die
   mDNS-Ankuendigung nennt (`ua_mdns.detect_lan_ipv4`).
2. Angemeldet wird mit dem schlichten `RegisterServer`, **ohne**
   `MdnsDiscoveryConfiguration` -- so, wie Conveyor, CardDispenser und die
   Roboter es nachweislich tun. Anlass war eine Beobachtung am 2026-09-21: mit
   dieser Konfiguration stand im `FindServersOnNetwork` des LDS einmal
   `opc.tcp://10.10.38.104.local:4840/raspi/server` -- ein an eine IP
   gehaengtes `.local`, das nicht aufloest. Eine spaetere Anmeldung desselben
   Codewegs ergab dagegen einen sauberen Eintrag, der Effekt ist also **nicht
   reproduzierbar** und hier nicht als Fehler behauptet. Den Aggregation-Server
   betrifft er ohnehin nicht, der nimmt die angemeldete Url. Wir richten uns
   trotzdem nach den Nachbarmodulen: eine Variable weniger, und unsere
   Faehigkeiten (`caps=DA`) stehen ohnehin in der eigenen mDNS-Ankuendigung.
3. Scheitert die Registrierung, laeuft der Server weiter und es steht eine
   Warnung im Log. Ein Server, den man per URL erreicht, ist mehr wert als gar
   keiner -- dieselbe Linie wie bei `ua_mdns`.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator

from asyncua import Client, ua

import ua_mdns

_log = logging.getLogger(__name__)

#: Discovery-Server der Zelle. `OPCUA_LDS_URL=""` schaltet die Registrierung ab.
DEFAULT_LDS_URL = "opc.tcp://10.10.38.27:4840/"

#: Erneuerung ist noetig, nicht optional -- gemessen am 2026-09-21: der LDS
#: startete um 15:56 neu, und Conveyor (seit 08.09. durchgehend) wie
#: CardDispenser (seit 07.09.) standen danach wieder im Anmeldebestand, ohne
#: selbst neu gestartet zu haben. Eine einmalige Anmeldung beim eigenen Start
#: lag im alten LDS-Prozess und waere weg gewesen.
#: 60 s ist der Standardabstand von `asyncua.Server.register_to_discovery()`;
#: wie lange ein Eintrag ohne Erneuerung tatsaechlich ueberlebt, ist nicht
#: gemessen.
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
    """Baut den `RegisteredServer`-Datensatz fuer die Anmeldung.

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


async def _send(url: str, registered: ua.RegisteredServer) -> None:
    """Schickt eine RegisterServer-Anfrage an den Discovery-Server.

    Anmeldung ist ein sessionloser Dienst -- es wird nur ein sicherer Kanal
    aufgebaut, keine Session. Zur bewussten Wahl von `RegisterServer` statt
    `RegisterServer2` siehe den Modulkopf.
    """
    client = Client(url=url, timeout=10)
    await client.connect_sessionless()
    try:
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
    address: str | None = None,
    url: str | None = None,
    renew_seconds: float = DEFAULT_RENEW_SECONDS,
) -> AsyncIterator[str | None]:
    """Meldet den Server beim LDS an und beim Verlassen wieder ab.

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
    address
        Anzukuendigende IPv4; ohne Angabe wird sie ermittelt.
    url
        Discovery-Server; ohne Angabe entscheidet `lds_url()`.
    renew_seconds
        Abstand der Erneuerungen; `0` meldet einmalig an.

    Yields
    ------
    Die angemeldete DiscoveryUrl, oder `None`, wenn nicht angemeldet wurde.
    """
    target = url if url is not None else lds_url()
    if not target:
        _log.info("Kein Discovery-Server konfiguriert; keine Anmeldung")
        yield None
        return

    ip = address or ua_mdns.detect_lan_ipv4()
    if ip is None:
        _log.warning("Keine LAN-IPv4 ermittelbar; LDS-Anmeldung uebersprungen")
        yield None
        return

    discovery_url = f"opc.tcp://{ip}:{port}{path}"
    online = _registered_server(application_uri, server_name, discovery_url, True)

    try:
        await _send(target, online)
    except Exception:
        _log.exception(
            "LDS-Anmeldung bei %s fehlgeschlagen; Server laeuft ohne sie weiter",
            target,
        )
        yield None
        return

    _log.info("Beim Discovery-Server %s angemeldet: %s", target, discovery_url)

    async def renew() -> None:
        # Laeuft die Anmeldung ab, verschwindet das Modul aus dem
        # Aggregation-Server -- ein einzelner Fehlversuch darf die Schleife
        # deshalb nicht beenden.
        while True:
            await asyncio.sleep(renew_seconds)
            try:
                await _send(target, online)
                _log.debug("LDS-Anmeldung erneuert")
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
            await _send(target, offline)
            _log.info("Beim Discovery-Server abgemeldet")
        except Exception as exc:
            _log.warning("LDS-Abmeldung fehlgeschlagen: %s", exc)
