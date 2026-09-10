"""Zusammenbau des Vision-Systems, als Einbau oder als eigener Prozess."""

import asyncio
import logging
from dataclasses import dataclass

from asyncua import Server, ua, uamethod

from .address_space import attach_vision_system, configure_server
from .config import VisionServerConfig
from .detection import DetectionSource, build_detection_sources
from .events import VisionEvents, create_event_generators
from .job import JobRunner
from .result_management import ResultStore
from .state_machine import VisionStateMachines

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisionMachine:
    """Das fertig verdrahtete Vision-System eines Servers."""

    config: VisionServerConfig
    states: VisionStateMachines
    events: VisionEvents
    results: ResultStore
    sources: dict[str, DetectionSource]
    jobs: JobRunner


async def install_vision_machine(server: Server, config: VisionServerConfig) -> VisionMachine:
    """Baut das Vision-System in einen initialisierten Server ein.

    Muss nach `server.init()` und vor `server.start()` laufen. Der Server darf
    daneben beliebige eigene Knoten haben — Endpoint, ApplicationURI und
    ServerName bleiben unberuehrt.
    """
    space = await attach_vision_system(server, config)
    states = await VisionStateMachines.bind(space)
    events = await create_event_generators(space)
    results = await ResultStore.create(space)
    sources = build_detection_sources(config)
    jobs = JobRunner(config, states, events, results, sources)

    @uamethod
    async def start_single_job(parent, meas_id, part_id, recipe_id, product_id, parameters):
        """OPC-UA-Einstiegspunkt fuer 1:StartSingleJob.

        Muss `async` sein: synchrone Handler laufen bei asyncua in einem
        ThreadPoolExecutor ohne laufenden Event-Loop, dort scheitert das
        Starten des Job-Tasks. `jobs.start_single_job` bleibt synchron und
        wird ohne `await`-Punkt aufgerufen — die Zulassung bleibt atomar.

        Gibt JobId und Error als String bzw. Int32 zurueck, obwohl das Nodeset
        `JobIdDataType` deklariert — asyncua validiert Methodenargumente nicht,
        und ein Client koennte das ExtensionObject nicht dekodieren. Die
        Rueckgabe muss ein Tupel sein; eine Liste wuerde asyncua als einen
        einzigen Variant verpacken.
        """
        job_id, error = jobs.start_single_job(
            meas_id, part_id, recipe_id, product_id, parameters
        )
        return (
            ua.Variant(job_id, ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    server.link_method(space.start_single_job, start_single_job)
    await states.enter_operational()

    _log.info(
        "Vision-System '%s' bereit (Recipes %s, Namespace %s)",
        config.vision_system_name,
        ", ".join(sorted(x for x in sources if x)),
        config.namespace_uri,
    )
    return VisionMachine(
        config=config,
        states=states,
        events=events,
        results=results,
        sources=sources,
        jobs=jobs,
    )


async def run(config: VisionServerConfig) -> None:
    """Startet den Vision-Server als eigenen Prozess und haelt ihn am Leben.

    Produktiv haengt das Vision-System im Server der Roboterzelle; dieser Weg
    ist fuer Entwicklung und isolierte Tests.
    """
    server = Server()
    await configure_server(server, config)
    await install_vision_machine(server, config)
    _log.info("Vision-Server laeuft auf %s", config.endpoint)
    async with server:
        await asyncio.Event().wait()
