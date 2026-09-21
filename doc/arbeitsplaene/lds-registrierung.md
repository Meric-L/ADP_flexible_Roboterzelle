# Registrierung beim Local Discovery Server der Zelle

**Status:** fertig — 21.09.2026
**Verantwortlich:** `Agent: LDS-Registrierung, damit der Aggregation-Server die Vision-Server aufnimmt`
**Thema:** mdns
**Branch:** worktree-lds-register-server2

## Ziel

Die beiden Vision-Server erscheinen im Aggregation-Server der Zelle
(`opc.tcp://10.10.38.27:48400/`) unter ihrer ApplicationUri, ohne dass jemand
sie dort von Hand einträgt. Vorher ging das nicht: die reine mDNS-Ankündigung
aus `src/ua_mdns.py` reicht dafür nicht aus.

## Bereits gelesen

- `doc/part10-programm-schnittstelle.md` (Abschnitt „Der Aggregation-Server
  findet uns von selbst" — inhaltlich widerlegt, siehe unten)
- `doc/vision-server-interface.md` (Endpoint, ApplicationUri)
- `doc/vision-system.md` (Ist-Stand auf dem Pi, Port 4840)
- `doc/altlasten.md` (B1, hartcodierte Pi-Adressen)
- `betreuer/OPC UA-mDNS-Kurzanleitung.md`

## Messung, die den Plan ausgelöst hat

Am 21.09.2026 im Labor gemessen, von `10.10.38.110` aus (gleiches Subnetz):

- Beide Pis waren per mDNS sauber sichtbar
  (`vision-ceiling-01` → `10.10.38.104:4840`, `vision-flange-01` →
  `10.10.38.109:4840`, TXT `path=/raspi/server/`, `caps=DA`), OPC UA direkt
  erreichbar, ApplicationUri korrekt.
- Der Aggregation-Server führte sie trotzdem nicht.
- Auf demselben Rechner läuft unter `opc.tcp://10.10.38.27:4840/` ein
  **open62541 Local Discovery Server**. Dessen `FindServers` liefert genau die
  Module, die der Aggregation-Server unter `Objects` führt.
- Der Aggregation-Server startete um 15:56 neu und nahm beim frischen Scan
  alle fünf dort registrierten Module auf. Unsere Pis liefen zu dem Zeitpunkt
  seit zehn Minuten und funkten — sie blieben aussen vor.
- Die Namen im LDS-Bestand (`Festo Conveyor OPC UA Server-10-10-38-41`,
  `OJIES-Aggregation-LDS-reuther`) stehen in 15 s Avahi-Suche **nirgends auf
  dem Draht**. Der LDS liest also nicht mDNS, das ist sein eigener
  Registrierungsbestand.
- Alle funktionierenden Module haben `ProductUri:
  urn:freeopcua.github.io:python:server` und `ServerType: ClientAndServer` —
  es sind asyncua-Server, die sich aktiv registrieren.

**Schlussfolgerung:** Der Weg in den Aggregation-Server führt über eine aktive
Anmeldung am LDS (`RegisterServer`), nicht über mDNS. Die Aussage in
`betreuer/OPC UA-mDNS-Kurzanleitung.md` und in
`doc/part10-programm-schnittstelle.md`, eine Anmeldung sei nicht nötig,
trifft nicht zu.

## Betroffene Dateien

- `src/ua_lds.py` (neu)
- `src/OPCUA/server.py` (Einbau in die Server-Schleife)
- `tests/test_ua_lds.py` (neu)
- `doc/part10-programm-schnittstelle.md` (Korrektur des Discovery-Abschnitts)
- `doc/vision-system.md` (Betriebsangaben)
- `doc/altlasten.md` (Eintrag zur falschen Betreuer-Angabe)

## Schnittstellen

### Netz / Betrieb

| Element | Wert |
| --- | --- |
| Discovery-Server (LDS) | `opc.tcp://10.10.38.27:4840/` |
| Aggregation-Server | `opc.tcp://10.10.38.27:48400/` |
| Dienst | `Server.register_to_discovery()` → `RegisterServer`, ohne `MdnsDiscoveryConfiguration` |
| Verbindung | sessionlos (`connect_sessionless`), `NoSecurity` |
| Erneuerung | alle 60 s — notwendig, nicht optional (gemessen, siehe unten) |
| Registrierte DiscoveryUrl | `opc.tcp://<LAN-IPv4>:4840/raspi/server/` |
| Abschaltung | `OPCUA_LDS_URL=""` |
| Abweichender LDS | `OPCUA_LDS_URL=opc.tcp://host:4840/` |

Die mDNS-Ankündigung aus `src/ua_mdns.py` **bleibt unverändert bestehen** —
Clients im Subnetz (u. a. das WSC-Frontend) finden uns darüber direkt, ohne
Umweg über den Discovery-Server.

### Python

```python
# Modul: src/ua_lds.py

DEFAULT_LDS_URL = "opc.tcp://10.10.38.27:4840/"
DEFAULT_RENEW_SECONDS = 60

def lds_url() -> str:
    """URL des Discovery-Servers aus `OPCUA_LDS_URL`; leer = nicht anmelden."""

def advertised_endpoint(
    port: int, path: str, address: str | None = None
) -> str | None:
    """Endpoint-URL mit der LAN-IPv4, oder None, wenn keine zu ermitteln war."""

@contextlib.asynccontextmanager
async def register(
    server: Server,
    url: str | None = None,
    renew_seconds: int = DEFAULT_RENEW_SECONDS,
) -> AsyncIterator[str | None]:
    """Meldet beim LDS an, erneuert periodisch, meldet beim Verlassen ab.

    Liefert die angemeldete DiscoveryUrl, oder None, wenn nicht angemeldet
    wurde (kein LDS konfiguriert, Endpoint nennt 0.0.0.0, Anmeldung
    gescheitert). Wirft nichts — Fehler landen im Log.
    """
```

In `src/OPCUA/server.py`:

```python
endpoint = ua_lds.advertised_endpoint(MDNS_PORT, MDNS_PATH) or ENDPOINT
server.set_endpoint(endpoint)            # was angekündigt wird: LAN-IPv4
server.socket_address = ("0.0.0.0", MDNS_PORT)   # woran gelauscht wird
```

Die Anmeldung selbst macht `asyncua.Server.register_to_discovery()`; `ua_lds`
ist nur die Klammer darum. Der ursprüngliche Entwurf baute den
`RegisteredServer` von Hand, weil die Methode `server.endpoint.geturl()`
einträgt und dort `0.0.0.0` stand. Das war unnötig: `Server.socket_address`
trennt Bindeadresse und angekündigte Adresse und ist genau dafür gedacht.
Was die Klammer noch leistet: Anmeldung verweigern, wenn im Endpoint doch
`0.0.0.0` steht; einen nicht erreichbaren LDS nicht den Serverstart kosten
lassen; und im `finally` abmelden, weil `Server.stop()` das nicht tut.

### Registrierungsdatensatz

| Feld | Wert |
| --- | --- |
| `ServerUri` | ApplicationUri des Moduls, z. B. `urn:plcm:camera-server:ceiling-01` |
| `ProductUri` | `urn:freeopcua.github.io:python:server` |
| `ServerNames` | `Raspberry Pi OPC UA Server` |
| `ServerType` | `ClientAndServer` |
| `DiscoveryUrls` | `opc.tcp://<LAN-IPv4>:4840/raspi/server/` |
| `IsOnline` | `True`; beim Beenden `False` (Abmeldung) |
| `DiscoveryConfiguration` | keine — `register_to_discovery()` ohne `discovery_configuration` schickt `RegisterServer`, wie bei den Nachbarmodulen |

Im Aggregation-Server erscheint das Modul danach als Objekt unter `Objects` in
dessen `ns=1`, benannt nach der ApplicationUri — erwartet also
`urn:plcm:camera-server:ceiling-01` und `urn:plcm:camera-server:roboter-hand-01`.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| LDS nicht erreichbar | Warnung im Log, Server läuft weiter, mDNS bleibt aktiv |
| Keine LAN-IPv4 ermittelbar | keine Registrierung, Warnung im Log |
| Erneuerung scheitert einmalig | Warnung, Schleife läuft weiter |
| `OPCUA_LDS_URL=""` | Registrierung abgeschaltet, Hinweis im Log |
| Harter Abbruch (kein SIGTERM) | keine Abmeldung; Eintrag verfällt am LDS |

## Vorgehen

1. `src/ua_lds.py` schreiben.
2. Live gegen den laufenden Pi beweisen, dass die Registrierung den
   Aggregation-Server tatsächlich dazu bringt, das Modul aufzunehmen.
3. Einbau in `src/OPCUA/server.py`, innerhalb des bestehenden
   `ua_mdns.announce`-Blocks.
4. Tests.
5. Doku korrigieren.

## Abweichungen vom Plan

- **Erst an `Server.register_to_discovery()` vorbeigebaut, dann doch darauf
  umgestellt.** Die Methode trägt `server.endpoint.geturl()` als DiscoveryUrl
  ein, und dort stand `0.0.0.0` — der Aggregation-Server übernimmt das wörtlich
  und verbindet ins Leere. Der erste Entwurf baute den `RegisteredServer`
  deshalb von Hand, samt eigener Erneuerungsschleife und Abmeldung: rund 190
  Zeilen.

  Das war unnötig. `Server.socket_address` trennt Bindeadresse und
  angekündigte Adresse und ist genau für diesen Fall gedacht („used when the IP
  address of the network interface is different from the endpoint IP offered to
  the client during discovery"). Damit nennt der Endpoint die LAN-IPv4, und
  gelauscht wird weiter auf `0.0.0.0`. `ua_lds` ist jetzt nur noch die Klammer
  um die Bibliotheksmethode. Als Altlast D10 vermerkt, weil das Übersehen des
  Attributs Zeit gekostet hat.

  Ebenfalls geprüft und entkräftet: die Sorge, die Bibliotheksschleife käme mit
  einem LDS-Neustart nicht klar. `_renew_registration()` baut bei jedem
  Durchlauf einen frischen Kanal auf.
- **Von `RegisterServer2` auf `RegisterServer` zurückgegangen.** Der erste
  Entwurf schickte eine `MdnsDiscoveryConfiguration` mit. Danach stand im
  `FindServersOnNetwork` des LDS einmal
  `opc.tcp://10.10.38.104.local:4840/raspi/server` — ein an eine IP gehängtes
  `.local`, das nicht auflöst. **Nicht reproduzierbar:** `flange-01` meldete
  sich über denselben Codeweg an und bekam einen sauberen Eintrag. Der Effekt
  ist damit nicht als Fehler belegt, und den Aggregation-Server betrifft er
  ohnehin nicht. Trotzdem melden wir uns jetzt so an wie Conveyor,
  CardDispenser und die Roboter — eine Variable weniger. `caps=DA` steht
  ohnehin in der eigenen mDNS-Ankündigung.
- **Erneuerung ist belegt notwendig.** Ursprünglich mit „die Spezifikation
  verlangt ≤ 10 min" begründet — das stammt aber aus dem asyncua-Docstring, war
  also übernommen und nicht gemessen. Der Beleg kam anders (siehe unten).

## Ergebnis der Abnahme

Live gegen die laufende Zelle gemessen (21.09.2026, Layer 1 = `ceiling-01` auf
`10.10.38.104`):

| Zeit | Beobachtung |
| --- | --- |
| 16:18:05 | `RegisterServer2` für `urn:plcm:camera-server:ceiling-01` gesendet; steht sofort im `FindServers` des LDS |
| 16:18:36 | Aggregation-Server führt das Modul als Objekt unter `Objects`, mit allen neun Namespaces inkl. `http://launch-rm.de/vision/urn:plcm:camera-server:ceiling-01` |
| 16:18:37 | Nach dem Abmelden (`IsOnline=False`) wieder aus dem LDS verschwunden |

Aufnahme also **31 Sekunden** nach der Anmeldung.

### Im Betrieb bestätigt

Nach Ausrollen auf die Pis und Neustart des Dienstes stehen **beide** Module im
Aggregation-Server, die Anmeldung kommt aus dem Server selbst:

```
urn:plcm:camera-server:ceiling-01
urn:plcm:camera-server:roboter-hand-01
```

Für `ceiling-01` zusätzlich über 4,5 Minuten und damit mehrere
Erneuerungszyklen beobachtet: durchgehend im LDS und im Aggregation-Server,
ohne Aussetzer.

### Beleg, dass Erneuerung notwendig ist

Kam durch einen Zufall und ist die belastbarste Messung des Tages: Der LDS
startete am 21.09.2026 um 15:56 neu (`LastCounterResetTime` springt mit).
Conveyor läuft seit dem 08.09. und CardDispenser seit dem 07.09. durch — beide
**ohne** eigenen Neustart —, und beide standen danach wieder im
Anmeldebestand. Eine Anmeldung, die nur einmal beim eigenen Start gesendet
wurde, lag im alten LDS-Prozess und wäre verloren. Die Nachbarmodule erneuern
also periodisch; sichtbar ist das in deren Code nicht, weil
`asyncua.Server.register_to_discovery()` die Schleife selbst startet
(Standardabstand 60 s).

Nicht gemessen: wie lange ein Eintrag ohne Erneuerung tatsächlich überlebt.
open62541 räumt nach einem eigenen Timeout ab.

### Nach der Umstellung auf `register_to_discovery()`

Lokal mit dem echten `src/OPCUA/server.py` geprüft (LDS-Anmeldung per
`OPCUA_LDS_URL=""` abgeschaltet, die Zelle also nicht angefasst):

```
INFO:raspi-opcua:Server startet auf opc.tcp://10.10.38.110:4840/raspi/server/
INFO:asyncua.server.binary_server_asyncio:Listening on 0.0.0.0:4840
```

Erreichbar über `127.0.0.1` **und** über die LAN-IP — die Trennung von
Endpoint und Bindeadresse funktioniert also, und `print_setpoint.py` sowie der
Hello-World-Client behalten ihren Weg über Loopback.

**Noch offen:** Die Pis laufen weiter mit dem Stand davor. Erst nach
`systemctl restart opcua-server.service` kommt die Anmeldung aus
`register_to_discovery()`; bis dahin ist die Umstellung nur lokal verifiziert.

Tests: `PYTHONPATH=src python3 -m unittest discover -s tests -t .` — 241 Tests,
davon 12 in `tests/test_ua_lds.py`, alle grün.

## Offene Fragen

- Der Aggregation-Server hängt Namespaces dauerhaft an (Franka und EVA hatten
  nach ihrem Verschwinden noch Namespaces, aber kein Objekt mehr). Ob ein
  Modulwechsel dort aufräumt, ist ungeklärt — für uns unkritisch.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- Fachwissen nach `doc/part10-programm-schnittstelle.md` und
  `doc/vision-system.md` übernommen.
