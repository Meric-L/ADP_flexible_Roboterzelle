"""JSON payload for `ResultContent` (schema from section 4.3 of the plan)."""

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .detection.base import Detection
from .errors import VisionErrorCode

PAYLOAD_SCHEMA = "wsc.vision.detections/1"
DEFAULT_FRAME_ID = "world"


def _json_default(value: Any) -> Any:
    """Make numpy values JSON-serializable.

    Duck-typed instead of importing numpy: this module is on the import chain of
    the cell server. It does not apply to dict keys (see `_detection_dict`).
    """
    item = getattr(value, "item", None)
    if callable(item):  # numpy scalars
        return item()
    tolist = getattr(value, "tolist", None)
    if callable(tolist):  # ndarray
        return tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"nicht JSON-serialisierbar: {type(value).__name__}")


def _dumps(payload: dict) -> str:
    """`NaN` and `Infinity` are deliberately an error.

    A bare `NaN` is not valid JSON -- `JSON.parse` throws and the frontend
    crashes. The job therefore falls into the error path instead.
    """
    return json.dumps(payload, default=_json_default, allow_nan=False)


def _envelope(
    *,
    vision_system_id: str,
    result_id: str,
    job_id: str,
    creation_time: datetime,
    result_state: int,
    frame_id: str = DEFAULT_FRAME_ID,
    frame_convention: str = "",
    configuration_id: str = "",
) -> dict:
    """Return the header fields of every payload.

    `frameConvention` and `configurationId` are only added if non-empty --
    additive, the schema stays `wsc.vision.detections/1`.
    """
    envelope = {
        "schema": PAYLOAD_SCHEMA,
        "visionSystemId": vision_system_id,
        "resultId": result_id,
        "jobId": job_id,
        "creationTime": creation_time.isoformat(timespec="milliseconds"),
        "resultState": result_state,
        "frameId": frame_id,
        "lengthUnit": "m",
        "angleUnit": "rad",
        "rotation": "quaternion_xyzw",
    }
    if frame_convention:
        envelope["frameConvention"] = frame_convention
    if configuration_id:
        envelope["configurationId"] = configuration_id
    return envelope


def _detection_dict(detection: Detection) -> dict:
    """Convert attribute keys to `str`, because `default=` does not apply to keys."""
    return {
        "moduleId": detection.module_id,
        "instanceId": detection.instance_id,
        "confidence": detection.confidence,
        "position": list(detection.position),
        "orientation": list(detection.orientation),
        "boundingBox": (
            list(detection.bounding_box) if detection.bounding_box is not None else None
        ),
        "attributes": {str(key): value for key, value in detection.attributes.items()},
    }


def build_result_payload(
    *,
    vision_system_id: str,
    result_id: str,
    job_id: str,
    creation_time: datetime,
    detections: Sequence[Detection],
    frame_id: str = DEFAULT_FRAME_ID,
    frame_convention: str = "",
    configuration_id: str = "",
    world_tags: Sequence[dict] = (),
    camera_localization: dict | None = None,
) -> str:
    """Serialize a successful result.

    `worldTags` is only added if world tags were observed, and
    `cameraLocalization` only with a camera pose -- additive, the schema stays
    `wsc.vision.detections/1`.
    """
    payload = _envelope(
        vision_system_id=vision_system_id,
        result_id=result_id,
        job_id=job_id,
        creation_time=creation_time,
        result_state=int(VisionErrorCode.OK),
        frame_id=frame_id,
        frame_convention=frame_convention,
        configuration_id=configuration_id,
    )
    payload["detections"] = [_detection_dict(detection) for detection in detections]
    if world_tags:
        payload["worldTags"] = list(world_tags)
    if camera_localization:
        payload["cameraLocalization"] = dict(camera_localization)
    return _dumps(payload)


def build_error_payload(
    *,
    vision_system_id: str,
    result_id: str,
    job_id: str,
    creation_time: datetime,
    code: VisionErrorCode,
    message: str,
    frame_id: str = DEFAULT_FRAME_ID,
    frame_convention: str = "",
    configuration_id: str = "",
    world_tags: Sequence[dict] = (),
    camera_localization: dict | None = None,
) -> str:
    """Serialize a failed result with the same schema."""
    payload = _envelope(
        vision_system_id=vision_system_id,
        result_id=result_id,
        job_id=job_id,
        creation_time=creation_time,
        result_state=int(code),
        frame_id=frame_id,
        frame_convention=frame_convention,
        configuration_id=configuration_id,
    )
    payload["errorCode"] = int(code)
    payload["errorText"] = message
    payload["detections"] = []
    if world_tags:
        payload["worldTags"] = list(world_tags)
    if camera_localization:
        payload["cameraLocalization"] = dict(camera_localization)
    return _dumps(payload)
