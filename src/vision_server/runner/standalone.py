"""Standalone-Lauf: das Vision-System als eigener Prozess (`run`)."""

import logging

from asyncua import Server

from ..address_space import configure_server
from ..aio import stop_event_on_signals
from ..config import VisionServerConfig
from .install import install_vision_machine

_log = logging.getLogger(__package__)


async def run(config: VisionServerConfig) -> None:
    """Startet den Vision-Server als eigenen Prozess und haelt ihn am Leben.

    Produktiv haengt das Vision-System im Server der Roboterzelle; dieser Weg
    ist fuer Entwicklung und isolierte Tests.
    """
    server = Server()
    await configure_server(server, config)
    machine = await install_vision_machine(server, config)
    _log.info("Vision-Server laeuft auf %s", config.endpoint)
    stop = stop_event_on_signals()
    try:
        async with server:
            await stop.wait()
    finally:
        await machine.aclose()
