import unittest

from hs80 import protocol as P
from hs80.device import Hs80Device, DeviceError
from tests import fakes


def make(responder=fakes.default_responder, preloaded=None):
    t = fakes.FakeTransport(responder, preloaded)
    clk = fakes.FakeClock()
    return Hs80Device(t, sleep=clk.sleep, clock=clk), t, clk


class DeviceTests(unittest.TestCase):
    def test_bare_battery_read_sends_one_request(self):
        dev, t, _ = make()
        pct, r = dev.read_battery()
        self.assertEqual(pct, 45.0)
        self.assertEqual(t.props_sent(), [(P.ROUTE_HEADSET, P.PROP_BATTERY)])
        self.assertEqual(r.raw[:6], fakes.BATTERY_45[:6])

    def test_write_gap_is_enforced(self):
        dev, t, clk = make()
        t0 = clk()
        dev.get_property(P.ROUTE_HEADSET, P.PROP_BATTERY)
        dev.get_property(P.ROUTE_HEADSET, P.PROP_CHARGE)
        self.assertGreaterEqual(clk() - t0, 0.05 - 1e-9)

    def test_handshake_fallback_after_bad_reply_then_success(self):
        calls = {"n": 0}

        def responder(frame):
            if frame[3] == P.PROP_BATTERY:
                calls["n"] += 1
                return [fakes.HEADSET_ID] if calls["n"] == 1 else [fakes.BATTERY_45]
            return fakes.default_responder(frame)

        dev, t, _ = make(responder)
        pct, _ = dev.read_battery()
        self.assertEqual(pct, 45.0)
        sent = t.props_sent()
        self.assertEqual(sent[0], (P.ROUTE_HEADSET, P.PROP_BATTERY))
        self.assertIn((P.ROUTE_RECEIVER, P.PROP_FIRMWARE), sent)
        self.assertIn((P.ROUTE_RECEIVER, P.PROP_HEARTBEAT), sent)
        self.assertIn((P.ROUTE_HEADSET, P.PROP_HEARTBEAT), sent)
        self.assertEqual(sent[-1], (P.ROUTE_HEADSET, P.PROP_BATTERY))

    def test_asleep_gives_none_after_three_attempts(self):
        def responder(frame):
            return [] if frame[1] == P.ROUTE_HEADSET else fakes.default_responder(frame)
        dev, t, _ = make(responder)
        pct, r = dev.read_battery()
        self.assertIsNone(pct)
        self.assertIsNone(r)
        self.assertEqual(sum(1 for p in t.props_sent() if p == (P.ROUTE_HEADSET, P.PROP_BATTERY)), 3)

    def test_zero_value_never_becomes_a_level(self):
        dev, _, _ = make(lambda f: [fakes.ZERO] if f[3] == P.PROP_BATTERY else fakes.default_responder(f))
        pct, _ = dev.read_battery()
        self.assertIsNone(pct)

    def test_nak_then_ok_in_same_window(self):
        dev, _, _ = make(lambda f: [fakes.NAK, fakes.BATTERY_45] if f[3] == P.PROP_BATTERY else [])
        pct, _ = dev.read_battery()
        self.assertEqual(pct, 45.0)

    def test_stale_reports_are_drained_before_request(self):
        # a foreign charge answer queued before our battery request must not be taken as a level
        dev, t, _ = make(preloaded=[fakes.reply(1, 2)])
        pct, _ = dev.read_battery()
        self.assertEqual(pct, 45.0)

    def test_charge_and_mic(self):
        dev, _, _ = make()
        self.assertFalse(dev.read_charge())
        self.assertTrue(dev.read_mic_muted())

    def test_transport_error_propagates(self):
        dev, t, _ = make()
        t.fail_write = DeviceError("write failed: boom")
        with self.assertRaises(DeviceError):
            dev.read_battery()

    def test_close_is_idempotent(self):
        dev, t, _ = make()
        dev.close()
        dev.close()
        self.assertTrue(t.closed)


if __name__ == "__main__":
    unittest.main()
