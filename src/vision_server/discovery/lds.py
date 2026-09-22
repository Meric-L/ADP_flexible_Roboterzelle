"""Anmeldung des OPC-UA-Servers beim Local Discovery Server der Zelle.

Ergaenzt die mDNS-Ankuendigung aus `mdns`, ersetzt sie nicht.

Warum beides noetig ist (am 2026-09-21 im Labor gemessen):
Der Aggregation-Server (`opc.tcp://10.10.38.27:48400/`) sammelt seine Module
**nicht** per mDNS ein, obwohl die Kurzanleitung des Betreuers das behauptete.
Auf demselben Rechner laeuft unter `opc.tcp://10.10.38.27:4840/` ein
open62541-Discovery-Server, und der Aggregation-Server fuehrt genau die Server,
die dort angemeldet sind. In diesen Bestand kommt man nur durch einen aktiven
Aufruf; eine mDNS-Ankuendigung allein traegt sich dort nicht ein.
Gegenprobe: der Aggregation-Server startete um 15:56 neu und nahm beim frischen
Scan alle fuenf angemeldeten Module auf -- unsere beiden Pis, die zu dem
Zeitpunkt seit zehn Minuten liefen und funkten, blieben aussen vor.

Die mDNS-Ankuendigung bleibt trotzdem: Clients im Subnetz (unter anderem das
WSC-Frontend) finden uns darueber ohne Umweg ueber den Discovery-Server.

Die eigentliche Arbeit macht `asyncua.Server.register_to_discovery()`: sie
meldet an und haelt die Anmeldung mit einer eigenen Schleife frisch (bei jedem
Durchlauf ein frischer Kanal, ueberlebt also einen LDS-Neustart). Dieses Modul
ist nur die Klammer darum:

1. **Die angekuendigte Adresse muss stimmen.** `register_to_discovery()` traegt
   `server.endpoint.geturl()` als DiscoveryUrl ein. Steht dort `0.0.0.0` -- der
   naheliegende Wert, damit der Server ueber jede Schnittstelle erreichbar ist
   --, uebernimmt der Aggregation-Server die Adresse woertlich und verbindet ins
   Leere. Loesung ist `Server.socket_address`: der Endpoint nennt die LAN-IPv4,
   gelauscht wird trotzdem auf `0.0.0.0`. `advertised_endpoint()` baut die URL,
   `server.py` setzt beides. Als Netz gegen Rueckfaelle verweigert `register()`
   die Anmeldung, wenn im Endpoint doch `0.0.0.0` steht.
2. **Ein nicht erreichbarer LDS darf den Start nicht verhindern.**
   `register_to_discovery()` wirft dann; hier bleibt es bei einer Warnung im
   Log. Ein Server, den man per URL erreicht, ist mehr wert als gar keiner --
   dieselbe Linie wie bei `mdns`.
3. **`Server.stop()` meldet nicht ab.** Es bricht nur die Erneuerungsschleife ab
   und trennt die Verbindung; der Eintrag bliebe bis zum Ablauf im LDS stehen,
   und der Aggregation-Server zeigte ein Modul, das er nicht mehr erreicht.
   Deshalb `unregister_from_discovery()` im `finally`.

Ohne `MdnsDiscoveryConfiguration`, also per schlichtem `RegisterServer` -- so
wie Conveyor, CardDispenser und die Roboter es tun. `caps=DA` steht ohnehin in
der eigenen mDNS-Ankuendigung. (`register_to_discovery()` schickt die
Konfiguration nur, wenn man sie ihr uebergibt.)
"""

import contextlib
import logging
import os
from collections.abc import AsyncIterator

from asyncua import Server

from . import mdns

_log = logging.getLogger(__name__)

#: Discovery-Server der Zelle. `OPCUA_LDS_URL=""` schaltet die Anmeldung ab.
DEFAULT_LDS_URL = "opc.tcp://10.10.38.27:4840/"

#: Erneuerung ist noetig, nicht optional -- gemessen am 2026-09-21: der LDS
#: startete um 15:56 neu, und Conveyor (seit 08.09. durchgehend) wie
#: CardDispenser (seit 07.09.) standen danach wieder im Anmeldebestand, ohne
#: selbst neu gestartet zu haben. Eine einmalige Anmeldung beim eigenen Start
#: lag im alten LDS-Prozess und waere weg gewesen.
#: 60 s ist auch der Standardwert von `register_to_discovery()`; wie lange ein
#: Eintrag ohne Erneuerung tatsaechlich ueberlebt, ist nicht gemessen.
DEFAULT_RENEW_SECONDS = 60


def lds_url() -> str:
    """URL des Discovery-Servers; leer bedeutet: nicht anmelden."""
    return os.getenv("OPCUA_LDS_URL", DEFAULT_LDS_URL).strip()


def advertised_endpoint(port: int, path: str, address: str | None = None) -> str | None:
    """Endpoint-URL, die der Server nach aussen nennt.

    Traegt die LAN-IPv4 statt `0.0.0.0`, damit die daraus gebildete
    DiscoveryUrl fuer den Aggregation-Server brauchbar ist. Gelauscht wird
    davon unabhaengig auf allen Schnittstellen -- siehe `Server.socket_address`
    in `server.py`.

    Returns
    -------
    Die URL, oder `None`, wenn keine LAN-IPv4 zu ermitteln war. Dann bleibt es
    beim bisherigen Endpoint, und `register()` meldet sich nicht an.
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
    """Meldet den Server beim LDS an und beim Verlassen wieder ab.

    Parameters
    ----------
    server
        Der bereits konfigurierte Server. Angemeldet werden seine
        ApplicationUri, sein Name und `server.endpoint` als DiscoveryUrl --
        der Endpoint muss also die LAN-IPv4 nennen, nicht `0.0.0.0`.
    url
        Discovery-Server; ohne Angabe entscheidet `lds_url()`.
    renew_seconds
        Abstand der Erneuerungen; `0` meldet einmalig an.

    Yields
    ------
    Die angemeldete DiscoveryUrl, oder `None`, wenn nicht angemeldet wurde.
    Wirft nichts -- Fehler landen im Log.
    """
    target = url if url is not None else lds_url()
    if not target:
        _log.info("Kein Discovery-Server konfiguriert; keine Anmeldung")
        yield None
        return

    advertised = server.endpoint.geturl()
    if server.endpoint.hostname in (None, "0.0.0.0", "::"):
        # Waere fuer den Aggregation-Server wertlos: er uebernimmt die Adresse
        # und verbindet ins Leere. Lieber gar nicht anmelden.
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
        # Abmelden, damit der Aggregation-Server nicht auf eine tote Adresse
        # verbindet. Greift nur, wenn der Prozess SIGTERM abfaengt.
        try:
            await server.unregister_from_discovery(target)
            _log.info("Beim Discovery-Server abgemeldet")
        except Exception as exc:
            _log.warning("LDS-Abmeldung fehlgeschlagen: %s", exc)
