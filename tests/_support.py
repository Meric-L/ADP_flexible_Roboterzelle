"""Shared test helpers, importable without numpy, cv2 or asyncua.

This module is not collected by `unittest discover` (its name does not match
`test*.py`). Import it as `from tests._support import ...`. `tagloc` and
`asyncua` are loaded inside the functions that need them.
"""

import asyncio
from types import SimpleNamespace

from vision_server.config import DEFAULT_AMCM_NODESET_PATHS

#: Without the nodesets in src/vision_server/nodesets/ the plant view (part 2)
#: is unavailable and the affected tests skip.
HAS_NODESETS = all(path.is_file() for path in DEFAULT_AMCM_NODESET_PATHS)


class RecordingNode:
    """An OPC UA node that records every `write_value` call.

    A `ua.Variant` is unwrapped (`written` then holds `.Value`); anything else
    is stored as given, so tests compare values rather than Variants. `nodeid`
    is enough for code that logs `node.nodeid.to_string()`; `fail=True` makes
    every write fail.
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
    """Start a publisher (`start()`/`stop()`), let it run for `seconds`, then stop it."""
    publisher.start()
    await asyncio.sleep(seconds)
    await publisher.stop()


def pose(translation, *, rvec=(0.0, 0.0, 0.0)):
    """A pose from a translation and a rotation vector (`geometry.from_rvec_tvec`)."""
    from tagloc import geometry

    return geometry.from_rvec_tvec(rvec, translation)


class AddressSpaceCache:
    """Build the vision address space (`configure_server` + `attach_vision_system`)
    once per variant instead of once per test method.

    Building takes several seconds (four nodeset imports, about 700 nodes);
    without caching, test_asset_model.py alone took two minutes. Each test
    module holds its own instance, because keys such as "mit" and "ohne" are
    only unique within one file, and some files build further on the address
    space (`attach_asset_model`).

    `port` only goes into the endpoint and only takes effect on the first build
    of a key.
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
