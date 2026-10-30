"""Tag-Erkennung hinter einem Protokoll: cv2.aruco oder pupil_apriltags.

Der Prototyp nutzte `pupil_apriltags`; die Zielarchitektur laut
`vision_server/profiles.py` und doc/projektdoku/altlasten.md D3 ist `cv2.aruco`.
Statt jetzt zu entscheiden und spaeter umzuschreiben, stehen beide hinter
derselben Schnittstelle. Die Posenschaetzung liegt bewusst in `pose.py` und
nicht hier -- sonst haenge die Posenguete vom Backend ab, und die beiden
waeren nicht vergleichbar.

`cv2` wird nur in Konstruktoren importiert, nie auf Modulebene. Der Import
dieses Moduls bleibt damit billig, und der Vision-Server kann ohne OpenCV
starten.
"""

import logging
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

import numpy as np

from .observations import TagObservation
from .tagmap import DEFAULT_TAG_FAMILY

_log = logging.getLogger(__name__)

#: Tag-Familie -> Name der `cv2.aruco.DICT_*`-Konstante. Die Zeichenkette
#: kommt aus `AprilTagProfileConfig.tag_family` und wird nur hier aufgeloest,
#: damit die Konfiguration eine schlichte Stdlib-Dataclass bleiben kann.
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
    """Findet Tags in einem Graustufenbild."""

    family: str

    def detect(self, gray: Any) -> list[TagObservation]:
        """Liefert alle gefundenen Tags, oder eine leere Liste, wenn keiner da ist."""
        ...


def _as_corner_tuple(corners) -> tuple[tuple[float, float], ...]:
    array = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    return tuple((float(point[0]), float(point[1])) for point in array)


def predefined_dictionary(name: str):
    """Loest einen `DICT_*`-Namen zum cv2-Dictionary-Objekt auf.

    Ueberbrueckt die neue (`getPredefinedDictionary`) und die alte
    (`Dictionary_get`) API. Auch ausserhalb des Detektors noetig, fuer alles,
    was Marker **erzeugt**, z. B. `tools/make_synthetic_scene.py`.
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


def aruco_dictionary(family: str = DEFAULT_TAG_FAMILY):
    """Dasselbe fuer einen Familiennamen aus der Konfiguration."""
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


#: Verfahren der Eckenverfeinerung (`corner_refinement_method`).
CORNER_METHOD_APRILTAG = "apriltag"
CORNER_METHOD_SUBPIX = "subpix"
CORNER_METHOD_NONE = "none"
CORNER_METHODS = (CORNER_METHOD_APRILTAG, CORNER_METHOD_SUBPIX, CORNER_METHOD_NONE)
_CORNER_REFINE_CONSTANTS = {
    CORNER_METHOD_APRILTAG: "CORNER_REFINE_APRILTAG",
    CORNER_METHOD_SUBPIX: "CORNER_REFINE_SUBPIX",
}


class ArucoTagDetector:
    """Erkennung ueber `cv2.aruco`. Der Standardweg.

    Ueberbrueckt die neue API (OpenCV >= 4.7: `ArucoDetector`,
    `getPredefinedDictionary`) und die alte (`Dictionary_get`, `detectMarkers`
    als freie Funktion). Das ist kein Luxus: laut doc/projektdoku/altlasten.md
    D2 laeuft lokal OpenCV 5.x, auf dem Pi 4.x -- ohne die Bruecke
    funktionierte der Code nur auf einem von beiden.
    """

    def __init__(
        self,
        family: str = DEFAULT_TAG_FAMILY,
        *,
        parameters: Any = None,
        subpixel_corner_refinement: bool = False,
        apriltag_quad_decimate: float = 0.0,
        detector_parameters: Mapping[str, Any] | None = None,
        corner_refinement_method: str = CORNER_METHOD_APRILTAG,
    ) -> None:
        """`detector_parameters`: Attributname von `cv2.aruco.DetectorParameters`
        -> Wert, z. B. `{"errorCorrectionRate": 0.8}`; `None`/leer = OpenCV-
        Standard. Unbekannter Name oder unpassender Wert wirft `ValueError`.
        Die Eintraege werden NACH `subpixel_corner_refinement` gesetzt und
        gewinnen damit, z. B. ein ausdrueckliches `cornerRefinementMethod`.
        `corner_refinement_method` waehlt bei aktiver Verfeinerung das
        Verfahren: `"apriltag"` (CORNER_REFINE_APRILTAG), `"subpix"`
        (CORNER_REFINE_SUBPIX) oder `"none"`.
        """
        import cv2

        if corner_refinement_method not in CORNER_METHODS:
            raise ValueError(
                f"Unbekannte Eckenverfeinerung '{corner_refinement_method}' "
                f"(erwartet eines von {', '.join(CORNER_METHODS)})"
            )
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
        if subpixel_corner_refinement and corner_refinement_method != CORNER_METHOD_NONE:
            refine = getattr(cv2.aruco, _CORNER_REFINE_CONSTANTS[corner_refinement_method], None)
            if refine is not None:
                try:
                    self._parameters.cornerRefinementMethod = refine
                except Exception:  # pragma: no cover - je nach Build schreibgeschuetzt
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
                except Exception:  # pragma: no cover - je nach Build schreibgeschuetzt
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
        else:  # pragma: no cover - alte OpenCV-Version
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
    """Erkennung ueber `pupil_apriltags`. Fuer Vergleichsmessungen.

    Die Bibliothek liefert die Ecken gegen den Uhrzeigersinn, unten links
    zuerst; hier werden sie in die kanonische Reihenfolge gedreht (im
    Uhrzeigersinn, oben links zuerst), damit `pose.py` fuer beide Backends
    gleich rechnet.
    """

    def __init__(self, family: str = DEFAULT_TAG_FAMILY, *, nthreads: int = 1) -> None:
        from pupil_apriltags import Detector

        self.family = family
        self._detector = Detector(families=family, nthreads=nthreads)

    def detect(self, gray: Any) -> list[TagObservation]:
        found = []
        for detection in self._detector.detect(gray, estimate_tag_pose=False):
            corners = list(_as_corner_tuple(detection.corners))
            corners.reverse()  # gegen den Uhrzeigersinn ab unten links -> im Uhrzeigersinn ab oben links
            found.append(
                TagObservation(
                    tag_id=int(detection.tag_id),
                    corners=tuple(corners),
                    decision_margin=float(getattr(detection, "decision_margin", 0.0)),
                )
            )
        return sorted(found, key=lambda observation: observation.tag_id)


def build_detector(
    family: str = DEFAULT_TAG_FAMILY,
    backend: str = "aruco",
    *,
    subpixel_corner_refinement: bool = False,
    apriltag_quad_decimate: float = 0.0,
    detector_parameters: Mapping[str, Any] | None = None,
    corner_refinement_method: str = CORNER_METHOD_APRILTAG,
) -> TagDetector:
    """Baut den Detektor fuer die gewuenschte Familie.

    `backend="auto"` versucht zuerst `cv2.aruco` und weicht auf
    `pupil_apriltags` aus, wenn diese OpenCV-Version die Familie nicht kennt.

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
        corner_refinement_method=corner_refinement_method,
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
    "apply_detector_parameters",
    "aruco_dictionary",
    "build_detector",
    "predefined_dictionary",
]
