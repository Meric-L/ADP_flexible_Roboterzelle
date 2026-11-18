# 0003 — mDNS discovery of the Pis instead of a URL list

- **Status:** accepted
- **Date:** 2026-09-21 (Pi announcement), 2026-11-17 (backend browsing)

## Context

Pi IP addresses change; a hard-coded URL list in the frontend does not scale to more modules.

## Decision

| Side | Behaviour |
| --- | --- |
| Pi | announces `<visionSystemId>._opcua-tcp._tcp.local.` (e.g. `vision-ceiling-01`); also registers at the LDS via `RegisterServer` without `MdnsDiscoveryConfiguration` |
| WSC backend | browses `_opcua-tcp._tcp.local.`, sends `serversAnnounced`; `WSC_MDNS=0` switches it off |
| Frontend | lists announced servers; manual `ConnectOpcUa` stays as fallback |

## Consequences

- mDNS ends at the subnet boundary (multicast, TTL 1); other VLANs need the manual entry.
- An mDNS announcement alone does not register at the aggregation server; the LDS call is needed.

## Alternatives considered

- Static URL list: breaks with changing IPs.
- Relying on the aggregation server to search mDNS itself: measured not true (correction 21.09.2026).

Sources: [`../interfaces/part10-program-interface.md`](../interfaces/part10-program-interface.md) §2,
[`../work-plans/pi-backend-discovery.md`](../work-plans/pi-backend-discovery.md),
WSC `docs/development.md` (`WSC_MDNS`).
