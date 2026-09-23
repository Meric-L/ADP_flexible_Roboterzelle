"""Eigene Knoten mit sprechender String-NodeId anlegen.

Alle additiven Knoten dieses Servers folgen demselben Schema: NodeId
`<Praefix>.<Name>` und BrowseName `<Name>`, beide im eigenen Namensraum.
Explizit statt der laufenden Nummer, die `add_variable(own_idx, ...)`
vergeben wuerde: die verschiebt sich, sobald jemand davor einen Knoten
einfuegt, und Frontend wie Backend sprechen diese Knoten ueber feste
Adressen an (doc/vision-server-interface.md §13.1).
"""

from typing import Any

from asyncua import ua
from asyncua.common.node import Node


def named_node_id(prefix: str, name: str, ns: int) -> ua.NodeId:
    """`ns=<ns>;s=<prefix>.<name>`."""
    return ua.NodeId(f"{prefix}.{name}", ns)


async def add_named_variable(
    parent: Node,
    prefix: str,
    name: str,
    ns: int,
    value: Any,
    vtype: ua.VariantType,
    *,
    writable: bool = False,
    description: str | None = None,
) -> Node:
    """Legt eine Variable `<prefix>.<name>` unter `parent` an.

    `writable` nur fuer Knoten, die ein Client wirklich setzen soll
    (Parameter, Stream-Modus) -- alles andere schreibt allein der Server.
    `description` landet im Description-Attribut, damit ein generischer
    Client ohne diese Doku sieht, was der Knoten erwartet.
    """
    node = await parent.add_variable(
        named_node_id(prefix, name, ns), ua.QualifiedName(name, ns), value, vtype
    )
    if writable:
        await node.set_writable()
    if description is not None:
        await node.write_attribute(
            ua.AttributeIds.Description,
            ua.DataValue(ua.Variant(ua.LocalizedText(description))),
        )
    return node


async def add_named_object(parent: Node, prefix: str, name: str, ns: int) -> Node:
    """Legt ein einfaches Objekt `<prefix>.<name>` unter `parent` an."""
    return await parent.add_object(named_node_id(prefix, name, ns), ua.QualifiedName(name, ns))
