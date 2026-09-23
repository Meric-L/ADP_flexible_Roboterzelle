"""Gemeinsames der OPC-UA-Kommandozeilen-Clients unter `tools/`.

Die Clients sind Referenzimplementierungen fuer Frontend und Backend und
bleiben darum reine OPC-UA-Clients: kein Import aus dem Server-Code, nur
asyncua und -- erst beim Dekodieren eines Bildes -- OpenCV.

Aufruf wie bisher mit `PYTHONPATH=src python3 src/vision_server/tools/<tool>.py`.
"""

import argparse
import base64
import logging
from typing import Any

from asyncua import Client, Node, ua

#: Vorgaben der Kommandozeile, fuer alle Clients gleich.
DEFAULT_URL = "opc.tcp://127.0.0.1:4840/raspi/server/"
DEFAULT_NAMESPACE = "http://launch-rm.de/vision"
DEFAULT_VISION_SYSTEM = "VisionMachine"


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    """`--url`, `--namespace` und `--vision-system` mit den Vorgaben oben."""
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--vision-system", default=DEFAULT_VISION_SYSTEM)


async def vision_node(client: Client, namespace: str, vision_system: str) -> tuple[Node, int]:
    """Der Knoten `ns=<vision>;s=<vision_system>` samt Namensraumindex.

    Den Index zur Laufzeit ueber die URI aufloesen, nie hartcodieren
    (doc/vision-server-interface.md §11.2).
    """
    own_idx = await client.get_namespace_index(namespace)
    return client.get_node(ua.NodeId(vision_system, own_idx)), own_idx


def decode_frame(encoded: str) -> Any:
    """`LatestCameraFrame` (Base64-JPEG) als BGR-Bild; `None`, wenn nicht dekodierbar.

    OpenCV erst hier: die Clients ohne Bildanzeige laufen auch ohne cv2.
    """
    import cv2
    import numpy as np

    jpeg = base64.b64decode(encoded)
    return cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)


def format_progress(progress: dict, separator: str = ", ") -> str:
    """`CalibrationProgress` kurz: `12/15, Abdeckung x 84% y 90%`."""
    return (
        f"{progress.get('samples', 0)}/{progress.get('minSamples', '?')}{separator}"
        f"Abdeckung x {progress.get('coverageX', 0.0) * 100:.0f}%"
        f" y {progress.get('coverageY', 0.0) * 100:.0f}%"
    )


def configure_logging(level_name: str) -> None:
    """Log-Level aus `--log-level`; Unbekanntes faellt auf WARNING zurueck."""
    logging.basicConfig(level=getattr(logging, level_name.upper(), logging.WARNING))
