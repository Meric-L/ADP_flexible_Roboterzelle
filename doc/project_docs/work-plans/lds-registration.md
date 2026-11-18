# Registration with the cell's local discovery server

**Status:** done — 21.09.2026
**Owner:** `Agent: LDS registration, so that the aggregation server takes up the vision servers`
**Topic:** mdns
**Branch:** worktree-lds-register-server2

## Goal

The two vision servers appear in the cell's aggregation server (`opc.tcp://10.10.38.27:48400/`) under
their ApplicationUri, without anyone entering them there by hand. Before, this did not work: the pure
mDNS announcement from `src/ua_mdns.py` is not sufficient for it.

## Already read

- `doc/part10-program-interface.md` (section „Der Aggregation-Server findet uns von selbst" (The
  aggregation server finds us by itself) — refuted in substance, see below)
- `doc/vision-server-interface.md` (endpoint, ApplicationUri)
- `doc/vision-system.md` (current state on the Pi, port 4840)
- `doc/legacy.md` (B1, hard-coded Pi addresses)
- `betreuer/OPC UA-mDNS-Kurzanleitung.md`

## Measurement that triggered the plan

Measured in the lab on 21.09.2026, from `10.10.38.110` (same subnet):

- Both Pis were cleanly visible via mDNS (`vision-ceiling-01` → `10.10.38.104:4840`,
  `vision-flange-01` → `10.10.38.109:4840`, TXT `path=/raspi/server/`, `caps=DA`), OPC UA directly
  reachable, ApplicationUri correct.
- The aggregation server nevertheless did not list them.
- On the same machine, under `opc.tcp://10.10.38.27:4840/`, an **open62541 Local Discovery Server**
  runs. Its `FindServers` returns exactly the modules that the aggregation server lists under `Objects`.
- The aggregation server restarted at 15:56 and took up all five modules registered there during the
  fresh scan. Our Pis had been running for ten minutes at that time and were broadcasting — they
  remained outside.
- The names in the LDS inventory (`Festo Conveyor OPC UA Server-10-10-38-41`,
  `OJIES-Aggregation-LDS-reuther`) appear **nowhere** on the wire in a 15 s Avahi search. The LDS
  therefore does not read mDNS; it has its own registration inventory.
- All working modules have `ProductUri: urn:freeopcua.github.io:python:server` and
  `ServerType: ClientAndServer` — they are asyncua servers that register actively.

**Conclusion:** The route into the aggregation server leads via an active registration at the LDS
(`RegisterServer`), not via mDNS. The statement in `betreuer/OPC UA-mDNS-Kurzanleitung.md` and in
`doc/part10-program-interface.md`, that a registration is not necessary, does not hold.

## Affected files

- `src/ua_lds.py` (new)
- `src/OPCUA/server.py` (integration into the server loop)
- `tests/test_ua_lds.py` (new)
- `doc/part10-program-interface.md` (correction of the discovery section)
- `doc/vision-system.md` (operating information)
- `doc/legacy.md` (entry on the wrong betreuer statement)

## Interfaces

### Network / operations

| Element | Value |
| --- | --- |
| Discovery server (LDS) | `opc.tcp://10.10.38.27:4840/` |
| Aggregation server | `opc.tcp://10.10.38.27:48400/` |
| Service | `Server.register_to_discovery()` → `RegisterServer`, without `MdnsDiscoveryConfiguration` |
| Connection | sessionless (`connect_sessionless`), `NoSecurity` |
| Renewal | every 60 s — necessary, not optional (measured, see below) |
| Registered DiscoveryUrl | `opc.tcp://<LAN-IPv4>:4840/raspi/server/` |
| Disabling | `OPCUA_LDS_URL=""` |
| Deviating LDS | `OPCUA_LDS_URL=opc.tcp://host:4840/` |

The mDNS announcement from `src/ua_mdns.py` **remains unchanged** — clients in the subnet (including
the WSC frontend) find us directly via it, without a detour through the discovery server.

### Python

```python
# Module: src/ua_lds.py

DEFAULT_LDS_URL = "opc.tcp://10.10.38.27:4840/"
DEFAULT_RENEW_SECONDS = 60

def lds_url() -> str:
    """URL of the discovery server from `OPCUA_LDS_URL`; empty = do not register."""

def advertised_endpoint(
    port: int, path: str, address: str | None = None
) -> str | None:
    """Endpoint URL with the LAN IPv4, or None if none could be determined."""

@contextlib.asynccontextmanager
async def register(
    server: Server,
    url: str | None = None,
    renew_seconds: int = DEFAULT_RENEW_SECONDS,
) -> AsyncIterator[str | None]:
    """Registers with the LDS, renews periodically, deregisters on exit.

    Returns the registered DiscoveryUrl, or None if no registration took place
    (no LDS configured, endpoint names 0.0.0.0, registration failed). Raises
    nothing — errors end up in the log.
    """
```

In `src/OPCUA/server.py`:

```python
endpoint = ua_lds.advertised_endpoint(MDNS_PORT, MDNS_PATH) or ENDPOINT
server.set_endpoint(endpoint)            # what is announced: LAN IPv4
server.socket_address = ("0.0.0.0", MDNS_PORT)   # what is listened on
```

The registration itself is done by `asyncua.Server.register_to_discovery()`; `ua_lds` is only the
wrapper around it. The original draft built the `RegisteredServer` by hand, because the method enters
`server.endpoint.geturl()`, and `0.0.0.0` stood there. That was unnecessary: `Server.socket_address`
separates bind address and announced address and is intended exactly for this. What the wrapper still
provides: refusing registration if the endpoint nevertheless contains `0.0.0.0`; not letting an
unreachable LDS cost the server its start; and deregistering in `finally`, because `Server.stop()`
does not do it.

### Registration record

| Field | Value |
| --- | --- |
| `ServerUri` | ApplicationUri of the module, e.g. `urn:plcm:camera-server:ceiling-01` |
| `ProductUri` | `urn:freeopcua.github.io:python:server` |
| `ServerNames` | `Raspberry Pi OPC UA Server` |
| `ServerType` | `ClientAndServer` |
| `DiscoveryUrls` | `opc.tcp://<LAN-IPv4>:4840/raspi/server/` |
| `IsOnline` | `True`; on shutdown `False` (deregistration) |
| `DiscoveryConfiguration` | none — `register_to_discovery()` without `discovery_configuration` sends `RegisterServer`, as the neighboring modules do |

In the aggregation server, the module then appears as an object under `Objects` in its `ns=1`, named
after the ApplicationUri — so `urn:plcm:camera-server:ceiling-01` and
`urn:plcm:camera-server:roboter-hand-01` are expected.

### Failure cases

| Case | Behavior |
| --- | --- |
| LDS unreachable | warning in the log, server keeps running, mDNS stays active |
| No LAN IPv4 determinable | no registration, warning in the log |
| Renewal fails once | warning, loop continues |
| `OPCUA_LDS_URL=""` | registration disabled, note in the log |
| Hard abort (no SIGTERM) | no deregistration; the entry expires at the LDS |

## Approach

1. Write `src/ua_lds.py`.
2. Prove live against the running Pi that the registration actually causes the aggregation server to
   take up the module.
3. Integrate into `src/OPCUA/server.py`, within the existing `ua_mdns.announce` block.
4. Tests.
5. Correct the docs.

## Deviations from the plan

- **First bypassed `Server.register_to_discovery()`, then switched to it after all.** The method enters
  `server.endpoint.geturl()` as DiscoveryUrl, and there stood `0.0.0.0` — the aggregation server takes
  this over literally and connects into nothing. The first draft therefore built the `RegisteredServer`
  by hand, including its own renewal loop and deregistration: around 190 lines.

  That was unnecessary. `Server.socket_address` separates bind address and announced address and is
  intended exactly for this case („used when the IP address of the network interface is different from
  the endpoint IP offered to the client during discovery"). With it, the endpoint names the LAN IPv4, and
  listening continues on `0.0.0.0`. `ua_lds` is now only the wrapper around the library method. Recorded
  as legacy D10, because overlooking the attribute cost time.

  Also checked and refuted: the concern that the library loop would not cope with an LDS restart.
  `_renew_registration()` builds a fresh channel on each run.
- **Went back from `RegisterServer2` to `RegisterServer`.** The first draft sent an
  `MdnsDiscoveryConfiguration`. Afterwards, in the LDS's `FindServersOnNetwork`, there appeared once
  `opc.tcp://10.10.38.104.local:4840/raspi/server` — a `.local` appended to an IP, which does not
  resolve. **Not reproducible:** `flange-01` registered via the same code path and got a clean entry.
  The effect is therefore not proven as an error, and it does not affect the aggregation server anyway.
  Nevertheless we now register the same way as Conveyor, CardDispenser and the robots — one variable
  fewer. `caps=DA` is in any case in our own mDNS announcement.
- **Renewal is proven necessary.** Originally justified with „die Spezifikation verlangt ≤ 10 min“
  (the specification requires ≤ 10 min) — but that comes from the asyncua docstring, so it was adopted
  and not measured. The proof came differently (see below).

## Result of acceptance

Measured live against the running cell (21.09.2026, layer 1 = `ceiling-01` on `10.10.38.104`):

| Time | Observation |
| --- | --- |
| 16:18:05 | `RegisterServer2` for `urn:plcm:camera-server:ceiling-01` sent; appears immediately in the LDS `FindServers` |
| 16:18:36 | aggregation server lists the module as an object under `Objects`, with all nine namespaces incl. `http://launch-rm.de/vision/urn:plcm:camera-server:ceiling-01` |
| 16:18:37 | after deregistration (`IsOnline=False`) gone from the LDS again |

Admission therefore took **31 seconds** after registration.

### Confirmed in operation

After rolling out to the Pis and restarting the service, **both** modules are in the aggregation server;
the registration comes from the server itself:

```
urn:plcm:camera-server:ceiling-01
urn:plcm:camera-server:roboter-hand-01
```

For `ceiling-01`, additionally observed over 4.5 minutes and thus several renewal cycles: continuously
in the LDS and in the aggregation server, without gaps.

### Evidence that renewal is necessary

It came about by chance and is the most reliable measurement of the day: the LDS restarted on
21.09.2026 at 15:56 (`LastCounterResetTime` jumps along). Conveyor has been running since 08.09. and
CardDispenser since 07.09. — both **without** their own restart — and both were back in the registration
inventory afterwards. A registration that was sent only once at the module's own start would have sat in
the old LDS process and been lost. The neighboring modules therefore renew periodically; this is not
visible in their code, because `asyncua.Server.register_to_discovery()` starts the loop itself (default
interval 60 s).

Not measured: how long an entry actually survives without renewal. open62541 cleans up after its own
timeout.

### After the switch to `register_to_discovery()`

Checked locally with the real `src/OPCUA/server.py` (LDS registration switched off via
`OPCUA_LDS_URL=""`, so the cell was not touched):

```
INFO:raspi-opcua:Server startet auf opc.tcp://10.10.38.110:4840/raspi/server/
INFO:asyncua.server.binary_server_asyncio:Listening on 0.0.0.0:4840
```

Reachable via `127.0.0.1` **and** via the LAN IP — so the separation of endpoint and bind address works,
and `print_setpoint.py` as well as the Hello World client keep their route via loopback.

**Still open:** the Pis still run the previous state. Only after `systemctl restart opcua-server.service`
does the registration come from `register_to_discovery()`; until then the switch is verified only
locally.

Tests: `PYTHONPATH=src python3 -m unittest discover -s tests -t .` — 241 tests, of which 12 in
`tests/test_ua_lds.py`, all green.

## Addendum: package move of 22.09.2026

One day after this plan, `legacy-removal-part10-facade.md` restructured the package. The paths named
above **no longer exist**:

| This plan names | Now located under |
| --- | --- |
| `src/ua_lds.py` | `src/vision_server/discovery/lds.py` |
| `src/ua_mdns.py` | `src/vision_server/discovery/mdns.py` |
| integration into `src/OPCUA/server.py` | `src/vision_server/server.py` (`src/OPCUA/server.py` has since been only a 15-line starter, see legacy D11) |
| `tests/test_ua_lds.py` | `tests/test_discovery_lds.py` |

The Python interface itself (`lds_url()`, `advertised_endpoint()`, `register()`, constants, behavior)
is unchanged — a pure move, no signature change. The current state is in
[`vision-server-interface.md`](../interfaces/vision-server-interface.md) and [`vision-system.md`](../topics/vision-system.md);
this plan remains as a historical record of the measurement.

## Open questions

- The aggregation server keeps namespaces permanently (Franka and EVA still had namespaces after they
  disappeared, but no object any longer). Whether a module change cleans up there is unclear — for us
  it is not critical.

## After completion

- Status set to `done`, actual interfaces entered.
- Line in the index `doc/work-plans/README.md` updated.
- Lasting domain knowledge carried over to `doc/part10-program-interface.md` and `doc/vision-system.md`.
