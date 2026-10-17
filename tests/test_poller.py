import unittest
from datetime import datetime

from hs80 import protocol as P
from hs80.device import Hs80Device, DeviceError, DongleNotFound
from hs80.poller import Poller, RECOVERY_POLL_S
from hs80.state import Status
from tests import fakes


class Rig:
    """A poller wired to fake devices; `scenario` is a list of responders / exceptions, one per open."""

    def __init__(self, opens, interval=60):
        self.opens = list(opens)
        self.transports = []
        self.clk = fakes.FakeClock()
        self.changes = []
        self.polls = []
        self.present = True
        self.poller = Poller(interval, on_change=self.changes.append, on_poll=self.polls.append,
                             open_device=self.open, receiver_present=lambda: self.present,
                             clock=self.clk, sleep=self.clk.sleep,
                             now=lambda: datetime(2026, 10, 16, 13, 0))

    def open(self):
        item = self.opens.pop(0) if self.opens else fakes.default_responder
        if isinstance(item, Exception):
            raise item
        t = fakes.FakeTransport(item)
        self.transports.append(t)
        return Hs80Device(t, sleep=self.clk.sleep, clock=self.clk)

    def poll(self, n=1):
        for _ in range(n):
            self.clk.t += 60
            st = self.poller.poll_once()
        return st


def asleep(frame):
    return [] if frame[1] == P.ROUTE_HEADSET else fakes.default_responder(frame)


class PollerTests(unittest.TestCase):
    def test_ok_poll(self):
        rig = Rig([fakes.default_responder])
        st = rig.poll()
        self.assertIs(st.status, Status.OK)
        self.assertEqual(st.percent, 45.0)
        self.assertFalse(st.charging)
        self.assertEqual(st.charge_raw, 2)
        self.assertEqual(st.last_percent, 45.0)
        self.assertEqual(st.polls, 1)
        self.assertEqual(len(rig.changes), 1)
        self.assertEqual(st.summary(), "45.0% not charging")
        self.assertEqual(rig.poller.next_delay_s, 60)

    def test_requests_per_ok_poll_are_battery_and_charge_only(self):
        rig = Rig([fakes.default_responder])
        rig.poll()
        self.assertEqual(rig.transports[0].props_sent(),
                         [(P.ROUTE_HEADSET, P.PROP_BATTERY), (P.ROUTE_HEADSET, P.PROP_CHARGE)])

    def test_no_change_no_callback(self):
        rig = Rig([fakes.default_responder])
        rig.poll(3)
        self.assertEqual(len(rig.changes), 1)
        self.assertEqual(len(rig.polls), 3)

    def test_asleep_keeps_last_percent(self):
        mode = {"asleep": False}
        rig = Rig([lambda f: asleep(f) if mode["asleep"] else fakes.default_responder(f)])
        rig.poll()
        mode["asleep"] = True
        st = rig.poll()
        self.assertIs(st.status, Status.ASLEEP)
        self.assertIsNone(st.percent)
        self.assertEqual(st.last_percent, 45.0)
        self.assertEqual(st.summary(), "headset asleep/off, last 45%")
        mode["asleep"] = False
        st = rig.poll()
        self.assertIs(st.status, Status.OK)
        self.assertEqual([c.status for c in rig.changes], [Status.OK, Status.ASLEEP, Status.OK])
        self.assertEqual(len(rig.transports), 1)   # device stays open through ASLEEP

    def test_no_dongle_then_found(self):
        rig = Rig([DongleNotFound("none"), fakes.default_responder])
        st = rig.poll()
        self.assertIs(st.status, Status.NO_DONGLE)
        self.assertEqual(st.summary(), "receiver not found")
        st = rig.poll()
        self.assertIs(st.status, Status.OK)

    def test_error_with_receiver_present_drops_handle_and_reopens_next_poll(self):
        rig = Rig([fakes.default_responder, fakes.default_responder])
        rig.poll()
        rig.transports[0].fail_write = DeviceError("write failed")
        st = rig.poll()
        self.assertIs(st.status, Status.ERROR)
        self.assertTrue(rig.transports[0].closed)                 # handle dropped at once
        self.assertEqual(rig.poller.next_delay_s, RECOVERY_POLL_S)
        st = rig.poll()                                           # reopened on a fresh transport
        self.assertIs(st.status, Status.OK)
        self.assertEqual(len(rig.transports), 2)
        self.assertEqual(rig.poller.next_delay_s, 60)

    def test_unplug_shows_no_dongle_and_replug_recovers_next_poll(self):
        rig = Rig([fakes.default_responder, fakes.default_responder])
        rig.poll()
        rig.transports[0].fail_write = DeviceError("read failed: read error")
        rig.present = False                                       # receiver pulled
        st = rig.poll()
        self.assertIs(st.status, Status.NO_DONGLE)
        self.assertIsNone(st.error)
        self.assertEqual(st.last_percent, 45.0)
        self.assertTrue(rig.transports[0].closed)
        self.assertEqual(rig.poller.next_delay_s, RECOVERY_POLL_S)
        rig.opens.insert(0, DongleNotFound("none"))               # still out on the next look
        st = rig.poll()
        self.assertIs(st.status, Status.NO_DONGLE)
        rig.present = True                                        # plugged back in
        st = rig.poll()
        self.assertIs(st.status, Status.OK)
        self.assertEqual(st.percent, 45.0)
        self.assertEqual(len(rig.transports), 2)
        self.assertEqual([c.status for c in rig.changes], [Status.OK, Status.NO_DONGLE, Status.OK])

    def test_repeated_errors_back_off_up_to_the_interval(self):
        rig = Rig([DeviceError("open failed")] * 20, interval=60)
        delays = []
        for _ in range(6):
            rig.poll()
            delays.append(rig.poller.next_delay_s)
        self.assertEqual(delays, [15, 30, 60, 60, 60, 60])

    def test_no_dongle_rechecks_every_15s(self):
        rig = Rig([DongleNotFound("none")] * 3, interval=60)
        rig.poll()
        self.assertEqual(rig.poller.next_delay_s, RECOVERY_POLL_S)
        rig2 = Rig([DongleNotFound("none")], interval=10)
        rig2.poll()
        self.assertEqual(rig2.poller.next_delay_s, 10)

    def test_rate_gate(self):
        rig = Rig([fakes.default_responder])
        rig.poll()
        t = rig.clk()
        rig.poller.poll_once()          # immediately again -> must wait the 2 s gate
        self.assertGreaterEqual(rig.clk() - t, 2.0)

    def test_stop_closes_device(self):
        rig = Rig([fakes.default_responder])
        rig.poll()
        rig.poller.stop()
        self.assertTrue(rig.transports[0].closed)


if __name__ == "__main__":
    unittest.main()
