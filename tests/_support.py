"""Gemeinsame Test-Helfer, die sonst in mehreren Testdateien kopiert lagen.

Kein Testmodul (der Name passt nicht auf `test*.py`), `unittest discover`
sammelt hier also nichts ein. Import als `from tests._support import ...`.

Auf Modulebene nur Standardbibliothek und `vision_server.config` (selbst
reine Standardbibliothek): auch Tests, die ohne numpy, cv2 oder asyncua
laufen muessen, importieren von hier. `tagloc` und `asyncua` werden erst in
der jeweiligen Funktion geladen.

Die Form folgt den Commits 1c938f6 und 9a7942f aus `apriltag-refactoring`;
sie liessen sich nicht ohne Konflikt uebernehmen und wurden hier nachgebaut.
"""

import asyncio
from types import SimpleNamespace

from vision_server.config import DEFAULT_AMCM_NODESET_PATHS

#: Ohne die Nodesets unter src/vision_server/nodesets/ gibt es keine
#: Anlagensicht (Part 2) -- die betroffenen Tests springen dann ab.
HAS_NODESETS = all(path.is_file() for path in DEFAULT_AMCM_NODESET_PATHS)


class RecordingNode:
    """Ein OPC-UA-Knoten, der jedes `write_value` mitschreibt.

    Ein `ua.Variant` wird ausgepackt (`written` haelt dann `.Value`), alles
    andere so abgelegt, wie es kam -- so vergleichen die Tests Werte statt
    Variants. `nodeid` reicht fuer Code, der `node.nodeid.to_string()` loggt;
    `fail=True` laesst jedes Schreiben scheitern.
    """

    def __init__(self, nodeid: str = "ns=6;s=Test", *, fail: bool = False) -> None:
        self.written: list = []
        self.fail = fail
        self.nodeid = SimpleNamespace(to_string=lambda: nodeid)

    async def write_value(self, value) -> None:
        if self.fail:
            raise RuntimeError("Knoten nicht beschreibbar")
        self.written.append(getattr(value, "Value", value))


async def run_briefly(publisher, seconds: float) -> None:
    """Startet einen Publisher (`start()`/`stop()`), laesst ihn `seconds`
    laufen und haelt ihn wieder an."""
    publisher.start()
    await asyncio.sleep(seconds)
    await publisher.stop()


def pose(translation, *, rvec=(0.0, 0.0, 0.0)):
    """Eine Pose aus Translation und Drehvektor (`geometry.from_rvec_tvec`)."""
    from tagloc import geometry

    return geometry.from_rvec_tvec(rvec, translation)


class AddressSpaceCache:
    """Baut den Vision-Adressraum (`configure_server` + `attach_vision_system`)
    einmal je Variante statt einmal je Testmethode.

    Der Aufbau kostet mehrere Sekunden -- vier Nodeset-Importe und rund 700
    instanziierte Knoten; unmemoisiert dauerte test_asset_model.py allein
    zwei Minuten. Jedes Testmodul haelt eine **eigene** Instanz: die
    Schluessel ("mit", "ohne") sind nur innerhalb einer Datei eindeutig,
    dieselbe Variante meint je Datei andere `AssetConfig`s, und manche Datei
    baut auf dem Adressraum weiter (`attach_asset_model`).

    `port` geht nur in den Endpunkt ein und wirkt nur beim ersten Aufbau
    eines Schluessels.
    """

    def __init__(self) -> None:
        self._built: dict[str, tuple] = {}

    async def build(self, assets, key: str, port: int):
        from asyncua import Server

        from vision_server.address_space import attach_vision_system, configure_server
        from vision_server.config import VisionServerConfig

        if key in self._built:
            return self._built[key]
        config = VisionServerConfig(
            endpoint=f"opc.tcp://127.0.0.1:{port}/test/", assets=assets
        )
        server = Server()
        await configure_server(server, config)
        self._built[key] = (server, await attach_vision_system(server, config))
        return self._built[key]
