"""Error codes for the `Error` output of the 40100 methods."""

from enum import IntEnum


class VisionErrorCode(IntEnum):
    """Vendor-specific codes; the nodeset defines no enum for them."""

    OK = 0
    INVALID_STATE = 1
    INVALID_ARGUMENT = 2
    BUSY = 3
    UNKNOWN_RECIPE = 4
    DETECTION_FAILED = 5
    INTERNAL = 6
    CANCELLED = 7


class VisionJobError(Exception):
    """Error carrying a code for the `Error` output."""

    def __init__(self, code: VisionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
