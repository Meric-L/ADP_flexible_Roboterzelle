"""Kleine asyncio-Helfer, die mehrere Module gleich brauchen.

Nur Standardbibliothek: auch die reinen OPC-UA-Clients unter `tools/`
importieren von hier, ohne dabei Server-Code mitzuziehen.
"""

import asyncio
import contextlib
import signal
from collections.abc import Iterable


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
