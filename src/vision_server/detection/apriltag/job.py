"""Der Job der AprilTag-Quelle: Bilder aufnehmen, Module lokalisieren, Detektionen bauen.

`JobMixin` ist `acquire_and_detect` (die Schnittstelle von `DetectionSource`)
samt dem Auswerten eines einzelnen Bilds (`_locate`). Dazu die Funktionen, die
Job-Eingaben lesen (`robot_pose_from_parameters`) und Job-Ergebnisse fuer das
Payload aufbereiten (Welttags, Kamera-Lokalisierung). Der Mixin erwartet am
Objekt `_config`, `_detector`, `_tag_map`, `_hand_eye`, `_anchor`, `_drift`,
`_localization`, `_world_tag_sample`, `world_tags`, `camera_localization` und
`frame_id` sowie die Methoden von `CaptureMixin`.
"""

import logging
import math
from typing import Any

from tagloc.jsonio import finite_or_none

from ...errors import VisionErrorCode, VisionJobError
from ..base import Detection, DetectionRequest

_log = logging.getLogger(__package__)

#: Stellen, auf die Zahlen im Payload der Erkennung gerundet werden.
PAYLOAD_DIGITS = 4

#: Frame id -> welche der beiden Kameras das ist. Spiegelt die Rahmen, die
#: `vision_server.server.PI_IDENTITIES` je Pi vergibt. Bewusst einfache
#: Zeichenketten und nicht `tagloc.localize.SOURCE_*`: dieses Modul muss ohne
#: numpy importierbar bleiben, `tagloc` wird deshalb nur in Methoden
#: importiert. Ein unbekannter Rahmen ergibt eine leere Quelle, die
#: `merge_locations` dann hinter beide bekannten Kameras einsortiert, statt zu
#: raten.
SOURCE_BY_FRAME = {"cam_ceiling": "ceiling", "cam_flange": "flange"}


def robot_pose_from_parameters(parameters) -> Any | None:
    """Liefert `T_base_flansch` aus den Job-Parametern, oder `None`.

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


def _observed_world_tags(tag_poses, tag_map, localization) -> list:
    """Wo dieses Bild die Welttags sieht:
    `(tag_id, T_world_tag, Reprojektionsfehler, mehrdeutig, Eckenfehler unter der gemeinsamen Pose)`.

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
        else finite_or_none(localization.reprojection_error_px, PAYLOAD_DIGITS),
        "spreadM": finite_or_none(localization.spread_m, PAYLOAD_DIGITS),
        "spreadDeg": finite_or_none(math.degrees(localization.spread_rad), PAYLOAD_DIGITS),
        "worldTagIds": [int(tag_id) for tag_id in localization.world_tag_ids],
    }
    if not anchored and localization.solver == "joint":
        # Die Pose selbst, nur wenn sie aus >= 2 Welttags gemeinsam geschaetzt
        # ist: Die Zellen-Ueberwachung merkt sie sich, damit danach ein einziger
        # Welttag genuegt. Dazu der Eckenfehler je Welttag als Grundlinie.
        from tagloc.geometry import pose_to_dict

        summary["poseInWorld"] = pose_to_dict(localization.pose_world_cam)
        summary["tagErrorsPx"] = {
            str(int(tag_id)): finite_or_none(error, PAYLOAD_DIGITS)
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
            "reprojectionErrorPx": finite_or_none(sum(errors) / len(errors), PAYLOAD_DIGITS)
            if errors
            else None,
            "ambiguous": any(observation[3] for observation in observations),
            "sampleCount": len(observations),
        }
        if joint_errors:
            entry["jointReprojectionErrorPx"] = finite_or_none(
                sum(joint_errors) / len(joint_errors), PAYLOAD_DIGITS
            )
        merged.append(entry)
    return tuple(merged)


class JobMixin:
    """`acquire_and_detect` und die Auswertung eines Bilds."""

    def _locate(self, image, pose_base_flange=None) -> list:
        """Wertet ein Bild aus. Laeuft im Worker-Thread, nie auf dem Loop."""
        from tagloc import frames as frame_tools
        from tagloc.localize import locate_modules, localize_camera
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
        localization = self._carry_with_anchor(localization, pose_base_flange)

        # Fuer `acquire_and_detect` festgehalten, das die Welttags und ihre
        # Streuung neben den Posen meldet. Wird nur vom einen Worker-Thread
        # geschrieben, also braucht es keine Sperre.
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

    def _carry_with_anchor(self, localization, pose_base_flange):
        """Verankert die Kamerapose am Welttag oder schreibt sie vom Anker fort.

        Der optische Wert gewinnt immer, wenn ein Welttag im Bild liegt --
        er ist die Messung, alles andere ist Fortschreibung. Liegt keiner
        im Bild, traegt der Anker: genau der Fall zwischen zwei Modulen.
        Ohne Hand-Auge oder ohne Roboterpose aendert sich nichts.
        """
        if self._hand_eye is None or pose_base_flange is None:
            return localization

        from tagloc.localize import (
            anchor_drift,
            anchor_from_localization,
            localize_camera_from_anchor,
        )

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
        return localization

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Nimmt mehrere Bilder auf, mittelt die Posen und meldet die Module."""
        from tagloc.localize import expected_but_missing, merge_by_module, merge_samples

        pose_base_flange = robot_pose_from_parameters(request.parameters)
        self._reset_job_report()

        samples, world_samples, seen_timestamp = await self._capture_samples(pose_base_flange)

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

        # Vor dem Zusammenfuehren je Modul: `expected_but_missing` vergleicht
        # Tag-IDs, und mehrere zu einem Ergebnis verschmolzene Roboter-Tags
        # liessen die verschluckten Tags sonst fehlen.
        missing = expected_but_missing(self._tag_map, located)
        if missing:
            _log.info("Erwartet, aber nicht gefunden: %s", ", ".join(missing))

        # Mehrere Tags am Roboter messen dieselbe Basispose.
        located = merge_by_module(located, tag_map=self._tag_map)

        # Der Rahmen folgt dem, was tatsaechlich gerechnet wurde: mit
        # Referenztags das Welt-KS, ohne sie das Kamera-KS.
        self.frame_id = located[0].frame_id or self._config.frame_id

        if self._localization is None:
            self._warn_no_world_tag(located)

        return [
            self._build_detection(location, request, len(samples), seen_timestamp, missing)
            for location in located
        ]

    def _reset_job_report(self) -> None:
        """Setzt Drift und gemeldete Welttags zurueck, bevor das erste Bild kommt.

        Ein Job, der an der Kamera scheitert, darf nicht die Welttags des
        vorigen melden.
        """
        self._drift = None
        self.world_tags = ()
        self.camera_localization = None

    async def _capture_samples(self, pose_base_flange) -> tuple[list, list, float | None]:
        """Ein frisches Bild je Sample auswerten: `(Module je Bild, Welttags je Bild, letzter Zeitstempel)`."""
        samples: list[list] = []
        world_samples: list[list] = []
        seen_timestamp: float | None = None
        for _ in range(max(1, self._config.samples_per_job)):
            frame = await self._next_frame(seen_timestamp)
            seen_timestamp = frame.timestamp
            self._world_tag_sample = []
            samples.append(await self.run_blocking(self._locate, frame.image, pose_base_flange))
            world_samples.append(self._world_tag_sample)
        return samples, world_samples, seen_timestamp

    def _warn_no_world_tag(self, located) -> None:
        """Warnt, warum es kein Welt-KS gibt: welche Tags gesehen wurden und welche die Karte kennt.

        Ohne Welttag bleiben die Posen im Kamera-KS, und jeder Konsument
        muss sie ablehnen -- eine Kamerapose laesst sich nicht in der
        Zelle platzieren. Bisher stand nirgends, WARUM keiner gefunden
        wurde; genau daran scheitert die Fehlersuche beim Aufbau.
        """
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

    def _build_detection(self, location, request, sample_count, seen_timestamp, missing) -> Detection:
        """Baut die `Detection` eines Moduls samt Payload-Attributen."""
        from tagloc.geometry import to_position_quaternion

        position, orientation = to_position_quaternion(location.pose)
        return Detection(
            module_id=location.module_id,
            instance_id=location.instance_id,
            position=position,
            orientation=orientation,
            confidence=finite_or_none(location.confidence, PAYLOAD_DIGITS) or 0.0,
            attributes=self._detection_attributes(
                location, request, sample_count, seen_timestamp, missing
            ),
        )

    def _detection_attributes(self, location, request, sample_count, seen_timestamp, missing) -> dict:
        """Die Attribute einer Detektion: Tag, Qualitaet, Bezug zum Welttag, Kamerapose, Anker.

        Namen und Reihenfolge sind Teil der Payload-Schnittstelle.
        """
        from tagloc.geometry import to_position_quaternion

        localization = self._localization
        attributes = {
            "tagId": location.tag_id,
            "reprojErrorPx": finite_or_none(location.reprojection_error_px, PAYLOAD_DIGITS),
            "ambiguous": location.ambiguous,
            "sampleCount": location.attributes.get("sampleCount", sample_count),
            "frameTimestamp": seen_timestamp,
            "recipeId": request.recipe_id,
            "source": location.source,
            "role": location.role,
        }
        if location.attributes.get("tagCount"):
            # Mehrere Tags haben dieses Modul gemessen -- sagen, welche, damit
            # ein falscher CAD-Versatz an einem von ihnen auffindbar bleibt.
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
            attributes["referenceDistanceM"] = finite_or_none(
                math.dist(reference_position, (0.0, 0.0, 0.0)), PAYLOAD_DIGITS
            )
        if localization is not None:
            attributes["worldTagIds"] = list(localization.world_tag_ids)
            attributes["cameraSpreadM"] = finite_or_none(localization.spread_m, PAYLOAD_DIGITS)
            attributes["cameraSpreadDeg"] = finite_or_none(
                math.degrees(localization.spread_rad), PAYLOAD_DIGITS
            )
            # Ob die Kamerapose gemessen oder fortgeschrieben wurde, ist
            # kein Detail: die beiden haben nicht dieselbe Genauigkeit.
            attributes["cameraPoseOrigin"] = localization.origin
            if localization.solver:
                attributes["cameraPoseSolver"] = localization.solver
                attributes["cameraReprojectionErrorPx"] = finite_or_none(
                    localization.reprojection_error_px, PAYLOAD_DIGITS
                )
        if self._anchor is not None:
            attributes["anchorWorldTagId"] = self._anchor.world_tag_id
        if self._drift is not None:
            drift_m, drift_rad = self._drift
            attributes["anchorDriftM"] = finite_or_none(drift_m, PAYLOAD_DIGITS)
            attributes["anchorDriftDeg"] = finite_or_none(math.degrees(drift_rad), PAYLOAD_DIGITS)
        if missing:
            attributes["missingModules"] = ", ".join(missing)
        return attributes
