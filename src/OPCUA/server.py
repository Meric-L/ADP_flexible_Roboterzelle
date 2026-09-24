#!/usr/bin/env python3
"""Startpunkt der systemd-Unit auf den Pis. Der Server liegt im Paket.

Diese Datei enthaelt **keine Logik**. Sie existiert allein, damit
`opcua-server.service` unveraendert weiterlaeuft: die Unit startet seit jeher
`src/OPCUA/server.py`, sie ist in keinem Repo versioniert (Altlast D5), und auf
beiden Pis von Hand nachzuziehen waere eine Fehlerquelle bei jedem Neuaufsetzen.

Der Server selbst steht in `vision_server/server.py`. Wer ihn direkt startet,
nimmt den Modulweg:

    PYTHONPATH=src python3 -m vision_server.server

Wegfallen kann diese Datei, sobald die systemd-Unit im Repo liegt und mit
ausgerollt wird -- dann ist der Einstiegspunkt dort nachvollziehbar
umzustellen. Siehe Altlast D5.
"""

import asyncio
import sys
from pathlib import Path

# `src/` auf den Pfad, damit `vision_server` importierbar ist. Die Unit ruft
# diese Datei ueber ihren absoluten Pfad auf; ein Verlass auf das
# Arbeitsverzeichnis waere unzuverlaessig.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vision_server.server import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main())
