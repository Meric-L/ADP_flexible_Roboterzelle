"""MeasureTags: measure tags in camera coordinates, for the frontend control loop.

Layer 2 has been split anew: the controller (webskillcomposition) plans and
moves, and the hand Pi only measures. When the robot stands still, the
controller calls `MeasureTags`. The Pi uses only images that arrive **after**
the call, averages over some of them, and returns `T_cam_tag` per tag -- without
world CS, without CAD offset, without knowledge of the robot. From this, the
controller computes the next flange target with FK and `T_flansch_cam`.

No asyncua and no camera here: measurements come in as data.
See `doc/project_docs/work-plans/layer2-measuretags.md`.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from tagloc.jsonio import finite_or_none

REQUEST_SCHEMA = "wsc.vision.measuretag.request/1"
RESULT_SCHEMA = "wsc.vision.measuretag.result/1"

#: OpenCV optical frame: +Z out of the lens, +X right, +Y down -- as in the
#: detection payload (`frameConvention`).
FRAME_CONVENTION = "z_forward_x_right_y_down"

DEFAULT_FRAMES = 3
MAX_FRAMES = 10
#: From this distance to the image edge (as a fraction of the image size) a tag
#: counts as near the edge. A tag cut off by the edge is not detected at all, so
#: the hint comes earlier, while the tag is still fully in the image.
EDGE_MARGIN = 0.05

NO_FRAME = "NO_FRAME"


@dataclass(frozen=True)
class MeasureRequest:
    """A parsed `MeasureTags` request."""

    #: ``None`` = all detected tags.
    tag_ids: frozenset[int] | None
    #: Size per tag from the request, in m.
    sizes_m: dict[int, float]
    frames: int = DEFAULT_FRAMES


@dataclass(frozen=True)
class FrameMeasurement:
    """One evaluated image."""

    captured_at: datetime
    image_size: tuple[int, int]
    #: `TagPose` from `tagloc.pose.estimate_tag_poses`, already filtered to
    #: `tag_ids`.
    tag_poses: tuple


def parse_request(text: str) -> MeasureRequest:
    """Parse a `wsc.vision.measuretag.request/1` document; raise ValueError with a plain message.

    An empty string or `{}` is valid: all tags, default number of images.
    """
    try:
        data = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", REQUEST_SCHEMA) != REQUEST_SCHEMA:
        raise ValueError(f"Erwartet schema „{REQUEST_SCHEMA}“.")

    raw_ids = data.get("tagIds")
    tag_ids = None
    if raw_ids is not None:
        if not isinstance(raw_ids, list) or not all(_is_int(v) and v >= 0 for v in raw_ids):
            raise ValueError("tagIds muss eine Liste nicht-negativer Ganzzahlen oder null sein.")
        tag_ids = frozenset(raw_ids)

    raw_sizes = data.get("tagSizeM") or {}
    if not isinstance(raw_sizes, dict):
        raise ValueError("tagSizeM muss ein Objekt {\"<tagId>\": Meter} sein.")
    sizes = {}
    for key, value in raw_sizes.items():
        try:
            tag_id = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"tagSizeM: Schluessel „{key}“ ist keine Tag-ID.") from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise ValueError(f"tagSizeM[{key}] muss eine positive Zahl sein.")
        sizes[tag_id] = float(value)

    frames = data.get("frames", DEFAULT_FRAMES)
    if not _is_int(frames) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine Ganzzahl von 1 bis {MAX_FRAMES} sein.")
    return MeasureRequest(tag_ids, sizes, int(frames))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class SizeLookup:
    """Tag size with precedence: request, then tag map, then fallback. Also reports the source.

    Has the same interface as `TagMap.size_for`, so `estimate_tag_poses` can
    use it directly.
    """

    def __init__(self, request_sizes: dict[int, float], tag_map, default_m: float) -> None:
        self._request = request_sizes
        self._tag_map = tag_map
        self._default = default_m

    def size_for(self, tag_id: int, default_m: float | None = None) -> float:
        return self.size_and_source(tag_id)[0]

    def size_and_source(self, tag_id: int) -> tuple[float, str]:
        if tag_id in self._request:
            return self._request[tag_id], "request"
        entry = self._tag_map.get(tag_id) if self._tag_map is not None else None
        if entry is not None:
            return float(entry.size_m), "tagmap"
        return self._default, "default"


def build_result(
    frames: list[FrameMeasurement],
    *,
    frames_requested: int,
    frame_id: str,
    sizes: SizeLookup,
    intrinsics_source: str,
    calibration_id: str,
    error: str | None = None,
) -> dict:
    """Return the result JSON (`wsc.vision.measuretag.result/1`) as a dict.

    Merged per tag over all images:

    * Position: mean over all images in which the tag was visible. It is
      reliable even when the orientation is ambiguous.
    * Orientation: mean over the unambiguous images; only if there are none,
      over all of them -- and then `ambiguous: true`.
    * Pixels (`cornersPx`, `centerPx`, `decisionMargin`): from the last image,
      raw as the detector delivers them (distorted).
    """
    from tagloc.geometry import average_poses, to_position_quaternion

    by_tag: dict[int, list] = {}
    for frame in frames:
        for tag_pose in frame.tag_poses:
            by_tag.setdefault(int(tag_pose.tag_id), []).append(tag_pose)

    tags = []
    for tag_id in sorted(by_tag):
        seen = by_tag[tag_id]
        clear = [tag_pose for tag_pose in seen if not tag_pose.is_ambiguous]
        oriented = average_poses([tag_pose.pose_cam_tag for tag_pose in (clear or seen)])
        pose = np.array(oriented, dtype=np.float64)
        pose[:3, 3] = np.mean([np.asarray(t.pose_cam_tag)[:3, 3] for t in seen], axis=0)
        position, orientation = to_position_quaternion(pose)
        last = seen[-1]
        corners = (
            [[float(u), float(v)] for u, v in last.observation.corners]
            if last.observation is not None
            else None
        )
        size_m, size_source = sizes.size_and_source(tag_id)
        errors = [t.reprojection_error_px for t in seen if math.isfinite(t.reprojection_error_px)]
        tags.append(
            {
                "tagId": tag_id,
                "poseInCamera": {"position": list(position), "orientation": list(orientation)},
                "centerPx": list(np.mean(corners, axis=0)) if corners else None,
                "cornersPx": corners,
                "reprojectionErrorPx": float(np.mean(errors)) if errors else None,
                "decisionMargin": finite_or_none(
                    getattr(last.observation, "decision_margin", None)
                ),
                "sizeM": size_m,
                "sizeSource": size_source,
                # Orientation only from ambiguous images -- do not control on it.
                "ambiguous": not clear,
                "ambiguityRatio": float(max(t.ambiguity_ratio for t in seen)),
                "frameCount": len(seen),
                "distanceM": float(np.linalg.norm(pose[:3, 3])),
                "viewAngleDeg": view_angle_deg(pose),
                "nearEdge": near_edge(corners, frames[-1].image_size) if corners else [],
            }
        )

    return {
        "schema": RESULT_SCHEMA,
        "capturedAt": _iso(frames[0].captured_at) if frames else None,
        "capturedUntil": _iso(frames[-1].captured_at) if frames else None,
        "frameId": frame_id,
        "frameConvention": FRAME_CONVENTION,
        "imageSize": list(frames[-1].image_size) if frames else None,
        "intrinsicsSource": intrinsics_source,
        "calibrationId": calibration_id,
        "framesRequested": frames_requested,
        "framesUsed": len(frames),
        "tags": tags,
        "error": error,
    }


def near_edge(corners, image_size, margin: float = EDGE_MARGIN) -> list[str]:
    """Return the image edges that the tag comes closer to than ``margin``.

    For the search in the controller: if the tag is at the edge, pan in that
    direction instead of searching blindly. The order is fixed: left, right,
    top, bottom (image CS: x to the right, y downward).
    """
    width, height = image_size
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    sides = []
    if min(xs) < margin * width:
        sides.append("left")
    if max(xs) > (1.0 - margin) * width:
        sides.append("right")
    if min(ys) < margin * height:
        sides.append("top")
    if max(ys) > (1.0 - margin) * height:
        sides.append("bottom")
    return sides


def view_angle_deg(pose_cam_tag) -> float:
    """Return the angle in degrees between the tag normal and the line of sight to the camera.

    0 means head-on.
    """
    pose = np.asarray(pose_cam_tag, dtype=np.float64)
    distance = float(np.linalg.norm(pose[:3, 3]))
    if distance < 1e-9:
        return 0.0
    towards_camera = -pose[:3, 3] / distance
    cosine = float(np.clip(pose[:3, 2] @ towards_camera, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _iso(moment: datetime) -> str:
    """Format a timestamp as ISO 8601 with millisecond precision."""
    return moment.isoformat(timespec="milliseconds")


def dumps(result: dict) -> str:
    """Serialize a result dict to JSON text. NaN and infinity are rejected."""
    return json.dumps(result, allow_nan=False, default=float)
