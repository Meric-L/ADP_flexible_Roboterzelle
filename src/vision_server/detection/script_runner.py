"""Erkennungsquelle, die ein externes Python-Script als Subprozess ausfuehrt."""

import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..errors import VisionErrorCode, VisionJobError
from .base import Detection, DetectionSource


class ScriptDetectionSource(DetectionSource):
    """Fuehrt ein Script aus und verpackt dessen Ausgabe als Pseudo-Detektion.

    Platzhalter fuer Kalibrierung und Bilderkennung: das ausgefuehrte Script
    traegt heute noch keine echte Logik, nur den Meldungstext auf stdout.
    Spaeter wird nur der Script-Inhalt durch die echte Kalibrierungs-/
    Erkennungslogik ersetzt, ohne dass sich diese Klasse oder der Job-Ablauf
    aendern muss.
    """

    def __init__(self, profile_id: str, script_path: Path) -> None:
        self.profile_id = profile_id
        self._script_path = script_path

    async def acquire_and_detect(self, parameters: Sequence[Any]) -> list[Detection]:
        """Startet das Script und liefert dessen stdout als Detektions-Nachricht."""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(self._script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
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
                attributes={"message": message},
            )
        ]
