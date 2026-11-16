"""The job of the AprilTag source: capture images, locate modules, build detections.

`JobMixin` holds `acquire_and_detect` (the `DetectionSource` interface) and the
evaluation of a single image (`_locate`). It also has the functions that read job
inputs (`robot_pose_from_parameters`) and prepare job results for the payload
(world tags, camera localization). The mixin expects these attributes on the
object: `_config`, `_detector`, `_tag_map`, `_hand_eye`, `_anchor`, `_drift`,
`_localization`, `_world_tag_sample`, `world_tags`, `camera_localization` and
`frame_id`. It also relies on the methods of `CaptureMixin`.
"""

import logging
import math
from typing import Any

from tagloc.jsonio import finite_or_none

from ...errors import VisionErrorCode, VisionJobError
from ..base import Detection, DetectionRequest

_log = logging.getLogger(__package__)

#: Digits to which numbers in the detection payload are rounded.
PAYLOAD_DIGITS = 4

#: Frame id -> which of the two cameras it belongs to. It mirrors the frames that
#: `vision_server.server.PI_IDENTITIES` assigns per Pi. Plain strings are used on
#: purpose, not `tagloc.localize.SOURCE_*`. This module must stay importable
#: without numpy, so `tagloc` is only imported inside methods. An unknown frame
#: gives an empty source. `merge_locations` then sorts it behind both known
#: cameras instead of guessing.
SOURCE_BY_FRAME = {"cam_ceiling": "ceiling", "cam_flange": "flange"}


def robot_pose_from_parameters(parameters) -> Any | None:
    """Return `T_base_flange` from the job parameters, or ``None``.

    Seven floats: `(x, y, z, qx, qy, qz, qw)`. Meters and a quaternion in xyzw,
    as everywhere in the project. The robot stands still during the captures, so
    one pose applies to the whole job. That makes a timestamp match with each
    single frame unnecessary.

    If the parameter is missing or unusable, ``None`` is returned and the anchor
    stays unused. Nothing is guessed. A wrong robot pose shifts every module pose,
    and the result does not show it.
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
    """Return where this image sees the world tags.

    Each entry is `(tag_id, T_world_tag, reprojection_error, ambiguous,
    corner_error_under_joint_pose)`.

    `T_world_cam · T_cam_tag` uses the merged camera pose. The deviation from the
    world pose in the tag map is the error measure of the map. `locate_modules`
    deliberately leaves world tags out, because they are not modules. This needs
    a camera pose. Without one there is no world frame.
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
                # Corner errors of this tag under the joint pose. They show
                # which world tag does not match the map.
                getattr(localization, "tag_errors_px", {}).get(tag_pose.tag_id),
            )
        )
    return observed


def _localization_summary(localization) -> dict | None:
    """Build `cameraLocalization` for the payload: how the camera pose was obtained.

    It comes from the last image of the job, like the `camera*` attributes of the
    detections. Here it is also built without any detection, for an image with
    only world tags. A pose carried forward from the anchor is called `anchor` and
    has no reprojection error, because it was not measured.
    """
    if localization is None:
        return None
    import numpy as np
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
        # For every solver: the frontend uses this to cut the viewing ray to the
        # tag at its known height, instead of trusting the estimated depth.
        "cameraPositionInWorld": [
            finite_or_none(float(v), PAYLOAD_DIGITS)
            for v in np.asarray(localization.pose_world_cam)[:3, 3]
        ],
    }
    if not anchored and localization.solver == "joint":
        # The pose itself, only when it was estimated jointly from two or more
        # world tags. The cell watch stores it, so that a single world tag is
        # enough afterwards. The corner error per world tag is included as a baseline.
        from tagloc.geometry import pose_to_dict

        summary["poseInWorld"] = pose_to_dict(localization.pose_world_cam)
        summary["tagErrorsPx"] = {
            str(int(tag_id)): finite_or_none(error, PAYLOAD_DIGITS)
            for tag_id, error in localization.tag_errors_px.items()
        }
    return summary


def _merge_world_tags(samples) -> tuple:
    """Average each world tag over the images of a job, ready for JSON."""
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
    """`acquire_and_detect` and the evaluation of one image."""

    def _locate(self, image, pose_base_flange=None) -> list:
        """Evaluate one image. Runs in the worker thread, never on the loop."""
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
        # With a calibration: for two or more world tags, one joint PnP over all
        # their corners, instead of the mean of the single tags.
        localization = localize_camera(
            tag_poses,
            self._tag_map,
            max_reprojection_error_px=self._config.max_reproj_error_px,
            calibration=calibration,
        )
        localization = self._carry_with_anchor(localization, pose_base_flange)

        # Kept for `acquire_and_detect`, which reports the world tags and their
        # spread next to the poses. Only the single worker thread writes these,
        # so no lock is needed.
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
        """Anchor the camera pose to the world tag, or carry it forward from the anchor.

        The optical value always wins when a world tag is in the image. It is the
        measurement, everything else is carried forward. If no world tag is in the
        image, the anchor carries the pose. This is the case between two modules.
        Without hand-eye or without a robot pose nothing changes.
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
        """Capture several images, average the poses and report the modules."""
        from tagloc.localize import expected_but_missing, merge_by_module, merge_samples

        pose_base_flange = robot_pose_from_parameters(request.parameters)
        self._reset_job_report()

        samples, world_samples, seen_timestamp = await self._capture_samples(pose_base_flange)

        # Before the module check: an image with only world tags ends in
        # DETECTION_FAILED right away, but the world tags should still be reported.
        self.world_tags = _merge_world_tags(world_samples)
        self.camera_localization = _localization_summary(self._localization)
        located = merge_samples(samples)
        if not located:
            raise VisionJobError(
                VisionErrorCode.DETECTION_FAILED,
                f"Kein AprilTag der Familie '{self._config.tag_family}' gefunden",
            )

        # Before merging per module: `expected_but_missing` compares tag IDs.
        # Several robot tags merged into one result would otherwise hide the
        # swallowed tags as missing.
        missing = expected_but_missing(self._tag_map, located)
        if missing:
            _log.info("Erwartet, aber nicht gefunden: %s", ", ".join(missing))

        # Several tags on the robot measure the same base pose.
        located = merge_by_module(located, tag_map=self._tag_map)

        # The frame follows what was actually computed: the world frame when
        # reference tags are present, otherwise the camera frame.
        self.frame_id = located[0].frame_id or self._config.frame_id

        if self._localization is None:
            self._warn_no_world_tag(located)

        return [
            self._build_detection(location, request, len(samples), seen_timestamp, missing)
            for location in located
        ]

    def _reset_job_report(self) -> None:
        """Reset drift and the reported world tags before the first image arrives.

        A job that fails at the camera must not report the world tags of the
        previous job.
        """
        self._drift = None
        self.world_tags = ()
        self.camera_localization = None

    async def _capture_samples(self, pose_base_flange) -> tuple[list, list, float | None]:
        """Evaluate one fresh image per sample: `(modules per image, world tags per image, last timestamp)`."""
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
        """Warn why there is no world frame: which tags were seen and which the map knows.

        Without a world tag the poses stay in the camera frame, and every consumer
        must reject them. The log names the seen tags and the known world tags, so
        the cause is visible.
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
        """Build the `Detection` of a module, including its payload attributes."""
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
        """Return the attributes of a detection: tag, quality, world tag reference, camera pose, anchor.

        Names and order are part of the payload interface.
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
            # Several tags measured this module. Record which ones, so that a
            # wrong CAD offset on one of them can still be traced.
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
            # Whether the camera pose was measured or carried forward matters:
            # the two do not have the same accuracy.
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
