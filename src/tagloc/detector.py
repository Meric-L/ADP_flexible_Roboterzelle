"""Tag detection behind a protocol: cv2.aruco or pupil_apriltags.

The prototype used `pupil_apriltags`; the target architecture per
`vision_server/profiles.py` and doc/altlasten.md D3 is `cv2.aruco`. Rather
than decide and rewrite later, both sit behind the same interface. Pose
estimation deliberately lives in `pose.py`, not here -- otherwise pose
quality would depend on the backend and the two wouldn't be comparable.

`cv2` is imported only inside constructors, never at module level, so
importing this module stays cheap and the vision server can start without
OpenCV.
"""

import logging
from typing import Any, Protocol, runtime_checkable

import numpy as np

from .observations import TagObservation

_log = logging.getLogger(__name__)

#: Tag family -> name of the `cv2.aruco.DICT_*` constant. The string comes
#: from `AprilTagProfileConfig.tag_family` and is resolved only here, so the
#: config can stay a plain stdlib dataclass.
ARUCO_DICTIONARIES = {
    "tag36h11": "DICT_APRILTAG_36h11",
    "tag25h9": "DICT_APRILTAG_25h9",
    "tag16h5": "DICT_APRILTAG_16h5",
    "tagcircle21h7": "DICT_APRILTAG_16h5",
    "aruco_4x4_50": "DICT_4X4_50",
    "aruco_5x5_100": "DICT_5X5_100",
    "aruco_6x6_250": "DICT_6X6_250",
    "aruco_original": "DICT_ARUCO_ORIGINAL",
}


@runtime_checkable
class TagDetector(Protocol):
    """Find tags in a grayscale image."""

    family: str

    def detect(self, gray: Any) -> list[TagObservation]:
        """Return all found tags, or an empty list if none are present."""
        ...


def _as_corner_tuple(corners) -> tuple[tuple[float, float], ...]:
    array = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    return tuple((float(point[0]), float(point[1])) for point in array)


def predefined_dictionary(name: str):
    """Resolve a `DICT_*` name to the cv2 dictionary object.

    Bridges the new (`getPredefinedDictionary`) and old (`Dictionary_get`)
    API. Also needed outside the detector by anything that **generates**
    markers, e.g. `tagloc.synthetic` and `tools/make_tag_sheet.py`.
    """
    import cv2

    aruco = cv2.aruco
    if not hasattr(aruco, name):
        raise ValueError(
            f"Diese OpenCV-Version kennt '{name}' nicht "
            "-- opencv-contrib-python installieren oder eine andere Familie waehlen"
        )
    dictionary_id = getattr(aruco, name)
    if hasattr(aruco, "getPredefinedDictionary"):
        return aruco.getPredefinedDictionary(dictionary_id)
    return aruco.Dictionary_get(dictionary_id)  # pragma: no cover - OpenCV < 4.7


def generate_marker(dictionary, tag_id: int, pixels: int):
    """Rendert einen Marker als Graustufenbild, `pixels` breit und hoch.

    Wie `predefined_dictionary` eine Bruecke zwischen neuer
    (`generateImageMarker`) und alter API (`drawMarker`). Fuer alles, was
    Marker **erzeugt**: Druckbogen und synthetische Szenen.
    """
    import cv2

    aruco = cv2.aruco
    if hasattr(aruco, "generateImageMarker"):
        return aruco.generateImageMarker(dictionary, int(tag_id), int(pixels))
    return aruco.drawMarker(dictionary, int(tag_id), int(pixels))  # pragma: no cover - OpenCV < 4.7


def aruco_dictionary(family: str = "tag36h11"):
    """Same, but for a family name from the configuration."""
    name = ARUCO_DICTIONARIES.get(family)
    if name is None:
        known = ", ".join(sorted(ARUCO_DICTIONARIES))
        raise ValueError(f"Unbekannte Tag-Familie '{family}' (bekannt: {known})")
    return predefined_dictionary(name)


class ArucoTagDetector:
    """Detection via `cv2.aruco`. The default path.

    Bridges the new API (OpenCV >= 4.7: `ArucoDetector`,
    `getPredefinedDictionary`) and the old one (`Dictionary_get`,
    `detectMarkers` as a free function). Not a luxury: per doc/altlasten.md
    D2, local is OpenCV 5.x while the Pi runs 4.x -- without the bridge, the
    code would only work on one of the two.
    """

    def __init__(self, family: str = "tag36h11", *, parameters: Any = None) -> None:
        import cv2

        self.family = family
        aruco = cv2.aruco
        self._dictionary = aruco_dictionary(family)

        if parameters is not None:
            self._parameters = parameters
        elif hasattr(aruco, "DetectorParameters"):
            self._parameters = aruco.DetectorParameters()
        else:  # OpenCV < 4.7
            self._parameters = aruco.DetectorParameters_create()

        # Subpixel-accurate corners: without it, pose quality at distance
        # noticeably drops -- exactly the Layer-1 case.
        refine = getattr(cv2.aruco, "CORNER_REFINE_APRILTAG", None)
        if refine is not None:
            try:
                self._parameters.cornerRefinementMethod = refine
            except Exception:  # pragma: no cover - read-only depending on build
                _log.debug("cornerRefinementMethod nicht setzbar")

        self._detector = None
        if hasattr(aruco, "ArucoDetector"):
            self._detector = aruco.ArucoDetector(self._dictionary, self._parameters)
        self._aruco = aruco

    def detect(self, gray: Any) -> list[TagObservation]:
        if self._detector is not None:
            corners, ids, _ = self._detector.detectMarkers(gray)
        else:  # pragma: no cover - old OpenCV version
            corners, ids, _ = self._aruco.detectMarkers(
                gray, self._dictionary, parameters=self._parameters
            )
        if ids is None or len(ids) == 0:
            return []
        found = []
        for tag_id, corner in zip(np.asarray(ids).reshape(-1), corners):
            found.append(
                TagObservation(tag_id=int(tag_id), corners=_as_corner_tuple(corner))
            )
        return sorted(found, key=lambda observation: observation.tag_id)


class PupilAprilTagDetector:
    """Detection via `pupil_apriltags`. For comparison measurements.

    The library outputs corners counter-clockwise from bottom-left; here they
    are rotated into the canonical order (clockwise from top-left) so
    `pose.py` computes identically for both backends.
    """

    def __init__(self, family: str = "tag36h11", *, nthreads: int = 1) -> None:
        from pupil_apriltags import Detector

        self.family = family
        self._detector = Detector(families=family, nthreads=nthreads)

    def detect(self, gray: Any) -> list[TagObservation]:
        found = []
        for detection in self._detector.detect(gray, estimate_tag_pose=False):
            corners = list(_as_corner_tuple(detection.corners))
            corners.reverse()  # ccw from bottom-left -> cw from top-left
            found.append(
                TagObservation(
                    tag_id=int(detection.tag_id),
                    corners=tuple(corners),
                    decision_margin=float(getattr(detection, "decision_margin", 0.0)),
                )
            )
        return sorted(found, key=lambda observation: observation.tag_id)


def build_detector(family: str = "tag36h11", backend: str = "aruco") -> TagDetector:
    """Build the detector for the requested family.

    `backend="auto"` tries `cv2.aruco` first and falls back to
    `pupil_apriltags` if this OpenCV version doesn't know the family.
    """
    backend = backend.lower()
    if backend == "aruco":
        return ArucoTagDetector(family)
    if backend == "pupil":
        return PupilAprilTagDetector(family)
    if backend == "auto":
        try:
            return ArucoTagDetector(family)
        except Exception as error:
            _log.warning("cv2.aruco nicht nutzbar (%s), weiche auf pupil_apriltags aus", error)
            return PupilAprilTagDetector(family)
    raise ValueError(f"Unbekanntes Detektor-Backend '{backend}' (aruco, pupil, auto)")


__all__ = [
    "ARUCO_DICTIONARIES",
    "ArucoTagDetector",
    "PupilAprilTagDetector",
    "TagDetector",
    "TagObservation",
    "aruco_dictionary",
    "build_detector",
    "generate_marker",
    "predefined_dictionary",
]
