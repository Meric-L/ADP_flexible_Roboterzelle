"""Tests fuer die Erkennungs-Naht: DetectionRequest, run_blocking, Lifecycle."""

import asyncio
import threading
import time
import unittest

from vision_server.detection.base import Detection, DetectionRequest, DetectionSource
from vision_server.detection.hello_world import (
    FORCE_ERROR_PARAMETER,
    HelloWorldDetectionSource,
)
from vision_server.errors import VisionErrorCode, VisionJobError


class BlockingSource(DetectionSource):
    profile_id = "blocking"

    def __init__(self, seconds: float = 0.2) -> None:
        self._seconds = seconds
        self.thread_names: list[str] = []
        self.opened = 0
        self.closed = 0

    async def open(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1
        await self.shutdown_executor()

    def _work(self) -> str:
        self.thread_names.append(threading.current_thread().name)
        time.sleep(self._seconds)
        return "fertig"

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        await self.run_blocking(self._work)
        return []


class RunBlockingTest(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_block_the_event_loop(self):
        source = BlockingSource(seconds=0.3)
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker = asyncio.create_task(tick())
        await source.acquire_and_detect(DetectionRequest(job_id="job-000001"))
        ticker.cancel()
        # Ohne Executor waere der Loop 0,3 s tot und es gaebe fast keine Ticks.
        self.assertGreater(ticks, 5)
        await source.close()

    async def test_uses_a_named_single_worker_thread(self):
        source = BlockingSource(seconds=0.01)
        for _ in range(3):
            await source.acquire_and_detect(DetectionRequest(job_id="job-000001"))
        self.assertEqual(len(set(source.thread_names)), 1)
        self.assertTrue(source.thread_names[0].startswith("vision-blocking"))
        await source.close()

    async def test_close_is_idempotent(self):
        source = BlockingSource()
        await source.close()
        await source.close()
        self.assertEqual(source.closed, 2)

    async def test_close_works_without_open(self):
        await BlockingSource().close()


class HelloWorldTest(unittest.IsolatedAsyncioTestCase):
    async def test_reports_the_recipe_id(self):
        source = HelloWorldDetectionSource(latency=0.0)
        [detection] = await source.acquire_and_detect(
            DetectionRequest(job_id="job-000001", recipe_id="image-recognition")
        )
        self.assertEqual(detection.attributes["recipeId"], "image-recognition")

    async def test_stays_simulated_and_in_world_frame(self):
        source = HelloWorldDetectionSource(latency=0.0)
        self.assertTrue(source.is_simulated)
        self.assertEqual(source.frame_id, "world")

    async def test_force_error_parameter_still_fails(self):
        source = HelloWorldDetectionSource(latency=0.0)
        with self.assertRaises(VisionJobError) as caught:
            await source.acquire_and_detect(
                DetectionRequest(job_id="job-1", parameters=(FORCE_ERROR_PARAMETER,))
            )
        self.assertEqual(caught.exception.code, VisionErrorCode.DETECTION_FAILED)


if __name__ == "__main__":
    unittest.main()
