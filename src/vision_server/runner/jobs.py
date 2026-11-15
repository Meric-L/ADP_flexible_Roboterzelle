"""Job methods of the vision system: StartSingleJob, Stop, StartContinuous, Abort, Halt, Reset.

The nodes come from the nodeset (`address_space.py`); here only handlers are
attached to them (`link_method`). All of them are `async`: synchronous handlers
run in asyncua's ThreadPoolExecutor without a running event loop, where starting
the job task fails.
"""

import logging
from collections.abc import Callable

from asyncua import ua, uamethod

from ..errors import VisionErrorCode
from .uamethods import code_reply
from .wiring import Wiring

_log = logging.getLogger(__package__)


def _start_method(wiring: Wiring, name: str, start: Callable):
    """Handler for `StartSingleJob` and `StartContinuous`.

    `start` is `jobs.start_single_job` or `jobs.start_continuous`; both stay
    synchronous and are called without an `await` point, so the admission stays
    atomic.

    Returns JobId and Error as String and Int32, although the nodeset declares
    `JobIdDataType` -- asyncua does not validate method arguments, and a client
    could not decode the ExtensionObject. The return value must be a tuple; a
    list would be packed by asyncua as a single Variant.
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
    """Handler for `1:Stop`.

    The frontend always sends `Cause`/`CauseDescription` fire-and-forget as
    0/"" and does not evaluate them; we do not evaluate them either.
    """

    @uamethod
    async def method(parent, cause, cause_description):
        # First the run, then the job: otherwise the run would still evaluate
        # the aborted job and publish a new target.
        if wiring.layer2_run is not None:
            await wiring.layer2_run.abort("Stop")
        error = await wiring.jobs.stop()
        return code_reply(error)

    return method


def _abort_method(wiring: Wiring):
    """Handler for `1:Abort` -- like Stop, but through the abort transition.

    For us the only difference is the state transition and the message in the
    result: there is no intermediate state that an abort could discard. Both
    report `CANCELLED`.
    """

    @uamethod
    async def method(parent, cause, cause_description):
        error = await wiring.jobs.stop(abort=True)
        return code_reply(error)

    return method


def _halt_method(wiring: Wiring):
    """Handler for `1:Halt` -- stop the running job, then accept no more jobs.

    Only `Reset` leads back from Halted. That is the point: Halt is the brake for
    the operator, not another operating state.
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
    """Handler for `1:Reset` -- back to the operational state.

    Goes via Preoperational, because the nodeset has no transition
    Halted -> Operational; `enter_operational` takes exactly this conformant path.
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
    """Attach the six job handlers to the method nodes of the nodeset."""
    space, jobs = wiring.space, wiring.jobs
    link = wiring.server.link_method
    link(space.start_single_job, _start_method(wiring, "StartSingleJob", jobs.start_single_job))
    link(space.stop, _stop_method(wiring))
    link(space.start_continuous, _start_method(wiring, "StartContinuous", jobs.start_continuous))
    link(space.abort, _abort_method(wiring))
    link(space.halt, _halt_method(wiring))
    link(space.reset, _reset_method(wiring))
