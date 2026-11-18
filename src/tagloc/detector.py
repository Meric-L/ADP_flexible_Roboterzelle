"""Tag detection behind one interface: cv2.aruco or pupil_apriltags.

The prototype used `pupil_apriltags`; the target architecture per
`vision_server/profiles.py` and doc/project_docs/work-plans/legacy.md D3 is `cv2.aruco`.
Instead of deciding now and rewriting later, both sit behind the same
interface. Pose estimation deliberately lives in `pose.py` and not here --
otherwise pose quality would depend on the backend, and the two would not be
comparable.

`cv2` is only imported in constructors, never at module level. Importing this
module stays cheap, and the vision server can start without OpenCV.
"""

import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Protocol, runtime_checkable

import numpy as np

from .observations import TagObservation
from .tagmap import DEFAULT_TAG_FAMILY

_log = logging.getLogger(__name__)

#: Tag family -> name of the `cv2.aruco.DICT_*` constant. The string comes from
#: `AprilTagProfileConfig.tag_family` and is only resolved here, so the
#: configuration can stay a plain stdlib dataclass.
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
        """Return all detected tags, or an empty list if none are found."""
        ...


def _as_corner_tuple(corners) -> tuple[tuple[float, float], ...]:
    array = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    return tuple((float(point[0]), float(point[1])) for point in array)


def predefined_dictionary(name: str):
    """Resolve a `DICT_*` name to the cv2 dictionary object.

    Requires OpenCV >= 4.8 (`getPredefinedDictionary`). Also needed outside the
    detector, for anything that **generates** markers, e.g.
    `tools/make_synthetic_scene.py`.
    """
    import cv2

    aruco = cv2.aruco
    if not hasattr(aruco, name):
        raise ValueError(
            f"Diese OpenCV-Version kennt '{name}' nicht "
            "-- opencv-contrib-python installieren oder eine andere Familie waehlen"
        )
    dictionary_id = getattr(aruco, name)
    return aruco.getPredefinedDictionary(dictionary_id)


def aruco_dictionary(family: str = DEFAULT_TAG_FAMILY):
    """Same as `predefined_dictionary`, for a family name from the configuration."""
    name = ARUCO_DICTIONARIES.get(family)
    if name is None:
        known = ", ".join(sorted(ARUCO_DICTIONARIES))
        raise ValueError(f"Unbekannte Tag-Familie '{family}' (bekannt: {known})")
    return predefined_dictionary(name)


def apply_detector_parameters(
    parameters: Any, values: Mapping[str, Any] | None
) -> dict[str, tuple[Any, Any]]:
    """Set `values` (attribute name -> value) on a `DetectorParameters` object.

    The value is converted to the type of the existing attribute (bool, int,
    float). Raises `ValueError` for an unknown name or a value that does not fit
    that type. Returns only the entries that differ from the previous value, as
    `{name: (before, after)}` -- for the start log.
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
    """Convert `value` to the type of `current`; raise `ValueError`/`TypeError` otherwise."""
    if isinstance(current, bool):  # before int: bool is an int
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


#: Corner refinement methods (`corner_refinement_method`).
CORNER_METHOD_APRILTAG = "apriltag"
CORNER_METHOD_SUBPIX = "subpix"
CORNER_METHOD_NONE = "none"
CORNER_METHODS = (CORNER_METHOD_APRILTAG, CORNER_METHOD_SUBPIX, CORNER_METHOD_NONE)
_CORNER_REFINE_CONSTANTS = {
    CORNER_METHOD_APRILTAG: "CORNER_REFINE_APRILTAG",
    CORNER_METHOD_SUBPIX: "CORNER_REFINE_SUBPIX",
}


class ArucoTagDetector:
    """Detection via `cv2.aruco`. The default path.

    Requires OpenCV >= 4.8 (`ArucoDetector`, `getPredefinedDictionary`); 4.x and
    5.x are supported, the Pis run OpenCV 5.0.
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
        """`detector_parameters`: attribute name of `cv2.aruco.DetectorParameters`
        -> value, e.g. `{"errorCorrectionRate": 0.8}`; `None`/empty = OpenCV
        default. An unknown name or an unfitting value raises `ValueError`.
        The entries are set AFTER `subpixel_corner_refinement`, so they win, e.g.
        an explicit `cornerRefinementMethod`.
        `corner_refinement_method` selects the method when refinement is active:
        `"apriltag"` (CORNER_REFINE_APRILTAG), `"subpix"` (CORNER_REFINE_SUBPIX)
        or `"none"`.
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
        else:
            self._parameters = aruco.DetectorParameters()

        # Subpixel-accurate corners: can improve pose quality at distance, but
        # measurably costs CPU time (py-spy on pi1: ~46% of the profile in
        # `_locate -> detect` on full 12 MP). Not needed for decodability
        # (history: `AprilTagProfileConfig.subpixel_corner_refinement`, default
        # off). The constructor default stays deliberately conservative (off);
        # callers should decide explicitly.
        if subpixel_corner_refinement and corner_refinement_method != CORNER_METHOD_NONE:
            refine = getattr(cv2.aruco, _CORNER_REFINE_CONSTANTS[corner_refinement_method], None)
            if refine is not None:
                try:
                    self._parameters.cornerRefinementMethod = refine
                except Exception:  # pragma: no cover - read-only in some builds
                    _log.debug("cornerRefinementMethod nicht setzbar")
            # `aprilTagQuadDecimate` (standard AprilTag parameter): lets the
            # refinement search run on a downscaled copy. Costs range and
            # robustness for very small or distant tags, but saves CPU time in
            # the job detection thread (py-spy on pi1: ~46% of the profile in
            # detect() with refinement on the full 12 MP frame). `0.0` = no
            # decimation; it only takes effect together with
            # `cornerRefinementMethod == CORNER_REFINE_APRILTAG`.
            if apriltag_quad_decimate:
                try:
                    self._parameters.aprilTagQuadDecimate = apriltag_quad_decimate
                except Exception:  # pragma: no cover - read-only in some builds
                    _log.debug("aprilTagQuadDecimate nicht setzbar")

        # User-selectable detector parameters (preset, environment variable, CLI).
        # Deliberately strict: a typo in the name should show at startup and not
        # silently have no effect.
        #: Entries that differ from the default, as `{name: (before, after)}`, for the log.
        self.changed_parameters = apply_detector_parameters(
            self._parameters, detector_parameters
        )

        self._detector = aruco.ArucoDetector(self._dictionary, self._parameters)

    def detect(self, gray: Any) -> list[TagObservation]:
        corners, ids, _ = self._detector.detectMarkers(gray)
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

    The library returns the corners counter-clockwise, starting bottom left;
    here they are rotated into the canonical order (clockwise, starting top
    left) so that `pose.py` computes the same result for both backends.
    """

    def __init__(self, family: str = DEFAULT_TAG_FAMILY, *, nthreads: int = 1) -> None:
        from pupil_apriltags import Detector

        self.family = family
        self._detector = Detector(families=family, nthreads=nthreads)

    def detect(self, gray: Any) -> list[TagObservation]:
        found = []
        for detection in self._detector.detect(gray, estimate_tag_pose=False):
            corners = list(_as_corner_tuple(detection.corners))
            corners.reverse()  # counter-clockwise from bottom left -> clockwise from top left
            found.append(
                TagObservation(
                    tag_id=int(detection.tag_id),
                    corners=tuple(corners),
                    decision_margin=float(getattr(detection, "decision_margin", 0.0)),
                )
            )
        return sorted(found, key=lambda observation: observation.tag_id)


class RoiTagDetector:
    """Detect only inside a fixed rectangle of the image (crop before detection).

    The image itself is not changed: only a view of the region is handed to the
    inner detector, and the corners are shifted back by the region's offset. For
    everything after the detector (pose, calibration, overlay) the corners stay in
    coordinates of the full image -- cropping the frame itself would move the
    principal point and invalidate the calibration.

    The region is given in pixels of `reference_size` (the configured camera
    resolution). For an image of a different size, e.g. the downscaled image the
    stream overlay detects on, it is scaled proportionally.
    """

    def __init__(
        self,
        inner: TagDetector,
        roi: tuple[int, int, int, int],
        reference_size: tuple[int, int],
    ) -> None:
        """Wrap `inner`.

        Args:
            inner: The detector that does the actual work.
            roi: `(x, y, width, height)` in pixels of `reference_size`.
            reference_size: `(width, height)` the region refers to.

        Raises:
            ValueError: If the region is empty or does not lie within `reference_size`.
        """
        x, y, width, height = (int(value) for value in roi)
        ref_width, ref_height = (int(value) for value in reference_size)
        if width <= 0 or height <= 0:
            raise ValueError(f"Detektions-Ausschnitt {roi} ist leer")
        if x < 0 or y < 0 or x + width > ref_width or y + height > ref_height:
            raise ValueError(
                f"Detektions-Ausschnitt {roi} (x, y, Breite, Hoehe) liegt nicht im Bild "
                f"{ref_width}x{ref_height}"
            )
        self._inner = inner
        self.family = inner.family
        self.roi = (x, y, width, height)
        self.reference_size = (ref_width, ref_height)
        #: Passed through, for the start log in `resources._build_detector`.
        self.changed_parameters = getattr(inner, "changed_parameters", {})

    def region_for(self, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        """Return `(x0, y0, x1, y1)` of the region for an image of this size."""
        from .frames import scaled_region

        return scaled_region(self.roi, self.reference_size, (image_width, image_height))

    def detect(self, gray: Any) -> list[TagObservation]:
        image = np.asarray(gray)
        x0, y0, x1, y1 = self.region_for(image.shape[1], image.shape[0])
        if x1 <= x0 or y1 <= y0:
            return []
        # Basic slicing returns a view, no copy of the 12 MP frame. OpenCV accepts
        # the non-contiguous view directly.
        found = self._inner.detect(image[y0:y1, x0:x1])
        return [
            replace(
                observation,
                corners=tuple((cx + x0, cy + y0) for cx, cy in observation.corners),
            )
            for observation in found
        ]


def build_detector(
    family: str = DEFAULT_TAG_FAMILY,
    backend: str = "aruco",
    *,
    subpixel_corner_refinement: bool = False,
    apriltag_quad_decimate: float = 0.0,
    detector_parameters: Mapping[str, Any] | None = None,
    corner_refinement_method: str = CORNER_METHOD_APRILTAG,
    roi: tuple[int, int, int, int] | None = None,
    roi_reference_size: tuple[int, int] | None = None,
) -> TagDetector:
    """Build the detector for the requested family.

    `backend="auto"` tries `cv2.aruco` first and falls back to
    `pupil_apriltags` if this OpenCV version does not know the family.

    `subpixel_corner_refinement`/`apriltag_quad_decimate` only apply to
    `"aruco"`/`"auto"` -- `pupil_apriltags` has no equivalent setting. The same
    holds for `detector_parameters` (attribute name of
    `cv2.aruco.DetectorParameters` -> value, `None` = OpenCV default).

    `roi` (`(x, y, width, height)` in pixels of `roi_reference_size`) restricts
    detection to that region, see `RoiTagDetector`. `None` = whole image.

    Raises:
        ValueError: Unknown backend, or `roi` without `roi_reference_size`, or a
            region outside the image.
    """
    detector = _build_plain_detector(
        family,
        backend,
        subpixel_corner_refinement=subpixel_corner_refinement,
        apriltag_quad_decimate=apriltag_quad_decimate,
        detector_parameters=detector_parameters,
        corner_refinement_method=corner_refinement_method,
    )
    if roi is None:
        return detector
    if roi_reference_size is None:
        raise ValueError("Detektions-Ausschnitt ohne Bezugsaufloesung (roi_reference_size)")
    return RoiTagDetector(detector, roi, roi_reference_size)


def _build_plain_detector(
    family: str,
    backend: str,
    *,
    subpixel_corner_refinement: bool,
    apriltag_quad_decimate: float,
    detector_parameters: Mapping[str, Any] | None,
    corner_refinement_method: str,
) -> TagDetector:
    """Build the detector without a region (see `build_detector`)."""
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
    "RoiTagDetector",
    "TagDetector",
    "TagObservation",
    "apply_detector_parameters",
    "aruco_dictionary",
    "build_detector",
    "predefined_dictionary",
]
