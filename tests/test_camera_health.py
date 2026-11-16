"""Camera state as `DeviceHealth` (OPC 40100-2).

Two levels, tested separately: the mapping is a pure function and needs neither
loop nor address space; the publisher is driven against fakes. That the nodes
sit correctly in the real address space is checked by `test_asset_model.py`.
"""

import asyncio
import unittest

from tests._support import RecordingNode, run_briefly
from vision_server.camera import CameraStatus
from vision_server.camera_health import (
    CameraHealthPublisher,
    HealthAlarms,
    device_health,
    write_device_health,
)
from vision_server.nodeset_ids import DeviceHealth
from vision_server.profiles import CameraStreamConfig

STALE_S = 2.0

FAST_CONFIG = CameraStreamConfig(stale_frame_s=STALE_S, health_interval_s=0.01)


def status(**overrides) -> CameraStatus:
    """A healthy state, with individual fields changed."""
    fields = {
        "running": True,
        "has_handle": True,
        "last_outcome": "ok",
        "consecutive_failures": 0,
        "reopen_attempts": 0,
        "gave_up": False,
        "frame_age_s": 0.1,
    }
    fields.update(overrides)
    return CameraStatus(**fields)


class DeviceHealthMappingTest(unittest.TestCase):
    """One test per row of the mapping table in `camera_health.py`."""

    def _health(self, **overrides) -> DeviceHealth:
        return device_health(status(**overrides), stale_frame_s=STALE_S)

    def test_fresh_frames_are_normal(self):
        self.assertEqual(DeviceHealth.NORMAL, self._health(frame_age_s=0.1))

    def test_warmup_without_a_frame_is_check_function(self):
        self.assertEqual(DeviceHealth.CHECK_FUNCTION, self._health(frame_age_s=None))

    def test_a_reopen_in_progress_is_check_function(self):
        self.assertEqual(DeviceHealth.CHECK_FUNCTION, self._health(has_handle=False))

    def test_a_stale_frame_is_off_spec(self):
        """A hang that can still heal itself: yellow, not red."""
        self.assertEqual(DeviceHealth.OFF_SPEC, self._health(frame_age_s=STALE_S + 0.1))

    def test_a_frame_exactly_at_the_threshold_is_still_normal(self):
        self.assertEqual(DeviceHealth.NORMAL, self._health(frame_age_s=STALE_S))

    def test_a_camera_that_never_opened_is_failure(self):
        self.assertEqual(
            DeviceHealth.FAILURE,
            self._health(running=False, has_handle=False, frame_age_s=None),
        )

    def test_an_exhausted_reopen_budget_is_failure(self):
        """The final case beats any temporary one."""
        self.assertEqual(
            DeviceHealth.FAILURE, self._health(gave_up=True, frame_age_s=0.1)
        )

    def test_never_reports_maintenance_required(self):
        """That would need a wear counter; `RemainingLifeTime` is deliberately not created."""
        for overrides in (
            {},
            {"frame_age_s": None},
            {"has_handle": False},
            {"frame_age_s": 99.0},
            {"running": False},
            {"gave_up": True},
        ):
            self.assertNotEqual(
                DeviceHealth.MAINTENANCE_REQUIRED, self._health(**overrides)
            )


class DefaultsAreConsistentTest(unittest.TestCase):
    """The default values must match each other, otherwise OFF_SPEC is dead.

    OFF_SPEC applies from `stale_frame_s` and ends when the watchdog escalates
    after `frame_timeout_s`. If the threshold lies behind the timeout, the state
    is never reported, a silent failure no other test would notice.
    """

    def setUp(self):
        self.defaults = CameraStreamConfig()

    def test_the_off_spec_window_exists(self):
        self.assertLess(self.defaults.stale_frame_s, self.defaults.frame_timeout_s)

    def test_the_off_spec_window_is_sampled_at_least_twice(self):
        window = self.defaults.frame_timeout_s - self.defaults.stale_frame_s
        self.assertLessEqual(self.defaults.health_interval_s, window / 2)


class FakeCamera:
    """Returns a preset state, independent of the clock."""

    def __init__(self, value: CameraStatus) -> None:
        self.value = value
        self.reads = 0

    def status(self, now: float) -> CameraStatus:
        self.reads += 1
        return self.value


class PublishLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_writes_the_first_state_once(self):
        camera = FakeCamera(status())
        node = RecordingNode()

        await run_briefly(CameraHealthPublisher(camera, [node], FAST_CONFIG), 0.05)

        self.assertEqual([int(DeviceHealth.NORMAL)], node.written)

    async def test_writes_nothing_while_the_state_stays_the_same(self):
        """Nothing is written at rest, however many ticks pass."""
        camera = FakeCamera(status())
        node = RecordingNode()

        await run_briefly(CameraHealthPublisher(camera, [node], FAST_CONFIG), 0.1)

        self.assertGreater(camera.reads, 3, "der Loop hat kaum getickt")
        self.assertEqual(1, len(node.written))

    async def test_writes_again_when_the_state_changes(self):
        camera = FakeCamera(status())
        node = RecordingNode()
        publisher = CameraHealthPublisher(camera, [node], FAST_CONFIG)

        publisher.start()
        await asyncio.sleep(0.05)
        camera.value = status(frame_age_s=STALE_S + 1.0)
        await asyncio.sleep(0.05)
        await publisher.stop()

        self.assertEqual(
            [int(DeviceHealth.NORMAL), int(DeviceHealth.OFF_SPEC)], node.written
        )
        self.assertEqual(DeviceHealth.OFF_SPEC, publisher.health)

    async def test_writes_to_every_configured_node(self):
        """Root and image sensor carry the same value."""
        camera = FakeCamera(status())
        nodes = [RecordingNode("ns=6;s=Wurzel"), RecordingNode("ns=6;s=Sensor")]

        await run_briefly(CameraHealthPublisher(camera, nodes, FAST_CONFIG), 0.05)

        for node in nodes:
            self.assertEqual([int(DeviceHealth.NORMAL)], node.written)

    async def test_an_unwritable_node_does_not_kill_the_loop(self):
        broken = RecordingNode("ns=6;s=Kaputt", fail=True)
        healthy = RecordingNode("ns=6;s=Heil")
        camera = FakeCamera(status())

        await run_briefly(
            CameraHealthPublisher(camera, [broken, healthy], FAST_CONFIG), 0.05
        )

        self.assertEqual([int(DeviceHealth.NORMAL)], healthy.written)

    async def test_stop_is_idempotent(self):
        publisher = CameraHealthPublisher(FakeCamera(status()), [RecordingNode()], FAST_CONFIG)
        publisher.start()

        await publisher.stop()
        await publisher.stop()

    async def test_stop_without_start_does_nothing(self):
        publisher = CameraHealthPublisher(FakeCamera(status()), [RecordingNode()], FAST_CONFIG)

        await publisher.stop()


class GiveUpHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_writes_failure_before_it_exits(self):
        node = RecordingNode()
        publisher = CameraHealthPublisher(FakeCamera(status()), [node], FAST_CONFIG)
        exited = []

        await publisher.give_up_handler(lambda: exited.append(True))()

        self.assertEqual([int(DeviceHealth.FAILURE)], node.written)
        self.assertEqual([True], exited)

    async def test_exits_even_when_the_write_hangs(self):
        """The process must die so that systemd restarts it; a hanging write must not prevent that."""

        class HangingNode(RecordingNode):
            async def write_value(self, value) -> None:
                await asyncio.sleep(30)

        publisher = CameraHealthPublisher(
            FakeCamera(status()),
            [HangingNode()],
            CameraStreamConfig(health_interval_s=0.01),
        )
        exited = []

        await asyncio.wait_for(
            publisher.give_up_handler(lambda: exited.append(True))(), timeout=5.0
        )

        self.assertEqual([True], exited)


def _text(value) -> str:
    """`LocalizedText` or plain string; the tests want the text."""
    return getattr(value, "Text", None) or str(value)


class FakeEvent:
    def __init__(self) -> None:
        self.Severity = None
        self.Retain = None
        self.ActiveState = None
        self.Message = None
        self.SourceNode = None


class FakeGenerator:
    def __init__(self, log: list, etype) -> None:
        self.event = FakeEvent()
        self._log = log
        self._etype = etype

    async def trigger(self) -> None:
        self._log.append(
            (
                self._etype,
                bool(self.event.Retain),
                self.event.Severity,
                _text(self.event.ActiveState),
                _text(self.event.Message),
            )
        )


class FakeAlarmNode:
    def __init__(self, etype: str) -> None:
        self.etype = etype
        self.nodeid = etype

    async def read_type_definition(self):
        return self.etype


class FakeServer:
    """Only what `HealthAlarms` needs from the server."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fired: list = []
        self.fail = fail

    def get_node(self, nodeid):
        return nodeid

    async def get_event_generator(self, etype, emitter):
        if self.fail:
            raise RuntimeError("kein Generator")
        return FakeGenerator(self.fired, etype)


def _alarms(server: FakeServer) -> HealthAlarms:
    return HealthAlarms(
        server,
        emitter="VisionMachine",
        alarms={
            DeviceHealth.FAILURE: FakeAlarmNode("Failure"),
            DeviceHealth.CHECK_FUNCTION: FakeAlarmNode("CheckFunction"),
            DeviceHealth.OFF_SPEC: FakeAlarmNode("OffSpec"),
        },
        source=FakeAlarmNode("ImageSensor"),
    )


class HealthAlarmsTest(unittest.IsolatedAsyncioTestCase):
    """The standard-conforming event path: the DI DeviceHealthAlarms."""

    async def test_fires_the_matching_alarm(self):
        server = FakeServer()
        await _alarms(server).set(DeviceHealth.FAILURE, "hung")

        self.assertEqual(1, len(server.fired))
        etype, retain, severity, active, message = server.fired[0]
        self.assertEqual("Failure", etype)
        self.assertTrue(retain)
        self.assertEqual(900, severity)
        self.assertEqual("Active", active)
        self.assertIn("hung", message)

    async def test_normal_fires_nothing_when_nothing_was_active(self):
        """NORMAL is the absence of an alarm, not an alarm of its own."""
        server = FakeServer()
        await _alarms(server).set(DeviceHealth.NORMAL, "ok")

        self.assertEqual([], server.fired)

    async def test_normal_clears_the_active_alarm(self):
        server = FakeServer()
        alarms = _alarms(server)
        await alarms.set(DeviceHealth.OFF_SPEC, "ok")
        server.fired.clear()

        await alarms.set(DeviceHealth.NORMAL, "ok")

        self.assertEqual(1, len(server.fired))
        etype, retain, _, active, _ = server.fired[0]
        self.assertEqual("OffSpec", etype)
        self.assertFalse(retain)
        self.assertEqual("Inactive", active)

    async def test_only_one_alarm_is_active_at_a_time(self):
        """The state is one of five; two active alarms would be a contradiction."""
        server = FakeServer()
        alarms = _alarms(server)
        await alarms.set(DeviceHealth.OFF_SPEC, "ok")
        server.fired.clear()

        await alarms.set(DeviceHealth.FAILURE, "hung")

        self.assertEqual(
            [("OffSpec", False), ("Failure", True)],
            [(etype, retain) for etype, retain, *_ in server.fired],
        )

    async def test_the_same_state_twice_fires_once(self):
        server = FakeServer()
        alarms = _alarms(server)
        await alarms.set(DeviceHealth.FAILURE, "hung")
        await alarms.set(DeviceHealth.FAILURE, "hung")

        self.assertEqual(1, len(server.fired))

    async def test_names_the_component_as_source(self):
        server = FakeServer()
        captured = []

        class Recording(FakeServer):
            async def get_event_generator(self, etype, emitter):
                gen = FakeGenerator(self.fired, etype)
                captured.append(gen)
                return gen

        recording = Recording()
        await _alarms(recording).set(DeviceHealth.FAILURE, "hung")

        self.assertEqual("ImageSensor", captured[0].event.SourceNode)

    async def test_a_broken_generator_does_not_raise(self):
        """A failed alarm must not take the state variable down with it."""
        await _alarms(FakeServer(fail=True)).set(DeviceHealth.FAILURE, "hung")

    async def test_an_unserved_state_is_skipped(self):
        """MAINTENANCE_REQUIRED has no alarm; it must not raise."""
        server = FakeServer()
        await _alarms(server).set(DeviceHealth.MAINTENANCE_REQUIRED, "x")

        self.assertEqual([], server.fired)


class PublisherAlarmTest(unittest.IsolatedAsyncioTestCase):
    async def test_publishes_variable_and_alarm_together(self):
        server = FakeServer()
        alarms = _alarms(server)
        node = RecordingNode()
        camera = FakeCamera(status(frame_age_s=STALE_S + 1.0))
        publisher = CameraHealthPublisher(
            camera, [node], FAST_CONFIG, alarms=alarms
        )

        await publisher.publish_now()

        self.assertEqual([int(DeviceHealth.OFF_SPEC)], node.written)
        self.assertEqual("OffSpec", server.fired[0][0])

    async def test_give_up_handler_fires_the_failure_alarm(self):
        server = FakeServer()
        node = RecordingNode()
        publisher = CameraHealthPublisher(
            FakeCamera(status()), [node], FAST_CONFIG, alarms=_alarms(server)
        )
        exited = []

        await publisher.give_up_handler(lambda: exited.append(True))()

        self.assertEqual([int(DeviceHealth.FAILURE)], node.written)
        self.assertEqual("Failure", server.fired[0][0])
        self.assertEqual([True], exited)

    async def test_works_without_alarms(self):
        """Without configured alarms the variable remains the reporting path."""
        node = RecordingNode()
        publisher = CameraHealthPublisher(FakeCamera(status()), [node], FAST_CONFIG)

        await publisher.publish_now()

        self.assertEqual([int(DeviceHealth.NORMAL)], node.written)


class WriteDeviceHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_enumeration_as_int32(self):
        """OPC UA transports an enumeration as Int32; otherwise the real node
        would answer with BadTypeMismatch."""
        from asyncua import ua

        captured = []

        class OrderedNode(RecordingNode):
            async def write_value(self, value) -> None:
                captured.append(value)

        await write_device_health([OrderedNode()], DeviceHealth.OFF_SPEC)

        self.assertEqual(1, len(captured))
        self.assertEqual(ua.VariantType.Int32, captured[0].VariantType)
        self.assertEqual(int(DeviceHealth.OFF_SPEC), captured[0].Value)

    async def test_never_raises(self):
        await write_device_health([RecordingNode(fail=True)], DeviceHealth.NORMAL)


if __name__ == "__main__":
    unittest.main()
