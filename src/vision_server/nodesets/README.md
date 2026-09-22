# Companion-Spec-Nodesets

Diese Dateien gehören **nicht** uns — sie stammen aus
[`OPCFoundation/UA-Nodeset`](https://github.com/OPCFoundation/UA-Nodeset) und
liegen hier, damit der Server ohne Netzzugriff startet.

| Datei | Spezifikation | Version | Tag im UA-Nodeset-Repo |
|---|---|---|---|
| `Opc.Ua.Di.NodeSet2.xml` | Device Integration | 1.04.0 | `DI-1.04.0-2022-11-03` |
| `Opc.Ua.Machinery.NodeSet2.xml` | Machinery | 1.03.0 | `Machinery-1.03.0-2023-08-01` |
| `Opc.Ua.MachineVision.AMCM.NodeSet2.xml` | **OPC 40100-2**, Asset Management and Condition Monitoring | 1.00.0 | `latest` (2024-05-17, einzige Version) |

Part 1 liegt eine Ebene höher als `../Opc.Ua.MachineVision.NodeSet2.xml`
(Version 1.0.0, 2019-07-11).

## Die Versionen sind absichtlich gepinnt — nicht auf `latest` heben

Gemessen mit asyncua 2.0.1:

* **DI 1.05.0 lässt sich nicht importieren.** Es fordert UA-Basis 1.05.04, und
  asyncuas eingebauter Standard-Adressraum ist dafür zu alt: der Import scheitert
  mit `BadParentNodeIdInvalid`. DI 1.04.0 (fordert 1.05.01) importiert sauber.
* **Machinery 1.04.1 zieht zusätzlich `IA` herein.** Machinery 1.03.0 braucht nur
  UA 1.05.02 und DI 1.04.0 — damit ist die Kette eine Datei kürzer.

Gewählt sind deshalb genau die Versionen, die AMCM 1.00.0 als `RequiredModel`
nennt. Wer hier aktualisiert, muss
`PYTHONPATH=src python3 tools/measure_nodeset_import.py` erneut laufen lassen.

## Importreihenfolge

DI → Machinery → AMCM. Part 1 ist unabhängig davon und kann vorher oder nachher
kommen. Die Reihenfolge steht in `tools/measure_nodeset_import.py` und in
`src/vision_server/nodeset_ids.py`.

## Kosten (Desktop, asyncua 2.0.1)

Part 1 allein 108,5 MB RSS; mit DI, Machinery und AMCM 121,3 MB — **rund 13 MB
und 1,6 s Startzeit für Part 2**. Auf dem Pi noch zu bestätigen.
