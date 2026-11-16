"""Measure the memory and time cost of importing the companion-spec nodesets.

Prints RSS and duration per nodeset import step. The numbers should be taken on the
Raspberry Pi that runs the vision server, not on a development machine.

    PYTHONPATH=src python3 tools/measure_nodeset_import.py
"""

import asyncio
import gc
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The nodesets live in the package that uses them.
NODESETS = REPO_ROOT / "src" / "vision_server" / "nodesets"

#: Import order by dependency. Versions are pinned to what AMCM 1.00.0 requires:
#: DI 1.04.0 and Machinery 1.03.0. The newest Machinery (1.04.1) also pulls in IA,
#: and the newest DI (1.05.0) fails against asyncua's base address space with
#: BadParentNodeIdInvalid.
IMPORT_ORDER = [
    ("Part 1  MachineVision", NODESETS / "Opc.Ua.MachineVision.NodeSet2.xml"),
    ("        DI 1.04.0", NODESETS / "Opc.Ua.Di.NodeSet2.xml"),
    ("        Machinery 1.03.0", NODESETS / "Opc.Ua.Machinery.NodeSet2.xml"),
    ("Part 2  AMCM 1.00.0", NODESETS / "Opc.Ua.MachineVision.AMCM.NodeSet2.xml"),
]


def rss_mb() -> float:
    """Return the resident set size of this process in MB, read from /proc (no psutil)."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return float("nan")


async def main() -> int:
    """Import the nodesets step by step and print the memory and time per step."""
    logging.disable(logging.CRITICAL)
    from asyncua import Server

    gc.collect()
    baseline = rss_mb()
    print(f"{'Schritt':<26} {'RSS':>9} {'+RSS':>8} {'Dauer':>8} {'Knoten':>8}")
    print("-" * 64)
    print(f"{'Python + asyncua':<26} {baseline:>7.1f} MB {'':>8} {'':>8} {'':>8}")

    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://127.0.0.1:48999/measure/")
    gc.collect()
    previous = rss_mb()
    print(f"{'Server.init()':<26} {previous:>7.1f} MB {previous - baseline:>6.1f} MB")

    for label, path in IMPORT_ORDER:
        if not path.is_file():
            print(f"{label:<26} {'FEHLT':>9}  {path}")
            continue
        started = time.perf_counter()
        try:
            nodes = await server.import_xml(str(path))
        except Exception as error:
            print(f"{label:<26} FEHLER: {type(error).__name__}: {error}")
            return 1
        elapsed = time.perf_counter() - started
        gc.collect()
        current = rss_mb()
        print(
            f"{label:<26} {current:>7.1f} MB {current - previous:>6.1f} MB "
            f"{elapsed:>6.2f} s {len(nodes):>8}"
        )
        previous = current

    print("-" * 64)
    print(f"Gesamt ueber der leeren Python-Laufzeit: {previous - baseline:.1f} MB")
    print()
    print("Registrierte Namensraeume:")
    for index, uri in enumerate(await server.get_namespace_array()):
        print(f"  {index}  {uri}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
