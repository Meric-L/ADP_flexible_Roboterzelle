"""Automatische Kinematik-Auswertung auf dem Pi (ohne asyncua testbar)."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

_log = logging.getLogger(__package__)

#: Sessions mit diesem Namensende werden nach jedem gespeicherten Bild ausgewertet.
KINEMATICS_SESSION_SUFFIX = "_kinematik"


class SessionRunCoalescer:
    """Je Session ein Lauf gleichzeitig; Anstoesse waehrend eines Laufs ergeben genau einen Folgelauf.

    `trigger(session)` kehrt sofort zurueck (kein await noetig, braucht laufende Event-Loop).
    `run(session)` ist eine blockierende Funktion und laeuft in `asyncio.to_thread`; Fehler
    werden nur geloggt. `wait_idle()` wartet auf alle Laeufe (fuer Tests).
    """

    def __init__(self, run: Callable[[str], Any]) -> None:
        self._run = run
        self._state: dict[str, bool] = {}  # Session -> dirty (Eintrag vorhanden = laeuft)
        self._tasks: set[asyncio.Task] = set()

    def trigger(self, session: str) -> None:
        if session in self._state:
            self._state[session] = True
            return
        self._state[session] = False
        task = asyncio.get_running_loop().create_task(self._loop(session))
        self._tasks.add(task)  # Referenz halten, sonst kann der Task eingesammelt werden
        task.add_done_callback(self._tasks.discard)

    async def _loop(self, session: str) -> None:
        try:
            while True:
                self._state[session] = False
                try:
                    await asyncio.to_thread(self._run, session)
                except Exception:
                    _log.exception("Kinematik-Auswertung fuer %s fehlgeschlagen", session)
                if not self._state[session]:
                    break
        finally:
            self._state.pop(session, None)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
