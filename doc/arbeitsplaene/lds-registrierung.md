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

**Schlussfolgerung:** Der Weg in den Aggregation-Server führt über
`RegisterServer2` am LDS, nicht über mDNS. Die Aussage in
`betreuer/OPC UA-mDNS-Kurzanleitung.md` und in
`doc/part10-programm-schnittstelle.md`, `RegisterServer2` sei nicht nötig,
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
| Dienst | `RegisterServer2`, Rückfall `RegisterServer` |
| Verbindung | sessionlos (`connect_sessionless`), `NoSecurity` |
| Erneuerung | alle 60 s (Spezifikation verlangt ≤ 10 min) |
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
PRODUCT_URI = "urn:freeopcua.github.io:python:server"

def lds_url() -> str:
    """URL des Discovery-Servers aus `OPCUA_LDS_URL`; leer = nicht registrieren."""

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
    """Registriert beim LDS, erneuert periodisch, meldet beim Verlassen ab.

    Liefert die registrierte DiscoveryUrl, oder None, wenn nicht registriert
    wurde (kein LDS konfiguriert, keine LAN-IPv4, Registrierung gescheitert).
    Wirft nichts — Fehler landen im Log.
    """
```

Warum nicht `asyncua.Server.register_to_discovery()`: die Methode trägt
`server.endpoint.geturl()` als DiscoveryUrl ein, hier also
`opc.tcp://0.0.0.0:4840/raspi/server/`. Der Aggregation-Server übernimmt diese
Adresse und verbindet ins Leere. `ua_lds` trägt stattdessen dieselbe LAN-IPv4
ein, die auch die mDNS-Ankündigung nennt (`ua_mdns.detect_lan_ipv4`).

### Registrierungsdatensatz

| Feld | Wert |
| --- | --- |
| `ServerUri` | ApplicationUri des Moduls, z. B. `urn:plcm:camera-server:ceiling-01` |
| `ProductUri` | `urn:freeopcua.github.io:python:server` |
| `ServerNames` | `Raspberry Pi OPC UA Server` |
| `ServerType` | `ClientAndServer` |
| `DiscoveryUrls` | `opc.tcp://<LAN-IPv4>:4840/raspi/server/` |
| `IsOnline` | `True`; beim Beenden `False` (Abmeldung) |
| `DiscoveryConfiguration` | `MdnsDiscoveryConfiguration(MdnsServerName=<mDNS-Instanzname>, ServerCapabilities=["DA"])` |

Im Aggregation-Server erscheint das Modul danach als Objekt unter `Objects` in
dessen `ns=1`, benannt nach der ApplicationUri — erwartet also
`urn:plcm:camera-server:ceiling-01` und `urn:plcm:camera-server:roboter-hand-01`.

### Fehlerfälle

| Fall | Verhalten |
| --- | --- |
| LDS nicht erreichbar | Warnung im Log, Server läuft weiter, mDNS bleibt aktiv |
| `RegisterServer2` abgelehnt | Rückfall auf `RegisterServer` ohne mDNS-Angaben |
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

- **Nicht `Server.register_to_discovery()` benutzt.** Die Methode trägt
  `server.endpoint.geturl()` als DiscoveryUrl ein, hier also
  `opc.tcp://0.0.0.0:4840/raspi/server/`. Der Aggregation-Server übernimmt die
  Adresse wörtlich. `ua_lds` baut den Datensatz deshalb selbst. Als Altlast
  D10 vermerkt.
- **`MdnsDiscoveryConfiguration` wird als Liste übergeben.** `asyncua` setzt in
  `Client.register_server` ein einzelnes Objekt, obwohl das Feld
  `list[ua.ExtensionObject]` ist.

## Ergebnis der Abnahme

Live gegen die laufende Zelle gemessen (21.09.2026, Layer 1 = `ceiling-01` auf
`10.10.38.104`):

| Zeit | Beobachtung |
| --- | --- |
| 16:18:05 | `RegisterServer2` für `urn:plcm:camera-server:ceiling-01` gesendet; steht sofort im `FindServers` des LDS |
| 16:18:36 | Aggregation-Server führt das Modul als Objekt unter `Objects`, mit allen neun Namespaces inkl. `http://launch-rm.de/vision/urn:plcm:camera-server:ceiling-01` |
| 16:18:37 | Nach dem Abmelden (`IsOnline=False`) wieder aus dem LDS verschwunden |

Aufnahme also **31 Sekunden** nach der Registrierung.

Tests: `PYTHONPATH=src python3 -m unittest discover -s tests -t .` — 232 Tests,
davon 14 neu in `tests/test_ua_lds.py`, alle grün.

**Noch offen:** Beide Pis müssen mit diesem Stand neu gestartet werden
(`systemctl restart opcua-server.service`), damit die Registrierung dauerhaft
aus dem Server selbst kommt. Zum Zeitpunkt der Abnahme lief nur Layer 1.

## Offene Fragen

- Der Aggregation-Server hängt Namespaces dauerhaft an (Franka und EVA hatten
  nach ihrem Verschwinden noch Namespaces, aber kein Objekt mehr). Ob ein
  Modulwechsel dort aufräumt, ist ungeklärt — für uns unkritisch.

## Nach Abschluss

- Status auf `fertig`, tatsächliche Schnittstellen eingetragen.
- Zeile im Index `doc/arbeitsplaene/README.md` aktualisiert.
- Fachwissen nach `doc/part10-programm-schnittstelle.md` und
  `doc/vision-system.md` übernommen.
