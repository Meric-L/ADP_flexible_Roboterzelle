"""Tests fuer das Ergebnis-Payload (`wsc.vision.detections/1`)."""

import json
import unittest
from datetime import datetime, timezone

from vision_server.detection.base import Detection
from vision_server.errors import VisionErrorCode
from vision_server.payload import (
    DEFAULT_FRAME_ID,
    PAYLOAD_SCHEMA,
    build_error_payload,
    build_result_payload,
)

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)


class FakeNumpyScalar:
    """Verhaelt sich wie ein numpy-Skalar, ohne numpy als Testabhaengigkeit.

    `np.float32`/`np.int64`/`np.bool_` bieten genau dieses `.item()`; nur
    `np.float64` ist eine float-Subklasse und serialisiert ohnehin.
    """

    def __init__(self, value):
        self._value = value

    def item(self):
        return self._value


class FakeNumpyArray:
    """Verhaelt sich wie ein ndarray (`.tolist()`)."""

    def __init__(self, values):
        self._values = list(values)

    def tolist(self):
        return self._values


def detection(**overrides) -> Detection:
    values = {
        "module_id": "MOD-1",
        "instance_id": "det-1",
        "position": (0.1, 0.2, 0.3),
        "orientation": (0.0, 0.0, 0.0, 1.0),
        "confidence": 0.9,
    }
    values.update(overrides)
    return Detection(**values)


def result(**overrides) -> dict:
    values = {
        "vision_system_id": "vision-test",
        "result_id": "res-job-000001",
        "job_id": "job-000001",
        "creation_time": NOW,
        "detections": [],
    }
    values.update(overrides)
    return json.loads(build_result_payload(**values))


class SchemaStabilityTest(unittest.TestCase):
    def test_schema_id_is_pinned(self):
        """Regressionsschutz: das Schema darf NICHT hochversioniert werden.

        Die Interface-Doku verlangt, dass Konsumenten unbekannte Versionen
        verwerfen — ein Bump bricht das Backend am Deploy-Tag. Additive
        Schluessel sind erlaubt, eine neue Version nicht.
        """
        self.assertEqual(PAYLOAD_SCHEMA, "wsc.vision.detections/1")
        self.assertEqual(result()["schema"], "wsc.vision.detections/1")

    def test_success_and_error_envelopes_match(self):
        success = set(result().keys())
        error = set(
            json.loads(
                build_error_payload(
                    vision_system_id="vision-test",
                    result_id="res-job-000001",
                    job_id="job-000001",
                    creation_time=NOW,
                    code=VisionErrorCode.DETECTION_FAILED,
                    message="kaputt",
                )
            ).keys()
        )
        self.assertEqual(error - success, {"errorCode", "errorText"})
        self.assertEqual(success - error, set())


class FrameIdTest(unittest.TestCase):
    def test_defaults_to_world(self):
        self.assertEqual(result()["frameId"], DEFAULT_FRAME_ID)

    def test_frame_id_is_overridable(self):
        self.assertEqual(result(frame_id="cam_ceiling")["frameId"], "cam_ceiling")

    def test_additive_keys_are_omitted_when_empty(self):
        payload = result()
        self.assertNotIn("frameConvention", payload)
        self.assertNotIn("configurationId", payload)

    def test_additive_keys_are_emitted_when_set(self):
        payload = result(
            frame_convention="z_forward_x_right_y_down",
            configuration_id="tag36h11@ceiling#1757500000",
        )
        self.assertEqual(payload["frameConvention"], "z_forward_x_right_y_down")
        self.assertEqual(payload["configurationId"], "tag36h11@ceiling#1757500000")


class JsonSafetyTest(unittest.TestCase):
    def test_numpy_like_scalars_in_attributes(self):
        payload = result(
            detections=[
                detection(
                    attributes={
                        "reprojErrorPx": FakeNumpyScalar(0.42),
                        "tagId": FakeNumpyScalar(7),
                        "ambiguous": FakeNumpyScalar(False),
                    }
                )
            ]
        )
        attributes = payload["detections"][0]["attributes"]
        self.assertEqual(attributes["reprojErrorPx"], 0.42)
        self.assertEqual(attributes["tagId"], 7)
        self.assertIs(attributes["ambiguous"], False)

    def test_numpy_like_array_in_attributes(self):
        payload = result(
            detections=[detection(attributes={"corners": FakeNumpyArray([1.0, 2.0])})]
        )
        self.assertEqual(payload["detections"][0]["attributes"]["corners"], [1.0, 2.0])

    def test_non_string_attribute_keys_are_coerced(self):
        """`default=` wird fuer dict-Keys nie aufgerufen — die muessen vorher weg."""
        payload = result(detections=[detection(attributes={7: "sieben"})])
        self.assertEqual(payload["detections"][0]["attributes"], {"7": "sieben"})

    def test_nan_raises_instead_of_emitting_invalid_json(self):
        """Nacktes `NaN` ist kein gueltiges JSON; der Browser wuerde crashen."""
        with self.assertRaises(ValueError):
            build_result_payload(
                vision_system_id="vision-test",
                result_id="res-job-000001",
                job_id="job-000001",
                creation_time=NOW,
                detections=[detection(confidence=float("nan"))],
            )

    def test_unserialisable_value_raises_type_error(self):
        with self.assertRaises(TypeError):
            build_result_payload(
                vision_system_id="vision-test",
                result_id="res-job-000001",
                job_id="job-000001",
                creation_time=NOW,
                detections=[detection(attributes={"kaputt": object()})],
            )

    def test_error_payload_cannot_contain_nan(self):
        """Der Fehlerpfad muss serialisieren koennen, sonst gibt es eine Rekursion."""
        payload = json.loads(
            build_error_payload(
                vision_system_id="vision-test",
                result_id="res-job-000001",
                job_id="job-000001",
                creation_time=NOW,
                code=VisionErrorCode.INTERNAL,
                message="Out of range float values are not JSON compliant",
            )
        )
        self.assertEqual(payload["detections"], [])
        self.assertEqual(payload["errorCode"], int(VisionErrorCode.INTERNAL))


class BoundingBoxTest(unittest.TestCase):
    def test_defaults_to_none(self):
        payload = result(detections=[detection()])
        self.assertIsNone(payload["detections"][0]["boundingBox"])

    def test_is_serialised_as_list(self):
        payload = result(detections=[detection(bounding_box=(1.0, 2.0, 3.0, 4.0))])
        self.assertEqual(payload["detections"][0]["boundingBox"], [1.0, 2.0, 3.0, 4.0])


if __name__ == "__main__":
    unittest.main()
