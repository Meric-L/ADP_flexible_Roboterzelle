"""Tests der Bausteine in `vision_server.runner`: JSON-Wrapper, Antworten, Sperr-Abfragen.

Der JSON-Wrapper (`json_method`) ersetzt den dreifach kopierten Rahmen von
`MeasureBoard`, `MeasureTags` und `MeasureTagReference` (Altlast D6). Diese
Tests nageln die Reihenfolge der Pruefungen fest: Anfrage lesen, Betriebszustand,
Sperre, Arbeit. Ohne Server: die Handler werden direkt aufgerufen, so wie
asyncua es tut (NodeId des Elternknotens, dann die Argumente als `ua.Variant`).
"""

import unittest
from pathlib import Path
from types import SimpleNamespace

from asyncua import ua

from vision_server.errors import VisionErrorCode
from vision_server.profiles import _REPO_ROOT
from vision_server.runner.uamethods import (
    code_reply,
    json_method,
    repo_relative,
    text_reply,
)
from vision_server.runner.wiring import Wiring

STRING = ua.VariantType.String
PARENT = ua.NodeId(1, 0)


def values(result) -> list:
    """Die Werte einer Methodenantwort."""
    return [variant.Value for variant in result]


class FakeStates:
    def __init__(self, state: str = "Operational") -> None:
        self.state = state

    def state_names(self) -> tuple[str, str]:
        return (self.state, "Ready")


class Recorder:
    """Merkt sich, welche Stufen des Wrappers erreicht wurden."""

    def __init__(self, *, parse_error: str | None = None, busy: bool = False) -> None:
        self.parse_error = parse_error
        self.is_busy = busy
        self.calls: list = []

    def parse(self, text: str):
        self.calls.append(("parse", text))
        if self.parse_error is not None:
            raise ValueError(self.parse_error)
        return {"text": text}

    def busy(self) -> bool:
        self.calls.append(("busy",))
        return self.is_busy

    async def run(self, request, text: str):
        self.calls.append(("run", request, text))
        return text_reply("fertig", VisionErrorCode.OK)

    @staticmethod
    def refuse(code: VisionErrorCode, message: str):
        return text_reply(message, code)


class JsonMethodTest(unittest.IsolatedAsyncioTestCase):
    def method(self, recorder: Recorder, state: str = "Operational", *, with_busy: bool = True):
        return json_method(
            "Messung",
            FakeStates(state),
            parse=recorder.parse,
            refuse=recorder.refuse,
            run=recorder.run,
            busy=recorder.busy if with_busy else None,
            busy_message="belegt",
        )

    async def call(self, method, text: str | None = "{}"):
        return await method(PARENT, ua.Variant(text, STRING))

    async def test_gueltige_anfrage_laeuft_bis_zur_arbeit(self):
        recorder = Recorder()
        result = await self.call(self.method(recorder), '{"a": 1}')
        self.assertEqual(values(result), ["fertig", int(VisionErrorCode.OK)])
        self.assertEqual(
            recorder.calls,
            [("parse", '{"a": 1}'), ("busy",), ("run", {"text": '{"a": 1}'}, '{"a": 1}')],
        )

    async def test_fehlendes_argument_gilt_als_leerer_text(self):
        recorder = Recorder()
        await self.call(self.method(recorder), None)
        self.assertEqual(recorder.calls[0], ("parse", ""))
        self.assertEqual(recorder.calls[-1], ("run", {"text": ""}, ""))

    async def test_ungueltige_anfrage_wird_geloggt_und_abgelehnt(self):
        recorder = Recorder(parse_error="kaputt")
        with self.assertLogs("vision_server.runner", "WARNING") as logs:
            result = await self.call(self.method(recorder))
        self.assertEqual(values(result), ["kaputt", int(VisionErrorCode.INVALID_ARGUMENT)])
        self.assertEqual(logs.output, ["WARNING:vision_server.runner:Messung abgelehnt: kaputt"])
        self.assertEqual(recorder.calls, [("parse", "{}")])

    async def test_nicht_operational_ist_invalid_state(self):
        recorder = Recorder()
        result = await self.call(self.method(recorder, "Halted"))
        self.assertEqual(
            values(result),
            ["Vision-System nicht betriebsbereit", int(VisionErrorCode.INVALID_STATE)],
        )
        # Der Zustand wird vor der Sperre geprueft: `busy` kommt gar nicht dran.
        self.assertEqual(recorder.calls, [("parse", "{}")])

    async def test_sperre_ist_busy_mit_dem_text_des_aufrufers(self):
        recorder = Recorder(busy=True)
        result = await self.call(self.method(recorder))
        self.assertEqual(values(result), ["belegt", int(VisionErrorCode.BUSY)])
        self.assertEqual(recorder.calls, [("parse", "{}"), ("busy",)])

    async def test_ohne_sperre_wird_nie_busy(self):
        recorder = Recorder(busy=True)
        result = await self.call(self.method(recorder, with_busy=False))
        self.assertEqual(values(result), ["fertig", int(VisionErrorCode.OK)])
        self.assertNotIn(("busy",), recorder.calls)

    async def test_anfrage_wird_vor_dem_zustand_geprueft(self):
        recorder = Recorder(parse_error="kaputt")
        with self.assertLogs("vision_server.runner", "WARNING"):
            result = await self.call(self.method(recorder, "Halted"))
        self.assertEqual(values(result)[1], int(VisionErrorCode.INVALID_ARGUMENT))


class ReplyTest(unittest.TestCase):
    def test_code_reply_hat_nur_error_als_int32(self):
        (variant,) = code_reply(VisionErrorCode.BUSY)
        self.assertEqual((variant.Value, variant.VariantType), (3, ua.VariantType.Int32))

    def test_text_reply_hat_string_und_error(self):
        text, code = text_reply("{}", VisionErrorCode.OK)
        self.assertEqual((text.Value, text.VariantType), ("{}", STRING))
        self.assertEqual((code.Value, code.VariantType), (0, ua.VariantType.Int32))

    def test_repo_relative_im_und_ausserhalb_des_repos(self):
        self.assertEqual(repo_relative(_REPO_ROOT / "data" / "a.png"), "data/a.png")
        self.assertEqual(repo_relative(Path("/tmp/fremd/a.png")), "/tmp/fremd/a.png")


class WiringTest(unittest.TestCase):
    """Die Sperr-Abfragen, die `install` den Installern als Lambdas uebergibt."""

    def wiring(self, **fields) -> Wiring:
        return Wiring(None, None, None, None, None, {}, None, **fields)

    def test_ohne_alles_ist_nichts_belegt(self):
        wiring = self.wiring()
        self.assertFalse(wiring.calibration_busy())
        self.assertFalse(wiring.calibration_processing())
        self.assertFalse(wiring.hand_eye_active())
        self.assertFalse(wiring.layer2_active())

    def test_kalibrierung_busy_und_processing_sind_getrennt(self):
        session = SimpleNamespace(busy=True, processing=False)
        wiring = self.wiring(calibration_session=session)
        self.assertTrue(wiring.calibration_busy())
        self.assertFalse(wiring.calibration_processing())
        session.processing = True
        self.assertTrue(wiring.calibration_processing())

    def test_sitzungen_werden_erst_beim_aufruf_gelesen(self):
        """Der Grund fuer `Wiring`: die Sitzungen entstehen nach den Handlern."""
        wiring = self.wiring()
        busy = lambda: wiring.hand_eye_active() or wiring.layer2_active()  # noqa: E731
        self.assertFalse(busy())
        wiring.hand_eye_session = SimpleNamespace(active=True)
        self.assertTrue(busy())
        wiring.hand_eye_session.active = False
        wiring.layer2_run = SimpleNamespace(active=True)
        self.assertTrue(busy())


if __name__ == "__main__":
    unittest.main()
