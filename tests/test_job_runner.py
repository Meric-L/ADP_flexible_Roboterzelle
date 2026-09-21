"""Tests fuer JobRunner: Zulassung, Timeout, Ergebniswahrheit."""

import asyncio
import contextlib
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

    async def stop_to_ready(self) -> None:
        self.transitions.append("stop_to_ready")

    async def to_continuous_execution(self) -> None:
        self.transitions.append("continuous_execution")

    async def continuous_to_ready(self, *, stopped: bool = True) -> None:
        self.transitions.append(
            "continuous_stop_to_ready" if stopped else "continuous_abort_to_ready"
        )

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
    """Die Attrappe bedient jedes Profil, damit jedes Rezept sie trifft."""
    config = replace(VisionServerConfig(), **config_overrides)
    states = FakeStates()
    events = FakeEvents()
    results = FakeResults(events.log)
    sources = {profile: source for profile in config.detection_profiles}
    runner = JobRunner(config, states, events, results, sources)
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
        job_id, code = runner.start_single_job(None, None, "gibts-nicht", None, [])
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
        for profile in runner._sources:
            runner._sources[profile] = ScriptedSource()
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
        await run_to_completion(runner, recipe_id="apriltag")
        self.assertEqual(results.published[-1].recipe_id, "apriltag")

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


class SlowToCancelSource(DetectionSource):
    """Verschluckt die erste Cancellation, um `stop()`s Timeout-Pfad zu testen."""

    profile_id = "hello_world"

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(10.0)
        await asyncio.sleep(0.05)
        return []


class StopTest(unittest.IsolatedAsyncioTestCase):
    async def test_ok_when_nothing_is_running(self):
        runner, _, _, _ = make_runner(ScriptedSource())
        code = await runner.stop()
        self.assertEqual(code, VisionErrorCode.OK)

    async def test_ok_after_a_job_already_finished(self):
        runner, _, _, _ = make_runner(ScriptedSource())
        await run_to_completion(runner)
        code = await runner.stop()
        self.assertEqual(code, VisionErrorCode.OK)

    async def test_cancels_a_running_job_and_resets_the_state(self):
        runner, states, events, results = make_runner(ScriptedSource(delay=5.0))
        runner.start_single_job(None, None, "", None, [])
        await asyncio.sleep(0)  # Task muss erst anlaufen (single_execution etc.)

        code = await runner.stop()

        self.assertEqual(code, VisionErrorCode.OK)
        self.assertFalse(runner._busy, "BUSY darf einen neuen Job nicht mehr blockieren")
        self.assertIn("stop_to_ready", states.transitions)
        self.assertNotIn("to_error", states.transitions, "Stop ist kein Fehlerzustand")
        self.assertEqual(
            results.published[-1].result_state, int(VisionErrorCode.CANCELLED)
        )
        self.assertIn("ready", events.log)

    async def test_accepts_a_new_job_immediately_after_stop(self):
        runner, _, _, _ = make_runner(ScriptedSource(delay=5.0))
        runner.start_single_job(None, None, "", None, [])
        await asyncio.sleep(0)
        await runner.stop()

        for profile in runner._sources:
            runner._sources[profile] = ScriptedSource()
        _, code = await run_to_completion(runner)
        self.assertEqual(code, VisionErrorCode.OK)

    async def test_reports_internal_error_when_the_job_will_not_cancel_in_time(self):
        runner, _, _, _ = make_runner(SlowToCancelSource(), stop_timeout=0.01)
        runner.start_single_job(None, None, "", None, [])
        await asyncio.sleep(0)

        code = await runner.stop()

        self.assertEqual(code, VisionErrorCode.INTERNAL)
        # `wait_for` hat den Task bereits bis zum Abschluss (cancelled) durchlaufen
        # lassen, bevor es TimeoutError geworfen hat -- hier ist nichts mehr offen.
        self.assertTrue(runner._task.done())


if __name__ == "__main__":
    unittest.main()


class ContinuousTest(unittest.IsolatedAsyncioTestCase):
    """Dauerbetrieb: laeuft, bis jemand ihn beendet.

    Anders als ein Einzeljob endet er nie von allein, also pruefen die Tests
    beides -- dass er wirklich mehrfach liefert, und dass er sich wirklich
    beenden laesst.
    """

    async def _run_briefly(self, cycles: int = 2, **overrides):
        source = ScriptedSource()
        runner, states, events, results = make_runner(
            source, continuous_interval_s=0.0, **overrides
        )
        job_id, code = runner.start_continuous(None, None, "", None, [])
        self.assertEqual(VisionErrorCode.OK, code)
        for _ in range(200):
            await asyncio.sleep(0)
            if len(results.published) >= cycles:
                break
        return runner, states, events, results, job_id

    async def test_publishes_one_result_per_cycle(self):
        runner, states, _, results, _ = await self._run_briefly(cycles=3)
        self.assertGreaterEqual(len(results.published), 3)
        await runner.stop()
        self.assertIn("continuous_execution", states.transitions)

    async def test_gives_every_cycle_its_own_result_id(self):
        """Ein Client muss die Folge auseinanderhalten koennen."""
        runner, _, _, results, job_id = await self._run_briefly(cycles=3)
        await runner.stop()
        ids = [entry.result_id for entry in results.published[:3]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(identifier.startswith(f"res-{job_id}-") for identifier in ids))

    async def test_stop_ends_it_through_the_stop_transition(self):
        runner, states, _, results, _ = await self._run_briefly()
        self.assertEqual(VisionErrorCode.OK, await runner.stop())
        self.assertIn("continuous_stop_to_ready", states.transitions)
        self.assertEqual(int(VisionErrorCode.CANCELLED), results.published[-1].result_state)

    async def test_abort_ends_it_through_the_abort_transition(self):
        runner, states, _, _, _ = await self._run_briefly()
        self.assertEqual(VisionErrorCode.OK, await runner.stop(abort=True))
        self.assertIn("continuous_abort_to_ready", states.transitions)

    async def test_refuses_a_second_job_while_running(self):
        runner, _, _, _, _ = await self._run_briefly()
        _, code = runner.start_continuous(None, None, "", None, [])
        self.assertEqual(VisionErrorCode.BUSY, code)
        _, single = runner.start_single_job(None, None, "", None, [])
        self.assertEqual(VisionErrorCode.BUSY, single)
        await runner.stop()

    async def test_refuses_to_start_when_not_ready(self):
        source = ScriptedSource()
        runner, states, _, _ = make_runner(source)
        states.ready = False
        _, code = runner.start_continuous(None, None, "", None, [])
        self.assertEqual(VisionErrorCode.INVALID_STATE, code)

    async def test_a_failing_cycle_ends_the_run(self):
        """Sonst erzeugte dieselbe Stoerung im Sekundentakt dieselbe Meldung."""
        source = ScriptedSource(error=VisionJobError(
            VisionErrorCode.DETECTION_FAILED, "kaputt"
        ))
        runner, states, _, results = make_runner(source, continuous_interval_s=0.0)
        runner.start_continuous(None, None, "", None, [])
        with contextlib.suppress(asyncio.CancelledError):
            await runner._task
        self.assertEqual(int(VisionErrorCode.DETECTION_FAILED), results.published[-1].result_state)
        self.assertIn("continuous_abort_to_ready", states.transitions)
        self.assertFalse(runner._busy)
