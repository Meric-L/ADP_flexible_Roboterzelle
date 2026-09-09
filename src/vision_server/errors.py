"""Fehlercodes fuer den `Error`-Ausgang der 40100-Methoden."""

from enum import IntEnum


class VisionErrorCode(IntEnum):
    """Herstellerspezifische Codes; das Nodeset definiert dafuer keinen Enum."""

    OK = 0
    INVALID_STATE = 1
    INVALID_ARGUMENT = 2
    BUSY = 3
    UNKNOWN_RECIPE = 4
    DETECTION_FAILED = 5
    INTERNAL = 6


class VisionJobError(Exception):
    """Fehler mit einem Code fuer den `Error`-Ausgang."""

    def __init__(self, code: VisionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
