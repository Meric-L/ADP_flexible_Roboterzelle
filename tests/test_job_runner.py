"""Tests fuer JobRunner: Zulassung, Timeout, Ergebniswahrheit."""

import asyncio
import unittest
from dataclasses import replace

from vision_server.config import VisionServerConfig
from vision_server.detection.base import Detection, DetectionRequest, DetectionSource
from vision_server.errors import VisionErrorCode, VisionJobError
from vision_server.job import JobRunner


class FakeStates:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.transitions: list[str] = []

    def is_ready(self) -> bool:
        return self.ready

    def state_names(self) -> tuple[str, str]:
        return ("Operational", "Ready" if self.ready else "Initialized")

    async def to_single_execution(self) -> None:
        self.transitions.append("single_execution")

    async def to_ready(self) -> None:
        self.transitions.append("ready")

    async def abort_to_ready(self) -> None:
        self.transitions.append("abort_to_ready")

    async def to_error(self, message: str) -> None:
        self.transitions.append("error")

    async def recover(self) -> None:
        self.transitions.append("recover")


class FakeGenerator:
    def __init__(self, log: list[str], name: str) -> None:
        self._log = log
        self._name = name
        self.event = type("Event", (), {})()

    async def trigger(self, message: str = "") -> None:
        self._log.append(self._name)


class FakeEvents:
    def __init__(self) -> None:
        self.log: list[str] = []
        self.job_started = FakeGenerator(self.log, "job_started")
        self.acquisition_done = FakeGenerator(self.log, "acquisition_done")
        self.result_ready = FakeGenerator(self.log, "result_ready")
        self.ready = FakeGenerator(self.log, "ready")


class FakeResults:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.published: list = []

    async def publish(self, result) -> None:
        self._log.append("publish")
        self.published.append(result)


class ScriptedSource(DetectionSource):
    profile_id = "hello_world"

    def __init__(self, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self._delay = delay
        self._error = error

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return [
            Detection(
                module_id="M",
                instance_id="det-1",
                position=(0.0, 0.0, 0.0),
                orientation=(0.0, 0.0, 0.0, 1.0),
                confidence=1.0,
            )
        ]


def make_runner(source: DetectionSource, **config_overrides):
    config = replace(VisionServerConfig(), **config_overrides)
    states = FakeStates()
    events = FakeEvents()
    results = FakeResults(events.log)
    runner = JobRunner(config, states, events, results, {"hello_world": source})
    return runner, states, events, results


async def run_to_completion(runner: JobRunner, recipe_id: str = "") -> tuple[str, VisionErrorCode]:
    job_id, code = runner.start_single_job(None, None, recipe_id, None, [])
    if runner._task is not None:
        await runner._task
    return job_id, code


class HappyPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_publishes_before_firing_the_event(self):
        """Der Client liest den Ergebnisknoten auf das Event hin — Reihenfolge zaehlt."""
        runner, _, events, _ = make_runner(ScriptedSource())
        await run_to_completion(runner)
        self.assertLess(events.log.index("publish"), events.log.index("result_ready"))

    async def test_event_order(self):
        runner, _, events, _ = make_runner(ScriptedSource())
        await run_to_completion(runner)
        self.assertEqual(
            events.log,
            ["job_started", "acquisition_done", "publish", "result_ready", "ready"],
        )

    async def test_releases_busy(self):
        runner, _, _, _ = make_runner(ScriptedSource())
        await run_to_completion(runner)
        self.assertFalse(runner._busy)


class AdmissionTest(unittest.IsolatedAsyncioTestCase):
    async def test_second_call_before_any_await_is_busy(self):
        runner, _, _, _ = make_runner(ScriptedSource(delay=0.05))
        first, first_code = runner.start_single_job(None, None, "", None, [])
        _, second_code = runner.start_single_job(None, None, "", None, [])
        self.assertEqual(first_code, VisionErrorCode.OK)
        self.assertEqual(second_code, VisionErrorCode.BUSY)
        self.assertTrue(first)
        await runner._task

    async def test_unknown_recipe_is_rejected_without_a_job(self):
        runner, _, events, _ = make_runner(ScriptedSource())
        job_id, code = runner.start_single_job(None, None, "calibration", None, [])
        self.assertEqual(code, VisionErrorCode.UNKNOWN_RECIPE)
        self.assertEqual(job_id, "")
        self.assertEqual(events.log, [])

    async def test_not_ready_is_rejected(self):
        runner, states, _, _ = make_runner(ScriptedSource())
        states.ready = False
        _, code = runner.start_single_job(None, None, "", None, [])
        self.assertEqual(code, VisionErrorCode.INVALID_STATE)


class TimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_hanging_source_does_not_wedge_the_server(self):
        """Ohne Timeout blieb _busy fuer immer gesetzt: Rettung nur per Neustart."""
        runner, states, events, results = make_runner(
            ScriptedSource(delay=5.0), job_timeout=0.1
        )
        await run_to_completion(runner)

        self.assertFalse(runner._busy, "Automat muss wieder annehmen koennen")
        self.assertIn("recover", states.transitions)
        self.assertIn("result_ready", events.log)
        self.assertEqual(
            results.published[-1].result_state, int(VisionErrorCode.DETECTION_FAILED)
        )

    async def test_accepts_the_next_job_after_a_timeout(self):
        runner, _, _, _ = make_runner(ScriptedSource(delay=5.0), job_timeout=0.1)
        await run_to_completion(runner)
        runner._sources["hello_world"] = ScriptedSource()
        _, code = await run_to_completion(runner)
        self.assertEqual(code, VisionErrorCode.OK)


class ResultTruthTest(unittest.IsolatedAsyncioTestCase):
    async def test_is_simulated_follows_the_source(self):
        source = ScriptedSource()
        source.is_simulated = False
        runner, _, _, results = make_runner(source)
        await run_to_completion(runner)
        self.assertFalse(results.published[-1].is_simulated)

    async def test_recipe_id_reaches_the_result_node(self):
        runner, _, _, results = make_runner(ScriptedSource())
        await run_to_completion(runner, recipe_id="image-recognition")
        self.assertEqual(results.published[-1].recipe_id, "image-recognition")

    async def test_frame_id_prefers_the_source(self):
        source = ScriptedSource()
        source.frame_id = "cam_ceiling"
        runner, _, _, results = make_runner(source)
        await run_to_completion(runner)
        self.assertIn('"frameId": "cam_ceiling"', results.published[-1].payload_json)

    async def test_frame_id_falls_back_to_the_config(self):
        runner, _, _, results = make_runner(ScriptedSource(), frame_id="zelle")
        await run_to_completion(runner)
        self.assertIn('"frameId": "zelle"', results.published[-1].payload_json)

    async def test_detection_error_takes_the_error_path(self):
        error = VisionJobError(VisionErrorCode.DETECTION_FAILED, "kein Tag")
        runner, states, _, results = make_runner(ScriptedSource(error=error))
        await run_to_completion(runner)
        self.assertIn("recover", states.transitions)
        self.assertIn('"errorText": "kein Tag"', results.published[-1].payload_json)
        self.assertFalse(runner._busy)


if __name__ == "__main__":
    unittest.main()
