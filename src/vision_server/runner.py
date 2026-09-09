"""Composition Root des Vision-Servers."""

import asyncio
import logging

from asyncua import Server, ua, uamethod

from .address_space import build_address_space
from .config import VisionServerConfig
from .detection import build_detection_source
from .events import create_event_generators
from .job import JobRunner
from .result_management import ResultStore
from .state_machine import VisionStateMachines

_log = logging.getLogger(__name__)


async def run(config: VisionServerConfig) -> None:
    """Startet den Vision-Server und haelt ihn bis zum Abbruch am Leben."""
    server = Server()
    space = await build_address_space(server, config)
    states = await VisionStateMachines.bind(space)
    events = await create_event_generators(space)
    results = await ResultStore.create(space)
    source = build_detection_source(config)
    runner = JobRunner(config, states, events, results, source)

    @uamethod
    async def start_single_job(parent, meas_id, part_id, recipe_id, product_id, parameters):
        """OPC-UA-Einstiegspunkt fuer 1:StartSingleJob.

        Muss `async` sein: synchrone Handler laufen bei asyncua in einem
        ThreadPoolExecutor ohne laufenden Event-Loop, dort scheitert das
        Starten des Job-Tasks. `runner.start_single_job` bleibt synchron und
        wird ohne `await`-Punkt aufgerufen — die Zulassung bleibt atomar.

        Gibt JobId und Error als String bzw. Int32 zurueck, obwohl das Nodeset
        `JobIdDataType` deklariert — asyncua validiert Methodenargumente nicht,
        und ein Client koennte das ExtensionObject nicht dekodieren.
        """
        job_id, error = runner.start_single_job(
            meas_id, part_id, recipe_id, product_id, parameters
        )
        return (
            ua.Variant(job_id, ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    server.link_method(space.start_single_job, start_single_job)
    await states.enter_operational()

    _log.info(
        "Vision-Server '%s' laeuft auf %s (Profil %s)",
        config.server_name,
        config.endpoint,
        source.profile_id,
    )
    async with server:
        await asyncio.Event().wait()
