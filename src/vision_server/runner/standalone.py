"""Standalone run: the vision system as its own process (`run`)."""

import logging

from asyncua import Server

from ..address_space import configure_server
from ..aio import stop_event_on_signals
from ..config import VisionServerConfig
from .install import install_vision_machine

_log = logging.getLogger(__package__)


async def run(config: VisionServerConfig) -> None:
    """Start the vision server as its own process and keep it alive.

    In production the vision system is part of the cell's robot server; this path is for
    development and isolated tests.
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
