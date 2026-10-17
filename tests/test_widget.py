import codecs
import json
import os
import tempfile
import unittest

import config as C
from hs80.state import BatteryState, Status
from widget import icon as I
from widget.notify import Alerts


def ok(p, charging=False):
    return BatteryState(status=Status.OK, percent=float(p), charging=charging, last_percent=float(p))


class IconTests(unittest.TestCase):
    def test_every_sample_state_renders(self):
        for name, st in I.sample_states().items():
            im = I.render(st)
            self.assertEqual(im.size, (I.SIZE, I.SIZE), name)
            self.assertEqual(im.mode, "RGBA", name)
            self.assertTrue(im.getbbox(), f"{name} rendered blank")
        self.assertEqual(I.render(ok(50), 32).size, (32, 32))

    def test_band_colours_match_thresholds(self):
        self.assertEqual(I.band_colour(50), I.GREEN)
        self.assertEqual(I.band_colour(49), I.AMBER)
        self.assertEqual(I.band_colour(30), I.AMBER)
        self.assertEqual(I.band_colour(29), I.RED)
        self.assertEqual(I.band_colour(None), I.GREY)

    def test_charging_icon_differs(self):
        self.assertNotEqual(I.render(ok(73)).tobytes(), I.render(ok(73, True)).tobytes())


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.a = Alerts((30, 10), send=lambda t, b: self.sent.append((t, b)))

    def test_fires_once_per_crossing(self):
        for p in (45, 31, 30, 29, 28):
            self.a.on_state(ok(p))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("low", self.sent[0][0])
        self.assertIn("30%", self.sent[0][1])

    def test_second_threshold_is_urgent(self):
        for p in (30, 20, 10, 9):
            self.a.on_state(ok(p))
        self.assertEqual([t for t, _ in self.sent], ["HS80 Max battery low", "HS80 Max battery critical"])

    def test_drop_past_both_gives_one_toast(self):
        self.a.on_state(ok(80))
        self.a.on_state(ok(5))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("critical", self.sent[0][0])

    def test_rearms_after_recovery(self):
        self.a.on_state(ok(25))
        self.a.on_state(ok(34))     # not above 30 + 5 yet
        self.a.on_state(ok(25))
        self.assertEqual(len(self.sent), 1)
        self.a.on_state(ok(36))
        self.a.on_state(ok(25))
        self.assertEqual(len(self.sent), 2)

    def test_charging_resets_and_silences(self):
        self.a.on_state(ok(25))
        self.a.on_state(ok(26, charging=True))
        self.a.on_state(ok(27, charging=True))
        self.assertEqual(len(self.sent), 1)
        self.a.on_state(ok(25))
        self.assertEqual(len(self.sent), 2)

    def test_disabled(self):
        self.a.configure(enabled=False)
        self.a.on_state(ok(5))
        self.assertEqual(self.sent, [])

    def test_non_ok_states_do_not_fire(self):
        self.a.on_state(BatteryState(status=Status.ASLEEP, last_percent=5))
        self.a.on_state(BatteryState(status=Status.ERROR, error="x"))
        self.assertEqual(self.sent, [])

    def test_disconnect_alert_optional(self):
        self.a.on_state(ok(50))
        self.a.on_state(BatteryState(status=Status.ASLEEP, last_percent=50))
        self.assertEqual(self.sent, [])
        self.a.configure(disconnect_alert=True)
        self.a.on_state(ok(50))
        self.a.on_state(BatteryState(status=Status.ASLEEP, last_percent=50))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("disconnected", self.sent[0][0])


class AlertPersistenceTests(unittest.TestCase):
    def test_recent_toast_is_not_repeated_after_restart(self):
        from datetime import datetime, timedelta
        sent, fired = [], []
        t0 = datetime(2026, 10, 17, 14, 39)
        a = Alerts((30, 10), send=lambda t, b: sent.append(t), on_fire=lambda th, when: fired.append((th, when)),
                   now=lambda: t0, fired_at={"30": (t0 - timedelta(minutes=50)).isoformat()})
        a.on_state(ok(26))
        self.assertEqual(sent, [])                       # 30 % toasted 50 min ago -> stay quiet
        a.on_state(ok(9))
        self.assertEqual(len(sent), 1)                   # 10 % is new -> fires
        self.assertEqual(fired, [(10, t0)])

    def test_old_toast_is_repeated_after_restart(self):
        from datetime import datetime, timedelta
        sent = []
        t0 = datetime(2026, 10, 17, 14, 39)
        a = Alerts((30, 10), send=lambda t, b: sent.append(t), now=lambda: t0,
                   fired_at={"30": (t0 - timedelta(hours=3)).isoformat(), "99": "garbage", "10": "not-a-date"})
        a.on_state(ok(26))
        self.assertEqual(len(sent), 1)

    def test_app_persists_alert_times(self):
        import config as C
        import widget.app as A
        saved = []

        class Fake:
            cfg = C.Config()
            def _save(self, cfg):
                saved.append(cfg); self.cfg = cfg
        f = Fake()
        from datetime import datetime
        A.App._remember_alert(f, 30, datetime(2026, 10, 17, 14, 39, 34))
        self.assertEqual(saved[-1].last_alerts, {"30": "2026-10-17T14:39:34"})
        A.App._remember_alert(f, 10, datetime(2026, 10, 17, 15, 0, 0))
        self.assertEqual(set(saved[-1].last_alerts), {"30", "10"})


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        c = C.Config()
        self.assertEqual((c.interval_s, c.thresholds, c.alerts), (60, (30, 10), True))

    def test_round_trip_and_coercion(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "config.json")
            C.save(C.Config(interval_s=120, thresholds=(10, 30), float_pos=(10, 20)), p)
            c = C.load(p)
            self.assertEqual(c.interval_s, 120)
            self.assertEqual(c.thresholds, (30, 10))
            self.assertEqual(c.float_pos, (10, 20))
            with open(p, "w") as f:
                json.dump({"interval_s": "bad", "thresholds": "x", "alerts": 0, "unknown": 1}, f)
            c = C.load(p)
            self.assertEqual((c.interval_s, c.thresholds, c.alerts), (60, (30, 10), False))
            self.assertEqual(c.last_alerts, {})
            self.assertEqual(C._coerce({"last_alerts": {30: "2026-10-17T14:39:34"}}).last_alerts, {"30": "2026-10-17T14:39:34"})
            self.assertEqual(C._coerce({"last_alerts": "bad"}).last_alerts, {})
            with open(p, "w") as f:
                f.write("{not json")
            self.assertEqual(C.load(p), C.Config())
            self.assertEqual(C.load(os.path.join(d, "missing.json")), C.Config())
            with open(p, "wb") as f:                       # BOM, as PowerShell's Out-File writes
                f.write(codecs.BOM_UTF8 + b'{"show_float": true}')
            self.assertTrue(C.load(p).show_float)


if __name__ == "__main__":
    unittest.main()
