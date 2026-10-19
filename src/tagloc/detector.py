"""Tag detection behind a protocol: cv2.aruco or pupil_apriltags.

The prototype used `pupil_apriltags`; the target architecture per
`vision_server/profiles.py` and doc/projektdoku/altlasten.md D3 is `cv2.aruco`. Rather
than decide and rewrite later, both sit behind the same interface. Pose
estimation deliberately lives in `pose.py`, not here -- otherwise pose
quality would depend on the backend and the two wouldn't be comparable.

`cv2` is imported only inside constructors, never at module level, so
importing this module stays cheap and the vision server can start without
OpenCV.
"""

import logging
from collections.abc import Mapping
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
    markers, e.g. `tools/make_synthetic_scene.py`.
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


def aruco_dictionary(family: str = "tag36h11"):
    """Same, but for a family name from the configuration."""
    name = ARUCO_DICTIONARIES.get(family)
    if name is None:
        known = ", ".join(sorted(ARUCO_DICTIONARIES))
        raise ValueError(f"Unbekannte Tag-Familie '{family}' (bekannt: {known})")
    return predefined_dictionary(name)


def apply_detector_parameters(
    parameters: Any, values: Mapping[str, Any] | None
) -> dict[str, tuple[Any, Any]]:
    """Setzt `values` (Attributname -> Wert) auf ein `DetectorParameters`-Objekt.

    Der Wert wird auf den Typ des vorhandenen Attributs gebracht (bool, int,
    float). `ValueError` bei unbekanntem Namen oder einem Wert, der nicht in
    diesen Typ passt. Rueckgabe: nur die Eintraege, die vom bisherigen Wert
    abweichen, als `{name: (vorher, nachher)}` -- fuers Start-Log.
    """
    changed: dict[str, tuple[Any, Any]] = {}
    for name, value in (values or {}).items():
        if not hasattr(parameters, name):
            raise ValueError(
                f"Unbekannter Detektor-Parameter '{name}' -- erwartet wird ein Attribut von "
                "cv2.aruco.DetectorParameters (z. B. errorCorrectionRate), Gross-/"
                "Kleinschreibung beachten"
            )
        current = getattr(parameters, name)
        try:
            converted = _as_type_of(current, value)
            setattr(parameters, name, converted)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Detektor-Parameter '{name}': Wert {value!r} passt nicht zum Typ "
                f"{type(current).__name__} ({error})"
            ) from error
        if converted != current:
            changed[name] = (current, converted)
    return changed


def _as_type_of(current: Any, value: Any) -> Any:
    """Bringt `value` auf den Typ von `current`; `ValueError`/`TypeError` sonst."""
    if isinstance(current, bool):  # vor int: bool ist ein int
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise ValueError("erwartet true/false")
    if isinstance(value, bool):
        raise ValueError("true/false statt einer Zahl")
    if isinstance(current, int):
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("erwartet eine ganze Zahl")
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value


class ArucoTagDetector:
    """Detection via `cv2.aruco`. The default path.

    Bridges the new API (OpenCV >= 4.7: `ArucoDetector`,
    `getPredefinedDictionary`) and the old one (`Dictionary_get`,
    `detectMarkers` as a free function). Not a luxury: per doc/projektdoku/altlasten.md
    D2, local is OpenCV 5.x while the Pi runs 4.x -- without the bridge, the
    code would only work on one of the two.
    """

    def __init__(
        self,
        family: str = "tag36h11",
        *,
        parameters: Any = None,
        subpixel_corner_refinement: bool = False,
        apriltag_quad_decimate: float = 0.0,
        detector_parameters: Mapping[str, Any] | None = None,
    ) -> None:
        """`detector_parameters`: Attributname von `cv2.aruco.DetectorParameters`
        -> Wert, z. B. `{"errorCorrectionRate": 0.8}`; `None`/leer = OpenCV-
        Standard. Unbekannter Name oder unpassender Wert wirft `ValueError`.
        Die Eintraege werden NACH `subpixel_corner_refinement` gesetzt und
        gewinnen damit, z. B. ein ausdrueckliches `cornerRefinementMethod`.
        """
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

        # Subpixel-accurate corners: kann die Pose-Qualitaet auf Distanz
        # verbessern, kostet aber messbar CPU-Zeit (py-spy auf pi1: ~46% des
        # Profils in `_locate -> detect` auf vollen 12 MP). Fuer
        # Dekodierbarkeit nicht noetig (Historie siehe
        # `AprilTagProfileConfig.subpixel_corner_refinement`, Default aus).
        # Konstruktor-Default bleibt bewusst konservativ (aus), Aufrufer
        # sollen explizit entscheiden.
        if subpixel_corner_refinement:
            refine = getattr(cv2.aruco, "CORNER_REFINE_APRILTAG", None)
            if refine is not None:
                try:
                    self._parameters.cornerRefinementMethod = refine
                except Exception:  # pragma: no cover - read-only depending on build
                    _log.debug("cornerRefinementMethod nicht setzbar")
            # `aprilTagQuadDecimate` (Standard-AprilTag-Parameter): laesst die
            # Verfeinerungssuche auf einer verkleinerten Kopie laufen --
            # kostet Reichweite/Robustheit bei sehr kleinen/entfernten Tags,
            # spart aber CPU-Zeit im Job-Erkennungs-Thread (py-spy auf pi1:
            # ~46% des Profils in detect() mit Verfeinerung auf vollem
            # 12-MP-Frame). `0.0` = keine Dezimierung, wirkt nur zusammen mit
            # `cornerRefinementMethod == CORNER_REFINE_APRILTAG`.
            if apriltag_quad_decimate:
                try:
                    self._parameters.aprilTagQuadDecimate = apriltag_quad_decimate
                except Exception:  # pragma: no cover - read-only depending on build
                    _log.debug("aprilTagQuadDecimate nicht setzbar")

        # Frei waehlbare Detektor-Parameter (Preset, Umgebungsvariable, CLI).
        # Bewusst streng: ein Tippfehler im Namen soll beim Start auffallen
        # und nicht stumm wirkungslos bleiben.
        #: Vom Standard abweichende Eintraege `{name: (vorher, nachher)}`, fuers Log.
        self.changed_parameters = apply_detector_parameters(
            self._parameters, detector_parameters
        )

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


def build_detector(
    family: str = "tag36h11",
    backend: str = "aruco",
    *,
    subpixel_corner_refinement: bool = False,
    apriltag_quad_decimate: float = 0.0,
    detector_parameters: Mapping[str, Any] | None = None,
) -> TagDetector:
    """Build the detector for the requested family.

    `backend="auto"` tries `cv2.aruco` first and falls back to
    `pupil_apriltags` if this OpenCV version doesn't know the family.

    `subpixel_corner_refinement`/`apriltag_quad_decimate` gelten nur fuer
    `"aruco"`/`"auto"` -- `pupil_apriltags` hat keine aequivalente Einstellung.
    Dasselbe gilt fuer `detector_parameters` (Attributname von
    `cv2.aruco.DetectorParameters` -> Wert, `None` = OpenCV-Standard).
    """
    backend = backend.lower()
    aruco_options = dict(
        subpixel_corner_refinement=subpixel_corner_refinement,
        apriltag_quad_decimate=apriltag_quad_decimate,
        detector_parameters=detector_parameters,
    )
    if backend == "aruco":
        return ArucoTagDetector(family, **aruco_options)
    if backend == "pupil":
        return PupilAprilTagDetector(family)
    if backend == "auto":
        try:
            return ArucoTagDetector(family, **aruco_options)
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
    "predefined_dictionary",
]
