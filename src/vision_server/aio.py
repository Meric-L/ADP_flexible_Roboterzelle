"""Kleine asyncio-Helfer, die mehrere Module gleich brauchen.

Nur Standardbibliothek: auch die reinen OPC-UA-Clients unter `tools/`
importieren von hier, ohne dabei Server-Code mitzuziehen.
"""

import asyncio
import contextlib
import functools
import signal
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any


async def cancel_and_wait(task: asyncio.Task | None) -> None:
    """Bricht einen Hintergrund-Task ab und wartet, bis er wirklich steht.

    `None` ist erlaubt und tut nichts -- so bleibt der Aufrufer ohne eigene
    Pruefung idempotent. Die `CancelledError` des Tasks wird geschluckt; ein
    Fehler, mit dem der Task vorher schon geendet hat, nicht.
    """
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def stop_event_on_signals(
    signals: Iterable[signal.Signals] = (signal.SIGTERM, signal.SIGINT),
) -> asyncio.Event:
    """Ein Event, das beim ersten der `signals` gesetzt wird.

    Strg+C waehrend eines `await` faengt `asyncio.run()` sonst **vor** der
    Coroutine ab -- ein `except KeyboardInterrupt` darin wird nie erreicht,
    und unter systemd kommt SIGTERM ohnehin nie als Ausnahme an. Ein echter
    Signal-Handler setzt stattdessen nur das Event, auf das der Aufrufer
    geordnet reagiert.

    Muss im laufenden Event-Loop aufgerufen werden. Wo der Loop keine
    Signal-Handler kann (z. B. Windows), bleibt es beim harten Abbruch.
    """
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in signals:
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    return stop


class SerialExecutor:
    """Ein einzelner, bei Bedarf gestarteter Worker-Thread fuer blockierende Arbeit.

    Eigener Ein-Worker-Pool statt `asyncio.to_thread`, weil letzterer den
    Default-Executor teilt und nichts serialisiert -- Kamera- und
    OpenCV-Zugriffe desselben Besitzers laufen hier strikt nacheinander. Ein
    haengender Aufruf blockiert diesen Besitzer dauerhaft; wer das nicht
    hinnehmen kann, braucht zusaetzlich eigene Timeouts.

    Nach `shutdown()` wieder verwendbar: der naechste `run` startet einen
    frischen Thread.
    """

    def __init__(self, thread_name_prefix: str) -> None:
        self._thread_name_prefix = thread_name_prefix
        self._pool: ThreadPoolExecutor | None = None

    async def run(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Fuehrt `func(*args, **kwargs)` im Worker aus und wartet darauf."""
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=self._thread_name_prefix
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, functools.partial(func, *args, **kwargs))

    def shutdown(self) -> None:
        """Gibt den Thread frei, ohne auf einen laufenden Aufruf zu warten;
        noch wartende Aufrufe werden verworfen. Idempotent."""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
