"""Strategie-Schnittstelle der Erkennungsstufe (Teil 4.5 des Plans)."""

import asyncio
import functools
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    """Ein erkanntes Modul mit Pose in Metern und Quaternion xyzw."""

    module_id: str
    instance_id: str
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    confidence: float
    attributes: dict[str, Any] = field(default_factory=dict)
    bounding_box: tuple[float, ...] | None = None  # 2D-Profil, Teil 4.5


@dataclass(frozen=True)
class DetectionRequest:
    """Job-Eingaben fuer eine Erkennungsquelle.

    Objekt statt Positionsargumenten, damit ein neues Feld keine brechende
    Aenderung ueber alle Quellen ist.
    """

    job_id: str
    recipe_id: str = ""
    parameters: tuple[Any, ...] = ()
    meas_id: str | None = None
    part_id: str | None = None
    product_id: str | None = None
    deadline: float | None = None


class DetectionSource(ABC):
    """Liefert Detektionen fuer einen Job; kennt keine OPC-UA-Details.

    Darf zustandsbehaftet sein — die Instanz lebt die ganze Prozesslaufzeit,
    eine Kamera bleibt also ueber Jobs hinweg offen.
    """

    profile_id: str

    #: Steuert `IsSimulated`. Eine echte Kameraquelle setzt `False`.
    is_simulated: bool = True

    #: Bezugsrahmen der Posen; `""` = Wert aus der Serverkonfiguration.
    frame_id: str = ""

    #: Wohin +Z zeigt, z. B. "z_forward_x_right_y_down".
    frame_convention: str = ""

    #: Identitaet der wirksamen Konfiguration, z. B. Kalibrierdatei plus mtime.
    configuration_id: str = ""

    _executor: ThreadPoolExecutor | None = None

    @abstractmethod
    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Fuehrt Aufnahme und Erkennung aus; wirft VisionJobError bei Erkennungsfehlern."""

    async def open(self) -> None:
        """Belegt Betriebsmittel. Idempotent.

        Laeuft vor dem Uebergang nach Operational; wirft sie, bleibt der Automat
        in Preoperational und Jobs bekommen INVALID_STATE.
        """

    async def close(self) -> None:
        """Gibt Betriebsmittel frei. Idempotent, auch nach fehlgeschlagenem `open()`."""
        await self.shutdown_executor()

    async def run_blocking(self, func, /, *args) -> Any:
        """Blockierende Arbeit im eigenen Thread. **Jede** OpenCV-Operation hierdurch.

        Direkt auf dem Loop friert es Automaten, Events und die 1-Hz-Schleife
        des Zellenservers ein. Eigener Ein-Worker-Pool statt `asyncio.to_thread`,
        weil letzterer den Default-Executor teilt und Kamerazugriffe nicht
        serialisiert. Ein haengender Worker blockiert dieses Profil dauerhaft —
        eine Quelle braucht zusaetzlich eigene Aufnahme-Timeouts.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool(), functools.partial(func, *args))

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"vision-{self.profile_id}"
            )
        return self._executor

    async def shutdown_executor(self) -> None:
        """Faehrt den Worker-Thread herunter, falls einer existiert."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
