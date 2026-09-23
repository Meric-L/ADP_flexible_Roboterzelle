"""From tag to module. Pure composition, no OpenCV.

This is the **only** place in the project where the chain

    T_world_module = T_world_cam @ T_cam_tag @ T_tag_module

is executed. Layer 1 and Layer 2 call the same function; they differ only
in where `pose_world_cam` comes from:

* Layer 1 (ceiling camera): from the reference tags in the same image
* Layer 2 (flange camera): from the robot pose -- while hand-eye calibration
  is still open, `None` is passed and results stay in the camera frame
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .geometry import Pose, compose, invert
from .observations import TagPose
from .tagmap import TagMap, merge_tag_poses

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModuleLocation:
    """A located module."""

    module_id: str
    instance_id: str
    pose: Pose
    frame_id: str
    tag_id: int
    reprojection_error_px: float = float("nan")
    ambiguous: bool = False
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)


def confidence_from(tag_pose: TagPose, *, max_reprojection_error_px: float = 3.0) -> float:
    """Compute confidence from reprojection error and ambiguity, in [0, 1].

    Deliberately not a constant 1.0 as in earlier sources: a confidence
    that's always 1.0 carries no information and tempts downstream logic
    to trust it anyway.
    """
    error = tag_pose.reprojection_error_px
    if math.isnan(error) or max_reprojection_error_px <= 0.0:
        quality = 0.5
    else:
        quality = max(0.0, 1.0 - error / max_reprojection_error_px)
    if tag_pose.is_ambiguous:
        quality *= 0.5
    return round(min(1.0, max(0.0, quality)), 4)


def camera_pose_from_reference_tags(
    tag_poses: Sequence[TagPose], tag_map: TagMap
) -> Pose | None:
    """Determine `T_world_cam` from all tags with a known world pose.

    For each reference tag, `T_world_cam = T_world_tag @ T_cam_tag^-1`.
    Multiple reference tags give multiple estimates, which are averaged.
    No reference tag visible -> `None`, and the caller stays in the camera
    frame.
    """
    references = tag_map.reference_poses()
    candidates = [
        compose(references[tag_pose.tag_id], invert(tag_pose.pose_cam_tag))
        for tag_pose in tag_poses
        if tag_pose.tag_id in references
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        _log.debug("Kamerapose aus %d Referenz-Tags gemittelt", len(candidates))
    return merge_tag_poses(candidates)


def locate_modules(
    tag_poses: Sequence[TagPose],
    tag_map: TagMap,
    *,
    pose_world_cam: Pose | None,
    frame_id: str,
    max_reprojection_error_px: float = 3.0,
) -> list[ModuleLocation]:
    """Convert module tags into module poses.

    Tags without a map entry are still included -- with their tag ID as the
    module identifier and no CAD offset. An unknown tag is information, not
    an error: most likely the map entry is simply missing.
    """
    located: list[ModuleLocation] = []
    for tag_pose in tag_poses:
        entry = tag_map.get(tag_pose.tag_id)
        if entry is not None and entry.is_reference:
            continue  # Reference tags are anchor points, not modules.
        chain = [tag_pose.pose_cam_tag]
        if entry is not None:
            chain.append(entry.tag_to_module)
        if pose_world_cam is not None:
            chain.insert(0, pose_world_cam)
        located.append(
            ModuleLocation(
                module_id=(entry.module_id if entry and entry.module_id else f"TAG-{tag_pose.tag_id}"),
                instance_id=(
                    entry.instance_id if entry and entry.instance_id else f"tag-{tag_pose.tag_id}"
                ),
                pose=compose(*chain),
                frame_id=frame_id,
                tag_id=tag_pose.tag_id,
                reprojection_error_px=tag_pose.reprojection_error_px,
                ambiguous=tag_pose.is_ambiguous,
                confidence=confidence_from(
                    tag_pose, max_reprojection_error_px=max_reprojection_error_px
                ),
                attributes=dict(tag_pose.attributes),
            )
        )
    return located


def locate_in_frame(
    tag_poses: Sequence[TagPose],
    tag_map: TagMap,
    *,
    camera_frame_id: str,
    max_reprojection_error_px: float = 3.0,
) -> tuple[Pose | None, list[ModuleLocation]]:
    """Ein Bild auswerten: Kamerapose aus Referenz-Tags, Bezugsrahmen, Module.

    Sind Referenz-Tags im Bild, landen die Module im KS der Karte
    (`tag_map.frame_id`), sonst bleiben sie im Kamera-KS `camera_frame_id`.
    Das ist kein Sonderfall, sondern der normale Layer-2-Betrieb: Module
    werden trotzdem aufgeloest, nur relativ zur Kamera.

    Gibt `(T_world_cam oder None, verortete Module)` zurueck -- die
    Kamerapose mit, weil die CLI sie anzeigt.
    """
    pose_world_cam = camera_pose_from_reference_tags(tag_poses, tag_map)
    frame_id = tag_map.frame_id if pose_world_cam is not None else camera_frame_id
    located = locate_modules(
        tag_poses,
        tag_map,
        pose_world_cam=pose_world_cam,
        frame_id=frame_id,
        max_reprojection_error_px=max_reprojection_error_px,
    )
    return pose_world_cam, located


def merge_samples(samples: Sequence[Sequence[ModuleLocation]]) -> list[ModuleLocation]:
    """Merge several samples of the same job into one result per module.

    Averaging only spans samples where the same tag appeared; the count ends
    up as `sampleCount` in the attributes, so the result records how many
    images the pose is based on.
    """
    grouped: dict[int, list[ModuleLocation]] = {}
    order: list[int] = []
    for sample in samples:
        for location in sample:
            if location.tag_id not in grouped:
                grouped[location.tag_id] = []
                order.append(location.tag_id)
            grouped[location.tag_id].append(location)

    merged: list[ModuleLocation] = []
    for tag_id in order:
        group = grouped[tag_id]
        first = group[0]
        errors = [
            location.reprojection_error_px
            for location in group
            if not math.isnan(location.reprojection_error_px)
        ]
        attributes = dict(first.attributes)
        attributes["sampleCount"] = len(group)
        merged.append(
            ModuleLocation(
                module_id=first.module_id,
                instance_id=first.instance_id,
                pose=merge_tag_poses([location.pose for location in group]),
                frame_id=first.frame_id,
                tag_id=tag_id,
                reprojection_error_px=(sum(errors) / len(errors)) if errors else float("nan"),
                ambiguous=any(location.ambiguous for location in group),
                confidence=min(location.confidence for location in group),
                attributes=attributes,
            )
        )
    return merged


def expected_but_missing(
    tag_map: TagMap, located: Sequence[ModuleLocation]
) -> list[str]:
    """Return modules from the map that were not found.

    Answers the abort criterion from concept/offene_punkte.md no. 6 without
    extra configuration: the expected module list lives in the tag map.
    """
    seen = {location.tag_id for location in located}
    return sorted(
        entry.module_id or f"TAG-{entry.tag_id}"
        for entry in tag_map.module_entries()
        if entry.tag_id not in seen
    )


__all__ = [
    "ModuleLocation",
    "camera_pose_from_reference_tags",
    "confidence_from",
    "expected_but_missing",
    "locate_in_frame",
    "locate_modules",
    "merge_samples",
]
