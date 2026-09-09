"""JSON-Payload fuer `ResultContent` (Schema aus Teil 4.3 des Plans)."""

import json
from collections.abc import Sequence
from datetime import datetime

from .detection import Detection
from .errors import VisionErrorCode

PAYLOAD_SCHEMA = "wsc.vision.detections/1"
FRAME_ID = "world"


def _envelope(
    *,
    vision_system_id: str,
    result_id: str,
    job_id: str,
    creation_time: datetime,
    result_state: int,
) -> dict:
    """Baut die schemagleichen Kopffelder jedes Payloads."""
    return {
        "schema": PAYLOAD_SCHEMA,
        "visionSystemId": vision_system_id,
        "resultId": result_id,
        "jobId": job_id,
        "creationTime": creation_time.isoformat(timespec="milliseconds"),
        "resultState": result_state,
        "frameId": FRAME_ID,
        "lengthUnit": "m",
        "angleUnit": "rad",
        "rotation": "quaternion_xyzw",
    }


def build_result_payload(
    *,
    vision_system_id: str,
    result_id: str,
    job_id: str,
    creation_time: datetime,
    detections: Sequence[Detection],
) -> str:
    """Serialisiert ein erfolgreiches Ergebnis."""
    payload = _envelope(
        vision_system_id=vision_system_id,
        result_id=result_id,
        job_id=job_id,
        creation_time=creation_time,
        result_state=int(VisionErrorCode.OK),
    )
    payload["detections"] = [
        {
            "moduleId": detection.module_id,
            "instanceId": detection.instance_id,
            "confidence": detection.confidence,
            "position": list(detection.position),
            "orientation": list(detection.orientation),
            "boundingBox": None,
            "attributes": detection.attributes,
        }
        for detection in detections
    ]
    return json.dumps(payload)


def build_error_payload(
    *,
    vision_system_id: str,
    result_id: str,
    job_id: str,
    creation_time: datetime,
    code: VisionErrorCode,
    message: str,
) -> str:
    """Serialisiert ein fehlgeschlagenes Ergebnis mit demselben Schema."""
    payload = _envelope(
        vision_system_id=vision_system_id,
        result_id=result_id,
        job_id=job_id,
        creation_time=creation_time,
        result_state=int(code),
    )
    payload["errorCode"] = int(code)
    payload["errorText"] = message
    payload["detections"] = []
    return json.dumps(payload)
