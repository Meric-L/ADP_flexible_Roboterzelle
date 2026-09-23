"""Gemeinsame Test-Helfer, die sonst in mehreren Testdateien kopiert lagen.

Kein Testmodul (der Name passt nicht auf `test*.py`), `unittest discover`
sammelt hier also nichts ein. Import als `from tests._support import ...`.

Auf Modulebene nur Standardbibliothek und `vision_server.config` (selbst
reine Standardbibliothek): auch Tests, die ohne numpy, cv2 oder asyncua
laufen muessen, importieren von hier. `tagloc` und `asyncua` werden erst in
der jeweiligen Funktion geladen.
"""

import asyncio
from types import SimpleNamespace

from vision_server.config import DEFAULT_AMCM_NODESET_PATHS

#: Ohne die Nodesets unter src/vision_server/nodesets/ gibt es keine
#: Anlagensicht (Part 2) -- die betroffenen Tests springen dann ab.
HAS_NODESETS = all(path.is_file() for path in DEFAULT_AMCM_NODESET_PATHS)

#: Ein 10x10-px-Quadrat als Tag-Ecken, genug fuer alles ausser Zeichnen.
SQUARE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))


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


async def wait_until(condition, timeout: float = 2.0) -> None:
    """Wartet, bis `condition()` wahr ist -- statt einer festen Schlafzeit,
    die auf einem langsamen Rechner zu kurz und sonst zu lang ist."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Bedingung nicht rechtzeitig erfuellt")
        await asyncio.sleep(0.01)


def make_tag_pose(
    tag_id: int,
    translation=None,
    *,
    error_px: float = 0.5,
    ambiguity: float = 0.0,
    corners=SQUARE,
):
    """Eine `TagPose` ohne Rotation, fuer Tests, die keine echte Schaetzung
    brauchen. Ohne `translation` bleibt `pose_cam_tag` `None` -- genug fuer
    den Qualitaetsfilter, der nur Fehler und Mehrdeutigkeit liest."""
    from tagloc.observations import TagObservation, TagPose

    pose_cam_tag = None
    if translation is not None:
        from tagloc import geometry

        pose_cam_tag = geometry.from_rvec_tvec((0.0, 0.0, 0.0), translation)
    return TagPose(
        tag_id=tag_id,
        pose_cam_tag=pose_cam_tag,
        reprojection_error_px=error_px,
        ambiguity_ratio=ambiguity,
        observation=TagObservation(tag_id=tag_id, corners=corners),
    )


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
