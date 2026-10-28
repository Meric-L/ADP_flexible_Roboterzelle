"""AprilTag detection over the camera shared with the livestream.

One source for both cameras. The ceiling camera and the eye-in-hand camera
use the same class and code; they differ only in the `AprilTagProfileConfig`
that `vision_server/server.py` sets per Pi, and in what they get to see:

* if world tags from the map are in the image, the camera pose is derived
  from them, and every module pose is reported in the world frame together
  with the world tag it sits closest to
* otherwise everything stays in the camera frame, and `frameConvention`
  tells the reader where +Z points

**Only the world tags are fixed** -- four of them on the border of the cell.
The robot is a module like any other: it carries its own tags and gets
measured, not read from a file. The ceiling camera is the only one that sees
the robot and the world tags at the same time, so it is the one that answers
which world tag the robot stands closest to. The eye-in-hand camera then
localises itself against that tag optically -- no hand-eye calibration
needed for that step.

The actual computation lives in `tagloc`; this file is the OPC-UA adapter.
"""

import asyncio
import functools
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from ..camera import SharedCamera
from ..errors import VisionErrorCode, VisionJobError
from ..profiles import AprilTagProfileConfig, CameraStreamConfig
from .base import Detection, DetectionRequest, DetectionSource

_log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.02
#: `MeasureBoard` sucht das Brett auf einem Bild dieser laengsten Kante (siehe `_observe_board`).
OBSERVE_MAX_SIDE = 640

#: Frame id -> which of the two cameras this is. Mirrors the frames that
#: `vision_server.server.PI_IDENTITIES` assigns per Pi. Deliberately plain
#: strings and not `tagloc.localize.SOURCE_*`: this module must stay
#: importable without numpy, so `tagloc` is only imported inside methods.
#: An unknown frame yields an empty source, which `merge_locations` then
#: ranks below both known cameras instead of guessing.
SOURCE_BY_FRAME = {"cam_ceiling": "ceiling", "cam_flange": "flange"}


@dataclass(frozen=True)
class TagSnapshot:
    """Die Tags eines Bilds in voller Kameraaufloesung -- fuers Livestream-Overlay.

    Der Livestream zeigt ein verkleinertes Bild, soll aber zeigen, was die
    Erkennung auf dem vollen Frame findet. Erkennt er dafuer selbst im
    Stream-Takt, frisst das am Pi eine Core (py-spy, 24.09.2026). Deshalb
    haelt die Quelle hier das neueste Ergebnis fest, das Job, Zellen-
    Ueberwachung oder Messung ohnehin berechnet haben, und das Overlay
    zeichnet es nur (`stream_overlay.py`). Die Ecken stehen in Pixeln von
    `image_size`; der Zeichner rechnet sie auf die Stream-Groesse um.
    """

    tag_poses: tuple
    image_size: tuple[int, int]
    #: "job" | "cellwatch" | "measure" | "overlay"
    origin: str
    #: `time.monotonic()` nach der Erkennung.
    taken_at: float


def robot_pose_from_parameters(parameters) -> Any | None:
    """Return `T_base_flansch` from the job parameters, or `None`.

    Sieben Floats: `(x, y, z, qx, qy, qz, qw)` -- Meter und Quaternion xyzw wie
    ueberall im Projekt. Der Roboter steht waehrend der Aufnahmen still, also
    gilt eine Pose fuer den ganzen Job; ein Zeitstempel-Abgleich mit dem
    einzelnen Frame eruebrigt sich damit.

    Fehlt der Parameter oder ist er unbrauchbar, kommt `None` zurueck und der
    Anker bleibt ungenutzt. Es wird nicht geraten -- eine falsche Roboterpose
    verschiebt jede Modulpose, ohne dass man es dem Ergebnis ansieht.
    """
    if not parameters or len(parameters) < 7:
        return None
    try:
        values = [float(value) for value in parameters[:7]]
    except (TypeError, ValueError):
        _log.warning("Roboterpose im Job ist nicht numerisch: %r", parameters[:7])
        return None
    if not all(math.isfinite(value) for value in values):
        _log.warning("Roboterpose im Job enthaelt NaN/Inf: %r", values)
        return None

    from tagloc.geometry import from_position_quaternion

    norm = math.sqrt(sum(value * value for value in values[3:]))
    if norm < 1e-9:
        _log.warning("Roboterpose im Job hat ein Quaternion der Laenge 0")
        return None
    return from_position_quaternion(values[:3], values[3:])


def _finite_or_none(value: float | None) -> float | None:
    """Map NaN and infinity to `None` before they reach the payload.

    `payload._dumps` serialises with `allow_nan=False` and raises on NaN --
    so a single outlier wouldn't just drop the attribute, it would end the
    whole job as INTERNAL. `None` is the honest answer here: "this value
    isn't available".
    """
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else round(number, 4)


def _world_tag_errors(tag_poses, tag_map, pose_world_cam, calibration) -> dict:
    """Je sichtbarem Welttag: RMS (px) seiner Ecken unter `pose_world_cam`.

    Die Weltpose kommt aus der Tag-Map, projiziert wird mit dem Linsenmodell
    und verglichen mit den **rohen** (verzeichneten) Ecken des Detektors.
    """
    import numpy as np

    from tagloc import lens
    from tagloc.geometry import compose, invert, rvec_from_rotation
    from tagloc.pose import tag_object_points

    references = tag_map.reference_poses()
    errors = {}
    for tag_pose in tag_poses:
        world_pose = references.get(tag_pose.tag_id)
        if world_pose is None or tag_pose.observation is None:
            continue
        pose_cam_tag = compose(invert(pose_world_cam), world_pose)
        projected = lens.project_points(
            tag_object_points(tag_map.size_for(tag_pose.tag_id, 0.0)),
            rvec_from_rotation(pose_cam_tag[:3, :3]),
            np.ascontiguousarray(pose_cam_tag[:3, 3]),
            calibration,
        )
        seen = np.asarray(tag_pose.observation.corners, dtype=np.float64).reshape(4, 2)
        errors[int(tag_pose.tag_id)] = float(
            np.sqrt(np.mean(np.sum((projected - seen) ** 2, axis=1)))
        )
    return errors


def _observed_world_tags(tag_poses, tag_map, localization) -> list:
    """Wo dieses Bild die Welttags sieht: `(tag_id, T_world_tag, Fehler, mehrdeutig)`.

    `T_world_cam · T_cam_tag` mit der zusammengefuehrten Kamerapose. Die
    Abweichung von der Weltpose in der Tag-Map ist das Fehlermass der Karte
    -- `locate_modules` laesst Welttags bewusst aus, sie sind keine Module.
    Nur mit Kamerapose; ohne sie gibt es kein Welt-KS.
    """
    from tagloc.geometry import compose

    if localization is None or tag_map is None:
        return []
    observed = []
    for tag_pose in tag_poses:
        entry = tag_map.get(tag_pose.tag_id)
        if entry is None or not entry.is_reference:
            continue
        observed.append(
            (
                tag_pose.tag_id,
                compose(localization.pose_world_cam, tag_pose.pose_cam_tag),
                tag_pose.reprojection_error_px,
                tag_pose.is_ambiguous,
                # Fehler der Ecken dieses Tags unter der gemeinsamen Pose --
                # zeigt, welcher Welttag nicht zur Karte passt.
                getattr(localization, "tag_errors_px", {}).get(tag_pose.tag_id),
            )
        )
    return observed


def _localization_summary(localization) -> dict | None:
    """`cameraLocalization` im Payload: wie die Kamerapose zustande kam.

    Aus dem letzten Bild des Jobs, wie die `camera*`-Attribute der
    Detektionen -- hier aber auch ohne Detektion, fuer ein Bild nur mit
    Welttags. Eine aus dem Anker fortgeschriebene Pose heisst `anchor` und
    hat keinen Reprojektionsfehler: sie ist nicht gemessen.
    """
    if localization is None:
        return None
    from tagloc.localize import ORIGIN_ROBOT_POSE

    anchored = localization.origin == ORIGIN_ROBOT_POSE
    summary = {
        "solver": "anchor" if anchored else localization.solver,
        "reprojectionErrorPx": None
        if anchored
        else _finite_or_none(localization.reprojection_error_px),
        "spreadM": _finite_or_none(localization.spread_m),
        "spreadDeg": _finite_or_none(math.degrees(localization.spread_rad)),
        "worldTagIds": [int(tag_id) for tag_id in localization.world_tag_ids],
    }
    if not anchored and localization.solver == "joint":
        # Die Pose selbst, nur wenn sie aus >= 2 Welttags gemeinsam geschaetzt
        # ist: Die Zellen-Ueberwachung merkt sie sich, damit danach ein einziger
        # Welttag genuegt. Dazu der Eckenfehler je Welttag als Grundlinie.
        from tagloc.geometry import pose_to_dict

        summary["poseInWorld"] = pose_to_dict(localization.pose_world_cam)
        summary["tagErrorsPx"] = {
            str(int(tag_id)): _finite_or_none(error)
            for tag_id, error in localization.tag_errors_px.items()
        }
    return summary


def _merge_world_tags(samples) -> tuple:
    """Je Welttag ueber die Bilder eines Jobs gemittelt, JSON-fertig."""
    from tagloc.geometry import to_position_quaternion
    from tagloc.tagmap import merge_tag_poses

    by_tag: dict[int, list] = {}
    for sample in samples:
        for observation in sample:
            by_tag.setdefault(observation[0], []).append(observation)
    merged = []
    for tag_id in sorted(by_tag):
        observations = by_tag[tag_id]
        position, orientation = to_position_quaternion(
            merge_tag_poses([observation[1] for observation in observations])
        )
        errors = [o[2] for o in observations if o[2] is not None and math.isfinite(o[2])]
        joint_errors = [o[4] for o in observations if o[4] is not None and math.isfinite(o[4])]
        entry = {
                "tagId": int(tag_id),
                "position": list(position),
                "orientation": list(orientation),
                "reprojectionErrorPx": _finite_or_none(sum(errors) / len(errors))
                if errors
                else None,
                "ambiguous": any(observation[3] for observation in observations),
                "sampleCount": len(observations),
            }
        if joint_errors:
            entry["jointReprojectionErrorPx"] = _finite_or_none(
                sum(joint_errors) / len(joint_errors)
            )
        merged.append(entry)
    return tuple(merged)


class AprilTagDetectionSource(DetectionSource):
    """Locate modules by their AprilTags."""

    profile_id = "apriltag"
    is_simulated = False

    def __init__(
        self,
        config: AprilTagProfileConfig,
        camera_config: CameraStreamConfig | None = None,
        *,
        camera: SharedCamera | None = None,
        detector: Any = None,
        calibration: Any = None,
        tag_map: Any = None,
    ) -> None:
        self._config = config
        self._camera_config = camera_config or CameraStreamConfig()
        #: Passed in from outside when `camera_stream.py` needs the same
        #: handle (see `runner.py`).
        self.camera = camera if camera is not None else SharedCamera(self._camera_config)
        #: Injectable so tests can run without OpenCV and without files.
        self._detector = detector
        self._calibration = calibration
        self._tag_map = tag_map
        #: Camera pose of the last evaluated image, set by `_locate`. `None`
        #: until the first image, and again whenever no world tag was visible
        #: and no anchor could carry it.
        self._localization = None
        #: Hand-Auge, einmal kalibriert -- die Kamera sitzt starr am Roboter.
        self._hand_eye = None
        #: Wo die Roboterbasis im Welt-KS steht. Wird beim ersten Welttag im
        #: Bild gesetzt und traegt danach ueber alle Module hinweg, auch wenn
        #: kein Welttag mehr zu sehen ist. Lebt bewusst ueber Jobs hinweg:
        #: geankert wird einmal je Lokalisierungsvorgang, nicht je Modul.
        self._anchor = None
        #: Letzte gemessene Drift gegen den Anker, `None` solange ungeprueft.
        self._drift = None
        #: Welttags des zuletzt ausgewerteten Bilds, von `_locate` gesetzt.
        self._world_tag_sample: list = []
        #: Laufende `MeasureTags`-Aufrufe (`runner.py`); das Stream-Overlay haelt
        #: solange an.
        self.measure_calls = 0
        #: Neuestes Ergebnis in voller Aufloesung, egal aus welchem Pfad
        #: (`TagSnapshot`). Wird nur ersetzt, nie veraendert -- das Overlay
        #: liest es aus seinem eigenen Thread ohne Sperre.
        self.latest_tags: TagSnapshot | None = None
        #: Wird je gemessenem Bild von `MeasureTagReference` mit
        #: `(debug, corners_px, image_size, title, layers)` aufgerufen (Mess-Thread),
        #: damit der Livestream die Kantensuche live zeigt. `None` = niemand hoert zu.
        self.reference_debug_sink: Callable[..., None] | None = None
        self.frame_id = config.frame_id
        self.frame_convention = config.frame_convention
        self.configuration_id = self._build_configuration_id()

    # -- Resources -----------------------------------------------------------

    def _build_configuration_id(self) -> str:
        """Combine tag family, calibration and map into one string.

        Later answers which calibration and map were active behind a bad
        pose (doc/projektdoku/altlasten.md C4). Deliberately needs no OpenCV and doesn't
        read the files.
        """
        from tagloc.identity import calibration_identity, tag_map_identity

        return (
            f"{self._config.tag_family}@{calibration_identity(self._config.calibration_path)}"
            f"+{tag_map_identity(self._config.tag_map_path)}"
        )

    async def open(self) -> None:
        """Load calibration and map, build the detector, open the camera.

        If calibration is missing, the source doesn't open and the server
        stays in Preoperational -- intentional, since a vision server
        without calibration would report meaningless numbers. Two
        exceptions, in this order: a camera that knows its own factory
        intrinsics (RealSense on the Hand-Pi) uses those -- measured, not
        guessed, just not ours -- and `allow_placeholder_calibration`, a
        deliberate, temporary bypass to run detection before the real
        calibration run exists.
        """
        from tagloc.calibration import default_calibration, load_calibration
        from tagloc.detector import build_detector
        from tagloc.tagmap import empty_tag_map, load_tag_map, validate_tag_map

        calibration_missing: FileNotFoundError | None = None
        if self._calibration is None:
            try:
                self._calibration = await self.run_blocking(
                    load_calibration, self._config.calibration_path
                )
            except FileNotFoundError as error:
                if getattr(self._camera_config, "backend", "") == "realsense":
                    # Entschieden wird erst mit offener Kamera: die RealSense
                    # kennt ihre Werks-Intrinsik, aber nur mit laufender Pipeline.
                    calibration_missing = error
                elif not self._config.allow_placeholder_calibration:
                    # Ohne Werks-Intrinsik: wie bisher abbrechen, ohne die
                    # Kamera ueberhaupt zu oeffnen.
                    raise
                else:
                    _log.warning(
                        "Keine Kalibrierung unter %s -- verwende Platzhalter-Intrinsik "
                        "(allow_placeholder_calibration=True). Posen sind NICHT masshaltig.",
                        self._config.calibration_path,
                    )
                    self._calibration = default_calibration(
                        self._config.resolution, frame_id=self._config.frame_id
                    )
        if self._tag_map is None:
            path = self._config.tag_map_path
            if path is not None and path.is_file():
                try:
                    self._tag_map = await self.run_blocking(load_tag_map, path)
                except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    # Eine unbrauchbare Karte darf die Erkennung nicht
                    # stilllegen. Ohne Karte laeuft alles weiter -- die Tags
                    # werden als TAG-<id> im Kamera-KS gemeldet, und das
                    # Frontend bekommt seine Platzhalter. Umgekehrt waere der
                    # ganze Vision-Server wegen einer Textdatei tot.
                    _log.error(
                        "Tag-Map %s ist unbrauchbar (%s) -- weiter OHNE Karte. "
                        "Tags werden als 'TAG-<id>' im Kamera-KS gemeldet, ohne "
                        "Modulnamen und ohne CAD-Versatz. Grund: %s",
                        path,
                        type(error).__name__,
                        error,
                    )
                    self._tag_map = empty_tag_map()
                else:
                    # Reported, not enforced: a cell still being built must be
                    # allowed to measure. Silence here would mean a missing
                    # world tag only ever shows up as quietly worse accuracy.
                    for problem in validate_tag_map(self._tag_map):
                        _log.warning("Tag-Map %s: %s", path, problem)
            else:
                _log.warning(
                    "Keine Tag-Map unter %s -- Posen bleiben im Kamera-KS und "
                    "Tags werden ohne Modulzuordnung gemeldet",
                    path,
                )
                self._tag_map = empty_tag_map()
        if self._hand_eye is None and self._config.hand_eye_path is not None:
            from tagloc.handeye import load_hand_eye

            try:
                self._hand_eye = await self.run_blocking(
                    load_hand_eye, self._config.hand_eye_path
                )
                _log.info(
                    "Hand-Auge geladen: %s (Streuung %.2f mm / %.3f deg ueber %d Posen)",
                    self._config.hand_eye_path,
                    (self._hand_eye.rms_position_m or 0.0) * 1000.0,
                    self._hand_eye.rms_rotation_deg or 0.0,
                    self._hand_eye.sample_count,
                )
            except FileNotFoundError:
                # Kein Fehler: die Deckenkamera hat keine, und der Hand-Pi darf
                # auch ohne messen -- dann eben nur mit Welttag im Bild.
                _log.warning(
                    "Keine Hand-Auge-Kalibrierung unter %s -- ohne Welttag im Bild "
                    "bleiben die Posen im Kamera-KS",
                    self._config.hand_eye_path,
                )

        if self._detector is None:
            # `run_blocking` reicht nur Positionalargumente durch --
            # `subpixel_corner_refinement` ist bei `build_detector` bewusst
            # keyword-only, deshalb hier per `partial` gebunden.
            build = functools.partial(
                build_detector,
                self._config.tag_family,
                self._config.detector_backend,
                subpixel_corner_refinement=self._config.subpixel_corner_refinement,
                corner_refinement_method=self._config.corner_refinement_method,
                apriltag_quad_decimate=self._config.apriltag_quad_decimate,
                detector_parameters=self._config.detector_parameters,
            )
            self._detector = await self.run_blocking(build)
            changed = getattr(self._detector, "changed_parameters", None)
            if changed:
                _log.info(
                    "Detektor-Parameter abweichend vom OpenCV-Standard: %s",
                    ", ".join(f"{name}={old} -> {new}" for name, (old, new) in changed.items()),
                )
            else:
                _log.info("Detektor-Parameter: OpenCV-Standard")
        self.configuration_id = self._build_configuration_id()
        await self.camera.open()
        if calibration_missing is not None:
            await self._fallback_calibration(calibration_missing, default_calibration)

    async def _fallback_calibration(self, missing: FileNotFoundError, default_calibration) -> None:
        """Ohne Kalibrierdatei: Werks-Intrinsik, sonst Platzhalter, sonst Abbruch."""
        factory = getattr(self.camera, "factory_calibration", None)
        calibration = await factory(self._config.frame_id) if factory is not None else None
        if calibration is not None:
            _log.warning(
                "Keine Kalibrierung unter %s -- verwende die Werks-Intrinsik der "
                "Kamera (%dx%d). Gemessen, aber nicht von uns: eine eigene "
                "Kalibrierung ist genauer.",
                self._config.calibration_path,
                *calibration.image_size,
            )
            self._calibration = calibration
            return
        if not self._config.allow_placeholder_calibration:
            await self.camera.close()
            raise missing
        _log.warning(
            "Keine Kalibrierung unter %s -- verwende Platzhalter-Intrinsik "
            "(allow_placeholder_calibration=True). Posen sind NICHT masshaltig.",
            self._config.calibration_path,
        )
        self._calibration = default_calibration(
            self._config.resolution, frame_id=self._config.frame_id
        )

    @property
    def intrinsics_source(self) -> str:
        """Woher die Intrinsik stammt: `calibration`, `realsense_factory`, `placeholder`."""
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

        from ..camera import REALSENSE_FACTORY_CALIBRATION_ID

        calibration_id = getattr(self._calibration, "calibration_id", "")
        if calibration_id == PLACEHOLDER_CALIBRATION_ID:
            return "placeholder"
        if calibration_id == REALSENSE_FACTORY_CALIBRATION_ID:
            return "realsense_factory"
        return "calibration"

    async def measure_tags(self, request) -> dict:
        """`MeasureTags`: Tags in Kamerakoordinaten, ueber frische Bilder gemittelt.

        Nur Bilder, die **nach** dem Aufruf eintreffen -- ein Bild aus der
        Fahrt, das noch im Puffer liegt, waere eine Messung an der falschen
        Stelle. Ohne Welt-KS, ohne CAD-Versatz, ohne Anker: genau das, was die
        Regelung der Steuerung braucht. Seiteneffektfrei.
        """
        from datetime import datetime, timedelta, timezone

        from ..measuretag import NO_FRAME, FrameMeasurement, SizeLookup, build_result

        sizes = SizeLookup(request.sizes_m, self._tag_map, self._config.tag_size_m)
        loop = asyncio.get_running_loop()
        latest = self.camera.latest_frame
        seen_timestamp = latest.timestamp if latest is not None else None
        frames: list[FrameMeasurement] = []
        error = None
        for _ in range(request.frames):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError:
                error = NO_FRAME
                break
            seen_timestamp = frame.timestamp
            tag_poses, image_size = await self.run_blocking(
                self._detect_in_camera, frame.image, sizes, request.tag_ids
            )
            # Zeitpunkt des Bilds, nicht des Aufrufs: `timestamp` ist die
            # Loop-Zeit beim Eintreffen, umgerechnet auf UTC.
            age_s = loop.time() - frame.timestamp
            captured_at = datetime.now(timezone.utc) - timedelta(seconds=age_s)
            frames.append(FrameMeasurement(captured_at, image_size, tuple(tag_poses)))
        return build_result(
            frames,
            frames_requested=request.frames,
            frame_id=self._config.frame_id,
            sizes=sizes,
            intrinsics_source=self.intrinsics_source,
            calibration_id=getattr(self._calibration, "calibration_id", ""),
            error=error if not frames else None,
        )

    def _detect_in_camera(self, image, sizes, tag_ids):
        """Ein Bild: `(TagPosen, Bildgroesse)`, auf `tag_ids` gefiltert. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.pose import estimate_tag_poses

        tag_poses = estimate_tag_poses(
            self._detector.detect(frame_tools.to_gray(image)),
            self._calibration_for(image),
            tag_map=sizes,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._remember_tags(tag_poses, image, "measure")
        if tag_ids is not None:
            tag_poses = [tag_pose for tag_pose in tag_poses if tag_pose.tag_id in tag_ids]
        return tag_poses, frame_tools.image_size(image)

    async def measure_tag_reference(self, request, artifacts: dict | None = None) -> dict:
        """`MeasureTagReference`: Tag-Position ueber Bezugskanten einmessen.

        Wie `measure_tags`: nur Bilder, die **nach** dem Aufruf eintreffen,
        seiteneffektfrei, ohne Welt-KS. Je Bild wird der Tag (`request.tag_id`)
        erkannt; aus den Bildern mit Tag entsteht eine gemeinsame Tag-Pose (der
        Roboter steht). Darauf misst `measure_reference_lines` die Kanten im
        Rohbild, `fit_in_plane_correction` rechnet je Bild die Korrektur,
        `average_corrections` mittelt sie.

        Mehrdeutiger Tag: Gibt es in mindestens einem Bild eine eindeutige
        Tag-Pose, gilt deren gemittelte Drehung fuer alle Bilder (der Roboter
        steht, die Pose ist dieselbe). Ist **kein** Bild eindeutig, ist die
        Drehung unbelastbar (Spiegelloesung): die Korrektur uebernimmt dann nur
        die Position, die Drehung bleibt aus dem Startwert
        (`tagreference.keep_guess_rotation`), und es gibt eine Warnung.

        Fehler (`error`): `NO_FRAME` (kein frisches Bild) bzw. `TAG_NOT_SEEN`
        (Tag in keinem Bild). Nicht messbare Kanten sind kein Fehler, sie
        senken nur `dofs`.

        `artifacts`: ist es ein dict, fuellt die Methode es fuer das
        Mess-Protokoll: `frames` (Rohbilder mit Tag) und `overlay` (Debug-
        Ansicht des letzten Bilds, BGR, volle Aufloesung; fehlt, wenn die
        Kantensuche nicht lief).
        """
        from datetime import datetime, timedelta, timezone

        from tagloc.reference_edges import average_corrections

        from ..measuretag import SizeLookup
        from ..tagreference import (
            NO_FRAME,
            TAG_NOT_SEEN,
            build_result,
            combine_tag_pose,
            reference_warnings,
            summarize_lines,
        )

        sizes = SizeLookup(
            {request.tag_id: request.tag_size_m} if request.tag_size_m else {},
            self._tag_map,
            self._config.tag_size_m,
        )
        size_m = sizes.size_for(request.tag_id)
        loop = asyncio.get_running_loop()
        latest = self.camera.latest_frame
        seen_timestamp = latest.timestamp if latest is not None else None
        #: `(Bild, TagPose | None, Aufnahmezeit, Bildgroesse)` je frischem Bild.
        captured: list[tuple] = []
        for _ in range(request.frames):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError:
                break
            seen_timestamp = frame.timestamp
            tag_poses, image_size = await self.run_blocking(
                self._detect_in_camera, frame.image, sizes, frozenset({request.tag_id})
            )
            age_s = loop.time() - frame.timestamp
            captured_at = datetime.now(timezone.utc) - timedelta(seconds=age_s)
            tag_pose = tag_poses[0] if tag_poses else None
            captured.append((frame.image, tag_pose, captured_at, image_size))

        common = {
            "captured_at": captured[0][2] if captured else None,
            "captured_until": captured[-1][2] if captured else None,
            "image_size": captured[-1][3] if captured else None,
            "intrinsics_source": self.intrinsics_source,
        }
        seen = [item for item in captured if item[1] is not None]
        if not captured:
            return build_result(**common, error=NO_FRAME)
        if not seen:
            return build_result(**common, error=TAG_NOT_SEEN)

        tag_poses = [item[1] for item in seen]
        tag_pose, tag_ambiguous = combine_tag_pose(tag_poses)
        if artifacts is not None:
            artifacts["frames"] = [item[0] for item in seen]
        line_frames, corrections = await self.run_blocking(
            self._measure_reference,
            [item[0] for item in seen],
            tag_pose,
            tag_ambiguous,
            request,
            size_m,
            artifacts,
        )
        correction, spread = average_corrections(corrections)
        warnings = reference_warnings(
            correction=correction,
            lines=summarize_lines(line_frames),
            search_band_m=request.search_band_m,
            tag_ambiguous=tag_ambiguous,
            intrinsics_source=self.intrinsics_source,
        )
        return build_result(
            **{**common, "captured_at": seen[0][2], "captured_until": seen[-1][2]},
            frames_used=len(seen),
            tag_poses=tag_poses,
            tag_pose=tag_pose,
            tag_ambiguous=tag_ambiguous,
            line_frames=line_frames,
            correction=correction,
            spread=spread,
            warnings=warnings,
        )

    def _measure_reference(
        self, images, tag_pose, tag_ambiguous, request, size_m, artifacts=None
    ):
        """Kanten und Korrektur je Bild: `(Messungen je Bild, Korrekturen je Bild)`. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.reference_edges import (
            fit_in_plane_correction,
            measure_reference_lines,
            tag_outline_px,
        )
        from tagloc.reference_overlay import draw_reference_debug

        from ..tagreference import keep_guess_rotation

        guess = request.pose_module_tag_guess
        line_frames = []
        corrections = []
        want_debug = artifacts is not None or self.reference_debug_sink is not None
        for index, image in enumerate(images, start=1):
            debug: list | None = [] if want_debug else None
            calibration = self._calibration_for(image)
            measurements = measure_reference_lines(
                frame_tools.to_gray(image),
                calibration,
                tag_pose,
                guess,
                request.lines,
                tag_size_m=size_m,
                search_band_m=request.search_band_m,
                debug=debug,
            )
            if debug is not None:
                try:
                    title = f"Tag {request.tag_id} - Messung Bild {index}/{len(images)}"
                    corners = tag_outline_px(calibration, tag_pose, size_m, 0.0)
                    if self.reference_debug_sink is not None:
                        self.reference_debug_sink(
                            debug, corners, frame_tools.image_size(image), title, request.overlay
                        )
                    if artifacts is not None and index == len(images):
                        artifacts["overlay"] = draw_reference_debug(
                            image,
                            debug,
                            tag_corners_px=corners,
                            title=title,
                            layers=request.overlay,
                        )
                except Exception:
                    # Die Anzeige darf die Messung nie stoeren.
                    _log.exception("Debug-Ansicht der Einmessung nicht erzeugt")
            correction = fit_in_plane_correction(measurements, guess)
            if tag_ambiguous:
                correction = keep_guess_rotation(correction, guess)
            line_frames.append(measurements)
            corrections.append(correction)
        return line_frames, corrections

    def plan_tag_reference_view(self, request) -> dict:
        """`PlanTagReferenceView`: Kamerapose im Tag-KS, aus der Tag und Kanten ins Bild passen.

        Rechnet nur mit der Kalibrierung (Brennweite, Bildgroesse), ohne
        Kamerabild -- kein Warten, keine Seiteneffekte.
        """
        from tagloc.reference_edges import plan_reference_view

        from ..measuretag import SizeLookup
        from ..tagreference import build_view

        sizes = SizeLookup(
            {request.tag_id: request.tag_size_m} if request.tag_size_m else {},
            self._tag_map,
            self._config.tag_size_m,
        )
        plan = plan_reference_view(
            request.pose_module_tag_guess,
            request.lines,
            self._calibration,
            tag_size_m=sizes.size_for(request.tag_id),
            line_extent_m=request.line_extent_m,
            tilt_deg=request.tilt_deg,
        )
        return build_view(plan)

    def apply_tag_map(self, tag_map: Any) -> list[str]:
        """Uebernimmt eine neue Tag-Map in die laufende Quelle.

        Gibt die Beanstandungen von `validate_tag_map` zurueck -- sie sind
        Bericht, kein Fehler: eine Zelle im Aufbau darf messen.

        Der Anker wird dabei **verworfen**. Er wurde gegen die Weltposen der
        alten Karte gerechnet; gegenueber einer neuen waere er stumm falsch,
        und ein stiller Versatz in jeder Modulpose ist genau die Sorte Fehler,
        die erst beim Danebengreifen auffaellt.
        """
        from tagloc.tagmap import validate_tag_map

        self._tag_map = tag_map
        self._anchor = None
        self._drift = None
        self._localization = None
        # Die Karte gehoert zur Identitaet der Konfiguration -- sonst sieht ein
        # spaeteres Ergebnis aus wie eines von vorher.
        self.configuration_id = self._build_configuration_id()
        return validate_tag_map(tag_map)

    async def close(self) -> None:
        await self.camera.close()
        await self.shutdown_executor()

    def apply_calibration(self, calibration: Any) -> None:
        """Ersetzt die aktive Kalibrierung ohne Neustart.

        Aufgerufen aus `runner.py`, direkt nachdem eine interaktive
        `CalibrationSession` (`StartCalibration`/`CaptureCalibrationSample`/
        `FinishCalibration`) eine neue Kalibrierung berechnet und gespeichert
        hat. `_locate()` liest `self._calibration` bei jedem Job frisch --
        der Tausch hier wirkt also ab dem naechsten Job, ohne dass jemand den
        Server neu starten muss.
        """
        self._calibration = calibration
        self.configuration_id = self._build_configuration_id()

    def apply_hand_eye(self, hand_eye: Any) -> None:
        """Uebernimmt eine neue Hand-Auge-Kalibrierung ohne Neustart.

        Der Anker wird verworfen: er wurde mit dem alten `T_flansch_cam`
        gerechnet, und jede daraus fortgeschriebene Kamerapose waere um genau
        den Unterschied versetzt.
        """
        self._hand_eye = hand_eye
        self._anchor = None
        self._drift = None

    def _latest_timestamp(self):
        """Zeitstempel des neuesten schon vorhandenen Bilds, oder `None`.

        Wer nur Bilder **nach** dem Aufruf will, uebergibt ihn als
        `seen_timestamp` an `_next_frame`: Sonst kaeme als erstes das Bild, das
        schon vor dem Aufruf im Puffer lag -- bis zu eine Bildperiode alt.
        """
        latest = self.camera.latest_frame
        return latest.timestamp if latest is not None else None

    async def measure_tag(self, tag_id: int) -> tuple[Any | None, str, dict]:
        """`T_cam_tag` eines Tags aus `samples_per_job` frischen Bildern.

        Fuer die Hand-Auge-Kalibrierung: rein im Kamera-KS, ohne Tag-Map-
        Versatz und ohne Welt-KS -- genau die Groesse, die `solve_hand_eye`
        braucht. Liefert `(Pose, "", Info)` oder `(None, Grund, Info)`; `Info`
        hat den mittleren `reprojectionErrorPx` aller Bilder mit dem Tag.
        """
        from tagloc.tagmap import merge_tag_poses

        poses = []
        errors = []
        ambiguous = 0
        seen_timestamp = self._latest_timestamp()
        samples = max(1, self._config.samples_per_job)
        for _ in range(samples):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError as error:
                return None, error.message, {"reprojectionErrorPx": None}
            seen_timestamp = frame.timestamp
            tag_pose = await self.run_blocking(self._measure_tag, frame.image, tag_id)
            if tag_pose is None:
                continue
            if math.isfinite(tag_pose.reprojection_error_px):
                errors.append(tag_pose.reprojection_error_px)
            if tag_pose.is_ambiguous:
                # Zwei gleich gute Loesungen: welche stimmt, weiss niemand --
                # eine falsch gekippte Messung verdirbt die ganze Kalibrierung.
                ambiguous += 1
                continue
            poses.append(tag_pose.pose_cam_tag)
        info = {"reprojectionErrorPx": sum(errors) / len(errors) if errors else None}
        if poses:
            return merge_tag_poses(poses), "", info
        if ambiguous:
            return None, (
                f"Tag {tag_id} mehrdeutig ({ambiguous} von {samples} Bildern) -- "
                "schraeger auf den Tag schauen"
            ), info
        return None, f"Tag {tag_id} nicht gesehen", info

    async def measure_board(self, spec) -> tuple[Any | None, str, dict]:
        """`T_cam_board` eines Schachbretts aus `samples_per_job` frischen Bildern.

        Wie `measure_tag`, nur mit einem Kalibrier-Schachbrett als festem
        Ziel: viele Ecken statt vier, also eine genauere und eindeutige Pose.
        Liefert `(Pose, "", Info)` oder `(None, Grund, Info)`. `Info` hat den
        mittleren `reprojectionErrorPx` (auch fuer verworfene Bilder). Bei Erfolg
        zusaetzlich fuer die Hand-Auge-Verfeinerung und den Datensatz:
        `corners` (kanonische Ecken `(N, 2)` im verzerrten Bild, Mittel ueber
        die verwendeten Bilder -- der Roboter steht), `calibration` (die dazu
        verwendete Intrinsik), `intrinsicsSource` und `image` (das letzte
        verwendete Bild).
        """
        import numpy as np

        from tagloc.tagmap import merge_tag_poses

        poses = []
        corner_sets = []
        errors: list[float] = []
        reasons: list[str] = []
        calibration = None
        last_image = None
        seen_timestamp = self._latest_timestamp()
        samples = max(1, self._config.samples_per_job)
        for _ in range(samples):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError as error:
                return None, error.message, {"reprojectionErrorPx": None}
            seen_timestamp = frame.timestamp
            pose, reason, error_px, corners, used_calibration = await self.run_blocking(
                self._measure_board, frame.image, spec
            )
            if error_px is not None:
                errors.append(error_px)
            if pose is None:
                reasons.append(reason)
                continue
            poses.append(pose)
            corner_sets.append(corners)
            calibration, last_image = used_calibration, frame.image
        info = {"reprojectionErrorPx": sum(errors) / len(errors) if errors else None}
        if poses:
            info.update(
                corners=np.mean(np.stack(corner_sets), axis=0),
                calibration=calibration,
                intrinsicsSource=self.intrinsics_source,
                image=last_image,
            )
            return merge_tag_poses(poses), "", info
        return None, reasons[-1] if reasons else "Board nicht gesehen", info

    def _measure_board(self, image, spec):
        """Ein Bild, ein Schachbrett:
        `(Pose | None, Grund, Reprojektionsfehler | None, Ecken | None,
        Kalibrierung | None)`. Die Ecken sind kanonisch geordnet, im
        verzerrten Bild. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.boards import (
            canonical_chessboard_corners,
            detect_board,
            estimate_board_pose,
        )

        gray = frame_tools.to_gray(image)
        sample = detect_board(gray, spec)
        if sample is None:
            return None, "Board nicht (vollstaendig) gesehen -- alle Ecken muessen im Bild sein", None, None, None
        corners = canonical_chessboard_corners(gray, sample.corners, spec)
        calibration = self._calibration_for(image)
        pose, error_px = estimate_board_pose(corners, spec, calibration)
        # Ein Board mit Dutzenden Ecken passt viel besser als ein Tag;
        # ein grosser Fehler heisst Unschaerfe, ein welliges Brett oder ein
        # falsches Raster.
        if error_px > self._config.max_reproj_error_px:
            return None, (
                f"Board-Pose passt schlecht ({error_px:.1f} px) -- Feldgroesse und "
                "Eckenzahl pruefen"
            ), error_px, None, None
        return pose, "", error_px, corners, calibration

    def default_board_spec(self):
        """Das Brett aus der Konfiguration der Kamera."""
        from tagloc.boards import BoardSpec

        return BoardSpec(
            type=self._config.calibration_board_type,
            cols=self._config.calibration_board_cols,
            rows=self._config.calibration_board_rows,
            square_size_m=self._config.calibration_board_square_size_m,
            marker_size_m=self._config.calibration_board_marker_size_m,
            dictionary=self._config.calibration_board_dictionary,
        )

    async def measure_board_view(self, request) -> dict:
        """`MeasureBoard`: das Brett fuer das Zentrieren messen, ohne Fahrbefehle.

        Nur Bilder nach dem Aufruf. Ganzes Brett: Pose und Ecken. Nur teilweise
        sichtbar: Schwerpunkt und Bildraender eines Teilrasters. Siehe
        `vision_server.measureboard`. Seiteneffektfrei.
        """
        from datetime import datetime, timedelta, timezone

        from ..measureboard import NO_FRAME, build_result

        spec = request.board or self.default_board_spec()
        loop = asyncio.get_running_loop()
        seen_timestamp = self._latest_timestamp()
        frames = []
        error = None
        last_image = None
        for index in range(request.frames):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError:
                error = NO_FRAME
                break
            seen_timestamp = frame.timestamp
            last_image = frame.image
            # Das Teilraster braucht nur das letzte Bild -- und die Suche ist der
            # teuerste Teil, wenn das Brett nicht ganz im Bild ist.
            observed = await self.run_blocking(
                self._observe_board, frame.image, spec, index == request.frames - 1
            )
            age_s = loop.time() - frame.timestamp
            frames.append(
                replace(observed, captured_at=datetime.now(timezone.utc) - timedelta(seconds=age_s))
            )
        diagnosis = None
        if request.diagnose and last_image is not None:
            diagnosis = await self.run_blocking(self._diagnose_board, last_image, spec)
        result = build_result(
            frames,
            spec=spec,
            frames_requested=request.frames,
            frame_id=self._config.frame_id,
            intrinsics_source=self.intrinsics_source,
            calibration_id=getattr(self._calibration, "calibration_id", ""),
            max_error_px=self._config.max_reproj_error_px,
            error=error if not frames else None,
        )
        if diagnosis is not None:
            result["diagnosis"] = diagnosis
        return result

    def _diagnose_board(self, image, spec):
        """Erkennungsversuche am letzten Bild, nur auf Anfrage. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.boards import diagnose_board

        return diagnose_board(frame_tools.to_gray(image), spec)

    def _observe_board(self, image, spec, with_partial: bool = True):
        """Ein Bild fuer `MeasureBoard`: ganzes Brett, Teilraster oder nichts. Worker-Thread.

        Gesucht wird auf einem auf `OBSERVE_MAX_SIDE` (laengste Kante) verkleinerten Bild, damit die
        Antwort auch ohne Brett im Bild schnell kommt (ein Fehlversuch kostet am
        vollen Bild Sekunden, am Pi ein Vielfaches). Die Ecken werden danach im
        vollen Bild nachgeschaerft."""
        from datetime import datetime, timezone

        from tagloc import frames as frame_tools
        from tagloc.boards import (
            canonical_chessboard_corners,
            detect_board,
            detect_partial_board,
            estimate_board_pose,
        )

        from ..measureboard import FrameBoard

        gray = frame_tools.to_gray(image)
        size = frame_tools.image_size(image)
        now = datetime.now(timezone.utc)
        sample = detect_board(gray, spec, max_side=OBSERVE_MAX_SIDE)
        if sample is None:
            partial = detect_partial_board(gray, spec, max_side=OBSERVE_MAX_SIDE) if with_partial else None
            return FrameBoard(now, size, partial=partial)
        corners = canonical_chessboard_corners(gray, sample.corners, spec)
        pose, error_px = estimate_board_pose(corners, spec, self._calibration_for(image))
        return FrameBoard(
            now, size, complete=True, pose=pose, reprojection_error_px=error_px, corners=corners
        )

    def observe_cell(self, image, detector: Any, stored_world_cam=None):
        """Ein Bild fuer die Zellen-Ueberwachung:
        `(Modul-Orte je Tag, Lokalisierung, alle erkannten Tag-IDs)`.

        Mit `stored_world_cam` (die frueher aus mindestens zwei Welttags
        geschaetzte Pose der fest montierten Kamera) rechnet das Bild auch dann
        im Welt-KS, wenn nur ein Welttag zu sehen ist: Die Pose aus einem
        einzelnen Tag ist aus 2,5 m um Zentimeter ungenau (gekippte Loesungen),
        die gespeicherte nicht. Die Lokalisierung hat dann `solver="stored"`,
        und `tag_errors_px` sagt je sichtbarem Welttag, wie weit seine Ecken
        unter dieser Pose von den gesehenen liegen -- die Konsistenzpruefung.

        Dieselbe Rechnung wie ein Job -- Tag-Posen, gemeinsames PnP ueber die
        Welttags, `locate_modules` --, aber **ohne Seiteneffekte**: kein Anker,
        keine Drift, kein `_localization`, kein `world_tags`. Und nicht
        zusammengefasst, damit jeder Tag fuer sich verglichen werden kann.

        `detector` ist eine eigene Instanz der Ueberwachung: sie laeuft in
        einem eigenen Thread, und ein Detektor ist nicht fuer zwei Threads
        zugleich gebaut. Laeuft blockierend -- im Worker-Thread aufrufen.

        Die erkannten IDs kommen auch dann zurueck, wenn das Bild keine
        Aussage erlaubt -- gerade dann will der Bediener sehen, welcher
        Welttag fehlt.
        """
        from tagloc import frames as frame_tools
        from tagloc.localize import (
            SOLVER_JOINT,
            SOLVER_STORED,
            locate_modules,
            localize_camera,
        )
        from tagloc.pose import estimate_tag_poses

        calibration = self._calibration_for(image)
        observations = detector.detect(frame_tools.to_gray(image))
        visible = sorted({int(observation.tag_id) for observation in observations})
        tag_poses = estimate_tag_poses(
            observations,
            calibration,
            tag_map=self._tag_map,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._remember_tags(tag_poses, image, "cellwatch")
        localization = localize_camera(
            tag_poses,
            self._tag_map,
            max_reprojection_error_px=self._config.max_reproj_error_px,
            calibration=calibration,
        )
        if localization is None:
            return [], None, visible
        if stored_world_cam is not None and localization.solver != SOLVER_JOINT:
            errors = _world_tag_errors(
                tag_poses, self._tag_map, stored_world_cam, calibration
            )
            localization = replace(
                localization,
                pose_world_cam=stored_world_cam,
                solver=SOLVER_STORED,
                reprojection_error_px=(
                    sum(errors.values()) / len(errors) if errors else float("nan")
                ),
                tag_errors_px=errors,
            )
        return (
            locate_modules(
                tag_poses,
                self._tag_map,
                localization=localization,
                frame_id=self._tag_map.frame_id,
                max_reprojection_error_px=self._config.max_reproj_error_px,
            ),
            localization,
            visible,
        )

    def _remember_tags(self, tag_poses, image, origin: str) -> None:
        """Haelt `tag_poses` als neuestes Ergebnis fuers Livestream-Overlay fest."""
        from tagloc import frames as frame_tools

        self.latest_tags = TagSnapshot(
            tag_poses=tuple(tag_poses),
            image_size=tuple(frame_tools.image_size(image)),
            origin=origin,
            taken_at=time.monotonic(),
        )

    def _calibration_for(self, image):
        """Die Kalibrierung, passend zur Aufloesung dieses Bilds."""
        from tagloc import frames as frame_tools
        from tagloc.calibration import check_resolution, scale_to_resolution

        size = frame_tools.image_size(image)
        calibration = self._calibration
        if tuple(calibration.image_size) != tuple(size):
            if not self._config.allow_resolution_mismatch:
                check_resolution(calibration, size)
            calibration = scale_to_resolution(calibration, size)
        return calibration

    def _measure_tag(self, image, tag_id: int):
        """Ein Bild, ein Tag. Laeuft im Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.pose import estimate_tag_poses

        tag_poses = estimate_tag_poses(
            self._detector.detect(frame_tools.to_gray(image)),
            self._calibration_for(image),
            tag_map=self._tag_map,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._remember_tags(tag_poses, image, "measure")
        return next((pose for pose in tag_poses if pose.tag_id == tag_id), None)

    # -- Capture ---------------------------------------------------------------

    async def _next_frame(self, seen_timestamp: float | None):
        """Wait for a frame newer than the last one processed.

        Its own timeout, because the runner's job timeout can't kill a
        stuck worker thread (see `base.run_blocking`).
        """
        deadline = time.monotonic() + self._config.capture_timeout_s
        while time.monotonic() < deadline:
            frame = self.camera.latest_frame
            if frame is not None and frame.timestamp != seen_timestamp:
                return frame
            await asyncio.sleep(POLL_INTERVAL_S)
        raise VisionJobError(
            VisionErrorCode.DETECTION_FAILED,
            f"Kein Kamerabild innerhalb von {self._config.capture_timeout_s:.1f} s",
        )

    def _locate(self, image, pose_base_flange=None) -> list:
        """Evaluate one image. Runs in the worker thread, never on the loop."""
        from tagloc import frames as frame_tools
        from tagloc.localize import (
            anchor_drift,
            anchor_from_localization,
            locate_modules,
            localize_camera,
            localize_camera_from_anchor,
        )
        from tagloc.pose import estimate_tag_poses

        calibration = self._calibration_for(image)
        tag_poses = estimate_tag_poses(
            self._detector.detect(frame_tools.to_gray(image)),
            calibration,
            tag_map=self._tag_map,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._remember_tags(tag_poses, image, "job")
        # Mit Kalibrierung: bei zwei und mehr Welttags ein gemeinsames PnP
        # ueber alle ihre Ecken statt des Mittels der Einzel-Tags.
        localization = localize_camera(
            tag_poses,
            self._tag_map,
            max_reprojection_error_px=self._config.max_reproj_error_px,
            calibration=calibration,
        )

        # Der optische Wert gewinnt immer, wenn ein Welttag im Bild liegt --
        # er ist die Messung, alles andere ist Fortschreibung. Liegt keiner
        # im Bild, traegt der Anker: genau der Fall zwischen zwei Modulen.
        if self._hand_eye is not None and pose_base_flange is not None:
            if localization is not None:
                if self._anchor is not None:
                    self._drift = anchor_drift(
                        self._anchor, localization, pose_base_flange, self._hand_eye
                    )
                self._anchor = anchor_from_localization(
                    localization, pose_base_flange, self._hand_eye
                )
            elif self._anchor is not None:
                localization = localize_camera_from_anchor(
                    self._anchor, pose_base_flange, self._hand_eye
                )

        # Kept for `acquire_and_detect`, which reports the world tags and
        # their spread alongside the poses. Only ever written from the single
        # worker thread, so no lock is needed.
        self._localization = localization
        self._world_tag_sample = _observed_world_tags(tag_poses, self._tag_map, localization)
        frame_id = self._tag_map.frame_id if localization is not None else self._config.frame_id
        return locate_modules(
            tag_poses,
            self._tag_map,
            localization=localization,
            frame_id=frame_id,
            source=SOURCE_BY_FRAME.get(self._config.frame_id, ""),
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Capture several images, average the poses, and report the modules."""
        from tagloc.geometry import to_position_quaternion
        from tagloc.localize import expected_but_missing, merge_by_module, merge_samples

        pose_base_flange = robot_pose_from_parameters(request.parameters)
        self._drift = None
        # Vor dem ersten Bild leeren: ein Job, der an der Kamera scheitert,
        # darf nicht die Welttags des vorigen melden.
        self.world_tags = ()
        self.camera_localization = None

        samples: list[list] = []
        world_samples: list[list] = []
        seen_timestamp: float | None = None
        for _ in range(max(1, self._config.samples_per_job)):
            frame = await self._next_frame(seen_timestamp)
            seen_timestamp = frame.timestamp
            self._world_tag_sample = []
            samples.append(
                await self.run_blocking(self._locate, frame.image, pose_base_flange)
            )
            world_samples.append(self._world_tag_sample)

        # Vor der Pruefung auf Module: ein Bild nur mit Welttags endet gleich
        # in DETECTION_FAILED, soll die Welttags aber trotzdem melden.
        self.world_tags = _merge_world_tags(world_samples)
        self.camera_localization = _localization_summary(self._localization)
        located = merge_samples(samples)
        if not located:
            raise VisionJobError(
                VisionErrorCode.DETECTION_FAILED,
                f"Kein AprilTag der Familie '{self._config.tag_family}' gefunden",
            )

        # Before merging by module: `expected_but_missing` compares tag ids,
        # and merging several robot tags into one result would make the tags
        # it swallowed look missing.
        missing = expected_but_missing(self._tag_map, located)
        if missing:
            _log.info("Erwartet, aber nicht gefunden: %s", ", ".join(missing))

        # Several tags on the robot measure one and the same base pose.
        located = merge_by_module(located, tag_map=self._tag_map)

        # The frame follows what was actually computed: with reference tags
        # the world frame, without them the camera frame.
        self.frame_id = located[0].frame_id or self._config.frame_id

        localization = self._localization
        if localization is None:
            # Ohne Welttag bleiben die Posen im Kamera-KS, und jeder Konsument
            # muss sie ablehnen -- eine Kamerapose laesst sich nicht in der
            # Zelle platzieren. Bisher stand nirgends, WARUM keiner gefunden
            # wurde; genau daran scheitert die Fehlersuche beim Aufbau.
            known = sorted(self._tag_map.reference_poses())
            _log.warning(
                "Kein Welttag im Bild -- Posen bleiben im Kamera-KS ('%s'). "
                "Gesehen: %s. Die Karte kennt als eingemessenen Welttag: %s. "
                "%s",
                self._config.frame_id,
                sorted(location.tag_id for location in located),
                known or "keinen",
                "Mindestens ein Tag braucht role='world' MIT 'poseInWorld' in "
                "config/tagmap.json -- sonst gibt es kein Welt-KS."
                if not known
                else "Keiner davon war in diesem Bild zu sehen.",
            )

        detections: list[Detection] = []
        for location in located:
            position, orientation = to_position_quaternion(location.pose)
            attributes = {
                "tagId": location.tag_id,
                "reprojErrorPx": _finite_or_none(location.reprojection_error_px),
                "ambiguous": location.ambiguous,
                "sampleCount": location.attributes.get("sampleCount", len(samples)),
                "frameTimestamp": seen_timestamp,
                "recipeId": request.recipe_id,
                "source": location.source,
                "role": location.role,
            }
            if location.attributes.get("tagCount"):
                # Several tags measured this module -- say which, so a wrong
                # CAD offset on one of them can be tracked down.
                attributes["tagIds"] = location.attributes["tagIds"]
                attributes["tagCount"] = location.attributes["tagCount"]
            if location.reference_tag_id >= 0:
                attributes["referenceTagId"] = location.reference_tag_id
            if location.pose_in_reference_tag is not None:
                reference_position, reference_orientation = to_position_quaternion(
                    location.pose_in_reference_tag
                )
                attributes["referencePosition"] = reference_position
                attributes["referenceOrientation"] = reference_orientation
                attributes["referenceDistanceM"] = _finite_or_none(
                    math.dist(reference_position, (0.0, 0.0, 0.0))
                )
            if localization is not None:
                attributes["worldTagIds"] = list(localization.world_tag_ids)
                attributes["cameraSpreadM"] = _finite_or_none(localization.spread_m)
                attributes["cameraSpreadDeg"] = _finite_or_none(
                    math.degrees(localization.spread_rad)
                )
                # Ob die Kamerapose gemessen oder fortgeschrieben wurde, ist
                # kein Detail: die beiden haben nicht dieselbe Genauigkeit.
                attributes["cameraPoseOrigin"] = localization.origin
                if localization.solver:
                    attributes["cameraPoseSolver"] = localization.solver
                    attributes["cameraReprojectionErrorPx"] = _finite_or_none(
                        localization.reprojection_error_px
                    )
            if self._anchor is not None:
                attributes["anchorWorldTagId"] = self._anchor.world_tag_id
            if self._drift is not None:
                drift_m, drift_rad = self._drift
                attributes["anchorDriftM"] = _finite_or_none(drift_m)
                attributes["anchorDriftDeg"] = _finite_or_none(math.degrees(drift_rad))
            if missing:
                attributes["missingModules"] = ", ".join(missing)
            detections.append(
                Detection(
                    module_id=location.module_id,
                    instance_id=location.instance_id,
                    position=position,
                    orientation=orientation,
                    confidence=_finite_or_none(location.confidence) or 0.0,
                    attributes=attributes,
                )
            )
        return detections
