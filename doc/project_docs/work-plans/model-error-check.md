# Check the model errors of the module pose and make them correctable

**Status:** planned — 10.11.2026
**Owner:** Meric
**Topic:** apriltag
**Branch:** apriltag-aufraeumen (implementation open)

## Goal

The systematic model errors of the module pose are checked and made correctable.
The robot grips at one point of the module, but only the tag is measured.
The offset tag→module is stored in the catalog. If it is wrong, the error has the same
effect on every measurement. The goal is an acceptance test per module that makes this
share visible, and a correction proposal for the catalog that is only adopted after
confirmation.

An adhesive or CAD error has the same effect on every measurement. It is not made visible
and not made smaller by averaging or calibration runs. Example: a 1° rotation with a 200 mm
lever arm gives roughly 3.5 mm offset at the gripping point.

## Already read

- `CLAUDE.md`, section „Before you start“
- `doc/project_docs/work-plans/README.md` (no open plan on this topic apart from the hand-eye plans that are already done)
- `doc/project_docs/work-plans/apriltag-hand-eye-anchoring.md`, including addendum of 10.11.2026
- `doc/project_docs/work-plans/layer2-measuretags.md`
- `doc/project_docs/topics/apriltag-localization.md`, sections 1.1–1.5

## Affected files

Not yet fixed. The implementation takes place in the frontend (webskillcomposition).
Known connection points:

- `ApproachPlanPanel.tsx` (layer 2 frontend, hand-eye panel with „Versatz-Prüfung“ (offset check))
- `layer2.ts` (anchor and module calculation)
- `usePickPlaceRun` (back-calculation via the base of the twin)
- Module catalog in webskillcomposition (location still to be clarified)

In the `apriltag` repo nothing is expected to be touched. A file that is touched in parallel
will be agreed on beforehand.

## Interfaces

### OPC UA

No new nodes, if the check runs in the frontend (robot motion and input by the operator).
If it instead runs via the hand Pi or the robot server, that is open (see Open questions).

### Python

Not planned. The check is frontend logic; the pure mathematics (correction calculation from
the deviations) will be decided during implementation.

### Data / payload

**Module catalog (extension per module):**

```json
{
  "checkPoints": [
    {
      "id": "ecke-oben-links",
      "label": "Brettecke oben links",
      "positionM": [0.0, 0.0, 0.0],
      "approachDirection": [0.0, 0.0, 1.0]
    }
  ]
}
```

- `positionM`: check point in the module CS, in metres.
- `label`: label for the operator.
- `approachDirection`: approach direction, unit vector in the module CS. The
  tool tip stands in front of the point, with a safety distance (model: 10 mm
  before the first board corner in the Versatz-Prüfung).

**Result record `wsc.modelcheck.result/1`:**

```json
{
  "schema": "wsc.modelcheck.result/1",
  "module": "MOD-A",
  "tagId": 7,
  "checkPointId": "ecke-oben-links",
  "targetInBase": { "position": [0.0, 0.0, 0.0] },
  "measuredDeviationMm": { "x": 0.0, "y": 0.0, "z": 0.0 },
  "confirmedBy": "bediener",
  "measuredAt": "2026-11-10T12:00:00+01:00",
  "calibrationId": "cam_flange@…"
}
```

- `targetInBase`: target position of the check point in the robot base CS.
- `measuredDeviationMm`: measured deviation (actual minus target) in millimetres.
- `calibrationId`: hand-eye or intrinsics calibration with which the measurement was made.

**Correction proposal `wsc.modelcheck.correction/1`:**

```json
{
  "schema": "wsc.modelcheck.correction/1",
  "tagId": 7,
  "deltaPositionMm": [0.0, 0.0, 0.0],
  "deltaRotationDeg": 0.0,
  "basedOn": ["ecke-oben-links", "bohrung-1", "anschlag-2"],
  "status": "vorschlag"
}
```

- The proposal is only created with `status: "vorschlag"`. It is adopted
  only after confirmation.

### Network / operations

No new services, ports or mDNS names as long as the check runs in the frontend.

### Failure cases

| Case | Behaviour |
| --- | --- |
| Check point unreachable or collision | Abort the approach, mark the point as „nicht geprüft“ (not checked), no substitute point without confirmation |
| Deviation greater than threshold | Abort the check, no auto-correction |
| Calibration (intrinsics or hand-eye) changed since the measurement | Discard the result or mark it as outdated, measure again |
| Module moved since layer 2 | Check invalid, re-run layer 2 |

## Approach

1. Acceptance test per module, analogous to the „Versatz-Prüfung“ (offset check) in the hand-eye panel:
   after layer 2 the robot moves the tool tip to defined check points on the
   module, with a safety distance.
2. Check points are stored in the module catalog (corners, bores,
   stop edges). At least two, better three points per module, so that displacement and
   rotation can be separated.
3. The operator measures the deviation (feeler gauge or vernier caliper) or
   confirms a hit.
4. The correction proposal for tag position and tag rotation in the catalog is calculated
   from the deviations.
5. Adoption only after confirmation. No automatic correction.

## Open questions

- Who measures the deviation: operator, probe or a second camera?
- Tolerance per module, or threshold for aborting?
- Which modules are checked first (tables for pick & place)?
- Is the height (so far from catalog and floor) also checked and corrected?
- Does the check lie in the frontend, or does it need an OPC UA method on the hand Pi or the robot server?

## Deviations from the plan

Not yet implemented.

## After completion

- [ ] Status set to `done`, actual interfaces entered.
- [ ] Line in the index `doc/project_docs/work-plans/README.md` updated.
- [ ] Lasting topic knowledge carried over into the matching topic doc under `doc/`.
