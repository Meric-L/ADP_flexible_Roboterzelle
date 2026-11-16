"""Small asyncio helpers that several modules need.

Standard library only: importing from here pulls in neither asyncua nor
server code.
"""

import asyncio
import contextlib
import signal
from collections.abc import Iterable


async def cancel_and_wait(task: asyncio.Task | None) -> None:
    """Cancel a background task and wait until it has really stopped.

    ``None`` is allowed and does nothing, so callers stay idempotent without
    their own check. The task's ``CancelledError`` is swallowed. An error the
    task had already raised before is not.
    """
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def stop_event_on_signals(
    signals: Iterable[signal.Signals] = (signal.SIGTERM, signal.SIGINT),
) -> asyncio.Event:
    """Return an event that is set on the first of the ``signals``.

    Otherwise ``asyncio.run()`` catches Ctrl+C during an ``await`` **before**
    the coroutine sees it. An ``except KeyboardInterrupt`` inside the coroutine
    is then never reached, and under systemd SIGTERM never arrives as an
    exception anyway. A real signal handler only sets the event instead, and
    the caller reacts to it in an orderly way.

    Must be called inside the running event loop. Where the loop cannot install
    signal handlers (e.g. Windows), the hard abort remains.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in signals:
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    return stop
