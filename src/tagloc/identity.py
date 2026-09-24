"""Identity of the effective configuration. Stdlib only.

Split out because the vision server needs the `configurationId` while still
building the detection source -- before anything is loaded, so numpy and
OpenCV must not be on the import chain.

The value later answers which calibration and which map produced a bad pose
(doc/projektdoku/altlasten.md C4). Filename plus mtime: both come for free without
parsing, and change on every recalibration.
"""

from pathlib import Path


def file_identity(path, missing: str = "fehlt") -> str:
    """Return `"<name>#<mtime>"` without opening the file."""
    path = Path(path)
    try:
        return f"{path.stem}#{int(path.stat().st_mtime)}"
    except OSError:
        return f"{path.stem}#{missing}"


def calibration_identity(path) -> str:
    """Return the calibration file's identity."""
    return file_identity(path)


def tag_map_identity(path) -> str:
    """Return the tag map's identity. `None` is valid -- operation without a map."""
    if path is None:
        return "tagmap#keine"
    return file_identity(path)
