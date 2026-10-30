"""Job-Methoden des Vision-Systems: StartSingleJob, Stop, StartContinuous, Abort, Halt, Reset.

Die Knoten stammen aus dem Nodeset (`address_space.py`); hier werden nur
Handler daran gehaengt (`link_method`). Alle sind `async`: synchrone Handler
laufen bei asyncua in einem ThreadPoolExecutor ohne laufenden Event-Loop, dort
scheitert das Starten des Job-Tasks.
"""

import logging
from collections.abc import Callable

from asyncua import ua, uamethod

from ..errors import VisionErrorCode
from .uamethods import code_reply
from .wiring import Wiring

_log = logging.getLogger(__package__)


def _start_method(wiring: Wiring, name: str, start: Callable):
    """Handler fuer `StartSingleJob` bzw. `StartContinuous`.

    `start` ist `jobs.start_single_job` oder `jobs.start_continuous`; beide
    bleiben synchron und werden ohne `await`-Punkt aufgerufen, die Zulassung
    bleibt atomar.

    Gibt JobId und Error als String bzw. Int32 zurueck, obwohl das Nodeset
    `JobIdDataType` deklariert -- asyncua validiert Methodenargumente nicht,
    und ein Client koennte das ExtensionObject nicht dekodieren. Die
    Rueckgabe muss ein Tupel sein; eine Liste wuerde asyncua als einen
    einzigen Variant verpacken.
    """

    @uamethod
    async def method(parent, meas_id, part_id, recipe_id, product_id, parameters):
        if wiring.calibration_busy():
            _log.warning("%s waehrend laufender Kalibrierung/Auswertung abgelehnt", name)
            return (
                ua.Variant("", ua.VariantType.String),
                ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),
            )
        job_id, error = start(meas_id, part_id, recipe_id, product_id, parameters)
        return (
            ua.Variant(job_id, ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    return method


def _stop_method(wiring: Wiring):
    """Handler fuer `1:Stop`.

    `Cause`/`CauseDescription` schickt das Frontend fire-and-forget immer
    als 0/"" und wertet sie nicht aus; wir werten sie ebenfalls nicht aus.
    """

    @uamethod
    async def method(parent, cause, cause_description):
        # Erst den Lauf, dann den Job: sonst wertete der Lauf den
        # abgebrochenen Job noch aus und veroeffentlichte ein neues Ziel.
        if wiring.layer2_run is not None:
            await wiring.layer2_run.abort("Stop")
        error = await wiring.jobs.stop()
        return code_reply(error)

    return method


def _abort_method(wiring: Wiring):
    """Handler fuer `1:Abort` -- wie Stop, aber ueber den Abort-Uebergang.

    Fuer uns ist der Unterschied nur der Zustandsuebergang und die Meldung
    im Ergebnis: es gibt keinen Zwischenstand, den ein Abbruch verwerfen
    koennte. Beide melden `CANCELLED`.
    """

    @uamethod
    async def method(parent, cause, cause_description):
        error = await wiring.jobs.stop(abort=True)
        return code_reply(error)

    return method


def _halt_method(wiring: Wiring):
    """Handler fuer `1:Halt` -- laufenden Job beenden, dann keine Jobs mehr annehmen.

    Aus Halted fuehrt nur `Reset` zurueck. Das ist der Sinn: Halt ist die
    Bremse fuer den Bediener, nicht ein weiterer Betriebszustand.
    """

    @uamethod
    async def method(parent, cause, cause_description):
        error = await wiring.jobs.stop()
        if error != VisionErrorCode.OK:
            return code_reply(error)
        try:
            await wiring.states.halt()
        except Exception:
            _log.exception("Halt fehlgeschlagen")
            return code_reply(VisionErrorCode.INTERNAL)
        return code_reply(VisionErrorCode.OK)

    return method


def _reset_method(wiring: Wiring):
    """Handler fuer `1:Reset` -- zurueck in den betriebsbereiten Zustand.

    Ueber Preoperational, weil das Nodeset keinen Uebergang
    Halted -> Operational kennt; `enter_operational` faehrt genau diesen
    konformen Weg.
    """

    @uamethod
    async def method(parent, cause, cause_description):
        try:
            await wiring.states.enter_operational()
        except Exception:
            _log.exception("Reset fehlgeschlagen")
            return code_reply(VisionErrorCode.INTERNAL)
        return code_reply(VisionErrorCode.OK)

    return method


def install_job_methods(wiring: Wiring) -> None:
    """Haengt die sechs Job-Handler an die Methoden-Knoten des Nodesets."""
    space, jobs = wiring.space, wiring.jobs
    link = wiring.server.link_method
    link(space.start_single_job, _start_method(wiring, "StartSingleJob", jobs.start_single_job))
    link(space.stop, _stop_method(wiring))
    link(space.start_continuous, _start_method(wiring, "StartContinuous", jobs.start_continuous))
    link(space.abort, _abort_method(wiring))
    link(space.halt, _halt_method(wiring))
    link(space.reset, _reset_method(wiring))
