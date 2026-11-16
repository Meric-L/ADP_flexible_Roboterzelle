"""Identity of the active configuration. Standard library only.

Kept separate because the vision server needs the `configurationId` while it
builds the detection source, before anything is loaded. numpy and OpenCV must
therefore not be on the import chain.

The value later shows which calibration and which map produced a bad pose
(doc/projektdoku/altlasten.md C4). It is the file name plus the mtime: both
are available without parsing, and the mtime changes on every recalibration.
"""

from pathlib import Path


def file_identity(path, missing: str = "fehlt") -> str:
    """Return ``"<name>#<mtime>"`` without opening the file.

    Args:
        path: Path to the file.
        missing: Placeholder used instead of the mtime if the file does not exist.

    Returns:
        The identity string.
    """
    path = Path(path)
    try:
        return f"{path.stem}#{int(path.stat().st_mtime)}"
    except OSError:
        return f"{path.stem}#{missing}"


def calibration_identity(path) -> str:
    """Return the identity of the calibration file."""
    return file_identity(path)


def tag_map_identity(path) -> str:
    """Return the identity of the tag map. ``None`` is valid: it means operation without a map."""
    if path is None:
        return "tagmap#keine"
    return file_identity(path)
