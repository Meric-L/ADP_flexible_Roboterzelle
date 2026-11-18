# Architecture Decision Records

An ADR records one decision that is **in force** in the code or docs: why it was taken and
what it costs. It is short and not rewritten when the decision changes; a new ADR supersedes it.

## Template

```
# NNNN — Title
- Status: accepted | superseded by NNNN
- Date: when the decision was taken
## Context
## Decision
## Consequences
## Alternatives considered
```

Files are named `NNNN-short-english-title.md`.

## Index

| No. | Title | Status |
| --- | --- | --- |
| 0001 | [OPC UA with the OPC 40100 Machine Vision model for the vision Pis](0001-opcua-with-machine-vision-40100.md) | accepted |
| 0002 | [Part 10 `VisionProgram` as the device root the backend discovers generically](0002-part10-program-as-device-root.md) | accepted |
| 0003 | [mDNS discovery of the Pis instead of a URL list](0003-mdns-discovery-of-the-pis.md) | accepted |
| 0004 | [Error codes as OPC UA enumeration, named method arguments](0004-error-codes-as-opcua-enumeration.md) | accepted |
| 0005 | [Vision workflows with robot motion stay in the WSC frontend](0005-robot-motion-workflows-stay-in-frontend.md) | accepted |
| 0006 | [Four fixed world tags as the common world frame](0006-world-tags-as-common-world-frame.md) | accepted |
| 0007 | [Hand-eye: one constant transform, anchored once, calibrated with the intrinsics](0007-hand-eye-anchoring-and-joint-calibration.md) | accepted |
| 0008 | [Pick & Place is the group's use case only; UI polish belongs to the WSC team](0008-pick-and-place-and-ui-scope.md) | accepted |
| 0009 | [Project documentation: English, current state, plan before code](0009-project-documentation-rules.md) | accepted |
| 0010 | [Tag map lives on the Pi, set by an OPC UA method](0010-tag-map-lives-on-the-pi.md) | accepted |
| 0011 | [Detection result as JSON string in `ResultContent[0]`](0011-result-payload-as-json-string.md) | accepted |
