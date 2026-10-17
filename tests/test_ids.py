"""Configurable vendor/product ids: config parsing, device discovery, CLI flags."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import config as C
from hs80 import device as D
from hs80 import protocol as P


class ParseIdTests(unittest.TestCase):
    def test_forms(self):
        for v, want in [("0x2A08", 0x2A08), ("2a08", 0x2A08), ("0A97", 0x0A97), (0x0A97, 0x0A97),
                        ("10760", 10760), (" 0x1b1c ", 0x1B1C), ("", None), ("zz", None), (0, None),
                        (70000, None), (True, None), (None, None), ("0x0", None)]:
            self.assertEqual(C.parse_id(v), want, repr(v))

    def test_config_defaults_and_coercion(self):
        c = C.Config()
        self.assertEqual((c.vendor_id, c.product_ids), (0x1B1C, (0x0A97, 0x2A08, 0x2A02)))
        c = C._coerce({"vendor_id": "0x1B1C", "product_ids": ["0x2A08", "0x0A97", "0x2A08", "junk"]})
        self.assertEqual(c.product_ids, (0x2A08, 0x0A97))
        c = C._coerce({"product_ids": "0x2A02"})
        self.assertEqual(c.product_ids, (0x2A02,))
        c = C._coerce({"product_ids": [], "vendor_id": "nope"})
        self.assertEqual((c.vendor_id, c.product_ids), (0x1B1C, C.DEFAULT_PIDS))

    def test_round_trip_writes_hex_strings(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            C.save(C.Config(vendor_id=0x1B1C, product_ids=(0x2A08, 0x0A97)), p)
            raw = json.load(open(p, encoding="utf-8"))
            self.assertEqual(raw["vendor_id"], "0x1B1C")
            self.assertEqual(raw["product_ids"], ["0x2A08", "0x0A97"])
            self.assertEqual(C.load(p).product_ids, (0x2A08, 0x0A97))


def fake_hid(devices):
    """A stand-in for the hidapi module whose enumerate() returns `devices`."""
    m = types.ModuleType("hid")
    m.enumerate = lambda vid=0, pid=0: [d for d in devices if (vid in (0, d["vendor_id"])) and (pid in (0, d["product_id"]))]
    return m


def dev(pid, usage_page, usage, iface, path):
    return {"vendor_id": 0x1B1C, "product_id": pid, "usage_page": usage_page, "usage": usage,
            "interface_number": iface, "path": path.encode(), "product_string": f"dev {pid:04x}"}


ENUM = [
    dev(0x0A97, 0x000C, 0x0001, 3, "hs80-col01"),
    dev(0x0A97, 0xFF42, 0x0001, 3, "hs80-col04"),       # the one we want
    dev(0x0A97, 0xFF42, 0x0002, 3, "hs80-col05"),
    dev(0x2A08, 0xFF42, 0x0001, 4, "void-ctrl"),         # a second receiver, different pid / interface
    dev(0x1B7F, 0xFF42, 0x0001, 0, "darkcore"),          # not in the list
]


class FindDeviceTests(unittest.TestCase):
    def test_default_finds_hs80_control_collection_by_usage(self):
        with mock.patch.dict(sys.modules, {"hid": fake_hid(ENUM)}):
            self.assertEqual(D.find_path(), b"hs80-col04")

    def test_pid_list_order_decides_when_both_present(self):
        with mock.patch.dict(sys.modules, {"hid": fake_hid(ENUM)}):
            self.assertEqual(D.find_path(P.VID, (0x2A08, 0x0A97)), b"void-ctrl")
            self.assertEqual(D.find_path(P.VID, (0x0A97, 0x2A08)), b"hs80-col04")
            self.assertEqual(D.find_path(P.VID, (0x2A02,)), None)
            self.assertEqual(D.find_device(P.VID, (0x2A08,))["interface_number"], 4)

    def test_open_reports_requested_ids_when_absent(self):
        with mock.patch.dict(sys.modules, {"hid": fake_hid([])}):
            with self.assertRaises(D.DongleNotFound) as cm:
                D.Hs80Device.open(vid=0x1B1C, pids=(0x2A08, 0x2A02))
            self.assertIn("1B1C:2A08", str(cm.exception))
            self.assertIn("1B1C:2A02", str(cm.exception))


class CliTests(unittest.TestCase):
    def test_pid_flags_reach_the_poller(self):
        import hs80.__main__ as M
        seen = {}

        class FakePoller:
            def __init__(self, interval, **kw):
                seen.update(kw)
                from hs80.state import BatteryState, Status
                self._st = BatteryState(status=Status.NO_DONGLE)
            def poll_once(self):
                return self._st
            def stop(self):
                pass
        with mock.patch.object(M, "Poller", FakePoller), mock.patch("sys.stdout"):
            rc = M.main(["--pid", "0x2A08", "--pid", "0A97", "--vid", "0x1B1C"])
        self.assertEqual(rc, 1)
        self.assertEqual(seen["pids"], (0x2A08, 0x0A97))
        self.assertEqual(seen["vid"], 0x1B1C)


if __name__ == "__main__":
    unittest.main()
