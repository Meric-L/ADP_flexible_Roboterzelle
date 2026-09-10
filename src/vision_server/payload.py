"""JSON-Payload fuer `ResultContent` (Schema aus Teil 4.3 des Plans)."""

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
    """numpy-Werte JSON-faehig machen.

    Entenartig statt per numpy-Import: dieses Modul liegt auf der Importkette
    des Zellenservers. Greift nicht fuer dict-Keys (siehe `_detection_dict`).
    """
    item = getattr(value, "item", None)
    if callable(item):  # numpy-Skalare
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
    """`NaN`/`Infinity` sind bewusst ein Fehler.

    Nacktes `NaN` ist kein gueltiges JSON — `JSON.parse` wirft und das Frontend
    stuerzt. So faellt der Job stattdessen in den Fehlerpfad.
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
    """Kopffelder jedes Payloads.

    `frameConvention`/`configurationId` nur bei nicht-leerem Wert — additiv,
    das Schema bleibt `wsc.vision.detections/1`.
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
    """Attribut-Keys zu `str`, weil `default=` fuer Keys nicht greift."""
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
) -> str:
    """Serialisiert ein erfolgreiches Ergebnis."""
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
) -> str:
    """Serialisiert ein fehlgeschlagenes Ergebnis mit demselben Schema."""
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
    return _dumps(payload)
