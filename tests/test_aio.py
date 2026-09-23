"""Tests fuer die asyncio-Helfer in `vision_server.aio`."""

import asyncio
import os
import signal
import threading
import unittest

from vision_server.aio import SerialExecutor, cancel_and_wait, stop_event_on_signals


class CancelAndWaitTest(unittest.IsolatedAsyncioTestCase):
    async def test_none_ist_erlaubt(self):
        await cancel_and_wait(None)

    async def test_bricht_ab_und_wartet(self):
        task = asyncio.create_task(asyncio.sleep(30))
        await asyncio.sleep(0)
        await cancel_and_wait(task)
        self.assertTrue(task.cancelled())

    async def test_ein_fertiger_task_bleibt_wie_er_ist(self):
        task = asyncio.create_task(asyncio.sleep(0, result=42))
        await task
        await cancel_and_wait(task)
        self.assertEqual(task.result(), 42)


class SerialExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_ein_benannter_thread_fuer_alle_aufrufe(self):
        executor = SerialExecutor("vision-test")
        names = await asyncio.gather(
            *(executor.run(lambda: threading.current_thread().name) for _ in range(3))
        )
        self.assertEqual(len(set(names)), 1)
        self.assertTrue(names[0].startswith("vision-test"))
        executor.shutdown()

    async def test_nach_shutdown_wieder_verwendbar(self):
        executor = SerialExecutor("vision-test")
        self.assertEqual(await executor.run(divmod, 7, 2), (3, 1))
        executor.shutdown()
        executor.shutdown()  # idempotent
        self.assertEqual(await executor.run(int, "12", base=8), 10)
        executor.shutdown()


@unittest.skipUnless(hasattr(signal, "SIGUSR1"), "kein SIGUSR1 auf dieser Plattform")
class StopEventTest(unittest.IsolatedAsyncioTestCase):
    async def test_signal_setzt_das_event(self):
        stop = stop_event_on_signals((signal.SIGUSR1,))
        self.assertFalse(stop.is_set())
        os.kill(os.getpid(), signal.SIGUSR1)
        await asyncio.wait_for(stop.wait(), 2.0)
        asyncio.get_running_loop().remove_signal_handler(signal.SIGUSR1)


if __name__ == "__main__":
    unittest.main()
