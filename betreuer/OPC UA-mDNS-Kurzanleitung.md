# OPC UA -Server automatisch in der Zelle anmelden

> **Korrigierte Fassung vom 21.09.2026.** Die ursprüngliche Anleitung sagte, der
> Aggregation-Server durchsuche mDNS selbst und `RegisterServer2` sei nicht
> nötig. Das stimmt nicht und hat uns einen Nachmittag gekostet. **mDNS allein
> reicht nicht** — ohne Anmeldung beim Discovery-Server taucht ein Modul im
> Aggregation-Server nie auf. Was genau gemessen wurde, steht unten unter
> „Wo der Fehler lag". Der mDNS-Teil der alten Anleitung war richtig und ist
> unverändert geblieben; er kommt nur nicht mehr allein.

Ein Modul braucht **zwei** Bekanntmachungen. Sie haben verschiedene Adressaten
und ersetzen einander nicht:

| | Wer hört zu | Wozu |
| --- | --- | --- |
| **mDNS** (`_opcua-tcp._tcp.local.`) | Clients im selben Subnetz, z. B. das Frontend | Modul ohne hartcodierte IP finden |
| **Anmeldung am Discovery-Server** (`RegisterServer`) | der Aggregation-Server der Zelle | Modul erscheint unter `Objects` im Aggregation-Server |

Adressen der Zelle:

| | |
| --- | --- |
| Discovery-Server (LDS) | `opc.tcp://10.10.38.27:4840/` |
| Aggregation-Server | `opc.tcp://10.10.38.27:48400/` |

---

## 1. Pakete installieren

```bash
python -m pip install asyncua zeroconf
```

## 2. Als `server.py` speichern und die Werte oben anpassen

```python
import asyncio

from asyncua import Server
from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

IP = "192.168.1.42"   # Eigene LAN-/WLAN-IPv4; nicht localhost/0.0.0.0
PORT = 4840
PATH = "/"            # Bei opc.tcp://IP:4840/UA/Server: "/UA/Server"
NAME = "gruppe-01"    # Eindeutig im Netz; Buchstaben, Zahlen, Bindestriche

# Unter dieser Uri fuehrt der Aggregation-Server das Modul -- nicht unter dem
# mDNS-Namen. Muss eindeutig sein; der Standardwert von asyncua
# ("urn:freeopcua:python:server") ist bei allen Gruppen derselbe.
APP_URI = "urn:meine-gruppe:gruppe-01"

LDS = "opc.tcp://10.10.38.27:4840/"   # Discovery-Server der Zelle


async def main():
    server = Server()
    await server.init()

    # Angekuendigt wird die echte IP: register_to_discovery() gibt genau
    # diesen Endpoint als DiscoveryUrl an den Discovery-Server weiter, und
    # mit 0.0.0.0 verbindet der Aggregation-Server spaeter ins Leere.
    server.set_endpoint(f"opc.tcp://{IP}:{PORT}{PATH}")
    # Gelauscht wird trotzdem auf allen Schnittstellen -- sonst ist der Server
    # auf dem eigenen Rechner nicht mehr ueber 127.0.0.1 erreichbar.
    server.socket_address = ("0.0.0.0", PORT)

    server.set_server_name(NAME)
    await server.set_application_uri(APP_URI)
    # Hier bei Bedarf eigene Nodes und Serverkonfiguration ergaenzen.

    service_type = "_opcua-tcp._tcp.local."
    info = ServiceInfo(
        type_=service_type,
        name=f"{NAME}.{service_type}",
        server=f"{NAME}.local.",
        parsed_addresses=[IP],
        port=PORT,
        properties={"path": PATH, "caps": "DA"},
    )

    async with server:  # Startet den Server; stoppt ihn beim Verlassen.
        mdns = AsyncZeroconf(interfaces=[IP], ip_version=IPVersion.V4Only)
        angemeldet = False
        try:
            # 1. mDNS: fuer Clients im Subnetz.
            await (await mdns.async_register_service(info))

            # 2. Discovery-Server: nur hierueber nimmt der Aggregation-Server
            #    das Modul auf. period=60 haelt die Anmeldung frisch; das ist
            #    noetig, siehe unten.
            try:
                await server.register_to_discovery(LDS, period=60)
                angemeldet = True
            except Exception as fehler:
                # Ein Server, den man per URL erreicht, ist mehr wert als gar
                # keiner -- also weiterlaufen statt abbrechen.
                print(f"LDS-Anmeldung fehlgeschlagen: {fehler}")

            print(f"Server und mDNS aktiv: opc.tcp://{IP}:{PORT}{PATH}")
            await asyncio.Event().wait()  # Oder hier eigene Server-Schleife.
        finally:
            # Achtung: server.stop() meldet NICHT ab. Ohne das hier bleibt eine
            # tote Adresse im Discovery-Server stehen, und der
            # Aggregation-Server zeigt ein Modul, das er nicht mehr erreicht.
            if angemeldet:
                try:
                    await server.unregister_from_discovery(LDS)
                except Exception:
                    pass
            await mdns.async_close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

## 3. Starten

```bash
python server.py
```

## 4. Prüfen, ob es geklappt hat

Der Reihe nach, damit man sieht, **wo** es hakt:

```bash
# a) Sieht das Netz die mDNS-Ankuendigung?
avahi-browse -rt _opcua-tcp._tcp

# b) Kennt der Discovery-Server uns? Die eigene ApplicationUri muss dabei sein.
python -c "
import asyncio; from asyncua import Client
async def m():
    c = Client('opc.tcp://10.10.38.27:4840/')
    for s in await c.connect_and_find_servers(): print(s.ApplicationUri)
asyncio.run(m())"

# c) Fuehrt der Aggregation-Server uns? Ein Objekt mit der eigenen
#    ApplicationUri muss unter Objects liegen.
python -c "
import asyncio; from asyncua import Client
async def m():
    async with Client('opc.tcp://10.10.38.27:48400/') as c:
        for n in await c.nodes.objects.get_children():
            print((await n.read_browse_name()).Name)
asyncio.run(m())"
```

Nach der Anmeldung dauert es rund **30 Sekunden**, bis das Modul im
Aggregation-Server steht (gemessen: 31 s).

---

## Wo der Fehler lag

Der Aggregation-Server **durchsucht mDNS nicht**. Auf demselben Rechner
(`10.10.38.27`) laufen zwei Dienste, und das wird leicht übersehen:

- Port **48400** — der Aggregation-Server
- Port **4840** — ein open62541 **Local Discovery Server (LDS)**

Der Aggregation-Server führt genau die Module, die im LDS angemeldet sind. Der
LDS wiederum füllt sich **nur** über `RegisterServer`/`RegisterServer2`. Eine
mDNS-Ankündigung trägt sich dort nicht ein.

Woran man das erkennt, ohne zu raten: `FindServersOnNetwork` am LDS listet
Namen wie `Festo Conveyor OPC UA Server-10-10-38-41`. Diese Namen stehen
**nirgends auf dem Draht** — ein `avahi-browse` findet sie nicht. Das ist also
sein Anmeldebestand, nicht sein mDNS-Empfang.

Dass es nicht am Scan-Zeitpunkt liegt, zeigte der Zufall: Der
Aggregation-Server startete während der Messung neu und zog beim frischen Scan
alle fünf angemeldeten Module. Zwei Server, die zu dem Zeitpunkt seit zehn
Minuten liefen und per mDNS funkten, blieben außen vor. Nach einer einzigen
`RegisterServer2`-Anfrage standen sie 31 Sekunden später drin.

### Drei Fallen, die Zeit kosten

1. **Ein Endpoint auf `0.0.0.0` meldet `0.0.0.0` an.**
   `register_to_discovery()` trägt `server.endpoint.geturl()` als DiscoveryUrl
   ein. Wer den Endpoint auf `0.0.0.0` setzt — naheliegend, damit der Server
   über jede Schnittstelle erreichbar ist —, meldet dem Aggregation-Server die
   Adresse `0.0.0.0`. Der übernimmt sie wörtlich und verbindet ins Leere.

   Beides gleichzeitig geht mit **`Server.socket_address`**: Der Endpoint nennt
   die echte LAN-IP, die Bindeadresse bleibt `0.0.0.0`. Genau dafür ist das
   Attribut da („used when the IP address of the network interface is different
   from the endpoint IP offered to the client during discovery"). Im Beispiel
   oben sind es die zwei Zeilen `set_endpoint(...)` und `socket_address = ...`.
   Verifiziert: darüber ist der Server sowohl über `127.0.0.1` als auch über
   die LAN-IP erreichbar, und angemeldet wird die LAN-IP.

2. **Ohne eigene ApplicationUri heißen alle gleich.** asyncua meldet sonst
   `urn:freeopcua:python:server`. Der Aggregation-Server führt Module unter
   dieser Uri — zwei Gruppen mit dem Standardwert überschreiben sich
   gegenseitig. `await server.set_application_uri(...)` **vor** dem Aufbau des
   Adressraums aufrufen.

3. **Einmal anmelden genügt nicht.** Das klingt nach Vorsicht, ist aber
   gemessen: Der LDS wurde am 21.09.2026 um 15:56 neu gestartet. Conveyor läuft
   seit dem 08.09. und CardDispenser seit dem 07.09. durch — **ohne** eigenen
   Neustart —, und beide standen danach wieder im Anmeldebestand. Wer sich nur
   einmal beim eigenen Start anmeldet, ist nach jedem LDS-Neustart still weg,
   bis er selbst neu startet.

   In fremdem Code sieht man die Erneuerung nur nicht:
   `register_to_discovery()` startet die Schleife selbst, Standardabstand 60 s
   (`period`). Bei jedem Durchlauf baut sie einen frischen Kanal auf, übersteht
   also auch einen Neustart des Discovery-Servers.

   Was sie **nicht** tut: abmelden. `server.stop()` bricht nur die Schleife ab.
   Ohne `unregister_from_discovery()` im `finally` bleibt eine tote Adresse
   stehen.

   Wie lange ein Eintrag ohne Erneuerung überlebt, ist **nicht** gemessen;
   open62541 räumt nach einem eigenen Timeout ab. Die verbreitete Angabe
   „mindestens alle 10 Minuten" stammt aus dem asyncua-Docstring.

   Umgekehrt gilt: nach einem harten Abbruch bleibt der Eintrag bis zum Ablauf
   stehen — der Aggregation-Server zeigt das Modul dann noch, kommt aber nicht
   mehr dran.

### Kleinigkeit am Rande

Bei `register_server2` mit einer `MdnsDiscoveryConfiguration` stand im
`FindServersOnNetwork` des LDS einmal eine DiscoveryUrl der Form
`opc.tcp://10.10.38.104.local:4840/...` — ein an eine IP gehängtes `.local`,
das nicht auflöst. Eine spätere Anmeldung über denselben Weg ergab dagegen
einen sauberen Eintrag; der Effekt ist also nicht reproduzierbar und hier
nicht als Fehler behauptet. Auf den Aggregation-Server wirkt er sich ohnehin
nicht aus, der nimmt die angemeldete Url. Das einfache `register_server` oben
ist trotzdem der ruhigere Weg — es ist das, womit die vorhandenen Module der
Zelle nachweislich laufen.

### Was weiterhin gilt

- **mDNS endet an der Subnetzgrenze** (UDP-Multicast `224.0.0.251:5353`,
  TTL 1). Aus einem anderen VLAN oder dem Gast-WLAN findet man nichts. Die
  Anmeldung am Discovery-Server ist davon nicht betroffen — das ist eine
  normale TCP-Verbindung.
- **Keine feste IP nötig**, eine DHCP-Reservierung reicht. Wechselt der Lease
  im Betrieb, zeigen Ankündigung und Anmeldung bis zum Neustart ins Leere.
- **Den Pfad aus dem TXT-Eintrag `path` lesen**, nicht annehmen — die Module
  der Zelle benutzen verschiedene (`/conveyor/`, `/card-dispenser/`,
  `/raspi/server/`).
- Der Aggregation-Server hängt die ApplicationUri an jeden Namespace des Moduls
  an (z. B. `http://launch-rm.de/vision/urn:plcm:camera-server:ceiling-01`).
  Wer über ihn zugreift, löst den Namensraum über die **zusammengesetzte** Uri
  auf; direkt am Modul bleibt es beim ursprünglichen.
- Er räumt Namespaces **nicht** auf: verschwundene Module hinterlassen ihre
  Namespaces. Verlässlich ist das Objekt unter `Objects`, nicht der Namespace.

[API-Dokumentation zu AsyncZeroconf](https://python-zeroconf.readthedocs.io/en/latest/api.html#zeroconf.asyncio.AsyncZeroconf)
