"""Strategie-Schnittstelle der Erkennungsstufe (Teil 4.5 des Plans)."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Detection:
    """Ein erkanntes Modul mit Pose in Metern und Quaternion xyzw."""

    module_id: str
    instance_id: str
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    confidence: float
    attributes: dict[str, Any] = field(default_factory=dict)


class DetectionSource(ABC):
    """Liefert Detektionen fuer einen Job; kennt keine OPC-UA-Details."""

    profile_id: str

    @abstractmethod
    async def acquire_and_detect(self, parameters: Sequence[Any]) -> list[Detection]:
        """Fuehrt Aufnahme und Erkennung aus; wirft VisionJobError bei Erkennungsfehlern."""
