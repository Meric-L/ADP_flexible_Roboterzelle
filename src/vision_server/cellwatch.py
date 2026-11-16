"""Cell watch (layer 1, ceiling camera): was a module moved, added or removed?

The Pi continuously compares what the ceiling camera sees with a reference -- either
the layer-1 result that the frontend holds in the viewport, or the current stable
observation ("Ignore"). A change is only reported once it is **confirmed**. A single
bad image, a person in the way or the robot arm in front of a tag must not trigger
an alarm.

For each tag, the module pose is monitored that this tag measures (tag pose times the
CAD offset from the tag map, as in the payload). The rules:

* Only images with a jointly solved camera pose (`joint`) and a plausible reprojection
  error count. All others count as "no statement", not as "tag missing".
* moved: the median of the last `persist_s` seconds deviates by more than
  `moved_threshold_m` or `rotated_threshold_deg` (rotation around the vertical axis)
  for longer than `persist_s`. For ambiguous tags only the position counts.
* appeared: a tag outside the reference is visible over `persist_s` in the majority
  of images.
* disappeared: a reference tag is missing for longer than `missing_persist_s`, and
  this holds for so many consecutive valid images that chance is ruled out given its
  past detection rate (`required_misses`). A tag that is only detected in every fifth
  image is often missing for 20 s purely by chance -- it counts as `unreliable` and
  needs longer.
* Hysteresis: a change only clears once the tag has been stable back for `persist_s`
  (below half the threshold, or seen again).

World tags are not monitored -- the source does not deliver them in the first place.

There is no asyncua and no camera here: observations come in as data. See
`doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .errors import VisionErrorCode

_log = logging.getLogger(__name__)

START_SCHEMA = "wsc.vision.cellwatch.start/1"
STATUS_SCHEMA = "wsc.vision.cellwatch.status/1"

OFF = "off"
WATCHING = "watching"
CHANGED = "changed"
PAUSED = "paused"
FAILED = "failed"

#: `PauseCellWatch`: pause duration if the caller names none, and the longest allowed.
#: Afterwards the watch resumes on its own, even if nobody calls `ResumeCellWatch`
#: (frontend crashed, robot stopped).
DEFAULT_PAUSE_S = 600.0
MAX_PAUSE_S = 3600.0
DEFAULT_PAUSE_REASON = "Ueberwachung pausiert"

MOVED = "moved"
APPEARED = "appeared"
DISAPPEARED = "disappeared"

#: A tag only counts as gone once it has been missing in this many valid images --
#: two images in 20 s would be no statement.
MIN_FRAMES_FOR_MISSING = 3
#: How unlikely it must be that an existing tag is missed by chance for the required
#: consecutive images. Deliberately very small: checked every 2 s, a tag with a 40 %
#: detection rate (measured on the ceiling Pi, 15.10.2026) produces about 700 gaps per
#: hour. With 1 % there were several false alarms per hour, with 0.01 % in simulation
#: still one in ten hours, with 0.001 % none in 20 hours.
MISS_BY_CHANCE = 1e-5
#: Upper limit of the required misses -- even a badly detected tag should eventually be
#: reportable as missing (at 2 s: 2 min).
MAX_REQUIRED_MISSES = 60
#: Below this detection rate a tag counts as unreliably detected.
UNRELIABLE_RATE = 0.5
#: A detection rate is only a statement from this many images on.
MIN_FRAMES_FOR_RATE = 5
#: A module counts as lying flat if its vertical axis deviates from the world Z by at
#: most this much. Then its yaw angle is also reliable under an ambiguous pose: the two
#: solutions differ only in tilt (simulated, top view from 2.5 m: 225 of 300 poses
#: ambiguous, yaw error of both solutions at most 6 degrees).
FLAT_MAX_TILT_DEG = 30.0


def is_flat(pose) -> bool:
    """Return whether the module lies flat (vertical axis near world Z, up or down)."""
    return abs(float(np.asarray(pose, dtype=np.float64)[2, 2])) >= math.cos(
        math.radians(FLAT_MAX_TILT_DEG)
    )


def conservative_rate(hits: int, frames: int) -> float:
    """Return the lower bound of the 95 % Wilson interval of the detection rate.

    A measured rate from few images scatters strongly: with a true 40 %, 60 % out of 20
    images is not rare. An overestimated rate would demand too few misses (found with a
    simulated hour). The lower bound estimates cautiously.
    """
    if frames <= 0:
        return 0.0
    z = 1.96
    p = hits / frames
    denominator = 1 + z * z / frames
    centre = p + z * z / (2 * frames)
    margin = z * math.sqrt(p * (1 - p) / frames + z * z / (4 * frames * frames))
    return max(0.0, (centre - margin) / denominator)


def required_misses(rate: float) -> int:
    """Return the consecutive misses before a tag with this detection rate counts as gone.

    Solves `(1 - rate)^n < MISS_BY_CHANCE` for `n`, clamped to the allowed range.
    """
    rate = min(max(rate, 0.05), 0.99)
    needed = math.ceil(math.log(MISS_BY_CHANCE) / math.log(1.0 - rate))
    return min(MAX_REQUIRED_MISSES, max(MIN_FRAMES_FOR_MISSING, needed))


@dataclass(frozen=True)
class CellWatchOptions:
    moved_threshold_m: float = 0.05
    rotated_threshold_deg: float = 10.0
    persist_s: float = 5.0
    missing_persist_s: float = 20.0
    #: Interval between evaluations. Detection on the full ceiling camera frame is
    #: expensive on the Pi -- 2 s instead of 1 s halves the load.
    interval_s: float = 2.0
    #: Images with a larger RMS of the camera pose count as "no statement".
    #: 6 px instead of 3: on Pi 1 the joint PnP with a calibration of 0.4 px stays stable
    #: at 3.3 px -- the rest is about 1 cm inaccuracy of the tag map. Against the 5 cm
    #: move threshold this is no reason to see nothing at all; a gross error (wrong tag
    #: size, shifted world tag) lies far above it.
    max_camera_error_px: float = 6.0
    #: Minimum number of world tags in the image. From 2 on, the Pi re-estimates the
    #: camera pose (joint PnP, accurate to about 6 mm). With 1 it uses the stored pose of
    #: the fixed camera and only checks the one world tag for consistency -- from a single
    #: tag the pose at 2.5 m would be inaccurate by centimeters (simulated: median 40 mm,
    #: outliers up to 1.5 m from tilted solutions, 31-38 % of images above 5 cm).
    #: With 2 it works as before: below two world tags the watch pauses.
    min_world_tags: int = 1
    #: How many more corner errors than when the pose was stored the one world tag may
    #: have before "camera moved?" applies. Measured: unmoved at most about 1 px more; at
    #: 5 mm shift about 1.4 px, at 10 mm about 3.4 px, at 0.1 degree about 1.4 px. So
    #: 2 px detects shifts from about 7 mm on.
    stored_pose_margin_px: float = 2.0

    def to_dict(self) -> dict:
        return {
            "movedThresholdM": self.moved_threshold_m,
            "rotatedThresholdDeg": self.rotated_threshold_deg,
            "persistS": self.persist_s,
            "missingPersistS": self.missing_persist_s,
            "intervalS": self.interval_s,
            "maxCameraErrorPx": self.max_camera_error_px,
            "minWorldTags": self.min_world_tags,
            "storedPoseMarginPx": self.stored_pose_margin_px,
        }


@dataclass(frozen=True)
class TagSighting:
    """A tag in a valid image: the module pose it measures."""

    position: tuple[float, float, float]
    #: Rotation around the vertical axis of the world frame, degrees.
    yaw_deg: float
    ambiguous: bool = False
    module_id: str = ""
    #: Lying flat: then the yaw angle counts even if `ambiguous`.
    flat: bool = False
    #: Camera position in the world frame: this lets the line of sight to the tag be
    #: intersected with a fixed height (the estimated depth scatters).
    camera_position: tuple[float, float, float] | None = None

    @property
    def has_yaw(self) -> bool:
        return not self.ambiguous or self.flat


@dataclass(frozen=True)
class ReferenceTag:
    position: tuple[float, float, float]
    yaw_deg: float
    module_id: str = ""
    #: Without a reliable rotation (ambiguous), only the position is compared.
    has_yaw: bool = True


@dataclass
class _TagState:
    exceeded_since: float | None = None
    #: Deviation at the last image, for `pending`.
    last_distance: float | None = None
    last_rotation: float | None = None
    back_since: float | None = None
    last_seen: float | None = None
    last_seen_wall: datetime | None = None
    change: dict | None = None


@dataclass
class CellWatchTracker:
    """Evaluate valid images against a reference. Pure, without a clock: every observation
    carries its time (`t` monotonic, `wall` for the status)."""

    options: CellWatchOptions
    reference: dict[int, ReferenceTag]
    started_at: float
    _frames: deque = field(default_factory=deque)
    _tags: dict[int, _TagState] = field(default_factory=dict)
    #: Last `(t, wall)` -- converts monotonic times to wall-clock time.
    _clock: tuple | None = None

    def carry_from(self, previous: "CellWatchTracker", *, same_reference: bool) -> None:
        """Take over the history of a previous watch (when arming again).

        The image history -- and with it the detection rate per tag -- applies
        regardless of the reference. Without it, "disappeared" would need up to 2 min
        after every change of a time or threshold. With the same reference, running
        candidates and reports also remain; with a new one, earlier images do not count
        as "missing".
        """
        self._frames = deque(previous._frames)
        self._clock = previous._clock
        if same_reference:
            self._tags = previous._tags
            self.started_at = previous.started_at

    def restart(self, t: float) -> None:
        """Start over after a pause: images from before do not count any more, all timers
        start at `t`, and all tags count as just seen.

        Confirmed reports remain -- the usual hysteresis clears them. Candidates
        (`pending`) are gone: during the pause the robot moved, and what it shifted in
        that time would not have been seen.
        """
        self._frames.clear()
        self.started_at = t
        for state in self._tags.values():
            state.exceeded_since = None
            state.back_since = None
            state.last_distance = None
            state.last_rotation = None
            state.last_seen = t

    def _wall_at(self, t: float) -> str | None:
        if self._clock is None:
            return None
        clock_t, clock_wall = self._clock
        return (clock_wall - timedelta(seconds=clock_t - t)).isoformat(timespec="seconds")

    def update(self, t: float, wall: datetime, sightings: dict[int, TagSighting]) -> None:
        """Process one valid image. Invalid images never get here."""
        self._clock = (t, wall)
        self._frames.append((t, sightings))
        # Long enough for the detection rate before a gap and for up to
        # `MAX_REQUIRED_MISSES` misses.
        horizon = t - max(
            4.0 * max(self.options.persist_s, self.options.missing_persist_s),
            3.0 * MAX_REQUIRED_MISSES * self.options.interval_s,
        )
        while self._frames and self._frames[0][0] < horizon:
            self._frames.popleft()
        for tag_id in set(self.reference) | set(sightings) | set(self._tags):
            state = self._tags.setdefault(tag_id, _TagState())
            if tag_id in sightings:
                state.last_seen = t
                state.last_seen_wall = wall
            if tag_id in self.reference:
                self._update_reference_tag(tag_id, state, t, wall)
            else:
                self._update_new_tag(tag_id, state, t, wall)

    def detection_rate(self, tag_id: int, until: float) -> tuple[float, int]:
        """Return the share of valid images with the tag up to `until`, over the whole
        retained history (up to `2 * MAX_REQUIRED_MISSES` ticks): `(rate, images)`.
        """
        frames = [sightings for frame_t, sightings in self._frames if frame_t <= until]
        if not frames:
            return 0.0, 0
        return sum(1 for sightings in frames if tag_id in sightings) / len(frames), len(frames)

    def visibility(self, t: float) -> list[dict]:
        """Return per tag (reference and seen): how often it was detected in the last
        `missing_persist_s` seconds of valid images.

        This is the stable answer to "is the tag in the image?" -- a single image flickers
        for a tag at the detection limit.
        """
        start = t - self.options.missing_persist_s
        window = [sightings for frame_t, sightings in self._frames if frame_t > start]
        tag_ids = set(self.reference) | {tag_id for sightings in window for tag_id in sightings}
        result = []
        for tag_id in sorted(tag_ids):
            seen = sum(1 for sightings in window if tag_id in sightings)
            rate, rated = self.detection_rate(tag_id, until=t)
            state = self._tags.get(tag_id)
            last_wall = state.last_seen_wall if state is not None else None
            result.append(
                {
                    "tagId": int(tag_id),
                    "seenRatio": seen / len(window) if window else None,
                    "framesChecked": len(window),
                    "lastSeenAt": last_wall.isoformat(timespec="seconds") if last_wall else None,
                    "unreliable": bool(
                        rated >= MIN_FRAMES_FOR_RATE and 0.0 < rate < UNRELIABLE_RATE
                    ),
                }
            )
        return result

    def pending(self, t: float) -> list[dict]:
        """Return what currently looks like a change but is not reported yet -- for
        immediate display. `confirmInS` is the time until the report if it persists
        (for "disappeared" it is an estimate: the required number of misses depends on
        the detection rate).
        """
        options = self.options
        window = self._window(t)
        result = []
        for tag_id in sorted(set(self.reference) | set(self._tags)):
            state = self._tags.get(tag_id)
            if state is None or state.change is not None:
                continue
            seen = any(tag_id in sightings for _, sightings in window)
            if tag_id in self.reference:
                module_id = self.reference[tag_id].module_id
                if not seen:
                    last = state.last_seen if state.last_seen is not None else self.started_at
                    missing = sum(1 for frame_t, _ in self._frames if frame_t > last)
                    if missing < 2:
                        continue
                    rate, rated = self.detection_rate(tag_id, until=last)
                    needed = required_misses(conservative_rate(round(rate * rated), rated))
                    confirm = max(
                        options.missing_persist_s - (t - last),
                        (needed - missing) * options.interval_s,
                        0.0,
                    )
                    result.append(
                        _pending(tag_id, module_id, DISAPPEARED, self._wall_at(last), None, None, confirm)
                    )
                elif state.exceeded_since is not None:
                    confirm = max(options.persist_s - (t - state.exceeded_since), 0.0)
                    result.append(
                        _pending(
                            tag_id, module_id, MOVED, self._wall_at(state.exceeded_since),
                            state.last_distance, state.last_rotation, confirm,
                        )
                    )
            elif seen:
                first_seen = next(
                    (frame_t for frame_t, sightings in self._frames if tag_id in sightings), t
                )
                module_id = next(
                    (
                        sightings[tag_id].module_id
                        for _, sightings in reversed(self._frames)
                        if tag_id in sightings
                    ),
                    "",
                )
                confirm = max(options.persist_s - (t - first_seen), 0.0)
                result.append(
                    _pending(tag_id, module_id, APPEARED, self._wall_at(first_seen), None, None, confirm)
                )
        return result

    @property
    def changes(self) -> list[dict]:
        return [
            self._tags[tag_id].change
            for tag_id in sorted(self._tags)
            if self._tags[tag_id].change is not None
        ]

    def stable_observation(self, t: float) -> dict[int, ReferenceTag] | None:
        """Return the medians of the last `persist_s` seconds as the new reference -- only
        tags that were seen in the majority of images. `None` as long as the observation
        does not yet span `persist_s`.
        """
        window = self._window(t)
        if not window or t - window[0][0] < self.options.persist_s * 0.8:
            return None
        tag_ids = {tag_id for _, sightings in window for tag_id in sightings}
        reference = {}
        for tag_id in tag_ids:
            seen = [sightings[tag_id] for _, sightings in window if tag_id in sightings]
            if len(seen) * 2 <= len(window):
                continue
            position, yaw, has_yaw = _median(seen)
            reference[tag_id] = ReferenceTag(position, yaw or 0.0, seen[-1].module_id, has_yaw)
        return reference

    # --- Rules ------------------------------------------------------------

    def _window(self, t: float) -> list:
        return [frame for frame in self._frames if frame[0] > t - self.options.persist_s]

    def _update_reference_tag(self, tag_id: int, state: _TagState, t: float, wall) -> None:
        options = self.options
        reference = self.reference[tag_id]
        window = self._window(t)
        seen = [sightings[tag_id] for _, sightings in window if tag_id in sightings]

        # Disappeared: not seen for a long time, in enough valid images.
        last = state.last_seen if state.last_seen is not None else self.started_at
        missing_frames = sum(1 for frame_t, _ in self._frames if frame_t > last)
        # Without history (e.g. right after arming) the cautious rate is 0 -- then the
        # upper limit applies, not 3 images.
        rate, rated_frames = self.detection_rate(tag_id, until=last)
        needed = required_misses(conservative_rate(round(rate * rated_frames), rated_frames))
        if not seen and t - last > options.missing_persist_s and missing_frames >= needed:
            if state.change is None or state.change["kind"] != DISAPPEARED:
                state.change = _change(tag_id, DISAPPEARED, reference, None, None, None, wall)
            return
        if state.change is not None and state.change["kind"] == DISAPPEARED:
            # Only back once it is seen again in the majority of images.
            if len(seen) * 2 > len(window) and t - window[0][0] >= options.persist_s * 0.8:
                state.change = None
            else:
                return
        if not seen:
            return

        # The height scatters (depth error along the line of sight): project each
        # sighting onto the reference height, and measure the distance horizontally only.
        seen = [_on_height(s, reference.position[2]) for s in seen]
        position, yaw, has_yaw = _median(seen)
        distance = math.hypot(
            position[0] - reference.position[0], position[1] - reference.position[1]
        )
        rotation = (
            abs(_wrap_deg(yaw - reference.yaw_deg)) if has_yaw and reference.has_yaw else None
        )
        state.last_distance, state.last_rotation = distance, rotation
        exceeded = distance > options.moved_threshold_m or (
            rotation is not None and rotation > options.rotated_threshold_deg
        )
        back = distance < options.moved_threshold_m / 2 and (
            rotation is None or rotation < options.rotated_threshold_deg / 2
        )
        if exceeded:
            state.back_since = None
            if state.exceeded_since is None:
                state.exceeded_since = t
            if t - state.exceeded_since >= options.persist_s:
                since = state.change["since"] if state.change and state.change["kind"] == MOVED else None
                state.change = _change(tag_id, MOVED, reference, position, distance, rotation, wall)
                if since is not None:
                    state.change["since"] = since
            return
        state.exceeded_since = None
        if state.change is not None and state.change["kind"] == MOVED:
            if back:
                if state.back_since is None:
                    state.back_since = t
                if t - state.back_since >= options.persist_s:
                    state.change = None
                    state.back_since = None
            else:
                state.back_since = None
                # Between the thresholds: the alarm stays, with current values.
                since = state.change["since"]
                state.change = _change(tag_id, MOVED, reference, position, distance, rotation, wall)
                state.change["since"] = since

    def _update_new_tag(self, tag_id: int, state: _TagState, t: float, wall) -> None:
        options = self.options
        window = self._window(t)
        seen = [sightings[tag_id] for _, sightings in window if tag_id in sightings]
        if state.change is not None:
            # Only clears once it has not been seen for `persist_s`.
            if state.last_seen is not None and t - state.last_seen >= options.persist_s:
                state.change = None
            elif seen:
                position, _, _ = _median(seen)
                since = state.change["since"]
                state.change = _change(tag_id, APPEARED, None, position, None, None, wall, seen[-1].module_id)
                state.change["since"] = since
            return
        first_seen = next(
            (frame_t for frame_t, sightings in self._frames if tag_id in sightings), None
        )
        if (
            first_seen is not None
            and t - first_seen >= options.persist_s
            and len(seen) * 2 > len(window)
        ):
            position, _, _ = _median(seen)
            state.change = _change(tag_id, APPEARED, None, position, None, None, wall, seen[-1].module_id)


def _on_height(sighting: TagSighting, height_m: float) -> TagSighting:
    """Project the sighting onto the plane `z = height_m`.

    Without a camera position, or if the ray is too flat, the measured position is kept.
    """
    if sighting.camera_position is None:
        return sighting
    from tagloc.geometry import point_on_ray_at_height

    hit = point_on_ray_at_height(sighting.camera_position, sighting.position, height_m)
    return sighting if hit is None else replace(sighting, position=hit)


def _median(seen: list[TagSighting]) -> tuple[tuple[float, float, float], float | None, bool]:
    """Return the median position and the median rotation over the sightings with a
    reliable yaw angle (unambiguous, or lying flat).
    """
    position = tuple(float(v) for v in np.median([s.position for s in seen], axis=0))
    yaws = [s.yaw_deg for s in seen if s.has_yaw]
    if not yaws:
        return position, None, False
    # Average around the first value, so that -179/+179 does not give 0.
    anchor = yaws[0]
    yaw = anchor + float(np.median([_wrap_deg(y - anchor) for y in yaws]))
    return position, _wrap_deg(yaw), True


def _pending(tag_id, module_id, kind, since, distance, rotation, confirm_s) -> dict:
    return {
        "tagId": int(tag_id),
        "moduleId": module_id or "",
        "kind": kind,
        "since": since,
        "distanceM": distance,
        "rotationDeg": rotation,
        "confirmInS": round(float(confirm_s), 1),
    }


def _wrap_deg(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def _change(tag_id, kind, reference, position, distance, rotation, wall, module_id=""):
    return {
        "tagId": int(tag_id),
        "moduleId": (reference.module_id if reference is not None else module_id) or "",
        "kind": kind,
        "referencePosition": list(reference.position) if reference is not None else None,
        "currentPosition": list(position) if position is not None else None,
        "distanceM": distance,
        "rotationDeg": rotation,
        "since": wall.isoformat(timespec="seconds"),
    }


def yaw_deg(pose) -> float:
    """Return the rotation around the vertical axis (+z of the world frame), in degrees."""
    pose = np.asarray(pose, dtype=np.float64)
    return math.degrees(math.atan2(pose[1, 0], pose[0, 0]))


# --- JSON ---------------------------------------------------------------------


def parse_start(text: str) -> tuple[str | None, CellWatchOptions]:
    """Parse `wsc.vision.cellwatch.start/1`: returns `(referenceJobId, options)`."""
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", START_SCHEMA) != START_SCHEMA:
        raise ValueError(f"Erwartet schema „{START_SCHEMA}“.")
    job_id = data.get("referenceJobId")
    if job_id is not None and not isinstance(job_id, str):
        raise ValueError("referenceJobId muss ein String oder null sein.")
    defaults = CellWatchOptions()
    values = {}
    for key, name in (
        ("movedThresholdM", "moved_threshold_m"),
        ("rotatedThresholdDeg", "rotated_threshold_deg"),
        ("persistS", "persist_s"),
        ("missingPersistS", "missing_persist_s"),
        ("intervalS", "interval_s"),
        ("maxCameraErrorPx", "max_camera_error_px"),
        ("storedPoseMarginPx", "stored_pose_margin_px"),
    ):
        value = data.get(key, getattr(defaults, name))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise ValueError(f"{key} muss eine positive Zahl sein.")
        values[name] = float(value)
    min_world_tags = data.get("minWorldTags", defaults.min_world_tags)
    if (
        isinstance(min_world_tags, bool)
        or not isinstance(min_world_tags, int)
        or not 1 <= min_world_tags <= 4
    ):
        raise ValueError("minWorldTags muss eine Ganzzahl von 1 bis 4 sein.")
    return job_id or None, CellWatchOptions(min_world_tags=min_world_tags, **values)


def reference_from_payload(payload_json: str, world_frame: str) -> dict[int, ReferenceTag]:
    """Build the reference from a job result (`wsc.vision.detections/1`).

    For each tag, the pose of the detection it belongs to -- for merged detections
    (`attributes.tagIds`) every tag gets the same pose.

    Raises:
        ValueError: If there is no successful result, or it is not in the world frame.
    """
    from tagloc.geometry import pose_from_dict

    payload = json.loads(payload_json)
    if payload.get("resultState", 0) != 0 or payload.get("errorCode"):
        raise ValueError("Referenz-Job ist fehlgeschlagen.")
    if payload.get("frameId") != world_frame:
        raise ValueError(
            f"Referenz-Job liegt nicht im Welt-KS („{payload.get('frameId')}“) -- kein Welttag im Bild?"
        )
    reference = {}
    for detection in payload.get("detections") or []:
        attributes = detection.get("attributes") or {}
        if attributes.get("role") == "world":
            continue
        pose = pose_from_dict(
            {"position": detection["position"], "orientation": detection["orientation"]}
        )
        tag_ids = attributes.get("tagIds") or [attributes.get("tagId")]
        for tag_id in tag_ids:
            if isinstance(tag_id, int):
                reference[tag_id] = ReferenceTag(
                    position=tuple(float(v) for v in pose[:3, 3]),
                    yaw_deg=yaw_deg(pose),
                    module_id=str(detection.get("moduleId") or ""),
                    has_yaw=not attributes.get("ambiguous", False) or is_flat(pose),
                )
    return reference


def sightings_from_locations(locations, localization, options: CellWatchOptions):
    """Return the sightings of one image from `locate_modules` (not merged).

    Returns `(None, reason)` if the image allows no statement: no jointly solved camera
    pose, or a reprojection error that is too large.
    """
    from tagloc.localize import SOLVER_JOINT

    if localization is None:
        return None, NO_WORLD_TAG
    if localization.solver != SOLVER_JOINT:
        return None, "weniger als zwei Welt-Tags im Bild -- Kamerapose zu ungenau"
    error = localization.reprojection_error_px
    if not math.isfinite(error) or error > options.max_camera_error_px:
        return None, f"Kamerapose unplausibel (RMS {error:.1f} px){_worst_tag(localization)}"
    return _sightings(locations, _camera_position(localization)), ""


#: Pause reason without a world tag in the image. All reasons where localization fails
#: name "Welt-Tag" -- the frontend recognizes the pause by it.
NO_WORLD_TAG = "kein Welt-Tag im Bild -- keine Verortung"


def _camera_position(localization) -> tuple[float, float, float] | None:
    """Return the translation of `pose_world_cam`, or `None` without localization."""
    pose = getattr(localization, "pose_world_cam", None)
    if pose is None:
        return None
    return tuple(float(v) for v in np.asarray(pose)[:3, 3])


def _sightings(locations, camera_position=None) -> dict:
    sightings = {}
    for location in locations:
        sightings[int(location.tag_id)] = TagSighting(
            position=tuple(float(v) for v in np.asarray(location.pose)[:3, 3]),
            yaw_deg=yaw_deg(location.pose),
            ambiguous=bool(location.ambiguous),
            module_id=location.module_id,
            flat=is_flat(location.pose),
            camera_position=camera_position,
        )
    return sightings


class CameraPoseKeeper:
    """Remember the pose of the fixed ceiling camera and judge each image.

    * From two world tags on (joint PnP, error within bounds), the pose from the image
      applies, and it is remembered: average of the last `HISTORY` poses, plus for each
      world tag its corner error as baseline (it contains the map error of about 1 cm
      that the joint solution cannot subtract).
    * With exactly one world tag, the remembered pose applies. The tag is only checked
      for consistency: if its corners lie more than `stored_pose_margin_px` above the
      baseline, the camera has probably moved -- the image does not count. After
      `BAD_FRAMES_TO_DROP` such images in a row, the pose is discarded and two world tags
      are needed again.
    * Without a remembered pose (start, after discarding, new tag map) with only one
      world tag: pause, no guessing.

    The pose survives restarts: it is stored in `path` (only from `SAVE_MIN_POSES` poses
    on, and then at most every `SAVE_INTERVAL_S`), and the reference scan of a
    `StartCellWatch` can supply it (`adopt`). Both are protected by the consistency check:
    if the camera was moved during the pause, the one world tag no longer fits and the
    pose is dropped. A changed tag map invalidates it as well.
    """

    SCHEMA = "wsc.vision.cellwatch.camerapose/1"
    SAVE_MIN_POSES = 5
    SAVE_INTERVAL_S = 600.0
    HISTORY = 20
    #: If a new joint result deviates this far from the remembered pose, the camera was
    #: moved: the history starts over instead of averaging.
    RESET_TRANSLATION_M = 0.015
    RESET_ROTATION_DEG = 0.5
    BAD_FRAMES_TO_DROP = 5
    #: Weight of the new value in the baseline (moving average).
    BASELINE_ALPHA = 0.2

    def __init__(self, path: Path | None = None, clock=time.monotonic, wall=None) -> None:
        self._poses: deque = deque(maxlen=self.HISTORY)
        self._baseline: dict[int, float] = {}
        self._map_key: tuple | None = None
        self._bad_frames = 0
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._wall = wall or (lambda: datetime.now(timezone.utc))
        #: Where the pose comes from: `measured` (>= 2 world tags in one image), `file`
        #: (restored after a restart) or `scan` (taken from the reference scan). `None`
        #: without a pose.
        self._source: str | None = None
        self._saved_clock: float | None = None
        self._saved_at: str | None = None

    @staticmethod
    def _key(tag_map) -> tuple:
        return tuple(
            sorted(
                (int(tag_id), tuple(round(float(v), 4) for v in np.asarray(pose)[:3, 3]))
                for tag_id, pose in tag_map.reference_poses().items()
            )
        )

    def clear(self) -> None:
        self._poses.clear()
        self._baseline.clear()
        self._bad_frames = 0
        self._source = None

    def drop(self) -> None:
        """Discard the pose **and** delete the file: the pose no longer applies, and a
        restart must not restore it.
        """
        self.clear()
        self._saved_clock = None
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                _log.warning("Gemerkte Kamerapose %s nicht loeschbar", self._path, exc_info=True)

    def info(self) -> dict:
        """Return the status info: where the pose comes from and how firm it is."""
        return {
            "source": self._source,
            "poseCount": len(self._poses),
            "savedAt": self._saved_at,
        }

    # --- File ------------------------------------------------------------

    def load(self) -> bool:
        """Load the remembered pose from the file. `False` without a (usable) file."""
        if self._path is None or not self._path.is_file():
            return False
        from tagloc.geometry import pose_from_dict

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("schema") != self.SCHEMA:
                raise ValueError(f"Schema {data.get('schema')!r}")
            pose = pose_from_dict(data["poseWorldCam"])
            baseline = {int(k): float(v) for k, v in (data.get("baselinePx") or {}).items()}
            map_key = tuple(
                (int(tag_id), tuple(float(v) for v in position))
                for tag_id, position in data["mapKey"]
            )
            count = max(1, min(int(data.get("poseCount", 1)), self.HISTORY))
        except (OSError, ValueError, KeyError, TypeError) as error:
            _log.warning("Gemerkte Kamerapose %s unbrauchbar (%s) -- ignoriert", self._path, error)
            return False
        self.clear()
        # With the weight it had when saved: new measurements replace it gradually,
        # instead of displacing it at once.
        self._poses.extend([pose] * count)
        self._baseline = baseline
        self._map_key = map_key
        self._source = "file"
        self._saved_clock = self._clock()
        self._saved_at = data.get("savedAt")
        _log.info("Gemerkte Kamerapose aus %s wiederhergestellt (%d Posen)", self._path, count)
        return True

    def _save(self) -> None:
        if self._path is None or not self._poses:
            return
        from tagloc.geometry import average_poses, pose_to_dict

        saved_at = self._wall().isoformat(timespec="seconds")
        payload = {
            "schema": self.SCHEMA,
            "savedAt": saved_at,
            "poseWorldCam": pose_to_dict(average_poses(list(self._poses))),
            "baselinePx": {str(tag_id): round(value, 4) for tag_id, value in self._baseline.items()},
            "mapKey": [[tag_id, list(position)] for tag_id, position in (self._map_key or ())],
            "poseCount": len(self._poses),
        }
        temporary = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            # A failure also counts as an attempt: otherwise the warning with traceback
            # would appear for every image (every 2 s) as long as the location is broken.
            self._saved_clock = self._clock()
            _log.warning("Kamerapose nicht gespeichert nach %s", self._path, exc_info=True)
            return
        self._saved_clock = self._clock()
        self._saved_at = saved_at

    def _maybe_save(self) -> None:
        """Save only from `SAVE_MIN_POSES` poses on, then at most every `SAVE_INTERVAL_S`:
        the file lies on an SD card, and the pose hardly changes.
        """
        if self._path is None or len(self._poses) < self.SAVE_MIN_POSES:
            return
        if self._saved_clock is None or self._clock() - self._saved_clock >= self.SAVE_INTERVAL_S:
            self._save()

    def adopt(self, pose, baseline: dict, tag_map) -> bool:
        """Take over the camera pose of a reference scan (`True` if it applies).

        The scan has already localized the camera from several world tags, just like a
        measurement here. If a remembered pose exists, it wins as long as both agree (it
        is averaged over many images). If the fresh scan deviates clearly, the camera was
        moved -- then the scan counts.
        """
        from tagloc.geometry import (
            average_poses,
            rotation_distance_rad,
            translation_distance_m,
        )

        self.stored_for(tag_map)  # checks the tag map
        if self._poses:
            current = average_poses(list(self._poses))
            if (
                translation_distance_m(current, pose) <= self.RESET_TRANSLATION_M
                and math.degrees(rotation_distance_rad(current, pose)) <= self.RESET_ROTATION_DEG
            ):
                return False
            self.clear()
        self._poses.append(pose)
        self._baseline.update({int(k): float(v) for k, v in baseline.items()})
        self._source = "scan"
        self._save()
        _log.info("Kamerapose aus dem Referenz-Scan uebernommen")
        return True

    def stored_for(self, tag_map):
        """Return the remembered pose, or `None`. A new tag map discards it: the pose only
        holds against the world poses it was estimated with.
        """
        key = self._key(tag_map)
        if key != self._map_key:
            if self._poses:
                _log.info("Tag-Map hat sich geaendert -- gemerkte Kamerapose verworfen")
            self.drop()
            self._map_key = key
        if not self._poses:
            return None
        from tagloc.geometry import average_poses

        return average_poses(list(self._poses))

    def judge(self, locations, localization, options: CellWatchOptions, tag_map):
        """Return `(sightings, "")` or `(None, reason)`; as `sightings_from_locations`."""
        from tagloc.localize import SOLVER_JOINT, SOLVER_STORED

        self.stored_for(tag_map)  # checks the tag map
        if localization is None:
            return None, NO_WORLD_TAG
        if localization.solver == SOLVER_JOINT:
            sightings, reason = sightings_from_locations(locations, localization, options)
            if sightings is not None:
                self._remember(localization)
            return sightings, reason
        if options.min_world_tags >= 2:
            return None, "weniger als zwei Welt-Tags im Bild -- Kamerapose zu ungenau"
        if localization.solver != SOLVER_STORED:
            return None, (
                "Kamerapose noch nicht bekannt -- erst muessen 2 Welt-Tags gleichzeitig "
                "zu sehen sein"
            )
        errors = localization.tag_errors_px
        excess = {
            tag_id: error - self._baseline.get(tag_id, self._mean_baseline())
            for tag_id, error in errors.items()
        }
        worst = max(excess, key=excess.get) if excess else None
        if worst is None or excess[worst] > options.stored_pose_margin_px:
            self._bad_frames += 1
            if self._bad_frames >= self.BAD_FRAMES_TO_DROP:
                self.drop()
                return None, (
                    "Kamera bewegt? Der Welt-Tag passt nicht mehr zur gespeicherten Kamerapose "
                    "-- Pose verworfen, 2 Welt-Tags gleichzeitig noetig"
                )
            if worst is None:
                return None, "Welt-Tag nicht auswertbar -- keine Verortung"
            return None, (
                f"Welt-Tag {worst} passt nicht zur gespeicherten Kamerapose "
                f"(+{excess[worst]:.1f} px) -- Kamera bewegt?"
            )
        self._bad_frames = 0
        return _sightings(locations, _camera_position(localization)), ""

    def _mean_baseline(self) -> float:
        return sum(self._baseline.values()) / len(self._baseline) if self._baseline else 0.0

    def _remember(self, localization) -> None:
        """Take a good joint result into the remembered pose."""
        from tagloc.geometry import (
            average_poses,
            rotation_distance_rad,
            translation_distance_m,
        )

        self._bad_frames = 0
        pose = localization.pose_world_cam
        if self._poses:
            current = average_poses(list(self._poses))
            if (
                translation_distance_m(current, pose) > self.RESET_TRANSLATION_M
                or math.degrees(rotation_distance_rad(current, pose)) > self.RESET_ROTATION_DEG
            ):
                self.clear()
        if not self._poses:
            self._source = "measured"
        self._poses.append(pose)
        for tag_id, error in localization.tag_errors_px.items():
            previous = self._baseline.get(tag_id)
            self._baseline[tag_id] = (
                error
                if previous is None
                else (1 - self.BASELINE_ALPHA) * previous + self.BASELINE_ALPHA * error
            )
        self._maybe_save()


def camera_pose_from_payload(payload_json: str):
    """Return `(pose, corner error per world tag)` of the camera from a job result, or `None`.

    Only if the pose in the job was estimated jointly from at least two world tags
    (`cameraLocalization.solver == "joint"`): from a single tag it would be inaccurate
    by centimeters.
    """
    from tagloc.geometry import pose_from_dict

    try:
        summary = json.loads(payload_json).get("cameraLocalization") or {}
        if summary.get("solver") != "joint" or not summary.get("poseInWorld"):
            return None
        pose = pose_from_dict(summary["poseInWorld"])
        errors = {
            int(tag_id): float(value)
            for tag_id, value in (summary.get("tagErrorsPx") or {}).items()
            if value is not None
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return pose, errors


def _worst_tag(localization) -> str:
    """Return the world tag that fits the joint pose least -- the candidate for
    re-measurement.
    """
    errors = getattr(localization, "tag_errors_px", None) or {}
    if not errors:
        return ""
    tag_id = max(errors, key=errors.get)
    return f" -- am schlechtesten passt Welt-Tag {tag_id} ({errors[tag_id]:.1f} px), nachmessen?"


# --- Flow -------------------------------------------------------------------


@dataclass(frozen=True)
class CellWatchPorts:
    """What the watch needs from the server."""

    #: Evaluates the newest camera image: `(sightings, "")` for a valid image,
    #: `(None, reason)` for "no statement", `(None, "")` = no new image.
    #: Optionally a third element with the IDs of all detected tags (world and module
    #: tags) -- also with "no statement", for `visibleTagIds`.
    observe: Callable[[CellWatchOptions], Awaitable[tuple]]
    #: Reason why the camera is currently needed elsewhere, otherwise `""`.
    busy_reason: Callable[[], str]
    #: Payload of a recent job, `None` if unknown.
    result_for_job: Callable[[str], "str | None"]
    #: Coordinate frame of the tag map ("world").
    world_frame: Callable[[], str]
    #: Writes `CellWatchStatus`.
    publish: Callable[[str], Awaitable[None]]
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    #: Receives the camera pose of a reference scan: `(pose, corner errors)`.
    adopt_pose: Callable[[Any, dict], Any] | None = None
    #: Where the remembered camera pose comes from, for the status.
    pose_info: Callable[[], dict] | None = None


class CellWatch:
    """Start, stop, tick and status around the `CellWatchTracker`."""

    def __init__(self, ports: CellWatchPorts) -> None:
        self._ports = ports
        self._state = OFF
        self._message = ""
        self._options = CellWatchOptions()
        self._tracker: CellWatchTracker | None = None
        self._reference_job_id: str | None = None
        self._reference_id: str | None = None
        self._reference_source = ""
        self._counter = 0
        self._last_check: datetime | None = None
        #: All tags detected in the most recently checked image; `None` before the first.
        self._visible: list[int] | None = None
        self._task: asyncio.Task | None = None
        #: Externally ordered pause (`PauseCellWatch`): reason and deadline (monotonic).
        #: `None` = no pause.
        self._pause_reason: str | None = None
        self._pause_until = 0.0

    @property
    def active(self) -> bool:
        return self._state not in (OFF, FAILED)

    async def start(self, options_json: str, *, run_loop: bool = True) -> VisionErrorCode:
        """Arm the watch, or arm it again with a new reference."""
        try:
            job_id, options = parse_start(options_json)
        except ValueError as error:
            _log.warning("StartCellWatch abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        reference = None
        if job_id is not None:
            payload = self._ports.result_for_job(job_id)
            if payload is None:
                _log.warning("StartCellWatch abgelehnt: Job %s unbekannt", job_id)
                return VisionErrorCode.INVALID_ARGUMENT
            try:
                reference = reference_from_payload(payload, self._ports.world_frame())
            except (ValueError, KeyError, TypeError) as error:
                _log.warning("StartCellWatch abgelehnt: %s", error)
                return VisionErrorCode.INVALID_STATE
            # The scan has already localized the camera: with its pose, one world tag is
            # enough afterwards, even right after the start.
            scan_pose = camera_pose_from_payload(payload)
            if scan_pose is not None and self._ports.adopt_pose is not None:
                self._ports.adopt_pose(*scan_pose)

        now = self._ports.monotonic()
        previous = self._tracker if self.active else None
        # Same reference as before -- typical when the frontend only changes a time or
        # threshold and arms again with the same job.
        same_reference = (
            previous is not None
            and job_id is not None
            and job_id == self._reference_job_id
            and self._reference_source == "job"
        )
        self._options = options
        self._reference_job_id = job_id
        if not self.active:
            self._visible = None  # after a stop, show nothing old
        if reference is not None:
            if same_reference:
                # Same reference ID: it is the same reference, only reconfigured.
                self._tracker = CellWatchTracker(options, reference, now)
            else:
                self._set_reference(reference, "job", now)
        else:
            # "Ignore": what is currently seen stably becomes the reference.
            # If the watch is already running, its observation is usually enough.
            observed = previous.stable_observation(now) if previous is not None else None
            if observed is not None:
                self._set_reference(observed, "observation", now)
            else:
                self._tracker = CellWatchTracker(options, {}, now)
                self._reference_id = None
                self._reference_source = "observation"
        if previous is not None:
            self._tracker.carry_from(previous, same_reference=same_reference)
        self._state = WATCHING
        self._message = "" if self._reference_id else "Referenz wird aufgenommen"
        if self._pause_reason is not None:
            # Arming again does not lift an ordered pause.
            self._state, self._message = PAUSED, self._pause_reason
        _log.info(
            "Zellen-Ueberwachung scharf (%s)",
            f"Referenz Job {job_id}" if job_id else "Referenz aus der Beobachtung",
        )
        if run_loop and (self._task is None or self._task.done()):
            self._task = asyncio.ensure_future(self._loop())
        await self._publish()
        return VisionErrorCode.OK

    async def stop(self) -> VisionErrorCode:
        if not self.active:
            return VisionErrorCode.INVALID_STATE
        await self._halt()
        self._pause_reason = None
        self._state = OFF
        self._message = "gestoppt"
        await self._publish()
        return VisionErrorCode.OK

    async def pause(self, reason: str, max_pause_s: float = 0.0) -> VisionErrorCode:
        """Pause while the cell is not standing still (robot is moving).

        During the pause no image is evaluated and nothing is added to the history.
        `max_pause_s <= 0` means `DEFAULT_PAUSE_S`; longer than `MAX_PAUSE_S` is not
        allowed. Calling again changes the reason and the deadline. Only while the watch
        is running.
        """
        if not self.active:
            return VisionErrorCode.INVALID_STATE
        try:
            seconds = float(max_pause_s)
        except (TypeError, ValueError):
            return VisionErrorCode.INVALID_ARGUMENT
        if not math.isfinite(seconds):
            return VisionErrorCode.INVALID_ARGUMENT
        seconds = DEFAULT_PAUSE_S if seconds <= 0 else min(seconds, MAX_PAUSE_S)
        self._pause_reason = (reason or "").strip() or DEFAULT_PAUSE_REASON
        self._pause_until = self._ports.monotonic() + seconds
        self._state, self._message = PAUSED, self._pause_reason
        _log.info("Zellen-Ueberwachung pausiert: %s (hoechstens %.0f s)", self._pause_reason, seconds)
        await self._publish()
        return VisionErrorCode.OK

    async def resume(self) -> VisionErrorCode:
        """End the pause. Without an ordered pause it does nothing (`OK`)."""
        if not self.active:
            return VisionErrorCode.INVALID_STATE
        if self._pause_reason is not None:
            await self._resume_now()
        return VisionErrorCode.OK

    async def _resume_now(self) -> None:
        self._pause_reason = None
        tracker = self._tracker
        tracker.restart(self._ports.monotonic())
        if self._reference_id is None:
            self._state, self._message = WATCHING, "Referenz wird aufgenommen"
        elif tracker.changes:
            self._state, self._message = CHANGED, _describe(tracker.changes)
        else:
            self._state, self._message = WATCHING, ""
        _log.info("Zellen-Ueberwachung geht weiter")
        await self._publish()

    async def aclose(self) -> None:
        await self._halt()

    async def publish_idle(self) -> None:
        await self._publish()

    async def _halt(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _set_reference(self, reference: dict, source: str, now: float) -> None:
        self._counter += 1
        self._tracker = CellWatchTracker(self._options, reference, now)
        self._reference_id = f"cw-{self._counter:06d}"
        self._reference_source = source

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # never die silently
                _log.exception("Zellen-Ueberwachung fehlgeschlagen")
                self._state = FAILED
                self._message = f"Fehler: {error}"
                await self._publish()
                return
            await asyncio.sleep(self._options.interval_s)

    async def tick(self) -> None:
        """Run one evaluation step. Public for tests."""
        if not self.active:
            return
        if self._pause_reason is not None:
            if self._ports.monotonic() < self._pause_until:
                await self._pause(self._pause_reason)
                return
            _log.warning("Zellen-Ueberwachung: Pause abgelaufen, ohne Resume -- geht von selbst weiter")
            await self._resume_now()
        reason = self._ports.busy_reason()
        if reason:
            await self._pause(reason)
            return
        result = await self._ports.observe(self._options)
        sightings, reason = result[0], result[1]
        visible_changed = False
        if len(result) > 2 and result[2] is not None:
            visible = sorted(int(tag_id) for tag_id in result[2])
            visible_changed = visible != self._visible
            self._visible = visible
        if sightings is None:
            if reason:
                await self._pause(reason, force=visible_changed)
            return
        now = self._ports.monotonic()
        wall = self._ports.now()
        self._last_check = wall
        tracker = self._tracker
        tracker.update(now, wall, sightings)
        if self._reference_id is None:
            observed = tracker.stable_observation(now)
            if observed is not None:
                self._set_reference(observed, "observation", now)
                _log.info("Zellen-Ueberwachung: Referenz aus %d Tags aufgenommen", len(observed))
        if self._reference_id is None:
            self._state, self._message = WATCHING, "Referenz wird aufgenommen"
        elif self._tracker.changes:
            self._state, self._message = CHANGED, _describe(self._tracker.changes)
        else:
            self._state, self._message = WATCHING, _unreliable_hint(tracker.visibility(now))
        await self._publish()

    async def _pause(self, reason: str, *, force: bool = False) -> None:
        # Write only on a change -- or if the set of visible tags changed: when pausing,
        # the operator wants to see which world tag is missing.
        if force or self._state != PAUSED or self._message != reason:
            self._state, self._message = PAUSED, reason
            await self._publish()

    def status_json(self) -> str:
        tracker = self._tracker
        referenced = tracker is not None and self._reference_id is not None
        return json.dumps(
            {
                "schema": STATUS_SCHEMA,
                "state": self._state,
                "message": self._message,
                "referenceId": self._reference_id,
                "referenceJobId": self._reference_job_id,
                "referenceSource": self._reference_source or None,
                "lastCheckAt": self._last_check.isoformat(timespec="seconds")
                if self._last_check
                else None,
                "watchedTagIds": sorted(tracker.reference) if referenced else [],
                "visibleTagIds": self._visible if self.active else None,
                "cameraPose": self._ports.pose_info() if self._ports.pose_info else None,
                "tagVisibility": tracker.visibility(self._ports.monotonic())
                if tracker is not None and self.active
                else [],
                "options": self._options.to_dict(),
                # Externally ordered pause, otherwise `null`.
                "manualPause": self._manual_pause_info(),
                "changes": tracker.changes if referenced and self.active else [],
                # Not confirmed yet -- for immediate display.
                "pending": tracker.pending(self._ports.monotonic())
                if referenced and self.active
                else [],
            },
            allow_nan=False,
            default=float,
        )

    def _manual_pause_info(self) -> dict | None:
        if self._pause_reason is None or not self.active:
            return None
        remaining = max(0.0, self._pause_until - self._ports.monotonic())
        return {
            "reason": self._pause_reason,
            "resumesAt": (self._ports.now() + timedelta(seconds=remaining)).isoformat(timespec="seconds"),
        }

    async def _publish(self) -> None:
        try:
            await self._ports.publish(self.status_json())
        except Exception:
            _log.exception("CellWatchStatus konnte nicht geschrieben werden")


def _unreliable_hint(visibility: list[dict]) -> str:
    """Return a hint about tags at the detection limit -- not an alarm, but the reason
    why "not in the image" flickers.
    """
    weak = [entry for entry in visibility if entry["unreliable"]]
    if not weak:
        return ""
    parts = ", ".join(
        f"Tag {entry['tagId']} ({round(100 * (entry['seenRatio'] or 0))} %)" for entry in weak
    )
    return f"unsicher erkannt: {parts} der Bilder -- Tag groesser drucken, umsetzen oder Licht pruefen"


def _describe(changes: list[dict]) -> str:
    words = {MOVED: "verschoben", APPEARED: "neu", DISAPPEARED: "fehlt"}
    return ", ".join(
        f"{change['moduleId'] or 'Tag ' + str(change['tagId'])} {words[change['kind']]}"
        for change in changes
    )
