import unittest

from hs80 import protocol as P
from tests import fakes


class FrameTests(unittest.TestCase):
    def test_battery_request_bytes(self):
        f = P.frame(P.ROUTE_HEADSET, P.PROP_BATTERY)
        self.assertEqual(len(f), 64)
        self.assertEqual(f[:4], bytes.fromhex("0209020f"))
        self.assertEqual(f[4:], bytes(60))

    def test_handshake_frames(self):
        self.assertEqual(P.frame(P.ROUTE_RECEIVER, P.PROP_FIRMWARE)[:4], bytes.fromhex("02080213"))
        self.assertEqual(P.frame(P.ROUTE_RECEIVER, P.PROP_HEARTBEAT)[:4], bytes.fromhex("02080212"))
        self.assertEqual(P.frame(P.ROUTE_HEADSET, P.PROP_HEARTBEAT)[:4], bytes.fromhex("02090212"))

    def test_no_set_command_in_constants(self):
        # the library must only ever build gets
        self.assertEqual(P.CMD_GET, 0x02)
        self.assertEqual(P.frame(P.ROUTE_HEADSET, P.PROP_BATTERY)[2], 0x02)


class ParseTests(unittest.TestCase):
    def test_captured_battery_reply(self):
        r = P.parse_reply(fakes.BATTERY_45)
        self.assertTrue(r.ok)
        self.assertEqual(r.route, P.REPLY_ROUTE_HEADSET)
        self.assertEqual(r.value, 450)
        self.assertEqual(P.decode_battery(r), 45.0)

    def test_receiver_replies_parse(self):
        fw = P.parse_reply(fakes.pad(fakes.CAPTURED["fw"]))
        hb = P.parse_reply(fakes.pad(fakes.CAPTURED["rx_hb"]))
        self.assertTrue(fw.ok and hb.ok)
        self.assertEqual(fw.route, P.REPLY_ROUTE_RECEIVER)
        self.assertEqual(hb.value, 0x0A97)

    def test_nak(self):
        r = P.parse_reply(fakes.NAK)
        self.assertTrue(r.nak)
        self.assertFalse(r.ok)
        self.assertIsNone(P.decode_battery(r))

    def test_short_and_none(self):
        self.assertIsNone(P.parse_reply(b"\x01\x01\x02"))
        self.assertIsNone(P.parse_reply(b""))
        self.assertIsNone(P.decode_battery(None))
        self.assertIsNone(P.decode_charge(None))

    def test_error_byte_rejected(self):
        r = P.parse_reply(fakes.reply(1, 450, err=0x05))
        self.assertFalse(r.ok)
        self.assertIsNone(P.decode_battery(r))

    def test_zero_and_out_of_range_are_unknown(self):
        self.assertIsNone(P.decode_battery(P.parse_reply(fakes.ZERO)))
        self.assertIsNone(P.decode_battery(P.parse_reply(fakes.HEADSET_ID)))   # 2710 would be "271 %"
        self.assertIsNone(P.decode_battery(P.parse_reply(fakes.reply(1, 1001))))
        self.assertEqual(P.decode_battery(P.parse_reply(fakes.reply(1, 1000))), 100.0)
        self.assertEqual(P.decode_battery(P.parse_reply(fakes.reply(1, 1))), 0.1)

    def test_charge(self):
        self.assertFalse(P.decode_charge(P.parse_reply(fakes.CHARGE_2)))
        self.assertTrue(P.decode_charge(P.parse_reply(fakes.CHARGE_1)))
        self.assertFalse(P.decode_charge(P.parse_reply(fakes.reply(1, 0))))

    def test_mic(self):
        self.assertTrue(P.decode_mic_muted(P.parse_reply(fakes.pad(fakes.CAPTURED["mic1"]))))
        self.assertFalse(P.decode_mic_muted(P.parse_reply(fakes.reply(1, 0))))


if __name__ == "__main__":
    unittest.main()
