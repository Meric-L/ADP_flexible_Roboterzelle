# Companion-spec nodesets

These files are **not** ours — they come from
[`OPCFoundation/UA-Nodeset`](https://github.com/OPCFoundation/UA-Nodeset) and
are kept here so the server starts without network access.

| File | Specification | Version | Tag in the UA-Nodeset repo |
|---|---|---|---|
| `Opc.Ua.Di.NodeSet2.xml` | Device Integration | 1.04.0 | `DI-1.04.0-2022-11-03` |
| `Opc.Ua.Machinery.NodeSet2.xml` | Machinery | 1.03.0 | `Machinery-1.03.0-2023-08-01` |
| `Opc.Ua.MachineVision.AMCM.NodeSet2.xml` | **OPC 40100-2**, Asset Management and Condition Monitoring | 1.00.0 | `latest` (2024-05-17, only version) |

Part 1 is one level higher, at `../Opc.Ua.MachineVision.NodeSet2.xml`
(version 1.0.0, 2019-07-11).

## The versions are deliberately pinned — do not move them to `latest`

Measured with asyncua 2.0.1:

* **DI 1.05.0 cannot be imported.** It requires UA base 1.05.04, and asyncua's
  built-in standard address space is too old for this: the import fails with
  `BadParentNodeIdInvalid`. DI 1.04.0 (requires 1.05.01) imports cleanly.
* **Machinery 1.04.1 additionally pulls in `IA`.** Machinery 1.03.0 only needs
  UA 1.05.02 and DI 1.04.0 — which makes the chain one file shorter.

The versions chosen are therefore exactly those that AMCM 1.00.0 names as
`RequiredModel`. Anyone updating here must run
`PYTHONPATH=src python3 tools/measure_nodeset_import.py` again.

## Import order

DI → Machinery → AMCM. Part 1 is independent of this and can come before or
after. The order is given in `tools/measure_nodeset_import.py` and in
`src/vision_server/nodeset_ids.py`.

## Cost (desktop, asyncua 2.0.1)

Part 1 alone 108.5 MB RSS; with DI, Machinery and AMCM 121.3 MB — **about 13 MB
and 1.6 s startup time for Part 2**. Still to be confirmed on the Pi.
