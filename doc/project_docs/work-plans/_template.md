# <Topic in one line>

**Status:** planned | in progress | done | dropped — <DD.MM.YYYY>
**Owner:** <name> or `Agent: <task description>`
**Topic:** opcua | vision | apriltag | mdns | frontend | other
**Branch:** <branch-name>

## Goal

One to three sentences: what works afterwards, what did not work before?

## Already read

Which topic docs under `doc/` were evaluated for this plan?
(CLAUDE.md, section "Before you start")

- `doc/…`

## Affected files

Paths that will be touched. This list effectively reserves the files —
anyone who wants to touch them in parallel coordinates beforehand.

- `src/…`
- `tests/…`

## Interfaces

**The most important section.** Concrete enough that someone else can develop against it
without this code. Do not strike out what does not apply.

### OPC UA

| Element | NodeId / BrowsePath | Type | Direction | Meaning |
| --- | --- | --- | --- | --- |
| … | `ns=4;s=…` | … | Call / Read / Event | … |

Methods with full signature: input arguments, data types,
return values, error StatusCodes.

### Python

```python
# Module: src/…
def function(param: Type) -> Return:
    """What it guarantees, what it raises."""
```

### Data / payload

```json
{ "field": "Type and meaning" }
```

### Network / operations

Ports, services, host names, mDNS names, configuration files, systemd units.

### Failure cases

What happens on timeout, missing camera, invalid job, abort?

## Approach

1. …
2. …

## Open questions

- …

## Deviations from the plan

Filled in during implementation. If reality deviates from the plan, the plan is
corrected — an outdated interface description is worse than none at all.

## After completion

- Status set to `done`, actual interfaces entered.
- Line in the index `doc/work-plans/README.md` updated.
- Lasting topic knowledge carried over into the matching topic doc under `doc/`
  (not just left in this plan).
