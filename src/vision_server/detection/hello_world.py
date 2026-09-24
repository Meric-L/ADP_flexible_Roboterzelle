"""Platzhalter-Erkennung ohne Bildverarbeitung."""

import asyncio

from ..errors import VisionErrorCode, VisionJobError
from .base import Detection, DetectionRequest, DetectionSource

FORCE_ERROR_PARAMETER = "force-error"
HELLO_WORLD_MESSAGE = "Hello World"


class HelloWorldDetectionSource(DetectionSource):
    """Liefert genau eine Pseudo-Detektion mit der Nachricht "Hello World".

    Enthalten die Parameter `force-error`, wird stattdessen ein
    VisionJobError(DETECTION_FAILED) geworfen — damit ist der Fehlerpfad des
    Servers deterministisch vorfuehrbar.
    """

    profile_id = "hello_world"
    is_simulated = True
    frame_id = "world"

    def __init__(self, latency: float = 0.25) -> None:
        self._latency = latency

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Wartet die konfigurierte Latenz ab und liefert die Platzhalter-Detektion."""
        await asyncio.sleep(self._latency)
        if FORCE_ERROR_PARAMETER in request.parameters:
            raise VisionJobError(
                VisionErrorCode.DETECTION_FAILED,
                f"Erkennung durch Parameter '{FORCE_ERROR_PARAMETER}' fehlgeschlagen",
            )
        return [
            Detection(
                module_id="HELLO-WORLD",
                instance_id="det-1",
                position=(0.0, 0.0, 0.0),
                orientation=(0.0, 0.0, 0.0, 1.0),
                confidence=1.0,
                # recipeId mitzugeben macht das Routing end-to-end sichtbar,
                # bevor es eine echte Erkennungsquelle gibt.
                attributes={
                    "message": HELLO_WORLD_MESSAGE,
                    "recipeId": request.recipe_id,
                },
            )
        ]
