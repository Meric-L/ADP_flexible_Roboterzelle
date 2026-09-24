"""Erkennungsquelle, die ein externes Python-Script als Subprozess ausfuehrt."""

import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from ..errors import VisionErrorCode, VisionJobError
from .base import Detection, DetectionRequest, DetectionSource

_log = logging.getLogger(__name__)

#: Frist zwischen SIGTERM und SIGKILL bei einem Stop-Abbruch.
TERMINATE_GRACE_S = 2.0


class ScriptDetectionSource(DetectionSource):
    """Fuehrt ein Script aus und verpackt dessen Ausgabe als Pseudo-Detektion.

    Platzhalter fuer Kalibrierung und Bilderkennung: das ausgefuehrte Script
    traegt heute noch keine echte Logik, nur den Meldungstext auf stdout.
    Spaeter wird nur der Script-Inhalt durch die echte Kalibrierungs-/
    Erkennungslogik ersetzt, ohne dass sich diese Klasse oder der Job-Ablauf
    aendern muss.
    """

    #: Der Subprozess laeuft ohne echte Kamera; das Ergebnis ist ein Platzhalter.
    is_simulated = True
    frame_id = "world"

    def __init__(self, profile_id: str, script_path: Path) -> None:
        self.profile_id = profile_id
        self._script_path = script_path

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Startet das Script und liefert dessen stdout als Detektions-Nachricht.

        Der Subprozess blockiert den Event-Loop nicht und haelt die
        Abhaengigkeiten des Scripts aus dem Serverprozess heraus. Preis: kein
        Zustand ueber Jobs hinweg, und pro Job ein Prozessstart -- fuer eine
        Kamera, die offen bleiben muss, ist `run_blocking` der richtige Weg.
        """
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self._script_path),
            *(str(parameter) for parameter in request.parameters),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            # `Stop` bricht diesen `await` ab; ohne Terminate liefe das Script
            # als verwaister Subprozess weiter (z. B. mit offener Kamera).
            await self._terminate(process)
            raise
        if process.returncode != 0:
            raise VisionJobError(
                VisionErrorCode.DETECTION_FAILED,
                f"Script {self._script_path.name} beendete mit Code "
                f"{process.returncode}: {stderr.decode('utf-8', errors='replace').strip()}",
            )
        message = stdout.decode("utf-8", errors="replace").strip()
        return [
            Detection(
                module_id=self.profile_id.upper(),
                instance_id="det-1",
                position=(0.0, 0.0, 0.0),
                orientation=(0.0, 0.0, 0.0, 1.0),
                confidence=1.0,
                attributes={"message": message, "recipeId": request.recipe_id},
            )
        ]

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        """SIGTERM, nach `TERMINATE_GRACE_S` SIGKILL, falls es nicht kooperiert."""
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), TERMINATE_GRACE_S)
        except asyncio.TimeoutError:
            _log.warning(
                "Script %s reagierte nicht auf terminate(), kill()",
                self._script_path.name,
            )
            process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
